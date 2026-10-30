"""Commercial repair contracts; provider calls are replaced by an explicit fixture."""
import json
import re
from copy import deepcopy
from unittest.mock import patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app import solar_logic
from app.models import AdminActionLog, Project, ProjectStatusLog, User, UserLocation, VendorLeadUnlock, db
from app.services.commercial_calculation_service import calculate_commercial_project, project_for_location
from app.services import estimation_service


@pytest.fixture
def solar_fixture(monkeypatch):
    monkeypatch.setattr(solar_logic, "fetch_solar_data", lambda *a, **kw:
                        ({m: 5.0 for m in solar_logic.MONTH_NAMES}, None, "test_fixture", "low"))


def payload():
    return {"address": "Fixture business roof", "latitude": 28.614, "longitude": 77.209,
            "polygon": [[28.6139, 77.2090], [28.6139, 77.2092], [28.6141, 77.2092], [28.6141, 77.2090]],
            "monthly_bill": 10000, "tariff_per_kwh": 10, "loan_rate_pct": 0,
            "loan_tenure_years": 7, "ppa_rate_rs_per_kwh": 5, "ppa_tenure_years": 10}


def csrf(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True)).group(1)


@pytest.mark.parametrize("rate", [None, ""])
def test_missing_ppa_quote_is_unavailable(rate):
    model = calculate_commercial_project(10, 15000, 10, ppa_rate_rs_per_kwh=rate)["models"]["resco"]
    assert model["ppa_rate_basis"] == "quote_required"
    assert model["year_one_savings_inr"] is None
    assert model["total_after_25_years"] is None
    assert model["cash_flows"] == []


@pytest.mark.parametrize("rate", [0, -1, "bad", float("nan"), float("inf"), True])
def test_invalid_supplied_ppa_is_not_an_absent_quote(rate):
    with pytest.raises(ValueError):
        calculate_commercial_project(10, 15000, 10, ppa_rate_rs_per_kwh=rate)


@pytest.mark.parametrize("rate,sign", [(12, -1), (2, 1)])
def test_ppa_retains_losses_and_stops_at_contract_end(rate, sign):
    result = calculate_commercial_project(10, 15000, 10, ppa_rate_rs_per_kwh=rate)
    model = result["models"]["resco"]
    assert model["year_one_savings_inr"] * sign > 0
    assert model["annual_ppa_cost_inr"] == 15000 * rate
    assert len(model["cash_flows"]) == 11
    assert model["total_after_10_years"] == round(sum(model["cash_flows"]), 2)
    assert model["total_after_25_years"] is None
    assert model["payback_years"] is None


def test_ppa_escalates_cost_separately_and_tariff_benefits_both_ownership_models():
    base = calculate_commercial_project(10, 15000, 10, ppa_rate_rs_per_kwh=3, annual_tariff_escalation=0)
    rising = calculate_commercial_project(10, 15000, 10, ppa_rate_rs_per_kwh=3, annual_tariff_escalation=10)
    expensive = calculate_commercial_project(10, 15000, 10, ppa_rate_rs_per_kwh=3,
                                            annual_tariff_escalation=0, ppa_escalation_pct=10)
    assert rising["financing_comparison"]["assumptions"]["tariff_escalation_pct"] == 10
    for key in ("buy", "loan", "resco"):
        assert rising["models"][key]["cash_flows"][2] > base["models"][key]["cash_flows"][2]
    assert expensive["models"]["resco"]["cash_flows"][2] < base["models"]["resco"]["cash_flows"][2]
    delta_buy = rising["models"]["buy"]["cash_flows"][2] - base["models"]["buy"]["cash_flows"][2]
    delta_loan = rising["models"]["loan"]["cash_flows"][2] - base["models"]["loan"]["cash_flows"][2]
    assert delta_buy == pytest.approx(delta_loan, abs=.02)


