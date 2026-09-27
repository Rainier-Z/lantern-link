"""Archive publication and database faults remain recoverable and idempotent."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from app.core.database import get_connection, initialize_database
from app.repositories.message_repository import MessageRepository
from app.services.asset_service import AssetService, FILE_POLICY
from app.services import asset_service


def service_for(tmp_path: Path) -> AssetService:
    database = tmp_path / "app-data" / "lantern_link.db"
    initialize_database(database)
    return AssetService(
        database, user_files_dir=tmp_path / "Downloads" / "lantern_link"
    )


def upload(service: AssetService, payload: bytes = b"complete upload"):
    return asyncio.run(service.persist_upload(
        UploadFile(filename="report.bin", file=BytesIO(payload)), "pc", FILE_POLICY
    ))


def reject_completion(service: AssetService) -> None:
    with get_connection(service.database_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_archive_completion BEFORE UPDATE ON assets "
            "WHEN OLD.status = 'ARCHIVE_PENDING' BEGIN "
            "SELECT RAISE(ABORT, 'injected metadata failure'); END"
        )


def allow_completion(service: AssetService) -> None:
    with get_connection(service.database_path) as connection:
        connection.execute("DROP TRIGGER fail_archive_completion")


def archived_files(service: AssetService) -> list[Path]:
    return sorted(
        path for path in service.user_files_dir.glob("????-??-??/*")
        if not path.name.startswith(".lantern_link_")
    )


def test_database_failure_after_publication_recovers_without_duplicate(tmp_path):
    service = service_for(tmp_path)
    reject_completion(service)

    with pytest.raises(sqlite3.IntegrityError, match="injected metadata failure"):
        upload(service)

    pending, = service.repository.list_archive_pending()
    formal, = archived_files(service)
    staging = service.staging_dir / f"{pending.id}.partial"
    assert formal.read_bytes() == staging.read_bytes() == b"complete upload"
    history, = MessageRepository(service.database_path).list()
    assert history.asset.id == pending.id
    assert history.asset.status == "ARCHIVE_PENDING"
    assert service.storage_stats().asset_count == 0

    old = datetime.now(timezone.utc) - timedelta(days=3)
    os.utime(staging, (old.timestamp(), old.timestamp()))
    restarted = service_for(tmp_path)
    assert restarted.scan_staging() == [staging]
    assert restarted.repository.get(pending.id).status == "ARCHIVE_PENDING"
    assert archived_files(restarted) == [formal]

    allow_completion(service)
    recovered = service_for(tmp_path)
    assert recovered.repository.get(pending.id).status == "AVAILABLE"
    assert archived_files(recovered) == [formal]
    assert formal.read_bytes() == b"complete upload"
    assert not staging.exists()
    assert recovered.recover_archive_pending() == []
    with get_connection(service.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_initial_database_failure_never_publishes_untracked_archive(tmp_path):
    service = service_for(tmp_path)
    with get_connection(service.database_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_message BEFORE INSERT ON messages BEGIN "
            "SELECT RAISE(ABORT, 'injected intent failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected intent failure"):
        upload(service)
    assert archived_files(service) == []
    with get_connection(service.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_publication_failure_keeps_record_and_reserved_name(tmp_path, monkeypatch):
    service = service_for(tmp_path)

    def fail_publication(*args, **kwargs):
        raise OSError("injected archive publication failure")

    monkeypatch.setattr(service, "_publish_archive_temp", fail_publication)
    with pytest.raises(OSError, match="injected archive publication failure"):
        upload(service)
    pending, = service.repository.list_archive_pending()
    assert archived_files(service) == []
    assert (service.staging_dir / f"{pending.id}.partial").is_file()

    monkeypatch.undo()
    _, second = upload(service, b"second upload")
    assert second.stored_filename == "report (1).bin"
    restarted = service_for(tmp_path)
    assert restarted.repository.get(pending.id).status == "AVAILABLE"
    assert (restarted.user_files_dir / pending.relative_path).read_bytes() == b"complete upload"
    assert len(archived_files(restarted)) == 2


def test_pending_archive_keeps_its_committed_target_when_timezone_changes(
    tmp_path, monkeypatch
):
    service = service_for(tmp_path)
    monkeypatch.setattr(asset_service, "_host_local_timezone", lambda: timezone(timedelta(hours=8)))
    original_place = service._place_in_archive
    monkeypatch.setattr(
        service, "_place_in_archive", lambda *args: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(OSError):
        upload(service)
    pending, = service.repository.list_archive_pending()
    committed_path = pending.relative_path

    monkeypatch.setattr(service, "_place_in_archive", original_place)
    monkeypatch.setattr(asset_service, "_host_local_timezone", lambda: timezone(timedelta(hours=-7)))
    assert service.recover_archive_pending() == [pending.id]

    recovered = service.repository.get(pending.id)
    assert recovered is not None
    assert recovered.relative_path == committed_path
    assert (service.user_files_dir / committed_path).is_file()


def test_windows_archive_publish_does_not_require_hard_links(tmp_path, monkeypatch):
    service = service_for(tmp_path)
    monkeypatch.setattr(asset_service, "_is_windows", lambda: True)
    monkeypatch.setattr(
        asset_service.os, "link", lambda *args, **kwargs: (_ for _ in ()).throw(OSError())
    )

    _, asset = upload(service)

    assert service.resolve_asset_path(asset).read_bytes() == b"complete upload"


def test_windows_archive_publish_does_not_replace_an_existing_destination(tmp_path):
    service = service_for(tmp_path)
    temporary = tmp_path / "complete.partial"
    destination = tmp_path / "report.bin"
    temporary.write_bytes(b"complete upload")
    destination.write_bytes(b"user's existing file")

    service._publish_archive_temp(temporary, destination)

    assert destination.read_bytes() == b"user's existing file"
    assert temporary.read_bytes() == b"complete upload"


def test_crash_immediately_after_publication_keeps_pending_intent(tmp_path, monkeypatch):
    service = service_for(tmp_path)
    original_publish = service._publish_archive_temp

    def publish_then_crash(temporary_path, destination):
        original_publish(temporary_path, destination)
        raise SystemExit("injected process crash")

    monkeypatch.setattr(service, "_publish_archive_temp", publish_then_crash)
    with pytest.raises(SystemExit, match="injected process crash"):
        upload(service)
    pending, = service.repository.list_archive_pending()
    formal, = archived_files(service)
    monkeypatch.setattr(service, "_publish_archive_temp", original_publish)

    restarted = service_for(tmp_path)
    assert restarted.repository.get(pending.id).status == "AVAILABLE"
    assert archived_files(restarted) == [formal]
    assert formal.read_bytes() == b"complete upload"


@pytest.mark.parametrize("failure", ["database", "not_updated"])
def test_legacy_failed_metadata_update_retries_same_copy(tmp_path, monkeypatch, failure):
    service = service_for(tmp_path)
    payload = b"legacy original bytes"
    relative = "assets/2026/01/02/legacy.bin"
    source = service.user_files_dir / relative
    source.parent.mkdir(parents=True)
    source.write_bytes(payload)
    with get_connection(service.database_path) as connection:
        connection.execute(
            "INSERT INTO assets (id, kind, original_filename, stored_filename, extension, "
            "mime_type, size, sha256, relative_path, status, created_at) "
            "VALUES ('legacy', 'file', 'report.bin', 'legacy.bin', '.bin', "
            "'application/octet-stream', ?, ?, ?, 'AVAILABLE', '2026-01-02T03:04:05+00:00')",
            (len(payload), hashlib.sha256(payload).hexdigest(), relative),
        )
    original_update = service.repository.update_storage_location
    if failure == "database":
        reject_completion(service)
    else:
        monkeypatch.setattr(service.repository, "update_storage_location", lambda *args: False)

    assert service.import_legacy_assets() == []
    pending = service.repository.get("legacy")
    assert pending.status == "ARCHIVE_PENDING"
    assert pending.relative_path == relative
    formal, = archived_files(service)
    assert formal.read_bytes() == source.read_bytes() == payload
    assert service.import_legacy_assets() == []
    assert service.recover_archive_pending() == []
    assert archived_files(service) == [formal]

    if failure == "database":
        allow_completion(service)
    else:
        monkeypatch.setattr(service.repository, "update_storage_location", original_update)
    restarted = service_for(tmp_path)
    completed = restarted.repository.get("legacy")
    assert completed.status == "AVAILABLE"
    assert completed.relative_path == "2026-01-02/report.bin"
    assert formal.read_bytes() == source.read_bytes() == payload
    assert archived_files(restarted) == [formal]
    assert restarted.import_legacy_assets() == []


def test_recovery_preserves_conflicting_user_file_and_pending_payload(tmp_path, monkeypatch):
    service = service_for(tmp_path)
    original_place = service._place_in_archive
    monkeypatch.setattr(service, "_place_in_archive", lambda *args: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        upload(service)
    pending, = service.repository.list_archive_pending()
    formal = service.user_files_dir / pending.relative_path
    formal.write_bytes(b"user created unrelated file")
    monkeypatch.setattr(service, "_place_in_archive", original_place)

    restarted = service_for(tmp_path)
    assert restarted.repository.get(pending.id).status == "ARCHIVE_PENDING"
    assert formal.read_bytes() == b"user created unrelated file"
    assert (service.staging_dir / f"{pending.id}.partial").read_bytes() == b"complete upload"
    assert archived_files(restarted) == [formal]


def test_delete_message_and_recover_delete_pending_keep_formal_files(tmp_path):
    service = service_for(tmp_path)
    message, asset = upload(service)
    formal = service.resolve_asset_path(asset)

    assert service.delete_message(message.id).status == "DELETED"
    assert service.repository.get(asset.id).status == "DELETED"
    assert formal.read_bytes() == b"complete upload"
    with get_connection(service.database_path) as connection:
        connection.execute("UPDATE assets SET status = 'DELETE_PENDING' WHERE id = ?", (asset.id,))
    assert service.recover_delete_pending() == [asset.id]
    assert service.repository.get(asset.id).status == "DELETED"
    assert formal.read_bytes() == b"complete upload"


def test_fresh_environment_import_and_bootstrap_initialize_before_recovery(tmp_path):
    environment = dict(os.environ)
    app_data = tmp_path / "app-data"
    user_files = tmp_path / "user-files"
    legacy_data = tmp_path / "legacy-data"
    environment["TEST_APP_DATA"] = str(app_data)
    environment["TEST_USER_FILES"] = str(user_files)
    environment["TEST_LEGACY_DATA"] = str(legacy_data)
    environment.pop("LOCALAPPDATA", None)
    environment.pop("USERPROFILE", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-c",
             "import os; "
             "from pathlib import Path; "
             "from app.main import create_app; "
             "from fastapi.testclient import TestClient; "
             "root = Path(os.environ['TEST_APP_DATA']).parent.resolve(); "
             "app = create_app(data_dir=os.environ['TEST_APP_DATA'], "
             "user_files_dir=os.environ['TEST_USER_FILES'], "
             "legacy_data_dir=os.environ['TEST_LEGACY_DATA']); "
             "client = TestClient(app); "
             "client.__enter__(); "
             "service = app.state.asset_service; "
             "assert root in service.database_path.resolve().parents; "
             "assert service.database_path.resolve() == "
             "(Path(os.environ['TEST_APP_DATA']) / 'lantern_link.db').resolve(); "
             "assert service.user_files_dir.resolve() == "
             "Path(os.environ['TEST_USER_FILES']).resolve(); "
             "assert root in service.user_files_dir.resolve().parents; "
             "assert app.state.asset_service.repository.list_archive_pending() == []; "
             "client.__exit__(None, None, None)"],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("crash_point", ["during_copy", "before_publish", "after_publish"])
def test_real_process_exit_recovers_only_operation_owned_temporary_files(tmp_path, crash_point):
    service = service_for(tmp_path)
    program = """
