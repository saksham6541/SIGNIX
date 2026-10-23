from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

from app.services import location_service

locations_bp = Blueprint("locations", __name__)


@locations_bp.route("/api/geocode")
def api_geocode():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400
    return jsonify(location_service.geocode(query))


@locations_bp.route("/api/reverse-geocode")
def api_reverse_geocode():
    latitude = request.args.get("lat", type=float)
    longitude = request.args.get("lon", type=float)
    if latitude is None or longitude is None:
        return jsonify({"error": "Missing lat/lon"}), 400
    address = location_service.reverse_address(latitude, longitude)
    if address is not None:
        return jsonify(
            {
                "address": address,
                "source": "nominatim",
                "lat": latitude,
                "lon": longitude,
            }
        )
    return jsonify(
        {
            "address": "Current location (address lookup unavailable)",
            "source": "mock_fallback",
            "lat": latitude,
            "lon": longitude,
        }
    )


@locations_bp.route("/api/parse-maps-url")
def api_parse_maps_url():
    data, status = location_service.parse_maps_url(
        (request.args.get("url") or "").strip()
    )
    return jsonify(data), status


@locations_bp.route("/api/locations")
@login_required
def api_locations():
    return jsonify(
        [
            location.to_dict()
            for location in location_service.list_locations(current_user.id)
        ]
    )


@locations_bp.route("/api/locations/<int:location_id>")
@login_required
def api_location_detail(location_id):
    location = location_service.get_location(location_id, current_user.id)
    if location is None:
        abort(404)
    return jsonify(location.to_dict())


@locations_bp.route("/api/locations/<int:location_id>/scenarios", methods=["POST"])
@login_required
def api_clone_scenario(location_id):
    """Clone a saved estimate, change one variable, re-run, return both for compare.

    JSON body:
      change: sizing_mode | bill_offset | needs_backup | battery_kwh
      value:  mode string | percent | bool | kWh
      scenario_label: optional display name
    """
    from app.services import estimation_service

    payload = request.get_json(silent=True) or {}
    change = payload.get("change")
    value = payload.get("value")
    scenario_label = payload.get("scenario_label")

    try:
        result, location, source = estimation_service.clone_estimate_with_change(
            user_id=current_user.id,
            source_location_id=location_id,
            change=change,
            value=value,
            scenario_label=scenario_label,
        )
    except estimation_service.InvalidEstimateInputError as exc:
        return jsonify({"error": str(exc)}), 400
    except estimation_service.EstimationEngineError as exc:
        return jsonify({"error": f"Estimation engine failed: {exc}"}), 500
    except estimation_service.EstimatePersistenceError as exc:
        return jsonify({"error": f"Could not save scenario: {exc}"}), 500

    ids = [source.id, location.id]
    response = result.copy()
    response["location_id"] = location.id
    response["parent_location_id"] = source.id
    response["roof_group_id"] = location.roof_group_id
    response["scenario_label"] = location.scenario_label
    response["compare_url"] = f"/compare?ids={source.id},{location.id}"
    response["redirect_url"] = response["compare_url"]
    return jsonify(response)
