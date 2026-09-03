# SIGNIX

**Track · Save · Impact**<br>
Rooftop solar potential and subsidy estimator for Indian homes.

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.0.3-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)]

SIGNIX helps homeowners in India understand the solar potential of their rooftop. Draw a rooftop footprint on a map, receive an estimated solar generation and financial outlook, and see an indicative PM Surya Ghar subsidy estimate. Results can be saved, reviewed from a dashboard, and exported as a PDF report.

## Key features

- Draw and edit rooftop polygons and obstructions on an interactive map.
- Look up solar irradiance and temperature data through NASA POWER, with PVGIS and mock-data fallbacks.
- Estimate system size, monthly and annual generation, savings, payback, environmental impact, and related metrics.
- Calculate indicative PM Surya Ghar subsidy amounts based on system size and eligibility inputs.
- Save estimates and browse recent results in a dashboard view.
- Generate downloadable PDF reports using WeasyPrint, with a ReportLab fallback when required system libraries are unavailable.

## Tech stack

| Layer           | Technology                           |
| --------------- | ------------------------------------ |
| Backend         | Flask 3.0.3                          |
| Templates       | Jinja2                               |
| Mapping         | Leaflet.js and Leaflet Draw          |
| Persistence     | Flask-SQLAlchemy, SQLAlchemy, SQLite |
| Solar modelling | pvlib, pandas, NumPy                 |
| Data services   | NASA POWER, PVGIS, requests          |
| Reports         | WeasyPrint, ReportLab fallback       |
| Charts          | Chart.js                             |

## Screenshots

Screenshots will be added as the interface is documented:

![Dashboard](docs/screenshot-dashboard.png)

![Rooftop estimate](docs/screenshot-estimate.png)

![Solar report](docs/screenshot-report.png)

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/saksham6541/SIGNIX.git
cd SIGNIX/solar_app
```

### 2. Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the application

```bash
python run.py
```

The development server runs at [http://127.0.0.1:5000](http://127.0.0.1:5000) by default.

## Project structure

```text
app/
├── __init__.py          # Flask application factory
├── config.py            # Application and solar-model configuration
├── models.py            # SQLAlchemy models and database setup
├── report_generator.py  # HTML-to-PDF reports and fallback generation
├── pages.py             # Page and report routes
├── estimate.py          # Estimation API route
├── locations.py         # Location and geocoding API routes
├── services/
│   ├── estimation_service.py # Estimation and persistence service
│   └── location_service.py   # Location queries and geocoding service
├── solar_logic.py       # Rooftop, irradiance, generation, and finance logic
├── static/
│   ├── css/style.css    # Application styles
│   └── js/              # Map and dashboard interactions
└── templates/           # Jinja2 page and report templates
```

## Roadmap

SIGNIX is being improved incrementally rather than treated as a finished production platform. Current improvement areas include:

- Add focused tests for geometry, solar calculations, subsidy rules, and API routes.
- Cache repeated irradiance lookups and continue measuring endpoint performance.
- Improve validation, observability, and error handling around external data services.
- Containerize the application for more repeatable development and deployment.
- Evaluate a larger frontend or backend migration only when product scope and usage justify it.

## License

This project is intended to be released under the MIT License.

## Author

Saksham Kaushik - [github.com/saksham6541/SIGNIX](https://github.com/saksham6541/SIGNIX)
