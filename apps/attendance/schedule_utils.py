import re
from datetime import datetime, timedelta, time
from django.utils import timezone
from django.conf import settings
from django.db.models import Q

def _local_time(value):
    """Return the wall-clock time() in the active/local timezone.
    Fixes: aware DateTimeFields are stored in UTC; calling .time()
    directly on them returns UTC time, not Asia/Dhaka local time."""
    if hasattr(value, 'time'):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.time()
    return value

def parse_time_from_string(time_str):
    """Parses a time string into a datetime.time object."""
    time_str = time_str.strip().upper()
    
    # Format: HH:MM (24-hour, e.g. 18:00 or 09:00)
    match = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
    if match:
        h, m = map(int, match.groups())
        if 0 <= h < 24 and 0 <= m < 60:
            return time(h, m)
            
    # Format: HH:MM AM/PM or H:MM AM/PM (e.g. 9:30 AM, 6:00 PM)
    match = re.match(r'^(\d{1,2}):(\d{2})\s*(AM|PM)$', time_str)
    if match:
        h, m, am_pm = match.groups()
        h, m = int(h), int(m)
        if am_pm == 'PM' and h < 12:
            h += 12
        elif am_pm == 'AM' and h == 12:
            h = 0
        if 0 <= h < 24 and 0 <= m < 60:
            return time(h, m)
            
    # Format: H AM/PM or HH AM/PM (e.g. 9 AM, 6 PM)
    match = re.match(r'^(\d{1,2})\s*(AM|PM)$', time_str)
    if match:
        h, am_pm = match.groups()
        h = int(h)
        m = 0
        if am_pm == 'PM' and h < 12:
            h += 12
        elif am_pm == 'AM' and h == 12:
            h = 0
        if 0 <= h < 24:
            return time(h, m)
            
    return None

def parse_shift_times(shift_str):
    """
    Extracts start_time and end_time from shift string.
    Example: "Day Shift (9 AM - 6 PM)" -> (time(9, 0), time(18, 0))
    Example: "Night Shift (20:00 - 05:00)" -> (time(20, 0), time(5, 0))
    """
    if not shift_str:
        return None, None
        
    parts = re.split(r'\s*(?:-|to)\s*', shift_str)
    time_candidates = []
    for part in parts:
        matches = re.findall(r'\b\d{1,2}(?::\d{2})?\s*(?:AM|PM)?\b', part, re.IGNORECASE)
        for m in matches:
            t = parse_time_from_string(m)
            if t is not None:
                time_candidates.append(t)
                
    if len(time_candidates) >= 2:
        return time_candidates[0], time_candidates[1]
        
    matches = re.findall(r'\b\d{1,2}(?::\d{2})?\s*(?:AM|PM)?\b', shift_str, re.IGNORECASE)
    time_candidates = []
    for m in matches:
        t = parse_time_from_string(m)
        if t is not None:
            time_candidates.append(t)
            
    if len(time_candidates) >= 2:
        return time_candidates[0], time_candidates[1]
        
    return None, None

class DynamicSchedule:
    def __init__(self, employee):
        self.employee = employee
        # Defaults
        self.office_start_time = time(9, 0)
        self.office_end_time = time(18, 0)
        self.late_after_minutes = 15
        self.early_checkout_before_minutes = 30
        self.overtime_after_minutes = 0
        self.working_days = ['saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday']
        self.has_branch_working_days = False
        
        branch_schedule = None
        if employee and employee.branch:
            try:
                branch_schedule = employee.branch.schedule
            except:
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

        # Override late grace minutes with policy settings
        try:
            from apps.attendance.models import AttendancePolicy
            branch = getattr(employee, 'branch', None)
            policy = None
            if branch:
                policy = AttendancePolicy.objects.filter(branch=branch).first()
            if not policy:
                policy = AttendancePolicy.objects.filter(branch__isnull=True).first()
            if policy:
                self.late_after_minutes = policy.late_grace_minutes
        except Exception:
            pass

    def get_late_threshold(self):
        start = datetime.combine(datetime.today(), self.office_start_time)
        return (start + timedelta(minutes=self.late_after_minutes)).time()

    def get_early_checkout_threshold(self):
        end = datetime.combine(datetime.today(), self.office_end_time)
        return (end - timedelta(minutes=self.early_checkout_before_minutes)).time()

def get_branch_schedule(employee):
    """Get office schedule wrapper for employee"""
    if not employee:
        return None
    return DynamicSchedule(employee)

