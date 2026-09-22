"""Image streaming, file storage, and asset/message lifecycle services."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterable
from uuid import uuid4

from app.core.config import PROJECT_ROOT
from app.core.database import DATABASE_PATH, get_connection, initialize_database
from app.models.asset import Asset, StorageStats
from app.models.message import Message
from app.repositories.asset_repository import AssetRepository, asset_from_row


LOGGER = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 100 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
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
_DISPOSITION_NAME = re.compile(r'(?:^|;)\s*name="([^"]*)"', re.IGNORECASE)
_DISPOSITION_FILENAME = re.compile(
    r'(?:^|;)\s*filename="([^"]*)"', re.IGNORECASE
)


class UploadError(ValueError):
    """A client-correctable multipart upload error."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_content_type(value: str | None) -> bytes:
    if not value or not value.lower().startswith("multipart/form-data"):
        raise UploadError(400, "Content-Type must be multipart/form-data")
    match = re.search(r"(?:^|;)\s*boundary=(?:\"([^\"]+)\"|([^;\s]+))", value, re.I)
    if not match:
        raise UploadError(400, "Multipart boundary is missing")
    boundary = (match.group(1) or match.group(2)).encode("latin-1")
    if not boundary or len(boundary) > 200:
        raise UploadError(400, "Multipart boundary is invalid")
    return b"--" + boundary


