#!/bin/sh
set -e

echo "========================================================="
echo "==> Starting FieldTrack Coolify Production Deployment..."
echo "========================================================="

# 1. Wait for database if DB_HOST is configured
if [ -n "$DB_HOST" ]; then
    echo "==> Waiting for PostgreSQL database at $DB_HOST:${DB_PORT:-5432}..."
    while ! nc -z "$DB_HOST" "${DB_PORT:-5432}"; do
        sleep 1
    done
    echo "==> Database connection established!"
fi

# 2. Run Database Migrations
echo "==> Running database migrations..."
python manage.py migrate --noinput

# 3. Synchronize RBAC Database Registry
echo "==> Synchronizing RBAC database registry..."
python manage.py shell -c "
try:
    from apps.accounts.rbac_registry import RBACRegistryService
    RBACRegistryService.sync_database()
    print('RBAC registry database sync completed successfully.')
except Exception as e:
    print(f'RBAC sync warning: {e}')
" || true

# 4. Build Tailwind CSS (production bundle)
echo "==> Building Tailwind CSS assets..."
python manage.py tailwind build || echo "Tailwind build skipped or using prebuilt CSS."

# 5. Collect Static Files for WhiteNoise
echo "==> Collecting static files..."
python manage.py collectstatic --noinput

# 6. Execute Gunicorn WSGI Server
echo "==> Launching Gunicorn server on port ${PORT:-8000}..."
exec gunicorn fieldtrack.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers ${GUNICORN_WORKERS:-3} \
    --threads ${GUNICORN_THREADS:-2} \
    --timeout ${GUNICORN_TIMEOUT:-120} \
    --access-logfile - \
    --error-logfile -
