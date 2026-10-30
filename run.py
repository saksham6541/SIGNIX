# filename: run.py
"""
Entry point: creates the Flask app, initializes the database (creating
tables if they don't exist), seeds mock tariff/subsidy reference data if
empty, and runs the dev server.
"""

from dotenv import load_dotenv

load_dotenv()

from app import create_app
from app.database import initialize_database
from app.models import db, TariffTable, SubsidyScheme

app = create_app()


def seed_reference_data():
    """Populate TariffTable and SubsidyScheme with representative mock data
    if they're empty. In production these would be maintained via an admin
    panel or synced from a regulator's published tariff orders."""

    if TariffTable.query.count() == 0:
        tariffs = [
            TariffTable(state="Delhi", utility_name="BSES Rajdhani", rate_per_kwh=10.0),
            TariffTable(state="Uttar Pradesh", utility_name="UPPCL", rate_per_kwh=6.5),
            TariffTable(state="Maharashtra", utility_name="MSEDCL", rate_per_kwh=8.2),
            TariffTable(state="Karnataka", utility_name="BESCOM", rate_per_kwh=7.8),
            TariffTable(state="Tamil Nadu", utility_name="TANGEDCO", rate_per_kwh=6.9),
            TariffTable(state="Gujarat", utility_name="UGVCL", rate_per_kwh=9.5),
            TariffTable(state="West Bengal", utility_name="WBSEDCL", rate_per_kwh=9.8),
            TariffTable(state="Rajasthan", utility_name="JVVNL", rate_per_kwh=9.5),
        ]
        db.session.add_all(tariffs)

    # Versioned PM Surya Ghar slabs (see app/seeds.py and scripts/seed_subsidy_scheme.py).
    # To revise policy later: INSERT a new set with a later effective_from —
    # do not edit historical rows in place.
    from app.seeds import seed_subsidy_scheme

    seed_subsidy_scheme(force=False)




if __name__ == "__main__":
    with app.app_context():
        initialize_database()
        seed_reference_data()

    app.run(debug=True, host="127.0.0.1", port=5000)
