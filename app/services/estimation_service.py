import math
import re
import time
import uuid

from app.config import Config
from app.geo import (
    GeometryValidationError,
    canonicalize_stored_ring,
    geojson_ring_to_latlng,
    leaflet_ring_to_geojson,
    ring_strictly_contains,
    rings_have_positive_area_overlap,
    validate_leaflet_ring,
)
from app.models import db, TariffTable, UserLocation
from app.services import (
    commercial_calculation_service,
    residential_calculation_service,
)
from app.solar_logic import (
    calculation_assumptions,
    current_assumption_fingerprint,
    generation_provenance_for_result,
    run_full_estimation,
)


def _finite_float(value, field_name, *, required=False, minimum=None, maximum=None):
    if value in (None, ""):
        if required:
            raise InvalidEstimateInputError(f"{field_name} is required")
        return None
    if isinstance(value, bool):
        raise InvalidEstimateInputError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidEstimateInputError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise InvalidEstimateInputError(f"{field_name} must be a finite number")
    if minimum is not None and number < minimum:
        raise InvalidEstimateInputError(f"{field_name} must be at least {minimum:g}")
    if maximum is not None and number > maximum:
        raise InvalidEstimateInputError(f"{field_name} must be at most {maximum:g}")
    return number



class EstimationEngineError(Exception):
    pass


class EstimatePersistenceError(Exception):
    def __init__(self, message, schema_outdated=False):
        super().__init__(message)
        self.schema_outdated = schema_outdated


class InvalidEstimateInputError(Exception):
    pass


def _planning_policy(planning_track, property_type=None, load_profile=None):
    if planning_track == "business":
        return commercial_calculation_service.resolve_policy(load_profile)
    return residential_calculation_service.resolve_policy(property_type, load_profile)


def _legacy_planning_track(property_type):
    return (
        "business"
        if str(property_type or "").strip().lower()
        in {"commercial", "industrial", "msme", "factory"}
        else "home"
    )


def lookup_tariff(state, planning_track="home"):
    """Return rate_per_kwh for state, or config default."""
    info = lookup_tariff_defaults(state, planning_track=planning_track)
    return info["rate_per_kwh"]


def lookup_tariff_defaults(state, planning_track="home"):
    """Track-aware tariff defaults for UI and estimate preparation."""
    policy = _planning_policy(planning_track)
    defaults = {
        "state": state,
        "planning_track": policy["planning_track"],
        "rate_per_kwh": policy["default_tariff_per_kwh"],
        "net_metering_mode": "full_offset",
        "export_credit_rate": getattr(Config, "DEFAULT_FEED_IN_TARIFF", 3.0),
        "utility_name": None,
        "notes": None,
        "found": False,
        "basis": policy["tariff_basis"],
    }
    # TariffTable currently stores residential/state defaults. Never silently
    # relabel those rows as a verified commercial tariff.
    if policy["planning_track"] == "business":
        defaults["notes"] = "Commercial tariff is an unverified configurable default; confirm the applicable bill rate."
        return defaults
    if not state:
        return defaults
    row = TariffTable.query.filter_by(state=state).first()
    if not row:
        # case-insensitive fallback
        row = TariffTable.query.filter(
            TariffTable.state.ilike(str(state).strip())
        ).first()
    if row:
        defaults.update(
            {
                "state": row.state,
                "rate_per_kwh": row.rate_per_kwh,
                "net_metering_mode": row.net_metering_mode or "full_offset",
                "export_credit_rate": (
                    row.export_credit_rate
                    if row.export_credit_rate is not None
                    else row.rate_per_kwh
                ),
                "utility_name": row.utility_name,
                "notes": row.notes,
                "found": True,
            }
        )
    return defaults


