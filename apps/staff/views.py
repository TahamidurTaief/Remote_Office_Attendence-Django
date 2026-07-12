import datetime
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
import calendar
from apps.attendance.models import Attendance
from apps.employees.models import EmployeeProfile


def check_staff_role(user):
    return user.is_authenticated and user.role == 'staff'


@login_required
def home(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)
    today = timezone.localdate()
    field_visits = Attendance.objects.filter(
        employee=employee,
        date=today,
        attendance_type='field_visit',
        is_expired=False
    ).order_by('-check_in_time')

    # Check if there is an active check-in
    active_session = Attendance.objects.filter(
        employee=employee,
        date=today,
        attendance_type='check_in',
        check_out_time__isnull=True,
        is_expired=False
    ).exists()

    total_leave_left = None
    pending_leave_count = 0
    if employee:
        total_leave_left = employee.total_leave_left_by_year[today.year]
        from apps.leave.models import LeaveRequest
        pending_leave_count = LeaveRequest.objects.filter(employee=employee, status='pending').count()

    return render(request, 'staff/home.html', {
        'employee': employee,
        'field_visits': field_visits,
        'is_checked_in': active_session,
        'total_leave_left': total_leave_left,
        'pending_leave_count': pending_leave_count
    })


@login_required
def attendance_card(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)
    today = timezone.localdate()

    # Active (unclosed) session
    active_session = Attendance.objects.filter(
        employee=employee,
        date=today,
        attendance_type='check_in',
        check_out_time__isnull=True,
        is_expired=False
    ).first()

    # All completed sessions today (for history strip)
    all_sessions_today = Attendance.objects.filter(
        employee=employee,
        date=today,
        attendance_type='check_in',
        is_expired=False
    ).order_by('check_in_time')

    # Monthly stats
    now = timezone.now()
    start_date = now.replace(day=1).date()
    _, last_day = calendar.monthrange(now.year, now.month)
    end_date = now.replace(day=last_day).date()

    attendances_this_month = Attendance.objects.filter(
        employee=employee,
        date__gte=start_date,
        date__lte=end_date,
        attendance_type='check_in',
        is_expired=False
    )

    stats = {
        'present': attendances_this_month.filter(status__in=['on_time', 'late']).values('date').distinct().count(),
        'absent':  attendances_this_month.filter(status='absent').count(),
        'late':    attendances_this_month.filter(status='late').count(),
    }

    context = {
        'employee':           employee,
        'active_session':     active_session,
        'all_sessions_today': all_sessions_today,
        'stats':              stats,
        'now':                timezone.localtime(),
        # Pass tracking interval (minutes) for the JS auto-sync timer
        'tracking_interval':  employee.tracking_interval if employee else 0,
    }
    return render(request, 'staff/partials/attendance_card.html', context)


@login_required
def check_in_page(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)
    today = timezone.localdate()

    # Block only if there is an active (unclosed) session
    active = Attendance.objects.filter(
        employee=employee,
        date=today,
        attendance_type='check_in',
        check_out_time__isnull=True,
        is_expired=False
    ).first()

    if active:
        return redirect('staff:home')

    return render(request, 'staff/check_in.html', {'employee': employee})


@login_required
def attendance_history(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)

    month_str = request.GET.get('month')
    if month_str:
        try:
            target_date = datetime.datetime.strptime(month_str, '%Y-%m').date()
        except ValueError:
            target_date = timezone.localdate().replace(day=1)
    else:
        target_date = timezone.localdate().replace(day=1)

    start_date = target_date
    _, last_day = calendar.monthrange(target_date.year, target_date.month)
    end_date = target_date.replace(day=last_day)

    prev_month = (start_date - datetime.timedelta(days=1)).replace(day=1)
    next_month = (end_date + datetime.timedelta(days=1))

    attendances = Attendance.objects.filter(
        employee=employee,
        date__gte=start_date,
        date__lte=end_date,
        is_expired=False
    ).order_by('-date', '-check_in_time')

    stats = {
        'present': attendances.filter(attendance_type='check_in', status__in=['on_time', 'late']).values('date').distinct().count(),
        'absent':  attendances.filter(status='absent').count(),
        'late':    attendances.filter(attendance_type='check_in', status='late').count(),
        'field':   attendances.filter(type='field').count()
    }

    context = {
        'employee':         employee,
        'attendances':      attendances,
        'stats':            stats,
        'current_month':    target_date.strftime('%B %Y'),
        'current_month_val': target_date.strftime('%Y-%m'),
        'prev_month_val':   prev_month.strftime('%Y-%m'),
        'next_month_val':   next_month.strftime('%Y-%m'),
    }

    if request.headers.get('HX-Request'):
        return render(request, 'staff/partials/attendance_list.html', context)

    return render(request, 'staff/attendance.html', context)


@login_required
def profile(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)

    now = timezone.now()
    start_date = now.replace(day=1).date()
    _, last_day = calendar.monthrange(now.year, now.month)
    end_date = now.replace(day=last_day).date()

    attendances_this_month = Attendance.objects.filter(
        employee=employee,
        date__gte=start_date,
        date__lte=end_date,
        is_expired=False
    )

    stats = {
        'present': attendances_this_month.filter(attendance_type='check_in', status__in=['on_time', 'late']).values('date').distinct().count(),
        'absent':  attendances_this_month.filter(status='absent').count(),
        'late':    attendances_this_month.filter(attendance_type='check_in', status='late').count(),
        'field':   attendances_this_month.filter(type='field').count()
    }

    return render(request, 'staff/profile.html', {
        'employee': employee,
        'stats':    stats
    })


@login_required
def field_visit_page(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)
    return render(request, 'staff/field_visit.html', {'employee': employee})


@login_required
def staff_change_password(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    from django.contrib import messages
    if request.method == 'POST':
        current_password = request.POST.get('current_password', '')
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')
        
        errors = {}
        
        # Verify current password
        if not request.user.check_password(current_password):
            errors['current_password'] = 'Current password is incorrect.'
        
        # Validate new password
        if len(new_password) < 8:
            errors['new_password'] = 'Password must be at least 8 characters.'
        
        # Check passwords match
        if new_password != confirm_password:
            errors['confirm_password'] = 'Passwords do not match.'
        
        if not errors:
            # Set new password
            request.user.set_password(new_password)
            request.user.save()
            
            # Keep user logged in after password change
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, request.user)
            
            messages.success(request, 'Password changed successfully.')
            return redirect('staff:profile')
        
        # Return with errors
        return render(request, 'staff/change_password.html', {'errors': errors})
    
    return render(request, 'staff/change_password.html')
