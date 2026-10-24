import json
from pathlib import Path

import pytest

from app.services.report_chart_data import build_report_chart_data


MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _estimate(**overrides):
    data = {
        "annual_generation": 1200.0,
        "monthly_data": {month: 100.0 for month in MONTHS},
        "system_size": 1.0,
        "generation_25yr": [round(1200 * (0.995 ** year), 1) for year in range(25)],
        "lifetime_kwh": round(sum(1200 * (0.995 ** year) for year in range(25))),
        "net_investment": 100000.0,
        "cashflow_25yr": [-40000.0, 30000.0] + [100000.0 + year * 70000 for year in range(23)],
        "annual_om": 1200.0,
        "monthly_savings": 5000.0,
        "bill_sizing": {
            "monthly_bill": 8000.0,
            "monthly_units": 800.0,
            "tariff_per_kwh": 10.0,
        },
        "self_consumption_frac": 0.6,
        "exported_kwh": 480.0,
        "tariff_escalation_pct": 4.0,
        "lcoe": 3.25,
        "roof_area_sqm": 100.0,
        "obstructed_area_sqm": 20.0,
        "usable_area_sqm": 80.0,
        "panel_count": 4,
        "orientation_deg": 180.0,
        "orientation_label": "South",
        "orientation_factor": 1.0,
        "recommended_tilt_deg": 28.0,
        "co2_reduction_tons": 1.4,
        "environmental_equivalents": {
            "trees_planted": 64,
            "km_not_driven": 5600,
            "fuel_liters_saved": 600,
        },
        "financing_comparison": {
            "capex": {
                "down_payment": 100000,
                "payback_years": 1.6,
                "total_cash_outflow": 100000,
                "cash_flows": [-100000, 60000, 70000],
            },
            "dpa": {
                "down_payment": 20000,
                "monthly_emi": 1700,
                "annual_emi": 20400,
                "payback_years": 2.2,
                "total_cash_outflow": 122000,
                "cash_flows": [-20000, 39600, 49600],
            },
            "assumptions": {"dpa_rate_pct": 10.0},
        },
    }
    data.update(overrides)
    return data


def test_monthly_generation_and_annual_consistency():
    chart = build_report_chart_data(_estimate())
    monthly = chart["energy"]["monthly_generation"]
    assert [item["month"] for item in monthly] == MONTHS
    assert sum(item["generation_kwh"] for item in monthly) == pytest.approx(
        chart["energy"]["annual_generation"]
    )
    assert chart["diagnostics"]["annual_generation_delta"] == 0


def test_generation_25yr_first_year_and_lifetime_are_consistent():
    chart = build_report_chart_data(_estimate())
    series = chart["energy"]["generation_25yr"]
    assert len(series) == 25
    assert series[0] == chart["energy"]["annual_generation"]
    assert sum(series) == pytest.approx(chart["energy"]["lifetime_generation"])
    assert all(series[index] <= series[index - 1] for index in range(1, 25))
    assert chart["energy"]["cumulative_generation_25yr"][-1] == pytest.approx(
        chart["energy"]["lifetime_generation"]
    )


def test_generation_fallback_uses_estimator_degradation_assumption():
    chart = build_report_chart_data(_estimate(generation_25yr=None))
    series = chart["energy"]["generation_25yr"]
    assert series[0] == 1200.0
    assert series[1] == pytest.approx(1194.0)
    assert chart["energy"]["generation_25yr_source"] == "derived_with_estimator_degradation"


def test_monthly_consumption_is_unavailable_without_real_profile():
    chart = build_report_chart_data(_estimate())
    assert chart["consumption"]["annual_consumption"] == 9600
    assert chart["consumption"]["monthly_available"] is False
    assert chart["consumption"]["monthly_consumption"] == []
    assert "does not provide" in chart["consumption"]["unavailable_reason"]


def test_real_monthly_consumption_profile_is_serialized():
    profile = {month: 80 + index for index, month in enumerate(MONTHS)}
    chart = build_report_chart_data(_estimate(monthly_consumption=profile))
    assert chart["consumption"]["monthly_available"] is True
    assert chart["consumption"]["monthly_consumption"] == list(profile.values())


def test_financial_series_cumulative_savings_and_payback():
    chart = build_report_chart_data(_estimate())
    financial = chart["financial"]
    assert financial["annual_savings"][:2] == [60000.0, 70000.0]
    assert financial["cumulative_savings"][:2] == [60000.0, 130000.0]
    assert financial["cumulative_cashflow"][:2] == [-40000.0, 30000.0]
    assert financial["payback_year"] == 1.6


