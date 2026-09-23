"""Runtime configuration for private_send."""

import logging
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
WEB_DIR = PROJECT_ROOT / "app" / "web"
HOST = "0.0.0.0"
PORT = 9527
MESSAGE_MAX_BYTES = 64 * 1024

DATABASE_NAME = "private_send.db"
# Legacy v0.3.1 database filename; migration only.
LEGACY_DATABASE_NAME = "rainier.db"
_LOGGER = logging.getLogger(__name__)


def resolve_database_path(data_dir: Path | str | None = None) -> Path:
    """Return the current database path, migrating the legacy filename once."""

    directory = Path(data_dir) if data_dir is not None else PROJECT_ROOT / "data"
    current_path = directory / DATABASE_NAME
    legacy_path = directory / LEGACY_DATABASE_NAME

    if current_path.exists():
        if legacy_path.exists():
            _LOGGER.warning(
                "Both private_send.db and legacy rainier.db exist; "
                "using private_send.db and retaining rainier.db"
            )
        return current_path

    if legacy_path.exists():
        try:
            legacy_path.replace(current_path)
        except FileNotFoundError:
            if current_path.exists():
                return current_path
            raise

    return current_path
