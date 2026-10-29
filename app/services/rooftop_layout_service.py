"""Server-validated acceptance of renderer-proposed rooftop panel layouts."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math

from flask import current_app

from app.assumptions import MODULE_WATTAGE, get_value
from app.models import RooftopDesign, db
from app.solar_logic import current_assumption_fingerprint
from app.services import (
    estimation_service,
    rooftop_design_service,
    rooftop_ingestion_service,
    rooftop_reconstruction_service,
)


class LayoutAcceptanceError(ValueError):
    def __init__(self, message, code="invalid_layout", status=400, details=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or []


def _fingerprint(revision_id, geometry_key, mode, panels):
    canonical = {
        "revision_id": revision_id,
        "geometry_key": geometry_key,
        "layout_mode": mode,
        "panels": panels,
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _point_in_ring(point, ring):
    x, y = point
    inside = False
    for index, current in enumerate(ring):
        previous = ring[index - 1]
        if ((current[1] > y) != (previous[1] > y)
                and x < (previous[0] - current[0]) * (y - current[1])
                / ((previous[1] - current[1]) or 1e-15) + current[0]):
            inside = not inside
    return inside


def _distance_point_edges(point, ring):
    result = math.inf
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = dx * dx + dy * dy
        fraction = 0 if not length else max(0, min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length))
        closest = [start[0] + fraction * dx, start[1] + fraction * dy]
        result = min(result, math.hypot(point[0] - closest[0], point[1] - closest[1]))
    return result


def _orientation(a, b, c):
    value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return 0 if abs(value) < 1e-8 else (1 if value > 0 else 2)


def _on_segment(a, b, p):
    return (min(a[0], b[0]) - 1e-7 <= p[0] <= max(a[0], b[0]) + 1e-7
            and min(a[1], b[1]) - 1e-7 <= p[1] <= max(a[1], b[1]) + 1e-7)


def _segments_intersect(a, b, c, d):
    o1, o2, o3, o4 = (_orientation(a, b, c), _orientation(a, b, d),
                      _orientation(c, d, a), _orientation(c, d, b))
    return ((o1 != o2 and o3 != o4)
            or (o1 == 0 and _on_segment(a, b, c))
            or (o2 == 0 and _on_segment(a, b, d))
            or (o3 == 0 and _on_segment(c, d, a))
            or (o4 == 0 and _on_segment(c, d, b)))


def _edges_intersect(a, b):
    return any(_segments_intersect(a[i], a[(i + 1) % len(a)], b[j], b[(j + 1) % len(b)])
               for i in range(len(a)) for j in range(len(b)))


def _overlap(a, b):
    return _edges_intersect(a, b) or _point_in_ring(a[0], b) or _point_in_ring(b[0], a)


def _polygon_distance(a, b):
    return min([_distance_point_edges(point, b) for point in a]
               + [_distance_point_edges(point, a) for point in b])


def _fits(panel, roof, obstructions, edge_setback, obstruction_setback):
    if any(not _point_in_ring(point, roof) for point in panel) or _edges_intersect(panel, roof):
        return False
    if edge_setback and any(_distance_point_edges(point, roof) < edge_setback - 1e-7 for point in panel):
        return False
    for obstruction in obstructions:
        if _overlap(panel, obstruction) or _polygon_distance(panel, obstruction) < obstruction_setback - 1e-7:
            return False
    return True


def _grid_valid(grid):
    try:
        return (isinstance(grid, dict) and grid.get("geometry_compatible") is not False
                and int(grid["rows"]) > 0 and int(grid["cols"]) > 0
                and len(grid["classifications"]) >= int(grid["rows"]) * int(grid["cols"])
                and float(grid["resolution_meters"]) > 0
                and math.isfinite(float(grid["origin_x"]))
                and math.isfinite(float(grid["origin_z"])))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _grid_classes(panel, grid):
    step, ox, oz = float(grid["resolution_meters"]), float(grid["origin_x"]), float(grid["origin_z"])
    rows, cols = int(grid["rows"]), int(grid["cols"])
    xs, zs = [p[0] for p in panel], [p[1] for p in panel]
    c0, c1 = max(0, math.floor((min(xs) - ox) / step)), min(cols - 1, math.floor((max(xs) - ox) / step))
    r0, r1 = max(0, math.floor((min(zs) - oz) / step)), min(rows - 1, math.floor((max(zs) - oz) / step))
    found = set()
    for row in range(r0, r1 + 1):
        for col in range(c0, c1 + 1):
            x0, z0 = ox + col * step, oz + row * step
            cell = [[x0, z0], [x0 + step, z0], [x0 + step, z0 + step], [x0, z0 + step]]
            if _overlap(panel, cell):
                found.add(grid["classifications"][row * cols + col])
    return found


def _sample_provider_flux(panel, grid):
    """Return the nearest absolute annual-flux value at a panel centroid."""
    if not isinstance(grid, dict) or grid.get("units") != "kWh_per_kW_per_year":
        return None
    if grid.get("temporal_coverage") != "annual":
        return None
    xs, zs, values = grid.get("x") or [], grid.get("z") or [], grid.get("rawFlux") or []
    rows, cols = int(grid.get("rows") or 0), int(grid.get("cols") or 0)
    if rows < 1 or cols < 1 or len(xs) != cols or len(zs) != rows or len(values) < rows * cols:
        return None
    x = sum(point[0] for point in panel) / len(panel)
    z = sum(point[1] for point in panel) / len(panel)
    if x < min(xs) or x > max(xs) or z < min(zs) or z > max(zs):
        return None
    column = min(range(cols), key=lambda item: abs(float(xs[item]) - x))
    row = min(range(rows), key=lambda item: abs(float(zs[item]) - z))
    try:
        value = float(values[row * cols + column])
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _height(surface, point):
    pitch = math.radians(float(surface.get("pitch_degrees") or 0))
    azimuth = math.radians(float(surface.get("azimuth_degrees") or 0))
    reference = surface.get("reference_local_xz_meters") or [0, 0]
    return -math.tan(pitch) * ((point[0] - reference[0]) * math.sin(azimuth)
                              - (point[1] - reference[1]) * math.cos(azimuth))


def _valid_dimensions(panel, surface, width, height):
    if (_segments_intersect(panel[0], panel[1], panel[2], panel[3])
            or _segments_intersect(panel[1], panel[2], panel[3], panel[0])):
        return False
    lengths = []
    for index, start in enumerate(panel):
        end = panel[(index + 1) % len(panel)]
        lengths.append(math.sqrt((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2
                                 + (_height(surface, end) - _height(surface, start)) ** 2))
    return (abs(lengths[0] - lengths[2]) <= .02 and abs(lengths[1] - lengths[3]) <= .02
            and all(abs(actual - expected) <= .025 for actual, expected in zip(sorted(lengths[:2]), sorted([width, height]))))


def _reconstruction(location, design):
    prototype = bool(current_app.config.get("ROOFTOP_PROTOTYPE_FIXTURE", True))
    key_configured = bool(current_app.config.get("GOOGLE_SOLAR_API_KEY") or current_app.config.get("GOOGLE_MAPS_API_KEY"))
    provider = None
    if not prototype or key_configured:
        provider = rooftop_ingestion_service.legacy_roof_model_payload(design, location=location)
    return rooftop_reconstruction_service.build_reconstruction(
        location, design_data=design.to_dict(), provider_payload=provider,
        prototype_fixture=prototype,
    )


def accept_layout(location_id, user_id, payload):
    if not isinstance(payload, dict):
        raise LayoutAcceptanceError("Request body must be a JSON object")
    forbidden = {"panel_count", "capacity_kw", "dc_capacity_kw", "generation", "annual_generation", "financials"}
    supplied = sorted(forbidden.intersection(payload))
    if supplied:
        raise LayoutAcceptanceError("Client-derived authoritative values are not accepted", "client_values_not_allowed", details=supplied)
    location = rooftop_design_service.get_owned_location(location_id, user_id)
    if location is None:
        return None
    design = (RooftopDesign.query.filter_by(user_location_id=location_id, is_current=True)
              .order_by(RooftopDesign.revision_number.desc()).first())
    if design is None:
        raise LayoutAcceptanceError("A current rooftop design is required", "design_required", 409)
    mode = payload.get("layout_mode")
    if mode not in {"planning_preview", "conservative"}:
        raise LayoutAcceptanceError("layout_mode must be planning_preview or conservative")
    panels = payload.get("panels")
    if not isinstance(panels, list) or not panels or len(panels) > 160:
        raise LayoutAcceptanceError("panels must contain between 1 and 160 placement records")
    normalized = []
    for index, item in enumerate(panels):
        if not isinstance(item, dict) or not isinstance(item.get("surface_id"), str):
            raise LayoutAcceptanceError(f"Panel {index + 1} has no valid surface_id")
        corners = item.get("corners_local_xz_meters")
        if not isinstance(corners, list) or len(corners) != 4:
            raise LayoutAcceptanceError(f"Panel {index + 1} must have four corners")
        try:
            corners = [[float(point[0]), float(point[1])] for point in corners]
        except (TypeError, ValueError, IndexError):
            raise LayoutAcceptanceError(f"Panel {index + 1} contains invalid coordinates") from None
        if not all(math.isfinite(value) for point in corners for value in point):
            raise LayoutAcceptanceError(f"Panel {index + 1} contains non-finite coordinates")
        normalized.append({"surface_id": item["surface_id"], "corners_local_xz_meters": [[round(v, 6) for v in p] for p in corners]})
    revision_id = payload.get("design_revision_id")
    geometry_key = payload.get("geometry_key")
    fingerprint = _fingerprint(revision_id, geometry_key, mode, normalized)
    assumption_fingerprint = current_assumption_fingerprint()
    existing = design.panel_layout if isinstance(design.panel_layout, dict) else {}
    if (
        existing.get("status") == "accepted"
        and existing.get("request_fingerprint") == fingerprint
        and existing.get("assumption_fingerprint") == assumption_fingerprint
    ):
        return {"accepted_layout": existing, "estimate": location.to_dict(), "idempotent": True}
    if revision_id != design.id or payload.get("design_revision_number") != design.revision_number:
        raise LayoutAcceptanceError("The rooftop design changed; reconstruct the layout before accepting it", "stale_design_revision", 409)
    model = _reconstruction(location, design)
    if not model.get("available") or geometry_key != model.get("geometry_key"):
        raise LayoutAcceptanceError("The reconstruction changed; reload the rooftop model", "incompatible_geometry_key", 409)
    constraints = model["constraints"]
    width, height = float(constraints["panel_width_meters"]), float(constraints["panel_height_meters"])
    edge_setback = float(constraints["edge_setback_meters"])
    obstruction_setback = .30
    boundary = model["authoritative_boundary_local_xz_meters"]
    obstructions = [item["footprint_local_xz_meters"] for item in model.get("structures", []) if item.get("is_obstruction", True)]
    surfaces = {item["id"]: item for item in model.get("surfaces", []) if item.get("is_valid")}
    grid = model.get("shade_analysis")
    if mode == "conservative" and not _grid_valid(grid):
        raise LayoutAcceptanceError("Conservative acceptance requires compatible roof classification data", "classification_unavailable", 409)
    accepted, preview_count = [], 0
    exclusions = {"outside_roof", "obstruction", "setback", "shaded"}
    for index, record in enumerate(normalized):
        panel, surface = record["corners_local_xz_meters"], surfaces.get(record["surface_id"])
        if surface is None:
            raise LayoutAcceptanceError(f"Panel {index + 1} references an unavailable surface")
        surface_ring = surface["polygon_local_xz_meters"]
        if not _valid_dimensions(panel, surface, width, height):
            raise LayoutAcceptanceError(f"Panel {index + 1} does not match the configured module dimensions")
        if not _fits(panel, surface_ring, [], 0, 0) or not _fits(panel, boundary, obstructions, edge_setback, obstruction_setback):
            raise LayoutAcceptanceError(f"Panel {index + 1} violates a roof boundary, setback, or obstruction", "panel_geometry_invalid")
        classes = _grid_classes(panel, grid) if _grid_valid(grid) else {"unknown"}
        if classes.intersection(exclusions) or (mode == "conservative" and "unknown" in classes):
            raise LayoutAcceptanceError(f"Panel {index + 1} intersects an excluded roof classification", "panel_classification_invalid")
        if any(_overlap(panel, other["corners_local_xz_meters"]) for other in accepted):
            raise LayoutAcceptanceError(f"Panel {index + 1} overlaps another panel", "panel_overlap")
        shade_verified = _grid_valid(grid) and "unknown" not in classes
        preview_count += int(not shade_verified)
        accepted.append({**record, "shade_verified": shade_verified})
    module_wattage = float(get_value("module_wattage", MODULE_WATTAGE.value))
    capacity_kw = round(len(accepted) * module_wattage / 1000, 6)
    provider_flux = model.get("shade") or {}
    surface_generation_inputs = []
    for surface_id in sorted({panel["surface_id"] for panel in accepted}):
        surface_panels = [panel for panel in accepted if panel["surface_id"] == surface_id]
        surface = surfaces[surface_id]
        samples = [
            _sample_provider_flux(panel["corners_local_xz_meters"], provider_flux)
            for panel in surface_panels
        ]
        valid_samples = [value for value in samples if value is not None]
        quantitative_flux = None
        shade_status = "unknown_not_quantified" if any(
            not panel["shade_verified"] for panel in surface_panels
        ) else "qualitative_classification_not_quantified"
        if len(valid_samples) == len(surface_panels):
            quantitative_flux = {
                "source": "provider_annual_flux",
                "units": "kWh_per_kW_per_year",
                "temporal_coverage": "annual",
                "specific_yield_kwh_per_kw_year": round(sum(valid_samples) / len(valid_samples), 3),
                "coverage_panels": len(valid_samples),
                "provenance": deepcopy(provider_flux.get("provenance") or {}),
            }
            shade_status = "provider_flux_incorporated_not_isolated"
        elif valid_samples:
            shade_status = "partial_provider_flux_not_applied"
        surface_generation_inputs.append({
            "surface_id": surface_id,
            "panel_count": len(surface_panels),
            "capacity_kw": round(len(surface_panels) * module_wattage / 1000, 6),
            "pitch_degrees": surface.get("pitch_degrees"),
            "azimuth_degrees": surface.get("azimuth_degrees"),
            "surface_source": surface.get("source"),
            "surface_confidence": surface.get("confidence"),
            "surface_geometry_kind": surface.get("geometry_kind"),
            "shade_status": shade_status,
            "quantitative_flux": quantitative_flux,
        })
    now = datetime.now(timezone.utc).isoformat()
    layout = {
        "schema_version": "1.0", "status": "accepted", "accepted_at": now,
        "based_on_design_revision_id": design.id,
        "based_on_design_revision_number": design.revision_number,
        "geometry_key": geometry_key, "layout_mode": mode,
        "shade_status": grid.get("status") if isinstance(grid, dict) else "unavailable",
        "shade_verified": preview_count == 0,
        "geometry_mode": model.get("mode"), "geometry_status": model.get("status_badge"),
        "panel_count": len(accepted), "preview_panel_count": preview_count,
        "module": {"width_meters": width, "height_meters": height, "wattage_w": module_wattage},
        "dc_capacity_kw": capacity_kw, "panels": accepted,
        "surface_generation_inputs": surface_generation_inputs,
        "request_fingerprint": fingerprint,
        "assumption_fingerprint": assumption_fingerprint,
        "limitations": [
            "Acceptance records a selected proposal; it does not certify installation or surveyed geometry.",
            "Unknown shade remains unverified and is not converted to usable shade data.",
            "Surface azimuth is used when valid; tilt remains unmodelled without plane-of-array irradiance.",
            "Qualitative shade classifications are not converted into numeric generation losses.",
        ],
    }
    try:
        revised = rooftop_design_service.create_revision(
            location_id, user_id,
            {"panel_layout": layout, "diagnostics": {**(design.diagnostics or {}), "derived_state": {"status": "current", "panel_layout_current": True, "estimate_recalculation_required": False}}},
            base_design=design, commit=False,
        )
        layout["accepted_design_revision_id"] = revised.id
        layout["accepted_design_revision_number"] = revised.revision_number
        revised.panel_layout = deepcopy(layout)
        result = estimation_service.recalculate_for_accepted_layout(
            location, len(accepted), module_wattage,
            {key: value for key, value in layout.items() if key != "panels"},
            surface_generation_inputs,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return {"accepted_layout": layout, "estimate": location.to_dict(), "calculation": result, "idempotent": False}
