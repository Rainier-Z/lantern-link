"""SQLite database configuration and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = PROJECT_ROOT / "data" / "rainier.db"


def get_connection(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys and row mappings enabled."""

    database_path = Path(path) if path is not None else DATABASE_PATH
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(path: Path | str | None = None) -> None:
    """Create the v0.2 metadata schema if it does not already exist."""

    with get_connection(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS assets (
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

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                sender TEXT NOT NULL CHECK (sender IN ('pc', 'iphone')),
                type TEXT NOT NULL CHECK (type IN ('text', 'image')),
                text_content TEXT,
                asset_id TEXT REFERENCES assets(id),
                status TEXT NOT NULL DEFAULT 'SENT',
                created_at TEXT NOT NULL,
                deleted_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_created_at
                ON messages (created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_asset_id
                ON messages (asset_id);
            """
        )


def database_connection(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a connection for repository callers and always close it."""

    connection = get_connection(path)
    try:
        yield connection
    finally:
        connection.close()
