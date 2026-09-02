# filename: run.py
"""
Entry point: creates the Flask app, initializes the database (creating
tables if they don't exist), seeds mock tariff/subsidy reference data if
empty, and runs the dev server.
"""
from app import create_app
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

    if SubsidyScheme.query.count() == 0:
        schemes = [
            # Reference only — live amounts computed in solar_logic.calculate_subsidy
            # (≤2 kW: ₹30k/kW; ≥3 kW: up to ₹78k). Off-grid → 0.
            SubsidyScheme(min_kw=0, max_kw=2, subsidy_amount=60000),
            SubsidyScheme(min_kw=2, max_kw=3, subsidy_amount=78000),
            SubsidyScheme(min_kw=3, max_kw=1000, subsidy_amount=78000),
        ]
        db.session.add_all(schemes)

    db.session.commit()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed_reference_data()

    app.run(debug=True, host="0.0.0.0", port=5000)
