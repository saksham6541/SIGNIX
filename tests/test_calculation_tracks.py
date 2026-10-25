"""Regression coverage for residential/commercial calculation separation."""

from io import BytesIO
from unittest.mock import patch

import pytest
from pypdf import PdfReader

from app import solar_logic
from app.config import Config
from app.models import TariffTable, UserLocation, db
from app.services import estimation_service, location_service


def _payload(**updates):
    payload = {
        "address": "Track policy rooftop",
        "latitude": 28.6139,
        "longitude": 77.2090,
        "polygon": [
            [28.6139, 77.2090],
            [28.6139, 77.2092],
            [28.6141, 77.2092],
            [28.6141, 77.2090],
        ],
        "monthly_bill": 8000,
        "tariff_per_kwh": 9.0,
    }
    payload.update(updates)
    return payload


def _irradiance():
    return {month: 5.0 for month in solar_logic.MONTH_NAMES}


def test_business_session_track_overrides_client_property_and_persists(client, app):
    assert client.get("/survey/business").status_code == 200
    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(_irradiance(), None, "test_fixture", "low"),
    ):
        response = client.post(
            "/api/estimate",
            json=_payload(property_type="residential", load_profile="day_heavy"),
        )

    assert response.status_code == 200
    result = response.get_json()
    assert result["planning_track"] == "business"
    assert result["property_type"] == "commercial"
    assert result["subsidy_amount"] == 0
    assert result["demand_charge_savings"] is None
    assert result["annual_net_benefit"] == result["year_one_net_savings"]
    assert result["calculation_policy"]["cost_per_kw"] == Config.COMMERCIAL_COST_PER_KW

    with app.app_context():
        location = db.session.get(UserLocation, result["location_id"])
        assert location.extras["planning_track"] == "business"
        assert location.extras["property_type"] == "commercial"
        assert location.extras["calculation_policy"]["policy_version"]
        _, report = location_service.get_report_context(location.id, location.user_id)
        assert report["planning_track"] == "business"
        assert report["annual_roi_pct"] == result["annual_roi_pct"]
        assert report["chart_data"]["planning_track"] == "business"
        assert report["chart_data"]["financial_overview"]["annual_net_benefit"] == result["annual_net_benefit"]


def test_home_track_keeps_eligible_subsidy_and_both_payback_views(client):
    assert client.get("/survey/home").status_code == 200
    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(_irradiance(), None, "test_fixture", "low"),
    ):
        response = client.post("/api/estimate", json=_payload())

    assert response.status_code == 200
    result = response.get_json()
    assert result["planning_track"] == "home"
    assert result["property_type"] == "residential"
    assert result["subsidy_amount"] > 0
    assert result["payback_without_subsidy_years"] >= result["subsidy_adjusted_payback_years"]


def test_business_tariff_does_not_relabel_residential_state_row(app):
    with app.app_context():
        db.session.add(
            TariffTable(
                state="Delhi",
                utility_name="Residential test utility",
                rate_per_kwh=7.25,
                net_metering_mode="export_at_feed_in_rate",
                export_credit_rate=2.5,
            )
        )
        db.session.commit()

        home = estimation_service.lookup_tariff_defaults("Delhi", "home")
        business = estimation_service.lookup_tariff_defaults("Delhi", "business")

    assert home["found"] is True
    assert home["rate_per_kwh"] == 7.25
    assert business["found"] is False
    assert business["rate_per_kwh"] == Config.DEFAULT_COMMERCIAL_TARIFF
    assert "confirm" in business["notes"].lower()


def test_commercial_export_and_nonpositive_payback_are_conservative():
    no_value = solar_logic.calculate_financials(
        5,
        8000,
        0,
        property_type="commercial",
        planning_track="business",
        net_metering_mode="full_offset",
    )
    assert no_value["subsidy_amount"] == 0
    assert no_value["export_value"] == 0
    assert no_value["simple_payback_years"] is None
    assert no_value["payback_without_subsidy_years"] is None

    credited = solar_logic.calculate_financials(
        5,
        8000,
        9,
        property_type="commercial",
        planning_track="business",
        net_metering_mode="export_at_feed_in_rate",
        feed_in_tariff=3,
    )
    assert credited["exported_kwh"] > 0
    assert credited["export_value"] == pytest.approx(credited["exported_kwh"] * 3)
    assert credited["export_value"] < credited["exported_kwh"] * 9


