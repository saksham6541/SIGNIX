# filename: app/solar_logic.py
"""
Core solar estimation logic:
- Polygon area calculation (local ENU + spherical excess, rooftop-accurate)
- Irradiance + temperature fetch (NASA POWER primary, PVGIS via pvlib
  secondary, mock as final fallback)
- Annual / monthly generation calculation with NASA-temperature-based
  panel derating for more accurate seasonal output
- PM Surya Ghar subsidy calculation
- Financial + environmental derived figures
"""

import hashlib
import json
import math
import requests
from app.assumptions import (
    AREA_PER_KWP,
    ANNUAL_TARIFF_ESCALATION,
    DISCOUNT_RATE,
    GRID_EMISSIONS_KG_CO2_PER_KWH,
    MODULE_WATTAGE,
    PRACTICAL_MAX_KWP,
    DEGRADATION_PCT,
    OM_PERCENT,
    PERFORMANCE_RATIO,
    SOILING_LOSS_PCT,
    assumptions_snapshot,
    get_value,
)
from app.config import Config
from app.services.cache import get_cached_irradiance, set_cached_irradiance
from app.services import (
    commercial_calculation_service,
    residential_calculation_service,
)

MONTH_NAMES = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

# Realistic-ish average daily peak sun hours per month for a generic Indian
# location (used to build mock/fallback irradiance profiles when live APIs
# are unreachable). Values are kWh/m^2/day.
MOCK_MONTHLY_PSH = [5.2, 5.8, 6.2, 6.6, 6.4, 5.0, 4.2, 4.3, 4.8, 5.3, 5.0, 4.8]

CALCULATION_MODEL_VERSION = "2.0"
FINANCIAL_MODEL_VERSION = "2.0"
PVGIS_PROFILE_KIND = "monthly_specific_yield_kwh_per_kwp"


def calculation_assumptions():
    """Return the complete, serializable input set that controls saved results."""
    return {
        "calculation_model_version": CALCULATION_MODEL_VERSION,
        "financial_model_version": FINANCIAL_MODEL_VERSION,
        "financial_model": {
            "projection_years": 25,
            "residential_cost_per_kw": Config.SYSTEM_COST_PER_KW,
            "commercial_cost_per_kw": getattr(
                Config, "COMMERCIAL_COST_PER_KW", 42000
            ),
            "battery_cost_per_kwh": getattr(
                Config, "BATTERY_COST_PER_KWH", 9000
            ),
            "subsidy_policy": {
                "scope": "residential_grid_connected",
                "first_2_kw_rate": 30000,
                "third_kw_rate": 18000,
                "maximum": 78000,
            },
            "self_consumption_presets": {
                "day_heavy": 0.70,
                "flat": 0.55,
                "evening_heavy": 0.40,
            },
            "track_policies": {
                "home": residential_calculation_service.policy_snapshot(),
                "business": commercial_calculation_service.policy_snapshot(),
            },
        },
        "assumptions": assumptions_snapshot(),
        "temperature_model": {
            "coefficient_per_celsius": -0.004,
            "cell_temperature_rise_celsius_at_6_psh": 20.0,
            "minimum_temperature_factor": 0.75,
        },
        "pvgis": {
            "endpoint": Config.PVGIS_URL,
            "configured_system_loss_percent": 14.0,
            "pv_technology": "crystSi",
            "mounting_place": "building",
            "profile_kind": PVGIS_PROFILE_KIND,
        },
        "nasa_power": {
            "endpoint": Config.NASA_POWER_URL,
            "parameters": ["ALLSKY_SFC_SW_DWN", "T2M"],
            "temporal_model": "monthly_climatology",
        },
    }


