"""Explicit additive SQLite migration; without --apply, inspect only.

Never initializes the application or alters existing tables. Stop application
writes before applying; every application verifies a unique online backup first.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.migrate_sqlite_users import connect, fingerprints, schema  # noqa: E402

VERSION = "20261030_01_vendor_saved_leads"
TABLE = "vendor_saved_leads"
HISTORY = "signix_schema_migrations"
CREATE = """CREATE TABLE vendor_saved_leads (
    id INTEGER NOT NULL PRIMARY KEY,
    vendor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_vendor_saved_lead UNIQUE(vendor_user_id, project_id)
)"""
INDEXES = {"ix_vendor_saved_leads_vendor_user_id": "vendor_user_id",
           "ix_vendor_saved_leads_project_id": "project_id"}


def inspect_database(connection):
    tables = schema(connection)
    for parent in ("users", "projects"):
        if parent not in tables or "id" not in {row[1] for row in tables[parent]}:
            raise RuntimeError("Existing users and projects tables are required; no changes made.")
    present = TABLE in tables
    if present:
        columns = [(row[1], row[2].upper(), row[3], row[5]) for row in tables[TABLE]]
        expected = [("id", "INTEGER", 1, 1), ("vendor_user_id", "INTEGER", 1, 0),
                    ("project_id", "INTEGER", 1, 0), ("created_at", "DATETIME", 1, 0)]
        if columns != expected:
            raise RuntimeError("Saved-lead schema drift; manual review required.")
        fks = {(r[2], r[3], r[4], r[6]) for r in connection.execute(f"PRAGMA foreign_key_list({TABLE})")}
        if fks != {("users", "vendor_user_id", "id", "CASCADE"), ("projects", "project_id", "id", "CASCADE")}:
            raise RuntimeError("Saved-lead foreign-key drift; manual review required.")
        indexes = list(connection.execute(f"PRAGMA index_list({TABLE})"))
        definitions = {r[1]: tuple(c[2] for c in connection.execute(f'PRAGMA index_info("{r[1]}")')) for r in indexes}
        if not any(r[2] and definitions[r[1]] == ("vendor_user_id", "project_id") for r in indexes):
            raise RuntimeError("Saved-lead uniqueness constraint is missing.")
        if any(definitions.get(name) != (column,) for name, column in INDEXES.items()):
            raise RuntimeError("Saved-lead index drift; manual review required.")
    applied = HISTORY in tables and connection.execute(
        f"SELECT 1 FROM {HISTORY} WHERE version=?", (VERSION,)).fetchone() is not None
    if applied and not present:
        raise RuntimeError("Applied migration has missing table; manual review required.")
    return {"version": VERSION, "table_present": present, "applied": bool(applied)}


def apply_migration(path, backup_dir):
    path = Path(path).resolve(strict=True)
    backup = None
    with closing(connect(path, writable=True)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            report = inspect_database(connection)
            if report["applied"]:
                connection.rollback()
                return {**report, "status": "already_applied"}
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Database integrity check failed.")
            original = schema(connection)
            original_values = fingerprints(connection, original)
            directory = Path(backup_dir)
            directory.mkdir(parents=True, exist_ok=True)
            backup = directory / f"{path.name}.{VERSION}.{uuid.uuid4().hex}.bak"
            backup.touch(exist_ok=False)
            with closing(connect(path)) as source, closing(sqlite3.connect(backup)) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or fingerprints(target, original) != original_values:
                    raise RuntimeError("Backup verification failed.")
            if not report["table_present"]:
                connection.execute(CREATE)
                for name, column in INDEXES.items():
                    connection.execute(f"CREATE INDEX {name} ON {TABLE} ({column})")
            connection.execute(f"CREATE TABLE IF NOT EXISTS {HISTORY} (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, backup_path TEXT NOT NULL)")
            connection.execute(f"INSERT INTO {HISTORY} VALUES (?, ?, ?)",
                               (VERSION, datetime.now(timezone.utc).isoformat(), str(backup)))
            inspect_database(connection)
            if fingerprints(
                connection,
                original,
                exclude_history_version=VERSION,
            ) != original_values:
                raise RuntimeError("Original records changed.")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Post-migration integrity check failed.")
            connection.commit()
            return {"status": "applied", "version": VERSION, "backup": str(backup), "original_records_unchanged": True}
        except Exception as exc:
            connection.rollback()
            raise RuntimeError(f"Migration rolled back. Backup: {backup or 'not created'}. {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path, help="Exact existing SQLite database file")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "instance" / "migration_backups")
    args = parser.parse_args()
    try:
        path = args.database.resolve(strict=True)
        if args.apply:
            result = apply_migration(path, args.backup_dir)
        else:
            with closing(connect(path)) as connection:
                result = inspect_database(connection)
        print(json.dumps({"database": str(path), **result}, indent=2))
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