import asyncio
import os
import shutil
import sys
from io import BytesIO
from pathlib import Path
from fastapi import UploadFile
from app.services.asset_service import AssetService, FILE_POLICY

service = AssetService(Path(sys.argv[1]), user_files_dir=Path(sys.argv[2]))
crash_point = sys.argv[3]
original_publish = service._publish_archive_temp

def interrupted_copy(source, destination, length):
    destination.write(source.read(3))
    destination.flush()
    os._exit(71)

def interrupted_publish(temporary_path, destination):
    if crash_point == 'before_publish':
        os._exit(71)
    original_publish(temporary_path, destination)
    os._exit(71)

if crash_point == 'during_copy':
    shutil.copyfileobj = interrupted_copy
else:
    service._publish_archive_temp = interrupted_publish
asyncio.run(service.persist_upload(
    UploadFile(filename='report.bin', file=BytesIO(b'complete upload')), 'pc', FILE_POLICY
))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", program, str(service.database_path),
         str(service.user_files_dir), crash_point],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 71, result.stderr
    pending, = service.repository.list_archive_pending()
    temporary = service._archive_temporary_path(
        pending.id, pending.created_at, pending.stored_filename
    )
    formal = service.user_files_dir / pending.relative_path
    if crash_point == "after_publish":
        assert formal.exists()
        assert formal.read_bytes() == b"complete upload"
    if temporary.exists():
        assert temporary.read_bytes() == (
            b"com" if crash_point == "during_copy" else b"complete upload"
        )
    source = service.staging_dir / f"{pending.id}.partial"
    assert source.read_bytes() == b"complete upload"
    if crash_point != "after_publish":
        assert not formal.exists()
    unrelated = temporary.parent / ".lantern_link_unrelated.partial"
    unrelated.write_bytes(b"user file")

    recovered = service_for(tmp_path)
    assert recovered.repository.get(pending.id).status == "AVAILABLE"
    assert formal.read_bytes() == b"complete upload"
    assert not temporary.exists()
    assert not source.exists()
    assert unrelated.read_bytes() == b"user file"
    assert archived_files(recovered) == [formal]