def current_assumption_fingerprint():
    payload = json.dumps(calculation_assumptions(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generation_provenance_for_result(result):
    """Build persisted provenance without widening the established API result."""
    source = result.get("irradiance_source")
    pvgis = source == "pvgis"
    surface_generation = result.get("surface_generation") or {}
    surfaces = surface_generation.get("surfaces") or []
    location_loss_model_used = (
        not surfaces
        or any(
            surface.get("generation_model")
            == "location_irradiance_azimuth_adjustment"
            for surface in surfaces
        )
    )
    return {
        "source": source,
        "profile_kind": (
            PVGIS_PROFILE_KIND
            if pvgis else "daily_horizontal_irradiance_kwh_per_m2"
        ),
        "profile_units": (
            "kWh_per_kWp_per_month" if pvgis else "kWh_per_m2_per_day"
        ),
        "performance_ratio_applied": not pvgis and location_loss_model_used,
        "performance_ratio": (
            result.get("performance_ratio")
            if not pvgis and location_loss_model_used else None
        ),
        "temperature_derating_applied": bool(
            result.get("temperature_derating_applied") and location_loss_model_used
        ),
        "orientation_adjustment_applied": not pvgis and location_loss_model_used,
        "provider_system_loss_percent": 14.0 if pvgis else None,
        "monthly_annual_reconciled": (
            round(sum((result.get("monthly_data") or {}).values()), 1)
            == result.get("annual_generation")
        ),
    }


def _normalize_ring(coordinates):
    """Clean a lat/lng ring: drop invalid points, remove duplicate close, ensure open ring."""
    pts = []
    for c in coordinates or []:
        if c is None or len(c) < 2:
            continue
        lat, lng = float(c[0]), float(c[1])
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        if pts and abs(pts[-1][0] - lat) < 1e-12 and abs(pts[-1][1] - lng) < 1e-12:
            continue
        pts.append((lat, lng))
    if (
        len(pts) >= 2
        and abs(pts[0][0] - pts[-1][0]) < 1e-12
        and abs(pts[0][1] - pts[-1][1]) < 1e-12
    ):
        pts = pts[:-1]
    return pts


def _area_local_tangent_sqm(pts):
    """Local ENU (east-north) projection about the ring centroid + shoelace.

    Very accurate for rooftop-scale polygons (metres to a few hundred metres)
    because curvature is negligible over that distance.
    Uses WGS84 mean radius and centroid latitude for the east-scale factor.
    """
    R = 6371008.8  # WGS84 authalic / mean radius (m)
    n = len(pts)
    lat0 = math.radians(sum(p[0] for p in pts) / n)
    lon0 = math.radians(sum(p[1] for p in pts) / n)
    cos_lat0 = math.cos(lat0)

    projected = []
    for lat, lng in pts:
        dlat = math.radians(lat) - lat0
        dlon = math.radians(lng) - lon0
        east = dlon * R * cos_lat0
        north = dlat * R
        projected.append((east, north))

    area = 0.0
    for i in range(n):
        x1, y1 = projected[i]
        x2, y2 = projected[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _area_spherical_excess_sqm(pts):
    """Spherical polygon area via unit-vector spherical excess (Girard).

    More accurate on larger polygons; for rooftops it should match the local
    tangent method within a fraction of a percent. Independent of projection.
    """
    R = 6371008.8
    n = len(pts)
    if n < 3:
        return 0.0

    def to_unit(lat, lng):
        phi = math.radians(lat)
        lam = math.radians(lng)
        cos_phi = math.cos(phi)
        return (
            cos_phi * math.cos(lam),
            cos_phi * math.sin(lam),
            math.sin(phi),
        )

    vecs = [to_unit(lat, lng) for lat, lng in pts]

    # Sum of angles at each vertex from consecutive great-circle planes
    total = 0.0
    for i in range(n):
        a = vecs[(i - 1) % n]
        b = vecs[i]
        c = vecs[(i + 1) % n]

        # Tangents in the plane of the sphere at b
        def cross(u, v):
            return (
                u[1] * v[2] - u[2] * v[1],
                u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0],
            )

        def norm(u):
            s = math.sqrt(u[0] * u[0] + u[1] * u[1] + u[2] * u[2])
            return (u[0] / s, u[1] / s, u[2] / s) if s > 0 else u

        def dot(u, v):
            return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]

        # Plane normals for arcs a-b and b-c
        n1 = cross(a, b)
        n2 = cross(b, c)
        nn1 = math.sqrt(n1[0] ** 2 + n1[1] ** 2 + n1[2] ** 2)
        nn2 = math.sqrt(n2[0] ** 2 + n2[1] ** 2 + n2[2] ** 2)
        if nn1 < 1e-15 or nn2 < 1e-15:
            continue
        n1 = (n1[0] / nn1, n1[1] / nn1, n1[2] / nn1)
        n2 = (n2[0] / nn2, n2[1] / nn2, n2[2] / nn2)

        # Interior angle from oriented normals
        sin_ang = dot(b, cross(n1, n2))
        cos_ang = -dot(n1, n2)  # exterior/interior convention
        angle = math.atan2(sin_ang, cos_ang)
        total += angle

    # Spherical excess E = total − (n−2)π  ; area = R² |E|
    excess = total - (n - 2) * math.pi
    # Normalize to principal value for small polygons
    while excess > math.pi:
        excess -= 2 * math.pi
    while excess < -math.pi:
        excess += 2 * math.pi
    return abs(excess) * R * R


def calculate_polygon_area_sqm(coordinates):
    """
    Precise area of a lat/lng polygon in square metres.

    coordinates: list of [lat, lng] pairs (Leaflet.draw order).

    Method:
      1. Clean the ring (dedupe, drop close point).
      2. Primary: local ENU tangent-plane shoelace (best for rooftop scale).
      3. Cross-check: spherical excess; if the two differ by >2%, prefer the
         spherical result (rare for small roofs; helps odd geometries).

    Typical rooftop error vs naive lat/lng shoelace without cos(lat) can be
    several percent in India (~25–30°N); this keeps error well under 1%.
    """
    pts = _normalize_ring(coordinates)
    if len(pts) < 3:
        return 0.0

    local = _area_local_tangent_sqm(pts)
    try:
        spherical = _area_spherical_excess_sqm(pts)
    except Exception:
        spherical = local

    if local <= 0:
        return round(max(spherical, 0.0), 2)
    if spherical <= 0:
        return round(local, 2)

    # Prefer local for small areas; blend if they disagree strongly
    rel = abs(local - spherical) / max(local, spherical)
    if rel > 0.02 and spherical > 0:
        # Unusual geometry — trust spherical excess
        return round(spherical, 2)
    return round(local, 2)


def area_to_system_size(area_sqm):
    """1 kWp per area_per_kwp sq. meters (versioned assumption)."""
    return round(area_sqm / get_value("area_per_kwp", AREA_PER_KWP.value), 2)


def calculate_usable_area(roof_area_sqm, obstruction_coordinates_list):
    """
    Subtracts obstruction polygons (water tanks, staircase rooms, AC units,
    existing coverings, etc.) from the gross rooftop area to get the area
    actually usable for panels.

    obstruction_coordinates_list: list of polygons, each a list of [lat,lng]
    pairs (same format as the main rooftop polygon).
    Returns (usable_area_sqm, obstructed_area_sqm).
    """
    obstructed_area_sqm = 0.0
    for obstruction in obstruction_coordinates_list or []:
        obstructed_area_sqm += calculate_polygon_area_sqm(obstruction)

    usable_area_sqm = max(roof_area_sqm - obstructed_area_sqm, 0.0)
    return usable_area_sqm, obstructed_area_sqm


def _mock_irradiance_profile(latitude, longitude):
    """Deterministic coordinate-aware mock irradiance for offline use.

    This is deliberately a rough estimate, not a climate model. Latitude
    adjusts the annual level by latitude band and longitude adds a small,
    deterministic regional variation so nearby points do not all share one
    national profile.
    """
    hemisphere_shift = 0 if latitude >= 0 else 6  # flip seasons below equator
    profile = MOCK_MONTHLY_PSH[hemisphere_shift:] + MOCK_MONTHLY_PSH[:hemisphere_shift]
    latitude_factor = max(0.82, 1.08 - 0.0035 * abs(latitude))
    longitude_factor = 1.0 + 0.04 * math.sin(math.radians(longitude))
    location_factor = latitude_factor * longitude_factor
    return {MONTH_NAMES[i]: round(profile[i] * location_factor, 2) for i in range(12)}


def classify_irradiance_source(source):
    """Map a fetch source tag to (normalized_source, confidence_tier).

    Normalized sources exposed to API/UI/PDF:
      nasa_power | pvgis | mock
    Confidence:
      high   — NASA POWER or PVGIS (good coverage)
      medium — reserved (e.g. partial coverage); not used in v1 pipeline
      low    — mock / offline fallback

    Internal pipeline may still emit ``mock_fallback``; it normalizes to ``mock``.
    """
    if not source:
        return "mock", "low"
    key = str(source).strip().lower()
    if key in ("nasa_power", "nasa"):
        return "nasa_power", "high"
    if key in ("pvgis",):
        return "pvgis", "high"
    if key in ("mock", "mock_fallback", "estimated", "offline"):
        return "mock", "low"
    # Unknown / test fixtures: treat as low confidence, keep original tag as source-ish
    if key in ("test_fixture", "test"):
        return key, "low"
    return key, "low"


def irradiance_confidence_tier(source):
    """Return confidence tier only ('high' | 'medium' | 'low')."""
    _, tier = classify_irradiance_source(source)
    return tier



def fetch_irradiance_nasa_power(latitude, longitude):
    """
    Primary data source: NASA POWER climatology API. Pulls both
    solar irradiance (ALLSKY_SFC_SW_DWN) and 2m air temperature (T2M) in a
    single request, since temperature drives the panel-temperature
    derating used in calculate_monthly_generation for a more accurate
    yield estimate than irradiance alone.

    Returns (irradiance_profile, temperature_profile, source) where each
    profile is {"Jan": value, ...}. Falls back to (None, None, None) on
    any failure so the caller can try PVGIS, then mock data.
    """
    try:
        params = {
            "parameters": "ALLSKY_SFC_SW_DWN,T2M",
            "community": "RE",
            "longitude": longitude,
            "latitude": latitude,
            "format": "JSON",
        }
        resp = requests.get(
            Config.NASA_POWER_URL, params=params, timeout=Config.REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        params_data = data["properties"]["parameter"]
        irr_raw = params_data["ALLSKY_SFC_SW_DWN"]
        temp_raw = params_data.get("T2M", {})

        irradiance = {
            MONTH_NAMES[i]: round(irr_raw[f"{i+1:02d}"], 2)
            for i in range(12)
            if f"{i+1:02d}" in irr_raw
        }
        temperature = (
            {
                MONTH_NAMES[i]: round(temp_raw[f"{i+1:02d}"], 1)
                for i in range(12)
                if f"{i+1:02d}" in temp_raw
            }
            if temp_raw
            else None
        )

        if len(irradiance) == 12:
            return irradiance, temperature, "nasa_power"
    except Exception:
        pass
    return None, None, None


def fetch_irradiance_pvgis(latitude, longitude, peak_power_kw=1.0):
    """
    Secondary source: pvlib/PVGIS. Used only if NASA POWER is unreachable.
    Falls back to mock data on any failure so the prototype always works
    offline.
    Returns PVGIS's modeled monthly PV energy normalized by requested DC
    capacity: {"Jan": kWh/kWp/month, ...}, source. This is not irradiance and
    must not receive another SIGNIX PR, temperature, or orientation derate.
    """
    try:
        import pvlib  # noqa: F401 - kept for the PVGIS schema/units it documents

        params = {
            "lat": latitude,
            "lon": longitude,
            "peakpower": peak_power_kw,
            "loss": 14,  # system losses %, PVGIS default assumption
            "outputformat": "json",
            "pvtechchoice": "crystSi",
            "mountingplace": "building",
        }
        resp = requests.get(
            Config.PVGIS_URL, params=params, timeout=Config.REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        monthly = data["outputs"]["monthly"]["fixed"]
        result = {}
        for row in monthly:
            month_idx = int(row["month"]) - 1
            specific_yield = row["E_m"] / peak_power_kw
            result[MONTH_NAMES[month_idx]] = round(specific_yield, 3)
        return result, "pvgis"
    except Exception:
        pass

    return _mock_irradiance_profile(latitude, longitude), "mock_fallback"


def fetch_solar_data(latitude, longitude, peak_power_kw=1.0):
    """
    Combined fetch pipeline, NASA POWER first:
      1. NASA POWER (irradiance + temperature -> best accuracy, used for
         both the irradiance profile and the temperature-derating term)
      2. PVGIS via pvlib (irradiance only, no temperature derating applied)
      3. Deterministic mock profile (fully offline fallback)

    Returns
    -------
    (irradiance_profile, temperature_profile_or_None, source, confidence_tier)

    source: ``nasa_power`` | ``pvgis`` | ``mock_fallback`` (pipeline tag;
            callers may normalize via ``classify_irradiance_source``)
    confidence_tier: ``high`` | ``medium`` | ``low``
    """
    cached = get_cached_irradiance(latitude, longitude)
    if (
        cached is not None
        and cached.get("source") == "pvgis"
        and cached.get("profile_kind") != PVGIS_PROFILE_KIND
    ):
        # Pre-v2 cache entries contain synthetic daily PSH derived from E_m.
        # They cannot be interpreted safely under the corrected unit contract.
        cached = None
    if cached is not None:
        source = cached["source"]
        _, tier = classify_irradiance_source(source)
        print(
            f"[CACHE] irradiance hit lat={latitude:.4f} lng={longitude:.4f} "
            f"source={source} confidence={tier}"
        )
        return cached["irradiance"], cached["temperature"], source, tier

    irradiance, temperature, source = fetch_irradiance_nasa_power(latitude, longitude)
    if irradiance:
        _, tier = classify_irradiance_source(source)
        set_cached_irradiance(
            latitude,
            longitude,
            {
                "irradiance": irradiance,
                "temperature": temperature,
                "source": source,
                "profile_kind": "daily_horizontal_irradiance_kwh_per_m2",
                "schema_version": CALCULATION_MODEL_VERSION,
            },
            ttl_days=30,
        )
        return irradiance, temperature, source, tier

    irradiance, source = fetch_irradiance_pvgis(latitude, longitude, peak_power_kw)
    _, tier = classify_irradiance_source(source)
    cached_data = {
        "irradiance": irradiance,
        "temperature": None,
        "source": source,
        "profile_kind": (
            PVGIS_PROFILE_KIND
            if source == "pvgis"
            else "daily_horizontal_irradiance_kwh_per_m2"
        ),
        "schema_version": CALCULATION_MODEL_VERSION,
    }
    if source == "mock_fallback":
        set_cached_irradiance(latitude, longitude, cached_data, ttl_hours=1)
    else:
        set_cached_irradiance(latitude, longitude, cached_data, ttl_days=30)
    return irradiance, None, source, tier



def calculate_monthly_generation(
    system_size_kw,
    irradiance_profile,
    temperature_profile=None,
    performance_ratio=None,
):
    """Monthly generation (kWh) = system_size(kWp) * PSH(kWh/m2/day) *
    days_in_month * effective_performance_ratio.

    When a temperature profile is available (NASA POWER's T2M), applies a
    standard crystalline-silicon temperature derating on top of the base
    performance ratio: panel output drops ~0.4%/°C above 25°C ambient
    (a widely used industry coefficient), giving a more accurate monthly
    split than a single flat performance ratio - e.g. hotter summer months
    yield slightly less per unit of irradiance than cooler months.

    Default performance_ratio comes from versioned assumptions. soiling_loss_pct
    is a placeholder (0.0) so outputs match the historical single 0.80 PR.
    """
    if performance_ratio is None:
        performance_ratio = get_value(
            "performance_ratio", PERFORMANCE_RATIO.value
        ) * (1.0 - get_value("soiling_loss_pct", SOILING_LOSS_PCT.value))

    days_in_month = {
        "Jan": 31,
        "Feb": 28,
        "Mar": 31,
        "Apr": 30,
        "May": 31,
        "Jun": 30,
        "Jul": 31,
        "Aug": 31,
        "Sep": 30,
        "Oct": 31,
        "Nov": 30,
        "Dec": 31,
    }
    temp_coefficient = -0.004  # -0.4% per °C above 25°C (crystalline silicon)
    monthly_kwh = {}
    for month in MONTH_NAMES:
        psh = irradiance_profile.get(month, 5.0)
        pr = performance_ratio
        if temperature_profile and month in temperature_profile:
            ambient_temp = temperature_profile[month]
            # Rough cell-temp estimate: ambient + irradiance-driven rise
            # (NOCT-style approximation, ~20C rise at ~800 W/m2 equivalent).
            estimated_cell_temp = ambient_temp + (psh / 6.0) * 20
            derate = 1 + temp_coefficient * max(estimated_cell_temp - 25, 0)
            pr = performance_ratio * max(
                derate, 0.75
            )  # floor to avoid unrealistic drops
        monthly_kwh[month] = round(system_size_kw * psh * days_in_month[month] * pr, 1)
    return monthly_kwh


def calculate_monthly_generation_from_specific_yield(
    system_size_kw, monthly_specific_yield
):
    """Convert provider-modeled kWh/kWp/month to monthly system energy once."""
    capacity = float(system_size_kw)
    if not math.isfinite(capacity) or capacity < 0:
        raise ValueError("system_size_kw must be finite and non-negative")
    result = {}
    for month in MONTH_NAMES:
        try:
            value = float(monthly_specific_yield[month])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"monthly specific yield is missing {month}") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"monthly specific yield for {month} must be non-negative")
        result[month] = round(capacity * value, 1)
    return result


def _default_pm_surya_ghar_slabs():
    """Central PM Surya Ghar slabs (Config defaults) as rate + cumulative helpers.

    Matches seed in scripts/seed_subsidy_scheme.py / run.py:
      0–2 kW → ₹30,000/kW  (₹60,000 at 2 kW)
      2–3 kW → +₹18,000/kW (₹78,000 at 3 kW)
      3 kW+  → capped at ₹78,000
    """
    first2 = float(getattr(Config, "SUBSIDY_PER_KW_FIRST_2", 30000.0))
    extra = float(getattr(Config, "SUBSIDY_EXTRA_2_TO_3", 18000.0))
    cap = float(getattr(Config, "SUBSIDY_CAP", 78000.0))
    return [
        {
            "min_kw": 0.0,
            "max_kw": 2.0,
            "amount_per_kw": first2,
            "fixed_amount": None,
            "subsidy_amount": 2.0 * first2,
        },
        {
            "min_kw": 2.0,
            "max_kw": 3.0,
            "amount_per_kw": extra,
            "fixed_amount": None,
            "subsidy_amount": min(2.0 * first2 + extra, cap),
        },
        {
            "min_kw": 3.0,
            "max_kw": 1000.0,
            "amount_per_kw": 0.0,
            "fixed_amount": cap,
            "subsidy_amount": cap,
        },
    ]


def _load_pm_surya_ghar_slabs(as_of=None):
    """Load the active central PM Surya Ghar slab set from SubsidyScheme.

    Picks the revision whose ``effective_from`` is the most recent date on or
    before *as_of* (default: today). Rows with ``effective_to`` before *as_of*
    are ignored. Falls back to Config defaults if the table is empty / unavailable.

    Scope: central PM Surya Ghar only (no state top-ups).
    """
    from datetime import date as date_cls

    if as_of is None:
        as_of = date_cls.today()
    elif hasattr(as_of, "date"):
        as_of = as_of.date()

    try:
        from app.models import SubsidyScheme

        candidates = (
            SubsidyScheme.query.filter(
                SubsidyScheme.scheme_code == "pm_surya_ghar",
                SubsidyScheme.effective_from <= as_of,
            )
            .order_by(SubsidyScheme.effective_from.desc())
            .all()
        )
        if not candidates:
            legacy = SubsidyScheme.query.order_by(
                SubsidyScheme.min_kw.asc(), SubsidyScheme.max_kw.asc()
            ).all()
            if legacy and all(
                getattr(r, "effective_from", None) is None for r in legacy
            ):
                return [
                    {
                        "min_kw": float(row.min_kw),
                        "max_kw": float(row.max_kw),
                        "amount_per_kw": getattr(row, "amount_per_kw", None),
                        "fixed_amount": getattr(row, "fixed_amount", None),
                        "subsidy_amount": float(row.subsidy_amount or 0),
                    }
                    for row in legacy
                ]
            return _default_pm_surya_ghar_slabs()

        latest_from = None
        for row in candidates:
            end = getattr(row, "effective_to", None)
            if end is not None and end < as_of:
                continue
            latest_from = row.effective_from
            break

        if latest_from is None:
            return _default_pm_surya_ghar_slabs()

        active = [
            row
            for row in candidates
            if row.effective_from == latest_from
            and (
                getattr(row, "effective_to", None) is None
                or row.effective_to >= as_of
            )
        ]
        if not active:
            return _default_pm_surya_ghar_slabs()

        return [
            {
                "min_kw": float(row.min_kw),
                "max_kw": float(row.max_kw),
                "amount_per_kw": (
                    float(row.amount_per_kw)
                    if getattr(row, "amount_per_kw", None) is not None
                    else None
                ),
                "fixed_amount": (
                    float(row.fixed_amount)
                    if getattr(row, "fixed_amount", None) is not None
                    else None
                ),
                "subsidy_amount": float(row.subsidy_amount or 0),
            }
            for row in sorted(active, key=lambda r: (r.min_kw, r.max_kw))
        ]
    except Exception:
        pass
    return _default_pm_surya_ghar_slabs()


def _subsidy_amount_from_slabs(system_size_kw, slabs):
    """Compute subsidy from ordered slabs (prefer amount_per_kw; else cumulative)."""
    size = max(0.0, float(system_size_kw or 0))
    if size <= 0 or not slabs:
        return 0.0

    ordered = sorted(slabs, key=lambda s: (s["min_kw"], s["max_kw"]))
    has_rates = any(s.get("amount_per_kw") is not None for s in ordered)

    if has_rates:
        amount = 0.0
        cap = None
        for slab in ordered:
            band_start = float(slab["min_kw"])
            band_end = float(slab["max_kw"])
            if size <= band_start:
                break
            portion = min(size, band_end) - band_start
            if portion <= 0:
                continue
            rate = slab.get("amount_per_kw")
            if rate is not None:
                amount += portion * float(rate)
            if slab.get("fixed_amount") is not None:
                cap = float(slab["fixed_amount"])
        if cap is not None:
            amount = min(amount, cap)
        cum_caps = [
            float(s["subsidy_amount"])
            for s in ordered
            if s.get("subsidy_amount") is not None
        ]
        if cum_caps:
            amount = min(amount, max(cum_caps))
        return round(amount, 2)

    prev_amount = 0.0
    for slab in ordered:
        band_start = float(slab["min_kw"])
        band_end = float(slab["max_kw"])
        end_amount = float(slab.get("subsidy_amount") or 0)
        if size <= band_end:
            span = band_end - band_start
            if span <= 0:
                return round(end_amount, 2)
            frac = (size - band_start) / span
            frac = max(0.0, min(1.0, frac))
            return round(prev_amount + frac * (end_amount - prev_amount), 2)
        prev_amount = end_amount
    return round(prev_amount, 2)


def calculate_subsidy(
    system_size_kw, inverter_type="on-grid", property_type="residential"
):
    """PM Surya Ghar central assistance (published structure):

      • ₹30,000 / kW for the first 2 kW
      • ₹18,000 for the additional kW from 2–3 kW
      • Maximum ₹78,000

    Example: 1 kW → 30k; 2 kW → 60k; 2.5 kW → 60k + 0.5×18k = 69k; 3 kW+ → 78k.

    Slabs are read from SubsidyScheme for the active revision (most recent
    effective_from on or before today). Config defaults are used if the table
    is empty. Central PM Surya Ghar only — no state top-ups.

    Eligibility (planning simplification):
      • Residential + On-grid / Hybrid → eligible (DISCOM/vendor conditions apply)
      • Off-grid → usually not eligible
      • Commercial / MSME / industrial → PM Surya Ghar residential subsidy generally N/A
    """
    itype = (inverter_type or "on-grid").lower().replace(" ", "-")
    prop = (property_type or "residential").lower()

    if itype in ("off-grid", "offgrid"):
        return 0.0
    if prop in ("commercial", "industrial", "msme", "factory", "agricultural"):
        return 0.0

    size = max(0.0, float(system_size_kw or 0))
    if size <= 0:
        return 0.0

    slabs = _load_pm_surya_ghar_slabs()
    return _subsidy_amount_from_slabs(size, slabs)



def size_from_monthly_bill(monthly_bill_inr, tariff_per_kwh=None):
    """Estimate required solar size from average monthly electricity bill.

    Energy path (guide):
      PV kWp ≈ daily AC energy ÷ (peak sun hours × performance ratio)
    Bill path (quick India rule of thumb):
      units/month ≈ bill ÷ tariff (default ₹10/unit)
      kWp ≈ units ÷ 135  (~4.5 units/day per kWp)

    Indicative PM Surya Ghar planning bands (by monthly units):
      0–150 → 1–2 kW | 150–300 → 2–3 kW | >300 → above 3 kW
    """
    tariff = float(
        tariff_per_kwh if tariff_per_kwh is not None else Config.DEFAULT_TARIFF
    )
    units_per_kw = float(getattr(Config, "MONTHLY_UNITS_PER_KW", 135.0))
    psh = float(getattr(Config, "DEFAULT_PEAK_SUN_HOURS", 5.0))
    pr = float(
        get_value("performance_ratio", getattr(Config, "DEFAULT_PERFORMANCE_RATIO", 0.80))
    )
    bill = max(0.0, float(monthly_bill_inr or 0))

    monthly_units = round(bill / tariff, 1) if tariff > 0 else 0.0
    daily_units = round(monthly_units / 30.0, 2)
    raw_kw = monthly_units / units_per_kw if units_per_kw > 0 else 0.0
    # Energy-model cross-check
    energy_kw = daily_units / (psh * pr) if (psh * pr) > 0 else raw_kw
    # Blend: prefer bill rule-of-thumb, note energy model
    design_kw = round((raw_kw * 0.6 + energy_kw * 0.4), 2)

    # Guide bands by monthly units + practical rounding
    if monthly_units <= 0:
        rec_min, rec_max, midpoint = 0.0, 0.0, 0.0
    elif monthly_units <= 150:
        rec_min, rec_max = 1.0, 2.0
        midpoint = min(max(design_kw, 1.0), 2.0)
    elif monthly_units <= 300:
        rec_min, rec_max = 2.0, 3.0
        midpoint = min(max(design_kw, 2.0), 3.0)
    elif monthly_units <= 500:
        rec_min, rec_max = 3.5, 4.0
        midpoint = round(max(design_kw, 3.5), 1)
    elif monthly_units <= 800:
        rec_min, rec_max = 5.5, 6.0
        midpoint = round(max(design_kw, 5.5), 1)
    elif monthly_units <= 1200:
        rec_min, rec_max = 8.0, 9.0
        midpoint = round(max(design_kw, 8.0), 1)
    else:
        rec_min, rec_max = 12.0, max(12.0, round(design_kw, 0))
        midpoint = round(max(design_kw, 12.0), 1)

    return {
        "monthly_bill": bill,
        "tariff_per_kwh": tariff,
        "monthly_units": monthly_units,
        "daily_units": daily_units,
        "raw_kw": round(raw_kw, 2),
        "energy_model_kw": round(energy_kw, 2),
        "peak_sun_hours": psh,
        "performance_ratio": pr,
        "recommended_min_kw": rec_min,
        "recommended_max_kw": rec_max,
        "recommended_kw": midpoint,
        "monthly_units_per_kw": units_per_kw,
        "sizing_note": (
            "Preliminary only. Final design must check monthly production, roof layout, "
            "inverter limits, shade, export/net-metering and DISCOM rules."
        ),
    }


# ---------------------------------------------------------------------------
# Structured multi-mode system sizing
# ---------------------------------------------------------------------------

SIZING_MODES = (
    "roof_max",
    "bill_offset_100%",  # also bill_offset_X% for other percentages
    "fastest_payback",
    "max_25yr_savings",
    # "export_limited",  # reserved
)


def normalize_sizing_mode(sizing_mode, monthly_bill=None):
    """Map user/API mode strings to a canonical mode key.

    Default preserves legacy behaviour: bill present → bill_offset_100%,
    otherwise roof_max.
    """
    if sizing_mode is None or str(sizing_mode).strip() == "":
        if monthly_bill is not None and float(monthly_bill or 0) > 0:
            return "bill_offset_100%"
        return "roof_max"

    key = str(sizing_mode).strip().lower().replace(" ", "_")
    if key in ("roof", "roof_max", "roof_area", "max_roof"):
        return "roof_max"
    if key in ("fastest_payback", "payback", "min_payback"):
        return "fastest_payback"
    if key in ("max_25yr_savings", "max_savings", "lifetime_savings"):
        return "max_25yr_savings"
    if key.startswith("bill_offset_"):
        return key if key.endswith("%") else f"{key}%"
    if key in ("bill", "bill_offset", "bill_capped_by_roof"):
        return "bill_offset_100%"
    return key


def _parse_bill_offset_pct(mode):
    """Extract offset fraction from bill_offset_X% (default 1.0)."""
    if not mode.startswith("bill_offset_"):
        return 1.0
    raw = mode[len("bill_offset_") :].rstrip("%")
    try:
        pct = float(raw)
    except ValueError:
        return 1.0
    return max(0.0, min(pct / 100.0, 1.5))  # allow slight oversize up to 150%


def compute_system_sizing(
    usable_area_sqm,
    monthly_bill=None,
    tariff_per_kwh=None,
    sizing_mode=None,
    budget_inr=None,
    min_system_kw=0.5,
    planning_track="home",
    property_type="residential",
):
    """Multi-mode system sizing with structured rationale and constraints.

    Parameters
    ----------
    usable_area_sqm : float
        Usable roof area after obstruction subtraction.
    monthly_bill : float, optional
        Average monthly electricity bill (INR).
    tariff_per_kwh : float, optional
        Tariff used for bill→units conversion.
    sizing_mode : str, optional
        One of: roof_max, bill_offset_X% (e.g. bill_offset_100%),
        fastest_payback, max_25yr_savings. Default preserves legacy behaviour.
    budget_inr : float, optional
        Optional hard budget cap (INR, pre-subsidy system cost basis).
    min_system_kw : float
        Practical floor (default 0.5 kW).

    Returns
    -------
    dict
        {
          "mode": str,
          "min_kw": float,
          "recommended_kw": float,
          "max_kw": float,
          "rationale": [str, ...],
          "constraints": [str, ...],
          "roof_capacity_kw": float,
          "bill_sizing": dict | None,
        }
    """
    area = max(0.0, float(usable_area_sqm or 0))
    # The practical minimum is a recommendation floor, not fictitious roof
    # capacity. A zero or undersized roof must never be reported as supporting
    # 0.5 kWp merely because that is the normal product minimum.
    roof_capacity_kw = area_to_system_size(area)
    mode = normalize_sizing_mode(sizing_mode, monthly_bill=monthly_bill)

    bill_sizing = None
    if monthly_bill is not None and float(monthly_bill or 0) > 0:
        bill_sizing = size_from_monthly_bill(monthly_bill, tariff_per_kwh)

    rationale = []
    constraints = []
    # Physical bounds
    max_kw = round(roof_capacity_kw, 2)
    practical_cap = get_value("practical_max_kwp", PRACTICAL_MAX_KWP.value)
    if max_kw > practical_cap:
        max_kw = round(practical_cap, 2)
    min_kw = round(float(min_system_kw), 2)
    constraints.append("Roof area")

    if planning_track == "business":
        sizing_policy = commercial_calculation_service.resolve_policy()
    else:
        sizing_policy = residential_calculation_service.resolve_policy(property_type)
    cost_per_kw = float(sizing_policy["cost_per_kw"])
    budget_cap_kw = None
    if budget_inr is not None and float(budget_inr or 0) > 0 and cost_per_kw > 0:
        budget_cap_kw = float(budget_inr) / cost_per_kw
        max_kw = round(min(max_kw, budget_cap_kw), 2)
        constraints.append("Stated budget")

    if mode == "roof_max":
        recommended = max_kw
        min_kw = round(min(min_kw, max_kw), 2)
        if roof_capacity_kw > practical_cap + 1e-6:
            constraints.append(f"Practical sizing cap ({practical_cap:.0f} kWp)")
            rationale.append(
                f"Roof could hold ~{roof_capacity_kw:.1f} kWp; "
                f"planning max capped at {practical_cap:.0f} kWp"
            )
        rationale.append(
            f"Sized to roof area limit of {area:.0f} m² "
            f"(~{min(roof_capacity_kw, practical_cap):.2f} kWp usable capacity)"
        )
        if budget_cap_kw is not None and recommended >= max_kw and max_kw < roof_capacity_kw:
            rationale.append("Capped by your stated budget")

    elif mode.startswith("bill_offset_"):
        frac = _parse_bill_offset_pct(mode)
        pct_label = int(round(frac * 100))
        if bill_sizing and bill_sizing.get("recommended_kw", 0) > 0:
            target = round(float(bill_sizing["recommended_kw"]) * frac, 2)
            recommended = round(min(target, max_kw), 2)
            recommended = max(recommended, min_kw) if max_kw >= min_kw else max_kw
            # Band from bill helper, scaled and roof-capped
            band_min = float(bill_sizing.get("recommended_min_kw") or target) * frac
            band_max = float(bill_sizing.get("recommended_max_kw") or target) * frac
            min_kw = round(max(min_kw, min(band_min, max_kw)), 2)
            # max_kw stays roof/budget; expose bill band top when lower
            if band_max < max_kw:
                # Keep physical max_kw as roof; recommended stays bill-derived
                pass
            rationale.append(
                f"Sized to offset ~{pct_label}% of monthly bill "
                f"(~{bill_sizing.get('monthly_units', 0)} units/month)"
            )
            if target > max_kw:
                reason = (
                    f"practical {practical_cap:.0f} kWp planning cap"
                    if roof_capacity_kw > practical_cap + 1e-6 and abs(max_kw - practical_cap) < 1e-6
                    else "roof capacity"
                )
                rationale.append(
                    f"Bill target {target:.2f} kW exceeds {reason}; "
                    f"capped at {max_kw:.2f} kW"
                )
                constraints.append("Roof area" if "roof" in reason else f"Practical sizing cap ({practical_cap:.0f} kWp)")
        else:
            recommended = max_kw
            rationale.append(
                "No monthly bill provided — fell back to roof-max sizing"
            )
            mode = "roof_max"

    elif mode == "fastest_payback":
        # Residential sizing may use the subsidy ceiling. Commercial sizing
        # has no such incentive and follows bill need (or roof capacity).
        if planning_track == "business":
            if bill_sizing and bill_sizing.get("recommended_kw", 0) > 0:
                target = float(bill_sizing["recommended_kw"])
                rationale.append(
                    f"Commercial fastest-payback preview follows bill-derived need (~{target:.2f} kW)"
                )
            else:
                target = roof_capacity_kw
                rationale.append(
                    "No bill supplied; commercial fastest-payback preview falls back to roof capacity"
                )
            recommended = round(min(target, max_kw), 2)
            recommended = max(recommended, min_kw) if max_kw >= min_kw else max_kw
        else:
            # Bias toward subsidy-efficient sizes (≤3 kW CFA ceiling) while
            # respecting bill need and roof limit.
            subsidy_sweet_spot = 3.0
            if bill_sizing and bill_sizing.get("recommended_kw", 0) > 0:
                target = min(float(bill_sizing["recommended_kw"]), subsidy_sweet_spot)
                rationale.append(
                    "Prefer size near PM Surya Ghar subsidy ceiling (3 kW) for faster payback"
                )
                rationale.append(
                    f"Bill-derived need ~{bill_sizing['recommended_kw']:.2f} kW"
                )
            else:
                target = min(roof_capacity_kw, subsidy_sweet_spot)
                rationale.append(
                    "Prefer size near PM Surya Ghar subsidy ceiling (3 kW) for faster payback"
                )
            recommended = round(min(target, max_kw), 2)
            recommended = max(recommended, min_kw) if max_kw >= min_kw else max_kw
            if roof_capacity_kw > subsidy_sweet_spot and recommended <= subsidy_sweet_spot:
                constraints.append("Subsidy efficiency (3 kW CFA ceiling)")

    elif mode == "max_25yr_savings":
        # Under flat-tariff / unlimited export assumption, larger system → more savings.
        recommended = max_kw
        rationale.append(
            "Maximised system size on available roof to increase 25-year bill savings "
            "(assumes useful self-consumption / export)"
        )
        if bill_sizing and bill_sizing.get("recommended_kw", 0) > 0:
            rationale.append(
                f"Bill offset size would be ~{bill_sizing['recommended_kw']:.2f} kW; "
                "roof-max chosen instead for max lifetime savings"
            )

    else:
        # Unknown mode → safe legacy path
        if bill_sizing and bill_sizing.get("recommended_kw", 0) > 0:
            recommended = round(min(float(bill_sizing["recommended_kw"]), max_kw), 2)
            recommended = max(recommended, min_kw) if max_kw >= min_kw else max_kw
            rationale.append(f"Unknown mode '{mode}' — used bill_offset_100% behaviour")
            mode = "bill_offset_100%"
        else:
            recommended = max_kw
            rationale.append(f"Unknown mode '{mode}' — used roof_max behaviour")
            mode = "roof_max"

    recommended = round(max(min(recommended, max_kw), min(min_kw, max_kw)), 2)
    min_kw = round(min(min_kw, recommended), 2)
    max_kw = round(max(max_kw, recommended), 2)

    # Always note inverter / export as soft constraints (not yet numeric caps)
    constraints.append("Inverter DC/AC ratio (final design)")
    # Deduplicate constraints while preserving order
    seen = set()
    constraints_unique = []
    for c in constraints:
        if c not in seen:
            seen.add(c)
            constraints_unique.append(c)

    return {
        "mode": mode,
        "min_kw": min_kw,
        "recommended_kw": recommended,
        "max_kw": max_kw,
        "rationale": rationale,
        "constraints": constraints_unique,
        "roof_capacity_kw": round(roof_capacity_kw, 2),
        "bill_sizing": bill_sizing,
    }



def size_battery_kwh(critical_load, backup_hours=None, dod=None, rte=None):
    """Nominal battery kWh from critical load and optional backup duration.

    Formula:
      energy_kWh = critical_load_kW × backup_hours   (when hours provided)
                 = critical_load                    (when hours omitted — treat as kWh)
      nominal_kWh = energy_kWh ÷ (DoD × round-trip efficiency)

    Example: 1 kW essential load × 4 h, 80% DoD, 90% RTE → 4 / 0.72 ≈ 5.56 kWh.
    """
    dod = dod if dod is not None else getattr(Config, "BATTERY_DEFAULT_DOD", 0.80)
    rte = rte if rte is not None else getattr(Config, "BATTERY_DEFAULT_RTE", 0.90)
    load = max(0.0, float(critical_load or 0))
    hours = None
    if backup_hours is not None and float(backup_hours or 0) > 0:
        hours = float(backup_hours)
        energy = load * hours  # load interpreted as continuous kW
    else:
        energy = load  # load interpreted as energy (kWh)
    denom = max(float(dod) * float(rte), 0.05)
    nominal = energy / denom if energy else 0.0
    return {
        "critical_load_kw": load if hours is not None else None,
        "critical_load_kwh": round(energy, 2),
        "backup_hours": hours,
        "depth_of_discharge": float(dod),
        "round_trip_efficiency": float(rte),
        "nominal_battery_kwh": round(nominal, 2),
        "note": "Do not put geysers, ACs, ovens or pumps on a small backup circuit without a load study.",
    }



def recommend_inverter(
    property_type="residential", needs_backup=False, battery_kwh=0.0, preferred=None
):
    """Recommend On-Grid / Hybrid / Off-Grid inverter (iNVERGY-style guidance).

    Rules of thumb:
      • Stable grid, bill savings only → On-Grid
      • Power cuts / battery / smart management → Hybrid
      • Remote / unreliable grid / full independence → Off-Grid
    """
    if preferred and str(preferred).lower() not in ("auto", "", "none"):
        key = str(preferred).lower().replace(" ", "-")
        catalog = {
            "on-grid": {
                "type": "On-Grid",
                "best_for": "Homes with stable electricity supply; users focused on reducing bills",
                "advantages": [
                    "Lower installation cost",
                    "Higher efficiency",
                    "Government subsidy eligibility",
                    "Low maintenance",
                ],
                "subsidy_eligible": True,
            },
            "hybrid": {
                "type": "Hybrid",
                "best_for": "Homes needing backup during power cuts; battery-supported systems",
                "advantages": [
                    "Works with solar + battery + grid",
                    "Backup support during outages",
                    "Better energy optimization",
                    "Eligible for rooftop subsidy in many cases",
                ],
                "subsidy_eligible": True,
            },
            "off-grid": {
                "type": "Off-Grid",
                "best_for": "Remote locations; unreliable grid; backup-focused setups",
                "advantages": [
                    "Complete energy independence",
                    "Battery-based operation",
                    "Reliable power backup",
                ],
                "subsidy_eligible": False,
            },
        }
        return catalog.get(key, catalog["on-grid"])

    prop = (property_type or "residential").lower()
    backup = bool(needs_backup) or float(battery_kwh or 0) > 0

    if prop in ("remote", "off-grid-site", "rural-unreliable"):
        choice = "off-grid"
    elif backup:
        choice = "hybrid"
    else:
        choice = "on-grid"

    return recommend_inverter(preferred=choice)


def calculate_environmental_equivalents(co2_reduction_tons):
    """Rough, widely-cited conversion factors used to turn a CO2 reduction
    figure into relatable equivalents for the Environmental tab."""
    co2_kg = co2_reduction_tons * 1000
    trees_planted = round(co2_kg / 21)  # ~21kg CO2 absorbed per tree/year
    km_not_driven = round(co2_kg / 0.12)  # ~120g CO2/km for an average car
    fuel_liters_saved = round(co2_kg / 2.31)  # ~2.31kg CO2 per liter of petrol
    return {
        "trees_planted": trees_planted,
        "km_not_driven": km_not_driven,
        "fuel_liters_saved": fuel_liters_saved,
    }


def estimate_orientation_from_polygon(coordinates):
    """
    Approximate roof azimuth from the longest edge of the drawn polygon.
    Returns (azimuth_deg 0-360, label). 0=North, 90=East, 180=South, 270=West.
    For northern India, south-facing (≈180°) is optimal.
    """
    if not coordinates or len(coordinates) < 2:
        return 180.0, "South (assumed)"

    # Close the ring if needed
    coords = list(coordinates)
    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]

    best_len = -1.0
    best_az = 180.0
    for i in range(len(coords) - 1):
        lat1, lng1 = coords[i]
        lat2, lng2 = coords[i + 1]
        # Approximate length in meters
        dlat = (lat2 - lat1) * 111320
        dlng = (lng2 - lng1) * 111320 * math.cos(math.radians((lat1 + lat2) / 2))
        length = math.hypot(dlat, dlng)
        if length > best_len:
            best_len = length
            # Bearing of the edge; roof ridge often runs along longest edge,
            # so panel azimuth is perpendicular to that edge (choose the
            # more southerly of the two perpendiculars).
            bearing = (math.degrees(math.atan2(dlng, dlat)) + 360) % 360
            perp1 = (bearing + 90) % 360
            perp2 = (bearing + 270) % 360

            # Prefer the direction closer to south (180)
            def south_dist(a):
                return min(abs(a - 180), 360 - abs(a - 180))

            best_az = perp1 if south_dist(perp1) <= south_dist(perp2) else perp2

    label = _azimuth_to_label(best_az)
    return round(best_az, 1), label


