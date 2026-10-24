from sqlalchemy import inspect, text

from app.models import ApplicationTrackerEvent, db


def initialize_database():
    """Create the schema and wipe legacy unowned locations once."""
    db.create_all()
    ApplicationTrackerEvent.__table__.create(bind=db.engine, checkfirst=True)
    inspector = inspect(db.engine)

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    user_settings_columns = {
        "default_tariff_per_kwh": "FLOAT",
        "default_state": "VARCHAR(128)",
        "default_property_type": "VARCHAR(32)",
        "language_preference": "VARCHAR(8)",
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
    if "user_id" not in columns:
        if db.engine.dialect.name != "sqlite":
            raise RuntimeError(
                "The PostgreSQL database contains a legacy user_locations table "
                "without user_id. Run an explicit data migration before startup."
            )

        # Legacy SQLite development databases had unowned locations. Recreate
        # that table only for SQLite; never delete data automatically in
        # PostgreSQL.
        db.session.execute(text("DROP TABLE user_locations"))
        db.session.commit()
        db.create_all()
        return

    if "user_priority" not in columns:
        db.session.execute(
            text("ALTER TABLE user_locations " "ADD COLUMN user_priority VARCHAR(32)")
        )
        db.session.commit()
        columns.add("user_priority")

    if "suitability_rating" not in columns:
        db.session.execute(
            text("ALTER TABLE user_locations " "ADD COLUMN suitability_rating JSON")
        )
        db.session.commit()
        columns.add("suitability_rating")

    # Scenario linking for multi-config compare on the same roof.
    scenario_additions = {
        "roof_group_id": "VARCHAR(36)",
        "parent_location_id": "INTEGER",
        "scenario_label": "VARCHAR(128)",
        "sizing_mode": "VARCHAR(64)",
    }
    for column_name, column_type in scenario_additions.items():
        if column_name not in columns:
            db.session.execute(
                text(
                    f"ALTER TABLE user_locations "
                    f"ADD COLUMN {column_name} {column_type}"
                )
            )
            columns.add(column_name)
    db.session.commit()

    # --- subsidy_scheme versioning columns (additive; safe on existing DBs) ---

    tables = set(inspector.get_table_names())
    if "rooftop_design_sources" in tables:
        source_cols = {
            column["name"]
            for column in inspector.get_columns("rooftop_design_sources")
        }
        source_additions = {
            "availability_status": "VARCHAR(32) DEFAULT 'available' NOT NULL",
            "coverage": "JSON",
            "cache_key": "VARCHAR(160)",
            "reusable_until": "TIMESTAMP",
            "is_usable": "BOOLEAN DEFAULT FALSE NOT NULL",
        }
        for column_name, column_type in source_additions.items():
            if column_name not in source_cols:
                db.session.execute(
                    text(
                        f"ALTER TABLE rooftop_design_sources "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
        db.session.commit()

    if "subsidy_scheme" in tables:
        subsidy_cols = {
            column["name"] for column in inspector.get_columns("subsidy_scheme")
        }
        subsidy_additions = {
            "scheme_code": "VARCHAR(64) DEFAULT 'pm_surya_ghar'",
            "amount_per_kw": "FLOAT",
            "fixed_amount": "FLOAT",
            "effective_from": "DATE",
            "effective_to": "DATE",
            "notes": "VARCHAR(256)",
        }
        for column_name, column_type in subsidy_additions.items():
            if column_name not in subsidy_cols:
                db.session.execute(
                    text(
                        f"ALTER TABLE subsidy_scheme "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
        db.session.commit()

    # Tariff table: net-metering defaults + export credit
    if "tariff_table" in inspector.get_table_names():
        tariff_cols = {
            column["name"] for column in inspector.get_columns("tariff_table")
        }
        tariff_additions = {
            "net_metering_mode": "VARCHAR(32)",
            "export_credit_rate": "FLOAT",
            "notes": "VARCHAR(256)",
        }
        for column_name, column_type in tariff_additions.items():
            if column_name not in tariff_cols:
                db.session.execute(
                    text(
                        f"ALTER TABLE tariff_table "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
        db.session.commit()

    try:
        from app.seeds import seed_tariff_table, seed_subsidy_scheme

        seed_subsidy_scheme()
        seed_tariff_table()
    except Exception as seed_exc:
        print(f"[WARN] Reference data seed skipped: {seed_exc}")
