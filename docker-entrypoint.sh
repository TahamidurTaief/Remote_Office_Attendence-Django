#!/bin/sh
set -e

echo "========================================================="
echo "==> Starting FieldTrack Production Deployment..."
echo "==> Domain: trackme.signtechlimited.com"
echo "========================================================="

# 1. Wait for database connection (PostgreSQL or SQLite)
echo "==> Verifying database connection..."
python -c "
import sys, time
import django
django.setup()
from django.db import connection
for attempt in range(1, 31):
    try:
        connection.ensure_connection()
        print('==> Database connection established successfully!')
        sys.exit(0)
    except Exception as e:
        print(f'==> Waiting for database (attempt {attempt}/30)... {e}')
        time.sleep(2)
sys.exit(1)
"

# 2. Run Database Migrations
echo "==> Running database migrations..."
python manage.py migrate --noinput

# 3. If target database has 0 users and sqlite_source exists, import data from SQLite
echo "==> Checking if initial database data needs to be imported from SQLite..."
python manage.py copy_sqlite_to_pg || echo "SQLite import skipped or already populated."

# 4. Bootstrap tenant, RBAC, workflows, and banks
echo "==> Bootstrapping tenant and system configuration..."
python manage.py bootstrap_tenant || true
python manage.py bootstrap_rbac || true
python manage.py seed_workflow_definitions || true
python manage.py seed_bangladesh_banks || true

# 5. Build Tailwind CSS (production bundle)
echo "==> Building Tailwind CSS assets..."
python manage.py tailwind build || echo "Tailwind build skipped or using prebuilt CSS."

# 6. Collect Static Files for WhiteNoise
echo "==> Collecting static files..."
python manage.py collectstatic --noinput

# 7. Execute Gunicorn WSGI Server (Fast, Multithreaded, Production-Hardened)
echo "==> Launching Gunicorn server on port ${PORT:-8000}..."
exec gunicorn fieldtrack.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers ${GUNICORN_WORKERS:-4} \
    --threads ${GUNICORN_THREADS:-2} \
    --worker-class gthread \
    --timeout ${GUNICORN_TIMEOUT:-120} \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile -
