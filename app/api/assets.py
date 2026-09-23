"""Authenticated image asset endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.core.security import require_bearer_token
from app.models.asset import Asset
from app.services.asset_service import AssetService, UploadError, asset_service


router = APIRouter(prefix="/api", tags=["assets"])


def _service_for(request: Request) -> AssetService:
    return getattr(request.app.state, "asset_service", asset_service)


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
