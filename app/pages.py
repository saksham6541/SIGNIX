import re
import json
import math
from datetime import date, datetime, timezone

from flask import (
    current_app,
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_babel import get_locale, gettext as _
from flask_login import current_user, login_required

from app.application_checklist import build_pm_surya_ghar_guidance
from app.config import Config
from app.models import (
    ApplicationTrackerEvent,
    PROJECT_STATUS_CHOICES,
    Project,
    UserLocation,
    VendorLeadUnlock,
    db,
)
from app.report_generator import generate_pdf_report
from app.installer_export import build_installer_package_zip
from app.services import location_service
from app.services.commercial_calculation_service import project_for_location, validate_finance_preferences
from app.services import estimation_service
from sqlalchemy.exc import SQLAlchemyError
from app.solar_logic import calculate_environmental_equivalents
from app.forms import INDIAN_STATES

pages_bp = Blueprint("pages", __name__)

APPLICATION_TRACKER_STATUS_CODES = frozenset(
    {
        "not_started",
        "applied",
        "feasibility_approved",
        "installed",
        "net_meter_inspection",
        "subsidy_received",
    }
)


def _tracker_status_labels():
    return {
        "not_started": _("Not started"),
        "applied": _("Applied"),
        "feasibility_approved": _("Feasibility approved"),
        "installed": _("Installed"),
        "net_meter_inspection": _("Net meter and inspection"),
        "subsidy_received": _("Subsidy received"),
    }




def _psg_guidance_allowed(location):
    if location.get("planning_track") == "business":
        return False
    if (location.get("property_type") or "").strip().lower() != "residential":
        return False
    inverter_type = (location.get("inverter_type") or "").strip().lower().replace(" ", "-")
    return inverter_type in ("on-grid", "hybrid") or float(
        location.get("subsidy_amount") or 0
    ) > 0


def _saved_location_psg_allowed(location):
    extras = location.extras if isinstance(location.extras, dict) else {}
    saved_values = {
        "property_type": extras.get("property_type"),
        "inverter_type": extras.get("inverter_type"),
        "subsidy_amount": location.subsidy_amount,
    }
    return _psg_guidance_allowed(saved_values)


def _latest_tracker_events(location_ids):
    if not location_ids:
        return {}
    events = (
        ApplicationTrackerEvent.query.filter(
            ApplicationTrackerEvent.location_id.in_(location_ids)
        )
        .order_by(
            ApplicationTrackerEvent.created_at.desc(),
            ApplicationTrackerEvent.id.desc(),
        )
        .all()
    )
    latest = {}
    for event in events:
        latest.setdefault(event.location_id, event)
    return latest


def _psg_rule_context():
    return {
        "per_kw_first_2": Config.SUBSIDY_PER_KW_FIRST_2,
        "per_kw_extra_2_to_3": Config.SUBSIDY_EXTRA_2_TO_3,
        "cap": Config.SUBSIDY_CAP,
    }


def _project_is_business(location):
    if location is None or location.user_id != current_user.id:
        return False
    extras = location.extras if isinstance(location.extras, dict) else {}
    track = str(extras.get("planning_track") or "").strip().lower()
    if track == "business":
        return True
    property_type = str(extras.get("property_type") or "").strip().lower()
    return property_type in {"commercial", "industrial", "msme", "factory"}


def _require_business_workspace_role():
    if current_user.role not in {"user", "admin"}:
        abort(403)


@pages_bp.route("/")
def index():
    return render_template(
        "landing.html", psg_guidance=build_pm_surya_ghar_guidance()
    )


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


def _active_planning_track():
    return "business" if session.get("planning_track") == "business" else "home"


def _location_planning_track(location):
    extras = location.extras if isinstance(location.extras, dict) else {}
    saved_track = extras.get("planning_track")
    if saved_track in PLANNING_TRACKS:
        return saved_track
    return (
        "business"
        if str(extras.get("property_type") or "").strip().lower()
        in {"commercial", "industrial", "msme", "factory"}
        else "home"
    )


def _locations_for_track(user_id, planning_track):
    return [
        location
        for location in location_service.list_locations(user_id)
        if _location_planning_track(location) == planning_track
    ]


@pages_bp.route("/plan/<planning_track>")
def plan(planning_track):
    _planning_track_or_404(planning_track)
    return redirect(
        url_for(
            "auth.login",
            next=url_for("pages.survey", planning_track=planning_track),
        )
    )


@pages_bp.route("/survey/<planning_track>")
@login_required
def survey(planning_track, template_name="index.html"):
    track = _planning_track_or_404(planning_track)
    # The authenticated survey route is the authoritative track selector.
    # Estimation APIs use this server-side value rather than trusting a
    # client-controlled property-type field.
    session["planning_track"] = planning_track
    default_property_type = track["property_type"]
    if (
        planning_track == "home"
        and current_user.default_property_type in ("residential", "remote")
    ):
        default_property_type = current_user.default_property_type
    return render_template(
        template_name,
        planning_track=planning_track,
        track=track,
        default_tariff_per_kwh=current_user.default_tariff_per_kwh,
        default_state=current_user.default_state,
        # A saved off-grid/remote household preference remains part of the
        # home workflow. Cross-track preferences never switch a home survey
        # into a commercial one (or vice versa).
        default_property_type=default_property_type,
        state_choices=INDIAN_STATES,
        psg_guidance=build_pm_surya_ghar_guidance()
        if planning_track == "home"
        else None,
        psg_rule=_psg_rule_context(),
    )


def _finite_float(value, field_name, *, minimum=None, maximum=None, allow_none=False):
    if value in (None, ""):
        if allow_none:
            return None
        raise ValueError(f"{field_name} is required")
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a finite number")
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{field_name} must be a finite number")
    if minimum is not None and number < minimum:
        raise ValueError(f"{field_name} must be at least {minimum:g}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name} must be at most {maximum:g}")
    return number


def _save_business_estimate_payload(payload):
    """Validate first, then calculate and persist the entire assessment atomically."""
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    payload = dict(payload)
    if not isinstance(payload.get("address"), str) or not payload["address"].strip():
        raise ValueError("Address is required")
    # Normal form submissions serialize the same Leaflet coordinate arrays as JSON.
    for field in ("polygon", "obstructions"):
        if isinstance(payload.get(field), str):
            try:
                payload[field] = json.loads(payload[field] or "[]")
            except ValueError as exc:
                raise ValueError(f"{field} must contain valid JSON coordinates") from exc
    if not isinstance(payload.get("polygon"), list):
        raise ValueError("polygon must be an array of coordinates")
    if not isinstance(payload.get("obstructions", []), list):
        raise ValueError("obstructions must be an array of polygons")
    for field in ("state", "load_profile", "net_metering_mode", "business_type", "daytime_power_use"):
        if payload.get(field) is not None and not isinstance(payload[field], str):
            raise ValueError(f"{field} must be text")
    preferences = validate_finance_preferences(payload)
    units = _finite_float(payload.get("monthly_units_kwh"), "monthly_units_kwh", minimum=0, allow_none=True)
    bill = _finite_float(payload.get("monthly_bill", payload.get("monthly_bill_rs")),
                         "monthly_bill", minimum=0, allow_none=True)
    if units is None and bill is None:
        raise ValueError("Monthly units or monthly bill is required")
    payload["monthly_bill"] = bill
    if "orientation" not in payload and payload.get("orientation_deg") not in (None, ""):
        payload["orientation"] = payload["orientation_deg"]
    if "load_profile" not in payload:
        payload["load_profile"] = {"mostly_daytime": "day_heavy", "mixed": "flat",
                                   "mostly_night": "evening_heavy"}.get(payload.get("daytime_power_use"), "day_heavy")
    inputs = estimation_service.prepare_estimate_inputs(payload, planning_track="business")
    if inputs["tariff_per_kwh"] <= 0:
        raise ValueError("A positive commercial energy tariff is required")
    # Units are a demand/sizing input only, never a solar-generation measurement.
    if bill is None:
        inputs["monthly_bill"] = units * inputs["tariff_per_kwh"]
    metadata = {}
    for field in ("business_type", "operating_hours", "daytime_power_use", "roof_ownership", "tariff_category"):
        value = payload.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > 128):
            raise ValueError(f"{field} must be text of at most 128 characters")
        metadata[field] = value
    for field, minimum, maximum in (("operating_days", 1, 365), ("sanctioned_load_kw", 0, None)):
        metadata[field] = _finite_float(payload.get(field), field, minimum=minimum, maximum=maximum, allow_none=True)
    try:
        result, location = estimation_service.create_estimate(current_user.id, **inputs, commit=False)
        location.extras = {**location.extras, **metadata, **preferences,
                           "monthly_units_kwh": units, "monthly_bill_rs": bill,
                           "source": "business_estimate_page"}
        comparison = project_for_location(location)
        # Shared reports and the commercial result use the identical financing flows.
        location.extras = {**location.extras, "financing_comparison": comparison["financing_comparison"]}
        result["financing_comparison"] = comparison["financing_comparison"]
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    session["planning_track"] = "business"
    return result, location


