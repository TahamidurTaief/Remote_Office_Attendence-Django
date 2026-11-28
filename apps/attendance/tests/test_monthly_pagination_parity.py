import decimal
import time as time_mod
from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.attendance.models import (
    Attendance,
    AttendanceCorrectionRequest,
    AttendanceCoverageRecord,
    AttendanceLocation,
    AttendancePolicy,
)
from apps.attendance.reporting_service import get_monthly_report_data
from apps.branches.models import Branch, Holiday, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")
FROZEN_NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=BD_TZ)


class MonthlyPaginationParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch_a = Branch.objects.create(
            name="Parity Branch A",
            latitude=decimal.Decimal("23.810300"),
            longitude=decimal.Decimal("90.412500"),
            radius_meters=200,
        )
        cls.branch_b = Branch.objects.create(
            name="Parity Branch B",
            latitude=decimal.Decimal("23.820300"),
            longitude=decimal.Decimal("90.422500"),
            radius_meters=200,
        )
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch_a,
            defaults={
                "office_start_time": time(9, 0),
                "office_end_time": time(18, 0),
                "weekly_holidays": "5,6",
            },
        )
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch_b,
            defaults={
                "office_start_time": time(9, 0),
                "office_end_time": time(18, 0),
                "weekly_holidays": "5,6",
            },
        )
        AttendancePolicy.objects.create(branch=cls.branch_a, gps_required="optional", photo_required=False)
        AttendancePolicy.objects.create(branch=cls.branch_b, gps_required="optional", photo_required=False)

    def _create_auto_closed_employees(self, count, branch, prefix="AC"):
        emps = []
        d1 = date(2026, 10, 5)  # Monday
        d2 = date(2026, 10, 6)  # Tuesday
        atts_to_create = []
        for idx in range(count):
            u = User.objects.create(
                email=f"{prefix.lower()}_{idx}@example.com",
                password="x",
                role="staff",
            )
            m = Employee.objects.create(
                user=u,
                employee_number=f"EMP-{prefix}-{idx:03d}",
                first_name=prefix,
                last_name=f"{idx:03d}",
                joined_date=date(2026, 10, 1),
            )
            p = EmployeeProfile.objects.create(
                user=u,
                master_employee=m,
                employee_id=f"EMP-{prefix}-{idx:03d}",
                full_name=f"{prefix} {idx:03d}",
                phone=f"017{sum(ord(c) for c in prefix) % 90 + 10}{idx:06d}",
                branch=branch,
                is_active=True,
                joined_date=date(2026, 10, 1),
            )
            emps.append(p)
            atts_to_create.append(
                Attendance(
                    employee=p,
                    date=d1,
                    check_in_time=datetime(2026, 10, 5, 9, 0, 0, tzinfo=BD_TZ),
                    check_out_time=datetime(2026, 10, 5, 18, 0, 0, tzinfo=BD_TZ),
                    attendance_type="check_in",
                    type="office",
                    note="Auto-closed by system",
                )
            )
            atts_to_create.append(
                Attendance(
                    employee=p,
                    date=d2,
                    check_in_time=datetime(2026, 10, 6, 9, 0, 0, tzinfo=BD_TZ),
                    check_out_time=datetime(2026, 10, 6, 18, 0, 0, tzinfo=BD_TZ),
                    attendance_type="check_in",
                    type="office",
                    note="Auto-closed by system",
                )
            )
        created_atts = Attendance.objects.bulk_create(atts_to_create)
        locs = [
            AttendanceLocation(
                attendance=a,
                event="check_out",
                latitude=decimal.Decimal("23.810300"),
                longitude=decimal.Decimal("90.412500"),
                address="HQ",
                accuracy=15.0,
                timestamp=a.check_out_time,
            )
            for a in created_atts
        ]
        AttendanceLocation.objects.bulk_create(locs)
        return emps

    def test_1_query_count_invariant_between_2_and_20_auto_closed_employees(self):
        """Paginated monthly report query count stays O(1) bounded for 2 vs 20 auto-closed employees."""
        emps_2 = self._create_auto_closed_employees(2, self.branch_a, prefix="TWO")
        qs_2 = EmployeeProfile.objects.filter(id__in=[e.id for e in emps_2])

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            with CaptureQueriesContext(connection) as ctx_2:
                get_monthly_report_data(year=2026, month=10, employee_queryset=qs_2, page=1, page_size=10)

        emps_20 = self._create_auto_closed_employees(20, self.branch_a, prefix="TWENTY")
        qs_20 = EmployeeProfile.objects.filter(id__in=[e.id for e in emps_20])

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            with CaptureQueriesContext(connection) as ctx_20:
                get_monthly_report_data(year=2026, month=10, employee_queryset=qs_20, page=1, page_size=10)

        loc_queries_20 = [q["sql"] for q in ctx_20 if "attendance_attendancelocation" in q["sql"].lower()]
        self.assertEqual(len(loc_queries_20), 1)
        self.assertEqual(len(ctx_2), len(ctx_20))
        print(f"\n[PARITY BENCH] 2 auto-closed emps: {len(ctx_2)} queries | 20 auto-closed emps: {len(ctx_20)} queries")

    def test_2_page1_page2_and_export_kpi_and_provenance_parity(self):
        """Page 1, Page 2, and full export (page=None) have identical full-scope KPIs and deterministic rows."""
        emps = self._create_auto_closed_employees(6, self.branch_a, prefix="PAG")
        qs = EmployeeProfile.objects.filter(id__in=[e.id for e in emps])

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            p1 = get_monthly_report_data(year=2026, month=10, employee_queryset=qs, page=1, page_size=3)
            p2 = get_monthly_report_data(year=2026, month=10, employee_queryset=qs, page=2, page_size=3)
            exp = get_monthly_report_data(year=2026, month=10, employee_queryset=qs, page=None)

        p1_ids = {r["employee"].id for r in p1["rows"]}
        p2_ids = {r["employee"].id for r in p2["rows"]}
        exp_ids = {r["employee"].id for r in exp["rows"]}
        self.assertEqual(len(p1_ids), 3)
        self.assertEqual(len(p2_ids), 3)
        self.assertTrue(p1_ids.isdisjoint(p2_ids))
        self.assertEqual(p1_ids | p2_ids, exp_ids)

        self.assertEqual(p1["employee_stats"], p2["employee_stats"])
        self.assertEqual(p1["employee_stats"], exp["employee_stats"])
        self.assertEqual(len(p1["employee_stats"]), 6)

    def test_3_latency_comparison_50_vs_500_auto_closed_sessions(self):
        """Compare 50 vs 500 auto-closed sessions latency under paginated monthly report."""
        emps_25 = self._create_auto_closed_employees(25, self.branch_a, prefix="L50")  # 50 auto-closed sessions
        qs_50 = EmployeeProfile.objects.filter(id__in=[e.id for e in emps_25])
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            t0 = time_mod.perf_counter()
            get_monthly_report_data(year=2026, month=10, employee_queryset=qs_50, page=1, page_size=25)
            ms_50 = (time_mod.perf_counter() - t0) * 1000.0

        emps_250 = self._create_auto_closed_employees(250, self.branch_b, prefix="L500")  # 500 auto-closed sessions
        qs_500 = EmployeeProfile.objects.filter(id__in=[e.id for e in emps_250])
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            t1 = time_mod.perf_counter()
            with CaptureQueriesContext(connection) as ctx_500:
                get_monthly_report_data(year=2026, month=10, employee_queryset=qs_500, page=1, page_size=25)
            ms_500 = (time_mod.perf_counter() - t1) * 1000.0

        self.assertLessEqual(len(ctx_500), 20)
        print(f"\n[PARITY BENCH] 50 sessions: {ms_50:.1f}ms | 500 sessions: {ms_500:.1f}ms ({len(ctx_500)} queries)")

    def test_4_former_employee_holiday_work_and_branch_scope_parity(self):
        """Former employee October attendance is visible to authorized branch viewer, excluded from other branch; holiday work separated."""
        u = User.objects.create_user(email="former_oct@example.com", password="x", role="staff")
        m = Employee.objects.create(
            user=u,
            employee_number="EMP-FORMER-OCT",
            first_name="Former",
            last_name="Oct",
            joined_date=date(2026, 10, 1),
        )
        p = EmployeeProfile.objects.create(
            user=u,
            master_employee=m,
            employee_id="EMP-FORMER-OCT",
            full_name="Former Oct",
            phone="01711111111",
            branch=self.branch_a,
            is_active=False,
            joined_date=date(2026, 10, 1),
        )

        # Holiday work on 2026-10-09 (Friday)
        Holiday.objects.create(name="Oct Holiday", date=date(2026, 10, 9), branch=self.branch_a)
        Attendance.objects.create(
            employee=p,
            date=date(2026, 10, 9),
            check_in_time=datetime(2026, 10, 9, 10, 0, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 10, 9, 14, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
            status="holiday_attendance",
        )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            res_a = get_monthly_report_data(year=2026, month=10, branch_id=self.branch_a.id, page=1, page_size=25)
            res_b = get_monthly_report_data(year=2026, month=10, branch_id=self.branch_b.id, page=1, page_size=25)

        a_ids = [r["employee"].id for r in res_a["rows"]]
        b_ids = [r["employee"].id for r in res_b["rows"]]
        self.assertIn(p.id, a_ids)
        self.assertNotIn(p.id, b_ids)
        self.assertEqual(res_a["employee_stats"][p.id]["holiday_work_count"], 1)
