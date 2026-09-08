from sqlalchemy import inspect, text

from app.models import db


def initialize_database():
    """Create the schema and wipe legacy unowned locations once."""
    db.create_all()
    inspector = inspect(db.engine)

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    user_settings_columns = {
        "default_tariff_per_kwh": "FLOAT",
        "default_state": "VARCHAR(128)",
        "default_property_type": "VARCHAR(32)",
    }
    for column_name, column_type in user_settings_columns.items():
        if column_name not in user_columns:
            db.session.execute(
                text(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
            )
    db.session.commit()

    if "user_locations" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("user_locations")}
    if "user_id" in columns:
        return

    if db.engine.dialect.name != "sqlite":
        raise RuntimeError(
            "The PostgreSQL database contains a legacy user_locations table "
            "without user_id. Run an explicit data migration before startup."
        )

    # Legacy SQLite development databases had unowned locations. Recreate that
    # table only for SQLite; never delete data automatically in PostgreSQL.
    db.session.execute(text("DROP TABLE user_locations"))
    db.session.commit()
    db.create_all()
