from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

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
from apps.branches.models import Branch
from apps.employees.models import Department, Designation, Employee, EmployeeProfile, EmployeeStatus
from apps.expense.models import Expense, ExpenseCategory

User = get_user_model()


class ExpenseReadScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch_a = Branch.objects.create(name="Exp Branch A", latitude=23.81, longitude=90.41, radius_meters=200)
        cls.branch_b = Branch.objects.create(name="Exp Branch B", latitude=23.82, longitude=90.42, radius_meters=200)
        cls.dept = Department.objects.create(name="Sales", is_global=True)
        cls.desig = Designation.objects.create(name="Rep")
        cls.cat = ExpenseCategory.objects.create(name="Travel", code="TRV", is_active=True)

        cls.mod_exp, _ = Module.objects.get_or_create(code="expense", defaults={"name": "Expense"})
        cls.mod_rep, _ = Module.objects.get_or_create(code="reports", defaults={"name": "Reports"})
        act_view, _ = Action.objects.get_or_create(code="view", defaults={"name": "View"})
        act_app, _ = Action.objects.get_or_create(code="approve", defaults={"name": "Approve"})

        cls.perm_exp_view, _ = Permission.objects.get_or_create(
            codename="expense.view",
            defaults={"module": cls.mod_exp, "action": act_view, "name": "View Expense"},
        )
        cls.perm_exp_approve, _ = Permission.objects.get_or_create(
            codename="expense.approve",
            defaults={"module": cls.mod_exp, "action": act_app, "name": "Approve Expense"},
        )
        cls.perm_rep_view, _ = Permission.objects.get_or_create(
            codename="reports.view",
            defaults={"module": cls.mod_rep, "action": act_view, "name": "View Reports"},
        )

        # Branch A user (BRANCH expense.view)
        cls.user_a = User.objects.create_user(email="exp_a@example.com", password="pass", role="staff")
        cls.emp_a = Employee.objects.create(
            user=cls.user_a, employee_number="EXP-A", first_name="Alice", last_name="Exp",
            phone="01730000001", branch=cls.branch_a, department=cls.dept, designation=cls.desig,
            joined_date=date(2024, 1, 1), status=EmployeeStatus.ACTIVE,
        )
        cls.prof_a = EmployeeProfile.objects.create(
            user=cls.user_a, master_employee=cls.emp_a, employee_id="EXP-A", full_name="Alice Exp",
            phone="01730000001", branch=cls.branch_a, is_active=True, joined_date=date(2024, 1, 1),
        )

        # Branch B user (OWN expense.view)
        cls.user_b = User.objects.create_user(email="exp_b@example.com", password="pass", role="staff")
        cls.emp_b = Employee.objects.create(
            user=cls.user_b, employee_number="EXP-B", first_name="Bob", last_name="Exp",
            phone="01730000002", branch=cls.branch_b, department=cls.dept, designation=cls.desig,
            joined_date=date(2024, 1, 1), status=EmployeeStatus.ACTIVE,
        )
        cls.prof_b = EmployeeProfile.objects.create(
            user=cls.user_b, master_employee=cls.emp_b, employee_id="EXP-B", full_name="Bob Exp",
            phone="01730000002", branch=cls.branch_b, is_active=True, joined_date=date(2024, 1, 1),
        )

        # Missing identity user (BRANCH expense.view, no profile/branch)
        cls.no_identity_user = User.objects.create_user(email="exp_noid@example.com", password="pass", role="staff")

        # Manager with GLOBAL reports.view + BRANCH expense.approve + OWN expense.view
        cls.narrow_mgr = User.objects.create_user(email="exp_mgr@example.com", password="pass", role="staff")
        cls.emp_m = Employee.objects.create(
            user=cls.narrow_mgr, employee_number="EXP-M", first_name="Mgr", last_name="Exp",
            phone="01730000003", branch=cls.branch_a, department=cls.dept, designation=cls.desig,
            joined_date=date(2024, 1, 1), status=EmployeeStatus.ACTIVE,
        )
        cls.prof_m = EmployeeProfile.objects.create(
            user=cls.narrow_mgr, master_employee=cls.emp_m, employee_id="EXP-M", full_name="Mgr Exp",
            phone="01730000003", branch=cls.branch_a, is_active=True, joined_date=date(2024, 1, 1),
        )

        role_branch = Role.objects.create(code="exp_branch_view", name="Expense Branch View")
        RolePermission.objects.create(role=role_branch, permission=cls.perm_exp_view, data_scope=DataScope.BRANCH)
        UserRoleAssignment.objects.create(user=cls.user_a, role=role_branch)
        UserRoleAssignment.objects.create(user=cls.no_identity_user, role=role_branch)

        role_own = Role.objects.create(code="exp_own_view", name="Expense Own View")
        RolePermission.objects.create(role=role_own, permission=cls.perm_exp_view, data_scope=DataScope.OWN)
        UserRoleAssignment.objects.create(user=cls.user_b, role=role_own)

        role_mgr = Role.objects.create(code="exp_narrow_mgr", name="Expense Narrow Mgr")
        RolePermission.objects.create(role=role_mgr, permission=cls.perm_rep_view, data_scope=DataScope.GLOBAL)
        RolePermission.objects.create(role=role_mgr, permission=cls.perm_exp_approve, data_scope=DataScope.BRANCH)
        RolePermission.objects.create(role=role_mgr, permission=cls.perm_exp_view, data_scope=DataScope.OWN)
        UserRoleAssignment.objects.create(user=cls.narrow_mgr, role=role_mgr)

        cls.exp_a1 = Expense.objects.create(
            employee=cls.prof_a, category=cls.cat, amount=Decimal("120.00"),
            description="A Pending", status="pending_manager",
        )
        cls.exp_a2 = Expense.objects.create(
            employee=cls.prof_a, category=cls.cat, amount=Decimal("220.00"),
            description="A Approved", status="approved",
        )
        cls.exp_b1 = Expense.objects.create(
            employee=cls.prof_b, category=cls.cat, amount=Decimal("999.00"),
            description="B Secret", status="rejected",
        )

        for u in (cls.user_a, cls.user_b, cls.no_identity_user, cls.narrow_mgr):
            PermissionEngine.invalidate_user_cache(u)

    def test_1_branch_viewer_sees_only_own_branch_rows_counters_and_constant_queries(self):
        self.client.force_login(self.user_a)
        url = reverse("expense:admin_expense_list")

        with CaptureQueriesContext(connection) as ctx_before:
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.context["expenses"]), {self.exp_a1, self.exp_a2})
        self.assertEqual(resp.context["all_count"], 2)
        self.assertEqual(resp.context["pending_count"], 1)
        self.assertEqual(resp.context["approved_count"], 1)
        self.assertEqual(resp.context["rejected_count"], 0)

        # Status filter only narrows within Branch A
        filtered = self.client.get(url, {"status": "rejected"})
        self.assertEqual(list(filtered.context["expenses"]), [])

        # Adding out-of-scope Branch B expenses must not grow query count
        for i in range(3):
            Expense.objects.create(
                employee=self.prof_b, category=self.cat, amount=Decimal("500.00"),
                description=f"B Extra {i}", status="approved",
            )
        with CaptureQueriesContext(connection) as ctx_after:
            resp_after = self.client.get(url)
        self.assertEqual(resp_after.status_code, 200)
        self.assertEqual(resp_after.context["all_count"], 2)
        self.assertLessEqual(len(ctx_after), len(ctx_before))

    def test_2_own_viewer_denied_other_detail_but_keeps_own_access(self):
        self.client.force_login(self.user_b)

        denied_api = self.client.get(reverse("expense:admin_expense_detail_api", kwargs={"pk": self.exp_a1.pk}))
        self.assertEqual(denied_api.status_code, 403)
        self.assertNotIn("120.00", denied_api.content.decode())

        denied_html = self.client.get(reverse("expense:expense_detail", kwargs={"pk": self.exp_a1.pk}))
        self.assertEqual(denied_html.status_code, 403)

        own_html = self.client.get(reverse("expense:expense_detail", kwargs={"pk": self.exp_b1.pk}))
        self.assertEqual(own_html.status_code, 200)

        own_api = self.client.get(reverse("expense:admin_expense_detail_api", kwargs={"pk": self.exp_b1.pk}))
        self.assertEqual(own_api.status_code, 200)
        self.assertEqual(own_api.json()["amount"], "999.00")

    def test_3_missing_canonical_identity_fails_closed(self):
        self.client.force_login(self.no_identity_user)

        list_resp = self.client.get(reverse("expense:admin_expense_list"))
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list(list_resp.context["expenses"]), [])
        self.assertEqual(list_resp.context["all_count"], 0)

        api_resp = self.client.get(reverse("expense:admin_expense_detail_api", kwargs={"pk": self.exp_a1.pk}))
        self.assertEqual(api_resp.status_code, 403)

    def test_4_approve_or_reports_permission_does_not_grant_global_expense_read(self):
        self.client.force_login(self.narrow_mgr)

        # Narrow manager has reports.view=GLOBAL, expense.approve=BRANCH, expense.view=OWN
        list_resp = self.client.get(reverse("expense:admin_expense_list"))
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list(list_resp.context["expenses"]), [])
        self.assertEqual(list_resp.context["all_count"], 0)

        # Even for same-branch employee exp_a1, expense.approve=BRANCH does NOT override expense.view=OWN on read
        api_a = self.client.get(reverse("expense:admin_expense_detail_api", kwargs={"pk": self.exp_a1.pk}))
        self.assertEqual(api_a.status_code, 403)

        api_b = self.client.get(reverse("expense:admin_expense_detail_api", kwargs={"pk": self.exp_b1.pk}))
        self.assertEqual(api_b.status_code, 403)
