import sqlite3
from pathlib import Path

import pytest

from scripts.migrate_consultation_requests import apply_migration as apply_consultation
from scripts.migrate_vendor_saved_leads import apply_migration as apply_saved_leads


def _create_pristine_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id))"
        )
        connection.execute(
            "INSERT INTO users (id, email) VALUES (1, 'fictional@example.test')"
        )
        connection.execute(
            "INSERT INTO projects (id, user_id) VALUES (7, 1)"
        )


def _assert_integrity(path):
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT id, email FROM users"
        ).fetchall() == [(1, "fictional@example.test")]
        assert connection.execute("SELECT id, user_id FROM projects").fetchall() == [
            (7, 1)
        ]


@pytest.mark.parametrize(
    "migration_order",
    [
        ("consultation", "saved_leads"),
        ("saved_leads", "consultation"),
    ],
)
def test_consultation_and_saved_lead_migrations_are_order_independent(
    tmp_path, migration_order
):
    database = tmp_path / f"{migration_order[0]}_first.sqlite"
    backup_dir = tmp_path / f"{migration_order[0]}_backups"
    _create_pristine_database(database)
    apply = {
        "consultation": apply_consultation,
        "saved_leads": apply_saved_leads,
    }

    results = [apply[name](database, backup_dir) for name in migration_order]

    assert [result["status"] for result in results] == ["applied", "applied"]
    for result in results:
        backup = Path(result["backup"])
        assert backup.is_file()
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchall() == [
                ("ok",)
            ]
            assert connection.execute(
                "SELECT id, email FROM users"
            ).fetchall() == [(1, "fictional@example.test")]
            assert connection.execute(
                "SELECT id, user_id FROM projects"
            ).fetchall() == [(7, 1)]

    _assert_integrity(database)
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"consultation_requests", "vendor_saved_leads"} <= tables
        assert connection.execute(
            "SELECT version FROM signix_schema_migrations ORDER BY version"
        ).fetchall() == [
            ("20261030_01_vendor_saved_leads",),
            ("20261030_02_consultation_requests",),
        ]


@pytest.mark.parametrize(
    ("name", "apply", "version", "table"),
    [
        (
            "consultation",
            apply_consultation,
            "20261030_02_consultation_requests",
            "consultation_requests",
        ),
        (
            "saved_leads",
            apply_saved_leads,
            "20261030_01_vendor_saved_leads",
            "vendor_saved_leads",
        ),
    ],
)
def test_migration_failure_rolls_back_schema_and_keeps_verified_backup(
    tmp_path, name, apply, version, table
):
    database = tmp_path / f"{name}_rollback.sqlite"
    backup_dir = tmp_path / f"{name}_rollback_backups"
    _create_pristine_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE signix_schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, backup_path TEXT NOT NULL)"
        )
        connection.execute(
            f"""
            CREATE TRIGGER reject_{name}_migration
            BEFORE INSERT ON signix_schema_migrations
            WHEN NEW.version = '{version}'
            BEGIN
                SELECT RAISE(ABORT, 'fixture rollback');
            END
            """
        )

    with pytest.raises(RuntimeError, match="Migration rolled back"):
        apply(database, backup_dir)

    _assert_integrity(database)
    backups = list(backup_dir.glob("*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute(
            f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{table}'"
        ).fetchone() is None
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM signix_schema_migrations WHERE version=?", (version,)
        ).fetchone() is None
