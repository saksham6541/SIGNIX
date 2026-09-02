# filename: app/models.py
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class UserLocation(db.Model):
    """Stores a single rooftop estimation run: the address, the drawn
    polygon, and the resulting solar estimate."""

    __tablename__ = "user_locations"

    id = db.Column(db.Integer, primary_key=True)
    address = db.Column(db.String(512), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)

    # GeoJSON polygon of the drawn rooftop, stored as JSON text (SQLite has
    # no native JSON type, SQLAlchemy's JSON type handles serialization).
    polygon_geojson = db.Column(db.JSON, nullable=True)
    # List of non-usable patches (water tanks, staircase rooms, AC units,
    # existing coverings) drawn inside the rooftop boundary, each a GeoJSON
    # polygon. Subtracted from roof_area_sqm to get usable_area_sqm.
    obstructions_geojson = db.Column(db.JSON, nullable=True)

    system_size = db.Column(db.Float, nullable=False, default=0.0)       # kWp
    annual_generation = db.Column(db.Float, nullable=False, default=0.0)  # kWh/year
    monthly_data = db.Column(db.JSON, nullable=True)                     # {"Jan": kWh, ...}

    # Cached financial figures so the report/PDF can be regenerated without
    # recomputation.
    roof_area_sqm = db.Column(db.Float, nullable=True)          # gross rooftop area
    obstructed_area_sqm = db.Column(db.Float, nullable=True)    # non-usable area subtracted
    usable_area_sqm = db.Column(db.Float, nullable=True)        # roof_area_sqm - obstructed_area_sqm
    system_cost = db.Column(db.Float, nullable=True)
    subsidy_amount = db.Column(db.Float, nullable=True)
    net_investment = db.Column(db.Float, nullable=True)
    monthly_savings = db.Column(db.Float, nullable=True)
    co2_reduction_tons = db.Column(db.Float, nullable=True)
    irradiance_source = db.Column(db.String(32), nullable=True)  # nasa_power / pvgis / mock_fallback

    # Orientation & battery
    orientation_deg = db.Column(db.Float, nullable=True)
    orientation_label = db.Column(db.String(32), nullable=True)
    orientation_factor = db.Column(db.Float, nullable=True)
    recommended_tilt_deg = db.Column(db.Float, nullable=True)
    battery_kwh = db.Column(db.Float, nullable=True, default=0.0)
    battery_cost = db.Column(db.Float, nullable=True, default=0.0)

    # Extra performance metrics (cached for report/PDF)
    specific_yield = db.Column(db.Float, nullable=True)       # kWh/kWp/year
    capacity_factor = db.Column(db.Float, nullable=True)      # %
    lcoe = db.Column(db.Float, nullable=True)                 # ₹/kWh
    lifetime_kwh = db.Column(db.Float, nullable=True)
    self_consumption_frac = db.Column(db.Float, nullable=True)
    payback_years = db.Column(db.Float, nullable=True)
    # Flexible bag for any extra numbers / labels
    extras = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "address": self.address,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "polygon_geojson": self.polygon_geojson,
            "obstructions_geojson": self.obstructions_geojson,
            "system_size": round(self.system_size, 2),
            "annual_generation": round(self.annual_generation, 2),
            "monthly_data": self.monthly_data,
            "roof_area_sqm": round(self.roof_area_sqm or 0, 2),
            "obstructed_area_sqm": round(self.obstructed_area_sqm or 0, 2),
            "usable_area_sqm": round(self.usable_area_sqm or (self.roof_area_sqm or 0), 2),
            "system_cost": round(self.system_cost or 0, 2),
            "subsidy_amount": round(self.subsidy_amount or 0, 2),
            "net_investment": round(self.net_investment or 0, 2),
            "monthly_savings": round(self.monthly_savings or 0, 2),
            "co2_reduction_tons": round(self.co2_reduction_tons or 0, 2),
            "irradiance_source": self.irradiance_source,
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
            "extras": self.extras,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TariffTable(db.Model):
    """Per-state / per-utility electricity tariff, used to convert kWh
    generated into ₹ savings."""

    __tablename__ = "tariff_table"

    id = db.Column(db.Integer, primary_key=True)
    state = db.Column(db.String(128), nullable=False)
    utility_name = db.Column(db.String(128), nullable=False)
    rate_per_kwh = db.Column(db.Float, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "state": self.state,
            "utility_name": self.utility_name,
            "rate_per_kwh": self.rate_per_kwh,
        }


class SubsidyScheme(db.Model):
    """PM Surya Ghar subsidy slabs, keyed by system size range (kW)."""

    __tablename__ = "subsidy_scheme"

    id = db.Column(db.Integer, primary_key=True)
    min_kw = db.Column(db.Float, nullable=False)
    max_kw = db.Column(db.Float, nullable=False)
    subsidy_amount = db.Column(db.Float, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "min_kw": self.min_kw,
            "max_kw": self.max_kw,
            "subsidy_amount": self.subsidy_amount,
        }