def test_buy_and_loan_share_net_operating_cashflows_and_om():
    result = calculate_commercial_project(10, 15000, 10, loan_rate_pct=0, loan_tenure_years=5)
    buy, loan = result["models"]["buy"], result["models"]["loan"]
    authoritative = solar_logic.calculate_financials(10, 15000, 10, planning_track="business", load_profile="day_heavy")
    assert buy["annual_om_inr"] == authoritative["annual_om"] > 0
    assert buy["year_one_savings_inr"] == authoritative["annual_net_benefit"]
    assert loan["annual_operating_benefit_inr"] == buy["year_one_savings_inr"]
    assert loan["monthly_emi_inr"] == round(loan["loan_amount_inr"] / 60, 2)
    for year in range(1, 26):
        expected_payment = loan["monthly_emi_inr"] * 12 if year <= 5 else 0
        assert loan["cash_flows"][year] == pytest.approx(buy["cash_flows"][year] - expected_payment, abs=.02)
    assert result["depreciation_enabled"] is False


@pytest.mark.parametrize("field", ["system_size_kw", "annual_generation_kwh", "tariff_per_kwh"])
def test_missing_authoritative_input_never_infers_generation(field):
    args = dict(system_size_kw=10, annual_generation_kwh=15000, tariff_per_kwh=10)
    args[field] = None
    with pytest.raises(ValueError):
        calculate_commercial_project(**args)


def test_browser_json_and_normal_form_use_authoritative_pipeline(client, app, solar_fixture):
    body = payload()
    response = client.post("/business/estimate", json=body)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["annual_generation"] != 12000  # representative consumption is not generation
    with app.app_context():
        loc = db.session.get(UserLocation, data["location_id"])
        assert loc.system_size == data["system_size"] > 0
        assert loc.annual_generation == data["annual_generation"] > 0
        assert loc.monthly_data == data["monthly_data"]
        assert loc.net_investment == data["net_investment"] > 0
        assert loc.subsidy_amount == 0
        assert loc.polygon_geojson["coordinates"][0][0] == [77.2090, 28.6139]
        compare = project_for_location(loc)
        assert compare["models"]["buy"]["year_one_savings_inr"] == loc.extras["annual_net_benefit"]
        assert compare["models"]["loan"]["cash_flows"] == loc.extras["financing_comparison"]["dpa"]["cash_flows"]
    html = client.get(data["business_result_url"]).get_data(as_text=True)
    assert "test_fixture" in html
    assert "25-year cumulative cash flow" in html
    assert "Unavailable" in html
    assert client.get(data["redirect_url"]).status_code == 200
    form = {**body, "polygon": json.dumps(body["polygon"]), "obstructions": "[]"}
    confirmed = client.post("/business/estimate/confirm", data=form)
    assert confirmed.status_code == 302
    assert "/business/estimate/result/" in confirmed.headers["Location"]


