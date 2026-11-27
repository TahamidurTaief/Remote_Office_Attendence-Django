import calendar as cal_mod
from datetime import date, datetime, time, timedelta
from collections import defaultdict
from django.utils import timezone
from django.conf import settings
from apps.employees.models import EmployeeProfile, EmploymentHistory
from apps.branches.models import Branch, OfficeSchedule, Holiday
from apps.attendance.models import Attendance, AttendancePolicy
from apps.leave.models import LeaveRequest, LeaveType, LeaveBalance
from apps.attendance.schedule_utils import get_branch_schedule, parse_time_from_string, parse_shift_times, resolve_employee_holiday

_EXIT_STATUS_VALUES = {'resigned', 'terminated'}
_ACTIVE_STATUS_VALUES = {'active', 'probation', 'on leave', 'on_leave', 'suspended'}


def _resolve_joined_date(employee):
    joined_date = getattr(employee, 'joined_date', None)
    if not joined_date and getattr(employee, 'master_employee', None):
        joined_date = getattr(employee.master_employee, 'joined_date', None)
    return joined_date


def _resolve_master_id(employee):
    mid = getattr(employee, 'master_employee_id', None)
    if mid is not None:
        return mid
    master = getattr(employee, 'master_employee', None)
    return getattr(master, 'id', None)


def build_employment_eligibility_map(employees):
    """
    Bulk-loads EmploymentHistory status transitions in 1 query and builds
    per-employee employment intervals [(start_date, end_date_or_None), ...].
    Never invents missing exit dates or reconstructs history from today's is_active.
    """
    master_ids = []
    for emp in employees:
        mid = _resolve_master_id(emp)
        if mid is not None:
            master_ids.append(mid)

    history_by_master = defaultdict(list)
    if master_ids:
        rows = (
            EmploymentHistory.objects.filter(
                employee_id__in=set(master_ids),
                field_changed='status',
            )
            .values_list('employee_id', 'new_value', 'effective_date')
            .order_by('effective_date', 'created_at', 'id')
        )
        for emp_id, new_val, eff_date in rows:
            history_by_master[emp_id].append((new_val, eff_date))

    elig_map = {}
    for emp in employees:
        joined_date = _resolve_joined_date(emp)
        mid = _resolve_master_id(emp)
        master = getattr(emp, 'master_employee', None)
        master_status = (getattr(master, 'status', None) or '').strip().lower() if master else ''
        transitions = history_by_master.get(mid, []) if mid is not None else []

        unresolved = joined_date is None
        intervals = []
        current_start = joined_date
        uncertain_from = None
        pre_join_conflict = False
        last_exit_date = None

        def _mark_uncertain(from_date):
            nonlocal unresolved, uncertain_from
            unresolved = True
            if from_date is not None:
                uncertain_from = from_date if uncertain_from is None else min(uncertain_from, from_date)

        for new_val, eff_date in transitions:
            norm = (new_val or '').strip().lower()
            if not eff_date:
                _mark_uncertain(joined_date)
                continue
            if joined_date and eff_date < joined_date:
                pre_join_conflict = True
                _mark_uncertain(eff_date)
                continue
            if norm in _EXIT_STATUS_VALUES:
                if current_start is not None:
                    if eff_date >= current_start:
                        intervals.append((current_start, eff_date))
                        last_exit_date = eff_date
                    else:
                        _mark_uncertain(eff_date)
                    current_start = None
                else:
                    _mark_uncertain(last_exit_date + timedelta(days=1) if last_exit_date else eff_date)
            elif norm in _ACTIVE_STATUS_VALUES:
                if current_start is None:
                    current_start = eff_date

        if current_start is not None:
            if master_status in _EXIT_STATUS_VALUES:
                # Resigned/terminated without documented exit: do not invent exit date.
                _mark_uncertain(current_start)
            else:
                intervals.append((current_start, None))

        elig_map[emp.id] = {
            'joined_date': joined_date,
            'intervals': intervals,
            'unresolved': unresolved,
            'uncertain_from': uncertain_from,
            'pre_join_conflict': pre_join_conflict,
        }
    return elig_map


def is_date_employed(target_date, employee, eligibility=None):
    """
    Shared eligibility rule:
    - Pre-joining (< joined_date) and post-last-working-day dates are Not Applicable (False, False).
    - Respects documented rejoining intervals.
    - Genuinely uncertain dates return (False, True) so they never become Absent.
    - Returns (is_employed: bool, date_unresolved: bool).
    """
    if eligibility is not None:
        joined_date = eligibility['joined_date']
        intervals = eligibility['intervals']
        unresolved = eligibility['unresolved']
        uncertain_from = eligibility.get('uncertain_from')
        pre_join_conflict = eligibility.get('pre_join_conflict', False)

        if joined_date and target_date < joined_date and not pre_join_conflict:
            return False, False
        if joined_date is None:
            return False, True
        if unresolved and (uncertain_from is None or target_date >= uncertain_from):
            return False, True
        for start_d, end_d in intervals:
            if target_date >= start_d and (end_d is None or target_date <= end_d):
                return True, False
        return False, False

    joined_date = _resolve_joined_date(employee)
    if joined_date and target_date < joined_date:
        return False, False
    if joined_date is None:
        return False, True
    return True, False


def _get_working_day_set(schedule):
    if schedule is not None:
        return {day.lower() for day in (schedule.working_days or [])}
    try:
        first_schedule = OfficeSchedule.objects.first()
        if first_schedule and first_schedule.working_days:
            return {day.lower() for day in first_schedule.working_days}
    except Exception:
        pass
    return {'saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday'}

