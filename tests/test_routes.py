from unittest.mock import patch

import pytest

from app import solar_logic
from app.models import User, UserLocation, db
from app.services.location_service import get_report_context


def _obstruction_payload(obstructions):
    return {
        "address": "Obstruction validation rooftop",
        "latitude": 28.6140,
        "longitude": 77.2091,
        "polygon": [
            [28.6139, 77.2090],
            [28.6139, 77.2092],
            [28.6141, 77.2092],
            [28.6141, 77.2090],
        ],
        "obstructions": obstructions,
    }


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
        "location_context": {
            "city": "New Delhi",
            "locality": "Connaught Place",
            "pincode": "110001",
        },
    }
    irradiance = {month: 5.0 for month in solar_logic.MONTH_NAMES}

    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(irradiance, None, "test_fixture", "low"),
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
        assert location.polygon_geojson["coordinates"][0][0] == [
            77.2090,
            28.6139,
        ]
        assert location.extras["location_context"] == payload["location_context"]
        assert location.extras["pincode"] == "110001"
    report = client.get(data["redirect_url"])
    assert report.status_code == 200
    assert "Share with verified installers" in report.text
    assert 'class="rpt-marketplace-form"' in report.text
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
        return_value=(irradiance, None, "test_fixture", "low"),
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
        return_value=(irradiance, None, "test_fixture", "low"),
    ):
        response = client.post("/api/estimate", json=payload)

    assert response.status_code == 200
    expected_rating = response.get_json()["suitability_rating"]
    with app.app_context():
        location = db.session.get(UserLocation, response.get_json()["location_id"])
        db.session.expire_all()
        fresh_location = db.session.get(UserLocation, location.id)
        assert fresh_location.suitability_rating == expected_rating
        owner_id = fresh_location.user_id
        ctx = get_report_context(fresh_location.id, owner_id)
        assert ctx is not None
        _, report_data = ctx
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


def test_api_estimate_rejects_self_intersecting_roof(client):
    response = client.post(
        "/api/estimate",
        json={
            "address": "Crossed rooftop",
            "latitude": 28.6139,
            "longitude": 77.2090,
            "polygon": [
                [28.6139, 77.2090],
                [28.6141, 77.2092],
                [28.6139, 77.2092],
                [28.6141, 77.2090],
            ],
        },
    )

    assert response.status_code == 400
    assert "must not self-intersect" in response.get_json()["error"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("latitude", "NaN", "latitude must be a finite number"),
        ("longitude", 181, "longitude must be at most 180"),
        ("battery_kwh", -1, "battery_kwh must be at least 0"),
        ("tariff_per_kwh", "not-a-number", "tariff_per_kwh must be a finite number"),
        ("needs_backup", "false", "needs_backup must be a boolean"),
    ],
)
def test_api_estimate_rejects_invalid_physical_inputs(client, field, value, message):
    payload = {
        "address": "Input validation rooftop",
        "latitude": 28.6139,
        "longitude": 77.2090,
        "polygon": [
            [28.6139, 77.2090],
            [28.6139, 77.2092],
            [28.6141, 77.2092],
            [28.6141, 77.2090],
        ],
    }
    payload[field] = value

    response = client.post("/api/estimate", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {"error": message}


@pytest.mark.parametrize(
    ("obstructions", "message"),
    [
        (
            [[[28.6142, 77.2090], [28.6142, 77.2091], [28.6143, 77.2091]]],
            "must remain fully inside",
        ),
        (
            [
                [[28.61395, 77.20905], [28.61395, 77.20912], [28.61402, 77.20912], [28.61402, 77.20905]],
                [[28.61398, 77.20908], [28.61398, 77.20915], [28.61405, 77.20915], [28.61405, 77.20908]],
            ],
            "must not overlap",
        ),
        (
            [
                [[28.61395, 77.20904], [28.61395, 77.20913], [28.61402, 77.20913], [28.61402, 77.20904]],
                [[28.61395, 77.20911], [28.61395, 77.20916], [28.61402, 77.20916], [28.61402, 77.20911]],
            ],
            "must not overlap",
        ),
    ],
)
def test_api_estimate_rejects_obstructions_that_corrupt_usable_area(
    client, obstructions, message
):
    response = client.post(
        "/api/estimate",
        json=_obstruction_payload(obstructions),
    )

    assert response.status_code == 400
    assert message in response.get_json()["error"]


def test_api_estimate_accepts_valid_obstruction_and_persists_it(client, app):
    obstruction = [
        [28.61395, 77.20905],
        [28.61395, 77.20910],
        [28.61400, 77.20910],
        [28.61400, 77.20905],
    ]
    irradiance = {month: 5.0 for month in solar_logic.MONTH_NAMES}

    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(irradiance, None, "test_fixture", "low"),
    ):
        response = client.post(
            "/api/estimate", json=_obstruction_payload([obstruction])
        )

    assert response.status_code == 200
    data = response.get_json()
    assert 0 < data["obstructed_area_sqm"] < data["roof_area_sqm"]
    assert data["usable_area_sqm"] < data["roof_area_sqm"]
    with app.app_context():
        saved = db.session.get(UserLocation, data["location_id"])
        assert saved.obstructions_geojson["coordinates"][0][0][0] == [
            77.20905,
            28.61395,
        ]


