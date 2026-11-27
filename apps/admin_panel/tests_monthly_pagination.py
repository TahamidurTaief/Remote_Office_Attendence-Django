import time
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.attendance.models import Attendance
from apps.attendance.reporting_service import get_monthly_report_data
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")
FROZEN_NOW = datetime(2026, 11, 30, 14, 0, 0, tzinfo=BD_TZ)


class MonthlyPaginationParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="Pag HQ", latitude=23.81, longitude=90.41, radius_meters=200)
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
                "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
            },
        )
        cls.dept = Department.objects.create(name="Ops")
        cls.desig = Designation.objects.create(name="Staff")
        cls.admin = User.objects.create_superuser(email="pag_adm@example.com", password="pass", role="admin")

    def _bulk_create_employees(self, count, prefix):
        users = [User(email=f"{prefix}_{i}@example.com", password="x", role="staff") for i in range(count)]
        User.objects.bulk_create(users)
        masters = [
            Employee(
                user=u, employee_number=f"{prefix}-{i}", first_name="E", last_name=f"{i:03d}",
                department=self.dept, designation=self.desig, branch=self.branch, joined_date=date(2026, 1, 1),
            )
            for i, u in enumerate(users)
        ]
        Employee.objects.bulk_create(masters)
        profiles = [
            EmployeeProfile(
                user=m.user, master_employee=m, employee_id=m.employee_number,
                full_name=f"Emp {prefix} {i:03d}", department="Ops",
                phone=f"011{abs(hash(m.employee_number)) % 100000000:08d}",
                branch=self.branch, is_active=True, joined_date=date(2026, 1, 1),
            )
            for i, m in enumerate(masters)
        ]
        EmployeeProfile.objects.bulk_create(profiles)
        return profiles

    def test_1_page_1_and_page_2_different_rows_identical_full_scope_kpis(self):
        """Page 1 and page 2 materialize only their page rows while preserving identical full-scope KPIs."""
        profs = self._bulk_create_employees(45, "P12")
        for p in profs[:10]:
            Attendance.objects.create(
                employee=p, date=date(2026, 11, 24), attendance_type="check_in", type="office", status="late",
                check_in_time=datetime(2026, 11, 24, 9, 30, tzinfo=BD_TZ),
                check_out_time=datetime(2026, 11, 24, 18, 0, tzinfo=BD_TZ),
                total_hours=Decimal("8.50"),
            )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            p1 = get_monthly_report_data(2026, 11, page=1, page_size=20)
            p2 = get_monthly_report_data(2026, 11, page=2, page_size=20)
            full = get_monthly_report_data(2026, 11, page=None)

        self.assertEqual(p1["materialized_row_count"], 20)
        self.assertEqual(p2["materialized_row_count"], 20)
        self.assertEqual(full["materialized_row_count"], 45)
        self.assertNotEqual(
            [r["employee"].id for r in p1["rows"]],
            [r["employee"].id for r in p2["rows"]],
        )
        for k in ("total_present", "total_late", "total_absent", "total_on_leave", "total_overtime_minutes", "total_count"):
            self.assertEqual(p1[k], full[k])
            self.assertEqual(p2[k], full[k])

    def test_2_out_of_range_page_clamps_deterministically_without_losing_kpis(self):
        """Out-of-range page returns last valid page via Django Paginator and preserves full KPIs."""
        self._bulk_create_employees(12, "POOR")
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            p99 = get_monthly_report_data(2026, 11, page=99, page_size=10)
            full = get_monthly_report_data(2026, 11, page=None)
        self.assertEqual(p99["materialized_row_count"], 2)
        self.assertEqual(p99["total_count"], 12)
        self.assertEqual(p99["total_present"], full["total_present"])

    def test_3_query_count_and_latency_50_vs_500_employees(self):
        """Query count stays bounded & constant between 50 and 500 employees on page=1."""
        self._bulk_create_employees(50, "Q50")
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), CaptureQueriesContext(connection) as ctx50:
            t0 = time.perf_counter()
            res50 = get_monthly_report_data(2026, 11, page=1, page_size=30)
            ms50 = (time.perf_counter() - t0) * 1000.0

        self._bulk_create_employees(450, "Q500")
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), CaptureQueriesContext(connection) as ctx500:
            t1 = time.perf_counter()
            res500 = get_monthly_report_data(2026, 11, page=1, page_size=30)
            ms500 = (time.perf_counter() - t1) * 1000.0

        self.assertEqual(res50["materialized_row_count"], 30)
        self.assertEqual(res500["materialized_row_count"], 30)
        self.assertEqual(res500["total_count"], 500)
        self.assertLessEqual(len(ctx50), 20)
        self.assertEqual(len(ctx500), len(ctx50))
        print(f"\n[PAGINATION BENCHMARK] 50-emp: {len(ctx50)}q {ms50:.1f}ms | 500-emp: {len(ctx500)}q {ms500:.1f}ms")
