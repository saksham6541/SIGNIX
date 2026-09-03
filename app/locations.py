from flask import Blueprint, abort, jsonify, request

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
def api_locations():
    return jsonify([location.to_dict() for location in location_service.list_locations()])


@locations_bp.route("/api/locations/<int:location_id>")
def api_location_detail(location_id):
    location = location_service.get_location(location_id)
    if location is None:
        abort(404)
    return jsonify(location.to_dict())
