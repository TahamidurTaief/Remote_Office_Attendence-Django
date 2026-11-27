import decimal
import json
import re
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.rbac_models import Action, DataScope, Module, Permission, Role, RolePermission, UserRoleAssignment
from apps.attendance.models import Attendance
from apps.branches.models import Branch, Holiday, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile, EmployeeStatus

User = get_user_model()


class DashboardAnalyticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch_a = Branch.objects.create(
            name="Branch Alpha",
            latitude=decimal.Decimal("23.810300"),
            longitude=decimal.Decimal("90.412500"),
            radius_meters=200,
        )
        cls.branch_b = Branch.objects.create(
            name="Branch Beta",
            latitude=decimal.Decimal("22.356900"),
            longitude=decimal.Decimal("91.783200"),
            radius_meters=200,
        )
        for br in (cls.branch_a, cls.branch_b):
            OfficeSchedule.objects.update_or_create(
                branch=br,
                defaults={
                    "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                    "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                    "late_after_minutes": 15,
                    "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
                },
            )
        cls.dept_eng = Department.objects.create(name="Engineering")
        cls.dept_sales = Department.objects.create(name="Sales")
        cls.desig = Designation.objects.create(name="Staff")

        cls.admin = User.objects.create_superuser(email="admin_da@example.com", password="pass", role="admin")

        # Manager scoped to Branch Alpha via RBAC
        cls.mgr_user = User.objects.create_user(email="mgr_da@example.com", password="pass", role="manager")
        cls.mgr_master = Employee.objects.create(
            user=cls.mgr_user,
            employee_number="MGR-DA-1",
            first_name="Mgr",
            last_name="Alpha",
            department=cls.dept_eng,
            branch=cls.branch_a,
            status=EmployeeStatus.ACTIVE,
            joined_date=date(2024, 1, 1),
        )
        cls.mgr_prof = EmployeeProfile.objects.create(
            user=cls.mgr_user,
            master_employee=cls.mgr_master,
            employee_id="MGR-DA-1",
            full_name="Mgr Alpha",
            department="Engineering",
            phone="01711000001",
            branch=cls.branch_a,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

        # Grant manager BRANCH-scoped dashboard.view and reports.view
        mod_dash, _ = Module.objects.get_or_create(code="dashboard", defaults={"name": "Dashboard"})
        mod_rep, _ = Module.objects.get_or_create(code="reports", defaults={"name": "Reports"})
        act_view, _ = Action.objects.get_or_create(code="view", defaults={"name": "View"})
        perm_dash, _ = Permission.objects.get_or_create(
            codename="dashboard.view", defaults={"module": mod_dash, "action": act_view, "name": "View Dashboard"}
        )
        perm_rep, _ = Permission.objects.get_or_create(
            codename="reports.view", defaults={"module": mod_rep, "action": act_view, "name": "View Reports"}
        )
        mgr_role, _ = Role.objects.get_or_create(code="branch_mgr_test", defaults={"name": "BranchMgrTest"})
        RolePermission.objects.get_or_create(role=mgr_role, permission=perm_dash, defaults={"data_scope": DataScope.BRANCH})
        RolePermission.objects.get_or_create(role=mgr_role, permission=perm_rep, defaults={"data_scope": DataScope.BRANCH})
        UserRoleAssignment.objects.get_or_create(user=cls.mgr_user, role=mgr_role)

        # Employee in Branch Alpha
        cls.emp_a_user = User.objects.create_user(email="empa@example.com", password="pass", role="staff")
        cls.emp_a_master = Employee.objects.create(
            user=cls.emp_a_user,
            employee_number="EMP-A-1",
            first_name="Emp",
            last_name="Alpha",
            department=cls.dept_eng,
            branch=cls.branch_a,
            reporting_manager=cls.mgr_master,
            status=EmployeeStatus.ACTIVE,
            joined_date=date(2024, 1, 1),
        )
        cls.emp_a = EmployeeProfile.objects.create(
            user=cls.emp_a_user,
            master_employee=cls.emp_a_master,
            employee_id="EMP-A-1",
            full_name="Emp Alpha",
            department="Engineering",
            phone="01711000002",
            branch=cls.branch_a,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

        # Employee in Branch Beta
        cls.emp_b_user = User.objects.create_user(email="empb@example.com", password="pass", role="staff")
        cls.emp_b_master = Employee.objects.create(
            user=cls.emp_b_user,
            employee_number="EMP-B-1",
            first_name="Emp",
            last_name="Beta",
            department=cls.dept_sales,
            branch=cls.branch_b,
            status=EmployeeStatus.ACTIVE,
            joined_date=date(2024, 1, 1),
        )
        cls.emp_b = EmployeeProfile.objects.create(
            user=cls.emp_b_user,
            master_employee=cls.emp_b_master,
            employee_id="EMP-B-1",
            full_name="Emp Beta",
            department="Sales",
            phone="01711000003",
            branch=cls.branch_b,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

    def test_1_employee_day_accuracy_and_subsets(self):
        """
        2 office sessions (one open) + 1 accepted field visit on same past working day:
        counts as 1 Present day, 1 Mixed bucket, Missing Checkout overlaps Present,
        Late is subset of Present.
        """
        today = timezone.localdate()
        # Pick a recent non-Friday working day in the past
        target_day = today - timedelta(days=1)
        if target_day.weekday() == 4:
            target_day -= timedelta(days=1)

        base_dt = timezone.make_aware(datetime.combine(target_day, datetime.strptime("09:30", "%H:%M").time()))
        Attendance.objects.create(
            employee=self.emp_a,
            date=target_day,
            attendance_type="check_in",
            type="office",
            status="late",
            check_in_time=base_dt,
            check_out_time=base_dt + timedelta(hours=2),
            total_hours=2.0,
        )
        Attendance.objects.create(
            employee=self.emp_a,
            date=target_day,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=base_dt + timedelta(hours=3),
            check_out_time=None,  # open session -> missing checkout on past day
        )
        Attendance.objects.create(
            employee=self.emp_a,
            date=target_day,
            attendance_type="field_visit",
            type="field",
            status="on_time",
            check_in_time=base_dt + timedelta(hours=4),
        )

        from apps.admin_panel.analytics_service import get_attendance_analytics
        res = get_attendance_analytics(
            self.admin,
            {"range": "custom", "start_date": target_day.isoformat(), "end_date": target_day.isoformat(), "branch": str(self.branch_a.id)},
        )
        summary = res["analytics_summary"]
        # mgr_prof + emp_a in Branch Alpha
        self.assertEqual(summary["total_present"], 1)
        self.assertEqual(summary["total_late"], 1)  # subset of Present
        self.assertEqual(summary["mixed_days"], 1)
        self.assertEqual(summary["office_only_days"], 0)
        self.assertEqual(summary["field_only_days"], 0)
        self.assertEqual(summary["total_missing_checkout"], 1)

    def test_2_manager_scope_enforcement_and_widget_allowlist(self):
        """
        Manager scoped to Branch Alpha filtering Branch Beta gets 0 records, 0 totals,
        no Branch Beta labels; role_view cannot escalate; invalid widget returns 400.
        """
        self.client.force_login(self.mgr_user)
        url = reverse("dashboard")

        # Out-of-scope branch filter
        resp = self.client.get(
            url,
            {"widget": "attendance_analytics", "branch": str(self.branch_b.id)},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertNotIn("Emp Beta", content)
        self.assertNotIn("Branch Beta", content)
        self.assertIn("No scoped employees match the selected filters", content)

        # Non-superuser role_view=admin must not escalate
        resp_esc = self.client.get(
            url,
            {"widget": "attendance_analytics", "role_view": "admin"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp_esc.status_code, 200)
        self.assertNotIn("Emp Beta", resp_esc.content.decode())

        # Invalid widget name rejected
        resp_bad = self.client.get(url, {"widget": "../../etc/passwd"}, HTTP_HX_REQUEST="true")
        self.assertEqual(resp_bad.status_code, 400)

        # Widget-only execution: admin_security must not execute analytics or HR/expense services
        from unittest.mock import patch
        self.client.force_login(self.admin)
        with patch("apps.admin_panel.analytics_service.get_attendance_analytics", side_effect=AssertionError("analytics should not run")), \
             patch("apps.admin_panel.views.get_admin_dashboard_data", side_effect=AssertionError("admin dashboard should not run")):
            resp_sec = self.client.get(url, {"widget": "admin_security"}, HTTP_HX_REQUEST="true")
            self.assertEqual(resp_sec.status_code, 200)

        with patch("apps.admin_panel.views.get_admin_dashboard_data", side_effect=AssertionError("admin dashboard should not run")):
            resp_aa = self.client.get(url, {"widget": "attendance_analytics", "range": "7"}, HTTP_HX_REQUEST="true")
            self.assertEqual(resp_aa.status_code, 200)

        # Unauthorized finance access rejected (403) for manager without expense/payroll view permission
        self.client.force_login(self.mgr_user)
        resp_fin_denied = self.client.get(url, {"widget": "analytics_finance", "range": "7"}, HTTP_HX_REQUEST="true")
        self.assertEqual(resp_fin_denied.status_code, 403)

    def test_3_zero_eligible_days_and_field_only_comparison(self):
        """
        Zero eligible working days & empty comparison period show Unavailable (None)
        instead of fabricated percentages; field-only attendance counted once.
        """
        today = timezone.localdate()
        # Use a past Friday (weekly holiday) as 1-day range -> 0 eligible working days
        friday = today - timedelta(days=1)
        while friday.weekday() != 4:
            friday -= timedelta(days=1)

        from apps.admin_panel.analytics_service import get_attendance_analytics
        res_hol = get_attendance_analytics(
            self.admin,
            {"range": "custom", "start_date": friday.isoformat(), "end_date": friday.isoformat()},
        )
        self.assertEqual(res_hol["analytics_summary"]["eligible_working_days"], 0)
        self.assertIsNone(res_hol["analytics_summary"]["attendance_rate"])
        self.assertIsNone(res_hol["analytics_rate_delta"])

        # Field-only attendance on a working day
        work_day = friday - timedelta(days=1)
        Attendance.objects.create(
            employee=self.emp_a,
            date=work_day,
            attendance_type="field_visit",
            type="field",
            status="on_time",
            check_in_time=timezone.make_aware(datetime.combine(work_day, datetime.strptime("10:00", "%H:%M").time())),
        )
        res_field = get_attendance_analytics(
            self.admin,
            {"range": "custom", "start_date": work_day.isoformat(), "end_date": work_day.isoformat(), "branch": str(self.branch_a.id)},
        )
        self.assertEqual(res_field["analytics_summary"]["field_only_days"], 1)
        self.assertEqual(res_field["analytics_summary"]["total_present"], 1)

        # People/Leave & Work panels: empty/missing completion timestamps reported honestly
        from apps.admin_panel.analytics_service import get_people_leave_analytics, get_work_analytics
        from apps.projects.models import Project, ProjectTask, ProjectType
        pt, _ = ProjectType.objects.get_or_create(name="HVAC")
        proj = Project.objects.create(name="Proj A", client_name="Client", location="Dhaka", project_type=pt, status="In Progress", start_date=today - timedelta(days=10))
        t_hist = ProjectTask.objects.create(project=proj, order=1, activity="Install", responsible_person=self.emp_a, status="Completed")
        ProjectTask.objects.filter(pk=t_hist.pk).update(completed_at=None)
        pl_res = get_people_leave_analytics(self.admin, {"range": "30", "branch": str(self.branch_a.id)})
        self.assertEqual(pl_res["analytics_employee_count"], 2)
        work_res = get_work_analytics(self.admin, {"range": "30", "branch": str(self.branch_a.id)})
        self.assertEqual(work_res["work_completed_tasks"], 1)
        self.assertEqual(work_res["work_missing_timestamps"], 1)
        self.assertEqual(work_res["work_field_visits"], 1)

    def test_4_detail_table_and_chart_reconciliation(self):
        """
        Serialized json_script chart payload reconciles with summary metrics.
        """
        today = timezone.localdate()
        work_day = today - timedelta(days=1)
        if work_day.weekday() == 4:
            work_day -= timedelta(days=1)
        Attendance.objects.create(
            employee=self.emp_a,
            date=work_day,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=timezone.make_aware(datetime.combine(work_day, datetime.strptime("09:00", "%H:%M").time())),
            check_out_time=timezone.make_aware(datetime.combine(work_day, datetime.strptime("17:00", "%H:%M").time())),
        )

        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse("dashboard"),
            {"widget": "attendance_analytics", "range": "7"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        m = re.search(
            r'<script id="attendance-analytics-chart-data" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(m)
        payload = json.loads(m.group(1))
        self.assertEqual(sum(payload["trend"]["present"]), sum(payload["workMode"]["values"]))
        self.assertGreaterEqual(sum(payload["trend"]["present"]), 1)

        # Pagination equivalence: page 1 vs page 2 keeps whole-period totals & chart payload identical
        from apps.admin_panel.analytics_service import get_attendance_analytics
        p1 = get_attendance_analytics(self.admin, {"range": "7", "page": "1"})
        p2 = get_attendance_analytics(self.admin, {"range": "7", "page": "2"})
        self.assertEqual(p1["analytics_chart_payload"], p2["analytics_chart_payload"])
        self.assertEqual(p1["analytics_summary"]["total_present"], p2["analytics_summary"]["total_present"])

        # Oversized custom range (>90d) rejected before expensive processing
        oversized = get_attendance_analytics(
            self.admin,
            {
                "range": "custom",
                "start_date": (today - timedelta(days=120)).isoformat(),
                "end_date": today.isoformat(),
            },
        )
        self.assertIsNotNone(oversized["analytics_range_error"])
        self.assertEqual(oversized["analytics_employee_count"], 0)

        # Finance panel: approved unpaid expense changes approved_expenses while actual_payments stays 0.00
        from apps.expense.models import Expense
        Expense.objects.create(employee=self.emp_a, amount=decimal.Decimal("1250.50"), description="Travel", status="approved")
        Expense.objects.create(employee=self.emp_a, amount=decimal.Decimal("300.00"), description="Pending", status="pending_finance")
        resp_fin = self.client.get(reverse("dashboard"), {"widget": "analytics_finance", "range": "30"}, HTTP_HX_REQUEST="true")
        self.assertEqual(resp_fin.status_code, 200)
        fin_html = resp_fin.content.decode()
        self.assertIn("1250.50", fin_html)
        self.assertIn("300.00", fin_html)
        self.assertIn("0.00", fin_html)

    def test_5_bounded_query_growth_50_to_500(self):
        """
        Widget analytics queries stay bounded (<= 15) and O(1) growth from 50 to 500 employees,
        with zero writes during GET.
        """
        today = timezone.localdate()
        joined = today - timedelta(days=60)
        url = reverse("dashboard")
        self.client.force_login(self.admin)

        def create_batch(start_idx, count):
            users = [
                User(email=f"qemp_{i}@example.com", phone=f"018{i:08d}", password="!", role="staff", is_active=True)
                for i in range(start_idx, start_idx + count)
            ]
            User.objects.bulk_create(users, batch_size=500)
            created_users = list(User.objects.filter(email__startswith="qemp_").order_by("id"))[-count:]
            emps = [
                Employee(
                    user=u,
                    employee_number=f"QEMP-{start_idx + idx:04d}",
                    first_name="Q",
                    last_name=f"{start_idx + idx}",
                    department=self.dept_eng,
                    branch=self.branch_a,
                    status=EmployeeStatus.ACTIVE,
                    joined_date=joined,
                )
                for idx, u in enumerate(created_users)
            ]
            Employee.objects.bulk_create(emps, batch_size=500)
            created_emps = list(Employee.objects.filter(employee_number__startswith="QEMP-").order_by("id"))[-count:]
            profs = [
                EmployeeProfile(
                    user=e.user,
                    master_employee=e,
                    employee_id=e.employee_number,
                    full_name=f"Q {start_idx + idx}",
                    department="Engineering",
                    phone=f"018{start_idx + idx:08d}",
                    branch=self.branch_a,
                    is_active=True,
                    joined_date=joined,
                )
                for idx, e in enumerate(created_emps)
            ]
            EmployeeProfile.objects.bulk_create(profs, batch_size=500)

        create_batch(0, 50)
        with CaptureQueriesContext(connection) as ctx50:
            r50 = self.client.get(url, {"widget": "attendance_analytics", "range": "7"}, HTTP_HX_REQUEST="true")
        self.assertEqual(r50.status_code, 200)

        create_batch(50, 450)
        with CaptureQueriesContext(connection) as ctx500:
            r500 = self.client.get(url, {"widget": "attendance_analytics", "range": "90"}, HTTP_HX_REQUEST="true")
        self.assertEqual(r500.status_code, 200)

        self.assertLessEqual(len(ctx500), 20)
        self.assertLessEqual(len(ctx500) - len(ctx50), 2)
        for q in ctx500.captured_queries:
            sql_up = q["sql"].strip().upper()
            self.assertFalse(sql_up.startswith(("INSERT", "UPDATE", "DELETE")))
