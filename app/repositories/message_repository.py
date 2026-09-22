"""SQLite repository for text and image message metadata."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.database import DATABASE_PATH, get_connection, initialize_database
from app.models.message import Message, MessageCreate
from app.repositories.asset_repository import AssetRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        sender=row["sender"],
        type=row["type"],
        content=row["text_content"],
        created_at=_parse_datetime(row["created_at"]),
        asset_id=row["asset_id"],
        status=row["status"],
        deleted_at=_parse_datetime(row["deleted_at"]),
    )


class MessageRepository:
    """CRUD operations for persisted message records."""

    def __init__(self, database_path: Path | str | None = None) -> None:
        self.database_path = Path(database_path) if database_path else DATABASE_PATH
        initialize_database(self.database_path)

    def create(
        self,
        payload: MessageCreate,
        *,
        asset_id: str | None = None,
        message_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Message:
        message = Message(
            id=message_id or str(uuid4()),
            sender=payload.sender,
            type=payload.type,
            content=payload.content,
            created_at=created_at or _utc_now(),
            asset_id=asset_id,
        )
        with get_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO messages
                    (id, sender, type, text_content, asset_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.sender,
                    message.type,
                    message.content,
                    message.asset_id,
                    message.status,
                    _serialize_datetime(message.created_at),
                ),
            )
        return message

    def get(self, message_id: str, *, include_deleted: bool = False) -> Message | None:
        query = "SELECT * FROM messages WHERE id = ?"
        params: tuple[object, ...] = (message_id,)
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        with get_connection(self.database_path) as connection:
            row = connection.execute(query, params).fetchone()
        return _to_message(row) if row else None

    def list(
        self,
        *,
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
    ) -> list[Message]:
        if before is not None and after is not None:
            raise ValueError("before and after cannot be combined")

        cursor_id = before or after
        cursor_created_at: str | None = None
        if cursor_id is not None:
            with get_connection(self.database_path) as connection:
                cursor = connection.execute(
                    "SELECT id, created_at FROM messages WHERE id = ?", (cursor_id,)
                ).fetchone()
            if cursor is None:
                return self.list(limit=limit) if after is not None else []
            cursor_created_at = cursor["created_at"]

        clauses = ["deleted_at IS NULL"]
        params: list[object] = []
        if before is not None:
            clauses.append("(created_at < ? OR (created_at = ? AND id < ?))")
            params.extend([cursor_created_at, cursor_created_at, cursor_id])
        elif after is not None:
            clauses.append("(created_at > ? OR (created_at = ? AND id > ?))")
            params.extend([cursor_created_at, cursor_created_at, cursor_id])

        order = "ASC" if after is not None else "DESC"
        query = (
            "SELECT * FROM messages WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY created_at {order}, id {order} LIMIT ?"
        )
        params.append(max(1, limit))
        with get_connection(self.database_path) as connection:
            rows = connection.execute(query, params).fetchall()
        assets = AssetRepository(self.database_path)
        messages: list[Message] = []
        for row in rows:
            message = _to_message(row)
            asset = assets.get(message.asset_id) if message.asset_id else None
            messages.append(message.model_copy(update={"asset": asset}))
        return messages

    def soft_delete(self, message_id: str) -> Message | None:
        deleted_at = _serialize_datetime(_utc_now())
        with get_connection(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE messages
                SET deleted_at = ?, status = 'DELETED'
                WHERE id = ? AND deleted_at IS NULL
                """,
                (deleted_at, message_id),
            )
        return self.get(message_id, include_deleted=True) if cursor.rowcount else None
