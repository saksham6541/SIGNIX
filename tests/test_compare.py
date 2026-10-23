from datetime import datetime

from app.models import User, UserLocation, db
from werkzeug.security import generate_password_hash

from app.services import estimation_service


def login(test_client, email, password):
    return test_client.post(
        "/login",
        data={"email": email, "password": password},
    )


def make_location(user_id, address, system_size, annual_generation):
    return UserLocation(
        user_id=user_id,
        address=address,
        latitude=28.61,
        longitude=77.20,
        system_size=system_size,
        annual_generation=annual_generation,
        monthly_savings=1500.0,
        payback_years=5.5,
        system_cost=100000.0,
        subsidy_amount=30000.0,
        roof_area_sqm=42.5,
        created_at=datetime(2026, 1, 15),
    )


def test_properties_compare_page_requires_login(app):
    response = app.test_client().get("/properties#compare")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_compare_rejects_another_users_location_id(app):
    with app.app_context():
        user_a = User(
            email="compare-a@example.com",
            password_hash=generate_password_hash("password-a", method="pbkdf2:sha256"),
            display_name="Compare A",
        )
        user_b = User(
            email="compare-b@example.com",
            password_hash=generate_password_hash("password-b", method="pbkdf2:sha256"),
            display_name="Compare B",
        )
        db.session.add_all([user_a, user_b])
        db.session.flush()
        own_location = make_location(user_a.id, "User A rooftop", 3.0, 4800.0)
        foreign_location = make_location(user_b.id, "User B rooftop", 8.0, 12000.0)
        db.session.add_all([own_location, foreign_location])
        db.session.commit()
        foreign_id = foreign_location.id
        own_id = own_location.id

    test_client = app.test_client()
    assert login(test_client, "compare-a@example.com", "password-a").status_code == 302
    response = test_client.post(
        "/properties",
        data={"location_ids": [str(own_id), str(foreign_id)]},
    )

    assert response.status_code == 200
    assert b"User B rooftop" not in response.data



def test_compare_displays_selected_location_data(app):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").one()
        first = make_location(user.id, "First rooftop", 3.25, 5100.0)
        second = make_location(user.id, "Second rooftop", 5.5, 8400.0)
        db.session.add_all([first, second])
        db.session.commit()
        first_id = first.id
        second_id = second.id

    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302
    response = test_client.post(
        "/properties",
        data={"location_ids": [str(first_id), str(second_id)]},
    )

    assert response.status_code == 200
    assert b"First rooftop" in response.data
    assert b"Second rooftop" in response.data
    assert b"3.25 kW" in response.data
    assert b"5.50 kW" in response.data
    assert b"Scenario comparison" in response.data or b"comparison" in response.data.lower()


def test_compare_invalid_selection_is_announced(app):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").one()
        db.session.add(make_location(user.id, "Only rooftop", 3.25, 5100.0))
        db.session.commit()

    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302

    response = test_client.post("/properties", data={"location_ids": []})

    assert response.status_code == 200
    assert b'class="compare-selection-error" role="alert"' in response.data
    assert b'aria-live="assertive"' in response.data


def test_compare_duplicate_id_does_not_create_duplicate_scenario_columns(app):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").one()
        location = make_location(user.id, "Single rooftop", 3.25, 5100.0)
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302
    response = test_client.get(f"/properties?ids={location_id},{location_id}#compare")

    assert response.status_code == 200
    assert b"Scenario comparison" not in response.data
    assert b'class="compare-selection-error" role="alert"' in response.data


def test_clone_api_returns_actionable_validation_error(app, monkeypatch):
    def reject_incomplete_request(**_kwargs):
        raise estimation_service.InvalidEstimateInputError(
            "change must be one of: sizing_mode, bill_offset, needs_backup, battery_kwh"
        )

    monkeypatch.setattr(
        estimation_service, "clone_estimate_with_change", reject_incomplete_request
    )
    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302

    response = test_client.post("/api/locations/999/scenarios", json={})

    assert response.status_code == 400
    assert "change must be one of" in response.get_json()["error"]


def test_clone_api_reports_estimation_engine_failure(app, monkeypatch):
    def fail_estimation(**_kwargs):
        raise estimation_service.EstimationEngineError("controlled QA failure")

    monkeypatch.setattr(
        estimation_service, "clone_estimate_with_change", fail_estimation
    )
    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302

    response = test_client.post(
        "/api/locations/999/scenarios",
        json={"change": "sizing_mode", "value": "roof_max"},
    )

    assert response.status_code == 500
    assert response.get_json()["error"] == "Estimation engine failed: controlled QA failure"


def test_failed_clone_does_not_partially_backfill_source_group(app, monkeypatch):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").one()
        source = make_location(user.id, "Atomic clone rooftop", 3.0, 4800.0)
        source.polygon_geojson = {
            "type": "Polygon",
            "coordinates": [[
                [77.20, 28.61], [77.2001, 28.61],
                [77.2001, 28.6101], [77.20, 28.61],
            ]],
        }
        source.extras = {"tariff_per_kwh": 8.0, "sizing_method": "roof_max"}
        db.session.add(source)
        db.session.commit()
        source_id = source.id
        original_count = UserLocation.query.count()

    def fail_estimation(**_kwargs):
        raise RuntimeError("controlled calculation failure")

    monkeypatch.setattr(estimation_service, "run_full_estimation", fail_estimation)
    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302

    response = test_client.post(
        f"/api/locations/{source_id}/scenarios",
        json={"change": "sizing_mode", "value": "bill_offset_80%"},
    )

    assert response.status_code == 500
    with app.app_context():
        saved = db.session.get(UserLocation, source_id)
        assert saved.roof_group_id is None
        assert UserLocation.query.count() == original_count


def test_failed_clone_commit_rolls_back_source_and_clone(app, monkeypatch):
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").one()
        source = make_location(user.id, "Atomic persistence rooftop", 3.0, 4800.0)
        source.polygon_geojson = {
            "type": "Polygon",
            "coordinates": [[
                [77.20, 28.61], [77.2001, 28.61],
                [77.2001, 28.6101], [77.20, 28.61],
            ]],
        }
        source.extras = {"tariff_per_kwh": 8.0, "sizing_method": "roof_max"}
        db.session.add(source)
        db.session.commit()
        source_id = source.id
        original_count = UserLocation.query.count()

    test_client = app.test_client()
    assert login(test_client, "test@example.com", "unused").status_code == 302
    original_commit = db.session.commit
    commit_calls = 0

    def fail_clone_commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 1:
            raise RuntimeError("controlled persistence failure")
        return original_commit()

    monkeypatch.setattr(db.session, "commit", fail_clone_commit)
    response = test_client.post(
        f"/api/locations/{source_id}/scenarios",
        json={"change": "sizing_mode", "value": "bill_offset_80%"},
    )

    assert response.status_code == 500
    assert response.get_json()["error"] == (
        "Could not save scenario: controlled persistence failure"
    )
    with app.app_context():
        db.session.expire_all()
        saved = db.session.get(UserLocation, source_id)
        assert saved.roof_group_id is None
        assert UserLocation.query.count() == original_count
