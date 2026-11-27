from collections import defaultdict
from datetime import date, timedelta
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone

from apps.attendance.models import Attendance, AttendancePolicy
from apps.attendance.reporting_service import (
    OptimizedSchedule,
    build_employment_eligibility_map,
    calculate_employee_day,
    is_employee_holiday_optimized,
)
from apps.attendance.scoping import get_scoped_employee_queryset
from apps.branches.models import Branch, Holiday, OfficeSchedule
from apps.employees.models import Department, EmployeeProfile
from apps.leave.models import LeaveRequest

MAX_CUSTOM_RANGE_DAYS = 90
DEFAULT_PAGE_SIZE = 10
_EMPTY_SET = frozenset()
_EMPTY_LIST = ()


class _BranchLite:
    __slots__ = ("id", "name", "schedule")

    def __init__(self, id, name, schedule=None):
        self.id = id
        self.name = name
        self.schedule = schedule


class _MasterLite:
    __slots__ = ("id", "status", "shift", "weekly_holiday_policy", "joined_date", "branch_id", "branch_name", "department_id", "department_name")

    def __init__(self, id, status, shift, weekly_holiday_policy, joined_date, branch_id, branch_name, department_id, department_name):
        self.id = id
        self.status = status
        self.shift = shift
        self.weekly_holiday_policy = weekly_holiday_policy
        self.joined_date = joined_date
        self.branch_id = branch_id
        self.branch_name = branch_name
        self.department_id = department_id
        self.department_name = department_name


class _EmpLite:
    __slots__ = ("id", "full_name", "branch_id", "branch", "department", "joined_date", "overtime_enabled", "master_employee")

    def __init__(self, id, full_name, branch_id, branch, department, joined_date, overtime_enabled, master_employee):
        self.id = id
        self.full_name = full_name
        self.branch_id = branch_id
        self.branch = branch
        self.department = department
        self.joined_date = joined_date
        self.overtime_enabled = overtime_enabled
        self.master_employee = master_employee


class _AttLite:
    __slots__ = (
        "id",
        "employee_id",
        "date",
        "attendance_type",
        "type",
        "status",
        "check_in_time",
        "check_out_time",
        "total_hours",
        "overtime_minutes",
        "is_expired",
    )

    def __init__(
        self,
        id,
        employee_id,
        date,
        attendance_type,
        type,
        status,
        check_in_time,
        check_out_time,
        total_hours,
        overtime_minutes,
        is_expired,
    ):
        self.id = id
        self.employee_id = employee_id
        self.date = date
        self.attendance_type = attendance_type
        self.type = type
        self.status = status
        self.check_in_time = check_in_time
        self.check_out_time = check_out_time
        self.total_hours = total_hours
        self.overtime_minutes = overtime_minutes
        self.is_expired = is_expired


def _parse_date(val):
    if not val:
        return None
    try:
        return date.fromisoformat(str(val).strip())
    except (ValueError, TypeError):
        return None


def _resolve_date_range(params, today):
    range_key = (params.get("range") or "7").strip().lower()
    if range_key not in ("7", "30", "90", "custom"):
        range_key = "7"

    range_error = None
    range_clamped = False
    range_rejected = False

    if range_key == "custom":
        start_date = _parse_date(params.get("start_date"))
        end_date = _parse_date(params.get("end_date"))
        if not start_date or not end_date:
            range_error = "Invalid custom date range."
            range_rejected = True
            end_date = today
            start_date = today
        elif start_date > end_date:
            range_error = "Invalid custom date range: start date must not be after end date."
            range_rejected = True
        elif (end_date - start_date).days + 1 > MAX_CUSTOM_RANGE_DAYS:
            range_error = f"Custom range cannot exceed {MAX_CUSTOM_RANGE_DAYS} days."
            range_rejected = True
    else:
        days = int(range_key)
        end_date = today
        start_date = today - timedelta(days=days - 1)

    num_days = (end_date - start_date).days + 1
    prev_end_date = start_date - timedelta(days=1)
    prev_start_date = prev_end_date - timedelta(days=num_days - 1)

    return {
        "range_key": range_key,
        "start_date": start_date,
        "end_date": end_date,
        "prev_start_date": prev_start_date,
        "prev_end_date": prev_end_date,
        "num_days": num_days,
        "range_error": range_error,
        "range_clamped": range_clamped,
        "range_rejected": range_rejected,
    }


def _get_authorized_employees(user, role_variant=None):
    """
    Returns authorized EmployeeProfile queryset using authoritative scoping.
    View-as (role_variant) never expands actual permissions.
    """
    base_qs = EmployeeProfile.objects.filter(is_active=True)

    # Try reports.view first, then attendance.view, then dashboard.view
    # Superusers get all via get_scoped_employee_queryset.
    qs = get_scoped_employee_queryset(user, permission_code="reports.view", action="view", base_qs=base_qs)
    if not user.is_superuser and not qs.exists():
        qs = get_scoped_employee_queryset(user, permission_code="attendance.view", action="view", base_qs=base_qs)
    if not user.is_superuser and not qs.exists():
        qs = get_scoped_employee_queryset(user, permission_code="dashboard.view", action="view", base_qs=base_qs)

    # View-as narrowing for superuser previewing employee role
    if user.is_superuser and role_variant == "employee":
        emp_profile = getattr(user, "employee_profile", None)
        if emp_profile:
            qs = qs.filter(pk=emp_profile.pk)
        else:
            qs = qs.none()

    return qs