@pytest.mark.parametrize("failure", ["unlink", "metadata"])
def test_legacy_delete_failure_remains_pending_and_startup_retries(tmp_path, monkeypatch, failure):
    from app.main import bootstrap_app

    service = service_for(tmp_path)
    relative = "assets/2026/01/02/managed.jpg"
    managed = service.user_files_dir / relative
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"old managed image")
    with get_connection(service.database_path) as connection:
        connection.execute(
            "INSERT INTO assets (id, kind, original_filename, stored_filename, extension, "
            "mime_type, size, sha256, relative_path, status, created_at) "
            "VALUES ('managed', 'image', 'managed.jpg', 'managed.jpg', '.jpg', "
            "'image/jpeg', 17, 'unused', ?, 'AVAILABLE', '2026-01-02T00:00:00+00:00')",
            (relative,),
        )
        connection.execute(
            "INSERT INTO messages (id, sender, type, asset_id, status, created_at) "
            "VALUES ('managed-message', 'pc', 'image', 'managed', 'SENT', "
            "'2026-01-02T00:00:00+00:00')"
        )
        if failure == "metadata":
            connection.execute(
                "CREATE TRIGGER fail_delete BEFORE UPDATE ON assets "
                "WHEN NEW.status = 'DELETED' BEGIN "
                "SELECT RAISE(ABORT, 'injected delete failure'); END"
            )
    original_unlink = Path.unlink

    def failed_unlink(path, *, missing_ok=False):
        if path == managed:
            raise PermissionError("injected managed unlink failure")
        return original_unlink(path, missing_ok=missing_ok)

    if failure == "unlink":
        monkeypatch.setattr(Path, "unlink", failed_unlink)
    assert service.delete_message("managed-message").status == "DELETED"
    assert service.repository.get("managed").status == "DELETE_PENDING"
    assert managed.exists() == (failure == "unlink")
    monkeypatch.setattr(Path, "unlink", original_unlink)
    if failure == "metadata":
        with get_connection(service.database_path) as connection:
            connection.execute("DROP TRIGGER fail_delete")

    _, _, recovered, _ = bootstrap_app(
        app_data_dir=service.data_dir, user_files_dir=service.user_files_dir
    )
    assert recovered.repository.get("managed").status == "DELETED"
    assert not managed.exists()
    assert recovered.recover_delete_pending() == []


