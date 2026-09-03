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

import math
import requests
from app.config import Config
from app.services.cache import get_cached_irradiance, set_cached_irradiance

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
    """1 kWp per Config.SQM_PER_KWP sq. meters, prototype assumption."""
    return round(area_sqm / Config.SQM_PER_KWP, 2)


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


def _mock_irradiance_profile(latitude):
    """Deterministic mock monthly irradiance (kWh/m^2/day) as a safe
    offline fallback. Latitude nudges the seasonal amplitude slightly so
    northern vs southern locations feel distinct, without needing a real
    climate model."""
    hemisphere_shift = 0 if latitude >= 0 else 6  # flip seasons below equator
    profile = MOCK_MONTHLY_PSH[hemisphere_shift:] + MOCK_MONTHLY_PSH[:hemisphere_shift]
    return {MONTH_NAMES[i]: profile[i] for i in range(12)}


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
    Returns dict: {"Jan": kWh/kWp/day-equivalent monthly PSH, ...}, source
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
            days_in_month = 30.44
            psh = row["E_m"] / (peak_power_kw * days_in_month)
            result[MONTH_NAMES[month_idx]] = round(psh, 2)
        return result, "pvgis"
    except Exception:
        pass

    return _mock_irradiance_profile(latitude), "mock_fallback"


def fetch_solar_data(latitude, longitude, peak_power_kw=1.0):
    """
    Combined fetch pipeline, NASA POWER first:
      1. NASA POWER (irradiance + temperature -> best accuracy, used for
         both the irradiance profile and the temperature-derating term)
      2. PVGIS via pvlib (irradiance only, no temperature derating applied)
      3. Deterministic mock profile (fully offline fallback)
    Returns (irradiance_profile, temperature_profile_or_None, source)
    """
    cached = get_cached_irradiance(latitude, longitude)
    if cached is not None:
        return cached["irradiance"], cached["temperature"], cached["source"]

    irradiance, temperature, source = fetch_irradiance_nasa_power(latitude, longitude)
    if irradiance:
        result = irradiance, temperature, source
        set_cached_irradiance(
            latitude,
            longitude,
            {
                "irradiance": result[0],
                "temperature": result[1],
                "source": result[2],
            },
            ttl_days=30,
        )
        return result

    irradiance, source = fetch_irradiance_pvgis(latitude, longitude, peak_power_kw)
    result = irradiance, None, source
    cached_data = {
        "irradiance": result[0],
        "temperature": result[1],
        "source": result[2],
    }
    if source == "mock_fallback":
        set_cached_irradiance(latitude, longitude, cached_data, ttl_hours=1)
    else:
        set_cached_irradiance(latitude, longitude, cached_data, ttl_days=30)
    return result


def calculate_monthly_generation(
    system_size_kw, irradiance_profile, temperature_profile=None, performance_ratio=0.80
):
    """Monthly generation (kWh) = system_size(kWp) * PSH(kWh/m2/day) *
    days_in_month * effective_performance_ratio.

    When a temperature profile is available (NASA POWER's T2M), applies a
    standard crystalline-silicon temperature derating on top of the base
    performance ratio: panel output drops ~0.4%/°C above 25°C ambient
    (a widely used industry coefficient), giving a more accurate monthly
    split than a single flat performance ratio - e.g. hotter summer months
    yield slightly less per unit of irradiance than cooler months.
    """
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


def calculate_subsidy(
    system_size_kw, inverter_type="on-grid", property_type="residential"
):
    """PM Surya Ghar central assistance (published structure, Aug 2026 guide):

      • ₹30,000 / kW for the first 2 kW
      • ₹18,000 for the additional kW from 2–3 kW
      • Maximum ₹78,000

    Example: 1 kW → 30k; 2 kW → 60k; 2.5 kW → 60k + 0.5×18k = 69k; 3 kW+ → 78k.

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

    first2 = getattr(Config, "SUBSIDY_PER_KW_FIRST_2", 30000.0)
    extra = getattr(Config, "SUBSIDY_EXTRA_2_TO_3", 18000.0)
    cap = getattr(Config, "SUBSIDY_CAP", 78000.0)

    if size <= 2.0:
        amount = size * first2
    elif size <= 3.0:
        amount = 2.0 * first2 + (size - 2.0) * extra
    else:
        amount = 2.0 * first2 + extra  # = 78,000
    return round(min(amount, cap), 2)


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
    pr = float(getattr(Config, "DEFAULT_PERFORMANCE_RATIO", 0.80))
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


def size_battery_kwh(critical_load_kwh, backup_hours=None, dod=None, rte=None):
    """Nominal battery kWh from critical-load energy (guide formula).

    nominal kWh = critical energy during backup ÷ (DoD × round-trip efficiency)
    Example: 4 kWh critical, 80% DoD, 90% RTE → ~5.6 kWh nominal.
    """
    dod = dod if dod is not None else getattr(Config, "BATTERY_DEFAULT_DOD", 0.80)
    rte = rte if rte is not None else getattr(Config, "BATTERY_DEFAULT_RTE", 0.90)
    energy = max(0.0, float(critical_load_kwh or 0))
    if backup_hours is not None and energy > 0:
        # If user passed kW continuous instead of energy, scale by hours
        pass
    denom = max(dod * rte, 0.05)
    nominal = energy / denom if energy else 0.0
    return {
        "critical_load_kwh": energy,
        "depth_of_discharge": dod,
        "round_trip_efficiency": rte,
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
    India (approximate, based on typical Indian PV literature).
    South ≈ 1.00, SE/SW ≈ 0.94–0.96, E/W ≈ 0.82–0.88, North ≈ 0.65–0.72.
    """
    # Distance from south (180)
    d = min(abs((azimuth_deg % 360) - 180), 360 - abs((azimuth_deg % 360) - 180))
    # Cosine-like falloff
    factor = 0.70 + 0.30 * math.cos(math.radians(d * 1.1))
    return round(max(0.65, min(1.0, factor)), 3)


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


