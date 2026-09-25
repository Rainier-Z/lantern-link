"""Database schema migration tests using real SQLite files."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import (
    resolve_app_data_dir,
    resolve_database_path,
    resolve_user_files_dir,
)
from app.core.database import (
    DatabaseVersionError,
    get_connection,
    initialize_database,
    migrate_database,
)
from app.repositories.asset_repository import AssetRepository
from app.repositories.message_repository import MessageRepository
from app.services.asset_service import AssetService


def _create_v02_database(database_path: Path, data_dir: Path) -> bytes:
    """Create the v0.2 schema and seed representative persisted records."""

    payload = b"legacy image bytes"
    relative_path = "assets/legacy-image.jpg"
    asset_path = data_dir / relative_path
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(payload)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE assets (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                extension TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'STAGING',
                created_at TEXT NOT NULL,
                deleted_at TEXT
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                sender TEXT NOT NULL CHECK (sender IN ('pc', 'iphone')),
                type TEXT NOT NULL CHECK (type IN ('text', 'image')),
                text_content TEXT,
                asset_id TEXT REFERENCES assets(id),
                status TEXT NOT NULL DEFAULT 'SENT',
                created_at TEXT NOT NULL,
                deleted_at TEXT
            );
            CREATE INDEX idx_messages_created_at
                ON messages (created_at DESC, id DESC);
            CREATE INDEX idx_messages_asset_id ON messages (asset_id);
            """
        )
        connection.execute(
            """INSERT INTO assets VALUES
               ('legacy-asset', 'image', 'legacy.jpg', 'legacy-image.jpg', '.jpg',
                'image/jpeg', ?, ?, ?, 'AVAILABLE', '2026-01-01T00:00:00+00:00', NULL)""",
            (len(payload), hashlib.sha256(payload).hexdigest(), relative_path),
        )
        connection.executemany(
            """INSERT INTO messages
               (id, sender, type, text_content, asset_id, status, created_at, deleted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    "legacy-text",
                    "pc",
                    "text",
                    "preserve this text",
                    None,
                    "SENT",
                    "2026-01-01T00:00:01+00:00",
                    None,
                ),
                (
                    "legacy-image-message",
                    "iphone",
                    "image",
                    None,
                    "legacy-asset",
                    "DELETED",
                    "2026-01-01T00:00:02+00:00",
                    "2026-01-02T00:00:00+00:00",
                ),
            ],
        )
    connection.close()
    return payload


def test_v02_database_migrates_idempotently_and_remains_usable(tmp_path: Path) -> None:
    database_path = tmp_path / "rainier.db"
    old_payload = _create_v02_database(database_path, tmp_path)

    initialize_database(database_path)

    with get_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        messages = {
            row["id"]: dict(row)
            for row in connection.execute("SELECT * FROM messages ORDER BY id")
        }
        assert messages["legacy-text"]["text_content"] == "preserve this text"
        assert messages["legacy-image-message"]["asset_id"] == "legacy-asset"
        assert messages["legacy-image-message"]["deleted_at"] == "2026-01-02T00:00:00+00:00"
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone()[0]
        assert "'file'" in schema
        assert {row["name"] for row in connection.execute("PRAGMA index_list(messages)")} >= {
            "idx_messages_created_at",
            "idx_messages_asset_id",
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        foreign_key = connection.execute("PRAGMA foreign_key_list(messages)").fetchone()
        assert foreign_key["table"] == "assets"
        assert foreign_key["from"] == "asset_id"
    connection.close()

    migrate_database(database_path)
    with get_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    connection.close()

    from app.main import create_app

    client_app = create_app(data_dir=tmp_path)
    with TestClient(client_app) as client:
        headers = {"Authorization": f"Bearer {client_app.state.token}"}
        download = client.get("/api/assets/legacy-asset?download=1", headers=headers)
        assert download.status_code == 200, download.text
        assert download.content == old_payload

        upload = client.post(
            "/api/assets/files",
            headers=headers,
            data={"sender": "pc"},
            files={"file": ("new.txt", b"new file bytes", "text/plain")},
        )
        assert upload.status_code == 200, upload.text
        assert upload.json()["message"]["type"] == "file"


def test_new_database_starts_at_schema_version_three(tmp_path: Path) -> None:
    database_path = tmp_path / "new.db"

    migrate_database(database_path)

    with get_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone()[0]
    assert "'file'" in schema


def test_future_database_version_is_refused_without_schema_changes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "future.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 4")

    with pytest.raises(DatabaseVersionError):
        initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == []


def test_bootstrap_copies_legacy_project_data_and_retains_source(
    tmp_path: Path,
) -> None:
    legacy_dir = tmp_path / "legacy-project-data"
    app_data_dir = tmp_path / "LocalAppData" / "private_send"
    user_files_dir = tmp_path / "Downloads" / "file_private_send"
    legacy_database = legacy_dir / "private_send.db"
    _create_v02_database(legacy_database, legacy_dir)
    source_bytes = legacy_database.read_bytes()
    source_asset = legacy_dir / "assets" / "legacy-image.jpg"

    from app.main import bootstrap_app

    database_path, _, asset_service, _ = bootstrap_app(
        app_data_dir=app_data_dir,
        user_files_dir=user_files_dir,
        legacy_data_dir=legacy_dir,
    )

    assert database_path == app_data_dir / "private_send.db"
    assert database_path.read_bytes() != source_bytes
    assert source_bytes == legacy_database.read_bytes()
    assert source_asset.read_bytes() == b"legacy image bytes"
    assert (user_files_dir / "assets" / "legacy-image.jpg").read_bytes() == source_asset.read_bytes()
    assert asset_service.staging_dir == app_data_dir / "staging"
    assert asset_service.assets_dir == user_files_dir / "assets"
    with get_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_bootstrap_cleans_only_its_migrated_legacy_asset_copy(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "legacy-project-data"
    app_data_dir = tmp_path / "app-data"
    user_files_dir = tmp_path / "user-files"
    legacy_database = legacy_dir / "private_send.db"
    _create_v02_database(legacy_database, legacy_dir)
    original_asset = legacy_dir / "assets" / "legacy-image.jpg"
    source_asset = legacy_dir / "assets" / "2026" / "01" / "02" / "legacy-image.jpg"
    source_asset.parent.mkdir(parents=True)
    source_asset.write_bytes(original_asset.read_bytes())
    original_asset.unlink()
    with sqlite3.connect(legacy_database) as connection:
        connection.execute(
            "UPDATE assets SET relative_path = ? WHERE id = 'legacy-asset'",
            ("assets/2026/01/02/legacy-image.jpg",),
        )

    from app.main import bootstrap_app

    _, _, service, _ = bootstrap_app(
        app_data_dir=app_data_dir,
        user_files_dir=user_files_dir,
        legacy_data_dir=legacy_dir,
    )

    migrated = service.repository.get("legacy-asset")
    assert migrated is not None
    assert migrated.relative_path == "2026-01-01/legacy.jpg"
    assert (user_files_dir / migrated.relative_path).read_bytes() == b"legacy image bytes"
    assert source_asset.read_bytes() == b"legacy image bytes"
    assert not (user_files_dir / "assets").exists()
    assert not (app_data_dir / ".legacy_assets_copy_owned").exists()


def test_bootstrap_preserves_conflicting_destination_files_and_copies_missing_legacy_files(
    tmp_path: Path,
) -> None:
    legacy_dir = tmp_path / "legacy-project-data"
    app_data_dir = tmp_path / "app-data"
    user_files_dir = tmp_path / "user-files"
    _create_v02_database(legacy_dir / "private_send.db", legacy_dir)
    (legacy_dir / "settings.json").write_bytes(b"legacy settings")
    (legacy_dir / "migration-note.txt").write_bytes(b"new project file")
    (legacy_dir / "existing-directory.txt").write_bytes(b"legacy file")
    (legacy_dir / "assets" / "nested").mkdir()
    (legacy_dir / "assets" / "nested" / "missing.jpg").write_bytes(b"new asset")

    app_data_dir.mkdir(parents=True)
    (app_data_dir / "settings.json").write_bytes(b"existing settings")
    destination_directory = app_data_dir / "existing-directory.txt"
    destination_directory.mkdir()
    (destination_directory / "keep.txt").write_bytes(b"keep directory")
    existing_asset = user_files_dir / "assets" / "legacy-image.jpg"
    existing_asset.parent.mkdir(parents=True)
    existing_asset.write_bytes(b"existing asset")

    from app.main import bootstrap_app

    bootstrap_app(
        app_data_dir=app_data_dir,
        user_files_dir=user_files_dir,
        legacy_data_dir=legacy_dir,
    )

    assert (app_data_dir / "settings.json").read_bytes() == b"existing settings"
    assert (app_data_dir / "migration-note.txt").read_bytes() == b"new project file"
    assert (destination_directory / "keep.txt").read_bytes() == b"keep directory"
    assert existing_asset.read_bytes() == b"existing asset"
    assert (
        user_files_dir / "assets" / "nested" / "missing.jpg"
    ).read_bytes() == b"new asset"


def test_bootstrap_does_not_copy_assets_through_destination_symlink(
    tmp_path: Path,
) -> None:
    legacy_dir = tmp_path / "legacy-project-data"
    app_data_dir = tmp_path / "app-data"
    user_files_dir = tmp_path / "user-files"
    outside_dir = tmp_path / "outside"
    _create_v02_database(legacy_dir / "private_send.db", legacy_dir)
    outside_dir.mkdir()
    user_files_dir.mkdir()
    try:
        (user_files_dir / "assets").symlink_to(
            outside_dir, target_is_directory=True
        )
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("creating directory symlinks requires Windows privileges")
        raise

    from app.main import bootstrap_app

    bootstrap_app(
        app_data_dir=app_data_dir,
        user_files_dir=user_files_dir,
        legacy_data_dir=legacy_dir,
    )

    assert list(outside_dir.iterdir()) == []


def test_app_data_database_takes_priority_over_legacy_project_data(
    tmp_path: Path,
) -> None:
    app_data_dir = tmp_path / "app-data"
    legacy_dir = tmp_path / "legacy"
    database_path = app_data_dir / "private_send.db"
    legacy_database = legacy_dir / "private_send.db"
    initialize_database(database_path)
    with get_connection(database_path) as connection:
        connection.execute(
            "INSERT INTO messages (id, sender, type, text_content, created_at) "
            "VALUES ('current', 'pc', 'text', 'current', '2026-01-01T00:00:00+00:00')"
        )
    _create_v02_database(legacy_database, legacy_dir)
    legacy_bytes = legacy_database.read_bytes()

    from app.main import bootstrap_app

    bootstrap_app(
        app_data_dir=app_data_dir,
        user_files_dir=tmp_path / "files",
        legacy_data_dir=legacy_dir,
    )

    with get_connection(database_path) as connection:
        assert [row[0] for row in connection.execute("SELECT id FROM messages")] == ["current"]
    assert legacy_database.read_bytes() == legacy_bytes


def test_repository_construction_does_not_migrate_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "rainier.db"
    _create_v02_database(database_path, tmp_path)

    AssetRepository(database_path)
    MessageRepository(database_path)
    AssetService(database_path)

    with get_connection(database_path) as connection:
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone()[0]
        assert "'file'" not in schema
    connection.close()

    from app.main import create_app

    application = create_app(data_dir=tmp_path)
    with TestClient(application):
        pass

    migrated_database_path = tmp_path / "private_send.db"
    with get_connection(migrated_database_path) as connection:
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone()[0]
        assert "'file'" in schema
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_create_app_migrates_legacy_database_name_and_keeps_history(
    tmp_path: Path,
) -> None:
    legacy_database = tmp_path / "rainier.db"
    private_database = tmp_path / "private_send.db"
    _create_v02_database(legacy_database, tmp_path)

    from app.main import create_app

    application = create_app(data_dir=tmp_path)
    with TestClient(application) as client:
        assert private_database.is_file()
        assert legacy_database.exists()
        response = client.get(
            "/api/messages",
            headers={"Authorization": f"Bearer {application.state.token}"},
        )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["messages"]] == ["legacy-text"]
    with get_connection(private_database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_create_app_uses_existing_private_database(tmp_path: Path) -> None:
    private_database = tmp_path / "private_send.db"
    initialize_database(private_database)

    from app.main import create_app

    application = create_app(data_dir=tmp_path)
    with TestClient(application) as client:
        created = client.post(
            "/api/messages",
            headers={"Authorization": f"Bearer {application.state.token}"},
            json={"sender": "pc", "type": "text", "content": "current db"},
        )
    assert created.status_code == 200, created.text
    assert private_database.exists()
    assert not (tmp_path / "rainier.db").exists()


def test_create_app_prefers_private_database_and_retains_legacy_file(
    tmp_path: Path, caplog,
) -> None:
    private_database = tmp_path / "private_send.db"
    legacy_database = tmp_path / "rainier.db"
    initialize_database(private_database)
    _create_v02_database(legacy_database, tmp_path)
    legacy_bytes_before = legacy_database.read_bytes()

    from app.main import create_app

    with caplog.at_level(logging.WARNING):
        application = create_app(data_dir=tmp_path)
    with TestClient(application) as client:
        created = client.post(
            "/api/messages",
            headers={"Authorization": f"Bearer {application.state.token}"},
            json={"sender": "pc", "type": "text", "content": "private db"},
        )
        history = client.get(
            "/api/messages",
            headers={"Authorization": f"Bearer {application.state.token}"},
        )

    assert created.status_code == 200, created.text
    assert [item["content"] for item in history.json()["messages"]] == ["private db"]
    assert legacy_database.read_bytes() == legacy_bytes_before
    assert any(
        "both private_send.db and legacy rainier.db exist" in record.message.lower()
        for record in caplog.records
    )


def test_database_path_resolution_does_not_mutate_legacy_data(tmp_path: Path) -> None:
    legacy_database = tmp_path / "rainier.db"
    legacy_database.write_bytes(b"legacy database")
    assert resolve_database_path(tmp_path) == tmp_path / "private_send.db"
    assert legacy_database.exists()
    assert not (tmp_path / "private_send.db").exists()


def test_windows_storage_roots_can_be_injected(tmp_path: Path) -> None:
    assert resolve_app_data_dir(tmp_path / "LocalAppData") == (
        tmp_path / "LocalAppData" / "private_send"
    )
    assert resolve_user_files_dir(tmp_path / "Profile") == (
        tmp_path / "Profile" / "Downloads" / "file_private_send"
    )