def resolve_employee_holiday(
    employee,
    target_date,
    schedule=None,
    branch_holidays=None,
    global_holidays=None,
):
    """
    Authoritative holiday resolver shared across attendance, reports, analytics, leave, and payroll.
    Weekly schedule precedence:
      1. Explicit employee weekly_holiday_policy (if non-empty)
      2. Configured branch OfficeSchedule.working_days (if configured)
      3. Company fallback (settings.WORKING_DAYS)
    Public/branch date holidays apply separately on top of the resolved weekly schedule.
    """
    day_name = target_date.strftime('%A').lower()

    # 1. Explicit employee weekly holiday policy
    master_employee = getattr(employee, 'master_employee', None)
    raw_policy = getattr(master_employee, 'weekly_holiday_policy', None) if master_employee else None
    employee_holidays = [d.strip().lower() for d in raw_policy.split(',') if d.strip()] if raw_policy else []

    if employee_holidays:
        is_weekly_holiday = day_name in employee_holidays
    else:
        # 2. Configured branch schedule working_days
        has_branch_cfg = getattr(schedule, 'has_branch_working_days', None)
        if has_branch_cfg is None:
            has_branch_cfg = bool(schedule and getattr(schedule, 'working_days', None))

        if has_branch_cfg and getattr(schedule, 'working_days', None):
            branch_working = {w.lower() for w in schedule.working_days}
            is_weekly_holiday = day_name not in branch_working
        else:
            # 3. Company weekly holiday fallback
            working_days = getattr(settings, 'WORKING_DAYS', [0, 1, 2, 3, 5, 6])
            is_weekly_holiday = target_date.weekday() not in working_days

    if is_weekly_holiday:
        return True

    # 4. Public / Branch date holidays (independent of weekly fallback)
    if global_holidays is not None:
        if target_date in global_holidays:
            return True
    else:
        try:
            from apps.branches.models import Holiday
            if Holiday.objects.filter(date=target_date, branch__isnull=True).exists():
                return True
        except Exception:
            pass

    branch_id = getattr(employee, 'branch_id', None)
    if branch_id is None:
        branch = getattr(employee, 'branch', None)
        branch_id = getattr(branch, 'id', None)

    if branch_id:
        if branch_holidays is not None:
            if isinstance(branch_holidays, dict):
                if target_date in branch_holidays.get(branch_id, set()):
                    return True
            elif isinstance(branch_holidays, (set, list, tuple)):
                if target_date in branch_holidays:
                    return True
        else:
            try:
                from apps.branches.models import Holiday
                if Holiday.objects.filter(date=target_date, branch_id=branch_id).exists():
                    return True
            except Exception:
                pass

    return False


def is_employee_holiday(employee, target_date):
    """
    Returns True if target_date is a weekly or public holiday for the employee.
    """
    schedule = get_branch_schedule(employee)
    return resolve_employee_holiday(employee, target_date, schedule=schedule)

def calculate_attendance_status(check_in_time, schedule):
    """
    Returns: 'on_time', 'late', 'holiday_attendance'
    """
    if not schedule:
        return 'on_time'
    
    if timezone.is_aware(check_in_time):
        session_date = timezone.localtime(check_in_time).date()
    else:
        session_date = check_in_time.date()
        
    employee = getattr(schedule, 'employee', None)
    if employee and is_employee_holiday(employee, session_date):
        return 'holiday_attendance'
        
    late_threshold_time = schedule.get_late_threshold()
    threshold = datetime.combine(session_date, late_threshold_time)
    if timezone.is_aware(check_in_time):
        threshold = timezone.make_aware(threshold, timezone.get_current_timezone())
        
    if check_in_time > threshold:
        return 'late'
    return 'on_time'

def calculate_overtime(check_out_time, schedule, employee, session_date=None):
    """
    Returns overtime in minutes or 0
    Only for employees with overtime_enabled=True
    """
    if not schedule:
        return 0
    if not employee.overtime_enabled:
        return 0
    
    if session_date is None:
        if timezone.is_aware(check_out_time):
            session_date = timezone.localtime(check_out_time).date()
        else:
            session_date = check_out_time.date()
            
    end_date = session_date
    if schedule.office_end_time < schedule.office_start_time:
        end_date = session_date + timedelta(days=1)

    end_time = datetime.combine(end_date, schedule.office_end_time)
    if timezone.is_aware(check_out_time):
        end_time = timezone.make_aware(end_time, timezone.get_current_timezone())
        
    overtime_starts = end_time + timedelta(
        minutes=schedule.overtime_after_minutes)
    
    if check_out_time > overtime_starts:
        diff = check_out_time - overtime_starts
        return int(diff.total_seconds() / 60)
    return 0

def calculate_early_checkout(check_out_time, schedule, session_date=None):
    """Returns True if employee left early"""
    if not schedule:
        return False
    
    if session_date is None:
        if timezone.is_aware(check_out_time):
            session_date = timezone.localtime(check_out_time).date()
        else:
            session_date = check_out_time.date()
            
    employee = getattr(schedule, 'employee', None)
    if employee and is_employee_holiday(employee, session_date):
        return False
        
    threshold_time = schedule.get_early_checkout_threshold()
    end_date = session_date
    if schedule.office_end_time < schedule.office_start_time:
        end_date = session_date + timedelta(days=1)

    threshold = datetime.combine(end_date, threshold_time)
    if timezone.is_aware(check_out_time):
        threshold = timezone.make_aware(threshold, timezone.get_current_timezone())
        
    return check_out_time < threshold


