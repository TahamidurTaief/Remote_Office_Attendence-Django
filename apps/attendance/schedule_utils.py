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
    
    late_threshold = schedule.get_late_threshold()
    check_in = _local_time(check_in_time)
    
    if check_in > late_threshold:
        return 'late'
    return 'on_time'

def calculate_overtime(check_out_time, schedule, employee):
    """
    Returns overtime in minutes or 0
    Only for employees with overtime_enabled=True
    """
    if not schedule:
        return 0
    if not employee.overtime_enabled:
        return 0
    
    from datetime import datetime
    end_time = datetime.combine(
        datetime.today(), schedule.office_end_time)
    overtime_starts = end_time + timedelta(
        minutes=schedule.overtime_after_minutes)
    
    checkout = datetime.combine(
        datetime.today(),
        _local_time(check_out_time)
    )
    
    if checkout > overtime_starts:
        diff = checkout - overtime_starts
        return int(diff.total_seconds() / 60)
    return 0

def calculate_early_checkout(check_out_time, schedule):
    """Returns True if employee left early"""
    if not schedule:
        return False
    
    threshold = schedule.get_early_checkout_threshold()
    checkout = _local_time(check_out_time)
    
    return checkout < threshold
