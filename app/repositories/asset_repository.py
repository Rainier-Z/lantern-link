"""SQLite access for generic asset metadata."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.core.database import DATABASE_PATH, get_connection
from app.models.asset import Asset, HistoryItem


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

    def get(self, asset_id: str) -> Asset | None:
        with get_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()
        return asset_from_row(row) if row else None

    def list_available(self) -> list[Asset]:
        with get_connection(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM assets WHERE status = 'AVAILABLE' "
                "ORDER BY created_at, id"
            ).fetchall()
        return [asset_from_row(row) for row in rows]

    def list_history(
        self, *, kind: str = "all", keyword: str | None = None
    ) -> list[HistoryItem]:
        """Return active attachment messages newest first, without disk paths."""

        clauses = [
            "m.deleted_at IS NULL",
            "m.asset_id IS NOT NULL",
            "a.status NOT IN ('DELETED', 'DELETE_PENDING')",
        ]
        parameters: list[object] = []
        if kind != "all":
            clauses.append("a.kind = ?")
            parameters.append(kind)
        with get_connection(self.database_path) as connection:
            rows = connection.execute(
                """SELECT m.id AS message_id, a.id AS asset_id, a.kind,
                          a.original_filename AS filename, a.size,
                          COALESCE(m.created_at, a.created_at) AS created_at,
                          a.status
                     FROM messages AS m
                     JOIN assets AS a ON a.id = m.asset_id
                    WHERE """ + " AND ".join(clauses) +
                " ORDER BY COALESCE(m.created_at, a.created_at) DESC, m.id DESC",
                parameters,
            ).fetchall()
        if keyword:
            folded_keyword = keyword.casefold()
            rows = [
                row for row in rows
                if folded_keyword in row["filename"].casefold()
            ]
        items = []
        for row in rows:
            availability = (
                "AVAILABLE" if row["status"] == "AVAILABLE"
                else "MISSING" if row["status"] == "MISSING"
                else "PENDING"
            )
            asset_id = row["asset_id"]
            is_available = availability == "AVAILABLE"
            items.append(
                HistoryItem(
                    message_id=row["message_id"],
                    asset_id=asset_id,
                    kind=row["kind"],
                    filename=row["filename"],
                    display_name=row["filename"],
                    size=row["size"],
                    created_at=_parse_datetime(row["created_at"]),
                    asset_url=f"/api/assets/{asset_id}" if is_available else None,
                    download_url=(
                        f"/api/assets/{asset_id}?download=1" if is_available else None
                    ),
                    availability=availability,
                )
            )
        return items

    def update_storage_location(
        self,
        asset_id: str,
        expected_relative_path: str,
        stored_filename: str,
        relative_path: str,
    ) -> bool:
        with get_connection(self.database_path) as connection:
            cursor = connection.execute(
                """UPDATE assets
                      SET stored_filename = ?, relative_path = ?,
                          status = CASE
                            WHEN EXISTS (
                                SELECT 1 FROM messages
                                WHERE asset_id = assets.id AND deleted_at IS NOT NULL
                            ) AND NOT EXISTS (
                                SELECT 1 FROM messages
                                WHERE asset_id = assets.id AND deleted_at IS NULL
                            ) THEN 'DELETE_PENDING'
                            ELSE 'AVAILABLE'
                          END
                    WHERE id = ? AND status = 'ARCHIVE_PENDING' AND relative_path = ?""",
                (stored_filename, relative_path, asset_id, expected_relative_path),
            )
        return cursor.rowcount == 1

    def list_archive_pending(self) -> list[Asset]:
        with get_connection(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM assets WHERE status = 'ARCHIVE_PENDING' "
                "ORDER BY created_at, id"
            ).fetchall()
        return [asset_from_row(row) for row in rows]

    def list_delete_pending(self) -> list[Asset]:
        """Return assets whose managed-file or metadata deletion is pending.

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