@pytest.mark.parametrize(
    ("provided_tariff", "expected_basis"),
    [(True, "user_supplied"), (False, "signix_configurable_default")],
)
def test_commercial_assessment_persists_tariff_provenance_and_creates_project(
    client, app, solar_fixture, provided_tariff, expected_basis
):
    survey_html = client.get("/survey/business").get_data(as_text=True)
    tariff_input = re.search(r'<input[^>]*id="tariff-per-kwh"[^>]*>', survey_html).group()
    assert 'value=""' in tariff_input
    assert "not an official utility tariff" in survey_html

    body = payload()
    if not provided_tariff:
        body.pop("tariff_per_kwh")

    response = client.post("/business/estimate", json=body)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["tariff_provenance"]["basis"] == expected_basis

    with app.app_context():
        location = db.session.get(UserLocation, data["location_id"])
        owner_id = location.user_id
        assert location.extras["tariff_provenance"]["basis"] == expected_basis
        assert location.extras["tariff_provenance"]["rate_per_kwh"] == data["tariff_provenance"]["rate_per_kwh"]
        assert location.extras["calculation_policy"]["residential_subsidy_may_apply"] is False
        assert location.subsidy_amount == 0

    result_html = client.get(data["business_result_url"]).get_data(as_text=True)
    assert "Tariff source" in result_html
    assert "SIGNIX configurable planning assumptions" in result_html
    assert "not an installer quote" in result_html
    if provided_tariff:
        assert "User-entered commercial rate; not independently verified" in result_html
    else:
        assert "SIGNIX configurable default; confirm against your utility bill" in result_html

    app.config["WTF_CSRF_ENABLED"] = True
    token = csrf(client, data["business_result_url"])
    project_response = client.post(
        "/projects/create",
        data={
            "csrf_token": token,
            "location_id": data["location_id"],
            "contact_name": "QA Commercial Contact",
            "email": "qa-commercial@example.com",
            "consent_given": "1",
            "consent_fee": "1",
        },
    )
    assert project_response.status_code == 302
    assert data["business_result_url"] in project_response.headers["Location"]
    with app.app_context():
        project = Project.query.one()
        assert project.location_id == data["location_id"]
        assert project.user_id == owner_id
        assert project.consent_given is True
        assert project.contact_name == "QA Commercial Contact"
        assert project.phone is None
        assert project.status == "under_review"


def test_obstruction_aware_commercial_assessment_persists_geometry(client, app, solar_fixture):
    body = payload()
    body["obstructions"] = [[
        [28.61395, 77.20905],
        [28.61395, 77.20915],
        [28.61405, 77.20915],
        [28.61405, 77.20905],
    ]]

    response = client.post("/business/estimate", json=body)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    with app.app_context():
        location = db.session.get(UserLocation, data["location_id"])
        assert location.roof_area_sqm > 0
        assert location.obstructed_area_sqm > 0
        assert location.usable_area_sqm == pytest.approx(
            location.roof_area_sqm - location.obstructed_area_sqm, abs=0.1
        )
        assert location.obstructions_geojson["type"] == "MultiPolygon"
        assert location.subsidy_amount == 0


@pytest.mark.parametrize("geometry", [[], [[1, 2]], "invalid", [[float("nan"), 1], [1, 2], [3, 4]]])
def test_incomplete_geometry_is_rejected_before_estimation(client, app, geometry):
    body = payload(); body["polygon"] = geometry
    with patch.object(estimation_service, "run_full_estimation") as engine:
        response = client.post("/business/estimate", json=body)
    assert response.status_code == 400
    engine.assert_not_called()
    with app.app_context():
        assert UserLocation.query.count() == 0


@pytest.mark.parametrize("failure", ["engine", "commit", "comparison"])
def test_assessment_failure_leaves_no_partial_record(client, app, solar_fixture, failure):
    if failure == "engine":
        target, name, error = estimation_service, "run_full_estimation", RuntimeError("provider failed")
    elif failure == "commit":
        target, name, error = db.session, "commit", SQLAlchemyError("persistence failed")
    else:
        import app.pages as pages
        target, name, error = pages, "project_for_location", ValueError("Calculated projection unavailable")
    with patch.object(target, name, side_effect=error):
        response = client.post("/business/estimate", json=payload())
    assert response.status_code == (400 if failure == "comparison" else 503)
    with app.app_context():
        assert UserLocation.query.count() == 0


def make_project(app, status="under_review"):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").one()
        location = UserLocation(user_id=user.id, address="Legacy assessment", latitude=28, longitude=77,
                                extras={"planning_track": "business"})
        db.session.add(location); db.session.flush()
        project = Project(user_id=user.id, location_id=location.id, status=status, owner="Fixture", consent_given=True)
        db.session.add(project); db.session.commit()
        return project.id, location.id, user.id


