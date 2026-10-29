# Rooftop orientation and generation audit

## Data flow and conventions

1. `estimation_service` validates the drawn polygon and optional user azimuth.
   Geographic azimuth is clockwise from true north: N=0, E=90, S=180, W=270.
2. `resolve_orientation` interprets a numeric input as a user-selected array
   direction. Auto uses a perpendicular to the longest footprint edge, preferring
   south. This is a low-confidence planning heuristic, not roof-face detection.
   The latitude-based recommended tilt is not measured pitch or installed tilt.
3. Reconstruction describes a roof face by downslope azimuth and pitch from
   horizontal. Provider planes retain provider confidence. Prototype planes and
   authoritative footprint display fallbacks do not establish pitch or facets.
   Authoritative boundary confidence does not imply authoritative roof pitch.
4. The compass rotates the displayed panel array and sends the global azimuth to
   orientation preview/apply. It does not measure a roof plane or persist an
   independent mounting/rack tilt. Accepted layouts carry per-surface roof data;
   generation assumes flush mounting only where usable plane metadata exists.
5. Layout acceptance groups validated modules per surface and passes source,
   confidence and geometry kind into generation. Unknown/simulated planes retain
   unknown roof/panel tilt and use an identified global planning azimuth.
6. NASA monthly horizontal irradiation is converted using capacity, calendar days,
   existing PR and temperature treatment. Pitched planes use the existing azimuth
   approximation, not a physical tilted-plane model. Horizontal flush planes use
   no azimuth penalty. A factor of 1 here means no azimuth adjustment relative to
   horizontal input, not equality with an optimally tilted system.
7. PVGIS monthly modeled PV energy is normalized by capacity and used directly,
   without another PR, temperature or azimuth derate. Complete compatible provider
   annual flux is also used directly. Neither provides an independent orientation
   reference ratio, so the separate orientation factor remains unavailable.
8. The resulting monthly/annual energy feeds the existing separate Home/Business
   financial policies, lifecycle projections and reports. `generation_provenance`
   in estimate extras carries orientation evidence; accepted-surface details carry
   roof angles separately from assumed panel angles. Chart serialization exposes
   that provenance. HTML and compass preview no longer display missing factors as
   zero. PDF unavailable disclosures and suitability scoring remain unchanged.

## Corrections and compatibility

- Horizontal flush arrays no longer receive global east/west/north penalties.
- Simulated planes cannot supply measured pitch or face orientation to generation.
- Missing and non-finite angles retain explicit unknown states; non-finite global
  requests are rejected. Existing heuristic inputs remain planning estimates.
- Accepted-layout recalculation preserves auto versus user-selected provenance,
  rather than relabelling a saved automatic direction as user specified.
- A surface-orientation model version participates in the existing assumptions
  fingerprint. Old results become stale through existing serialization rules;
  stored estimates are not rewritten. Irradiance cache schema is unchanged.
- No new provider, dependency, schema or financial-policy change is required.

## Highest-impact remaining limitations

- A full pitch/azimuth model requires time-resolved solar position and irradiance
  components. The current monthly GHI profiles do not uniquely determine DNI/DHI
  or plane-of-array energy. PVLib is installed, but is only imported by the PVGIS
  fetch path; no physical POA transposition currently runs. Introducing temporal
  disaggregation or diffuse decomposition would require explicit new assumptions.
- PVGIS requests currently omit explicit panel tilt/azimuth. Its cached monthly
  output therefore does not demonstrate production for the selected roof planes.
  Applying a cosine factor to that energy would double-adjust an unspecified
  provider configuration. A future orientation-aware provider request must also
  key its cache by that configuration and retain the modeled plane metadata.
- Pitched panel azimuth from the visual compass can differ from accepted-face
  generation azimuth. Independent rack tilt/azimuth and flush mounting are not
  separate stored controls. Do not interpret the preview as installation geometry.
- Missing-provenance legacy plane metadata remains unverified. Newly accepted
  layouts carry explicit source/confidence/kind; old numeric fields are not
  retroactively certified. Footprint heuristics cannot resolve shed/gable faces.
- Qualitative shade/classification is not a quantitative shade-loss percentage.
  Provider annual-flux monthly distribution uses the location profile shape, not
  measured surface seasonality. Mixed-source factors remain unavailable when an
  independent aggregate reference is absent.
- Existing suitability assigns an unavailable orientation factor a score of zero;
  it is a rating convention, not measured zero production. Scoring was not changed.
- PR and ambient-temperature loss treatment, weather variability, albedo and
  hourly self-consumption remain separate accuracy work; formulas are unchanged.

## Focused verification

Run the surface-generation, solar-logic, report-chart-data, layout-acceptance and
calculation-track Python modules, plus `node tests/roof3d_geometry.test.js`.
No live provider calls are needed: fixtures mock weather/provider responses.
Browser and production-provider verification were not performed in this audit.
