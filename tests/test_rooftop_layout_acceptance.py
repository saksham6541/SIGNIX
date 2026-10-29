from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from app import solar_logic
from app.models import RooftopDesign, User, UserLocation, db
from app.services import estimation_service


ROOF = [
    [28.6139, 77.2090], [28.6139, 77.2092],
    [28.6141, 77.2092], [28.6141, 77.2090],
]


@pytest.fixture
def layout_context(client, app):
    irradiance = {month: 5.0 for month in solar_logic.MONTH_NAMES}
    with patch.object(solar_logic, "fetch_solar_data", return_value=(irradiance, None, "test_fixture", "low")):
        estimate = client.post("/api/estimate", json={
            "address": "Layout acceptance roof", "latitude": 28.614,
            "longitude": 77.2091, "polygon": ROOF, "tariff_per_kwh": 8,
        }).get_json()
    location_id = estimate["location_id"]
    model = client.get(f"/api/roof-model/{location_id}?high_fidelity=0").get_json()
    panel = {
        "surface_id": model["surfaces"][0]["id"],
        "corners_local_xz_meters": [
            [-0.567, -0.881], [0.567, -0.881],
            [0.567, 0.881], [-0.567, 0.881],
        ],
    }
    payload = {
        "design_revision_id": model["design_revision"]["id"],
        "design_revision_number": model["design_revision"]["number"],
        "geometry_key": model["geometry_key"],
        "layout_mode": "planning_preview",
        "panels": [panel],
    }
    return location_id, model, payload, irradiance


def test_viewing_preview_does_not_mutate_saved_estimate(client, app, layout_context):
    location_id, _, _, _ = layout_context
    with app.app_context():
        before = db.session.get(UserLocation, location_id).system_size
        revision_count = RooftopDesign.query.filter_by(user_location_id=location_id).count()
    client.get(f"/api/roof-model/{location_id}")
    with app.app_context():
        assert db.session.get(UserLocation, location_id).system_size == before
        assert RooftopDesign.query.filter_by(user_location_id=location_id).count() == revision_count


def test_acceptance_derives_capacity_and_recalculates_estimate(client, app, layout_context):
    location_id, _, payload, irradiance = layout_context
    with patch.object(solar_logic, "fetch_solar_data", return_value=(irradiance, None, "test_fixture", "low")):
        response = client.post(f"/api/locations/{location_id}/rooftop-layout/accept", json=payload)

    assert response.status_code == 201
    data = response.get_json()
    layout = data["accepted_layout"]
    assert layout["panel_count"] == 1
    assert layout["dc_capacity_kw"] == pytest.approx(.545)
    assert layout["layout_mode"] == "planning_preview"
    assert layout["shade_verified"] is False
    assert layout["shade_status"] == "insufficient_data"
    assert data["calculation"]["system_size"] == pytest.approx(.545)
    assert data["calculation"]["sizing_method"] == "accepted_layout"
    assert data["calculation"]["generation_model"] == "location_irradiance_azimuth_adjustment"
    assert data["calculation"]["surface_generation"]["panel_count"] == 1
    assert data["calculation"]["surface_generation"]["surfaces"][0]["shade_loss_percent"] is None
    assert data["calculation"]["annual_generation"] > 0
    assert data["calculation"]["net_investment"] >= 0
    with app.app_context():
        saved = db.session.get(UserLocation, location_id)
        current = RooftopDesign.query.filter_by(user_location_id=location_id, is_current=True).one()
        assert saved.system_size == pytest.approx(.545)
        assert saved.extras["accepted_layout"]["status"] == "accepted"
        orientation = saved.extras["generation_provenance"]["orientation"]
        assert orientation["global_direction_source"] == "footprint_longest_edge_heuristic"
        assert saved.extras["orientation_input"] == "auto"
        plane = saved.extras["surface_generation"]["surfaces"][0]
        assert plane["roof_pitch_degrees"] is None
        assert plane["surface_geometry_kind"] == layout["surface_generation_inputs"][0]["surface_geometry_kind"]
        assert saved.extras["surface_generation_status"] == "current"
        assert saved.extras["surface_generation"]["geometry_key"] == layout["geometry_key"]
        assert current.panel_layout["panel_count"] == 1