def test_capex_dpa_comparison_uses_persisted_cashflows():
    financing = build_report_chart_data(_estimate())["financing"]
    assert financing["available"] is True
    assert financing["capex"]["cumulative_cashflow"] == [-100000.0, -40000.0, 30000.0]
    assert financing["dpa"]["cumulative_cashflow"] == [-20000.0, 19600.0, 69200.0]
    assert financing["dpa"]["annual_payment"] == 20400


def test_energy_flow_and_bill_impact_use_model_outputs():
    chart = build_report_chart_data(_estimate())
    assert chart["energy_flow"] == {
        "available": True,
        "solar_generation_kwh": 1200.0,
        "self_consumed_kwh": 720.0,
        "grid_export_kwh": 480.0,
        "grid_import_kwh": 8880.0,
    }
    assert chart["monthly_bill_impact"]["estimated_bill_with_solar"] == 3000
    assert chart["monthly_bill_impact"]["savings_pct"] == 62.5


def test_missing_data_has_explicit_unavailable_states():
    chart = build_report_chart_data({})
    assert chart["energy"]["monthly_generation"] == []
    assert chart["energy"]["generation_25yr"] == []
    assert chart["energy_flow"]["available"] is False
    assert chart["financial"]["available"] is False
    assert chart["financing"]["available"] is False
    assert chart["electricity_cost"]["available"] is False
    assert chart["monthly_bill_impact"]["available"] is False


@pytest.mark.parametrize(
    "cashflow",
    ([1000.0], [1000.0] * 24 + [float("nan")]),
)
def test_malformed_lifecycle_cashflow_fails_closed(cashflow):
    chart = build_report_chart_data(_estimate(cashflow_25yr=cashflow))

    assert chart["financial"] == {
        "available": False,
        "reason": "Persisted annual cashflow series unavailable",
    }
    json.dumps(chart, allow_nan=False)


def test_financial_chart_rejects_missing_or_non_finite_required_investment():
    for investment in (None, float("inf"), -1):
        chart = build_report_chart_data(_estimate(net_investment=investment))
        assert chart["financial"]["available"] is False
        json.dumps(chart, allow_nan=False)


def test_financial_chart_rejects_inconsistent_lifecycle_rows():
    cashflow = _estimate()["cashflow_25yr"]
    previous = -100000.0
    rows = []
    for index, cumulative in enumerate(cashflow, start=1):
        rows.append({
            "year": index,
            "net_cash_flow": cumulative - previous,
            "cumulative_cash_flow": cumulative,
        })
        previous = cumulative
    rows[8]["cumulative_cash_flow"] += 500

    chart = build_report_chart_data(
        _estimate(lifecycle_projection={"years": rows})
    )

    assert chart["financial"]["available"] is False


def test_inconsistent_financing_cashflows_fail_closed():
    financing = _estimate()["financing_comparison"]
    financing["dpa"]["cash_flows"] = financing["dpa"]["cash_flows"][:-1]

    chart = build_report_chart_data(_estimate(financing_comparison=financing))

    assert chart["financing"] == {
        "available": False,
        "reason": "Financing cash-flow series are inconsistent",
    }


def test_orientation_and_system_size_changes_produce_fresh_data():
    original = build_report_chart_data(_estimate())
    changed = build_report_chart_data(
        _estimate(
            system_size=2.0,
            annual_generation=2200.0,
            monthly_data={month: 2200 / 12 for month in MONTHS},
            generation_25yr=[round(2200 * (0.995 ** year), 1) for year in range(25)],
            orientation_deg=90.0,
            orientation_label="East",
            orientation_factor=0.9,
        )
    )
    assert changed["roof_utilization"]["system_size_kw"] == 2.0
    assert changed["energy"]["annual_generation"] == 2200.0
    assert changed["energy"]["generation_25yr"] != original["energy"]["generation_25yr"]
    assert changed["orientation"]["azimuth_degrees"] == 90.0
    assert changed["orientation"]["factor"] == 0.9


def test_chart_data_is_json_serializable():
    encoded = json.dumps(build_report_chart_data(_estimate()))
    assert '"schema_version": "1.0"' in encoded


