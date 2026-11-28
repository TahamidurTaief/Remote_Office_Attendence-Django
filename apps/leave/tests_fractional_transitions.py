from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from apps.attendance.models import AttendanceCoverageRecord, AttendancePolicy
from apps.branches.models import Branch, Holiday, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile
from apps.leave.forms import LeaveRequestForm
from apps.leave.models import LeaveBalance, LeaveRequest, LeaveType
from apps.payroll.models import EmployeeSalaryAssignment, PayrollRun, SalaryStructure
from apps.payroll.services import PayrollService

User = get_user_model()


class FractionalLeaveTransitionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Leave HQ",
            latitude=Decimal("23.810300"),
            longitude=Decimal("90.412500"),
        )
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": "09:00",
                "office_end_time": "18:00",
                "weekly_holidays": "5,6",
            },
        )
        AttendancePolicy.objects.create(branch=cls.branch, gps_required="optional", photo_required=False)

        cls.lt_casual = LeaveType.objects.create(
            name="Casual Fractional",
            category="casual",
            default_days_per_year=10,
            deduction_percent=Decimal("0.00"),
            is_active=True,
        )
        cls.lt_unpaid = LeaveType.objects.create(
            name="Unpaid Fractional",
            category="other",
            default_days_per_year=10,
            deduction_percent=Decimal("100.00"),
            is_active=True,
        )

        cls.user = User.objects.create_user(email="frac_emp@example.com", password="password123", role="staff")
        cls.master = Employee.objects.create(
            user=cls.user,
            employee_number="EMP-FRAC-01",
            first_name="Frac",
            last_name="Emp",
            joined_date=date(2026, 1, 1),
        )
        cls.prof = EmployeeProfile.objects.create(
            user=cls.user,
            master_employee=cls.master,
            employee_id="EMP-FRAC-01",
            full_name="Frac Emp",
            phone="01788888888",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2026, 1, 1),
        )
        sal = SalaryStructure.objects.create(name="Frac Standard")
        EmployeeSalaryAssignment.objects.create(
            employee=cls.master,
            salary_structure=sal,
            gross_salary=Decimal("30000.00"),
            effective_from=date(2026, 1, 1),
        )

    def test_1_exact_decimal_approval_cancellation_idempotency_and_delete(self):
        """used_days=2.00 + approved 0.5 -> 2.50; cancel -> 2.00; repeat cancel/approve idempotent; delete refunds once."""
        bal = LeaveBalance.objects.create(
            employee=self.prof,
            leave_type=self.lt_casual,
            year=2026,
            total_days=Decimal("10.00"),
            used_days=Decimal("2.00"),
        )
        d = date(2026, 11, 16)  # Monday
        lr = LeaveRequest.objects.create(
            employee=self.prof,
            leave_type=self.lt_casual,
            start_date=d,
            end_date=d,
            number_of_days=Decimal("0.5"),
            reason="Half day",
            status="pending",
        )
        lr.status = "approved"
        lr.save()
        bal.refresh_from_db()
        self.assertEqual(bal.used_days, Decimal("2.50"))
        self.assertEqual(bal.remaining_days, Decimal("7.50"))

        # Repeated approval is idempotent
        lr.save()
        bal.refresh_from_db()
        self.assertEqual(bal.used_days, Decimal("2.50"))

        # Cancel -> 2.00; repeated cancel -> remains 2.00
        lr.status = "cancelled"
        lr.save()
        bal.refresh_from_db()
        self.assertEqual(bal.used_days, Decimal("2.00"))

        lr.status = "cancelled"
        lr.save()
        bal.refresh_from_db()
        self.assertEqual(bal.used_days, Decimal("2.00"))

        # Re-approve and delete -> refunds 0.50 once
        lr.status = "approved"
        lr.save()
        bal.refresh_from_db()
        self.assertEqual(bal.used_days, Decimal("2.50"))

        lr.delete()
        bal.refresh_from_db()
        self.assertEqual(bal.used_days, Decimal("2.00"))

    def test_2_atomic_rollback_and_type_year_change_on_approved_request(self):
        """Changing leave_type/year reverts old balance and updates new; mid-save failure rolls back both."""
        d = date(2026, 11, 17)  # Tuesday
        lr = LeaveRequest.objects.create(
            employee=self.prof,
            leave_type=self.lt_casual,
            start_date=d,
            end_date=d,
            number_of_days=Decimal("0.5"),
            reason="Type switch",
            status="approved",
        )
        bal_casual = LeaveBalance.objects.get(employee=self.prof, leave_type=self.lt_casual, year=2026)
        self.assertEqual(bal_casual.used_days, Decimal("0.50"))

        # Switch leave_type on approved request
        lr.leave_type = self.lt_unpaid
        lr.save()
        bal_casual.refresh_from_db()
        bal_unpaid = LeaveBalance.objects.get(employee=self.prof, leave_type=self.lt_unpaid, year=2026)
        self.assertEqual(bal_casual.used_days, Decimal("0.00"))
        self.assertEqual(bal_unpaid.used_days, Decimal("0.50"))

        # Simulate DB failure on LeaveRequest.save -> both balances remain intact
        with patch("django.db.models.base.Model.save", side_effect=RuntimeError("db failure")):
            with self.assertRaises(RuntimeError):
                lr.leave_type = self.lt_casual
                lr.save()

        bal_casual.refresh_from_db()
        bal_unpaid.refresh_from_db()
        self.assertEqual(bal_casual.used_days, Decimal("0.00"))
        self.assertEqual(bal_unpaid.used_days, Decimal("0.50"))

    def test_3_holiday_half_day_zero_deduction_and_overlap_rejected(self):
        """Half-day on holiday is 0.00 deductible; overlapping approved request rejected in form."""
        hol_date = date(2026, 11, 18)  # Wednesday
        Holiday.objects.create(name="Midweek Holiday", date=hol_date, branch=self.branch)
        lr_hol = LeaveRequest.objects.create(
            employee=self.prof,
            leave_type=self.lt_casual,
            start_date=hol_date,
            end_date=hol_date,
            number_of_days=Decimal("0.5"),
            reason="Holiday",
            status="approved",
        )
        self.assertEqual(lr_hol.number_of_days, Decimal("0.00"))
        self.assertFalse(
            LeaveBalance.objects.filter(employee=self.prof, leave_type=self.lt_casual, year=2026, used_days__gt=0).exists()
        )

        # Overlap check on active working day
        work_date = date(2026, 11, 19)  # Thursday
        LeaveRequest.objects.create(
            employee=self.prof,
            leave_type=self.lt_casual,
            start_date=work_date,
            end_date=work_date,
            number_of_days=Decimal("0.5"),
            reason="First",
            status="approved",
        )
        form = LeaveRequestForm(
            data={
                "leave_type": self.lt_casual.id,
                "start_date": work_date.isoformat(),
                "end_date": work_date.isoformat(),
                "duration_type": "half",
                "reason": "Overlap",
            },
            employee=self.prof,
        )
        self.assertFalse(form.is_valid())

    def test_4_payroll_half_day_monetary_and_period_clipping(self):
        """Half-day inside period deducts 0.50 * daily_rate (no rounding to 1.00); outside period excluded."""
        d_in = date(2026, 11, 16)
        d_out = date(2026, 11, 23)
        AttendanceCoverageRecord.objects.create(
            employee=self.prof,
            date=d_in,
            coverage_state="complete",
            attestation_source="ingestion_reconciler",
        )
        LeaveRequest.objects.create(
            employee=self.prof,
            leave_type=self.lt_unpaid,
            start_date=d_in,
            end_date=d_in,
            number_of_days=Decimal("0.5"),
            reason="In period",
            status="approved",
        )
        LeaveRequest.objects.create(
            employee=self.prof,
            leave_type=self.lt_unpaid,
            start_date=d_out,
            end_date=d_out,
            number_of_days=Decimal("0.5"),
            reason="Out of period",
            status="approved",
        )

        run = PayrollRun.objects.create(
            name="Nov 16 Single Day",
            period_start=d_in,
            period_end=d_in,
        )
        PayrollService.sync_payroll_inputs(run)
        calc = run.calculations.get(employee=self.master)
        self.assertEqual(calc.unpaid_absent_days, Decimal("0.50"))
        # Daily rate for Nov (30 days): 30000 / 30 = 1000.00; 0.50 day deduction = 500.00
        self.assertEqual(calc.absence_deduction, Decimal("500.00"))


