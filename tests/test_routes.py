from unittest.mock import patch

from app import solar_logic
from app.models import UserLocation, db
from app.services.location_service import get_report_context


def test_api_estimate_returns_estimate_for_valid_polygon(client, app):
    payload = {
        "address": "Test rooftop",
        "latitude": 28.6139,
        "longitude": 77.2090,
        "polygon": [
            [28.6139, 77.2090],
            [28.6139, 77.2092],
            [28.6141, 77.2092],
            [28.6141, 77.2090],
        ],
        "tariff_per_kwh": 8.0,
    }
    irradiance = {month: 5.0 for month in solar_logic.MONTH_NAMES}

    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(irradiance, None, "test_fixture"),
    ) as solar_data_fetch:
        response = client.post("/api/estimate", json=payload)

    assert response.status_code == 200
    data = response.get_json()
    assert data["location_id"] > 0
    assert data["redirect_url"] == f"/report/{data['location_id']}"
    assert data["irradiance_source"] == "test_fixture"
    assert data["roof_area_sqm"] > 0
    assert data["annual_generation"] > 0
    with app.app_context():
        location = db.session.get(UserLocation, data["location_id"])
        assert location.user_priority == "no_preference"
    solar_data_fetch.assert_called_once()


def test_api_estimate_saves_and_retrieves_user_priority(client, app):
    payload = {
        "address": "Priority rooftop",
        "latitude": 28.6139,
        "longitude": 77.2090,
        "polygon": [
            [28.6139, 77.2090],
            [28.6139, 77.2092],
            [28.6141, 77.2092],
            [28.6141, 77.2090],
        ],
        "user_priority": "backup_power",
    }
    irradiance = {month: 5.0 for month in solar_logic.MONTH_NAMES}

    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(irradiance, None, "test_fixture"),
    ):
        response = client.post("/api/estimate", json=payload)

    assert response.status_code == 200
    with app.app_context():
        location = db.session.get(UserLocation, response.get_json()["location_id"])
        assert location.user_priority == "backup_power"
        assert location.to_dict()["user_priority"] == "backup_power"


def test_api_estimate_rating_round_trips_from_saved_location(client, app):
    payload = {
        "address": "Rated rooftop",
        "latitude": 28.6139,
        "longitude": 77.2090,
        "polygon": [
            [28.6139, 77.2090],
            [28.6139, 77.2092],
            [28.6141, 77.2092],
            [28.6141, 77.2090],
        ],
        "user_priority": "fastest_payback",
    }
    irradiance = {month: 5.0 for month in solar_logic.MONTH_NAMES}

    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(irradiance, None, "test_fixture"),
    ):
        response = client.post("/api/estimate", json=payload)

    assert response.status_code == 200
    expected_rating = response.get_json()["suitability_rating"]
    with app.app_context():
        location = db.session.get(UserLocation, response.get_json()["location_id"])
        db.session.expire_all()
        fresh_location = db.session.get(UserLocation, location.id)
        assert fresh_location.suitability_rating == expected_rating
        _, report_data = get_report_context(fresh_location.id, 1)
        assert report_data["suitability_rating"] == expected_rating


def test_api_estimate_rejects_malformed_polygon(client):
    response = client.post(
        "/api/estimate",
        json={
            "address": "Malformed rooftop",
            "latitude": 28.6139,
            "longitude": 77.2090,
            "polygon": [[28.6139, 77.2090], [28.6139, 77.2092]],
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "A rooftop polygon with at least 3 points is required"
    }