def test_chart_renderer_destroys_instances_before_replacement():
    source = (
        Path(__file__).parents[1] / "app" / "static" / "js" / "report-charts.js"
    ).read_text(encoding="utf-8")
    assert "if (charts[name]) charts[name].destroy()" in source
    assert "signix:chart-data" in source
    assert "document.addEventListener('DOMContentLoaded', window.SIGNIX_REPORT_CHARTS.init)" in source
    assert "renderVisible" in source
    assert "health" in source
    assert "panel.dataset.bound === 'true'" in source


def test_report_renders_serialized_chart_contract(client, app):
    from app.models import User, UserLocation, db

    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="Chart report",
            latitude=28.614,
            longitude=77.209,
            polygon_geojson={
                "type": "Polygon",
                "coordinates": [[
                    [77.209, 28.614], [77.21, 28.614],
                    [77.21, 28.615], [77.209, 28.614],
                ]],
            },
            system_size=1.0,
            annual_generation=1200.0,
            monthly_data={month: 100 for month in MONTHS},
            roof_area_sqm=100,
            usable_area_sqm=80,
            obstructed_area_sqm=20,
            net_investment=100000,
            monthly_savings=5000,
            co2_reduction_tons=1.4,
            extras={
                "cashflow_25yr": [-40000, 30000] + [100000] * 23,
                "generation_25yr": [round(1200 * (0.995 ** year), 1) for year in range(25)],
                "environmental_equivalents": {
                    "trees_planted": 64,
                    "km_not_driven": 5600,
                    "fuel_liters_saved": 600,
                },
            },
        )
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    response = client.get(f"/report/{location_id}")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "let REPORT_CHART_DATA" in body
    assert 'id="monthlyGenerationChart"' in body
    assert 'id="annualSavingsChart"' in body
    assert 'id="roofUtilizationChart"' in body
    assert 'id="financing-controls"' not in body
    assert 'js/vendor/chart.umd.min.js' in body
    assert 'id="rpt-summary"' in body
    assert 'id="rpt-suitability"' in body
    assert 'id="rpt-rooftop"' in body
    assert 'id="rpt-sizing"' in body
    assert 'id="rpt-financing"' in body
    assert 'id="rpt-environmental"' in body
    assert 'id="rpt-assumptions"' in body
    assert 'id="rpt-next-steps"' in body
    assert 'data-roof3d-view="3d" aria-pressed="true"' in body
    assert 'class="roof3d-compass-ring" role="slider"' in body
    assert 'data-source-sizing-mode=' in body
    assert 'Choose a value different from this saved estimate before cloning.' in body
    assert 'Enter a bill offset from 20% to 100%.' in body


def _financing_location(app):
    from app.models import User, UserLocation, db
    from app.solar_logic import build_financing_comparison, current_assumption_fingerprint

    comparison = build_financing_comparison(100000, 60000, 5000, 1200)
    cumulative_cashflow = [-40000, 30000] + [100000] * 23
    previous = -100000
    lifecycle_years = []
    for year, cumulative in enumerate(cumulative_cashflow, start=1):
        lifecycle_years.append({
            "year": year,
            "net_cash_flow": cumulative - previous,
            "cumulative_cash_flow": cumulative,
        })
        previous = cumulative
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="Financing preview",
            latitude=28.614,
            longitude=77.209,
            system_size=1.0,
            annual_generation=1200.0,
            monthly_data={month: 100 for month in MONTHS},
            roof_area_sqm=100,
            usable_area_sqm=80,
            system_cost=100000,
            subsidy_amount=0,
            net_investment=100000,
            monthly_savings=5000,
            extras={
                "assumption_fingerprint": current_assumption_fingerprint(),
                "annual_savings": 60000,
                "annual_om": 0,
                "tariff_per_kwh": 10,
                "feed_in_tariff": None,
                "net_metering_mode": "full_offset",
                "bill_sizing": {
                    "monthly_bill": 8000,
                    "monthly_units": 800,
                    "tariff_per_kwh": 10,
                },
                "cashflow_25yr": cumulative_cashflow,
                "financing_comparison": comparison,
                "lifecycle_projection": {"years": lifecycle_years},
                "financial_provenance": {
                    "inputs": {
                        "generation_kwh": 1200,
                        "system_size_kw": 1,
                        "system_cost": 100000,
                        "battery_cost": 0,
                        "subsidy_amount": 0,
                        "tariff_per_kwh": 10,
                        "feed_in_tariff_per_kwh": None,
                        "net_metering_mode": "full_offset",
                    }
                },
                "generation_25yr": [round(1200 * (0.995 ** year), 1) for year in range(25)],
            },
        )
        db.session.add(location)
        db.session.commit()
        return location.id