def _emp_branch_info(emp):
    master = getattr(emp, "master_employee", None)
    if master and getattr(master, "branch_id", None):
        return master.branch_id, master.branch_name
    if emp.branch:
        return emp.branch.id, emp.branch.name
    return None, "Unassigned"


def _emp_dept_info(emp):
    master = getattr(emp, "master_employee", None)
    if master and getattr(master, "department_id", None):
        return f"id:{master.department_id}", master.department_name
    if emp.department and str(emp.department).strip():
        return f"name:{emp.department.strip().lower()}", emp.department.strip()
    return "none", "Unassigned"


def _compute_period_metrics(
    employees,
    schedules_by_emp,
    att_map,
    approved_leaves,
    branch_holidays,
    global_holidays,
    start_date,
    end_date,
    today,
    holiday_cache=None,
    emp_meta=None,
    include_breakdowns=True,
    eligibility_map=None,
):
    """
    Computes authoritative employee-day metrics over [start_date, end_date].
    Each employee-day is counted once.
    """
    date_list = []
    curr = start_date
    while curr <= end_date:
        date_list.append(curr)
        curr += timedelta(days=1)

    total_present = 0
    total_late = 0
    total_absent = 0
    total_on_leave = 0
    total_pending = 0
    total_missing_checkout = 0
    total_leave_conflicts = 0
    eligible_working_days = 0

    office_only_days = 0
    field_only_days = 0
    mixed_days = 0

    if holiday_cache is None:
        holiday_cache = {}
    if emp_meta is None:
        emp_meta = {}
        for emp in employees:
            b_key, b_name = _emp_branch_info(emp)
            d_key, d_name = _emp_dept_info(emp)
            b_key_str = str(b_key) if b_key is not None else "none"
            emp_meta[emp.id] = (b_key_str, b_name, d_key, d_name)

    daily_series = [] if include_breakdowns else None
    dept_stats = defaultdict(lambda: {"name": "", "present": 0, "eligible": 0}) if include_breakdowns else None
    branch_stats = defaultdict(lambda: {"name": "", "present": 0, "eligible": 0}) if include_breakdowns else None
    emp_stats = (
        {
            emp.id: {
                "employee": emp,
                "branch_name": emp_meta[emp.id][1],
                "dept_name": emp_meta[emp.id][3],
                "present": 0,
                "late": 0,
                "absent": 0,
                "on_leave": 0,
                "pending": 0,
                "eligible": 0,
                "office_only": 0,
                "field_only": 0,
                "mixed": 0,
                "missing_checkout": 0,
            }
            for emp in employees
        }
        if include_breakdowns
        else None
    )

    # Pre-bind per-employee loop invariants
    emp_records = []
    for emp in employees:
        eid = emp.id
        schedule = schedules_by_emp[eid]
        master = emp.master_employee
        wh_policy = master.weekly_holiday_policy if master else ""
        wd_tuple = tuple(schedule.working_days) if getattr(schedule, "working_days", None) else ()
        h_key = (wh_policy, emp.branch_id, wd_tuple)
        emp_records.append((
            emp,
            eid,
            schedule,
            h_key,
            approved_leaves.get(eid, _EMPTY_SET),
            emp_meta[eid],
            emp_stats[eid] if include_breakdowns else None,
        ))

    for d in date_list:
        day_present = 0
        day_late = 0
        day_absent = 0
        day_on_leave = 0
        day_pending = 0

        for emp, eid, schedule, h_key, emp_leaves, meta, es in emp_records:
            cache_k = (h_key, d)
            is_holiday = holiday_cache.get(cache_k)
            if is_holiday is None:
                is_holiday = is_employee_holiday_optimized(emp, d, schedule, branch_holidays, global_holidays)
                holiday_cache[cache_k] = is_holiday

            is_on_leave = d in emp_leaves
            emp_day_atts = att_map.get((eid, d), _EMPTY_LIST)

            day_calc = calculate_employee_day(
                emp,
                d,
                emp_day_atts,
                schedule=schedule,
                is_holiday=is_holiday,
                is_on_leave=is_on_leave,
                today=today,
                eligibility=eligibility_map.get(eid) if eligibility_map else None,
            )

            status = day_calc["status"]
            if day_calc["is_future"] or status == "not_employed":
                continue

            if status == "scheduled" and day_calc["status_display"] == "Pending":
                total_pending += 1
                if include_breakdowns:
                    day_pending += 1
                    es["pending"] += 1
                continue

            if status == "holiday":
                continue

            # Eligible working day (Present, Absent, or On Leave; or Holiday Attendance)
            eligible_working_days += 1
            if include_breakdowns:
                b_key_str, b_name, d_key, d_name = meta
                if not dept_stats[d_key]["name"]:
                    dept_stats[d_key]["name"] = d_name
                if not branch_stats[b_key_str]["name"]:
                    branch_stats[b_key_str]["name"] = b_name
                es["eligible"] += 1
                dept_stats[d_key]["eligible"] += 1
                branch_stats[b_key_str]["eligible"] += 1

            if day_calc["is_present"]:
                total_present += 1
                if include_breakdowns:
                    day_present += 1
                    es["present"] += 1
                    dept_stats[d_key]["present"] += 1
                    branch_stats[b_key_str]["present"] += 1

                if day_calc["is_late"]:
                    total_late += 1
                    if include_breakdowns:
                        day_late += 1
                        es["late"] += 1

                if day_calc["is_missing_checkout"]:
                    total_missing_checkout += 1
                    if include_breakdowns:
                        es["missing_checkout"] += 1

                if day_calc["has_leave_conflict"]:
                    total_leave_conflicts += 1

                if include_breakdowns:
                    has_office = len(day_calc["check_in_sessions"]) > 0
                    has_field = len(day_calc["field_visits"]) > 0
                    if has_office and has_field:
                        mixed_days += 1
                        es["mixed"] += 1
                    elif has_office:
                        office_only_days += 1
                        es["office_only"] += 1
                    elif has_field:
                        field_only_days += 1
                        es["field_only"] += 1
            elif day_calc["is_on_leave"]:
                total_on_leave += 1
                if include_breakdowns:
                    day_on_leave += 1
                    es["on_leave"] += 1
            elif day_calc["is_absent"]:
                total_absent += 1
                if include_breakdowns:
                    day_absent += 1
                    es["absent"] += 1

        if include_breakdowns:
            daily_series.append({
                "date": d,
                "label": d.strftime("%d %b"),
                "present": day_present,
                "late": day_late,
                "absent": day_absent,
                "on_leave": day_on_leave,
                "pending": day_pending,
            })

    attendance_rate = (
        round((total_present / eligible_working_days) * 100, 1)
        if eligible_working_days > 0
        else None
    )

    return {
        "total_present": total_present,
        "total_late": total_late,
        "total_absent": total_absent,
        "total_on_leave": total_on_leave,
        "total_pending": total_pending,
        "total_missing_checkout": total_missing_checkout,
        "total_leave_conflicts": total_leave_conflicts,
        "eligible_working_days": eligible_working_days,
        "attendance_rate": attendance_rate,
        "office_only_days": office_only_days,
        "field_only_days": field_only_days,
        "mixed_days": mixed_days,
        "daily_series": daily_series or [],
        "dept_stats": dept_stats,
        "branch_stats": branch_stats,
        "emp_stats": emp_stats,
    }


