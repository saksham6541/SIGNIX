from flask import Blueprint, jsonify, request

from app.config import Config
from app.services import estimation_service, location_service

estimate_bp = Blueprint("estimate", __name__)


@estimate_bp.route("/api/estimate", methods=["POST"])
def api_estimate():
    payload = request.get_json(silent=True) or {}

    address = payload.get("address", "Unknown address")
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    polygon = payload.get("polygon", [])
    obstructions = payload.get("obstructions", [])
    state = payload.get("state")
    orientation = payload.get("orientation", "auto")
    battery_kwh = float(payload.get("battery_kwh") or 0)
    monthly_bill = payload.get("monthly_bill")
    if monthly_bill is not None and monthly_bill != "":
        try:
            monthly_bill = float(monthly_bill)
        except (TypeError, ValueError):
            monthly_bill = None
    else:
        monthly_bill = None
    property_type = payload.get("property_type") or "residential"
    needs_backup = bool(payload.get("needs_backup"))
    inverter_preference = payload.get("inverter_preference") or "auto"

    if latitude is None or longitude is None:
        return jsonify({"error": "latitude and longitude are required"}), 400
    if not polygon or len(polygon) < 3:
        return (
            jsonify({"error": "A rooftop polygon with at least 3 points is required"}),
            400,
        )

    tariff = location_service.lookup_tariff(state)
    if payload.get("tariff_per_kwh"):
        try:
            tariff = float(payload["tariff_per_kwh"])
        except (TypeError, ValueError):
            pass

    try:
        result, location = estimation_service.create_estimate(
            address=address,
            latitude=latitude,
            longitude=longitude,
            polygon=polygon,
            obstructions=obstructions,
            tariff_per_kwh=tariff,
            orientation=orientation,
            battery_kwh=battery_kwh,
            monthly_bill=monthly_bill,
            property_type=property_type,
            needs_backup=needs_backup,
            inverter_preference=inverter_preference,
        )
    except estimation_service.EstimationEngineError as exc:
        return jsonify({"error": f"Estimation engine failed: {exc}"}), 500
    except estimation_service.EstimatePersistenceError as exc:
        if exc.schema_outdated:
            return (
                jsonify(
                    {
                        "error": (
                            "Database schema is outdated (new orientation/battery fields). "
                            "Stop the server, delete the file solar_app.db next to run.py, "
                            "then start again with: python run.py"
                        )
                    }
                ),
                500,
            )
        return jsonify({"error": f"Could not save estimate: {exc}"}), 500

    response = result.copy()
    response["location_id"] = location.id
    response["redirect_url"] = f"/report/{location.id}"
    return jsonify(response)
