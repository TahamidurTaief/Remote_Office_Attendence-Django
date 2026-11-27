from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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
from apps.payroll.models import (
    EmployeePayrollCalculation,
    PayrollAdjustment,
    PayrollRun,
    PayrollRunStatus,
    SalaryComponent,
    SalaryComponentType,
    SalaryComponentValueType,
)
from apps.payroll.services import PayrollService

User = get_user_model()


class PayrollRBACAuthorizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch_a = Branch.objects.create(name="Pay Branch A", latitude=23.81, longitude=90.41, radius_meters=200)
        cls.branch_b = Branch.objects.create(name="Pay Branch B", latitude=23.82, longitude=90.42, radius_meters=200)
        cls.dept = Department.objects.create(name="Finance Dept", is_global=True)
        cls.desig = Designation.objects.create(name="Executive")

        cls.mod_payroll, _ = Module.objects.get_or_create(code="payroll", defaults={"name": "Payroll"})
        cls.perms = {}
        for code in ("view", "add", "edit", "delete", "approve", "export"):
            act, _ = Action.objects.get_or_create(code=code, defaults={"name": code.title()})
            perm, _ = Permission.objects.get_or_create(
                codename=f"payroll.{code}",
                defaults={"module": cls.mod_payroll, "action": act, "name": f"Payroll {code.title()}"},
            )
            cls.perms[code] = perm

        # Employees in Branch A and Branch B
        cls.user_a = User.objects.create_user(email="pay_a@example.com", password="pass", role="staff")
        cls.emp_a = Employee.objects.create(
            user=cls.user_a,
            employee_number="PAY-A",
            first_name="Alice",
            last_name="Pay",
            phone="01720000001",
            branch=cls.branch_a,
            department=cls.dept,
            designation=cls.desig,
            joined_date=date(2024, 1, 1),
            status=EmployeeStatus.ACTIVE,
        )
        cls.prof_a = EmployeeProfile.objects.create(
            user=cls.user_a,
            master_employee=cls.emp_a,
            employee_id="PAY-A",
            full_name="Alice Pay",
            phone="01720000001",
            branch=cls.branch_a,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

        cls.user_b = User.objects.create_user(email="pay_b@example.com", password="pass", role="staff")
        cls.emp_b = Employee.objects.create(
            user=cls.user_b,
            employee_number="PAY-B",
            first_name="Bob",
            last_name="Pay",
            phone="01720000002",
            branch=cls.branch_b,
            department=cls.dept,
            designation=cls.desig,
            joined_date=date(2024, 1, 1),
            status=EmployeeStatus.ACTIVE,
        )
        cls.prof_b = EmployeeProfile.objects.create(
            user=cls.user_b,
            master_employee=cls.emp_b,
            employee_id="PAY-B",
            full_name="Bob Pay",
            phone="01720000002",
            branch=cls.branch_b,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

        # View-only user (GLOBAL)
        cls.viewer_user = User.objects.create_user(email="pay_viewer@example.com", password="pass", role="staff")
        role_view = Role.objects.create(code="payroll_viewer_only", name="Payroll Viewer Only")
        RolePermission.objects.create(role=role_view, permission=cls.perms["view"], data_scope=DataScope.GLOBAL)
        UserRoleAssignment.objects.create(user=cls.viewer_user, role=role_view)

        # Branch A scoped manager (view + edit + approve + export at BRANCH scope)
        role_branch = Role.objects.create(code="branch_payroll_mgr", name="Branch Payroll Mgr")
        for p_code in ("view", "edit", "approve", "export"):
            RolePermission.objects.create(role=role_branch, permission=cls.perms[p_code], data_scope=DataScope.BRANCH)
        UserRoleAssignment.objects.create(user=cls.user_a, role=role_branch)

        # HR/Finance role-string user with NO RBAC permissions (revoked)
        cls.revoked_hr_user = User.objects.create_user(
            email="revoked_hr@example.com", password="pass", role="hr", is_staff=True
        )

        cls.component = SalaryComponent.objects.create(
            name="Bonus",
            code="BONUS_TEST",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.FIXED,
            value=Decimal("500.00"),
            is_active=True,
        )

        cls.payroll_run = PayrollRun.objects.create(
            name="May 2024 Payroll",
            period_start=date(2024, 5, 1),
            period_end=date(2024, 5, 31),
            status=PayrollRunStatus.DRAFT,
        )
        cls.calc_a = EmployeePayrollCalculation.objects.create(
            payroll_run=cls.payroll_run,
            employee=cls.emp_a,
            gross_salary=Decimal("10000.00"),
            total_earnings=Decimal("10000.00"),
            total_deductions=Decimal("0.00"),
            net_payable=Decimal("10000.00"),
            bank_payable=Decimal("10000.00"),
            cash_payable=Decimal("0.00"),
            structure_snapshot={"components": []},
        )
        cls.calc_b = EmployeePayrollCalculation.objects.create(
            payroll_run=cls.payroll_run,
            employee=cls.emp_b,
            gross_salary=Decimal("25000.00"),
            total_earnings=Decimal("25000.00"),
            total_deductions=Decimal("0.00"),
            net_payable=Decimal("25000.00"),
            bank_payable=Decimal("25000.00"),
            cash_payable=Decimal("0.00"),
            structure_snapshot={"components": []},
        )

        for u in (cls.viewer_user, cls.user_a, cls.user_b, cls.revoked_hr_user):
            PermissionEngine.invalidate_user_cache(u)

    def test_1_payroll_view_only_user_blocked_from_mutations_and_exports(self):
        self.client.force_login(self.viewer_user)

        sync_resp = self.client.post(reverse("payroll:payroll_run_sync", kwargs={"pk": self.payroll_run.pk}))
        adj_resp = self.client.post(
            reverse("payroll:payroll_adjustment_add", kwargs={"pk": self.payroll_run.pk}),
            data={
                "employee_id": self.emp_a.pk,
                "component_id": self.component.pk,
                "amount": "250.00",
                "type": SalaryComponentType.EARNING,
            },
        )
        trans_resp = self.client.post(
            reverse("payroll:payroll_run_transition", kwargs={"pk": self.payroll_run.pk}),
            data={"target_status": PayrollRunStatus.REVIEW},
        )
        export_resp = self.client.get(
            reverse("payroll:payroll_register_export", kwargs={"pk": self.payroll_run.pk, "format": "csv"})
        )

        for resp in (sync_resp, adj_resp, trans_resp, export_resp):
            self.assertEqual(resp.status_code, 403)

        self.payroll_run.refresh_from_db()
        self.assertEqual(self.payroll_run.status, PayrollRunStatus.DRAFT)
        self.assertEqual(PayrollAdjustment.objects.filter(payroll_run=self.payroll_run).count(), 0)

    def test_2_scoped_viewer_sees_only_permitted_salary_data_and_own_payslip_preserved(self):
        self.client.force_login(self.user_a)
        detail_url = reverse("payroll:payroll_run_detail", kwargs={"pk": self.payroll_run.pk})
        with CaptureQueriesContext(connection) as ctx_before:
            detail_resp = self.client.get(detail_url)
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.context["totals"]["gross"], Decimal("10000.00"))
        self.assertEqual(detail_resp.context["totals"]["count"], 1)

        # Adding out-of-scope employees/calculations must not increase query count
        for idx in range(3):
            extra_emp = Employee.objects.create(
                employee_number=f"PAY-X{idx}",
                first_name="Extra",
                last_name=f"B{idx}",
                phone=f"0172999000{idx}",
                branch=self.branch_b,
                department=self.dept,
                designation=self.desig,
                joined_date=date(2024, 1, 1),
                status=EmployeeStatus.ACTIVE,
            )
            EmployeePayrollCalculation.objects.create(
                payroll_run=self.payroll_run,
                employee=extra_emp,
                gross_salary=Decimal("5000.00"),
                total_earnings=Decimal("5000.00"),
                total_deductions=Decimal("0.00"),
                net_payable=Decimal("5000.00"),
                bank_payable=Decimal("5000.00"),
                cash_payable=Decimal("0.00"),
                structure_snapshot={"components": []},
            )
        with CaptureQueriesContext(connection) as ctx_after:
            detail_resp_after = self.client.get(detail_url)
        self.assertEqual(detail_resp_after.status_code, 200)
        self.assertEqual(detail_resp_after.context["totals"]["gross"], Decimal("10000.00"))
        self.assertLessEqual(len(ctx_after), len(ctx_before))

        reg_resp = self.client.get(reverse("payroll:payroll_register", kwargs={"pk": self.payroll_run.pk}))
        self.assertEqual(reg_resp.status_code, 200)
        self.assertEqual(list(reg_resp.context["calculations"]), [self.calc_a])
        self.assertEqual(reg_resp.context["totals"]["tot_gross"], Decimal("10000.00"))

        # Branch A manager cannot view Branch B's payslip
        other_payslip = self.client.get(reverse("payroll:payslip_detail", kwargs={"pk": self.calc_b.pk}))
        self.assertEqual(other_payslip.status_code, 403)

        # Branch B employee can still view own payslip
        self.client.force_login(self.user_b)
        own_payslip = self.client.get(reverse("payroll:payslip_detail", kwargs={"pk": self.calc_b.pk}))
        self.assertEqual(own_payslip.status_code, 200)

    def test_3_restricted_scope_cannot_execute_full_run_mutations(self):
        self.client.force_login(self.user_a)

        sync_resp = self.client.post(reverse("payroll:payroll_run_sync", kwargs={"pk": self.payroll_run.pk}))
        trans_resp = self.client.post(
            reverse("payroll:payroll_run_transition", kwargs={"pk": self.payroll_run.pk}),
            data={"target_status": PayrollRunStatus.APPROVED_LOCKED},
        )
        self.assertEqual(sync_resp.status_code, 403)
        self.assertEqual(trans_resp.status_code, 403)

        self.payroll_run.refresh_from_db()
        self.assertEqual(self.payroll_run.status, PayrollRunStatus.DRAFT)

    def test_4_forged_cross_branch_employee_and_adjustment_ids_blocked(self):
        adj_b = PayrollAdjustment.objects.create(
            payroll_run=self.payroll_run,
            employee=self.emp_b,
            component=self.component,
            amount=Decimal("300.00"),
            type=SalaryComponentType.EARNING,
            reason="Branch B only",
            created_by=self.user_b,
        )
        self.client.force_login(self.user_a)

        forged_add = self.client.post(
            reverse("payroll:payroll_adjustment_add", kwargs={"pk": self.payroll_run.pk}),
            data={
                "employee_id": self.emp_b.pk,
                "component_id": self.component.pk,
                "amount": "999.00",
                "type": SalaryComponentType.EARNING,
            },
        )
        forged_del = self.client.post(
            reverse("payroll:payroll_adjustment_delete", kwargs={"pk": self.payroll_run.pk, "adj_pk": adj_b.pk})
        )
        self.assertEqual(forged_add.status_code, 403)
        self.assertEqual(forged_del.status_code, 403)
        self.assertTrue(PayrollAdjustment.objects.filter(pk=adj_b.pk).exists())
        self.assertEqual(PayrollAdjustment.objects.filter(payroll_run=self.payroll_run, employee=self.emp_b).count(), 1)

    def test_5_role_string_bypass_removed_when_permissions_revoked(self):
        self.client.force_login(self.revoked_hr_user)

        post_resp = self.client.post(reverse("payroll:payroll_run_sync", kwargs={"pk": self.payroll_run.pk}))
        export_resp = self.client.get(
            reverse("payroll:payroll_register_export", kwargs={"pk": self.payroll_run.pk, "format": "csv"})
        )
        self.assertEqual(post_resp.status_code, 403)
        self.assertEqual(export_resp.status_code, 403)

        locked_run = PayrollRun.objects.create(
            name="Locked Run",
            period_start=date(2024, 4, 1),
            period_end=date(2024, 4, 30),
            status=PayrollRunStatus.APPROVED_LOCKED,
        )
        with self.assertRaises(ValidationError):
            PayrollService.reverse_payroll_run(locked_run, self.revoked_hr_user, note="bypass attempt")
        locked_run.refresh_from_db()
        self.assertEqual(locked_run.status, PayrollRunStatus.APPROVED_LOCKED)
