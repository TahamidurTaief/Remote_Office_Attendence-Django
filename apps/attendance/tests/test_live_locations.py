from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

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
from apps.attendance.models import Attendance, AttendanceLocation
from apps.branches.models import Branch
from apps.employees.models import EmployeeLocationSync, EmployeeProfile

User = get_user_model()


class LiveLocationsScopeAndBoundsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch_a = Branch.objects.create(name="Live Branch A", latitude=23.81, longitude=90.41, radius_meters=200)
        cls.branch_b = Branch.objects.create(name="Live Branch B", latitude=23.82, longitude=90.42, radius_meters=200)

        mod_att, _ = Module.objects.get_or_create(code="attendance", defaults={"name": "Attendance"})
        act_view, _ = Action.objects.get_or_create(code="view", defaults={"name": "View"})
        perm_att, _ = Permission.objects.get_or_create(
            codename="attendance.view",
            defaults={"module": mod_att, "action": act_view, "name": "View Attendance"},
        )

        cls.user_a = User.objects.create_user(email="live_a@example.com", password="pass", role="staff")
        cls.prof_a = EmployeeProfile.objects.create(
            user=cls.user_a, employee_id="LIVE-A", full_name="Alice Live",
            phone="01740000001", branch=cls.branch_a, is_active=True, joined_date=date(2024, 1, 1),
        )

        cls.user_b = User.objects.create_user(email="live_b@example.com", password="pass", role="staff")
        cls.prof_b = EmployeeProfile.objects.create(
            user=cls.user_b, employee_id="LIVE-B", full_name="Bob Live",
            phone="01740000002", branch=cls.branch_b, is_active=True, joined_date=date(2024, 1, 1),
        )

        cls.no_emp_user = User.objects.create_user(email="live_noemp@example.com", password="pass", role="staff")

        cls.global_user = User.objects.create_user(email="live_global@example.com", password="pass", role="staff")

        role_own = Role.objects.create(code="live_own_view", name="Live Own View")
        RolePermission.objects.create(role=role_own, permission=perm_att, data_scope=DataScope.OWN)
        UserRoleAssignment.objects.create(user=cls.user_a, role=role_own)
        UserRoleAssignment.objects.create(user=cls.no_emp_user, role=role_own)

        role_global = Role.objects.create(code="live_global_view", name="Live Global View")
        RolePermission.objects.create(role=role_global, permission=perm_att, data_scope=DataScope.GLOBAL)
        UserRoleAssignment.objects.create(user=cls.global_user, role=role_global)

        for u in (cls.user_a, cls.user_b, cls.no_emp_user, cls.global_user):
            PermissionEngine.invalidate_user_cache(u)

    def test_1_own_scope_returns_only_own_location_with_private_cache_and_narrowing_filters(self):
        now = timezone.now()
        today = timezone.localdate()

        att_a = Attendance.objects.create(
            employee=self.prof_a, date=today, attendance_type="check_in",
            check_in_time=now, is_expired=False,
        )
        AttendanceLocation.objects.create(
            attendance=att_a, event="check_in", latitude=Decimal("23.810100"),
            longitude=Decimal("90.410100"), address="A CheckIn", accuracy=5.0, timestamp=now,
        )

        att_b = Attendance.objects.create(
            employee=self.prof_b, date=today, attendance_type="check_in",
            check_in_time=now, is_expired=False,
        )
        AttendanceLocation.objects.create(
            attendance=att_b, event="check_in", latitude=Decimal("23.820200"),
            longitude=Decimal("90.420200"), address="B Secret", accuracy=5.0, timestamp=now,
        )

        self.client.force_login(self.user_a)
        url = reverse("attendance:live_locations")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["employees"]), 1)
        self.assertEqual(data["employees"][0]["employee_id"], "LIVE-A")
        self.assertNotIn("Bob Live", resp.content.decode())
        self.assertIn("private", resp.get("Cache-Control", ""))
        self.assertIn("no-store", resp.get("Cache-Control", ""))

        # Forged/cross-branch filter can only narrow, never widen
        narrowed = self.client.get(url, {"branch_id": self.branch_b.pk})
        self.assertEqual(narrowed.status_code, 200)
        self.assertEqual(narrowed.json()["employees"], [])

    def test_2_hundreds_of_historical_syncs_bounded_queries_and_rows(self):
        now = timezone.now()
        today = timezone.localdate()
        Attendance.objects.create(
            employee=self.prof_a, date=today, attendance_type="check_in",
            check_in_time=now - timedelta(hours=2), is_expired=False,
        )
        for i in range(5):
            sync = EmployeeLocationSync.objects.create(
                employee=self.prof_a, latitude=Decimal("23.810000"),
                longitude=Decimal("90.410000"), address=f"Ping {i}",
            )
            EmployeeLocationSync.objects.filter(pk=sync.pk).update(
                timestamp=now - timedelta(minutes=10 - i)
            )

        self.client.force_login(self.global_user)
        url = reverse("attendance:live_locations")
        with CaptureQueriesContext(connection) as ctx_small:
            resp_small = self.client.get(url)
        self.assertEqual(resp_small.status_code, 200)

        # Add 150 historical syncs
        bulk_syncs = [
            EmployeeLocationSync(
                employee=self.prof_a, latitude=Decimal("23.810500"),
                longitude=Decimal("90.410500"), address=f"Hist {i}",
            )
            for i in range(150)
        ]
        EmployeeLocationSync.objects.bulk_create(bulk_syncs)
        latest_ping = EmployeeLocationSync.objects.create(
            employee=self.prof_a, latitude=Decimal("23.819999"),
            longitude=Decimal("90.419999"), address="Latest Ping",
        )
        EmployeeLocationSync.objects.filter(pk=latest_ping.pk).update(timestamp=now + timedelta(seconds=5))

        with CaptureQueriesContext(connection) as ctx_large:
            resp_large = self.client.get(url)
        self.assertEqual(resp_large.status_code, 200)
        payload = resp_large.json()
        self.assertEqual(len(payload["employees"]), 1)
        self.assertEqual(payload["employees"][0]["address"], "Latest Ping")
        self.assertLessEqual(len(ctx_large), len(ctx_small))

        # Verify bounded sync query uses IN (...) with only 1 ID, not scanning/returning all 156
        sync_queries = [q["sql"] for q in ctx_large if "employees_employeelocationsync" in q["sql"] and "IN (" in q["sql"]]
        self.assertEqual(len(sync_queries), 1)

    def test_3_no_canonical_employee_or_no_checkin_location_fails_safely(self):
        self.client.force_login(self.no_emp_user)
        resp_no_emp = self.client.get(reverse("attendance:live_locations"))
        self.assertEqual(resp_no_emp.status_code, 200)
        self.assertEqual(resp_no_emp.json()["employees"], [])

        # Active session with NO sync and NO check-in location
        now = timezone.now()
        Attendance.objects.create(
            employee=self.prof_a, date=timezone.localdate(), attendance_type="check_in",
            check_in_time=now, is_expired=False,
        )
        self.client.force_login(self.user_a)
        resp_no_loc = self.client.get(reverse("attendance:live_locations"))
        self.assertEqual(resp_no_loc.status_code, 200)
        self.assertEqual(resp_no_loc.json()["employees"], [])

    def test_4_overnight_and_multiple_active_sessions_return_single_latest_session(self):
        now = timezone.now()
        yesterday = timezone.localdate() - timedelta(days=1)

        # Older overnight active session
        att_old = Attendance.objects.create(
            employee=self.prof_a, date=yesterday, attendance_type="check_in",
            check_in_time=now - timedelta(hours=14), is_expired=False,
        )
        AttendanceLocation.objects.create(
            attendance=att_old, event="check_in", latitude=Decimal("23.811111"),
            longitude=Decimal("90.411111"), address="Overnight CheckIn", accuracy=5.0,
            timestamp=now - timedelta(hours=14),
        )

        # Second open session (more recent overnight/earlier today)
        att_newer = Attendance.objects.create(
            employee=self.prof_a, date=yesterday, attendance_type="check_in",
            check_in_time=now - timedelta(hours=10), is_expired=False,
        )
        AttendanceLocation.objects.create(
            attendance=att_newer, event="check_in", latitude=Decimal("23.812222"),
            longitude=Decimal("90.412222"), address="Latest Overnight CheckIn", accuracy=5.0,
            timestamp=now - timedelta(hours=10),
        )

        self.client.force_login(self.user_a)
        resp = self.client.get(reverse("attendance:live_locations"))
        self.assertEqual(resp.status_code, 200)
        emps = resp.json()["employees"]
        self.assertEqual(len(emps), 1)
        self.assertEqual(emps[0]["address"], "Latest Overnight CheckIn")
        self.assertTrue(emps[0]["is_overnight"])
        self.assertEqual(emps[0]["location_state"], "live")
