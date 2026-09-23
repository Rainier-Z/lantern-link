"""Database schema migration tests using real SQLite files."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.database import get_connection, initialize_database, migrate_database
from app.repositories.asset_repository import AssetRepository
from app.repositories.message_repository import MessageRepository


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

    migrate_database(database_path)
    with get_connection(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2

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


def test_repository_construction_does_not_migrate_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "rainier.db"
    _create_v02_database(database_path, tmp_path)

    AssetRepository(database_path)
    MessageRepository(database_path)

    with get_connection(database_path) as connection:
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone()[0]
        assert "'file'" not in schema

    from app.main import create_app

    create_app(data_dir=tmp_path)

    with get_connection(database_path) as connection:
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone()[0]
        assert "'file'" in schema
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
