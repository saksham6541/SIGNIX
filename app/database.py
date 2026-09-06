from sqlalchemy import inspect, text

from app.models import db


def initialize_database():
    """Create the schema and wipe legacy unowned locations once."""
    db.create_all()
    inspector = inspect(db.engine)
    if "user_locations" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("user_locations")}
    if "user_id" in columns:
        return

    if db.engine.dialect.name == "sqlite":
        db.session.execute(text("DROP TABLE user_locations"))
    else:
        db.session.execute(text("DELETE FROM user_locations"))
        db.session.execute(
            text(
                "ALTER TABLE user_locations "
                "ADD COLUMN user_id INTEGER NOT NULL REFERENCES users(id)"
            )
        )
    db.session.commit()
    db.create_all()
