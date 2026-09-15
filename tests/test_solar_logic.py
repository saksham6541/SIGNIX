import math
from unittest.mock import patch

import pytest

from app import solar_logic

EARTH_RADIUS_M = 6371008.8


@pytest.fixture(autouse=True)
def isolate_irradiance_cache(monkeypatch):
    monkeypatch.setattr(solar_logic, "get_cached_irradiance", lambda lat, lng: None)
    monkeypatch.setattr(
        solar_logic,
        "set_cached_irradiance",
        lambda lat, lng, data, **kwargs: None,
    )


def meters_to_degrees(meters):
    return math.degrees(meters / EARTH_RADIUS_M)


def test_mock_irradiance_varies_by_latitude_and_longitude():
    delhi = solar_logic._mock_irradiance_profile(28.6139, 77.2090)
    shillong = solar_logic._mock_irradiance_profile(25.5788, 91.8933)
    same_latitude_different_longitude = solar_logic._mock_irradiance_profile(
        28.6139, 91.8933
    )
    same_longitude_different_latitude = solar_logic._mock_irradiance_profile(
        25.5788, 77.2090
    )

    assert delhi != shillong
    assert delhi["Jan"] != shillong["Jan"]
    assert delhi != same_latitude_different_longitude
    assert delhi != same_longitude_different_latitude


def rating_estimate(**overrides):
    estimate = {
        "payback_years": 4,
        "orientation_factor": 1.0,
        "usable_area_sqm": 12,
        "system_size": 1,
        "net_investment": 20000,
        "cashflow_25yr": [100000],
        "co2_reduction_tons": 3,
        "irradiance_source": "pvgis",
        "inverter_type": "off-grid",
        "battery_kwh": 10,
    }
    estimate.update(overrides)
    return estimate


def test_suitability_rating_excellent_everything():
    rating = solar_logic.calculate_suitability_rating(rating_estimate())

    assert rating["overall_viability"]["tier"] == "excellent"
    assert rating["overall_viability"]["score"] == 100
    assert rating["data_confidence"] == {"tier": "high", "score": 100}


def test_suitability_rating_poor_everything():
    rating = solar_logic.calculate_suitability_rating(
        rating_estimate(
            payback_years=15,
            orientation_factor=0.65,
            usable_area_sqm=3,
            system_size=1,
            net_investment=100000,
            cashflow_25yr=[-10000],
            co2_reduction_tons=0.1,
            irradiance_source="mock_fallback",
            inverter_type="on-grid",
            battery_kwh=0,
        )
    )

    assert rating["overall_viability"]["tier"] == "poor"
    assert rating["data_confidence"] == {"tier": "low", "score": 60}


def test_suitability_rating_marks_not_viable_financial_case():
    rating = solar_logic.calculate_suitability_rating(
        rating_estimate(net_investment=100000, cashflow_25yr=[0])
    )

    financial = rating["factors"]["financial_viability"]
    assert financial["ratio"] == 1.0
    assert financial["tier"] == "not_viable"
    assert financial["score"] == 0


def test_suitability_rating_distinguishes_oversized_and_undersized_roofs():
    oversized = solar_logic.calculate_suitability_rating(
        rating_estimate(usable_area_sqm=80, system_size=1)
    )
    undersized = solar_logic.calculate_suitability_rating(
        rating_estimate(usable_area_sqm=5, system_size=1)
    )

    assert oversized["factors"]["roof_fit"]["tier"] == "fair"
    assert oversized["factors"]["roof_fit"]["score"] == 50
    assert undersized["factors"]["roof_fit"]["tier"] == "poor"
    assert undersized["factors"]["roof_fit"]["score"] == 25


def test_suitability_rating_priority_changes_weighting():
    estimate = rating_estimate(
        payback_years=4,
        co2_reduction_tons=0.3,
        inverter_type="on-grid",
        battery_kwh=0,
    )

    payback_rating = solar_logic.calculate_suitability_rating(
        estimate, user_priority="fastest_payback"
    )
    environmental_rating = solar_logic.calculate_suitability_rating(
        estimate, user_priority="environmental_impact"
    )

    assert (
        payback_rating["overall_viability"]["score"]
        > environmental_rating["overall_viability"]["score"]
    )
    assert payback_rating["weights"]["payback"] == 40
    assert environmental_rating["weights"]["co2_reduction"] == 50


def test_calculate_polygon_area_sqm_rectangle():
    width_m = 20.0
    height_m = 10.0
    width_degrees = meters_to_degrees(width_m)
    height_degrees = meters_to_degrees(height_m)
    coordinates = [
        [0.0, 0.0],
        [0.0, width_degrees],
        [height_degrees, width_degrees],
        [height_degrees, 0.0],
    ]

    area = solar_logic.calculate_polygon_area_sqm(coordinates)

    assert area == pytest.approx(width_m * height_m, abs=0.05)


def test_calculate_polygon_area_sqm_irregular_polygon():
    coordinates_in_meters = [
        (0.0, 0.0),
        (30.0, 0.0),
        (45.0, 15.0),
        (20.0, 30.0),
        (0.0, 20.0),
    ]
    coordinates = [
        [meters_to_degrees(north), meters_to_degrees(east)]
        for north, east in coordinates_in_meters
    ]

    area = solar_logic.calculate_polygon_area_sqm(coordinates)

    assert area == pytest.approx(950.0, abs=0.1)


