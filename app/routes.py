# filename: app/routes.py
from flask import Blueprint, render_template, request, jsonify, send_file, abort
import requests

from app.config import Config
from app.models import db, UserLocation, TariffTable, SubsidyScheme
from app.solar_logic import run_full_estimation, calculate_environmental_equivalents
from app.report_generator import generate_pdf_report

bp = Blueprint("routes", __name__)


# ---------------------------------------------------------------- Pages ----


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/dashboard")
def dashboard():
    recent = UserLocation.query.order_by(UserLocation.created_at.desc()).limit(10).all()
    return render_template("dashboard.html", locations=recent)


@bp.route("/report/<int:location_id>")
def report(location_id):
    location = UserLocation.query.get_or_404(location_id)
    loc_dict = location.to_dict()
    # Prefer stored extras / computed values; fall back if older rows
    extras = loc_dict.get("extras") or {}
    loc_dict["environmental_equivalents"] = extras.get(
        "environmental_equivalents"
    ) or calculate_environmental_equivalents(loc_dict.get("co2_reduction_tons") or 0)
    if not loc_dict.get("payback_years") and loc_dict.get("monthly_savings"):
        loc_dict["payback_years"] = round(
            loc_dict["net_investment"] / (loc_dict["monthly_savings"] * 12), 1
        )
    loc_dict["cashflow_25yr"] = extras.get("cashflow_25yr")
    for key in (
        "inverter",
        "inverter_type",
        "bill_sizing",
        "roof_capacity_kw",
        "sizing_method",
        "rule_of_thumb_monthly_units",
        "rule_of_thumb_annual_units",
        "property_type",
        "needs_backup",
        "planning_notes",
        "system_cost_low",
        "system_cost_high",
        "annual_om",
        "performance_ratio",
        "peak_sun_hours_assumed",
        "daily_units_per_kw_range",
        "dc_note",
        "cost_per_kw",
    ):
        if key in extras and extras[key] is not None:
            loc_dict[key] = extras[key]
    return render_template("report.html", loc=loc_dict)


# ------------------------------------------------------------- REST API ----


