FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////app/data/solar_app.db

# Set SECRET_KEY at runtime. DATABASE_URL, NASA_POWER_URL, and PVGIS_URL may
# also be overridden at runtime for a persistent database or alternate APIs.

WORKDIR /app

# WeasyPrint uses these native libraries for text shaping, fonts, images, and PDF output.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libcairo2 \
        libffi-dev \
        libffi8 \
        libfontconfig1 \
        libfreetype6 \
        libgdk-pixbuf-2.0-0 \
        libglib2.0-0 \
        libharfbuzz0b \
        libharfbuzz-subset0 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libpangoft2-1.0-0 \
        libjpeg62-turbo \
        libopenjp2-7 \
        libpng16-16 \
        shared-mime-info \
        fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/data

VOLUME ["/app/data"]
EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "run:app"]