def _empty_summary():
    return {
        "total_present": 0,
        "total_late": 0,
        "total_absent": 0,
        "total_on_leave": 0,
        "total_pending": 0,
        "total_missing_checkout": 0,
        "total_leave_conflicts": 0,
        "eligible_working_days": 0,
        "attendance_rate": None,
        "office_only_days": 0,
        "field_only_days": 0,
        "mixed_days": 0,
        "daily_series": [],
        "dept_stats": {},
        "branch_stats": {},
        "emp_stats": {},
    }


def get_attendance_analytics(user, params=None, role_variant=None):
    """
    Authoritative, scoped attendance analytics for the dashboard.
    Read-only: performs zero attendance writes and bounded grouped/bulk queries.
    """
    params = params or {}
    today = timezone.localdate()
    dr = _resolve_date_range(params, today)

    selected_branch = (params.get("branch") or "").strip()
    selected_dept = (params.get("department") or "").strip()
    group_by = (params.get("group_by") or "department").strip().lower()
    if group_by not in ("department", "branch"):
        group_by = "department"

    can_view_reports = bool(
        user.is_superuser
        or _has_report_permission(user)
    )

    if dr["range_rejected"]:
        empty_summary = _empty_summary()
        return {
            "analytics_range": dr["range_key"],
            "analytics_start_date": dr["start_date"],
            "analytics_end_date": dr["end_date"],
            "analytics_prev_start_date": dr["prev_start_date"],
            "analytics_prev_end_date": dr["prev_end_date"],
            "analytics_num_days": dr["num_days"],
            "analytics_range_error": dr["range_error"],
            "analytics_range_clamped": dr["range_clamped"],
            "analytics_selected_branch": selected_branch,
            "analytics_selected_dept": selected_dept,
            "analytics_group_by": group_by,
            "analytics_branch_options": [],
            "analytics_dept_options": [],
            "analytics_employee_count": 0,
            "analytics_summary": empty_summary,
            "analytics_prev_summary": empty_summary,
            "analytics_rate_delta": None,
            "analytics_bar_items": [],
            "analytics_page_obj": Paginator([], DEFAULT_PAGE_SIZE).get_page(1),
            "analytics_chart_payload": {
                "trend": {"labels": [], "present": [], "late": [], "absent": []},
                "groupBar": {"labels": [], "rates": [], "present": [], "eligible": []},
                "workMode": {"labels": ["Office Only", "Field Only", "Mixed (Office + Field)"], "values": [0, 0, 0]},
            },
            "analytics_can_view_reports": can_view_reports,
        }

    scoped_qs = _get_authorized_employees(user, role_variant=role_variant)

    branch_options_map = {}
    dept_options_map = {}

    # Only query scoped_meta separately if a filter is active; otherwise derive from employees below
    if selected_branch or selected_dept:
        scoped_meta = scoped_qs.values_list(
            "branch_id",
            "branch__name",
            "master_employee__branch_id",
            "master_employee__branch__name",
            "master_employee__department_id",
            "master_employee__department__name",
            "department",
        )
        for b_id, b_name, mb_id, mb_name, md_id, md_name, prof_dept in scoped_meta:
            eff_b_id = mb_id or b_id
            eff_b_name = mb_name or b_name
            if eff_b_id and eff_b_name:
                branch_options_map[str(eff_b_id)] = eff_b_name
            if md_id and md_name:
                dept_options_map[f"id:{md_id}"] = md_name
            elif prof_dept and str(prof_dept).strip():
                dept_options_map[f"name:{prof_dept.strip().lower()}"] = prof_dept.strip()

    filtered_qs = scoped_qs
    if selected_branch:
        try:
            b_pk = int(selected_branch)
            filtered_qs = filtered_qs.filter(Q(branch_id=b_pk) | Q(master_employee__branch_id=b_pk))
        except ValueError:
            filtered_qs = filtered_qs.none()

    if selected_dept:
        if selected_dept.startswith("id:"):
            try:
                d_pk = int(selected_dept.split(":", 1)[1])
                filtered_qs = filtered_qs.filter(master_employee__department_id=d_pk)
            except ValueError:
                filtered_qs = filtered_qs.none()
        elif selected_dept.startswith("name:"):
            d_name = selected_dept.split(":", 1)[1]
            filtered_qs = filtered_qs.filter(
                Q(department__iexact=d_name) | Q(master_employee__department__name__iexact=d_name)
            )
        else:
            filtered_qs = filtered_qs.filter(
                Q(department__iexact=selected_dept) | Q(master_employee__department__name__iexact=selected_dept)
            )

    emp_rows = list(
        filtered_qs.order_by("full_name", "id").values_list(
            "id",
            "full_name",
            "branch_id",
            "branch__name",
            "department",
            "joined_date",
            "overtime_enabled",
            "master_employee_id",
            "master_employee__status",
            "master_employee__shift",
            "master_employee__weekly_holiday_policy",
            "master_employee__joined_date",
            "master_employee__branch_id",
            "master_employee__branch__name",
            "master_employee__department_id",
            "master_employee__department__name",
        )
    )

    if not emp_rows:
        empty_summary = _empty_summary()
        return {
            "analytics_range": dr["range_key"],
            "analytics_start_date": dr["start_date"],
            "analytics_end_date": dr["end_date"],
            "analytics_prev_start_date": dr["prev_start_date"],
            "analytics_prev_end_date": dr["prev_end_date"],
            "analytics_num_days": dr["num_days"],
            "analytics_range_error": dr["range_error"],
            "analytics_range_clamped": dr["range_clamped"],
            "analytics_selected_branch": selected_branch,
            "analytics_selected_dept": selected_dept,
            "analytics_group_by": group_by,
            "analytics_branch_options": sorted(branch_options_map.items(), key=lambda x: x[1]),
            "analytics_dept_options": sorted(dept_options_map.items(), key=lambda x: x[1]),
            "analytics_employee_count": 0,
            "analytics_summary": empty_summary,
            "analytics_prev_summary": empty_summary,
            "analytics_rate_delta": None,
            "analytics_bar_items": [],
            "analytics_page_obj": Paginator([], DEFAULT_PAGE_SIZE).get_page(1),
            "analytics_chart_payload": {
                "trend": {"labels": [], "present": [], "late": [], "absent": []},
                "groupBar": {"labels": [], "rates": [], "present": [], "eligible": []},
                "workMode": {"labels": ["Office Only", "Field Only", "Mixed (Office + Field)"], "values": [0, 0, 0]},
            },
            "analytics_can_view_reports": can_view_reports,
        }

    # Load branch schedules once instead of hydrating per-employee ORM joins
    branch_schedules = {
        s.branch_id: s
        for s in OfficeSchedule.objects.all()
    }
    branch_cache = {}
    employees = []
    emp_ids = []
    emp_meta = {}

    for (
        eid,
        full_name,
        b_id,
        b_name,
        prof_dept,
        joined_date,
        overtime_enabled,
        master_id,
        m_status,
        m_shift,
        m_wh_policy,
        m_joined_date,
        mb_id,
        mb_name,
        md_id,
        md_name,
    ) in emp_rows:
        branch_obj = None
        if b_id is not None:
            branch_obj = branch_cache.get(b_id)
            if branch_obj is None:
                branch_obj = _BranchLite(b_id, b_name, branch_schedules.get(b_id))
                branch_cache[b_id] = branch_obj

        master_obj = (
            _MasterLite(master_id, m_status, m_shift, m_wh_policy, m_joined_date, mb_id, mb_name, md_id, md_name)
            if master_id is not None
            else None
        )
        emp = _EmpLite(eid, full_name, b_id, branch_obj, prof_dept, joined_date, overtime_enabled, master_obj)
        employees.append(emp)
        emp_ids.append(eid)

        eff_b_id, eff_b_name = _emp_branch_info(emp)
        eff_d_key, eff_d_name = _emp_dept_info(emp)
        emp_meta[eid] = (str(eff_b_id) if eff_b_id is not None else "none", eff_b_name, eff_d_key, eff_d_name)
        if not (selected_branch or selected_dept):
            if eff_b_id is not None:
                branch_options_map[str(eff_b_id)] = eff_b_name
            if eff_d_key != "none":
                dept_options_map[eff_d_key] = eff_d_name

    # Bulk load supporting data covering both current and previous periods
    policies = list(AttendancePolicy.objects.all())
    policies_by_branch = {p.branch_id: p for p in policies if p.branch_id is not None}
    global_policy = next((p for p in policies if p.branch_id is None), None)

    # Deduplicate OptimizedSchedule construction for employees sharing (branch_id, shift)
    schedule_cache = {}
    schedules_by_emp = {}
    for emp in employees:
        m_shift = emp.master_employee.shift if emp.master_employee else None
        s_key = (emp.branch_id, m_shift)
        sched = schedule_cache.get(s_key)
        if sched is None:
            sched = OptimizedSchedule(emp, policies_by_branch, global_policy)
            schedule_cache[s_key] = sched
        schedules_by_emp[emp.id] = sched

    holidays = list(
        Holiday.objects.filter(
            date__gte=dr["prev_start_date"],
            date__lte=dr["end_date"],
        ).values("date", "branch_id")
    )
    branch_holidays = defaultdict(set)
    global_holidays = set()
    for h in holidays:
        if h["branch_id"]:
            branch_holidays[h["branch_id"]].add(h["date"])
        else:
            global_holidays.add(h["date"])

    approved_leaves = defaultdict(set)
    att_map = defaultdict(list)

    leave_rows = LeaveRequest.objects.filter(
        employee_id__in=emp_ids,
        status="approved",
        start_date__lte=dr["end_date"],
        end_date__gte=dr["prev_start_date"],
    ).values_list("employee_id", "start_date", "end_date")

    for emp_id, s_d, e_d in leave_rows:
        s_clamped = max(s_d, dr["prev_start_date"])
        e_clamped = min(e_d, dr["end_date"])
        curr_d = s_clamped
        emp_leave_set = approved_leaves[emp_id]
        while curr_d <= e_clamped:
            emp_leave_set.add(curr_d)
            curr_d += timedelta(days=1)

    att_rows = (
        Attendance.objects.filter(
            employee_id__in=emp_ids,
            date__gte=dr["prev_start_date"],
            date__lte=dr["end_date"],
            is_expired=False,
        )
        .values_list(
            "id",
            "employee_id",
            "date",
            "attendance_type",
            "type",
            "status",
            "check_in_time",
            "check_out_time",
            "total_hours",
            "overtime_minutes",
            "is_expired",
        )
        .order_by("date", "check_in_time")
    )
    for row in att_rows:
        att_map[(row[1], row[2])].append(_AttLite(*row))

    eligibility_map = build_employment_eligibility_map(employees)
    holiday_cache = {}
    curr_metrics = _compute_period_metrics(
        employees,
        schedules_by_emp,
        att_map,
        approved_leaves,
        branch_holidays,
        global_holidays,
        dr["start_date"],
        dr["end_date"],
        today,
        holiday_cache=holiday_cache,
        emp_meta=emp_meta,
        include_breakdowns=True,
        eligibility_map=eligibility_map,
    )

    prev_metrics = _compute_period_metrics(
        employees,
        schedules_by_emp,
        att_map,
        approved_leaves,
        branch_holidays,
        global_holidays,
        dr["prev_start_date"],
        dr["prev_end_date"],
        today,
        holiday_cache=holiday_cache,
        emp_meta=emp_meta,
        include_breakdowns=False,
        eligibility_map=eligibility_map,
    )

    # Previous period comparison (never fabricate when either denominator is 0)
    curr_rate = curr_metrics["attendance_rate"]
    prev_rate = prev_metrics["attendance_rate"]
    if curr_rate is not None and prev_rate is not None:
        rate_delta = round(curr_rate - prev_rate, 1)
    else:
        rate_delta = None

    # Bar chart grouping (department or branch)
    group_source = curr_metrics["branch_stats"] if group_by == "branch" else curr_metrics["dept_stats"]
    bar_items = []
    for _, g in group_source.items():
        if g["eligible"] > 0:
            rate = round((g["present"] / g["eligible"]) * 100, 1)
        else:
            rate = None
        bar_items.append({
            "name": g["name"],
            "present": g["present"],
            "eligible": g["eligible"],
            "rate": rate,
        })
    bar_items.sort(key=lambda x: (x["rate"] is not None, x["rate"] or 0.0, x["present"]), reverse=True)
    bar_items = bar_items[:12]

    # Paginate first, then materialize detail rows only for the current page
    page_num = params.get("page") or 1
    paginator = Paginator(employees, DEFAULT_PAGE_SIZE)
    page_obj = paginator.get_page(page_num)
    page_rows = []
    for emp in page_obj.object_list:
        es = curr_metrics["emp_stats"][emp.id]
        es["rate"] = round((es["present"] / es["eligible"]) * 100, 1) if es["eligible"] > 0 else None
        page_rows.append(es)
    page_obj.object_list = page_rows

    # Safe chart payload for json_script
    chart_payload = {
        "trend": {
            "labels": [d["label"] for d in curr_metrics["daily_series"]],
            "present": [d["present"] for d in curr_metrics["daily_series"]],
            "late": [d["late"] for d in curr_metrics["daily_series"]],
            "absent": [d["absent"] for d in curr_metrics["daily_series"]],
        },
        "groupBar": {
            "labels": [b["name"] for b in bar_items],
            "rates": [b["rate"] if b["rate"] is not None else 0 for b in bar_items],
            "present": [b["present"] for b in bar_items],
            "eligible": [b["eligible"] for b in bar_items],
        },
        "workMode": {
            "labels": ["Office Only", "Field Only", "Mixed (Office + Field)"],
            "values": [
                curr_metrics["office_only_days"],
                curr_metrics["field_only_days"],
                curr_metrics["mixed_days"],
            ],
        },
    }

    return {
        "analytics_range": dr["range_key"],
        "analytics_start_date": dr["start_date"],
        "analytics_end_date": dr["end_date"],
        "analytics_prev_start_date": dr["prev_start_date"],
        "analytics_prev_end_date": dr["prev_end_date"],
        "analytics_num_days": dr["num_days"],
        "analytics_range_error": dr["range_error"],
        "analytics_range_clamped": dr["range_clamped"],
        "analytics_selected_branch": selected_branch,
        "analytics_selected_dept": selected_dept,
        "analytics_group_by": group_by,
        "analytics_branch_options": sorted(branch_options_map.items(), key=lambda x: x[1]),
        "analytics_dept_options": sorted(dept_options_map.items(), key=lambda x: x[1]),
        "analytics_employee_count": len(employees),
        "analytics_summary": curr_metrics,
        "analytics_prev_summary": prev_metrics,
        "analytics_rate_delta": rate_delta,
        "analytics_bar_items": bar_items,
        "analytics_page_obj": page_obj,
        "analytics_chart_payload": chart_payload,
        "analytics_can_view_reports": can_view_reports,
        "analytics_can_view_finance": has_finance_analytics_permission(user),
    }


