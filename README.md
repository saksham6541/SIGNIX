# SIGNIX --- Rooftop Solar Planning & Estimation

SIGNIX is a Flask-based rooftop solar planning application for India. It
helps users define a rooftop footprint, mark obstructions, estimate
solar generation, explore indicative financial outcomes, save estimates,
compare scenarios, and export homeowner-friendly and technical PDF
reports.

> **Important:** SIGNIX provides planning estimates, not an engineering
> certification, vendor quotation, subsidy approval, or guarantee of
> savings. Results depend on roof geometry, shading, irradiance data,
> tariffs, user inputs, assumptions, and local DISCOM/MNRE rules.

## Contents

-   [Highlights](#highlights)
-   [Residential and Commercial
    tracks](#residential-and-commercial-tracks)
-   [Architecture](#architecture)
-   [End-to-end workflow](#end-to-end-workflow)
-   [Repository map](#repository-map)
-   [Technology stack](#technology-stack)
-   [Local development](#local-development)
-   [Configuration and secrets](#configuration-and-secrets)
-   [Testing and quality checks](#testing-and-quality-checks)
-   [PDF reporting](#pdf-reporting)
-   [Data integrity and calculation
    principles](#data-integrity-and-calculation-principles)
-   [Deployment notes](#deployment-notes)
-   [Known limitations](#known-limitations)
-   [Contributing](#contributing)

## Highlights

-   **Map-based roof planning:** locate a property, draw or edit a roof
    boundary, and mark obstructions.
-   **Geometry-aware sizing:** estimate roof area, usable area,
    obstruction area, and indicative capacity.
-   **Irradiance and generation estimates:** use available solar-data
    providers with fallback behavior when a preferred source is
    unavailable.
-   **Financial planning:** estimate system costs, generation value,
    savings, operating costs, payback, and other available financial
    indicators.
-   **Distinct planning tracks:** separate Residential and Commercial
    calculation policies and report copy.
-   **Saved estimates:** persist estimates for later review and
    comparison.
-   **Scenario comparison:** compare supported variants of an estimate
    while preserving their shared roof grouping.
-   **Report exports:** generate a homeowner-oriented summary PDF and a
    detailed technical PDF.
-   **Bilingual support:** English and Hindi content, including packaged
    Devanagari font handling in PDF templates.
-   **Rooftop visualization:** map-based roof drawings and a 3D rooftop
    visualization workflow.
-   **Testing:** regression tests cover estimation, authentication,
    calculation tracks, report output, rooftop
    geometry/design/ingestion, saved estimates, and other application
    behavior.

## Residential and Commercial tracks

SIGNIX uses separate planning policies for Residential and Commercial
estimates while reusing shared roof geometry and generation workflows.

  -----------------------------------------------------------------------
  Area                    Residential             Commercial
  ----------------------- ----------------------- -----------------------
  Primary financial view  Household bill savings  Annual net operating
                          and payback             benefit, ROI, and
                                                  payback

  Subsidy                 May show an indicative  Does not apply the PM
                          residential subsidy     Surya Ghar residential
                          when applicable; final  subsidy
                          eligibility and amount
                          require official
                          verification    

  Tariff/cost assumptions Residential planning    Commercial planning
                          assumptions             assumptions

  Demand-charge savings   Not presented as a      Demand-charge savings
                          commercial benefit      are **not modelled**
                                                  unless explicitly
                                                  supported by validated
                                                  inputs

  Report copy             Homeowner-focused       Business-focused
  -----------------------------------------------------------------------

The selected track must be established and validated server-side.
Client-supplied fields must not be allowed to switch a request into a
different calculation policy.

## Architecture

SIGNIX follows a Flask application architecture with server-rendered
Jinja templates, route modules, domain services, persisted estimate
records, and external data providers.

``` mermaid
flowchart TD
    U[User / Browser] --> UI[Flask + Jinja pages]
    UI --> MAP[Leaflet map and roof editor]
    MAP --> GEO[Roof geometry and obstruction data]
    UI --> ROUTES[Page, auth and estimate routes]

    ROUTES --> TRACK[Validate planning track and inputs]
    TRACK --> EST[Estimation service]
    GEO --> EST

    EST --> SOLAR[Solar generation and financial modelling]
    SOLAR --> PROVIDERS[Irradiance / weather providers]
    PROVIDERS --> P1[NASA POWER, when configured/available]
    PROVIDERS --> P2[PVGIS, when configured/available]
    PROVIDERS --> P3[Fallback/mock data, where applicable]
    P1 --> SOLAR
    P2 --> SOLAR
    P3 --> SOLAR

    SOLAR --> POLICY{Planning track}
    POLICY --> RES[Residential calculation policy]
    POLICY --> COM[Commercial calculation policy]
    RES --> SAVE[Persist estimate and provenance]
    COM --> SAVE

    SAVE --> DB[(SQLAlchemy-backed database)]
    DB --> DASH[Dashboard, saved estimates and comparisons]
    DB --> CTX[Report context service]
    CTX --> WEBREPORT[HTML report]
    CTX --> PDF[PDF report generator]
    PDF --> W[WeasyPrint when available]
    PDF --> R[ReportLab fallback]
```

### Main responsibilities

-   **Flask application and blueprints:** initialize the app and
    register page, authentication, and estimate endpoints.
-   **Jinja templates:** render the public site, authenticated
    workspace, dashboards, reports, and PDF documents.
-   **Map and geometry workflow:** capture the user-selected roof
    boundary and obstructions; saved user geometry remains
    authoritative.
-   **Estimation service:** validate the request, orchestrate
    calculations, and persist the resulting estimate.
-   **Solar and financial logic:** calculate generation and the
    available financial metrics using the active planning policy.
-   **Provider ingestion:** obtain solar/environmental inputs from
    available providers and handle fallback/cache behavior.
-   **Persistence layer:** store user and estimate records, roof
    geometry, financial outputs, planning track, and relevant
    provenance.
-   **Report context and generator:** enrich saved estimates for
    HTML/PDF reports and render the full technical or homeowner summary
    output.

## End-to-end workflow

``` mermaid
flowchart TD
    A[Open SIGNIX] --> B{Planning track}
    B -->|Residential| C[Residential entry / survey]
    B -->|Commercial| D[Business entry / survey]
    C --> E[Sign in or create account]
    D --> E
    E --> F[Locate property]
    F --> G[Draw or confirm roof boundary]
    G --> H[Mark obstructions]
    H --> I[Configure system and bill / tariff inputs]
    I --> J[Validate track and estimate inputs]
    J --> K[Fetch available irradiance data]
    K --> L[Calculate roof capacity and generation]
    L --> M[Apply track-specific financial policy]
    M --> N[Save estimate and provenance]
    N --> O[Review report and dashboard]
    O --> P[Compare supported scenarios]
    O --> Q[Export homeowner summary PDF]
    O --> R[Export full technical PDF]
```

### Estimate lifecycle

1.  The user chooses a Residential or Commercial workflow.
2.  The application authenticates the user where required and preserves
    a safe return destination.
3.  The user locates the property and draws/edits the roof boundary.
4.  The user marks obstructions and supplies the available system/bill
    inputs.
5.  The server validates inputs and applies the authoritative planning
    track.
6.  The estimation workflow calculates geometry, generation, and
    financial outputs.
7.  The estimate and relevant track/provenance data are persisted.
8.  The user reviews saved estimates, compares supported scenarios, and
    exports reports.

## Repository map

The following are key modules and their responsibilities. The exact tree
may evolve; use the current repository as the source of truth.

``` text
SIGNIX/
├── app/
│   ├── init.py                         # Flask app setup and blueprint registration
│   ├── auth.py                         # Authentication, profile/settings routes
│   ├── pages.py                        # Page routes, dashboards, reports and saved views
│   ├── estimate.py                     # Estimate API and request handling
│   ├── forms.py                        # WTForms definitions
│   ├── models.py                       # SQLAlchemy models and persisted estimate data
│   ├── solar_logic.py                  # Core solar calculations and data orchestration
│   ├── report_generator.py             # PDF generation and renderer fallback
│   ├── discom_lookup.py                # DISCOM-related lookup/guidance
│   ├── application_checklist.py        # Subsidy/application guidance
│   ├── services/
│   │   ├── estimation_service.py       # Estimate orchestration and persistence
│   │   ├── residential_calculation_service.py
│   │   ├── commercial_calculation_service.py
│   │   ├── location_service.py         # Location/report context enrichment
│   │   ├── rooftop_ingestion_service.py
│   │   ├── rooftop_design_service.py
│   │   ├── report_chart_data.py
│   │   └── ...                         # Other domain services
│   ├── templates/                      # Jinja HTML pages and PDF templates
│   │   ├── pdf_summary.html            # Homeowner/business summary PDF
│   │   ├── pdf_report.html             # Full technical PDF
│   │   ├── report.html                 # Interactive report
│   │   ├── index.html                  # Map/estimate workspace
│   │   └── ...
│   └── static/
│       ├── css/
│       ├── js/
│       ├── fonts/
│       └── ...
├── tests/
│   ├── goldens/                        # Regression fixtures where applicable
│   └── test_*.py                       # Unit/integration/regression tests
├── translations/
│   └── hi/LC_MESSAGES/                 # Hindi translation catalogue
├── docs/                               # Project documentation, if present
├── run.py                              # Local application entry point
├── requirements.txt                    # Python dependencies, if present
├── README.md
└── ...
```

## Technology stack

  -----------------------------------------------------------------------
  Layer                               Technologies
  ----------------------------------- -----------------------------------
  Backend                             Python, Flask

  HTML rendering                      Jinja2

  Forms and validation                WTForms / Flask-WTF

  Persistence                         Flask-SQLAlchemy / SQLAlchemy;
                                      SQLite may be used for local
                                      development

  Mapping                             Leaflet.js and Leaflet Draw

  Rooftop visualization               Three.js and supporting frontend
                                      modules

  Charts                              Chart.js and existing report
                                      chart-data utilities

  Solar/scientific calculations       Existing solar model and
                                      numerical/geospatial dependencies
                                      used by the project

  External data                       NASA POWER, PVGIS, and
                                      configured/fallback providers as
                                      available

  PDF                                 WeasyPrint where its native
                                      dependencies are available;
                                      ReportLab fallback

  Tests                               pytest

  Localization                        English/Hindi translations and
                                      packaged Devanagari font assets
  -----------------------------------------------------------------------

Provider availability depends on configuration, network access, and the
relevant service response. Do not assume every provider is active in
every environment.

## Local development

### Requirements

-   Python version supported by the repository's dependency pins.
-   Git.
-   Windows users may use PowerShell.
-   External provider API keys only if the chosen provider requires
    them.
-   WeasyPrint's native libraries if you want to use its PDF renderer
    locally; ReportLab can serve as the fallback when supported by the
    application.

### Windows / PowerShell setup

Use the project's compatible virtual environment if one already exists.
In this workspace, Python 3.12 was used in `.venv312`.

``` powershell
# From the SIGNIX repository root
.\.venv312\Scripts\Activate.ps1

# Install dependencies only if the environment has not already been set up
python -m pip install -r requirements.txt

# Start the application
python run.py
```

If the virtual environment does not exist, create one using the Python
version supported by the current dependency pins:

``` powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run.py
```

If the repository's dependency file has a different name or setup
instructions, follow the current checked-in project configuration rather
than guessing package versions.

### Run tests

Run the suite from the repository root:

``` powershell
python -m pytest tests -q
```

Run focused tests while working on a specific area:

``` powershell
python -m pytest tests/test_calculation_tracks.py -q
python -m pytest tests/test_report_generator.py -q
```

Other useful checks:

``` powershell
git diff --check
python -m compileall app
```

Some tests and PDF output can vary by operating system and installed
native libraries. A local ReportLab PDF pass does not prove that
WeasyPrint produces identical visual output in CI, and vice versa.

## Configuration and secrets

-   Keep secrets in environment variables or the local environment file
    expected by the application.
-   Never commit `.env`, API tokens, database credentials, private keys,
    or real user data.
-   Do not put provider keys into frontend JavaScript or templates.
-   Keep `.env`, virtual environments, local databases, caches, and
    generated artifacts excluded from version control as appropriate.
-   Use a configured public application base URL for external report
    links and QR codes. Do not publish localhost, loopback,
    private-network, or intranet addresses in reports intended for
    external users.
-   If no valid public URL is configured, the report should omit the
    external link/QR or clearly identify the link as local-only.

Consult the actual configuration module and environment template in the
repository for the exact variable names. Do not assume a key or setting
is enabled merely because a provider is mentioned in the documentation.

## Testing and quality checks

The test suite includes coverage for areas such as:

-   Authentication and account settings.
-   Residential/Commercial calculation-track enforcement.
-   Estimate routes and authorization.
-   Saved estimates and scenario comparisons.
-   Location discovery.
-   Report chart data and PDF/report output.
-   Rooftop geometry, reconstruction, design, ingestion, and layout
    acceptance.
-   Regression fixtures/golden outputs.

Before a release or a large merge:

1.  Review the diff and ensure unrelated work is not staged.
2.  Run focused tests for the changed area.
3.  Run the full test suite.
4.  Run `git diff --check`.
5.  Generate a Residential summary and full report.
6.  Generate a Commercial summary and full report.
7.  Inspect page breaks, financial reconciliation, track-specific copy,
    Unicode/Hindi text, chart readability, and QR/public-link behavior.
8.  Check CI, especially if it uses a different PDF renderer or
    operating system.

## PDF reporting

SIGNIX provides two report levels:

### Homeowner / business summary

A concise decision-support document intended to explain the estimate in
plain language. It may include KPI cards, cost breakdown, payback
scenarios, suitability, generation/financial visuals, assumptions,
limitations, and next steps when supported by available data.

### Full technical report

A detailed supporting document that can include roof geometry,
obstruction areas, system sizing, suitability factors, performance
indicators, monthly generation, financial outputs, environmental
estimates, assumptions, and limitations.

### PDF rendering

-   **WeasyPrint** is used when it is available and its required native
    dependencies load correctly.
-   **ReportLab** provides a fallback path.
-   Templates and styles must work across the renderers where the
    application supports both.
-   Hindi PDF output relies on the packaged Devanagari font path and
    should be tested explicitly.
-   Keep financial headings with their associated tables where
    practical, avoid split rows and orphaned headings, and check actual
    rendered pages after layout changes.

PDFs are planning documents. Financial values, subsidy amounts,
generation, and savings must be derived from the estimate data and
labelled with assumptions. Do not copy values from example PDFs into
production logic.

## Data integrity and calculation principles

-   The user-drawn rooftop footprint is authoritative. Provider or
    AI-derived geometry must not silently overwrite the saved user
    boundary.
-   Preserve coordinate conventions: GeoJSON coordinates use
    longitude/latitude order; Leaflet APIs commonly use
    latitude/longitude order.
-   Validate the planning track server-side; do not rely on a
    client-provided `property_type` alone to determine calculation
    policy.
-   Keep Residential and Commercial financial policies separate while
    reusing shared geometry and generation components.
-   Preserve provenance and uncertainty where available. A fallback/mock
    result must not be presented as a verified provider result.
-   Do not imply subsidy approval, guaranteed bill reduction, guaranteed
    payback, or a firm EPC quote.
-   If a cost component is missing, label it unavailable rather than
    treating it as zero.
-   Clearly distinguish simple/nominal payback, subsidy-adjusted
    payback, and discounted payback.
-   Commercial demand-charge savings are not modelled unless validated
    data and an implemented calculation explicitly support them.

## Deployment notes

Before deploying:

-   Set a secure secret key and production configuration.
-   Use a production WSGI server appropriate for the hosting platform.
-   Configure a persistent database and backups appropriate for the
    deployment.
-   Ensure the instance has access to required static assets, packaged
    fonts, and PDF dependencies.
-   Configure the public application URL for links and QR codes.
-   Keep debug mode disabled in production.
-   Check allowed hosts, HTTPS, secure cookies, authentication
    redirects, and authorization on estimate/report routes.
-   Review logs for provider failures and PDF-renderer fallback
    behavior.
-   Verify the production build with both Residential and Commercial
    flows.
-   Do not expose local file paths, internal IP addresses, secrets, or
    debugging details to end users.

Deployment commands and environment variables vary by hosting provider;
use the actual deployment configuration in the repository.

## Known limitations

-   Estimates depend on the quality and completeness of roof, shading,
    weather/irradiance, tariff, and user-input data.
-   A remote solar-data provider may be unavailable or return incomplete
    data; fallback behavior may be used.
-   Shading from nearby buildings/trees and detailed site conditions may
    require a site survey.
-   Net-metering, sanctioned load, tariff slabs, subsidy rules, and
    local incentives require confirmation with the relevant DISCOM and
    official sources.
-   Demand-charge and time-of-use savings are not modelled unless
    explicitly supported by validated inputs and implementation.
-   PDF appearance can differ between WeasyPrint and ReportLab.
    WeasyPrint may require operating-system native libraries that are
    not installed by default.
-   Sensitivity analysis should be shown only when the application can
    calculate it from available inputs; unsupported values must not be
    fabricated.
-   SIGNIX is not a substitute for structural review, electrical design,
    installer assessment, official subsidy approval, or a binding vendor
    quotation.

## Contributing

1.  Create a branch for the change.
2.  Keep changes scoped to the intended feature.
3.  Preserve the existing Residential/Commercial calculation policies
    and saved-estimate behavior.
4.  Add or update regression tests for behavior changes.
5.  Run focused tests and the full suite where practical.
6.  Review `git diff --check` and inspect staged changes before
    committing.
7.  Never commit secrets, local databases, virtual environments, or
    generated preview files.

## License and project details

Confirm the repository's current license file before redistributing this
project. If no license is present, do not assume the code is open-source
licensed.

Project repository: <https://github.com/saksham6541/SIGNIX>