def test_admin_review_pages_and_real_transition_history(client, app):
    first, _, owner = make_project(app)
    second, _, _ = make_project(app)
    assert client.get("/admin/projects").status_code == 403
    with app.app_context():
        db.session.get(User, owner).role = "admin"; db.session.commit()
    app.config["WTF_CSRF_ENABLED"] = True
    token = csrf(client, "/admin")
    assert client.get("/admin/projects").status_code == 200
    assert client.post(f"/admin/projects/{first}/approve").status_code == 400
    assert client.post(f"/admin/projects/{first}/approve", data={"csrf_token": token}).status_code == 302
    assert client.post(f"/admin/projects/{second}/reject", data={"csrf_token": token, "reason": "Incomplete"}).status_code == 302
    with app.app_context():
        logs = ProjectStatusLog.query.order_by(ProjectStatusLog.id).all()
        assert [(log.from_status, log.to_status) for log in logs] == [("under_review", "open_to_vendors"), ("under_review", "closed")]
        assert db.session.get(Project, second).closed_at is not None
        assert AdminActionLog.query.count() == 2
    client.post(f"/admin/projects/{first}/reject", data={"csrf_token": token})
    with app.app_context():
        assert db.session.get(Project, first).status == "open_to_vendors"
        assert ProjectStatusLog.query.count() == 2


def test_vendor_outcome_does_not_close_or_reopen_projects(client, app):
    project_id, _, user_id = make_project(app, "closed")
    with app.app_context():
        user = db.session.get(User, user_id); user.role = "vendor"; user.vendor_status = "approved"
        db.session.commit()
    client.post(f"/vendor/board/{project_id}/status", data={"outcome": "won"})
    with app.app_context():
        assert AdminActionLog.query.count() == 0
        db.session.add(VendorLeadUnlock(vendor_user_id=user_id, project_id=project_id, credits_charged=2))
        db.session.commit()
    for outcome in ("lost", "won", "won", "invalid"):
        assert client.post(f"/vendor/board/{project_id}/status", data={"outcome": outcome}).status_code == 302
    with app.app_context():
        assert db.session.get(Project, project_id).status == "closed"
        assert ProjectStatusLog.query.count() == 0
        events = AdminActionLog.query.order_by(AdminActionLog.id).all()
        assert [event.detail for event in events] == ["lost", "won"]
        assert all(event.actor_user_id == user_id and event.target_type == "vendor_lead_unlock" for event in events)


def test_project_forms_with_real_csrf_create_close_delete(client, app, solar_fixture):
    data = client.post("/business/estimate", json=payload()).get_json()
    app.config["WTF_CSRF_ENABLED"] = True
    token = csrf(client, data["business_result_url"])
    values = {"location_id": data["location_id"], "consent_given": "1", "consent_fee": "1"}
    assert client.post("/projects/create", data=values).status_code == 400
    assert client.post("/projects/create", data={**values, "csrf_token": token}).status_code == 302
    with app.app_context():
        project_id = Project.query.one().id
    detail = client.get(f"/projects/{project_id}").get_data(as_text=True)
    assert 'name="csrf_token"' in detail
    assert "cumulative cash flow" in detail
    assert client.post(f"/projects/{project_id}/close").status_code == 400
    assert client.post(f"/projects/{project_id}/close", data={"csrf_token": token}).status_code == 302
    assert client.post(f"/projects/{project_id}/delete").status_code == 400
    assert client.post(f"/projects/{project_id}/delete", data={"csrf_token": token}).status_code == 302
    with app.app_context():
        assert Project.query.count() == 0
        assert UserLocation.query.count() == 1


def test_legacy_placeholder_is_unavailable_not_recalculated(client, app):
    project_id, location_id, _ = make_project(app)
    with app.app_context():
        before = deepcopy(db.session.get(UserLocation, location_id).extras)
        generation_before = db.session.get(UserLocation, location_id).annual_generation
    response = client.get(f"/business/estimate/result/{location_id}")
    assert response.status_code == 200
    assert "no usable calculated projection" in response.get_data(as_text=True)
    detail = client.get(f"/projects/{project_id}").get_data(as_text=True)
    assert "Model-specific projections are unavailable" in detail
    assert "yearly_savings *" not in detail
    with app.app_context():
        assert db.session.get(UserLocation, location_id).annual_generation == generation_before
        assert db.session.get(UserLocation, location_id).extras == before