def _has_report_permission(user):
    try:
        from apps.accounts.engine import PermissionEngine
        return PermissionEngine.evaluate(user, "reports.view", action_type="view").allowed
    except Exception:
        return False


def has_finance_analytics_permission(user):
    if getattr(user, "is_superuser", False):
        return True
    try:
        from apps.accounts.engine import PermissionEngine
        return bool(
            PermissionEngine.evaluate(user, "expense.view", action_type="view").allowed
            or PermissionEngine.evaluate(user, "payroll.view", action_type="view").allowed
        )
    except Exception:
        return False


def _filter_scoped_profiles(user, params, role_variant=None):
    """Shared date-bound + RBAC-scoped EmployeeProfile filter for lazy panels."""
    params = params or {}
    today = timezone.localdate()
    dr = _resolve_date_range(params, today)
    selected_branch = (params.get("branch") or "").strip()
    selected_dept = (params.get("department") or "").strip()
    page_num = params.get("page") or 1

    if dr["range_rejected"]:
        return dr, selected_branch, selected_dept, page_num, []

    qs = _get_authorized_employees(user, role_variant=role_variant)
    if selected_branch:
        try:
            b_pk = int(selected_branch)
            qs = qs.filter(Q(branch_id=b_pk) | Q(master_employee__branch_id=b_pk))
        except ValueError:
            qs = qs.none()

    if selected_dept:
        if selected_dept.startswith("id:"):
            try:
                d_pk = int(selected_dept.split(":", 1)[1])
                qs = qs.filter(master_employee__department_id=d_pk)
            except ValueError:
                qs = qs.none()
        elif selected_dept.startswith("name:"):
            d_name = selected_dept.split(":", 1)[1]
            qs = qs.filter(
                Q(department__iexact=d_name) | Q(master_employee__department__name__iexact=d_name)
            )
        else:
            qs = qs.filter(
                Q(department__iexact=selected_dept) | Q(master_employee__department__name__iexact=selected_dept)
            )

    rows = list(
        qs.order_by("full_name", "id").values(
            "id",
            "full_name",
            "branch__name",
            "master_employee_id",
            "master_employee__branch__name",
            "master_employee__department__name",
            "department",
            "joined_date",
        )
    )
    return dr, selected_branch, selected_dept, page_num, rows