def _business_estimate_response(*, confirm=False):
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict(flat=True)
    try:
        result, location = _save_business_estimate_payload(payload)
    except (ValueError, estimation_service.InvalidEstimateInputError) as exc:
        return jsonify({"error": str(exc)}), 400
    except (estimation_service.EstimationEngineError, estimation_service.EstimatePersistenceError, SQLAlchemyError):
        db.session.rollback()
        current_app.logger.warning("Commercial assessment failed; no assessment was saved")
        return jsonify({"error": "The assessment could not be calculated or saved. Please retry."}), 503
    destination = url_for("pages.business_estimate_result", location_id=location.id)
    if confirm:
        return redirect(destination)
    return jsonify({**result, "status": "saved", "location_id": location.id,
                    "planning_track": "business", "business_result_url": destination,
                    "redirect_url": url_for("pages.report", location_id=location.id)})


@pages_bp.route("/business/estimate", methods=["GET", "POST"])
@login_required
def business_estimate():
    if request.method == "POST":
        return _business_estimate_response()
    return survey("business", template_name="business_estimate.html")


@pages_bp.post("/business/estimate/confirm")
@login_required
def business_estimate_confirm():
    return _business_estimate_response(confirm=True)


def _commercial_comparison_or_none(location):
    try:
        return project_for_location(location)
    except (ValueError, TypeError, KeyError):
        # Old placeholder assessments do not become real estimates on a GET.
        return None