def test_recalculation_and_clone_preserve_custom_loan_terms(client, app, solar_fixture):
    data = client.post("/business/estimate", json=payload()).get_json()
    with app.app_context():
        location = db.session.get(UserLocation, data["location_id"])
        estimation_service.apply_orientation(location, 180)
        saved = location.extras["financing_comparison"]
        assert saved["dpa"]["annual_rate_pct"] == 0
        assert saved["dpa"]["tenure_years"] == 7
        assert project_for_location(location)["models"]["loan"]["cash_flows"] == saved["dpa"]["cash_flows"]
        result, clone, _ = estimation_service.clone_estimate_with_change(location.user_id, location.id, "battery_kwh", 5)
        assert clone.extras["loan_rate_pct"] == 0
        assert clone.extras["ppa_rate_rs_per_kwh"] == 5
        assert clone.extras["financing_comparison"]["dpa"]["tenure_years"] == 7
        assert clone.extras["financing_comparison"] == result["financing_comparison"]
        assert clone.subsidy_amount == 0


def test_result_and_project_ownership_remain_enforced(client, app):
    project_id, location_id, _ = make_project(app)
    with app.app_context():
        other = User(email="other-repair@example.test", password_hash="unused", display_name="Other")
        db.session.add(other); db.session.flush()
        db.session.get(Project, project_id).user_id = other.id
        db.session.get(UserLocation, location_id).user_id = other.id
        db.session.commit()
    assert client.get(f"/business/estimate/result/{location_id}").status_code == 404
    assert client.get(f"/projects/{project_id}").status_code == 404
    assert client.post(f"/projects/{project_id}/close").status_code == 404
    assert client.post(f"/projects/{project_id}/delete").status_code == 404


def test_ppa_contract_25_years_is_available_and_corrupt_lifecycle_is_rejected():
    financials = solar_logic.calculate_financials(10, 15000, 10, planning_track="business", load_profile="day_heavy")
    result = calculate_commercial_project(10, 15000, 10, financials=financials,
                                          ppa_rate_rs_per_kwh=3, ppa_tenure_years=25)
    assert len(result["models"]["resco"]["cash_flows"]) == 26
    assert result["models"]["resco"]["total_after_25_years"] is not None
    financials["lifecycle_projection"]["years"][0]["net_cash_flow"] += 100
    with pytest.raises(ValueError, match="Inconsistent lifecycle"):
        calculate_commercial_project(10, 15000, 10, financials=financials)


@pytest.mark.parametrize("field,value", [("polygon", 123), ("obstructions", "{}"),
                                        ("state", []), ("daytime_power_use", {}),
                                        ("loan_rate_pct", "NaN"), ("ppa_tenure_years", 2.5)])
def test_malformed_assessment_fields_are_client_errors(client, field, value):
    body = payload(); body[field] = value
    with patch.object(estimation_service, "run_full_estimation") as engine:
        response = client.post("/business/estimate", json=body)
    assert response.status_code == 400
    engine.assert_not_called()


def test_assessment_json_requires_real_csrf_header(client, app, solar_fixture):
    app.config["WTF_CSRF_ENABLED"] = True
    token = csrf(client, "/business/estimate")
    assert client.post("/business/estimate", json=payload()).status_code == 400
    response = client.post("/business/estimate", json=payload(), headers={"X-CSRFToken": token})
    assert response.status_code == 200
    with app.app_context():
        assert UserLocation.query.count() == 1


def test_commercial_comparison_rejects_residential_financials():
    financials = solar_logic.calculate_financials(10, 15000, 10, planning_track="home")
    with pytest.raises(ValueError, match="without residential subsidy"):
        calculate_commercial_project(10, 15000, 10, financials=financials)
