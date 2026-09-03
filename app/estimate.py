from flask import Blueprint, jsonify, request

from app.services import estimation_service

estimate_bp = Blueprint("estimate", __name__)


@estimate_bp.route("/api/estimate", methods=["POST"])
def api_estimate():
    payload = request.get_json(silent=True) or {}

    try:
        estimate_inputs = estimation_service.prepare_estimate_inputs(payload)
    except estimation_service.InvalidEstimateInputError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        result, location = estimation_service.create_estimate(
            **estimate_inputs,
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
