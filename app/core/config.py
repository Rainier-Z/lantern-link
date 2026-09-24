"""Runtime configuration for private_send."""

import logging
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
WEB_DIR = PROJECT_ROOT / "app" / "web"
HOST = "0.0.0.0"
PORT = 9527
MESSAGE_MAX_BYTES = 64 * 1024

DATABASE_NAME = "private_send.db"
# Older database filename retained for migration only.
LEGACY_DATABASE_NAME = "rainier.db"
_LOGGER = logging.getLogger(__name__)


def resolve_database_path(data_dir: Path | str | None = None) -> Path:
    """Return the selected database path without mutating stored data."""

    directory = Path(data_dir) if data_dir is not None else resolve_app_data_dir()
    return directory / DATABASE_NAME


def resolve_app_data_dir(local_app_data: Path | str | None = None) -> Path:
    """Return the private_send app-data directory, with an injectable base."""

    base = local_app_data or os.environ.get("LOCALAPPDATA")
    if base is None:
        base = Path.home() / "AppData" / "Local"
    return Path(base) / "private_send"


def resolve_user_files_dir(
    user_profile: Path | str | None = None,
) -> Path:
    """Return the user-visible file root, with an injectable profile path."""

    base = user_profile or os.environ.get("USERPROFILE")
    if base is None:
        base = Path.home()
    return Path(base) / "Downloads" / "file_private_send"
