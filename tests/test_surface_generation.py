from unittest.mock import patch

import pytest

from app import solar_logic
from app.services import rooftop_layout_service


UNIFORM_PSH = {month: 5.0 for month in solar_logic.MONTH_NAMES}
BASE_MONTHLY_1KW = {
    "Jan": 124.0, "Feb": 112.0, "Mar": 124.0, "Apr": 120.0,
    "May": 124.0, "Jun": 120.0, "Jul": 124.0, "Aug": 124.0,
    "Sep": 120.0, "Oct": 124.0, "Nov": 120.0, "Dec": 124.0,
}


def _surface(surface_id, panels, capacity, pitch=20, azimuth=180, **extra):
    return {
        "surface_id": surface_id, "panel_count": panels,
        "capacity_kw": capacity, "pitch_degrees": pitch,
        "azimuth_degrees": azimuth, "shade_status": "unknown_not_quantified",
        **extra,
    }


def test_single_flat_surface_has_no_azimuth_penalty_and_uniform_reference():
    result = solar_logic.calculate_accepted_surface_generation(
        [_surface("flat", 2, 1.0, pitch=0, azimuth=0)], UNIFORM_PSH,
        fallback_azimuth=180,
    )
    assert result["monthly_generation_kwh"] == BASE_MONTHLY_1KW
    assert result["annual_generation_kwh"] == 1460.0
    surface = result["surfaces"][0]
    assert surface["orientation_source"] == "horizontal_plane"
    assert surface["effective_azimuth_degrees"] is None
    assert surface["tilt_model"] == "not_applied_no_plane_of_array_irradiance"


def test_equal_capacity_south_and_east_surfaces_aggregate_independently():
    result = solar_logic.calculate_accepted_surface_generation([
        _surface("south", 2, 1.0, azimuth=180),
        _surface("east", 2, 1.0, azimuth=90),
    ], UNIFORM_PSH)

    # Existing documented azimuth approximation gives east factor 0.653.
    expected_east = {
        month: round(value * 0.653, 1) for month, value in BASE_MONTHLY_1KW.items()
    }
    assert result["surfaces"][0]["annual_generation_kwh"] == 1460.0
    assert result["surfaces"][1]["monthly_generation_kwh"] == expected_east
    assert result["annual_generation_kwh"] == round(1460 + sum(expected_east.values()), 1)
    assert result["capacity_kw"] == 2.0
    assert result["panel_count"] == 4


def test_different_panel_counts_preserve_capacity_and_count_totals():
    result = solar_logic.calculate_accepted_surface_generation([
        _surface("one", 1, .545), _surface("three", 3, 1.635),
    ], UNIFORM_PSH)
    assert result["panel_count"] == 4
    assert result["capacity_kw"] == pytest.approx(2.18)
    assert sum(item["panel_count"] for item in result["surfaces"]) == 4
    assert sum(item["capacity_kw"] for item in result["surfaces"]) == pytest.approx(2.18)


def test_missing_orientation_and_unknown_shade_remain_explicit():
    result = solar_logic.calculate_accepted_surface_generation([
        _surface("unknown", 1, .545, pitch=None, azimuth=None),
    ], UNIFORM_PSH, fallback_azimuth=180)
    surface = result["surfaces"][0]
    assert surface["orientation_source"] == "global_fallback"
    assert surface["shade_status"] == "unknown_not_quantified"
    assert surface["shade_loss_percent"] is None
    assert surface["quantitative_flux"] is None
    assert surface["roof_pitch_degrees"] is None
    assert surface["panel_tilt_degrees"] is None
    assert surface["mounting_basis"] == "mounting_unavailable_global_azimuth_assumption"


def test_qualitative_shade_is_not_converted_to_a_numeric_loss():
    result = solar_logic.calculate_accepted_surface_generation([
        _surface("qualitative", 1, 1.0,
                 shade_status="qualitative_classification_not_quantified"),
    ], UNIFORM_PSH)
    surface = result["surfaces"][0]
    assert surface["generation_model"] == "location_irradiance_azimuth_adjustment"
    assert surface["shade_loss_percent"] is None


def test_complete_provider_annual_flux_is_used_directly_once():
    flux = {
        "source": "provider_annual_flux", "units": "kWh_per_kW_per_year",
        "temporal_coverage": "annual", "specific_yield_kwh_per_kw_year": 1400,
        "coverage_panels": 2, "provenance": {"provider": "google_solar"},
    }
    result = solar_logic.calculate_accepted_surface_generation([
        _surface("provider", 2, 1.0, azimuth=90, quantitative_flux=flux),
    ], UNIFORM_PSH)
    surface = result["surfaces"][0]
    assert surface["generation_model"] == "provider_annual_flux_direct"
    assert surface["orientation_factor"] is None
    assert surface["shade_status"] == "provider_flux_incorporated_not_isolated"
    assert result["annual_generation_kwh"] == pytest.approx(1400, abs=.6)
    assert sum(result["monthly_generation_kwh"].values()) == pytest.approx(
        result["annual_generation_kwh"], abs=1e-9
    )


def test_pvgis_monthly_yield_is_scaled_once_without_pr_or_orientation():
    monthly_specific_yield = {month: 100.0 for month in solar_logic.MONTH_NAMES}
    result = solar_logic.calculate_accepted_surface_generation(
        [_surface("east", 4, 2.0, pitch=20, azimuth=90)],
        monthly_specific_yield,
        fallback_azimuth=90,
        profile_kind=solar_logic.PVGIS_PROFILE_KIND,
    )

    assert result["monthly_generation_kwh"] == {
        month: 200.0 for month in solar_logic.MONTH_NAMES
    }
    assert result["annual_generation_kwh"] == 2400.0
    assert result["weighted_orientation_factor"] is None
    surface = result["surfaces"][0]
    assert surface["orientation_factor"] is None
    assert surface["orientation_source"] == "provider_model_not_separately_adjusted"
    assert surface["generation_model"] == "pvgis_monthly_specific_yield_direct"


def test_full_pvgis_estimate_reconciles_monthly_annual_and_financial_generation():
    coordinates = [[28.6139, 77.2090], [28.6139, 77.2092],
                   [28.6141, 77.2092], [28.6141, 77.2090]]
    monthly_specific_yield = {month: 100.0 for month in solar_logic.MONTH_NAMES}
    with patch.object(
        solar_logic, "fetch_solar_data",
        return_value=(monthly_specific_yield, None, "pvgis", "high"),
    ):
        result = solar_logic.run_full_estimation(
            28.614, 77.2091, coordinates, tariff_per_kwh=8,
            orientation=90, accepted_panel_count=2,
            accepted_module_wattage=500,
            accepted_surfaces=[_surface("east", 2, 1.0, azimuth=90)],
        )

    assert result["annual_generation"] == 1200.0
    assert sum(result["monthly_data"].values()) == result["annual_generation"]
    assert result["surface_generation"]["annual_generation_kwh"] == 1200.0
    assert result["generation_25yr"][0] == 1200.0
    assert result["orientation_factor"] is None
    assert result["orientation_provenance"]["global_direction_source"] == "user_specified"
    assert result["orientation_provenance"]["factor_unavailable_reason"] == "provider_yield_includes_orientation_no_separate_reference_ratio"
    provenance = solar_logic.generation_provenance_for_result(result)
    assert provenance["profile_units"] == "kWh_per_kWp_per_month"
    assert provenance["performance_ratio_applied"] is False
    assert provenance["orientation_adjustment_applied"] is False
    assert provenance["provider_system_loss_percent"] == 14.0


