"""REST endpoints for sending and polling messages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.config import MESSAGE_MAX_BYTES
from app.core.security import require_bearer_token
from app.models.message import Message, MessageCreate
from app.services.asset_service import AssetService, asset_service
from app.services.message_service import message_service


router = APIRouter(prefix="/api", tags=["messages"])


def _service_for(request: Request):
    """Use the service bound to the application, with the default as fallback."""

    return getattr(request.app.state, "message_service", message_service)


def _asset_service_for(request: Request) -> AssetService:
    return getattr(request.app.state, "asset_service", asset_service)


@router.post("/messages")
def create_message(
    payload: MessageCreate,
    request: Request,
    _: str = Depends(require_bearer_token),
) -> dict[str, object]:
    """Store one text message after validating its content size."""

    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Message content cannot be empty")
    if len(payload.content.encode("utf-8")) > MESSAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Message content exceeds 64 KiB")

    message = _service_for(request).create_text_message(payload)
    return {"success": True, "message": message}


@router.get("/messages")
def list_messages(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    before: str | None = Query(default=None),
    after: str | None = Query(default=None),
    _: str = Depends(require_bearer_token),
) -> dict[str, list[Message]]:
    """Return all messages or messages after a client's cursor."""

    if before is not None and after is not None:
        raise HTTPException(status_code=400, detail="before and after cannot be combined")
    messages = _service_for(request).list_messages(
        limit=limit, before=before, after=after
    )
    return {"messages": messages}


@router.delete("/messages/{message_id}")
def delete_message(
    message_id: str,
    request: Request,
    _: str = Depends(require_bearer_token),
) -> dict[str, object]:
    """Soft-delete a message and release an unreferenced image asset."""

    message = _asset_service_for(request).delete_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"success": True, "message": message}
