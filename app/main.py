"""FastAPI application and local server entry point."""

from __future__ import annotations

import logging
import os
import tempfile
import webbrowser
import shutil
from contextlib import asynccontextmanager, suppress
from io import BytesIO
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.messages import router as messages_router
from app.api.assets import router as assets_router
from app.core.config import (
    HOST,
    LEGACY_DATABASE_NAME,
    PROJECT_ROOT,
    PORT,
    WEB_DIR,
    resolve_app_data_dir,
    resolve_database_path,
    resolve_user_files_dir,
)
from app.core.database import initialize_database
from app.core.network import build_service_url, get_lan_ip
from app.core.security import (
    SESSION_COOKIE_NAME,
    get_access_token,
    is_valid_token,
    require_bearer_token,
)
from app.core.version import APP_BUILD, APP_VERSION
from app.repositories.message_repository import MessageRepository
from app.services.message_service import MessageService
from app.services.asset_service import AssetService


LOGGER = logging.getLogger(__name__)

STATIC_NO_CACHE_PATHS = {"/", "/app.js", "/style.css"}
LEGACY_ASSETS_CLEANUP_MARKER = ".legacy_assets_copy_owned"
LEGACY_MIGRATION_DIRECTORY = "migration"
LEGACY_MIGRATION_STARTED = "legacy_v1_started"
LEGACY_MIGRATION_COMPLETED = "legacy_v1_completed"


def configure_cache_policy(application: FastAPI) -> None:
    """Disable browser persistence for the small, version-sensitive web shell."""

    @application.middleware("http")
    async def cache_policy(request: Request, call_next):
        response = await call_next(request)
        if request.url.path in STATIC_NO_CACHE_PATHS:
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


def health() -> dict[str, str]:
    """Return a lightweight service liveness response."""

    return {"status": "ok", "service": "private_send"}


def version() -> dict[str, str]:
    """Return public runtime version metadata without exposing private state."""

    return {"version": APP_VERSION, "build": APP_BUILD}


def pairing(_: str = Depends(require_bearer_token)) -> dict[str, str]:
    """Return the authenticated pairing URL and QR image endpoint."""

    return {
        "pairing_url": build_service_url(get_lan_ip(), PORT, get_access_token()),
        "qr_image_url": "/api/pairing/qr",
    }


def pairing_qr(_: str = Depends(require_bearer_token)) -> Response:
    """Return the authenticated pairing QR image."""

    return Response(content=generate_pairing_qr(), media_type="image/png")