@pytest.mark.parametrize(
    "second",
    [
        [
            [28.61395, 77.20910],
            [28.61395, 77.20915],
            [28.61400, 77.20915],
            [28.61400, 77.20910],
        ],
        [
            [28.61400, 77.20910],
            [28.61400, 77.20915],
            [28.61405, 77.20915],
            [28.61405, 77.20910],
        ],
    ],
    ids=["shared-edge", "shared-point"],
)
def test_api_estimate_allows_zero_area_obstruction_contact(client, second):
    first = [
        [28.61395, 77.20905],
        [28.61395, 77.20910],
        [28.61400, 77.20910],
        [28.61400, 77.20905],
    ]
    irradiance = {month: 5.0 for month in solar_logic.MONTH_NAMES}

    with patch.object(
        solar_logic,
        "fetch_solar_data",
        return_value=(irradiance, None, "test_fixture", "low"),
    ):
        response = client.post(
            "/api/estimate", json=_obstruction_payload([first, second])
        )

    assert response.status_code == 200
    assert response.get_json()["obstructed_area_sqm"] > 0


@pytest.mark.parametrize(
    "obstruction",
    [
        [],
        [[28.61395, 77.20905], [28.61395, 77.20906], [28.61395, 77.20907]],
        [[28.61395, 77.20905], [28.61396, 77.20906], [28.61395, 77.20906], [28.61396, 77.20905]],
    ],
    ids=["empty", "degenerate", "self-intersecting"],
)
def test_api_estimate_rejects_invalid_obstruction_geometry(client, obstruction):
    response = client.post(
        "/api/estimate", json=_obstruction_payload([obstruction])
    )

    assert response.status_code == 400
    assert "Invalid obstruction polygon" in response.get_json()["error"]


def test_rejected_obstructions_are_not_calculated_or_persisted(client, app):
    outside = [
        [28.61405, 77.20915],
        [28.61405, 77.20925],
        [28.61415, 77.20925],
        [28.61415, 77.20915],
    ]
    with app.app_context():
        before = UserLocation.query.count()

    with patch(
        "app.services.estimation_service.run_full_estimation"
    ) as estimation_engine:
        response = client.post(
            "/api/estimate", json=_obstruction_payload([outside])
        )

    assert response.status_code == 400
    estimation_engine.assert_not_called()
    with app.app_context():
        assert UserLocation.query.count() == before


