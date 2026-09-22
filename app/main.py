"""FastAPI application and local server entry point."""

from __future__ import annotations

import webbrowser
from contextlib import suppress
from io import BytesIO
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.api.messages import router as messages_router
from app.api.assets import router as assets_router
from app.core.database import initialize_database
from app.core.config import HOST, PORT, WEB_DIR
from app.core.network import build_service_url, get_lan_ip
from app.core.security import get_access_token, is_valid_token, require_bearer_token
from app.repositories.message_repository import MessageRepository
from app.services.message_service import MessageService, message_service
from app.services.asset_service import AssetService, asset_service


initialize_database()

app = FastAPI(title="Rainier Link", version="0.2.0")
app.state.token = get_access_token()
app.state.message_service = message_service
app.state.asset_service = asset_service
app.include_router(messages_router)
app.include_router(assets_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Return a lightweight service liveness response."""

    return {"status": "ok", "service": "rainier-link"}


@app.get("/api/pairing")
def pairing(_: str = Depends(require_bearer_token)) -> dict[str, str]:
    """Return the authenticated pairing URL and QR image endpoint."""

    return {
        "pairing_url": build_service_url(get_lan_ip(), PORT, get_access_token()),
        "qr_image_url": "/api/pairing/qr",
    }


@app.get("/api/pairing/qr", include_in_schema=False)
def pairing_qr(_: str = Depends(require_bearer_token)) -> Response:
    """Return the authenticated pairing QR image."""

    return Response(content=generate_pairing_qr(), media_type="image/png")


@app.get("/", include_in_schema=False)
def root(request: Request, token: str | None = None):
    """Serve the paired web client without exposing the token to LAN visitors."""

    if token is None:
        client_host = request.client.host if request.client else ""
        if client_host in {"127.0.0.1", "::1"}:
            return RedirectResponse(
                url=build_service_url("127.0.0.1", PORT, get_access_token()),
                status_code=307,
            )
        return JSONResponse(
            {"detail": "Pairing token required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not is_valid_token(token):
        return JSONResponse(
            {"detail": "Invalid pairing token"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    index_path = WEB_DIR / "index.html"
    if index_path.is_file():
        return Response(content=index_path.read_bytes(), media_type="text/html")
    return {"service": "rainier-link", "status": "online"}


if WEB_DIR.is_dir():
    # Keep this conditional so the backend remains importable before the UI exists.
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=WEB_DIR), name="web")


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    """Build an application bound to a selected data directory.

    The factory is used by tests and future embedded instances so each app can
    use an isolated SQLite file without changing the module-level server app.
    """

    if data_dir is None:
        service = message_service
    else:
        database_path = Path(data_dir) / "rainier.db"
        service = MessageService(MessageRepository(database_path))
    asset_bound_service = asset_service if data_dir is None else AssetService(database_path)

    application = FastAPI(title="Rainier Link", version="0.2.0")
    application.state.token = get_access_token()
    application.state.message_service = service
    application.state.asset_service = asset_bound_service
    application.include_router(messages_router)
    application.include_router(assets_router)
    application.add_api_route("/api/health", health, methods=["GET"])
    application.add_api_route(
        "/api/pairing",
        pairing,
        methods=["GET"],
    )
    application.add_api_route(
        "/api/pairing/qr",
        pairing_qr,
        methods=["GET"],
        include_in_schema=False,
    )
    application.add_api_route("/", root, methods=["GET"], include_in_schema=False)
    if WEB_DIR.is_dir():
        application.mount("/", StaticFiles(directory=WEB_DIR), name="web")
    return application


def generate_pairing_qr() -> bytes:
    """Generate pairing QR PNG bytes without writing the token to disk."""

    import qrcode

    pairing_url = build_service_url(get_lan_ip(), PORT, get_access_token())
    image = qrcode.make(pairing_url)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def start_server() -> None:
    """Prepare pairing assets, open the local UI, and serve on the LAN."""

    import uvicorn

    generate_pairing_qr()
    local_url = build_service_url("127.0.0.1", PORT, get_access_token())
    with suppress(Exception):
        webbrowser.open(local_url)
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    start_server()