def resolve_shift_schedule_times(employee, snapshot=None):
    """
    Resolve (start_time, end_time, source, error) without guessing defaults.
    Precedence:
      1. Immutable session snapshot (if provided)
      2. Explicit master_employee.shift string
      3. Branch OfficeSchedule
    Returns (None, None, 'unresolved', reason) when no valid schedule exists.
    """
    if snapshot is not None:
        s_time = snapshot.get('shift_start_time') if isinstance(snapshot, dict) else getattr(snapshot, 'shift_start_time', None)
        e_time = snapshot.get('shift_end_time') if isinstance(snapshot, dict) else getattr(snapshot, 'shift_end_time', None)
        if s_time is not None and e_time is not None:
            return s_time, e_time, 'snapshot', None
        return None, None, 'unresolved', 'Incomplete schedule snapshot.'

    if not employee:
        return None, None, 'unresolved', 'Employee is required to resolve schedule.'

    master_employee = getattr(employee, 'master_employee', None)
    raw_shift = getattr(master_employee, 'shift', None) if master_employee else None
    if raw_shift and str(raw_shift).strip():
        s_time, e_time = parse_shift_times(str(raw_shift))
        if s_time is not None and e_time is not None:
            return s_time, e_time, 'employee_shift', None
        if str(raw_shift).strip().lower() != 'day shift':
            return None, None, 'unresolved', f'Unparseable employee shift: {raw_shift}'

    branch = getattr(employee, 'branch', None)
    branch_schedule = None
    if branch:
        try:
            branch_schedule = branch.schedule
        except Exception:
            branch_schedule = None

    if branch_schedule:
        s_time = branch_schedule.office_start_time
        e_time = branch_schedule.office_end_time
        if isinstance(s_time, str):
            s_time = parse_time_from_string(s_time)
        if isinstance(e_time, str):
            e_time = parse_time_from_string(e_time)
        if s_time is not None and e_time is not None:
            return s_time, e_time, 'branch_schedule', None

    return None, None, 'unresolved', 'No branch schedule or employee shift configured.'


def _localize_dt(dt, tz):
    if timezone.is_aware(dt):
        return timezone.localtime(dt, tz)
    return timezone.make_aware(dt, tz)


def resolve_checkout_window(check_in_time, employee=None, schedule_times=None, snapshot=None, tz=None):
    """
    Pure resolver for a concrete shift instance and its next daily shift-start boundary.
    Holidays never extend the deadline.
    """
    active_tz = tz or timezone.get_current_timezone()

    if schedule_times is not None:
        start_time, end_time = schedule_times
        source = 'explicit'
        err = None if (start_time is not None and end_time is not None) else 'Incomplete schedule times.'
    else:
        start_time, end_time, source, err = resolve_shift_schedule_times(employee, snapshot=snapshot)

    if err or start_time is None or end_time is None:
        return {
            'resolved': False,
            'source': source,
            'reason': err or 'unresolved_schedule',
            'shift_date': None,
            'shift_start_at': None,
            'shift_end_at': None,
            'checkout_deadline_at': None,
            'is_overnight': None,
        }

    local_in = _localize_dt(check_in_time, active_tz)
    is_overnight = end_time <= start_time

    # Early arrival / overnight shift-date assignment:
    # For overnight shifts (e.g. 22:00-06:00), arrivals strictly before end_time (00:00-05:59)
    # belong to the shift that started on the previous calendar day; arrivals >= end_time
    # (including early arrivals before 22:00) belong to the shift starting on local_in.date().
    if is_overnight and local_in.time() < end_time:
        shift_date = local_in.date() - timedelta(days=1)
    else:
        shift_date = local_in.date()

    shift_start_at = timezone.make_aware(datetime.combine(shift_date, start_time), active_tz)
    end_date = shift_date + timedelta(days=1) if is_overnight else shift_date
    shift_end_at = timezone.make_aware(datetime.combine(end_date, end_time), active_tz)
    checkout_deadline_at = timezone.make_aware(
        datetime.combine(shift_date + timedelta(days=1), start_time),
        active_tz,
    )

    return {
        'resolved': True,
        'source': source,
        'reason': None,
        'shift_date': shift_date,
        'shift_start_at': shift_start_at,
        'shift_end_at': shift_end_at,
        'checkout_deadline_at': checkout_deadline_at,
        'is_overnight': is_overnight,
    }


def evaluate_checkout_eligibility(check_in_time, checkout_time, employee=None, schedule_times=None, snapshot=None, tz=None):
    """
    Pure evaluator: permits checkout strictly before checkout_deadline_at;
    at or after checkout_deadline_at requires the missing-checkout correction workflow.
    """
    window = resolve_checkout_window(
        check_in_time=check_in_time,
        employee=employee,
        schedule_times=schedule_times,
        snapshot=snapshot,
        tz=tz,
    )
    if not window['resolved']:
        return {
            **window,
            'allowed': False,
            'requires_correction_workflow': False,
        }

    active_tz = tz or timezone.get_current_timezone()
    local_out = _localize_dt(checkout_time, active_tz)
    deadline = window['checkout_deadline_at']
    allowed = local_out < deadline
    return {
        **window,
        'allowed': allowed,
        'requires_correction_workflow': not allowed,
        'reason': None if allowed else 'checkout_window_expired',
    }