def _azimuth_to_label(az):
    az = az % 360
    if az >= 337.5 or az < 22.5:
        return "North"
    if az < 67.5:
        return "North-East"
    if az < 112.5:
        return "East"
    if az < 157.5:
        return "South-East"
    if az < 202.5:
        return "South"
    if az < 247.5:
        return "South-West"
    if az < 292.5:
        return "West"
    return "North-West"


def orientation_factor(azimuth_deg):
    """
    Simple relative yield factor vs optimal south orientation for northern
    India. The current cosine-like formula produces approximately:
    South ≈ 1.00, SE/SW ≈ 0.90, E/W ≈ 0.65, and North ≈ 0.65.
    Values are clamped to the range 0.65–1.00.
    """
    # Distance from south (180)
    d = min(abs((azimuth_deg % 360) - 180), 360 - abs((azimuth_deg % 360) - 180))
    # Cosine-like falloff
    factor = 0.70 + 0.30 * math.cos(math.radians(d * 1.1))
    return round(max(0.65, min(1.0, factor)), 3)


def _tier_score(tier):
    return {
        "excellent": 100,
        "good": 75,
        "fair": 50,
        "poor": 25,
        "not_viable": 0,
        "unavailable": 0,
    }[tier]


def _payback_tier(payback_years):
    if payback_years is None or payback_years <= 0:
        return "unavailable"
    if payback_years <= 5:
        return "excellent"
    if payback_years <= 8:
        return "good"
    if payback_years <= 12:
        return "fair"
    return "poor"