def test_financing_preview_recalculates_dpa_and_chart_data(client, app):
    location_id = _financing_location(app)
    baseline = client.post(
        f"/api/estimate/{location_id}/financing-preview",
        json={"down_payment_pct": 20, "dpa_rate_pct": 10, "dpa_tenure_years": 5},
    )
    changed = client.post(
        f"/api/estimate/{location_id}/financing-preview",
        json={"down_payment_pct": 35, "dpa_rate_pct": 14, "dpa_tenure_years": 8},
    )
    assert baseline.status_code == changed.status_code == 200
    first = baseline.get_json()
    second = changed.get_json()
    assert first["financing_comparison"]["dpa"]["loan_amount"] == 80000
    assert second["financing_comparison"]["dpa"]["loan_amount"] == 65000
    assert second["financing_comparison"]["dpa"]["monthly_emi"] != first["financing_comparison"]["dpa"]["monthly_emi"]
    assert second["financing_comparison"]["dpa"]["total_repayment"] != first["financing_comparison"]["dpa"]["total_repayment"]
    assert second["financing_comparison"]["dpa"]["payback_years"] != first["financing_comparison"]["dpa"]["payback_years"]
    assert second["chart_data"]["financing"]["dpa"]["cumulative_cashflow"] != first["chart_data"]["financing"]["dpa"]["cumulative_cashflow"]
    assert second["chart_data"]["electricity_cost"]["solar_dpa_lifetime_cost"] != first["chart_data"]["electricity_cost"]["solar_dpa_lifetime_cost"]


def test_financing_preview_reuses_canonical_lifecycle_cashflows(client, app):
    from app.models import User, UserLocation, db
    from app.solar_logic import calculate_financials, current_assumption_fingerprint

    result = calculate_financials(
        system_size_kw=4.0,
        annual_generation_kwh=6_000,
        tariff_per_kwh=8.0,
        net_metering_mode="export_at_feed_in_rate",
        feed_in_tariff=3.0,
        annual_consumption_kwh=3_000,
    )
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="Canonical financing preview",
            latitude=28.614,
            longitude=77.209,
            system_size=4.0,
            annual_generation=6_000,
            monthly_data={month: 500 for month in MONTHS},
            roof_area_sqm=100,
            usable_area_sqm=80,
            system_cost=result["system_cost"],
            subsidy_amount=result["subsidy_amount"],
            net_investment=result["net_investment"],
            monthly_savings=result["monthly_savings"],
            extras={
                "assumption_fingerprint": current_assumption_fingerprint(),
                "annual_savings": result["annual_savings"],
                "annual_om": result["annual_om"],
                "tariff_per_kwh": 8.0,
                "feed_in_tariff": result["feed_in_tariff"],
                "net_metering_mode": result["net_metering_mode"],
                "financial_provenance": result["financial_provenance"],
                "lifecycle_projection": result["lifecycle_projection"],
                "financing_comparison": result["financing_comparison"],
            },
        )
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    response = client.post(
        f"/api/estimate/{location_id}/financing-preview",
        json={"down_payment_pct": 25, "dpa_rate_pct": 11, "dpa_tenure_years": 7},
    )
    assert response.status_code == 200
    preview = response.get_json()["financing_comparison"]
    canonical = [
        row["net_cash_flow"] for row in result["lifecycle_projection"]["years"]
    ]
    assert preview["capex"]["cash_flows"][1:] == pytest.approx(canonical)
    assert preview["capex"]["cash_flows"][1] == pytest.approx(
        result["annual_savings"] - result["annual_om"]
    )
    naive_whole_value_escalation = (
        result["annual_savings"] * 0.995 * 1.04 - result["annual_om"]
    )
    assert canonical[1] != pytest.approx(naive_whole_value_escalation)
    assert preview["assumptions"]["annual_om"] == result["annual_om"]
    assert preview["dpa"]["loan_amount"] == pytest.approx(
        result["net_investment"] * 0.75
    )
    assert preview["dpa"]["cash_flows"][1] == pytest.approx(
        canonical[0] - preview["dpa"]["annual_emi"], abs=0.01
    )
    assert preview["dpa"]["monthly_operating_benefit"] == pytest.approx(
        canonical[0] / 12, abs=0.01
    )
    assert preview["dpa"]["covers_emi"] == (
        preview["dpa"]["monthly_operating_benefit"]
        >= preview["dpa"]["monthly_emi"]
    )


