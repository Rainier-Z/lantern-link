"""Runtime configuration for Rainier Link."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
WEB_DIR = PROJECT_ROOT / "app" / "web"
HOST = "0.0.0.0"
PORT = 9527
MESSAGE_MAX_BYTES = 64 * 1024