def _get_working_days(year, month, schedule=None):
    cal_mod.setfirstweekday(cal_mod.SATURDAY)
    working_day_set = _get_working_day_set(schedule)
    count = 0
    first_wd = cal_mod.firstweekday()
    for week in cal_mod.monthcalendar(year, month):
        for idx, day in enumerate(week):
            if day:
                actual_weekday = (first_wd + idx) % 7
                if cal_mod.day_name[actual_weekday].lower() in working_day_set:
                    count += 1
    return count

class OptimizedSchedule:
    def __init__(self, employee, policies_by_branch=None, global_policy=None):
        self.employee = employee
        self.office_start_time = time(9, 0)
        self.office_end_time = time(18, 0)
        self.late_after_minutes = 15
        self.early_checkout_before_minutes = 30
        self.overtime_after_minutes = 0
        self.working_days = ['saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday']
        self.has_branch_working_days = False
        
        branch = getattr(employee, 'branch', None)
        branch_schedule = None
        if branch:
            try:
                branch_schedule = branch.schedule
            except Exception:
                pass
                
        if branch_schedule:
            self.office_start_time = branch_schedule.office_start_time
            self.office_end_time = branch_schedule.office_end_time
            if isinstance(self.office_start_time, str):
                self.office_start_time = parse_time_from_string(self.office_start_time)
            if isinstance(self.office_end_time, str):
                self.office_end_time = parse_time_from_string(self.office_end_time)
            self.late_after_minutes = branch_schedule.late_after_minutes
            self.early_checkout_before_minutes = branch_schedule.early_checkout_before_minutes
            self.overtime_after_minutes = branch_schedule.overtime_after_minutes
            if branch_schedule.working_days:
                self.working_days = branch_schedule.working_days
                self.has_branch_working_days = True
            
        master_employee = getattr(employee, 'master_employee', None)
        if master_employee and master_employee.shift:
            s_time, e_time = parse_shift_times(master_employee.shift)
            if s_time is not None:
                self.office_start_time = s_time
            if e_time is not None:
                self.office_end_time = e_time

        branch_id = getattr(employee, 'branch_id', None)
        if policies_by_branch is not None:
            policy = policies_by_branch.get(branch_id, global_policy)
        else:
            try:
                from apps.attendance.models import AttendancePolicy
                policy = AttendancePolicy.objects.filter(branch_id=branch_id).first() if branch_id else None
                if not policy:
                    policy = AttendancePolicy.objects.filter(branch__isnull=True).first()
            except Exception:
                policy = None
        if policy:
            self.late_after_minutes = policy.late_grace_minutes

    def get_late_threshold(self):
        start = datetime.combine(datetime.today(), self.office_start_time)
        return (start + timedelta(minutes=self.late_after_minutes)).time()

    def get_early_checkout_threshold(self):
        end = datetime.combine(datetime.today(), self.office_end_time)
        return (end - timedelta(minutes=self.early_checkout_before_minutes)).time()

def is_employee_holiday_optimized(employee, target_date, schedule, branch_holidays=None, global_holidays=None):
    return resolve_employee_holiday(
        employee,
        target_date,
        schedule=schedule,
        branch_holidays=branch_holidays,
        global_holidays=global_holidays,
    )

from zoneinfo import ZoneInfo
from apps.attendance.scoping import get_scoped_employee_queryset, get_scoped_employee_or_404

BD_TZ = ZoneInfo('Asia/Dhaka')

def format_bd_timestamp(dt, with_seconds=True):
    """
    Formats datetime in Bangladesh Standard Time (UTC+6) with seconds:
    e.g. 09:34:12. Avoids deprecated timezone.utc.
    """
    if not dt:
        return ''
    import datetime as dt_mod
    if timezone.is_aware(dt):
        local_dt = timezone.localtime(dt, BD_TZ)
    else:
        local_dt = timezone.make_aware(dt, dt_mod.timezone.utc).astimezone(BD_TZ) if timezone.is_naive(dt) else dt
    if with_seconds:
        return local_dt.strftime('%H:%M:%S')
    return local_dt.strftime('%H:%M')


def format_bd_time_12h(dt, with_seconds=False):
    """
    Formats datetime in Bangladesh Standard Time (UTC+6) 12-hour format:
    e.g. 9:34A, 3:56P, 12:05P.
    """
    if not dt:
        return ''
    import datetime as dt_mod
    if timezone.is_aware(dt):
        local_dt = timezone.localtime(dt, BD_TZ)
    else:
        local_dt = timezone.make_aware(dt, dt_mod.timezone.utc).astimezone(BD_TZ) if timezone.is_naive(dt) else dt
    hour = str(int(local_dt.strftime('%I')))
    minute = local_dt.strftime('%M')
    sec = local_dt.strftime('%S')
    ampm = 'A' if local_dt.strftime('%p').upper() == 'AM' else 'P'
    if with_seconds:
        return f"{hour}:{minute}:{sec}{ampm}"
    return f"{hour}:{minute}{ampm}"


