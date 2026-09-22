"""Pydantic models for persisted generic binary assets."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Asset(BaseModel):
    """Metadata returned for an asset stored outside SQLite."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["image", "file"]
    original_filename: str
    stored_filename: str
    extension: str
    mime_type: str
    size: int
    sha256: str
    relative_path: str
    status: str
    created_at: datetime
    deleted_at: datetime | None = None
    url: str | None = None


class StorageStats(BaseModel):
    """Aggregate storage counters for the local data directory."""

    total_bytes: int
    image_bytes: int
    file_bytes: int = 0
    asset_count: int
    message_count: int