@bp.route("/api/geocode")
def api_geocode():
    """Geocode a free-text address.

    Tries Nominatim (OpenStreetMap) first with parameters tuned for
    niche Indian places (colleges, societies, buildings), then falls
    back to Photon (Komoot) which often surfaces smaller OSM POIs.
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400

    results = []
    source = "none"
    headers = {
        "User-Agent": "RooftopSolarEstimator/1.1 (educational prototype; contact: local)",
        "Accept-Language": "en-IN,en,hi",
    }

    # --- Primary: Nominatim (detailed, India-biased) ---
    try:
        resp = requests.get(
            Config.NOMINATIM_URL,
            params={
                "q": query,
                "format": "json",
                "limit": 12,
                "countrycodes": "in",
                "addressdetails": 1,
                "extratags": 1,
                "namedetails": 1,
                "dedupe": 1,
            },
            headers=headers,
            timeout=max(Config.REQUEST_TIMEOUT, 10),
        )
        resp.raise_for_status()
        for r in resp.json():
            display = r.get("display_name") or query
            # Prefer a tighter label when address parts exist
            addr = r.get("address") or {}
            parts = [
                addr.get("building")
                or addr.get("amenity")
                or addr.get("office")
                or addr.get("shop")
                or addr.get("tourism")
                or addr.get("leisure"),
                addr.get("road") or addr.get("neighbourhood") or addr.get("suburb"),
                addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("county"),
                addr.get("state"),
            ]
            short = ", ".join(p for p in parts if p)
            results.append(
                {
                    "display_name": display,
                    "label": short or display,
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "type": r.get("type") or r.get("class") or "",
                    "importance": r.get("importance"),
                }
            )
        if results:
            source = "nominatim"
    except Exception:
        pass

    # --- Secondary: Photon (good for niche / incomplete OSM names) ---
    if len(results) < 3:
        try:
            resp = requests.get(
                "https://photon.komoot.io/api/",
                params={
                    "q": query,
                    "limit": 10,
                    "lang": "en",
                    "lat": 22.0,  # bias toward India
                    "lon": 78.0,
                },
                headers=headers,
                timeout=max(Config.REQUEST_TIMEOUT, 10),
            )
            resp.raise_for_status()
            features = (resp.json() or {}).get("features") or []
            seen = {(round(r["lat"], 5), round(r["lon"], 5)) for r in results}
            for f in features:
                props = f.get("properties") or {}
                geom = f.get("geometry") or {}
                coords = geom.get("coordinates") or [None, None]
                lon, lat = coords[0], coords[1]
                if lat is None or lon is None:
                    continue
                # Prefer India / nearby when country is known
                country = (
                    props.get("country") or props.get("countrycode") or ""
                ).lower()
                if country and country not in ("india", "in", "भारत"):
                    # still allow if query is clearly local and we have few hits
                    if len(results) >= 5:
                        continue
                key = (round(float(lat), 5), round(float(lon), 5))
                if key in seen:
                    continue
                seen.add(key)
                name_parts = [
                    props.get("name"),
                    props.get("street"),
                    props.get("locality") or props.get("district") or props.get("city"),
                    props.get("state"),
                    props.get("country"),
                ]
                display = ", ".join(p for p in name_parts if p) or query
                results.append(
                    {
                        "display_name": display,
                        "label": display,
                        "lat": float(lat),
                        "lon": float(lon),
                        "type": props.get("osm_value") or props.get("type") or "",
                        "importance": None,
                    }
                )
            if results and source == "none":
                source = "photon"
            elif results:
                source = f"{source}+photon"
        except Exception:
            pass

    if not results:
        # Last-resort mock so the UI never goes empty during demos / offline
        return jsonify(
            {
                "results": [
                    {
                        "display_name": f"{query} (geocoding unavailable — try a fuller address)",
                        "label": query,
                        "lat": 28.6139,
                        "lon": 77.2090,
                        "type": "mock",
                    }
                ],
                "source": "mock_fallback",
            }
        )

    return jsonify({"results": results[:12], "source": source})


@bp.route("/api/reverse-geocode")
def api_reverse_geocode():
    """Reverse-geocode a lat/lon (used for 'use current location')."""
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    if lat is None or lon is None:
        return jsonify({"error": "Missing lat/lon"}), 400

    headers = {
        "User-Agent": "RooftopSolarEstimator/1.1 (educational prototype; contact: local)",
        "Accept-Language": "en-IN,en,hi",
    }
    try:
        resp = requests.get(
            Config.NOMINATIM_REVERSE_URL,
            params={
                "lat": lat,
                "lon": lon,
                "format": "json",
                "addressdetails": 1,
                "zoom": 18,  # building-level when available
            },
            headers=headers,
            timeout=max(Config.REQUEST_TIMEOUT, 10),
        )
        resp.raise_for_status()
        data = resp.json()
        return jsonify(
            {
                "address": data.get("display_name", "Unknown location"),
                "source": "nominatim",
                "lat": lat,
                "lon": lon,
            }
        )
    except Exception:
        return jsonify(
            {
                "address": "Current location (address lookup unavailable)",
                "source": "mock_fallback",
                "lat": lat,
                "lon": lon,
            }
        )


@bp.route("/api/parse-maps-url")
def api_parse_maps_url():
    """Extract lat/lng (and optional place name) from a Google Maps URL.

    Supports common formats:
      • https://www.google.com/maps/@lat,lng,zoom
      • https://www.google.com/maps/place/.../@lat,lng,zoom
      • https://www.google.com/maps?q=lat,lng
      • https://maps.google.com/?q=lat,lng
      • https://maps.app.goo.gl/...  (short links — followed once)
      • https://goo.gl/maps/...
      • query=lat,lng  /  !3dLAT!4dLNG  place data
    """
    import re
    from urllib.parse import urlparse, parse_qs, unquote

    raw = (request.args.get("url") or "").strip()
    if not raw:
        return jsonify({"error": "Missing url parameter"}), 400

    # Allow bare coordinates pasted by accident
    bare = re.match(r"^\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*$", raw)
    if bare:
        lat, lon = float(bare.group(1)), float(bare.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            address = _reverse_address(lat, lon)
            return jsonify(
                {"lat": lat, "lon": lon, "address": address, "source": "coordinates"}
            )
        return jsonify({"error": "Coordinates out of range"}), 400

    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw

    final_url = raw
    # Expand short Google links (one hop)
    try:
        if re.search(r"(maps\.app\.goo\.gl|goo\.gl/maps|g\.page)/", raw, re.I):
            resp = requests.get(
                raw,
                headers={"User-Agent": "RooftopSolarEstimator/1.1"},
                timeout=max(Config.REQUEST_TIMEOUT, 10),
                allow_redirects=True,
            )
            final_url = str(resp.url or raw)
    except Exception:
        final_url = raw

    lat = lon = None
    place_hint = None

    # Prefer the place MARKER coordinates (!3dLAT!4dLNG) over the camera
    # center (@lat,lng). Google Maps share links often center the viewport
    # slightly away from the actual pin; !3d/!4d is the pin itself.
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", final_url)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))

    # data=!4m...!8m2!3dLAT!4dLNG sometimes appears more than once — take last
    if lat is None:
        all_pins = re.findall(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", final_url)
        if all_pins:
            lat, lon = float(all_pins[-1][0]), float(all_pins[-1][1])

    # @lat,lng,zoom — camera / viewport center (fallback)
    if lat is None:
        m = re.search(
            r"@(-?\d+\.?\d*),\s*(-?\d+\.?\d*)(?:,\d+\.?\d*[a-z]?)?", final_url, re.I
        )
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))

    # query params: q= / query= / ll=
    if lat is None:
        try:
            parsed = urlparse(final_url)
            qs = parse_qs(parsed.query)
            for key in ("q", "query", "ll", "center"):
                vals = qs.get(key) or []
                if not vals:
                    continue
                val = unquote(vals[0])
                cm = re.search(r"(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)", val)
                if cm:
                    lat, lon = float(cm.group(1)), float(cm.group(2))
                    # leftover text before coords can be a place name
                    text = val[: cm.start()].strip(" ,+")
                    if text and not re.match(r"^-?\d", text):
                        place_hint = text
                    break
                # q=Place+Name without coords — geocode later
                if key in ("q", "query") and val and not re.search(r"-?\d+\.\d+", val):
                    place_hint = val.replace("+", " ")
        except Exception:
            pass

    # /maps/place/Place+Name/
    if place_hint is None:
        m = re.search(r"/maps/place/([^/@]+)", final_url)
        if m:
            place_hint = unquote(m.group(1).replace("+", " ")).strip()

    # /dir/... destinations sometimes
    if lat is None:
        m = re.search(r"/dir/[^/]*/(-?\d+\.?\d*),\s*(-?\d+\.?\d*)", final_url)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))

    if lat is None or lon is None:
        # Fall back: if we only have a place name, geocode it
        if place_hint:
            try:
                resp = requests.get(
                    Config.NOMINATIM_URL,
                    params={
                        "q": place_hint,
                        "format": "json",
                        "limit": 1,
                        "countrycodes": "in",
                    },
                    headers={"User-Agent": "RooftopSolarEstimator/1.1"},
                    timeout=max(Config.REQUEST_TIMEOUT, 10),
                )
                resp.raise_for_status()
                rows = resp.json() or []
                if rows:
                    lat = float(rows[0]["lat"])
                    lon = float(rows[0]["lon"])
                    address = rows[0].get("display_name") or place_hint
                    return jsonify(
                        {
                            "lat": lat,
                            "lon": lon,
                            "address": address,
                            "source": "maps_url_geocoded",
                            "resolved_url": final_url,
                        }
                    )
            except Exception:
                pass
        return (
            jsonify(
                {
                    "error": (
                        "Could not read coordinates from this link. "
                        "Open the place in Google Maps, copy the link from the browser "
                        "address bar (or Share → Copy link), and try again."
                    )
                }
            ),
            400,
        )

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify({"error": "Coordinates out of range"}), 400

    address = place_hint or _reverse_address(lat, lon)
    # Prefer reverse-geocoded full address when we only had a short hint
    if place_hint and len(place_hint) < 12:
        full = _reverse_address(lat, lon)
        if full:
            address = full

    return jsonify(
        {
            "lat": lat,
            "lon": lon,
            "address": address or f"{lat:.6f}, {lon:.6f}",
            "source": "maps_url",
            "resolved_url": final_url,
        }
    )


def _reverse_address(lat, lon):
    """Best-effort reverse geocode for Maps URL / coordinate paste."""
    try:
        resp = requests.get(
            Config.NOMINATIM_REVERSE_URL,
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 18},
            headers={"User-Agent": "RooftopSolarEstimator/1.1"},
            timeout=max(Config.REQUEST_TIMEOUT, 8),
        )
        resp.raise_for_status()
        return (resp.json() or {}).get("display_name")
    except Exception:
        return None


@bp.route("/api/estimate", methods=["POST"])
def api_estimate():
    """
    Main estimation endpoint. Expects JSON:
    {
      "address": str,
      "latitude": float,
      "longitude": float,
      "polygon": [[lat, lng], [lat, lng], ...],
      "obstructions": optional list of polygons,
      "orientation": "auto" | degrees (0=N … 180=S),
      "battery_kwh": float (0 = no battery),
      "state": str (optional, for tariff lookup)
    }
    Runs the full solar_logic pipeline, persists a UserLocation row, and
    returns the computed estimate.
    """
    import time

    route_start = time.perf_counter()
    payload = request.get_json(silent=True) or {}

    address = payload.get("address", "Unknown address")
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    polygon = payload.get("polygon", [])
    obstructions = payload.get("obstructions", [])
    state = payload.get("state")
    orientation = payload.get("orientation", "auto")
    battery_kwh = float(payload.get("battery_kwh") or 0)
    monthly_bill = payload.get("monthly_bill")
    if monthly_bill is not None and monthly_bill != "":
        try:
            monthly_bill = float(monthly_bill)
        except (TypeError, ValueError):
            monthly_bill = None
    else:
        monthly_bill = None
    property_type = payload.get("property_type") or "residential"
    needs_backup = bool(payload.get("needs_backup"))
    inverter_preference = payload.get("inverter_preference") or "auto"

    if latitude is None or longitude is None:
        return jsonify({"error": "latitude and longitude are required"}), 400
    if not polygon or len(polygon) < 3:
        return (
            jsonify({"error": "A rooftop polygon with at least 3 points is required"}),
            400,
        )

    tariff = _lookup_tariff(state)
    # Prefer explicit tariff from bill calculator when provided
    if payload.get("tariff_per_kwh"):
        try:
            tariff = float(payload["tariff_per_kwh"])
        except (TypeError, ValueError):
            pass

    try:
        result = run_full_estimation(
            latitude=latitude,
            longitude=longitude,
            coordinates=polygon,
            obstructions=obstructions,
            tariff_per_kwh=tariff,
            orientation=orientation,
            battery_kwh=battery_kwh,
            monthly_bill=monthly_bill,
            property_type=property_type,
            needs_backup=needs_backup,
            inverter_preference=inverter_preference,
        )
    except Exception as exc:
        return jsonify({"error": f"Estimation engine failed: {exc}"}), 500

    try:
        location = UserLocation(
            address=address,
            latitude=latitude,
            longitude=longitude,
            polygon_geojson={"type": "Polygon", "coordinates": [polygon]},
            obstructions_geojson=(
                {"type": "MultiPolygon", "coordinates": [[o] for o in obstructions]}
                if obstructions
                else None
            ),
            system_size=result["system_size"],
            annual_generation=result["annual_generation"],
            monthly_data=result["monthly_data"],
            roof_area_sqm=result["roof_area_sqm"],
            obstructed_area_sqm=result["obstructed_area_sqm"],
            usable_area_sqm=result["usable_area_sqm"],
            system_cost=result["system_cost"],
            subsidy_amount=result["subsidy_amount"],
            net_investment=result["net_investment"],
            monthly_savings=result["monthly_savings"],
            co2_reduction_tons=result["co2_reduction_tons"],
            irradiance_source=result["irradiance_source"],
            orientation_deg=result.get("orientation_deg"),
            orientation_label=result.get("orientation_label"),
            orientation_factor=result.get("orientation_factor"),
            recommended_tilt_deg=result.get("recommended_tilt_deg"),
            battery_kwh=result.get("battery_kwh", 0),
            battery_cost=result.get("battery_cost", 0),
            specific_yield=result.get("specific_yield"),
            capacity_factor=result.get("capacity_factor"),
            lcoe=result.get("lcoe"),
            lifetime_kwh=result.get("lifetime_kwh"),
            self_consumption_frac=result.get("self_consumption_frac"),
            payback_years=result.get("payback_years"),
            extras={
                "orientation_source": result.get("orientation_source"),
                "cashflow_25yr": result.get("cashflow_25yr"),
                "environmental_equivalents": result.get("environmental_equivalents"),
                "inverter": result.get("inverter"),
                "inverter_type": result.get("inverter_type"),
                "bill_sizing": result.get("bill_sizing"),
                "roof_capacity_kw": result.get("roof_capacity_kw"),
                "sizing_method": result.get("sizing_method"),
                "rule_of_thumb_monthly_units": result.get(
                    "rule_of_thumb_monthly_units"
                ),
                "rule_of_thumb_annual_units": result.get("rule_of_thumb_annual_units"),
                "property_type": result.get("property_type"),
                "needs_backup": result.get("needs_backup"),
                "planning_notes": result.get("planning_notes"),
                "system_cost_low": result.get("system_cost_low"),
                "system_cost_high": result.get("system_cost_high"),
                "annual_om": result.get("annual_om"),
                "performance_ratio": result.get("performance_ratio"),
                "peak_sun_hours_assumed": result.get("peak_sun_hours_assumed"),
                "daily_units_per_kw_range": result.get("daily_units_per_kw_range"),
                "dc_note": result.get("dc_note"),
                "cost_per_kw": result.get("cost_per_kw"),
            },
        )
        db_save_start = time.perf_counter()
        db.session.add(location)
        db.session.commit()
        db_save_elapsed = (time.perf_counter() - db_save_start) * 1000
        print(f"[TIMING] /api/estimate DB save elapsed: {db_save_elapsed:.2f} ms")
    except Exception as exc:
        db.session.rollback()
        # Common cause: old SQLite DB missing new columns. Auto-heal once.
        err_msg = str(exc)
        if "no such column" in err_msg.lower() or "has no column" in err_msg.lower():
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

    elapsed_ms = (time.perf_counter() - route_start) * 1000
    print(f"[TIMING] /api/estimate total elapsed: {elapsed_ms:.2f} ms")
    return jsonify(response)


@bp.route("/api/locations")
def api_locations():
    """List saved locations (used by dashboard's 'My Reports' / 'Saved Locations')."""
    locations = UserLocation.query.order_by(UserLocation.created_at.desc()).all()
    return jsonify([loc.to_dict() for loc in locations])


@bp.route("/api/locations/<int:location_id>")
def api_location_detail(location_id):
    location = UserLocation.query.get_or_404(location_id)
    return jsonify(location.to_dict())


@bp.route("/report/<int:location_id>/pdf")
def download_pdf(location_id):
    location = UserLocation.query.get_or_404(location_id)
    try:
        pdf_buffer = generate_pdf_report(location)
    except Exception as exc:
        # WeasyPrint may need cairo/pango; ReportLab is the pure-Python fallback.
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

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"solar_report_{location_id}.pdf",
        max_age=0,
    )


# ------------------------------------------------------------- helpers -----


def _lookup_tariff(state):
    if state:
        row = TariffTable.query.filter_by(state=state).first()
        if row:
            return row.rate_per_kwh
    return Config.DEFAULT_TARIFF