def _parse_headers(raw: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in raw.split(b"\r\n"):
        if not line or b":" not in line:
            continue
        name, value = line.split(b":", 1)
        headers[name.decode("latin-1").strip().lower()] = value.decode(
            "latin-1"
        ).strip()
    return headers


class AssetService:
    """Own the filesystem and transaction boundaries for image assets."""

    def __init__(self, database_path: Path | str | None = None) -> None:
        self.database_path = Path(database_path) if database_path else DATABASE_PATH
        self.data_dir = self.database_path.parent
        self.staging_dir = self.data_dir / "staging"
        self.assets_dir = self.data_dir / "assets"
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        initialize_database(self.database_path)
        self.repository = AssetRepository(self.database_path)
        self.scan_staging()

    def scan_staging(self) -> list[Path]:
        """Report partial uploads without deleting or mutating them."""

        partials = sorted(self.staging_dir.glob("*.partial"))
        if partials:
            LOGGER.warning("Found %d staged partial upload(s): %s", len(partials), partials)
        return partials

    async def _parse_multipart(
        self,
        chunks: AsyncIterable[bytes],
        content_type: str | None,
        staging_path: Path,
    ) -> tuple[str, str, str, str, int, str]:
        """Stream one multipart image part to staging and return its metadata."""

        boundary = _parse_content_type(content_type)
        delimiter = b"\r\n" + boundary
        buffer = bytearray()
        state = "preamble"
        part_name: str | None = None
        filename: str | None = None
        file_mime: str | None = None
        sender: str | None = None
        sender_bytes = bytearray()
        file_part_seen = False
        digest = hashlib.sha256()
        size = 0

        def emit_file(data: bytes, output) -> None:
            nonlocal size
            if not data:
                return
            size += len(data)
            if size > MAX_IMAGE_BYTES:
                raise UploadError(413, "Image exceeds the 100 MiB limit")
            digest.update(data)
            output.write(data)

        with staging_path.open("wb") as output:
            async for chunk in chunks:
                if not chunk:
                    continue
                buffer.extend(chunk)
                while True:
                    if state == "preamble":
                        index = buffer.find(boundary)
                        if index < 0:
                            keep = max(len(boundary) + 4, 16)
                            if len(buffer) > keep:
                                del buffer[:-keep]
                            break
                        del buffer[: index + len(boundary)]
                        if buffer.startswith(b"--"):
                            state = "done"
                            continue
                        if not buffer.startswith(b"\r\n"):
                            raise UploadError(400, "Malformed multipart body")
                        del buffer[:2]
                        state = "headers"
                    elif state == "headers":
                        index = buffer.find(b"\r\n\r\n")
                        if index < 0:
                            if len(buffer) > 16 * 1024:
                                raise UploadError(400, "Multipart headers are too large")
                            break
                        headers = _parse_headers(bytes(buffer[:index]))
                        del buffer[: index + 4]
                        disposition = headers.get("content-disposition", "")
                        name_match = _DISPOSITION_NAME.search(disposition)
                        if not name_match:
                            raise UploadError(400, "Multipart field name is missing")
                        part_name = name_match.group(1)
                        filename_match = _DISPOSITION_FILENAME.search(disposition)
                        filename = filename_match.group(1) if filename_match else None
                        if filename is not None:
                            try:
                                filename = filename.encode("latin-1").decode("utf-8")
                            except UnicodeError:
                                # Older clients may send a legacy filename encoding.
                                pass
                        file_mime = headers.get("content-type")
                        if part_name == "file":
                            if file_part_seen:
                                raise UploadError(400, "Only one file is allowed")
                            file_part_seen = True
                        state = "data"
                    elif state == "data":
                        index = buffer.find(delimiter)
                        if index < 0:
                            keep = len(delimiter) + 4
                            if len(buffer) > keep:
                                payload = bytes(buffer[:-keep])
                                if part_name == "file":
                                    emit_file(payload, output)
                                elif part_name == "sender":
                                    sender_bytes.extend(payload)
                                del buffer[:-keep]
                            break
                        payload = bytes(buffer[:index])
                        if part_name == "file":
                            emit_file(payload, output)
                        elif part_name == "sender":
                            sender_bytes.extend(payload)
                        del buffer[: index + len(delimiter)]
                        state = "boundary_end"
                    elif state == "boundary_end":
                        if len(buffer) < 2:
                            break
                        if buffer.startswith(b"--"):
                            del buffer[:2]
                            state = "done"
                            continue
                        if not buffer.startswith(b"\r\n"):
                            raise UploadError(400, "Malformed multipart boundary")
                        del buffer[:2]
                        state = "headers"
                    elif state == "done":
                        # Ignore the optional final CRLF and reject non-whitespace bytes.
                        if buffer and bytes(buffer).strip(b"\r\n 	"):
                            raise UploadError(400, "Unexpected bytes after multipart body")
                        buffer.clear()
                        break
            if state not in {"done", "boundary_end"}:
                raise UploadError(400, "Incomplete multipart upload")

        if state == "boundary_end":
            raise UploadError(400, "Incomplete multipart upload")
        sender = bytes(sender_bytes).decode("utf-8", errors="replace").strip()
        if sender not in {"pc", "iphone"}:
            raise UploadError(422, "sender must be pc or iphone")
        if not file_part_seen or not filename:
            raise UploadError(400, "An image file field is required")
        safe_filename = Path(filename.replace("\\", "/")).name
        extension = Path(safe_filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise UploadError(415, "Unsupported image format")
        if size <= 0:
            raise UploadError(400, "Image cannot be empty")
        mime_type = file_mime or MIME_BY_EXTENSION[extension]
        if mime_type == "application/octet-stream":
            mime_type = MIME_BY_EXTENSION[extension]
        if not mime_type.startswith("image/"):
            raise UploadError(415, "Unsupported image media type")
        return sender, safe_filename, extension[1:], mime_type, size, digest.hexdigest()

    async def upload_image(
        self, chunks: AsyncIterable[bytes], content_type: str | None
    ) -> tuple[Message, Asset]:
        """Persist a streamed image and its image message atomically."""

        upload_id = str(uuid4())
        staging_path = self.staging_dir / f"{upload_id}.partial"
        final_path: Path | None = None
        try:
            sender, filename, extension, mime_type, size, sha256 = await self._parse_multipart(
                chunks, content_type, staging_path
            )
            created_at = _utc_now()
            date_dir = self.assets_dir / created_at.strftime("%Y") / created_at.strftime("%m") / created_at.strftime("%d")
            date_dir.mkdir(parents=True, exist_ok=True)
            stored_filename = f"{upload_id}.{extension}"
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
                    VALUES (?, 'image', ?, ?, ?, ?, ?, ?, ?, 'AVAILABLE', ?)
                    """,
                    (
                        upload_id,
                        filename,
                        stored_filename,
                        f".{extension}",
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
                    VALUES (?, ?, 'image', NULL, ?, 'SENT', ?)
                    """,
                    (message_id, sender, upload_id, _serialize_datetime(created_at)),
                )
                asset_row = connection.execute(
                    "SELECT * FROM assets WHERE id = ?", (upload_id,)
                ).fetchone()
            if asset_row is None:
                raise RuntimeError("Image asset metadata was not created")
            message = Message(
                id=message_id,
                sender=sender,
                type="image",
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
        """Soft-delete a message and delete an unreferenced image file."""

        asset_path: Path | None = None
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
                        connection.execute(
                            """
                            UPDATE assets SET status = 'DELETED', deleted_at = ?
                            WHERE id = ? AND status <> 'DELETED'
                            """,
                            (deleted_at, asset_id),
                        )
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
        if asset_path is not None and asset_path.is_file():
            try:
                asset_path.unlink()
            except OSError:
                LOGGER.exception("Unable to remove deleted asset file %s", asset_path)
        return message

    def storage_stats(self) -> StorageStats:
        total_bytes, image_bytes, asset_count, message_count = self.repository.stats()
        return StorageStats(
            total_bytes=total_bytes,
            image_bytes=image_bytes,
            asset_count=asset_count,
            message_count=message_count,
        )


asset_service = AssetService()
