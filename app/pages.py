from flask import (
    Blueprint,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_babel import get_locale, gettext as _
from flask_login import current_user, login_required

from app.report_generator import generate_pdf_report
from app.installer_export import build_installer_package_zip
from app.services import location_service
from app.solar_logic import calculate_environmental_equivalents
from app.forms import INDIAN_STATES

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def index():
    return render_template("landing.html")


@pages_bp.get("/consultation")
def consultation():
    return render_template("consultation.html")


PLANNING_TRACKS = {
    "home": {
        "property_type": "residential",
        "title": "Solar for Home",
    },
    "business": {
        "property_type": "commercial",
        "title": "Solar for Business",
    },
}


def _planning_track_or_404(planning_track):
    track = PLANNING_TRACKS.get(planning_track)
    if track is None:
        abort(404)
    return track


@pages_bp.route("/plan/<planning_track>")
def plan(planning_track):
    track = _planning_track_or_404(planning_track)
    return render_template(
        "plan.html",
        planning_track=planning_track,
        track=track,
    )


@pages_bp.route("/survey/<planning_track>")
@login_required
def survey(planning_track):
    track = _planning_track_or_404(planning_track)
    default_property_type = track["property_type"]
    if (
        planning_track == "home"
        and current_user.default_property_type in ("residential", "remote")
    ):
        default_property_type = current_user.default_property_type
    return render_template(
        "index.html",
        planning_track=planning_track,
        track=track,
        default_tariff_per_kwh=current_user.default_tariff_per_kwh,
        default_state=current_user.default_state,
        # A saved off-grid/remote household preference remains part of the
        # home workflow. Cross-track preferences never switch a home survey
        # into a commercial one (or vice versa).
        default_property_type=default_property_type,
        state_choices=INDIAN_STATES,
    )


@pages_bp.route("/dashboard")
@login_required
def dashboard():
    locations = location_service.list_locations(current_user.id)
    summary = {
        "saved_locations": len(locations),
        "total_capacity_kw": sum(location.system_size or 0 for location in locations),
        "annual_generation_kwh": sum(
            location.annual_generation or 0 for location in locations
        ),
        "monthly_savings": sum(location.monthly_savings or 0 for location in locations),
    }
    return render_template(
        "dashboard.html", locations=locations[:6], summary=summary
    )


@pages_bp.get("/properties")
@login_required
def properties():
    locations = location_service.list_locations(current_user.id)
    return render_template("properties.html", locations=locations)


@pages_bp.get("/reports")
def reports():
    return redirect(url_for("pages.properties"))


@pages_bp.get("/saved-locations")
def saved_locations():
    return redirect(url_for("pages.properties"))


@pages_bp.route("/compare", methods=["GET", "POST"])
@login_required
def compare():
    locations = location_service.list_locations(current_user.id)
    selected_locations = []
    selected_ids = []

    if request.method == "POST":
        try:
            selected_ids = [
                int(location_id) for location_id in request.form.getlist("location_ids")
            ]
        except ValueError:
            selected_ids = []
    else:
        # Deep-link from clone flow: /compare?ids=12,34,56
        raw = (request.args.get("ids") or "").strip()
        if raw:
            try:
                selected_ids = [int(part) for part in raw.split(",") if part.strip()]
            except ValueError:
                selected_ids = []
        group = (request.args.get("group") or "").strip()
        if group and not selected_ids:
            selected_ids = [
                loc.id
                for loc in location_service.list_group_scenarios(
                    current_user.id, group
                )
            ]

    # Prevent repeated IDs from rendering duplicate scenario columns.
    selected_ids = list(dict.fromkeys(selected_ids))[:3]

    if selected_ids:
        selected_locations = location_service.list_locations_by_ids(
            current_user.id, selected_ids
        )

    comparison = None
    if len(selected_locations) >= 2:
        comparison = _build_scenario_comparison(selected_locations)

    return render_template(
        "compare.html",
        locations=locations,
        selected_locations=selected_locations,
        comparison=comparison,
    )




@pages_bp.route("/savings")
@login_required
def savings():
    locations = location_service.list_locations(current_user.id)
    monthly_savings = sum(location.monthly_savings or 0 for location in locations)
    annual_savings = monthly_savings * 12
    co2_reduction_tons = sum(location.co2_reduction_tons or 0 for location in locations)
    environmental_equivalents = calculate_environmental_equivalents(co2_reduction_tons)
    best_opportunity = max(
        locations,
        key=lambda location: (
            location.monthly_savings or 0,
            -(location.payback_years or float("inf")),
        ),
        default=None,
    )

    return render_template(
        "savings.html",
        locations=locations,
        monthly_savings=monthly_savings,
        annual_savings=annual_savings,
        co2_reduction_tons=co2_reduction_tons,
        environmental_equivalents=environmental_equivalents,
        best_opportunity=best_opportunity,
    )


@pages_bp.route("/report/<int:location_id>")
@login_required
def report(location_id):
    report_data = location_service.get_report_context(location_id, current_user.id)
    if report_data is None:
        abort(404)
    _, loc_dict = report_data
    return render_template("report.html", loc=loc_dict)


@pages_bp.route("/report/<int:location_id>/pdf")
@login_required
def download_pdf(location_id):
    location = location_service.get_location(location_id, current_user.id)
    if location is None:
        abort(404)
    mode = (request.args.get("mode") or "full").strip().lower()
    if mode not in ("full", "summary"):
        mode = "full"
    try:
        pdf_buffer = generate_pdf_report(
            location, locale=str(get_locale()), mode=mode
        )
    except Exception as exc:
        return (
            jsonify(
                {
                    "error": (
                        "PDF generation failed. Install reportlab (`pip install reportlab`) "
                        "or system packages for WeasyPrint. "
                        f"Details: {exc}"
                    )
                }
            ),
            500,
        )

    if mode == "summary":
        download_name = f"{_('Solar Summary')}_{location_id}.pdf"
    else:
        download_name = f"{_('Solar Report')}_{location_id}.pdf"

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=download_name,
        max_age=0,
    )


