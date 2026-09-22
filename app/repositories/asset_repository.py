"""SQLite access for generic asset metadata."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.core.database import DATABASE_PATH, get_connection, initialize_database
from app.models.asset import Asset


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def asset_from_row(row: sqlite3.Row, *, url: str | None = None) -> Asset:
    """Map a SQLite row to its API model."""

    return Asset(
        id=row["id"],
        kind=row["kind"],
        original_filename=row["original_filename"],
        stored_filename=row["stored_filename"],
        extension=row["extension"],
        mime_type=row["mime_type"],
        size=row["size"],
        sha256=row["sha256"],
        relative_path=row["relative_path"],
        status=row["status"],
        created_at=_parse_datetime(row["created_at"]),
        deleted_at=_parse_datetime(row["deleted_at"]),
        url=url,
    )


class AssetRepository:
    """Read-only and metadata helpers for image and file assets."""

    def __init__(self, database_path: Path | str | None = None) -> None:
        self.database_path = Path(database_path) if database_path else DATABASE_PATH
        initialize_database(self.database_path)
        self._ensure_file_message_type()

    def _ensure_file_message_type(self) -> None:
        """Migrate the v0.2 message check constraint to include generic files.

        SQLite cannot alter a CHECK constraint in place.  This idempotent
        migration preserves every existing message and index while allowing
        the v0.3 ``file`` message type.  It is kept here so callers that
        instantiate the asset repository get a compatible schema without a
        second migration framework.
        """

        with get_connection(self.database_path) as connection:
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
            ).fetchone()
            sql = (schema[0] or "").lower() if schema else ""
            if "'file'" in sql or '"file"' in sql:
                return
            connection.execute("PRAGMA foreign_keys = OFF")
            try:
                connection.execute("ALTER TABLE messages RENAME TO messages_v02")
                connection.execute(
                    """
                    CREATE TABLE messages (
                        id TEXT PRIMARY KEY,
                        sender TEXT NOT NULL CHECK (sender IN ('pc', 'iphone')),
                        type TEXT NOT NULL CHECK (type IN ('text', 'image', 'file')),
                        text_content TEXT,
                        asset_id TEXT REFERENCES assets(id),
                        status TEXT NOT NULL DEFAULT 'SENT',
                        created_at TEXT NOT NULL,
                        deleted_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO messages
                        (id, sender, type, text_content, asset_id, status,
                         created_at, deleted_at)
                    SELECT id, sender, type, text_content, asset_id, status,
                           created_at, deleted_at
                      FROM messages_v02
                    """
                )
                connection.execute("DROP TABLE messages_v02")
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_messages_created_at
                        ON messages (created_at DESC, id DESC)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_messages_asset_id
                        ON messages (asset_id)
                    """
                )
            finally:
                connection.execute("PRAGMA foreign_keys = ON")

    def get(self, asset_id: str) -> Asset | None:
        with get_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()
        return asset_from_row(row) if row else None

    def list_delete_pending(self) -> list[Asset]:
        """Return assets whose physical deletion still needs to be completed.

        Recovery is deliberately scoped to ``DELETE_PENDING`` rows.  In
        particular, startup must never reinterpret an ``AVAILABLE`` or
        ``MISSING`` asset as a deletion candidate.
        """

        with get_connection(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM assets WHERE status = 'DELETE_PENDING' "
                "ORDER BY created_at, id"
            ).fetchall()
        return [asset_from_row(row) for row in rows]

    def mark_deleted(self, asset_id: str, deleted_at: str) -> bool:
        """Complete a pending deletion, without changing other asset states."""

        with get_connection(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE assets
                   SET status = 'DELETED', deleted_at = ?
                 WHERE id = ? AND status = 'DELETE_PENDING'
                """,
                (deleted_at, asset_id),
            )
        return cursor.rowcount == 1

    def stats(self) -> tuple[int, int, int, int, int]:
        with get_connection(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN status = 'AVAILABLE' THEN size ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN status = 'AVAILABLE' AND kind = 'image' THEN size ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN status = 'AVAILABLE' AND kind = 'file' THEN size ELSE 0 END), 0),
                    COUNT(CASE WHEN status = 'AVAILABLE' THEN 1 END),
                    (SELECT COUNT(*) FROM messages WHERE deleted_at IS NULL)
                FROM assets
                """
            ).fetchone()
        total_bytes, image_bytes, file_bytes, asset_count, message_count = (
            int(value) for value in row
        )
        return total_bytes, image_bytes, file_bytes, asset_count, message_count
