import json
import datetime
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from apps.attendance.models import Attendance, AttendanceLocation
from apps.employees.models import EmployeeLocationSync
from apps.branches.utils import is_within_geofence
from apps.notifications.utils import notify_admins


def get_employee(user):
    if not hasattr(user, 'employee_profile'):
        return None
    return user.employee_profile


def check_role(user):
    return user.is_authenticated and user.role in ['staff', 'manager']


# ─────────────────────────────────────────────────────────────────
# CHECK IN  (allows multiple sessions per day)
# ─────────────────────────────────────────────────────────────────
@login_required
@require_POST
def check_in(request):
    if not check_role(request.user):
        return JsonResponse({'success': False, 'error': 'Unauthorized role.'}, status=403)

    try:
        employee = get_employee(request.user)
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=400)

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

        lat      = data.get('latitude')
        lng      = data.get('longitude')
        accuracy = data.get('accuracy', 0)
        note     = data.get('note', '')
        address  = data.get('address', '')

        if lat is None or lng is None or lat == '' or lng == '':
            return JsonResponse({'success': False, 'error': 'Location is required for attendance.'}, status=400)

        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid coordinates.'}, status=400)

        # Validate Photo
        photo = request.FILES.get('photo')
        if not photo:
            return JsonResponse({'success': False, 'error': 'Photo is required for attendance.'}, status=400)

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

        # Late check — only for the FIRST check-in of the day
        now = timezone.localtime()
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
            status = calculate_attendance_status(now, schedule)
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
            check_in_time=now,
            type=attendance_type,
            attendance_type='check_in',
            status=status,
            note=note,
            photo=photo
        )

        AttendanceLocation.objects.create(
            attendance=attendance,
            event='check_in',
            latitude=float(lat),
            longitude=float(lng),
            address=address,
            accuracy=float(accuracy) if accuracy else 0.0,
            timestamp=now
        )

        notif_type = 'late' if status == 'late' else 'check_in'
        notify_admins(employee, notif_type, location=address)

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

    try:
        employee = get_employee(request.user)
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=400)

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

        lat      = data.get('latitude')
        lng      = data.get('longitude')
        accuracy = data.get('accuracy', 0)
        address  = data.get('address', '') or 'Location unavailable at check-out'

        if lat is None or lng is None or lat == '' or lng == '':
            return JsonResponse({'success': False, 'error': 'Location is required for attendance.'}, status=400)

        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid coordinates.'}, status=400)

        photo = request.FILES.get('photo')
        if not photo:
            return JsonResponse({'success': False, 'error': 'Photo is required for attendance.'}, status=400)

        allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
        if photo.content_type not in allowed_types:
            return JsonResponse({'success': False, 'error': 'Invalid file type. Only images allowed.'}, status=400)

        if photo.size > 10 * 1024 * 1024:
            return JsonResponse({'success': False, 'error': 'Photo too large. Max 10MB allowed.'}, status=400)

        now = timezone.localtime()
        attendance.check_out_time = now
        duration = now - attendance.check_in_time
        attendance.total_hours = round(duration.total_seconds() / 3600.0, 2)

        from .schedule_utils import get_branch_schedule, calculate_overtime, calculate_early_checkout
        schedule = get_branch_schedule(employee)
        attendance.overtime_minutes = calculate_overtime(now, schedule, employee, attendance.date)
        attendance.is_early_checkout = calculate_early_checkout(now, schedule, attendance.date)

        attendance.save()

        AttendanceLocation.objects.create(
            attendance=attendance,
            event='check_out',
            latitude=float(lat),
            longitude=float(lng),
            address=address,
            accuracy=float(accuracy) if accuracy else 0.0,
            timestamp=now,
            event_photo=photo
        )

        notify_admins(employee, 'check_out', location=address)

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
            return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=400)

        today = timezone.localdate()

        # Active session (checked-in, not yet checked out)
        active = Attendance.objects.filter(
            employee=employee,
            date=today,
            attendance_type='check_in',
            check_out_time__isnull=True,
            is_expired=False
        ).first()

        # All sessions today
        all_sessions = Attendance.objects.filter(
            employee=employee,
            date=today,
            attendance_type='check_in',
            is_expired=False
        ).order_by('check_in_time')

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

        # Only sync if actively checked in
        today = timezone.localdate()
        active = Attendance.objects.filter(
            employee=employee,
            date=today,
            attendance_type='check_in',
            check_out_time__isnull=True,
            is_expired=False
        ).first()

        if not active:
            return JsonResponse({'success': False, 'error': 'No active session. Location not synced.'})

        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            data = request.POST

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

        EmployeeLocationSync.objects.create(
            employee=employee,
            latitude=lat,   # already float after cast above
            longitude=lng,
            accuracy=accuracy,
            address=address
        )

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
    if not (request.user.is_authenticated and request.user.role in ['admin', 'superadmin']):
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

        today = timezone.localdate()
        now   = timezone.localtime()

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

        lat          = data.get('latitude')
        lng          = data.get('longitude')
        accuracy     = data.get('accuracy', 0)
        address      = data.get('address', '')
        visit_title  = data.get('visit_title', '')
        client_name  = data.get('client_name', '')
        site_address = data.get('site_address', '')
        note         = data.get('note', '')

        if lat is None or lng is None or lat == '' or lng == '':
            return JsonResponse({'success': False, 'error': 'Location is required.'}, status=400)

        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid coordinates.'}, status=400)

        photo = request.FILES.get('photo')
        if not photo:
            return JsonResponse({'success': False, 'error': 'Photo is required.'}, status=400)

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
            check_in_time=now,
            type='field',
            attendance_type='field_visit',
            status='on_time',
            visit_title=visit_title,
            client_name=client_name,
            site_address=site_address,
            note=note,
            photo=photo
        )

        AttendanceLocation.objects.create(
            attendance=attendance,
            event='check_in',
            latitude=float(lat),
            longitude=float(lng),
            address=address,
            accuracy=float(accuracy) if accuracy else 0.0,
            timestamp=now
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
        timestamp=timezone.now()
    )
    
    # Also save to EmployeeLocationSync for admin live dashboard
    EmployeeLocationSync.objects.create(
        employee=employee,
        latitude=lat,
        longitude=lng,
        accuracy=float(accuracy) if accuracy else 0.0,
        address=address
    )
    
    return JsonResponse({
        'success': True,
        'message': 'Location saved',
        'timestamp': timezone.now().isoformat()
    })
