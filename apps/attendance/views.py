import json
import datetime
import uuid
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render, redirect
from apps.attendance.models import Attendance, AttendanceLocation
from apps.attendance.sync_utils import parse_and_validate_client_time
from apps.employees.models import EmployeeLocationSync
from apps.branches.utils import is_within_geofence
from apps.notifications.utils import notify_admins


def get_employee(user):
    if not hasattr(user, 'employee_profile'):
        return None
    return user.employee_profile


def check_role(user):
    if not user or not user.is_authenticated:
        return False
    from apps.accounts.engine import PermissionEngine
    if PermissionEngine.evaluate(user, 'attendance.view').allowed and hasattr(user, 'employee_profile'):
        return True
    return getattr(user, 'role', '') in ('staff', 'manager', 'admin', 'hr')


def get_attendance_policy(employee):
    from apps.attendance.models import AttendancePolicy
    branch = getattr(employee, 'branch', None)
    if branch:
        policy = AttendancePolicy.objects.filter(branch=branch).first()
        if policy:
            return policy
    # fallback to global policy
    global_policy = AttendancePolicy.objects.filter(branch__isnull=True).first()
    if global_policy:
        return global_policy
    # default in-memory policy
    return AttendancePolicy(photo_required=True, geofencing_policy='warning')


