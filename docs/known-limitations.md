# Known Limitations

These constraints are part of the current implementation and should remain visible in product copy and engineering decisions.

## Roof and geographic data

- The user-drawn polygon is a footprint constraint, not evidence of roof type, pitch, ridge, elevation, or structural condition.
- Prototype fixtures are simulated and default to flat geometry when reliable pitch/type data is absent.
- Provider coverage can be unavailable, malformed, stale, incomplete, or clipped by the authoritative boundary.
- Manual and accepted obstruction heights may be unknown; visual extrusion is not a measured height.
- Local projection is suitable for rooftop-scale geometry, not large regional polygons.

## Shade and panels

- Missing shade remains unknown. Planning preview may show physically feasible panels over unknown cells, but those panels are not shade verified.
- Conservative mode fails closed without compatible confirmed classifications.
- Qualitative shade classes are not converted into a numeric generation loss.
- Panel packing is a planning layout, not an electrical, structural, fire-code, wind-load, access-path, or installation design.

## Generation

- NASA POWER provides location-wide horizontal irradiance, not roof-plane irradiance.
- The NASA temperature model and azimuth adjustment are planning approximations.
- Pitch is preserved but is not used for a plane-of-array transposition model.
- PVGIS fallback uses provider-modeled monthly production and currently does not pass roof-specific angle/aspect; it is not per-surface roof evidence.
- Google annual specific yield can be used only with explicit annual units and complete panel coverage. Its weather, orientation, and shade effects cannot be isolated.
- Monthly distribution of an annual provider yield is estimated from the available location profile.
- Offline fallback is deterministic and location-sensitive but not provider observations.

## Financial and environmental results

- Tariffs, export rules, system costs, financing rates, load profiles, escalation, degradation, O&M, and subsidy eligibility are assumptions that require current DISCOM/vendor verification.
- Savings are not guaranteed and do not model every billing slab, demand charge, curtailment, outage, tax, maintenance event, or equipment replacement.
- The displayed annual O&M reserve is not deducted from the current payback cashflow.
- CO₂ impact uses a capacity-based planning factor, not a location- and time-specific marginal grid-emissions series.
- PM Surya Ghar output is indicative and does not represent MNRE/DISCOM approval.

## Providers and deployment

- Google Solar and Gemini require separately configured keys and may incur provider costs; the prototype does not require either.
- Geocoding, Leaflet tiles, satellite imagery, Google Fonts, Leaflet scripts, and Three.js depend on third-party availability and network policy.
- The assistant's daily message counter is database-backed. Gemini chat still requires `GEMINI_API_KEY`, while browser voice recognition and speech output depend on the browser, microphone permission, installed voices, and the browser vendor's recognition service.
- Diskcache is local to an application filesystem; multiple hosts do not share it.
- The repository has no committed CI workflow, Alembic migration history, JavaScript lockfile, or Render infrastructure manifest.

## Validation gaps

- Automated tests cover calculation, route, geometry, and selected JavaScript behavior but not complete browser visual regression, accessibility conformance, provider availability, Docker image build, PostgreSQL migration compatibility, or production load.
- Historical performance measurements are local observations and not current production benchmarks.
