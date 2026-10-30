"""Explicit, additive SQLite migration. Default mode is read-only inspection.

Run with the same environment as the server, or select an existing --database.
Never imports run.py or calls initialize_database/create_all.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import db  # noqa: E402

VERSION = "20261029_01_user_roles_preferences"
HISTORY = "signix_schema_migrations"
# Frozen migration, not a generic automatic model-to-schema synchronizer.
ADDITIONS = {
    "role": "VARCHAR(32) NOT NULL DEFAULT 'user'",
    "vendor_status": "VARCHAR(32)",
    "vendor_profile": "JSON",
    "vendor_credits_balance": "FLOAT NOT NULL DEFAULT 0.0",
    "default_tariff_per_kwh": "FLOAT",
    "default_state": "VARCHAR(128)",
    "default_property_type": "VARCHAR(32)",
    "language_preference": "VARCHAR(8) DEFAULT 'en'",
}
INDEXES = {"ix_users_role": "role", "ix_users_vendor_status": "vendor_status"}


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def connect(path, *, writable=False):
    return sqlite3.connect(
        Path(path).resolve().as_uri() + ("?mode=rw" if writable else "?mode=ro"),
        uri=True, timeout=5, isolation_level=None,
    )


def resolve_database(explicit=None):
    if explicit:
        path = Path(explicit).resolve()
    else:
        from app import create_app
        app = create_app()
        with app.app_context():
            url = db.engine.url
            if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
                raise RuntimeError("This migration requires an existing file-backed SQLite database.")
            path = Path(url.database).resolve()
            db.engine.dispose()
    if not path.is_file():
        raise RuntimeError(f"Database does not exist; refusing to create it: {path}")
    return path


def schema(connection):
    names = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]
    return {name: list(connection.execute(f"PRAGMA table_info({quote(name)})")) for name in names}


def inspect_database(connection):
    actual = schema(connection)
    missing_tables = []
    missing_columns = {}
    for table in db.metadata.sorted_tables:
        if table.name not in actual:
            missing_tables.append(table.name)
        else:
            present = {r[1] for r in actual[table.name]}
            missing = [c.name for c in table.columns if c.name not in present]
            if missing:
                missing_columns[table.name] = missing
    indexes = {r[1] for r in connection.execute('PRAGMA index_list("users")')}
    for name, column in INDEXES.items():
        if name in indexes:
            columns = [r[2] for r in connection.execute(f"PRAGMA index_info({quote(name)})")]
            if columns != [column]:
                raise RuntimeError(f"Unexpected definition for index {name}; manual review required.")
    applied = HISTORY in actual and connection.execute(
        f"SELECT 1 FROM {quote(HISTORY)} WHERE version=?", (VERSION,)
    ).fetchone() is not None
    return {
        "database": connection.execute("PRAGMA database_list").fetchone()[2],
        "version": VERSION, "applied": bool(applied),
        "users_columns": [dict(zip(("cid", "name", "type", "not_null", "default", "pk"), r))
                          for r in actual.get("users", [])],
        "missing_tables": missing_tables, "missing_columns": missing_columns,
        "missing_indexes": [name for name in INDEXES if name not in indexes],
        "row_counts": {name: connection.execute(f"SELECT count(*) FROM {quote(name)}").fetchone()[0]
                       for name in actual if name != HISTORY},
    }


def validate_scope(report):
    unknown = {table: cols for table, cols in report["missing_columns"].items()
               if table != "users" or set(cols) - ADDITIONS.keys()}
    if report["missing_tables"] or unknown:
        raise RuntimeError("Schema has missing tables/columns outside this version; inspect output and migrate explicitly.")
    if report["applied"] and (report["missing_columns"] or report["missing_indexes"]):
        raise RuntimeError("Applied migration has schema drift; refusing automatic repair.")


def fingerprints(connection, original_schema, *, exclude_history_version=None):
    """Compare all original values, without logging users, hashes or estimates."""
    result = {}
    for table, columns in original_schema.items():
        fields = ", ".join(quote(r[1]) for r in columns)
        digest = hashlib.sha256()
        count = 0
        if table == HISTORY and exclude_history_version is not None:
            rows = connection.execute(
                f"SELECT {fields} FROM {quote(table)} WHERE version != ? ORDER BY {fields}",
                (exclude_history_version,),
            )
        else:
            rows = connection.execute(
                f"SELECT {fields} FROM {quote(table)} ORDER BY {fields}"
            )
        for row in rows:
            digest.update(repr(row).encode("utf-8"))
            digest.update(b"\n")
            count += 1
        result[table] = (count, digest.hexdigest())
    return result


def backup_database(path, directory):
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = directory / f"{path.name}.{VERSION}.{stamp}.{uuid.uuid4().hex[:8]}.bak"
    backup.touch(exist_ok=False)
    # Separate read connection: writer already holds BEGIN IMMEDIATE, but has
    # made no changes. Other writers cannot race the snapshot and migration.
    with closing(connect(path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise RuntimeError(f"Backup integrity check failed: {backup}")
    return backup


def apply_migration(path, backup_dir):
    path = resolve_database(path)
    backup = None
    with closing(connect(path, writable=True)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            before = inspect_database(connection)
            validate_scope(before)
            if before["applied"]:
                connection.rollback()
                return {"status": "already_applied", "database": str(path), "columns_added": []}
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Database integrity check failed; no changes made.")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Existing foreign-key violations require review; no changes made.")
            original_schema = schema(connection)
            original_values = fingerprints(connection, original_schema)
            backup = backup_database(path, Path(backup_dir).resolve())
            with closing(connect(backup)) as saved:
                if schema(saved) != original_schema or fingerprints(saved, original_schema) != original_values:
                    raise RuntimeError("Backup does not match the locked source database.")
            print(f"Verified backup: {backup}", flush=True)
            added = before["missing_columns"].get("users", [])
            for name in added:
                connection.execute(f"ALTER TABLE users ADD COLUMN {quote(name)} {ADDITIONS[name]}")
            for name in before["missing_indexes"]:
                connection.execute(f"CREATE INDEX {quote(name)} ON users ({quote(INDEXES[name])})")
            connection.execute(f"CREATE TABLE IF NOT EXISTS {quote(HISTORY)} ("
                               "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, backup_path TEXT NOT NULL)")
            connection.execute(f"INSERT INTO {quote(HISTORY)} VALUES (?, ?, ?)",
                               (VERSION, datetime.now(timezone.utc).isoformat(), str(backup)))
            after = inspect_database(connection)
            validate_scope(after)
            if fingerprints(
                connection,
                original_schema,
                exclude_history_version=VERSION,
            ) != original_values:
                raise RuntimeError("Original record verification failed.")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Post-migration integrity check failed.")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Post-migration foreign-key check failed.")
            connection.commit()
            return {"status": "applied", "database": str(path), "version": VERSION,
                    "columns_added": added, "indexes_added": before["missing_indexes"],
                    "backup": str(backup), "original_records_unchanged": True,
                    "row_counts": after["row_counts"]}
        except Exception as exc:
            connection.rollback()
            raise RuntimeError(f"Migration rolled back. Backup: {backup or 'not created (no schema changes)'}. {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", help="Exact existing SQLite file; omit to resolve Flask configuration")
    parser.add_argument("--apply", action="store_true", help="Back up, then apply this version atomically")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "instance" / "migration_backups")
    args = parser.parse_args()
    try:
        path = resolve_database(args.database)
        if args.apply:
            result = apply_migration(path, args.backup_dir)
        else:
            with closing(connect(path)) as connection:
                result = inspect_database(connection)
        print(json.dumps(result, indent=2))
    except (RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
