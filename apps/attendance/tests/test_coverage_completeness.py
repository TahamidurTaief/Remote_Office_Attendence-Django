import decimal
import io
from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.attendance.coverage_service import reconcile_attendance_coverage
from apps.attendance.models import (
    Attendance,
    AttendanceAbsentLog,
    AttendanceActivityLog,
    AttendanceCoverageRecord,
    AttendancePolicy,
    RetentionRunLog,
    SyncLog,
)
from apps.attendance.reporting_service import get_daily_report_data, get_monthly_report_data
from apps.attendance.transaction_service import AttendanceTransactionService
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile
from apps.leave.models import LeaveBalance, LeaveType
from apps.payroll.models import EmployeeSalaryAssignment, PayrollRun, SalaryStructure
from apps.payroll.services import PayrollService

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")
FROZEN_NOW = datetime(2026, 11, 27, 10, 0, 0, tzinfo=BD_TZ)
YESTERDAY = datetime(2026, 11, 26).date()


class CoverageCompletenessIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Cov HQ",
            latitude=decimal.Decimal("23.810300"),
            longitude=decimal.Decimal("90.412500"),
            radius_meters=200,
        )
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": time(9, 0),
                "office_end_time": time(18, 0),
                "late_after_minutes": 15,
            },
        )
        AttendancePolicy.objects.create(
            branch=cls.branch,
            gps_required="required",
            photo_required=False,
        )
        cls.leave_type = LeaveType.objects.create(
            name="Casual Leave",
            category="casual",
            default_days_per_year=10,
            is_active=True,
            is_default=True,
        )

        cls.u1 = User.objects.create_user(email="cov1@example.com", password="password123", role="staff")
        cls.m1 = Employee.objects.create(
            user=cls.u1,
            employee_number="EMP-COV-01",
            first_name="Cov",
            last_name="One",
            joined_date=datetime(2026, 10, 1).date(),
        )
        cls.e1 = EmployeeProfile.objects.create(
            user=cls.u1,
            master_employee=cls.m1,
            employee_id="EMP-COV-01",
            full_name="Cov One",
            phone="01777777771",
            branch=cls.branch,
            is_active=True,
            joined_date=datetime(2026, 10, 1).date(),
        )

        cls.u2 = User.objects.create_user(email="cov2@example.com", password="password123", role="staff")
        cls.m2 = Employee.objects.create(
            user=cls.u2,
            employee_number="EMP-COV-02",
            first_name="Cov",
            last_name="Two",
            joined_date=datetime(2026, 10, 1).date(),
        )
        cls.e2 = EmployeeProfile.objects.create(
            user=cls.u2,
            master_employee=cls.m2,
            employee_id="EMP-COV-02",
            full_name="Cov Two",
            phone="01777777772",
            branch=cls.branch,
            is_active=True,
            joined_date=datetime(2026, 10, 1).date(),
        )
        sal_struct = SalaryStructure.objects.create(name="Standard")
        EmployeeSalaryAssignment.objects.create(
            employee=cls.m1,
            salary_structure=sal_struct,
            gross_salary=decimal.Decimal("30000.00"),
            effective_from=datetime(2026, 10, 1).date(),
        )
        EmployeeSalaryAssignment.objects.create(
            employee=cls.m2,
            salary_structure=sal_struct,
            gross_salary=decimal.Decimal("30000.00"),
            effective_from=datetime(2026, 10, 1).date(),
        )

    def test_1_independent_coverage_producer_dry_run_and_anomaly_detection(self):
        """reconcile_coverage --dry-run writes 0 rows; real run marks clean days complete and failed syncs incomplete_activity."""
        SyncLog.objects.create(
            employee=self.e2,
            started_at=datetime(2026, 11, 26, 12, 0, 0, tzinfo=BD_TZ),
            completed_at=datetime(2026, 11, 26, 12, 5, 0, tzinfo=BD_TZ),
            records_total=1,
            records_success=0,
            records_failed=1,
            failure_reason="Network error",
        )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            dry_out = io.StringIO()
            call_command("reconcile_coverage", date=YESTERDAY.isoformat(), dry_run=True, stdout=dry_out)
            self.assertEqual(AttendanceCoverageRecord.objects.count(), 0)
            self.assertIn("complete=1", dry_out.getvalue())
            self.assertIn("incomplete_activity=1", dry_out.getvalue())

            out = io.StringIO()
            call_command("reconcile_coverage", date=YESTERDAY.isoformat(), stdout=out)

        rec1 = AttendanceCoverageRecord.objects.get(employee=self.e1, date=YESTERDAY)
        rec2 = AttendanceCoverageRecord.objects.get(employee=self.e2, date=YESTERDAY)
        self.assertEqual(rec1.coverage_state, "complete")
        self.assertEqual(rec1.attestation_source, "ingestion_reconciler")
        self.assertEqual(rec2.coverage_state, "incomplete_activity")

    def test_2_reports_and_mark_daily_absences_require_complete_coverage(self):
        """Unattested days stay history_unavailable; attested complete working days confirm absence & deduct leave."""
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            # Before coverage attestation -> history_unavailable, 0 deductions
            rep_unknown = get_daily_report_data(report_date=YESTERDAY)
            self.assertEqual(rep_unknown["rows"][0]["status"], "history_unavailable")

            # Attest e1 complete, leave e2 unattested
            AttendanceCoverageRecord.objects.create(
                employee=self.e1,
                date=YESTERDAY,
                coverage_state="complete",
                attestation_source="ingestion_reconciler",
            )

            dry_out = io.StringIO()
            call_command("mark_daily_absences", date=YESTERDAY.isoformat(), dry_run=True, stdout=dry_out)
            self.assertIn("would be marked absent: 1", dry_out.getvalue())
            self.assertEqual(AttendanceAbsentLog.objects.count(), 0)

            run_out = io.StringIO()
            call_command("mark_daily_absences", date=YESTERDAY.isoformat(), stdout=run_out)
            self.assertIn("marked absent: 1", run_out.getvalue())
            self.assertTrue(AttendanceAbsentLog.objects.filter(employee=self.e1, date=YESTERDAY).exists())
            self.assertFalse(AttendanceAbsentLog.objects.filter(employee=self.e2, date=YESTERDAY).exists())
            bal = LeaveBalance.objects.get(employee=self.e1, leave_type=self.leave_type, year=2026)
            self.assertEqual(bal.used_days, decimal.Decimal("1.00"))

    def test_3_late_offline_checkin_surfaces_reconciliation_without_financial_reversal(self):
        """Late offline check-in updates coverage to reconciled_late_offline and logs discrepancy without reversing LeaveBalance."""
        AttendanceCoverageRecord.objects.create(
            employee=self.e1,
            date=YESTERDAY,
            coverage_state="complete",
            attestation_source="ingestion_reconciler",
        )
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            call_command("mark_daily_absences", date=YESTERDAY.isoformat(), stdout=io.StringIO())

        bal_before = LeaveBalance.objects.get(employee=self.e1, leave_type=self.leave_type, year=2026).used_days

        late_ci_time = datetime(2026, 11, 26, 9, 10, 0, tzinfo=BD_TZ).isoformat()
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            res = AttendanceTransactionService.check_in(
                self.u1,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "accuracy": 20,
                    "client_event_time": late_ci_time,
                },
                validate_photo=False,
            )
            self.assertTrue(res["success"])

        cov = AttendanceCoverageRecord.objects.get(employee=self.e1, date=YESTERDAY)
        self.assertEqual(cov.coverage_state, "reconciled_late_offline")
        self.assertTrue(
            AttendanceActivityLog.objects.filter(employee=self.e1, action="late_offline_reconciliation").exists()
        )
        # Absence log & leave balance are NOT silently reversed
        self.assertTrue(AttendanceAbsentLog.objects.filter(employee=self.e1, date=YESTERDAY).exists())
        bal_after = LeaveBalance.objects.get(employee=self.e1, leave_type=self.leave_type, year=2026).used_days
        self.assertEqual(bal_before, bal_after)

    def test_4_payroll_preflight_blocks_unknown_and_late_discrepancy_but_allows_complete_and_purged(self):
        """Payroll preflight rejects mixed unknown/discrepancy periods and succeeds when all days are attested."""
        run = PayrollRun.objects.create(
            name="Nov 2026 Partial",
            period_start=YESTERDAY,
            period_end=YESTERDAY,
        )
        # Unknown coverage -> blocked
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            with self.assertRaises(ValidationError) as cm:
                PayrollService.sync_payroll_inputs(run)
            self.assertIn("unavailable or expired attendance history", str(cm.exception).lower())

        # Reconciled late offline + absent log -> blocked as discrepancy (attest e2 complete)
        AttendanceCoverageRecord.objects.create(
            employee=self.e2,
            date=YESTERDAY,
            coverage_state="complete",
            attestation_source="ingestion_reconciler",
        )
        AttendanceCoverageRecord.objects.create(
            employee=self.e1,
            date=YESTERDAY,
            coverage_state="reconciled_late_offline",
            attestation_source="late_offline_sync",
        )
        AttendanceAbsentLog.objects.create(employee=self.e1, date=YESTERDAY, leave_type_deducted=self.leave_type)
        Attendance.objects.create(
            employee=self.e1,
            date=YESTERDAY,
            check_in_time=datetime(2026, 11, 26, 9, 0, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 11, 26, 18, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
        )
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            with self.assertRaises(ValidationError) as cm_disc:
                PayrollService.sync_payroll_inputs(run)
            self.assertIn("late offline attendance", str(cm_disc.exception).lower())

        # Clean purged_retained with explicit pre_purge_status='absent' -> allowed
        AttendanceAbsentLog.objects.filter(employee=self.e1, date=YESTERDAY).delete()
        Attendance.objects.filter(employee=self.e1, date=YESTERDAY).delete()
        ret_run = RetentionRunLog.objects.create(
            cutoff_expired_date=YESTERDAY,
            cutoff_purged_before=FROZEN_NOW,
        )
        AttendanceCoverageRecord.objects.filter(employee=self.e1, date=YESTERDAY).update(
            coverage_state="purged_retained",
            pre_purge_status="absent",
            retention_run=ret_run,
        )
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            PayrollService.sync_payroll_inputs(run)
            e1_calc = run.calculations.get(employee=self.m1)
            self.assertEqual(e1_calc.unpaid_absent_days, decimal.Decimal("1.00"))

    def test_5_monthly_report_uses_bounded_coverage_query(self):
        """get_monthly_report_data loads coverage in 1 bounded query without N+1 per employee/day."""
        AttendanceCoverageRecord.objects.create(
            employee=self.e1,
            date=YESTERDAY,
            coverage_state="complete",
            attestation_source="ingestion_reconciler",
        )
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "django.utils.timezone.localdate", return_value=FROZEN_NOW.date()
        ):
            with CaptureQueriesContext(connection) as ctx:
                res = get_monthly_report_data(year=2026, month=11, employee_queryset=EmployeeProfile.objects.filter(id=self.e1.id))
            cov_queries = [q["sql"] for q in ctx if "attendance_attendancecoveragerecord" in q["sql"].lower()]
            self.assertEqual(len(cov_queries), 1)
            self.assertGreaterEqual(res["rows"][0]["absent"], 1)