def get_people_leave_analytics(user, params=None, role_variant=None):
    """Scoped People & Leave analytics panel (read-only, bounded queries)."""
    from decimal import Decimal
    from django.db.models import Count, Sum

    dr, selected_branch, selected_dept, page_num, emp_rows = _filter_scoped_profiles(
        user, params, role_variant=role_variant
    )
    base_ctx = {
        "panel_name": "people_leave",
        "analytics_range": dr["range_key"],
        "analytics_start_date": dr["start_date"],
        "analytics_end_date": dr["end_date"],
        "analytics_range_error": dr["range_error"],
        "analytics_selected_branch": selected_branch,
        "analytics_selected_dept": selected_dept,
        "analytics_employee_count": len(emp_rows),
    }
    if dr["range_rejected"] or not emp_rows:
        base_ctx.update({
            "pl_joined_in_period": 0,
            "pl_approved_count": 0,
            "pl_pending_count": 0,
            "pl_rejected_count": 0,
            "pl_approved_days": Decimal("0.0"),
            "pl_type_items": [],
            "pl_page_obj": Paginator([], DEFAULT_PAGE_SIZE).get_page(1),
            "pl_chart_payload": {"leaveTypes": {"labels": [], "counts": []}},
        })
        return base_ctx

    emp_ids = [r["id"] for r in emp_rows]
    emp_name_map = {r["id"]: r["full_name"] for r in emp_rows}
    joined_in_period = sum(
        1 for r in emp_rows
        if r["joined_date"] and dr["start_date"] <= r["joined_date"] <= dr["end_date"]
    )

    leave_qs = LeaveRequest.objects.filter(
        employee_id__in=emp_ids,
        start_date__lte=dr["end_date"],
        end_date__gte=dr["start_date"],
    )
    status_counts = dict(leave_qs.values_list("status").annotate(cnt=Count("id")))
    approved_count = status_counts.get("approved", 0)
    pending_count = status_counts.get("pending", 0) + status_counts.get("manager_approved", 0)
    rejected_count = status_counts.get("rejected", 0) + status_counts.get("returned", 0) + status_counts.get("cancelled", 0)

    approved_days = (
        leave_qs.filter(status="approved").aggregate(total=Sum("number_of_days"))["total"]
        or Decimal("0.0")
    )

    type_rows = list(
        leave_qs.filter(status="approved")
        .values("leave_type__name")
        .annotate(cnt=Count("id"), days=Sum("number_of_days"))
        .order_by("-cnt")
    )
    type_items = [
        {"name": r["leave_type__name"] or "Leave", "count": r["cnt"], "days": r["days"] or Decimal("0.0")}
        for r in type_rows
    ]

    emp_leave_rows = list(
        leave_qs.values("employee_id", "leave_type__name", "start_date", "end_date", "number_of_days", "status")
        .order_by("-start_date", "-id")
    )
    paginator = Paginator(emp_leave_rows, DEFAULT_PAGE_SIZE)
    page_obj = paginator.get_page(page_num)
    for row in page_obj.object_list:
        row["employee_name"] = emp_name_map.get(row["employee_id"], "Employee")

    base_ctx.update({
        "pl_joined_in_period": joined_in_period,
        "pl_approved_count": approved_count,
        "pl_pending_count": pending_count,
        "pl_rejected_count": rejected_count,
        "pl_approved_days": approved_days,
        "pl_type_items": type_items,
        "pl_page_obj": page_obj,
        "pl_chart_payload": {
            "leaveTypes": {
                "labels": [t["name"] for t in type_items],
                "counts": [t["count"] for t in type_items],
            }
        },
    })
    return base_ctx


