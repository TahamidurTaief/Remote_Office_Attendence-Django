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
        from apps.projects.models import Project, ProjectTask, ProjectType, ProjectType
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


class FinanceAnalyticsPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.expense.models import Expense
        from apps.payroll.models import EmployeePayrollCalculation, PaymentMode, PayrollRun, PayrollRunStatus

        today = timezone.localdate()
        cls.branch_a = Branch.objects.create(
            name="Fin Branch Alpha",
            latitude=decimal.Decimal("23.810300"),
            longitude=decimal.Decimal("90.412500"),
            radius_meters=200,
        )
        cls.branch_b = Branch.objects.create(
            name="Fin Branch Beta",
            latitude=decimal.Decimal("22.356900"),
            longitude=decimal.Decimal("91.783200"),
            radius_meters=200,
        )
        cls.dept = Department.objects.create(name="FinanceOps")
        cls.admin = User.objects.create_superuser(email="fin_admin@example.com", password="pass", role="admin")

        # Employee A (Branch Alpha)
        cls.emp_a_user = User.objects.create_user(email="fin_empa@example.com", password="pass", role="staff")
        cls.emp_a_master = Employee.objects.create(
            user=cls.emp_a_user,
            employee_number="FIN-A-1",
            first_name="Fin",
            last_name="Alpha",
            department=cls.dept,
            branch=cls.branch_a,
            status=EmployeeStatus.ACTIVE,
            joined_date=date(2024, 1, 1),
        )
        cls.emp_a = EmployeeProfile.objects.create(
            user=cls.emp_a_user,
            master_employee=cls.emp_a_master,
            employee_id="FIN-A-1",
            full_name="Fin Alpha",
            department="FinanceOps",
            phone="01722000001",
            branch=cls.branch_a,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

        # Employee B (Branch Beta)
        cls.emp_b_user = User.objects.create_user(email="fin_empb@example.com", password="pass", role="staff")
        cls.emp_b_master = Employee.objects.create(
            user=cls.emp_b_user,
            employee_number="FIN-B-1",
            first_name="Fin",
            last_name="Beta",
            department=cls.dept,
            branch=cls.branch_b,
            status=EmployeeStatus.ACTIVE,
            joined_date=date(2024, 1, 1),
        )
        cls.emp_b = EmployeeProfile.objects.create(
            user=cls.emp_b_user,
            master_employee=cls.emp_b_master,
            employee_id="FIN-B-1",
            full_name="Fin Beta",
            department="FinanceOps",
            phone="01722000002",
            branch=cls.branch_b,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

        # Expenses in both branches
        Expense.objects.create(employee=cls.emp_a, amount=decimal.Decimal("1111.00"), description="Alpha Exp", status="approved")
        Expense.objects.create(employee=cls.emp_a, amount=decimal.Decimal("222.00"), description="Alpha Pend", status="pending_finance")
        Expense.objects.create(employee=cls.emp_b, amount=decimal.Decimal("3333.00"), description="Beta Exp", status="approved")

        # Payroll runs & calculations in both branches
        run_draft = PayrollRun.objects.create(
            name="Approved Run",
            period_start=today - timedelta(days=5),
            period_end=today,
            status=PayrollRunStatus.APPROVED_LOCKED,
        )
        run_paid = PayrollRun.objects.create(
            name="Paid Run",
            period_start=today - timedelta(days=5),
            period_end=today,
            status=PayrollRunStatus.DISBURSED,
        )
        for run, emp_m, amt in [
            (run_draft, cls.emp_a_master, decimal.Decimal("4444.00")),
            (run_draft, cls.emp_b_master, decimal.Decimal("5555.00")),
            (run_paid, cls.emp_a_master, decimal.Decimal("6666.00")),
            (run_paid, cls.emp_b_master, decimal.Decimal("7777.00")),
        ]:
            EmployeePayrollCalculation.objects.create(
                payroll_run=run,
                employee=emp_m,
                gross_salary=amt,
                payment_mode=PaymentMode.BANK,
                total_earnings=amt,
                total_deductions=decimal.Decimal("0.00"),
                net_payable=amt,
                bank_payable=amt,
                cash_payable=decimal.Decimal("0.00"),
                structure_snapshot={},
            )

        # RBAC Permissions
        mod_exp, _ = Module.objects.get_or_create(code="expense", defaults={"name": "Expense"})
        mod_pay, _ = Module.objects.get_or_create(code="payroll", defaults={"name": "Payroll"})
        mod_rep, _ = Module.objects.get_or_create(code="reports", defaults={"name": "Reports"})
        act_view, _ = Action.objects.get_or_create(code="view", defaults={"name": "View"})
        cls.perm_exp, _ = Permission.objects.get_or_create(
            codename="expense.view", defaults={"module": mod_exp, "action": act_view, "name": "View Expense"}
        )
        cls.perm_pay, _ = Permission.objects.get_or_create(
            codename="payroll.view", defaults={"module": mod_pay, "action": act_view, "name": "View Payroll"}
        )
        cls.perm_rep, _ = Permission.objects.get_or_create(
            codename="reports.view", defaults={"module": mod_rep, "action": act_view, "name": "View Reports"}
        )

    def _make_user_with_perms(self, email, perm_scopes, branch=None, link_emp=None):
        user = User.objects.create_user(email=email, password="pass", role="manager")
        if link_emp:
            # Reuse existing emp_a canonical mapping if requested, else create own
            pass
        elif branch:
            m = Employee.objects.create(
                user=user,
                employee_number=f"M-{user.pk}",
                first_name="Fin",
                last_name="User",
                department=self.dept,
                branch=branch,
                status=EmployeeStatus.ACTIVE,
                joined_date=date(2024, 1, 1),
            )
            EmployeeProfile.objects.create(
                user=user,
                master_employee=m,
                employee_id=f"M-{user.pk}",
                full_name="Fin User",
                department="FinanceOps",
                phone=f"01799{user.pk:06d}",
                branch=branch,
                is_active=True,
                joined_date=date(2024, 1, 1),
            )
        role = Role.objects.create(code=f"fin_role_{user.pk}", name=f"FinRole{user.pk}")
        for perm, scope in perm_scopes:
            RolePermission.objects.create(role=role, permission=perm, data_scope=scope)
        UserRoleAssignment.objects.create(user=user, role=role)
        return user

    def _extract_chart_payload(self, html):
        m = re.search(
            r'<script id="finance-analytics-chart-data" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(m)
        return json.loads(m.group(1))["financeBreakdown"]

    def test_1_expense_only_excludes_payroll_and_skips_payroll_queries(self):
        from apps.admin_panel.analytics_service import get_finance_analytics

        user = self._make_user_with_perms(
            "exp_only@example.com",
            [(self.perm_exp, DataScope.BRANCH)],
            branch=self.branch_a,
        )
        with CaptureQueriesContext(connection) as ctx:
            res = get_finance_analytics(user, {"range": "30"})
        self.assertTrue(res["fin_authorized"])
        self.assertEqual(res["fin_approved_expenses"], decimal.Decimal("1111.00"))
        self.assertEqual(res["fin_pending_claims"], decimal.Decimal("222.00"))
        self.assertNotIn("fin_payroll_liability", res)
        self.assertNotIn("fin_payroll_disbursed", res)
        self.assertFalse(any("payroll_employeepayrollcalculation" in q["sql"].lower() for q in ctx.captured_queries))

        self.client.force_login(user)
        resp = self.client.get(reverse("dashboard"), {"widget": "analytics_finance", "range": "30"}, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("1111.00", html)
        self.assertNotIn("Approved Payroll Liabilities", html)
        self.assertNotIn("Disbursed Payroll", html)
        self.assertNotIn("4444.00", html)
        self.assertNotIn("6666.00", html)
        chart = self._extract_chart_payload(html)
        self.assertEqual(chart["labels"], ["Approved Expenses", "Pending Claims"])
        self.assertEqual(chart["values"], [1111.0, 222.0])

    def test_2_payroll_only_without_reports_or_attendance_excludes_expenses(self):
        from apps.admin_panel.analytics_service import get_finance_analytics

        # Payroll-only user lacks reports.view, attendance.view, and dashboard.view
        user = self._make_user_with_perms(
            "pay_only@example.com",
            [(self.perm_pay, DataScope.BRANCH)],
            branch=self.branch_a,
        )
        with CaptureQueriesContext(connection) as ctx:
            res = get_finance_analytics(user, {"range": "30"})
        self.assertTrue(res["fin_authorized"])
        self.assertEqual(res["fin_payroll_liability"], decimal.Decimal("4444.00"))
        self.assertEqual(res["fin_payroll_disbursed"], decimal.Decimal("6666.00"))
        self.assertNotIn("fin_approved_expenses", res)
        self.assertNotIn("fin_pending_claims", res)
        self.assertNotIn("fin_page_obj", res)
        self.assertFalse(any("expense_expense" in q["sql"].lower() for q in ctx.captured_queries))

        self.client.force_login(user)
        resp = self.client.get(reverse("dashboard"), {"widget": "analytics_finance", "range": "30"}, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("4444.00", html)
        self.assertIn("6666.00", html)
        self.assertNotIn("Approved Expenses", html)
        self.assertNotIn("Pending Claims", html)
        self.assertNotIn("1111.00", html)
        chart = self._extract_chart_payload(html)
        self.assertEqual(chart["labels"], ["Approved Payroll Liabilities", "Draft/Review Estimates", "Disbursed Payroll"])
        self.assertEqual(chart["values"], [4444.0, 0.0, 6666.0])

    def test_3_mixed_scopes_expense_global_payroll_own(self):
        from apps.admin_panel.analytics_service import get_finance_analytics

        # Assign expense.view=GLOBAL and payroll.view=OWN directly to emp_a_user
        role = Role.objects.create(code="mixed_fin_role", name="MixedFinRole")
        RolePermission.objects.create(role=role, permission=self.perm_exp, data_scope=DataScope.GLOBAL)
        RolePermission.objects.create(role=role, permission=self.perm_pay, data_scope=DataScope.OWN)
        UserRoleAssignment.objects.create(user=self.emp_a_user, role=role)

        res = get_finance_analytics(self.emp_a_user, {"range": "30"})
        self.assertTrue(res["fin_authorized"])
        # Global expenses: Alpha (1111) + Beta (3333) = 4444.00
        self.assertEqual(res["fin_approved_expenses"], decimal.Decimal("4444.00"))
        self.assertEqual(res["fin_pending_claims"], decimal.Decimal("222.00"))
        # Own payroll: only emp_a_master (4444.00 liability, 6666.00 disbursed), NOT emp_b_master
        self.assertEqual(res["fin_payroll_liability"], decimal.Decimal("4444.00"))
        self.assertEqual(res["fin_payroll_disbursed"], decimal.Decimal("6666.00"))

    def test_4_denied_access_returns_403_and_superuser_preserved(self):
        from apps.admin_panel.analytics_service import get_finance_analytics

        # User has reports.view GLOBAL but neither expense.view nor payroll.view
        no_fin_user = self._make_user_with_perms(
            "no_fin@example.com",
            [(self.perm_rep, DataScope.GLOBAL)],
            branch=self.branch_a,
        )
        self.assertFalse(get_finance_analytics(no_fin_user, {"range": "30"})["fin_authorized"])

        self.client.force_login(no_fin_user)
        resp = self.client.get(reverse("dashboard"), {"widget": "analytics_finance", "range": "30"}, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 403)

        # Superuser sees all global expense + payroll data
        self.client.force_login(self.admin)
        resp_admin = self.client.get(reverse("dashboard"), {"widget": "analytics_finance", "range": "30"}, HTTP_HX_REQUEST="true")
        self.assertEqual(resp_admin.status_code, 200)
        chart = self._extract_chart_payload(resp_admin.content.decode())
        self.assertEqual(chart["labels"], ["Approved Expenses", "Pending Claims", "Approved Payroll Liabilities", "Draft/Review Estimates", "Disbursed Payroll"])
        self.assertEqual(chart["values"], [4444.0, 222.0, 9999.0, 0.0, 14443.0])

    def test_5_forged_branch_filter_and_role_view_cannot_expand_scope(self):
        user = self._make_user_with_perms(
            "forged_fin@example.com",
            [(self.perm_exp, DataScope.BRANCH), (self.perm_pay, DataScope.BRANCH)],
            branch=self.branch_a,
        )
        self.client.force_login(user)
        resp = self.client.get(
            reverse("dashboard"),
            {
                "widget": "analytics_finance",
                "range": "30",
                "branch": str(self.branch_b.id),
                "role_view": "admin",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("No scoped employees match the selected filters.", html)
        self.assertNotIn("3333.00", html)
        self.assertNotIn("5555.00", html)
        self.assertNotIn("7777.00", html)
        chart = self._extract_chart_payload(html)
        self.assertEqual(chart["values"], [])


class PeopleLeavePeriodClippingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="PL Branch", latitude=23.81, longitude=90.41, radius_meters=200)
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
            },
        )
        cls.dept = Department.objects.create(name="PLDept")
        cls.admin = User.objects.create_superuser(email="pl_adm@example.com", password="pass", role="admin")
        cls.emp_user = User.objects.create_user(email="pl_emp@example.com", password="pass", role="staff")
        cls.emp_master = Employee.objects.create(
            user=cls.emp_user,
            employee_number="PL-1",
            first_name="PL",
            last_name="Emp",
            department=cls.dept,
            branch=cls.branch,
            status=EmployeeStatus.ACTIVE,
            joined_date=date(2024, 1, 1),
            weekly_holiday_policy="",
        )
        cls.emp = EmployeeProfile.objects.create(
            user=cls.emp_user,
            master_employee=cls.emp_master,
            employee_id="PL-1",
            full_name="PL Emp",
            department="PLDept",
            phone="01300000001",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

    def test_1_three_day_leave_clipped_to_single_day_range(self):
        from apps.admin_panel.analytics_service import get_people_leave_analytics
        from apps.leave.models import LeaveRequest, LeaveType

        lt = LeaveType.objects.create(name="Annual")
        # Mon-Wed (3 working days)
        mon = date(2026, 11, 16)
        wed = date(2026, 11, 18)
        LeaveRequest.objects.create(
            employee=self.emp, leave_type=lt, start_date=mon, end_date=wed, number_of_days=decimal.Decimal("3.0"), reason="Trip", status="approved"
        )
        res = get_people_leave_analytics(
            self.admin,
            {"range": "custom", "start_date": wed.isoformat(), "end_date": wed.isoformat(), "branch": str(self.branch.id)},
        )
        self.assertEqual(res["pl_approved_count"], 1)
        self.assertEqual(res["pl_approved_days"], decimal.Decimal("1.00"))
        self.assertEqual(res["pl_type_items"][0]["count"], 1)
        self.assertEqual(res["pl_type_items"][0]["days"], decimal.Decimal("1.00"))
        self.assertEqual(res["pl_page_obj"].object_list[0]["in_range_days"], decimal.Decimal("1.00"))

    def test_2_half_day_inside_and_outside_range(self):
        from apps.admin_panel.analytics_service import get_people_leave_analytics
        from apps.leave.models import LeaveRequest, LeaveType

        lt = LeaveType.objects.create(name="Casual")
        tue = date(2026, 11, 17)
        thu = date(2026, 11, 19)
        lr_in = LeaveRequest.objects.create(
            employee=self.emp, leave_type=lt, start_date=tue, end_date=tue, number_of_days=decimal.Decimal("0.5"), reason="HalfIn", status="approved"
        )
        LeaveRequest.objects.filter(pk=lr_in.pk).update(number_of_days=decimal.Decimal("0.5"))
        lr_out = LeaveRequest.objects.create(
            employee=self.emp, leave_type=lt, start_date=thu, end_date=thu, number_of_days=decimal.Decimal("0.5"), reason="HalfOut", status="approved"
        )
        LeaveRequest.objects.filter(pk=lr_out.pk).update(number_of_days=decimal.Decimal("0.5"))

        res = get_people_leave_analytics(
            self.admin,
            {"range": "custom", "start_date": tue.isoformat(), "end_date": tue.isoformat(), "branch": str(self.branch.id)},
        )
        self.assertEqual(res["pl_approved_count"], 1)
        self.assertEqual(res["pl_approved_days"], decimal.Decimal("0.50"))

    def test_3_cross_year_and_holiday_inside_overlap_excluded(self):
        from apps.admin_panel.analytics_service import get_people_leave_analytics
        from apps.leave.models import LeaveRequest, LeaveType

        lt = LeaveType.objects.create(name="CrossYear")
        # Dec 30 2025 (Tue) to Jan 1 2026 (Thu), with Jan 1 as a public holiday
        Holiday.objects.create(name="New Year", date=date(2026, 1, 1), branch=None)
        LeaveRequest.objects.create(
            employee=self.emp, leave_type=lt, start_date=date(2025, 12, 30), end_date=date(2026, 1, 3), number_of_days=decimal.Decimal("4.0"), reason="NY", status="approved"
        )
        res = get_people_leave_analytics(
            self.admin,
            {"range": "custom", "start_date": "2026-01-01", "end_date": "2026-01-03", "branch": str(self.branch.id)},
        )
        # Jan 1 = Holiday, Jan 2 = Friday (off), Jan 3 = Saturday (working) -> 1.00 day
        self.assertEqual(res["pl_approved_count"], 1)
        self.assertEqual(res["pl_approved_days"], decimal.Decimal("1.00"))

    def test_4_overlapping_approvals_and_bounded_queries_and_html(self):
        from apps.admin_panel.analytics_service import get_people_leave_analytics
        from apps.leave.models import LeaveRequest, LeaveType

        lt = LeaveType.objects.create(name="Overlap")
        mon = date(2026, 11, 16)
        LeaveRequest.objects.create(
            employee=self.emp, leave_type=lt, start_date=mon, end_date=mon, number_of_days=decimal.Decimal("1.0"), reason="A", status="approved"
        )
        lr_b = LeaveRequest.objects.create(
            employee=self.emp, leave_type=lt, start_date=mon, end_date=mon, number_of_days=decimal.Decimal("1.0"), reason="B", status="approved"
        )
        LeaveRequest.objects.filter(pk=lr_b.pk).update(number_of_days=decimal.Decimal("1.0"))
        with CaptureQueriesContext(connection) as ctx:
            res = get_people_leave_analytics(
                self.admin,
                {"range": "custom", "start_date": mon.isoformat(), "end_date": mon.isoformat(), "branch": str(self.branch.id)},
            )
        self.assertLessEqual(len(ctx), 10)
        self.assertEqual(res["pl_approved_days"], decimal.Decimal("1.00"))
        self.assertEqual(res["pl_overlap_count"], 1)

        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse("dashboard"),
            {"widget": "analytics_people_leave", "range": "custom", "start_date": mon.isoformat(), "end_date": mon.isoformat(), "branch": str(self.branch.id)},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Request Days", html)
        self.assertIn("In-Range Days", html)
        self.assertIn("Overlapping Requests", html)


class FinancePayrollLiabilityClassificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="FinClass Branch", latitude=23.81, longitude=90.41, radius_meters=200)
        cls.dept = Department.objects.create(name="FinClassDept")
        cls.admin = User.objects.create_superuser(email="fc_adm@example.com", password="pass", role="admin")
        cls.emp_user = User.objects.create_user(email="fc_emp@example.com", password="pass", role="staff")
        cls.emp_master = Employee.objects.create(
            user=cls.emp_user,
            employee_number="FC-1",
            first_name="FC",
            last_name="Emp",
            department=cls.dept,
            branch=cls.branch,
            status=EmployeeStatus.ACTIVE,
            joined_date=date(2024, 1, 1),
        )
        cls.emp = EmployeeProfile.objects.create(
            user=cls.emp_user,
            master_employee=cls.emp_master,
            employee_id="FC-1",
            full_name="FC Emp",
            department="FinClassDept",
            phone="01311111111",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

    def _add_calc(self, name, status, amount):
        from apps.payroll.models import EmployeePayrollCalculation, PaymentMode, PayrollRun

        today = timezone.localdate()
        run = PayrollRun.objects.create(
            name=name,
            period_start=today - timedelta(days=5),
            period_end=today,
            status=status,
        )
        EmployeePayrollCalculation.objects.create(
            payroll_run=run,
            employee=self.emp_master,
            gross_salary=amount,
            payment_mode=PaymentMode.BANK,
            total_earnings=amount,
            total_deductions=decimal.Decimal("0.00"),
            net_payable=amount,
            bank_payable=amount,
            cash_payable=decimal.Decimal("0.00"),
            structure_snapshot={},
        )
        return run

    def test_1_draft_and_review_excluded_from_approved_liability(self):
        from apps.admin_panel.analytics_service import get_finance_analytics
        from apps.payroll.models import PayrollRunStatus

        self._add_calc("DraftRun", PayrollRunStatus.DRAFT, decimal.Decimal("100.00"))
        self._add_calc("ReviewRun", PayrollRunStatus.REVIEW, decimal.Decimal("200.00"))
        self._add_calc("ApprovedRun", PayrollRunStatus.APPROVED_LOCKED, decimal.Decimal("300.00"))

        with CaptureQueriesContext(connection) as ctx:
            res = get_finance_analytics(self.admin, {"range": "30", "branch": str(self.branch.id)})
        self.assertLessEqual(len(ctx), 10)
        self.assertEqual(res["fin_payroll_liability"], decimal.Decimal("300.00"))
        self.assertEqual(res["fin_payroll_draft_estimate"], decimal.Decimal("300.00"))
        self.assertEqual(res["fin_payroll_disbursed"], decimal.Decimal("0.00"))

    def test_2_approved_moved_to_disbursed_leaves_approved_liability(self):
        from apps.admin_panel.analytics_service import get_finance_analytics
        from apps.payroll.models import PayrollRunStatus

        run = self._add_calc("ToDisburse", PayrollRunStatus.APPROVED_LOCKED, decimal.Decimal("450.00"))
        res_before = get_finance_analytics(self.admin, {"range": "30", "branch": str(self.branch.id)})
        self.assertEqual(res_before["fin_payroll_liability"], decimal.Decimal("450.00"))

        run.status = PayrollRunStatus.DISBURSED
        run.save()
        res_after = get_finance_analytics(self.admin, {"range": "30", "branch": str(self.branch.id)})
        self.assertEqual(res_after["fin_payroll_liability"], decimal.Decimal("0.00"))
        self.assertEqual(res_after["fin_payroll_disbursed"], decimal.Decimal("450.00"))

    def test_3_reversed_run_returning_to_draft_moves_to_estimate(self):
        from apps.admin_panel.analytics_service import get_finance_analytics
        from apps.payroll.models import PayrollRunStatus

        run = self._add_calc("ReversedRun", PayrollRunStatus.APPROVED_LOCKED, decimal.Decimal("500.00"))
        run.status = PayrollRunStatus.DRAFT
        run.save()
        res = get_finance_analytics(self.admin, {"range": "30", "branch": str(self.branch.id)})
        self.assertEqual(res["fin_payroll_liability"], decimal.Decimal("0.00"))
        self.assertEqual(res["fin_payroll_draft_estimate"], decimal.Decimal("500.00"))

    def test_4_empty_scope_returns_zero_decimals(self):
        from apps.admin_panel.analytics_service import get_finance_analytics

        other_branch = Branch.objects.create(name="EmptyFinBranch", latitude=23.90, longitude=90.50, radius_meters=200)
        res = get_finance_analytics(self.admin, {"range": "30", "branch": str(other_branch.id)})
        self.assertEqual(res["analytics_employee_count"], 0)
        self.assertEqual(res["fin_payroll_liability"], decimal.Decimal("0.00"))
        self.assertEqual(res["fin_payroll_draft_estimate"], decimal.Decimal("0.00"))
        self.assertEqual(res["fin_payroll_disbursed"], decimal.Decimal("0.00"))


class AnalyticsBoundedPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.expense.models import Expense, ExpenseCategory
        from apps.leave.models import LeaveRequest, LeaveType
        from apps.projects.models import Project, ProjectTask, ProjectType

        cls.branch_a = Branch.objects.create(name="Pag Branch A", latitude=23.81, longitude=90.41, radius_meters=200)
        cls.branch_b = Branch.objects.create(name="Pag Branch B", latitude=23.82, longitude=90.42, radius_meters=200)
        cls.dept = Department.objects.create(name="PagDept")
        cls.admin = User.objects.create_superuser(email="pag_adm@example.com", password="pass", role="admin")
        cls.emp_user = User.objects.create_user(email="pag_emp@example.com", password="pass", role="staff")
        cls.emp_m = Employee.objects.create(
            user=cls.emp_user, employee_number="PAG-1", first_name="Pag", last_name="Emp",
            department=cls.dept, branch=cls.branch_a, status=EmployeeStatus.ACTIVE, joined_date=date(2024, 1, 1),
        )
        cls.emp = EmployeeProfile.objects.create(
            user=cls.emp_user, master_employee=cls.emp_m, employee_id="PAG-1", full_name="Pag Emp",
            department="PagDept", phone="01322222222", branch=cls.branch_a, is_active=True, joined_date=date(2024, 1, 1),
        )
        cls.mgr_user = User.objects.create_user(email="pag_mgr@example.com", password="pass", role="manager")
        cls.mgr_m = Employee.objects.create(
            user=cls.mgr_user, employee_number="PAG-M", first_name="Pag", last_name="Mgr",
            department=cls.dept, branch=cls.branch_a, status=EmployeeStatus.ACTIVE, joined_date=date(2024, 1, 1),
        )
        EmployeeProfile.objects.create(
            user=cls.mgr_user, master_employee=cls.mgr_m, employee_id="PAG-M", full_name="Pag Mgr",
            department="PagDept", phone="01322222223", branch=cls.branch_a, is_active=True, joined_date=date(2024, 1, 1),
        )
        mod_exp, _ = Module.objects.get_or_create(code="expense", defaults={"name": "Expense"})
        mod_rep, _ = Module.objects.get_or_create(code="reports", defaults={"name": "Reports"})
        act_view, _ = Action.objects.get_or_create(code="view", defaults={"name": "View"})
        perm_exp, _ = Permission.objects.get_or_create(codename="expense.view", defaults={"module": mod_exp, "action": act_view, "name": "View Expense"})
        perm_rep, _ = Permission.objects.get_or_create(codename="reports.view", defaults={"module": mod_rep, "action": act_view, "name": "View Reports"})
        role = Role.objects.create(code="pag_mgr_role", name="PagMgrRole")
        RolePermission.objects.create(role=role, permission=perm_exp, data_scope=DataScope.BRANCH)
        RolePermission.objects.create(role=role, permission=perm_rep, data_scope=DataScope.BRANCH)
        UserRoleAssignment.objects.create(user=cls.mgr_user, role=role)

        today = timezone.localdate()
        cls.target_day = today - timedelta(days=2)
        lt = LeaveType.objects.create(name="PagLeave")
        ptype = ProjectType.objects.create(name="PagType")
        proj = Project.objects.create(
            name="PagProj",
            client_name="PagClient",
            location="Dhaka",
            project_type=ptype,
            start_date=today - timedelta(days=30),
        )
        cat = ExpenseCategory.objects.create(name="PagCat")

        # 25 rows each with identical sort keys to test bounded hydration + deterministic tie-break
        LeaveRequest.objects.bulk_create([
            LeaveRequest(employee=cls.emp, leave_type=lt, start_date=cls.target_day, end_date=cls.target_day, number_of_days=decimal.Decimal("1.0"), reason=f"R{i}", status="pending")
            for i in range(25)
        ])
        ProjectTask.objects.bulk_create([
            ProjectTask(project=proj, activity=f"Task {i}", order=i, responsible_person=cls.emp, status="Pending", planned_start=cls.target_day, planned_finish=cls.target_day)
            for i in range(25)
        ])
        Expense.objects.bulk_create([
            Expense(employee=cls.emp, category=cat, amount=decimal.Decimal("10.00"), description=f"Exp {i}", status="approved")
            for i in range(25)
        ])

    def test_1_page_bounds_hydrate_only_requested_page_with_deterministic_tiebreak(self):
        from apps.admin_panel.analytics_service import DEFAULT_PAGE_SIZE, get_finance_analytics, get_people_leave_analytics, get_work_analytics

        for fn, key in [
            (get_people_leave_analytics, "pl_page_obj"),
            (get_work_analytics, "work_page_obj"),
            (get_finance_analytics, "fin_page_obj"),
        ]:
            p1 = fn(self.admin, {"range": "30", "branch": str(self.branch_a.id), "page": "1"})[key]
            p2 = fn(self.admin, {"range": "30", "branch": str(self.branch_a.id), "page": "2"})[key]
            p99 = fn(self.admin, {"range": "30", "branch": str(self.branch_a.id), "page": "999"})[key]

            self.assertEqual(len(p1.object_list), DEFAULT_PAGE_SIZE)
            self.assertEqual(len(p2.object_list), DEFAULT_PAGE_SIZE)
            self.assertEqual(len(p99.object_list), 25 % DEFAULT_PAGE_SIZE or DEFAULT_PAGE_SIZE)
            ids1 = {r["id"] for r in p1.object_list}
            ids2 = {r["id"] for r in p2.object_list}
            self.assertTrue(ids1.isdisjoint(ids2))

    def test_2_page_1_vs_page_2_preserve_identical_kpi_and_chart_totals(self):
        from apps.admin_panel.analytics_service import get_finance_analytics, get_people_leave_analytics, get_work_analytics

        pl1 = get_people_leave_analytics(self.admin, {"range": "30", "branch": str(self.branch_a.id), "page": "1"})
        pl2 = get_people_leave_analytics(self.admin, {"range": "30", "branch": str(self.branch_a.id), "page": "2"})
        self.assertEqual(pl1["pl_pending_count"], 25)
        self.assertEqual(pl1["pl_pending_count"], pl2["pl_pending_count"])

        wk1 = get_work_analytics(self.admin, {"range": "30", "branch": str(self.branch_a.id), "page": "1"})
        wk2 = get_work_analytics(self.admin, {"range": "30", "branch": str(self.branch_a.id), "page": "2"})
        self.assertEqual(wk1["work_total_tasks"], 25)
        self.assertEqual(wk1["work_chart_payload"], wk2["work_chart_payload"])

        fn1 = get_finance_analytics(self.admin, {"range": "30", "branch": str(self.branch_a.id), "page": "1"})
        fn2 = get_finance_analytics(self.admin, {"range": "30", "branch": str(self.branch_a.id), "page": "2"})
        self.assertEqual(fn1["fin_approved_expenses"], decimal.Decimal("250.00"))
        self.assertEqual(fn1["fin_chart_payload"], fn2["fin_chart_payload"])

    def test_3_forged_filters_and_scope_enforced_with_bounded_queries(self):
        from apps.admin_panel.analytics_service import get_finance_analytics, get_people_leave_analytics, get_work_analytics

        params = {"range": "30", "branch": str(self.branch_b.id), "page": "2"}
        for fn, page_key in [
            (get_people_leave_analytics, "pl_page_obj"),
            (get_work_analytics, "work_page_obj"),
            (get_finance_analytics, "fin_page_obj"),
        ]:
            with CaptureQueriesContext(connection) as ctx:
                res = fn(self.mgr_user, params, role_variant="employee")
            self.assertLessEqual(len(ctx), 10)
            self.assertEqual(res["analytics_employee_count"], 0)
            self.assertEqual(len(res[page_key].object_list), 0)
