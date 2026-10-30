from sqlalchemy import inspect

from app.models import db


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
        return

    db.create_all()
    from app.seeds import seed_tariff_table, seed_subsidy_scheme
    seed_subsidy_scheme()
    seed_tariff_table()