def _sum_non_overlapping_hours(check_in_sessions):
    """
    Sums completed office/check-in session durations in hours, merging overlapping
    intervals so overlapping sessions are not double-counted. Open sessions
    (check_out_time is None) contribute 0 (unknown duration excluded).
    """
    completed = [
        s for s in check_in_sessions
        if s.check_in_time and s.check_out_time and s.check_out_time > s.check_in_time
    ]
    if not completed:
        return 0.0

    completed.sort(key=lambda s: s.check_in_time)
    has_overlap = any(
        completed[i].check_in_time < completed[i - 1].check_out_time
        for i in range(1, len(completed))
    )
    if not has_overlap:
        total = 0.0
        for s in completed:
            if s.total_hours is not None:
                total += float(s.total_hours)
            else:
                total += round((s.check_out_time - s.check_in_time).total_seconds() / 3600.0, 2)
        return round(total, 2)

    merged = [(completed[0].check_in_time, completed[0].check_out_time)]
    for s in completed[1:]:
        prev_start, prev_end = merged[-1]
        if s.check_in_time <= prev_end:
            if s.check_out_time > prev_end:
                merged[-1] = (prev_start, s.check_out_time)
        else:
            merged.append((s.check_in_time, s.check_out_time))
    total_seconds = sum((end - start).total_seconds() for start, end in merged)
    return round(total_seconds / 3600.0, 2)