@pages_bp.get("/business/estimate/result/<int:location_id>")
@login_required
def business_estimate_result(location_id):
    location = db.session.get(UserLocation, location_id)
    if not _project_is_business(location):
        abort(404)
    session["planning_track"] = "business"
    project_rows = Project.query.filter_by(user_id=current_user.id, location_id=location.id).order_by(Project.created_at.desc()).all()
    return render_template(
        "business_estimate_result.html", planning_track="business", location=location,
        project=_commercial_comparison_or_none(location), extras=location.extras or {},
        projects=project_rows, consent_text_version=Config.PROJECT_CONSENT_TEXT_VERSION,
    )


def _persisted_finite_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _persisted_nonnegative_number(value):
    number = _persisted_finite_number(value)
    return number if number is not None and number >= 0 else None


def _project_location_context(project, *, installer_count=0):
    location = project.location
    extras = location.extras if isinstance(location.extras, dict) else {}
    location_context = extras.get("location_context") if isinstance(extras.get("location_context"), dict) else {}
    city = str(extras.get("city") or location_context.get("city") or "").strip()
    pin = str(extras.get("pincode") or location_context.get("pincode") or "").strip()
    business_type = extras.get("business_type") or extras.get("property_type")
    system_size_kw = _persisted_nonnegative_number(location.system_size)
    if not system_size_kw:
        system_size_kw = _persisted_nonnegative_number(extras.get("system_size_kw"))
    if system_size_kw == 0:
        system_size_kw = None

    annual_savings = _persisted_finite_number(extras.get("annual_net_benefit"))
    if annual_savings is None:
        monthly_savings = _persisted_finite_number(location.monthly_savings)
        if monthly_savings is not None:
            annual_savings = monthly_savings * 12

    installed_capacity_kw = _persisted_nonnegative_number(
        extras.get("installed_capacity_kw")
    )
    return {
        "site_name": str(
            extras.get("project_name")
            or extras.get("site_name")
            or extras.get("business_name")
            or project.owner
            or f"Project {project.id}"
        ),
        "city": city,
        "pin": pin,
        "business_type": (
            str(business_type).replace("_", " ").title()
            if business_type
            else None
        ),
        "system_size_kw": system_size_kw,
        "installed_capacity_kw": installed_capacity_kw,
        "yearly_savings": annual_savings,
        "installer_count": installer_count,
        "posted_date": project.created_at.date().isoformat() if project.created_at else None,
    }


