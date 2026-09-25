"""SQLite database configuration and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from app.core.config import resolve_database_path


DATABASE_PATH = resolve_database_path()
CURRENT_DATABASE_VERSION = 3


class DatabaseVersionError(RuntimeError):
    """Raised when a database was written by a newer application version."""


def get_connection(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys and row mappings enabled."""

    database_path = Path(path) if path is not None else DATABASE_PATH
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.create_function(
        "UNICODE_CASEFOLD", 1, lambda value: value.casefold() if value else ""
    )
    return connection


def _create_v3_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS assets (
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
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            sender TEXT NOT NULL CHECK (sender IN ('pc', 'iphone')),
            type TEXT NOT NULL CHECK (type IN ('text', 'image', 'file')),
            text_content TEXT,
            asset_id TEXT REFERENCES assets(id),
            status TEXT NOT NULL DEFAULT 'SENT',
            created_at TEXT NOT NULL,
            deleted_at TEXT
        )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_messages_created_at
           ON messages (created_at DESC, id DESC)"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_asset_id ON messages (asset_id)"
    )


def migrate_database(path: Path | str | None = None) -> None:
    """Create schema version 3 or upgrade an existing v0.2 database."""

    connection = get_connection(path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > CURRENT_DATABASE_VERSION:
            raise DatabaseVersionError(
                f"Database version {version} is newer than supported version "
                f"{CURRENT_DATABASE_VERSION}"
            )

        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone()
        if schema is None:
            _create_v3_schema(connection)
            connection.execute("PRAGMA user_version = 3")
            return

        sql = (schema[0] or "").lower()
        if "'file'" in sql or '"file"' in sql:
            connection.execute("PRAGMA user_version = 3")
            return

        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("ALTER TABLE messages RENAME TO messages_v02")
            connection.execute(
                """CREATE TABLE messages (
                    id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL CHECK (sender IN ('pc', 'iphone')),
                    type TEXT NOT NULL CHECK (type IN ('text', 'image', 'file')),
                    text_content TEXT,
                    asset_id TEXT REFERENCES assets(id),
                    status TEXT NOT NULL DEFAULT 'SENT',
                    created_at TEXT NOT NULL,
                    deleted_at TEXT
                )"""
            )
            connection.execute(
                """INSERT INTO messages
                   (id, sender, type, text_content, asset_id, status, created_at, deleted_at)
                   SELECT id, sender, type, text_content, asset_id, status, created_at, deleted_at
                     FROM messages_v02"""
            )
            connection.execute("DROP TABLE messages_v02")
            connection.execute(
                """CREATE INDEX idx_messages_created_at
                   ON messages (created_at DESC, id DESC)"""
            )
            connection.execute(
                "CREATE INDEX idx_messages_asset_id ON messages (asset_id)"
            )
            connection.execute("PRAGMA user_version = 3")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
    finally:
        connection.close()


def initialize_database(path: Path | str | None = None) -> None:
    """Initialize or migrate a database to the current schema version."""

    migrate_database(path)


def database_connection(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a connection for repository callers and always close it."""

    connection = get_connection(path)
    try:
        yield connection
    finally:
        connection.close()
