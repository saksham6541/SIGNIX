# SIGNIX

SIGNIX is a Flask application for preliminary rooftop-solar planning in India. Users can explore residential or business planning entry points, draw a roof boundary and obstructions, review an indicative estimate, and inspect a saved report.

SIGNIX is a planning aid. Its estimates are not a site survey, engineering design, vendor quotation, guaranteed saving, or government approval.

## Contents

- [Project overview](#project-overview)
- [Key features](#key-features)
- [Demo and screenshots](#demo-and-screenshots)
- [Technology stack](#technology-stack)
- [Architecture and project structure](#architecture-and-project-structure)
- [Prerequisites](#prerequisites)
- [Local installation and setup](#local-installation-and-setup)
- [Environment variables and API configuration](#environment-variables-and-api-configuration)
- [Database setup](#database-setup)
- [Running the application](#running-the-application)
- [Testing and validation](#testing-and-validation)
- [CI workflow](#ci-workflow)
- [Estimation assumptions and limitations](#estimation-assumptions-and-limitations)
- [Security and contributions](#security-and-contributions)
- [Documentation](#documentation)
- [License](#license)

## Project overview

The application combines a server-rendered public landing page and planning-track pages with authenticated estimate creation, saved locations, and detailed reports. Its Python application factory is `create_app()` in `app/__init__.py`; `run.py` loads `.env`, exposes the Flask app as `run:app`, and initializes the local database and reference data when run directly.

The supported planning entry points are:

| Purpose | Route | Availability |
| --- | --- | --- |
| Public landing page | `/` | Public |
| Residential planning entry | `/plan/home` | Public |
| Business/commercial planning entry | `/plan/business` | Public |
| Rooftop survey | `/survey/home` or `/survey/business` | Login required |
| Saved estimate report | `/report/<location_id>` | Login required; owner only |
| Consultation information | `/consultation` | Public |

The business entry track exists, but it should not be interpreted as a production-grade commercial engineering, procurement, or EPC service. Industrial planning is marked as coming soon.

## Key features

- **Roof-first estimate workflow:** locate a property, draw a roof boundary, and add obstructions in the survey map. The submitted boundary is authoritative for planning calculations.
- **Rooftop designer:** inspect a Three.js model, roof classification areas, obstruction and setback handling, surface information, and proposed module placement. Planning-preview and conservative placement modes communicate different shade-validation requirements.
- **Explicit layout acceptance:** preview interactions do not update a saved estimate. A proposed layout must pass server-side checks and be explicitly accepted before accepted panel capacity and related estimate results are saved.
- **Estimate and energy report:** review indicative system sizing, annual and monthly energy forecasts, savings, payback, financial projections, and environmental estimates.
- **Report tabs:** System Sizing, Energy Forecasting, Financing Options, CAPEX vs DPA, Environmental, and Next Steps. The report includes charts, assumptions, and downloadable PDF output. The CAPEX/DPA comparison is an assumption-based model; the PPA comparison is deferred.
- **Financing exploration:** inspect indicative loan/EMI and cashflow outputs and, where a financing comparison is available, preview DPA assumptions without overwriting saved estimate inputs.
- **PM Surya Ghar guidance:** display scheme information and eligibility caveats. Government-set rules, application steps, approvals, and subsidy amounts must be checked with the relevant official sources and DISCOM.
- **Saved estimates and account pages:** account-scoped saved locations, scenarios, comparisons, settings, and English/Hindi localization.
- **Optional provider-backed data:** NASA POWER and PVGIS generation inputs, optional Google Solar rooftop data, and optional Gemini assistant/roof-analysis features. Availability depends on configuration, provider coverage, and network access.

## Demo and screenshots

The repository does not currently provide a maintained public demo URL or an application screenshot set. Image files under `app/static/images/` are UI assets, not a substitute for a live demo or verified product screenshots.

## Technology stack

- Python 3.11 in CI; the local development setup below uses Python 3.12.
- Flask, Jinja2, Flask-Login, Flask-WTF, Flask-Babel, Flask-SQLAlchemy, and SQLAlchemy.
- SQLite by default; PostgreSQL can be selected with `DATABASE_URL`.
- Vanilla JavaScript, Leaflet for the survey map, vendored Three.js r128 for the 3D viewer, and vendored Chart.js for report charts.
- WeasyPrint for PDF generation where its native libraries are available, with a ReportLab fallback.
- pytest and Node.js's built-in test runner.
- Docker and Docker Compose for containerized operation.

Python dependencies are declared in the root [`requirements.txt`](requirements.txt). There is no JavaScript package manifest, frontend bundler, or separate frontend application.

## Architecture and project structure

```text
app/
  __init__.py           Flask application factory and extension/blueprint setup
  auth.py               Authentication, profile, settings, and language routes
  pages.py              Landing, survey pages, dashboard, reports, and exports
  estimate.py           Estimate, orientation, financing, and context APIs
  locations.py          Geocoding, saved locations, and scenarios APIs
  rooftop_design.py     Design revisions and layout acceptance API
  roof_model.py         Reconstructed roof model and provider layers
  roof_ai.py            Optional rooftop imagery and analysis endpoints
  assistant.py          Optional assistant endpoint
  models.py             SQLAlchemy models
  database.py           Schema initialization and compatibility updates
  solar_logic.py        Sizing, energy, subsidy, and financial calculations
  services/             Estimation, locations, reports, rooftop, and caching
  templates/             Jinja pages and shared navigation
  static/css/            Shared and responsive styles
  static/js/             Survey, viewer, report, assistant, and chart behavior
tests/                   Python tests, Node tests, and calculation goldens
docs/                    Architecture, API, development, and limitation guides
scripts/                 Documentation validation and maintainer utilities
translations/            English and Hindi Babel catalogs
run.py                   Local Flask entry point and reference-data initialization
Dockerfile               Container image definition
docker-entrypoint.sh     Container database initialization before startup
docker-compose.yml       SQLite service and optional PostgreSQL profile
.github/workflows/ci.yml GitHub Actions validation workflow
```

The primary browser/API flow starts in the Jinja templates and static JavaScript, passes through Flask blueprints, and delegates business logic to `app/services/` and domain modules. Unsafe requests require CSRF protection; saved estimate data is scoped to the authenticated account.

Key page routes include `/`, `/plan/<planning_track>`, `/survey/<planning_track>`, `/dashboard`, `/properties`, `/compare`, `/savings`, and `/report/<location_id>`. Useful API route families include `/api/estimate`, `/api/locations`, `/api/roof-model`, `/api/locations/<location_id>/rooftop-design`, and `/api/locations/<location_id>/rooftop-layout/accept`. See [`docs/api-contracts.md`](docs/api-contracts.md) and the route blueprints for exact methods and response contracts.

## Prerequisites

- Python 3.12 for the local virtual environment documented here. CI currently tests Python 3.11.
- Node.js 24 for JavaScript syntax checks and Node regression tests.
- Git.
- Windows PowerShell for the commands below.
- Optional: Docker Desktop and Docker Compose.
- Optional provider credentials only for integrations that use them.

## Local installation and setup

From PowerShell, clone the repository and create the ignored local `.venv312` environment:

```powershell
git clone https://github.com/saksham6541/SIGNIX.git
Set-Location SIGNIX
if (-not (Test-Path .venv312)) { py -3.12 -m venv .venv312 }
.\.venv312\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

`.venv312` is a local development environment and is ignored by Git; it is not included in the repository. Edit the local `.env` only if you need to set a non-default secret or optional integration. Do not commit it.

If PowerShell blocks virtual-environment activation, use the Python executable directly instead:

```powershell
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
.\.venv312\Scripts\python.exe run.py
```

## Environment variables and API configuration

`run.py` loads `.env` through `python-dotenv`. The Flask configuration is defined in `app/config.py`.

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Flask session and security signing key. Set a private, unique value outside tests and local-only development. |
| `DATABASE_URL` | SQLAlchemy connection URL. If unset, the local app uses `solar_app.db` in the repository root. |
| `GEMINI_API_KEY` | Enables Gemini-backed assistant and optional rooftop analysis. Not required to run the core application. |
| `GEMINI_MODEL` | Assistant model override; defaults to the model set in `app/config.py`. |
| `GEMINI_ROOF_MODEL` | Rooftop-analysis model override; defaults to `GEMINI_MODEL`. |
| `GOOGLE_SOLAR_API_KEY` / `GOOGLE_MAPS_API_KEY` | Optional Google rooftop-data provider credentials. Provider coverage and successful responses are not guaranteed. |
| `NASA_POWER_URL` / `PVGIS_URL` | Optional endpoint overrides for the irradiance and modeled-yield sources. |
| `NOMINATIM_URL` / `NOMINATIM_REVERSE_URL` | Optional forward- and reverse-geocoding endpoint overrides. |
| `ROOFTOP_PROTOTYPE_FIXTURE` | Controls use of the prototype rooftop fixture when configured data is unavailable; defaults to enabled. |
| `ROOFTOP_EXTERNAL_CACHE_DAYS` | Cache lifetime for reusable external rooftop data; defaults to 30 days. |

The safe variable names and example placeholders are in [`.env.example`](.env.example). Never paste or publish actual credentials in documentation, issue reports, logs, tests, or commits. The Gemini assistant requires `GEMINI_API_KEY`; the rest of the application can run without it. External services also depend on network availability and provider policies.

## Database setup

For local development, running `python run.py` initializes the SQLAlchemy schema, performs the compatibility updates implemented in `app/database.py`, and seeds reference tariff and subsidy data when needed. The default SQLite database is `solar_app.db` in the repository root and is ignored by Git.

Database evolution is handled by `db.create_all()` plus explicit compatibility logic in `app/database.py`; the repository does not use Alembic or maintain an Alembic migration history. This is not a general-purpose migration system. Review and test schema changes explicitly, especially when upgrading an existing database or using PostgreSQL.

For Docker Compose, the default `web` service uses SQLite at `/app/data/solar_app.db` and persists it in the `solar_data` volume. An optional PostgreSQL Compose profile is defined in `docker-compose.yml`; configure deployment credentials securely rather than relying on development defaults.

## Running the application

Start the local development server from the repository root:

```powershell
python run.py
```

Open <http://127.0.0.1:5000/>. The local entry point initializes the database and reference data before starting Flask.

To run the containerized SQLite service, create a private local `.env` from `.env.example`, configure `SECRET_KEY`, and run:

```powershell
docker compose up --build web
```

Open <http://127.0.0.1:8000/>. The container entrypoint initializes the schema and reference data before starting Gunicorn. For a production container command, the image uses `gunicorn --bind 0.0.0.0:8000 --workers 2 run:app`.

## Testing and validation

Run these from the repository root. The commands assume the `.venv312` environment is active and Node.js is installed:

```powershell
python -m pytest tests -q
python -m compileall -q app tests

$javascriptFiles = Get-ChildItem app\static\js -Filter *.js -File | Sort-Object Name
if ($javascriptFiles.Count -eq 0) { throw "No application JavaScript files were found under app/static/js." }
$javascriptFiles | ForEach-Object { node --check $_.FullName }

node --test tests\roof3d_geometry.test.js tests\assistant_voice.test.js tests\voice_mode.test.js
node --test tests\report_tabs.test.js
python scripts\validate_markdown_links.py
git diff --check
```

The report-tabs test is listed separately because the current CI Node invocation does not include `tests/report_tabs.test.js`. Python tests use isolated databases and mocked provider responses; they should not require real API credentials.

## CI workflow

The [`CI workflow`](.github/workflows/ci.yml) runs on pushes and pull requests targeting `main`. It uses Ubuntu 24.04, Python 3.11, and Node.js 24. The job installs the PDF native libraries and Python requirements, verifies WeasyPrint and rasterio, runs the full Python test suite and Python compilation, runs Node syntax validation, runs the three configured Node regression suites, validates Markdown links and anchors, and checks patch whitespace.

JavaScript syntax validation discovers every top-level `*.js` file under `app/static/js` at runtime and runs `node --check` on each discovered file. CI does not depend on a fixed expected file count. The workflow does not build the Docker image, test PostgreSQL, run full browser visual regression, or deploy the application.

## Estimation assumptions and limitations

- Generation, savings, subsidy, financing, payback, environmental impact, and comparison figures are indicative outputs based on persisted assumptions and available inputs. They are not guarantees or approvals.
- Solar-resource paths can use NASA POWER, PVGIS modeled production, or a deterministic low-confidence offline fallback. These sources have different meanings and coverage; provider-modeled output must not be interpreted as a site measurement.
- A drawn roof boundary constrains planning geometry; it does not establish the roof's structure, pitch, elevation, condition, or safe access.
- Rooftop geometry may be simulated prototype geometry when no usable provider reconstruction is available. Provider coverage can be missing, stale, incomplete, malformed, or unavailable.
- Planning-preview panels may overlap areas with unknown shade and are not shade-verified. Conservative placement requires compatible classifications and fails closed when validation data is missing.
- Qualitative shade classes are not converted into a numeric generation loss. Panel placement does not constitute an electrical design, structural assessment, fire-code review, or installation plan.
- Tariffs, export rules, installed costs, financing rates, consumption, degradation, operations and maintenance, and subsidy eligibility are assumptions that may differ from current local conditions.
- PM Surya Ghar guidance is informational. SIGNIX is not affiliated with MNRE or a DISCOM and does not approve, submit, or track government applications.
- Residential and business entry routes are implemented; the business track is not a validated commercial EPC design workflow. Industrial planning is not available.

See [`docs/known-limitations.md`](docs/known-limitations.md) for implementation-specific details.

## Security and contributions

- Do not commit `.env`, API keys, passwords, local databases, generated PDFs, or virtual environments.
- Keep authentication, account ownership, and CSRF checks in place for protected routes and unsafe requests.
- Use mocked provider responses in tests; do not make paid live provider calls from automated tests.
- Before contributing, inspect `git status` and preserve unrelated working-tree changes. Keep changes scoped, add regression tests for behavioral fixes, and update relevant documentation.
- For security concerns, do not include credentials or private user data in a public issue.

The project uses MIT licensing; see [`LICENSE`](LICENSE). Development and collaboration conventions are in [`docs/development.md`](docs/development.md).

## Documentation

- [Architecture and data flows](docs/architecture.md)
- [Frontend guide](docs/frontend-guide.md)
- [Backend guide](docs/backend-guide.md)
- [Verified API contracts](docs/api-contracts.md)
- [Development, testing, and CI](docs/development.md)
- [Known limitations](docs/known-limitations.md)
- [Product principles](PRODUCT.md)
- [Design system](DESIGN.md)

`PERFORMANCE_BASELINE.md` and `PRD_TECH_STACK_MIGRATION.md` are historical references, not current setup or architecture instructions.

## License

SIGNIX is distributed under the MIT License. See [`LICENSE`](LICENSE).
