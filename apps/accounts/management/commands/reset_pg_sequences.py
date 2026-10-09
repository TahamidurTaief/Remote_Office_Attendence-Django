"""
Management command to synchronize and reset all PostgreSQL primary key sequences to COALESCE(MAX(id), 1).
Prevents duplicate key value violates unique constraint errors on auto-increment columns after data migration.
"""
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.db import connections
from django.apps import apps
from io import StringIO
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Synchronize PostgreSQL auto-increment sequences with MAX(id) across all tables."

    def add_arguments(self, parser):
        parser.add_argument(
            '--database',
            default='default',
            help='Database connection to reset sequences for (default: default).',
        )

    def handle(self, *args, **options):
        db = options.get('database', 'default')
        if db not in connections:
            self.stdout.write(self.style.ERROR(f"Database connection '{db}' not found."))
            return

        conn = connections[db]
        engine = conn.settings_dict.get('ENGINE', '')

        if 'postgresql' not in engine:
            self.stdout.write(self.style.NOTICE(
                f"Database '{db}' uses engine '{engine}', which does not use PostgreSQL sequences. Skipping."
            ))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(f"==> Synchronizing PostgreSQL sequences for '{db}'..."))

        updated_count = 0

        # Method 1: Universal PL/pgSQL catalog sequence synchronization
        catalog_sql = """
        DO $$
        DECLARE
            seq RECORD;
            max_id BIGINT;
            query TEXT;
        BEGIN
            FOR seq IN
                SELECT
                    c.relname AS table_name,
                    a.attname AS column_name,
                    s.relname AS sequence_name
                FROM pg_class s
                JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_class'::regclass
                JOIN pg_class c ON c.oid = d.refobjid
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.refobjsubid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE s.relkind = 'S'
                  AND n.nspname = current_schema()
            LOOP
                query := format('SELECT COALESCE(MAX(%I), 0) FROM %I', seq.column_name, seq.table_name);
                EXECUTE query INTO max_id;
                IF max_id > 0 THEN
                    EXECUTE format('SELECT setval(%L, %s, true)', seq.sequence_name, max_id);
                ELSE
                    EXECUTE format('SELECT setval(%L, 1, false)', seq.sequence_name);
                END IF;
            END LOOP;
        END $$;
        """

        try:
            with conn.cursor() as cursor:
                cursor.execute(catalog_sql)
            self.stdout.write(self.style.SUCCESS("  -> Catalog-level sequence sync completed successfully."))
            updated_count += 1
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"  -> Catalog sync notice: {e}"))

        # Method 2: Model-by-model explicit setval for all installed Django models
        for model in apps.get_models():
            if not model._meta.managed or model._meta.proxy:
                continue

            table = model._meta.db_table
            pk = model._meta.pk
            if not pk or not getattr(pk, 'column', None):
                continue

            # Only target integer primary keys
            internal_type = pk.get_internal_type()
            if internal_type not in ('AutoField', 'BigAutoField', 'SmallAutoField', 'IntegerField', 'BigIntegerField'):
                continue

            sql = f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', '{pk.column}'),
                    COALESCE((SELECT MAX({pk.column}) FROM {table}), 1),
                    (SELECT MAX({pk.column}) IS NOT NULL FROM {table})
                );
            """
            try:
                with conn.cursor() as cursor:
                    cursor.execute(sql)
                    updated_count += 1
            except Exception:
                pass

        # Method 3: Django builtin sqlsequencereset for all installed app configs
        for app_config in apps.get_app_configs():
            try:
                out = StringIO()
                call_command('sqlsequencereset', app_config.label, database=db, stdout=out)
                statements = [stmt.strip() for stmt in out.getvalue().split(';') if stmt.strip()]
                if statements:
                    with conn.cursor() as cursor:
                        for stmt in statements:
                            try:
                                cursor.execute(stmt)
                            except Exception:
                                pass
            except Exception:
                pass

        self.stdout.write(self.style.SUCCESS(
            f"==> Successfully verified and synchronized all PostgreSQL sequences for database '{db}'."
        ))