def _project_status_label(status):
    labels = {
        "assessment_done": "Assessment done",
        "under_review": "Under review",
        "open_to_vendors": "Open to vendors",
        "vendor_connected": "Vendor connected",
        "decision": "Decision",
        "installation": "Installation",
        "closed": "Closed",
    }
    return labels.get(status, status.replace("_", " ").title())


@pages_bp.get("/api/projects")
@login_required
def api_projects():
    projects = Project.query.filter_by(user_id=current_user.id).order_by(Project.created_at.desc()).all()
    return jsonify(
        {
            "projects": [
                {
                    "id": project.id,
                    "location_id": project.location_id,
                    "status": project.status,
                    "contact_name": project.contact_name,
                    "email": project.email,
                    "phone": project.phone,
                    "closed_at": project.closed_at.isoformat() if project.closed_at else None,
                }
                for project in projects
            ]
        }
    )


@pages_bp.route("/business/projects")
@login_required
def business_projects():
    _require_business_workspace_role()
    status_filter = (request.args.get("status") or "all").strip().lower()
    query = (request.args.get("q") or "").strip().casefold()
    filter_statuses = {
        "all": None,
        "under_review": {"under_review"},
        "open_to_vendors": {"open_to_vendors"},
        "vendor_connected": {"vendor_connected", "decision", "installation"},
        "closed": {"closed"},
    }
    if status_filter not in filter_statuses:
        abort(400)

    projects = [
        project
        for project in Project.query.filter_by(user_id=current_user.id)
        .order_by(Project.created_at.desc())
        .all()
        if _project_is_business(project.location)
    ]
    project_ids = [project.id for project in projects]
    unlock_counts = {}
    if project_ids:
        unlock_counts = dict(
            db.session.query(
                VendorLeadUnlock.project_id,
                db.func.count(VendorLeadUnlock.id),
            )
            .filter(VendorLeadUnlock.project_id.in_(project_ids))
            .group_by(VendorLeadUnlock.project_id)
            .all()
        )

    project_summaries = {
        project.id: _project_location_context(
            project, installer_count=unlock_counts.get(project.id, 0)
        )
        for project in projects
    }
    selected_statuses = filter_statuses[status_filter]
    filtered_projects = [
        project
        for project in projects
        if (selected_statuses is None or project.status in selected_statuses)
        and (
            not query
            or any(
                query in value.casefold()
                for value in (
                    project_summaries[project.id]["site_name"],
                    project_summaries[project.id]["city"],
                    project_summaries[project.id]["pin"],
                )
                if value
            )
        )
    ]

    project_status_counts = {status: 0 for status in PROJECT_STATUS_CHOICES}
    for project in projects:
        if project.status in project_status_counts:
            project_status_counts[project.status] += 1

    filter_counts = {
        "all": len(projects),
        "under_review": project_status_counts["under_review"],
        "open_to_vendors": project_status_counts["open_to_vendors"],
        "vendor_connected": sum(
            project_status_counts[status]
            for status in ("vendor_connected", "decision", "installation")
        ),
        "closed": project_status_counts["closed"],
    }

    totals = {
        "project_count": len(projects),
        "capacity_kw": sum(
            summary["system_size_kw"]
            for summary in project_summaries.values()
            if summary["system_size_kw"] is not None
        ),
        "capacity_count": sum(
            summary["system_size_kw"] is not None
            for summary in project_summaries.values()
        ),
        "installed_capacity_kw": sum(
            summary["installed_capacity_kw"]
            for summary in project_summaries.values()
            if summary["installed_capacity_kw"] is not None
        ),
        "installed_capacity_count": sum(
            summary["installed_capacity_kw"] is not None
            for summary in project_summaries.values()
        ),
        "yearly_savings": sum(
            summary["yearly_savings"]
            for summary in project_summaries.values()
            if summary["yearly_savings"] is not None
        ),
        "yearly_savings_count": sum(
            summary["yearly_savings"] is not None
            for summary in project_summaries.values()
        ),
    }

    session["planning_track"] = "business"
    return render_template(
        "projects.html",
        planning_track="business",
        projects=filtered_projects,
        project_summaries=project_summaries,
        project_status_labels={
            status: _project_status_label(status)
            for status in PROJECT_STATUS_CHOICES
        },
        selected_status=status_filter,
        search_query=query,
        status_counts=filter_counts,
        project_status_counts=project_status_counts,
        totals=totals,
        active_nav="projects",
        project_context={
            "page_title": "Projects",
            "business_track": True,
        },
    )


