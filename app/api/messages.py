"""REST endpoints for sending and polling messages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import MESSAGE_MAX_BYTES
from app.core.security import require_bearer_token
from app.models.message import Message, MessageCreate
from app.services.message_store import message_store


router = APIRouter(prefix="/api", tags=["messages"])


@router.post("/messages")
def create_message(
    payload: MessageCreate,
    _: str = Depends(require_bearer_token),
) -> dict[str, object]:
    """Store one text message after validating its content size."""

    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Message content cannot be empty")
    if len(payload.content.encode("utf-8")) > MESSAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Message content exceeds 64 KiB")

    message = message_store.add(payload)
    return {"success": True, "message": message}


@router.get("/messages")
def list_messages(
    after: str | None = Query(default=None),
    _: str = Depends(require_bearer_token),
) -> dict[str, list[Message]]:
    """Return all messages or messages after a client's cursor."""

    messages = message_store.list() if after is None else message_store.get_after(after)
    return {"messages": messages}
