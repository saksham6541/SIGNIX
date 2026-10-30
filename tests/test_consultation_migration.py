import sqlite3
from pathlib import Path

from scripts.migrate_consultation_requests import apply_migration, inspect_database


def test_consultation_migration_is_additive_backed_up_and_repeatable(tmp_path):
    database = tmp_path / "existing.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample_data (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO sample_data (value) VALUES ('preserve me')")
        assert inspect_database(connection)["table_present"] is False

    result = apply_migration(database, tmp_path / "backups")

    assert result["status"] == "applied"
    assert result["original_records_unchanged"] is True
    assert Path(result["backup"]).exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM sample_data").fetchone() == (
            "preserve me",
        )
        assert inspect_database(connection)["applied"] is True

    repeated = apply_migration(database, tmp_path / "backups")

    assert repeated["status"] == "already_applied"
