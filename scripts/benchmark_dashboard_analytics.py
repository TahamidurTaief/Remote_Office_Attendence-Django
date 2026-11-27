#!/usr/bin/env python3
"""
Reproducible Dashboard Attendance Analytics Benchmark.
Uses an isolated Django test SQLite database (never modifies db.sqlite3).
Measures 50 vs 500 employees, 30 vs 90-day ranges, admin & manager roles:
- 1 warm-up + 5 measured requests (median/max ms, query count, response bytes)
- Phase breakdown: SQL, current-period calc, previous-period calc, template render
"""

import os
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fieldtrack.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext, setup_test_environment, teardown_test_environment
from django.urls import reverse
from django.utils import timezone

from apps.accounts.rbac_models import Action, DataScope, Module, Permission, Role, RolePermission, UserRoleAssignment
from apps.admin_panel import analytics_service
from apps.attendance.models import Attendance
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Department, Employee, EmployeeProfile, EmployeeStatus

User = get_user_model()


def seed_benchmark_data(target_total_employees, branch_a, branch_b, dept_eng, dept_sales, mgr_master, start_idx=0):
    today = timezone.localdate()
    joined = today - timedelta(days=200)
    current_count = EmployeeProfile.objects.filter(employee_id__startswith="BENCH-").count()
    to_create = target_total_employees - current_count
    if to_create <= 0:
        return

    users = [
        User(
            email=f"bench_{start_idx + i}@example.com",
            phone=f"019{start_idx + i:08d}",
            password="!",
            role="staff",
            is_active=True,
        )
        for i in range(to_create)
    ]
    User.objects.bulk_create(users, batch_size=500)
    created_users = list(User.objects.filter(email__startswith="bench_").order_by("id"))[-to_create:]

    emps = []
    for idx, u in enumerate(created_users):
        num = start_idx + idx
        br = branch_a if num % 2 == 0 else branch_b
        dp = dept_eng if num % 2 == 0 else dept_sales
        emps.append(
            Employee(
                user=u,
                employee_number=f"BENCH-{num:04d}",
                first_name="Bench",
                last_name=f"{num:04d}",
                department=dp,
                branch=br,
                reporting_manager=mgr_master if br == branch_a else None,
                status=EmployeeStatus.ACTIVE,
                joined_date=joined,
            )
        )
    Employee.objects.bulk_create(emps, batch_size=500)
    created_emps = list(Employee.objects.filter(employee_number__startswith="BENCH-").order_by("id"))[-to_create:]

    profs = [
        EmployeeProfile(
            user=e.user,
            master_employee=e,
            employee_id=e.employee_number,
            full_name=f"Bench {e.employee_number}",
            department="Engineering" if e.branch_id == branch_a.id else "Sales",
            phone=f"019{start_idx + idx:08d}",
            branch=e.branch,
            is_active=True,
            joined_date=joined,
        )
        for idx, e in enumerate(created_emps)
    ]
    EmployeeProfile.objects.bulk_create(profs, batch_size=500)
    created_profs = list(EmployeeProfile.objects.filter(employee_id__startswith="BENCH-").order_by("id"))[-to_create:]

    # Create attendance rows across last 180 days (covers 90d + 90d prev period)
    # ~10 working days sampled per employee + multi-session + field visits to simulate real load
    att_rows = []
    for prof_idx, prof in enumerate(created_profs):
        for offset in range(1, 180, 6):
            d = today - timedelta(days=offset)
            if d.weekday() == 4:
                continue
            ci = timezone.make_aware(datetime.combine(d, datetime.strptime("09:05", "%H:%M").time()))
            co = ci + timedelta(hours=8)
            att_rows.append(
                Attendance(
                    employee=prof,
                    date=d,
                    attendance_type="check_in",
                    type="office",
                    status="late" if (prof_idx + offset) % 5 == 0 else "on_time",
                    check_in_time=ci,
                    check_out_time=None if (prof_idx + offset) % 17 == 0 else co,
                    total_hours=0.0 if (prof_idx + offset) % 17 == 0 else 8.0,
                )
            )
            if (prof_idx + offset) % 7 == 0:
                att_rows.append(
                    Attendance(
                        employee=prof,
                        date=d,
                        attendance_type="field_visit",
                        type="field",
                        status="on_time",
                        check_in_time=ci + timedelta(hours=2),
                    )
                )
    Attendance.objects.bulk_create(att_rows, batch_size=2000)


def measure_endpoint(client, url, params, headers=None, warmup=1, runs=5):
    headers = headers or {}
    for _ in range(warmup):
        resp = client.get(url, params, **headers)
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"

    times_ms = []
    query_counts = []
    sql_ms_list = []
    sizes = []

    for _ in range(runs):
        with CaptureQueriesContext(connection) as ctx:
            t0 = time.perf_counter()
            resp = client.get(url, params, **headers)
            t1 = time.perf_counter()
        assert resp.status_code == 200
        times_ms.append((t1 - t0) * 1000.0)
        query_counts.append(len(ctx))
        sql_ms = sum(float(q.get("time", 0) or 0) * 1000.0 for q in ctx.captured_queries)
        sql_ms_list.append(sql_ms)
        sizes.append(len(resp.content))

    return {
        "median_ms": round(statistics.median(times_ms), 2),
        "max_ms": round(max(times_ms), 2),
        "sql_median_ms": round(statistics.median(sql_ms_list), 2),
        "queries": max(query_counts),
        "bytes": max(sizes),
    }


