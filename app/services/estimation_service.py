import time

from app.config import Config
from app.models import db, UserLocation
from app.solar_logic import run_full_estimation


class EstimationEngineError(Exception):
    pass


class EstimatePersistenceError(Exception):
    def __init__(self, message, schema_outdated=False):
        super().__init__(message)
        self.schema_outdated = schema_outdated


def create_estimate(
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
    needs_backup=False,
    inverter_preference="auto",
):
    total_start = time.perf_counter()
    try:
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
            needs_backup=needs_backup,
            inverter_preference=inverter_preference,
        )
    except Exception as exc:
        raise EstimationEngineError(str(exc)) from exc

    try:
        location = UserLocation(
            address=address,
            latitude=latitude,
            longitude=longitude,
            polygon_geojson={"type": "Polygon", "coordinates": [polygon]},
            obstructions_geojson=(
                {"type": "MultiPolygon", "coordinates": [[o] for o in obstructions]}
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
            extras={
                "orientation_source": result.get("orientation_source"),
                "cashflow_25yr": result.get("cashflow_25yr"),
                "environmental_equivalents": result.get("environmental_equivalents"),
                "inverter": result.get("inverter"),
                "inverter_type": result.get("inverter_type"),
                "bill_sizing": result.get("bill_sizing"),
                "roof_capacity_kw": result.get("roof_capacity_kw"),
                "sizing_method": result.get("sizing_method"),
                "rule_of_thumb_monthly_units": result.get(
                    "rule_of_thumb_monthly_units"
                ),
                "rule_of_thumb_annual_units": result.get("rule_of_thumb_annual_units"),
                "property_type": result.get("property_type"),
                "needs_backup": result.get("needs_backup"),
                "planning_notes": result.get("planning_notes"),
                "system_cost_low": result.get("system_cost_low"),
                "system_cost_high": result.get("system_cost_high"),
                "annual_om": result.get("annual_om"),
                "performance_ratio": result.get("performance_ratio"),
                "peak_sun_hours_assumed": result.get("peak_sun_hours_assumed"),
                "daily_units_per_kw_range": result.get("daily_units_per_kw_range"),
                "dc_note": result.get("dc_note"),
                "cost_per_kw": result.get("cost_per_kw"),
            },
        )
        db_save_start = time.perf_counter()
        db.session.add(location)
        db.session.commit()
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