class LeaveBalanceMigrationPreservationTests(TransactionTestCase):
    migrate_from = [("leave", "0009_leavetype_deduction_percent")]
    migrate_to = [("leave", "0010_alter_leavebalance_total_days_and_more")]

    def test_5_migration_preserves_synthetic_integer_balances_and_uniqueness(self):
        """Migrating 0009 -> 0010 preserves existing integer balances (10 -> 10.00, 2 -> 2.00) and unique constraint."""
        u = User.objects.create_user(email="mig_leave@example.com", password="x", role="staff")
        b = Branch.objects.create(
            name="Mig Branch",
            latitude=Decimal("23.810300"),
            longitude=Decimal("90.412500"),
        )
        emp = EmployeeProfile.objects.create(
            user=u,
            employee_id="EMP-MIG-01",
            full_name="Mig Emp",
            phone="01799999999",
            branch=b,
            joined_date=date(2026, 1, 1),
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        LeaveTypeOld = old_apps.get_model("leave", "LeaveType")
        LeaveBalanceOld = old_apps.get_model("leave", "LeaveBalance")

        lt = LeaveTypeOld.objects.create(name="Mig Leave", default_days_per_year=10)
        LeaveBalanceOld.objects.create(employee_id=emp.id, leave_type=lt, year=2026, total_days=10, used_days=2)

        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        LeaveBalanceNew = new_apps.get_model("leave", "LeaveBalance")

        bal = LeaveBalanceNew.objects.get(employee_id=emp.id, leave_type_id=lt.id, year=2026)
        self.assertEqual(bal.total_days, Decimal("10.00"))
        self.assertEqual(bal.used_days, Decimal("2.00"))

        with self.assertRaises(IntegrityError):
            LeaveBalanceNew.objects.create(
                employee_id=emp.id, leave_type_id=lt.id, year=2026, total_days=Decimal("5.00"), used_days=Decimal("0.00")
            )
