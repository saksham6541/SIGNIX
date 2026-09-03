import re
from urllib.parse import parse_qs, unquote, urlparse

import requests

from app.config import Config
from app.models import UserLocation
from app.solar_logic import calculate_environmental_equivalents


def list_recent_locations(limit=10):
    return (
        UserLocation.query.order_by(UserLocation.created_at.desc()).limit(limit).all()
    )


def list_locations():
    return UserLocation.query.order_by(UserLocation.created_at.desc()).all()


def get_location(location_id):
    return UserLocation.query.get(location_id)


def get_report_context(location_id):
    location = get_location(location_id)
    if location is None:
        return None
    loc_dict = location.to_dict()
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
    return location, loc_dict


def reverse_address(latitude, longitude):
    try:
        response = requests.get(
            Config.NOMINATIM_REVERSE_URL,
            params={"lat": latitude, "lon": longitude, "format": "json", "zoom": 18},
            headers={"User-Agent": "RooftopSolarEstimator/1.1"},
            timeout=max(Config.REQUEST_TIMEOUT, 8),
        )
        response.raise_for_status()
        return (response.json() or {}).get("display_name")
    except Exception:
        return None


def geocode(query):
    results = []
    source = "none"
    headers = {
        "User-Agent": "RooftopSolarEstimator/1.1 (educational prototype; contact: local)",
        "Accept-Language": "en-IN,en,hi",
    }

    try:
        response = requests.get(
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
        response.raise_for_status()
        for row in response.json():
            display = row.get("display_name") or query
            address = row.get("address") or {}
            parts = [
                address.get("building")
                or address.get("amenity")
                or address.get("office")
                or address.get("shop")
                or address.get("tourism")
                or address.get("leisure"),
                address.get("road")
                or address.get("neighbourhood")
                or address.get("suburb"),
                address.get("city")
                or address.get("town")
                or address.get("village")
                or address.get("county"),
                address.get("state"),
            ]
            short = ", ".join(part for part in parts if part)
            results.append(
                {
                    "display_name": display,
                    "label": short or display,
                    "lat": float(row["lat"]),
                    "lon": float(row["lon"]),
                    "type": row.get("type") or row.get("class") or "",
                    "importance": row.get("importance"),
                }
            )
        if results:
            source = "nominatim"
    except Exception:
        pass

    if len(results) < 3:
        try:
            response = requests.get(
                "https://photon.komoot.io/api/",
                params={
                    "q": query,
                    "limit": 10,
                    "lang": "en",
                    "lat": 22.0,
                    "lon": 78.0,
                },
                headers=headers,
                timeout=max(Config.REQUEST_TIMEOUT, 10),
            )
            response.raise_for_status()
            features = (response.json() or {}).get("features") or []
            seen = {(round(row["lat"], 5), round(row["lon"], 5)) for row in results}
            for feature in features:
                properties = feature.get("properties") or {}
                geometry = feature.get("geometry") or {}
                coordinates = geometry.get("coordinates") or [None, None]
                longitude, latitude = coordinates[0], coordinates[1]
                if latitude is None or longitude is None:
                    continue
                country = (
                    properties.get("country") or properties.get("countrycode") or ""
                ).lower()
                if (
                    country
                    and country not in ("india", "in", "भारत")
                    and len(results) >= 5
                ):
                    continue
                key = (round(float(latitude), 5), round(float(longitude), 5))
                if key in seen:
                    continue
                seen.add(key)
                name_parts = [
                    properties.get("name"),
                    properties.get("street"),
                    properties.get("locality")
                    or properties.get("district")
                    or properties.get("city"),
                    properties.get("state"),
                    properties.get("country"),
                ]
                display = ", ".join(part for part in name_parts if part) or query
                results.append(
                    {
                        "display_name": display,
                        "label": display,
                        "lat": float(latitude),
                        "lon": float(longitude),
                        "type": properties.get("osm_value")
                        or properties.get("type")
                        or "",
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
        return {
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
    return {"results": results[:12], "source": source}


def parse_maps_url(raw):
    if not raw:
        return {"error": "Missing url parameter"}, 400

    bare = re.match(r"^\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*$", raw)
    if bare:
        latitude, longitude = float(bare.group(1)), float(bare.group(2))
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            address = reverse_address(latitude, longitude)
            return {
                "lat": latitude,
                "lon": longitude,
                "address": address,
                "source": "coordinates",
            }, 200
        return {"error": "Coordinates out of range"}, 400

    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    final_url = raw
    try:
        if re.search(r"(maps\.app\.goo\.gl|goo\.gl/maps|g\.page)/", raw, re.I):
            response = requests.get(
                raw,
                headers={"User-Agent": "RooftopSolarEstimator/1.1"},
                timeout=max(Config.REQUEST_TIMEOUT, 10),
                allow_redirects=True,
            )
            final_url = str(response.url or raw)
    except Exception:
        final_url = raw

    latitude = longitude = None
    place_hint = None
    match = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", final_url)
    if match:
        latitude, longitude = float(match.group(1)), float(match.group(2))
    if latitude is None:
        all_pins = re.findall(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", final_url)
        if all_pins:
            latitude, longitude = float(all_pins[-1][0]), float(all_pins[-1][1])
    if latitude is None:
        match = re.search(
            r"@(-?\d+\.?\d*),\s*(-?\d+\.?\d*)(?:,\d+\.?\d*[a-z]?)?",
            final_url,
            re.I,
        )
        if match:
            latitude, longitude = float(match.group(1)), float(match.group(2))
    if latitude is None:
        try:
            parsed = urlparse(final_url)
            query_params = parse_qs(parsed.query)
            for key in ("q", "query", "ll", "center"):
                values = query_params.get(key) or []
                if not values:
                    continue
                value = unquote(values[0])
                coordinate_match = re.search(
                    r"(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)", value
                )
                if coordinate_match:
                    latitude = float(coordinate_match.group(1))
                    longitude = float(coordinate_match.group(2))
                    text = value[: coordinate_match.start()].strip(" ,+")
                    if text and not re.match(r"^-?\d", text):
                        place_hint = text
                    break
                if (
                    key in ("q", "query")
                    and value
                    and not re.search(r"-?\d+\.\d+", value)
                ):
                    place_hint = value.replace("+", " ")
        except Exception:
            pass
    if place_hint is None:
        match = re.search(r"/maps/place/([^/@]+)", final_url)
        if match:
            place_hint = unquote(match.group(1).replace("+", " ")).strip()
    if latitude is None:
        match = re.search(r"/dir/[^/]*/(-?\d+\.?\d*),\s*(-?\d+\.?\d*)", final_url)
        if match:
            latitude, longitude = float(match.group(1)), float(match.group(2))

    if latitude is None or longitude is None:
        if place_hint:
            try:
                response = requests.get(
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
                response.raise_for_status()
                rows = response.json() or []
                if rows:
                    latitude = float(rows[0]["lat"])
                    longitude = float(rows[0]["lon"])
                    return {
                        "lat": latitude,
                        "lon": longitude,
                        "address": rows[0].get("display_name") or place_hint,
                        "source": "maps_url_geocoded",
                        "resolved_url": final_url,
                    }, 200
            except Exception:
                pass
        return {
            "error": (
                "Could not read coordinates from this link. "
                "Open the place in Google Maps, copy the link from the browser "
                "address bar (or Share → Copy link), and try again."
            )
        }, 400

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return {"error": "Coordinates out of range"}, 400
    address = place_hint or reverse_address(latitude, longitude)
    if place_hint and len(place_hint) < 12:
        full_address = reverse_address(latitude, longitude)
        if full_address:
            address = full_address
    return {
        "lat": latitude,
        "lon": longitude,
        "address": address or f"{latitude:.6f}, {longitude:.6f}",
        "source": "maps_url",
        "resolved_url": final_url,
    }, 200
