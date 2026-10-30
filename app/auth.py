import re
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import SQLAlchemyError
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
    ConsultationRequest,
    Project,
    User,
    UserLocation,
    VendorCreditLedger,
    VendorLeadUnlock,
    VendorSavedLead,
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
    if current_user.is_authenticated and current_user.role == "vendor":
        return url_for(
            "auth.vendor_board"
            if current_user.is_vendor_approved
            else "auth.vendor_pending"
        )
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


def _render_vendor_leads(saved_only=False):
    from app.services.vendor_lead_service import BUSINESS_TYPES, find_leads, parse_filters
    filters, errors = parse_filters(request.args)
    leads, total, page, pages = ([], 0, 1, 1) if errors else find_leads(
        current_user, filters, saved_only=saved_only)
    endpoint = "auth.vendor_saved_leads" if saved_only else "auth.vendor_board"
    def page_url(number):
        return url_for(endpoint, **{**{k: v for k, v in filters.items() if v}, "page": number})
    return render_template(
        "vendor_board.html", leads=leads, filters=filters, errors=errors,
        total=total, page=page, pages=pages, page_url=page_url,
        saved_only=saved_only, business_types=BUSINESS_TYPES,
        credit_balance=current_user.vendor_credits_balance,
    ), 400 if errors else 200


@auth_bp.get("/vendor/board")
@require_approved_vendor
def vendor_board():
    return _render_vendor_leads()


@auth_bp.get("/vendor/saved-leads")
@require_approved_vendor
def vendor_saved_leads():
    return _render_vendor_leads(saved_only=True)


@auth_bp.get("/vendor/leads/<int:project_id>")
@require_approved_vendor
def vendor_lead_detail(project_id):
    from app.services.vendor_lead_service import eligible_query, public_card
    from app.services.vendor_credit_service import outcome_history
    unlock = VendorLeadUnlock.query.filter_by(vendor_user_id=current_user.id, project_id=project_id).first()
    query, _columns = eligible_query(current_user, owned_only=unlock is not None)
    row = query.filter(Project.id == project_id).first()
    if row is None:
        abort(404)
    saved = VendorSavedLead.query.filter_by(vendor_user_id=current_user.id, project_id=project_id).first()
    history = outcome_history(unlock).limit(50).all() if unlock else []
    return render_template("vendor_lead_detail.html", item=public_card(row, {project_id} if saved else set()),
        project=db.session.get(Project, project_id) if unlock else None, unlock=unlock,
        outcome=history[0].detail if history else None, outcome_events=history)


@auth_bp.post("/vendor/leads/<int:project_id>/saved")
@require_roles("vendor")
def vendor_save_lead(project_id):
    from sqlalchemy.exc import IntegrityError, SQLAlchemyError
    from app.services.vendor_lead_service import eligible_query
    if not current_user.is_vendor_approved:
        abort(403)
    state = request.form.get("saved")
    if state not in {"true", "false"}:
        return jsonify(error=_("Choose save or unsave.")), 400
    try:
        if state == "true":
            query, _columns = eligible_query(current_user)
            if query.filter(Project.id == project_id).first() is None:
                return jsonify(error=_("This lead is no longer available.")), 404
            existing = VendorSavedLead.query.filter_by(vendor_user_id=current_user.id, project_id=project_id).first()
            if existing is None:
                # Unique constraint is the final arbiter for concurrent saves.
                db.session.add(VendorSavedLead(vendor_user_id=current_user.id, project_id=project_id))
        else:
            # Idempotent and vendor-scoped, even when the project is no longer eligible.
            VendorSavedLead.query.filter_by(vendor_user_id=current_user.id, project_id=project_id).delete()
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if state != "true" or VendorSavedLead.query.filter_by(vendor_user_id=current_user.id, project_id=project_id).first() is None:
                raise
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error=_("Could not update saved leads. Please try again.")), 503
    message = _("Lead saved.") if state == "true" else _("Lead removed from saved leads.")
    if request.accept_mimetypes.best == "application/json":
        return jsonify(saved=state == "true", message=message)
    flash(message, "success")
    # Fixed local destinations only. Never accept arbitrary return URLs.
    return redirect(url_for("auth.vendor_saved_leads" if request.form.get("view") == "saved" else "auth.vendor_board"))


def _credit_operation(operation, success, destination="auth.vendor_credits", **destination_args):
    from sqlalchemy.exc import SQLAlchemyError
    from app.services.vendor_credit_service import CreditError
    try:
        result = operation()
    except CreditError as exc:
        if request.accept_mimetypes.best == "application/json":
            return jsonify(error=_(exc.message)), exc.status
        flash(_(exc.message), "error")
        return redirect(url_for(destination, **destination_args))
    except SQLAlchemyError:
        db.session.rollback()
        message = _("Credit operation failed. No partial changes were saved. Please try again.")
        if request.accept_mimetypes.best == "application/json":
            return jsonify(error=message), 503
        flash(message, "error")
        return redirect(url_for(destination, **destination_args))
    if request.accept_mimetypes.best == "application/json":
        return jsonify(message=_(success), result=result)
    flash(_(success), "success")
    return redirect(url_for(destination, **destination_args))


@auth_bp.post("/vendor/board/<int:project_id>/unlock")
@require_roles("vendor")
def vendor_unlock_project(project_id):
    from app.services.vendor_credit_service import unlock_lead
    vendor_id = current_user.id
    return _credit_operation(lambda: unlock_lead(vendor_id, project_id),
        "Lead unlocked. You can view the project details and contact information.", "auth.vendor_unlocked_leads")


