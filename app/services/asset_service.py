"""Generic binary upload, storage, and asset/message lifecycle services."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import stat
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import resolve_user_files_dir
from app.core.database import DATABASE_PATH, get_connection
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


def _host_local_timezone():
    """Resolve the host-local timezone at use time for archive folders."""

    return datetime.now().astimezone().tzinfo


def archive_date_for(created_at: datetime) -> str:
    """Return the stable host-local archive date for a UTC metadata timestamp."""

    return created_at.astimezone(_host_local_timezone()).date().isoformat()


def archive_path_identity(relative_path: str) -> str:
    """Return the Windows-safe identity used to reserve archive paths."""

    normalized = relative_path.replace("\\", "/")
    return unicodedata.normalize("NFC", normalized).casefold()


def _serialize_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _normalize_archive_filename(filename: str) -> str:
    """Return one safe basename suitable for the dated user-file archive."""

    name = filename.replace("\\", "/").split("/")[-1].strip()
    name = "".join(
        "_" if ord(character) < 32 or character in '<>:"/\\|?*' else character
        for character in name
    ).rstrip(" .")
    if name in {"", ".", ".."}:
        name = "upload"
    device_name = name.split(".", maxsplit=1)[0].casefold()
    if device_name in {"con", "prn", "aux", "nul"} or (
        len(device_name) == 4
        and device_name[:3] in {"com", "lpt"}
        and device_name[3] in "123456789"
    ):
        name = f"_{name}"
    return name


class AssetService:
    """Own filesystem and transaction boundaries for generic assets."""

    def __init__(
        self,
        database_path: Path | str | None = None,
        *,
        staging_dir: Path | str | None = None,
        user_files_dir: Path | str | None = None,
        cleanup_legacy_assets: bool = False,
        import_legacy_on_start: bool = True,
    ) -> None:
        self.database_path = Path(database_path) if database_path else DATABASE_PATH
        self.data_dir = self.database_path.parent
        self.staging_dir = Path(staging_dir) if staging_dir else self.data_dir / "staging"
        self.user_files_dir = (
            Path(user_files_dir)
            if user_files_dir
            else resolve_user_files_dir() if database_path is None else self.data_dir
        )
        self.assets_dir = self.user_files_dir / "assets"
        self.cleanup_legacy_assets = cleanup_legacy_assets
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.repository = AssetRepository(self.database_path)
        if database_path is not None:
            self.recover_archive_pending()
            if import_legacy_on_start:
                self.import_legacy_assets()

    def _archive_directory(self, created_at: datetime) -> Path:
        date_directory = self.user_files_dir / archive_date_for(created_at)
        date_directory.mkdir(parents=True, exist_ok=True)
        user_files_root = self.user_files_dir.resolve()
        if (
            date_directory.is_symlink()
            or date_directory.resolve() == user_files_root
            or user_files_root not in date_directory.resolve().parents
        ):
            raise RuntimeError("Archive date directory escapes the user-files root")
        return date_directory

    @staticmethod
    def _sync_directory(directory: Path) -> bool:
        # Windows 不支持通过 Python 的目录 fd 做 fsync；仍同步文件内容，
        # 并把 staging 保留到 AVAILABLE 提交。此回退不承诺突然断电时目录项持久化。
        if os.name == "nt":
            return False
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return True

    def _safe_user_file_path(self, relative_path: str) -> Path:
        """Resolve a database path below the configured user-files root."""

        normalized = relative_path.replace("\\", "/")
        candidate = (self.user_files_dir / normalized).resolve()
        root = self.user_files_dir.resolve()
        if candidate == root or root not in candidate.parents:
            raise RuntimeError("Asset path escapes the configured user-files root")
        return candidate

    def _archive_temporary_path(
        self,
        asset_id: str,
        relative_path: str | datetime,
        filename: str | None = None,
    ) -> Path:
        """Return the operation-owned temporary beside its committed target."""

        if filename is not None:
            if not isinstance(relative_path, datetime):
                raise TypeError("Legacy temporary path requires a creation time")
            relative_path = (Path(archive_date_for(relative_path)) / filename).as_posix()
        if not isinstance(relative_path, str):
            raise TypeError("Archive temporary path requires a relative path")
        destination = self._safe_user_file_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        root = self.user_files_dir.resolve()
        if (
            destination.parent.is_symlink()
            or destination.parent.resolve() == root
            or root not in destination.parent.resolve().parents
        ):
            raise RuntimeError("Archive date directory escapes the user-files root")
        operation = f"{asset_id}\n{relative_path}"
        key = hashlib.sha256(operation.encode("utf-8")).hexdigest()
        return destination.parent / f".private_send_{key}.partial"

    @staticmethod
    def _remove_archive_temporary(path: Path) -> None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISREG(mode):
            raise RuntimeError("Pending archive temporary path is not a regular file")
        path.unlink()

    def _reserve_archive_filename(
        self, connection: sqlite3.Connection, created_at: datetime, filename: str
    ) -> str:
        """Reserve a destination while the caller holds BEGIN IMMEDIATE."""

        directory = self._archive_directory(created_at)
        reserved_path_keys = {
            archive_path_identity(row["relative_path"])
            for row in connection.execute("SELECT relative_path FROM assets")
        }
        filename_path = Path(filename)
        collision_number = 0
        while True:
            candidate = filename if collision_number == 0 else (
                f"{filename_path.stem} ({collision_number}){filename_path.suffix}"
            )
            destination = directory / candidate
            relative_path = (Path(directory.name) / candidate).as_posix()
            relative_path_key = archive_path_identity(relative_path)
            if not (
                destination.exists() or destination.is_symlink()
                or relative_path_key in reserved_path_keys
            ):
                return candidate
            collision_number += 1

    @staticmethod
    def _file_digest(path: Path) -> str:
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()

    def _publish_archive_temp(self, temporary_path: Path, destination: Path) -> None:
        """Publish a complete temporary copy without replacing user files."""

        try:
            if os.name == "nt":
                os.rename(temporary_path, destination)
            else:
                os.link(temporary_path, destination)
        except FileExistsError:
            return
        except OSError:
            if os.name != "nt":
                raise
            destination_created = False
            try:
                with temporary_path.open("rb") as source, destination.open("xb") as target:
                    destination_created = True
                    shutil.copyfileobj(source, target, CHUNK_SIZE)
                    target.flush()
                    os.fsync(target.fileno())
            except FileExistsError:
                return
            except BaseException:
                if destination_created:
                    destination.unlink(missing_ok=True)
                raise

    def _place_in_archive(
        self, source_path: Path, relative_path: str, asset_id: str
    ) -> Path:
        """Publish only the committed reservation; retries reuse the same file."""

        destination = self._safe_user_file_path(relative_path)
        if _normalize_archive_filename(destination.name) != destination.name:
            raise RuntimeError("Invalid archive filename")
        destination.parent.mkdir(parents=True, exist_ok=True)
        date_directory = destination.parent
        temporary_path = self._archive_temporary_path(asset_id, relative_path)
        if destination.exists() or destination.is_symlink():
            if (
                destination.is_symlink() or not destination.is_file()
                or self._file_digest(destination) != self._file_digest(source_path)
            ):
                raise RuntimeError("Reserved archive destination has different content")
            self._sync_directory(date_directory)
            self._remove_archive_temporary(temporary_path)
            self._sync_directory(date_directory)
            return destination

        if temporary_path.exists() or temporary_path.is_symlink():
            if temporary_path.is_symlink() or not temporary_path.is_file():
                raise RuntimeError("Pending archive temporary path is not a regular file")
            if self._file_digest(temporary_path) != self._file_digest(source_path):
                # 只清理由这条 pending 记录确定的临时副本，完整源文件仍可重试。
                self._remove_archive_temporary(temporary_path)
        if not temporary_path.exists():
            with temporary_path.open("xb") as temporary:
                with source_path.open("rb") as source:
                    shutil.copyfileobj(source, temporary, CHUNK_SIZE)
                temporary.flush()
        with temporary_path.open("r+b") as temporary:
            os.fsync(temporary.fileno())
        self._sync_directory(self.user_files_dir)
        self._publish_archive_temp(temporary_path, destination)
        if (
            destination.is_symlink() or not destination.is_file()
            or self._file_digest(destination) != self._file_digest(source_path)
        ):
            raise RuntimeError("Reserved archive destination has different content")
        self._sync_directory(date_directory)
        self._remove_archive_temporary(temporary_path)
        self._sync_directory(date_directory)
        return destination

    @staticmethod
    def _is_legacy_asset_path(relative_path: str) -> bool:
        parts = relative_path.replace("\\", "/").split("/")
        return (
            len(parts) == 5
            and parts[0] == "assets"
            and len(parts[1]) == 4
            and parts[1].isdigit()
            and len(parts[2]) == 2
            and parts[2].isdigit()
            and len(parts[3]) == 2
            and parts[3].isdigit()
            and bool(parts[4])
            and parts[4] not in {".", ".."}
        )

    def import_legacy_assets(self) -> list[str]:
        """Copy legacy date-partitioned assets into the formal archive."""

        imported: list[str] = []
        for asset in self.repository.list_available():
            if not self._is_legacy_asset_path(asset.relative_path):
                continue
            try:
                source_path = self.resolve_asset_path(asset)
                if not source_path.is_file():
                    continue
                with get_connection(self.database_path) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    filename = self._reserve_archive_filename(
                        connection, asset.created_at,
                        _normalize_archive_filename(asset.original_filename),
                    )
                    cursor = connection.execute(
                        "UPDATE assets SET status = 'ARCHIVE_PENDING', stored_filename = ? "
                        "WHERE id = ? AND status = 'AVAILABLE' AND relative_path = ?",
                        (filename, asset.id, asset.relative_path),
                    )
                if cursor.rowcount != 1:
                    continue
                pending = asset.model_copy(update={
                    "stored_filename": filename, "status": "ARCHIVE_PENDING",
                })
                if self._complete_archive(pending):
                    imported.append(asset.id)
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                LOGGER.warning(
                    "Unable to import legacy asset; will retry on next startup "
                    "(asset_id=%s, error_type=%s)",
                    asset.id,
                    type(exc).__name__,
                )
        return imported

    def has_legacy_asset_work(self) -> bool:
        """Report whether a legacy migration still has recoverable asset work."""

        return any(
            self._is_legacy_asset_path(asset.relative_path)
            for asset in [
                *self.repository.list_available(),
                *self.repository.list_archive_pending(),
            ]
        )

    def _remove_owned_legacy_source(self, source_path: Path) -> None:
        """Remove one migrated compatibility copy, never the project source."""

        if not self.cleanup_legacy_assets:
            return
        managed_root = self.assets_dir.resolve()
        if (
            self.assets_dir.is_symlink()
            or managed_root != self.user_files_dir.resolve() / "assets"
            or managed_root not in source_path.resolve().parents
        ):
            raise RuntimeError("Legacy compatibility path escapes its storage root")
        source_path.unlink(missing_ok=True)
        directory = source_path.parent
        while directory != managed_root:
            try:
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent

    def _complete_archive(self, asset: Asset) -> bool:
        if _normalize_archive_filename(asset.stored_filename) != asset.stored_filename:
            raise RuntimeError("Invalid archive filename")
        legacy = self._is_legacy_asset_path(asset.relative_path)
        source_path = (
            self.resolve_asset_path(asset) if legacy
            else self.staging_dir / f"{asset.id}.partial"
        )
        target_relative_path = (
            (Path(archive_date_for(asset.created_at)) / asset.stored_filename).as_posix()
            if legacy
            else asset.relative_path
        )
        destination = self._safe_user_file_path(target_relative_path)
        if source_path.is_file():
            if not legacy and (
                source_path.stat().st_size != asset.size
                or self._file_digest(source_path) != asset.sha256
            ):
                raise RuntimeError("Pending upload content does not match its metadata")
            destination = self._place_in_archive(
                source_path, target_relative_path, asset.id
            )
        else:
            if (
                destination.is_symlink() or not destination.is_file()
                or destination.stat().st_size != asset.size
                or self._file_digest(destination) != asset.sha256
            ):
                raise RuntimeError("Pending archive has no complete recoverable copy")
            self._sync_directory(destination.parent)
            self._remove_archive_temporary(
                self._archive_temporary_path(asset.id, target_relative_path)
            )
            self._sync_directory(destination.parent)
        completed = self.repository.update_storage_location(
            asset.id, asset.relative_path, destination.name,
            target_relative_path,
        )
        if completed:
            if legacy:
                self._remove_owned_legacy_source(source_path)
            else:
                source_path.unlink(missing_ok=True)
        if completed:
            archived = self.repository.get(asset.id)
            if archived is not None and archived.status == "DELETE_PENDING":
                self._complete_delete(archived)
        return completed

    def recover_archive_pending(self) -> list[str]:
        """Finish committed archive intents without removing formal user files."""

        completed: list[str] = []
        for asset in self.repository.list_archive_pending():
            try:
                if self._complete_archive(asset):
                    completed.append(asset.id)
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                LOGGER.warning(
                    "Unable to complete pending archive; will retry on next startup "
                    "(asset_id=%s, error_type=%s)", asset.id, type(exc).__name__,
                )
        return completed

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
        pending_uploads = {
            f"{asset.id}.partial" for asset in self.repository.list_archive_pending()
            if not self._is_legacy_asset_path(asset.relative_path)
        }
        for path in sorted(self.staging_dir.glob("*.partial")):
            if path.name in pending_uploads:
                retained.append(path)
                continue
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
        """Retry managed-file deletion while retaining formal date archives."""

        completed: list[str] = []
        for asset in self.repository.list_delete_pending():
            if self._complete_delete(asset):
                completed.append(asset.id)
        return completed

    def _complete_delete(self, asset: Asset) -> bool:
        try:
            if self._is_legacy_asset_path(asset.relative_path):
                path = self.user_files_dir.joinpath(
                    *asset.relative_path.replace("\\", "/").split("/")
                )
                managed_root = self.assets_dir.resolve()
                if (
                    self.assets_dir.is_symlink()
                    or managed_root != self.user_files_dir.resolve() / "assets"
                    or managed_root not in path.resolve().parents
                ):
                    raise RuntimeError("Managed asset path escapes its storage root")
                path.unlink(missing_ok=True)
            return self.repository.mark_deleted(asset.id, _serialize_datetime(_utc_now()))
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            LOGGER.warning(
                "Unable to complete pending asset deletion; will retry on startup "
                "(asset_id=%s, error_type=%s)", asset.id, type(exc).__name__,
            )
            return False

    async def persist_upload(
        self,
        file: UploadFile,
        sender: str,
        policy: UploadPolicy,
    ) -> tuple[Message, Asset]:
        """Stream one upload into generic asset storage atomically."""

        upload_id = str(uuid4())
        staging_path = self.staging_dir / f"{upload_id}.partial"
        preserve_staging = False
        try:
            sender = sender.strip()
            if sender not in {"pc", "iphone"}:
                raise UploadError(422, "sender must be pc or iphone")

            filename = (file.filename or "").strip()
            if not filename:
                raise UploadError(400, "A file field is required")
            safe_filename = _normalize_archive_filename(filename)
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
                output.flush()
                os.fsync(output.fileno())
            if size <= 0:
                raise UploadError(400, "Upload cannot be empty")
            self._sync_directory(self.staging_dir)
            sha256 = digest.hexdigest()
            filename = safe_filename
            created_at = _utc_now()
            message_id = str(uuid4())
            preserve_staging = True
            with get_connection(self.database_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                stored_filename = self._reserve_archive_filename(
                    connection, created_at, filename
                )
                relative_path = (
                    Path(archive_date_for(created_at)) / stored_filename
                ).as_posix()
                connection.execute(
                    """
                    INSERT INTO assets
                        (id, kind, original_filename, stored_filename, extension,
                         mime_type, size, sha256, relative_path, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ARCHIVE_PENDING', ?)
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
            if not self._complete_archive(asset_from_row(asset_row)):
                raise RuntimeError("Asset archive metadata remains pending")
            message = Message(
                id=message_id,
                sender=sender,
                type=policy.kind,
                content=None,
                created_at=created_at,
                asset_id=upload_id,
                status="SENT",
            )
            asset = asset_from_row(asset_row, url=f"/api/assets/{upload_id}")
            return message, asset.model_copy(update={"status": "AVAILABLE"})
        except Exception:
            if not preserve_staging and staging_path.is_file():
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

    async def upload(self, file: UploadFile, sender: str) -> tuple[Message, Asset]:
        """Persist an upload using the server-owned canonical classification."""

        filename = _normalize_archive_filename((file.filename or "").strip())
        extension = Path(filename).suffix.lower()
        policy = IMAGE_POLICY if extension in ALLOWED_EXTENSIONS else FILE_POLICY
        return await self.persist_upload(file, sender, policy)

    def resolve_asset_path(self, asset: Asset) -> Path:
        """Resolve an internally stored relative path below the user-files root."""

        return self._safe_user_file_path(asset.relative_path)

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
        """Delete unreferenced managed files, retaining formal archive files."""

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
                        if asset_row["status"] == "ARCHIVE_PENDING":
                            asset_id_to_delete = asset_id
                        else:
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
        if asset_id_to_delete is not None:
            asset = self.repository.get(asset_id_to_delete)
            if asset is not None:
                if asset.status == "ARCHIVE_PENDING":
                    try:
                        self._complete_archive(asset)
                    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                        LOGGER.warning(
                            "Deleted message retains its pending archive for recovery "
                            "(asset_id=%s, error_type=%s)",
                            asset.id, type(exc).__name__,
                        )
                else:
                    self._complete_delete(asset)
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