def profile_service_phases(user, params, role_variant=None):
    timings = {"curr_ms": 0.0, "prev_ms": 0.0, "total_ms": 0.0}
    orig_compute = analytics_service._compute_period_metrics
    call_idx = {"n": 0}

    def wrapped_compute(*args, **kwargs):
        t0 = time.perf_counter()
        res = orig_compute(*args, **kwargs)
        dt = (time.perf_counter() - t0) * 1000.0
        if call_idx["n"] == 0:
            timings["curr_ms"] = round(dt, 2)
        else:
            timings["prev_ms"] = round(dt, 2)
        call_idx["n"] += 1
        return res

    with patch.object(analytics_service, "_compute_period_metrics", side_effect=wrapped_compute):
        t0 = time.perf_counter()
        analytics_service.get_attendance_analytics(user, params, role_variant=role_variant)
        timings["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
    return timings


def main():
    setup_test_environment()
    old_db_name = connection.creation.create_test_db(verbosity=0, autoclobber=True)
    try:
        branch_a = Branch.objects.create(name="Bench Alpha", latitude=23.81, longitude=90.41, radius_meters=200)
        branch_b = Branch.objects.create(name="Bench Beta", latitude=22.35, longitude=91.78, radius_meters=200)
        for br in (branch_a, branch_b):
            OfficeSchedule.objects.update_or_create(
                branch=br,
                defaults={
                    "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                    "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                    "late_after_minutes": 15,
                    "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
                },
            )
        dept_eng = Department.objects.create(name="Engineering")
        dept_sales = Department.objects.create(name="Sales")

        admin = User.objects.create_superuser(email="bench_admin@example.com", password="pass", role="admin")
        mgr_user = User.objects.create_user(email="bench_mgr@example.com", password="pass", role="manager")
        mgr_master = Employee.objects.create(
            user=mgr_user,
            employee_number="BENCH-MGR",
            first_name="Bench",
            last_name="Mgr",
            department=dept_eng,
            branch=branch_a,
            status=EmployeeStatus.ACTIVE,
            joined_date=date(2024, 1, 1),
        )
        EmployeeProfile.objects.create(
            user=mgr_user,
            master_employee=mgr_master,
            employee_id="BENCH-MGR",
            full_name="Bench Mgr",
            department="Engineering",
            phone="01700000001",
            branch=branch_a,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )
        mod_dash, _ = Module.objects.get_or_create(code="dashboard", defaults={"name": "Dashboard"})
        mod_rep, _ = Module.objects.get_or_create(code="reports", defaults={"name": "Reports"})
        act_view, _ = Action.objects.get_or_create(code="view", defaults={"name": "View"})
        perm_dash, _ = Permission.objects.get_or_create(
            codename="dashboard.view", defaults={"module": mod_dash, "action": act_view, "name": "View Dashboard"}
        )
        perm_rep, _ = Permission.objects.get_or_create(
            codename="reports.view", defaults={"module": mod_rep, "action": act_view, "name": "View Reports"}
        )
        mgr_role, _ = Role.objects.get_or_create(code="bench_mgr_role", defaults={"name": "BenchMgr"})
        RolePermission.objects.get_or_create(role=mgr_role, permission=perm_dash, defaults={"data_scope": DataScope.BRANCH})
        RolePermission.objects.get_or_create(role=mgr_role, permission=perm_rep, defaults={"data_scope": DataScope.BRANCH})
        UserRoleAssignment.objects.get_or_create(user=mgr_user, role=mgr_role)

        url = reverse("dashboard")
        client = Client()

        for count in (50, 500):
            seed_benchmark_data(count, branch_a, branch_b, dept_eng, dept_sales, mgr_master, start_idx=count - 50 if count == 500 else 0)
            print(f"\n=== {count} EMPLOYEES ===")
            for role_name, user_obj in (("admin", admin), ("manager", mgr_user)):
                client.force_login(user_obj)
                for r in ("30", "90"):
                    m = measure_endpoint(
                        client,
                        url,
                        {"widget": "attendance_analytics", "range": r},
                        headers={"HTTP_HX_REQUEST": "true"},
                    )
                    phases = profile_service_phases(user_obj, {"range": r}, role_variant=role_name)
                    print(
                        f"role={role_name:7s} range={r}d | median={m['median_ms']:7.2f}ms max={m['max_ms']:7.2f}ms "
                        f"sql={m['sql_median_ms']:6.2f}ms q={m['queries']:2d} bytes={m['bytes']:6d} | "
                        f"svc_total={phases['total_ms']:6.2f}ms curr={phases['curr_ms']:6.2f}ms prev={phases['prev_ms']:6.2f}ms"
                    )
            # Also measure non-analytics widget refresh (e.g. admin_security)
            client.force_login(admin)
            m_sec = measure_endpoint(
                client,
                url,
                {"widget": "admin_security"},
                headers={"HTTP_HX_REQUEST": "true"},
            )
            print(
                f"role=admin   widget=admin_security | median={m_sec['median_ms']:7.2f}ms max={m_sec['max_ms']:7.2f}ms "
                f"q={m_sec['queries']:2d} bytes={m_sec['bytes']:6d}"
            )
    finally:
        connection.creation.destroy_test_db(old_db_name, verbosity=0)
        teardown_test_environment()


if __name__ == "__main__":
    main()