def test_directory_sync_fallback_keeps_staging_until_available_commit(tmp_path, monkeypatch):
    service = service_for(tmp_path)
    with monkeypatch.context() as context:
        context.setattr(asset_service, "_is_windows", lambda: True)

        def unexpected_directory_open(*args, **kwargs):
            raise AssertionError("Windows fallback must not open a directory fd")

        context.setattr(os, "open", unexpected_directory_open)
        assert service._sync_directory(service.staging_dir) is False

    monkeypatch.setattr(service, "_sync_directory", lambda directory: False)
    original_update = service.repository.update_storage_location
    verified = []

    def verify_staging_before_and_after_commit(asset_id, expected_path, filename, relative_path):
        staging = service.staging_dir / f"{asset_id}.partial"
        assert staging.read_bytes() == b"complete upload"
        assert service.repository.get(asset_id).status == "ARCHIVE_PENDING"
        completed = original_update(asset_id, expected_path, filename, relative_path)
        assert service.repository.get(asset_id).status == "AVAILABLE"
        assert staging.read_bytes() == b"complete upload"
        verified.append(asset_id)
        return completed

    monkeypatch.setattr(service.repository, "update_storage_location", verify_staging_before_and_after_commit)
    _, asset = upload(service)
    assert verified == [asset.id]
    assert not (service.staging_dir / f"{asset.id}.partial").exists()


