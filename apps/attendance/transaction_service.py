import json
import uuid
import datetime
import decimal
from django.utils import timezone
from django.db import transaction
from apps.attendance.models import Attendance, AttendanceLocation
from apps.attendance.sync_utils import (
    ClientTimeValidationError,
    parse_and_validate_client_time,
    validate_client_time,
)
from apps.branches.utils import is_within_geofence
from apps.notifications.utils import notify_admins
from apps.employees.models import EmployeeProfile


import math
import logging

logger = logging.getLogger(__name__)


def auto_close_past_sessions(employee=None, today=None):
    """
    Deprecated no-op: fabricated auto-closure is disabled.
    Past unclosed sessions remain open (check_out_time=NULL) for overnight
    checkout, offline retry, or authorized forgot-checkout/correction workflows.
    """
    return 0


def parse_and_validate_coordinates(lat_val, lng_val, accuracy_val=None):
    """
    Parses and validates coordinates and positive GPS accuracy.
    - Both lat and lng absent/blank -> returns (None, None, None, True) (missing).
    - Zero coordinates (0.0, 0.0) are valid when explicitly supplied.
    - Rejects booleans, non-finite (NaN, Inf), out-of-range coordinates,
      and missing, zero, negative, or non-finite accuracy with 400 error.
    Returns (lat: float | None, lng: float | None, accuracy: float | None, is_missing: bool).
    """
    lat_missing = lat_val is None or (isinstance(lat_val, str) and lat_val.strip() == '')
    lng_missing = lng_val is None or (isinstance(lng_val, str) and lng_val.strip() == '')
    if lat_missing and lng_missing:
        return 0.0, 0.0, 0.0, True
    if lat_missing or lng_missing:
        raise AttendanceTransactionError('Both latitude and longitude are required.', 400)

    if isinstance(lat_val, bool) or isinstance(lng_val, bool):
        raise AttendanceTransactionError('Invalid coordinates format.', 400)

    try:
        lat = float(lat_val)
        lng = float(lng_val)
    except (TypeError, ValueError):
        raise AttendanceTransactionError('Invalid coordinates format.', 400)

    if not (math.isfinite(lat) and math.isfinite(lng)):
        raise AttendanceTransactionError('Non-finite coordinates are not allowed.', 400)

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        raise AttendanceTransactionError('Coordinates out of valid geographic range.', 400)

    if accuracy_val is None or (isinstance(accuracy_val, str) and accuracy_val.strip() == '') or isinstance(accuracy_val, bool):
        raise AttendanceTransactionError('Valid positive GPS accuracy is required.', 400)

    try:
        acc = float(accuracy_val)
    except (TypeError, ValueError):
        raise AttendanceTransactionError('Valid positive GPS accuracy is required.', 400)

    if not math.isfinite(acc) or acc <= 0:
        raise AttendanceTransactionError('Valid positive GPS accuracy is required.', 400)

    return lat, lng, acc, False


def validate_location_capture_time(captured_at_raw, event_time, max_age_seconds=60):
    """
    Validates optional location_captured_at ISO8601 timestamp against the punch event_time
    (preserving offline capture-to-event comparison rather than upload time).
    - Absent / blank -> returns None (legacy compatibility).
    - Supplied but malformed, after event_time, or older than max_age_seconds -> raises 400.
    """
    if captured_at_raw is None:
        return None
    if isinstance(captured_at_raw, str) and not captured_at_raw.strip():
        return None
    if not isinstance(captured_at_raw, str):
        raise AttendanceTransactionError('Invalid location_captured_at format.', 400)

    raw = captured_at_raw.strip()
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'

    try:
        captured_dt = datetime.datetime.fromisoformat(raw)
    except ValueError:
        raise AttendanceTransactionError('Invalid location_captured_at format.', 400)

    if timezone.is_naive(captured_dt):
        captured_dt = timezone.make_aware(captured_dt, timezone.get_current_timezone())

    ref_event = event_time
    if timezone.is_naive(ref_event):
        ref_event = timezone.make_aware(ref_event, timezone.get_current_timezone())

    if captured_dt > ref_event:
        raise AttendanceTransactionError('Location capture time cannot be after event time.', 400)

    if (ref_event - captured_dt).total_seconds() > max_age_seconds:
        raise AttendanceTransactionError(
            f'Location fix is stale (captured more than {max_age_seconds}s before event).',
            400
        )

    return captured_dt


