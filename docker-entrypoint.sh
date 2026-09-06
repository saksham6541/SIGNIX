#!/bin/sh
set -eu

python -c "from run import app, initialize_database, seed_reference_data; ctx = app.app_context(); ctx.push(); initialize_database(); seed_reference_data()"

exec "$@"