def test_partial_provider_coverage_falls_back_without_fabricating_unshaded_data():
    partial = {
        "source": "provider_annual_flux", "units": "kWh_per_kW_per_year",
        "specific_yield_kwh_per_kw_year": 1400, "coverage_panels": 1,
    }
    result = solar_logic.calculate_accepted_surface_generation([
        _surface("partial", 2, 1.0, quantitative_flux=partial,
                 shade_status="partial_provider_flux_not_applied"),
    ], UNIFORM_PSH)
    surface = result["surfaces"][0]
    assert surface["generation_model"] == "location_irradiance_azimuth_adjustment"
    assert surface["shade_status"] == "partial_provider_flux_not_applied"
    assert surface["shade_loss_percent"] is None


def test_provider_flux_sampling_requires_explicit_absolute_units():
    panel = [[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]]
    grid = {
        "rows": 1, "cols": 1, "x": [0.0], "z": [0.0],
        "rawFlux": [1375.0], "units": "kWh_per_kW_per_year",
        "temporal_coverage": "annual",
    }
    assert rooftop_layout_service._sample_provider_flux(panel, grid) == 1375.0
    assert rooftop_layout_service._sample_provider_flux(
        panel, {**grid, "units": "relative"}
    ) is None


def test_full_estimation_passes_surface_generation_to_existing_financials():
    surfaces = [_surface("accepted", 2, 1.0, azimuth=180)]
    coordinates = [[28.6139, 77.2090], [28.6139, 77.2092],
                   [28.6141, 77.2092], [28.6141, 77.2090]]
    with patch.object(
        solar_logic, "fetch_solar_data",
        return_value=(UNIFORM_PSH, None, "test_fixture", "low"),
    ):
        result = solar_logic.run_full_estimation(
            28.614, 77.2091, coordinates, tariff_per_kwh=8,
            accepted_panel_count=2, accepted_module_wattage=500,
            accepted_surfaces=surfaces,
        )
    assert result["system_size"] == 1.0
    assert result["annual_generation"] == 1460.0
    assert result["surface_generation"]["annual_generation_kwh"] == 1460.0
    assert result["annual_savings"] >= result["monthly_savings"] * 12 - 1
    assert len(result["generation_25yr"]) == 25


def test_legacy_estimate_without_surfaces_keeps_global_orientation_path():
    coordinates = [[28.6139, 77.2090], [28.6139, 77.2092],
                   [28.6141, 77.2092], [28.6141, 77.2090]]
    with patch.object(
        solar_logic, "fetch_solar_data",
        return_value=(UNIFORM_PSH, None, "test_fixture", "low"),
    ):
        result = solar_logic.run_full_estimation(
            28.614, 77.2091, coordinates, tariff_per_kwh=8, orientation=90,
        )
    assert result.get("surface_generation") is None
    assert result["orientation_factor"] == 0.653


@pytest.mark.parametrize("azimuth,label", [(0, "North"), (90, "East"), (180, "South"), (270, "West"), (360, "North")])
def test_geographic_azimuth_convention(azimuth, label):
    degrees, actual_label, factor, source = solar_logic.resolve_orientation(azimuth, [])
    assert degrees == azimuth % 360
    assert actual_label == label
    assert source == "user-selected"
    assert .65 <= factor <= 1


@pytest.mark.parametrize("heading", [0, 90, 180, 270])
def test_horizontal_flush_array_is_independent_of_compass_heading(heading):
    result = solar_logic.calculate_accepted_surface_generation([
        _surface("flat", 2, 1, pitch=0, azimuth=None, surface_source="provider", surface_confidence="provider"),
    ], UNIFORM_PSH, fallback_azimuth=heading)
    surface = result["surfaces"][0]
    assert result["annual_generation_kwh"] == 1460
    assert result["weighted_orientation_factor"] == 1
    assert surface["roof_face_azimuth_degrees"] is None
    assert surface["panel_azimuth_degrees"] is None
    assert surface["panel_tilt_degrees"] == 0
    assert surface["mounting_basis"] == "flush_to_roof_planning_assumption"