def calculate_financials(
    system_size_kw,
    annual_generation_kwh,
    tariff_per_kwh,
    battery_kwh=0.0,
    inverter_type="on-grid",
    property_type="residential",
):
    prop = (property_type or "residential").lower()
    if prop in ("commercial", "industrial", "msme", "factory"):
        unit_cost = getattr(Config, "COMMERCIAL_COST_PER_KW", 42000)
        cost_band = (
            getattr(Config, "COMMERCIAL_COST_PER_KW", 35000),
            getattr(Config, "COMMERCIAL_COST_PER_KW", 50000),
        )
        # commercial guide band 35–50k; use midpoint already
        cost_low = round(system_size_kw * 35000, 2)
        cost_high = round(system_size_kw * 50000, 2)
    else:
        unit_cost = Config.SYSTEM_COST_PER_KW
        cost_low = round(
            system_size_kw * getattr(Config, "SYSTEM_COST_PER_KW_LOW", 55000), 2
        )
        cost_high = round(
            system_size_kw * getattr(Config, "SYSTEM_COST_PER_KW_HIGH", 85000), 2
        )

    system_cost = round(system_size_kw * unit_cost, 2)
    subsidy = calculate_subsidy(
        system_size_kw, inverter_type=inverter_type, property_type=property_type
    )

    battery_cost = 0.0
    battery_kwh = max(0.0, float(battery_kwh or 0))
    if battery_kwh > 0:
        battery_cost = round(
            battery_kwh * getattr(Config, "BATTERY_COST_PER_KWH", 9000), 2
        )

    net_investment = round(max(system_cost + battery_cost - subsidy, 0), 2)
    annual_om = round(system_cost * getattr(Config, "OM_PERCENT_PER_YEAR", 0.012), 2)

    # Without battery, assume ~55% of generation offsets bill (export/import mix).
    # With battery, self-consumption rises (more of the generation is used on-site).
    self_consumption_frac = 0.55
    if battery_kwh > 0:
        # Scale: 5 kWh → ~0.72, 10 kWh → ~0.82, 20 kWh → ~0.90 (capped)
        boost = min(0.35, 0.12 + 0.03 * battery_kwh)
        self_consumption_frac = min(0.92, 0.55 + boost)

    usable_kwh = annual_generation_kwh * self_consumption_frac
    annual_savings = round(usable_kwh * tariff_per_kwh, 2)
    monthly_savings = round(annual_savings / 12, 2)
    co2_reduction_tons = round(system_size_kw * Config.CO2_TONS_PER_KW_YEAR, 2)

    # 25-year cumulative cashflow with mild panel degradation (0.5%/yr)
    # and flat tariff (prototype).
    cashflow_25yr = []
    cumulative = -net_investment
    degradation = 0.005
    for year in range(1, 26):
        year_factor = (1 - degradation) ** (year - 1)
        cumulative += annual_savings * year_factor
        cashflow_25yr.append(round(cumulative, 2))

    payback_years = (
        round(net_investment / annual_savings, 1) if annual_savings > 0 else None
    )

    # Lifetime energy (25 yr with degradation)
    lifetime_kwh = round(
        sum(
            annual_generation_kwh * ((1 - degradation) ** (y - 1)) for y in range(1, 26)
        ),
        0,
    )

    # Simple LCOE (₹/kWh) = net investment / lifetime kWh
    lcoe = round(net_investment / lifetime_kwh, 2) if lifetime_kwh > 0 else None

    # Specific yield
    specific_yield = (
        round(annual_generation_kwh / system_size_kw, 0) if system_size_kw > 0 else 0
    )
    capacity_factor = (
        round((annual_generation_kwh / (system_size_kw * 8760)) * 100, 1)
        if system_size_kw > 0
        else 0
    )

    return {
        "system_cost": system_cost,
        "system_cost_low": cost_low,
        "system_cost_high": cost_high,
        "cost_per_kw": unit_cost,
        "battery_cost": battery_cost,
        "battery_kwh": battery_kwh,
        "subsidy_amount": subsidy,
        "net_investment": net_investment,
        "annual_savings": annual_savings,
        "monthly_savings": monthly_savings,
        "annual_om": annual_om,
        "co2_reduction_tons": co2_reduction_tons,
        "cashflow_25yr": cashflow_25yr,
        "payback_years": payback_years,
        "self_consumption_frac": round(self_consumption_frac, 2),
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
            "O&M and module cleaning should be budgeted annually; panel degradation ~0.5%/year assumed in cashflow.",
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
    needs_backup=False,
    inverter_preference="auto",
):
    """End-to-end estimation pipeline used by the /api/estimate route.

    Sizing strategy:
      1. Roof usable area → maximum installable kW (physical limit).
      2. Optional monthly electricity bill → recommended kW using
         ₹10/unit and 135 units/month/kW (Indian rule of thumb).
      3. Final system size = min(roof capacity, bill recommendation)
         when bill is provided; otherwise roof capacity (floor 0.5 kW).

    Inverter recommendation follows On-Grid / Hybrid / Off-Grid guidance
    based on backup need, battery, and property type.
    """
    import time

    total_start = time.perf_counter()

    roof_area_start = time.perf_counter()
    roof_area_sqm = calculate_polygon_area_sqm(coordinates)
    usable_area_sqm, obstructed_area_sqm = calculate_usable_area(
        roof_area_sqm, obstructions
    )
    roof_area_elapsed = (time.perf_counter() - roof_area_start) * 1000
    print(
        f"[TIMING] run_full_estimation roof area calc elapsed: {roof_area_elapsed:.2f} ms"
    )

    roof_capacity_kw = area_to_system_size(usable_area_sqm)
    roof_capacity_kw = max(roof_capacity_kw, 0.5)

    bill_sizing = None
    if monthly_bill is not None and float(monthly_bill or 0) > 0:
        bill_sizing = size_from_monthly_bill(monthly_bill, tariff_per_kwh)
        # Prefer bill-based size but never exceed what the roof can hold
        system_size_kw = min(bill_sizing["recommended_kw"], roof_capacity_kw)
        system_size_kw = max(system_size_kw, 0.5)
        sizing_method = "bill_capped_by_roof"
    else:
        system_size_kw = roof_capacity_kw
        sizing_method = "roof_area"

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
    irradiance_profile, temperature_profile, source = fetch_solar_data(
        latitude, longitude, system_size_kw
    )
    irradiance_elapsed = (time.perf_counter() - irradiance_start) * 1000
    print(
        f"[TIMING] run_full_estimation irradiance fetch elapsed: {irradiance_elapsed:.2f} ms"
    )

    generation_start = time.perf_counter()
    monthly_generation = calculate_monthly_generation(
        system_size_kw, irradiance_profile, temperature_profile
    )
    generation_elapsed = (time.perf_counter() - generation_start) * 1000
    print(
        f"[TIMING] run_full_estimation monthly generation calc elapsed: {generation_elapsed:.2f} ms"
    )

    # Apply orientation derating
    monthly_generation = {
        m: round(v * orient_factor, 1) for m, v in monthly_generation.items()
    }
    annual_generation = round(sum(monthly_generation.values()), 1)

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
        "sizing_method": sizing_method,
        "annual_generation": annual_generation,
        "monthly_data": monthly_generation,
        "rule_of_thumb_monthly_units": thumb_monthly,
        "rule_of_thumb_annual_units": thumb_annual,
        "irradiance_source": source,
        "temperature_derating_applied": temperature_profile is not None,
        "orientation_deg": az,
        "orientation_label": az_label,
        "orientation_factor": orient_factor,
        "orientation_source": orient_source,
        "recommended_tilt_deg": recommended_tilt,
        "inverter": inverter,
        "inverter_type": inverter["type"],
        "property_type": property_type,
        "needs_backup": bool(needs_backup) or float(battery_kwh or 0) > 0,
        "performance_ratio": getattr(Config, "DEFAULT_PERFORMANCE_RATIO", 0.80),
        "peak_sun_hours_assumed": getattr(Config, "DEFAULT_PEAK_SUN_HOURS", 5.0),
        "daily_units_per_kw_range": [
            getattr(Config, "DAILY_UNITS_PER_KW_LOW", 4.0),
            getattr(Config, "DAILY_UNITS_PER_KW_HIGH", 5.5),
        ],
        "dc_note": "System size is DC kWp. AC output depends on PR, irradiance and grid availability.",
        **financials,
    }
    if bill_sizing:
        result["bill_sizing"] = bill_sizing
    return result
