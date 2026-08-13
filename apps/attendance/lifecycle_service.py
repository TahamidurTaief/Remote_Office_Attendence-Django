import uuid
import datetime
from django.utils import timezone
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.http import HttpResponseForbidden
from apps.attendance.models import Attendance, AttendanceLocation, ForgotCheckoutRequest, AttendanceCorrectionRequest, AttendanceAuditLog
from apps.attendance.sync_utils import parse_and_validate_client_time
from apps.employees.models import EmployeeProfile, EmployeeLocationSync

class AttendanceLifecycleError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code

class AttendanceLifecycleService:
    @staticmethod
    def submit_forgot_checkout(user, data):
        from apps.attendance.views import get_employee
        employee = get_employee(user)
        if not employee:
            raise AttendanceLifecycleError('Employee profile not found.', 400)

        attendance_id = data.get('attendance_id')
        reason = data.get('reason')
        check_out_time_str = data.get('check_out_time')

        if not attendance_id or not reason or not check_out_time_str:
            raise AttendanceLifecycleError('Missing required fields.', 400)

        attendance = get_object_or_404(Attendance, pk=attendance_id, employee=employee)
        if attendance.check_out_time:
            raise AttendanceLifecycleError('Session already checked out.', 400)

        try:
            check_out_time = timezone.datetime.fromisoformat(check_out_time_str)
            if timezone.is_naive(check_out_time):
                check_out_time = timezone.make_aware(check_out_time, timezone.get_current_timezone())
        except ValueError:
            raise AttendanceLifecycleError('Invalid datetime format.', 400)

        if check_out_time <= attendance.check_in_time:
            raise AttendanceLifecycleError('Check-out time must be after check-in time.', 400)

        if ForgotCheckoutRequest.objects.filter(attendance=attendance, status__in=['pending_manager', 'pending_hr']).exists():
            raise AttendanceLifecycleError('A pending request already exists for this session.', 400)

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

        return {'success': True, 'message': 'Forgot checkout request submitted.'}

    @staticmethod
    def process_forgot_checkout(user, pk, action, rejection_reason=''):
        from apps.attendance.views import check_approval_permissions
        req = get_object_or_404(ForgotCheckoutRequest, pk=pk)

        is_manager, is_hr = check_approval_permissions(user, req.attendance.employee)
        if not (is_manager or is_hr):
            raise AttendanceLifecycleError('You do not have permission to approve/reject this request.', 403)

        if req.status not in ['pending_manager', 'pending_hr']:
            raise AttendanceLifecycleError('This request has already been processed.', 400)

        if action == 'reject':
            req.status = 'rejected'
            if req.status == 'pending_manager':
                req.reviewed_by_manager = user
            else:
                req.reviewed_by_hr = user
            req.rejection_reason = rejection_reason
            req.save()
            return {'success': True, 'message': 'Request rejected.', 'status': 'rejected'}

        elif action == 'approve':
            if req.status == 'pending_manager':
                if not is_manager and not is_hr:
                    raise AttendanceLifecycleError('Only reporting manager can perform manager approval.', 403)
                req.status = 'pending_hr'
                req.reviewed_by_manager = user
                req.save()
                return {'success': True, 'message': 'Manager approval registered. Routed to HR.', 'status': 'pending_hr'}

            elif req.status == 'pending_hr':
                if not is_hr:
                    raise AttendanceLifecycleError('Only HR/Admin can perform HR approval.', 403)

                with transaction.atomic():
                    req.status = 'approved'
                    req.reviewed_by_hr = user
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
                        changed_by=user
                    )
                return {'success': True, 'message': 'Request approved and session updated.', 'status': 'approved'}

        raise AttendanceLifecycleError('Invalid action.', 400)

    @staticmethod
    def submit_attendance_correction(user, data, files):
        from apps.attendance.views import get_employee
        employee = get_employee(user)
        if not employee:
            raise AttendanceLifecycleError('Employee profile not found.', 400)

        attendance_id = data.get('attendance_id')
        reason = data.get('reason')
        check_in_time_str = data.get('check_in_time')
        check_out_time_str = data.get('check_out_time')
        note = data.get('note', '')
        attachment = files.get('attachment')

        if not attendance_id or not reason:
            raise AttendanceLifecycleError('Missing required fields.', 400)

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
            raise AttendanceLifecycleError('Invalid datetime format.', 400)

        if proposed_check_in and proposed_check_out and proposed_check_out <= proposed_check_in:
            raise AttendanceLifecycleError('Check-out time must be after check-in time.', 400)

        if AttendanceCorrectionRequest.objects.filter(attendance=attendance, status='pending').exists():
            raise AttendanceLifecycleError('A pending correction request already exists for this session.', 400)

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

        return {'success': True, 'message': 'Correction request submitted.'}

    @staticmethod
    def process_attendance_correction(user, pk, action, rejection_reason=''):
        from apps.attendance.views import check_approval_permissions
        req = get_object_or_404(AttendanceCorrectionRequest, pk=pk)

        is_manager, is_hr = check_approval_permissions(user, req.attendance.employee)
        if not is_manager and not is_hr:
            raise AttendanceLifecycleError('You do not have permission to process this request.', 403)

        if req.status != 'pending':
            raise AttendanceLifecycleError('This request has already been processed.', 400)

        if action == 'reject':
            req.status = 'rejected'
            req.reviewed_by = user
            req.rejection_reason = rejection_reason
            req.save()
            return {'success': True, 'message': 'Request rejected.', 'status': 'rejected'}

        elif action == 'approve':
            with transaction.atomic():
                req.status = 'approved'
                req.reviewed_by = user
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

                    from .schedule_utils import get_branch_schedule, calculate_overtime, calculate_early_checkout, calculate_attendance_status, is_employee_holiday
                    schedule = get_branch_schedule(attendance.employee)
                    overtime_minutes = calculate_overtime(attendance.check_out_time, schedule, attendance.employee, attendance.date)
                    attendance.is_early_checkout = calculate_early_checkout(attendance.check_out_time, schedule, attendance.date)

                    if overtime_minutes > 0:
                        attendance.overtime_minutes = overtime_minutes
                        attendance.ot_status = 'pending'
                    else:
                        attendance.overtime_minutes = 0
                        attendance.ot_status = 'none'

                    # Recalculate status (late status) if check-in time corrected
                    if req.check_in_time:
                        if is_employee_holiday(attendance.employee, attendance.date):
                            attendance.status = 'holiday_attendance'
                        else:
                            first_checkin_today = Attendance.objects.filter(
                                employee=attendance.employee,
                                date=attendance.date,
                                attendance_type='check_in',
                                is_expired=False
                            ).exclude(pk=attendance.pk).order_by('check_in_time').first()

                            if first_checkin_today is None or attendance.check_in_time < first_checkin_today.check_in_time:
                                attendance.status = calculate_attendance_status(attendance.check_in_time, schedule)
                            else:
                                attendance.status = 'on_time'

                attendance.save()

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
                    changed_by=user
                )

            return {'success': True, 'message': 'Request approved and session updated.', 'status': 'approved'}

        raise AttendanceLifecycleError('Invalid action.', 400)

    @staticmethod
    def process_overtime(user, pk, action):
        from apps.attendance.views import check_approval_permissions
        attendance = get_object_or_404(Attendance, pk=pk)

        is_manager, is_hr = check_approval_permissions(user, attendance.employee)
        if not is_manager and not is_hr:
            raise AttendanceLifecycleError('You do not have permission to approve/reject overtime.', 403)

        if attendance.ot_status != 'pending':
            raise AttendanceLifecycleError('Overtime is not in pending status.', 400)

        with transaction.atomic():
            if action == 'approve':
                attendance.ot_status = 'approved'
                attendance.save()

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
                    changed_by=user
                )
                return {'success': True, 'message': 'Overtime approved.', 'status': 'approved'}

            elif action == 'reject':
                attendance.ot_status = 'rejected'
                attendance.save()

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
                    changed_by=user
                )
                return {'success': True, 'message': 'Overtime rejected.', 'status': 'rejected'}

        raise AttendanceLifecycleError('Invalid action.', 400)

    @staticmethod
    def save_location(user, post_data):
        sync_uuid = post_data.get('sync_uuid')
        if sync_uuid:
            existing_loc = AttendanceLocation.objects.filter(sync_uuid=sync_uuid, event='auto_track').first()
            if existing_loc:
                return {
                    'success': True,
                    'message': 'Location saved',
                    'timestamp': existing_loc.timestamp.isoformat()
                }

        client_event_time_str = post_data.get('client_event_time')
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
            employee = user.employee_profile
        except Exception:
            raise AttendanceLifecycleError('Employee profile not found.', 404)

        # Lock checks for active attendance
        active_attendance = Attendance.objects.filter(
            employee=employee,
            date=today,
            attendance_type='check_in',
            check_out_time__isnull=True,
            is_expired=False
        ).first()

        if not active_attendance:
            return {
                'success': False,
                'error': 'No active shift',
                'stop_tracking': True
            }

        lat = post_data.get('latitude')
        lng = post_data.get('longitude')
        accuracy = post_data.get('accuracy', 0)
        address = post_data.get('address', '')

        if not lat or not lng:
            raise AttendanceLifecycleError('Location required.', 400)

        if not client_time:
            from datetime import timedelta
            recent_track = AttendanceLocation.objects.filter(
                attendance=active_attendance,
                event='auto_track',
                timestamp__gte=timezone.now() - timedelta(seconds=50)
            ).exists()

            if recent_track:
                return {
                    'success': True,
                    'message': 'Location already saved recently'
                }

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

        return {
            'success': True,
            'message': 'Location saved',
            'timestamp': event_time.isoformat()
        }
