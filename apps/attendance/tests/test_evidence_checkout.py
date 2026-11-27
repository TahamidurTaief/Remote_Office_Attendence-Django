import csv
import io
import time
import uuid
import decimal
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.admin_panel.views import ExportReportCSVView, ExportReportExcelView, ExportReportPDFView
from apps.attendance.lifecycle_service import AttendanceLifecycleService, AttendanceLifecycleError
from apps.attendance.models import Attendance, AttendanceAuditLog, AttendancePolicy, ForgotCheckoutRequest
from apps.attendance.reporting_service import (
    BD_TZ,
    calculate_employee_day,
    get_daily_report_data,
    get_monthly_report_data,
)
from apps.attendance.transaction_service import (
    AttendanceTransactionError,
    AttendanceTransactionService,
    auto_close_past_sessions,
)
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile
from apps.leave.models import LeaveRequest, LeaveType

User = get_user_model()


class EvidenceBasedCheckoutRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Dhaka HQ",
            latitude=decimal.Decimal("23.810300"),
            longitude=decimal.Decimal("90.412500"),
            radius_meters=200,
        )
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
            },
        )
        cls.policy = AttendancePolicy.objects.create(
            branch=cls.branch,
            geofencing_policy="warn",
            allow_outside_geofence=True,
            photo_required=False,
        )

        cls.admin_user = User.objects.create_superuser(
            email="admin_ev@example.com",
            password="password123",
        )
        cls.hr_user = User.objects.create_user(
            email="hr_ev@example.com",
            password="password123",
            role="hr",
        )
        cls.mgr_user = User.objects.create_user(
            email="mgr_ev@example.com",
            password="password123",
            role="staff",
        )
        cls.mgr_master = Employee.objects.create(
            user=cls.mgr_user,
            employee_number="MGR-EV-01",
            first_name="Reporting",
            last_name="Manager",
            joined_date=date(2024, 1, 1),
        )
        cls.mgr_profile = EmployeeProfile.objects.create(
            user=cls.mgr_user,
            master_employee=cls.mgr_master,
            employee_id="MGR-EV-01",
            full_name="Reporting Manager",
            phone="01700000010",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

        cls.staff_user = User.objects.create_user(
            email="staff_ev@example.com",
            password="password123",
            role="staff",
        )
        cls.staff_master = Employee.objects.create(
            user=cls.staff_user,
            employee_number="EMP-EV-01",
            first_name="Staff",
            last_name="One",
            joined_date=date(2025, 1, 1),
            reporting_manager=cls.mgr_master,
        )
        cls.staff_emp = EmployeeProfile.objects.create(
            user=cls.staff_user,
            master_employee=cls.staff_master,
            employee_id="EMP-EV-01",
            full_name="Staff One",
            phone="01700000011",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2025, 1, 1),
        )

        cls.other_user = User.objects.create_user(
            email="other_ev@example.com",
            password="password123",
            role="staff",
        )
        cls.other_master = Employee.objects.create(
            user=cls.other_user,
            employee_number="EMP-EV-02",
            first_name="Staff",
            last_name="Two",
            joined_date=date(2025, 1, 1),
        )
        cls.other_emp = EmployeeProfile.objects.create(
            user=cls.other_user,
            master_employee=cls.other_master,
            employee_id="EMP-EV-02",
            full_name="Staff Two",
            phone="01700000012",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2025, 1, 1),
        )
        cls.factory = RequestFactory()

    def _bd_dt(self, d, h, m=0, s=0):
        return datetime(d.year, d.month, d.day, h, m, s, tzinfo=BD_TZ)

    FROZEN_NOW = datetime(2026, 11, 25, 14, 0, 0, tzinfo=BD_TZ)

    def test_1_missing_checkout_no_fabrication_and_consistent_exports(self):
        """Scenario 1: Yesterday open check-in stays NULL after today check-in; Present + Missing Checkout."""
        with patch("django.utils.timezone.now", return_value=self.FROZEN_NOW):
            today = timezone.localdate()
            yesterday = today - timedelta(days=1)

            att_y = Attendance.objects.create(
                employee=self.staff_emp,
                date=yesterday,
                check_in_time=self._bd_dt(yesterday, 9, 0),
                attendance_type="check_in",
                type="office",
                status="on_time",
                check_out_time=None,
            )

            with patch("apps.attendance.transaction_service.notify_admins"):
                # auto_close_past_sessions must be a no-op
                self.assertEqual(auto_close_past_sessions(self.staff_emp, today), 0)

                res_t = AttendanceTransactionService.check_in(
                    self.staff_user,
                    {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "type": "office",
                    },
                    validate_photo=False,
                )
                self.assertTrue(res_t["success"])

            att_y.refresh_from_db()
            self.assertIsNone(att_y.check_out_time)
            self.assertIsNone(att_y.total_hours)

            daily = get_daily_report_data(report_date=yesterday, employee_id=self.staff_emp.id)
            row = daily["rows"][0]
            self.assertTrue(row["is_present"])
            self.assertFalse(row["is_absent"])
            self.assertTrue(row["is_open_session"])
            self.assertTrue(row["is_missing_checkout"])
            self.assertIsNone(row["check_out_time"])
            self.assertEqual(row["check_out_time_str"], "")
            self.assertIn("Missing Checkout", row["status_display"])

            # CSV export consistency
            req = self.factory.get(f"/admin-panel/reports/export/csv/?date={yesterday.isoformat()}&employee={self.staff_emp.id}")
            req.user = self.admin_user
            csv_resp = ExportReportCSVView.as_view()(req)
            self.assertEqual(csv_resp.status_code, 200)
            rows = list(csv.reader(io.StringIO(csv_resp.content.decode("utf-8-sig"))))
            self.assertEqual(rows[1][5], "")  # Check-out column empty

            # Excel & PDF export consistency
            req_xl = self.factory.get(f"/admin-panel/reports/export/excel/?date={yesterday.isoformat()}&employee={self.staff_emp.id}")
            req_xl.user = self.admin_user
            xl_resp = ExportReportExcelView.as_view()(req_xl)
            self.assertEqual(xl_resp.status_code, 200)
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(xl_resp.content))
            ws = wb.active
            self.assertEqual(ws.cell(row=5, column=6).value, "—")  # Out column empty ('—')

            req_pdf = self.factory.get(f"/admin-panel/reports/export/pdf/?date={yesterday.isoformat()}&employee={self.staff_emp.id}")
            req_pdf.user = self.admin_user
            self.assertEqual(ExportReportPDFView.as_view()(req_pdf).status_code, 200)

    def test_2_mixed_sessions_overlapping_and_field_visits(self):
        """Scenario 2: Closed + overlapping + open office sessions + field visits."""
        with patch("django.utils.timezone.now", return_value=self.FROZEN_NOW):
            d = timezone.localdate() - timedelta(days=2)
            s1 = Attendance.objects.create(
                employee=self.staff_emp,
                date=d,
                check_in_time=self._bd_dt(d, 9, 0),
                check_out_time=self._bd_dt(d, 13, 0),
                total_hours=decimal.Decimal("4.00"),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )
            # Overlapping session (11:00-15:00 -> merged [09:00, 15:00] = 6.00h, not 8.00h)
            s2 = Attendance.objects.create(
                employee=self.staff_emp,
                date=d,
                check_in_time=self._bd_dt(d, 11, 0),
                check_out_time=self._bd_dt(d, 15, 0),
                total_hours=decimal.Decimal("4.00"),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )
            # Open session on same day
            s3 = Attendance.objects.create(
                employee=self.staff_emp,
                date=d,
                check_in_time=self._bd_dt(d, 16, 0),
                check_out_time=None,
                attendance_type="check_in",
                type="office",
                status="on_time",
            )
            fv = Attendance.objects.create(
                employee=self.staff_emp,
                date=d,
                check_in_time=self._bd_dt(d, 17, 30),
                attendance_type="field_visit",
                type="field",
                status="on_time",
                visit_title="Client Site",
            )

            day_calc = calculate_employee_day(self.staff_emp, d, [s1, s2, s3, fv], today=timezone.localdate())
            self.assertTrue(day_calc["is_present"])
            self.assertEqual(day_calc["present_count"], 1)
            self.assertTrue(day_calc["is_open_session"])
            self.assertIsNone(day_calc["check_out_time"])
            self.assertIsNone(day_calc["last_departure"])
            self.assertEqual(day_calc["check_out_time_str"], "")
            self.assertEqual(day_calc["total_hours"], 6.0)
            self.assertEqual(day_calc["field_visit_count"], 1)

    def test_3_field_only_attendance_no_office_checkout_or_invented_duration(self):
        """Scenario 3: Standalone field visits count Present once, office checkout empty, 0.0h."""
        with patch("django.utils.timezone.now", return_value=self.FROZEN_NOW), \
             patch("apps.attendance.transaction_service.notify_admins"):
            visit_dt = timezone.localtime(timezone.now() - timedelta(hours=2), BD_TZ)
            d = visit_dt.date()
            res = AttendanceTransactionService.field_visit(
                self.staff_user,
                {
                    "latitude": 23.75,
                    "longitude": 90.39,
                    "visit_title": "Site Survey",
                    "client_name": "Acme",
                    "client_event_time": visit_dt.isoformat(),
                },
                validate_photo=False,
            )
            self.assertTrue(res["success"])

            daily = get_daily_report_data(report_date=d, employee_id=self.staff_emp.id)
            row = daily["rows"][0]
            self.assertTrue(row["is_present"])
            self.assertFalse(row["is_open_session"])
            self.assertFalse(row["is_missing_checkout"])
            self.assertIsNone(row["check_out_time"])
            self.assertEqual(row["check_out_time_str"], "")
            self.assertEqual(row["total_hours"], 0.0)
            self.assertEqual(len(row["field_visits"]), 1)

    def test_4_overnight_and_offline_retry_session_matching_and_idempotency(self):
        """Scenario 4: Overnight & offline retry match yesterday's session even after today's check-in."""
        with patch("django.utils.timezone.now", return_value=self.FROZEN_NOW), \
             patch("apps.attendance.transaction_service.notify_admins"):
            today = timezone.localdate()
            yesterday = today - timedelta(days=1)

            y_in = AttendanceTransactionService.check_in(
                self.staff_user,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "client_event_time": self._bd_dt(yesterday, 22, 0).isoformat(),
                },
                validate_photo=False,
            )
            t_in = AttendanceTransactionService.check_in(
                self.staff_user,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "client_event_time": self._bd_dt(today, 9, 0).isoformat(),
                },
                validate_photo=False,
            )

            retry_uuid = uuid.uuid4()
            # Overnight checkout at 02:00 today (before today's 09:00 check-in)
            out_res = AttendanceTransactionService.check_out(
                self.staff_user,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "client_event_time": self._bd_dt(today, 2, 0).isoformat(),
                    "sync_uuid": retry_uuid,
                },
                validate_photo=False,
            )
            self.assertTrue(out_res["success"])
            self.assertEqual(out_res["total_hours"], 4.0)

            # Idempotent retry
            out_retry = AttendanceTransactionService.check_out(
                self.staff_user,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "client_event_time": self._bd_dt(today, 2, 0).isoformat(),
                    "sync_uuid": retry_uuid,
                },
                validate_photo=False,
            )
            self.assertEqual(out_retry["total_hours"], 4.0)

            # Foreign employee using same sync_uuid is rejected
            with self.assertRaises(AttendanceTransactionError) as cm:
                AttendanceTransactionService.check_out(
                    self.other_user,
                    {"latitude": 23.8103, "longitude": 90.4125, "sync_uuid": retry_uuid},
                    validate_photo=False,
                )
            self.assertEqual(cm.exception.status_code, 400)

            y_att = Attendance.objects.get(pk=y_in["session_id"])
            t_att = Attendance.objects.get(pk=t_in["session_id"])
            self.assertEqual(y_att.check_out_time, self._bd_dt(today, 2, 0))
            self.assertIsNone(t_att.check_out_time)

    def test_5_authorized_forgot_checkout_and_audit_trail(self):
        """Scenario 5: Unauthorized approval blocked (403); manager+HR approval updates session & audit."""
        with patch("django.utils.timezone.now", return_value=self.FROZEN_NOW):
            yesterday = timezone.localdate() - timedelta(days=1)
            att = Attendance.objects.create(
                employee=self.staff_emp,
                date=yesterday,
                check_in_time=self._bd_dt(yesterday, 9, 0),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )
            sub = AttendanceLifecycleService.submit_forgot_checkout(
                self.staff_user,
                {
                    "attendance_id": att.id,
                    "reason": "Forgot to punch out",
                    "check_out_time": self._bd_dt(yesterday, 17, 30).isoformat(),
                },
            )
            self.assertTrue(sub["success"])
            req_obj = ForgotCheckoutRequest.objects.get(attendance=att)

            # Unauthorized user blocked
            with self.assertRaises(AttendanceLifecycleError) as cm:
                AttendanceLifecycleService.process_forgot_checkout(self.other_user, req_obj.id, "approve")
            self.assertEqual(cm.exception.status_code, 403)

            # Manager approves -> pending_hr
            r1 = AttendanceLifecycleService.process_forgot_checkout(self.mgr_user, req_obj.id, "approve")
            self.assertEqual(r1["status"], "pending_hr")

            # HR approves -> approved + audit log
            r2 = AttendanceLifecycleService.process_forgot_checkout(self.hr_user, req_obj.id, "approve")
            self.assertEqual(r2["status"], "approved")

            att.refresh_from_db()
            self.assertEqual(att.check_out_time, self._bd_dt(yesterday, 17, 30))
            self.assertEqual(float(att.total_hours), 8.5)
            self.assertTrue(AttendanceAuditLog.objects.filter(attendance=att, action="forgot_checkout").exists())

    def test_6_leave_conflict_and_pre_joining_boundaries(self):
        """Leave + attendance conflict flagged for HR; pre-joining date not marked Absent."""
        with patch("django.utils.timezone.now", return_value=self.FROZEN_NOW):
            pre_join = date(2024, 12, 1)
            dc_pre = calculate_employee_day(self.staff_emp, pre_join, [], today=timezone.localdate())
            self.assertFalse(dc_pre["is_absent"])
            self.assertEqual(dc_pre["absent_count"], 0)

            d = timezone.localdate() - timedelta(days=4)
            att = Attendance.objects.create(
                employee=self.staff_emp,
                date=d,
                check_in_time=self._bd_dt(d, 9, 0),
                check_out_time=self._bd_dt(d, 17, 0),
                total_hours=decimal.Decimal("8.00"),
                attendance_type="check_in",
                status="on_time",
            )
            dc_conflict = calculate_employee_day(self.staff_emp, d, [att], is_on_leave=True, today=timezone.localdate())
            self.assertTrue(dc_conflict["has_leave_conflict"])
            self.assertEqual(dc_conflict["present_count"], 1)
            self.assertEqual(dc_conflict["on_leave_count"], 0)

    def test_7_reporting_query_count_and_latency_no_n_plus_one(self):
        """Verify bulk reporting query counts remain bounded (no per-row queries)."""
        with patch("django.utils.timezone.now", return_value=self.FROZEN_NOW):
            d = timezone.localdate() - timedelta(days=5)
            with CaptureQueriesContext(connection) as ctx_daily:
                t0 = time.perf_counter()
                get_daily_report_data(report_date=d)
                daily_ms = (time.perf_counter() - t0) * 1000.0

            with CaptureQueriesContext(connection) as ctx_monthly:
                t1 = time.perf_counter()
                get_monthly_report_data(d.year, d.month)
                monthly_ms = (time.perf_counter() - t1) * 1000.0

            # Both must execute in small bounded query counts regardless of employee count
            self.assertLessEqual(len(ctx_daily), 15)
            self.assertLessEqual(len(ctx_monthly), 15)
            print(f"\n[BENCHMARK] Daily: {len(ctx_daily)} queries, {daily_ms:.2f}ms | Monthly: {len(ctx_monthly)} queries, {monthly_ms:.2f}ms")

    def test_8_cross_employee_session_id_and_ambiguous_offline_checkout_rejected(self):
        """Another employee's session_id and ambiguous multiple open sessions fail explicitly without mutation."""
        with patch("django.utils.timezone.now", return_value=self.FROZEN_NOW):
            today = timezone.localdate()
            d1 = today - timedelta(days=2)
            d2 = today - timedelta(days=1)

            s1 = Attendance.objects.create(
                employee=self.staff_emp,
                date=d1,
                check_in_time=self._bd_dt(d1, 9, 0),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )
            s2 = Attendance.objects.create(
                employee=self.staff_emp,
                date=d2,
                check_in_time=self._bd_dt(d2, 9, 0),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )

            # Foreign employee attempting checkout with staff_emp's session_id
            with self.assertRaises(AttendanceTransactionError) as cm_foreign:
                AttendanceTransactionService.check_out(
                    self.other_user,
                    {"latitude": 23.8103, "longitude": 90.4125, "session_id": s1.id},
                    validate_photo=False,
                )
            self.assertEqual(cm_foreign.exception.status_code, 400)
            s1.refresh_from_db()
            self.assertIsNone(s1.check_out_time)

            # Ambiguous offline checkout (2 older open sessions, no session_id)
            offline_dt = timezone.localtime(timezone.now() - timedelta(hours=2), BD_TZ)
            with self.assertRaises(AttendanceTransactionError) as cm_ambig:
                AttendanceTransactionService.check_out(
                    self.staff_user,
                    {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "client_event_time": offline_dt.isoformat(),
                    },
                    validate_photo=False,
                )
            self.assertEqual(cm_ambig.exception.status_code, 400)
            s1.refresh_from_db()
            s2.refresh_from_db()
            self.assertIsNone(s1.check_out_time)
            self.assertIsNone(s2.check_out_time)

    def test_9_checkout_notification_failure_after_commit_does_not_fail_punch(self):
        """Notification failure in on_commit during check_out does not fail or roll back committed punch."""
        with patch("django.utils.timezone.now", return_value=self.FROZEN_NOW):
            att = Attendance.objects.create(
                employee=self.staff_emp,
                date=timezone.localdate(),
                check_in_time=timezone.now() - timedelta(hours=2),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )
            with patch("apps.attendance.transaction_service.notify_admins", side_effect=RuntimeError("Push down")):
                res = AttendanceTransactionService.check_out(
                    self.staff_user,
                    {"latitude": 23.8103, "longitude": 90.4125, "session_id": att.id},
                    validate_photo=False,
                )
            self.assertTrue(res["success"])
            att.refresh_from_db()
            self.assertIsNotNone(att.check_out_time)

    def test_10_explicit_session_id_yesterday_retry_and_cross_employee_isolation(self):
        """Explicit session_id closes targeted yesterday session, preserves today's, rejects foreign/malformed IDs, and avoids duplicate writes."""
        from apps.attendance.models import AttendanceLocation

        with patch("django.utils.timezone.now", return_value=self.FROZEN_NOW), \
             patch("apps.attendance.transaction_service.notify_admins"):
            today = timezone.localdate()
            yesterday = today - timedelta(days=1)

            y_in = AttendanceTransactionService.check_in(
                self.staff_user,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "client_event_time": self._bd_dt(yesterday, 20, 0).isoformat(),
                },
                validate_photo=False,
            )
            t_in = AttendanceTransactionService.check_in(
                self.staff_user,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "client_event_time": self._bd_dt(today, 9, 0).isoformat(),
                },
                validate_photo=False,
            )

            # Foreign employee targeting staff_user's session_id is rejected
            with self.assertRaises(AttendanceTransactionError) as cm_foreign:
                AttendanceTransactionService.check_out(
                    self.other_user,
                    {"latitude": 23.8103, "longitude": 90.4125, "session_id": y_in["session_id"]},
                    validate_photo=False,
                )
            self.assertEqual(cm_foreign.exception.status_code, 400)

            # Malformed session_id is rejected with 400
            with self.assertRaises(AttendanceTransactionError) as cm_bad:
                AttendanceTransactionService.check_out(
                    self.staff_user,
                    {"latitude": 23.8103, "longitude": 90.4125, "session_id": "not-an-int"},
                    validate_photo=False,
                )
            self.assertEqual(cm_bad.exception.status_code, 400)

            checkout_uuid = uuid.uuid4()
            out_res = AttendanceTransactionService.check_out(
                self.staff_user,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "session_id": y_in["session_id"],
                    "client_event_time": self._bd_dt(today, 10, 0).isoformat(),
                    "sync_uuid": checkout_uuid,
                },
                validate_photo=False,
            )
            self.assertTrue(out_res["success"])
            self.assertEqual(out_res["total_hours"], 14.0)

            loc_count_before = AttendanceLocation.objects.filter(attendance_id=y_in["session_id"], event="check_out").count()
            self.assertEqual(loc_count_before, 1)

            # Repeated identical retry (with sync_uuid and with session_id only) produces no extra writes
            retry_uuid_res = AttendanceTransactionService.check_out(
                self.staff_user,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "session_id": y_in["session_id"],
                    "sync_uuid": checkout_uuid,
                },
                validate_photo=False,
            )
            retry_sid_res = AttendanceTransactionService.check_out(
                self.staff_user,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "session_id": y_in["session_id"],
                },
                validate_photo=False,
            )
            self.assertEqual(retry_uuid_res["total_hours"], 14.0)
            self.assertEqual(retry_sid_res["total_hours"], 14.0)
            self.assertEqual(
                AttendanceLocation.objects.filter(attendance_id=y_in["session_id"], event="check_out").count(),
                1,
            )

            y_att = Attendance.objects.get(pk=y_in["session_id"])
            t_att = Attendance.objects.get(pk=t_in["session_id"])
            self.assertEqual(y_att.check_out_time, self._bd_dt(today, 10, 0))
            self.assertIsNone(t_att.check_out_time)


class OfflineTimestampValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Dhaka Sync Branch",
            latitude=decimal.Decimal("23.810300"),
            longitude=decimal.Decimal("90.412500"),
            radius_meters=200,
        )
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
            },
        )
        AttendancePolicy.objects.create(
            branch=cls.branch,
            geofencing_policy="warn",
            allow_outside_geofence=True,
            photo_required=False,
        )
        cls.user = User.objects.create_user(
            email="sync_ts@example.com",
            password="password123",
            role="staff",
        )
        cls.emp = EmployeeProfile.objects.create(
            user=cls.user,
            employee_id="EMP-TS-01",
            full_name="Sync Tester",
            phone="01700000099",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2025, 1, 1),
        )

    def test_1_live_without_timestamp_uses_server_time_and_malformed_rejected(self):
        """Absent/blank client_event_time uses server time; malformed timestamp fails before writes."""
        from apps.attendance.models import AttendanceLocation

        frozen_now = datetime(2026, 11, 27, 16, 50, 0, tzinfo=ZoneInfo("UTC"))
        with patch("django.utils.timezone.now", return_value=frozen_now), \
             patch("apps.attendance.transaction_service.notify_admins"):
            # Malformed check_in, check_out, field_visit must all reject with 0 writes
            for fn in (
                AttendanceTransactionService.check_in,
                AttendanceTransactionService.check_out,
                AttendanceTransactionService.field_visit,
            ):
                with self.assertRaises(AttendanceTransactionError) as cm:
                    fn(
                        self.user,
                        {"latitude": 23.8103, "longitude": 90.4125, "client_event_time": "not-an-iso-time"},
                        validate_photo=False,
                    )
                self.assertEqual(cm.exception.status_code, 400)
            self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 0)
            self.assertEqual(AttendanceLocation.objects.count(), 0)

            # Live check-in without client_event_time succeeds using server time
            res = AttendanceTransactionService.check_in(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125},
                validate_photo=False,
            )
            self.assertTrue(res["success"])
            att = Attendance.objects.get(pk=res["session_id"])
            self.assertIsNone(att.client_event_time)

    def test_2_72h_stale_and_future_boundaries(self):
        """Exactly 72h old succeeds; 72h+1s old or 1s in future fails with zero writes."""
        from apps.attendance.models import AttendanceLocation

        frozen_now = datetime(2026, 11, 27, 16, 50, 0, tzinfo=ZoneInfo("UTC"))
        exact_72h = frozen_now - timedelta(hours=72)
        stale_72h_1s = frozen_now - timedelta(hours=72, seconds=1)
        future_1s = frozen_now + timedelta(seconds=1)

        with patch("django.utils.timezone.now", return_value=frozen_now), \
             patch("apps.attendance.transaction_service.notify_admins"):
            for bad_dt in (stale_72h_1s, future_1s):
                with self.assertRaises(AttendanceTransactionError) as cm:
                    AttendanceTransactionService.check_in(
                        self.user,
                        {"latitude": 23.8103, "longitude": 90.4125, "client_event_time": bad_dt.isoformat()},
                        validate_photo=False,
                    )
                self.assertEqual(cm.exception.status_code, 400)
            self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 0)
            self.assertEqual(AttendanceLocation.objects.count(), 0)

            res = AttendanceTransactionService.check_in(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125, "client_event_time": exact_72h.isoformat()},
                validate_photo=False,
            )
            self.assertTrue(res["success"])
            att = Attendance.objects.get(pk=res["session_id"])
            self.assertEqual(att.client_event_time, exact_72h)

    def test_3_overnight_dates_preserved_under_frozen_late_night_clock(self):
        """Frozen 22:50+06:00 clock still preserves yesterday 22:00 check-in and 02:00 overnight checkout."""
        frozen_now = datetime(2026, 11, 27, 22, 50, 0, tzinfo=BD_TZ)
        y_in_dt = datetime(2026, 11, 26, 22, 0, 0, tzinfo=BD_TZ)
        t_in_dt = datetime(2026, 11, 27, 9, 0, 0, tzinfo=BD_TZ)
        y_out_dt = datetime(2026, 11, 27, 2, 0, 0, tzinfo=BD_TZ)

        with patch("django.utils.timezone.now", return_value=frozen_now), \
             patch("apps.attendance.transaction_service.notify_admins"):
            y_in = AttendanceTransactionService.check_in(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125, "client_event_time": y_in_dt.isoformat()},
                validate_photo=False,
            )
            t_in = AttendanceTransactionService.check_in(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125, "client_event_time": t_in_dt.isoformat()},
                validate_photo=False,
            )
            y_out = AttendanceTransactionService.check_out(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125, "client_event_time": y_out_dt.isoformat()},
                validate_photo=False,
            )
            self.assertEqual(y_out["total_hours"], 4.0)
            self.assertEqual(Attendance.objects.get(pk=y_in["session_id"]).check_out_time, y_out_dt)
            self.assertIsNone(Attendance.objects.get(pk=t_in["session_id"]).check_out_time)

    def test_4_accepted_event_retried_after_expiry_returns_existing_without_writes(self):
        """An already accepted sync_uuid retried after 72h expiry returns original result with zero new writes."""
        from apps.attendance.models import AttendanceLocation

        t0 = datetime(2026, 11, 24, 9, 0, 0, tzinfo=BD_TZ)
        t_out = datetime(2026, 11, 24, 17, 0, 0, tzinfo=BD_TZ)
        t_expired = t0 + timedelta(hours=80)
        cin_uuid = uuid.uuid4()
        cout_uuid = uuid.uuid4()

        with patch("django.utils.timezone.now", return_value=t_out), \
             patch("apps.attendance.transaction_service.notify_admins"):
            in_res = AttendanceTransactionService.check_in(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125, "client_event_time": t0.isoformat(), "sync_uuid": cin_uuid},
                validate_photo=False,
            )
            out_res = AttendanceTransactionService.check_out(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125, "client_event_time": t_out.isoformat(), "sync_uuid": cout_uuid},
                validate_photo=False,
            )
            self.assertEqual(out_res["total_hours"], 8.0)

        with patch("django.utils.timezone.now", return_value=t_expired), \
             patch("apps.attendance.transaction_service.notify_admins"):
            in_retry = AttendanceTransactionService.check_in(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125, "client_event_time": t0.isoformat(), "sync_uuid": cin_uuid},
                validate_photo=False,
            )
            out_retry = AttendanceTransactionService.check_out(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125, "client_event_time": t_out.isoformat(), "sync_uuid": cout_uuid},
                validate_photo=False,
            )
            self.assertEqual(in_retry["session_id"], in_res["session_id"])
            self.assertEqual(out_retry["total_hours"], 8.0)
            self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 1)
            self.assertEqual(AttendanceLocation.objects.filter(attendance_id=in_res["session_id"]).count(), 2)

    def test_5_bulk_sync_marks_stale_timestamp_permanently_failed_for_client(self):
        """bulk_sync returns permanent=True failed status for stale/malformed timestamps and zero writes."""
        import json
        from django.urls import reverse

        frozen_now = datetime(2026, 11, 27, 16, 50, 0, tzinfo=ZoneInfo("UTC"))
        stale_dt = frozen_now - timedelta(hours=80)
        self.client.force_login(self.user)

        with patch("django.utils.timezone.now", return_value=frozen_now):
            res = self.client.post(
                reverse("attendance:bulk_sync"),
                json.dumps({
                    "items": [{
                        "uuid": str(uuid.uuid4()),
                        "action": "check_in",
                        "payload": {
                            "latitude": 23.8103,
                            "longitude": 90.4125,
                            "client_event_time": stale_dt.isoformat(),
                        },
                    }]
                }),
                content_type="application/json",
            )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["synced"], 0)
        self.assertEqual(body["results"][0]["status"], "failed")
        self.assertTrue(body["results"][0]["permanent"])
        self.assertIn("72-hour", body["results"][0]["error"])
        self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 0)

    def test_6_overnight_fixture_identical_at_0015_and_2330_bd_time(self):
        """Same overnight fixture executed at 00:15+06:00 (previous UTC date) and 23:30+06:00 gives identical result."""
        y_in_dt = datetime(2026, 11, 24, 22, 0, 0, tzinfo=BD_TZ)
        y_out_dt = datetime(2026, 11, 25, 0, 10, 0, tzinfo=BD_TZ)

        results = []
        for clock in (
            datetime(2026, 11, 25, 0, 15, 0, tzinfo=BD_TZ),   # UTC: 2026-11-24 18:15Z
            datetime(2026, 11, 25, 23, 30, 0, tzinfo=BD_TZ),  # UTC: 2026-11-25 17:30Z
        ):
            Attendance.objects.filter(employee=self.emp).delete()
            with patch("django.utils.timezone.now", return_value=clock), \
                 patch("apps.attendance.transaction_service.notify_admins"):
                ci = AttendanceTransactionService.check_in(
                    self.user,
                    {"latitude": 23.8103, "longitude": 90.4125, "client_event_time": y_in_dt.isoformat()},
                    validate_photo=False,
                )
                co = AttendanceTransactionService.check_out(
                    self.user,
                    {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "session_id": ci["session_id"],
                        "client_event_time": y_out_dt.isoformat(),
                    },
                    validate_photo=False,
                )
                att = Attendance.objects.get(pk=ci["session_id"])
                results.append((att.date, att.check_in_time, att.check_out_time, co["total_hours"]))

        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0], date(2026, 11, 24))
