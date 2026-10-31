"""SQL-backed, privacy-limited installer discovery. No financial calculations."""
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app
from flask_babel import format_date, gettext as _
from sqlalchemy import and_, case, func, or_, select

from app.models import Project, UserLocation, VendorLeadUnlock, VendorSavedLead, db

BUSINESS_TYPES = ("Shop", "Office", "Hotel", "School or institution", "Small factory", "Other")
MODELS = ("buy", "loan", "ppa")
SORTS = ("newest", "oldest", "capacity_asc", "capacity_desc", "bill_asc", "bill_desc")
PAGE_SIZE = 12
LEAD_BOARD_TIMEZONE = ZoneInfo("Asia/Kolkata")


def parse_filters(args):
    fields = ("city", "locality", "pincode", "capacity_min", "capacity_max", "business_type",
              "preferred_model", "bill_min", "bill_max", "posted", "sort", "page")
    values = {key: args.get(key, "").strip() for key in fields}
    errors = {}
    for key in fields:
        if len(args.getlist(key)) > 1 or len(values[key]) > 100:
            errors[key] = _("Enter one value of at most 100 characters.")
    for key in ("capacity_min", "capacity_max", "bill_min", "bill_max"):
        if values[key]:
            try:
                number = float(values[key])
                if not math.isfinite(number) or not 0 <= number <= 1e12:
                    raise ValueError
            except ValueError:
                errors[key] = _("Enter a finite, non-negative number.")
    for prefix in ("capacity", "bill"):
        low, high = prefix + "_min", prefix + "_max"
        if values[low] and values[high] and low not in errors and high not in errors:
            if float(values[low]) > float(values[high]):
                errors[high] = _("Maximum must be at least the minimum.")
    if values["pincode"] and not re.fullmatch(r"[0-9]{6}", values["pincode"]):
        errors["pincode"] = _("Enter a six-digit PIN code.")
    for key, options in (("business_type", BUSINESS_TYPES), ("preferred_model", MODELS),
                         ("posted", ("7", "30")), ("sort", SORTS)):
        if values[key] and values[key] not in options:
            errors[key] = _("Choose one of the listed options.")
    if values["page"] and (not values["page"].isascii() or not values["page"].isdigit()
                           or not 1 <= int(values["page"]) <= 1000000):
        errors["page"] = _("Choose a valid page number.")
    values["sort"] = values["sort"] or "newest"
    return values, errors


def _number(value, *, positive=False):
    # Upper bound also excludes SQLite text values and infinities. Never cast
    # malformed JSON strings to zero, nor estimate capacity from roof area.
    return case((and_(value > 0 if positive else value >= 0, value < 1e308), value))


def _json_number(key):
    value = UserLocation.extras[key].as_float()
    if db.engine.dialect.name == "sqlite":
        value = case((func.json_type(UserLocation.extras, "$." + key).in_(("integer", "real")), value))
    return _number(value)


def lead_columns():
    extras = UserLocation.extras
    def string_value(value, path, *, allow_integer=False):
        if db.engine.dialect.name == "sqlite":
            kinds = ("text", "integer") if allow_integer else ("text",)
            value = case((func.json_type(extras, path).in_(kinds), value))
        return func.nullif(func.trim(value), "")
    def text(key):
        return string_value(extras[key].as_string(), "$." + key, allow_integer=key == "pincode")
    def location_text(key):
        return func.coalesce(text(key), string_value(extras["location_context"][key].as_string(),
                                                    "$.location_context." + key, allow_integer=key == "pincode"))
    # Business units-only estimates contain a derived bill for sizing. The
    # explicit monthly_bill_rs NULL is authoritative: it is not an actual bill.
    bill = case((extras["source"].as_string() == "business_estimate_page", _json_number("monthly_bill_rs")),
                else_=func.coalesce(_json_number("monthly_bill_rs"), _json_number("monthly_bill")))
    legacy_type = func.lower(text("property_type"))
    planning_track = case(
        (text("planning_track").in_(("home", "business")), text("planning_track")),
        (legacy_type.in_(("commercial", "industrial", "msme", "factory")), "business"),
        (legacy_type.in_(("residential", "remote")), "home"),
    )
    return {
        "id": Project.id,
        "planning_track": planning_track,
        "city": location_text("city"), "locality": location_text("locality"),
        "pincode": location_text("pincode"),
        "business_type": text("business_type"), "preferred_model": func.lower(text("preferred_model")),
        "system_size_kw": func.coalesce(_number(UserLocation.system_size, positive=True),
                                         _number(_json_number("system_size_kw"), positive=True)),
        "roof_area_sqm": func.coalesce(_number(UserLocation.roof_area_sqm, positive=True),
                                       _number(_json_number("roof_area_sqm"), positive=True)),
        "monthly_bill": bill, "posted_date": Project.created_at,
        "timeline": text("installation_timeline"),
    }