def root(request: Request, token: str | None = None):
    """Establish a browser session, then serve the paired web client."""

    if token is not None:
        if not is_valid_token(token):
            return JSONResponse(
                {"detail": "Invalid pairing token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            path="/",
            secure=False,
            httponly=True,
            samesite="strict",
        )
        return response

    if not is_valid_token(request.cookies.get(SESSION_COOKIE_NAME)):
        return JSONResponse(
            {"detail": "Pairing session required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    index_path = WEB_DIR / "index.html"
    if index_path.is_file():
        return Response(content=index_path.read_bytes(), media_type="text/html")
    return {"service": "private_send", "status": "online"}


def bootstrap_app(
    app_data_dir: str | Path | None = None,
    user_files_dir: str | Path | None = None,
    legacy_data_dir: str | Path | None = None,
) -> tuple[Path, Path, AssetService, MessageService]:
    """Select paths, safely copy legacy data, initialize storage, and recover."""

    app_data_path = (
        Path(app_data_dir) if app_data_dir is not None else resolve_app_data_dir()
    )
    files_path = (
        Path(user_files_dir)
        if user_files_dir is not None
        else resolve_user_files_dir() if app_data_dir is None else app_data_path
    )
    source_path = (
        Path(legacy_data_dir)
        if legacy_data_dir is not None
        else PROJECT_ROOT / "data" if app_data_dir is None else app_data_path
    )
    database_path = resolve_database_path(app_data_path)

    cleanup_legacy_assets = False
    cleanup_marker = app_data_path / LEGACY_ASSETS_CLEANUP_MARKER
    migration_directory = app_data_path / LEGACY_MIGRATION_DIRECTORY
    started_marker = migration_directory / LEGACY_MIGRATION_STARTED
    completed_marker = migration_directory / LEGACY_MIGRATION_COMPLETED
    legacy_private_database = source_path / "private_send.db"
    legacy_rainier_database = source_path / LEGACY_DATABASE_NAME
    source_is_external = source_path.resolve() != app_data_path.resolve()
    has_legacy_database = source_is_external and (
        legacy_private_database.is_file() or legacy_rainier_database.is_file()
    )
    migration_started = started_marker.is_file()
    migration_completed = completed_marker.is_file()
    migration_active = has_legacy_database and not migration_completed

    if migration_active and database_path.exists() and not migration_started:
        LOGGER.warning(
            "Current private_send.db exists with an unstarted legacy source; "
            "retaining the current database without merging legacy data"
        )
        migration_directory.mkdir(parents=True, exist_ok=True)
        completed_marker.touch(exist_ok=True)
        migration_active = False

    if migration_active:
        app_data_path.mkdir(parents=True, exist_ok=True)
        migration_directory.mkdir(parents=True, exist_ok=True)
        started_marker.touch(exist_ok=True)
        _copy_missing_tree(
            source_path,
            app_data_path,
            ignored_names={"private_send.db", LEGACY_DATABASE_NAME, "assets"},
        )
        source_database = legacy_private_database
        if not source_database.is_file():
            source_database = legacy_rainier_database
        if source_database.is_file() and not database_path.exists():
            _copy_database_if_absent(source_database, database_path)
        legacy_assets = source_path / "assets"
        target_assets = files_path / "assets"
        protected_project_assets = (PROJECT_ROOT / "data" / "assets").resolve()
        target_is_protected = target_assets.resolve() == protected_project_assets
        if target_is_protected:
            cleanup_marker.unlink(missing_ok=True)
        if (
            legacy_assets.is_dir()
            and legacy_assets.resolve() != target_assets.resolve()
        ):
            target_assets_was_absent = not target_assets.exists()
            if target_assets_was_absent and not target_is_protected:
                app_data_path.mkdir(parents=True, exist_ok=True)
                cleanup_marker.touch(exist_ok=True)
            _copy_missing_tree(legacy_assets, target_assets)

    target_assets = files_path / "assets"
    protected_project_assets = (PROJECT_ROOT / "data" / "assets").resolve()
    cleanup_legacy_assets = (
        cleanup_marker.is_file()
        and target_assets.resolve() != protected_project_assets
    )

    app_data_path.mkdir(parents=True, exist_ok=True)
    files_path.mkdir(parents=True, exist_ok=True)
    initialize_database(database_path)
    service = AssetService(
        database_path,
        staging_dir=app_data_path / "staging",
        user_files_dir=files_path,
        cleanup_legacy_assets=cleanup_legacy_assets,
        import_legacy_on_start=migration_active,
    )
    service.recover_delete_pending()
    if cleanup_legacy_assets:
        try:
            service.assets_dir.rmdir()
        except OSError:
            pass
        else:
            cleanup_marker.unlink(missing_ok=True)
    service.scan_staging()
    if migration_active and not service.has_legacy_asset_work():
        completed_marker.touch(exist_ok=True)
    message_service = MessageService(MessageRepository(database_path))
    return database_path, files_path, service, message_service


def _copy_missing_tree(
    source: Path,
    destination: Path,
    *,
    ignored_names: set[str] | None = None,
) -> None:
    """Copy missing files from a tree without replacing destination paths."""

    ignored = ignored_names or set()
    for current, directory_names, file_names in os.walk(source):
        current_path = Path(current)
        relative_path = current_path.relative_to(source)
        if relative_path == Path("."):
            directory_names[:] = [name for name in directory_names if name not in ignored]
        target_directory = destination / relative_path
        if target_directory.is_symlink() or (
            target_directory.exists() and not target_directory.is_dir()
        ):
            directory_names.clear()
            continue
        target_directory.mkdir(parents=True, exist_ok=True)

        for file_name in file_names:
            if relative_path == Path(".") and file_name in ignored:
                continue
            source_file = current_path / file_name
            target_file = target_directory / file_name
            if target_file.exists() or target_file.is_symlink():
                continue
            try:
                with source_file.open("rb") as source_stream:
                    with target_file.open("xb") as target_stream:
                        shutil.copyfileobj(source_stream, target_stream)
            except FileExistsError:
                continue


def _copy_database_if_absent(source: Path, destination: Path) -> bool:
    """Copy a legacy database without replacing a database created meanwhile."""

    if destination.exists():
        return False
    with tempfile.NamedTemporaryFile(
        prefix="private_send_migration_", suffix=".tmp", dir=destination.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        shutil.copy2(source, temporary_path)
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            return False
        return True
    finally:
        temporary_path.unlink(missing_ok=True)


def create_app(
    data_dir: str | Path | None = None,
    *,
    user_files_dir: str | Path | None = None,
    legacy_data_dir: str | Path | None = None,
) -> FastAPI:
    """Build an application bound to selected data directories.

    The factory deliberately performs no storage work.  Lifespan startup owns
    bootstrapping so importing ``app.main`` cannot create user directories or
    open a database.
    """

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        _, _, asset_bound_service, message_bound_service = bootstrap_app(
            app_data_dir=data_dir,
            user_files_dir=user_files_dir,
            legacy_data_dir=legacy_data_dir,
        )
        application.state.message_service = message_bound_service
        application.state.asset_service = asset_bound_service
        yield

    application = FastAPI(
        title="private_send", version=APP_VERSION, lifespan=lifespan
    )
    application.state.token = get_access_token()
    application.include_router(messages_router)
    application.include_router(assets_router)
    configure_cache_policy(application)
    application.add_api_route("/api/health", health, methods=["GET"])
    application.add_api_route("/api/version", version, methods=["GET"])
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


app = create_app()


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
