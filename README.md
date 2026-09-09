# SIGNIX

**Track · Save · Impact**

A rooftop solar potential and subsidy estimator for Indian homes. Draw your rooftop on a map, get a data-backed solar generation estimate, PM Surya Ghar subsidy calculation, and a downloadable PDF report — all scoped to your own account.

🔗 **Live app:** [https://signix.onrender.com](https://signix.onrender.com)
*(Free-tier hosting — the app sleeps after 15 minutes of inactivity, so the first request after a while may take 30–60 seconds to wake up.)*

![Python](https://img.shields.io/badge/python-3.11-blue)
![Flask](https://img.shields.io/badge/flask-web%20framework-black)
![License](https://img.shields.io/badge/license-MIT-green)

---

## What it does

SIGNIX estimates how much solar potential a rooftop has and what it would cost/save, using real location data rather than flat assumptions:

1. Search for an address or paste a Google Maps link
2. Draw your rooftop boundary on a satellite map, and mark non-usable areas (water tanks, staircases, AC units)
3. Get an estimate: system size, annual generation, monthly savings, payback period, and PM Surya Ghar subsidy — calculated from real irradiance data for that exact latitude/longitude, not a generic national average
4. Save estimates to your account, compare them side by side, and download a full PDF report

---

## Features

- **Location-aware solar estimation** — pulls real irradiance data from NASA POWER and PVGIS for the exact coordinates drawn, with a location-sensitive offline fallback if both external APIs are unavailable (clearly flagged in the results, not silently substituted)
- **Interactive rooftop drawing** — Leaflet-based polygon drawing for roof boundary and obstructions, with live area/capacity calculation
- **PM Surya Ghar subsidy calculation** — indicative subsidy based on system size and property type
- **Financial modeling** — system cost, net investment, monthly/annual savings, payback period, 25-year cashflow, LCOE
- **PDF report generation** — a downloadable, detailed report per saved estimate (WeasyPrint, with a fallback renderer)
- **User accounts** — signup/login/logout, with every saved location and estimate scoped privately to your account
- **Dashboard** — quick overview of your recent estimates
- **Compare** — put 2–3 of your saved locations side by side across system size, generation, savings, payback, and cost
- **Savings** — aggregated view of total potential savings and environmental impact (CO₂ reduction, equivalent trees planted, fuel saved) across all your saved estimates
- **Profile** — manage your display name, email, and password
- **Settings** — save default tariff rate, state, and property type so new estimates start pre-filled
- **Irradiance caching** — repeated lookups for the same location are cached, cutting estimate time from several seconds to well under 50ms on a cache hit

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Flask (blueprints + service layer) |
| Database | PostgreSQL (production) / SQLite (local dev) via SQLAlchemy |
| Auth | Flask-Login, Flask-WTF (CSRF), Werkzeug password hashing |
| Solar calculations | pvlib-style modeling, NASA POWER & PVGIS APIs |
| Caching | diskcache (irradiance lookups) |
| PDF generation | WeasyPrint (primary), ReportLab (fallback) |
| Frontend | Jinja2 templates, vanilla JS, Leaflet.js (maps), Chart.js (charts) |
| Testing | pytest |
| Containerization | Docker, docker-compose |
| Deployment | Render (web service + PostgreSQL) |

---

## Screenshots

*(Add screenshots to a `docs/` folder and reference them here, e.g.)*

```markdown
![Dashboard](docs/screenshot-dashboard.png)
![Rooftop drawing](docs/screenshot-estimate.png)
![Results panel](docs/screenshot-results.png)
```

---

## Running locally

### Option 1: Docker (recommended, matches production)

```bash
git clone https://github.com/saksham6541/SIGNIX.git
cd SIGNIX
cp .env.example .env   # then set a real SECRET_KEY
docker compose up --build
```
Visit `http://localhost:8000`. This runs against local SQLite by default.

To test against PostgreSQL locally (matching the production setup):
```bash
docker compose --profile postgres up --build web-postgres postgres
```

### Option 2: Local Python environment

```bash
git clone https://github.com/saksham6541/SIGNIX.git
cd SIGNIX
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
python run.py
```
Visit `http://localhost:5000`.

### Running tests

```bash
pytest
```

---

## Project structure

```
app/
├── __init__.py
├── config.py
├── database.py            # schema init + safe startup migrations
├── models.py               # User, UserLocation, TariffTable, SubsidyScheme
├── solar_logic.py          # geometry, irradiance, financials, orchestration
├── report_generator.py     # PDF generation (WeasyPrint + fallback)
├── auth.py                 # signup, login, logout
├── forms.py                # WTForms definitions
├── services/
│   ├── estimation_service.py
│   ├── location_service.py
│   └── cache.py             # diskcache-backed irradiance caching
├── pages.py                 # dashboard, profile, compare, savings, settings
├── estimate.py               # /api/estimate
├── locations.py               # location CRUD, geocoding
├── static/
│   ├── css/style.css
│   └── js/ (main.js, map.js)
└── templates/
tests/
├── test_solar_logic.py
├── test_routes.py
├── test_auth.py
├── test_compare.py
├── test_savings.py
└── conftest.py
```

---

## Roadmap

This project is being built incrementally rather than all at once. Completed so far:

- ✅ Core estimation engine with real irradiance data and location-sensitive fallback
- ✅ Test suite covering calculations, routes, auth, and cross-user data isolation
- ✅ Services-layer refactor (routes split into blueprints, business logic extracted)
- ✅ Irradiance caching
- ✅ Frontend polish (inline results panel, no full-page reload on estimate)
- ✅ Full authentication with per-user data scoping
- ✅ Compare, Savings, Profile, and Settings pages
- ✅ Dockerized, deployed on Render with PostgreSQL

Possible future directions: PDF comparison exports, richer savings tracking against real utility bills, notification preferences.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Author

**Saksham Kaushik**
[github.com/saksham6541/SIGNIX](https://github.com/saksham6541/SIGNIX)

---

*Indicative estimates only. Verify DISCOM / MNRE rules before procurement.*