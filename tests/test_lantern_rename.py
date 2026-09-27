from pathlib import Path

from app.core.config import (
    resolve_app_data_dir,
    resolve_legacy_app_data_dir,
    resolve_legacy_user_files_dir,
    resolve_user_files_dir,
)
from app.core.database import get_connection, initialize_database
from app.core import network


def test_lantern_paths_use_new_roots_and_keep_private_send_as_a_legacy_source(
    tmp_path: Path,
) -> None:
    local_app_data = tmp_path / "LocalAppData"
    user_profile = tmp_path / "Profile"

    assert resolve_app_data_dir(local_app_data) == local_app_data / "lantern_link"
    assert resolve_user_files_dir(user_profile) == (
        user_profile / "Downloads" / "lantern_link"
    )
    assert resolve_legacy_app_data_dir(local_app_data) == (
        local_app_data / "private_send"
    )
    assert resolve_legacy_user_files_dir(user_profile) == (
        user_profile / "Downloads" / "file_private_send"
    )


def test_first_lantern_start_copies_private_send_database_and_archive(
    tmp_path: Path, monkeypatch
) -> None:
    import app.main as main_module

    legacy_data = tmp_path / "LocalAppData" / "private_send"
    lantern_data = tmp_path / "LocalAppData" / "lantern_link"
    legacy_files = tmp_path / "Profile" / "Downloads" / "file_private_send"
    lantern_files = tmp_path / "Profile" / "Downloads" / "lantern_link"
    legacy_database = legacy_data / "private_send.db"
    initialize_database(legacy_database)
    with get_connection(legacy_database) as connection:
        connection.execute(
            "INSERT INTO messages (id, sender, type, text_content, created_at) "
            "VALUES ('old-message', 'pc', 'text', 'kept', '2026-01-01T00:00:00+00:00')"
        )
    archived_file = legacy_files / "2026-01-01" / "kept.txt"
    archived_file.parent.mkdir(parents=True)
    archived_file.write_bytes(b"kept archive")

    monkeypatch.setattr(main_module, "resolve_app_data_dir", lambda: lantern_data)
    monkeypatch.setattr(main_module, "resolve_user_files_dir", lambda: lantern_files)
    monkeypatch.setattr(
        main_module, "resolve_legacy_app_data_dir", lambda: legacy_data
    )
    monkeypatch.setattr(
        main_module, "resolve_legacy_user_files_dir", lambda: legacy_files
    )

    database_path, files_path, _, _ = main_module.bootstrap_app()

    assert database_path == lantern_data / "lantern_link.db"
    assert files_path == lantern_files
    assert (lantern_files / "2026-01-01" / "kept.txt").read_bytes() == b"kept archive"
    assert archived_file.read_bytes() == b"kept archive"
    with get_connection(database_path) as connection:
        assert connection.execute("SELECT text_content FROM messages").fetchone()[0] == "kept"


def test_lantern_lan_override_uses_the_new_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("LANTERN_LINK_LAN_IP", "192.168.10.8")
    monkeypatch.setattr(network, "get_local_ipv4_candidates", lambda: [])
    monkeypatch.setattr(network, "get_route_ipv4", lambda: None)

    info = network.get_lan_network_info()

    assert info.selected_ip == "192.168.10.8"
    assert info.source == "override"


def test_lantern_migration_does_not_inherit_private_send_control_files(
    tmp_path: Path,
) -> None:
    from app.main import bootstrap_app

    legacy_data = tmp_path / "private-send"
    lantern_data = tmp_path / "lantern"
    initialize_database(legacy_data / "private_send.db")
    legacy_marker = legacy_data / "migration" / "legacy_v1_completed"
    legacy_marker.parent.mkdir(parents=True)
    legacy_marker.touch()
    (legacy_data / ".legacy_assets_copy_owned").touch()

    database_path, _, _, _ = bootstrap_app(
        app_data_dir=lantern_data,
        user_files_dir=tmp_path / "Downloads" / "lantern_link",
        legacy_data_dir=legacy_data,
    )

    assert database_path.is_file()
    assert not (lantern_data / "migration" / "legacy_v1_completed").exists()
    assert not (lantern_data / ".legacy_assets_copy_owned").exists()
    assert (
        lantern_data
        / "migration"
        / "private_send_to_lantern_link_completed"
    ).is_file()
