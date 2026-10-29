import re
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.config import Config
from app.forms import (
    ChangePasswordForm,
    EditProfileForm,
    LoginForm,
    SettingsForm,
    SignupForm,
)
from app import SUPPORTED_LOCALES
from app.models import (
    AdminActionLog,
    Project,
    User,
    UserLocation,
    VendorCreditLedger,
    VendorLeadUnlock,
    VendorRefundRequest,
    db,
)

auth_bp = Blueprint("auth", __name__)
VALID_PLANNING_TRACKS = frozenset({"home", "business"})


def require_roles(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in allowed_roles:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def require_approved_vendor(view_func):
    @wraps(view_func)
    @require_roles("vendor")
    def wrapper(*args, **kwargs):
        if (current_user.vendor_status or "pending") != "approved":
            return render_template("vendor_pending.html")
        return view_func(*args, **kwargs)

    return wrapper


def _parse_numeric_range(raw_value):
    if raw_value is None:
        return 0.0, float("inf")
    text = str(raw_value).strip()
    if not text:
        return 0.0, float("inf")
    matches = re.findall(r"\d+(?:\.\d+)?", text)
    if not matches:
        return 0.0, float("inf")
    if len(matches) == 1:
        value = float(matches[0])
        return 0.0, value
    return float(matches[0]), float(matches[-1])


def _project_size_kw(project):
    location = project.location
    extras = location.extras if isinstance(location.extras, dict) else {}
    if location.system_size and float(location.system_size) > 0:
        return float(location.system_size)
    size = extras.get("system_size_kw")
    if size not in (None, ""):
        try:
            return float(size)
        except (TypeError, ValueError):
            pass
    roof_area = extras.get("roof_area_sqm") or location.roof_area_sqm
    if roof_area not in (None, ""):
        try:
            return float(roof_area) / 10.0
        except (TypeError, ValueError):
            pass
    return 0.0


def _lead_service_match(project, vendor_profile=None):
    vendor_profile = vendor_profile or {}
    service_area = str(vendor_profile.get("service_area") or "").strip()
    if not service_area:
        return True
    location = project.location
    extras = location.extras if isinstance(location.extras, dict) else {}
    city = str(extras.get("city") or "").strip()
    pincode = str(extras.get("pincode") or "").strip()
    text = f"{city} {pincode} {location.address or ''}".lower()
    candidate_tokens = [
        token.strip().lower() for token in re.split(r"[,;\n|]+", service_area) if token.strip()
    ]
    return any(
        token and token in text
        for token in candidate_tokens
    )


def _vendor_unlock_cost(project):
    size_kw = _project_size_kw(project)
    if size_kw <= 10:
        return Config.VENDOR_LEAD_UNLOCK_COSTS["small"]
    if size_kw <= 50:
        return Config.VENDOR_LEAD_UNLOCK_COSTS["medium"]
    return Config.VENDOR_LEAD_UNLOCK_COSTS["large"]


def _log_admin_action(action, *, target_type=None, target_id=None, detail=None):
    db.session.add(
        AdminActionLog(
            actor_user_id=current_user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )
    db.session.commit()


def _safe_next_url(value):
    """Return a local post-authentication target, never an external redirect."""
    if not value:
        return None
    target = urlsplit(value)
    if target.scheme or target.netloc:
        if target.scheme not in ("http", "https") or target.netloc != request.host:
            return None
    path = target.path or "/"
    if not path.startswith("/") or path.startswith("//"):
        return None
    return f"{path}?{target.query}" if target.query else path


def _requested_planning_track():
    explicit = (request.args.get("track") or "").strip().lower()
    if explicit in VALID_PLANNING_TRACKS:
        return explicit
    safe_next = _safe_next_url(request.args.get("next"))
    if safe_next:
        path = urlsplit(safe_next).path.rstrip("/")
        candidate = path.rsplit("/", 1)[-1]
        if path.startswith("/survey/") and candidate in VALID_PLANNING_TRACKS:
            return candidate
    return "home"


def _post_auth_destination(planning_track):
    """Persist the selected product and prevent a mismatched survey redirect."""
    track = planning_track if planning_track in VALID_PLANNING_TRACKS else "home"
    session["planning_track"] = track
    safe_next = _safe_next_url(request.args.get("next"))
    if safe_next:
        path = urlsplit(safe_next).path.rstrip("/")
        if path.startswith("/survey/"):
            return url_for("pages.survey", planning_track=track)
        return safe_next
    return url_for("pages.survey", planning_track=track)


@auth_bp.get("/language/<language>")
def set_language(language):
    if language not in SUPPORTED_LOCALES:
        return redirect(request.referrer or url_for("pages.index"))

    session["language"] = language
    if current_user.is_authenticated:
        current_user.language_preference = language
        db.session.commit()

    return redirect(request.referrer or url_for("pages.index"))


@auth_bp.route("/vendor/register", methods=["GET", "POST"])
@login_required
def vendor_register():
    if current_user.role == "admin":
        abort(403)
    if request.method == "POST":
        profile = {
            "company_name": (request.form.get("company_name") or "").strip(),
            "contact_person": (request.form.get("contact_person") or "").strip(),
            "phone": (request.form.get("phone") or "").strip(),
            "email": (request.form.get("email") or "").strip(),
            "gst_number": (request.form.get("gst_number") or "").strip(),
            "service_area": (request.form.get("service_area") or "").strip(),
            "capacity_range": (request.form.get("capacity_range") or "").strip(),
            "past_project_references": (request.form.get("past_project_references") or "").strip(),
        }
        if not all(profile.values()):
            flash(_("Please complete all vendor registration fields."), "error")
            return render_template("vendor_register.html", profile=profile)

        current_user.role = "vendor"
        current_user.vendor_status = "pending"
        current_user.vendor_profile = profile
        db.session.commit()
        flash(_("Your vendor registration is submitted and pending approval."), "success")
        return redirect(url_for("auth.vendor_pending"))

    return render_template("vendor_register.html", profile=current_user.vendor_profile or {})


@auth_bp.route("/vendor/pending")
@login_required
def vendor_pending():
    if current_user.role != "vendor":
        abort(403)
    return render_template("vendor_pending.html", vendor_status=current_user.vendor_status or "pending")


@auth_bp.route("/vendor/board")
@login_required
@require_approved_vendor
def vendor_board():
    filters = {
        "city": (request.args.get("city") or "").strip(),
        "capacity": (request.args.get("capacity") or "").strip(),
        "business_type": (request.args.get("business_type") or "").strip(),
        "preferred_model": (request.args.get("preferred_model") or "").strip(),
        "posted_date": (request.args.get("posted_date") or "").strip(),
    }
    vendor_profile = current_user.vendor_profile or {}
    lead_candidates = Project.query.filter_by(status="open_to_vendors").order_by(Project.created_at.desc()).all()
    leads = []
    unlocks = {item.project_id: item for item in VendorLeadUnlock.query.filter_by(vendor_user_id=current_user.id).all()}
    unlocked_project_ids = set(unlocks)
    outcomes = {}
    for event in AdminActionLog.query.filter_by(
        actor_user_id=current_user.id, target_type="vendor_lead_unlock", action="vendor_lead_outcome"
    ).order_by(AdminActionLog.id.desc()).all():
        outcomes.setdefault(event.target_id, event.detail)
    for project in lead_candidates:
        if not project.location:
            continue
        extras = project.location.extras if isinstance(project.location.extras, dict) else {}
        summary = {
            "city": str(extras.get("city") or "").strip() or (project.location.address or "").strip(),
            "pin": str(extras.get("pincode") or "").strip(),
            "business_type": str(extras.get("business_type") or "Commercial").strip(),
            "preferred_model": str(extras.get("preferred_model") or "").strip(),
            "monthly_units": extras.get("monthly_units_kwh"),
            "monthly_bill": extras.get("monthly_bill_rs"),
            "roof_area_sqm": extras.get("roof_area_sqm") or project.location.roof_area_sqm,
            "posted_date": project.created_at.date().isoformat() if project.created_at else "",
            "system_size_kw": _project_size_kw(project),
        }
        if filters["city"] and filters["city"].lower() not in f"{summary['city']} {summary['pin']}".lower():
            continue
        if filters["business_type"] and filters["business_type"].lower() != summary["business_type"].lower():
            continue
        if filters["preferred_model"] and filters["preferred_model"].lower() != summary["preferred_model"].lower():
            continue
        if filters["posted_date"] and summary["posted_date"] != filters["posted_date"]:
            continue
        if filters["capacity"]:
            try:
                min_kw, max_kw = _parse_numeric_range(filters["capacity"])
            except ValueError:
                min_kw, max_kw = 0.0, float("inf")
            if not (min_kw <= summary["system_size_kw"] <= max_kw or summary["system_size_kw"] >= min_kw):
                continue
        if not _lead_service_match(project, vendor_profile):
            continue
        unlock_count = VendorLeadUnlock.query.filter_by(project_id=project.id).count()
        if unlock_count >= Config.VENDOR_MAX_LEADS_PER_PROJECT and project.id not in unlocked_project_ids:
            continue
        leads.append({
            "project": project,
            "summary": summary,
            "unlock_count": unlock_count,
            "unlocked": project.id in unlocked_project_ids,
            "outcome": outcomes.get(unlocks[project.id].id) if project.id in unlocks else None,
            "unlock_cost": _vendor_unlock_cost(project),
        })

    return render_template(
        "vendor_board.html",
        leads=leads,
        filters=filters,
        current_user=current_user,
        credit_balance=current_user.vendor_credits_balance,
        max_unlocks=Config.VENDOR_MAX_LEADS_PER_PROJECT,
    )


@auth_bp.post("/vendor/board/<int:project_id>/unlock")
@login_required
@require_approved_vendor
def vendor_unlock_project(project_id):
    project = Project.query.get_or_404(project_id)
    if project.status != "open_to_vendors":
        flash(_("This lead is no longer open to vendors."), "error")
        return redirect(url_for("auth.vendor_board"))
    if not _lead_service_match(project, current_user.vendor_profile or {}):
        flash(_("This lead is outside your service area."), "error")
        return redirect(url_for("auth.vendor_board"))

    unlock_cost = _vendor_unlock_cost(project)
    with db.session.begin_nested():
        existing = VendorLeadUnlock.query.filter_by(
            vendor_user_id=current_user.id,
            project_id=project.id,
        ).first()
        if existing is not None:
            flash(_("This lead was already unlocked; you were not charged again."), "info")
            return redirect(url_for("auth.vendor_board"))

        current_unlock_count = VendorLeadUnlock.query.filter_by(project_id=project.id).count()
        if current_unlock_count >= Config.VENDOR_MAX_LEADS_PER_PROJECT:
            flash(_("This lead has reached its max vendor unlock limit."), "error")
            return redirect(url_for("auth.vendor_board"))

        if current_user.vendor_credits_balance < unlock_cost:
            flash(_("You do not have enough credits to unlock this lead."), "error")
            return redirect(url_for("auth.vendor_board"))

        current_user.vendor_credits_balance -= unlock_cost
        db.session.add(
            VendorCreditLedger(
                vendor_user_id=current_user.id,
                project_id=project.id,
                delta=-unlock_cost,
                balance_after=current_user.vendor_credits_balance,
                reason="lead_unlock",
                note=f"Unlocked lead {project.id} for {unlock_cost} credits.",
            )
        )
        db.session.add(
            VendorLeadUnlock(
                vendor_user_id=current_user.id,
                project_id=project.id,
                credits_charged=unlock_cost,
                source="lead_unlock",
            )
        )

    flash(_("Lead unlocked. You can view the project details and contact information."), "success")
    return redirect(url_for("auth.vendor_board"))


@auth_bp.post("/vendor/board/<int:project_id>/status")
@login_required
@require_approved_vendor
def vendor_lead_status(project_id):
    project = Project.query.get_or_404(project_id)
    unlock = VendorLeadUnlock.query.filter_by(vendor_user_id=current_user.id, project_id=project.id).first()
    if unlock is None:
        flash(_("Unlock the lead before marking its status."), "error")
        return redirect(url_for("auth.vendor_board"))

    outcome = (request.form.get("outcome") or "").strip().lower()
    if outcome not in {"won", "lost"}:
        flash(_("Choose a valid lead outcome."), "error")
        return redirect(url_for("auth.vendor_board"))
    previous = AdminActionLog.query.filter_by(
        actor_user_id=current_user.id, target_type="vendor_lead_unlock", target_id=unlock.id,
        action="vendor_lead_outcome",
    ).order_by(AdminActionLog.id.desc()).first()
    if previous is None or previous.detail != outcome:
        db.session.add(AdminActionLog(
            actor_user_id=current_user.id, action="vendor_lead_outcome",
            target_type="vendor_lead_unlock", target_id=unlock.id, detail=outcome,
        ))
    flash(_("Your lead outcome was recorded. The project status is unchanged."), "success")
    db.session.commit()
    return redirect(url_for("auth.vendor_board"))


@auth_bp.route("/admin")
@login_required
@require_roles("admin")
def admin_overview():
    projects = Project.query.order_by(Project.created_at.desc()).all()
    under_review = [project for project in projects if project.status == "under_review"]
    open_projects = [project for project in projects if project.status == "open_to_vendors"]
    won = sum(1 for project in projects if project.status == "vendor_connected")
    lost = sum(1 for project in projects if project.status == "closed")
    unlocked = VendorLeadUnlock.query.count()
    return render_template(
        "admin_projects.html",
        projects=under_review,
        open_projects=open_projects,
        won_count=won,
        lost_count=lost,
        unlocked_count=unlocked,
    )


@auth_bp.route("/admin/projects")
@login_required
@require_roles("admin")
def admin_projects():
    projects = Project.query.filter_by(status="under_review").order_by(Project.created_at.desc()).all()
    return render_template("admin_projects.html", projects=projects)


@auth_bp.post("/admin/projects/<int:project_id>/approve")
@login_required
@require_roles("admin")
def admin_project_approve(project_id):
    project = Project.query.get_or_404(project_id)
    if project.status != "under_review":
        flash(_("Only projects in under-review can be approved."), "error")
        return redirect(url_for("auth.admin_projects"))
    project.transition_status("admin", current_user.id, "open_to_vendors", note="Approved by admin")
    _log_admin_action(
        "approve_project",
        target_type="project",
        target_id=project.id,
        detail=f"Project {project.id} moved to open_to_vendors",
    )
    db.session.commit()
    flash(_("Project approved and opened to vendors."), "success")
    return redirect(url_for("auth.admin_projects"))


@auth_bp.post("/admin/projects/<int:project_id>/reject")
@login_required
@require_roles("admin")
def admin_project_reject(project_id):
    project = Project.query.get_or_404(project_id)
    if project.status != "under_review":
        flash(_("Only projects in under-review can be rejected."), "error")
        return redirect(url_for("auth.admin_projects"))
    reason = (request.form.get("reason") or "").strip() or "Rejected by admin"
    project.transition_status("admin", current_user.id, "closed", note=reason)
    _log_admin_action(
        "reject_project",
        target_type="project",
        target_id=project.id,
        detail=f"Project {project.id} rejected: {reason}",
    )
    db.session.commit()
    flash(_("Project rejected."), "success")
    return redirect(url_for("auth.admin_projects"))


@auth_bp.route("/admin/vendors")
@login_required
@require_roles("admin")
def admin_vendors():
    vendors = User.query.filter_by(role="vendor").order_by(User.created_at.desc()).all()
    pending_refunds = VendorRefundRequest.query.filter_by(status="pending").order_by(VendorRefundRequest.created_at.desc()).all()
    return render_template("admin_vendors.html", vendors=vendors, pending_refunds=pending_refunds)


@auth_bp.post("/admin/vendors/<int:user_id>/approve")
@login_required
@require_roles("admin")
def admin_vendor_approve(user_id):
    vendor = User.query.get_or_404(user_id)
    if vendor.role != "vendor":
        abort(404)
    vendor.vendor_status = "approved"
    if vendor.vendor_credits_balance <= 0:
        vendor.vendor_credits_balance = float(Config.VENDOR_STARTING_CREDITS)
        db.session.add(
            VendorCreditLedger(
                vendor_user_id=vendor.id,
                delta=float(Config.VENDOR_STARTING_CREDITS),
                balance_after=vendor.vendor_credits_balance,
                reason="approved_vendor_credits",
                note="Starting vendor credits issued on approval.",
            )
        )
    _log_admin_action(
        "approve_vendor",
        target_type="vendor",
        target_id=vendor.id,
        detail=f"Vendor {vendor.id} approved",
    )
    db.session.commit()
    flash(_("Vendor approved."), "success")
    return redirect(url_for("auth.admin_vendors"))


@auth_bp.post("/admin/vendors/<int:user_id>/suspend")
@login_required
@require_roles("admin")
def admin_vendor_suspend(user_id):
    vendor = User.query.get_or_404(user_id)
    if vendor.role != "vendor":
        abort(404)
    vendor.vendor_status = "suspended"
    db.session.commit()
    flash(_("Vendor suspended."), "success")
    return redirect(url_for("auth.admin_vendors"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    profile_form = EditProfileForm(
        display_name=current_user.display_name,
        email=current_user.email,
    )
    password_form = ChangePasswordForm()

    if (
        request.form.get("profile_action") == "profile"
        and profile_form.validate_on_submit()
    ):
        email = profile_form.email.data.strip().lower()
        other_user = User.query.filter(
            User.email == email,
            User.id != current_user.id,
        ).first()
        if other_user is not None:
            profile_form.email.errors.append(_("That email is already registered."))
        else:
            current_user.display_name = profile_form.display_name.data.strip()
            current_user.email = email
            db.session.commit()
            flash(_("Profile updated."), "success")
            return redirect(url_for("auth.profile"))

    if (
        request.form.get("password_action") == "password"
        and password_form.validate_on_submit()
    ):
        if not check_password_hash(
            current_user.password_hash, password_form.current_password.data
        ):
            password_form.current_password.errors.append(
                _("Current password is incorrect.")
            )
        else:
            current_user.password_hash = generate_password_hash(
                password_form.new_password.data
            )
            db.session.commit()
            flash(_("Password changed."), "success")
            return redirect(url_for("auth.profile"))

    location_count = UserLocation.query.filter_by(user_id=current_user.id).count()
    return render_template(
        "profile.html",
        profile_form=profile_form,
        password_form=password_form,
        location_count=location_count,
    )


@auth_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    form = SettingsForm(
        default_tariff_per_kwh=current_user.default_tariff_per_kwh,
        default_state=current_user.default_state or "",
        default_property_type=current_user.default_property_type or "residential",
    )
    if form.validate_on_submit():
        current_user.default_tariff_per_kwh = form.default_tariff_per_kwh.data
        current_user.default_state = form.default_state.data or None
        current_user.default_property_type = form.default_property_type.data or None
        db.session.commit()
        flash(_("Settings updated."), "success")
        return redirect(url_for("auth.settings"))

    return render_template("settings.html", form=form)


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(
            _safe_next_url(request.args.get("next"))
            or url_for("pages.dashboard")
        )
    form = SignupForm(planning_track=_requested_planning_track())
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if User.query.filter_by(email=email).first() is not None:
            form.email.errors.append(_("That email is already registered."))
        else:
            user = User(
                email=email,
                display_name=form.display_name.data.strip(),
                password_hash=generate_password_hash(form.password.data),
                role="user",
            )
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(_post_auth_destination(form.planning_track.data))
    return render_template("signup.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        flash(_("You're already logged in."), "info")
        return redirect(
            _safe_next_url(request.args.get("next"))
            or url_for("pages.dashboard")
        )
    form = LoginForm(planning_track=_requested_planning_track())
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user is None or not check_password_hash(
            user.password_hash, form.password.data
        ):
            flash(_("Invalid email or password."), "error")
        else:
            login_user(user)
            return redirect(_post_auth_destination(form.planning_track.data))
    return render_template("login.html", form=form)


@auth_bp.post("/logout")
def logout():
    logout_user()
    return redirect(url_for("pages.index"))