# ─────────────────────────────────────────────────────────────────
# CHECK IN  (allows multiple sessions per day)
# ─────────────────────────────────────────────────────────────────
@login_required
@require_POST
def check_in(request):
    if not check_role(request.user):
        return JsonResponse({'success': False, 'error': 'Unauthorized role.'}, status=403)

    master = getattr(request.user, 'employee_master', None)
    if not master and hasattr(request.user, 'employee_profile') and request.user.employee_profile.master_employee:
        master = request.user.employee_profile.master_employee
    if master and master.is_suspended:
        return JsonResponse({'success': False, 'error': 'Account is suspended.'}, status=403)

    try:
        employee = get_employee(request.user)
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=400)

        # For multipart/form-data (file uploads), request.body is already consumed
        # by Django's parser, so we must use request.POST directly.
        content_type = request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(request.body)
            except (json.JSONDecodeError, ValueError):
                data = request.POST
        else:
            data = request.POST

        sync_uuid = data.get('sync_uuid')
        if sync_uuid:
            existing = Attendance.objects.filter(sync_uuid=sync_uuid).first()
            if existing:
                ci_loc = existing.locations.filter(event='check_in').first()
                existing_address = ci_loc.address if ci_loc else (existing.site_address or '')
                return JsonResponse({
                    'success': True,
                    'session_id': existing.id,
                    'type': existing.type,
                    'status': existing.status,
                    'address': existing_address
                })

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

        # Block new check-in if there is an ACTIVE (unclosed) session today
        active_session = Attendance.objects.filter(
            employee=employee,
            date=today,
            attendance_type='check_in',
            check_out_time__isnull=True,
            is_expired=False
        ).first()
        if active_session:
            return JsonResponse({
                'success': False,
                'error': 'You are already checked in. Please check out first.'
            }, status=400)

        policy = get_attendance_policy(employee)
        
        from django.conf import settings
        require_gps = getattr(settings, 'REQUIRE_GPS', True)

        lat      = data.get('latitude')
        lng      = data.get('longitude')
        accuracy = data.get('accuracy', 0)
        note     = data.get('note', '')
        address  = data.get('address', '')

        if lat is None or lat == '':
            lat = 0.0
        if lng is None or lng == '':
            lng = 0.0

        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid coordinates.'}, status=400)

        # GPS Validation (based on policy)
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

        is_admin_or_hr = request.user.is_superuser or getattr(request.user, 'role', '') in ('admin', 'hr')

        if is_gps_missing:
            gps_quality = 'missing'
            if policy.gps_required == 'required' and not is_admin_or_hr:
                return JsonResponse({'success': False, 'error': 'GPS location is required for attendance.'}, status=400)
            else:
                is_exception = True
                note = f"{note} [POLICY EXCEPTION: GPS location missing]".strip()
        elif is_gps_poor:
            gps_quality = 'poor'
            if policy.gps_required == 'required' and not is_admin_or_hr:
                return JsonResponse({
                    'success': False,
                    'error': f'GPS accuracy ({int(acc_val)}m) exceeds maximum allowed limit ({policy.max_gps_accuracy_meters}m).'
                }, status=400)
            else:
                is_exception = True
                note = f"{note} [POLICY EXCEPTION: Poor GPS accuracy {int(acc_val)}m]".strip()

        # Validate Photo (dependent on policy)
        photo = request.FILES.get('photo')
        if policy.photo_required and not photo:
            return JsonResponse({'success': False, 'error': 'Photo is required for attendance.'}, status=400)

        if photo:
            allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
            if photo.content_type not in allowed_types:
                return JsonResponse({'success': False, 'error': 'Invalid file type. Only images allowed.'}, status=400)

            if photo.size > 10 * 1024 * 1024:
                return JsonResponse({'success': False, 'error': 'Photo too large. Max 10MB allowed.'}, status=400)

        # Determine office/field — trust the frontend value; only auto-detect
        # via geofence if the client did not send a recognised type.
        attendance_type = data.get('type', '')
        if attendance_type not in ('office', 'field'):
            # Auto-detect: try geofence, default to 'field'
            attendance_type = 'field'
            branch = employee.branch
            if branch and branch.latitude and branch.longitude:
                within_geofence, _ = is_within_geofence(float(lat), float(lng), branch)
                if within_geofence:
                    attendance_type = 'office'

        # Geofence Validation: warn or block based on policy
        if policy.geofencing_policy != 'disabled':
            branch = employee.branch
            if branch and branch.latitude and branch.longitude:
                within_geofence, distance = is_within_geofence(float(lat), float(lng), branch)
                if not within_geofence:
                    if policy.geofencing_policy == 'block':
                        return JsonResponse({
                            'success': False,
                            'error': f'Geofence validation failed. You are outside the office radius by {int(distance)} meters.'
                        }, status=400)
                    elif policy.geofencing_policy == 'warning':
                        warning_msg = f" [GEOFENCE WARNING: Checked in {int(distance)}m outside geofence]"
                        note = f"{note}{warning_msg}".strip()

        # Holiday validation
        from .schedule_utils import is_employee_holiday
        is_holiday = is_employee_holiday(employee, today)

        if is_holiday:
            status = 'holiday_attendance'
            if not policy.allow_holiday_attendance:
                is_exception = True
                warning_msg = " [POLICY EXCEPTION: Holiday attendance not allowed]"
                note = f"{note}{warning_msg}".strip()
        else:
            # Late check — only for the FIRST check-in of the day
            first_checkin_today = Attendance.objects.filter(
                employee=employee,
                date=today,
                attendance_type='check_in',
                is_expired=False
            ).order_by('check_in_time').first()

            if first_checkin_today is None:
                # This IS the first check-in → apply late logic
                from .schedule_utils import get_branch_schedule, calculate_attendance_status
                schedule = get_branch_schedule(employee)
                status = calculate_attendance_status(event_time, schedule)
            else:
                status = 'on_time'  # subsequent sessions are never "late"

        # Check if project was submitted or can be inferred
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
                project = Project.objects.filter(Q(project_manager=employee) | Q(site_engineer=employee)).first()
                if not project:
                    task = ProjectTask.objects.filter(responsible_person=employee, planned_start__lte=today, planned_finish__gte=today).first()
                    if task:
                        project = task.project
                if not project and employee.branch:
                    project = Project.objects.filter(branch=employee.branch).first()
            except Exception:
                pass

        # Create new session
        attendance = Attendance.objects.create(
            employee=employee,
            project=project,
            date=today,
            check_in_time=event_time,
            type=attendance_type,
            attendance_type='check_in',
            status=status,
            note=note,
            photo=photo,
            is_policy_exception=is_exception,
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
        notify_admins(employee, notif_type, location=address)

        from apps.attendance.models import AttendanceActivityLog
        AttendanceActivityLog.objects.create(
            employee=employee,
            action='check_in',
            description=f"Checked In at {event_time.strftime('%I:%M %p')}"
        )

        return JsonResponse({
            'success': True,
            'session_id': attendance.id,
            'type': attendance_type,
            'status': status,
            'address': address
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ─────────────────────────────────────────────────────────────────
# CHECK OUT  (closes the active session)
# ─────────────────────────────────────────────────────────────────
@login_required
@require_POST
def check_out(request):
    if not check_role(request.user):
        return JsonResponse({'success': False, 'error': 'Unauthorized role.'}, status=403)

    master = getattr(request.user, 'employee_master', None)
    if not master and hasattr(request.user, 'employee_profile') and request.user.employee_profile.master_employee:
        master = request.user.employee_profile.master_employee
    if master and master.is_suspended:
        return JsonResponse({'success': False, 'error': 'Account is suspended.'}, status=403)

    try:
        employee = get_employee(request.user)
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=400)

        # For multipart/form-data (file uploads), request.body is already consumed
        # by Django's parser, so we must use request.POST directly.
        content_type = request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(request.body)
            except (json.JSONDecodeError, ValueError):
                data = request.POST
        else:
            data = request.POST

        sync_uuid = data.get('sync_uuid')
        if sync_uuid:
            existing_loc = AttendanceLocation.objects.filter(sync_uuid=sync_uuid, event='check_out').first()
            if existing_loc:
                return JsonResponse({
                    'success': True,
                    'total_hours': float(existing_loc.attendance.total_hours)
                })

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

        # Find the active (unclosed) session
        attendance = Attendance.objects.filter(
            employee=employee,
            date=today,
            attendance_type='check_in',
            check_out_time__isnull=True,
            is_expired=False
        ).first()

        if not attendance:
            return JsonResponse({'success': False, 'error': 'No active check-in session found.'}, status=400)

        policy = get_attendance_policy(employee)
        
        from django.conf import settings
        require_gps = getattr(settings, 'REQUIRE_GPS', True)

        lat      = data.get('latitude')
        lng      = data.get('longitude')
        accuracy = data.get('accuracy', 0)
        address  = data.get('address', '') or 'Location unavailable at check-out'

        if (lat is None or lng is None or lat == '' or lng == '') and require_gps:
            return JsonResponse({'success': False, 'error': 'Location is required for attendance.'}, status=400)

        if lat is None or lat == '':
            lat = 0.0
        if lng is None or lng == '':
            lng = 0.0

        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid coordinates.'}, status=400)

        # Validate Photo (dependent on policy)
        photo = request.FILES.get('photo')
        if policy.photo_required and not photo:
            return JsonResponse({'success': False, 'error': 'Photo is required for attendance.'}, status=400)

        if photo:
            allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
            if photo.content_type not in allowed_types:
                return JsonResponse({'success': False, 'error': 'Invalid file type. Only images allowed.'}, status=400)

            if photo.size > 10 * 1024 * 1024:
                return JsonResponse({'success': False, 'error': 'Photo too large. Max 10MB allowed.'}, status=400)

        attendance.check_out_time = event_time
        duration = event_time - attendance.check_in_time
        attendance.total_hours = round(duration.total_seconds() / 3600.0, 2)

        from .schedule_utils import get_branch_schedule, calculate_overtime, calculate_early_checkout
        schedule = get_branch_schedule(employee)
        overtime_minutes = calculate_overtime(event_time, schedule, employee, attendance.date)
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

        notify_admins(employee, 'check_out', location=address)

        from apps.attendance.models import AttendanceActivityLog
        AttendanceActivityLog.objects.create(
            employee=employee,
            action='check_out',
            description=f"Checked Out at {event_time.strftime('%I:%M %p')}"
        )

        return JsonResponse({
            'success': True,
            'total_hours': float(attendance.total_hours)
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ─────────────────────────────────────────────────────────────────
# ATTENDANCE STATUS  (returns active session + today's sessions)
# ─────────────────────────────────────────────────────────────────
@login_required
@require_GET
def attendance_status(request):
    if not check_role(request.user):
        return JsonResponse({'success': False, 'error': 'Unauthorized role.'}, status=403)

    try:
        employee = get_employee(request.user)
        if not employee:
            from apps.employees.models import Employee
            employee = Employee.objects.filter(is_active=True).first()

        today = timezone.localdate()

        # Active session (checked-in, not yet checked out)
        active = Attendance.objects.filter(
            employee=employee,
            date=today,
            attendance_type='check_in',
            check_out_time__isnull=True,
            is_expired=False
        ).first() if employee else None

        # All sessions today
        all_sessions = Attendance.objects.filter(
            employee=employee,
            date=today,
            attendance_type='check_in',
            is_expired=False
        ).order_by('check_in_time') if employee else []

        sessions_data = []
        for s in all_sessions:
            sessions_data.append({
                'id': s.id,
                'check_in_time': s.check_in_time.isoformat() if s.check_in_time else None,
                'check_out_time': s.check_out_time.isoformat() if s.check_out_time else None,
                'total_hours': float(s.total_hours) if s.total_hours else None,
                'status': s.status,
                'type': s.type,
            })

        # If requested directly in browser (HTML page request)
        accept_header = request.headers.get('accept', '')
        is_html_request = ('text/html' in accept_header or 'application/xhtml+xml' in accept_header) and not request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.GET.get('format') != 'json'

        if is_html_request:
            from django.shortcuts import render
            recent_locations = AttendanceLocation.objects.filter(
                attendance__employee=employee,
                attendance__date=today
            ).order_by('-timestamp')[:10]

            branch = employee.branch
            context = {
                'employee': employee,
                'active_session': active,
                'sessions_today': all_sessions,
                'tracking_interval': employee.tracking_interval,
                'recent_locations': recent_locations,
                'branch': branch,
            }
            return render(request, 'attendance/status.html', context)

        return JsonResponse({
            'success': True,
            'has_active_session': active is not None,
            'active_session_id': active.id if active else None,
            'active_check_in_time': active.check_in_time.isoformat() if active else None,
            'sessions_today': sessions_data,
            'tracking_interval': employee.tracking_interval,  # minutes, 0=disabled
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ─────────────────────────────────────────────────────────────────
# BACKGROUND LOCATION SYNC  (periodic ping while checked in)
# ─────────────────────────────────────────────────────────────────
@login_required
@require_POST
def location_sync(request):
    """
    Called by the employee's browser every N minutes (set by admin).
    Only saves if employee has an active check-in session.
    """
    if not check_role(request.user):
        return JsonResponse({'success': False, 'error': 'Unauthorized role.'}, status=403)

    try:
        employee = get_employee(request.user)
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=400)

        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            data = request.POST

        sync_uuid = data.get('sync_uuid')
        if sync_uuid:
            existing = EmployeeLocationSync.objects.filter(sync_uuid=sync_uuid).first()
            if existing:
                return JsonResponse({'success': True})

        client_event_time_str = data.get('client_event_time')
        client_time = parse_and_validate_client_time(client_event_time_str)

        if client_time:
            synced_at = timezone.now()
            today = timezone.localdate(client_time)
        else:
            synced_at = None
            today = timezone.localdate()

        # Only sync if actively checked in
        active = Attendance.objects.filter(
            employee=employee,
            date=today,
            attendance_type='check_in',
            check_out_time__isnull=True,
            is_expired=False
        ).first()

        if not active:
            return JsonResponse({'success': False, 'error': 'No active session. Location not synced.'})

        lat      = data.get('latitude')
        lng      = data.get('longitude')
        address  = data.get('address', '')
        try:
            accuracy = float(data.get('accuracy', 0))
        except (TypeError, ValueError):
            accuracy = 0.0

        if not lat or not lng:
            return JsonResponse({'success': False, 'error': 'Coordinates required.'}, status=400)

        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid coordinates.'}, status=400)

        sync_record = EmployeeLocationSync.objects.create(
            employee=employee,
            latitude=lat,
            longitude=lng,
            accuracy=accuracy,
            address=address,
            sync_uuid=sync_uuid or uuid.uuid4(),
            client_event_time=client_time,
            synced_at=synced_at
        )
        if client_time:
            EmployeeLocationSync.objects.filter(pk=sync_record.pk).update(timestamp=client_time)

        return JsonResponse({'success': True})

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ─────────────────────────────────────────────────────────────────
# LIVE LOCATION API  (admin dashboard polls this)
# ─────────────────────────────────────────────────────────────────
@login_required
@require_GET
def live_locations(request):
    """
    Returns the latest location ping for every employee who is currently checked in.
    Used by the admin live-map.
    """
    from apps.accounts.engine import PermissionEngine
    if not (request.user.is_authenticated and (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'attendance.view').allowed or getattr(request.user, 'role', '') == 'admin')):
        return JsonResponse({'success': False, 'error': 'Unauthorized.'}, status=403)

    today = timezone.localdate()
    active_sessions = Attendance.objects.filter(
        date=today,
        attendance_type='check_in',
        check_out_time__isnull=True,
        is_expired=False
    ).select_related('employee').prefetch_related('locations')

    active_employee_ids = [s.employee_id for s in active_sessions]
    
    # Fetch all syncs for active employees, ordered by timestamp descending
    latest_syncs_qs = EmployeeLocationSync.objects.filter(
        employee_id__in=active_employee_ids
    ).order_by('employee_id', '-timestamp')
    
    # Group by employee_id to keep only the latest sync
    latest_sync_by_emp = {}
    for sync in latest_syncs_qs:
        if sync.employee_id not in latest_sync_by_emp:
            latest_sync_by_emp[sync.employee_id] = sync

    result = []
    for session in active_sessions:
        emp = session.employee
        latest_sync = latest_sync_by_emp.get(emp.id)
        # Fall back to check-in location if no sync yet
        if not latest_sync:
            ci_loc = next((loc for loc in session.locations.all() if loc.event == 'check_in'), None)
            if ci_loc:
                result.append({
                    'employee_id': emp.employee_id,
                    'full_name': emp.full_name,
                    'lat': float(ci_loc.latitude),
                    'lng': float(ci_loc.longitude),
                    'address': ci_loc.address,
                    'timestamp': ci_loc.timestamp.isoformat(),
                    'source': 'check_in',
                })
        else:
            result.append({
                'employee_id': emp.employee_id,
                'full_name': emp.full_name,
                'lat': float(latest_sync.latitude),
                'lng': float(latest_sync.longitude),
                'address': latest_sync.address,
                'timestamp': latest_sync.timestamp.isoformat(),
                'source': 'sync',
            })

    return JsonResponse({'success': True, 'employees': result})


# ─────────────────────────────────────────────────────────────────
# FIELD VISIT SUBMIT
# ─────────────────────────────────────────────────────────────────
@login_required
@require_POST
def field_visit_submit(request):
    if not check_role(request.user):
        return JsonResponse({'success': False, 'error': 'Unauthorized role.'}, status=403)

    try:
        employee = get_employee(request.user)
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=400)

        # For multipart/form-data (file uploads), request.body is already consumed
        # by Django's parser, so we must use request.POST directly.
        content_type = request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(request.body)
            except (json.JSONDecodeError, ValueError):
                data = request.POST
        else:
            data = request.POST

        sync_uuid = data.get('sync_uuid')
        if sync_uuid:
            existing = Attendance.objects.filter(sync_uuid=sync_uuid, attendance_type='field_visit').first()
            if existing:
                return JsonResponse({'success': True})

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

        policy = get_attendance_policy(employee)
        
        from django.conf import settings
        require_gps = getattr(settings, 'REQUIRE_GPS', True)

        if (lat is None or lng is None or lat == '' or lng == '') and require_gps:
            return JsonResponse({'success': False, 'error': 'Location is required.'}, status=400)

        if lat is None or lat == '':
            lat = 0.0
        if lng is None or lng == '':
            lng = 0.0

        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid coordinates.'}, status=400)

        # Validate Photo (dependent on policy)
        photo = request.FILES.get('photo')
        if policy.photo_required and not photo:
            return JsonResponse({'success': False, 'error': 'Photo is required.'}, status=400)

        if photo:
            allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
            if photo.content_type not in allowed_types:
                return JsonResponse({'success': False, 'error': 'Invalid file type.'}, status=400)

            if photo.size > 10 * 1024 * 1024:
                return JsonResponse({'success': False, 'error': 'Photo too large. Max 10MB allowed.'}, status=400)

        # Check if project was submitted or can be inferred
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
                project = Project.objects.filter(Q(project_manager=employee) | Q(site_engineer=employee)).first()
                if not project:
                    task = ProjectTask.objects.filter(responsible_person=employee, planned_start__lte=today, planned_finish__gte=today).first()
                    if task:
                        project = task.project
                if not project and employee.branch:
                    project = Project.objects.filter(branch=employee.branch).first()
            except Exception:
                pass

        attendance = Attendance.objects.create(
            employee=employee,
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

        notify_admins(employee, 'field_visit', location=site_address or address)

        return JsonResponse({'success': True})

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@login_required
@require_GET
def get_tracking_config(request):
    """Returns tracking config for logged-in employee"""
    try:
        employee = request.user.employee_profile
        if employee.tracking_interval and employee.tracking_interval > 0:
            interval_minutes = employee.tracking_interval
        elif employee.branch and hasattr(employee.branch, 'schedule'):
            interval_minutes = employee.branch.schedule.tracking_interval_minutes
        else:
            interval_minutes = 10
    except:
        interval_minutes = 10  # fallback default
    
    # If disabled (0), return large number
    # so tracking never fires
    if interval_minutes == 0:
        interval_ms = 999 * 60 * 1000
        is_enabled = False
    else:
        interval_ms = interval_minutes * 60 * 1000
        is_enabled = True
    
    return JsonResponse({
        'interval_minutes': interval_minutes,
        'interval_ms': interval_ms,
        'is_enabled': is_enabled,
    })

@login_required  
def save_location(request):
    """Auto-saves employee location during active shift"""
    if request.method != 'POST':
        return JsonResponse(
            {'success': False}, status=405)
    
    sync_uuid = request.POST.get('sync_uuid')
    if sync_uuid:
        existing_loc = AttendanceLocation.objects.filter(sync_uuid=sync_uuid, event='auto_track').first()
        if existing_loc:
            return JsonResponse({
                'success': True,
                'message': 'Location saved',
                'timestamp': existing_loc.timestamp.isoformat()
            })

    client_event_time_str = request.POST.get('client_event_time')
    client_time = parse_and_validate_client_time(client_event_time_str)

    if client_time:
        event_time = client_time
        synced_at = timezone.now()
        today = timezone.localdate(client_time)
    else:
        event_time = timezone.now()
        synced_at = None
        today = timezone.localdate()
    
    try:
        employee = request.user.employee_profile
    except:
        return JsonResponse(
            {'success': False, 
             'error': 'Employee not found'}, status=404)
    
    active_attendance = Attendance.objects.filter(
        employee=employee,
        date=today,
        attendance_type='check_in',
        check_out_time__isnull=True,  # Not checked out
        is_expired=False
    ).first()
    
    if not active_attendance:
        # No active shift, stop tracking
        return JsonResponse({
            'success': False,
            'error': 'No active shift',
            'stop_tracking': True  # Signal frontend to stop
        })
    
    lat = request.POST.get('latitude')
    lng = request.POST.get('longitude')
    accuracy = request.POST.get('accuracy', 0)
    address = request.POST.get('address', '')
    
    if not lat or not lng:
        return JsonResponse(
            {'success': False, 
             'error': 'Location required'})
    
    # Rate limit: check if we already saved an auto_track location within the last 50 seconds
    # (only apply if not using a specific sync_uuid or client time)
    if not client_time:
        from datetime import timedelta
        recent_track = AttendanceLocation.objects.filter(
            attendance=active_attendance,
            event='auto_track',
            timestamp__gte=timezone.now() - timedelta(seconds=50)
        ).exists()
        
        if recent_track:
            return JsonResponse({
                'success': True,
                'message': 'Location already saved recently'
            })
    
    # Save location
    AttendanceLocation.objects.create(
        attendance=active_attendance,
        event='auto_track',
        latitude=lat,
        longitude=lng,
        address=address,
        accuracy=float(accuracy) if accuracy else 0.0,
        timestamp=event_time,
        sync_uuid=sync_uuid or uuid.uuid4(),
        client_event_time=client_time,
        synced_at=synced_at
    )
    
    # Also save to EmployeeLocationSync for admin live dashboard
    sync_record = EmployeeLocationSync.objects.create(
        employee=employee,
        latitude=lat,
        longitude=lng,
        accuracy=float(accuracy) if accuracy else 0.0,
        address=address,
        sync_uuid=uuid.uuid4(),
        client_event_time=client_time,
        synced_at=synced_at
    )
    if client_time:
        EmployeeLocationSync.objects.filter(pk=sync_record.pk).update(timestamp=client_time)
    
    return JsonResponse({
        'success': True,
        'message': 'Location saved',
        'timestamp': event_time.isoformat()
    })


@login_required
@require_POST
def save_mandatory_location(request):
    """Saves mandatory employee location when they access the dashboard"""
    try:
        employee = request.user.employee_profile
    except Exception:
        return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=400)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = request.POST

    sync_uuid = data.get('sync_uuid')
    if sync_uuid:
        existing = EmployeeLocationSync.objects.filter(sync_uuid=sync_uuid).first()
        if existing:
            request.session['location_approved'] = True
            return JsonResponse({'success': True})

    client_event_time_str = data.get('client_event_time')
    client_time = parse_and_validate_client_time(client_event_time_str)

    if client_time:
        synced_at = timezone.now()
    else:
        synced_at = None

    lat = data.get('latitude')
    lng = data.get('longitude')
    accuracy = data.get('accuracy', 0)
    address = data.get('address', '')

    if not lat or not lng:
        return JsonResponse({'success': False, 'error': 'Coordinates required.'}, status=400)

    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid coordinates.'}, status=400)

    sync_record = EmployeeLocationSync.objects.create(
        employee=employee,
        latitude=lat,
        longitude=lng,
        accuracy=float(accuracy) if accuracy else 0.0,
        address=address,
        sync_uuid=sync_uuid or uuid.uuid4(),
        client_event_time=client_time,
        synced_at=synced_at
    )
    if client_time:
        EmployeeLocationSync.objects.filter(pk=sync_record.pk).update(timestamp=client_time)

    request.session['location_approved'] = True

    return JsonResponse({'success': True})


# ─────────────────────────────────────────────────────────────────
# PHASE 2: FORGOT CHECKOUT & CORRECTION & OT APPROVALS
# ─────────────────────────────────────────────────────────────────
from django.db import transaction
from django.http import HttpResponse

def check_approval_permissions(user, target_employee):
    """
    Checks if the user has approval permissions for the target employee's requests.
    Returns: (is_manager, is_hr)
    """
    if not user.is_authenticated:
        return False, False
        
    is_hr = False
    from apps.accounts.engine import PermissionEngine
    res = PermissionEngine.evaluate(user, 'attendance.approve')
    if res.allowed or user.is_superuser or getattr(user, 'role', '') == 'admin':
        is_hr = True
        
    is_manager = False
    emp_master = getattr(target_employee, 'master_employee', None)
    if emp_master and emp_master.reporting_manager and emp_master.reporting_manager.user == user:
        is_manager = True
        
    return is_manager, is_hr


def render_htmx_status_badge(status_val, label):
    variant = 'neutral'
    if status_val in ['approved', 'none', 'approved']:
        variant = 'success'
    elif status_val in ['rejected', 'terminated']:
        variant = 'danger'
    elif status_val in ['pending', 'pending_manager', 'pending_hr']:
        variant = 'warning'
    return HttpResponse(f'<span class="ft-badge ft-badge-{variant}">{label}</span>')


@login_required
@require_POST
def submit_forgot_checkout(request):
    employee = get_employee(request.user)
    if not employee:
        return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=400)

    attendance_id = request.POST.get('attendance_id')
    reason = request.POST.get('reason')
    check_out_time_str = request.POST.get('check_out_time')
    
    if not attendance_id or not reason or not check_out_time_str:
        return JsonResponse({'success': False, 'error': 'Missing required fields.'}, status=400)
        
    attendance = get_object_or_404(Attendance, pk=attendance_id, employee=employee)
    if attendance.check_out_time:
        return JsonResponse({'success': False, 'error': 'Session already checked out.'}, status=400)
        
    try:
        check_out_time = timezone.datetime.fromisoformat(check_out_time_str)
        if timezone.is_naive(check_out_time):
            check_out_time = timezone.make_aware(check_out_time, timezone.get_current_timezone())
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid datetime format.'}, status=400)

    if check_out_time <= attendance.check_in_time:
        return JsonResponse({'success': False, 'error': 'Check-out time must be after check-in time.'}, status=400)

    from apps.attendance.models import ForgotCheckoutRequest
    if ForgotCheckoutRequest.objects.filter(attendance=attendance, status__in=['pending_manager', 'pending_hr']).exists():
        return JsonResponse({'success': False, 'error': 'A pending request already exists for this session.'}, status=400)

    ForgotCheckoutRequest.objects.create(
        attendance=attendance,
        reason=reason,
        check_out_time=check_out_time
    )

    from apps.attendance.models import AttendanceActivityLog
    AttendanceActivityLog.objects.create(
        employee=employee,
        action='forgot_checkout_request',
        description=f"Submitted Forgot Check-out Request for {attendance.date.strftime('%d/%m/%Y')}"
    )

    return JsonResponse({'success': True, 'message': 'Forgot checkout request submitted.'})


@login_required
@require_POST
def process_forgot_checkout(request, pk):
    from apps.attendance.models import ForgotCheckoutRequest
    req = get_object_or_404(ForgotCheckoutRequest, pk=pk)
    action = request.POST.get('action')
    rejection_reason = request.POST.get('rejection_reason', '')
    
    is_manager, is_hr = check_approval_permissions(request.user, req.attendance.employee)
    
    if not (is_manager or is_hr):
        return HttpResponseForbidden("You do not have permission to approve/reject this request.")

    if req.status not in ['pending_manager', 'pending_hr']:
        return JsonResponse({'success': False, 'error': 'This request has already been processed.'}, status=400)

    if action == 'reject':
        req.status = 'rejected'
        if req.status == 'pending_manager':
            req.reviewed_by_manager = request.user
        else:
            req.reviewed_by_hr = request.user
        req.rejection_reason = rejection_reason
        req.save()
        
        if request.headers.get('hx-request'):
            return render_htmx_status_badge('rejected', 'Rejected')
        return JsonResponse({'success': True, 'message': 'Request rejected.'})
        
    elif action == 'approve':
        if req.status == 'pending_manager':
            if not is_manager and not is_hr:
                return HttpResponseForbidden("Only reporting manager can perform manager approval.")
            req.status = 'pending_hr'
            req.reviewed_by_manager = request.user
            req.save()
            
            if request.headers.get('hx-request'):
                return render_htmx_status_badge('pending_hr', 'Pending HR Approval')
            return JsonResponse({'success': True, 'message': 'Manager approval registered. Routed to HR.'})
            
        elif req.status == 'pending_hr':
            if not is_hr:
                return HttpResponseForbidden("Only HR/Admin can perform HR approval.")
            
            with transaction.atomic():
                req.status = 'approved'
                req.reviewed_by_hr = request.user
                req.save()
                
                attendance = req.attendance
                old_ci = attendance.check_in_time
                old_co = attendance.check_out_time
                old_status = attendance.status
                
                attendance.check_out_time = req.check_out_time
                duration = req.check_out_time - attendance.check_in_time
                attendance.total_hours = round(duration.total_seconds() / 3600.0, 2)
                
                from .schedule_utils import get_branch_schedule, calculate_overtime, calculate_early_checkout
                schedule = get_branch_schedule(attendance.employee)
                overtime_minutes = calculate_overtime(req.check_out_time, schedule, attendance.employee, attendance.date)
                attendance.is_early_checkout = calculate_early_checkout(req.check_out_time, schedule, attendance.date)
                
                if overtime_minutes > 0:
                    attendance.overtime_minutes = overtime_minutes
                    attendance.ot_status = 'pending'
                else:
                    attendance.overtime_minutes = 0
                    attendance.ot_status = 'none'
                    
                attendance.save()
                
                from apps.attendance.models import AttendanceLocation, AttendanceAuditLog
                AttendanceLocation.objects.create(
                    attendance=attendance,
                    event='check_out',
                    latitude=0.0,
                    longitude=0.0,
                    address='Checked out via approved forgot-checkout request',
                    accuracy=0.0,
                    timestamp=req.check_out_time,
                    sync_uuid=uuid.uuid4(),
                    client_event_time=req.check_out_time,
                    synced_at=timezone.now()
                )
                
                AttendanceAuditLog.objects.create(
                    attendance=attendance,
                    action='forgot_checkout',
                    old_check_in_time=old_ci,
                    old_check_out_time=old_co,
                    old_status=old_status,
                    new_check_in_time=attendance.check_in_time,
                    new_check_out_time=attendance.check_out_time,
                    new_status=attendance.status,
                    reason=req.reason,
                    changed_by=request.user
                )
                
            if request.headers.get('hx-request'):
                return render_htmx_status_badge('approved', 'Approved')
            return JsonResponse({'success': True, 'message': 'Request approved and session updated.'})

    return JsonResponse({'success': False, 'error': 'Invalid action.'}, status=400)


@login_required
@require_POST
def submit_attendance_correction(request):
    employee = get_employee(request.user)
    if not employee:
        return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=400)

    attendance_id = request.POST.get('attendance_id')
    reason = request.POST.get('reason')
    check_in_time_str = request.POST.get('check_in_time')
    check_out_time_str = request.POST.get('check_out_time')
    note = request.POST.get('note', '')
    attachment = request.FILES.get('attachment')
    
    if not attendance_id or not reason:
        return JsonResponse({'success': False, 'error': 'Missing required fields.'}, status=400)
        
    attendance = get_object_or_404(Attendance, pk=attendance_id, employee=employee)
    
    proposed_check_in = None
    proposed_check_out = None
    
    try:
        if check_in_time_str:
            proposed_check_in = timezone.datetime.fromisoformat(check_in_time_str)
            if timezone.is_naive(proposed_check_in):
                proposed_check_in = timezone.make_aware(proposed_check_in, timezone.get_current_timezone())
        if check_out_time_str:
            proposed_check_out = timezone.datetime.fromisoformat(check_out_time_str)
            if timezone.is_naive(proposed_check_out):
                proposed_check_out = timezone.make_aware(proposed_check_out, timezone.get_current_timezone())
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid datetime format.'}, status=400)

    if proposed_check_in and proposed_check_out and proposed_check_out <= proposed_check_in:
        return JsonResponse({'success': False, 'error': 'Check-out time must be after check-in time.'}, status=400)

    from apps.attendance.models import AttendanceCorrectionRequest
    if AttendanceCorrectionRequest.objects.filter(attendance=attendance, status='pending').exists():
        return JsonResponse({'success': False, 'error': 'A pending correction request already exists for this session.'}, status=400)

    AttendanceCorrectionRequest.objects.create(
        attendance=attendance,
        reason=reason,
        check_in_time=proposed_check_in,
        check_out_time=proposed_check_out,
        note=note,
        attachment=attachment
    )

    from apps.attendance.models import AttendanceActivityLog
    AttendanceActivityLog.objects.create(
        employee=employee,
        action='correction_request',
        description=f"Submitted Correction Request for {attendance.date.strftime('%d/%m/%Y')}"
    )

    return JsonResponse({'success': True, 'message': 'Correction request submitted.'})


@login_required
@require_POST
def process_attendance_correction(request, pk):
    from apps.attendance.models import AttendanceCorrectionRequest
    req = get_object_or_404(AttendanceCorrectionRequest, pk=pk)
    action = request.POST.get('action')
    rejection_reason = request.POST.get('rejection_reason', '')
    
    is_manager, is_hr = check_approval_permissions(request.user, req.attendance.employee)
    if not is_manager and not is_hr:
        return HttpResponseForbidden("You do not have permission to process this request.")

    if req.status != 'pending':
        return JsonResponse({'success': False, 'error': 'This request has already been processed.'}, status=400)

    if action == 'reject':
        req.status = 'rejected'
        req.reviewed_by = request.user
        req.rejection_reason = rejection_reason
        req.save()
        
        if request.headers.get('hx-request'):
            return render_htmx_status_badge('rejected', 'Rejected')
        return JsonResponse({'success': True, 'message': 'Request rejected.'})
        
    elif action == 'approve':
        with transaction.atomic():
            req.status = 'approved'
            req.reviewed_by = request.user
            req.reviewed_at = timezone.now()
            req.save()
            
            attendance = req.attendance
            old_ci = attendance.check_in_time
            old_co = attendance.check_out_time
            old_status = attendance.status
            
            if req.check_in_time:
                attendance.check_in_time = req.check_in_time
            if req.check_out_time:
                attendance.check_out_time = req.check_out_time
            if req.note:
                attendance.note = req.note
                
            if attendance.check_in_time and attendance.check_out_time:
                duration = attendance.check_out_time - attendance.check_in_time
                attendance.total_hours = round(duration.total_seconds() / 3600.0, 2)
                
                from .schedule_utils import get_branch_schedule, calculate_overtime, calculate_early_checkout
                schedule = get_branch_schedule(attendance.employee)
                overtime_minutes = calculate_overtime(attendance.check_out_time, schedule, attendance.employee, attendance.date)
                attendance.is_early_checkout = calculate_early_checkout(attendance.check_out_time, schedule, attendance.date)
                
                if overtime_minutes > 0:
                    attendance.overtime_minutes = overtime_minutes
                    attendance.ot_status = 'pending'
                else:
                    attendance.overtime_minutes = 0
                    attendance.ot_status = 'none'
            
            attendance.save()
            
            from apps.attendance.models import AttendanceAuditLog
            AttendanceAuditLog.objects.create(
                attendance=attendance,
                action='correction',
                old_check_in_time=old_ci,
                old_check_out_time=old_co,
                old_status=old_status,
                new_check_in_time=attendance.check_in_time,
                new_check_out_time=attendance.check_out_time,
                new_status=attendance.status,
                reason=req.reason,
                changed_by=request.user
            )
            
        if request.headers.get('hx-request'):
            return render_htmx_status_badge('approved', 'Approved')
        return JsonResponse({'success': True, 'message': 'Request approved and session updated.'})

    return JsonResponse({'success': False, 'error': 'Invalid action.'}, status=400)


@login_required
@require_POST
def process_overtime(request, pk):
    attendance = get_object_or_404(Attendance, pk=pk)
    action = request.POST.get('action')
    
    is_manager, is_hr = check_approval_permissions(request.user, attendance.employee)
    if not is_manager and not is_hr:
        return HttpResponseForbidden("You do not have permission to approve/reject overtime.")

    if attendance.ot_status != 'pending':
        return JsonResponse({'success': False, 'error': 'Overtime is not in pending status.'}, status=400)

    with transaction.atomic():
        if action == 'approve':
            attendance.ot_status = 'approved'
            attendance.save()
            
            from apps.attendance.models import AttendanceAuditLog
            AttendanceAuditLog.objects.create(
                attendance=attendance,
                action='ot_approve',
                old_check_in_time=attendance.check_in_time,
                old_check_out_time=attendance.check_out_time,
                old_status=attendance.status,
                new_check_in_time=attendance.check_in_time,
                new_check_out_time=attendance.check_out_time,
                new_status=attendance.status,
                reason='Overtime approved by manager',
                changed_by=request.user
            )
            if request.headers.get('hx-request'):
                return render_htmx_status_badge('approved', 'Approved')
            return JsonResponse({'success': True, 'message': 'Overtime approved.'})
            
        elif action == 'reject':
            attendance.ot_status = 'rejected'
            attendance.save()
            
            from apps.attendance.models import AttendanceAuditLog
            AttendanceAuditLog.objects.create(
                attendance=attendance,
                action='ot_reject',
                old_check_in_time=attendance.check_in_time,
                old_check_out_time=attendance.check_out_time,
                old_status=attendance.status,
                new_check_in_time=attendance.check_in_time,
                new_check_out_time=attendance.check_out_time,
                new_status=attendance.status,
                reason='Overtime rejected by manager',
                changed_by=request.user
            )
            if request.headers.get('hx-request'):
                return render_htmx_status_badge('rejected', 'Rejected')
            return JsonResponse({'success': True, 'message': 'Overtime rejected.'})

    return JsonResponse({'success': False, 'error': 'Invalid action.'}, status=400)


from django.views.generic import ListView
from apps.accounts.mixins import RoleRequiredMixin

class AdminAttendanceRequestsView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'manager']
    template_name = 'admin_panel/attendance/requests_list.html'
    context_object_name = 'forgot_checkouts'
    
    def get_queryset(self):
        user = self.request.user
        from apps.attendance.models import ForgotCheckoutRequest
        qs = ForgotCheckoutRequest.objects.filter(status__in=['pending_manager', 'pending_hr']).select_related('attendance__employee')
        
        # Scoping check: managers only see their team
        if getattr(user, 'role', '') == 'manager' and not user.is_superuser:
            qs = qs.filter(attendance__employee__master_employee__reporting_manager__user=user)
        return qs
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        from apps.attendance.models import AttendanceCorrectionRequest, Attendance
        
        # Corrections
        corr_qs = AttendanceCorrectionRequest.objects.filter(status='pending').select_related('attendance__employee')
        if getattr(user, 'role', '') == 'manager' and not user.is_superuser:
            corr_qs = corr_qs.filter(attendance__employee__master_employee__reporting_manager__user=user)
        context['corrections'] = corr_qs
        
        # Overtime
        ot_qs = Attendance.objects.filter(ot_status='pending').select_related('employee')
        if getattr(user, 'role', '') == 'manager' and not user.is_superuser:
            ot_qs = ot_qs.filter(employee__master_employee__reporting_manager__user=user)
        context['overtimes'] = ot_qs
        
        return context


