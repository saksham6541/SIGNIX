# Development Guide

## Prerequisites

- Python 3.11
- Node.js 24 for syntax and browser-logic unit tests
- Git
- Optional: Docker Desktop and Docker Compose

WeasyPrint requires native libraries in some local environments; the Dockerfile installs its Linux dependencies. The application retains a ReportLab fallback.

Hindi PDF reports use the packaged `app/static/fonts/NotoSansDevanagari-Regular.ttf` file. The application gives WeasyPrint an absolute `file:` URI so report rendering does not depend on a running Flask static-file server. Linux CI and the Docker image also register that same file with Fontconfig; `fc-scan` and `fc-match "Noto Sans Devanagari"` should identify it. On Windows, a missing Pango runtime causes the application to use its ReportLab fallback, so a local PDF test can pass without exercising WeasyPrint; the Ubuntu CI job is the authoritative WeasyPrint check.

## Environment

Create `.env` from `.env.example` and replace `SECRET_KEY`. The SIGNIX Assistant requires a valid `GEMINI_API_KEY`; the app's `run.py` entry point loads `.env` before creating the Flask application. Set `GEMINI_API_KEY` in the server environment for other deployment entry points. Replace the example placeholder with a key from Google AI Studio, keep it server-side, and restart the application. Provider keys for non-chat features are otherwise optional. `.env`, databases, caches, virtual environments, editor settings, logs, generated PDFs, and build output are ignored.

Important optional variables:

- `DATABASE_URL`
- `NASA_POWER_URL`, `PVGIS_URL`
- `NOMINATIM_URL`, `NOMINATIM_REVERSE_URL`
- `GEMINI_API_KEY` (required for Assistant chat), `GEMINI_MODEL`, `GEMINI_ROOF_MODEL`
- `GOOGLE_SOLAR_API_KEY` or `GOOGLE_MAPS_API_KEY`
- `ROOFTOP_PROTOTYPE_FIXTURE`, `ROOFTOP_EXTERNAL_CACHE_DAYS`

Do not put real credentials in examples, tests, Markdown, or committed fixtures.

## Local workflow

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
```

The root `requirements.txt` is authoritative.

## Test workflow

Start focused, then run the complete active suite:

```powershell
python -m pytest tests/test_solar_logic.py -q
python -m pytest tests -q
python -m compileall -q app tests
Get-ChildItem app\static\js -Filter *.js | ForEach-Object { node --check $_.FullName }
node --test tests\roof3d_geometry.test.js
node --test tests\assistant_voice.test.js tests\voice_mode.test.js
python scripts\validate_markdown_links.py
git diff --check
```

Tests may create ignored `__pycache__/` and `.pytest_cache/` directories. They are disposable and must not be committed.

## Continuous integration

`.github/workflows/ci.yml` runs one least-privilege Ubuntu 24.04 job for pushes and pull requests targeting `main`. The job uses Python 3.11 and Node.js 24 and performs these checks once, in order:

1. Install the documented Pango/Harfbuzz/font runtime libraries used by WeasyPrint.
2. `python -m pip install -r requirements.txt`
3. Render a minimal WeasyPrint PDF and import rasterio, so native dependency failures cannot hide behind the ReportLab fallback or lazy imports.
4. `python -m pytest tests -q`
5. `python -m compileall -q app tests`
6. `node --check` for every `app/static/js/*.js` file
7. `node --test tests/roof3d_geometry.test.js tests/assistant_voice.test.js tests/voice_mode.test.js`
8. `python scripts/validate_markdown_links.py`
9. `git diff --check` over the pushed or pull-request change range

The explicit `tests` path keeps collection scoped to the maintained application suite. The Node tests use the built-in test runner and deterministic test doubles; they do not require a browser or JavaScript package installation. The Markdown validator uses only the Python standard library and validates tracked or non-ignored local Markdown targets and GitHub-style heading anchors.

Tests isolate databases through pytest fixtures and mock provider requests. CI deliberately does not supply `.env`, Google Solar, Gemini, NASA POWER, PVGIS, geocoding, database, or other service credentials. A failure caused by an attempted live request should be treated as a test-isolation defect, not fixed by adding a secret.

To reproduce CI in Windows PowerShell after activating the Python 3.11 environment:

```powershell
python -m pip install -r requirements.txt
python -m pytest tests -q
python -m compileall -q app tests
$javascriptFiles = Get-ChildItem app\static\js -Filter *.js -File | Sort-Object Name
if ($javascriptFiles.Count -eq 0) { throw "No application JavaScript files were found under app/static/js." }
$javascriptFiles | ForEach-Object { node --check $_.FullName }
node --test tests\roof3d_geometry.test.js tests\assistant_voice.test.js tests\voice_mode.test.js
python scripts\validate_markdown_links.py
git diff --check
```

Interpret failures from the first failing step: pytest identifies backend/contract regressions; `compileall` identifies invalid Python; `node --check` identifies invalid JavaScript; Node TAP output identifies geometry or voice regressions; the Markdown validator prints each broken source link; and `git diff --check` identifies whitespace errors. GitHub-hosted Linux remains the authoritative check for filesystem case sensitivity and Linux dependency compatibility.

CI does not build or deploy the Docker image, exercise PostgreSQL, or run full browser automation. Those remain explicit integration or release checks.

## Collaboration

1. Branch from the agreed integration branch. Use small, topic-specific changes.
2. Inspect `git status` before editing; preserve other developers' uncommitted work.
3. Frontend work stays primarily in templates/static assets; backend work stays in route/service/domain modules.
4. Coordinate changes to API payloads, GeoJSON/local-coordinate contracts, surface IDs, geometry keys, layout states, and persisted extras.
5. Add regression tests for corrected behavior. Do not weaken assertions or regenerate goldens without an intentional reviewed model change.
6. Never use live paid provider calls in automated tests.
7. Do not commit local databases, caches, credentials, PDFs, or virtual environments.

## Dependency policy

Check imports, scripts, Docker, report generation, and tests before removing a package. Do not broadly upgrade or regenerate dependency files during unrelated work. The project currently has one active Python dependency manifest and no JavaScript package manifest.

## Documentation policy

- `README.md` is the setup entry point.
- `docs/` contains current technical guidance.
- `PRODUCT.md` and `DESIGN.md` define product and interface intent.
- Root performance/PRD documents are historical records.
- Update affected documentation and verify relative links with every structural or contract change.

## Deployment changes

Validate `Dockerfile`, `docker-entrypoint.sh`, and Compose paths together. The image build context is the repository root. CI validates source and tests but does not build the image or deploy it. No Render manifest currently exists.