def _safe_notify_admins(employee, notif_type, location=''):
    """Guarded notification callback executed after transaction commit."""
    try:
        notify_admins(employee, notif_type, location=location)
    except Exception as exc:
        logger.warning(
            "Guarded notification dispatch failed for employee %s (%s): %s",
            getattr(employee, 'id', None),
            notif_type,
            exc
        )

class AttendanceTransactionError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _sync_pg_sequence_for_model(model):
    """Synchronize PostgreSQL serial/identity sequence to MAX(id) if out of sync."""
    from django.db import connection
    try:
        if 'postgresql' in connection.settings_dict.get('ENGINE', ''):
            table = model._meta.db_table
            pk_col = model._meta.pk.column
            with connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', '{pk_col}'),
                        GREATEST((SELECT COALESCE(MAX({pk_col}), 1) FROM {table}), 1),
                        (SELECT MAX({pk_col}) IS NOT NULL FROM {table})
                    );
                """)
    except Exception:
        pass


def _safe_create_with_seq_retry(model, **kwargs):
    """
    Creates a model instance inside a savepoint. If a duplicate key error occurs
    specifically on the primary key sequence, synchronizes the sequence and retries.
    Never alters sync_uuid or resets sequences for non-pk unique conflicts.
    """
    from django.db import transaction, IntegrityError
    try:
        with transaction.atomic():
            return model.objects.create(**kwargs)
    except IntegrityError as e:
        err_msg = str(e).lower()
        if '_pkey' in err_msg or ('duplicate key' in err_msg and 'sync_uuid' not in err_msg):
            _sync_pg_sequence_for_model(model)
            with transaction.atomic():
                return model.objects.create(**kwargs)
        raise e

def validate_uploaded_photo(photo):
    """
    Validates uploaded photo:
    - Checks file size <= 10MB
    - Checks content_type is an allowed image type
    - Inspects and verifies actual image header/bytes with PIL,
      ensuring client MIME headers are not spoofed and photo is not corrupted.
    """
    if not photo:
        return
    allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
    if getattr(photo, 'content_type', None) not in allowed_types:
        raise AttendanceTransactionError('Invalid file type. Only images allowed.', 400)
    if photo.size > 10 * 1024 * 1024:
        raise AttendanceTransactionError('Photo too large. Max 10MB allowed.', 400)

    try:
        from PIL import Image as PILImage
        pos = photo.tell() if hasattr(photo, 'tell') else 0
        if hasattr(photo, 'seek'):
            photo.seek(0)
        img = PILImage.open(photo)
        img.verify()
        if (img.format or '').lower() not in ('jpeg', 'png', 'webp'):
            raise AttendanceTransactionError('Invalid image format.', 400)
        if hasattr(photo, 'seek'):
            photo.seek(pos)
    except AttendanceTransactionError:
        raise
    except Exception:
        raise AttendanceTransactionError('Invalid or corrupted image file.', 400)


def _parse_positive_int_id(raw_value, error_message='Invalid attendance session target.'):
    """Parse strict positive integer IDs or digit strings; reject bools, floats, <= 0, and non-digits."""
    if isinstance(raw_value, bool):
        raise AttendanceTransactionError(error_message, 400)
    if isinstance(raw_value, int):
        if raw_value <= 0:
            raise AttendanceTransactionError(error_message, 400)
        return raw_value
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not stripped or not stripped.isdigit():
            raise AttendanceTransactionError(error_message, 400)
        parsed = int(stripped)
        if parsed <= 0:
            raise AttendanceTransactionError(error_message, 400)
        return parsed
    raise AttendanceTransactionError(error_message, 400)


class AttendanceTransactionService:
    @staticmethod
    def check_in(user, data, photo=None, validate_photo=True):
        from apps.attendance.views import check_role, get_employee, get_attendance_policy

        employee = get_employee(user)
        if not employee:
            raise AttendanceTransactionError('Employee profile not found.', 403)
        if not employee.is_active:
            raise AttendanceTransactionError('Employee profile is inactive.', 403)

        if not check_role(user):
            raise AttendanceTransactionError('Unauthorized role.', 403)

        master = getattr(user, 'employee_master', None)
        if not master and hasattr(user, 'employee_profile') and user.employee_profile.master_employee:
            master = user.employee_profile.master_employee
        if master and (master.is_suspended or master.business_status == 'suspended'):
            raise AttendanceTransactionError('Account is suspended.', 403)

        sync_uuid = data.get('sync_uuid')

        with transaction.atomic():
            emp_locked = EmployeeProfile.objects.select_for_update().get(pk=employee.pk)
            if not emp_locked.is_active:
                raise AttendanceTransactionError('Employee profile is inactive.', 403)

            if sync_uuid:
                foreign_existing = Attendance.objects.filter(sync_uuid=sync_uuid).exclude(employee=emp_locked).first()
                if foreign_existing:
                    raise AttendanceTransactionError('Invalid idempotency key for this employee.', 400)

                existing = Attendance.objects.filter(
                    sync_uuid=sync_uuid,
                    employee=emp_locked,
                    attendance_type='check_in'
                ).first()
                if existing:
                    ci_loc = existing.locations.filter(event='check_in').first()
                    existing_address = ci_loc.address if ci_loc else (existing.site_address or '')
                    return {
                        'success': True,
                        'session_id': existing.id,
                        'type': existing.type,
                        'status': existing.status,
                        'address': existing_address
                    }

            client_event_time_str = data.get('client_event_time')
            try:
                client_time = validate_client_time(client_event_time_str)
            except ClientTimeValidationError as exc:
                raise AttendanceTransactionError(str(exc), 400)

            if client_time:
                event_time = client_time
                synced_at = timezone.now()
                today = timezone.localdate(client_time)
            else:
                event_time = timezone.localtime()
                synced_at = None
                today = timezone.localdate()

            active_session = Attendance.objects.filter(
                employee=emp_locked,
                date=today,
                attendance_type='check_in',
                check_out_time__isnull=True,
                is_expired=False
            ).first()
            if active_session:
                if sync_uuid and str(active_session.sync_uuid) == str(sync_uuid):
                    ci_loc = active_session.locations.filter(event='check_in').first()
                    existing_address = ci_loc.address if ci_loc else (active_session.site_address or '')
                    return {
                        'success': True,
                        'session_id': active_session.id,
                        'type': active_session.type,
                        'status': active_session.status,
                        'address': existing_address
                    }
                raise AttendanceTransactionError('You are already checked in. Please check out first.', 400)

            policy = get_attendance_policy(emp_locked)

            from django.conf import settings
            require_gps = getattr(settings, 'REQUIRE_GPS', True)

            lat, lng, acc_val, is_gps_missing = parse_and_validate_coordinates(
                data.get('latitude'),
                data.get('longitude'),
                data.get('accuracy')
            )
            if not is_gps_missing:
                validate_location_capture_time(data.get('location_captured_at'), event_time)
            note = data.get('note', '')
            address = data.get('address', '')

            is_exception = False
            gps_quality = 'good'
            is_gps_poor = False

            if not is_gps_missing and policy.max_gps_accuracy_meters and acc_val > policy.max_gps_accuracy_meters:
                is_gps_poor = True

            from apps.accounts.engine import PermissionEngine
            is_admin_or_hr = user.is_superuser or PermissionEngine.evaluate(user, 'attendance.override').allowed or PermissionEngine.evaluate(user, 'attendance.edit').allowed or getattr(user, 'role', '') in ('admin', 'hr', 'system_owner', 'super_admin')

            if is_gps_missing:
                gps_quality = 'missing'
                if policy.gps_required == 'required' and not is_admin_or_hr:
                    raise AttendanceTransactionError('GPS location is required for attendance.', 400)
                else:
                    is_exception = True
                    note = f"{note} [POLICY EXCEPTION: GPS location missing]".strip()
            elif is_gps_poor:
                gps_quality = 'poor'
                if policy.gps_required == 'required' and not is_admin_or_hr:
                    raise AttendanceTransactionError(
                        f'GPS accuracy ({int(acc_val)}m) exceeds maximum allowed limit ({policy.max_gps_accuracy_meters}m).',
                        400
                    )
                else:
                    is_exception = True
                    note = f"{note} [POLICY EXCEPTION: Poor GPS accuracy {int(acc_val)}m]".strip()

            if validate_photo and policy.photo_required and not photo and not is_admin_or_hr:
                raise AttendanceTransactionError('Photo is required for attendance.', 400)

            if photo:
                validate_uploaded_photo(photo)

            attendance_type = data.get('type', '')
            if attendance_type not in ('office', 'field'):
                attendance_type = 'field'
                branch = emp_locked.branch
                if not is_gps_missing and branch and branch.latitude and branch.longitude:
                    within_geofence, _ = is_within_geofence(float(lat), float(lng), branch)
                    if within_geofence:
                        attendance_type = 'office'

            is_outside_geofence = False
            enforce_geofence = not policy.allow_outside_geofence or policy.geofencing_policy != 'disabled'
            if not is_gps_missing and enforce_geofence:
                branch = emp_locked.branch
                if branch and branch.latitude and branch.longitude:
                    within_geofence, distance = is_within_geofence(float(lat), float(lng), branch)
                    if not within_geofence:
                        is_outside_geofence = True
                        should_block = (policy.geofencing_policy == 'block') or (not policy.allow_outside_geofence and policy.geofencing_policy == 'block')
                        if should_block and not is_admin_or_hr:
                            raise AttendanceTransactionError(
                                f'Geofence validation failed. You are outside the office radius by {int(distance)} meters.',
                                400
                            )
                        else:
                            is_exception = True
                            warning_msg = f" [POLICY EXCEPTION: Checked in {int(distance)}m outside geofence GEOFENCE WARNING]"
                            note = f"{note}{warning_msg}".strip()

            from .schedule_utils import is_employee_holiday
            is_holiday = is_employee_holiday(emp_locked, today)

            if is_holiday:
                status = 'holiday_attendance'
                if not policy.allow_holiday_attendance:
                    is_exception = True
                    warning_msg = " [POLICY EXCEPTION: Holiday attendance not allowed]"
                    note = f"{note}{warning_msg}".strip()
            else:
                first_checkin_today = Attendance.objects.filter(
                    employee=emp_locked,
                    date=today,
                    attendance_type='check_in',
                    is_expired=False
                ).order_by('check_in_time').first()

                if first_checkin_today is None:
                    from .schedule_utils import get_branch_schedule, calculate_attendance_status
                    schedule = get_branch_schedule(emp_locked)
                    status = calculate_attendance_status(event_time, schedule)
                else:
                    status = 'on_time'

            project = None
            project_id = data.get('project') or data.get('project_id')
            if project_id:
                try:
                    from apps.projects.models import Project
                    project = Project.objects.get(pk=project_id)
                except Exception:
                    pass
            if not project:
                try:
                    from apps.projects.models import Project, ProjectTask
                    from django.db.models import Q
                    project = Project.objects.filter(Q(project_managers=emp_locked) | Q(site_engineers=emp_locked)).first()
                    if not project:
                        task = ProjectTask.objects.filter(responsible_person=emp_locked, planned_start__lte=today, planned_finish__gte=today).first()
                        if task:
                            project = task.project
                    if not project and emp_locked.branch:
                        project = Project.objects.filter(branch=emp_locked.branch).first()
                except Exception:
                    pass

            attendance = _safe_create_with_seq_retry(
                Attendance,
                employee=emp_locked,
                project=project,
                date=today,
                check_in_time=event_time,
                type=attendance_type,
                attendance_type='check_in',
                status=status,
                note=note,
                photo=photo,
                is_policy_exception=is_exception,
                is_outside_geofence=is_outside_geofence,
                gps_quality=gps_quality,
                sync_uuid=sync_uuid or uuid.uuid4(),
                client_event_time=client_time,
                synced_at=synced_at
            )

            _safe_create_with_seq_retry(
                AttendanceLocation,
                attendance=attendance,
                event='check_in',
                latitude=float(lat),
                longitude=float(lng),
                address=address,
                accuracy=float(acc_val) if acc_val else 0.0,
                timestamp=event_time,
                sync_uuid=uuid.uuid4(),
                client_event_time=client_time,
                synced_at=synced_at
            )

            notif_type = 'late' if status == 'late' else 'check_in'
            transaction.on_commit(lambda: _safe_notify_admins(emp_locked, notif_type, location=address))

            from apps.attendance.models import AttendanceActivityLog
            try:
                _safe_create_with_seq_retry(
                    AttendanceActivityLog,
                    employee=emp_locked,
                    action='check_in',
                    description=f"Checked In at {event_time.strftime('%I:%M %p')}"
                )
            except Exception:
                pass

            return {
                'success': True,
                'session_id': attendance.id,
                'type': attendance_type,
                'status': status,
                'address': address
            }

    @staticmethod
    def check_out(user, data, photo=None, validate_photo=True):
        from apps.attendance.views import check_role, get_employee, get_attendance_policy

        employee = get_employee(user)
        if not employee:
            raise AttendanceTransactionError('Employee profile not found.', 403)
        if not employee.is_active:
            raise AttendanceTransactionError('Employee profile is inactive.', 403)

        if not check_role(user):
            raise AttendanceTransactionError('Unauthorized role.', 403)

        master = getattr(user, 'employee_master', None)
        if not master and hasattr(user, 'employee_profile') and user.employee_profile.master_employee:
            master = user.employee_profile.master_employee
        if master and (master.is_suspended or master.business_status == 'suspended'):
            raise AttendanceTransactionError('Account is suspended.', 403)

        sync_uuid = data.get('sync_uuid')

        raw_session_id = data.get('session_id')
        raw_attendance_id = data.get('attendance_id')
        has_session_id = raw_session_id is not None and raw_session_id != ''
        has_attendance_id = raw_attendance_id is not None and raw_attendance_id != ''

        target_session_pk = None
        if has_session_id:
            target_session_pk = _parse_positive_int_id(raw_session_id)
        if has_attendance_id:
            att_pk = _parse_positive_int_id(raw_attendance_id)
            if target_session_pk is not None and target_session_pk != att_pk:
                raise AttendanceTransactionError('Conflicting attendance session targets.', 400)
            target_session_pk = att_pk

        raw_dep_sync_uuid = (
            data.get('depends_on_sync_uuid')
            or data.get('check_in_sync_uuid')
            or data.get('parent_sync_uuid')
        )
        dep_uuid_obj = None
        if raw_dep_sync_uuid is not None and str(raw_dep_sync_uuid).strip() != '':
            try:
                dep_uuid_obj = uuid.UUID(str(raw_dep_sync_uuid).strip())
            except (TypeError, ValueError):
                raise AttendanceTransactionError('Invalid check-in dependency reference.', 400)

        with transaction.atomic():
            emp_locked = EmployeeProfile.objects.select_for_update().get(pk=employee.pk)
            if not emp_locked.is_active:
                raise AttendanceTransactionError('Employee profile is inactive.', 403)

            if sync_uuid:
                existing_loc = AttendanceLocation.objects.filter(sync_uuid=sync_uuid).select_related('attendance__employee').first()
                if existing_loc:
                    if existing_loc.attendance.employee_id != emp_locked.pk or existing_loc.event != 'check_out':
                        raise AttendanceTransactionError('Invalid idempotency key for this operation.', 400)
                    if target_session_pk is not None and target_session_pk != existing_loc.attendance_id:
                        raise AttendanceTransactionError('Conflicting retry target for existing checkout.', 400)
                    if dep_uuid_obj is not None:
                        dep_att = Attendance.objects.filter(
                            sync_uuid=dep_uuid_obj,
                            employee=emp_locked,
                            attendance_type='check_in',
                            is_expired=False
                        ).first()
                        if not dep_att or dep_att.id != existing_loc.attendance_id:
                            raise AttendanceTransactionError('Conflicting retry dependency for existing checkout.', 400)
                    return {
                        'success': True,
                        'session_id': existing_loc.attendance_id,
                        'total_hours': float(existing_loc.attendance.total_hours or 0.0)
                    }

            client_event_time_str = data.get('client_event_time')
            try:
                client_time = validate_client_time(client_event_time_str)
            except ClientTimeValidationError as exc:
                raise AttendanceTransactionError(str(exc), 400)

            if client_time:
                event_time = client_time
                synced_at = timezone.now()
                today = timezone.localdate(client_time)
            else:
                event_time = timezone.localtime()
                synced_at = None
                today = timezone.localdate()

            if target_session_pk is not None:
                attendance = Attendance.objects.filter(
                    pk=target_session_pk,
                    employee=emp_locked,
                    attendance_type='check_in',
                    is_expired=False
                ).select_for_update().first()
                if not attendance:
                    raise AttendanceTransactionError('Invalid attendance session target.', 400)
                if dep_uuid_obj is not None:
                    dep_attendance = Attendance.objects.filter(
                        sync_uuid=dep_uuid_obj,
                        employee=emp_locked,
                        attendance_type='check_in',
                        is_expired=False
                    ).select_for_update().first()
                    if not dep_attendance or dep_attendance.id != attendance.id:
                        raise AttendanceTransactionError(
                            'Conflicting checkout session target and dependency reference.',
                            400
                        )
                if attendance.check_out_time is not None:
                    return {
                        'success': True,
                        'session_id': attendance.id,
                        'total_hours': float(attendance.total_hours or 0.0)
                    }
            elif dep_uuid_obj is not None:
                attendance = Attendance.objects.filter(
                    sync_uuid=dep_uuid_obj,
                    employee=emp_locked,
                    attendance_type='check_in',
                    is_expired=False
                ).select_for_update().first()
                if not attendance:
                    raise AttendanceTransactionError(
                        'Dependent check-in session is unresolved or unauthorized.',
                        400
                    )
                if attendance.check_out_time is not None:
                    return {
                        'success': True,
                        'session_id': attendance.id,
                        'total_hours': float(attendance.total_hours or 0.0)
                    }
            else:
                open_candidates = list(
                    Attendance.objects.filter(
                        employee=emp_locked,
                        attendance_type='check_in',
                        check_out_time__isnull=True,
                        check_in_time__lte=event_time,
                        is_expired=False
                    ).order_by('-check_in_time').select_for_update()
                )
                if len(open_candidates) == 1:
                    attendance = open_candidates[0]
                elif len(open_candidates) > 1:
                    if client_time is None:
                        raise AttendanceTransactionError(
                            'Multiple open check-in sessions found. Please specify session_id.',
                            400
                        )
                    raise AttendanceTransactionError(
                        'Ambiguous open check-in sessions for offline checkout. Please specify session_id.',
                        400
                    )
                else:
                    attendance = None

            if not attendance:
                raise AttendanceTransactionError('No active check-in session found.', 400)

            policy = get_attendance_policy(emp_locked)

            from django.conf import settings
            require_gps = getattr(settings, 'REQUIRE_GPS', True)

            lat, lng, accuracy, is_gps_missing = parse_and_validate_coordinates(
                data.get('latitude'),
                data.get('longitude'),
                data.get('accuracy')
            )
            if not is_gps_missing:
                validate_location_capture_time(data.get('location_captured_at'), event_time)
            address = data.get('address', '') or 'Location unavailable at check-out'

            if is_gps_missing and require_gps:
                raise AttendanceTransactionError('Location is required for attendance.', 400)

            from apps.accounts.engine import PermissionEngine
            is_admin_or_hr = user.is_superuser or PermissionEngine.evaluate(user, 'attendance.override').allowed or PermissionEngine.evaluate(user, 'attendance.edit').allowed

            if not is_gps_missing and policy.max_gps_accuracy_meters and accuracy > policy.max_gps_accuracy_meters:
                if policy.gps_required == 'required' and not is_admin_or_hr:
                    raise AttendanceTransactionError(
                        f'GPS accuracy ({int(accuracy)}m) exceeds maximum allowed limit ({policy.max_gps_accuracy_meters}m).',
                        400
                    )

            if is_gps_missing and policy.gps_required == 'required' and not is_admin_or_hr:
                address = address or 'GPS unavailable at check-out'

            if validate_photo and policy.photo_required and not photo and not is_admin_or_hr:
                raise AttendanceTransactionError('Photo is required for attendance.', 400)

            if photo:
                validate_uploaded_photo(photo)

            if not attendance.check_in_time:
                raise AttendanceTransactionError('Malformed session: missing check-in time. Please submit an attendance correction request.', 400)

            duration = event_time - attendance.check_in_time
            diff_seconds = duration.total_seconds()
            if diff_seconds < 0:
                raise AttendanceTransactionError('Check-out time cannot be earlier than check-in time.', 400)

            diff_hours = diff_seconds / 3600.0
            if diff_hours > 999.99:
                raise AttendanceTransactionError('Session duration exceeds system limits (999.99 hours). Please submit an attendance correction request for manual review.', 400)

            attendance.check_out_time = event_time
            attendance.total_hours = round(decimal.Decimal(str(diff_hours)), 2)

            from .schedule_utils import get_branch_schedule, calculate_overtime, calculate_early_checkout
            schedule = get_branch_schedule(emp_locked)
            overtime_minutes = calculate_overtime(event_time, schedule, emp_locked, attendance.date)
            attendance.is_early_checkout = calculate_early_checkout(event_time, schedule, attendance.date)

            if overtime_minutes > 0:
                attendance.overtime_minutes = overtime_minutes
                attendance.ot_status = 'pending'
            else:
                attendance.overtime_minutes = 0
                attendance.ot_status = 'none'

            if client_time:
                attendance.client_event_time = client_time
                attendance.synced_at = synced_at

            attendance.save()

            _safe_create_with_seq_retry(
                AttendanceLocation,
                attendance=attendance,
                event='check_out',
                latitude=float(lat),
                longitude=float(lng),
                address=address,
                accuracy=float(accuracy) if accuracy else 0.0,
                timestamp=event_time,
                event_photo=photo,
                sync_uuid=sync_uuid or uuid.uuid4(),
                client_event_time=client_time,
                synced_at=synced_at
            )

            transaction.on_commit(lambda: _safe_notify_admins(emp_locked, 'check_out', location=address))

            from apps.attendance.models import AttendanceActivityLog
            try:
                _safe_create_with_seq_retry(
                    AttendanceActivityLog,
                    employee=emp_locked,
                    action='check_out',
                    description=f"Checked Out at {event_time.strftime('%I:%M %p')}"
                )
            except Exception:
                pass

            return {
                'success': True,
                'session_id': attendance.id,
                'total_hours': float(attendance.total_hours)
            }

    @staticmethod
    def field_visit(user, data, photo=None, validate_photo=True):
        from apps.attendance.views import check_role, get_employee, get_attendance_policy

        employee = get_employee(user)
        if not employee:
            raise AttendanceTransactionError('Employee profile not found.', 403)
        if not employee.is_active:
            raise AttendanceTransactionError('Employee profile is inactive.', 403)

        if not check_role(user):
            raise AttendanceTransactionError('Unauthorized role.', 403)

        master = getattr(user, 'employee_master', None)
        if not master and hasattr(user, 'employee_profile') and user.employee_profile.master_employee:
            master = user.employee_profile.master_employee
        if master and (master.is_suspended or master.business_status == 'suspended'):
            raise AttendanceTransactionError('Account is suspended.', 403)

        sync_uuid = data.get('sync_uuid')

        with transaction.atomic():
            emp_locked = EmployeeProfile.objects.select_for_update().get(pk=employee.pk)
            if not emp_locked.is_active:
                raise AttendanceTransactionError('Employee profile is inactive.', 403)

            if sync_uuid:
                existing = Attendance.objects.filter(sync_uuid=sync_uuid).first()
                if existing:
                    if existing.employee_id != emp_locked.pk or existing.attendance_type != 'field_visit':
                        raise AttendanceTransactionError('Invalid idempotency key for this operation.', 400)
                    return {'success': True, 'session_id': existing.id}

            client_event_time_str = data.get('client_event_time')
            try:
                client_time = validate_client_time(client_event_time_str)
            except ClientTimeValidationError as exc:
                raise AttendanceTransactionError(str(exc), 400)

            if client_time:
                event_time = client_time
                synced_at = timezone.now()
                today = timezone.localdate(client_time)
            else:
                event_time = timezone.localtime()
                synced_at = None
                today = timezone.localdate()

            lat, lng, accuracy, is_gps_missing = parse_and_validate_coordinates(
                data.get('latitude'),
                data.get('longitude'),
                data.get('accuracy')
            )
            if not is_gps_missing:
                validate_location_capture_time(data.get('location_captured_at'), event_time)
            address      = data.get('address', '')
            visit_title  = data.get('visit_title', '')
            client_name  = data.get('client_name', '')
            site_address = data.get('site_address', '')
            note         = data.get('note', '')

            policy = get_attendance_policy(emp_locked)
            from django.conf import settings
            require_gps = getattr(settings, 'REQUIRE_GPS', True)

            if is_gps_missing and require_gps:
                raise AttendanceTransactionError('Location is required.', 400)

            if not is_gps_missing and policy.max_gps_accuracy_meters and accuracy > policy.max_gps_accuracy_meters:
                if policy.gps_required == 'required':
                    raise AttendanceTransactionError(
                        f'GPS accuracy ({int(accuracy)}m) exceeds maximum allowed limit ({policy.max_gps_accuracy_meters}m).',
                        400
                    )

            if validate_photo and policy.photo_required and not photo:
                raise AttendanceTransactionError('Photo is required.', 400)

            if photo:
                validate_uploaded_photo(photo)

            project = None
            project_id = data.get('project') or data.get('project_id')
            if project_id:
                try:
                    from apps.projects.models import Project
                    project = Project.objects.get(pk=project_id)
                except Exception:
                    pass
            if not project:
                try:
                    from apps.projects.models import Project, ProjectTask
                    from django.db.models import Q
                    project = Project.objects.filter(Q(project_managers=emp_locked) | Q(site_engineers=emp_locked)).first()
                    if not project:
                        task = ProjectTask.objects.filter(responsible_person=emp_locked, planned_start__lte=today, planned_finish__gte=today).first()
                        if task:
                            project = task.project
                    if not project and emp_locked.branch:
                        project = Project.objects.filter(branch=emp_locked.branch).first()
                except Exception:
                    pass

            attendance = _safe_create_with_seq_retry(
                Attendance,
                employee=emp_locked,
                project=project,
                date=today,
                check_in_time=event_time,
                type='field',
                attendance_type='field_visit',
                status='on_time',
                visit_title=visit_title,
                client_name=client_name,
                site_address=site_address,
                note=note,
                photo=photo,
                sync_uuid=sync_uuid or uuid.uuid4(),
                client_event_time=client_time,
                synced_at=synced_at
            )

            _safe_create_with_seq_retry(
                AttendanceLocation,
                attendance=attendance,
                event='check_in',
                latitude=float(lat),
                longitude=float(lng),
                address=address,
                accuracy=float(accuracy) if accuracy else 0.0,
                timestamp=event_time,
                sync_uuid=uuid.uuid4(),
                client_event_time=client_time,
                synced_at=synced_at
            )

            transaction.on_commit(lambda: _safe_notify_admins(emp_locked, 'field_visit', location=site_address or address))

            from apps.attendance.models import AttendanceActivityLog
            try:
                _safe_create_with_seq_retry(
                    AttendanceActivityLog,
                    employee=emp_locked,
                    action='field_visit',
                    description=f"Field visit recorded: {visit_title or client_name or 'Visit'}"
                )
            except Exception:
                pass

            return {
                'success': True,
                'session_id': attendance.id
            }
