"""Authenticated image asset endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.core.security import require_bearer_token
from app.models.asset import Asset, HistoryResponse
from app.services.asset_service import AssetService, UploadError, asset_service


router = APIRouter(prefix="/api", tags=["assets"])


def _service_for(request: Request) -> AssetService:
    return getattr(request.app.state, "asset_service", asset_service)


@router.get("/history", response_model=HistoryResponse)
def list_history(
    request: Request,
    type: Literal["all", "image", "file"] = Query(
        default="all", description="Filter attachment kind: all, image, or file."
    ),
    q: str | None = Query(
        default=None,
        max_length=200,
        description="Optional case-insensitive substring of the stored filename.",
    ),
    _: str = Depends(require_bearer_token),
) -> HistoryResponse:
    """Return active attachment history newest first.

    ``type`` selects all attachment kinds or just images/files. ``q`` searches
    the filename. Each item exposes IDs, display metadata, a UTC timestamp,
    relative asset URLs only while available, and AVAILABLE/MISSING/PENDING
    availability; server filesystem paths are never part of this contract.
    """

    keyword = q.strip() if q and q.strip() else None
    repository = _service_for(request).repository
    items = repository.list_history(kind=type, keyword=keyword)
    service = _service_for(request)
    for index, item in enumerate(items):
        if item.availability != "AVAILABLE":
            continue
        asset_file = service.get_asset_file(item.asset_id)
        if asset_file is None:
            items[index] = item.model_copy(update={
                "asset_url": None,
                "download_url": None,
                "availability": "PENDING",
            })
            continue
        asset, _ = asset_file
        if asset.status != "AVAILABLE":
            items[index] = item.model_copy(update={
                "asset_url": None,
                "download_url": None,
                "availability": (
                    "MISSING" if asset.status == "MISSING" else "PENDING"
                ),
            })
    return HistoryResponse(items=items, count=len(items))


def _validate_disposition_flags(*, download: bool, preview: bool) -> None:
    if download and preview:
        raise HTTPException(
            status_code=400,
            detail="download and preview cannot be used together",
        )


def resolve_content_disposition(
    asset: Asset, *, download: bool, preview: bool
) -> str:
    """Choose whether an asset is served inline or as a download attachment."""

    _validate_disposition_flags(download=download, preview=preview)
    if download:
        return "attachment"
    if asset.kind == "file":
        if not preview:
            return "attachment"
        if (
            asset.extension.lower() == ".pdf"
            and asset.mime_type.lower() == "application/pdf"
        ):
            return "inline"
        raise HTTPException(status_code=415, detail="Asset preview is unsupported")
    return "inline"


@router.post("/assets/images")
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    sender: str = Form(...),
    _: str = Depends(require_bearer_token),
) -> dict[str, object]:
    """Stream an authenticated multipart image into persistent storage."""

    try:
        message, asset = await _service_for(request).upload_image(file, sender)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"success": True, "message": message, "asset": asset}


@router.post("/assets/files")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    sender: str = Form(...),
    _: str = Depends(require_bearer_token),
) -> dict[str, object]:
    """Stream an authenticated generic file into persistent storage."""

    try:
        message, asset = await _service_for(request).upload_file(file, sender)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"success": True, "message": message, "asset": asset}


@router.get("/assets/{asset_id}")
def get_asset(
    asset_id: str,
    request: Request,
    download: bool = Query(default=False),
    preview: bool = Query(default=False),
    _: str = Depends(require_bearer_token),
) -> FileResponse:
    """Stream an available asset with its resolved content disposition."""

    _validate_disposition_flags(download=download, preview=preview)
    result = _service_for(request).get_asset_file(asset_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset, path = result
    if asset.status == "MISSING":
        raise HTTPException(status_code=410, detail="Asset file is missing")
    if asset.status != "AVAILABLE":
        raise HTTPException(status_code=404, detail="Asset not found")
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Asset file is missing")
    disposition = resolve_content_disposition(
        asset, download=download, preview=preview
    )
    return FileResponse(
        path,
        media_type=asset.mime_type,
        filename=asset.original_filename if disposition == "attachment" else None,
        content_disposition_type=disposition,
        headers={"Content-Disposition": "inline"} if disposition == "inline" else None,
    )


@router.get("/storage/stats")
def storage_stats(
    request: Request,
    _: str = Depends(require_bearer_token),
) -> dict[str, object]:
    """Return aggregate local image and message storage counters."""

    return _service_for(request).storage_stats().model_dump()