def calculate_employee_day(
    employee,
    target_date,
    day_attendances,
    schedule=None,
    is_holiday=False,
    is_on_leave=False,
    today=None,
    eligibility=None,
):
    """
    Authoritative single employee-day calculation.
    Shared by Daily, Monthly, Individual reports, calendar statuses and exports.
    """
    if today is None:
        today = timezone.localdate()

    # Non-expired attendances only
    day_atts = [a for a in (day_attendances or []) if not getattr(a, 'is_expired', False)]
    check_in_sessions = [a for a in day_atts if a.attendance_type == 'check_in']
    field_visits = [a for a in day_atts if a.attendance_type == 'field_visit']

    has_check_in = len(check_in_sessions) > 0
    has_field_visit = len(field_visits) > 0
    has_any_attendance = has_check_in or has_field_visit
    is_employed, elig_unresolved = is_date_employed(target_date, employee, eligibility=eligibility)
    emp_unresolved = bool(eligibility and eligibility.get('unresolved'))

    # Rule: Future dates must contribute no present, absent, leave, late, hours or field-visit totals.
    if target_date > today:
        return {
            'date': target_date,
            'is_future': True,
            'status': 'scheduled',
            'status_display': 'Scheduled',
            'monthly_status': '—',
            'calendar_color': 'future',
            'is_present': False,
            'is_absent': False,
            'is_late': False,
            'is_on_leave': False,
            'is_holiday': is_holiday,
            'is_weekend': is_holiday,
            'present_count': 0,
            'absent_count': 0,
            'on_leave_count': 0,
            'late_count': 0,
            'field_visit_count': 0,
            'total_hours': 0.0,
            'overtime_minutes': 0,
            'first_arrival': None,
            'last_departure': None,
            'is_open_session': False,
            'is_missing_checkout': False,
            'has_leave_conflict': False,
            'eligibility_conflict': False,
            'eligibility_unresolved': False,
            'needs_review': False,
            'check_in': None,
            'check_in_time': None,
            'check_out_time': None,
            'check_in_time_str': '—',
            'check_out_time_str': '—',
            'check_in_sessions': [],
            'field_visits': [],
        }

    # Non-future dates:
    # 1. Any attendance:
    # "Multiple check-ins and field visits on one date count as one present day."
    # "Field-only days count as present."
    # "Fix Individual late counts: count each employee-day once, not each session."
    # "Sum valid check-in session hours consistently."
    if has_any_attendance:
        day_has_late = any(a.status == 'late' for a in check_in_sessions)
        if not day_has_late and check_in_sessions and schedule and hasattr(schedule, 'get_late_threshold'):
            first_ci = check_in_sessions[0]
            if first_ci.check_in_time:
                late_time = schedule.get_late_threshold()
                ci_time = first_ci.check_in_time
                ci_local = timezone.localtime(ci_time, BD_TZ).time() if timezone.is_aware(ci_time) else ci_time.time()
                if ci_local > late_time:
                    day_has_late = True

        has_open_office = any(s.check_out_time is None for s in check_in_sessions)
        is_missing_checkout = bool(has_open_office and target_date < today)
        has_leave_conflict = bool(is_on_leave)

        if day_has_late:
            status = 'late'
            status_display = 'Late (Missing Checkout)' if is_missing_checkout else 'Late'
            monthly_status = 'Late'
            calendar_color = 'amber'
        elif has_check_in:
            first_ci = check_in_sessions[0]
            status = first_ci.status if first_ci.status in ('on_time', 'present') else 'on_time'
            if is_missing_checkout:
                status_display = 'Present (Missing Checkout)'
            else:
                status_display = 'On Time' if status == 'on_time' else 'Present'
            monthly_status = 'Present'
            calendar_color = 'green'
        else:
            status = 'on_time'
            status_display = 'On Field'
            monthly_status = 'Field Visit'
            calendar_color = 'purple'

        # Sum non-overlapping completed office session hours (exclude open/unknown & field visits)
        total_hours = _sum_non_overlapping_hours(check_in_sessions)
        ot_minutes = sum(int(getattr(a, 'overtime_minutes', 0) or 0) for a in check_in_sessions if a.check_out_time)
        if ot_minutes == 0 and check_in_sessions and schedule:
            from apps.attendance.schedule_utils import calculate_overtime
            ot_minutes = sum(
                calculate_overtime(a.check_out_time, schedule, employee, session_date=target_date)
                if a.check_out_time else 0
                for a in check_in_sessions
            )

        all_sessions = check_in_sessions + field_visits
        ci_times = [s.check_in_time for s in all_sessions if s and s.check_in_time]
        earliest_ci = min(ci_times) if ci_times else None

        # Final office checkout stays empty while any office session remains open,
        # and field visit timestamps never become office checkout.
        office_co_times = [s.check_out_time for s in check_in_sessions if s and s.check_out_time]
        if has_open_office:
            last_completed_co = None
        else:
            last_completed_co = max(office_co_times) if office_co_times else None

        primary_ci = check_in_sessions[0] if check_in_sessions else (field_visits[0] if field_visits else None)

        in_time_str = format_bd_timestamp(earliest_ci, with_seconds=True) if earliest_ci else ('Field' if field_visits else 'Present')
        if has_open_office:
            out_time_str = ''
        elif last_completed_co:
            out_time_str = format_bd_timestamp(last_completed_co, with_seconds=True)
        else:
            out_time_str = ''

        return {
            'date': target_date,
            'is_future': False,
            'status': status,
            'status_display': status_display,
            'monthly_status': monthly_status,
            'calendar_color': calendar_color,
            'is_present': True,
            'is_absent': False,
            'is_late': day_has_late,
            'is_on_leave': False,
            'is_holiday': is_holiday,
            'is_weekend': is_holiday,
            'present_count': 1,
            'absent_count': 0,
            'on_leave_count': 0,
            'late_count': 1 if day_has_late else 0,
            'field_visit_count': len(field_visits),
            'total_hours': total_hours,
            'overtime_minutes': ot_minutes,
            'first_arrival': earliest_ci,
            'last_departure': last_completed_co,
            'is_open_session': has_open_office,
            'is_missing_checkout': is_missing_checkout,
            'has_leave_conflict': has_leave_conflict,
            'check_in': primary_ci,
            'check_in_time': earliest_ci,
            'check_out_time': last_completed_co,
            'check_in_time_str': in_time_str,
            'check_out_time_str': out_time_str,
            'check_in_sessions': check_in_sessions,
            'field_visits': field_visits,
            'eligibility_conflict': not is_employed,
            'eligibility_unresolved': elig_unresolved or emp_unresolved,
            'needs_review': False,
        }


    # 2. Not employed (pre-joining, post-exit, or employment gap): Not Applicable
    # Checked before leave/absent so dates outside employment contribute no absence or denominator.
    if not is_employed and not elig_unresolved:
        return {
            'date': target_date,
            'is_future': False,
            'status': 'not_employed',
            'status_display': '—',
            'monthly_status': '—',
            'calendar_color': 'gray',
            'is_present': False,
            'is_absent': False,
            'is_late': False,
            'is_on_leave': False,
            'is_holiday': is_holiday,
            'is_weekend': is_holiday,
            'present_count': 0,
            'absent_count': 0,
            'on_leave_count': 0,
            'late_count': 0,
            'field_visit_count': 0,
            'total_hours': 0.0,
            'overtime_minutes': 0,
            'first_arrival': None,
            'last_departure': None,
            'is_open_session': False,
            'is_missing_checkout': False,
            'has_leave_conflict': False,
            'eligibility_conflict': False,
            'eligibility_unresolved': False,
            'needs_review': False,
            'check_in': None,
            'check_in_time': None,
            'check_out_time': None,
            'check_in_time_str': '—',
            'check_out_time_str': '—',
            'check_in_sessions': [],
            'field_visits': [],
        }

    # 3. No attendance: Check approved leave
    if is_on_leave:
        return {
            'date': target_date,
            'is_future': False,
            'status': 'on_leave',
            'status_display': 'On Leave',
            'monthly_status': 'On Leave',
            'calendar_color': 'blue',
            'is_present': False,
            'is_absent': False,
            'is_late': False,
            'is_on_leave': True,
            'is_holiday': is_holiday,
            'is_weekend': is_holiday,
            'present_count': 0,
            'absent_count': 0,
            'on_leave_count': 1,
            'late_count': 0,
            'field_visit_count': 0,
            'total_hours': 0.0,
            'overtime_minutes': 0,
            'first_arrival': None,
            'last_departure': None,
            'is_open_session': False,
            'is_missing_checkout': False,
            'has_leave_conflict': False,
            'check_in': None,
            'check_in_time': None,
            'check_out_time': None,
            'check_in_time_str': 'On Leave',
            'check_out_time_str': 'On Leave',
            'check_in_sessions': [],
            'field_visits': [],
        }

    # 3. Holiday / off-day without attendance: Not absent
    # "Holiday/off-day without attendance is not absent."
    if is_holiday:
        return {
            'date': target_date,
            'is_future': False,
            'status': 'holiday',
            'status_display': 'Holiday',
            'monthly_status': 'Holiday',
            'calendar_color': 'gray',
            'is_present': False,
            'is_absent': False,
            'is_late': False,
            'is_on_leave': False,
            'is_holiday': True,
            'is_weekend': True,
            'present_count': 0,
            'absent_count': 0,
            'on_leave_count': 0,
            'late_count': 0,
            'field_visit_count': 0,
            'total_hours': 0.0,
            'overtime_minutes': 0,
            'first_arrival': None,
            'last_departure': None,
            'is_open_session': False,
            'is_missing_checkout': False,
            'has_leave_conflict': False,
            'check_in': None,
            'check_in_time': None,
            'check_out_time': None,
            'check_in_time_str': 'Holiday',
            'check_out_time_str': 'Holiday',
            'check_in_sessions': [],
            'field_visits': [],
        }

    # 4. Unresolved employment boundary: explicit review-needed state, never Absent
    if elig_unresolved:
        return {
            'date': target_date,
            'is_future': False,
            'status': 'eligibility_unresolved',
            'status_display': 'Review Needed',
            'monthly_status': 'Review',
            'calendar_color': 'amber',
            'is_present': False,
            'is_absent': False,
            'is_late': False,
            'is_on_leave': False,
            'is_holiday': False,
            'is_weekend': False,
            'present_count': 0,
            'absent_count': 0,
            'on_leave_count': 0,
            'late_count': 0,
            'field_visit_count': 0,
            'total_hours': 0.0,
            'overtime_minutes': 0,
            'first_arrival': None,
            'last_departure': None,
            'is_open_session': False,
            'is_missing_checkout': False,
            'has_leave_conflict': False,
            'eligibility_conflict': False,
            'eligibility_unresolved': True,
            'needs_review': True,
            'check_in': None,
            'check_in_time': None,
            'check_out_time': None,
            'check_in_time_str': 'Review Needed',
            'check_out_time_str': 'Review Needed',
            'check_in_sessions': [],
            'field_visits': [],
        }

    # 5. Before shift + sync grace period end: pending
    if target_date == today and schedule and getattr(schedule, 'office_end_time', None):
        sync_grace_minutes = int(getattr(settings, 'ATTENDANCE_SYNC_GRACE_MINUTES', 0) or 0)
        end_dt = datetime.combine(target_date, schedule.office_end_time)
        if getattr(schedule, 'office_start_time', None) and schedule.office_end_time < schedule.office_start_time:
            end_dt += timedelta(days=1)
        end_dt = timezone.make_aware(end_dt, BD_TZ) + timedelta(minutes=sync_grace_minutes)
        if timezone.localtime(timezone.now(), BD_TZ) < end_dt:
            return {
                'date': target_date,
                'is_future': False,
                'status': 'scheduled',
                'status_display': 'Pending',
                'monthly_status': '—',
                'calendar_color': 'future',
                'is_present': False,
                'is_absent': False,
                'is_late': False,
                'is_on_leave': False,
                'is_holiday': False,
                'is_weekend': False,
                'present_count': 0,
                'absent_count': 0,
                'on_leave_count': 0,
                'late_count': 0,
                'field_visit_count': 0,
                'total_hours': 0.0,
                'overtime_minutes': 0,
                'first_arrival': None,
                'last_departure': None,
                'is_open_session': False,
                'is_missing_checkout': False,
                'has_leave_conflict': False,
                'check_in': None,
                'check_in_time': None,
                'check_out_time': None,
                'check_in_time_str': '—',
                'check_out_time_str': '—',
                'check_in_sessions': [],
                'field_visits': [],
            }

    # 5. Absent on regular working day
    return {
        'date': target_date,
        'is_future': False,
        'status': 'absent',
        'status_display': 'Absent',
        'monthly_status': 'Absent',
        'calendar_color': 'red',
        'is_present': False,
        'is_absent': True,
        'is_late': False,
        'is_on_leave': False,
        'is_holiday': False,
        'is_weekend': False,
        'present_count': 0,
        'absent_count': 1,
        'on_leave_count': 0,
        'late_count': 0,
        'field_visit_count': 0,
        'total_hours': 0.0,
        'overtime_minutes': 0,
        'first_arrival': None,
        'last_departure': None,
        'is_open_session': False,
        'is_missing_checkout': False,
        'has_leave_conflict': False,
        'check_in': None,
        'check_in_time': None,
        'check_out_time': None,
        'check_in_time_str': 'Absent',
        'check_out_time_str': 'Absent',
        'check_in_sessions': [],
        'field_visits': [],
    }

