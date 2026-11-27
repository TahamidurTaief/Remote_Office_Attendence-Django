from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import Action, DataScope, Module, Permission, Role, RolePermission, UserRoleAssignment
from apps.admin_panel.views import DailyReportView, ExportReportCSVView, MonthlyReportView
from apps.attendance.models import Attendance
from apps.attendance.reporting_service import get_daily_report_data, get_monthly_report_data
from apps.attendance.scoping import get_historical_scoped_employee_queryset, get_scoped_employee_queryset
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile, EmployeeStatus, EmploymentHistory

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")
FROZEN_NOW = datetime(2026, 11, 25, 14, 0, 0, tzinfo=BD_TZ)


class HistoricalScopingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch_a = Branch.objects.create(name="Branch A", latitude=23.81, longitude=90.41, radius_meters=200)
        cls.branch_b = Branch.objects.create(name="Branch B", latitude=23.82, longitude=90.42, radius_meters=200)
        for b in (cls.branch_a, cls.branch_b):
            OfficeSchedule.objects.update_or_create(
                branch=b,
                defaults={
                    "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                    "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                    "late_after_minutes": 15,
                    "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
                },
            )
        cls.dept = Department.objects.create(name="Ops")
        cls.desig = Designation.objects.create(name="Staff")
        cls.admin = User.objects.create_superuser(email="hist_adm@example.com", password="pass", role="admin")
        cls.factory = RequestFactory()

        # Branch A viewer with BRANCH-scoped reports.view & reports.export
        cls.branch_a_user = User.objects.create_user(email="mgr_a@example.com", password="pass", role="staff")
        cls.branch_a_master = Employee.objects.create(
            user=cls.branch_a_user, employee_number="MGR-A", first_name="Mgr", last_name="A",
            branch=cls.branch_a, department=cls.dept, designation=cls.desig, joined_date=date(2025, 1, 1),
        )
        cls.branch_a_prof = EmployeeProfile.objects.create(
            user=cls.branch_a_user, master_employee=cls.branch_a_master, employee_id="MGR-A",
            full_name="Mgr A", phone="01700000010", branch=cls.branch_a, is_active=True, joined_date=date(2025, 1, 1),
        )
        cls.no_id_user = User.objects.create_user(email="noid@example.com", password="pass", role="staff")

        mod, _ = Module.objects.get_or_create(code="reports", defaults={"name": "Reports"})
        act_v, _ = Action.objects.get_or_create(code="view", defaults={"name": "View"})
        act_e, _ = Action.objects.get_or_create(code="export", defaults={"name": "Export"})
        perm_v, _ = Permission.objects.get_or_create(codename="reports.view", defaults={"module": mod, "action": act_v, "name": "View Reports"})
        perm_e, _ = Permission.objects.get_or_create(codename="reports.export", defaults={"module": mod, "action": act_e, "name": "Export Reports"})
        role, _ = Role.objects.get_or_create(name="BranchReporter", defaults={"is_active": True})
        RolePermission.objects.get_or_create(role=role, permission=perm_v, defaults={"data_scope": DataScope.BRANCH})
        RolePermission.objects.get_or_create(role=role, permission=perm_e, defaults={"data_scope": DataScope.BRANCH})
        UserRoleAssignment.objects.create(user=cls.branch_a_user, role=role)
        UserRoleAssignment.objects.create(user=cls.no_id_user, role=role)

    def _make_emp(self, code, branch, joined, status=EmployeeStatus.ACTIVE, is_active=True):
        u = User.objects.create_user(email=f"{code.lower()}@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u, employee_number=code, first_name="Emp", last_name=code,
            department=self.dept, designation=self.desig, branch=branch, status=status, joined_date=joined,
        )
        p = EmployeeProfile.objects.create(
            user=u, master_employee=m, employee_id=code, full_name=f"Emp {code}",
            department="Ops", phone=f"014{abs(hash(code)) % 100000000:08d}",
            branch=branch, is_active=is_active, joined_date=joined,
        )
        return m, p

    def test_1_former_employee_left_in_november_visible_in_october_reports_and_exports(self):
        """Employee who left in November (is_active=False) appears in October daily, monthly, and CSV exports."""
        m, p = self._make_emp("HIST-NOV", self.branch_a, date(2026, 1, 1), status=EmployeeStatus.RESIGNED, is_active=False)
        EmploymentHistory.objects.create(
            employee=m, field_changed="status", old_value="Active", new_value="Resigned", effective_date=date(2026, 11, 10)
        )
        oct_day = date(2026, 10, 15)
        Attendance.objects.create(
            employee=p, date=oct_day, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 10, 15, 9, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 10, 15, 18, 0, tzinfo=BD_TZ),
            total_hours=Decimal("9.00"),
        )

        # Active-only directory default remains active-only
        self.assertFalse(get_scoped_employee_queryset(self.admin).filter(pk=p.pk).exists())

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            req_d = self.factory.get(f"/admin-panel/reports/daily/?date={oct_day.isoformat()}")
            req_d.user = self.branch_a_user
            resp_d = DailyReportView.as_view()(req_d)
            self.assertEqual(resp_d.status_code, 200)
            self.assertContains(resp_d, "Emp HIST-NOV")

            req_m = self.factory.get("/admin-panel/reports/monthly/?year=2026&month=10")
            req_m.user = self.branch_a_user
            resp_m = MonthlyReportView.as_view()(req_m)
            self.assertEqual(resp_m.status_code, 200)
            self.assertContains(resp_m, "Emp HIST-NOV")

            req_csv = self.factory.get(f"/admin-panel/reports/export/csv/?date={oct_day.isoformat()}")
            req_csv.user = self.branch_a_user
            resp_csv = ExportReportCSVView.as_view()(req_csv)
            self.assertEqual(resp_csv.status_code, 200)
            self.assertIn("Emp HIST-NOV", resp_csv.content.decode("utf-8-sig"))

    def test_2_branch_a_viewer_cannot_access_former_branch_b_and_missing_identity_fails_closed(self):
        """Branch A viewer cannot see former Branch B employee; missing canonical identity returns empty."""
        m_b, p_b = self._make_emp("HIST-B", self.branch_b, date(2026, 1, 1), status=EmployeeStatus.RESIGNED, is_active=False)
        EmploymentHistory.objects.create(
            employee=m_b, field_changed="status", old_value="Active", new_value="Resigned", effective_date=date(2026, 11, 10)
        )
        oct_day = date(2026, 10, 15)

        scoped_a = get_historical_scoped_employee_queryset(self.branch_a_user, oct_day, oct_day)
        self.assertFalse(scoped_a.filter(pk=p_b.pk).exists())

        scoped_noid = get_historical_scoped_employee_queryset(self.no_id_user, oct_day, oct_day)
        self.assertEqual(scoped_noid.count(), 0)

    def test_3_rejoining_after_employment_gap_respected_in_historical_reports(self):
        """Rejoining employee shows not_employed in gap and present after rejoin."""
        m, p = self._make_emp("HIST-REJ", self.branch_a, date(2026, 9, 1), is_active=True)
        EmploymentHistory.objects.create(employee=m, field_changed="status", old_value="Active", new_value="Resigned", effective_date=date(2026, 10, 10))
        EmploymentHistory.objects.create(employee=m, field_changed="status", old_value="Resigned", new_value="Active", effective_date=date(2026, 10, 20))
        Attendance.objects.create(
            employee=p, date=date(2026, 10, 22), attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 10, 22, 9, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 10, 22, 18, 0, tzinfo=BD_TZ),
            total_hours=Decimal("9.00"),
        )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            gap_rep = get_daily_report_data(report_date=date(2026, 10, 15), employee_id=p.id)
            post_rep = get_daily_report_data(report_date=date(2026, 10, 22), employee_id=p.id)
        self.assertEqual(gap_rep["rows"][0]["status"], "not_employed")
        self.assertEqual(post_rep["rows"][0]["is_present"], True)

    def test_4_inactive_profile_with_valid_attendance_and_incomplete_metadata(self):
        """Inactive resigned profile with no exit history preserves valid historical attendance and flags unresolved."""
        _, p = self._make_emp("HIST-INC", self.branch_a, date(2026, 9, 1), status=EmployeeStatus.RESIGNED, is_active=False)
        d = date(2026, 10, 15)
        Attendance.objects.create(
            employee=p, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 10, 15, 9, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 10, 15, 17, 0, tzinfo=BD_TZ),
            total_hours=Decimal("8.00"),
        )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            qs = get_historical_scoped_employee_queryset(self.branch_a_user, d, d)
            rep = get_daily_report_data(report_date=d, employee_queryset=qs)

        row = next(r for r in rep["rows"] if r["employee"].id == p.id)
        self.assertTrue(row["is_present"])
        self.assertTrue(row["eligibility_unresolved"])

    def test_5_bounded_query_count_with_bulk_historical_scoping(self):
        """Historical scoping + monthly report query count remains bounded across multiple former employees."""
        for i in range(15):
            m, _ = self._make_emp(f"HIST-Q{i}", self.branch_a, date(2026, 1, 1), status=EmployeeStatus.RESIGNED, is_active=False)
            EmploymentHistory.objects.create(
                employee=m, field_changed="status", old_value="Active", new_value="Resigned", effective_date=date(2026, 11, 10)
            )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), CaptureQueriesContext(connection) as ctx:
            qs = get_historical_scoped_employee_queryset(self.branch_a_user, date(2026, 10, 1), date(2026, 10, 31))
            get_monthly_report_data(2026, 10, employee_queryset=qs)

        self.assertLessEqual(len(ctx), 15)
