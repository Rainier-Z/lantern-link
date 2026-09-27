"""Release guard: tests must never select a production storage root."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_subprocess_bootstrap_uses_only_explicit_test_paths(tmp_path: Path) -> None:
    paths = {
        "TEST_APP_DATA": tmp_path / "app-data",
        "TEST_USER_FILES": tmp_path / "Downloads" / "file_private_send",
        "TEST_LEGACY_DATA": tmp_path / "legacy",
    }
    environment = os.environ | {key: str(value) for key, value in paths.items()}
    program = " ".join((
        "import os; from pathlib import Path;",
        "from app.main import create_app;",
        "from fastapi.testclient import TestClient;",
        "app = create_app(data_dir=Path(os.environ['TEST_APP_DATA']),",
        "user_files_dir=Path(os.environ['TEST_USER_FILES']),",
        "legacy_data_dir=Path(os.environ['TEST_LEGACY_DATA']));",
        "client = TestClient(app); client.__enter__();",
        "service = app.state.asset_service;",
        "assert service.database_path.is_relative_to(Path(os.environ['TEST_APP_DATA']));",
        "assert service.staging_dir.is_relative_to(Path(os.environ['TEST_APP_DATA']));",
        "assert service.user_files_dir.is_relative_to(Path(os.environ['TEST_USER_FILES']));",
        "client.__exit__(None, None, None)",
    ))

    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (paths["TEST_APP_DATA"] / "private_send.db").is_file()
    assert paths["TEST_USER_FILES"].is_dir()