@pytest.mark.parametrize("field", ["panel_count", "capacity_kw", "annual_generation", "financials"])
def test_rejects_client_authoritative_values(client, layout_context, field):
    location_id, _, payload, _ = layout_context
    payload[field] = 999
    response = client.post(f"/api/locations/{location_id}/rooftop-layout/accept", json=payload)
    assert response.status_code == 400
    assert response.get_json()["code"] == "client_values_not_allowed"


def test_rejects_stale_revision_and_geometry_key(client, layout_context):
    location_id, _, payload, _ = layout_context
    stale = {**payload, "design_revision_number": payload["design_revision_number"] + 1}
    assert client.post(f"/api/locations/{location_id}/rooftop-layout/accept", json=stale).get_json()["code"] == "stale_design_revision"
    incompatible = {**payload, "geometry_key": "tampered"}
    response = client.post(f"/api/locations/{location_id}/rooftop-layout/accept", json=incompatible)
    assert response.status_code == 409
    assert response.get_json()["code"] == "incompatible_geometry_key"


def test_rejects_outside_panel_and_conservative_unknown(client, layout_context):
    location_id, _, payload, _ = layout_context
    outside = {**payload, "panels": [{**payload["panels"][0], "corners_local_xz_meters": [[100, 100], [101.134, 100], [101.134, 101.762], [100, 101.762]]}]}
    response = client.post(f"/api/locations/{location_id}/rooftop-layout/accept", json=outside)
    assert response.status_code == 400
    assert response.get_json()["code"] == "panel_geometry_invalid"
    conservative = {**payload, "layout_mode": "conservative"}
    response = client.post(f"/api/locations/{location_id}/rooftop-layout/accept", json=conservative)
    assert response.status_code == 400
    assert response.get_json()["code"] == "panel_classification_invalid"


def test_rejects_panel_intersecting_manual_obstruction(client, layout_context):
    location_id, _, _, _ = layout_context
    obstruction = {"type": "MultiPolygon", "coordinates": [[[
        [77.209095, 28.613995], [77.209105, 28.613995],
        [77.209105, 28.614005], [77.209095, 28.614005],
        [77.209095, 28.613995],
    ]]]}
    revised = client.post(
        f"/api/locations/{location_id}/rooftop-design",
        json={"manual_obstructions_geojson": obstruction},
    )
    assert revised.status_code == 201
    model = client.get(f"/api/roof-model/{location_id}").get_json()
    payload = {
        "design_revision_id": model["design_revision"]["id"],
        "design_revision_number": model["design_revision"]["number"],
        "geometry_key": model["geometry_key"], "layout_mode": "planning_preview",
        "panels": [{"surface_id": model["surfaces"][0]["id"],
                    "corners_local_xz_meters": [[-.567, -.881], [.567, -.881], [.567, .881], [-.567, .881]]}],
    }
    response = client.post(f"/api/locations/{location_id}/rooftop-layout/accept", json=payload)
    assert response.status_code == 400
    assert response.get_json()["code"] == "panel_geometry_invalid"


def test_failed_recalculation_rolls_back_revision_and_estimate(client, app, layout_context):
    location_id, _, payload, _ = layout_context
    with app.app_context():
        before_size = db.session.get(UserLocation, location_id).system_size
        before_count = RooftopDesign.query.filter_by(user_location_id=location_id).count()
    with patch.object(estimation_service, "recalculate_for_accepted_layout", side_effect=RuntimeError("forced")):
        response = client.post(f"/api/locations/{location_id}/rooftop-layout/accept", json=payload)
    assert response.status_code == 500
    assert response.get_json()["code"] == "recalculation_failed"
    with app.app_context():
        assert db.session.get(UserLocation, location_id).system_size == before_size
        assert RooftopDesign.query.filter_by(user_location_id=location_id).count() == before_count


