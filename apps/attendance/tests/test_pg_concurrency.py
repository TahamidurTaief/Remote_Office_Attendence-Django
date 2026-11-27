import decimal
import os
import threading
import unittest
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TransactionTestCase
from django.utils import timezone

from apps.attendance.models import Attendance, AttendanceLocation, AttendancePolicy
from apps.attendance.reporting_service import BD_TZ
from apps.attendance.transaction_service import (
    AttendanceTransactionError,
    AttendanceTransactionService,
)
from apps.branches.models import Branch
from apps.employees.models import Employee, EmployeeProfile

User = get_user_model()


def _is_postgres():
    return connection.vendor == "postgresql"


def _require_postgres():
    return os.getenv("REQUIRE_POSTGRES_TESTS") == "1"


@unittest.skipIf(
    not _is_postgres() and not _require_postgres(),
    "Requires isolated PostgreSQL for row-level locking concurrency proof",
)
class PostgreSQLCheckoutConcurrencyTests(TransactionTestCase):
    """
    Separate-connection concurrency tests using TransactionTestCase + threading.Barrier.
    When REQUIRE_POSTGRES_TESTS=1 (CI), fails immediately if backend is not PostgreSQL.
    """

    def setUp(self):
        self.assertEqual(
            connection.vendor,
            "postgresql",
            f"Expected postgresql backend, got {connection.vendor}",
        )
        self.branch = Branch.objects.create(
            name="PG Concurrency HQ",
            latitude=decimal.Decimal("23.810300"),
            longitude=decimal.Decimal("90.412500"),
            radius_meters=200,
        )
        AttendancePolicy.objects.create(
            branch=self.branch,
            geofencing_policy="warn",
            allow_outside_geofence=True,
            photo_required=False,
        )
        self.user = User.objects.create_user(
            email="pg_conc@example.com",
            password="password123",
            role="staff",
        )
        self.master = Employee.objects.create(
            user=self.user,
            employee_number="PG-EMP-01",
            first_name="PG",
            last_name="Worker",
            joined_date=date(2025, 1, 1),
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            master_employee=self.master,
            employee_id="PG-EMP-01",
            full_name="PG Worker",
            phone="01799000001",
            branch=self.branch,
            is_active=True,
            joined_date=date(2025, 1, 1),
        )

    def _run_concurrent(self, callables):
        barrier = threading.Barrier(len(callables), timeout=10)
        results = [None] * len(callables)
        errors = [None] * len(callables)

        def worker(idx, fn):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                results[idx] = fn()
            except Exception as exc:
                errors[idx] = exc
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=worker, args=(i, fn))
            for i, fn in enumerate(callables)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
            if t.is_alive():
                raise TimeoutError("Concurrent worker thread timed out")
        return results, errors

    def test_0_postgresql_backend_and_version(self):
        """Assert PostgreSQL backend is active and log server version."""
        self.assertEqual(connection.vendor, "postgresql")
        with connection.cursor() as cursor:
            cursor.execute("SELECT version();")
            version_str = cursor.fetchone()[0]
        print(f"\n[POSTGRES_VERSION] {version_str}")
        self.assertIn("PostgreSQL", version_str)

    def test_concurrent_identical_checkout_retries_idempotent(self):
        """Two simultaneous retries for one checkout produce one persisted checkout and identical idempotent results."""
        att = Attendance.objects.create(
            employee=self.employee,
            date=timezone.localdate(),
            check_in_time=timezone.now() - timedelta(hours=3),
            attendance_type="check_in",
            type="office",
            status="on_time",
        )
        shared_uuid = uuid.uuid4()
        payload = {
            "latitude": 23.8103,
            "longitude": 90.4125,
            "session_id": att.id,
            "sync_uuid": shared_uuid,
        }

        with patch("apps.attendance.transaction_service.notify_admins"):
            results, errors = self._run_concurrent([
                lambda: AttendanceTransactionService.check_out(self.user, dict(payload), validate_photo=False),
                lambda: AttendanceTransactionService.check_out(self.user, dict(payload), validate_photo=False),
            ])

        self.assertEqual(errors, [None, None])
        self.assertTrue(results[0]["success"] and results[1]["success"])
        self.assertEqual(results[0]["total_hours"], results[1]["total_hours"])
        self.assertEqual(AttendanceLocation.objects.filter(attendance=att, event="check_out").count(), 1)

    def test_concurrent_yesterday_checkout_and_today_checkin(self):
        """Concurrent checkout of yesterday's session and today's new check-in preserve both cleanly."""
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        y_att = Attendance.objects.create(
            employee=self.employee,
            date=yesterday,
            check_in_time=datetime(yesterday.year, yesterday.month, yesterday.day, 22, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
            status="on_time",
        )
        overnight_out = datetime(today.year, today.month, today.day, 2, 0, tzinfo=BD_TZ)

        with patch("apps.attendance.transaction_service.notify_admins"):
            results, errors = self._run_concurrent([
                lambda: AttendanceTransactionService.check_out(
                    self.user,
                    {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "session_id": y_att.id,
                        "client_event_time": overnight_out.isoformat(),
                    },
                    validate_photo=False,
                ),
                lambda: AttendanceTransactionService.check_in(
                    self.user,
                    {"latitude": 23.8103, "longitude": 90.4125, "type": "office"},
                    validate_photo=False,
                ),
            ])

        self.assertEqual(errors, [None, None])
        y_att.refresh_from_db()
        self.assertEqual(y_att.check_out_time, overnight_out)
        self.assertEqual(
            Attendance.objects.filter(
                employee=self.employee, date=today, attendance_type="check_in", check_out_time__isnull=True
            ).count(),
            1,
        )

    def test_concurrent_distinct_uuid_checkouts_single_winner(self):
        """Two distinct-UUID checkouts against one open session allow exactly one winner."""
        att = Attendance.objects.create(
            employee=self.employee,
            date=timezone.localdate(),
            check_in_time=timezone.now() - timedelta(hours=2),
            attendance_type="check_in",
            type="office",
            status="on_time",
        )

        with patch("apps.attendance.transaction_service.notify_admins"):
            results, errors = self._run_concurrent([
                lambda: AttendanceTransactionService.check_out(
                    self.user, {"latitude": 23.8103, "longitude": 90.4125, "sync_uuid": uuid.uuid4()}, validate_photo=False
                ),
                lambda: AttendanceTransactionService.check_out(
                    self.user, {"latitude": 23.8103, "longitude": 90.4125, "sync_uuid": uuid.uuid4()}, validate_photo=False
                ),
            ])

        unexpected = [e for e in errors if e is not None and not isinstance(e, AttendanceTransactionError)]
        self.assertEqual(unexpected, [])
        successes = [r for r in results if r and r.get("success")]
        tx_errors = [e for e in errors if isinstance(e, AttendanceTransactionError)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(tx_errors), 1)
        self.assertEqual(AttendanceLocation.objects.filter(attendance=att, event="check_out").count(), 1)

    def test_concurrent_same_day_checkins_no_duplicate_active_session(self):
        """Two concurrent check-ins with distinct UUIDs create only one active session for the day."""
        with patch("apps.attendance.transaction_service.notify_admins"):
            results, errors = self._run_concurrent([
                lambda: AttendanceTransactionService.check_in(
                    self.user, {"latitude": 23.8103, "longitude": 90.4125, "sync_uuid": uuid.uuid4()}, validate_photo=False
                ),
                lambda: AttendanceTransactionService.check_in(
                    self.user, {"latitude": 23.8103, "longitude": 90.4125, "sync_uuid": uuid.uuid4()}, validate_photo=False
                ),
            ])

        unexpected = [e for e in errors if e is not None and not isinstance(e, AttendanceTransactionError)]
        self.assertEqual(unexpected, [])
        successes = [r for r in results if r and r.get("success")]
        tx_errors = [e for e in errors if isinstance(e, AttendanceTransactionError)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(tx_errors), 1)
        self.assertEqual(
            Attendance.objects.filter(
                employee=self.employee, date=timezone.localdate(), attendance_type="check_in", check_out_time__isnull=True
            ).count(),
            1,
        )