@pages_bp.route("/projects")
@login_required
def projects_page():
    status_filter = (request.args.get("status") or "all").strip().lower()
    query = (request.args.get("q") or "").strip().lower()
    projects = Project.query.filter_by(user_id=current_user.id).order_by(Project.created_at.desc()).all()

    filtered_projects = []
    for project in projects:
        summary = _project_location_context(project)
        text = " ".join(
            [
                project.owner or "",
                project.contact_name or "",
                summary["site_name"] or "",
                summary["city"] or "",
                summary["pin"] or "",
                project.status or "",
                summary["business_type"] or "",
            ]
        ).lower()
        if status_filter != "all" and project.status != status_filter:
            continue
        if query and query not in text:
            continue
        filtered_projects.append(project)

    status_counts = {
        "all": len(projects),
        "assessment_done": 0,
        "under_review": 0,
        "open_to_vendors": 0,
        "vendor_connected": 0,
        "decision": 0,
        "installation": 0,
        "closed": 0,
    }
    for project in projects:
        status_counts[project.status] = status_counts.get(project.status, 0) + 1

    return jsonify({
        "projects": [
            {
                "id": project.id,
                "location_id": project.location_id,
                "status": project.status,
                "owner": project.owner,
                "site_name": _project_location_context(project)["site_name"],
                "city": _project_location_context(project)["city"],
                "pin": _project_location_context(project)["pin"],
                "system_size_kw": _project_location_context(project)["system_size_kw"],
                "yearly_savings": _project_location_context(project)["yearly_savings"],
            }
            for project in filtered_projects
        ],
        "counts": status_counts,
    })


@pages_bp.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    project = Project.query.filter_by(id=project_id, user_id=current_user.id).first_or_404()
    return _render_project_detail(project)


def _render_project_detail(project):
    location = project.location
    extras = location.extras if isinstance(location.extras, dict) else {}
    if project.user_id != current_user.id or location.user_id != current_user.id:
        abort(404)
    is_business = _project_is_business(location)
    if is_business:
        _require_business_workspace_role()
    installer_count = VendorLeadUnlock.query.filter_by(project_id=project.id).count()
    summary = _project_location_context(project, installer_count=installer_count)
    active_status = _project_status_label(project.status)
    financial_estimates = []
    for label, value, kind, allow_negative in (
        (_("Annual savings"), summary["yearly_savings"], "money", True),
        (_("Monthly bill"), extras.get("monthly_bill_rs"), "money", False),
        (_("Monthly consumption"), extras.get("monthly_units_kwh"), "number", False),
        (_("Energy tariff"), extras.get("tariff_per_kwh"), "tariff", False),
        (_("System cost"), location.system_cost, "money", False),
        (_("Net investment"), location.net_investment, "money", False),
        (_("Payback period"), location.payback_years, "years", False),
    ):
        number = (
            _persisted_finite_number(value)
            if allow_negative
            else _persisted_nonnegative_number(value)
        )
        if number is not None:
            financial_estimates.append(
                {"label": label, "value": number, "kind": kind}
            )

    assessment_details = []
    for label, value, kind in (
        (_("Planned system size"), summary["system_size_kw"], "capacity"),
        (_("Annual generation"), location.annual_generation, "energy"),
        (_("Roof area"), location.roof_area_sqm, "area"),
        (_("Usable roof area"), location.usable_area_sqm, "area"),
    ):
        number = _persisted_nonnegative_number(value)
        if number:
            assessment_details.append(
                {"label": label, "value": number, "kind": kind}
            )

    available_transitions = [
        status
        for status in PROJECT_STATUS_CHOICES
        if status != project.status
        and Project.is_valid_transition(current_user.role, project.status, status)
    ]
    return render_template(
        "project_detail.html",
        project=project,
        location=location,
        extras=extras,
        summary=summary,
        status_label=active_status,
        installer_count=installer_count,
        is_business=is_business,
        assessment_details=assessment_details,
        financial_estimates=financial_estimates,
        available_transitions=available_transitions,
        commercial_comparison=(
            _commercial_comparison_or_none(location) if is_business else None
        ),
    )