@pytest.mark.parametrize(
    "coordinates",
    [
        [[0.0, 0.0], [0.0, 0.001]],
        [[0.0, 0.0], [0.0, 0.001], [0.0, 0.002]],
        [[0.0, 0.0], [0.0, 0.001], [0.0, 0.001], [0.0, 0.0]],
    ],
)
def test_calculate_polygon_area_sqm_degenerate_polygon_returns_zero(coordinates):
    assert solar_logic.calculate_polygon_area_sqm(coordinates) == 0.0


def test_calculate_polygon_area_sqm_cross_checks_both_methods(monkeypatch):
    coordinates = [[0.0, 0.0], [0.0, 0.001], [0.001, 0.001]]
    calls = []

    def fake_local_area(points):
        calls.append("local")
        return 100.0

    def fake_spherical_area(points):
        calls.append("spherical")
        return 120.0

    monkeypatch.setattr(solar_logic, "_area_local_tangent_sqm", fake_local_area)
    monkeypatch.setattr(solar_logic, "_area_spherical_excess_sqm", fake_spherical_area)

    area = solar_logic.calculate_polygon_area_sqm(coordinates)

    assert calls == ["local", "spherical"]
    assert area == 120.0


def test_calculate_polygon_area_sqm_uses_local_area_when_methods_agree():
    coordinates = [[0.0, 0.0], [0.0, 0.001], [0.001, 0.001]]
    points = solar_logic._normalize_ring(coordinates)
    local_area = solar_logic._area_local_tangent_sqm(points)
    spherical_area = solar_logic._area_spherical_excess_sqm(points)

    area = solar_logic.calculate_polygon_area_sqm(coordinates)

    assert abs(local_area - spherical_area) / max(local_area, spherical_area) < 0.02
    assert area == pytest.approx(local_area, abs=0.01)


def test_fetch_solar_data_uses_nasa_power_when_successful():
    irradiance = {"Jan": 5.0}
    temperature = {"Jan": 25.0}

    with patch.object(
        solar_logic,
        "fetch_irradiance_nasa_power",
        return_value=(irradiance, temperature, "nasa_power"),
    ) as nasa_fetch, patch.object(solar_logic, "fetch_irradiance_pvgis") as pvgis_fetch:
        result = solar_logic.fetch_solar_data(28.6, 77.2, peak_power_kw=2.0)

    assert result == (irradiance, temperature, "nasa_power")
    nasa_fetch.assert_called_once_with(28.6, 77.2)
    pvgis_fetch.assert_not_called()


def test_fetch_solar_data_tries_pvgis_when_nasa_power_fails():
    irradiance = {"Jan": 4.5}

    with patch.object(
        solar_logic,
        "fetch_irradiance_nasa_power",
        return_value=(None, None, None),
    ) as nasa_fetch, patch.object(
        solar_logic,
        "fetch_irradiance_pvgis",
        return_value=(irradiance, "pvgis"),
    ) as pvgis_fetch:
        result = solar_logic.fetch_solar_data(28.6, 77.2, peak_power_kw=2.0)

    assert result == (irradiance, None, "pvgis")
    nasa_fetch.assert_called_once_with(28.6, 77.2)
    pvgis_fetch.assert_called_once_with(28.6, 77.2, 2.0)


def test_fetch_solar_data_uses_mock_profile_when_external_fetches_fail():
    mock_profile = {"Jan": 5.2, "Feb": 5.8}

    with patch.object(
        solar_logic.requests, "get", side_effect=RuntimeError("network unavailable")
    ) as http_get, patch.object(
        solar_logic, "_mock_irradiance_profile", return_value=mock_profile
    ) as mock_profile_fetch, patch.object(
        solar_logic, "set_cached_irradiance"
    ) as cache_writer:
        result = solar_logic.fetch_solar_data(28.6, 77.2, peak_power_kw=2.0)

    assert result == (mock_profile, None, "mock_fallback")
    assert http_get.call_count == 2
    mock_profile_fetch.assert_called_once_with(28.6, 77.2)
    cache_writer.assert_called_once_with(
        28.6,
        77.2,
        {
            "irradiance": mock_profile,
            "temperature": None,
            "source": "mock_fallback",
        },
        ttl_hours=1,
    )


def test_fetch_solar_data_returns_cached_result_without_external_fetches(monkeypatch):
    cached = {
        "irradiance": {"Jan": 5.0},
        "temperature": {"Jan": 25.0},
        "source": "cached",
    }
    monkeypatch.setattr(solar_logic, "get_cached_irradiance", lambda lat, lng: cached)
    with patch.object(
        solar_logic, "fetch_irradiance_nasa_power"
    ) as nasa_fetch, patch.object(solar_logic, "fetch_irradiance_pvgis") as pvgis_fetch:
        result = solar_logic.fetch_solar_data(28.6, 77.2, peak_power_kw=2.0)

    assert result == ({"Jan": 5.0}, {"Jan": 25.0}, "cached")
    nasa_fetch.assert_not_called()
    pvgis_fetch.assert_not_called()


def test_fetch_solar_data_caches_successful_external_result(monkeypatch):
    irradiance = {"Jan": 5.0}
    temperature = {"Jan": 25.0}
    set_cache = patch.object(solar_logic, "set_cached_irradiance")

    monkeypatch.setattr(solar_logic, "get_cached_irradiance", lambda lat, lng: None)
    with patch.object(
        solar_logic,
        "fetch_irradiance_nasa_power",
        return_value=(irradiance, temperature, "nasa_power"),
    ), set_cache as cache_writer:
        result = solar_logic.fetch_solar_data(28.6, 77.2)

    assert result == (irradiance, temperature, "nasa_power")
    cache_writer.assert_called_once_with(
        28.6,
        77.2,
        {
            "irradiance": irradiance,
            "temperature": temperature,
            "source": "nasa_power",
        },
        ttl_days=30,
    )