@auth_bp.post("/vendor/board/<int:project_id>/status")
@require_roles("vendor")
def vendor_lead_status(project_id):
    from app.services.vendor_credit_service import record_outcome
    vendor_id = current_user.id
    outcome = (request.form.get("outcome") or "").strip().lower()
    return _credit_operation(lambda: record_outcome(vendor_id, project_id, outcome),
        "Your lead outcome was recorded. The project status is unchanged.", "auth.vendor_unlocked_leads")


@auth_bp.get("/vendor/unlocked-leads")
@require_approved_vendor
def vendor_unlocked_leads():
    from app.services.vendor_lead_service import eligible_query, public_card
    from app.services.vendor_credit_service import outcome_history
    pagination = VendorLeadUnlock.query.filter_by(vendor_user_id=current_user.id).order_by(
        VendorLeadUnlock.id.desc()).paginate(per_page=12, error_out=False)
    items = []
    for unlock in pagination.items:
        query, _columns = eligible_query(current_user, owned_only=True)
        row = query.filter(Project.id == unlock.project_id).first()
        history = outcome_history(unlock).limit(50).all()
        items.append(dict(unlock=unlock, item=public_card(row, set()) if row else None,
                          project=unlock.project if row else None, outcome_events=history,
                          outcome=history[0].detail if history else None))
    return render_template("vendor_unlocked_leads.html", items=items, pagination=pagination)


@auth_bp.get("/vendor/credits")
@require_approved_vendor
def vendor_credits():
    from app.services.vendor_credit_service import ledger_summary, CreditError
    try:
        summary = ledger_summary(current_user)
    except CreditError:
        summary = {"balance": None, "opening": None, "reconciled": False}
    transactions = VendorCreditLedger.query.filter_by(vendor_user_id=current_user.id).order_by(
        VendorCreditLedger.id.desc()).paginate(per_page=25, error_out=False)
    requests = VendorRefundRequest.query.filter_by(vendor_user_id=current_user.id, issue_type="credit_request").order_by(
        VendorRefundRequest.id.desc()).limit(50).all()
    return render_template("vendor_credits.html", summary=summary, transactions=transactions, credit_requests=requests, credit_costs=current_app.config["VENDOR_LEAD_UNLOCK_COSTS"])


@auth_bp.post("/vendor/credits/request")
@require_roles("vendor")
def vendor_request_credits():
    from app.services.vendor_credit_service import request_credits
    vendor_id = current_user.id
    requested, reason = request.form.get("amount"), request.form.get("reason", "")
    return _credit_operation(lambda: request_credits(vendor_id, requested, reason),
                             "Credit request submitted for administrator review.")


@auth_bp.post("/admin/credit-requests/<int:request_id>/review")
@require_roles("admin")
def admin_credit_request_review(request_id):
    from app.services.vendor_credit_service import review_credit_request
    admin_id = current_user.id
    decision = request.form.get("decision")
    return _credit_operation(lambda: review_credit_request(admin_id, request_id, decision),
                             "Credit request reviewed.", "auth.admin_vendors")


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


@auth_bp.get("/admin/consultations")
@require_roles("admin")
def admin_consultations():
    consultation_requests = ConsultationRequest.query.order_by(
        ConsultationRequest.created_at.desc(), ConsultationRequest.id.desc()
    ).all()
    return render_template(
        "admin_consultations.html",
        consultation_requests=consultation_requests,
        consultation_statuses=("pending", "contacted", "scheduled", "completed"),
        consultation_status_labels={
            "pending": _("Pending"),
            "contacted": _("Contacted"),
            "scheduled": _("Scheduled"),
            "completed": _("Completed"),
        },
    )


@auth_bp.post("/admin/consultations/<int:consultation_id>/update")
@require_roles("admin")
def admin_consultation_update(consultation_id):
    consultation_request = db.session.get(ConsultationRequest, consultation_id)
    if consultation_request is None:
        abort(404)
    status = (request.form.get("status") or "").strip()
    admin_notes = (request.form.get("admin_notes") or "").strip()
    valid_statuses = {"pending", "contacted", "scheduled", "completed"}
    if status not in valid_statuses:
        flash(_("Choose a valid consultation request status."), "error")
        return redirect(url_for("auth.admin_consultations"))
    if len(admin_notes) > 2000:
        flash(_("Administrative notes must be 2,000 characters or fewer."), "error")
        return redirect(url_for("auth.admin_consultations"))

    previous_status = consultation_request.status
    consultation_request.status = status
    consultation_request.admin_notes = admin_notes
    consultation_request.updated_at = datetime.now(timezone.utc)
    db.session.add(
        AdminActionLog(
            actor_user_id=current_user.id,
            action="update_consultation_request",
            target_type="consultation_request",
            target_id=consultation_request.id,
            detail=f"Status changed from {previous_status} to {status}",
        )
    )
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Consultation request update could not be saved")
        flash(_("The consultation update could not be saved. Please try again."), "error")
        return redirect(url_for("auth.admin_consultations"))

    flash(_("Consultation request updated."), "success")
    return redirect(url_for("auth.admin_consultations"))


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
    return render_template("admin_vendors.html", vendors=vendors, pending_refunds=pending_refunds,
                           credit_requests=[item for item in pending_refunds if item.issue_type == "credit_request"])


@auth_bp.post("/admin/vendors/<int:user_id>/approve")
@login_required
@require_roles("admin")
def admin_vendor_approve(user_id):
    from app.services.vendor_credit_service import approve_vendor
    admin_id = current_user.id
    return _credit_operation(lambda: approve_vendor(admin_id, user_id), "Vendor approved.", "auth.admin_vendors")


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