def eligible_query(vendor, *, owned_only=False):
    """All access paths share eligibility, without fetching customer columns."""
    columns = lead_columns()
    extras = UserLocation.extras
    track = extras["planning_track"].as_string()
    legacy_type = func.lower(extras["property_type"].as_string())
    supported_project = or_(
        track.in_(("home", "business")),
        and_(
            or_(track.is_(None), track.notin_(("home", "business"))),
            legacy_type.in_(("residential", "remote", "commercial", "industrial", "msme", "factory")),
        ),
    )
    unlocked = select(VendorLeadUnlock.id).where(
        VendorLeadUnlock.project_id == Project.id, VendorLeadUnlock.vendor_user_id == vendor.id).exists()
    unlock_count = select(func.count(VendorLeadUnlock.id)).where(
        VendorLeadUnlock.project_id == Project.id).correlate(Project).scalar_subquery()
    query = db.session.query(*[value.label(key) for key, value in columns.items()]).select_from(Project).join(UserLocation)
    query = query.filter(Project.consent_given.is_(True), supported_project)
    if owned_only:
        query = query.filter(unlocked)
    else:
        query = query.filter(Project.status == "open_to_vendors",
                             or_(unlocked, unlock_count < current_app.config["VENDOR_MAX_LEADS_PER_PROJECT"]))
    return query, columns


def public_card(row, saved):
    card = dict(row._mapping)
    card.setdefault("available", True)
    posted_date = card["posted_date"]
    if posted_date:
        # SQLite strips timezone metadata from UTC datetimes. Reattach UTC
        # before converting so late-evening UTC publications show the correct
        # calendar date in SIGNIX's Asia/Kolkata operating timezone.
        if posted_date.tzinfo is None:
            posted_date = posted_date.replace(tzinfo=timezone.utc)
        posted_date = posted_date.astimezone(LEAD_BOARD_TIMEZONE).date()
    card["posted_label"] = format_date(posted_date, format="medium") if posted_date else None
    # Keep unknown enum values out of the UI rather than exposing arbitrary metadata.
    card["business_type"] = card["business_type"] if card["business_type"] in BUSINESS_TYPES else None
    card["preferred_model"] = card["preferred_model"] if card["preferred_model"] in MODELS else None
    size = card["system_size_kw"]
    tier = "small" if size is not None and size <= 10 else "medium" if size is not None and size <= 50 else "large"
    costs = current_app.config["VENDOR_LEAD_UNLOCK_COSTS"]
    cost = costs.get(tier) if isinstance(costs, dict) and size is not None else None
    card["unlock_cost"] = cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost > 0 else None
    card["saved"] = card["id"] in saved
    return card


def find_leads(vendor, filters, *, saved_only=False):
    query, columns = eligible_query(vendor)
    saved = select(VendorSavedLead.project_id).where(VendorSavedLead.vendor_user_id == vendor.id)
    if saved_only:
        visible = query.subquery()
        columns = {key: visible.c[key] for key in columns}
        columns["id"] = VendorSavedLead.project_id
        query = db.session.query(*[value.label(key) for key, value in columns.items()],
                                 visible.c.id.is_not(None).label("available")).select_from(VendorSavedLead).outerjoin(
            visible, visible.c.id == VendorSavedLead.project_id).filter(VendorSavedLead.vendor_user_id == vendor.id)
    for key in ("city", "locality"):
        if filters[key]:
            query = query.filter(func.lower(columns[key]).contains(filters[key].lower(), autoescape=True))
    for key in ("pincode", "business_type", "preferred_model"):
        if filters[key]:
            query = query.filter(columns[key] == filters[key])
    for prefix, column in (("capacity", columns["system_size_kw"]), ("bill", columns["monthly_bill"])):
        if filters[prefix + "_min"]:
            query = query.filter(column >= float(filters[prefix + "_min"]))
        if filters[prefix + "_max"]:
            query = query.filter(column <= float(filters[prefix + "_max"]))
    if filters["posted"]:
        now = datetime.now(timezone.utc)
        query = query.filter(columns["posted_date"] >= now - timedelta(days=int(filters["posted"])), columns["posted_date"] <= now)
    sorting = filters["sort"]
    column = columns["system_size_kw"] if sorting.startswith("capacity") else columns["monthly_bill"] if sorting.startswith("bill") else columns["posted_date"]
    order = column.asc() if sorting in ("oldest", "capacity_asc", "bill_asc") else column.desc()
    query = query.order_by(column.is_(None), order, columns["id"].desc())
    total = query.order_by(None).count()
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(int(filters["page"] or 1), pages)
    rows = query.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE).all()
    ids = [row.id for row in rows]
    saved_ids = set(db.session.scalars(saved.where(VendorSavedLead.project_id.in_(ids))))
    return [public_card(row, saved_ids) for row in rows], total, page, pages
