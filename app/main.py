"""FastAPI application and local server entry point."""

from __future__ import annotations

import webbrowser
from contextlib import suppress
from io import BytesIO

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.api.messages import router as messages_router
from app.core.config import HOST, PORT, WEB_DIR
from app.core.network import build_service_url, get_lan_ip
from app.core.security import get_access_token, is_valid_token, require_bearer_token


app = FastAPI(title="Rainier Link", version="0.1.0")
app.state.token = get_access_token()
app.include_router(messages_router)


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