@pages_bp.route("/business/projects/<int:project_id>")
@login_required
def business_project_detail(project_id):
    _require_business_workspace_role()
    project = Project.query.filter_by(
        id=project_id, user_id=current_user.id
    ).first_or_404()
    if not _project_is_business(project.location):
        abort(404)
    return _render_project_detail(project)


@pages_bp.post("/business/projects/<int:project_id>/status")
@login_required
def transition_business_project(project_id):
    _require_business_workspace_role()
    project = Project.query.filter_by(
        id=project_id, user_id=current_user.id
    ).first_or_404()
    if not _project_is_business(project.location):
        abort(404)

    next_status = (request.form.get("status") or "").strip()
    reason = (request.form.get("reason") or "").strip()
    if (
        not next_status
        or next_status == project.status
        or not reason
        or len(reason) > 500
    ):
        abort(400)
    try:
        project.transition_status(
            current_user.role,
            current_user.id,
            next_status,
            note=reason,
        )
    except ValueError:
        flash(_("That project status transition is not allowed."), "error")
        return redirect(
            url_for("pages.business_project_detail", project_id=project.id)
        )
    db.session.commit()
    flash(_("Project status updated."), "success")
    return redirect(
        url_for("pages.business_project_detail", project_id=project.id)
    )


@pages_bp.post("/projects/create")
@login_required
def create_project():
    if current_user.role != "user":
        abort(403)
    location_id = request.form.get("location_id")
    if location_id is None and request.is_json:
        payload = request.get_json(silent=True) or {}
        location_id = payload.get("location_id")
    try:
        location_id = int(location_id)
    except (TypeError, ValueError):
        flash(_("Choose a valid commercial assessment to turn into a project."), "error")
        return redirect(url_for("pages.dashboard"))

    location = db.session.get(UserLocation, location_id)
    if location is None or location.user_id != current_user.id or not _project_is_business(location):
        abort(400)

    contact_name = (request.form.get("contact_name") or "").strip() or current_user.display_name
    phone = (request.form.get("phone") or "").strip()
    email = (request.form.get("email") or "").strip() or current_user.email
    note = (request.form.get("note") or "").strip()
    consent_given = request.form.get("consent_given") in {"1", "true", "yes", "on"}
    consent_fee = request.form.get("consent_fee") in {"1", "true", "yes", "on"}
    if not consent_given or not consent_fee:
        flash(_("Both consent statements are required before a project can be created."), "error")
        return redirect(url_for("pages.business_estimate_result", location_id=location.id))

    project = Project.query.filter_by(user_id=current_user.id, location_id=location.id).first()
    if project is None:
        project = Project(
            user_id=current_user.id,
            location_id=location.id,
            owner=current_user.display_name or contact_name,
            location_link=url_for("pages.business_estimate_result", location_id=location.id, _external=True),
            status="assessment_done",
            consent_given=True,
            consent_timestamp=datetime.now(timezone.utc),
            consent_text_version=Config.PROJECT_CONSENT_TEXT_VERSION,
            contact_name=contact_name,
            phone=phone or None,
            email=email or None,
            note=note or None,
        )
        db.session.add(project)
        db.session.flush()
        project.transition_status(current_user.role, current_user.id, "under_review", note="Project created")
    else:
        project.owner = current_user.display_name or contact_name
        project.location_link = url_for("pages.business_estimate_result", location_id=location.id, _external=True)
        project.contact_name = contact_name
        project.phone = phone or None
        project.email = email or None
        project.note = note or None
        project.consent_given = True
        project.consent_timestamp = datetime.now(timezone.utc)
        project.consent_text_version = Config.PROJECT_CONSENT_TEXT_VERSION
        if project.status == "closed":
            db.session.rollback()
            flash(_("A closed project cannot be reopened by resubmitting this form."), "error")
            return redirect(url_for("pages.business_estimate_result", location_id=location.id))
        if project.status == "assessment_done":
            project.transition_status(current_user.role, current_user.id, "under_review", note="Project created")

    db.session.commit()
    flash(_("Project created and sent for review."), "success")
    return redirect(url_for("pages.business_estimate_result", location_id=location.id))


