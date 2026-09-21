"""Message request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
    type: Literal["text"]
    content: str
    created_at: datetime