def _validate_estimate_geometry(polygon, obstructions=None):
    try:
        polygon = validate_leaflet_ring(polygon)
    except GeometryValidationError as exc:
        raise InvalidEstimateInputError(f"Invalid rooftop polygon: {exc}") from exc

    if obstructions is None:
        obstructions = []
    if not isinstance(obstructions, list):
        raise InvalidEstimateInputError("obstructions must be an array of polygons")
    validated_obstructions = []
    for index, obstruction in enumerate(obstructions):
        try:
            validated = validate_leaflet_ring(obstruction)
        except GeometryValidationError as exc:
            raise InvalidEstimateInputError(
                f"Invalid obstruction polygon at index {index}: {exc}"
            ) from exc
        if not ring_strictly_contains(polygon, validated):
            raise InvalidEstimateInputError(
                f"Obstruction polygon at index {index} must remain fully inside the rooftop polygon"
            )
        if any(
            rings_have_positive_area_overlap(validated, existing)
            for existing in validated_obstructions
        ):
            raise InvalidEstimateInputError("Obstruction polygons must not overlap")
        validated_obstructions.append(validated)
    return polygon, validated_obstructions


def normalize_location_context(value):
    """Validate optional public-area metadata without inventing missing values."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise InvalidEstimateInputError("location_context must be an object")
    normalized = {}
    for field in ("city", "locality", "pincode"):
        raw = value.get(field)
        if raw in (None, ""):
            continue
        if not isinstance(raw, str) or len(raw.strip()) > 120:
            raise InvalidEstimateInputError(
                f"{field} must be text of at most 120 characters"
            )
        normalized[field] = raw.strip()
    if normalized.get("pincode") and not re.fullmatch(
        r"[0-9]{6}", normalized["pincode"]
    ):
        raise InvalidEstimateInputError("pincode must be a six-digit PIN code")
    return normalized


def prepare_estimate_inputs(payload, planning_track="home"):
    if not isinstance(payload, dict):
        raise InvalidEstimateInputError("Request body must be a JSON object")
    latitude = _finite_float(
        payload.get("latitude"), "latitude", required=True, minimum=-90, maximum=90
    )
    longitude = _finite_float(
        payload.get("longitude"), "longitude", required=True, minimum=-180, maximum=180
    )
    polygon = payload.get("polygon", [])

    if not polygon or len(polygon) < 3:
        raise InvalidEstimateInputError(
            "A rooftop polygon with at least 3 points is required"
        )
    polygon, validated_obstructions = _validate_estimate_geometry(
        polygon, payload.get("obstructions", [])
    )

    battery_kwh = _finite_float(
        payload.get("battery_kwh", 0), "battery_kwh", minimum=0
    )
    monthly_bill = _finite_float(
        payload.get("monthly_bill"), "monthly_bill", minimum=0
    )

    planning_track = "business" if planning_track == "business" else "home"
    policy = _planning_policy(
        planning_track, payload.get("property_type"), payload.get("load_profile")
    )
    state_defaults = lookup_tariff_defaults(
        payload.get("state"), planning_track=planning_track
    )
    tariff = state_defaults["rate_per_kwh"]
    if payload.get("tariff_per_kwh") not in (None, ""):
        tariff = _finite_float(
            payload["tariff_per_kwh"], "tariff_per_kwh", minimum=0
        )

    # Net-metering: explicit payload wins; else state default
    net_mode = payload.get("net_metering_mode") or state_defaults["net_metering_mode"]
    feed_in = _finite_float(
        payload.get("feed_in_tariff"), "feed_in_tariff", minimum=0
    )
    if feed_in is None:
        feed_in = state_defaults.get("export_credit_rate")

    needs_backup = payload.get("needs_backup", False)
    if not isinstance(needs_backup, bool):
        raise InvalidEstimateInputError("needs_backup must be a boolean")

    orientation = payload.get("orientation", "auto")
    if str(orientation).strip().lower() not in {"", "auto", "none"}:
        orientation = _finite_float(
            orientation, "orientation", minimum=0, maximum=360
        )

    return {
        "address": payload.get("address", "Unknown address"),
        "latitude": latitude,
        "longitude": longitude,
        "polygon": polygon,
        "obstructions": validated_obstructions,
        "tariff_per_kwh": tariff,
        "orientation": orientation,
        "battery_kwh": battery_kwh,
        "monthly_bill": monthly_bill,
        "planning_track": planning_track,
        "property_type": policy["property_type"],
        "user_priority": payload.get("user_priority") or "no_preference",
        "needs_backup": needs_backup,
        "inverter_preference": payload.get("inverter_preference") or "auto",
        "sizing_mode": payload.get("sizing_mode"),
        "budget_inr": _finite_float(payload.get("budget_inr"), "budget_inr", minimum=0),
        "critical_load_kw": _finite_float(
            payload.get("critical_load_kw"), "critical_load_kw", minimum=0
        ),
        "backup_hours": _finite_float(
            payload.get("backup_hours"), "backup_hours", minimum=0
        ),
        "load_profile": payload.get("load_profile") or policy["default_load_profile"],
        "net_metering_mode": net_mode or "full_offset",
        "feed_in_tariff": feed_in,
        "state": payload.get("state"),
        "location_context": normalize_location_context(
            payload.get("location_context")
        ),
    }


def create_estimate(
    user_id,
    address,
    latitude,
    longitude,
    polygon,
    obstructions=None,
    tariff_per_kwh=Config.DEFAULT_TARIFF,
    orientation="auto",
    battery_kwh=0.0,
    monthly_bill=None,
    property_type="residential",
    planning_track=None,
    user_priority="no_preference",
    needs_backup=False,
    inverter_preference="auto",
    sizing_mode=None,
    budget_inr=None,
    critical_load_kw=None,
    backup_hours=None,
    load_profile="flat",
    net_metering_mode="full_offset",
    feed_in_tariff=None,
    state=None,
    location_context=None,
    roof_group_id=None,
    parent_location_id=None,
    scenario_label=None,
    commit=True,
):
    polygon, obstructions = _validate_estimate_geometry(polygon, obstructions)
    total_start = time.perf_counter()
    try:
        planning_track = planning_track or _legacy_planning_track(property_type)
        result = run_full_estimation(
            latitude=latitude,
            longitude=longitude,
            coordinates=polygon,
            obstructions=obstructions,
            tariff_per_kwh=tariff_per_kwh,
            orientation=orientation,
            battery_kwh=battery_kwh,
            monthly_bill=monthly_bill,
            property_type=property_type,
            planning_track=planning_track,
            user_priority=user_priority,
            needs_backup=needs_backup,
            inverter_preference=inverter_preference,
            sizing_mode=sizing_mode,
            budget_inr=budget_inr,
            critical_load_kw=critical_load_kw,
            backup_hours=backup_hours,
            load_profile=load_profile,
            net_metering_mode=net_metering_mode,
            feed_in_tariff=feed_in_tariff,
        )
    except Exception as exc:
        raise EstimationEngineError(str(exc)) from exc

    try:
        resolved_group = roof_group_id or str(uuid.uuid4())
        resolved_mode = sizing_mode or result.get("sizing_method") or (
            (result.get("sizing") or {}).get("mode")
        )
        if not scenario_label:
            scenario_label = _default_scenario_label(
                resolved_mode, needs_backup or float(battery_kwh or 0) > 0
            )
        location = UserLocation(
            user_id=user_id,
            address=address,
            latitude=latitude,
            longitude=longitude,
            user_priority=user_priority or "no_preference",
            roof_group_id=resolved_group,
            parent_location_id=parent_location_id,
            scenario_label=scenario_label,
            sizing_mode=resolved_mode,
            polygon_geojson={
                "type": "Polygon",
                "coordinates": [leaflet_ring_to_geojson(polygon)],
            },
            obstructions_geojson=(
                {
                    "type": "MultiPolygon",
                    "coordinates": [[leaflet_ring_to_geojson(o)] for o in obstructions],
                }
                if obstructions
                else None
            ),
            system_size=result["system_size"],
            annual_generation=result["annual_generation"],
            monthly_data=result["monthly_data"],
            roof_area_sqm=result["roof_area_sqm"],
            obstructed_area_sqm=result["obstructed_area_sqm"],
            usable_area_sqm=result["usable_area_sqm"],
            system_cost=result["system_cost"],
            subsidy_amount=result["subsidy_amount"],
            net_investment=result["net_investment"],
            monthly_savings=result["monthly_savings"],
            co2_reduction_tons=result["co2_reduction_tons"],
            irradiance_source=result["irradiance_source"],
            orientation_deg=result.get("orientation_deg"),
            orientation_label=result.get("orientation_label"),
            orientation_factor=result.get("orientation_factor"),
            recommended_tilt_deg=result.get("recommended_tilt_deg"),
            battery_kwh=result.get("battery_kwh", 0),
            battery_cost=result.get("battery_cost", 0),
            specific_yield=result.get("specific_yield"),
            capacity_factor=result.get("capacity_factor"),
            lcoe=result.get("lcoe"),
            lifetime_kwh=result.get("lifetime_kwh"),
            self_consumption_frac=result.get("self_consumption_frac"),
            payback_years=result.get("payback_years"),
            suitability_rating=result.get("suitability_rating"),
            extras={
                "monthly_bill": monthly_bill,
                "tariff_per_kwh": tariff_per_kwh,
                "load_profile": load_profile,
                "net_metering_mode": net_metering_mode,
                "feed_in_tariff": feed_in_tariff,
                "state": state,
                "orientation_input": orientation,
                "inverter_preference": inverter_preference,
                "critical_load_kw": critical_load_kw,
                "backup_hours": backup_hours,
                "budget_inr": budget_inr,
                "orientation_source": result.get("orientation_source"),
                "irradiance_confidence": result.get("irradiance_confidence"),
                "cashflow_25yr": result.get("cashflow_25yr"),
                "annual_savings": result.get("annual_savings"),
                "year_one_net_savings": result.get("year_one_net_savings"),
                "self_consumed_kwh": result.get("self_consumed_kwh"),
                "exported_kwh": result.get("exported_kwh"),
                "export_value": result.get("export_value"),
                "environmental_equivalents": result.get("environmental_equivalents"),
                "inverter": result.get("inverter"),
                "inverter_type": result.get("inverter_type"),
                "bill_sizing": result.get("bill_sizing"),
                "roof_capacity_kw": result.get("roof_capacity_kw"),
                "sizing_method": result.get("sizing_method"),
                "sizing": result.get("sizing"),
                "battery_sizing": result.get("battery_sizing"),
                "rule_of_thumb_monthly_units": result.get(
                    "rule_of_thumb_monthly_units"
                ),
                "rule_of_thumb_annual_units": result.get("rule_of_thumb_annual_units"),
                "property_type": result.get("property_type"),
                "planning_track": result.get("planning_track"),
                "calculation_policy": result.get("calculation_policy"),
                "payback_without_subsidy_years": result.get("payback_without_subsidy_years"),
                "subsidy_adjusted_payback_years": result.get("subsidy_adjusted_payback_years"),
                "annual_net_benefit": result.get("annual_net_benefit"),
                "annual_roi_pct": result.get("annual_roi_pct"),
                "lifetime_net_benefit": result.get("lifetime_net_benefit"),
                "demand_charge_savings": result.get("demand_charge_savings"),
                "needs_backup": result.get("needs_backup"),
                "planning_notes": result.get("planning_notes"),
                "system_cost_low": result.get("system_cost_low"),
                "system_cost_high": result.get("system_cost_high"),
                "annual_om": result.get("annual_om"),
                "discounted_payback_years": result.get("discounted_payback_years"),
                "npv": result.get("npv"),
                "discount_rate_pct": result.get("discount_rate_pct"),
                "lifecycle_projection": result.get("lifecycle_projection"),
                "financial_provenance": result.get("financial_provenance"),
                "performance_ratio": result.get("performance_ratio"),
                "peak_sun_hours_assumed": result.get("peak_sun_hours_assumed"),
                "daily_units_per_kw_range": result.get("daily_units_per_kw_range"),
                "dc_note": result.get("dc_note"),
                "cost_per_kw": result.get("cost_per_kw"),
                "simple_payback_years": result.get("simple_payback_years"),
                "nominal_payback_years": result.get("nominal_payback_years"),
                "tariff_escalation_pct": result.get("tariff_escalation_pct"),
                "emi": result.get("emi"),
                "financing_snapshot": result.get("financing_snapshot"),
                "financing_comparison": result.get("financing_comparison"),
                "generation_25yr": result.get("generation_25yr"),
                "sensitivity": result.get("sensitivity"),
                "panel_count": result.get("panel_count"),
                "panel_estimate": result.get("panel_estimate"),
                "generation_model": result.get("generation_model"),
                "generation_provenance": generation_provenance_for_result(result),
                "calculation_assumptions": calculation_assumptions(),
                "assumption_fingerprint": current_assumption_fingerprint(),
                "surface_generation": result.get("surface_generation"),
                "surface_generation_status": "current" if result.get("surface_generation") else None,
                "location_context": dict(location_context or {}),
                **dict(location_context or {}),
            },
        )

        db_save_start = time.perf_counter()
        db.session.add(location)
        if commit:
            db.session.commit()
        else:
            db.session.flush()
        db_save_elapsed = (time.perf_counter() - db_save_start) * 1000
        print(f"[TIMING] /api/estimate DB save elapsed: {db_save_elapsed:.2f} ms")
    except Exception as exc:
        db.session.rollback()
        err_msg = str(exc)
        schema_outdated = (
            "no such column" in err_msg.lower() or "has no column" in err_msg.lower()
        )
        raise EstimatePersistenceError(
            err_msg, schema_outdated=schema_outdated
        ) from exc

    elapsed_ms = (time.perf_counter() - total_start) * 1000
    print(f"[TIMING] /api/estimate total elapsed: {elapsed_ms:.2f} ms")
    return result, location



def _estimate_inputs_from_location(location):
    """Reconstruct the original estimation inputs for a saved location."""
    extras = location.extras if isinstance(location.extras, dict) else {}
    polygon = _polygon_from_location(location)
    if not polygon:
        raise InvalidEstimateInputError("Saved estimate has no rooftop polygon")
    obstructions = _obstructions_from_location(location)
    polygon, obstructions = _validate_estimate_geometry(polygon, obstructions)
    property_type = extras.get("property_type") or "residential"
    planning_track = extras.get("planning_track")
    if planning_track not in {"home", "business"}:
        planning_track = _legacy_planning_track(property_type)
    inputs = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "coordinates": polygon,
        "obstructions": obstructions,
        "tariff_per_kwh": extras.get("tariff_per_kwh") or Config.DEFAULT_TARIFF,
        "battery_kwh": float(location.battery_kwh or 0),
        "monthly_bill": extras.get("monthly_bill"),
        "property_type": property_type,
        "planning_track": planning_track,
        "user_priority": location.user_priority or "no_preference",
        "needs_backup": bool(extras.get("needs_backup")),
        "inverter_preference": extras.get("inverter_preference") or "auto",
        "sizing_mode": location.sizing_mode or extras.get("sizing_method"),
        "budget_inr": extras.get("budget_inr"),
        "critical_load_kw": extras.get("critical_load_kw"),
        "backup_hours": extras.get("backup_hours"),
        "load_profile": extras.get("load_profile") or "flat",
        "net_metering_mode": extras.get("net_metering_mode") or "full_offset",
        "feed_in_tariff": extras.get("feed_in_tariff"),
    }
    accepted_layout = extras.get("accepted_layout")
    if isinstance(accepted_layout, dict) and accepted_layout.get("status") == "accepted":
        module = accepted_layout.get("module") or {}
        inputs["accepted_panel_count"] = accepted_layout.get("panel_count")
        inputs["accepted_module_wattage"] = module.get("wattage_w")
        inputs["accepted_surfaces"] = accepted_layout.get("surface_generation_inputs")
    return inputs


def preview_orientation(location, orientation_deg):
    """Re-run only the deterministic solar pipeline for a new azimuth.

    This is intentionally a preview: no database fields are changed. The
    frontend can drag the compass freely and receive the same generation and
    financial calculations used by the saved estimate.
    """
    try:
        az = float(orientation_deg) % 360.0
    except (TypeError, ValueError) as exc:
        raise InvalidEstimateInputError("orientation must be a number from 0 to 359") from exc

    inputs = _estimate_inputs_from_location(location)
    inputs["orientation"] = az
    try:
        return run_full_estimation(**inputs)
    except Exception as exc:
        raise EstimationEngineError(str(exc)) from exc


def apply_orientation(location, orientation_deg):
    """Persist a new orientation on an existing estimate and refresh its cached outputs."""
    result = preview_orientation(location, orientation_deg)
    _apply_result_to_location(location, result)
    db.session.commit()
    return result


def _apply_result_to_location(location, result, extra_updates=None):
    """Apply one authoritative engine result without committing the transaction."""
    extras = dict(location.extras) if isinstance(location.extras, dict) else {}

    location.system_size = result["system_size"]
    location.annual_generation = result["annual_generation"]
    location.monthly_data = result["monthly_data"]
    location.roof_area_sqm = result["roof_area_sqm"]
    location.obstructed_area_sqm = result["obstructed_area_sqm"]
    location.usable_area_sqm = result["usable_area_sqm"]
    location.system_cost = result["system_cost"]
    location.subsidy_amount = result["subsidy_amount"]
    location.net_investment = result["net_investment"]
    location.monthly_savings = result["monthly_savings"]
    location.co2_reduction_tons = result["co2_reduction_tons"]
    location.irradiance_source = result["irradiance_source"]
    location.orientation_deg = result.get("orientation_deg")
    location.orientation_label = result.get("orientation_label")
    location.orientation_factor = result.get("orientation_factor")
    location.recommended_tilt_deg = result.get("recommended_tilt_deg")
    location.battery_kwh = result.get("battery_kwh", location.battery_kwh or 0)
    location.battery_cost = result.get("battery_cost", location.battery_cost or 0)
    location.specific_yield = result.get("specific_yield")
    location.capacity_factor = result.get("capacity_factor")
    location.lcoe = result.get("lcoe")
    location.lifetime_kwh = result.get("lifetime_kwh")
    location.self_consumption_frac = result.get("self_consumption_frac")
    location.payback_years = result.get("payback_years")
    location.suitability_rating = result.get("suitability_rating")

    extras.update({
        "planning_track": result.get("planning_track"),
        "property_type": result.get("property_type"),
        "calculation_policy": result.get("calculation_policy"),
        "orientation_input": (
            "auto" if (result.get("orientation_provenance") or {}).get("global_direction_source")
            == "footprint_longest_edge_heuristic" else result.get("orientation_deg")
        ),
        "orientation_source": result.get("orientation_source"),
        "irradiance_confidence": result.get("irradiance_confidence"),
        "cashflow_25yr": result.get("cashflow_25yr"),
        "annual_savings": result.get("annual_savings"),
        "payback_without_subsidy_years": result.get("payback_without_subsidy_years"),
        "subsidy_adjusted_payback_years": result.get("subsidy_adjusted_payback_years"),
        "annual_net_benefit": result.get("annual_net_benefit"),
        "annual_roi_pct": result.get("annual_roi_pct"),
        "lifetime_net_benefit": result.get("lifetime_net_benefit"),
        "demand_charge_savings": result.get("demand_charge_savings"),
        "year_one_net_savings": result.get("year_one_net_savings"),
        "self_consumed_kwh": result.get("self_consumed_kwh"),
        "exported_kwh": result.get("exported_kwh"),
        "export_value": result.get("export_value"),
        "environmental_equivalents": result.get("environmental_equivalents"),
        "inverter": result.get("inverter"),
        "inverter_type": result.get("inverter_type"),
        "bill_sizing": result.get("bill_sizing"),
        "roof_capacity_kw": result.get("roof_capacity_kw"),
        "sizing_method": result.get("sizing_method"),
        "sizing": result.get("sizing"),
        "battery_sizing": result.get("battery_sizing"),
        "planning_notes": result.get("planning_notes"),
        "system_cost_low": result.get("system_cost_low"),
        "system_cost_high": result.get("system_cost_high"),
        "annual_om": result.get("annual_om"),
        "discounted_payback_years": result.get("discounted_payback_years"),
        "npv": result.get("npv"),
        "discount_rate_pct": result.get("discount_rate_pct"),
        "lifecycle_projection": result.get("lifecycle_projection"),
        "financial_provenance": result.get("financial_provenance"),
        "performance_ratio": result.get("performance_ratio"),
        "peak_sun_hours_assumed": result.get("peak_sun_hours_assumed"),
        "daily_units_per_kw_range": result.get("daily_units_per_kw_range"),
        "dc_note": result.get("dc_note"),
        "cost_per_kw": result.get("cost_per_kw"),
        "simple_payback_years": result.get("simple_payback_years"),
        "nominal_payback_years": result.get("nominal_payback_years"),
        "tariff_escalation_pct": result.get("tariff_escalation_pct"),
        "emi": result.get("emi"),
        "financing_snapshot": result.get("financing_snapshot"),
        "financing_comparison": result.get("financing_comparison"),
        "generation_25yr": result.get("generation_25yr"),
        "sensitivity": result.get("sensitivity"),
        "panel_count": result.get("panel_count"),
        "panel_estimate": result.get("panel_estimate"),
        "generation_model": result.get("generation_model"),
        "generation_provenance": generation_provenance_for_result(result),
        "calculation_assumptions": calculation_assumptions(),
        "assumption_fingerprint": current_assumption_fingerprint(),
        "surface_generation": result.get("surface_generation"),
        "surface_generation_status": "current" if result.get("surface_generation") else None,
    })
    if extra_updates:
        extras.update(extra_updates)
    location.extras = extras
    commercial_calculation_service.sync_saved_financing(location, result)
    db.session.add(location)


def recalculate_for_accepted_layout(
    location, panel_count, module_wattage, metadata, surface_generation_inputs=None
):
    """Run the established engine at validated layout capacity; caller commits."""
    inputs = _estimate_inputs_from_location(location)
    inputs["orientation"] = (
        (location.extras or {}).get("orientation_input", "auto")
        if (location.extras or {}).get("orientation_source") == "auto-detect"
        or (location.extras or {}).get("orientation_input") == "auto"
        else location.orientation_deg if location.orientation_deg is not None else "auto"
    )
    inputs["accepted_panel_count"] = panel_count
    inputs["accepted_module_wattage"] = module_wattage
    inputs["accepted_surfaces"] = surface_generation_inputs
    try:
        result = run_full_estimation(**inputs)
    except Exception as exc:
        raise EstimationEngineError(str(exc)) from exc
    if isinstance(result.get("surface_generation"), dict):
        result["surface_generation"].update({
            "status": "current",
            "geometry_key": metadata.get("geometry_key"),
            "accepted_design_revision_id": metadata.get("accepted_design_revision_id"),
            "accepted_design_revision_number": metadata.get("accepted_design_revision_number"),
            "irradiance_source": result.get("irradiance_source"),
            "irradiance_confidence": result.get("irradiance_confidence"),
        })
    _apply_result_to_location(location, result, {"accepted_layout": metadata})
    return result

def _default_scenario_label(sizing_mode, has_battery):
    mode = (sizing_mode or "auto").replace("_", " ")
    label = mode
    if has_battery:
        label = f"{label} + battery"
    return label[:128]


def _polygon_from_location(location):
    geo = location.polygon_geojson or {}
    coords = geo.get("coordinates") or []
    if not coords:
        raise InvalidEstimateInputError("Source estimate has no rooftop polygon to clone")
    ring = coords[0] if isinstance(coords[0], list) else coords
    canonical = canonicalize_stored_ring(
        ring, location.latitude, location.longitude
    )
    return geojson_ring_to_latlng(canonical)


def _obstructions_from_location(location):
    geo = location.obstructions_geojson
    if not geo:
        return []
    coords = geo.get("coordinates") or []
    # Stored as MultiPolygon coordinates: [[[ring]], ...]
    out = []
    for poly in coords:
        if not poly:
            continue
        ring = poly[0] if poly and isinstance(poly[0][0], (list, tuple)) else poly
        canonical = canonicalize_stored_ring(
            ring, location.latitude, location.longitude
        )
        out.append(geojson_ring_to_latlng(canonical))
    return out


def clone_estimate_with_change(user_id, source_location_id, change, value, scenario_label=None):
    """Clone a saved estimate, change one input, re-run pipeline, link scenarios.

    change:
      - sizing_mode: value is mode string (roof_max, bill_offset_80%, ...)
      - needs_backup: value is bool / "true" / "false"
      - bill_offset: value is percent number (e.g. 80 → bill_offset_80%)
      - battery_kwh: value is kWh float (forces battery on)
    """
    source = UserLocation.query.filter_by(
        id=source_location_id, user_id=user_id
    ).first()
    if source is None:
        raise InvalidEstimateInputError("Source estimate not found")

    extras = source.extras if isinstance(source.extras, dict) else {}
    polygon = _polygon_from_location(source)
    obstructions = _obstructions_from_location(source)
    polygon, obstructions = _validate_estimate_geometry(polygon, obstructions)

    monthly_bill = extras.get("monthly_bill")
    tariff = extras.get("tariff_per_kwh") or Config.DEFAULT_TARIFF
    orientation = extras.get("orientation_input") or "auto"
    property_type = extras.get("property_type") or "residential"
    planning_track = extras.get("planning_track")
    if planning_track not in {"home", "business"}:
        planning_track = _legacy_planning_track(property_type)
    inverter_preference = extras.get("inverter_preference") or "auto"
    critical_load_kw = extras.get("critical_load_kw")
    backup_hours = extras.get("backup_hours")
    budget_inr = extras.get("budget_inr")
    load_profile = extras.get("load_profile") or "flat"
    net_metering_mode = extras.get("net_metering_mode") or "full_offset"
    feed_in_tariff = extras.get("feed_in_tariff")
    sizing_mode = source.sizing_mode or extras.get("sizing_method")
    needs_backup = bool(extras.get("needs_backup"))
    battery_kwh = float(source.battery_kwh or 0)

    change = (change or "").strip().lower()
    if change == "sizing_mode":
        sizing_mode = str(value or "").strip() or sizing_mode
    elif change == "bill_offset":
        try:
            pct = int(float(value))
        except (TypeError, ValueError) as exc:
            raise InvalidEstimateInputError("bill_offset value must be a number") from exc
        sizing_mode = f"bill_offset_{pct}%"
    elif change == "needs_backup":
        if isinstance(value, str):
            needs_backup = value.strip().lower() in ("1", "true", "yes", "on")
        else:
            needs_backup = bool(value)
        if needs_backup and battery_kwh <= 0:
            battery_kwh = 0  # auto-size in pipeline
        if not needs_backup:
            battery_kwh = 0
    elif change == "battery_kwh":
        try:
            battery_kwh = float(value)
        except (TypeError, ValueError) as exc:
            raise InvalidEstimateInputError("battery_kwh must be a number") from exc
        needs_backup = battery_kwh > 0 or needs_backup
    else:
        raise InvalidEstimateInputError(
            "change must be one of: sizing_mode, bill_offset, needs_backup, battery_kwh"
        )

    group_id = source.roof_group_id or str(uuid.uuid4())
    # Backfill the source and create the clone in one transaction. A failed
    # recalculation must not mutate an otherwise valid saved estimate.
    if not source.roof_group_id:
        source.roof_group_id = group_id
        db.session.add(source)

    if not scenario_label:
        scenario_label = _default_scenario_label(sizing_mode, needs_backup or battery_kwh > 0)

    try:
        result, location = create_estimate(
            user_id=user_id,
            address=source.address,
            latitude=source.latitude,
            longitude=source.longitude,
            polygon=polygon,
            obstructions=obstructions,
            tariff_per_kwh=tariff,
            orientation=orientation,
            battery_kwh=battery_kwh,
            monthly_bill=monthly_bill,
            property_type=property_type,
            planning_track=planning_track,
            user_priority=source.user_priority or "no_preference",
            needs_backup=needs_backup,
            inverter_preference=inverter_preference,
            sizing_mode=sizing_mode,
            budget_inr=budget_inr,
            critical_load_kw=critical_load_kw,
            backup_hours=backup_hours,
            load_profile=load_profile,
            net_metering_mode=net_metering_mode,
            feed_in_tariff=feed_in_tariff,
            roof_group_id=group_id,
            parent_location_id=source.id,
            scenario_label=scenario_label,
            commit=False,
        )
        if planning_track == "business" and "loan_rate_pct" in extras:
            location.extras = {**location.extras, **commercial_calculation_service.validate_finance_preferences(extras)}
            commercial_calculation_service.sync_saved_financing(location, result)
        db.session.commit()
    except (InvalidEstimateInputError, EstimationEngineError, EstimatePersistenceError):
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        raise EstimatePersistenceError(str(exc)) from exc
    return result, location, source