def _orientation_tier(factor):
    if factor is None:
        return "unavailable"
    if factor >= 0.95:
        return "excellent"
    if factor >= 0.85:
        return "good"
    if factor > 0.65:
        return "fair"
    return "poor"


def _roof_fit_tier(usable_area_sqm, system_size_kw):
    if usable_area_sqm is None or system_size_kw is None or system_size_kw <= 0:
        return "unavailable", None
    ratio = usable_area_sqm / system_size_kw
    if 10 <= ratio <= 16:
        tier = "excellent"
    elif 8 <= ratio < 10 or 16 < ratio <= 20:
        tier = "good"
    elif 6 <= ratio < 8 or 20 < ratio <= 25:
        tier = "fair"
    elif ratio > 25:
        # Extra roof area is unused potential, not a physical fit failure.
        tier = "fair"
    else:
        # Less than 6 m²/kW may not physically fit the proposed system.
        tier = "poor"
    return tier, round(ratio, 2)


def _financial_viability_tier(net_investment, cashflow_25yr):
    if net_investment is None or not cashflow_25yr:
        return "unavailable", None
    lifetime_savings = cashflow_25yr[-1] + net_investment
    if lifetime_savings <= 0:
        return "not_viable", None
    ratio = net_investment / lifetime_savings
    if ratio >= 1.0:
        tier = "not_viable"
    elif ratio <= 0.25:
        tier = "excellent"
    elif ratio <= 0.40:
        tier = "good"
    elif ratio <= 0.60:
        tier = "fair"
    else:
        tier = "poor"
    return tier, round(ratio, 3)


RATING_ALGORITHM_VERSION = "1.0"

# Human-readable labels for suitability factors (assistant + UI share these).
_FACTOR_POSITIVE_COPY = {
    "payback": {
        "excellent": "Excellent payback period",
        "good": "Strong payback period",
        "fair": "Acceptable payback period",
        "poor": "Payback is relatively long",
    },
    "financial_viability": {
        "excellent": "Strong lifetime savings outlook",
        "good": "Solid lifetime savings outlook",
        "fair": "Moderate lifetime savings",
        "poor": "Limited lifetime savings margin",
    },
    "roof_fit": {
        "excellent": "Generous usable roof area for the system size",
        "good": "Good usable roof area for the system size",
        "fair": "Adequate roof area for the system size",
        "poor": "Limited by available roof area",
    },
    "orientation": {
        "excellent": "Excellent roof orientation",
        "good": "Favourable roof orientation",
        "fair": "Orientation is only fair for solar",
        "poor": "Roof orientation is a limiting factor",
    },
    "co2_reduction": {
        "excellent": "High estimated CO₂ reduction",
        "good": "Meaningful estimated CO₂ reduction",
        "fair": "Modest estimated CO₂ reduction",
        "poor": "Low estimated CO₂ reduction",
    },
    "backup_capability": {
        "excellent": "Strong backup capability",
        "good": "Useful backup capability",
        "fair": "Limited backup capability",
        "poor": "Little or no backup capability",
    },
}


def _factor_readable(name, tier, limiting=False):
    tier = (tier or "fair").lower()
    table = _FACTOR_POSITIVE_COPY.get(name) or {}
    text = table.get(tier) or table.get("fair") or name.replace("_", " ").title()
    if limiting and tier in ("poor", "fair"):
        # Prefer limiting-oriented phrasing already in poor/fair rows
        return text
    return text


