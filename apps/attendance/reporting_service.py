import calendar as cal_mod
from datetime import date, datetime, timedelta
from collections import defaultdict
from django.utils import timezone
from django.conf import settings
from apps.employees.models import EmployeeProfile
from apps.branches.models import Branch, OfficeSchedule, Holiday
from apps.attendance.models import Attendance, AttendancePolicy
from apps.leave.models import LeaveRequest, LeaveType, LeaveBalance
from apps.attendance.schedule_utils import get_branch_schedule, parse_time_from_string, parse_shift_times

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
    def __init__(self, employee, policies_by_branch, global_policy):
        self.employee = employee
        self.office_start_time = datetime.time(datetime.today().replace(hour=9, minute=0))
        self.office_end_time = datetime.time(datetime.today().replace(hour=18, minute=0))
        self.late_after_minutes = 15
        self.early_checkout_before_minutes = 30
        self.overtime_after_minutes = 0
        self.working_days = ['saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday']
        
        branch = employee.branch
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
            self.working_days = branch_schedule.working_days
            
        master_employee = getattr(employee, 'master_employee', None)
        if master_employee and master_employee.shift:
            s_time, e_time = parse_shift_times(master_employee.shift)
            if s_time is not None:
                self.office_start_time = s_time
            if e_time is not None:
                self.office_end_time = e_time

        branch_id = branch.id if branch else None
        policy = policies_by_branch.get(branch_id, global_policy)
        if policy:
            self.late_after_minutes = policy.late_grace_minutes

def is_employee_holiday_optimized(employee, target_date, schedule, branch_holidays, global_holidays):
    day_name = target_date.strftime('%A').lower()
    
    # 1. Employee weekly holiday policy
    master_employee = getattr(employee, 'master_employee', None)
    if master_employee and master_employee.weekly_holiday_policy:
        employee_holidays = [d.strip().lower() for d in master_employee.weekly_holiday_policy.split(',') if d.strip()]
        if day_name in employee_holidays:
            return True
            
    # 2. Branch weekly holiday policy
    if schedule:
        if day_name not in schedule.working_days:
            return True
            
    # 3. Company weekly holiday policy
    working_days = getattr(settings, 'WORKING_DAYS', [0, 1, 2, 3, 5, 6])
    if target_date.weekday() not in working_days:
        return True
        
    # 4. Public / Branch Holidays
    if target_date in global_holidays:
        return True
    branch_id = employee.branch_id
    if branch_id and target_date in branch_holidays[branch_id]:
        return True
        
    return False

def get_monthly_report_data(year, month, employee_id=None, branch_id=None):
    """
    Canonical monthly attendance statistics service (optimized for speed & database access).
    """
    days_in_month = cal_mod.monthrange(year, month)[1]
    all_days = [date(year, month, d) for d in range(1, days_in_month + 1)]
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)

    # 1. Fetch active employees and employees active during reporting period
    from django.db.models import Q
    employees_qs = EmployeeProfile.objects.filter(
        Q(is_active=True) |
        Q(master_employee__employment_history__field_changed='status', master_employee__employment_history__effective_date__gte=month_start) |
        Q(attendances__date__gte=month_start, attendances__date__lte=month_end)
    ).distinct().select_related(
        'branch',
        'branch__schedule',
    ).order_by('full_name')

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

    # 3. Fetch all attendances for the month in one query (ordered deterministically)
    attendances_qs = Attendance.objects.filter(
        date__gte=month_start,
        date__lte=month_end,
        employee_id__in=employee_ids
    ).order_by('date', 'check_in_time')

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

    balances_qs = LeaveBalance.objects.filter(employee_id__in=employee_ids, year=year)
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
    display_att_lookup = defaultdict(dict)
    employee_stats = {}
    rows = []

    total_present = total_absent = total_on_leave = total_late = total_field = 0

    for emp in employees:
        schedule = OptimizedSchedule(emp, policies_by_branch, global_policy)
        emp_working_days = sum(
            1 for d in all_days 
            if not is_employee_holiday_optimized(emp, d, schedule, branch_holidays, global_holidays)
        )

        present_count = 0
        late_count = 0
        total_ot_minutes = 0
        absent_count = 0
        holiday_work_count = 0
        on_leave_count = 0
        field_visit_count = 0
        total_hours = 0.0

        for d in all_days:
            day_atts = att_by_emp_date[(emp.id, d)]
            
            main_att = next((a for a in day_atts if a.attendance_type == 'check_in'), None)
            if not main_att and day_atts:
                main_att = day_atts[0]
            
            if main_att:
                display_att_lookup[emp.id][d] = main_att
                
            has_check_in = any(a.attendance_type == 'check_in' for a in day_atts)
            has_field_visit = any(a.attendance_type == 'field_visit' for a in day_atts)
            has_any_attendance = has_check_in or has_field_visit
            
            if has_check_in or has_field_visit:
                present_count += 1
            if has_field_visit:
                field_visit_count += sum(1 for a in day_atts if a.attendance_type == 'field_visit')

            day_has_late = False
            for a in day_atts:
                if a.attendance_type == 'check_in':
                    if a.status == 'late':
                        day_has_late = True
                    if getattr(a, 'overtime_minutes', 0) > 0:
                        total_ot_minutes += a.overtime_minutes
                    if a.total_hours:
                        total_hours += float(a.total_hours)

            if day_has_late:
                late_count += 1

            is_holiday = is_employee_holiday_optimized(emp, d, schedule, branch_holidays, global_holidays)
            if not is_holiday:
                if not has_any_attendance:
                    is_on_leave = d in approved_leaves_map[emp.id]
                    if is_on_leave:
                        on_leave_count += 1
                    else:
                        absent_count += 1
            else:
                if has_check_in:
                    holiday_work_count += 1

        if getattr(emp, 'overtime_enabled', False) and total_ot_minutes > 0:
            ot_hours = total_ot_minutes / 60
            ot_display = f"{int(ot_hours)}h {int(total_ot_minutes % 60)}m"
        else:
            ot_display = '-'
            
        att_pct = round((present_count / emp_working_days * 100) if emp_working_days else 0, 1)

        employee_stats[emp.id] = {
            'present_count': present_count,
            'late_count': late_count,
            'total_ot_minutes': total_ot_minutes,
            'absent_count': absent_count,
            'holiday_work_count': holiday_work_count,
            'overtime_display': ot_display,
            'is_overtime_enabled': getattr(emp, 'overtime_enabled', False)
        }

        rows.append({
            'employee': emp,
            'present': present_count,
            'absent': absent_count,
            'on_leave': on_leave_count,
            'late': late_count,
            'field_visits': field_visit_count,
            'total_hours': round(total_hours, 2),
            'att_pct': att_pct,
            'leave_balances': balances_by_emp[emp.id]
        })

        total_present += present_count
        total_absent += absent_count
        total_on_leave += on_leave_count
        total_late += late_count
        total_field += field_visit_count

    avg_att_pct = round(
        sum(r['att_pct'] for r in rows) / len(rows) if rows else 0, 1
    )

    return {
        'year': year,
        'month': month,
        'days_in_month': days_in_month,
        'all_days': all_days,
        'employees': employees,
        'att_lookup': display_att_lookup,
        'employee_stats': employee_stats,
        'approved_leaves': approved_leaves_map,
        'rows': rows,
        'total_present': total_present,
        'total_absent': total_absent,
        'total_on_leave': total_on_leave,
        'total_late': total_late,
        'total_field': total_field,
        'avg_att_pct': avg_att_pct,
        'working_days': working_days,
    }
