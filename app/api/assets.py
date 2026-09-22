"""Authenticated image asset endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.core.security import require_bearer_token
from app.services.asset_service import AssetService, UploadError, asset_service


router = APIRouter(prefix="/api", tags=["assets"])


def _service_for(request: Request) -> AssetService:
    return getattr(request.app.state, "asset_service", asset_service)


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


@router.get("/assets/{asset_id}")
def get_asset(
    asset_id: str,
    request: Request,
    download: bool = Query(default=False),
    _: str = Depends(require_bearer_token),
) -> FileResponse:
    """Serve a stored image inline or as an attachment."""

    result = _service_for(request).get_asset_file(asset_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset, path = result
    if asset.status == "MISSING":
        raise HTTPException(status_code=410, detail="Asset file is missing")
    if asset.status == "DELETED":
        raise HTTPException(status_code=404, detail="Asset has been deleted")
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Asset file is missing")
    return FileResponse(
        path,
        media_type=asset.mime_type,
        filename=asset.original_filename if download else None,
        content_disposition_type="attachment",
    )


@router.get("/storage/stats")
def storage_stats(
    request: Request,
    _: str = Depends(require_bearer_token),
) -> dict[str, object]:
    """Return aggregate local image and message storage counters."""

    return _service_for(request).storage_stats().model_dump()
