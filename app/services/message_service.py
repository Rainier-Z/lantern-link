"""Application service for validated, persisted messages."""

from __future__ import annotations

from datetime import datetime

from app.models.message import Message, MessageCreate
from app.repositories.message_repository import MessageRepository


class MessageService:
    """Coordinate message persistence without exposing SQLite to the API."""

    def __init__(self, repository: MessageRepository | None = None) -> None:
        self.repository = repository or MessageRepository()

    def create_text_message(self, payload: MessageCreate) -> Message:
        return self.repository.create(payload)

    def list_messages(
        self,
        *,
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
    ) -> list[Message]:
        return self.repository.list(limit=limit, before=before, after=after)

    def delete_message(self, message_id: str) -> Message | None:
        return self.repository.soft_delete(message_id)
