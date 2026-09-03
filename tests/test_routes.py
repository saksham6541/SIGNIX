from unittest.mock import patch

from app import solar_logic


def test_api_estimate_returns_estimate_for_valid_polygon(client):
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
    solar_data_fetch.assert_called_once()


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