def get_daily_report_data(
    report_date=None,
    start_date=None,
    end_date=None,
    employee_id=None,
    branch_id=None,
    allowed_employee_ids=None,
    employee_queryset=None,
    status=None,
    attendance_type=None,
):
    """
    Authoritative daily attendance calculation service.
    Shared by Daily report page and Daily CSV/PDF exports.
    """
    today = timezone.localdate()
    if start_date is None and end_date is None:
        if report_date is None:
            start_date = end_date = today
        else:
            start_date = end_date = report_date
    elif start_date is None:
        start_date = end_date
    elif end_date is None:
        end_date = start_date

    # Query employees
    from django.db.models import Q
    if employee_id:
        employees_qs = EmployeeProfile.objects.filter(id=employee_id)
    else:
        employees_qs = EmployeeProfile.objects.filter(
            Q(is_active=True) |
            Q(attendances__date__gte=start_date, attendances__date__lte=end_date, attendances__is_expired=False)
        ).distinct()

    employees_qs = employees_qs.select_related(
        'branch',
        'branch__schedule',
        'master_employee',
        'master_employee__department',
    ).order_by('full_name')

    if employee_queryset is not None:
        employees_qs = employees_qs.filter(id__in=employee_queryset.values_list('id', flat=True))
    elif allowed_employee_ids is not None:
        employees_qs = employees_qs.filter(id__in=allowed_employee_ids)

    if employee_id:
        employees_qs = employees_qs.filter(id=employee_id)
    if branch_id:
        employees_qs = employees_qs.filter(branch_id=branch_id)

    employees = list(employees_qs)
    target_emp_ids = [e.id for e in employees]

    # Bulk fetch policies, holidays, approved leaves, attendances
    policies = list(AttendancePolicy.objects.all())
    policies_by_branch = {p.branch_id: p for p in policies if p.branch_id is not None}
    global_policy = next((p for p in policies if p.branch_id is None), None)

    holidays = list(Holiday.objects.filter(date__gte=start_date, date__lte=end_date))
    branch_holidays = defaultdict(set)
    global_holidays = set()
    for h in holidays:
        if h.branch_id:
            branch_holidays[h.branch_id].add(h.date)
        else:
            global_holidays.add(h.date)

    leave_requests = LeaveRequest.objects.filter(
        employee_id__in=target_emp_ids,
        status='approved',
        start_date__lte=end_date,
        end_date__gte=start_date
    ).select_related('leave_type')

    approved_leaves = defaultdict(set)
    for req in leave_requests:
        s_date = max(req.start_date, start_date)
        e_date = min(req.end_date, end_date)
        curr = s_date
        while curr <= e_date:
            approved_leaves[req.employee_id].add(curr)
            curr += timedelta(days=1)

    attendances = list(
        Attendance.objects.filter(
            employee_id__in=target_emp_ids,
            date__gte=start_date,
            date__lte=end_date,
            is_expired=False
        )
        .select_related('employee', 'employee__branch')
        .prefetch_related('locations')
        .order_by('date', 'check_in_time')
    )

    att_map = defaultdict(list)
    for a in attendances:
        att_map[(a.employee_id, a.date)].append(a)

    date_list = []
    curr = start_date
    while curr <= end_date:
        date_list.append(curr)
        curr += timedelta(days=1)

    eligibility_map = build_employment_eligibility_map(employees)

    rows = []
    for d in date_list:
        for emp in employees:
            schedule = OptimizedSchedule(emp, policies_by_branch, global_policy)
            is_holiday = is_employee_holiday_optimized(emp, d, schedule, branch_holidays, global_holidays)
            is_on_leave = d in approved_leaves.get(emp.id, set())

            emp_day_atts = att_map.get((emp.id, d), [])
            day_calc = calculate_employee_day(
                emp, d, emp_day_atts,
                schedule=schedule,
                is_holiday=is_holiday,
                is_on_leave=is_on_leave,
                today=today,
                eligibility=eligibility_map.get(emp.id),
            )

            ci = day_calc['check_in']
            cis = day_calc['check_in_sessions']
            fvs = day_calc['field_visits']

            # Location without N+1 query:
            loc = next((l for l in ci.locations.all() if l.event == 'check_in'), None) if ci else None

            # Notes
            all_notes = [s.note.strip() for s in (cis or []) + (fvs or []) if getattr(s, 'note', None) and s.note.strip()]
            if all_notes:
                seen_n = set()
                notes = " | ".join(n for n in all_notes if not (n in seen_n or seen_n.add(n)))
            else:
                notes = ci.note if ci and ci.note else (fvs[0].note if fvs and fvs[0].note else '')

            row_data = {
                'employee': emp,
                'date': d,
                'check_in': ci,
                'check_in_sessions': cis,
                'first_arrival': day_calc.get('first_arrival'),
                'last_departure': day_calc.get('last_departure'),
                'is_open_session': day_calc.get('is_open_session', False),
                'is_missing_checkout': day_calc.get('is_missing_checkout', False),
                'has_leave_conflict': day_calc.get('has_leave_conflict', False),
                'check_in_time': day_calc.get('check_in_time'),
                'check_out_time': day_calc.get('check_out_time'),
                'check_in_time_str': day_calc.get('check_in_time_str', '—'),
                'check_out_time_str': day_calc.get('check_out_time_str', ''),
                'total_hours': day_calc['total_hours'],
                'field_visits': fvs,
                'location': loc,
                'status': day_calc['status'],
                'status_display': day_calc['status_display'],
                'is_present': day_calc['is_present'],
                'is_absent': day_calc['is_absent'],
                'is_late': day_calc['is_late'],
                'eligibility_unresolved': day_calc.get('eligibility_unresolved', False),
                'needs_review': day_calc.get('needs_review', False),
                'notes': notes,
                'day_calc': day_calc,
            }

            # Filter by status
            if status:
                sp = status.lower()
                if sp == 'present' and not row_data['is_present']:
                    continue
                elif sp == 'absent' and not row_data['is_absent']:
                    continue
                elif sp == 'late' and row_data['status'] != 'late':
                    continue
                elif sp in ('leave', 'on_leave') and row_data['status'] != 'on_leave':
                    continue
                elif sp in ('on_time', 'scheduled', 'holiday') and row_data['status'] != sp:
                    continue

            # Filter by attendance_type
            if attendance_type:
                at_lower = attendance_type.lower()
                if at_lower in ('check_in', 'office') and len(cis) == 0:
                    continue
                elif at_lower in ('field_visit', 'field') and len(fvs) == 0:
                    continue

            rows.append(row_data)

    present = sum(1 for r in rows if r['is_present'])
    late = sum(1 for r in rows if r['status'] == 'late')
    absent = sum(1 for r in rows if r['is_absent'])
    review_needed = sum(1 for r in rows if r.get('needs_review'))
    field_total = sum(len(r['field_visits']) for r in rows)
    total_hours = round(sum(r['total_hours'] for r in rows), 2)

    return {
        'start_date': start_date,
        'end_date': end_date,
        'report_date': start_date,
        'rows': rows,
        'present': present,
        'absent': absent,
        'review_needed': review_needed,
        'late': late,
        'field_total': field_total,
        'total_hours': total_hours,
        'employees': employees,
    }

