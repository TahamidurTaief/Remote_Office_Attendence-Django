from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.engine import PermissionEngine
from apps.accounts.rbac_models import (
    Action,
    DataScope,
    Module,
    Permission,
    Role,
    RolePermission,
    UserRoleAssignment,
)
from apps.admin_panel.analytics_service import get_attendance_analytics
from apps.admin_panel.dashboard_services import get_hr_dashboard_data
from apps.attendance.models import Attendance
from apps.branches.models import Branch, Holiday, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile, EmployeeStatus
from apps.leave.models import LeaveRequest, LeaveType

User = get_user_model()


class HRDashboardCanonicalAgreementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch_a = Branch.objects.create(name="HR Dash Branch A", latitude=23.81, longitude=90.41, radius_meters=200)
        cls.branch_b = Branch.objects.create(name="HR Dash Branch B", latitude=23.82, longitude=90.42, radius_meters=200)
        cls.today = timezone.localdate()
        all_days = ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday"]
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch_a,
            defaults={"working_days": all_days, "office_start_time": time(9, 0), "office_end_time": time(9, 1)},
        )
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch_b,
            defaults={"working_days": all_days, "office_start_time": time(9, 0), "office_end_time": time(9, 1)},
        )
        cls.dept = Department.objects.create(name="HR Dash Dept", is_global=True)
        cls.desig = Designation.objects.create(name="Specialist")
        cls.admin = User.objects.create_superuser(email="hr_dash_adm@example.com", password="pass", role="admin")

        mod_rep, _ = Module.objects.get_or_create(code="reports", defaults={"name": "Reports"})
        mod_lv, _ = Module.objects.get_or_create(code="leave", defaults={"name": "Leave"})
        act_view, _ = Action.objects.get_or_create(code="view", defaults={"name": "View"})
        perm_rep, _ = Permission.objects.get_or_create(
            codename="reports.view", defaults={"module": mod_rep, "action": act_view, "name": "View Reports"}
        )
        perm_lv, _ = Permission.objects.get_or_create(
            codename="leave.view", defaults={"module": mod_lv, "action": act_view, "name": "View Leave"}
        )

        cls.branch_a_user = User.objects.create_user(email="hr_dash_ba@example.com", password="pass", role="hr")
        cls.emp_mgr_a = Employee.objects.create(
            user=cls.branch_a_user, employee_number="HRD-MGR", first_name="Mgr", last_name="A",
            phone="01780000001", branch=cls.branch_a, department=cls.dept, designation=cls.desig,
            joined_date=cls.today - timedelta(days=30), status=EmployeeStatus.ACTIVE,
            weekly_holiday_policy="none",
        )
        cls.prof_mgr_a = EmployeeProfile.objects.create(
            user=cls.branch_a_user, master_employee=cls.emp_mgr_a, employee_id="HRD-MGR", full_name="Mgr A",
            phone="01780000001", branch=cls.branch_a, is_active=True, joined_date=cls.today - timedelta(days=30),
        )
        role_ba = Role.objects.create(code="hr_dash_branch_a", name="HR Dash Branch A")
        RolePermission.objects.create(role=role_ba, permission=perm_rep, data_scope=DataScope.BRANCH)
        RolePermission.objects.create(role=role_ba, permission=perm_lv, data_scope=DataScope.BRANCH)
        UserRoleAssignment.objects.create(user=cls.branch_a_user, role=role_ba)
        PermissionEngine.invalidate_user_cache(cls.branch_a_user)

    def _make_emp(self, code, branch, joined_date=None, status=EmployeeStatus.ACTIVE):
        j_date = joined_date if joined_date is not None else (self.today - timedelta(days=30))
        u = User.objects.create_user(email=f"{code.lower()}@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u, employee_number=code, first_name="E", last_name=code,
            phone=f"0178100{User.objects.count():04d}", branch=branch, department=self.dept,
            designation=self.desig, joined_date=j_date, status=status, weekly_holiday_policy="none",
        )
        p = EmployeeProfile.objects.create(
            user=u, master_employee=m, employee_id=code, full_name=f"E {code}",
            phone=m.phone, branch=branch, is_active=True, joined_date=j_date,
        )
        return m, p

    def test_1_field_only_employee_counted_present_and_agrees_with_analytics(self):
        # Give mgr_a a field visit so both employees in branch_a are field-only
        Attendance.objects.create(
            employee=self.prof_mgr_a, date=self.today, attendance_type="field_visit",
            check_in_time=timezone.now(), is_expired=False,
        )
        today_params = {"range": "custom", "start_date": self.today.isoformat(), "end_date": self.today.isoformat()}
        hr_data = get_hr_dashboard_data(self.branch_a_user)
        canon = get_attendance_analytics(self.branch_a_user, today_params)["analytics_summary"]

        self.assertEqual(hr_data["hr_present_count"], 1)
        self.assertEqual(hr_data["today_attendance_count"], 1)
        self.assertEqual(hr_data["hr_absent_count"], 0)
        self.assertEqual(hr_data["hr_present_count"], canon["total_present"])
        self.assertEqual(hr_data["hr_absent_count"], canon["total_absent"])

    def test_2_before_shift_grace_completion_is_pending_not_absent(self):
        OfficeSchedule.objects.filter(branch=self.branch_a).update(office_end_time=time(23, 59))
        today_params = {"range": "custom", "start_date": self.today.isoformat(), "end_date": self.today.isoformat()}
        hr_data = get_hr_dashboard_data(self.branch_a_user)
        canon = get_attendance_analytics(self.branch_a_user, today_params)["analytics_summary"]

        self.assertEqual(hr_data["hr_pending_count"], 1)
        self.assertEqual(hr_data["hr_absent_count"], 0)
        self.assertEqual(hr_data["hr_pending_count"], canon["total_pending"])
        self.assertEqual(hr_data["hr_absent_count"], canon["total_absent"])

    def test_3_multiple_office_sessions_plus_approved_leave_counted_once(self):
        lt = LeaveType.objects.create(name="Annual HRD", category="annual", default_days_per_year=10)
        LeaveRequest.objects.create(
            employee=self.prof_mgr_a, leave_type=lt, start_date=self.today, end_date=self.today,
            reason="Conflict", status="approved",
        )
        now = timezone.now()
        Attendance.objects.create(
            employee=self.prof_mgr_a, date=self.today, attendance_type="check_in",
            status="on_time", check_in_time=now - timedelta(hours=3), check_out_time=now - timedelta(hours=2),
            is_expired=False,
        )
        Attendance.objects.create(
            employee=self.prof_mgr_a, date=self.today, attendance_type="check_in",
            status="on_time", check_in_time=now - timedelta(hours=1), is_expired=False,
        )

        today_params = {"range": "custom", "start_date": self.today.isoformat(), "end_date": self.today.isoformat()}
        hr_data = get_hr_dashboard_data(self.branch_a_user)
        canon = get_attendance_analytics(self.branch_a_user, today_params)["analytics_summary"]

        self.assertEqual(hr_data["hr_present_count"], 1)
        self.assertEqual(hr_data["hr_missing_checkout_count"], 0)  # today open session is not missing checkout
        self.assertEqual(hr_data["hr_leave_conflict_count"], 1)
        self.assertEqual(hr_data["hr_leave_count"], 0)
        self.assertEqual(hr_data["hr_present_count"], canon["total_present"])
        self.assertEqual(hr_data["hr_leave_conflict_count"], canon["total_leave_conflicts"])

    def test_4_holiday_pre_joining_and_unresolved_employment_never_absent(self):
        # Mgr A on weekly holiday
        self.emp_mgr_a.weekly_holiday_policy = self.today.strftime("%A").lower()
        self.emp_mgr_a.save(update_fields=["weekly_holiday_policy"])
        # Future joiner
        self._make_emp("HRD-FUT", self.branch_a, joined_date=self.today + timedelta(days=5))
        # Unresolved employment (resigned master without exit history, profile still active)
        m_unres, p_unres = self._make_emp("HRD-UNR", self.branch_a)
        m_unres.status = EmployeeStatus.RESIGNED
        m_unres.save(update_fields=["status"])
        EmployeeProfile.objects.filter(pk=p_unres.pk).update(is_active=True)

        today_params = {"range": "custom", "start_date": self.today.isoformat(), "end_date": self.today.isoformat()}
        hr_data = get_hr_dashboard_data(self.branch_a_user)
        canon = get_attendance_analytics(self.branch_a_user, today_params)["analytics_summary"]

        self.assertEqual(hr_data["hr_absent_count"], 0)
        self.assertEqual(hr_data["hr_review_needed_count"], 1)
        self.assertEqual(hr_data["hr_absent_count"], canon["total_absent"])

    def test_5_restricted_viewer_does_not_see_global_totals_and_queries_stay_bounded(self):
        lt = LeaveType.objects.create(name="Scoped Leave", category="casual", default_days_per_year=10)
        _, prof_b = self._make_emp("HRD-B1", self.branch_b)
        LeaveRequest.objects.create(
            employee=prof_b, leave_type=lt, start_date=self.today, end_date=self.today,
            reason="B leave", status="approved",
        )
        Attendance.objects.create(
            employee=prof_b, date=self.today, attendance_type="check_in",
            status="on_time", check_in_time=timezone.now(), is_expired=False,
        )

        with CaptureQueriesContext(connection) as ctx_before:
            hr_data = get_hr_dashboard_data(self.branch_a_user)

        self.assertEqual(hr_data["total_employees"], 1)
        self.assertEqual(hr_data["hr_present_count"], 0)
        self.assertEqual(hr_data["hr_leave_count"], 0)
        self.assertEqual(hr_data["on_leave_today"], [])

        # Adding 5 out-of-scope Branch B employees must not grow query count
        for i in range(5):
            self._make_emp(f"HRD-BX{i}", self.branch_b)

        with CaptureQueriesContext(connection) as ctx_after:
            get_hr_dashboard_data(self.branch_a_user)
        self.assertLessEqual(len(ctx_after), len(ctx_before))
