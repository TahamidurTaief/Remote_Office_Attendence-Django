import datetime
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.attendance.models import (
    Attendance,
    AttendanceActivityLog,
    AttendanceCoverageRecord,
    AttendancePolicy,
    SyncLog,
)
from apps.attendance.reporting_service import BD_TZ, OptimizedSchedule
from apps.employees.models import EmployeeLocationSync, EmployeeProfile


def reconcile_attendance_coverage(target_date, employee_queryset=None, now=None, dry_run=False):
    """
    Independent daily coverage reconciler.
    Attests (employee, target_date) completeness only after shift end + offline sync grace,
    checking SyncLog, AttendanceActivityLog, EmployeeLocationSync, and expired rows.
    Never overwrites 'purged_retained' or 'reconciled_late_offline' records.
    """
    ref_now = now or timezone.now()
    local_now = timezone.localtime(ref_now, BD_TZ)
    today = local_now.date()

    if target_date >= today:
        raise ValueError("Cannot attest coverage for today or future dates.")

    if employee_queryset is None:
        employees = list(
            EmployeeProfile.objects.filter(is_active=True).select_related(
                "branch", "branch__schedule", "master_employee"
            )
        )
    else:
        employees = list(employee_queryset)

    emp_ids = [e.id for e in employees]
    if not emp_ids:
        return {"complete": 0, "incomplete_activity": 0, "preserved": 0, "pending_grace": 0}

    # Bounded bulk queries (no N+1)
    day_atts = Attendance.objects.filter(employee_id__in=emp_ids, date=target_date).values(
        "employee_id", "is_expired", "attendance_type"
    )
    usable_emp_ids = set()
    expired_emp_ids = set()
    for a in day_atts:
        if a["is_expired"]:
            expired_emp_ids.add(a["employee_id"])
        else:
            usable_emp_ids.add(a["employee_id"])

    day_start = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time.min), BD_TZ)
    day_end = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time.max), BD_TZ)

    failed_sync_emp_ids = set(
        SyncLog.objects.filter(
            employee_id__in=emp_ids,
            records_failed__gt=0,
            completed_at__gte=day_start,
            completed_at__lte=day_end,
        ).values_list("employee_id", flat=True)
    )
    activity_emp_ids = set(
        AttendanceActivityLog.objects.filter(
            employee_id__in=emp_ids,
            timestamp__gte=day_start,
            timestamp__lte=day_end,
        )
        .exclude(action="late_offline_reconciliation")
        .values_list("employee_id", flat=True)
    )
    loc_sync_emp_ids = set(
        EmployeeLocationSync.objects.filter(
            employee_id__in=emp_ids,
            timestamp__gte=day_start,
            timestamp__lte=day_end,
        ).values_list("employee_id", flat=True)
    )

    existing_map = {
        rec.employee_id: rec
        for rec in AttendanceCoverageRecord.objects.filter(employee_id__in=emp_ids, date=target_date)
    }

    policies = list(AttendancePolicy.objects.all())
    policies_by_branch = {p.branch_id: p for p in policies if p.branch_id is not None}
    global_policy = next((p for p in policies if p.branch_id is None), None)
    sync_grace_minutes = int(getattr(settings, "ATTENDANCE_SYNC_GRACE_MINUTES", 0) or 0)

    to_create = []
    to_update = []
    counts = {"complete": 0, "incomplete_activity": 0, "preserved": 0, "pending_grace": 0}

    for emp in employees:
        existing = existing_map.get(emp.id)
        if existing and existing.coverage_state in ("purged_retained", "reconciled_late_offline"):
            counts["preserved"] += 1
            continue

        schedule = OptimizedSchedule(emp, policies_by_branch, global_policy)
        end_date = target_date + datetime.timedelta(days=1) if schedule.office_end_time < schedule.office_start_time else target_date
        grace_close_dt = timezone.make_aware(
            datetime.datetime.combine(end_date, schedule.office_end_time), BD_TZ
        ) + datetime.timedelta(minutes=sync_grace_minutes)

        if local_now < grace_close_dt:
            counts["pending_grace"] += 1
            continue

        has_expired_without_tombstone = emp.id in expired_emp_ids and emp.id not in usable_emp_ids
        has_unmatched_activity = (
            (emp.id in activity_emp_ids or emp.id in loc_sync_emp_ids or emp.id in failed_sync_emp_ids)
            and emp.id not in usable_emp_ids
        )
        if has_expired_without_tombstone or has_unmatched_activity:
            new_state = "incomplete_activity"
            counts["incomplete_activity"] += 1
        else:
            new_state = "complete"
            counts["complete"] += 1

        if existing:
            existing.coverage_state = new_state
            existing.attestation_source = "ingestion_reconciler"
            existing.offline_grace_closed_at = ref_now
            to_update.append(existing)
        else:
            to_create.append(
                AttendanceCoverageRecord(
                    employee=emp,
                    date=target_date,
                    coverage_state=new_state,
                    attestation_source="ingestion_reconciler",
                    offline_grace_closed_at=ref_now,
                )
            )

    if not dry_run and (to_create or to_update):
        with transaction.atomic():
            if to_create:
                AttendanceCoverageRecord.objects.bulk_create(to_create)
            if to_update:
                AttendanceCoverageRecord.objects.bulk_update(
                    to_update,
                    ["coverage_state", "attestation_source", "offline_grace_closed_at", "updated_at"],
                )

    return counts