def get_work_analytics(user, params=None, role_variant=None):
    """Scoped Work & Field activity analytics panel (no speculative rankings)."""
    from django.db.models import Count
    from apps.projects.models import ProjectTask

    dr, selected_branch, selected_dept, page_num, emp_rows = _filter_scoped_profiles(
        user, params, role_variant=role_variant
    )
    base_ctx = {
        "panel_name": "work",
        "analytics_range": dr["range_key"],
        "analytics_start_date": dr["start_date"],
        "analytics_end_date": dr["end_date"],
        "analytics_range_error": dr["range_error"],
        "analytics_selected_branch": selected_branch,
        "analytics_selected_dept": selected_dept,
        "analytics_employee_count": len(emp_rows),
    }
    if dr["range_rejected"] or not emp_rows:
        base_ctx.update({
            "work_total_tasks": 0,
            "work_completed_tasks": 0,
            "work_delayed_tasks": 0,
            "work_completion_rate": None,
            "work_field_visits": 0,
            "work_missing_timestamps": 0,
            "work_status_items": [],
            "work_page_obj": Paginator([], DEFAULT_PAGE_SIZE).get_page(1),
            "work_chart_payload": {"taskStatus": {"labels": [], "counts": []}},
        })
        return base_ctx

    emp_ids = [r["id"] for r in emp_rows]
    emp_name_map = {r["id"]: r["full_name"] for r in emp_rows}

    tasks_qs = ProjectTask.objects.filter(responsible_person_id__in=emp_ids).filter(
        Q(planned_start__isnull=True, planned_finish__isnull=True)
        | Q(planned_start__lte=dr["end_date"], planned_finish__gte=dr["start_date"])
        | Q(actual_finish__gte=dr["start_date"], actual_finish__lte=dr["end_date"])
    )
    status_rows = list(tasks_qs.values("status").annotate(cnt=Count("id")).order_by("-cnt"))
    total_tasks = sum(r["cnt"] for r in status_rows)
    completed_tasks = sum(r["cnt"] for r in status_rows if r["status"] == "Completed")
    delayed_tasks = sum(r["cnt"] for r in status_rows if r["status"] == "Delayed")
    completion_rate = round((completed_tasks / total_tasks) * 100, 1) if total_tasks > 0 else None
    missing_timestamps = tasks_qs.filter(status="Completed", completed_at__isnull=True).count()

    field_visits = Attendance.objects.filter(
        employee_id__in=emp_ids,
        date__gte=dr["start_date"],
        date__lte=dr["end_date"],
        attendance_type="field_visit",
        is_expired=False,
    ).count()

    task_list = list(
        tasks_qs.values("id", "activity", "project__name", "responsible_person_id", "status", "progress_percent", "completed_at")
        .order_by("status", "-id")
    )
    paginator = Paginator(task_list, DEFAULT_PAGE_SIZE)
    page_obj = paginator.get_page(page_num)
    for row in page_obj.object_list:
        row["employee_name"] = emp_name_map.get(row["responsible_person_id"], "Unassigned")

    base_ctx.update({
        "work_total_tasks": total_tasks,
        "work_completed_tasks": completed_tasks,
        "work_delayed_tasks": delayed_tasks,
        "work_completion_rate": completion_rate,
        "work_field_visits": field_visits,
        "work_missing_timestamps": missing_timestamps,
        "work_status_items": status_rows,
        "work_page_obj": page_obj,
        "work_chart_payload": {
            "taskStatus": {
                "labels": [r["status"] for r in status_rows],
                "counts": [r["cnt"] for r in status_rows],
            }
        },
    })
    return base_ctx


