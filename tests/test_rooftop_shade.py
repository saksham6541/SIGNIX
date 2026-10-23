from app.services import rooftop_shade_service as shade


ORIGIN = [77.20907, 28.61395]
ROOF = {
    "type": "Polygon",
    "coordinates": [[
        [77.20900, 28.61390], [77.20914, 28.61390],
        [77.20914, 28.61400], [77.20900, 28.61400],
        [77.20900, 28.61390],
    ]],
}
OBSTRUCTION = {
    "id": "manual-1",
    "is_obstruction": True,
    "height_meters": None,
    "footprint_geojson": {
        "type": "Polygon",
        "coordinates": [[
            [77.209055, 28.613935], [77.209085, 28.613935],
            [77.209085, 28.613965], [77.209055, 28.613965],
            [77.209055, 28.613935],
        ]],
    },
}


def test_missing_shade_data_stays_unknown_but_geometry_is_classified():
    analysis = shade.build_shade_analysis(ROOF, ORIGIN, [OBSTRUCTION])

    assert analysis["schema_version"] == "1.0"
    assert analysis["status"] == "insufficient_data"
    assert analysis["source"]["type"] == "none"
    assert analysis["placement_policy"]["unknown_cells"] == "exclude"
    assert analysis["placement_policy"]["default_mode"] == "planning_preview"
    assert analysis["placement_policy"]["modes"]["planning_preview"] == {
        "unknown_cells": "allow_unverified", "shade_verified": False,
    }
    assert analysis["placement_policy"]["modes"]["conservative"] == {
        "unknown_cells": "exclude", "shade_verified": True,
    }
    assert analysis["counts"]["unknown"] > 0
    assert analysis["counts"]["obstruction"] > 0
    assert analysis["counts"]["setback"] > 0
    assert analysis["counts"]["shaded"] == 0
    assert analysis["areas_sqm"]["usable"] == 0
    assert analysis["areas_sqm"]["classified_roof"] == analysis["areas_sqm"]["authoritative_roof"]
    assert any("heights are unavailable" in item for item in analysis["diagnostics"])


def test_provider_flux_classifies_shade_and_excludes_missing_cells():
    provider = {
        "rows": 2,
        "cols": 2,
        "x": [-8.0, 8.0],
        "z": [-7.0, 7.0],
        "relativeFlux": [0.2, 0.95, None, 0.95],
        "threshold": 0.65,
    }
    analysis = shade.build_shade_analysis(ROOF, ORIGIN, provider_flux=provider)

    assert analysis["status"] == "available"
    assert analysis["source"]["type"] == "provider_annual_flux"
    assert analysis["placement_policy"]["unknown_cells"] == "exclude"
    assert analysis["placement_policy"]["default_mode"] == "conservative"
    assert analysis["counts"]["shaded"] > 0
    assert analysis["counts"]["usable"] > 0
    assert analysis["counts"]["unknown"] > 0


def test_known_height_can_cast_geometry_shadow_but_unknown_height_cannot():
    sun = {"azimuth_degrees": 90, "elevation_degrees": 35}
    unknown = shade.build_shade_analysis(ROOF, ORIGIN, [OBSTRUCTION], sun=sun)
    known_structure = dict(OBSTRUCTION, height_meters=2.0)
    known = shade.build_shade_analysis(ROOF, ORIGIN, [known_structure], sun=sun)

    assert unknown["source"]["type"] == "none"
    assert unknown["counts"]["shaded"] == 0
    assert known["source"]["type"] == "geometric_known_height"
    assert known["counts"]["shaded"] > 0


def test_provider_flux_has_priority_over_geometry_and_invalid_flux_is_insufficient():
    known_structure = dict(OBSTRUCTION, height_meters=2.0)
    provider = {
        "rows": 1, "cols": 1, "x": [0.0], "z": [0.0],
        "relativeFlux": [0.9], "threshold": 0.65,
    }
    preferred = shade.build_shade_analysis(
        ROOF, ORIGIN, [known_structure], provider_flux=provider,
        sun={"azimuth_degrees": 90, "elevation_degrees": 35},
    )
    invalid = shade.build_shade_analysis(
        ROOF, ORIGIN, provider_flux={"rows": 1, "cols": 1, "relativeFlux": [None]}
    )

    assert preferred["source"]["type"] == "provider_annual_flux"
    assert invalid["status"] == "insufficient_data"
    assert invalid["source"]["type"] == "none"


def test_overlapping_and_malformed_obstructions_do_not_double_count_or_crash():
    malformed = {"is_obstruction": True, "footprint_geojson": {"type": "Polygon", "coordinates": [[]]}}
    overlapping = {
        **OBSTRUCTION,
        "id": "manual-2",
        "footprint_geojson": {
            "type": "Polygon",
            "coordinates": [[
                [77.209070, 28.613940], [77.209100, 28.613940],
                [77.209100, 28.613970], [77.209070, 28.613970],
                [77.209070, 28.613940],
            ]],
        },
    }
    analysis = shade.build_shade_analysis(ROOF, ORIGIN, [OBSTRUCTION, overlapping, malformed])

    classified = sum(
        analysis["areas_sqm"][name]
        for name in ("usable", "obstruction", "setback", "shaded", "unknown")
    )
    assert analysis["areas_sqm"]["obstruction"] > 0
    assert round(classified, 2) == analysis["areas_sqm"]["classified_roof"]
    assert classified <= analysis["areas_sqm"]["authoritative_roof"]
    assert analysis["classification_precedence"] == [
        "outside_roof", "obstruction", "setback", "shaded", "unknown", "usable"
    ]


def test_classification_is_deterministic_and_resource_bounded_for_concave_roof():
    concave = {
        "type": "Polygon",
        "coordinates": [[
            [77.2080, 28.6130], [77.2110, 28.6130], [77.2110, 28.6160],
            [77.2095, 28.6145], [77.2080, 28.6160], [77.2080, 28.6130],
        ]],
    }
    first = shade.build_shade_analysis(concave, ORIGIN, resolution_meters=.2)
    second = shade.build_shade_analysis(concave, ORIGIN, resolution_meters=.2)

    assert first == second
    assert first["rows"] * first["cols"] <= shade.MAX_GRID_CELLS
    assert first["counts"]["outside_roof"] > 0
    assert sum(first["counts"].values()) == first["rows"] * first["cols"]


def test_explicit_demo_simulation_is_labelled_and_never_implicit():
    normal = shade.build_shade_analysis(ROOF, ORIGIN)
    demo = shade.build_shade_analysis(ROOF, ORIGIN, demo_simulation=True)

    assert normal["status"] == "insufficient_data"
    assert normal["counts"]["shaded"] == 0
    assert demo["status"] == "simulated"
    assert demo["source"]["type"] == "explicit_demo_simulation"
    assert demo["counts"]["shaded"] > 0