@pytest.mark.parametrize("metadata", [
    {"surface_source": "prototype_fixture", "surface_confidence": "simulated"},
    {"surface_source": "user_boundary", "surface_confidence": "authoritative"},
    {"surface_geometry_kind": "simulated_planar_surface"},
])
@pytest.mark.parametrize("pitch", [0, 20])
def test_display_geometry_does_not_supply_measured_panel_orientation(metadata, pitch):
    result = solar_logic.calculate_accepted_surface_generation([
        _surface("display", 2, 1, pitch=pitch, azimuth=0, **metadata),
    ], UNIFORM_PSH, fallback_azimuth=180)
    surface = result["surfaces"][0]
    assert result["annual_generation_kwh"] == 1460
    assert surface["orientation_source"] == "global_fallback"
    assert surface["roof_pitch_degrees"] is None
    assert surface["roof_face_azimuth_degrees"] is None
    assert surface["panel_tilt_degrees"] is None
    assert surface["orientation_factor_basis"] == "azimuth_only_planning_approximation"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_global_orientation_is_rejected(invalid):
    with pytest.raises(ValueError, match="finite"):
        solar_logic.resolve_orientation(invalid, [])


@pytest.mark.parametrize("track", ["home", "business"])
def test_flat_surface_result_flows_to_track_financials_and_forecasts(track):
    from app.services.report_chart_data import build_report_chart_data

    coordinates = [[28.6139, 77.2090], [28.6139, 77.2092],
                   [28.6141, 77.2092], [28.6141, 77.2090]]
    with patch.object(solar_logic, "fetch_solar_data", return_value=(UNIFORM_PSH, None, "test_fixture", "low")):
        result = solar_logic.run_full_estimation(
            28.614, 77.2091, coordinates, tariff_per_kwh=8, orientation=90,
            accepted_panel_count=2, accepted_module_wattage=500,
            accepted_surfaces=[_surface("flat", 2, 1, pitch=0)], planning_track=track,
        )
    assert result["annual_generation"] == 1460
    assert result["generation_25yr"][0] == 1460
    assert result["financial_provenance"]["generation_input_kwh"] == 1460
    assert result["planning_track"] == track
    if track == "business":
        assert result["subsidy_amount"] == 0
    else:
        assert result["subsidy_amount"] > 0
    provenance = solar_logic.generation_provenance_for_result(result)
    assert provenance["orientation"]["global_direction_source"] == "user_specified"
    assert provenance["orientation"]["panel_tilt_degrees"] is None
    chart = build_report_chart_data({**result, "extras": {"generation_provenance": provenance}})
    assert chart["orientation"]["factor"] == 1
    assert chart["orientation"]["provenance"] == provenance["orientation"]


@pytest.mark.parametrize("pitch,azimuth", [(None, 90), (float("nan"), 90), (20, float("inf")), (True, 90)])
def test_missing_or_invalid_plane_never_claims_known_panel_orientation(pitch, azimuth):
    result = solar_logic.calculate_accepted_surface_generation([
        _surface("invalid", 2, 1, pitch=pitch, azimuth=azimuth),
    ], UNIFORM_PSH, fallback_azimuth=180)
    surface = result["surfaces"][0]
    assert surface["orientation_source"] == "global_fallback"
    assert surface["roof_face_azimuth_degrees"] is None
    assert surface["orientation_factor_basis"] == "azimuth_only_planning_approximation"


def test_horizontal_plane_does_not_require_a_valid_azimuth():
    result = solar_logic.calculate_accepted_surface_generation([
        _surface("flat", 2, 1, pitch=0, azimuth="unavailable"),
    ], UNIFORM_PSH, fallback_azimuth=90)
    assert result["annual_generation_kwh"] == 1460
    assert result["surfaces"][0]["panel_tilt_degrees"] == 0
