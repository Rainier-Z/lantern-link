"""Message request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.asset import Asset


class MessageCreate(BaseModel):
    """Payload accepted by the message endpoint."""

    model_config = ConfigDict(extra="forbid")

    sender: Literal["pc", "iphone"]
    type: Literal["text"]
    content: str


class Message(BaseModel):
    """Stored message returned to clients."""

    model_config = ConfigDict(extra="forbid")

    id: str
    sender: Literal["pc", "iphone"]
    type: Literal["text", "image"]
    content: str | None = None
    created_at: datetime
    asset_id: str | None = None
    status: str = "SENT"
    deleted_at: datetime | None = None
    asset: Asset | None = None
