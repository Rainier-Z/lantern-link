"""Generic binary upload, storage, and asset/message lifecycle services."""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import PROJECT_ROOT
from app.core.database import DATABASE_PATH, get_connection, initialize_database
from app.models.asset import Asset, StorageStats
from app.models.message import Message
from app.repositories.asset_repository import AssetRepository, asset_from_row


LOGGER = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
STAGING_RETENTION_SECONDS = 24 * 60 * 60
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
MIME_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}


class UploadError(ValueError):
    """A client-correctable multipart upload error."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class UploadPolicy:
    """Validation rules shared by image and generic file uploads."""

    kind: str
    max_bytes: int
    allowed_extensions: frozenset[str] | set[str] | None = None
    allowed_mime: frozenset[str] | set[str] | None = None
    _normalized_extensions: frozenset[str] = field(init=False, repr=False)
    _normalized_mime: frozenset[str] | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.kind not in {"image", "file"}:
            raise ValueError("UploadPolicy kind must be image or file")
        if self.max_bytes <= 0:
            raise ValueError("UploadPolicy max_bytes must be positive")
        extension_values = self.allowed_extensions
        if isinstance(extension_values, str):
            extension_values = (extension_values,)
        extensions = None if extension_values is None else frozenset(
            value.lower() if value.startswith(".") else f".{value.lower()}"
            for value in extension_values
        )
        mime_values = self.allowed_mime
        if isinstance(mime_values, str):
            mime_values = (mime_values,)
        mime = None if mime_values is None else frozenset(
            value.lower() for value in mime_values
        )
        object.__setattr__(self, "_normalized_extensions", extensions or frozenset())
        object.__setattr__(self, "_normalized_mime", mime)

    def accepts_extension(self, extension: str) -> bool:
        return self.allowed_extensions is None or extension.lower() in self._normalized_extensions

    def accepts_mime(self, mime_type: str) -> bool:
        if self._normalized_mime is None:
            return True
        mime_type = mime_type.lower()
        return any(
            allowed == mime_type
            or (allowed.endswith("/*") and mime_type.startswith(allowed[:-1]))
            for allowed in self._normalized_mime
        )


IMAGE_POLICY = UploadPolicy(
    kind="image",
    max_bytes=MAX_IMAGE_BYTES,
    allowed_extensions=frozenset(ALLOWED_EXTENSIONS),
    allowed_mime=frozenset({"image/*"}),
)
FILE_POLICY = UploadPolicy(kind="file", max_bytes=MAX_FILE_BYTES)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class AssetService:
    """Own filesystem and transaction boundaries for generic assets."""

    def __init__(self, database_path: Path | str | None = None) -> None:
        self.database_path = Path(database_path) if database_path else DATABASE_PATH
        self.data_dir = self.database_path.parent
        self.staging_dir = self.data_dir / "staging"
        self.assets_dir = self.data_dir / "assets"
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        initialize_database(self.database_path)
        self.repository = AssetRepository(self.database_path)
        self.recover_delete_pending()
        self.scan_staging()

    def scan_staging(self, now: datetime | None = None) -> list[Path]:
        """Remove stale, regular partial uploads and retain recent ones.

        Upload resume is not supported yet, so a regular ``*.partial`` file
        older than 24 hours has no recovery value.  Only direct children of
        the configured staging directory are considered; directories,
        symlinks, and all other names are left untouched.  A filesystem error
        is logged without exposing local paths and never prevents startup.

        The returned paths are the partial files still present after the scan
        (for example, files newer than the retention window or files whose
        deletion failed).
        """

        now_timestamp = (now or _utc_now()).timestamp()
        retained: list[Path] = []
        removed = 0
        failures = 0
        for path in sorted(self.staging_dir.glob("*.partial")):
            try:
                file_stat = path.lstat()
            except OSError as exc:
                failures += 1
                LOGGER.warning(
                    "Unable to inspect staged partial upload; cleanup will retry "
                    "(error_type=%s)",
                    type(exc).__name__,
                )
                retained.append(path)
                continue

            if not stat.S_ISREG(file_stat.st_mode):
                continue
            if now_timestamp - file_stat.st_mtime <= STAGING_RETENTION_SECONDS:
                retained.append(path)
                continue

            try:
                path.unlink()
            except OSError as exc:
                failures += 1
                LOGGER.warning(
                    "Unable to remove stale staged partial upload; cleanup will "
                    "retry (error_type=%s)",
                    type(exc).__name__,
                )
                retained.append(path)
            else:
                removed += 1

        if removed:
            LOGGER.info("Removed %d stale staged partial upload(s)", removed)
        if failures:
            LOGGER.warning("Staged partial cleanup had %d failure(s)", failures)
        return retained

    def recover_delete_pending(self) -> list[str]:
        """Retry only assets already committed as ``DELETE_PENDING``.

        A successful, idempotent unlink is followed by a separate metadata
        transition to ``DELETED``.  If unlinking fails, the row remains
        ``DELETE_PENDING`` so the next process startup can retry it.  The
        asset identifier is used in logs instead of a local filesystem path.
        """

        completed: list[str] = []
        for asset in self.repository.list_delete_pending():
            try:
                self.resolve_asset_path(asset).unlink(missing_ok=True)
            except OSError as exc:
                LOGGER.warning(
                    "Unable to remove pending asset file; will retry on next startup "
                    "(asset_id=%s, error_type=%s)",
                    asset.id,
                    type(exc).__name__,
                )
                continue
            if self.repository.mark_deleted(asset.id, _serialize_datetime(_utc_now())):
                completed.append(asset.id)
        return completed

    async def persist_upload(
        self,
        file: UploadFile,
        sender: str,
        policy: UploadPolicy,
    ) -> tuple[Message, Asset]:
        """Stream one upload into generic asset storage atomically."""

        upload_id = str(uuid4())
        staging_path = self.staging_dir / f"{upload_id}.partial"
        final_path: Path | None = None
        try:
            sender = sender.strip()
            if sender not in {"pc", "iphone"}:
                raise UploadError(422, "sender must be pc or iphone")

            filename = (file.filename or "").strip()
            if not filename:
                raise UploadError(400, "A file field is required")
            safe_filename = Path(filename.replace("\\", "/")).name
            extension = Path(safe_filename).suffix.lower()
            if not policy.accepts_extension(extension):
                raise UploadError(415, "Unsupported upload format")

            mime_type = (file.content_type or "application/octet-stream").strip()
            if policy.kind == "image" and mime_type.lower() == "application/octet-stream":
                mime_type = MIME_BY_EXTENSION.get(extension, mime_type)
            if not policy.accepts_mime(mime_type):
                raise UploadError(415, "Unsupported upload media type")

            digest = hashlib.sha256()
            size = 0
            with staging_path.open("wb") as output:
                while True:
                    chunk = await file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > policy.max_bytes:
                        limit_mib = policy.max_bytes / (1024 * 1024)
                        label = f"{limit_mib:g} MiB"
                        raise UploadError(413, f"Upload exceeds the {label} limit")
                    digest.update(chunk)
                    output.write(chunk)
            if size <= 0:
                raise UploadError(400, "Upload cannot be empty")
            sha256 = digest.hexdigest()
            filename = safe_filename
            created_at = _utc_now()
            date_dir = self.assets_dir / created_at.strftime("%Y") / created_at.strftime("%m") / created_at.strftime("%d")
            date_dir.mkdir(parents=True, exist_ok=True)
            stored_filename = f"{upload_id}{extension}"
            final_path = date_dir / stored_filename
            os.replace(staging_path, final_path)
            relative_path = final_path.relative_to(self.data_dir).as_posix()
            message_id = str(uuid4())
            with get_connection(self.database_path) as connection:
                connection.execute(
                    """
                    INSERT INTO assets
                        (id, kind, original_filename, stored_filename, extension,
                         mime_type, size, sha256, relative_path, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'AVAILABLE', ?)
                    """,
                    (
                        upload_id,
                        policy.kind,
                        filename,
                        stored_filename,
                        extension,
                        mime_type,
                        size,
                        sha256,
                        relative_path,
                        _serialize_datetime(created_at),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO messages
                        (id, sender, type, text_content, asset_id, status, created_at)
                    VALUES (?, ?, ?, NULL, ?, 'SENT', ?)
                    """,
                    (
                        message_id,
                        sender,
                        policy.kind,
                        upload_id,
                        _serialize_datetime(created_at),
                    ),
                )
                asset_row = connection.execute(
                    "SELECT * FROM assets WHERE id = ?", (upload_id,)
                ).fetchone()
            if asset_row is None:
                raise RuntimeError("Asset metadata was not created")
            message = Message(
                id=message_id,
                sender=sender,
                type=policy.kind,
                content=None,
                created_at=created_at,
                asset_id=upload_id,
                status="SENT",
            )
            return message, asset_from_row(asset_row, url=f"/api/assets/{upload_id}")
        except Exception:
            if final_path is not None and final_path.is_file():
                final_path.unlink()
            if staging_path.is_file():
                staging_path.unlink()
            raise
        finally:
            await file.close()

    async def upload_image(self, file: UploadFile, sender: str) -> tuple[Message, Asset]:
        """Persist a streamed image using the image policy."""

        return await self.persist_upload(file, sender, IMAGE_POLICY)

    async def upload_file(self, file: UploadFile, sender: str) -> tuple[Message, Asset]:
        """Persist a streamed generic file using the 512 MiB file policy."""

        return await self.persist_upload(file, sender, FILE_POLICY)

    def resolve_asset_path(self, asset: Asset) -> Path:
        """Resolve an internally stored relative path below the data directory."""

        candidate = (self.data_dir / asset.relative_path).resolve()
        root = self.data_dir.resolve()
        if candidate != root and root not in candidate.parents:
            raise RuntimeError("Asset path escapes the configured data directory")
        return candidate

    def get_asset_file(self, asset_id: str) -> tuple[Asset, Path] | None:
        asset = self.repository.get(asset_id)
        if asset is None:
            return None
        if asset.status != "AVAILABLE":
            return asset, self.resolve_asset_path(asset)
        path = self.resolve_asset_path(asset)
        if not path.is_file():
            with get_connection(self.database_path) as connection:
                connection.execute(
                    "UPDATE assets SET status = 'MISSING' WHERE id = ? AND status = 'AVAILABLE'",
                    (asset_id,),
                )
            asset = self.repository.get(asset_id) or asset.model_copy(update={"status": "MISSING"})
        return asset, path

    def delete_message(self, message_id: str) -> Message | None:
        """Soft-delete a message and complete deletion of an unreferenced image."""

        asset_path: Path | None = None
        asset_id_to_delete: str | None = None
        with get_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE id = ? AND deleted_at IS NULL", (message_id,)
            ).fetchone()
            if row is None:
                return None
            deleted_at = _serialize_datetime(_utc_now())
            connection.execute(
                "UPDATE messages SET deleted_at = ?, status = 'DELETED' WHERE id = ?",
                (deleted_at, message_id),
            )
            asset_id = row["asset_id"]
            if asset_id:
                references = connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE asset_id = ? AND deleted_at IS NULL",
                    (asset_id,),
                ).fetchone()[0]
                if references == 0:
                    asset_row = connection.execute(
                        "SELECT * FROM assets WHERE id = ?", (asset_id,)
                    ).fetchone()
                    if asset_row is not None:
                        asset = asset_from_row(asset_row)
                        asset_path = self.resolve_asset_path(asset)
                        pending_cursor = connection.execute(
                            """
                            UPDATE assets SET status = 'DELETE_PENDING', deleted_at = NULL
                            WHERE id = ? AND status <> 'DELETED'
                            """,
                            (asset_id,),
                        )
                        if pending_cursor.rowcount == 1:
                            asset_id_to_delete = asset_id
            message = Message(
                id=row["id"],
                sender=row["sender"],
                type=row["type"],
                content=row["text_content"],
                created_at=datetime.fromisoformat(row["created_at"]),
                asset_id=row["asset_id"],
                status="DELETED",
                deleted_at=datetime.fromisoformat(deleted_at),
            )
        if asset_path is not None and asset_id_to_delete is not None:
            try:
                asset_path.unlink(missing_ok=True)
            except OSError as exc:
                LOGGER.warning(
                    "Unable to remove pending asset file; will retry on next startup "
                    "(asset_id=%s, error_type=%s)",
                    asset_id_to_delete,
                    type(exc).__name__,
                )
            else:
                self.repository.mark_deleted(
                    asset_id_to_delete,
                    _serialize_datetime(_utc_now()),
                )
        return message

    def storage_stats(self) -> StorageStats:
        total_bytes, image_bytes, file_bytes, asset_count, message_count = self.repository.stats()
        return StorageStats(
            total_bytes=total_bytes,
            image_bytes=image_bytes,
            file_bytes=file_bytes,
            asset_count=asset_count,
            message_count=message_count,
        )


asset_service = AssetService()
