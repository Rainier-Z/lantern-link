"""SQLite access for image asset metadata."""

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
    """Read-only and metadata helpers for image assets."""

    def __init__(self, database_path: Path | str | None = None) -> None:
        self.database_path = Path(database_path) if database_path else DATABASE_PATH
        initialize_database(self.database_path)

    def get(self, asset_id: str) -> Asset | None:
        with get_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()
        return asset_from_row(row) if row else None

    def stats(self) -> tuple[int, int, int, int]:
        with get_connection(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN status = 'AVAILABLE' THEN size ELSE 0 END), 0),
                    COUNT(CASE WHEN status = 'AVAILABLE' THEN 1 END),
                    (SELECT COUNT(*) FROM messages WHERE deleted_at IS NULL)
                FROM assets
                """
            ).fetchone()
        total_bytes, asset_count, message_count = (int(value) for value in row)
        return total_bytes, total_bytes, asset_count, message_count
