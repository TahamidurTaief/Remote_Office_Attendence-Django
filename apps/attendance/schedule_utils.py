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
        
        branch_schedule = None
        if employee and employee.branch:
            try:
                branch_schedule = employee.branch.schedule
            except:
                pass
                
        if branch_schedule:
            self.office_start_time = branch_schedule.office_start_time
            self.office_end_time = branch_schedule.office_end_time
            self.late_after_minutes = branch_schedule.late_after_minutes
            self.early_checkout_before_minutes = branch_schedule.early_checkout_before_minutes
            self.overtime_after_minutes = branch_schedule.overtime_after_minutes
            self.working_days = branch_schedule.working_days
            
        master_employee = getattr(employee, 'master_employee', None)
        if master_employee and master_employee.shift:
            s_time, e_time = parse_shift_times(master_employee.shift)
            if s_time is not None:
                self.office_start_time = s_time
            if e_time is not None:
                self.office_end_time = e_time

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

def is_employee_holiday(employee, target_date):
    """
    Returns True if target_date is a weekly or public holiday for the employee.
    WeeklyHolidayPolicy is resolved hierarchically:
      1. Employee-specific weekly holiday policy
      2. Branch level weekly holiday policy
      3. Company level weekly holiday policy
      4. Public/Branch Holidays
    """
    day_name = target_date.strftime('%A').lower()
    
    # 1. Employee-specific weekly holiday policy
    master_employee = getattr(employee, 'master_employee', None)
    if master_employee and master_employee.weekly_holiday_policy:
        employee_holidays = [d.strip().lower() for d in master_employee.weekly_holiday_policy.split(',') if d.strip()]
        if day_name in employee_holidays:
            return True
            
    # 2. Branch level weekly holiday policy
    schedule = get_branch_schedule(employee)
    if schedule:
        if day_name not in schedule.working_days:
            return True
            
    # 3. Company level weekly holiday policy
    working_days = getattr(settings, 'WORKING_DAYS', [0, 1, 2, 3, 5, 6])
    if target_date.weekday() not in working_days:
        return True
        
    # 4. Public / Branch Holidays
    from apps.branches.models import Holiday
    branch = getattr(employee, 'branch', None)
    if Holiday.objects.filter(date=target_date).filter(Q(branch=branch) | Q(branch__isnull=True)).exists():
        return True
        
    return False

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
        
    if is_employee_holiday(schedule.employee, session_date):
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
            
    end_time = datetime.combine(session_date, schedule.office_end_time)
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
            
    if is_employee_holiday(schedule.employee, session_date):
        return False
        
    threshold_time = schedule.get_early_checkout_threshold()
    threshold = datetime.combine(session_date, threshold_time)
    if timezone.is_aware(check_out_time):
        threshold = timezone.make_aware(threshold, timezone.get_current_timezone())
        
    return check_out_time < threshold
