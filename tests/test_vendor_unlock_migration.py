import sqlite3

import pytest

from app.database import _handle_unsafe_unlock_constraint
from scripts import migrate_vendor_unlock_receipts as migration


def _legacy_database(path, *, check_constraint=False):
    connection = sqlite3.connect(path)
    check_sql = ", CHECK (credits_charged >= 0)" if check_constraint else ""
    connection.executescript(
        f"""
        PRAGMA foreign_keys=ON;
        CREATE TABLE users (id INTEGER NOT NULL PRIMARY KEY);
        CREATE TABLE projects (id INTEGER NOT NULL PRIMARY KEY);
        CREATE TABLE vendor_lead_unlocks (
            id INTEGER NOT NULL PRIMARY KEY,
            vendor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            credits_charged FLOAT NOT NULL,
            unlocked_at DATETIME NOT NULL,
            source VARCHAR(64) NOT NULL,
            CONSTRAINT uq_vendor_lead_unlock_vendor_project UNIQUE(vendor_user_id, project_id)
            {check_sql}
        );
        CREATE INDEX ix_vendor_lead_unlocks_vendor_user_id ON vendor_lead_unlocks(vendor_user_id);
        CREATE INDEX ix_vendor_lead_unlocks_project_id ON vendor_lead_unlocks(project_id);
        CREATE TABLE vendor_credit_ledger (
            id INTEGER NOT NULL PRIMARY KEY,
            vendor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
            delta FLOAT NOT NULL,
            balance_after FLOAT NOT NULL,
            reason VARCHAR(128) NOT NULL,
            note TEXT,
            created_at DATETIME NOT NULL
        );
        INSERT INTO users VALUES (1);
        INSERT INTO projects VALUES (7);
        INSERT INTO vendor_lead_unlocks VALUES
            (11, 1, 7, 2.0, '2026-10-30 10:00:00', 'lead_unlock');
        INSERT INTO vendor_credit_ledger VALUES
            (12, 1, 7, -2.0, 8.0, 'lead_unlock', 'Unlock 11; project 7',
             '2026-10-30 10:00:00');
        """
    )
    connection.commit()
    connection.close()


def _snapshot(path):
    connection = sqlite3.connect(path)
    try:
        return {
            "schema": connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE sql IS NOT NULL ORDER BY type, name"
            ).fetchall(),
            "receipts": connection.execute(
                "SELECT * FROM vendor_lead_unlocks ORDER BY id"
            ).fetchall(),
            "ledger": connection.execute(
                "SELECT * FROM vendor_credit_ledger ORDER BY id"
            ).fetchall(),
            "projects": connection.execute("SELECT * FROM projects ORDER BY id").fetchall(),
        }
    finally:
        connection.close()


def test_startup_constraint_handling_remains_strict_for_sqlite():
    with pytest.raises(RuntimeError, match="migrate_vendor_unlock_receipts.py"):
        _handle_unsafe_unlock_constraint("sqlite", "CASCADE")


def test_startup_constraint_handling_warns_for_deferred_postgresql(caplog):
    _handle_unsafe_unlock_constraint("postgresql", "CASCADE")

    assert "database-level receipt-preservation migration is deferred" in caplog.text
    assert "ON DELETE CASCADE" in caplog.text


def test_unlock_receipt_migration_is_backed_up_repeatable_and_restricts_delete(
    tmp_path,
):
    database = tmp_path / "legacy.sqlite"
    backups = tmp_path / "backups"
    _legacy_database(database)

    with migration.connect(database) as connection:
        before = migration.inspect_database(connection)
    assert before["project_delete_action"] == "CASCADE"
    assert before["unlock_receipts"] == 1

    applied = migration.apply_migration(database, backups)
    assert applied["status"] == "applied"
    assert applied["project_delete_action"] == "RESTRICT"
    assert applied["original_records_unchanged"] is True
    assert sqlite3.connect(applied["backup"]).execute(
        "PRAGMA integrity_check"
    ).fetchone() == ("ok",)

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    assert connection.execute("SELECT * FROM vendor_lead_unlocks").fetchone() == (
        11,
        1,
        7,
        2.0,
        "2026-10-30 10:00:00",
        "lead_unlock",
    )
    assert connection.execute("SELECT delta, balance_after FROM vendor_credit_ledger").fetchone() == (-2.0, 8.0)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM projects WHERE id=7")
    connection.rollback()
    connection.close()

    repeated = migration.apply_migration(database, backups)
    assert repeated["status"] == "already_applied"
    assert len(list(backups.iterdir())) == 1


def test_unlock_receipt_migration_rolls_back_table_rebuild_on_failure(
    tmp_path, monkeypatch
):
    database = tmp_path / "rollback.sqlite"
    backups = tmp_path / "backups"
    _legacy_database(database)
    original = migration._rebuild_table

    def fail_after_rebuild(connection):
        original(connection)
        raise RuntimeError("injected failure")

    monkeypatch.setattr(migration, "_rebuild_table", fail_after_rebuild)
    with pytest.raises(RuntimeError, match="Migration rolled back"):
        migration.apply_migration(database, backups)

    with migration.connect(database) as connection:
        report = migration.inspect_database(connection)
        assert report["project_delete_action"] == "CASCADE"
        assert report["unlock_receipts"] == 1
        assert connection.execute(
            "SELECT delta, balance_after FROM vendor_credit_ledger"
        ).fetchone() == (-2.0, 8.0)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert len(list(backups.iterdir())) == 1


@pytest.mark.parametrize("variation", ["custom_index", "custom_trigger", "check_constraint"])
def test_unlock_receipt_migration_rejects_unsupported_schema_without_changes(
    tmp_path, variation
):
    database = tmp_path / f"{variation}.sqlite"
    backups = tmp_path / "backups"
    _legacy_database(database, check_constraint=variation == "check_constraint")

    if variation != "check_constraint":
        connection = sqlite3.connect(database)
        if variation == "custom_index":
            connection.execute(
                "CREATE INDEX custom_unlock_source ON vendor_lead_unlocks(source)"
            )
        else:
            connection.execute(
                "CREATE TRIGGER custom_unlock_audit AFTER INSERT ON vendor_lead_unlocks "
                "BEGIN SELECT NEW.id; END"
            )
        connection.commit()
        connection.close()

    before = _snapshot(database)
    with pytest.raises(RuntimeError, match="no changes made|rolled back"):
        migration.apply_migration(database, backups)

    assert _snapshot(database) == before
    assert not backups.exists()