def _scenario_label(location, index):
    if location.scenario_label:
        return location.scenario_label
    return f"Scenario {chr(ord('A') + index)}"


def _build_scenario_comparison(locations):
    """Derive per-metric winners and a short recommendation sentence."""
    rows = []
    for index, loc in enumerate(locations):
        monthly = float(loc.monthly_savings or 0)
        # Prefer escalated/degraded cashflow lifetime savings when present
        extras = loc.extras if isinstance(getattr(loc, "extras", None), dict) else {}
        cashflow = extras.get("cashflow_25yr") if extras else None
        if not cashflow and isinstance(getattr(loc, "to_dict", None), type(None)):
            cashflow = None
        if isinstance(cashflow, list) and cashflow:
            # cashflow is cumulative from -net_investment; total bill savings ≈ last + net
            net = float(loc.net_investment or 0)
            savings_25 = float(cashflow[-1]) + net
        else:
            savings_25 = monthly * 12 * 25
        rating = loc.suitability_rating if isinstance(loc.suitability_rating, dict) else {}
        # Current schema: overall_viability.score; keep legacy fallbacks
        score = None
        ov = rating.get("overall_viability")
        if isinstance(ov, dict):
            score = ov.get("score")
        if score is None:
            score = rating.get("overall_score")
        if score is None and isinstance(rating.get("viability"), dict):
            score = rating["viability"].get("score")
        try:
            score = float(score) if score is not None else None
        except (TypeError, ValueError):
            score = None
        rows.append(
            {
                "id": loc.id,
                "index": index,
                "label": _scenario_label(loc, index),
                "system_size": float(loc.system_size or 0),
                "net_investment": float(loc.net_investment or 0),
                "savings_25yr": savings_25,
                "payback_years": (
                    float(loc.payback_years)
                    if loc.payback_years is not None
                    else None
                ),
                "suitability_score": score,
                "priority": (loc.user_priority or "no_preference"),
            }
        )

    def best_ids(key, higher_is_better):
        values = []
        for row in rows:
            val = row.get(key)
            if val is None:
                continue
            values.append((row["id"], val))
        if not values:
            return set()
        target = max(v for _, v in values) if higher_is_better else min(
            v for _, v in values
        )
        # Treat near-ties as joint winners
        winners = set()
        for loc_id, val in values:
            if higher_is_better:
                if abs(val - target) <= max(1e-6, abs(target) * 1e-9):
                    winners.add(loc_id)
            else:
                if abs(val - target) <= max(1e-6, abs(target) * 1e-9):
                    winners.add(loc_id)
        # Only highlight if there is a real spread
        spread = max(v for _, v in values) - min(v for _, v in values)
        if spread <= max(1e-6, abs(target) * 1e-9):
            return set()
        return winners

    winners = {
        "system_size": best_ids("system_size", higher_is_better=True),
        "net_investment": best_ids("net_investment", higher_is_better=False),
        "savings_25yr": best_ids("savings_25yr", higher_is_better=True),
        "payback_years": best_ids("payback_years", higher_is_better=False),
        "suitability_score": best_ids("suitability_score", higher_is_better=True),
    }

    # Priority for recommendation: first scenario's stated priority, else no_preference
    priority = rows[0]["priority"] or "no_preference"

    by_id = {row["id"]: row for row in rows}

    def pick(metric, higher):
        ids = winners.get(metric) or set()
        if not ids:
            # fall back to absolute best even if all equal
            vals = [
                (row["id"], row[metric])
                for row in rows
                if row.get(metric) is not None
            ]
            if not vals:
                return None
            target = max(v for _, v in vals) if higher else min(v for _, v in vals)
            for loc_id, val in vals:
                if abs(val - target) <= 1e-6:
                    return by_id[loc_id]
            return by_id[vals[0][0]]
        return by_id[next(iter(ids))]

    best_payback = pick("payback_years", higher=False)
    best_savings = pick("savings_25yr", higher=True)
    best_invest = pick("net_investment", higher=False)

    recommendation = None
    if best_payback and best_savings:
        if best_payback["id"] == best_savings["id"]:
            recommendation = (
                f"{best_payback['label']} leads on both fastest payback "
                f"({best_payback['payback_years']:.1f} years) and 25-year savings "
                f"(₹{best_savings['savings_25yr']:,.0f})."
            )
        else:
            delta = abs(
                best_savings["savings_25yr"] - best_payback["savings_25yr"]
            )
            recommendation = (
                f"{best_payback['label']} offers the fastest payback "
                f"({best_payback['payback_years']:.1f} years) but "
                f"{best_savings['label']} delivers ₹{delta:,.0f} more in 25-year savings."
            )

        # Nudge wording toward user's stated priority when it is clear
        if priority == "fastest_payback" and best_payback:
            recommendation = (
                f"For fastest payback, prefer {best_payback['label']} "
                f"({best_payback['payback_years']:.1f} years)"
            )
            if best_savings and best_savings["id"] != best_payback["id"]:
                recommendation += (
                    f"; {best_savings['label']} still adds ₹"
                    f"{abs(best_savings['savings_25yr'] - best_payback['savings_25yr']):,.0f} "
                    f"more over 25 years."
                )
            else:
                recommendation += "."
        elif priority == "maximum_savings" and best_savings:
            recommendation = (
                f"For maximum long-term savings, prefer {best_savings['label']} "
                f"(≈ ₹{best_savings['savings_25yr']:,.0f} over 25 years)"
            )
            if best_payback and best_payback["id"] != best_savings["id"]:
                recommendation += (
                    f"; {best_payback['label']} pays back sooner "
                    f"({best_payback['payback_years']:.1f} years)."
                )
            else:
                recommendation += "."
        elif priority == "backup_power":
            # Prefer a scenario that actually has battery if detectable via label/mode
            batt = next(
                (
                    row
                    for row in rows
                    if "battery" in (row["label"] or "").lower()
                ),
                None,
            )
            if batt:
                recommendation = (
                    f"For backup priority, {batt['label']} includes storage; "
                    f"compare payback ({batt['payback_years']:.1f} years if known) "
                    f"against pure bill-savings options."
                )

    return {
        "rows": rows,
        "winners": {key: list(ids) for key, ids in winners.items()},
        "recommendation": recommendation,
        "priority": priority,
    }


@pages_bp.route("/report/<int:location_id>/installer-package")
@login_required
def download_installer_package(location_id):
    """Vendor-neutral zip: GeoJSON + metrics JSON + homeowner PDF summary."""
    location = location_service.get_location(location_id, current_user.id)
    if location is None:
        abort(404)
    try:
        pdf_buffer = generate_pdf_report(
            location, locale=str(get_locale()), mode="summary"
        )
        pdf_bytes = pdf_buffer.read()
        package = build_installer_package_zip(location, pdf_bytes)
    except Exception as exc:
        return (
            jsonify(
                {
                    "error": (
                        "Installer package failed to build. "
                        f"Details: {exc}"
                    )
                }
            ),
            500,
        )
    return send_file(
        package,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"SIGNIX_installer_package_{location_id}.zip",
        max_age=0,
    )