@pages_bp.post("/projects/<int:project_id>/close")
@login_required
def close_project(project_id):
    _require_business_workspace_role()
    project = Project.query.filter_by(id=project_id, user_id=current_user.id).first_or_404()
    if project.status == "closed":
        flash(_("Project cannot be closed from its current status."), "error")
        return redirect(
            url_for("pages.business_estimate_result", location_id=project.location_id)
        )
    reason = (request.form.get("reason") or "").strip()
    if len(reason) > 500:
        abort(400)
    if not reason:
        reason = f"Project closed by {current_user.role}"
    try:
        project.transition_status(
            current_user.role, current_user.id, "closed", note=reason
        )
    except ValueError:
        flash(_("Project cannot be closed from its current status."), "error")
        return redirect(url_for("pages.business_estimate_result", location_id=project.location_id))
    db.session.commit()
    flash(_("Project closed. Installers who already unlocked it keep their own records."), "success")
    return redirect(url_for("pages.business_estimate_result", location_id=project.location_id))


@pages_bp.post("/projects/<int:project_id>/delete")
@login_required
def delete_project(project_id):
    _require_business_workspace_role()
    project = Project.query.filter_by(id=project_id, user_id=current_user.id).first_or_404()
    location_id = project.location_id
    db.session.delete(project)
    db.session.commit()
    flash(_("Project deleted. Installers who already unlocked it keep their own records."), "success")
    return redirect(url_for("pages.business_estimate_result", location_id=location_id))


@pages_bp.route("/dashboard")
@login_required
def dashboard():
    planning_track = _active_planning_track()
    locations = _locations_for_track(current_user.id, planning_track)
    annual_net_benefit = sum(
        float((location.extras or {}).get("annual_net_benefit") or 0)
        for location in locations
    )
    total_investment = sum(float(location.net_investment or 0) for location in locations)
    summary = {
        "saved_locations": len(locations),
        "total_capacity_kw": sum(location.system_size or 0 for location in locations),
        "annual_generation_kwh": sum(
            location.annual_generation or 0 for location in locations
        ),
        "monthly_savings": sum(location.monthly_savings or 0 for location in locations),
        "system_cost": sum(location.system_cost or 0 for location in locations),
        "total_investment": total_investment,
        "annual_net_benefit": annual_net_benefit,
        "annual_roi_pct": (
            round(annual_net_benefit / total_investment * 100, 1)
            if total_investment > 0
            else None
        ),
        "simple_payback_years": (
            round(total_investment / annual_net_benefit, 1)
            if annual_net_benefit > 0
            else None
        ),
        "subsidy_amount": sum(location.subsidy_amount or 0 for location in locations),
    }
    return render_template(
        "dashboard.html",
        locations=locations[:6],
        summary=summary,
        planning_track=planning_track,
    )


@pages_bp.get("/properties")
@login_required
def properties():
    planning_track = _active_planning_track()
    locations = _locations_for_track(current_user.id, planning_track)
    tracker_latest = _latest_tracker_events([location.id for location in locations])
    return render_template(
        "properties.html",
        locations=locations,
        tracker_latest=tracker_latest,
        tracker_status_labels=_tracker_status_labels(),
        planning_track=planning_track,
        tracker_enabled_location_ids={
            location.id
            for location in locations
            if _saved_location_psg_allowed(location)
        },
    )


@pages_bp.get("/reports")
def reports():
    return redirect(url_for("pages.properties"))


@pages_bp.get("/saved-locations")
def saved_locations():
    return redirect(url_for("pages.properties"))


@pages_bp.route("/compare", methods=["GET", "POST"])
@login_required
def compare():
    planning_track = _active_planning_track()
    locations = _locations_for_track(current_user.id, planning_track)
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
        selected_locations = [
            location
            for location in selected_locations
            if _location_planning_track(location) == planning_track
        ]

    comparison = None
    if len(selected_locations) >= 2:
        comparison = _build_scenario_comparison(selected_locations)

    return render_template(
        "compare.html",
        locations=locations,
        selected_locations=selected_locations,
        comparison=comparison,
        planning_track=planning_track,
    )




