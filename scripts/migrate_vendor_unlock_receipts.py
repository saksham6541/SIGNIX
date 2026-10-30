"""Explicit SQLite migration preserving installer unlock receipts on project deletion.

Inspection is read-only unless --apply is supplied. Application startup never runs
this migration. Stop application writers and select the exact database file.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migrate_sqlite_users import connect, fingerprints, quote, schema  # noqa: E402

VERSION = "20261030_02_preserve_vendor_unlock_receipts"
HISTORY = "signix_schema_migrations"
TABLE = "vendor_lead_unlocks"
TEMP_TABLE = "vendor_lead_unlocks_receipt_migration"
EXPECTED_COLUMNS = [
    ("id", "INTEGER", 1, 1),
    ("vendor_user_id", "INTEGER", 1, 0),
    ("project_id", "INTEGER", 1, 0),
    ("credits_charged", "FLOAT", 1, 0),
    ("unlocked_at", "DATETIME", 1, 0),
    ("source", "VARCHAR(64)", 1, 0),
]
CREATE = f"""CREATE TABLE {TEMP_TABLE} (
    id INTEGER NOT NULL PRIMARY KEY,
    vendor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    credits_charged FLOAT NOT NULL,
    unlocked_at DATETIME NOT NULL,
    source VARCHAR(64) NOT NULL,
    CONSTRAINT uq_vendor_lead_unlock_vendor_project UNIQUE(vendor_user_id, project_id)
)"""
INDEXES = {
    "ix_vendor_lead_unlocks_vendor_user_id": "vendor_user_id",
    "ix_vendor_lead_unlocks_project_id": "project_id",
}


def resolve_database(explicit):
    path = Path(explicit).resolve()
    if not path.is_file():
        raise RuntimeError(f"Database does not exist; refusing to create it: {path}")
    return path


def _foreign_keys(connection):
    return {
        (row[2], row[3], row[4]): str(row[6] or "").upper()
        for row in connection.execute(f"PRAGMA foreign_key_list({quote(TABLE)})")
    }


def _indexes(connection):
    rows = list(connection.execute(f"PRAGMA index_list({quote(TABLE)})"))
    return {
        row[1]: {
            "unique": bool(row[2]),
            "origin": row[3],
            "partial": bool(row[4]),
            "columns": tuple(
                item[2]
                for item in connection.execute(
                    f"PRAGMA index_info({quote(row[1])})"
                )
            ),
        }
        for row in rows
    }


def inspect_database(connection):
    tables = schema(connection)
    for parent in ("users", "projects", TABLE):
        if parent not in tables:
            raise RuntimeError(f"Required table {parent} is missing; no changes made.")
    columns = [
        (row[1], str(row[2]).upper(), row[3], row[5])
        for row in tables[TABLE]
    ]
    if columns != EXPECTED_COLUMNS:
        raise RuntimeError("Unlock-receipt column drift; manual review required.")
    extended_columns = list(
        connection.execute(f"PRAGMA table_xinfo({quote(TABLE)})")
    )
    if len(extended_columns) != len(EXPECTED_COLUMNS) or any(
        row[6] != 0 for row in extended_columns
    ):
        raise RuntimeError(
            "Unlock-receipt hidden/generated column drift; manual review required."
        )

    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone()
    table_sql = str(table_sql_row[0] if table_sql_row else "")
    if (
        re.search(r"\bCHECK\s*\(", table_sql, flags=re.IGNORECASE)
        or re.search(r"\bWITHOUT\s+ROWID\b", table_sql, flags=re.IGNORECASE)
        or re.search(r"\bSTRICT\s*$", table_sql, flags=re.IGNORECASE)
    ):
        raise RuntimeError(
            "Unlock-receipt table has unsupported constraints or table options; "
            "manual review is required before rebuilding it."
        )

    foreign_key_rows = list(
        connection.execute(f"PRAGMA foreign_key_list({quote(TABLE)})")
    )
    if len(foreign_key_rows) != 2:
        raise RuntimeError(
            "Unlock-receipt table has unexpected foreign keys; manual review required."
        )
    foreign_keys = _foreign_keys(connection)
    user_action = foreign_keys.get(("users", "vendor_user_id", "id"))
    project_action = foreign_keys.get(("projects", "project_id", "id"))
    if user_action != "CASCADE" or project_action not in {"CASCADE", "RESTRICT", "NO ACTION"}:
        raise RuntimeError("Unlock-receipt foreign-key drift; manual review required.")

    indexes = _indexes(connection)
    unique_index_names = [
        name
        for name, item in indexes.items()
        if item["unique"]
        and item["columns"] == ("vendor_user_id", "project_id")
        and item["origin"] == "u"
        and not item["partial"]
    ]
    if len(unique_index_names) != 1:
        raise RuntimeError("Unlock-receipt uniqueness constraint is missing.")
    for name, column in INDEXES.items():
        if name not in indexes or indexes[name]["columns"] != (column,):
            raise RuntimeError(f"Unlock-receipt index drift for {name}.")
        if indexes[name]["unique"] or indexes[name]["partial"]:
            raise RuntimeError(f"Unlock-receipt index drift for {name}.")
    recognized_indexes = set(INDEXES) | set(unique_index_names)
    unexpected_indexes = sorted(set(indexes) - recognized_indexes)
    if unexpected_indexes:
        raise RuntimeError(
            "Unlock-receipt custom indexes cannot be preserved automatically: "
            + ", ".join(unexpected_indexes)
            + ". Manual review is required; no changes made."
        )

    triggers = list(
        connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
            (TABLE,),
        )
    )
    if triggers:
        names = ", ".join(sorted(row[0] for row in triggers))
        raise RuntimeError(
            "Unlock-receipt custom triggers cannot be preserved automatically: "
            f"{names}. Manual review is required; no changes made."
        )

    applied = HISTORY in tables and connection.execute(
        f"SELECT 1 FROM {quote(HISTORY)} WHERE version=?", (VERSION,)
    ).fetchone() is not None
    protected = project_action in {"RESTRICT", "NO ACTION"}
    if applied and not protected:
        raise RuntimeError("Applied migration has unsafe foreign-key drift.")
    return {
        "version": VERSION,
        "applied": bool(applied),
        "project_delete_action": project_action,
        "protected": protected,
        "unlock_receipts": connection.execute(
            f"SELECT count(*) FROM {quote(TABLE)}"
        ).fetchone()[0],
    }


def _backup_database(path, directory, original_schema, original_values):
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = directory / f"{path.name}.{VERSION}.{stamp}.{uuid.uuid4().hex[:8]}.bak"
    backup.touch(exist_ok=False)
    with closing(connect(path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    with closing(connect(backup)) as saved:
        if saved.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise RuntimeError("Backup integrity check failed.")
        if schema(saved) != original_schema:
            raise RuntimeError("Backup schema does not match the locked source.")
        if fingerprints(saved, original_schema) != original_values:
            raise RuntimeError("Backup records do not match the locked source.")
    return backup


def _rebuild_table(connection):
    connection.execute(CREATE)
    connection.execute(
        f"INSERT INTO {TEMP_TABLE} "
        "(id, vendor_user_id, project_id, credits_charged, unlocked_at, source) "
        f"SELECT id, vendor_user_id, project_id, credits_charged, unlocked_at, source FROM {TABLE}"
    )
    connection.execute(f"DROP TABLE {TABLE}")
    connection.execute(f"ALTER TABLE {TEMP_TABLE} RENAME TO {TABLE}")
    for name, column in INDEXES.items():
        connection.execute(
            f"CREATE INDEX {quote(name)} ON {quote(TABLE)} ({quote(column)})"
        )


def apply_migration(path, backup_dir):
    path = resolve_database(path)
    backup = None
    with closing(connect(path, writable=True)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            before = inspect_database(connection)
            if before["applied"]:
                connection.rollback()
                return {"status": "already_applied", "database": str(path), **before}
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Database integrity check failed; no changes made.")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Existing foreign-key violations require review.")

            original_schema = schema(connection)
            original_values = fingerprints(connection, original_schema)
            backup = _backup_database(
                path, Path(backup_dir).resolve(), original_schema, original_values
            )

            if not before["protected"]:
                _rebuild_table(connection)
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {quote(HISTORY)} ("
                "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, backup_path TEXT NOT NULL)"
            )
            connection.execute(
                f"INSERT INTO {quote(HISTORY)} VALUES (?, ?, ?)",
                (VERSION, datetime.now(timezone.utc).isoformat(), str(backup)),
            )

            after = inspect_database(connection)
            if not after["protected"] or not after["applied"]:
                raise RuntimeError("Receipt protection was not installed.")
            if fingerprints(
                connection, original_schema, exclude_history_version=VERSION
            ) != original_values:
                raise RuntimeError("Original record verification failed.")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Post-migration integrity check failed.")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Post-migration foreign-key check failed.")
            connection.commit()
            return {
                "status": "applied",
                "database": str(path),
                "version": VERSION,
                "backup": str(backup),
                "project_delete_action": after["project_delete_action"],
                "unlock_receipts_preserved": after["unlock_receipts"],
                "original_records_unchanged": True,
            }
        except Exception as exc:
            connection.rollback()
            raise RuntimeError(
                f"Migration rolled back. Backup: {backup or 'not created'}. {exc}"
            ) from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=ROOT / "instance" / "migration_backups",
    )
    args = parser.parse_args()
    try:
        path = resolve_database(args.database)
        if args.apply:
            result = apply_migration(path, args.backup_dir)
        else:
            with closing(connect(path)) as connection:
                result = {"database": str(path), **inspect_database(connection)}
        print(json.dumps(result, indent=2))
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