def get_monthly_report_data(year, month, employee_id=None, branch_id=None, allowed_employee_ids=None, employee_queryset=None, status=None, attendance_type=None):
    """
    Canonical monthly attendance statistics service (optimized for speed & database access).
    """
    days_in_month = cal_mod.monthrange(year, month)[1]
    all_days = [date(year, month, d) for d in range(1, days_in_month + 1)]
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)
    today = timezone.localdate()

    if year < today.year or (year == today.year and month < today.month):
        max_date = month_end
    elif year == today.year and month == today.month:
        max_date = today
    else:
        max_date = month_start - timedelta(days=1)

    # 1. Fetch active employees and employees active during reporting period
    from django.db.models import Q
    if employee_id:
        employees_qs = EmployeeProfile.objects.filter(id=employee_id)
    else:
        employees_qs = EmployeeProfile.objects.filter(
            Q(is_active=True) |
            Q(master_employee__employment_history__field_changed='status', master_employee__employment_history__effective_date__gte=month_start) |
            Q(attendances__date__gte=month_start, attendances__date__lte=month_end, attendances__is_expired=False)
        ).distinct()

    employees_qs = employees_qs.select_related(
        'branch',
        'branch__schedule',
        'master_employee',
        'master_employee__department',
    ).order_by('full_name')

    if employee_queryset is not None:
        employees_qs = employees_qs.filter(id__in=employee_queryset.values_list('id', flat=True))
    elif allowed_employee_ids is not None:
        employees_qs = employees_qs.filter(id__in=allowed_employee_ids)

    if employee_id:
        employees_qs = employees_qs.filter(id=employee_id)
    if branch_id:
        employees_qs = employees_qs.filter(branch_id=branch_id)
    
    employees = list(employees_qs)
    employee_ids = [emp.id for emp in employees]

    # 2. Fetch all policies and holidays in bulk
    policies = list(AttendancePolicy.objects.all())
    policies_by_branch = {p.branch_id: p for p in policies if p.branch_id is not None}
    global_policy = next((p for p in policies if p.branch_id is None), None)

    holidays = list(Holiday.objects.filter(date__gte=month_start, date__lte=month_end))
    branch_holidays = defaultdict(set)
    global_holidays = set()
    for h in holidays:
        if h.branch_id:
            branch_holidays[h.branch_id].add(h.date)
        else:
            global_holidays.add(h.date)

    # 3. Fetch all attendances for the month in one query (ordered deterministically, non-expired only)
    attendances_qs = Attendance.objects.filter(
        date__gte=month_start,
        date__lte=month_end,
        employee_id__in=employee_ids,
        is_expired=False
    ).select_related('employee', 'employee__branch').prefetch_related('locations').order_by('date', 'check_in_time')

    att_by_emp_date = defaultdict(list)
    for att in attendances_qs:
        att_by_emp_date[(att.employee_id, att.date)].append(att)

    # 4. Fetch approved leave requests
    leave_requests = LeaveRequest.objects.filter(
        status='approved',
        start_date__lte=month_end,
        end_date__gte=month_start,
        employee_id__in=employee_ids
    ).select_related('leave_type')

    approved_leaves_map = defaultdict(dict)
    for req in leave_requests:
        s_date = max(req.start_date, month_start)
        e_date = min(req.end_date, month_end)
        curr = s_date
        while curr <= e_date:
            approved_leaves_map[req.employee_id][curr] = req
            curr += timedelta(days=1)

    # 5. Fetch leave balances for matching employees in year
    leave_types = list(LeaveType.objects.all())
    from apps.employees.models import EmployeeLeaveRule
    rules_qs = EmployeeLeaveRule.objects.filter(employee_id__in=employee_ids)
    rules_map = {(r.employee_id, r.leave_type_id): r.days_per_year for r in rules_qs}

    balances_qs = LeaveBalance.objects.filter(employee_id__in=employee_ids, year=year).select_related('leave_type')
    balances_by_emp = defaultdict(list)
    for bal in balances_qs:
        balances_by_emp[bal.employee_id].append({
            'type': bal.leave_type,
            'remaining': bal.remaining_days,
            'total': bal.total_days
        })
    for e_id in employee_ids:
        emp_bals = balances_by_emp[e_id]
        existing_types = {b['type'].id for b in emp_bals}
        for lt in leave_types:
            if lt.id not in existing_types:
                limit = rules_map.get((e_id, lt.id), lt.default_days_per_year)
                emp_bals.append({
                    'type': lt,
                    'remaining': limit,
                    'total': limit
                })

    # Determine summary schedule for the working days KPI card
    summary_schedule = None
    if employee_id and len(employees) > 0:
        summary_schedule = OptimizedSchedule(employees[0], policies_by_branch, global_policy)
    elif branch_id:
        try:
            summary_schedule = Branch.objects.get(id=branch_id).schedule
        except (Branch.DoesNotExist, OfficeSchedule.DoesNotExist):
            summary_schedule = None

    working_days = _get_working_days(year, month, summary_schedule)

    # 6. Calculate statistics per employee
    eligibility_map = build_employment_eligibility_map(employees)
    display_att_lookup = defaultdict(dict)
    daily_status_lookup = defaultdict(dict)
    employee_stats = {}
    rows = []

    for emp in employees:
        schedule = OptimizedSchedule(emp, policies_by_branch, global_policy)
        emp_elig = eligibility_map.get(emp.id)
        emp_working_days_so_far = sum(
            1 for d in all_days
            if d <= max_date
            and is_date_employed(d, emp, eligibility=emp_elig)[0]
            and not is_employee_holiday_optimized(emp, d, schedule, branch_holidays, global_holidays)
        )

        present_count = 0
        late_count = 0
        total_ot_minutes = 0
        absent_count = 0
        review_needed_count = 0
        holiday_work_count = 0
        on_leave_count = 0
        field_visit_count = 0
        total_hours = 0.0
        emp_daily_statuses = {}
        emp_day_calcs = {}

        for d in all_days:
            day_atts = att_by_emp_date.get((emp.id, d), [])
            is_holiday = is_employee_holiday_optimized(emp, d, schedule, branch_holidays, global_holidays)
            is_on_leave = d in approved_leaves_map.get(emp.id, {})

            day_calc = calculate_employee_day(
                emp, d, day_atts,
                schedule=schedule,
                is_holiday=is_holiday,
                is_on_leave=is_on_leave,
                today=today,
                eligibility=emp_elig,
            )
            emp_day_calcs[d] = day_calc

            if day_calc['check_in']:
                display_att_lookup[emp.id][d] = day_calc['check_in']
            elif day_atts:
                display_att_lookup[emp.id][d] = day_atts[0]

            emp_daily_statuses[d.day] = day_calc['monthly_status']
            daily_status_lookup[emp.id][d] = day_calc['monthly_status']

            present_count += day_calc['present_count']
            late_count += day_calc['late_count']
            absent_count += day_calc['absent_count']
            review_needed_count += 1 if day_calc.get('needs_review') else 0
            on_leave_count += day_calc['on_leave_count']
            field_visit_count += day_calc['field_visit_count']
            total_hours += day_calc['total_hours']
            total_ot_minutes += day_calc['overtime_minutes']
            if day_calc['is_present'] and is_holiday:
                holiday_work_count += 1

        if total_ot_minutes > 0:
            ot_hours = total_ot_minutes // 60
            ot_mins = total_ot_minutes % 60
            ot_display = f"{ot_hours}h {ot_mins}m" if ot_mins > 0 else f"{ot_hours}h"
        else:
            ot_display = '—'
            
        att_pct = round(min(100.0, (present_count / emp_working_days_so_far * 100)), 1) if emp_working_days_so_far > 0 else 0.0

        employee_stats[emp.id] = {
            'present_count': present_count,
            'late_count': late_count,
            'total_ot_minutes': total_ot_minutes,
            'overtime_display': ot_display,
            'overtime_hours': round(total_ot_minutes / 60.0, 2),
            'absent_count': absent_count,
            'review_needed_count': review_needed_count,
            'holiday_work_count': holiday_work_count,
            'is_overtime_enabled': getattr(emp, 'overtime_enabled', False)
        }

        row_item = {
            'employee': emp,
            'present': present_count,
            'absent': absent_count,
            'review_needed': review_needed_count,
            'eligibility_unresolved': bool(emp_elig and emp_elig.get('unresolved')),
            'on_leave': on_leave_count,
            'late': late_count,
            'field_visits': field_visit_count,
            'total_hours': round(total_hours, 2),
            'total_ot_minutes': total_ot_minutes,
            'overtime_hours': round(total_ot_minutes / 60.0, 2),
            'overtime': ot_display,
            'overtime_display': ot_display,
            'att_pct': att_pct,
            'leave_balances': balances_by_emp[emp.id],
            'daily_statuses': emp_daily_statuses,
            'day_calcs': emp_day_calcs,
            'schedule': schedule,
        }

        # Apply optional status / attendance_type filters
        if status:
            s_lower = status.lower()
            if s_lower == 'present' and present_count == 0:
                continue
            elif s_lower == 'absent' and absent_count == 0:
                continue
            elif s_lower == 'late' and late_count == 0:
                continue
            elif s_lower in ('leave', 'on_leave') and on_leave_count == 0:
                continue
            elif s_lower == 'on_time' and (present_count - late_count) <= 0:
                continue
        if attendance_type:
            at_lower = attendance_type.lower()
            if at_lower in ('field_visit', 'field') and field_visit_count == 0:
                continue
            elif at_lower in ('check_in', 'office'):
                has_any_ci = any(any(a.attendance_type == 'check_in' for a in att_by_emp_date.get((emp.id, d), [])) for d in all_days)
                if not has_any_ci:
                    continue

        rows.append(row_item)

    total_present = sum(r['present'] for r in rows)
    total_absent = sum(r['absent'] for r in rows)
    total_review_needed = sum(r['review_needed'] for r in rows)
    total_on_leave = sum(r['on_leave'] for r in rows)
    total_late = sum(r['late'] for r in rows)
    total_field = sum(r['field_visits'] for r in rows)

    total_ot_all = sum(r.get('total_ot_minutes', 0) for r in rows)
    ot_h_all = total_ot_all // 60
    ot_m_all = total_ot_all % 60
    total_ot_str = f"{ot_h_all}h {ot_m_all}m" if ot_m_all > 0 else f"{ot_h_all}h"
    if total_ot_all == 0:
        total_ot_str = "0h"

    avg_att_pct = round(
        sum(r['att_pct'] for r in rows) / len(rows) if rows else 0, 1
    )

    return {
        'year': year,
        'month': month,
        'days_in_month': days_in_month,
        'all_days': all_days,
        'employees': [r['employee'] for r in rows],
        'att_lookup': display_att_lookup,
        'daily_statuses': daily_status_lookup,
        'employee_stats': employee_stats,
        'approved_leaves': approved_leaves_map,
        'rows': rows,
        'total_present': total_present,
        'total_absent': total_absent,
        'total_review_needed': total_review_needed,
        'total_on_leave': total_on_leave,
        'total_late': total_late,
        'total_field': total_field,
        'total_overtime': total_ot_str,
        'total_overtime_minutes': total_ot_all,
        'avg_att_pct': avg_att_pct,
        'working_days': working_days,
    }