def test_legacy_commercial_estimate_serializes_as_business(app):
    with app.app_context():
        location = UserLocation(
            user_id=1,
            address="Legacy commercial roof",
            latitude=19.0,
            longitude=72.0,
            system_size=10,
            annual_generation=14000,
            extras={"property_type": "commercial"},
        )
        db.session.add(location)
        db.session.commit()
        serialized = location.to_dict()

    assert serialized["planning_track"] == "business"
    assert serialized["extras"]["planning_track"] == "business"


def test_track_specific_report_copy(client, app):
    assert client.get("/survey/business").status_code == 200
    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(_irradiance(), None, "test_fixture", "low"),
    ):
        estimate = client.post("/api/estimate", json=_payload()).get_json()

    report = client.get(f"/report/{estimate['location_id']}")
    html = report.get_data(as_text=True)
    assert report.status_code == 200
    assert "Year-one net operating benefit" in html
    assert "Demand-charge savings are not modelled." in html
    assert "PM Surya Ghar subsidy estimate" not in html

    pdf = client.get(f"/report/{estimate['location_id']}/pdf?mode=summary")
    assert pdf.status_code == 200
    assert pdf.data.startswith(b"%PDF")
    text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(pdf.data)).pages
    )
    # WeasyPrint applies the summary-card CSS text transform while the
    # ReportLab fallback preserves source casing. PDF extractors may also
    # split a visual label across lines, so assert the rendered copy after
    # normalizing those renderer-specific differences.
    normalized_text = " ".join(text.split()).casefold()
    assert "annual net benefit" in normalized_text
    assert "BUSINESS SOLAR SUMMARY" in text
    assert "Demand-charge savings" in text
    assert "not modelled" in normalized_text
    assert "Central subsidy" not in text


def test_login_redirect_establishes_business_session_track(app):
    test_client = app.test_client()
    login = test_client.post(
        "/login?next=/survey/business",
        data={"email": "test@example.com", "password": "unused"},
    )
    assert login.status_code == 302
    assert login.headers["Location"].endswith("/survey/business")
    assert test_client.get(login.headers["Location"]).status_code == 200
    with test_client.session_transaction() as saved_session:
        assert saved_session["planning_track"] == "business"


def test_product_dashboards_filter_saved_estimates_and_use_track_metrics(client, app):
    with app.app_context():
        db.session.add_all(
            [
                UserLocation(
                    user_id=1,
                    address="Home-only rooftop",
                    latitude=28.61,
                    longitude=77.20,
                    system_size=4,
                    annual_generation=5600,
                    system_cost=220000,
                    subsidy_amount=78000,
                    net_investment=142000,
                    monthly_savings=4100,
                    extras={"planning_track": "home", "property_type": "residential"},
                ),
                UserLocation(
                    user_id=1,
                    address="Business-only site",
                    latitude=19.07,
                    longitude=72.87,
                    system_size=50,
                    annual_generation=72000,
                    net_investment=2350000,
                    monthly_savings=0,
                    extras={
                        "planning_track": "business",
                        "property_type": "commercial",
                        "annual_net_benefit": 420000,
                        "annual_roi_pct": 17.87,
                        "lifetime_net_benefit": 6200000,
                    },
                ),
            ]
        )
        db.session.commit()

    assert client.get("/survey/business").status_code == 200
    business = client.get("/dashboard").get_data(as_text=True)
    assert "Commercial solar decisions, in one workspace" in business
    assert "Business-only site" in business
    assert "Home-only rooftop" not in business
    assert "Annual net benefit" in business
    assert "Indicative subsidy" not in business

    assert client.get("/survey/home").status_code == 200
    home = client.get("/dashboard").get_data(as_text=True)
    assert "Your home solar plan, kept simple" in home
    assert "Home-only rooftop" in home
    assert "Business-only site" not in home
    assert "Indicative subsidy" in home
    assert "Annual net benefit" not in home
