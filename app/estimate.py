
def _json_safe(obj):
    """Recursively make a structure JSON-safe (no NaN/Inf/datetime surprises)."""
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    # numpy scalars etc.
    try:
        if hasattr(obj, "item"):
            return _json_safe(obj.item())
    except Exception:
        pass
    try:
        return float(obj)
    except Exception:
        return str(obj)


from flask import Blueprint, jsonify, request, session
import math
from datetime import date, datetime
from flask_login import current_user, login_required

from app.assumptions import (
    MONETARY_DISCLAIMER,
    SUBSIDY_CAVEAT,
    SUBSIDY_ELIGIBILITY_ASSUMPTION,
)
from app.services import estimation_service

estimate_bp = Blueprint("estimate", __name__)


@estimate_bp.route("/api/estimate", methods=["POST"])
@login_required
def api_estimate():
    payload = request.get_json(silent=True) or {}

    try:
        estimate_inputs = estimation_service.prepare_estimate_inputs(
            payload, planning_track=session.get("planning_track", "home")
        )
    except estimation_service.InvalidEstimateInputError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        result, location = estimation_service.create_estimate(
            user_id=current_user.id,
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
    response["disclaimer"] = MONETARY_DISCLAIMER
    response["subsidy_caveat"] = SUBSIDY_CAVEAT
    response["subsidy_eligibility_assumption"] = SUBSIDY_ELIGIBILITY_ASSUMPTION
    try:
        return jsonify(_json_safe(response))
    except Exception as exc:
        # Last-resort: never return a non-JSON 200 to the browser
        return jsonify({"error": f"Could not serialize estimate response: {exc}"}), 500


@estimate_bp.route("/api/estimate/<int:location_id>/orientation-preview", methods=["POST"])
@login_required
def api_orientation_preview(location_id):
    """Preview generation/financial impact while the report compass is dragged."""
    location = estimation_service.UserLocation.query.filter_by(
        id=location_id, user_id=current_user.id
    ).first()
    if location is None:
        return jsonify({"error": "Estimate not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        result = estimation_service.preview_orientation(
            location, payload.get("orientation")
        )
    except estimation_service.InvalidEstimateInputError as exc:
        return jsonify({"error": str(exc)}), 400
    except estimation_service.EstimationEngineError as exc:
        return jsonify({"error": f"Orientation preview failed: {exc}"}), 500

    from app.services.report_chart_data import build_report_chart_data

    return jsonify(_json_safe({
        "orientation_deg": result.get("orientation_deg"),
        "orientation_label": result.get("orientation_label"),
        "orientation_factor": result.get("orientation_factor"),
        "annual_generation": result.get("annual_generation"),
        "monthly_savings": result.get("monthly_savings"),
        "payback_years": result.get("payback_years"),
        "monthly_data": result.get("monthly_data"),
        "chart_data": build_report_chart_data(result),
    }))


@estimate_bp.route("/api/estimate/<int:location_id>/orientation", methods=["POST"])
@login_required
def api_apply_orientation(location_id):
    """Persist the orientation selected with the report compass."""
    location = estimation_service.UserLocation.query.filter_by(
        id=location_id, user_id=current_user.id
    ).first()
    if location is None:
        return jsonify({"error": "Estimate not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        result = estimation_service.apply_orientation(
            location, payload.get("orientation")
        )
    except estimation_service.InvalidEstimateInputError as exc:
        return jsonify({"error": str(exc)}), 400
    except estimation_service.EstimationEngineError as exc:
        return jsonify({"error": f"Could not apply orientation: {exc}"}), 500
    except Exception as exc:
        estimation_service.db.session.rollback()
        return jsonify({"error": f"Could not save orientation: {exc}"}), 500

    from app.services.report_chart_data import build_report_chart_data

    return jsonify(_json_safe({
        "orientation_deg": result.get("orientation_deg"),
        "orientation_label": result.get("orientation_label"),
        "orientation_factor": result.get("orientation_factor"),
        "annual_generation": result.get("annual_generation"),
        "monthly_savings": result.get("monthly_savings"),
        "payback_years": result.get("payback_years"),
        "chart_data": build_report_chart_data(result),
    }))


def _finite_number(payload, name, minimum, maximum, *, integer=False):
    value = payload.get(name)
    if isinstance(value, bool):
        raise estimation_service.InvalidEstimateInputError(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise estimation_service.InvalidEstimateInputError(f"{name} must be a number")
    if not math.isfinite(number):
        raise estimation_service.InvalidEstimateInputError(f"{name} must be finite")
    if number < minimum or number > maximum:
        raise estimation_service.InvalidEstimateInputError(
            f"{name} must be between {minimum:g} and {maximum:g}"
        )
    if integer and not number.is_integer():
        raise estimation_service.InvalidEstimateInputError(f"{name} must be a whole number")
    return int(number) if integer else number


def _persisted_lifecycle_cashflows(context, years=25):
    """Return the saved annual operating cash flows without re-modelling them.

    Current estimates persist the authoritative lifecycle projection. A
    financing preview may change loan terms, but must not replace that saved
    series with a new financial model or infer one for a legacy estimate.
    """

    lifecycle = context.get("lifecycle_projection")
    rows = lifecycle.get("years") if isinstance(lifecycle, dict) else None
    if not isinstance(rows, list) or len(rows) != years:
        return None
    candidates = [
        row.get("net_cash_flow") if isinstance(row, dict) else None
        for row in rows
    ]
    try:
        cashflows = [float(value) for value in candidates]
    except (TypeError, ValueError):
        return None
    return cashflows if all(math.isfinite(value) for value in cashflows) else None


@estimate_bp.route("/api/estimate/<int:location_id>/financing-preview", methods=["POST"])
@login_required
def api_financing_preview(location_id):
    """Recalculate a report's DPA comparison without persisting assumptions."""
    from app.services import location_service
    from app.services.report_chart_data import build_report_chart_data
    from app.solar_logic import build_financing_comparison

    report = location_service.get_report_context(location_id, current_user.id)
    if report is None:
        return jsonify({"error": "Estimate not found"}), 404
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    try:
        down_payment_pct = _finite_number(payload, "down_payment_pct", 0, 100)
        dpa_rate_pct = _finite_number(payload, "dpa_rate_pct", 0, 50)
        dpa_tenure_years = _finite_number(
            payload, "dpa_tenure_years", 1, 30, integer=True
        )
    except estimation_service.InvalidEstimateInputError as exc:
        return jsonify({"error": str(exc)}), 400

    _, context = report
    if context.get("financial_status") != "current":
        return jsonify({
            "error": (
                "Financing preview is unavailable because this estimate's "
                "financial model is legacy or stale. Re-run the estimate first."
            )
        }), 409
    annual_net_cashflows = _persisted_lifecycle_cashflows(context)
    if annual_net_cashflows is None:
        return jsonify({
            "error": (
                "Financing preview is unavailable because this estimate has no "
                "valid saved 25-year lifecycle cash-flow series."
            )
        }), 409
    annual_savings = context.get("annual_savings")
    if annual_savings is None:
        annual_savings = float(context.get("monthly_savings") or 0) * 12
    comparison = build_financing_comparison(
        net_investment=context.get("net_investment"),
        annual_savings=annual_savings,
        monthly_savings=context.get("monthly_savings"),
        annual_generation_kwh=context.get("annual_generation"),
        down_payment_pct=down_payment_pct,
        dpa_tenure_years=dpa_tenure_years,
        dpa_rate_pct=dpa_rate_pct,
        annual_net_cashflows=annual_net_cashflows,
        annual_om=context.get("annual_om") or 0,
    )
    preview_context = dict(context)
    preview_context["financing_comparison"] = comparison
    return jsonify(_json_safe({
        "financing_comparison": comparison,
        "chart_data": build_report_chart_data(preview_context),
    }))


@estimate_bp.route("/api/tariff", methods=["GET"])
@login_required
def api_tariff_defaults():
    """Return tariff defaults for the active server-side planning track."""
    state = (request.args.get("state") or "").strip()
    info = estimation_service.lookup_tariff_defaults(
        state or None, planning_track=session.get("planning_track", "home")
    )
    return jsonify(info)


@estimate_bp.route("/api/location-context", methods=["GET"])
@login_required
def api_location_context():
    """Best-effort DISCOM + scheme + tariff defaults for a place."""
    from app.discom_lookup import lookup_location_context

    def _f(name):
        raw = request.args.get(name)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    ctx = lookup_location_context(
        state=(request.args.get("state") or "").strip() or None,
        city=(request.args.get("city") or "").strip() or None,
        address=(request.args.get("address") or "").strip() or None,
        pincode=(request.args.get("pincode") or "").strip() or None,
        latitude=_f("lat"),
        longitude=_f("lng"),
        planning_track=session.get("planning_track", "home"),
    )
    return jsonify(ctx)
