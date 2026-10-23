# Frontend Guide

The frontend is server-rendered Jinja plus vanilla JavaScript. There is no Node package manifest, bundler, or separate frontend application.

## Ownership

Frontend developers normally work in:

- `app/templates/`: page structure and accessible server-rendered content.
- `app/static/css/style.css`: shared visual system, responsive rules, states, and print styles.
- `app/static/js/main.js`: estimate form, location discovery, contextual inputs, and submission.
- `app/static/js/map.js`: Leaflet map state, roof polygons, and obstructions.
- `app/static/js/roof3d.js`: Three.js reconstruction, panels, layers, compass, and layout acceptance.
- `app/static/js/roof-ai.js`: rooftop provider/AI workflow.
- `app/static/js/report-charts.js`: chart datasets and financing-preview interaction.
- `app/static/js/assistant.js` and `voice_mode.js`: assistant and voice controls.
- `app/static/js/vendor/chart.umd.min.js`: vendored dependency; do not hand-edit.

Backend-owned response construction and calculations should not be duplicated in JavaScript.

## Templates and assets

Flask discovers `app/templates/` and `app/static/` through `Flask(__name__)`. Templates load local assets with `url_for('static', filename='...')`. `base.html` owns common navigation, CSS, CSRF metadata, assistant controls, and shared scripts. `landing.html` is the public overview, `plan.html` renders the distinct home and business entry experiences, and `index.html` remains the single shared rooftop survey workspace. Public templates set `hide_sidebar`; authenticated application pages use the sidebar shell.

Leaflet and Leaflet Draw are loaded from unpkg only on `/survey/<planning_track>`. The public landing and planning-entry pages do not initialize a map. Three.js r128 and Chart.js are vendored locally for the report workspace, so the 3D viewer does not depend on a public CDN after the report HTML loads. The survey page still needs network access for its Leaflet assets even when backend providers are disabled.

## API calls

Use same-origin `fetch()` and include:

```javascript
const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
const response = await fetch('/api/example', {
  method: 'POST',
  credentials: 'same-origin',
  headers: {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    'X-CSRFToken': csrf,
    'X-Requested-With': 'XMLHttpRequest'
  },
  body: JSON.stringify(payload)
});
```

Recoverable errors should appear in nearby status elements with `role="status"` or `aria-live`, not blocking alerts. New async requests must guard against stale responses and cancel obsolete delayed map actions.

## Coordinate and state rules

- Convert Leaflet `[lat, lng]` to GeoJSON `[lng, lat]` only at explicit boundaries.
- Never mutate the authoritative roof polygon while changing visualization layers or panel-placement mode.
- Planning-preview panels over unknown shade must remain visually and textually unverified.
- Orientation preview is reversible and non-persistent; applying orientation is an explicit server mutation.
- Only successful layout acceptance may refresh saved panel capacity and financial results.

## UI changes

Preserve truthful loading, empty, unavailable, stale, and error states. Do not label simulated geometry as surveyed, unknown shade as usable, or provider failure as a successful fallback location. Follow `DESIGN.md` and `PRODUCT.md` for visual and product language.

If a frontend change modifies payload fields, response assumptions, coordinate order, surface identifiers, geometry keys, layout modes, or CSRF behavior, coordinate the change with the backend owner and update `docs/api-contracts.md` plus route tests.

## Validation

```powershell
Get-ChildItem app\static\js -Filter *.js | ForEach-Object { node --check $_.FullName }
node --test tests\roof3d_geometry.test.js
node --test tests\assistant_voice.test.js tests\voice_mode.test.js
python -m pytest tests/test_routes.py tests/test_report_chart_data.py -q
```

Manually verify affected keyboard focus, mobile breakpoints, request cancellation, map synchronization, truthful error states, and the browser console. Automated tests do not provide complete visual or accessibility coverage.
