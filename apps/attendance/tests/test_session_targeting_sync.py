import decimal
import json
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import Attendance, AttendanceLocation, AttendancePolicy
from apps.attendance.transaction_service import (
    AttendanceTransactionError,
    AttendanceTransactionService,
)
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")
FIXED_NOW = datetime(2026, 11, 28, 18, 0, 0, tzinfo=BD_TZ)


class SessionTargetingAndOfflineSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Targeting HQ",
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

        cls.staff_user = User.objects.create_user(
            email="staff_target@example.com",
            password="password123",
            role="staff",
        )
        cls.staff_master = Employee.objects.create(
            user=cls.staff_user,
            employee_number="EMP-TGT-01",
            first_name="Target",
            last_name="One",
            joined_date=date(2025, 1, 1),
        )
        cls.staff_emp = EmployeeProfile.objects.create(
            user=cls.staff_user,
            master_employee=cls.staff_master,
            employee_id="EMP-TGT-01",
            full_name="Target One",
            phone="01711111111",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2025, 1, 1),
        )

        cls.other_user = User.objects.create_user(
            email="other_target@example.com",
            password="password123",
            role="staff",
        )
        cls.other_master = Employee.objects.create(
            user=cls.other_user,
            employee_number="EMP-TGT-02",
            first_name="Other",
            last_name="Two",
            joined_date=date(2025, 1, 1),
        )
        cls.other_emp = EmployeeProfile.objects.create(
            user=cls.other_user,
            master_employee=cls.other_master,
            employee_id="EMP-TGT-02",
            full_name="Other Two",
            phone="01722222222",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2025, 1, 1),
        )

    def _bd_dt(self, d, hour, minute=0):
        return datetime(d.year, d.month, d.day, hour, minute, 0, tzinfo=BD_TZ)

    # Group 1: Online explicit-session propagation and ownership
    def test_group_1_online_explicit_session_propagation_and_ownership(self):
        with patch("django.utils.timezone.now", return_value=FIXED_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            today = FIXED_NOW.date()
            session = Attendance.objects.create(
                employee=self.staff_emp,
                date=today,
                check_in_time=self._bd_dt(today, 9, 0),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )
            other_session = Attendance.objects.create(
                employee=self.other_emp,
                date=today,
                check_in_time=self._bd_dt(today, 9, 15),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )

            self.client.force_login(self.staff_user)

            # Template renders explicit ACTIVE_SESSION_ID and propagates session_id
            page_resp = self.client.get(reverse("staff:check_in"))
            self.assertEqual(page_resp.status_code, 200)
            self.assertContains(page_resp, f"const ACTIVE_SESSION_ID = {session.id};")
            self.assertContains(page_resp, "formData.append('session_id', targetSessionId)")

            # Status API returns active_session_id
            status_resp = self.client.get(reverse("attendance:status"))
            self.assertEqual(status_resp.status_code, 200)
            self.assertEqual(status_resp.json()["active_session_id"], session.id)

            # Foreign employee cannot close staff_emp's session
            self.client.force_login(self.other_user)
            foreign_resp = self.client.post(
                reverse("attendance:check_out"),
                data={
                    "latitude": "23.8103",
                    "longitude": "90.4125",
                    "session_id": str(session.id),
                },
            )
            self.assertEqual(foreign_resp.status_code, 400)
            session.refresh_from_db()
            self.assertIsNone(session.check_out_time)

            # Owner closes their own session explicitly
            self.client.force_login(self.staff_user)
            ok_resp = self.client.post(
                reverse("attendance:check_out"),
                data={
                    "latitude": "23.8103",
                    "longitude": "90.4125",
                    "session_id": str(session.id),
                    "sync_uuid": str(uuid.uuid4()),
                },
            )
            self.assertEqual(ok_resp.status_code, 200)
            payload = ok_resp.json()
            self.assertTrue(payload["success"])
            self.assertEqual(payload["session_id"], session.id)

            session.refresh_from_db()
            other_session.refresh_from_db()
            self.assertIsNotNone(session.check_out_time)
            self.assertIsNone(other_session.check_out_time)

    # Group 2: Offline check-in -> checkout dependency via bulk_sync
    def test_group_2_offline_checkin_to_checkout_dependency_via_bulk_sync(self):
        with patch("django.utils.timezone.now", return_value=FIXED_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            today = FIXED_NOW.date()
            ci_uuid = str(uuid.uuid4())
            co_uuid = str(uuid.uuid4())
            unrelated = Attendance.objects.create(
                employee=self.other_emp,
                date=today,
                check_in_time=self._bd_dt(today, 9, 0),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )

            self.client.force_login(self.staff_user)
            # Send checkout before check_in in raw list to verify server-side ordering + resolution
            batch = {
                "items": [
                    {
                        "uuid": co_uuid,
                        "action": "check_out",
                        "depends_on_sync_uuid": ci_uuid,
                        "payload": {
                            "latitude": 23.8103,
                            "longitude": 90.4125,
                            "accuracy": 10,
                            "address": "HQ",
                            "client_event_time": self._bd_dt(today, 17, 30).isoformat(),
                            "depends_on_sync_uuid": ci_uuid,
                        },
                    },
                    {
                        "uuid": ci_uuid,
                        "action": "check_in",
                        "payload": {
                            "latitude": 23.8103,
                            "longitude": 90.4125,
                            "accuracy": 10,
                            "address": "HQ",
                            "type": "office",
                            "client_event_time": self._bd_dt(today, 9, 0).isoformat(),
                        },
                    },
                ]
            }

            with CaptureQueriesContext(connection) as ctx:
                resp = self.client.post(
                    reverse("attendance:bulk_sync"),
                    data=json.dumps(batch),
                    content_type="application/json",
                )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["synced"], 2)
            # Single HTTP bulk-sync request; bounded query count (no full-history scans or per-item polling)
            self.assertLess(len(ctx), 65)

            ci_res = next(r for r in data["results"] if r["uuid"] == ci_uuid)
            co_res = next(r for r in data["results"] if r["uuid"] == co_uuid)
            self.assertEqual(ci_res["status"], "success")
            self.assertEqual(co_res["status"], "success")
            resolved_id = ci_res["data"]["session_id"]
            self.assertEqual(co_res["data"]["session_id"], resolved_id)

            att = Attendance.objects.get(pk=resolved_id)
            self.assertEqual(str(att.sync_uuid), ci_uuid)
            self.assertEqual(att.check_out_time, self._bd_dt(today, 17, 30))
            self.assertEqual(Attendance.objects.filter(employee=self.staff_emp).count(), 1)
            self.assertEqual(AttendanceLocation.objects.filter(attendance=att).count(), 2)
            unrelated.refresh_from_db()
            self.assertIsNone(unrelated.check_out_time)

    # Group 3: Yesterday/today session isolation
    def test_group_3_yesterday_today_session_isolation(self):
        with patch("django.utils.timezone.now", return_value=FIXED_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            today = FIXED_NOW.date()
            yesterday = today - timedelta(days=1)

            session_a = Attendance.objects.create(
                employee=self.staff_emp,
                date=yesterday,
                check_in_time=self._bd_dt(yesterday, 9, 0),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )
            session_b = Attendance.objects.create(
                employee=self.staff_emp,
                date=today,
                check_in_time=self._bd_dt(today, 9, 0),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )

            self.client.force_login(self.staff_user)
            co_uuid = str(uuid.uuid4())
            resp = self.client.post(
                reverse("attendance:bulk_sync"),
                data=json.dumps(
                    {
                        "items": [
                            {
                                "uuid": co_uuid,
                                "action": "check_out",
                                "payload": {
                                    "session_id": session_a.id,
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "client_event_time": self._bd_dt(yesterday, 18, 0).isoformat(),
                                },
                            }
                        ]
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["synced"], 1)

            session_a.refresh_from_db()
            session_b.refresh_from_db()
            self.assertEqual(session_a.check_out_time, self._bd_dt(yesterday, 18, 0))
            self.assertIsNone(session_b.check_out_time)
            self.assertEqual(Attendance.objects.filter(employee=self.staff_emp).count(), 2)
            self.assertEqual(
                AttendanceLocation.objects.filter(attendance=session_a, event="check_out").count(),
                1,
            )
            self.assertEqual(
                AttendanceLocation.objects.filter(attendance=session_b, event="check_out").count(),
                0,
            )

    # Group 4: Lost-response retries with stable UUIDs and no duplicate writes
    def test_group_4_lost_response_retries_stable_uuids_no_duplicates(self):
        with patch("django.utils.timezone.now", return_value=FIXED_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            today = FIXED_NOW.date()
            ci_uuid = str(uuid.uuid4())
            co_uuid = str(uuid.uuid4())

            # Step 1: check_in succeeded on server, response lost before client persisted it
            ci_res = AttendanceTransactionService.check_in(
                self.staff_user,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "sync_uuid": ci_uuid,
                    "client_event_time": self._bd_dt(today, 9, 0).isoformat(),
                },
                validate_photo=False,
            )

            self.client.force_login(self.staff_user)
            # Step 2: client retries full batch [check_in, dependent check_out] with identical UUIDs
            retry_batch = {
                "items": [
                    {
                        "uuid": ci_uuid,
                        "action": "check_in",
                        "payload": {
                            "latitude": 23.8103,
                            "longitude": 90.4125,
                            "client_event_time": self._bd_dt(today, 9, 0).isoformat(),
                        },
                    },
                    {
                        "uuid": co_uuid,
                        "action": "check_out",
                        "depends_on_sync_uuid": ci_uuid,
                        "payload": {
                            "latitude": 23.8103,
                            "longitude": 90.4125,
                            "client_event_time": self._bd_dt(today, 17, 0).isoformat(),
                            "depends_on_sync_uuid": ci_uuid,
                        },
                    },
                ]
            }

            r1 = self.client.post(
                reverse("attendance:bulk_sync"),
                data=json.dumps(retry_batch),
                content_type="application/json",
            )
            r2 = self.client.post(
                reverse("attendance:bulk_sync"),
                data=json.dumps(retry_batch),
                content_type="application/json",
            )
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(r1.json()["synced"], 2)
            self.assertEqual(r2.json()["synced"], 2)

            self.assertEqual(Attendance.objects.filter(employee=self.staff_emp).count(), 1)
            att = Attendance.objects.get(pk=ci_res["session_id"])
            self.assertEqual(AttendanceLocation.objects.filter(attendance=att, event="check_in").count(), 1)
            self.assertEqual(AttendanceLocation.objects.filter(attendance=att, event="check_out").count(), 1)

    # Group 5: Permanent dependency failure + safe legacy-client handling
    def test_group_5_permanent_dependency_failure_and_legacy_client_safety(self):
        with patch("django.utils.timezone.now", return_value=FIXED_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            today = FIXED_NOW.date()
            yesterday = today - timedelta(days=1)

            # Existing open session A (yesterday) and B (today)
            sess_a = Attendance.objects.create(
                employee=self.staff_emp,
                date=yesterday,
                check_in_time=self._bd_dt(yesterday, 9, 0),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )
            sess_b = Attendance.objects.create(
                employee=self.staff_emp,
                date=today,
                check_in_time=self._bd_dt(today, 9, 0),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )

            self.client.force_login(self.staff_user)
            # Failed check_in C (already checked in -> 400 permanent) + dependent checkout C
            bad_ci_uuid = str(uuid.uuid4())
            dep_co_uuid = str(uuid.uuid4())
            resp = self.client.post(
                reverse("attendance:bulk_sync"),
                data=json.dumps(
                    {
                        "items": [
                            {
                                "uuid": bad_ci_uuid,
                                "action": "check_in",
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "client_event_time": self._bd_dt(today, 10, 0).isoformat(),
                                },
                            },
                            {
                                "uuid": dep_co_uuid,
                                "action": "check_out",
                                "depends_on_sync_uuid": bad_ci_uuid,
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "client_event_time": self._bd_dt(today, 17, 0).isoformat(),
                                    "depends_on_sync_uuid": bad_ci_uuid,
                                },
                            },
                        ]
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            results = resp.json()["results"]
            co_res = next(r for r in results if r["uuid"] == dep_co_uuid)
            self.assertEqual(co_res["status"], "failed")
            self.assertTrue(co_res["blocked"])
            self.assertTrue(co_res["permanent"])

            # Neither sess_a nor sess_b was touched
            sess_a.refresh_from_db()
            sess_b.refresh_from_db()
            self.assertIsNone(sess_a.check_out_time)
            self.assertIsNone(sess_b.check_out_time)

            # Legacy no-session_id online checkout when 2 open sessions exist -> rejected with 400
            with self.assertRaises(AttendanceTransactionError) as cm_online_ambig:
                AttendanceTransactionService.check_out(
                    self.staff_user,
                    {"latitude": 23.8103, "longitude": 90.4125},
                    validate_photo=False,
                )
            self.assertEqual(cm_online_ambig.exception.status_code, 400)

            # Malformed / foreign / missing session_id while multiple sessions exist -> rejected
            for bad_sid in ["abc", -1, True, 999999]:
                with self.assertRaises(AttendanceTransactionError):
                    AttendanceTransactionService.check_out(
                        self.staff_user,
                        {"latitude": 23.8103, "longitude": 90.4125, "session_id": bad_sid},
                        validate_photo=False,
                    )

            # Single open session legacy client (other_emp has only 1 open session) -> provably unambiguous
            other_open = Attendance.objects.create(
                employee=self.other_emp,
                date=today,
                check_in_time=self._bd_dt(today, 9, 0),
                attendance_type="check_in",
                type="office",
                status="on_time",
            )
            legacy_ok = AttendanceTransactionService.check_out(
                self.other_user,
                {"latitude": 23.8103, "longitude": 90.4125},
                validate_photo=False,
            )
            self.assertTrue(legacy_ok["success"])
            self.assertEqual(legacy_ok["session_id"], other_open.id)

    # Group 6: Multi-session In-A -> Out-A -> In-B -> Out-B (chronological & shuffled)
    def test_group_6_multi_session_chronological_and_shuffled_bulk_sync(self):
        with patch("django.utils.timezone.now", return_value=FIXED_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            today = FIXED_NOW.date()
            in_a = str(uuid.uuid4())
            out_a = str(uuid.uuid4())
            in_b = str(uuid.uuid4())
            out_b = str(uuid.uuid4())

            self.client.force_login(self.staff_user)
            ordered_items = [
                {
                    "uuid": in_a,
                    "action": "check_in",
                    "payload": {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "type": "office",
                        "client_event_time": self._bd_dt(today, 9, 0).isoformat(),
                    },
                },
                {
                    "uuid": out_a,
                    "action": "check_out",
                    "depends_on_sync_uuid": in_a,
                    "payload": {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "depends_on_sync_uuid": in_a,
                        "client_event_time": self._bd_dt(today, 12, 0).isoformat(),
                    },
                },
                {
                    "uuid": in_b,
                    "action": "check_in",
                    "payload": {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "type": "office",
                        "client_event_time": self._bd_dt(today, 13, 0).isoformat(),
                    },
                },
                {
                    "uuid": out_b,
                    "action": "check_out",
                    "depends_on_sync_uuid": in_b,
                    "payload": {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "depends_on_sync_uuid": in_b,
                        "client_event_time": self._bd_dt(today, 18, 0).isoformat(),
                    },
                },
            ]

            with CaptureQueriesContext(connection) as ctx:
                resp = self.client.post(
                    reverse("attendance:bulk_sync"),
                    data=json.dumps({"items": ordered_items}),
                    content_type="application/json",
                )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["synced"], 4)
            self.assertLess(len(ctx), 110)

            res_by_uuid = {r["uuid"]: r for r in data["results"]}
            sess_a_id = res_by_uuid[in_a]["data"]["session_id"]
            sess_b_id = res_by_uuid[in_b]["data"]["session_id"]
            self.assertNotEqual(sess_a_id, sess_b_id)
            self.assertEqual(res_by_uuid[out_a]["data"]["session_id"], sess_a_id)
            self.assertEqual(res_by_uuid[out_b]["data"]["session_id"], sess_b_id)

            sess_a = Attendance.objects.get(pk=sess_a_id)
            sess_b = Attendance.objects.get(pk=sess_b_id)
            self.assertEqual(sess_a.check_in_time, self._bd_dt(today, 9, 0))
            self.assertEqual(sess_a.check_out_time, self._bd_dt(today, 12, 0))
            self.assertEqual(sess_b.check_in_time, self._bd_dt(today, 13, 0))
            self.assertEqual(sess_b.check_out_time, self._bd_dt(today, 18, 0))

            # Shuffled permutation on other_user produces identical session relationships
            in_a2 = str(uuid.uuid4())
            out_a2 = str(uuid.uuid4())
            in_b2 = str(uuid.uuid4())
            out_b2 = str(uuid.uuid4())
            shuffled_items = [
                {
                    "uuid": out_b2,
                    "action": "check_out",
                    "depends_on_sync_uuid": in_b2,
                    "payload": {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "depends_on_sync_uuid": in_b2,
                        "client_event_time": self._bd_dt(today, 18, 0).isoformat(),
                    },
                },
                {
                    "uuid": in_b2,
                    "action": "check_in",
                    "payload": {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "type": "office",
                        "client_event_time": self._bd_dt(today, 13, 0).isoformat(),
                    },
                },
                {
                    "uuid": out_a2,
                    "action": "check_out",
                    "depends_on_sync_uuid": in_a2,
                    "payload": {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "depends_on_sync_uuid": in_a2,
                        "client_event_time": self._bd_dt(today, 12, 0).isoformat(),
                    },
                },
                {
                    "uuid": in_a2,
                    "action": "check_in",
                    "payload": {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "type": "office",
                        "client_event_time": self._bd_dt(today, 9, 0).isoformat(),
                    },
                },
            ]
            self.client.force_login(self.other_user)
            resp2 = self.client.post(
                reverse("attendance:bulk_sync"),
                data=json.dumps({"items": shuffled_items}),
                content_type="application/json",
            )
            self.assertEqual(resp2.status_code, 200)
            data2 = resp2.json()
            self.assertEqual(data2["synced"], 4)
            res2 = {r["uuid"]: r for r in data2["results"]}
            self.assertEqual(res2[out_a2]["data"]["session_id"], res2[in_a2]["data"]["session_id"])
            self.assertEqual(res2[out_b2]["data"]["session_id"], res2[in_b2]["data"]["session_id"])
            self.assertNotEqual(res2[in_a2]["data"]["session_id"], res2[in_b2]["data"]["session_id"])

    # Group 7: Equal timestamps with explicit dependencies
    def test_group_7_equal_timestamps_with_explicit_dependencies(self):
        with patch("django.utils.timezone.now", return_value=FIXED_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            today = FIXED_NOW.date()
            same_ts = self._bd_dt(today, 9, 0).isoformat()
            ci_uuid = str(uuid.uuid4())
            co_uuid = str(uuid.uuid4())

            self.client.force_login(self.staff_user)
            resp = self.client.post(
                reverse("attendance:bulk_sync"),
                data=json.dumps(
                    {
                        "items": [
                            {
                                "uuid": co_uuid,
                                "action": "check_out",
                                "depends_on_sync_uuid": ci_uuid,
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "depends_on_sync_uuid": ci_uuid,
                                    "client_event_time": same_ts,
                                },
                            },
                            {
                                "uuid": ci_uuid,
                                "action": "check_in",
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "type": "office",
                                    "client_event_time": same_ts,
                                },
                            },
                        ]
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["synced"], 2)
            res = {r["uuid"]: r for r in data["results"]}
            self.assertEqual(res[ci_uuid]["status"], "success")
            self.assertEqual(res[co_uuid]["status"], "success")
            self.assertEqual(res[co_uuid]["data"]["session_id"], res[ci_uuid]["data"]["session_id"])

    # Group 8: Dependency cycles and missing dependencies fail closed without blocking independent events
    def test_group_8_cycle_and_missing_dependency_fail_closed(self):
        with patch("django.utils.timezone.now", return_value=FIXED_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            today = FIXED_NOW.date()
            cyc_a = str(uuid.uuid4())
            cyc_b = str(uuid.uuid4())
            self_cyc = str(uuid.uuid4())
            missing_dep_co = str(uuid.uuid4())
            valid_ci = str(uuid.uuid4())

            self.client.force_login(self.staff_user)
            resp = self.client.post(
                reverse("attendance:bulk_sync"),
                data=json.dumps(
                    {
                        "items": [
                            {
                                "uuid": cyc_a,
                                "action": "check_in",
                                "depends_on_sync_uuid": cyc_b,
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "client_event_time": self._bd_dt(today, 9, 0).isoformat(),
                                },
                            },
                            {
                                "uuid": cyc_b,
                                "action": "check_out",
                                "depends_on_sync_uuid": cyc_a,
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "client_event_time": self._bd_dt(today, 9, 30).isoformat(),
                                },
                            },
                            {
                                "uuid": self_cyc,
                                "action": "check_out",
                                "depends_on_sync_uuid": self_cyc,
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "client_event_time": self._bd_dt(today, 9, 45).isoformat(),
                                },
                            },
                            {
                                "uuid": missing_dep_co,
                                "action": "check_out",
                                "depends_on_sync_uuid": str(uuid.uuid4()),
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "client_event_time": self._bd_dt(today, 10, 0).isoformat(),
                                },
                            },
                            {
                                "uuid": valid_ci,
                                "action": "check_in",
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "type": "office",
                                    "client_event_time": self._bd_dt(today, 10, 15).isoformat(),
                                },
                            },
                        ]
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["synced"], 1)
            res = {r["uuid"]: r for r in data["results"]}

            for bad_id in (cyc_a, cyc_b, self_cyc, missing_dep_co):
                self.assertEqual(res[bad_id]["status"], "failed")
                self.assertTrue(res[bad_id]["blocked"])
                self.assertTrue(res[bad_id]["permanent"])

            self.assertEqual(res[valid_ci]["status"], "success")
            self.assertEqual(Attendance.objects.filter(employee=self.staff_emp).count(), 1)

    # Group 9: Failed parent blocks dependent checkout while independent valid event succeeds
    def test_group_9_failed_parent_blocks_dependent_checkout_allows_independent(self):
        with patch("django.utils.timezone.now", return_value=FIXED_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            today = FIXED_NOW.date()
            bad_ci = str(uuid.uuid4())
            dep_co = str(uuid.uuid4())
            indep_fv = str(uuid.uuid4())

            self.client.force_login(self.staff_user)
            resp = self.client.post(
                reverse("attendance:bulk_sync"),
                data=json.dumps(
                    {
                        "items": [
                            {
                                "uuid": bad_ci,
                                "action": "check_in",
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "type": "office",
                                    # Invalid future timestamp -> parent fails permanently
                                    "client_event_time": (FIXED_NOW + timedelta(hours=2)).isoformat(),
                                },
                            },
                            {
                                "uuid": dep_co,
                                "action": "check_out",
                                "depends_on_sync_uuid": bad_ci,
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "depends_on_sync_uuid": bad_ci,
                                    "client_event_time": self._bd_dt(today, 17, 0).isoformat(),
                                },
                            },
                            {
                                "uuid": indep_fv,
                                "action": "field_visit",
                                "payload": {
                                    "latitude": 23.8103,
                                    "longitude": 90.4125,
                                    "visit_title": "Client Site",
                                    "client_name": "Acme",
                                    "client_event_time": self._bd_dt(today, 14, 0).isoformat(),
                                },
                            },
                        ]
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["synced"], 1)
            res = {r["uuid"]: r for r in data["results"]}

            self.assertEqual(res[bad_ci]["status"], "failed")
            self.assertTrue(res[bad_ci]["permanent"])
            self.assertEqual(res[dep_co]["status"], "failed")
            self.assertTrue(res[dep_co]["blocked"])
            self.assertTrue(res[dep_co]["permanent"])
            self.assertEqual(res[indep_fv]["status"], "success")
            self.assertEqual(Attendance.objects.filter(employee=self.staff_emp, attendance_type="field_visit").count(), 1)
