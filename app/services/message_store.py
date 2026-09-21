"""In-memory message store used by the MVP."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from app.models.message import Message, MessageCreate


class MessageStore:
    """Thread-safe append-only store for messages during one server run."""

    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._lock = Lock()

    def add(self, payload: MessageCreate) -> Message:
        message = Message(
            id=str(uuid4()),
            sender=payload.sender,
            type=payload.type,
            content=payload.content,
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._messages.append(message)
        return message

    def list(self) -> list[Message]:
        with self._lock:
            return list(self._messages)

    def get_after(self, message_id: str) -> list[Message]:
        with self._lock:
            for index, message in enumerate(self._messages):
                if message.id == message_id:
                    return list(self._messages[index + 1 :])
            # A stale cursor after a restart should trigger a full resync.
            return list(self._messages)

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()


message_store = MessageStore()
