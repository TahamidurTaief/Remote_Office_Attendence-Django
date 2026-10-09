#!/bin/sh
set -e

# 0. Ensure runtime storage volume permissions & drop to appuser if running as root
if [ "$(id -u)" = "0" ]; then
    echo "==> Ensuring storage volume permissions for appuser..."
    mkdir -p /app/staticfiles /app/media /app/data /app/media/attendance/photos /app/media/cache
    chown -R appuser:appuser /app/staticfiles /app/media /app/data
    chmod -R 775 /app/staticfiles /app/media /app/data
    if command -v gosu >/dev/null 2>&1; then
        exec gosu appuser "$0" "$@"
    elif command -v su-exec >/dev/null 2>&1; then
        exec su-exec appuser "$0" "$@"
    else
        exec su -s /bin/sh appuser -c "$0 $*"
    fi
fi

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
from django.conf import settings

target_db = settings.DATABASES['default']
db_name = target_db.get('NAME')

for attempt in range(1, 31):
    try:
        connection.ensure_connection()
        print(f'==> Database connection established successfully to {db_name}!')
        sys.exit(0)
    except Exception as e:
        err_msg = str(e)
        if 'does not exist' in err_msg and 'postgresql' in target_db.get('ENGINE', ''):
            print(f'==> Target database \"{db_name}\" does not exist. Attempting auto-creation...')
            try:
                import psycopg2
                from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
                conn = psycopg2.connect(
                    dbname='postgres',
                    user=target_db.get('USER'),
                    password=target_db.get('PASSWORD'),
                    host=target_db.get('HOST'),
                    port=target_db.get('PORT') or 5432,
                )
                conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                cur = conn.cursor()
                cur.execute(f'CREATE DATABASE \"{db_name}\"')
                conn.close()
                print(f'==> Database \"{db_name}\" created successfully!')
                connection.close()
                connection.ensure_connection()
                sys.exit(0)
            except Exception as ce:
                print(f'==> Auto-creation warning: {ce}')
        print(f'==> Waiting for database (attempt {attempt}/30)... {e}')
        time.sleep(2)
sys.exit(1)
"

# 2. Run Database Migrations
echo "==> Running database migrations..."
python manage.py migrate --noinput

# 3. Synchronize PostgreSQL Primary Key Sequences with Table Max IDs
echo "==> Synchronizing database primary key sequences..."
python manage.py reset_pg_sequences || true

# 4. If target database has 0 users and sqlite_source exists, import data from SQLite
echo "==> Checking if initial database data needs to be imported from SQLite..."
python manage.py copy_sqlite_to_pg || echo "SQLite import skipped or already populated."
python manage.py reset_pg_sequences || true

# 5. Bootstrap tenant and RBAC registry
echo "==> Bootstrapping tenant and system configuration..."
python manage.py bootstrap_tenant || true
python manage.py bootstrap_rbac || true

# 6. Collect Static Files for WhiteNoise
echo "==> Collecting static files..."
python manage.py collectstatic --noinput

# 6. Execute Custom Command or Gunicorn WSGI Server
if [ $# -gt 0 ]; then
    echo "==> Executing custom command: $@"
    exec "$@"
fi

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
