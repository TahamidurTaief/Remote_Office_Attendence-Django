from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.attendance.models import Attendance, AttendanceAbsentLog
from apps.branches.models import Branch, Holiday, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile, EmployeeStatus
from apps.leave.models import LeaveBalance, LeaveRequest, LeaveType

User = get_user_model()


class MarkDailyAbsencesCanonicalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="Abs Branch", latitude=23.81, longitude=90.41, radius_meters=200)
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={"working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"]},
        )
        cls.dept = Department.objects.create(name="Ops", is_global=True)
        cls.desig = Designation.objects.create(name="Staff")

        cls.target_date = timezone.localdate() - timedelta(days=2)
        # Ensure target_date is not Friday
        if cls.target_date.weekday() == 4:
            cls.target_date -= timedelta(days=1)

    def _make_emp(self, code, joined_date, is_active=True):
        u = User.objects.create_user(email=f"{code.lower()}@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u,
            employee_number=code,
            first_name="Emp",
            last_name=code,
            phone=f"0175000{len(code):02d}{User.objects.count():02d}",
            branch=self.branch,
            department=self.dept,
            designation=self.desig,
            joined_date=joined_date,
            status=EmployeeStatus.ACTIVE,
        )
        p = EmployeeProfile.objects.create(
            user=u,
            master_employee=m,
            employee_id=code,
            full_name=f"Emp {code}",
            phone=m.phone,
            branch=self.branch,
            is_active=is_active,
            joined_date=joined_date,
        )
        return p

    def test_1_pre_joining_holiday_and_field_visit_never_deducted(self):
        LeaveType.objects.create(name="Default Casual", category="casual", is_default=True, default_days_per_year=10)
        # 1. Joined after target_date
        emp_future = self._make_emp("FUT", joined_date=self.target_date + timedelta(days=1))
        # 2. Field visit only on target_date
        emp_field = self._make_emp("FLD", joined_date=self.target_date - timedelta(days=30))
        Attendance.objects.create(
            employee=emp_field,
            date=self.target_date,
            attendance_type="field_visit",
            check_in_time=timezone.now() - timedelta(days=2),
            is_expired=False,
        )
        # 3. Branch holiday
        branch_hol = Branch.objects.create(name="Hol Branch", latitude=23.82, longitude=90.42, radius_meters=200)
        Holiday.objects.create(branch=branch_hol, date=self.target_date, name="Local Holiday")
        emp_hol = self._make_emp("HOL", joined_date=self.target_date - timedelta(days=30))
        emp_hol.branch = branch_hol
        emp_hol.save()
        if emp_hol.master_employee:
            emp_hol.master_employee.branch = branch_hol
            emp_hol.master_employee.save()

        out = StringIO()
        call_command("mark_daily_absences", date=self.target_date.isoformat(), stdout=out)

        self.assertEqual(AttendanceAbsentLog.objects.filter(date=self.target_date).count(), 0)
        self.assertEqual(LeaveBalance.objects.count(), 0)
        self.assertNotIn(emp_future.id, list(AttendanceAbsentLog.objects.values_list("employee_id", flat=True)))

    def test_2_dry_run_and_rerun_idempotency(self):
        lt = LeaveType.objects.create(name="Default Annual", category="annual", is_default=True, default_days_per_year=15)
        emp = self._make_emp("ABS1", joined_date=self.target_date - timedelta(days=30))

        # Dry run: predicts 1, writes 0
        dry_out = StringIO()
        call_command("mark_daily_absences", date=self.target_date.isoformat(), dry_run=True, stdout=dry_out)
        self.assertIn("would be marked absent: 1", dry_out.getvalue())
        self.assertEqual(AttendanceAbsentLog.objects.count(), 0)
        self.assertEqual(LeaveBalance.objects.count(), 0)

        # First execution: 1 log, 1.00 day deducted
        run_out_1 = StringIO()
        call_command("mark_daily_absences", date=self.target_date.isoformat(), stdout=run_out_1)
        self.assertIn("Total employees marked absent: 1", run_out_1.getvalue())
        self.assertEqual(AttendanceAbsentLog.objects.filter(employee=emp, date=self.target_date).count(), 1)
        bal = LeaveBalance.objects.get(employee=emp, leave_type=lt, year=self.target_date.year)
        self.assertEqual(bal.used_days, Decimal("1.00"))

        # Re-run: idempotent, still 1 log and 1.00 day deducted
        run_out_2 = StringIO()
        call_command("mark_daily_absences", date=self.target_date.isoformat(), stdout=run_out_2)
        self.assertIn("Total employees marked absent: 0", run_out_2.getvalue())
        self.assertEqual(AttendanceAbsentLog.objects.filter(employee=emp, date=self.target_date).count(), 1)
        bal.refresh_from_db()
        self.assertEqual(bal.used_days, Decimal("1.00"))

    def test_3_missing_explicit_default_policy_reports_review_needed_without_deduction(self):
        # Non-default leave types exist, but none has is_default=True
        LeaveType.objects.create(name="Casual Only", category="casual", is_default=False, default_days_per_year=10)
        emp = self._make_emp("NOPOL", joined_date=self.target_date - timedelta(days=30))

        out = StringIO()
        call_command("mark_daily_absences", date=self.target_date.isoformat(), stdout=out)
        output = out.getvalue()
        self.assertIn("Review needed: 1", output)
        self.assertIn("Total employees marked absent: 0", output)
        self.assertEqual(AttendanceAbsentLog.objects.filter(employee=emp).count(), 0)
        self.assertEqual(LeaveBalance.objects.count(), 0)

    def test_4_pending_or_returned_leave_and_unresolved_eligibility_require_review(self):
        lt = LeaveType.objects.create(name="Default Leave", category="casual", is_default=True, default_days_per_year=10)
        emp_pending = self._make_emp("PEND", joined_date=self.target_date - timedelta(days=30))
        LeaveRequest.objects.create(
            employee=emp_pending,
            leave_type=lt,
            start_date=self.target_date,
            end_date=self.target_date,
            reason="Pending medical",
            status="pending",
        )

        # Unresolved eligibility: master employee resigned without documented exit date
        emp_unresolved = self._make_emp("UNRES", joined_date=self.target_date - timedelta(days=30))
        emp_unresolved.master_employee.status = EmployeeStatus.RESIGNED
        emp_unresolved.master_employee.save(update_fields=["status"])
        EmployeeProfile.objects.filter(pk=emp_unresolved.pk).update(is_active=True)

        out = StringIO()
        call_command("mark_daily_absences", date=self.target_date.isoformat(), stdout=out)
        output = out.getvalue()
        self.assertIn("Review needed: 2", output)
        self.assertEqual(AttendanceAbsentLog.objects.count(), 0)
        self.assertEqual(LeaveBalance.objects.count(), 0)

    def test_5_no_partial_write_on_error_and_bounded_bulk_queries(self):
        lt = LeaveType.objects.create(name="Default Atomic", category="casual", is_default=True, default_days_per_year=10)
        emp = self._make_emp("ATOM", joined_date=self.target_date - timedelta(days=30))

        with patch.object(AttendanceAbsentLog.objects, "create", side_effect=RuntimeError("db failure")):
            out = StringIO()
            call_command("mark_daily_absences", date=self.target_date.isoformat(), stdout=out)

        # Transaction rolled back LeaveBalance creation/update
        self.assertEqual(AttendanceAbsentLog.objects.filter(employee=emp).count(), 0)
        self.assertEqual(LeaveBalance.objects.filter(employee=emp, leave_type=lt).count(), 0)

        # Query count stays bounded when adding non-absent (future-joined) employees
        with CaptureQueriesContext(connection) as ctx_before:
            call_command("mark_daily_absences", date=self.target_date.isoformat(), dry_run=True, stdout=StringIO())
        for i in range(5):
            self._make_emp(f"BULK{i}", joined_date=self.target_date + timedelta(days=2))
        with CaptureQueriesContext(connection) as ctx_after:
            call_command("mark_daily_absences", date=self.target_date.isoformat(), dry_run=True, stdout=StringIO())
        self.assertLessEqual(len(ctx_after), len(ctx_before))
