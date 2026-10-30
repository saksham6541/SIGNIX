"""Explicit additive SQLite migration for public consultation requests.

Without --apply this script inspects only. Applying requires an existing
database, makes and verifies an online backup, and preserves existing rows.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid


VERSION = "20261030_02_consultation_requests"
TABLE = "consultation_requests"
HISTORY = "signix_schema_migrations"
CREATE = """CREATE TABLE consultation_requests (
    id INTEGER NOT NULL PRIMARY KEY,
    submission_token VARCHAR(36) NOT NULL UNIQUE,
    full_name VARCHAR(120) NOT NULL,
    phone VARCHAR(10) NOT NULL,
    email VARCHAR(254),
    segment VARCHAR(16) NOT NULL,
    business_name VARCHAR(160),
    city VARCHAR(120) NOT NULL,
    state VARCHAR(128) NOT NULL,
    pin_code VARCHAR(6) NOT NULL,
    monthly_bill VARCHAR(32) NOT NULL,
    business_type VARCHAR(120),
    operating_profile VARCHAR(32),
    requirements TEXT NOT NULL,
    consent BOOLEAN NOT NULL,
    status VARCHAR(16) NOT NULL,
    admin_notes TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT ck_consultation_request_segment
        CHECK (segment IN ('residential', 'commercial', 'industrial')),
    CONSTRAINT ck_consultation_request_status
        CHECK (status IN ('pending', 'contacted', 'scheduled', 'completed'))
)"""
INDEXES = {
    "ix_consultation_requests_status": "status",
    "ix_consultation_requests_created_at": "created_at",
}
EXPECTED_COLUMNS = (
    ("id", "INTEGER", 1, 1),
    ("submission_token", "VARCHAR(36)", 1, 0),
    ("full_name", "VARCHAR(120)", 1, 0),
    ("phone", "VARCHAR(10)", 1, 0),
    ("email", "VARCHAR(254)", 0, 0),
    ("segment", "VARCHAR(16)", 1, 0),
    ("business_name", "VARCHAR(160)", 0, 0),
    ("city", "VARCHAR(120)", 1, 0),
    ("state", "VARCHAR(128)", 1, 0),
    ("pin_code", "VARCHAR(6)", 1, 0),
    ("monthly_bill", "VARCHAR(32)", 1, 0),
    ("business_type", "VARCHAR(120)", 0, 0),
    ("operating_profile", "VARCHAR(32)", 0, 0),
    ("requirements", "TEXT", 1, 0),
    ("consent", "BOOLEAN", 1, 0),
    ("status", "VARCHAR(16)", 1, 0),
    ("admin_notes", "TEXT", 1, 0),
    ("created_at", "DATETIME", 1, 0),
    ("updated_at", "DATETIME", 1, 0),
)


def _tables(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _fingerprint(connection, table_names, *, exclude_history_version=None):
    result = []
    for table in sorted(table_names):
        columns = [
            row[1]
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        ]
        if table == HISTORY and exclude_history_version is not None:
            rows = connection.execute(
                f'SELECT * FROM "{table}" WHERE version != ? ORDER BY rowid',
                (exclude_history_version,),
            ).fetchall()
        else:
            rows = connection.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid'
            ).fetchall()
        result.append((table, tuple(columns), tuple(rows)))
    return tuple(result)


def inspect_database(connection):
    table_names = _tables(connection)
    present = TABLE in table_names
    if present:
        columns = tuple(
            (row[1], row[2].upper(), row[3], row[5])
            for row in connection.execute(f"PRAGMA table_info({TABLE})")
        )
        if columns != EXPECTED_COLUMNS:
            raise RuntimeError("Consultation request schema drift; manual review required.")
        indexes = list(connection.execute(f"PRAGMA index_list({TABLE})"))
        definitions = {
            row[1]: tuple(
                item[2]
                for item in connection.execute(f'PRAGMA index_info("{row[1]}")')
            )
            for row in indexes
        }
        if not any(row[2] and definitions[row[1]] == ("submission_token",) for row in indexes):
            raise RuntimeError("Consultation request idempotency constraint is missing.")
        if any(definitions.get(name) != (column,) for name, column in INDEXES.items()):
            raise RuntimeError("Consultation request index drift; manual review required.")
        checks = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,),
        ).fetchone()[0]
        if "ck_consultation_request_segment" not in checks or "ck_consultation_request_status" not in checks:
            raise RuntimeError("Consultation request constraints are missing.")
    applied = HISTORY in table_names and connection.execute(
        f"SELECT 1 FROM {HISTORY} WHERE version=?", (VERSION,)
    ).fetchone() is not None
    if applied and not present:
        raise RuntimeError("Applied migration has a missing table; manual review required.")
    return {"version": VERSION, "table_present": present, "applied": bool(applied)}


def apply_migration(database_path, backup_dir):
    path = Path(database_path).resolve(strict=True)
    backup_path = None
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            report = inspect_database(connection)
            if report["applied"]:
                connection.rollback()
                return {**report, "status": "already_applied"}
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Database integrity check failed.")

            existing_tables = _tables(connection)
            original_rows = _fingerprint(connection, existing_tables)
            directory = Path(backup_dir)
            directory.mkdir(parents=True, exist_ok=True)
            backup_path = directory / f"{path.name}.{VERSION}.{uuid.uuid4().hex}.bak"
            backup_path.touch(exist_ok=False)
            with closing(sqlite3.connect(path)) as source, closing(
                sqlite3.connect(backup_path)
            ) as backup:
                source.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise RuntimeError("Backup integrity check failed.")
                if _fingerprint(backup, existing_tables) != original_rows:
                    raise RuntimeError("Backup verification failed.")

            if not report["table_present"]:
                connection.execute(CREATE)
                for index_name, column in INDEXES.items():
                    connection.execute(
                        f"CREATE INDEX {index_name} ON {TABLE} ({column})"
                    )
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {HISTORY} "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, backup_path TEXT NOT NULL)"
            )
            connection.execute(
                f"INSERT INTO {HISTORY} VALUES (?, ?, ?)",
                (VERSION, datetime.now(timezone.utc).isoformat(), str(backup_path)),
            )
            inspect_database(connection)
            if _fingerprint(
                connection,
                existing_tables,
                exclude_history_version=VERSION,
            ) != original_rows:
                raise RuntimeError("Existing database records changed.")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Post-migration integrity check failed.")
            connection.commit()
            return {
                "status": "applied",
                "version": VERSION,
                "backup": str(backup_path),
                "original_records_unchanged": True,
            }
        except Exception as exc:
            connection.rollback()
            raise RuntimeError(
                f"Migration rolled back. Backup: {backup_path or 'not created'}. {exc}"
            ) from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "instance"
        / "migration_backups",
    )
    args = parser.parse_args()
    try:
        path = args.database.resolve(strict=True)
        if args.apply:
            result = apply_migration(path, args.backup_dir)
        else:
            with closing(sqlite3.connect(path)) as connection:
                result = inspect_database(connection)
        print(json.dumps({"database": str(path), **result}, indent=2))
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
