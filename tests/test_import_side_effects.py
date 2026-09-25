"""Importing the ASGI module must not touch real or configured user storage."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_importing_main_creates_no_appdata_or_downloads(tmp_path: Path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    user_profile = tmp_path / "Profile"
    environment = os.environ | {
        "LOCALAPPDATA": str(local_app_data),
        "USERPROFILE": str(user_profile),
    }

    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not (local_app_data / "private_send").exists()
    assert not (user_profile / "Downloads" / "file_private_send").exists()
