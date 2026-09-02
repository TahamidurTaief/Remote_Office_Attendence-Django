import json
import uuid
import datetime
from django.utils import timezone
from django.db import transaction
from apps.attendance.models import Attendance, AttendanceLocation
from apps.attendance.sync_utils import parse_and_validate_client_time
from apps.branches.utils import is_within_geofence
from apps.notifications.utils import notify_admins
from apps.employees.models import EmployeeProfile

class AttendanceTransactionError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code

class AttendanceTransactionService:
    @staticmethod
    def check_in(user, data, photo=None, validate_photo=True):
        from apps.attendance.views import check_role, get_employee, get_attendance_policy

        if not check_role(user):
            raise AttendanceTransactionError('Unauthorized role.', 403)

        master = getattr(user, 'employee_master', None)
        if not master and hasattr(user, 'employee_profile') and user.employee_profile.master_employee:
            master = user.employee_profile.master_employee
        if master and (master.is_suspended or master.business_status == 'suspended'):
            raise AttendanceTransactionError('Account is suspended.', 403)

        employee = get_employee(user)
        if not employee:
            raise AttendanceTransactionError('Employee profile not found.', 403)

        sync_uuid = data.get('sync_uuid')

        with transaction.atomic():
            emp_locked = EmployeeProfile.objects.select_for_update().get(pk=employee.pk)
            if not emp_locked.is_active:
                raise AttendanceTransactionError('Employee profile is inactive.', 403)

            if sync_uuid:
                existing = Attendance.objects.filter(sync_uuid=sync_uuid).first()
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
            client_time = parse_and_validate_client_time(client_event_time_str)

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

            lat = data.get('latitude')
            lng = data.get('longitude')
            accuracy = data.get('accuracy', 0)
            note = data.get('note', '')
            address = data.get('address', '')

            if lat is None or lat == '':
                lat = 0.0
            if lng is None or lng == '':
                lng = 0.0

            try:
                lat = float(lat)
                lng = float(lng)
            except (TypeError, ValueError):
                raise AttendanceTransactionError('Invalid coordinates.', 400)

            is_exception = False
            gps_quality = 'good'
            is_gps_missing = (lat == 0.0 and lng == 0.0)
            is_gps_poor = False

            try:
                acc_val = float(accuracy) if accuracy else 0.0
            except (TypeError, ValueError):
                acc_val = 0.0

            if not is_gps_missing and policy.max_gps_accuracy_meters and acc_val > policy.max_gps_accuracy_meters:
                is_gps_poor = True

            is_admin_or_hr = user.is_superuser or getattr(user, 'role', '') in ('admin', 'hr')

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

            if validate_photo and policy.photo_required and not photo:
                raise AttendanceTransactionError('Photo is required for attendance.', 400)

            if photo:
                allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
                if photo.content_type not in allowed_types:
                    raise AttendanceTransactionError('Invalid file type. Only images allowed.', 400)
                if photo.size > 10 * 1024 * 1024:
                    raise AttendanceTransactionError('Photo too large. Max 10MB allowed.', 400)

            attendance_type = data.get('type', '')
            if attendance_type not in ('office', 'field'):
                attendance_type = 'field'
                branch = emp_locked.branch
                if branch and branch.latitude and branch.longitude:
                    within_geofence, _ = is_within_geofence(float(lat), float(lng), branch)
                    if within_geofence:
                        attendance_type = 'office'

            is_outside_geofence = False
            enforce_geofence = not policy.allow_outside_geofence or policy.geofencing_policy != 'disabled'
            if enforce_geofence:
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

            attendance = Attendance.objects.create(
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

            AttendanceLocation.objects.create(
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

            notif_type = 'late' if status == 'late' else 'check_in'
            notify_admins(emp_locked, notif_type, location=address)

            from apps.attendance.models import AttendanceActivityLog
            AttendanceActivityLog.objects.create(
                employee=emp_locked,
                action='check_in',
                description=f"Checked In at {event_time.strftime('%I:%M %p')}"
            )

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

        if not check_role(user):
            raise AttendanceTransactionError('Unauthorized role.', 403)

        master = getattr(user, 'employee_master', None)
        if not master and hasattr(user, 'employee_profile') and user.employee_profile.master_employee:
            master = user.employee_profile.master_employee
        if master and (master.is_suspended or master.business_status == 'suspended'):
            raise AttendanceTransactionError('Account is suspended.', 403)

        employee = get_employee(user)
        if not employee:
            raise AttendanceTransactionError('Employee profile not found.', 403)

        sync_uuid = data.get('sync_uuid')

        with transaction.atomic():
            emp_locked = EmployeeProfile.objects.select_for_update().get(pk=employee.pk)
            if not emp_locked.is_active:
                raise AttendanceTransactionError('Employee profile is inactive.', 403)

            if sync_uuid:
                existing_loc = AttendanceLocation.objects.filter(sync_uuid=sync_uuid, event='check_out').first()
                if existing_loc:
                    return {
                        'success': True,
                        'total_hours': float(existing_loc.attendance.total_hours)
                    }

            client_event_time_str = data.get('client_event_time')
            client_time = parse_and_validate_client_time(client_event_time_str)

            if client_time:
                event_time = client_time
                synced_at = timezone.now()
                today = timezone.localdate(client_time)
            else:
                event_time = timezone.localtime()
                synced_at = None
                today = timezone.localdate()

            attendance = Attendance.objects.filter(
                employee=emp_locked,
                attendance_type='check_in',
                check_out_time__isnull=True,
                is_expired=False
            ).order_by('-check_in_time').select_for_update().first()

            if not attendance:
                if sync_uuid:
                    completed_loc = AttendanceLocation.objects.filter(sync_uuid=sync_uuid, event='check_out').first()
                    if completed_loc:
                        return {
                            'success': True,
                            'total_hours': float(completed_loc.attendance.total_hours)
                        }
                raise AttendanceTransactionError('No active check-in session found.', 400)

            policy = get_attendance_policy(emp_locked)

            from django.conf import settings
            require_gps = getattr(settings, 'REQUIRE_GPS', True)

            lat = data.get('latitude')
            lng = data.get('longitude')
            accuracy = data.get('accuracy', 0)
            address = data.get('address', '') or 'Location unavailable at check-out'

            if (lat is None or lng is None or lat == '' or lng == '') and require_gps:
                raise AttendanceTransactionError('Location is required for attendance.', 400)

            if lat is None or lat == '':
                lat = 0.0
            if lng is None or lng == '':
                lng = 0.0

            try:
                lat = float(lat)
                lng = float(lng)
            except (TypeError, ValueError):
                raise AttendanceTransactionError('Invalid coordinates.', 400)

            if validate_photo and policy.photo_required and not photo:
                raise AttendanceTransactionError('Photo is required for attendance.', 400)

            if photo:
                allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
                if photo.content_type not in allowed_types:
                    raise AttendanceTransactionError('Invalid file type. Only images allowed.', 400)
                if photo.size > 10 * 1024 * 1024:
                    raise AttendanceTransactionError('Photo too large. Max 10MB allowed.', 400)

            attendance.check_out_time = event_time
            duration = event_time - attendance.check_in_time
            attendance.total_hours = round(duration.total_seconds() / 3600.0, 2)

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

            AttendanceLocation.objects.create(
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

            notify_admins(emp_locked, 'check_out', location=address)

            from apps.attendance.models import AttendanceActivityLog
            AttendanceActivityLog.objects.create(
                employee=emp_locked,
                action='check_out',
                description=f"Checked Out at {event_time.strftime('%I:%M %p')}"
            )

            return {
                'success': True,
                'total_hours': float(attendance.total_hours)
            }

    @staticmethod
    def field_visit(user, data, photo=None, validate_photo=True):
        from apps.attendance.views import check_role, get_employee, get_attendance_policy

        if not check_role(user):
            raise AttendanceTransactionError('Unauthorized role.', 403)

        master = getattr(user, 'employee_master', None)
        if not master and hasattr(user, 'employee_profile') and user.employee_profile.master_employee:
            master = user.employee_profile.master_employee
        if master and (master.is_suspended or master.business_status == 'suspended'):
            raise AttendanceTransactionError('Account is suspended.', 403)

        employee = get_employee(user)
        if not employee:
            raise AttendanceTransactionError('Employee profile not found.', 403)

        sync_uuid = data.get('sync_uuid')

        with transaction.atomic():
            emp_locked = EmployeeProfile.objects.select_for_update().get(pk=employee.pk)
            if not emp_locked.is_active:
                raise AttendanceTransactionError('Employee profile is inactive.', 403)

            if sync_uuid:
                existing = Attendance.objects.filter(sync_uuid=sync_uuid, attendance_type='field_visit').first()
                if existing:
                    return {'success': True, 'session_id': existing.id}

            client_event_time_str = data.get('client_event_time')
            client_time = parse_and_validate_client_time(client_event_time_str)

            if client_time:
                event_time = client_time
                synced_at = timezone.now()
                today = timezone.localdate(client_time)
            else:
                event_time = timezone.localtime()
                synced_at = None
                today = timezone.localdate()

            lat          = data.get('latitude')
            lng          = data.get('longitude')
            accuracy     = data.get('accuracy', 0)
            address      = data.get('address', '')
            visit_title  = data.get('visit_title', '')
            client_name  = data.get('client_name', '')
            site_address = data.get('site_address', '')
            note         = data.get('note', '')

            policy = get_attendance_policy(emp_locked)
            from django.conf import settings
            require_gps = getattr(settings, 'REQUIRE_GPS', True)

            if (lat is None or lng is None or lat == '' or lng == '') and require_gps:
                raise AttendanceTransactionError('Location is required.', 400)

            if lat is None or lat == '':
                lat = 0.0
            if lng is None or lng == '':
                lng = 0.0

            try:
                lat = float(lat)
                lng = float(lng)
            except (TypeError, ValueError):
                raise AttendanceTransactionError('Invalid coordinates.', 400)

            if validate_photo and policy.photo_required and not photo:
                raise AttendanceTransactionError('Photo is required.', 400)

            if photo:
                allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
                if photo.content_type not in allowed_types:
                    raise AttendanceTransactionError('Invalid file type.', 400)
                if photo.size > 10 * 1024 * 1024:
                    raise AttendanceTransactionError('Photo too large. Max 10MB allowed.', 400)

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

            attendance = Attendance.objects.create(
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

            AttendanceLocation.objects.create(
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

            notify_admins(emp_locked, 'field_visit', location=site_address or address)

            from apps.attendance.models import AttendanceActivityLog
            AttendanceActivityLog.objects.create(
                employee=emp_locked,
                action='field_visit',
                description=f"Field visit recorded: {visit_title or client_name or 'Visit'}"
            )

            return {
                'success': True,
                'session_id': attendance.id
            }