def test_financing_preview_fails_closed_without_saved_cashflow_series(client, app):
    from app.models import User, UserLocation, db
    from app.solar_logic import current_assumption_fingerprint

    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        location = UserLocation(
            user_id=user.id,
            address="Legacy financing data",
            latitude=28.614,
            longitude=77.209,
            system_size=1.0,
            annual_generation=1_200,
            monthly_data={month: 100 for month in MONTHS},
            system_cost=100_000,
            net_investment=100_000,
            monthly_savings=5_000,
            extras={
                "assumption_fingerprint": current_assumption_fingerprint(),
                "annual_savings": 60_000,
                "tariff_per_kwh": 10,
                "feed_in_tariff": None,
                "net_metering_mode": "full_offset",
                "financial_provenance": {
                    "inputs": {
                        "generation_kwh": 1_200,
                        "system_size_kw": 1,
                        "system_cost": 100_000,
                        "battery_cost": 0,
                        "subsidy_amount": 0,
                        "tariff_per_kwh": 10,
                        "feed_in_tariff_per_kwh": None,
                        "net_metering_mode": "full_offset",
                    }
                },
            },
        )
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    response = client.post(
        f"/api/estimate/{location_id}/financing-preview",
        json={"down_payment_pct": 20, "dpa_rate_pct": 10, "dpa_tenure_years": 5},
    )
    assert response.status_code == 409
    assert "no valid saved 25-year lifecycle" in response.get_json()["error"]


def test_financing_preview_fails_closed_for_stale_financial_model(client, app):
    location_id = _financing_location(app)
    from app.models import UserLocation, db

    with app.app_context():
        location = db.session.get(UserLocation, location_id)
        extras = dict(location.extras)
        extras["assumption_fingerprint"] = "stale-fingerprint"
        location.extras = extras
        db.session.commit()

    response = client.post(
        f"/api/estimate/{location_id}/financing-preview",
        json={"down_payment_pct": 20, "dpa_rate_pct": 10, "dpa_tenure_years": 5},
    )
    assert response.status_code == 409
    assert "legacy or stale" in response.get_json()["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {"down_payment_pct": -1, "dpa_rate_pct": 10, "dpa_tenure_years": 5},
        {"down_payment_pct": 101, "dpa_rate_pct": 10, "dpa_tenure_years": 5},
        {"down_payment_pct": 20, "dpa_rate_pct": -1, "dpa_tenure_years": 5},
        {"down_payment_pct": 20, "dpa_rate_pct": 51, "dpa_tenure_years": 5},
        {"down_payment_pct": 20, "dpa_rate_pct": 10, "dpa_tenure_years": 0},
        {"down_payment_pct": 20, "dpa_rate_pct": 10, "dpa_tenure_years": 5.5},
        {"down_payment_pct": "NaN", "dpa_rate_pct": 10, "dpa_tenure_years": 5},
    ],
)
def test_financing_preview_rejects_invalid_inputs(client, app, payload):
    location_id = _financing_location(app)
    response = client.post(
        f"/api/estimate/{location_id}/financing-preview", json=payload
    )
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_financing_preview_requires_authentication_and_json(client, app):
    location_id = _financing_location(app)
    anonymous = app.test_client()
    unauthenticated = anonymous.post(
        f"/api/estimate/{location_id}/financing-preview",
        json={"down_payment_pct": 20, "dpa_rate_pct": 10, "dpa_tenure_years": 5},
    )
    malformed = client.post(
        f"/api/estimate/{location_id}/financing-preview",
        data="not-json",
        content_type="application/json",
    )
    assert unauthenticated.status_code in (302, 401, 403)
    assert malformed.status_code == 400
    assert malformed.get_json()["error"] == "A JSON object is required"


def test_report_renders_dpa_controls_for_financing_data(client, app):
    location_id = _financing_location(app)
    response = client.get(f"/report/{location_id}")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="financing-controls"' in body
    assert 'id="dpa-down-payment-range"' in body
    assert 'id="dpa-interest-rate-range"' in body
    assert 'id="dpa-tenure-range"' in body
    assert f'/api/estimate/{location_id}/financing-preview' in body
    assert "Year-1 operating benefit after O&amp;M" in body
    assert "25-year cumulative operating benefit after O&amp;M" in body
