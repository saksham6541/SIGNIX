import logging

from sqlalchemy import inspect

from app.models import db


logger = logging.getLogger(__name__)


def _unlock_project_delete_action(inspector):
    foreign_keys = inspector.get_foreign_keys("vendor_lead_unlocks")
    matches = [
        foreign_key
        for foreign_key in foreign_keys
        if foreign_key.get("constrained_columns") == ["project_id"]
        and foreign_key.get("referred_table") == "projects"
        and foreign_key.get("referred_columns") == ["id"]
    ]
    if len(matches) != 1:
        return None
    return str((matches[0].get("options") or {}).get("ondelete") or "NO ACTION").upper()


def _handle_unsafe_unlock_constraint(dialect, ondelete):
    if dialect == "sqlite":
        raise RuntimeError(
            "Database schema (sqlite) requires the explicit backed-up "
            "unlock-receipt migration; startup made no changes. Run "
            "scripts/migrate_vendor_unlock_receipts.py as documented in "
            "docs/vendor_unlock_receipt_integrity.md. The migration must "
            "change vendor_lead_unlocks.project_id from ON DELETE CASCADE "
            "to RESTRICT without changing receipt or ledger records."
        )
    logger.warning(
        "Database dialect %s still reports "
        "vendor_lead_unlocks.project_id ON DELETE %s. Application-level "
        "project archival remains active, but the database-level "
        "receipt-preservation migration is deferred for this dialect; "
        "do not hard-delete projects outside the application workflow.",
        dialect,
        ondelete or "UNKNOWN",
    )


def initialize_database():
    """Create a fresh schema, or validate an existing one without migrating it.

    Existing installations must be backed up and explicitly migrated by an
    operator. Startup must never alter or recreate their tables.
    """
    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    if existing:
        missing = []
        for table in db.metadata.sorted_tables:
            if table.name not in existing:
                missing.append(table.name + " (table)")
                continue
            columns = {column["name"] for column in inspector.get_columns(table.name)}
            missing.extend(f"{table.name}.{column.name}" for column in table.columns
                           if column.name not in columns)
        if missing:
            raise RuntimeError(
                "Database schema requires an explicit backed-up migration; startup made no changes. "
                "Missing: " + ", ".join(missing) + ". "
                "For legacy users columns see scripts/migrate_sqlite_users.py and "
                "docs/sqlite_user_schema_migration.md. For vendor_saved_leads see "
                "scripts/migrate_vendor_saved_leads.py and docs/installer_leads.md. "
                "Other schema gaps require a reviewed migration."
            )
        if "vendor_lead_unlocks" in existing:
            ondelete = _unlock_project_delete_action(inspector)
            if ondelete not in {"RESTRICT", "NO ACTION"}:
                _handle_unsafe_unlock_constraint(db.engine.dialect.name, ondelete)
        return

    db.create_all()
    from app.seeds import seed_tariff_table, seed_subsidy_scheme
    seed_subsidy_scheme()
    seed_tariff_table()