def derive_top_factors(factors, weights=None):
    """Pick top positive and top limiting factors as short human-readable strings.

    Shared by the API, UI, and assistant rating context so wording stays consistent.
    """
    if not factors:
        return None, None
    ranked = []
    for name, meta in factors.items():
        if not isinstance(meta, dict):
            continue
        try:
            score = float(meta.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        weight = 1.0
        if weights and name in weights:
            try:
                weight = float(weights[name]) / 100.0
            except (TypeError, ValueError):
                weight = 1.0
        ranked.append((name, score, score * weight, meta.get("tier") or "fair"))
    if not ranked:
        return None, None
    # Positive: highest raw score (ties broken by weighted contribution)
    ranked_pos = sorted(ranked, key=lambda x: (x[1], x[2]), reverse=True)
    # Limiting: lowest raw score
    ranked_lim = sorted(ranked, key=lambda x: (x[1], -x[2]))
    pos_name, _, _, pos_tier = ranked_pos[0]
    lim_name, _, _, lim_tier = ranked_lim[0]
    # If the same factor is both best and worst (all equal), only report positive
    if pos_name == lim_name and ranked_pos[0][1] == ranked_lim[0][1]:
        return _factor_readable(pos_name, pos_tier), None
    return (
        _factor_readable(pos_name, pos_tier, limiting=False),
        _factor_readable(lim_name, lim_tier, limiting=True),
    )


def calculate_suitability_rating(estimate, user_priority="no_preference"):

    """Score estimate suitability and data confidence separately."""
    estimate = estimate or {}
    payback_tier = _payback_tier(estimate.get("payback_years"))
    orientation_tier = _orientation_tier(estimate.get("orientation_factor"))
    roof_tier, roof_ratio = _roof_fit_tier(
        estimate.get("usable_area_sqm"), estimate.get("system_size")
    )
    financial_tier, financial_ratio = _financial_viability_tier(
        estimate.get("net_investment"), estimate.get("cashflow_25yr")
    )

    co2 = estimate.get("co2_reduction_tons")
    if co2 is None or co2 < 0:
        co2_score = 0
        co2_tier = "unavailable"
    else:
        co2_score = round(min(100, co2 / 3 * 100))
        co2_tier = (
            "excellent" if co2_score >= 75
            else "good" if co2_score >= 50
            else "fair" if co2_score >= 25
            else "poor"
        )

    inverter = str(estimate.get("inverter_type") or "").lower()
    battery_kwh = float(estimate.get("battery_kwh") or 0)
    if inverter == "off-grid":
        backup_score = 100 if battery_kwh > 0 else 55
    elif inverter == "hybrid":
        backup_score = 90 if battery_kwh > 0 else 55
    elif battery_kwh > 0:
        backup_score = 45
    else:
        backup_score = 10

    factors = {
        "payback": {"tier": payback_tier, "score": _tier_score(payback_tier)},
        "financial_viability": {
            "tier": financial_tier,
            "score": _tier_score(financial_tier),
            "ratio": financial_ratio,
        },
        "roof_fit": {
            "tier": roof_tier,
            "score": _tier_score(roof_tier),
            "usable_area_per_kw": roof_ratio,
        },
        "orientation": {
            "tier": orientation_tier,
            "score": _tier_score(orientation_tier),
            "factor": estimate.get("orientation_factor"),
        },
        "co2_reduction": {
            "tier": co2_tier,
            "score": co2_score,
            "tons": co2,
        },
        "backup_capability": {"score": backup_score},
    }

    weights = {
        "payback": 25,
        "financial_viability": 25,
        "roof_fit": 15,
        "orientation": 10,
        "co2_reduction": 15,
        "backup_capability": 10,
    }
    priority_weights = {
        "fastest_payback": {"payback": 40, "financial_viability": 25, "roof_fit": 10, "orientation": 10, "co2_reduction": 5, "backup_capability": 10},
        "maximum_savings": {"payback": 20, "financial_viability": 40, "roof_fit": 15, "orientation": 10, "co2_reduction": 5, "backup_capability": 10},
        "environmental_impact": {"payback": 10, "financial_viability": 10, "roof_fit": 10, "orientation": 10, "co2_reduction": 50, "backup_capability": 10},
        "backup_power": {"payback": 15, "financial_viability": 10, "roof_fit": 10, "orientation": 5, "co2_reduction": 5, "backup_capability": 55},
    }
    weights.update(priority_weights.get(user_priority, {}))
    score = sum(
        factors[name]["score"] * weight / 100
        for name, weight in weights.items()
    )
    viability_score = round(score)
    viability_tier = (
        "excellent" if viability_score >= 85
        else "good" if viability_score >= 65
        else "fair" if viability_score >= 45
        else "poor"
    )
    source = estimate.get("irradiance_source")
    # Prefer explicit top-level confidence when present (set by run_full_estimation).
    explicit_tier = estimate.get("irradiance_confidence")
    if explicit_tier in ("high", "medium", "low"):
        tier = explicit_tier
    else:
        _, tier = classify_irradiance_source(source)
    # Keep historical scores: high=100, low mock=60, unknown=50
    confidence_score = {"high": 100, "medium": 75, "low": 60}.get(tier, 50)

    top_positive, top_limiting = derive_top_factors(factors, weights)
    return {
        "overall_viability": {"tier": viability_tier, "score": viability_score},
        "data_confidence": {"tier": tier, "score": confidence_score},
        "user_priority": user_priority,
        "weights": weights,
        "factors": factors,
        "top_positive_factor": top_positive,
        "top_limiting_factor": top_limiting,
        "rating_algorithm_version": RATING_ALGORITHM_VERSION,
    }


def resolve_orientation(orientation_input, coordinates):
    """
    orientation_input: 'auto' or a numeric string/number (degrees).
    Returns (azimuth_deg, label, factor, source).
    """
    if orientation_input is None or str(orientation_input).lower() in (
        "auto",
        "",
        "none",
    ):
        az, label = estimate_orientation_from_polygon(coordinates)
        return az, label, orientation_factor(az), "auto-detect"
    try:
        az = float(orientation_input) % 360
    except (TypeError, ValueError):
        az, label = estimate_orientation_from_polygon(coordinates)
        return az, label, orientation_factor(az), "auto-detect"
    label = _azimuth_to_label(az)
    return az, label, orientation_factor(az), "user-selected"



def financing_snapshot(net_investment, monthly_savings=None):
    """Informational EMI range snapshot — not a financing offer.

    Shows indicative monthly EMI at the corners of the common retail range:
    interest 8–12% p.a., tenure 3–7 years. Default point estimate remains 10%/5yr.
    """
    principal = max(0.0, float(net_investment or 0))
    scenarios = []
    for rate in (8.0, 10.0, 12.0):
        for years in (3, 5, 7):
            emi = calculate_emi(principal, rate, years)
            scenarios.append(
                {
                    "annual_rate_pct": rate,
                    "tenure_years": years,
                    "monthly_emi": emi["monthly_emi"],
                }
            )
    # Highlight corners + midpoint
    def pick(rate, years):
        for s in scenarios:
            if s["annual_rate_pct"] == rate and s["tenure_years"] == years:
                return s
        return None

    low = pick(8.0, 7)   # softest EMI
    mid = pick(10.0, 5)
    high = pick(12.0, 3)  # highest EMI
    monthly_savings = float(monthly_savings or 0)
    return {
        "principal": round(principal, 2),
        "interest_range_pct": [8.0, 12.0],
        "tenure_range_years": [3, 7],
        "emi_low": low,
        "emi_mid": mid,
        "emi_high": high,
        "monthly_savings": round(monthly_savings, 2),
        "label": "Informational financing ranges only — not a loan offer or partnership product.",
        "disclaimer": (
            "Indicative EMI using standard reducing-balance math. "
            "Actual rates, eligibility, and tenure depend on your bank or NBFC. "
            "Indicative only — verify with DISCOM / vendor / lender."
        ),
    }



def calculate_emi(principal, annual_rate_pct, tenure_years):
    """Standard reducing-balance monthly EMI.

    principal: loan amount (₹) — use net_investment
    annual_rate_pct: e.g. 10 for 10%
    tenure_years: 3–7 typical home-solar loans
    """
    principal = max(0.0, float(principal or 0))
    tenure_years = max(1, int(tenure_years))
    n = tenure_years * 12
    if principal <= 0 or n <= 0:
        return {
            "principal": 0.0,
            "annual_rate_pct": float(annual_rate_pct or 0),
            "tenure_years": tenure_years,
            "monthly_emi": 0.0,
            "total_payment": 0.0,
            "total_interest": 0.0,
        }
    r = float(annual_rate_pct or 0) / 100.0 / 12.0
    if r <= 0:
        emi = principal / n
    else:
        factor = (1 + r) ** n
        emi = principal * r * factor / (factor - 1)
    emi = round(emi, 2)
    total = round(emi * n, 2)
    return {
        "principal": round(principal, 2),
        "annual_rate_pct": float(annual_rate_pct or 0),
        "tenure_years": tenure_years,
        "monthly_emi": emi,
        "total_payment": total,
        "total_interest": round(total - principal, 2),
        "covers_emi": None,  # filled by caller using monthly_savings
    }


def calculate_equity_irr(cash_flows, max_iterations=100, tol=1e-6):
    """Newton-Raphson IRR for a series of annual cash flows.

    cash_flows[0] should be the initial outlay (negative). Returns IRR as
    a fraction (e.g. 0.12 for 12%) or None if it cannot be computed.
    """
    if not cash_flows or len(cash_flows) < 2:
        return None
    # Need at least one sign change
    signs = [1 if c > 0 else (-1 if c < 0 else 0) for c in cash_flows]
    if 1 not in signs or -1 not in signs:
        return None
    r = 0.1  # initial guess
    for _ in range(max_iterations):
        npv = 0.0
        d_npv = 0.0
        for t, cf in enumerate(cash_flows):
            denom = (1 + r) ** t
            npv += cf / denom
            if t > 0:
                d_npv -= t * cf / ((1 + r) ** (t + 1))
        if abs(d_npv) < 1e-12:
            break
        r_new = r - npv / d_npv
        if abs(r_new - r) < tol:
            # Sanity bounds: solar equity IRR rarely outside -50%..+100%
            if -0.5 <= r_new <= 1.0:
                return round(r_new, 4)
            return None
        r = r_new
    if -0.5 <= r <= 1.0:
        return round(r, 4)
    return None


def _annual_generation_series(annual_generation_kwh, years=25, degradation=None):
    """Year-by-year generation (kWh) with panel degradation."""
    if degradation is None:
        degradation = get_value("degradation_pct", DEGRADATION_PCT.value)
    series = []
    for y in range(1, years + 1):
        series.append(round(annual_generation_kwh * ((1 - degradation) ** (y - 1)), 1))
    return series


def build_lifecycle_projection(
    net_investment,
    avoided_retail_value_year1,
    export_value_year1,
    annual_om,
    annual_generation_kwh,
    *,
    export_value_escalates=False,
    years=25,
    degradation=None,
    tariff_escalation=None,
    discount_rate=None,
):
    """Build the single authoritative lifecycle cash-flow projection.

    Retail bill avoidance escalates with the tariff assumption. A fixed feed-in
    rate does not. Both energy-value streams degrade with modeled generation.
    O&M is a constant nominal annual planning reserve and is subtracted exactly
    once. Discounting is applied only to discounted metrics, never to the
    nominal cash-flow series.
    """
    degradation = (
        get_value("degradation_pct", DEGRADATION_PCT.value)
        if degradation is None else float(degradation)
    )
    tariff_escalation = (
        get_value("annual_tariff_escalation", ANNUAL_TARIFF_ESCALATION.value)
        if tariff_escalation is None else float(tariff_escalation)
    )
    discount_rate = (
        get_value("discount_rate", DISCOUNT_RATE.value)
        if discount_rate is None else float(discount_rate)
    )
    upfront = max(0.0, float(net_investment or 0))
    retail0 = max(0.0, float(avoided_retail_value_year1 or 0))
    export0 = max(0.0, float(export_value_year1 or 0))
    om = max(0.0, float(annual_om or 0))
    generation0 = max(0.0, float(annual_generation_kwh or 0))

    rows = []
    cumulative = -upfront
    discounted_cumulative = -upfront
    nominal_payback = None
    discounted_payback = None
    pv_energy = 0.0
    pv_om = 0.0
    for year in range(1, int(years) + 1):
        degradation_factor = (1 - degradation) ** (year - 1)
        tariff_factor = (1 + tariff_escalation) ** (year - 1)
        export_factor = tariff_factor if export_value_escalates else 1.0
        avoided_retail = retail0 * degradation_factor * tariff_factor
        export_value = export0 * degradation_factor * export_factor
        gross_savings = avoided_retail + export_value
        net_cash_flow = gross_savings - om
        discount_factor = (1 + discount_rate) ** year
        discounted_cash_flow = net_cash_flow / discount_factor
        generation = generation0 * degradation_factor
        previous = cumulative
        previous_discounted = discounted_cumulative
        cumulative += net_cash_flow
        discounted_cumulative += discounted_cash_flow
        pv_energy += generation / discount_factor
        pv_om += om / discount_factor
        if nominal_payback is None and cumulative >= 0 and net_cash_flow > 0:
            nominal_payback = round(
                (year - 1) + max(0.0, min(1.0, -previous / net_cash_flow)), 1
            )
        if (
            discounted_payback is None
            and discounted_cumulative >= 0
            and discounted_cash_flow > 0
        ):
            discounted_payback = round(
                (year - 1)
                + max(0.0, min(1.0, -previous_discounted / discounted_cash_flow)),
                1,
            )
        rows.append({
            "year": year,
            "generation_kwh": round(generation, 2),
            "avoided_retail_value": round(avoided_retail, 2),
            "export_value": round(export_value, 2),
            "gross_savings": round(gross_savings, 2),
            "om_cost": round(om, 2),
            "net_cash_flow": round(net_cash_flow, 2),
            "discounted_net_cash_flow": round(discounted_cash_flow, 2),
            "cumulative_cash_flow": round(cumulative, 2),
            "discounted_cumulative_cash_flow": round(discounted_cumulative, 2),
        })

    lifetime_generation = sum(row["generation_kwh"] for row in rows)
    lifetime_gross_savings = sum(row["gross_savings"] for row in rows)
    lifetime_om = sum(row["om_cost"] for row in rows)
    discounted_lcoe = (
        (upfront + pv_om) / pv_energy if pv_energy > 0 else None
    )
    return {
        "schema_version": "1.0",
        "years": rows,
        "nominal_payback_years": nominal_payback,
        "discounted_payback_years": discounted_payback,
        "npv": round(discounted_cumulative, 2),
        "discount_rate_pct": round(discount_rate * 100, 2),
        "lifetime_generation_kwh": round(lifetime_generation, 2),
        "lifetime_gross_savings": round(lifetime_gross_savings, 2),
        "lifetime_om_cost": round(lifetime_om, 2),
        "lifetime_net_savings_before_upfront": round(
            lifetime_gross_savings - lifetime_om, 2
        ),
        "discounted_lcoe": round(discounted_lcoe, 2) if discounted_lcoe is not None else None,
        "om_escalation_pct": 0.0,
    }


def build_financing_comparison(
    net_investment,
    annual_savings,
    monthly_savings,
    annual_generation_kwh,
    down_payment_pct=20.0,
    dpa_tenure_years=5,
    dpa_rate_pct=10.0,
    years=25,
    annual_net_cashflows=None,
    annual_om=0.0,
):
    """Side-by-side CAPEX vs DPA financing comparison (PPA deferred).

    CAPEX = full cash purchase (existing model).
    DPA  = Deferred Payment Agreement / loan: down-payment % + EMI on remainder.
    Returns table-ready dicts plus equity IRR and payback for each path.
    """
    degradation = get_value("degradation_pct", DEGRADATION_PCT.value)
    tariff_escalation = get_value(
        "annual_tariff_escalation", ANNUAL_TARIFF_ESCALATION.value
    )
    net = max(0.0, float(net_investment or 0))
    ann_sav = float(annual_savings or 0)
    mon_sav = float(monthly_savings or 0)

    if annual_net_cashflows is not None:
        if len(annual_net_cashflows) != years:
            raise ValueError("annual_net_cashflows must contain one value per year")
        projected_cashflows = [float(value) for value in annual_net_cashflows]
    else:
        projected_cashflows = [
            ann_sav
            * ((1 - degradation) ** (y - 1))
            * ((1 + tariff_escalation) ** (y - 1))
            - float(annual_om or 0)
            for y in range(1, years + 1)
        ]
    monthly_operating_benefit = (
        projected_cashflows[0] / 12.0 if projected_cashflows else 0.0
    )

    # ---- CAPEX (cash purchase) ----
    capex_flows = [-net]
    cum = -net
    capex_payback = None
    for y, cf in enumerate(projected_cashflows, start=1):
        prev = cum
        cum += cf
        capex_flows.append(round(cf, 2))
        if capex_payback is None and cum >= 0 and cf > 0:
            frac = (-prev) / cf if cf else 0.0
            capex_payback = round((y - 1) + max(0.0, min(1.0, frac)), 1)
    capex_irr = calculate_equity_irr(capex_flows)

    # ---- DPA (loan / deferred payment) ----
    dp_pct = max(0.0, min(100.0, float(down_payment_pct or 0)))
    down_payment = round(net * dp_pct / 100.0, 2)
    loan_amount = round(net - down_payment, 2)
    tenure = max(1, int(dpa_tenure_years or 5))
    rate = float(dpa_rate_pct or 10.0)
    emi_info = calculate_emi(loan_amount, rate, tenure)
    monthly_emi = emi_info["monthly_emi"]
    annual_emi = round(monthly_emi * 12, 2)

    dpa_flows = [-down_payment]  # equity outlay = down payment only
    cum_dpa = -down_payment
    dpa_payback = None
    for y, year_savings in enumerate(projected_cashflows, start=1):
        # EMI only during loan tenure
        year_emi = annual_emi if y <= tenure else 0.0
        cf = year_savings - year_emi
        prev = cum_dpa
        cum_dpa += cf
        dpa_flows.append(round(cf, 2))
        if dpa_payback is None and cum_dpa >= 0 and cf > 0:
            frac = (-prev) / cf if cf else 0.0
            dpa_payback = round((y - 1) + max(0.0, min(1.0, frac)), 1)
    dpa_irr = calculate_equity_irr(dpa_flows)

    gen_series = _annual_generation_series(annual_generation_kwh, years, degradation)

    return {
        "capex": {
            "label": "CAPEX (Cash Purchase)",
            "down_payment": round(net, 2),
            "loan_amount": 0.0,
            "tenure_years": None,
            "annual_rate_pct": None,
            "monthly_emi": 0.0,
            "payback_years": capex_payback,
            "equity_irr_pct": round(capex_irr * 100, 1) if capex_irr is not None else None,
            "total_cash_outflow": round(net, 2),
            "cash_flows": capex_flows,
        },
        "dpa": {
            "label": "DPA (Deferred Payment / Loan)",
            "down_payment": down_payment,
            "loan_amount": loan_amount,
            "tenure_years": tenure,
            "annual_rate_pct": rate,
            "monthly_emi": monthly_emi,
            "annual_emi": annual_emi,
            "total_repayment": round(emi_info["total_payment"], 2),
            "total_interest": round(emi_info["total_interest"], 2),
            "payback_years": dpa_payback,
            "equity_irr_pct": round(dpa_irr * 100, 1) if dpa_irr is not None else None,
            "total_cash_outflow": round(down_payment + emi_info["total_payment"], 2),
            "covers_emi": (
                bool(monthly_operating_benefit >= monthly_emi)
                if monthly_emi else None
            ),
            "monthly_operating_benefit": round(monthly_operating_benefit, 2),
            "cash_flows": dpa_flows,
        },
        "assumptions": {
            "down_payment_pct": dp_pct,
            "dpa_tenure_years": tenure,
            "dpa_rate_pct": rate,
            "years": years,
            "degradation_pct": round(degradation * 100, 2),
            "tariff_escalation_pct": round(tariff_escalation * 100, 2),
            "annual_om": round(float(annual_om or 0), 2),
            "note": (
                "Indicative comparison only. CAPEX = full net investment upfront. "
                "DPA = down-payment + reducing-balance EMI on remainder. "
                "Equity IRR uses annual net cash flows (energy value − O&M − EMI) "
                "with no terminal value. "
                "Not a loan offer — verify rates with your bank/NBFC."
            ),
        },
        "generation_25yr": gen_series,
    }



def _project_npv(net_investment, annual_savings, years=25, discount_rate=None,
                 degradation=None, tariff_escalation=None, annual_om=0.0):
    """NPV of nominal energy value less O&M and upfront net investment."""
    if discount_rate is None:
        discount_rate = get_value("discount_rate", DISCOUNT_RATE.value)
    if degradation is None:
        degradation = get_value("degradation_pct", DEGRADATION_PCT.value)
    if tariff_escalation is None:
        tariff_escalation = get_value(
            "annual_tariff_escalation", ANNUAL_TARIFF_ESCALATION.value
        )
    npv = -float(net_investment or 0)
    savings0 = float(annual_savings or 0)
    for year in range(1, years + 1):
        deg = (1 - degradation) ** (year - 1)
        esc = (1 + tariff_escalation) ** (year - 1)
        cash = savings0 * deg * esc - float(annual_om or 0)
        npv += cash / ((1 + discount_rate) ** year)
    return round(npv, 2)


def _simple_payback(net_investment, annual_savings, annual_om=0.0):
    net_annual = float(annual_savings or 0) - float(annual_om or 0)
    if net_annual <= 0:
        return None
    return round(float(net_investment) / net_annual, 1)


def build_sensitivity_table(
    system_size_kw,
    annual_generation_kwh,
    tariff_per_kwh,
    system_cost,
    battery_cost,
    subsidy_amount,
    self_consumption_frac,
    net_metering_mode="full_offset",
    feed_in_tariff=None,
    annual_consumption_kwh=None,
):
    """Compact sensitivity: generation ±10%, tariff ±₹2, system cost ±10%.

    Returns rows with base / low / high simple payback and NPV for each driver.
    """
    battery_cost = float(battery_cost or 0)
    subsidy = float(subsidy_amount or 0)
    base_cost = float(system_cost or 0)
    gen0 = float(annual_generation_kwh or 0)
    tariff0 = float(tariff_per_kwh or 0)
    sc = float(self_consumption_frac or 0.55)

    def metrics(gen, tariff, cost):
        net = max(cost + battery_cost - subsidy, 0.0)
        value = value_generation_kwh(
            gen,
            sc,
            tariff,
            net_metering_mode=net_metering_mode,
            feed_in_tariff=feed_in_tariff,
            annual_consumption_kwh=annual_consumption_kwh,
        )
        annual = value["annual_savings"]
        annual_om = cost * get_value("om_percent", OM_PERCENT.value)
        projection = build_lifecycle_projection(
            net,
            value["avoided_retail_value"],
            value["export_value"],
            annual_om,
            gen,
            export_value_escalates=(
                value["net_metering_mode"] == "export_at_tariff"
            ),
        )
        return {
            "net_investment": round(net, 2),
            "annual_savings": round(annual, 2),
            "simple_payback_years": _simple_payback(net, annual, annual_om),
            "npv_8pct": projection["npv"],
        }

    base = metrics(gen0, tariff0, base_cost)
    rows = [
        {
            "driver": "Generation ±10%",
            "low_label": "−10% generation",
            "high_label": "+10% generation",
            "base": base,
            "low": metrics(gen0 * 0.9, tariff0, base_cost),
            "high": metrics(gen0 * 1.1, tariff0, base_cost),
        },
        {
            "driver": "Tariff ±₹2/kWh",
            "low_label": f"₹{max(tariff0 - 2, 0):.0f}/kWh",
            "high_label": f"₹{tariff0 + 2:.0f}/kWh",
            "base": base,
            "low": metrics(gen0, max(tariff0 - 2, 0), base_cost),
            "high": metrics(gen0, tariff0 + 2, base_cost),
        },
        {
            "driver": "System cost ±10%",
            "low_label": "−10% cost",
            "high_label": "+10% cost",
            "base": base,
            "low": metrics(gen0, tariff0, base_cost * 0.9),
            "high": metrics(gen0, tariff0, base_cost * 1.1),
        },
    ]
    return {
        "discount_rate_pct": round(
            get_value("discount_rate", DISCOUNT_RATE.value) * 100, 2
        ),
        "note": (
            "Indicative sensitivity only — not a Monte Carlo. "
            "NPV discounts escalated/degraded bill savings less annual O&M."
        ),
        "rows": rows,
    }



# ---------------------------------------------------------------------------
# Load-profile presets → base self-consumption fraction (no battery)
# Battery still applies the existing boost curve on top of the base.
# ---------------------------------------------------------------------------
LOAD_PROFILE_PRESETS = {
    "day_heavy": {
        "label": "Day-heavy",
        "base_self_consumption": 0.70,
        "note": (
            "Loads concentrated during solar hours (offices, daytime AC). "
            "Higher on-site use of generation."
        ),
    },
    "flat": {
        "label": "Flat / mixed",
        "base_self_consumption": 0.55,
        "note": (
            "Mixed residential profile — historical SIGNIX default (~55% "
            "self-consumption without storage)."
        ),
    },
    "evening_heavy": {
        "label": "Evening-heavy",
        "base_self_consumption": 0.40,
        "note": (
            "Most demand after sunset (typical homes with evening peaks). "
            "More daytime export without a battery."
        ),
    },
}

NET_METERING_MODES = (
    "full_offset",          # only self-consumed kWh valued at retail tariff
    "export_at_tariff",     # all kWh valued at retail tariff
    "export_at_feed_in_rate",  # self-consumed @ retail; export @ feed-in rate
)


def resolve_load_profile(load_profile=None):
    key = (load_profile or "flat").strip().lower().replace("-", "_").replace(" ", "_")
    if key in ("day", "dayheavy", "day_time"):
        key = "day_heavy"
    elif key in ("evening", "eveningheavy", "night"):
        key = "evening_heavy"
    elif key in ("mixed", "default", "balanced"):
        key = "flat"
    if key not in LOAD_PROFILE_PRESETS:
        key = "flat"
    preset = LOAD_PROFILE_PRESETS[key]
    return key, preset


def resolve_net_metering_mode(mode=None):
    key = (mode or "full_offset").strip().lower().replace("-", "_")
    aliases = {
        "offset": "full_offset",
        "full": "full_offset",
        "retail": "export_at_tariff",
        "export_retail": "export_at_tariff",
        "feed_in": "export_at_feed_in_rate",
        "feedin": "export_at_feed_in_rate",
        "fit": "export_at_feed_in_rate",
    }
    key = aliases.get(key, key)
    if key not in NET_METERING_MODES:
        key = "full_offset"
    return key


def self_consumption_fraction(load_profile=None, battery_kwh=0.0):
    """Base preset SC fraction, then existing battery boost curve (unchanged)."""
    key, preset = resolve_load_profile(load_profile)
    base = float(preset["base_self_consumption"])
    battery_kwh = max(0.0, float(battery_kwh or 0))
    sc = base
    if battery_kwh > 0:
        # Same documented curve as before: 5 kWh → +0.27, 10 → +0.42 capped path
        boost = min(0.35, 0.12 + 0.03 * battery_kwh)
        sc = min(0.92, base + boost)
    return round(sc, 4), key, preset


def value_generation_kwh(
    annual_generation_kwh,
    self_consumption_frac,
    tariff_per_kwh,
    net_metering_mode="full_offset",
    feed_in_tariff=None,
    annual_consumption_kwh=None,
):
    """Value year-one energy without treating generation as measured savings.

    ``self_consumption_frac`` is a planning assumption. When household demand is
    available, direct consumption is additionally capped by that demand.
    """
    gen = max(0.0, float(annual_generation_kwh or 0))
    sc = min(max(float(self_consumption_frac or 0), 0.0), 1.0)
    retail = float(tariff_per_kwh or 0)
    mode = resolve_net_metering_mode(net_metering_mode)
    self_kwh = gen * sc
    if annual_consumption_kwh is not None:
        self_kwh = min(self_kwh, max(0.0, float(annual_consumption_kwh)))
    export_kwh = gen - self_kwh
    if feed_in_tariff is None:
        feed_in_tariff = getattr(Config, "DEFAULT_FEED_IN_TARIFF", max(retail * 0.3, 0.0))
    fit = float(feed_in_tariff)

    if mode == "export_at_tariff":
        annual_savings = gen * retail
        export_value = export_kwh * retail
        export_compensation_basis = "retail_tariff_assumption"
    elif mode == "export_at_feed_in_rate":
        annual_savings = self_kwh * retail + export_kwh * fit
        export_value = export_kwh * fit
        export_compensation_basis = "feed_in_tariff_assumption"
    else:  # full_offset
        annual_savings = self_kwh * retail
        export_value = 0.0
        export_compensation_basis = "no_export_value_assumed"

    return {
        "net_metering_mode": mode,
        "self_consumed_kwh": round(self_kwh, 2),
        "exported_kwh": round(export_kwh, 2),
        "feed_in_tariff": round(fit, 2),
        "export_value": round(export_value, 2),
        "avoided_retail_value": round(self_kwh * retail, 2),
        "annual_savings": round(annual_savings, 2),
        "monthly_savings": round(annual_savings / 12, 2),
        "annual_consumption_kwh": (
            round(float(annual_consumption_kwh), 2)
            if annual_consumption_kwh is not None else None
        ),
        "self_consumption_basis": (
            "load_profile_assumption_capped_by_estimated_demand"
            if annual_consumption_kwh is not None
            else "load_profile_assumption_demand_unavailable"
        ),
        "export_compensation_basis": export_compensation_basis,
    }


def calculate_financials(
    system_size_kw,
    annual_generation_kwh,
    tariff_per_kwh,
    battery_kwh=0.0,
    inverter_type="on-grid",
    property_type="residential",
    load_profile="flat",
    net_metering_mode="full_offset",
    feed_in_tariff=None,
    annual_consumption_kwh=None,
    planning_track=None,
):
    if planning_track is None:
        planning_track = (
            "business"
            if str(property_type or "").lower()
            in ("commercial", "industrial", "msme", "factory")
            else "home"
        )
    if planning_track == "business":
        policy = commercial_calculation_service.resolve_policy(load_profile)
    else:
        planning_track = "home"
        policy = residential_calculation_service.resolve_policy(
            property_type, load_profile
        )
    property_type = policy["property_type"]
    load_profile = policy["default_load_profile"]
    unit_cost = policy["cost_per_kw"]
    cost_low = round(system_size_kw * policy["cost_per_kw_low"], 2)
    cost_high = round(system_size_kw * policy["cost_per_kw_high"], 2)

    system_cost = round(system_size_kw * unit_cost, 2)
    subsidy = (
        calculate_subsidy(
            system_size_kw, inverter_type=inverter_type, property_type=property_type
        )
        if policy["residential_subsidy_may_apply"]
        else 0.0
    )

    battery_cost = 0.0
    battery_kwh = max(0.0, float(battery_kwh or 0))
    if battery_kwh > 0:
        battery_cost = round(
            battery_kwh * getattr(Config, "BATTERY_COST_PER_KWH", 9000), 2
        )

    net_investment = round(max(system_cost + battery_cost - subsidy, 0), 2)
    annual_om = round(
        system_cost * get_value("om_percent", OM_PERCENT.value), 2
    )

    # Load-profile preset sets base self-consumption; battery boost curve unchanged.
    self_consumption_frac, profile_key, profile_meta = self_consumption_fraction(
        load_profile, battery_kwh
    )
    valuation = value_generation_kwh(
        annual_generation_kwh,
        self_consumption_frac,
        tariff_per_kwh,
        net_metering_mode=net_metering_mode,
        feed_in_tariff=feed_in_tariff,
        annual_consumption_kwh=annual_consumption_kwh,
    )
    usable_kwh = valuation["self_consumed_kwh"]
    annual_savings = valuation["annual_savings"]
    monthly_savings = valuation["monthly_savings"]
    emissions_factor = get_value(
        "grid_emissions_kg_co2_per_kwh", GRID_EMISSIONS_KG_CO2_PER_KWH.value
    )
    co2_reduction_tons = round(
        float(annual_generation_kwh or 0) * emissions_factor / 1000.0, 2
    )

    degradation = get_value("degradation_pct", DEGRADATION_PCT.value)
    tariff_escalation = get_value(
        "annual_tariff_escalation", ANNUAL_TARIFF_ESCALATION.value
    )
    lifecycle = build_lifecycle_projection(
        net_investment=net_investment,
        avoided_retail_value_year1=valuation["avoided_retail_value"],
        export_value_year1=valuation["export_value"],
        annual_om=annual_om,
        annual_generation_kwh=annual_generation_kwh,
        export_value_escalates=(valuation["net_metering_mode"] == "export_at_tariff"),
        degradation=degradation,
        tariff_escalation=tariff_escalation,
    )
    financial_inputs = {
        "assumption_fingerprint": current_assumption_fingerprint(),
        "generation_kwh": round(float(annual_generation_kwh or 0), 4),
        "system_size_kw": round(float(system_size_kw or 0), 6),
        "tariff_per_kwh": round(float(tariff_per_kwh or 0), 4),
        "feed_in_tariff_per_kwh": valuation["feed_in_tariff"],
        "net_metering_mode": valuation["net_metering_mode"],
        "self_consumption_fraction": self_consumption_frac,
        "annual_consumption_kwh": valuation["annual_consumption_kwh"],
        "system_cost": system_cost,
        "battery_cost": battery_cost,
        "subsidy_amount": subsidy,
        "planning_track": planning_track,
        "property_type": property_type,
    }
    financial_input_fingerprint = hashlib.sha256(
        json.dumps(financial_inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    cashflow_25yr = [row["cumulative_cash_flow"] for row in lifecycle["years"]]
    nominal_payback_years = lifecycle["nominal_payback_years"]
    discounted_payback_years = lifecycle["discounted_payback_years"]
    year_one_net_savings = round(annual_savings - annual_om, 2)
    simple_payback_years = _simple_payback(
        net_investment, annual_savings, annual_om
    )
    payback_without_subsidy_years = _simple_payback(
        system_cost + battery_cost, annual_savings, annual_om
    )
    # Primary payback shown in summaries = nominal (escalating tariff).
    payback_years = (
        nominal_payback_years
        if nominal_payback_years is not None
        else simple_payback_years
    )

    # Lifetime energy (25 yr with degradation)
    lifetime_kwh = round(lifecycle["lifetime_generation_kwh"], 0)
    lcoe = lifecycle["discounted_lcoe"]

    # Specific yield
    specific_yield = (
        round(annual_generation_kwh / system_size_kw, 0) if system_size_kw > 0 else 0
    )
    capacity_factor = (
        round((annual_generation_kwh / (system_size_kw * 8760)) * 100, 1)
        if system_size_kw > 0
        else 0
    )

    # EMI defaults: 5-year tenure, 10% p.a. (midpoint of 3–7 yr / 8–12%)
    emi = calculate_emi(net_investment, annual_rate_pct=10.0, tenure_years=5)
    monthly_net_savings = round(year_one_net_savings / 12.0, 2)
    emi["covers_emi"] = (
        bool(monthly_net_savings >= emi["monthly_emi"])
        if emi["monthly_emi"] else None
    )
    emi["monthly_savings"] = monthly_savings
    emi["monthly_net_savings_after_om"] = monthly_net_savings
    emi["net_after_emi"] = round(monthly_net_savings - emi["monthly_emi"], 2)

    sensitivity = build_sensitivity_table(
        system_size_kw=system_size_kw,
        annual_generation_kwh=annual_generation_kwh,
        tariff_per_kwh=tariff_per_kwh,
        system_cost=system_cost,
        battery_cost=battery_cost,
        subsidy_amount=subsidy,
        self_consumption_frac=self_consumption_frac,
        net_metering_mode=valuation["net_metering_mode"],
        feed_in_tariff=valuation["feed_in_tariff"],
        annual_consumption_kwh=valuation["annual_consumption_kwh"],
    )

    # CAPEX vs DPA financing comparison (PPA deferred per PRD)
    financing_comparison = build_financing_comparison(
        net_investment=net_investment,
        annual_savings=annual_savings,
        monthly_savings=monthly_savings,
        annual_generation_kwh=annual_generation_kwh,
        down_payment_pct=20.0,
        dpa_tenure_years=5,
        dpa_rate_pct=10.0,
        annual_net_cashflows=[row["net_cash_flow"] for row in lifecycle["years"]],
        annual_om=annual_om,
    )
    generation_25yr = financing_comparison.get("generation_25yr") or _annual_generation_series(
        annual_generation_kwh
    )

    return {
        "planning_track": planning_track,
        "property_type": property_type,
        "calculation_policy": policy,
        "system_cost": system_cost,
        "system_cost_low": cost_low,
        "system_cost_high": cost_high,
        "cost_per_kw": unit_cost,
        "battery_cost": battery_cost,
        "battery_kwh": battery_kwh,
        "subsidy_amount": subsidy,
        "net_investment": net_investment,
        "annual_savings": annual_savings,
        "year_one_net_savings": year_one_net_savings,
        "annual_net_benefit": year_one_net_savings,
        "annual_roi_pct": (
            round((year_one_net_savings / net_investment) * 100, 2)
            if net_investment > 0 else None
        ),
        "lifetime_net_benefit": (
            lifecycle["years"][-1]["cumulative_cash_flow"]
            if lifecycle.get("years") else None
        ),
        "demand_charge_savings": None,
        "payback_without_subsidy_years": payback_without_subsidy_years,
        "subsidy_adjusted_payback_years": simple_payback_years,
        "monthly_savings": monthly_savings,
        "annual_om": annual_om,
        "co2_reduction_tons": co2_reduction_tons,
        "cashflow_25yr": cashflow_25yr,
        "emi": emi,
        "financing_snapshot": financing_snapshot(net_investment, monthly_savings),
        "financing_comparison": financing_comparison,
        "generation_25yr": generation_25yr,
        "sensitivity": sensitivity,
        "tariff_escalation_pct": round(tariff_escalation * 100, 2),
        "simple_payback_years": simple_payback_years,
        "nominal_payback_years": nominal_payback_years,
        "discounted_payback_years": discounted_payback_years,
        "npv": lifecycle["npv"],
        "discount_rate_pct": lifecycle["discount_rate_pct"],
        "lifecycle_projection": lifecycle,

        "payback_years": payback_years,
        "self_consumption_frac": round(self_consumption_frac, 2),
        "self_consumed_kwh": valuation["self_consumed_kwh"],
        "load_profile": profile_key,
        "load_profile_label": profile_meta.get("label"),
        "load_profile_note": profile_meta.get("note"),
        "net_metering_mode": valuation["net_metering_mode"],
        "exported_kwh": valuation["exported_kwh"],
        "export_value": valuation["export_value"],
        "avoided_retail_value": valuation["avoided_retail_value"],
        "feed_in_tariff": valuation["feed_in_tariff"],
        "financial_provenance": {
            "model_version": FINANCIAL_MODEL_VERSION,
            "input_fingerprint": financial_input_fingerprint,
            "inputs": financial_inputs,
            "generation_input_kwh": round(float(annual_generation_kwh or 0), 2),
            "self_consumption_basis": valuation["self_consumption_basis"],
            "annual_consumption_kwh": valuation["annual_consumption_kwh"],
            "export_compensation_basis": valuation["export_compensation_basis"],
            "tariff_per_kwh": round(float(tariff_per_kwh or 0), 2),
            "feed_in_tariff_per_kwh": valuation["feed_in_tariff"],
            "discount_rate_pct": lifecycle["discount_rate_pct"],
            "om_treatment": "constant_nominal_annual_reserve_subtracted_once",
            "environmental_factor": {
                "value": emissions_factor,
                "units": "kg_CO2_per_kWh",
                "source": GRID_EMISSIONS_KG_CO2_PER_KWH.source,
                "scope": "India_grid_weighted_average_planning_estimate",
            },
        },
        "lifetime_kwh": lifetime_kwh,
        "lcoe": lcoe,
        "specific_yield": specific_yield,
        "capacity_factor": capacity_factor,
        "environmental_equivalents": calculate_environmental_equivalents(
            co2_reduction_tons
        ),
        "planning_notes": [
            "kWp is DC capacity; usable energy is in kWh (units). Larger kWp does not provide backup unless hybrid/off-grid with battery.",

            "Grid-tied on-grid inverters normally shut down during a utility outage (anti-islanding).",
            "Performance ratio ~0.75–0.85 includes temperature, dust, mismatch, wiring and inverter losses.",
            "Indicative generation ~4–5.5 units/day per kWp on suitable days; shade and orientation matter.",
            "Residential installed cost often ~₹55,000–85,000/kW; commercial EPC ~₹35,000–50,000/kW at scale.",
            "Subsidy, net-metering and DISCOM approval are required before treating savings as certain.",
            "O&M and module cleaning are subtracted as a constant nominal annual reserve; panel degradation ~0.5%/year is applied to energy value.",
            "Electricity tariff is assumed to escalate ~4%/year; fixed feed-in compensation does not escalate. Simple payback uses year-one net savings.",
        ],
    }



def estimate_panel_count(system_size_kw, module_wattage=None):
    """Approximate module count for a DC system size.

    panel_count ≈ ceil(kW * 1000 / module_W). Uses assumptions.module_wattage
    (default 545 W). Returns None for non-positive sizes.
    """
    import math

    kw = float(system_size_kw or 0)
    if kw <= 0:
        return None
    watts = float(
        module_wattage
        if module_wattage is not None
        else get_value("module_wattage", MODULE_WATTAGE.value)
    )
    if watts <= 0:
        return None
    count = int(math.ceil((kw * 1000.0) / watts))
    return {
        "panel_count": count,
        "module_wattage_w": round(watts, 0),
        "label": f"approximately {count} panels",
        "note": f"Based on {watts:.0f} W modules — indicative count only, not a layout.",
    }


def calculate_accepted_surface_generation(
    accepted_surfaces,
    irradiance_profile,
    temperature_profile=None,
    fallback_azimuth=180.0,
    profile_kind="daily_horizontal_irradiance_kwh_per_m2",
):
    """Aggregate accepted capacity surface-by-surface without inventing POA data.

    Location irradiance is adjusted with the existing azimuth factor only; tilt
    is recorded but not modelled because the current inputs do not support a
    defensible transposition calculation. A provider annual-flux specific yield
    is used directly only with explicit units and complete panel coverage.
    """
    if not isinstance(accepted_surfaces, list) or not accepted_surfaces:
        return None
    monthly_total = {month: 0.0 for month in MONTH_NAMES}
    breakdown = []
    total_capacity = 0.0
    total_panels = 0
    weighted_orientation = 0.0
    provider_direct = 0
    for index, item in enumerate(accepted_surfaces):
        if not isinstance(item, dict):
            raise ValueError("accepted surface generation inputs must be objects")
        panel_count = item.get("panel_count")
        capacity = item.get("capacity_kw")
        if (isinstance(panel_count, bool) or not isinstance(panel_count, int)
                or panel_count < 1):
            raise ValueError("accepted surface panel_count must be positive")
        try:
            capacity = float(capacity)
        except (TypeError, ValueError) as exc:
            raise ValueError("accepted surface capacity_kw must be finite") from exc
        if not math.isfinite(capacity) or capacity <= 0:
            raise ValueError("accepted surface capacity_kw must be finite and positive")

        pitch = item.get("pitch_degrees")
        azimuth = item.get("azimuth_degrees")
        try:
            pitch = float(pitch) if pitch is not None else None
            azimuth = float(azimuth) if azimuth is not None else None
        except (TypeError, ValueError):
            pitch = azimuth = None
        orientation_usable = (
            pitch is not None and azimuth is not None
            and math.isfinite(pitch) and math.isfinite(azimuth)
            and 0 < pitch < 89 and 0 <= azimuth <= 360
        )
        effective_azimuth = azimuth % 360 if orientation_usable else float(fallback_azimuth) % 360
        provider_monthly_yield = profile_kind == PVGIS_PROFILE_KIND
        factor = orientation_factor(effective_azimuth)
        base_monthly = (
            calculate_monthly_generation_from_specific_yield(capacity, irradiance_profile)
            if provider_monthly_yield
            else calculate_monthly_generation(capacity, irradiance_profile, temperature_profile)
        )
        shade_input = item.get("quantitative_flux") or {}
        direct_yield = shade_input.get("specific_yield_kwh_per_kw_year")
        direct_valid = (
            shade_input.get("source") == "provider_annual_flux"
            and shade_input.get("units") == "kWh_per_kW_per_year"
            and shade_input.get("coverage_panels") == panel_count
        )
        try:
            direct_yield = float(direct_yield)
        except (TypeError, ValueError):
            direct_valid = False
        direct_valid = direct_valid and math.isfinite(direct_yield) and direct_yield >= 0
        if direct_valid:
            annual = capacity * direct_yield
            weight_total = sum(base_monthly.values())
            monthly = {
                month: (annual * base_monthly[month] / weight_total if weight_total else 0.0)
                for month in MONTH_NAMES
            }
            model = "provider_annual_flux_direct"
            provider_direct += 1
            shade_status = "provider_flux_incorporated_not_isolated"
            applied_factor = None
        elif provider_monthly_yield:
            monthly = base_monthly
            annual = sum(monthly.values())
            model = "pvgis_monthly_specific_yield_direct"
            shade_status = item.get("shade_status") or "unknown_not_quantified"
            applied_factor = None
        else:
            monthly = {month: value * factor for month, value in base_monthly.items()}
            annual = sum(monthly.values())
            model = "location_irradiance_azimuth_adjustment"
            shade_status = item.get("shade_status") or "unknown_not_quantified"
            applied_factor = factor
            weighted_orientation += capacity * factor
        rounded_monthly = {month: round(value, 1) for month, value in monthly.items()}
        annual = round(sum(rounded_monthly.values()), 1)
        for month in MONTH_NAMES:
            monthly_total[month] += rounded_monthly[month]
        total_capacity += capacity
        total_panels += panel_count
        breakdown.append({
            "surface_id": item.get("surface_id") or f"surface-{index + 1}",
            "panel_count": panel_count,
            "capacity_kw": round(capacity, 6),
            "pitch_degrees": pitch,
            "azimuth_degrees": azimuth,
            "effective_azimuth_degrees": effective_azimuth if not direct_valid else None,
            "orientation_source": (
                "provider_model_not_separately_adjusted"
                if provider_monthly_yield and not direct_valid
                else ("surface_metadata" if orientation_usable else "global_fallback")
            ),
            "orientation_factor": applied_factor,
            "tilt_model": (
                "provider_model_not_separately_adjusted"
                if provider_monthly_yield and not direct_valid
                else "not_applied_no_plane_of_array_irradiance"
            ),
            "shade_status": shade_status,
            "shade_loss_percent": None,
            "generation_model": model,
            "monthly_generation_kwh": rounded_monthly,
            "annual_generation_kwh": annual,
            "quantitative_flux": shade_input if direct_valid else None,
        })
    monthly_total = {month: round(value, 1) for month, value in monthly_total.items()}
    return {
        "schema_version": "1.0",
        "model": "mixed" if 0 < provider_direct < len(breakdown) else (
            "provider_annual_flux_direct" if provider_direct == len(breakdown)
            else (
                "pvgis_monthly_specific_yield_direct"
                if profile_kind == PVGIS_PROFILE_KIND
                else "location_irradiance_azimuth_adjustment"
            )
        ),
        "surface_count": len(breakdown),
        "panel_count": total_panels,
        "capacity_kw": round(total_capacity, 6),
        "monthly_generation_kwh": monthly_total,
        "annual_generation_kwh": round(sum(monthly_total.values()), 1),
        "weighted_orientation_factor": (
            round(weighted_orientation / total_capacity, 3)
            if total_capacity and provider_direct == 0 and profile_kind != PVGIS_PROFILE_KIND else None
        ),
        "surfaces": breakdown,
        "limitations": [
            "Tilt is recorded but not transposed because plane-of-array irradiance is unavailable.",
            "Qualitative shaded/unknown classifications are not converted to numeric shade losses.",
            "Provider annual flux is a combined production input; its shade component is not isolated as a percentage.",
        ],
    }

def run_full_estimation(
    latitude,
    longitude,
    coordinates,
    obstructions=None,
    tariff_per_kwh=Config.DEFAULT_TARIFF,
    orientation="auto",
    battery_kwh=0.0,
    monthly_bill=None,
    property_type="residential",
    planning_track=None,
    user_priority="no_preference",
    needs_backup=False,
    inverter_preference="auto",
    sizing_mode=None,
    budget_inr=None,
    critical_load_kw=None,
    backup_hours=None,
    load_profile="flat",
    net_metering_mode="full_offset",
    feed_in_tariff=None,
    accepted_panel_count=None,
    accepted_module_wattage=None,
    accepted_surfaces=None,
):
    """End-to-end estimation pipeline used by the /api/estimate route.

    Sizing strategy (see ``compute_system_sizing``):
      - roof_max, bill_offset_X%, fastest_payback, max_25yr_savings
      - Default: bill_offset_100% when monthly_bill is set, else roof_max
        (preserves legacy min(roof, bill) behaviour)

    Battery: if needs_backup and battery_kwh is 0, size_battery_kwh() sizes
    storage from critical_load_kw × backup_hours (defaults 1 kW × 4 h).
    Financial model already folds battery cost — we only supply kWh.

    Inverter recommendation follows On-Grid / Hybrid / Off-Grid guidance
    based on backup need, battery, and property type.
    """
    import time

    total_start = time.perf_counter()

    if planning_track is None:
        planning_track = (
            "business"
            if str(property_type or "").lower()
            in ("commercial", "industrial", "msme", "factory")
            else "home"
        )
    if planning_track == "business":
        policy = commercial_calculation_service.resolve_policy(load_profile)
    else:
        planning_track = "home"
        policy = residential_calculation_service.resolve_policy(
            property_type, load_profile
        )
    property_type = policy["property_type"]
    load_profile = policy["default_load_profile"]

    roof_area_start = time.perf_counter()
    roof_area_sqm = calculate_polygon_area_sqm(coordinates)
    usable_area_sqm, obstructed_area_sqm = calculate_usable_area(
        roof_area_sqm, obstructions
    )
    roof_area_elapsed = (time.perf_counter() - roof_area_start) * 1000
    print(
        f"[TIMING] run_full_estimation roof area calc elapsed: {roof_area_elapsed:.2f} ms"
    )

    sizing = compute_system_sizing(
        usable_area_sqm=usable_area_sqm,
        monthly_bill=monthly_bill,
        tariff_per_kwh=tariff_per_kwh,
        sizing_mode=sizing_mode,
        budget_inr=budget_inr,
        planning_track=planning_track,
        property_type=property_type,
    )
    roof_capacity_kw = sizing["roof_capacity_kw"]
    system_size_kw = sizing["recommended_kw"]
    bill_sizing = sizing.get("bill_sizing")
    sizing_method = sizing["mode"]
    if accepted_panel_count is not None:
        if isinstance(accepted_panel_count, bool) or int(accepted_panel_count) != accepted_panel_count or accepted_panel_count < 1:
            raise ValueError("accepted_panel_count must be a positive whole number")
        module_wattage = float(
            accepted_module_wattage
            if accepted_module_wattage is not None
            else get_value("module_wattage", MODULE_WATTAGE.value)
        )
        if not math.isfinite(module_wattage) or module_wattage <= 0:
            raise ValueError("accepted_module_wattage must be positive")
        system_size_kw = round(int(accepted_panel_count) * module_wattage / 1000, 6)
        sizing_method = "accepted_layout"
        sizing = {
            **sizing,
            "mode": sizing_method,
            "recommended_kw": system_size_kw,
            "rationale": "Capacity derived from a server-validated accepted panel layout.",
        }

    # Auto-size battery when backup is requested but no explicit kWh was sent.
    battery_sizing = None
    battery_kwh = max(0.0, float(battery_kwh or 0))
    if bool(needs_backup) and battery_kwh <= 0:
        load_kw = (
            float(critical_load_kw)
            if critical_load_kw is not None and float(critical_load_kw or 0) > 0
            else 1.0
        )
        hours = (
            float(backup_hours)
            if backup_hours is not None and float(backup_hours or 0) > 0
            else 4.0
        )
        battery_sizing = size_battery_kwh(load_kw, backup_hours=hours)
        battery_kwh = float(battery_sizing["nominal_battery_kwh"])
    elif battery_kwh > 0:
        battery_sizing = {
            "nominal_battery_kwh": round(battery_kwh, 2),
            "note": "User-selected battery capacity",
        }

    inverter = recommend_inverter(
        property_type=property_type,
        needs_backup=needs_backup or float(battery_kwh or 0) > 0,
        battery_kwh=battery_kwh,
        preferred=inverter_preference,
    )
    inverter_type_key = inverter["type"].lower().replace(" ", "-")

    az, az_label, orient_factor, orient_source = resolve_orientation(
        orientation, coordinates
    )
    recommended_tilt = round(abs(latitude), 1)

    irradiance_start = time.perf_counter()
    irradiance_profile, temperature_profile, source, confidence_tier = fetch_solar_data(
        latitude, longitude, system_size_kw
    )
    # Normalize for API/UI while keeping pipeline source for cache keys / logs.
    normalized_source, confidence_tier = classify_irradiance_source(source)
    profile_kind = (
        PVGIS_PROFILE_KIND
        if source == "pvgis"
        else "daily_horizontal_irradiance_kwh_per_m2"
    )
    irradiance_elapsed = (time.perf_counter() - irradiance_start) * 1000
    print(
        f"[TIMING] run_full_estimation irradiance fetch elapsed: {irradiance_elapsed:.2f} ms "
        f"source={source} confidence={confidence_tier}"
    )

    generation_start = time.perf_counter()
    surface_generation = calculate_accepted_surface_generation(
        accepted_surfaces,
        irradiance_profile,
        temperature_profile,
        az,
        profile_kind=profile_kind,
    ) if accepted_surfaces else None
    if surface_generation:
        if surface_generation["panel_count"] != int(accepted_panel_count or 0):
            raise ValueError("accepted surface panel totals do not match the accepted layout")
        if abs(surface_generation["capacity_kw"] - system_size_kw) > 1e-6:
            raise ValueError("accepted surface capacity does not match the accepted layout")
        monthly_generation = surface_generation["monthly_generation_kwh"]
    else:
        monthly_generation = (
            calculate_monthly_generation_from_specific_yield(
                system_size_kw, irradiance_profile
            )
            if profile_kind == PVGIS_PROFILE_KIND
            else calculate_monthly_generation(
                system_size_kw, irradiance_profile, temperature_profile
            )
        )
    generation_elapsed = (time.perf_counter() - generation_start) * 1000
    print(
        f"[TIMING] run_full_estimation monthly generation calc elapsed: {generation_elapsed:.2f} ms"
    )

    # Apply orientation derating
    if not surface_generation and profile_kind != PVGIS_PROFILE_KIND:
        monthly_generation = {
            m: round(v * orient_factor, 1) for m, v in monthly_generation.items()
        }
    annual_generation = round(sum(monthly_generation.values()), 1)
    location_loss_model_used = (
        not surface_generation
        or any(
            surface.get("generation_model")
            == "location_irradiance_azimuth_adjustment"
            for surface in surface_generation.get("surfaces", [])
        )
    )

    # Also expose rule-of-thumb generation (135 units/kW/month) for comparison
    thumb_monthly = round(
        system_size_kw * getattr(Config, "MONTHLY_UNITS_PER_KW", 135.0), 1
    )
    thumb_annual = round(thumb_monthly * 12, 1)

    financial_start = time.perf_counter()
    financials = calculate_financials(
        system_size_kw,
        annual_generation,
        tariff_per_kwh,
        battery_kwh=battery_kwh,
        inverter_type=inverter_type_key,
        property_type=property_type,
        load_profile=load_profile,
        net_metering_mode=net_metering_mode,
        feed_in_tariff=feed_in_tariff,
        annual_consumption_kwh=(
            float(bill_sizing["monthly_units"]) * 12
            if bill_sizing and bill_sizing.get("monthly_units") is not None
            else None
        ),
        planning_track=planning_track,
    )
    financial_elapsed = (time.perf_counter() - financial_start) * 1000
    print(
        f"[TIMING] run_full_estimation financial projection elapsed: {financial_elapsed:.2f} ms"
    )

    result = {
        "roof_area_sqm": round(roof_area_sqm, 2),
        "obstructed_area_sqm": round(obstructed_area_sqm, 2),
        "usable_area_sqm": round(usable_area_sqm, 2),
        "roof_capacity_kw": roof_capacity_kw,
        "system_size": system_size_kw,
        "panel_count": int(accepted_panel_count) if accepted_panel_count is not None else (estimate_panel_count(system_size_kw) or {}).get("panel_count"),
        "panel_estimate": ({
            "panel_count": int(accepted_panel_count),
            "module_wattage_w": round(module_wattage, 0),
            "note": "Exact count from the server-validated accepted layout.",
        } if accepted_panel_count is not None else estimate_panel_count(system_size_kw)),
        "sizing_method": sizing_method,
        "sizing": {
            "mode": sizing["mode"],
            "min_kw": sizing["min_kw"],
            "recommended_kw": sizing["recommended_kw"],
            "max_kw": sizing["max_kw"],
            "rationale": sizing["rationale"],
            "constraints": sizing["constraints"],
        },
        "annual_generation": annual_generation,
        "monthly_data": monthly_generation,
        "rule_of_thumb_monthly_units": thumb_monthly,
        "rule_of_thumb_annual_units": thumb_annual,
        # Public source tags: nasa_power | pvgis | mock (mock_fallback normalized)
        "irradiance_source": normalized_source,
        "irradiance_confidence": confidence_tier,  # high | medium | low
        "temperature_derating_applied": (
            temperature_profile is not None
            and profile_kind != PVGIS_PROFILE_KIND
            and location_loss_model_used
        ),
        "orientation_deg": az,
        "orientation_label": az_label,
        "orientation_factor": orient_factor,
        "orientation_source": orient_source,
        "recommended_tilt_deg": recommended_tilt,
        "inverter": inverter,
        "inverter_type": inverter["type"],
        "property_type": property_type,
        "planning_track": planning_track,
        "needs_backup": bool(needs_backup) or float(battery_kwh or 0) > 0,
        "performance_ratio": get_value(
            "performance_ratio", PERFORMANCE_RATIO.value
        ),
        "peak_sun_hours_assumed": getattr(Config, "DEFAULT_PEAK_SUN_HOURS", 5.0),
        "daily_units_per_kw_range": [
            getattr(Config, "DAILY_UNITS_PER_KW_LOW", 4.0),
            getattr(Config, "DAILY_UNITS_PER_KW_HIGH", 5.5),
        ],

        "dc_note": "System size is DC kWp. AC output depends on PR, irradiance and grid availability.",
        **financials,
    }
    if profile_kind == PVGIS_PROFILE_KIND:
        result["orientation_factor"] = None
        result["orientation_source"] = "provider_model_not_separately_adjusted"
    if surface_generation:
        result["surface_generation"] = surface_generation
        result["generation_model"] = surface_generation["model"]
        result["orientation_factor"] = surface_generation.get("weighted_orientation_factor")
        result["orientation_source"] = "accepted_surface_metadata"
        if profile_kind == PVGIS_PROFILE_KIND:
            result["orientation_source"] = "provider_model_not_separately_adjusted"
    result["suitability_rating"] = calculate_suitability_rating(
        result, user_priority=user_priority or "no_preference"
    )
    if bill_sizing:
        result["bill_sizing"] = bill_sizing
    if battery_sizing:
        result["battery_sizing"] = battery_sizing
    return result

