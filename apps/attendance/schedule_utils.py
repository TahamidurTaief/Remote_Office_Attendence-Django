from datetime import datetime, timedelta
from django.utils import timezone

def _local_time(value):
    """Return the wall-clock time() in the active/local timezone.
    Fixes: aware DateTimeFields are stored in UTC; calling .time()
    directly on them returns UTC time, not Asia/Dhaka local time."""
    if hasattr(value, 'time'):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.time()
    return value

def get_branch_schedule(employee):
    """Get office schedule for employee's branch"""
    try:
        return employee.branch.schedule
    except:
        return None

def calculate_attendance_status(check_in_time, schedule):
    """
    Returns: 'on_time', 'late', 'early'
    """
    if not schedule:
        return 'on_time'
    
    late_threshold_time = schedule.get_late_threshold()
    if timezone.is_aware(check_in_time):
        session_date = timezone.localtime(check_in_time).date()
    else:
        session_date = check_in_time.date()
        
    from datetime import datetime
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
    
    from datetime import datetime
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
    
    from datetime import datetime
    if session_date is None:
        if timezone.is_aware(check_out_time):
            session_date = timezone.localtime(check_out_time).date()
        else:
            session_date = check_out_time.date()
            
    threshold_time = schedule.get_early_checkout_threshold()
    threshold = datetime.combine(session_date, threshold_time)
    if timezone.is_aware(check_out_time):
        threshold = timezone.make_aware(threshold, timezone.get_current_timezone())
        
    return check_out_time < threshold