@pytest.mark.parametrize("failure", ["publication", "database"])
def test_deleting_pending_upload_keeps_recovery_ownership_until_archive_completion(
    tmp_path, monkeypatch, failure
):
    service = service_for(tmp_path)
    def failed_publish(*args, **kwargs):
        raise OSError("injected publication failure")

    if failure == "publication":
        monkeypatch.setattr(service, "_publish_archive_temp", failed_publish)
        expected_error = OSError
    else:
        reject_completion(service)
        expected_error = sqlite3.IntegrityError
    with pytest.raises(expected_error):
        upload(service)
    pending, = service.repository.list_archive_pending()
    message, = MessageRepository(service.database_path).list()
    temporary = service._archive_temporary_path(
        pending.id, pending.created_at, pending.stored_filename
    )
    assert temporary.exists() == (failure == "publication")

    assert service.delete_message(message.id).status == "DELETED"
    assert MessageRepository(service.database_path).list() == []
    assert service.repository.get(pending.id).status == "ARCHIVE_PENDING"
    assert temporary.exists() == (failure == "publication")
    staging = service.staging_dir / f"{pending.id}.partial"
    assert staging.read_bytes() == b"complete upload"

    if failure == "database":
        allow_completion(service)
    else:
        monkeypatch.undo()
    recovered = service_for(tmp_path)
    assert recovered.repository.get(pending.id).status == "DELETED"
    assert (service.user_files_dir / pending.relative_path).read_bytes() == b"complete upload"
    assert not temporary.exists()
    assert not staging.exists()
    assert recovered.repository.list_archive_pending() == []
