from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from apps.attendance.schedule_utils import evaluate_checkout_eligibility, resolve_checkout_window

BD_TZ = ZoneInfo("Asia/Dhaka")


class CheckoutWindowResolverTests(SimpleTestCase):
    def test_1_day_shift_0900_1800_exact_next_0900_boundary(self):
        """09:00-18:00 shift permits checkout before next 09:00 and requires correction at/after 09:00."""
        ci = datetime(2026, 11, 25, 9, 5, 0, tzinfo=BD_TZ)
        before_boundary = datetime(2026, 11, 26, 8, 59, 59, tzinfo=BD_TZ)
        at_boundary = datetime(2026, 11, 26, 9, 0, 0, tzinfo=BD_TZ)

        res_ok = evaluate_checkout_eligibility(ci, before_boundary, schedule_times=(time(9, 0), time(18, 0)), tz=BD_TZ)
        self.assertTrue(res_ok["resolved"])
        self.assertFalse(res_ok["is_overnight"])
        self.assertEqual(res_ok["checkout_deadline_at"], at_boundary)
        self.assertTrue(res_ok["allowed"])
        self.assertFalse(res_ok["requires_correction_workflow"])

        res_expired = evaluate_checkout_eligibility(ci, at_boundary, schedule_times=(time(9, 0), time(18, 0)), tz=BD_TZ)
        self.assertFalse(res_expired["allowed"])
        self.assertTrue(res_expired["requires_correction_workflow"])
        self.assertEqual(res_expired["reason"], "checkout_window_expired")

    def test_2_overnight_shift_2200_0600_crosses_midnight_until_next_2200(self):
        """22:00-06:00 shift remains open after midnight until the next daily 22:00 boundary."""
        ci = datetime(2026, 11, 25, 22, 0, 0, tzinfo=BD_TZ)
        after_midnight = datetime(2026, 11, 26, 2, 30, 0, tzinfo=BD_TZ)
        before_next_22 = datetime(2026, 11, 26, 21, 59, 59, tzinfo=BD_TZ)
        at_next_22 = datetime(2026, 11, 26, 22, 0, 0, tzinfo=BD_TZ)

        res_mid = evaluate_checkout_eligibility(ci, after_midnight, schedule_times=(time(22, 0), time(6, 0)), tz=BD_TZ)
        self.assertTrue(res_mid["resolved"])
        self.assertTrue(res_mid["is_overnight"])
        self.assertTrue(res_mid["allowed"])
        self.assertEqual(res_mid["checkout_deadline_at"], at_next_22)

        res_late = evaluate_checkout_eligibility(ci, before_next_22, schedule_times=(time(22, 0), time(6, 0)), tz=BD_TZ)
        self.assertTrue(res_late["allowed"])

        res_exp = evaluate_checkout_eligibility(ci, at_next_22, schedule_times=(time(22, 0), time(6, 0)), tz=BD_TZ)
        self.assertFalse(res_exp["allowed"])
        self.assertTrue(res_exp["requires_correction_workflow"])

    def test_3_following_calendar_day_holiday_never_extends_deadline(self):
        """Even when the next day is Friday (weekly holiday), deadline stays next daily 09:00."""
        # 2026-11-26 is Thursday; 2026-11-27 is Friday
        ci_thursday = datetime(2026, 11, 26, 9, 0, 0, tzinfo=BD_TZ)
        friday_0900 = datetime(2026, 11, 27, 9, 0, 0, tzinfo=BD_TZ)

        res = evaluate_checkout_eligibility(ci_thursday, friday_0900, schedule_times=(time(9, 0), time(18, 0)), tz=BD_TZ)
        self.assertEqual(res["checkout_deadline_at"], friday_0900)
        self.assertFalse(res["allowed"])
        self.assertTrue(res["requires_correction_workflow"])

    def test_4_early_arrival_and_post_midnight_overnight_shift_assignment(self):
        """Early arrival before 22:00 binds to today's 22:00 shift; 01:00 arrival binds to yesterday's 22:00 shift."""
        early_ci = datetime(2026, 11, 25, 21, 30, 0, tzinfo=BD_TZ)
        early_win = resolve_checkout_window(early_ci, schedule_times=(time(22, 0), time(6, 0)), tz=BD_TZ)
        self.assertEqual(early_win["shift_date"], datetime(2026, 11, 25).date())
        self.assertEqual(early_win["checkout_deadline_at"], datetime(2026, 11, 26, 22, 0, 0, tzinfo=BD_TZ))

        post_mid_ci = datetime(2026, 11, 26, 1, 0, 0, tzinfo=BD_TZ)
        post_mid_win = resolve_checkout_window(post_mid_ci, schedule_times=(time(22, 0), time(6, 0)), tz=BD_TZ)
        self.assertEqual(post_mid_win["shift_date"], datetime(2026, 11, 25).date())
        self.assertEqual(post_mid_win["checkout_deadline_at"], datetime(2026, 11, 26, 22, 0, 0, tzinfo=BD_TZ))

    def test_5_unresolved_schedule_and_immutable_snapshot_precedence(self):
        """Unresolved schedule is reported explicitly without guessing; snapshot overrides live schedule changes."""
        ci = datetime(2026, 11, 25, 9, 0, 0, tzinfo=BD_TZ)
        unresolved = evaluate_checkout_eligibility(ci, datetime(2026, 11, 25, 17, 0, 0, tzinfo=BD_TZ), employee=None, tz=BD_TZ)
        self.assertFalse(unresolved["resolved"])
        self.assertFalse(unresolved["allowed"])
        self.assertFalse(unresolved["requires_correction_workflow"])

        snap = {"shift_start_time": time(9, 0), "shift_end_time": time(18, 0)}
        snap_res = evaluate_checkout_eligibility(
            ci,
            datetime(2026, 11, 26, 8, 59, 0, tzinfo=BD_TZ),
            snapshot=snap,
            tz=BD_TZ,
        )
        self.assertTrue(snap_res["resolved"])
        self.assertEqual(snap_res["source"], "snapshot")
        self.assertTrue(snap_res["allowed"])
