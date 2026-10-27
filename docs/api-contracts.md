# API Contracts

This is a maintainer-oriented summary of routes registered by `create_app()`. Source code and tests remain authoritative. All responses are JSON unless noted.

## Authentication and CSRF

- Public: location discovery endpoints, `/`, `/login`, `/signup`, and language selection.
- Authenticated: saved locations, estimates, reports, rooftop data, assistant, profile, and settings.
- All unsafe methods (`POST`, `PATCH`, and form mutations) require a valid Flask-WTF CSRF token.
- Unauthenticated API requests return `401` JSON with `error` and `login_url`.
- Ownership failures generally return `404` to avoid disclosing another user's records.

Browser callers should send same-origin credentials, `Accept: application/json`, `X-Requested-With: XMLHttpRequest`, and `X-CSRFToken` for unsafe methods.

## Location discovery

| Method | Route | Auth | Contract |
|---|---|---:|---|
| GET | `/api/geocode?q=...` | No | Search candidates from configured geocoder; missing query is `400`. |
| GET | `/api/reverse-geocode?lat=...&lon=...` | No | Address/location fields; invalid or missing coordinates are `400`. |
| GET | `/api/parse-maps-url?url=...` | No | Parses supported Google Maps URLs without treating provider failure as a successful location. |
| GET | `/api/location-context?...` | Yes | Context for latitude/longitude plus optional state, city, address, pincode and system inputs. |
| GET | `/api/tariff?state=...` | Yes | Stored state tariff/default net-metering context or configured defaults. |

## Estimate creation

`POST /api/estimate` requires authentication and CSRF.

Core request:

```json
{
  "address": "Property label",
  "latitude": 28.6139,
  "longitude": 77.2090,
  "polygon": [[28.6139, 77.2090], [28.6139, 77.2092], [28.6141, 77.2092]],
  "obstructions": [],
  "tariff_per_kwh": 8.0,
  "orientation": "auto",
  "monthly_bill": 3000,
  "property_type": "residential",
  "needs_backup": false
}
```

`polygon` and `obstructions` use Leaflet `[latitude, longitude]` order at this API boundary. The backend validates finite coordinates, non-degenerate rings, containment, and non-overlapping obstructions before persisting valid GeoJSON.

Optional supported fields include `battery_kwh`, `user_priority`, `inverter_preference`, `sizing_mode`, `budget_inr`, `critical_load_kw`, `backup_hours`, `load_profile`, `net_metering_mode`, `feed_in_tariff`, and `state`.

Success includes the saved `location_id`, geometry/capacity, annual and monthly generation, costs, savings, assumptions, financing/report datasets, source confidence, and `report_url`. Treat response fields as server-derived; clients must not recalculate authoritative totals.

Invalid input returns `400`. Calculation or serialization failure returns `500`. An outdated local schema returns a `500` response that identifies the initialization problem without exposing credentials.

## Saved estimates and scenarios

| Method | Route | Contract |
|---|---|---|
| GET | `/api/locations` | Current user's saved locations. |
| GET | `/api/locations/<location_id>` | One owned saved estimate. |
| POST | `/api/locations/<location_id>/scenarios` | Clone an owned estimate with one supported scenario change; returns the new ID and report URL. |
| POST | `/api/estimate/<location_id>/orientation-preview` | Non-persistent recalculation for `{ "orientation": 0..360 }`. |
| POST | `/api/estimate/<location_id>/orientation` | Persist orientation and refreshed calculation outputs. |
| POST | `/api/estimate/<location_id>/financing-preview` | Non-persistent financing comparison for validated down payment, rate, and tenure inputs. |

Orientation preview must not mutate the saved record. Applying orientation uses the same estimation pipeline and transaction handling as other saved calculations.

## Rooftop design revisions

| Method | Route | Contract |
|---|---|---|
| GET | `/api/locations/<location_id>/rooftop-design` | Current owned design revision. |
| POST | `/api/locations/<location_id>/rooftop-design` | Create a revision from validated authoritative/manual or accepted fields. |
| POST | `/api/locations/<location_id>/rooftop-design/ingest` | Explicitly ingest selected `google_solar` and/or `gemini` providers. |
| PATCH | `/api/locations/<location_id>/rooftop-design/<design_id>/sources/<source_id>` | Review an advisory source; accepted values produce a new design revision. |

The user boundary is authoritative. Provider and AI data remain advisory until an explicit accepted review action. Geometry-affecting changes invalidate derived layout, shade, and surface-generation state.

## Roof model and layers

| Method | Route | Contract |
|---|---|---|
| GET | `/api/roof-model/<location_id>` | Reconstruction contract. Query `high_fidelity=0` disables high-fidelity ingestion; `refresh_provider=1` explicitly refreshes configured provider data. |
| GET | `/api/roof-model/<location_id>/layer/<layer>` | Protected proxy for supported provider layers. |

The model includes availability/status, design revision, geometry key, authoritative boundary, normalized surfaces, structures, constraints, shade/classification data, provider provenance, and diagnostics. A missing usable model may return `422`; provider transport failures may return `502`; unconfigured protected provider layers return `503`.

## Layout acceptance

`POST /api/locations/<location_id>/rooftop-layout/accept` requires:

```json
{
  "design_revision_id": 12,
  "design_revision_number": 4,
  "geometry_key": "reconstruction-fingerprint",
  "layout_mode": "planning_preview",
  "panels": [
    {
      "surface_id": "surface-1",
      "corners_local_xz_meters": [[0, 0], [1.134, 0], [1.134, 1.762], [0, 1.762]]
    }
  ]
}
```

`layout_mode` is `planning_preview` or `conservative`. The server rejects client-supplied panel count, capacity, generation, annual generation, or financial values. It verifies revision/geometry compatibility, exact configured module dimensions, surface and roof containment, setbacks, obstructions, classification policy, collisions, and finite coordinates.

Success is `201`, or `200` for an idempotent retry under the same current calculation assumptions. It returns `accepted_layout`, refreshed `estimate`, and `calculation`. Expected error codes include `client_values_not_allowed`, `stale_design_revision`, `incompatible_geometry_key`, `classification_unavailable`, `panel_geometry_invalid`, `panel_classification_invalid`, `panel_overlap`, and `recalculation_failed`.

## AI and assistant

| Method | Route | Contract |
|---|---|---|
| POST | `/api/roof-ai/imagery` | Satellite crop for a valid GeoJSON-order polygon. |
| POST | `/api/roof-ai/analyze` | Advisory Gemini analysis for a valid polygon; unavailable configuration is explicit. |
| POST | `/api/assistant` | Authenticated non-empty `{ "message": "..." }` up to 2,000 characters; database-backed daily user limit. Provider selection is server-side. Successful responses include the provider name and whether the configured OpenAI fallback was used. Returns stable error codes for missing/invalid provider configuration, provider quota, timeout, connection, malformed response, and empty response failures. |

Do not present AI output as measured roof evidence or silently copy it into authoritative financial/solar inputs.

## HTML and document routes

Public pages are `/`, `/plan/home`, `/plan/business`, `/login`, and `/signup`. The shared rooftop survey is authenticated at `/survey/home` or `/survey/business`; any other planning-track segment returns `404`. The route fixes the home-versus-business context before the existing map and estimate workflow loads. Authenticated pages also include `/dashboard`, `/reports`, `/saved-locations`, `/compare`, `/savings`, `/profile`, `/settings`, and `/report/<location_id>`. PDF and installer exports are `/report/<location_id>/pdf` and `/report/<location_id>/installer-package`.
