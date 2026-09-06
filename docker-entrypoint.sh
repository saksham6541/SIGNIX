#!/bin/sh
set -eu

python -c "from run import app, seed_reference_data; from app.models import db; ctx = app.app_context(); ctx.push(); db.create_all(); seed_reference_data()"

exec "$@"