@login_required
@require_POST
def bulk_sync(request):
    try:
        data = json.loads(request.body)
        actions = data.get('actions', [])
        synced_count = 0
        
        for act in actions:
            action_type = act.get('action')
            lat = act.get('latitude')
            lng = act.get('longitude')
            accuracy = act.get('accuracy', 0.0)
            note = act.get('note', '')
            address = act.get('address', '')
            sync_uuid = act.get('sync_uuid')
            client_event_time_str = act.get('client_event_time')
            
            if sync_uuid:
                if action_type == 'check_in':
                    if Attendance.objects.filter(sync_uuid=sync_uuid).exists():
                        continue
                elif action_type == 'check_out':
                    from apps.attendance.models import AttendanceLocation
                    if AttendanceLocation.objects.filter(sync_uuid=sync_uuid, event='check_out').exists():
                        continue
                        
            client_time = parse_and_validate_client_time(client_event_time_str) or timezone.now()
            today = timezone.localdate(client_time)
            employee = get_employee(request.user)
            if not employee:
                continue
            
            if action_type == 'check_in':
                active_session = Attendance.objects.filter(
                    employee=employee,
                    date=today,
                    attendance_type='check_in',
                    check_out_time__isnull=True,
                    is_expired=False
                ).first()
                if active_session:
                    continue
                    
                policy = get_attendance_policy(employee)
                if policy.geofencing_policy != 'disabled':
                    branch = employee.branch
                    if branch and branch.latitude and branch.longitude:
                        within_geofence, distance = is_within_geofence(float(lat), float(lng), branch)
                        if not within_geofence:
                            if policy.geofencing_policy == 'block':
                                continue
                            elif policy.geofencing_policy == 'warning':
                                note = f"{note} [GEOFENCE WARNING: Checked in {int(distance)}m outside geofence]".strip()
                                
                first_checkin_today = Attendance.objects.filter(
                    employee=employee,
                    date=today,
                    attendance_type='check_in',
                    is_expired=False
                ).order_by('check_in_time').first()
                
                if first_checkin_today is None:
                    from .schedule_utils import get_branch_schedule, calculate_attendance_status
                    schedule = get_branch_schedule(employee)
                    status = calculate_attendance_status(client_time, schedule)
                else:
                    status = 'on_time'
                    
                attendance = Attendance.objects.create(
                    employee=employee,
                    date=today,
                    check_in_time=client_time,
                    type='office' if policy.geofencing_policy != 'disabled' else 'field',
                    attendance_type='check_in',
                    status=status,
                    note=note,
                    sync_uuid=sync_uuid or uuid.uuid4(),
                    client_event_time=client_time,
                    synced_at=timezone.now()
                )
                
                from apps.attendance.models import AttendanceLocation, AttendanceActivityLog
                AttendanceLocation.objects.create(
                    attendance=attendance,
                    event='check_in',
                    latitude=float(lat),
                    longitude=float(lng),
                    address=address or 'Offline Check-in location',
                    accuracy=float(accuracy),
                    timestamp=client_time,
                    sync_uuid=uuid.uuid4(),
                    client_event_time=client_time,
                    synced_at=timezone.now()
                )
                
                AttendanceActivityLog.objects.create(
                    employee=employee,
                    action='check_in',
                    description=f"Checked In (Offline Synced) at {client_time.strftime('%I:%M %p')}"
                )
                synced_count += 1
                
            elif action_type == 'check_out':
                attendance = Attendance.objects.filter(
                    employee=employee,
                    date=today,
                    attendance_type='check_in',
                    check_out_time__isnull=True,
                    is_expired=False
                ).first()
                if not attendance:
                    continue
                    
                attendance.check_out_time = client_time
                duration = client_time - attendance.check_in_time
                attendance.total_hours = round(duration.total_seconds() / 3600.0, 2)
                
                from .schedule_utils import get_branch_schedule, calculate_overtime, calculate_early_checkout
                schedule = get_branch_schedule(employee)
                overtime_minutes = calculate_overtime(client_time, schedule, employee, attendance.date)
                attendance.is_early_checkout = calculate_early_checkout(client_time, schedule, attendance.date)
                
                if overtime_minutes > 0:
                    attendance.overtime_minutes = overtime_minutes
                    attendance.ot_status = 'pending'
                else:
                    attendance.overtime_minutes = 0
                    attendance.ot_status = 'none'
                    
                attendance.client_event_time = client_time
                attendance.synced_at = timezone.now()
                attendance.save()
                
                from apps.attendance.models import AttendanceLocation, AttendanceActivityLog
                AttendanceLocation.objects.create(
                    attendance=attendance,
                    event='check_out',
                    latitude=float(lat),
                    longitude=float(lng),
                    address=address or 'Offline Check-out location',
                    accuracy=float(accuracy),
                    timestamp=client_time,
                    sync_uuid=sync_uuid or uuid.uuid4(),
                    client_event_time=client_time,
                    synced_at=timezone.now()
                )
                
                AttendanceActivityLog.objects.create(
                    employee=employee,
                    action='check_out',
                    description=f"Checked Out (Offline Synced) at {client_time.strftime('%I:%M %p')}"
                )
                synced_count += 1
                
        return JsonResponse({'success': True, 'synced': synced_count})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def employee_timeline(request):
    is_staff = request.user.role == 'staff'
    is_manager = request.user.role in ('manager', 'admin') or request.user.is_superuser
    
    if not (is_staff or is_manager):
        return redirect('accounts:login')
        
    selected_employee = None
    if is_manager:
        emp_id = request.GET.get('employee_id')
        if emp_id:
            from apps.employees.models import EmployeeProfile
            selected_employee = get_object_or_404(EmployeeProfile, pk=emp_id)
            
    if not selected_employee:
        selected_employee = get_employee(request.user)
        
    date_str = request.GET.get('date')
    if date_str:
        try:
            target_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            target_date = timezone.localdate()
    else:
        target_date = timezone.localdate()
        
    from apps.attendance.models import AttendanceLocation
    locations = AttendanceLocation.objects.filter(
        attendance__employee=selected_employee,
        attendance__date=target_date
    ).order_by('timestamp')
    
    context = {
        'selected_employee': selected_employee,
        'target_date': target_date,
        'locations': locations,
    }
    return render(request, 'staff/timeline.html', context)