def test_legacy_invalid_obstructions_are_rejected_before_recalculation(client, app):
    roof = {
        "type": "Polygon",
        "coordinates": [[
            [77.2090, 28.6139],
            [77.2092, 28.6139],
            [77.2092, 28.6141],
            [77.2090, 28.6141],
            [77.2090, 28.6139],
        ]],
    }
    overlapping = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[77.20904, 28.61394], [77.20912, 28.61394], [77.20912, 28.61402], [77.20904, 28.61402], [77.20904, 28.61394]]],
            [[[77.20908, 28.61398], [77.20916, 28.61398], [77.20916, 28.61406], [77.20908, 28.61406], [77.20908, 28.61398]]],
        ],
    }
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        legacy = UserLocation(
            user_id=user.id,
            address="Legacy invalid obstruction data",
            latitude=28.6140,
            longitude=77.2091,
            polygon_geojson=roof,
            obstructions_geojson=overlapping,
            system_size=3.0,
        )
        db.session.add(legacy)
        db.session.commit()
        legacy_id = legacy.id
        before = UserLocation.query.count()

    with patch(
        "app.services.estimation_service.run_full_estimation"
    ) as estimation_engine:
        preview = client.post(
            f"/api/estimate/{legacy_id}/orientation-preview",
            json={"orientation": 180},
        )
        clone = client.post(
            f"/api/locations/{legacy_id}/scenarios",
            json={"change": "sizing_mode", "value": "roof_max"},
        )

    assert preview.status_code == 400
    assert clone.status_code == 400
    assert "must not overlap" in preview.get_json()["error"]
    assert "must not overlap" in clone.get_json()["error"]
    estimation_engine.assert_not_called()
    with app.app_context():
        saved = db.session.get(UserLocation, legacy_id)
        assert saved.roof_group_id is None
        assert UserLocation.query.count() == before


def test_business_estimate_page_renders(client):
    response = client.get("/business/estimate")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="map"' in html
    assert 'id="business-assessment-fields"' in html
    assert 'data-estimate-url="/business/estimate"' in html
    assert "State" in html


def test_business_estimate_requires_location_and_usage(client):
    response = client.post(
        "/business/estimate",
        json={
            "address": "",
            "latitude": 28.6139,
            "longitude": 77.2090,
            "polygon": [
                [28.6139, 77.2090],
                [28.6139, 77.2092],
                [28.6141, 77.2092],
                [28.6141, 77.2090],
            ],
        },
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "Address is required"

    response = client.post(
        "/business/estimate",
        json={
            "address": "Business rooftop",
            "latitude": 28.6139,
            "longitude": 77.2090,
            "polygon": [
                [28.6139, 77.2090],
                [28.6139, 77.2092],
                [28.6141, 77.2092],
                [28.6141, 77.2090],
            ],
        },
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "Monthly units or monthly bill is required"


def test_business_estimate_saves_valid_payload(client, app, monkeypatch):
    monkeypatch.setattr(solar_logic, "fetch_solar_data", lambda *a, **kw: ({m: 5.0 for m in solar_logic.MONTH_NAMES}, None, "test_fixture", "low"))
    payload = {
        "address": "Business rooftop",
        "latitude": 28.6139,
        "longitude": 77.2090,
        "polygon": [
            [28.6139, 77.2090],
            [28.6139, 77.2092],
            [28.6141, 77.2092],
            [28.6141, 77.2090],
        ],
        "state": "Maharashtra",
        "business_type": "Shop",
        "daytime_power_use": "mixed",
        "operating_days": 6,
        "operating_hours": "9am-6pm",
        "monthly_units_kwh": 1200,
        "roof_ownership": "own",
        "tax_rate_pct": 25,
        "loan_rate_pct": 9.5,
        "loan_tenure_years": 6,
        "ppa_rate_rs_per_kwh": 5.5,
        "ppa_tenure_years": 7,
        "include_depreciation_tax_benefit": "on",
        "location_context": {
            "city": "New Delhi",
            "locality": "Connaught Place",
            "pincode": "110001",
        },
    }

    response = client.post("/business/estimate", json=payload)

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "saved"
    assert data["location_id"] > 0
    with app.app_context():
        location = db.session.get(UserLocation, data["location_id"])
        assert location is not None
        assert location.extras["planning_track"] == "business"
        assert location.extras["business_type"] == "Shop"
        assert location.extras["monthly_units_kwh"] == 1200.0
        assert location.extras["include_depreciation_tax_benefit"] is True
        assert location.extras["location_context"] == {
            "city": "New Delhi",
            "locality": "Connaught Place",
            "pincode": "110001",
        }
        assert location.extras["pincode"] == "110001"
        assert location.system_size > 0
        assert location.annual_generation > 0
        assert location.net_investment > 0
        assert location.subsidy_amount == 0
