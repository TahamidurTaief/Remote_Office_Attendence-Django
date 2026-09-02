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
    try:
        if hasattr(user, 'employee_profile') and user.employee_profile:
            return user.employee_profile
    except Exception:
        pass
    try:
        if hasattr(user, 'employee_master') and user.employee_master:
            emp = user.employee_master
            if hasattr(emp, 'legacy_profile') and emp.legacy_profile:
                return emp.legacy_profile
    except Exception:
        pass
    return None


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
    try:
        content_type = request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(request.body)
            except (json.JSONDecodeError, ValueError):
                data = request.POST
        else:
            data = request.POST

        photo = request.FILES.get('photo')

        from apps.attendance.transaction_service import AttendanceTransactionService, AttendanceTransactionError
        result = AttendanceTransactionService.check_in(request.user, data, photo)
        return JsonResponse(result)
    except AttendanceTransactionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=e.status_code)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ─────────────────────────────────────────────────────────────────
# CHECK OUT  (closes the active session)
# ─────────────────────────────────────────────────────────────────
@login_required
@require_POST
def check_out(request):
    try:
        content_type = request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(request.body)
            except (json.JSONDecodeError, ValueError):
                data = request.POST
        else:
            data = request.POST

        photo = request.FILES.get('photo')

        from apps.attendance.transaction_service import AttendanceTransactionService, AttendanceTransactionError
        result = AttendanceTransactionService.check_out(request.user, data, photo)
        return JsonResponse(result)
    except AttendanceTransactionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=e.status_code)
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

    accept_header = request.headers.get('accept', '')
    is_html_request = ('text/html' in accept_header or 'application/xhtml+xml' in accept_header) and not request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.GET.get('format') != 'json'

    try:
        employee = get_employee(request.user)
        if not employee:
            if is_html_request:
                from django.shortcuts import render
                return render(request, 'attendance/status.html', {
                    'error_message': 'Employee profile not found.',
                    'employee': None,
                    'active_session': None,
                    'sessions_today': [],
                    'tracking_interval': 0,
                    'recent_locations': [],
                    'branch': None,
                    'total_hours_today': 0,
                }, status=404)
            return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=404)

        if not employee.is_active:
            if is_html_request:
                from django.shortcuts import render
                return render(request, 'attendance/status.html', {
                    'error_message': 'Employee profile is inactive.',
                    'employee': None,
                    'active_session': None,
                    'sessions_today': [],
                    'tracking_interval': 0,
                    'recent_locations': [],
                    'branch': None,
                    'total_hours_today': 0,
                }, status=403)
            return JsonResponse({'success': False, 'error': 'Employee profile is inactive.'}, status=403)

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

        tracking_interval = getattr(employee, 'tracking_interval', 0) if employee else 0
        branch = getattr(employee, 'branch', None) if employee else None

        if is_html_request:
            from django.shortcuts import render
            recent_locations = AttendanceLocation.objects.filter(
                attendance__employee=employee,
                attendance__date=today
            ).order_by('-timestamp')[:10] if employee else []

            total_hours_today = sum([float(s.total_hours or 0) for s in all_sessions])

            context = {
                'employee': employee,
                'active_session': active,
                'sessions_today': all_sessions,
                'tracking_interval': tracking_interval,
                'recent_locations': recent_locations,
                'branch': branch,
                'total_hours_today': total_hours_today,
            }
            return render(request, 'attendance/status.html', context)

        return JsonResponse({
            'success': True,
            'has_active_session': active is not None,
            'active_session_id': active.id if active else None,
            'active_check_in_time': active.check_in_time.isoformat() if active else None,
            'sessions_today': sessions_data,
            'tracking_interval': tracking_interval,  # minutes, 0=disabled
        })

    except Exception as e:
        if is_html_request:
            from django.shortcuts import render
            return render(request, 'attendance/status.html', {
                'error_message': str(e),
                'employee': None,
                'active_session': None,
                'sessions_today': [],
                'tracking_interval': 0,
                'recent_locations': [],
                'branch': None,
                'total_hours_today': 0,
            })
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
            return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=404)
        if not employee.is_active:
            return JsonResponse({'success': False, 'error': 'Employee profile is inactive.'}, status=403)

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
            return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=404)
        if not employee.is_active:
            return JsonResponse({'success': False, 'error': 'Employee profile is inactive.'}, status=403)

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
    employee = get_employee(request.user)
    if not employee:
        return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=404)
    if not employee.is_active:
        return JsonResponse({'success': False, 'error': 'Employee profile is inactive.'}, status=403)
    try:
        if employee.tracking_interval and employee.tracking_interval > 0:
            interval_minutes = employee.tracking_interval
        elif employee.branch and hasattr(employee.branch, 'schedule'):
            interval_minutes = employee.branch.schedule.tracking_interval_minutes
        else:
            interval_minutes = 10
    except Exception:
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
    try:
        from apps.attendance.lifecycle_service import AttendanceLifecycleService, AttendanceLifecycleError
        result = AttendanceLifecycleService.save_location(request.user, request.POST)
        return JsonResponse(result)
    except AttendanceLifecycleError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=e.status_code)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def save_mandatory_location(request):
    """Saves mandatory employee location when they access the dashboard"""
    employee = get_employee(request.user)
    if not employee:
        return JsonResponse({'success': False, 'error': 'Employee profile not found.'}, status=404)
    if not employee.is_active:
        return JsonResponse({'success': False, 'error': 'Employee profile is inactive.'}, status=403)

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
    try:
        from apps.attendance.lifecycle_service import AttendanceLifecycleService, AttendanceLifecycleError
        result = AttendanceLifecycleService.submit_forgot_checkout(request.user, request.POST)
        return JsonResponse(result)
    except AttendanceLifecycleError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=e.status_code)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def process_forgot_checkout(request, pk):
    try:
        from apps.attendance.lifecycle_service import AttendanceLifecycleService, AttendanceLifecycleError
        action = request.POST.get('action')
        rejection_reason = request.POST.get('rejection_reason', '')
        result = AttendanceLifecycleService.process_forgot_checkout(request.user, pk, action, rejection_reason)
        
        if request.headers.get('hx-request'):
            label = 'Approved' if result['status'] == 'approved' else ('Rejected' if result['status'] == 'rejected' else 'Pending HR Approval')
            return render_htmx_status_badge(result['status'], label)
        return JsonResponse(result)
    except AttendanceLifecycleError as e:
        if request.headers.get('hx-request') and e.status_code in (403, 400):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden(str(e))
        return JsonResponse({'success': False, 'error': str(e)}, status=e.status_code)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def submit_attendance_correction(request):
    try:
        from apps.attendance.lifecycle_service import AttendanceLifecycleService, AttendanceLifecycleError
        result = AttendanceLifecycleService.submit_attendance_correction(request.user, request.POST, request.FILES)
        return JsonResponse(result)
    except AttendanceLifecycleError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=e.status_code)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def process_attendance_correction(request, pk):
    try:
        from apps.attendance.lifecycle_service import AttendanceLifecycleService, AttendanceLifecycleError
        action = request.POST.get('action')
        rejection_reason = request.POST.get('rejection_reason', '')
        result = AttendanceLifecycleService.process_attendance_correction(request.user, pk, action, rejection_reason)
        
        if request.headers.get('hx-request'):
            label = 'Approved' if result['status'] == 'approved' else 'Rejected'
            return render_htmx_status_badge(result['status'], label)
        return JsonResponse(result)
    except AttendanceLifecycleError as e:
        if request.headers.get('hx-request') and e.status_code in (403, 400):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden(str(e))
        return JsonResponse({'success': False, 'error': str(e)}, status=e.status_code)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def process_overtime(request, pk):
    try:
        from apps.attendance.models import OvertimeRequest
        from apps.workflow.services import record_action
        action = request.POST.get('action')
        if action not in ['approve', 'reject', 'return']:
            return JsonResponse({'success': False, 'error': 'Invalid action.'}, status=400)

        ot_req = get_object_or_404(OvertimeRequest, pk=pk)
        wf_instance = ot_req.workflow_instance

        if wf_instance and not wf_instance.completed_at:
            record_action(wf_instance, request.user, action, f"{action.capitalize()}d via admin requests view")
        else:
            if action == 'approve' and ot_req.status in ['pending', 'manager_approved']:
                ot_req.status = 'approved'
                ot_req.reviewed_by = request.user
                ot_req.reviewed_at = timezone.now()
                ot_req.save()
            elif action == 'reject' and ot_req.status in ['pending', 'manager_approved']:
                ot_req.status = 'rejected'
                ot_req.reviewed_by = request.user
                ot_req.reviewed_at = timezone.now()
                ot_req.save()
            elif action == 'return':
                ot_req.status = 'pending'
                ot_req.save()
            else:
                return JsonResponse({'success': False, 'error': 'Request already processed.'}, status=400)

        ot_req.refresh_from_db()
        status_label = ot_req.get_status_display() if hasattr(ot_req, 'get_status_display') else ot_req.status
        if request.headers.get('hx-request'):
            return render_htmx_status_badge(ot_req.status, status_label)
        return JsonResponse({'success': True, 'status': ot_req.status, 'message': f'Overtime {action}d successfully.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


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
        from apps.attendance.models import AttendanceCorrectionRequest, OvertimeRequest
        
        # Corrections
        corr_qs = AttendanceCorrectionRequest.objects.filter(status='pending').select_related('attendance__employee')
        if getattr(user, 'role', '') == 'manager' and not user.is_superuser:
            corr_qs = corr_qs.filter(attendance__employee__master_employee__reporting_manager__user=user)
        context['corrections'] = corr_qs
        
        # Overtime Requests (Workflow-backed)
        ot_qs = OvertimeRequest.objects.filter(status__in=['pending', 'manager_approved']).select_related('employee', 'attendance')
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
        
        from apps.attendance.transaction_service import AttendanceTransactionService, AttendanceTransactionError
        for act in actions:
            action_type = act.get('action')
            try:
                if action_type == 'check_in':
                    AttendanceTransactionService.check_in(request.user, act, validate_photo=False)
                    synced_count += 1
                elif action_type == 'check_out':
                    AttendanceTransactionService.check_out(request.user, act, validate_photo=False)
                    synced_count += 1
            except AttendanceTransactionError:
                # If an error happens (e.g. double check-in validation), skip in bulk sync
                pass
                
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
