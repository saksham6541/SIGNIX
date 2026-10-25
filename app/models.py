# filename: app/models.py
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(320), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    default_tariff_per_kwh = db.Column(db.Float, nullable=True)
    default_state = db.Column(db.String(128), nullable=True)
    default_property_type = db.Column(db.String(32), nullable=True)
    language_preference = db.Column(db.String(8), nullable=True, default="en")

    locations = db.relationship(
        "UserLocation",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserLocation(db.Model):
    """Stores a single rooftop estimation run: the address, the drawn
    polygon, and the resulting solar estimate."""

    __tablename__ = "user_locations"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user = db.relationship("User", back_populates="locations")
    address = db.Column(db.String(512), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    user_priority = db.Column(db.String(32), nullable=True, default="no_preference")

    # Scenario / compare linking: multiple estimate configs for the same roof geometry.
    # roof_group_id is shared by all scenarios for one physical rooftop.
    # parent_location_id points at the estimate that was cloned (nullable for roots).
    roof_group_id = db.Column(db.String(36), nullable=True, index=True)
    parent_location_id = db.Column(
        db.Integer,
        db.ForeignKey("user_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scenario_label = db.Column(db.String(128), nullable=True)
    sizing_mode = db.Column(db.String(64), nullable=True)

    # GeoJSON polygon of the drawn rooftop, stored as JSON text (SQLite has
    # no native JSON type, SQLAlchemy's JSON type handles serialization).
    polygon_geojson = db.Column(db.JSON, nullable=True)
    # List of non-usable patches (water tanks, staircase rooms, AC units,
    # existing coverings) drawn inside the rooftop boundary, each a GeoJSON
    # polygon. Subtracted from roof_area_sqm to get usable_area_sqm.
    obstructions_geojson = db.Column(db.JSON, nullable=True)

    system_size = db.Column(db.Float, nullable=False, default=0.0)  # kWp
    annual_generation = db.Column(db.Float, nullable=False, default=0.0)  # kWh/year
    monthly_data = db.Column(db.JSON, nullable=True)  # {"Jan": kWh, ...}

    # Cached financial figures so the report/PDF can be regenerated without
    # recomputation.
    roof_area_sqm = db.Column(db.Float, nullable=True)  # gross rooftop area
    obstructed_area_sqm = db.Column(
        db.Float, nullable=True
    )  # non-usable area subtracted
    usable_area_sqm = db.Column(
        db.Float, nullable=True
    )  # roof_area_sqm - obstructed_area_sqm
    system_cost = db.Column(db.Float, nullable=True)
    subsidy_amount = db.Column(db.Float, nullable=True)
    net_investment = db.Column(db.Float, nullable=True)
    monthly_savings = db.Column(db.Float, nullable=True)
    co2_reduction_tons = db.Column(db.Float, nullable=True)
    irradiance_source = db.Column(
        db.String(32), nullable=True
    )  # nasa_power / pvgis / mock_fallback

    # Orientation & battery
    orientation_deg = db.Column(db.Float, nullable=True)
    orientation_label = db.Column(db.String(32), nullable=True)
    orientation_factor = db.Column(db.Float, nullable=True)
    recommended_tilt_deg = db.Column(db.Float, nullable=True)
    battery_kwh = db.Column(db.Float, nullable=True, default=0.0)
    battery_cost = db.Column(db.Float, nullable=True, default=0.0)

    # Extra performance metrics (cached for report/PDF)
    specific_yield = db.Column(db.Float, nullable=True)  # kWh/kWp/year
    capacity_factor = db.Column(db.Float, nullable=True)  # %
    lcoe = db.Column(db.Float, nullable=True)  # ₹/kWh
    lifetime_kwh = db.Column(db.Float, nullable=True)
    self_consumption_frac = db.Column(db.Float, nullable=True)
    payback_years = db.Column(db.Float, nullable=True)
    suitability_rating = db.Column(db.JSON, nullable=True)
    # Flexible bag for any extra numbers / labels
    extras = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    rooftop_designs = db.relationship(
        "RooftopDesign",
        back_populates="location",
        cascade="all, delete-orphan",
        order_by="RooftopDesign.revision_number",
    )
    application_tracker_events = db.relationship(
        "ApplicationTrackerEvent",
        back_populates="location",
        cascade="all, delete-orphan",
        order_by="ApplicationTrackerEvent.created_at",
    )

    def to_dict(self):
        from app.geo import canonicalize_stored_ring
        from app.solar_logic import current_assumption_fingerprint

        polygon_geojson = self.polygon_geojson
        if isinstance(polygon_geojson, dict):
            coordinates = polygon_geojson.get("coordinates") or []
            if coordinates:
                polygon_geojson = dict(polygon_geojson)
                polygon_geojson["coordinates"] = [
                    canonicalize_stored_ring(
                        ring, self.latitude, self.longitude
                    )
                    for ring in coordinates
                ]

        obstructions_geojson = self.obstructions_geojson
        if (
            isinstance(obstructions_geojson, dict)
            and obstructions_geojson.get("type") == "MultiPolygon"
        ):
            obstructions_geojson = dict(obstructions_geojson)
            obstructions_geojson["coordinates"] = [
                [
                    canonicalize_stored_ring(
                        ring, self.latitude, self.longitude
                    )
                    for ring in polygon
                ]
                for polygon in (obstructions_geojson.get("coordinates") or [])
            ]
        extras = dict(self.extras) if isinstance(self.extras, dict) else {}
        planning_track = extras.get("planning_track")
        if planning_track not in {"home", "business"}:
            planning_track = (
                "business"
                if str(extras.get("property_type") or "").strip().lower()
                in {"commercial", "industrial", "msme", "factory"}
                else "home"
            )
        extras["planning_track"] = planning_track
        saved_fingerprint = extras.get("assumption_fingerprint")
        current_fingerprint = current_assumption_fingerprint()
        if not saved_fingerprint:
            calculation_status = "legacy_unversioned"
        elif saved_fingerprint != current_fingerprint:
            calculation_status = "stale_assumptions"
            if extras.get("surface_generation"):
                extras["surface_generation_status"] = "stale"
        else:
            calculation_status = "current"
        extras["calculation_status"] = calculation_status

        accepted_layout = extras.get("accepted_layout")
        if (
            isinstance(accepted_layout, dict)
            and accepted_layout.get("status") == "accepted"
            and accepted_layout.get("assumption_fingerprint")
            != current_fingerprint
        ):
            extras["accepted_layout"] = {
                **accepted_layout,
                "status": "stale",
                "stale_reason": "calculation_assumptions_changed",
            }

        financial_provenance = extras.get("financial_provenance")
        if not isinstance(financial_provenance, dict):
            financial_status = "legacy_unversioned"
        elif calculation_status != "current":
            financial_status = "stale_assumptions"
        else:
            saved_inputs = financial_provenance.get("inputs") or {}

            def _same_number(left, right, tolerance=1e-6):
                try:
                    return abs(float(left) - float(right)) <= tolerance
                except (TypeError, ValueError):
                    return left is None and right is None

            numeric_checks = (
                (saved_inputs.get("generation_kwh"), self.annual_generation),
                (saved_inputs.get("system_size_kw"), self.system_size),
                (saved_inputs.get("system_cost"), self.system_cost),
                (saved_inputs.get("battery_cost"), self.battery_cost or 0),
                (saved_inputs.get("subsidy_amount"), self.subsidy_amount or 0),
                (saved_inputs.get("tariff_per_kwh"), extras.get("tariff_per_kwh")),
                (saved_inputs.get("feed_in_tariff_per_kwh"), extras.get("feed_in_tariff")),
            )
            text_checks = (
                saved_inputs.get("net_metering_mode")
                == extras.get("net_metering_mode", "full_offset"),
                saved_inputs.get("planning_track", planning_track) == planning_track,
            )
            financial_status = (
                "current"
                if all(_same_number(left, right) for left, right in numeric_checks)
                and all(text_checks)
                else "stale_inputs"
            )
        extras["financial_status"] = financial_status

        return {
            "id": self.id,
            "user_id": self.user_id,
            "address": self.address,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "user_priority": self.user_priority or "no_preference",
            "roof_group_id": self.roof_group_id,
            "parent_location_id": self.parent_location_id,
            "scenario_label": self.scenario_label,
            "sizing_mode": self.sizing_mode
                or ((self.extras or {}).get("sizing_method") if isinstance(self.extras, dict) else None),
            "polygon_geojson": polygon_geojson,
            "obstructions_geojson": obstructions_geojson,
            "system_size": round(self.system_size, 2),
            "annual_generation": round(self.annual_generation, 2),
            "monthly_data": self.monthly_data,
            "roof_area_sqm": round(self.roof_area_sqm or 0, 2),
            "obstructed_area_sqm": round(self.obstructed_area_sqm or 0, 2),
            "usable_area_sqm": round(
                self.usable_area_sqm or (self.roof_area_sqm or 0), 2
            ),
            "system_cost": round(self.system_cost or 0, 2),
            "subsidy_amount": round(self.subsidy_amount or 0, 2),
            "net_investment": round(self.net_investment or 0, 2),
            "monthly_savings": round(self.monthly_savings or 0, 2),
            "co2_reduction_tons": round(self.co2_reduction_tons or 0, 2),
            "irradiance_source": self.irradiance_source,
            "irradiance_confidence": (
                (self.extras or {}).get("irradiance_confidence")
                if isinstance(self.extras, dict)
                else None
            )
            or {
                "nasa_power": "high",
                "pvgis": "high",
                "mock": "low",
                "mock_fallback": "low",
            }.get(self.irradiance_source, "low"),

            "orientation_deg": self.orientation_deg,
            "orientation_label": self.orientation_label,
            "orientation_factor": self.orientation_factor,
            "recommended_tilt_deg": self.recommended_tilt_deg,
            "battery_kwh": self.battery_kwh or 0,
            "battery_cost": round(self.battery_cost or 0, 2),
            "specific_yield": self.specific_yield,
            "capacity_factor": self.capacity_factor,
            "lcoe": self.lcoe,
            "lifetime_kwh": self.lifetime_kwh,
            "self_consumption_frac": self.self_consumption_frac,
            "payback_years": self.payback_years,
            "suitability_rating": self.suitability_rating,
            "calculation_status": calculation_status,
            "financial_status": financial_status,
            "planning_track": planning_track,
            "extras": extras,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ApplicationTrackerEvent(db.Model):
    """A user-entered status change for a saved estimate."""

    __tablename__ = "application_tracker_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('not_started', 'applied', 'feasibility_approved', "
            "'installed', 'net_meter_inspection', 'subsidy_received')",
            name="ck_application_tracker_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(
        db.Integer,
        db.ForeignKey("user_locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    location = db.relationship(
        "UserLocation", back_populates="application_tracker_events"
    )
    status = db.Column(db.String(32), nullable=False)
    application_reference = db.Column(db.String(128), nullable=True)
    changed_on = db.Column(db.Date, nullable=False)
    note = db.Column(db.String(1000), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class RooftopDesign(db.Model):
    """One immutable revision of rooftop-design state for an estimate.

    Authoritative fields are deliberately separate from advisory provider
    records. Creating or reviewing a design produces a new revision; financial
    fields on ``UserLocation`` are never modified by this model.
    """

    __tablename__ = "rooftop_designs"
    __table_args__ = (
        db.UniqueConstraint(
            "user_location_id",
            "revision_number",
            name="uq_rooftop_design_location_revision",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_location_id = db.Column(
        db.Integer,
        db.ForeignKey("user_locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    location = db.relationship("UserLocation", back_populates="rooftop_designs")

    revision_number = db.Column(db.Integer, nullable=False)
    schema_version = db.Column(db.String(32), nullable=False, default="1.0")
    review_status = db.Column(db.String(32), nullable=False, default="draft")
    is_current = db.Column(db.Boolean, nullable=False, default=True, index=True)

    # Authoritative state: user-created geometry and explicitly accepted data.
    authoritative_roof_geojson = db.Column(db.JSON, nullable=False)
    manual_obstructions_geojson = db.Column(db.JSON, nullable=True)
    accepted_roof_planes = db.Column(db.JSON, nullable=True)
    accepted_obstructions_geojson = db.Column(db.JSON, nullable=True)
    roof_type = db.Column(db.String(32), nullable=True)
    pitch_degrees = db.Column(db.Float, nullable=True)
    azimuth_degrees = db.Column(db.Float, nullable=True)
    elevation_meters = db.Column(db.Float, nullable=True)
    height_meters = db.Column(db.Float, nullable=True)

    # Future renderer outputs are persisted but not interpreted here.
    panel_layout = db.Column(db.JSON, nullable=True)
    shade_analysis = db.Column(db.JSON, nullable=True)
    diagnostics = db.Column(db.JSON, nullable=True)

    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    sources = db.relationship(
        "RooftopDesignSource",
        back_populates="design",
        cascade="all, delete-orphan",
        order_by="RooftopDesignSource.id",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_location_id": self.user_location_id,
            "revision_number": self.revision_number,
            "schema_version": self.schema_version,
            "review_status": self.review_status,
            "is_current": bool(self.is_current),
            "authoritative": {
                "roof_boundary": self.authoritative_roof_geojson,
                "manual_obstructions": self.manual_obstructions_geojson,
                "accepted_roof_planes": self.accepted_roof_planes,
                "accepted_obstructions": self.accepted_obstructions_geojson,
                "roof_metadata": {
                    "roof_type": self.roof_type,
                    "pitch_degrees": self.pitch_degrees,
                    "azimuth_degrees": self.azimuth_degrees,
                    "elevation_meters": self.elevation_meters,
                    "height_meters": self.height_meters,
                },
            },
            "advisory_sources": [source.to_dict() for source in self.sources],
            "panel_layout": self.panel_layout,
            "shade_analysis": self.shade_analysis,
            "diagnostics": self.diagnostics,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class RooftopDesignSource(db.Model):
    """Advisory provider data attached to one rooftop-design revision."""

    __tablename__ = "rooftop_design_sources"

    id = db.Column(db.Integer, primary_key=True)
    rooftop_design_id = db.Column(
        db.Integer,
        db.ForeignKey("rooftop_designs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    design = db.relationship("RooftopDesign", back_populates="sources")

    provider = db.Column(db.String(64), nullable=False)
    data_type = db.Column(db.String(64), nullable=False)
    retrieved_at = db.Column(db.DateTime, nullable=True)
    source_version = db.Column(db.String(128), nullable=True)
    confidence = db.Column(db.String(64), nullable=True)
    quality = db.Column(db.String(64), nullable=True)
    availability_status = db.Column(
        db.String(32), nullable=False, default="available", index=True
    )
    coverage = db.Column(db.JSON, nullable=True)
    cache_key = db.Column(db.String(160), nullable=True, index=True)
    reusable_until = db.Column(db.DateTime, nullable=True)
    is_usable = db.Column(db.Boolean, nullable=False, default=False)
    review_status = db.Column(db.String(32), nullable=False, default="pending")
    external_reference = db.Column(db.JSON, nullable=True)
    raw_payload = db.Column(db.JSON, nullable=True)
    analysis_result = db.Column(db.JSON, nullable=True)
    review_state = db.Column(db.JSON, nullable=True)
    diagnostics = db.Column(db.JSON, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "provider": self.provider,
            "data_type": self.data_type,
            "retrieved_at": self.retrieved_at.isoformat()
            if self.retrieved_at
            else None,
            "source_version": self.source_version,
            "confidence": self.confidence,
            "quality": self.quality,
            "availability_status": self.availability_status,
            "coverage": self.coverage,
            "cache_key": self.cache_key,
            "reusable_until": self.reusable_until.isoformat()
            if self.reusable_until
            else None,
            "is_usable": bool(self.is_usable),
            "review_status": self.review_status,
            "external_reference": self.external_reference,
            "raw_payload": self.raw_payload,
            "analysis_result": self.analysis_result,
            "review_state": self.review_state,
            "diagnostics": self.diagnostics,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TariffTable(db.Model):
    """Per-state / per-utility electricity tariff, used to convert kWh
    generated into ₹ savings.

    Also carries a sensible default net-metering assumption for the state
    (user can still override on the estimate form).
    """

    __tablename__ = "tariff_table"

    id = db.Column(db.Integer, primary_key=True)
    state = db.Column(db.String(128), nullable=False, index=True)
    utility_name = db.Column(db.String(128), nullable=False)
    rate_per_kwh = db.Column(db.Float, nullable=False)
    # full_offset | export_at_tariff | export_at_feed_in_rate
    net_metering_mode = db.Column(db.String(32), nullable=True, default="full_offset")
    # ₹/kWh credited for exported units when mode uses a feed-in / export credit
    export_credit_rate = db.Column(db.Float, nullable=True)
    notes = db.Column(db.String(256), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "state": self.state,
            "utility_name": self.utility_name,
            "rate_per_kwh": self.rate_per_kwh,
            "net_metering_mode": self.net_metering_mode or "full_offset",
            "export_credit_rate": self.export_credit_rate,
            "notes": self.notes,
        }


class SubsidyScheme(db.Model):
    """Versioned central subsidy slabs (PM Surya Ghar).

    Each revision is a set of rows sharing the same ``scheme_code`` and
    ``effective_from``. To publish a new policy, **insert** a new set with a
    later ``effective_from`` (and optionally set ``effective_to`` on the prior
    set) — do not edit historical rows in place.

    Amount fields:
      - amount_per_kw: rate applied to kW falling inside [min_kw, max_kw)
      - fixed_amount: absolute total used as a cap / fixed band total when set
      - subsidy_amount: cumulative amount at max_kw (legacy / display helper)
    """

    __tablename__ = "subsidy_scheme"

    id = db.Column(db.Integer, primary_key=True)
    scheme_code = db.Column(db.String(64), nullable=False, default="pm_surya_ghar")
    min_kw = db.Column(db.Float, nullable=False)
    max_kw = db.Column(db.Float, nullable=False)
    amount_per_kw = db.Column(db.Float, nullable=True)
    fixed_amount = db.Column(db.Float, nullable=True)
    # Cumulative amount at the top of the band (kept for display + legacy path)
    subsidy_amount = db.Column(db.Float, nullable=False, default=0.0)
    effective_from = db.Column(db.Date, nullable=False)
    effective_to = db.Column(db.Date, nullable=True)  # null = open-ended / current
    notes = db.Column(db.String(256), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "scheme_code": self.scheme_code,
            "min_kw": self.min_kw,
            "max_kw": self.max_kw,
            "amount_per_kw": self.amount_per_kw,
            "fixed_amount": self.fixed_amount,
            "subsidy_amount": self.subsidy_amount,
            "effective_from": self.effective_from.isoformat()
            if self.effective_from
            else None,
            "effective_to": self.effective_to.isoformat()
            if self.effective_to
            else None,
            "notes": self.notes,
        }

class AssistantDailyUsage(db.Model):
    """Per-user daily message counter for the AI assistant.

    Keyed by (user_id, usage_date) so counts survive process restarts and
    work across multiple app instances sharing the same database.
    """

    __tablename__ = "assistant_daily_usage"
    __table_args__ = (
        db.UniqueConstraint("user_id", "usage_date", name="uq_assistant_user_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    usage_date = db.Column(db.Date, nullable=False, index=True)
    message_count = db.Column(db.Integer, nullable=False, default=0)

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "usage_date": self.usage_date.isoformat() if self.usage_date else None,
            "message_count": self.message_count,
        }