def get_finance_analytics(user, params=None, role_variant=None):
    """
    Scoped Finance analytics panel (read-only, Decimal, separate currencies).
    Keeps approved expenses, pending claims, payroll liabilities and disbursed cash strictly separate.
    """
    from decimal import Decimal
    from django.db.models import Count, Sum
    from apps.expense.models import Expense
    from apps.payroll.models import EmployeePayrollCalculation, PayrollConfiguration, PayrollRunStatus

    if not has_finance_analytics_permission(user):
        return {"panel_name": "finance", "fin_authorized": False}

    dr, selected_branch, selected_dept, page_num, emp_rows = _filter_scoped_profiles(
        user, params, role_variant=role_variant
    )
    currency = PayrollConfiguration.objects.values_list("currency", flat=True).first() or "BDT"

    base_ctx = {
        "panel_name": "finance",
        "fin_authorized": True,
        "fin_currency": currency,
        "analytics_range": dr["range_key"],
        "analytics_start_date": dr["start_date"],
        "analytics_end_date": dr["end_date"],
        "analytics_range_error": dr["range_error"],
        "analytics_selected_branch": selected_branch,
        "analytics_selected_dept": selected_dept,
        "analytics_employee_count": len(emp_rows),
    }
    if dr["range_rejected"] or not emp_rows:
        zero = Decimal("0.00")
        base_ctx.update({
            "fin_approved_expenses": zero,
            "fin_approved_expense_count": 0,
            "fin_pending_claims": zero,
            "fin_pending_claim_count": 0,
            "fin_payroll_liability": zero,
            "fin_payroll_disbursed": zero,
            "fin_page_obj": Paginator([], DEFAULT_PAGE_SIZE).get_page(1),
            "fin_chart_payload": {"financeBreakdown": {"labels": [], "values": [], "currency": currency}},
        })
        return base_ctx

    emp_ids = [r["id"] for r in emp_rows]
    emp_name_map = {r["id"]: r["full_name"] for r in emp_rows}
    master_ids = [r["master_employee_id"] for r in emp_rows if r["master_employee_id"] is not None]

    exp_qs = Expense.objects.filter(
        employee_id__in=emp_ids,
        requested_at__date__gte=dr["start_date"],
        requested_at__date__lte=dr["end_date"],
    )
    exp_aggs = exp_qs.aggregate(
        approved_sum=Sum("amount", filter=Q(status="approved")),
        approved_cnt=Count("id", filter=Q(status="approved")),
        pending_sum=Sum("amount", filter=Q(status__in=["pending_manager", "pending_finance", "pending_accounts"])),
        pending_cnt=Count("id", filter=Q(status__in=["pending_manager", "pending_finance", "pending_accounts"])),
    )
    approved_expenses = exp_aggs["approved_sum"] or Decimal("0.00")
    pending_claims = exp_aggs["pending_sum"] or Decimal("0.00")

    payroll_liability = Decimal("0.00")
    payroll_disbursed = Decimal("0.00")
    if master_ids:
        pay_qs = EmployeePayrollCalculation.objects.filter(
            employee_id__in=master_ids,
            payroll_run__period_start__lte=dr["end_date"],
            payroll_run__period_end__gte=dr["start_date"],
        )
        pay_aggs = pay_qs.aggregate(
            liability_sum=Sum(
                "net_payable",
                filter=Q(payroll_run__status__in=[PayrollRunStatus.DRAFT, PayrollRunStatus.REVIEW, PayrollRunStatus.APPROVED_LOCKED]),
            ),
            disbursed_sum=Sum(
                "net_payable",
                filter=Q(payroll_run__status=PayrollRunStatus.DISBURSED),
            ),
        )
        payroll_liability = pay_aggs["liability_sum"] or Decimal("0.00")
        payroll_disbursed = pay_aggs["disbursed_sum"] or Decimal("0.00")

    exp_list = list(
        exp_qs.values("id", "employee_id", "category__name", "amount", "status", "requested_at")
        .order_by("-requested_at", "-id")
    )
    paginator = Paginator(exp_list, DEFAULT_PAGE_SIZE)
    page_obj = paginator.get_page(page_num)
    for row in page_obj.object_list:
        row["employee_name"] = emp_name_map.get(row["employee_id"], "Employee")

    base_ctx.update({
        "fin_approved_expenses": approved_expenses,
        "fin_approved_expense_count": exp_aggs["approved_cnt"] or 0,
        "fin_pending_claims": pending_claims,
        "fin_pending_claim_count": exp_aggs["pending_cnt"] or 0,
        "fin_payroll_liability": payroll_liability,
        "fin_payroll_disbursed": payroll_disbursed,
        "fin_page_obj": page_obj,
        "fin_chart_payload": {
            "financeBreakdown": {
                "currency": currency,
                "labels": ["Approved Expenses", "Pending Claims", "Payroll Liabilities", "Actual Payments"],
                "values": [
                    float(approved_expenses),
                    float(pending_claims),
                    float(payroll_liability),
                    float(payroll_disbursed),
                ],
            }
        },
    })
    return base_ctx
