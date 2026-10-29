from contextlib import closing
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateTable
from werkzeug.security import generate_password_hash

from app.models import User, db
from scripts import migrate_sqlite_users as migration


def legacy_database(tmp_path, extra_columns=""):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(320) NOT NULL UNIQUE, "
                           "password_hash VARCHAR(255) NOT NULL, display_name VARCHAR(128) NOT NULL, "
                           "created_at DATETIME NOT NULL" + extra_columns + ")")
        for table in db.metadata.sorted_tables:
            if table.name != "users":
                connection.execute(str(CreateTable(table).compile(dialect=dialect())))
        connection.execute("INSERT INTO users(id,email,password_hash,display_name,created_at) VALUES (1,?,?,?,?)",
                           ("legacy@example.com", generate_password_hash("migration-test-password"),
                            "Legacy user", "2025-01-01 00:00:00"))
        connection.execute("INSERT INTO user_locations(id,user_id,address,latitude,longitude,system_size,annual_generation,extras) "
                           "VALUES (1,1,'Preserved location',28.6,77.2,5,7000,?)", ('{"saved":true}',))
    return path


def test_additive_backup_preservation_and_idempotence(tmp_path):
    path = legacy_database(tmp_path)
    with closing(migration.connect(path)) as connection:
        original = migration.schema(connection)
        values = migration.fingerprints(connection, original)
    result = migration.apply_migration(path, tmp_path / "backups")
    assert set(result["columns_added"]) == set(migration.ADDITIONS)
    assert result["original_records_unchanged"]
    with closing(migration.connect(result["backup"])) as backup:
        assert migration.schema(backup) == original
        assert migration.fingerprints(backup, original) == values
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    with closing(migration.connect(path)) as connection:
        assert migration.fingerprints(connection, original) == values
        assert connection.execute("SELECT role,vendor_status,vendor_profile,vendor_credits_balance,"
                                  "default_tariff_per_kwh,default_state,default_property_type,language_preference "
                                  "FROM users").fetchone() == ("user", None, None, 0.0, None, None, None, "en")
        assert migration.inspect_database(connection)["missing_indexes"] == []
    assert migration.apply_migration(path, tmp_path / "backups")["status"] == "already_applied"
    assert len(list((tmp_path / "backups").glob("*.bak"))) == 1


def test_existing_roles_balances_and_preferences_not_overwritten(tmp_path):
    path = legacy_database(tmp_path, ", role VARCHAR(32) NOT NULL DEFAULT 'user', "
                           "vendor_credits_balance FLOAT NOT NULL DEFAULT 0, language_preference VARCHAR(8)")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE users SET role='vendor',vendor_credits_balance=3.5,language_preference='hi'")
    result = migration.apply_migration(path, tmp_path / "backups")
    assert "role" not in result["columns_added"]
    with closing(migration.connect(path)) as connection:
        assert connection.execute("SELECT role,vendor_credits_balance,language_preference FROM users").fetchone() == ("vendor", 3.5, "hi")


def test_failure_rolls_back_all_ddl_and_keeps_backup(tmp_path):
    path = legacy_database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(f"CREATE TABLE {migration.HISTORY} (version TEXT PRIMARY KEY, applied_at TEXT, backup_path TEXT)")
        connection.execute(f"CREATE TRIGGER reject_version BEFORE INSERT ON {migration.HISTORY} "
                           "BEGIN SELECT RAISE(ABORT, 'injected persistence failure'); END")
        original = migration.schema(connection)
        values = migration.fingerprints(connection, original)
    with pytest.raises(RuntimeError, match="rolled back.*injected persistence failure"):
        migration.apply_migration(path, tmp_path / "backups")
    with closing(migration.connect(path)) as connection:
        assert migration.schema(connection) == original
        assert migration.fingerprints(connection, original) == values
        assert connection.execute(f"SELECT count(*) FROM {migration.HISTORY}").fetchone() == (0,)
    assert len(list((tmp_path / "backups").glob("*.bak"))) == 1


def test_backup_failure_prevents_schema_changes(tmp_path, monkeypatch):
    path = legacy_database(tmp_path)
    with closing(migration.connect(path)) as connection:
        original = migration.schema(connection)
    def fail(*args):
        raise OSError("backup storage unavailable")
    monkeypatch.setattr(migration, "backup_database", fail)
    with pytest.raises(RuntimeError, match="backup storage unavailable"):
        migration.apply_migration(path, tmp_path / "backups")
    with closing(migration.connect(path)) as connection:
        assert migration.schema(connection) == original


def test_refuses_unrelated_schema_gaps_and_nonexistent_file(tmp_path):
    with pytest.raises(RuntimeError, match="refusing to create"):
        migration.resolve_database(tmp_path / "absent.db")
    assert not (tmp_path / "absent.db").exists()
    path = tmp_path / "incomplete.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="outside this version"):
        migration.apply_migration(path, tmp_path / "backups")
    assert not (tmp_path / "backups").exists()


def test_login_works_after_migration_without_initialization(tmp_path, monkeypatch):
    from app import create_app
    from app.config import Config
    path = legacy_database(tmp_path)
    migration.apply_migration(path, tmp_path / "backups")
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", "sqlite:///" + path.as_posix())
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SECRET_KEY="isolated-migration-test")
    try:
        client = app.test_client()
        response = client.post("/login", data={"email": "legacy@example.com", "password": "migration-test-password", "planning_track": "home"})
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/survey/home")
        with client.session_transaction() as session:
            assert session["_user_id"] == "1"
        with app.app_context():
            assert db.session.get(User, 1).role == "user"
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_copied_legacy_database_upgrades_without_touching_source(tmp_path):
    import shutil
    original = legacy_database(tmp_path)
    copied = tmp_path / "demo-copy.sqlite"
    shutil.copy2(original, copied)
    original_bytes = original.read_bytes()
    result = migration.apply_migration(copied, tmp_path / "copy-backups")
    assert result["original_records_unchanged"]
    assert original.read_bytes() == original_bytes
    with closing(migration.connect(copied)) as connection:
        assert connection.execute("SELECT role FROM users").fetchone() == ("user",)
    assert Path(result["backup"]).is_file()


def test_startup_rejects_legacy_schema_without_any_mutation(tmp_path, monkeypatch):
    from app import create_app
    from app.config import Config
    from app.database import initialize_database
    path = legacy_database(tmp_path)
    original = path.read_bytes()
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", "sqlite:///" + path.as_posix())
    app = create_app()
    with app.app_context():
        try:
            with pytest.raises(RuntimeError, match="explicit backed-up migration"):
                initialize_database()
            assert path.read_bytes() == original
        finally:
            db.session.remove()
            db.engine.dispose()