@pages_bp.route("/savings")
@login_required
def savings():
    planning_track = _active_planning_track()
    locations = _locations_for_track(current_user.id, planning_track)
    monthly_savings = sum(location.monthly_savings or 0 for location in locations)
    annual_savings = (
        sum(
            float((location.extras or {}).get("annual_net_benefit") or 0)
            for location in locations
        )
        if planning_track == "business"
        else monthly_savings * 12
    )
    co2_reduction_tons = sum(location.co2_reduction_tons or 0 for location in locations)
    environmental_equivalents = calculate_environmental_equivalents(co2_reduction_tons)
    best_opportunity = max(
        locations,
        key=lambda location: (
            (
                float((location.extras or {}).get("annual_net_benefit") or 0)
                if planning_track == "business"
                else location.monthly_savings or 0
            ),
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
        planning_track=planning_track,
    )


@pages_bp.route("/report/<int:location_id>")
@login_required
def report(location_id):
    report_data = location_service.get_report_context(location_id, current_user.id)
    if report_data is None:
        abort(404)
    _, loc_dict = report_data
    session["planning_track"] = loc_dict.get("planning_track") or "home"
    extras = loc_dict.get("extras") or {}
    location_context = extras.get("location_context") or {}
    discom_name = (
        location_context.get("discom_name")
        if isinstance(location_context, dict)
        else None
    )
    psg_allowed = _psg_guidance_allowed(loc_dict)
    tracker_events = []
    latest_tracker_event = None
    psg_guidance = None
    if psg_allowed:
        tracker_events = (
            ApplicationTrackerEvent.query.filter_by(location_id=location_id)
            .order_by(
                ApplicationTrackerEvent.created_at.desc(),
                ApplicationTrackerEvent.id.desc(),
            )
            .all()
        )
        latest_tracker_event = tracker_events[0] if tracker_events else None
        psg_guidance = build_pm_surya_ghar_guidance(
            system_size_kw=loc_dict.get("system_size"),
            subsidy_amount=loc_dict.get("subsidy_amount"),
            discom_name=discom_name,
        )
    return render_template(
        "report.html",
        loc=loc_dict,
        psg_guidance_allowed=psg_allowed,
        psg_guidance=psg_guidance,
        tracker_events=tracker_events,
        latest_tracker_event=latest_tracker_event,
        tracker_status_labels=_tracker_status_labels(),
        today_date=date.today().isoformat(),
        psg_rule=_psg_rule_context(),
    )


@pages_bp.post("/report/<int:location_id>/tracker")
@login_required
def update_application_tracker(location_id):
    location = UserLocation.query.filter_by(
        id=location_id, user_id=current_user.id
    ).first()
    if location is None:
        abort(404)
    report_data = location_service.get_report_context(location_id, current_user.id)
    if report_data is None or not _psg_guidance_allowed(report_data[1]):
        abort(404)

    status = (request.form.get("status") or "").strip()
    application_reference = (request.form.get("application_reference") or "").strip()
    note = (request.form.get("note") or "").strip()
    changed_on_raw = (request.form.get("changed_on") or "").strip()

    if status not in APPLICATION_TRACKER_STATUS_CODES:
        flash(_("Choose a valid application status."), "error")
        return redirect(url_for("pages.report", location_id=location_id))
    if len(application_reference) > 128:
        flash(_("The application reference must be 128 characters or fewer."), "error")
        return redirect(url_for("pages.report", location_id=location_id))
    if len(note) > 1000:
        flash(_("The note must be 1,000 characters or fewer."), "error")
        return redirect(url_for("pages.report", location_id=location_id))
    try:
        changed_on = date.fromisoformat(changed_on_raw)
    except ValueError:
        flash(_("Enter a valid status-change date."), "error")
        return redirect(url_for("pages.report", location_id=location_id))

    db.session.add(
        ApplicationTrackerEvent(
            location_id=location_id,
            status=status,
            application_reference=application_reference or None,
            changed_on=changed_on,
            note=note or None,
        )
    )
    db.session.commit()
    flash(_("Your tracker reminder was updated."), "success")
    return redirect(url_for("pages.report", location_id=location_id))


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
    base = location.scenario_label or location.address or f"Scenario {chr(ord('A') + index)}"
    if location.system_size is None:
        return base
    configuration = f"{float(location.system_size):.2f} kW"
    if location.battery_kwh is not None:
        configuration += (
            f" + {float(location.battery_kwh):.1f} kWh battery"
            if float(location.battery_kwh) > 0
            else " · solar only"
        )
    return f"{base} · {configuration}"


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
