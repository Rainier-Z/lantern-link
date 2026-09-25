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


def _resolve_windows_downloads() -> Path | None:
    """Return the redirected Windows Downloads known folder when available."""

    if os.name != "nt":
        return None
    try:
        import ctypes

        class _Guid(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folder_id = _Guid(
            0x374DE290,
            0x123F,
            0x4565,
            (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B),
        )
        value = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(value)
        )
        if result != 0 or not value.value:
            return None
        try:
            return Path(value.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(value)
    except (AttributeError, OSError):
        return None


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

    if user_profile is None:
        downloads = _resolve_windows_downloads()
        if downloads is not None:
            return downloads / "file_private_send"
    base = user_profile or os.environ.get("USERPROFILE")
    if base is None:
        base = Path.home()
    return Path(base) / "Downloads" / "file_private_send"
