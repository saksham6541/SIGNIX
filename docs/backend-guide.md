# Backend Guide

## Entry point and application factory

- `run.py` creates the Flask application and provides local development startup plus seed helpers.
- `app/__init__.py` contains `create_app()`, extension initialization, blueprint registration, locale selection, authentication behavior, and common error handlers.
- Production uses `gunicorn --bind 0.0.0.0:8000 --workers 2 run:app`.

## Route modules

- `auth.py`: signup, login, logout, profile, settings, and language.
- `pages.py`: HTML pages, report/PDF delivery, comparisons, and installer export.
- `estimate.py`: estimate, orientation, financing, tariff, and location-context APIs.
- `locations.py`: discovery, saved-location retrieval, and scenario cloning.
- `rooftop_design.py`: design revisions, ingestion/review, and layout acceptance.
- `roof_model.py`: reconstructed roof model and protected provider layers.
- `roof_ai.py`: satellite imagery and Gemini advisory analysis.
- `assistant.py`: authenticated, provider-configurable FAQ assistant with Groq as the default and an optional OpenAI fallback.

Routes should remain thin: authenticate, parse, delegate, and shape HTTP errors. Put reusable domain work in services.

## Services

- `estimation_service.py`: request validation, saved estimates, orientation recalculation, scenarios, and accepted-layout recalculation.
- `location_service.py`: geocoding, Maps-link parsing, report contexts, and location queries.
- `rooftop_design_service.py`: revision ownership, authoritative/advisory separation, reviews, and invalidation.
- `rooftop_ingestion_service.py`: provider retrieval, normalization, caching, and diagnostics.
- `rooftop_reconstruction_service.py`: clipped surfaces, structures, geometry keys, and provider/prototype states.
- `rooftop_shade_service.py`: non-overlapping roof classification and area metrics.
- `rooftop_layout_service.py`: server validation and transactional layout acceptance.
- `report_chart_data.py`: presentation-safe datasets derived from persisted calculations.
- `cache.py`: diskcache-based irradiance cache.

## Domain modules

- `solar_logic.py`: sizing, irradiance paths, generation, subsidy, financing, and provenance.
- `geo.py`: coordinate contracts, validation, clipping, and local projection.
- `models.py`: SQLAlchemy records and serialization.
- `assumptions.py`: versioned planning constants and public caveats.
- `report_generator.py` and `installer_export.py`: PDF and installer deliverables.
- `database.py` and `seeds.py`: schema initialization/compatibility and reference rows.

## Authentication and request safety

Saved data and calculation mutations are account-scoped with `@login_required`. Unsafe methods require Flask-WTF CSRF validation. API callers receive JSON 401/400 responses; normal pages redirect unauthenticated users to login.

Never accept client-derived authoritative panel capacity, generation, or financial totals. Layout acceptance derives those values after server-side geometry validation.

## Providers and graceful fallback

- NASA POWER is the primary location-wide irradiance source.
- PVGIS is a modeled-production fallback.
- Deterministic mock irradiance supports offline planning and must remain low confidence.
- Google Solar is optional and must not be called automatically without configuration and an explicit workflow.
- Gemini roof analysis is advisory and requires explicit review before accepted data changes.
- Assistant chat supports `groq`, `openai`, and the retained `gemini` provider. OpenAI fallback is attempted only after a recoverable primary-provider failure; authentication and configuration failures do not fall through.

Provider failures must return diagnostics or a truthful unavailable state while preserving the last successful reusable cache entry where the service supports it.

## Database work

Local data defaults to ignored `solar_app.db`; Docker SQLite uses `/app/data/solar_app.db`. PostgreSQL is selected by `DATABASE_URL`. There is no Alembic directory, so changes to model columns or constraints must be paired with `database.py` compatibility logic or an explicit migration plan and tested on both backends.

Do not rewrite existing GeoJSON records to change coordinate interpretation. Conversion belongs at explicit application boundaries.

## Tests

Run the full active suite from the outer repository root:

```powershell
python -m pytest tests -q
python -m compileall -q app tests
```

Use mocked provider responses. Focused test modules cover coordinates, calculations, golden estimates, design revisions, ingestion, reconstruction, shade, layout acceptance, routes, report output, authentication, and ownership.
