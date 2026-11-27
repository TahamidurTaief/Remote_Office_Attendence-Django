from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.engine import PermissionEngine
from apps.branches.models import Branch
from apps.employees.models import Department, Designation, Employee, EmployeeStatus
from apps.payroll.models import (
    EmployeePayrollCalculation,
    EmployeeSalaryAssignment,
    PaymentMode,
    PayrollAdjustment,
    PayrollRun,
    PayrollRunStatus,
    SalaryComponent,
    SalaryComponentType,
    SalaryComponentValueType,
    SalaryStructure,
    SalaryStructureComponent,
)
from apps.payroll.services import PayrollService

User = get_user_model()


class PayrollAdjustmentPolicyPreservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(email="adj_adm@example.com", password="pass", role="admin")
        cls.branch = Branch.objects.create(name="Adj Branch", latitude=23.81, longitude=90.41, radius_meters=200)
        cls.dept = Department.objects.create(name="Adj Dept", is_global=True)
        cls.desig = Designation.objects.create(name="Engineer")

        cls.emp = Employee.objects.create(
            employee_number="ADJ-001",
            first_name="Tariq",
            last_name="Policy",
            phone="01770000001",
            branch=cls.branch,
            department=cls.dept,
            designation=cls.desig,
            joined_date=date(2024, 1, 1),
            status=EmployeeStatus.ACTIVE,
            overtime_policy="standard",
        )
        cls.basic_comp = SalaryComponent.objects.create(
            name="Basic",
            code="BASIC_ADJ",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.PERCENTAGE,
            value=Decimal("100.00"),
            is_active=True,
        )
        cls.bonus_comp = SalaryComponent.objects.create(
            name="Spot Bonus",
            code="SPOT_ADJ",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.FIXED,
            value=Decimal("500.00"),
            is_active=True,
        )
        cls.structure = SalaryStructure.objects.create(name="Flat 100%", is_active=True)
        SalaryStructureComponent.objects.create(
            salary_structure=cls.structure,
            salary_component=cls.basic_comp,
            value_type=SalaryComponentValueType.PERCENTAGE,
            value=Decimal("100.00"),
        )
        EmployeeSalaryAssignment.objects.create(
            employee=cls.emp,
            salary_structure=cls.structure,
            gross_salary=Decimal("26000.00"),
            effective_from=date(2024, 1, 1),
            payment_mode=PaymentMode.BANK,
        )
        PermissionEngine.invalidate_user_cache(cls.admin)

    def _create_run_and_calc(self, divisor=26, ot_mult=Decimal("2.00"), absent_days=Decimal("2.00"), ot_hours=Decimal("4.00")):
        run = PayrollRun.objects.create(
            name="Policy Run",
            period_start=date(2024, 5, 1),
            period_end=date(2024, 5, 31),
            status=PayrollRunStatus.DRAFT,
            configuration_snapshot={"working_day_basis": "working_days", "overtime_multiplier": str(ot_mult)},
        )
        synced_at = timezone.now()
        calc = PayrollService.run_payroll_for_employee(
            payroll_run=run,
            employee=self.emp,
            unpaid_absent_days=absent_days,
            ot_hours=ot_hours,
            absence_divisor=divisor,
            ot_multiplier=ot_mult,
            synced_at=synced_at,
            source_total_present_days=Decimal("20.00"),
            source_total_approved_leave_days=Decimal("1.00"),
            source_total_approved_ot_hours=ot_hours,
        )
        return run, calc, synced_at

    def test_1_adding_adjustment_preserves_original_divisor_26_and_synced_inputs(self):
        run, calc, synced_at = self._create_run_and_calc(divisor=26, ot_mult=Decimal("1.50"), absent_days=Decimal("2.00"), ot_hours=Decimal("0.00"))
        # Expected: 26000 / 26 * 2 = 2000.00
        self.assertEqual(calc.absence_deduction, Decimal("2000.00"))

        self.client.force_login(self.admin)
        url = reverse("payroll:payroll_adjustment_add", kwargs={"pk": run.pk})
        with CaptureQueriesContext(connection) as ctx_single:
            resp = self.client.post(url, {
                "employee_id": self.emp.pk,
                "component_id": self.bonus_comp.pk,
                "amount": "500.00",
                "type": SalaryComponentType.EARNING,
                "reason": "Extra",
            })
        self.assertEqual(resp.status_code, 302)

        calc.refresh_from_db()
        self.assertEqual(calc.absence_deduction, Decimal("2000.00"))
        self.assertEqual(calc.total_earnings, Decimal("26500.00"))
        self.assertEqual(calc.net_payable, Decimal("24500.00"))
        self.assertEqual(calc.synced_at, synced_at)
        self.assertEqual(calc.source_total_present_days, Decimal("20.00"))

        # Adding unrelated employees/calculations to the run must not grow recalculation queries
        for i in range(3):
            other_emp = Employee.objects.create(
                employee_number=f"ADJ-X{i}",
                first_name="Other",
                last_name=f"E{i}",
                phone=f"0177999000{i}",
                branch=self.branch,
                department=self.dept,
                designation=self.desig,
                joined_date=date(2024, 1, 1),
                status=EmployeeStatus.ACTIVE,
            )
            EmployeePayrollCalculation.objects.create(
                payroll_run=run,
                employee=other_emp,
                gross_salary=Decimal("10000.00"),
                total_earnings=Decimal("10000.00"),
                total_deductions=Decimal("0.00"),
                net_payable=Decimal("10000.00"),
                bank_payable=Decimal("10000.00"),
                cash_payable=Decimal("0.00"),
                structure_snapshot={},
            )
        with CaptureQueriesContext(connection) as ctx_multi:
            resp2 = self.client.post(url, {
                "employee_id": self.emp.pk,
                "component_id": self.bonus_comp.pk,
                "amount": "100.00",
                "type": SalaryComponentType.EARNING,
                "reason": "Extra 2",
            })
        self.assertEqual(resp2.status_code, 302)
        self.assertLessEqual(len(ctx_multi), len(ctx_single))

    def test_2_deleting_adjustment_preserves_original_ot_multiplier_2_0(self):
        run, calc, _ = self._create_run_and_calc(divisor=26, ot_mult=Decimal("2.00"), absent_days=Decimal("0.00"), ot_hours=Decimal("4.00"))
        adj = PayrollAdjustment.objects.create(
            payroll_run=run,
            employee=self.emp,
            component=self.bonus_comp,
            amount=Decimal("500.00"),
            type=SalaryComponentType.EARNING,
            reason="Temp bonus",
            created_by=self.admin,
        )
        PayrollService.recalculate_employee_for_adjustment(run, self.emp, calc)
        calc.refresh_from_db()
        # OT = (26000 / (26 * 8)) * 2.00 * 4 = 1000.00
        self.assertEqual(calc.ot_amount, Decimal("1000.00"))
        self.assertEqual(calc.net_payable, Decimal("27500.00"))

        self.client.force_login(self.admin)
        del_url = reverse("payroll:payroll_adjustment_delete", kwargs={"pk": run.pk, "adj_pk": adj.pk})
        resp = self.client.post(del_url)
        self.assertEqual(resp.status_code, 302)

        calc.refresh_from_db()
        self.assertEqual(calc.ot_amount, Decimal("1000.00"))
        self.assertEqual(calc.net_payable, Decimal("27000.00"))

    def test_3_recalculation_failure_rolls_back_adjustment_creation(self):
        run, _calc, _ = self._create_run_and_calc()
        self.client.force_login(self.admin)
        url = reverse("payroll:payroll_adjustment_add", kwargs={"pk": run.pk})

        with patch.object(PayrollService, "run_payroll_for_employee", side_effect=RuntimeError("calc boom")):
            with self.assertRaises(RuntimeError):
                self.client.post(url, {
                    "employee_id": self.emp.pk,
                    "component_id": self.bonus_comp.pk,
                    "amount": "500.00",
                    "type": SalaryComponentType.EARNING,
                })

        self.assertEqual(PayrollAdjustment.objects.filter(payroll_run=run, employee=self.emp).count(), 0)

    def test_4_missing_policy_snapshot_rejected_before_mutation(self):
        run = PayrollRun.objects.create(
            name="No Snapshot Run",
            period_start=date(2024, 6, 1),
            period_end=date(2024, 6, 30),
            status=PayrollRunStatus.DRAFT,
            configuration_snapshot={},
        )
        EmployeePayrollCalculation.objects.create(
            payroll_run=run,
            employee=self.emp,
            gross_salary=Decimal("26000.00"),
            total_earnings=Decimal("26000.00"),
            total_deductions=Decimal("0.00"),
            net_payable=Decimal("26000.00"),
            bank_payable=Decimal("26000.00"),
            cash_payable=Decimal("0.00"),
            structure_snapshot={},
        )
        adj = PayrollAdjustment.objects.create(
            payroll_run=run,
            employee=self.emp,
            component=self.bonus_comp,
            amount=Decimal("100.00"),
            type=SalaryComponentType.EARNING,
            reason="Existing",
            created_by=self.admin,
        )

        self.client.force_login(self.admin)
        add_resp = self.client.post(reverse("payroll:payroll_adjustment_add", kwargs={"pk": run.pk}), {
            "employee_id": self.emp.pk,
            "component_id": self.bonus_comp.pk,
            "amount": "200.00",
            "type": SalaryComponentType.EARNING,
        })
        del_resp = self.client.post(reverse("payroll:payroll_adjustment_delete", kwargs={"pk": run.pk, "adj_pk": adj.pk}))

        self.assertEqual(add_resp.status_code, 400)
        self.assertEqual(del_resp.status_code, 400)
        self.assertEqual(PayrollAdjustment.objects.filter(payroll_run=run, employee=self.emp).count(), 1)

    def test_5_locked_run_rejects_adjustment_add_and_delete(self):
        run, _calc, _ = self._create_run_and_calc()
        adj = PayrollAdjustment.objects.create(
            payroll_run=run,
            employee=self.emp,
            component=self.bonus_comp,
            amount=Decimal("100.00"),
            type=SalaryComponentType.EARNING,
            reason="Before lock",
            created_by=self.admin,
        )
        run.status = PayrollRunStatus.APPROVED_LOCKED
        run.save(update_fields=["status"])

        self.client.force_login(self.admin)
        add_resp = self.client.post(reverse("payroll:payroll_adjustment_add", kwargs={"pk": run.pk}), {
            "employee_id": self.emp.pk,
            "component_id": self.bonus_comp.pk,
            "amount": "200.00",
            "type": SalaryComponentType.EARNING,
        })
        del_resp = self.client.post(reverse("payroll:payroll_adjustment_delete", kwargs={"pk": run.pk, "adj_pk": adj.pk}))

        self.assertEqual(add_resp.status_code, 400)
        self.assertEqual(del_resp.status_code, 400)
        self.assertEqual(PayrollAdjustment.objects.filter(payroll_run=run, employee=self.emp).count(), 1)