def test_repeated_acceptance_is_idempotent(client, app, layout_context):
    location_id, _, payload, irradiance = layout_context
    with patch.object(solar_logic, "fetch_solar_data", return_value=(irradiance, None, "test_fixture", "low")):
        first = client.post(f"/api/locations/{location_id}/rooftop-layout/accept", json=payload)
        second = client.post(f"/api/locations/{location_id}/rooftop-layout/accept", json=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json()["idempotent"] is True
    with app.app_context():
        assert RooftopDesign.query.filter_by(user_location_id=location_id).count() == 2


def test_changed_calculation_assumptions_are_not_treated_as_idempotent(
    client, app, layout_context, monkeypatch
):
    location_id, _, payload, irradiance = layout_context
    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(irradiance, None, "test_fixture", "low"),
    ):
        first = client.post(
            f"/api/locations/{location_id}/rooftop-layout/accept", json=payload
        )
    assert first.status_code == 201

    monkeypatch.setattr(solar_logic, "CALCULATION_MODEL_VERSION", "test-change")
    second = client.post(
        f"/api/locations/{location_id}/rooftop-layout/accept", json=payload
    )

    assert second.status_code == 409
    assert second.get_json()["code"] == "stale_design_revision"
    with app.app_context():
        location = db.session.get(UserLocation, location_id)
        saved = location.to_dict()
        assert saved["calculation_status"] == "stale_assumptions"
        assert saved["extras"]["accepted_layout"]["status"] == "stale"
        assert (
            saved["extras"]["accepted_layout"]["stale_reason"]
            == "calculation_assumptions_changed"
        )
        assert location.extras["accepted_layout"]["status"] == "accepted"


def test_geometry_revision_marks_accepted_layout_stale(client, app, layout_context):
    location_id, _, payload, irradiance = layout_context
    with patch.object(solar_logic, "fetch_solar_data", return_value=(irradiance, None, "test_fixture", "low")):
        assert client.post(f"/api/locations/{location_id}/rooftop-layout/accept", json=payload).status_code == 201
    changed = {"manual_obstructions_geojson": {"type": "MultiPolygon", "coordinates": [[[[77.20904, 28.61394], [77.20906, 28.61394], [77.20906, 28.61396], [77.20904, 28.61396], [77.20904, 28.61394]]]]}}
    assert client.post(f"/api/locations/{location_id}/rooftop-design", json=changed).status_code == 201
    with app.app_context():
        saved = db.session.get(UserLocation, location_id)
        current = RooftopDesign.query.filter_by(user_location_id=location_id, is_current=True).one()
        assert current.panel_layout is None
        assert saved.extras["accepted_layout"]["status"] == "stale"
        assert saved.extras["surface_generation_status"] == "stale"
        assert saved.extras["surface_generation"]["status"] == "stale"


def test_cross_location_revision_is_rejected(client, app, layout_context):
    _, _, payload, _ = layout_context
    with app.app_context():
        original = db.session.get(UserLocation, 1)
        other = UserLocation(user_id=original.user_id, address="Other", latitude=28.614, longitude=77.2091,
                             polygon_geojson=original.polygon_geojson, system_size=1, annual_generation=1)
        db.session.add(other); db.session.commit(); other_id = other.id
    client.get(f"/api/roof-model/{other_id}")
    response = client.post(f"/api/locations/{other_id}/rooftop-layout/accept", json=payload)
    assert response.status_code == 409
    assert response.get_json()["code"] == "stale_design_revision"


def test_other_users_location_is_not_disclosed(client, app, layout_context):
    _, _, payload, _ = layout_context
    with app.app_context():
        other_user = User(email="other-layout@example.com", password_hash=generate_password_hash("unused"), display_name="Other")
        db.session.add(other_user); db.session.flush()
        location = UserLocation(user_id=other_user.id, address="Private", latitude=28.614,
                                longitude=77.2091, polygon_geojson={"type": "Polygon", "coordinates": [[[77.209, 28.6139], [77.2092, 28.6139], [77.2092, 28.6141], [77.209, 28.6141], [77.209, 28.6139]]]},
                                system_size=1, annual_generation=1)
        db.session.add(location); db.session.commit(); private_id = location.id
    response = client.post(f"/api/locations/{private_id}/rooftop-layout/accept", json=payload)
    assert response.status_code == 404
    assert response.get_json()["code"] == "location_not_found"
