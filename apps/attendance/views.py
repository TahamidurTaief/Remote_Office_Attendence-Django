import json
import datetime
import uuid
import base64
import re
import logging
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render, redirect
from django.middleware.csrf import get_token
from django.core.files.uploadedfile import SimpleUploadedFile
from apps.attendance.models import Attendance, AttendanceLocation
from apps.attendance.sync_utils import parse_and_validate_client_time
from apps.employees.models import EmployeeLocationSync
from apps.branches.utils import is_within_geofence
from apps.notifications.utils import notify_admins

logger = logging.getLogger(__name__)


def _decode_offline_photo(photo_data, sync_uuid=None):
    """
    Decodes base64 data URI or raw base64 string into a SimpleUploadedFile.
    Returns None if no photo data or decoding fails.
    """
    if not photo_data or not isinstance(photo_data, str) or len(photo_data.strip()) == 0:
        return None
    try:
        data = photo_data.strip()
        ext = 'jpg'
        content_type = 'image/jpeg'
        if data.startswith('data:'):
            match = re.match(r'^data:(image\/(jpeg|jpg|png|webp));base64,(.*)$', data, re.IGNORECASE)
            if match:
                content_type = match.group(1).lower()
                fmt = match.group(2).lower()
                ext = 'jpg' if fmt in ('jpeg', 'jpg') else fmt
                base64_str = match.group(3)
            else:
                parts = data.split(';base64,', 1)
                base64_str = parts[1] if len(parts) > 1 else parts[0]
        else:
            base64_str = data

        file_bytes = base64.b64decode(base64_str)
        if len(file_bytes) < 30:
            return None
        filename = f"offline_{sync_uuid or uuid.uuid4()}.{ext}"
        return SimpleUploadedFile(filename, file_bytes, content_type=content_type)
    except Exception as e:
        logger.warning(f"Failed to decode offline photo: {e}")
        return None


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
    if getattr(user, 'role', '') in ('staff', 'manager', 'admin', 'hr') or user.is_superuser or getattr(user, 'is_staff', False):
        return True
    from apps.accounts.engine import PermissionEngine
    return PermissionEngine.evaluate(user, 'attendance.view').allowed and hasattr(user, 'employee_profile')


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
# CSRF TOKEN REFRESH (Same-Origin, Authenticated, Non-Cacheable)
# ─────────────────────────────────────────────────────────────────
@require_GET
def get_csrf_token_view(request):
    """
    Same-origin, authenticated, non-cacheable CSRF token provider.
    Backed by Django get_token() to provide fresh masked token.
    """
    if not request.user.is_authenticated:
        return JsonResponse({
            'success': False,
            'error': 'Authentication required.',
            'code': 'UNAUTHENTICATED'
        }, status=401)

    token = get_token(request)
    response = JsonResponse({
        'success': True,
        'csrf_token': token
    })
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate, private'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


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

        if not check_role(request.user):
            return JsonResponse({'success': False, 'error': 'Unauthorized role.'}, status=403)

        today = timezone.localdate()

        # Active session (checked-in, not yet checked out)
        active = Attendance.objects.filter(
            employee=employee,
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

        from apps.attendance.transaction_service import (
            AttendanceTransactionError,
            parse_and_validate_coordinates,
            validate_location_capture_time,
        )
        try:
            lat, lng, accuracy, is_gps_missing = parse_and_validate_coordinates(
                data.get('latitude'),
                data.get('longitude'),
                data.get('accuracy')
            )
            if is_gps_missing:
                return JsonResponse({'success': False, 'error': 'Coordinates required.'}, status=400)
            event_ref = client_time or timezone.localtime()
            validate_location_capture_time(data.get('location_captured_at'), event_ref)
            policy = get_attendance_policy(employee)
            if policy.max_gps_accuracy_meters and accuracy > policy.max_gps_accuracy_meters and policy.gps_required == 'required':
                return JsonResponse({
                    'success': False,
                    'error': f'GPS accuracy ({int(accuracy)}m) exceeds maximum allowed limit ({policy.max_gps_accuracy_meters}m).'
                }, status=400)
        except AttendanceTransactionError as exc:
            return JsonResponse({'success': False, 'error': str(exc)}, status=exc.status_code)

        address = data.get('address', '')

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
    Returns one relevant latest location per authorized active employee.
    Enforces attendance.view with canonical employee scope and bounded SQL subqueries.
    Preserves overnight active sessions without arbitrary freshness cutoffs.
    """
    from django.db.models import OuterRef, Q, Subquery
    from apps.accounts.engine import PermissionEngine
    from apps.attendance.scoping import get_scoped_employee_queryset

    if not request.user.is_superuser:
        eval_res = PermissionEngine.evaluate(request.user, 'attendance.view', action_type='view')
        if not eval_res.allowed:
            resp = JsonResponse({'success': False, 'error': 'Unauthorized.'}, status=403)
            resp['Cache-Control'] = 'no-cache, no-store, must-revalidate, private'
            resp['Pragma'] = 'no-cache'
            return resp

    scoped_employees = get_scoped_employee_queryset(
        request.user, permission_code='attendance.view', action='view'
    )

    # Narrowing-only optional filters
    branch_id = request.GET.get('branch_id')
    if branch_id and branch_id.isdigit():
        bid = int(branch_id)
        scoped_employees = scoped_employees.filter(
            Q(branch_id=bid) | Q(master_employee__branch_id=bid)
        )
    dept = (request.GET.get('department') or '').strip()
    if dept:
        scoped_employees = scoped_employees.filter(
            Q(department__iexact=dept) | Q(master_employee__department__name__iexact=dept)
        )
    emp_code = (request.GET.get('employee_id') or '').strip()
    if emp_code:
        scoped_employees = scoped_employees.filter(employee_id=emp_code)

    active_session_qs = Attendance.objects.filter(
        employee_id=OuterRef('pk'),
        attendance_type='check_in',
        check_out_time__isnull=True,
        is_expired=False,
    ).order_by('-check_in_time', '-id')

    latest_sync_sq = Subquery(
        EmployeeLocationSync.objects.filter(
            employee_id=OuterRef('pk'),
        ).order_by('-timestamp', '-id').values('id')[:1]
    )
    latest_ci_loc_sq = Subquery(
        AttendanceLocation.objects.filter(
            attendance_id=OuterRef('latest_session_id'),
            event='check_in',
            is_expired=False,
        ).order_by('-timestamp', '-id').values('id')[:1]
    )

    active_emp_rows = list(
        scoped_employees.annotate(
            latest_session_id=Subquery(active_session_qs.values('id')[:1]),
        )
        .filter(latest_session_id__isnull=False)
        .annotate(
            latest_sync_id=latest_sync_sq,
            latest_ci_loc_id=latest_ci_loc_sq,
        )
        .values(
            'id',
            'employee_id',
            'full_name',
            'latest_session_id',
            'latest_sync_id',
            'latest_ci_loc_id',
        )
    )

    if not active_emp_rows:
        resp = JsonResponse({'success': True, 'employees': []})
        resp['Cache-Control'] = 'no-cache, no-store, must-revalidate, private'
        resp['Pragma'] = 'no-cache'
        return resp

    session_ids = [r['latest_session_id'] for r in active_emp_rows]
    sync_ids = [r['latest_sync_id'] for r in active_emp_rows if r['latest_sync_id']]
    ci_loc_ids = [r['latest_ci_loc_id'] for r in active_emp_rows if r['latest_ci_loc_id']]

    sessions_by_id = {
        s['id']: s
        for s in Attendance.objects.filter(id__in=session_ids).values('id', 'date', 'check_in_time')
    }
    syncs_by_id = (
        EmployeeLocationSync.objects.in_bulk(sync_ids) if sync_ids else {}
    )
    ci_locs_by_id = (
        AttendanceLocation.objects.in_bulk(ci_loc_ids) if ci_loc_ids else {}
    )

    today = timezone.localdate()
    result = []
    for row in active_emp_rows:
        session_info = sessions_by_id.get(row['latest_session_id'])
        if not session_info:
            continue
        check_in_dt = session_info['check_in_time']
        is_overnight = bool(session_info['date'] and session_info['date'] < today)
        latest_sync = syncs_by_id.get(row['latest_sync_id'])
        ci_loc = ci_locs_by_id.get(row['latest_ci_loc_id'])

        chosen = None
        source = None
        location_state = 'unknown'

        if latest_sync and (not check_in_dt or latest_sync.timestamp >= check_in_dt):
            chosen = latest_sync
            source = 'sync'
            location_state = 'live'
        elif ci_loc:
            chosen = ci_loc
            source = 'check_in'
            location_state = 'live'
        elif latest_sync:
            chosen = latest_sync
            source = 'sync'
            location_state = 'stale'

        if not chosen:
            continue

        result.append({
            'employee_id': row['employee_id'],
            'full_name': row['full_name'],
            'lat': float(chosen.latitude),
            'lng': float(chosen.longitude),
            'address': chosen.address,
            'timestamp': chosen.timestamp.isoformat(),
            'source': source,
            'location_state': location_state,
            'is_overnight': is_overnight,
            'check_in_time': check_in_dt.isoformat() if check_in_dt else None,
        })

    resp = JsonResponse({'success': True, 'employees': result})
    resp['Cache-Control'] = 'no-cache, no-store, must-revalidate, private'
    resp['Pragma'] = 'no-cache'
    return resp


# ─────────────────────────────────────────────────────────────────
# FIELD VISIT SUBMIT
# ─────────────────────────────────────────────────────────────────
@login_required
@require_POST
def field_visit_submit(request):
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
        res = AttendanceTransactionService.field_visit(request.user, data, photo=photo, validate_photo=True)
        return JsonResponse(res)
    except AttendanceTransactionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=e.status_code)
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
    if res.allowed or user.is_superuser or getattr(user, 'role', '') in ('admin', 'hr', 'system_owner', 'super_admin'):
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
            from apps.attendance.models import ForgotCheckoutRequest
            req = get_object_or_404(ForgotCheckoutRequest.objects.select_related('attendance__employee'), pk=pk)
            return render(request, 'admin_panel/attendance/partials/request_row_forgot.html', {'req': req})
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
            from apps.attendance.models import AttendanceCorrectionRequest
            req = get_object_or_404(AttendanceCorrectionRequest.objects.select_related('attendance__employee'), pk=pk)
            return render(request, 'admin_panel/attendance/partials/request_row_correction.html', {'req': req})
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
        if request.headers.get('hx-request'):
            ot_req = get_object_or_404(OvertimeRequest.objects.select_related('employee', 'attendance'), pk=pk)
            return render(request, 'admin_panel/attendance/partials/request_row_ot.html', {'req': ot_req})
        return JsonResponse({'success': True, 'status': ot_req.status, 'message': f'Overtime {action}d successfully.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


from django.views.generic import ListView
from apps.accounts.mixins import RoleRequiredMixin

class AdminAttendanceRequestsView(RoleRequiredMixin, ListView):
    required_permission = 'attendance.view'
    action_type = 'view'
    template_name = 'admin_panel/attendance/requests_list.html'
    context_object_name = 'forgot_checkouts'
    
    def get_queryset(self):
        user = self.request.user
        from apps.attendance.models import ForgotCheckoutRequest
        qs = ForgotCheckoutRequest.objects.filter(status__in=['pending_manager', 'pending_hr']).select_related('attendance__employee')
        
        from apps.accounts.engine import PermissionEngine
        eval_res = PermissionEngine.evaluate(user, 'attendance.view', action_type='view')
        if not user.is_superuser:
            if eval_res.data_scope in ('own', 'team'):
                qs = qs.filter(attendance__employee__master_employee__reporting_manager__user=user)
            elif eval_res.data_scope in ('department', 'branch'):
                qs = PermissionEngine.filter_by_data_scope(user, qs, employee_field='attendance__employee')
        return qs
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        from apps.attendance.models import AttendanceCorrectionRequest, OvertimeRequest
        from apps.accounts.engine import PermissionEngine
        
        eval_res = PermissionEngine.evaluate(user, 'attendance.view', action_type='view')
        is_team_scoped = not user.is_superuser and eval_res.data_scope in ('own', 'team')

        # Corrections
        corr_qs = AttendanceCorrectionRequest.objects.filter(status='pending').select_related('attendance__employee')
        if is_team_scoped:
            corr_qs = corr_qs.filter(attendance__employee__master_employee__reporting_manager__user=user)
        elif not user.is_superuser and eval_res.data_scope in ('department', 'branch'):
            corr_qs = PermissionEngine.filter_by_data_scope(user, corr_qs, employee_field='attendance__employee')
        context['corrections'] = corr_qs
        
        # Overtime Requests (Workflow-backed)
        ot_qs = OvertimeRequest.objects.filter(status__in=['pending', 'manager_approved']).select_related('employee', 'attendance')
        if is_team_scoped:
            ot_qs = ot_qs.filter(employee__master_employee__reporting_manager__user=user)
        elif not user.is_superuser and eval_res.data_scope in ('department', 'branch'):
            ot_qs = PermissionEngine.filter_by_data_scope(user, ot_qs, employee_field='employee')
        context['overtimes'] = ot_qs
        
        return context


MAX_BULK_SYNC_ITEMS = 200


def _bulk_action_tie_rank(action_type):
    if action_type == 'check_in':
        return 0
    if action_type == 'field_visit':
        return 1
    if action_type == 'check_out':
        return 2
    return 3


def _order_bulk_sync_items(raw_items):
    """
    Deterministically order offline sync items using dependency edges and validated
    event timestamps, preserving original index order on ties.
    Returns (ordered_entries, cycle_entries) where each entry is a dict with:
      index, item, action_type, item_uuid, dep_uuid, payload, time_error
    """
    import heapq
    from apps.attendance.sync_utils import ClientTimeValidationError, validate_client_time

    fallback_now = timezone.now()
    entries = []
    uuid_to_idx = {}

    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            item = {}
        action_type = item.get('action') or item.get('action_type')
        raw_uuid = item.get('uuid') or item.get('sync_uuid')
        item_uuid = str(raw_uuid).strip() if raw_uuid is not None and str(raw_uuid).strip() else None

        payload = item.get('payload')
        if not isinstance(payload, dict):
            payload = dict(item)
        else:
            payload = dict(payload)

        if item_uuid and 'sync_uuid' not in payload:
            payload['sync_uuid'] = item_uuid

        raw_dep = (
            item.get('depends_on_sync_uuid')
            or payload.get('depends_on_sync_uuid')
            or payload.get('check_in_sync_uuid')
            or payload.get('parent_sync_uuid')
        )
        dep_uuid = str(raw_dep).strip() if raw_dep is not None and str(raw_dep).strip() else None
        if dep_uuid and not payload.get('depends_on_sync_uuid'):
            payload['depends_on_sync_uuid'] = dep_uuid

        client_time_str = payload.get('client_event_time')
        if client_time_str is None:
            client_time_str = item.get('client_event_time')

        time_error = None
        event_dt = fallback_now
        try:
            parsed_dt = validate_client_time(client_time_str)
            if parsed_dt is not None:
                event_dt = parsed_dt
        except ClientTimeValidationError as exc:
            time_error = str(exc)

        entry = {
            'index': idx,
            'item': item,
            'action_type': action_type,
            'item_uuid': item_uuid,
            'dep_uuid': dep_uuid,
            'payload': payload,
            'event_dt': event_dt,
            'tie_rank': _bulk_action_tie_rank(action_type),
            'time_error': time_error,
        }
        entries.append(entry)
        if item_uuid and item_uuid not in uuid_to_idx:
            uuid_to_idx[item_uuid] = idx

    n = len(entries)
    children = [[] for _ in range(n)]
    in_degree = [0] * n
    self_cycle_indices = set()

    for idx, entry in enumerate(entries):
        dep_uuid = entry['dep_uuid']
        if not dep_uuid:
            continue
        if entry['item_uuid'] and dep_uuid == entry['item_uuid']:
            self_cycle_indices.add(idx)
            in_degree[idx] += 1
            continue
        parent_idx = uuid_to_idx.get(dep_uuid)
        if parent_idx is not None:
            children[parent_idx].append(idx)
            in_degree[idx] += 1

    ready_heap = []
    for idx, entry in enumerate(entries):
        if in_degree[idx] == 0:
            heapq.heappush(ready_heap, (entry['event_dt'], entry['tie_rank'], idx))

    ordered_entries = []
    visited = set()
    while ready_heap:
        _, _, idx = heapq.heappop(ready_heap)
        visited.add(idx)
        ordered_entries.append(entries[idx])
        for child_idx in children[idx]:
            in_degree[child_idx] -= 1
            if in_degree[child_idx] == 0:
                c_entry = entries[child_idx]
                heapq.heappush(ready_heap, (c_entry['event_dt'], c_entry['tie_rank'], child_idx))

    cycle_entries = [entries[idx] for idx in range(n) if idx not in visited]
    return ordered_entries, cycle_entries


@login_required
@require_POST
def bulk_sync(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'status': 'failed', 'error': 'Invalid JSON body.'}, status=400)

    try:
        raw_items = data.get('items') or data.get('actions') or []
        if not isinstance(raw_items, list):
            return JsonResponse({'success': False, 'status': 'failed', 'error': 'Items must be a list.'}, status=400)
        if len(raw_items) > MAX_BULK_SYNC_ITEMS:
            return JsonResponse(
                {'success': False, 'status': 'failed', 'error': f'Batch size exceeds maximum of {MAX_BULK_SYNC_ITEMS} items.'},
                status=400,
            )

        results = []
        synced_count = 0

        from apps.attendance.transaction_service import AttendanceTransactionService, AttendanceTransactionError

        ordered_entries, cycle_entries = _order_bulk_sync_items(raw_items)
        batch_item_states = {}

        for entry in cycle_entries:
            item_uuid = entry['item_uuid']
            err_msg = 'Blocked: dependency cycle detected.'
            if item_uuid:
                batch_item_states[item_uuid] = {
                    'status': 'failed',
                    'permanent': True,
                    'error': err_msg,
                }
            results.append({
                'uuid': item_uuid,
                'status': 'failed',
                'blocked': True,
                'permanent': True,
                'error': err_msg,
            })

        for entry in ordered_entries:
            item = entry['item']
            action_type = entry['action_type']
            item_uuid = entry['item_uuid']
            payload = entry['payload']
            dep_uuid = entry['dep_uuid']

            if dep_uuid and dep_uuid in batch_item_states:
                dep_state = batch_item_states[dep_uuid]
                if dep_state['status'] != 'success':
                    err_msg = f"Blocked: dependent event failed ({dep_state['error']})"
                    if item_uuid:
                        batch_item_states[item_uuid] = {
                            'status': 'failed',
                            'permanent': dep_state['permanent'],
                            'error': err_msg,
                        }
                    results.append({
                        'uuid': item_uuid,
                        'status': 'failed',
                        'blocked': True,
                        'permanent': dep_state['permanent'],
                        'error': err_msg,
                    })
                    continue
                has_explicit_target = (
                    (payload.get('session_id') is not None and payload.get('session_id') != '')
                    or (payload.get('attendance_id') is not None and payload.get('attendance_id') != '')
                )
                if action_type == 'check_out' and not has_explicit_target:
                    if dep_state.get('session_id'):
                        payload['session_id'] = dep_state['session_id']

            if entry['time_error']:
                err_msg = entry['time_error']
                if item_uuid:
                    batch_item_states[item_uuid] = {
                        'status': 'failed',
                        'permanent': True,
                        'error': err_msg,
                    }
                results.append({
                    'uuid': item_uuid,
                    'status': 'failed',
                    'blocked': False,
                    'permanent': True,
                    'error': err_msg,
                })
                continue

            # Extract and decode offline photo if present
            raw_photo = payload.get('photo') or item.get('photo') or payload.get('event_photo') or item.get('event_photo')
            photo_file = _decode_offline_photo(raw_photo, item_uuid)

            try:
                # Strictly preserve photo-required policy (validate_photo=True).
                # Missing required photos will raise AttendanceTransactionError and remain pending.
                if action_type == 'check_in':
                    res = AttendanceTransactionService.check_in(request.user, payload, photo=photo_file, validate_photo=True)
                    synced_count += 1
                    if item_uuid:
                        batch_item_states[item_uuid] = {
                            'status': 'success',
                            'session_id': res.get('session_id')
                        }
                    results.append({'uuid': item_uuid, 'status': 'success', 'data': res})
                elif action_type == 'check_out':
                    res = AttendanceTransactionService.check_out(request.user, payload, photo=photo_file, validate_photo=True)
                    synced_count += 1
                    if item_uuid:
                        batch_item_states[item_uuid] = {
                            'status': 'success',
                            'session_id': res.get('session_id')
                        }
                    results.append({'uuid': item_uuid, 'status': 'success', 'data': res})
                elif action_type == 'field_visit':
                    res = AttendanceTransactionService.field_visit(request.user, payload, photo=photo_file, validate_photo=True)
                    synced_count += 1
                    if item_uuid:
                        batch_item_states[item_uuid] = {
                            'status': 'success',
                            'session_id': res.get('session_id')
                        }
                    results.append({'uuid': item_uuid, 'status': 'success', 'data': res})
                else:
                    if item_uuid:
                        batch_item_states[item_uuid] = {
                            'status': 'failed',
                            'permanent': True,
                            'error': 'Unknown action',
                        }
                    results.append({'uuid': item_uuid, 'status': 'skipped', 'error': 'Unknown action'})
            except AttendanceTransactionError as e:
                err_str = str(e)
                is_missing_photo = 'photo is required' in err_str.lower()
                is_permanent = False if is_missing_photo else (e.status_code != 500)
                if item_uuid:
                    batch_item_states[item_uuid] = {
                        'status': 'failed',
                        'permanent': is_permanent,
                        'error': err_str
                    }
                is_blocked_dep = (
                    action_type == 'check_out'
                    and ('dependent check-in session is unresolved' in err_str.lower()
                         or 'invalid check-in dependency reference' in err_str.lower())
                )
                results.append({
                    'uuid': item_uuid,
                    'status': 'failed',
                    'blocked': is_blocked_dep,
                    'permanent': is_permanent,
                    'error': 'Photo is required by policy. Offline punch remains pending until photo is attached.' if is_missing_photo else err_str
                })
            except Exception as e:
                if item_uuid:
                    batch_item_states[item_uuid] = {
                        'status': 'failed',
                        'permanent': False,
                        'error': str(e)
                    }
                results.append({
                    'uuid': item_uuid,
                    'status': 'failed',
                    'permanent': False,
                    'error': str(e)
                })

        return JsonResponse({
            'success': True,
            'status': 'success',
            'synced': synced_count,
            'results': results
        })
    except Exception as e:
        return JsonResponse({'success': False, 'status': 'failed', 'error': str(e)}, status=500)


@login_required
def employee_timeline(request):
    from apps.accounts.engine import PermissionEngine
    eval_res = PermissionEngine.evaluate(request.user, 'attendance.view')
    if not (request.user.is_superuser or eval_res.allowed or check_role(request.user)):
        from django.http import HttpResponseForbidden
        from django.template.loader import render_to_string
        return HttpResponseForbidden(render_to_string('cotton/permission_denied_hx.html', {'message': 'You do not have permission to view the timeline.'}, request=request))
    is_manager = request.user.is_superuser or eval_res.scope in ('global', 'branch', 'department', 'team')
    is_staff = True
        
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
