import csv
import io
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
import openpyxl

from apps.accounts.rbac_models import Action, DataScope, Module, Permission, Role, RolePermission, UserRoleAssignment
from apps.attendance.models import Attendance
from apps.attendance.reporting_service import get_daily_report_data, get_monthly_report_data
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile, EmployeeStatus
from apps.leave.models import LeaveRequest, LeaveType

User = get_user_model()


class ReportConsistencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch_a = Branch.objects.create(name="Branch A", latitude=23.81, longitude=90.41, radius_meters=200)
        cls.branch_b = Branch.objects.create(name="Branch B", latitude=22.35, longitude=91.78, radius_meters=200)
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
        cls.admin = User.objects.create_superuser(email="rep_adm@example.com", password="pass", role="admin")
        cls.mgr = User.objects.create_user(email="rep_mgr@example.com", password="pass", role="manager")
        cls.mgr_emp = EmployeeProfile.objects.create(
            user=cls.mgr, employee_id="MGR-R1", full_name="Mgr R1", phone="01755550001", branch=cls.branch_a, is_active=True, joined_date=date(2024, 1, 1)
        )
        mod_att, _ = Module.objects.get_or_create(code="attendance", defaults={"name": "Attendance"})
        act_exp, _ = Action.objects.get_or_create(code="export", defaults={"name": "Export"})
        perm_att_exp, _ = Permission.objects.get_or_create(codename="attendance.export", defaults={"module": mod_att, "action": act_exp, "name": "Export Attendance"})
        role, _ = Role.objects.get_or_create(code="mgr_exp_test", defaults={"name": "MgrExpTest"})
        RolePermission.objects.get_or_create(role=role, permission=perm_att_exp, defaults={"data_scope": DataScope.BRANCH})
        UserRoleAssignment.objects.get_or_create(user=cls.mgr, role=role)

        u1 = User.objects.create_user(email="rep_e1@example.com", password="pass", role="staff")
        cls.emp_a = EmployeeProfile.objects.create(
            user=u1, employee_id="REP-A1", full_name="Rep Emp A", phone="01755550002", branch=cls.branch_a, is_active=True, joined_date=date(2024, 1, 1)
        )
        u2 = User.objects.create_user(email="rep_e2@example.com", password="pass", role="staff")
        cls.emp_b = EmployeeProfile.objects.create(
            user=u2, employee_id="REP-B1", full_name="Rep Emp B", phone="01755550003", branch=cls.branch_b, is_active=True, joined_date=date(2024, 1, 1)
        )

    def _workday(self, days_ago=1):
        d = timezone.localdate() - timedelta(days=days_ago)
        while d.weekday() == 4:
            d -= timedelta(days=1)
        return d

    def test_1_multi_session_open_field_expired_agreement(self):
        d = self._workday(1)
        t0 = timezone.make_aware(datetime.combine(d, datetime.strptime("09:00", "%H:%M").time()))
        Attendance.objects.create(employee=self.emp_a, date=d, attendance_type="check_in", type="office", status="on_time", check_in_time=t0, check_out_time=t0 + timedelta(hours=3), total_hours=3.0)
        Attendance.objects.create(employee=self.emp_a, date=d, attendance_type="check_in", type="office", status="on_time", check_in_time=t0 + timedelta(hours=2), check_out_time=t0 + timedelta(hours=5), total_hours=3.0)
        Attendance.objects.create(employee=self.emp_a, date=d, attendance_type="check_in", type="office", status="on_time", check_in_time=t0 + timedelta(hours=6), check_out_time=None)
        Attendance.objects.create(employee=self.emp_a, date=d, attendance_type="field_visit", type="field", status="on_time", check_in_time=t0 + timedelta(hours=7))
        Attendance.objects.create(employee=self.emp_a, date=d, attendance_type="check_in", type="office", status="late", check_in_time=t0 + timedelta(hours=8), check_out_time=t0 + timedelta(hours=9), is_expired=True)

        daily = get_daily_report_data(report_date=d, employee_id=self.emp_a.id)
        monthly = get_monthly_report_data(year=d.year, month=d.month, employee_id=self.emp_a.id)
        self.assertEqual(daily["present"], 1)
        self.assertEqual(daily["rows"][0]["total_hours"], 5.0)
        self.assertTrue(daily["rows"][0]["is_open_session"])
        self.assertEqual(monthly["rows"][0]["present"], 1)
        self.assertEqual(monthly["rows"][0]["total_hours"], 5.0)

        self.client.force_login(self.admin)
        for url_name, extra in (
            ("admin_panel:reports_export_csv", {"date": d.isoformat(), "employee": self.emp_a.id}),
            ("admin_panel:attendance_export", {"date_from": d.isoformat(), "date_to": d.isoformat(), "employee": self.emp_a.id, "format": "csv"}),
        ):
            resp = self.client.get(reverse(url_name), extra)
            self.assertEqual(resp.status_code, 200)
            rows = list(csv.reader(io.StringIO(resp.content.decode())))
            self.assertEqual(len(rows), 2)  # header + 1 deduplicated employee-day
            self.assertEqual(rows[1][6 if url_name.endswith("csv") else 7], "5.00")

        resp_xlsx = self.client.get(reverse("admin_panel:attendance_export"), {"date_from": d.isoformat(), "date_to": d.isoformat(), "employee": self.emp_a.id, "format": "xlsx"})
        wb = openpyxl.load_workbook(io.BytesIO(resp_xlsx.content))
        ws = wb.active
        self.assertEqual(ws.max_row, 5)
        self.assertEqual(ws["G5"].value, "—")
        self.assertEqual(float(ws["H5"].value), 5.0)

    def test_2_field_only_day_no_invented_checkout(self):
        d = self._workday(2)
        t0 = timezone.make_aware(datetime.combine(d, datetime.strptime("10:00", "%H:%M").time()))
        Attendance.objects.create(employee=self.emp_a, date=d, attendance_type="field_visit", type="field", status="on_time", check_in_time=t0)

        daily = get_daily_report_data(report_date=d, employee_id=self.emp_a.id)
        self.assertEqual(daily["present"], 1)
        self.assertEqual(daily["rows"][0]["check_out_time_str"], "")

        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_panel:attendance_export"), {"date_from": d.isoformat(), "date_to": d.isoformat(), "employee": self.emp_a.id, "format": "csv"})
        rows = list(csv.reader(io.StringIO(resp.content.decode())))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][6], "")  # Check Out empty

    def test_3_overnight_attendance_belongs_to_original_session(self):
        d = self._workday(3)
        ci = timezone.make_aware(datetime.combine(d, datetime.strptime("22:00", "%H:%M").time()))
        co = ci + timedelta(hours=8)
        Attendance.objects.create(employee=self.emp_a, date=d, attendance_type="check_in", type="office", status="on_time", check_in_time=ci, check_out_time=co, total_hours=8.0)

        daily = get_daily_report_data(report_date=d, employee_id=self.emp_a.id)
        self.assertEqual(daily["rows"][0]["total_hours"], 8.0)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("admin_panel:attendance_export"), {"date_from": d.isoformat(), "date_to": d.isoformat(), "employee": self.emp_a.id, "format": "csv"})
        rows = list(csv.reader(io.StringIO(resp.content.decode())))
        self.assertEqual(rows[1][7], "8.00")

    def test_4_equal_timestamps_and_leave_conflict(self):
        d = self._workday(4)
        t0 = timezone.make_aware(datetime.combine(d, datetime.strptime("09:00", "%H:%M").time()))
        Attendance.objects.create(employee=self.emp_a, date=d, attendance_type="check_in", type="office", status="on_time", check_in_time=t0, check_out_time=t0 + timedelta(hours=4), total_hours=4.0)
        Attendance.objects.create(employee=self.emp_a, date=d, attendance_type="check_in", type="office", status="on_time", check_in_time=t0, check_out_time=t0 + timedelta(hours=4), total_hours=4.0)
        lt = LeaveType.objects.create(name="Annual")
        LeaveRequest.objects.create(employee=self.emp_a, leave_type=lt, start_date=d, end_date=d, number_of_days=1, reason="Test", status="approved")

        daily = get_daily_report_data(report_date=d, employee_id=self.emp_a.id)
        self.assertEqual(daily["present"], 1)
        self.assertEqual(daily["rows"][0]["total_hours"], 4.0)
        self.assertTrue(daily["rows"][0]["has_leave_conflict"])

    def test_5_scope_and_bounded_export_queries(self):
        d = self._workday(1)
        self.client.force_login(self.mgr)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse("admin_panel:attendance_export"), {"date_from": d.isoformat(), "date_to": d.isoformat(), "format": "csv"})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn("REP-B1", body)
        self.assertLessEqual(len(ctx), 30)
