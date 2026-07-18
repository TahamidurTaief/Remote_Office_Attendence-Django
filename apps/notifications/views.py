from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from .models import Notification


def _admin_required(view_func):
    """Decorator: allow only admin users."""
    from functools import wraps
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if request.user.role != 'admin':
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden('Admins only.')
        return view_func(request, *args, **kwargs)
    return _wrapped


@login_required
def notification_list(request):
    filter_type = request.GET.get('type', 'all')
    notifs = Notification.objects.filter(
        recipient=request.user
    ).select_related('employee')

    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

    if filter_type == 'unread':
        notifs = notifs.filter(is_read=False)
    elif filter_type != 'all':
        notifs = notifs.filter(notif_type=filter_type)

    paginator = Paginator(notifs, 20)
    page_number = request.GET.get('page')
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    return render(request, 'notifications/list.html', {
        'notifs': page_obj,
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'filter_type': filter_type,
        'unread_count': Notification.objects.filter(
            recipient=request.user, is_read=False
        ).count(),
        'filter_tabs': [
            ('all', 'All'),
            ('unread', 'Unread'),
            ('check_in', 'Check-ins'),
            ('check_out', 'Check-outs'),
            ('field_visit', 'Field Visits'),
            ('late', 'Late Alerts'),
            ('missing', 'Missing'),
            ('task_assigned', 'Task Assigned'),
            ('task_completed', 'Task Completed'),
            ('task_delayed', 'Task Delayed'),
            ('role_changed', 'Role/Group Changed'),
            ('permission_changed', 'Permission Changed'),
        ],
    })


@login_required
def notification_count(request):
    count = Notification.objects.filter(
        recipient=request.user, is_read=False
    ).count()
    badge = str(count) if count else ''
    hidden_class = '' if count else ' hidden'
    return HttpResponse(
        f'<span id="notif-badge" '
        f'class="absolute -top-1 -right-1 w-5 h-5 '
        f'bg-red-500 text-white text-xs rounded-full '
        f'flex items-center justify-center{hidden_class}">'
        f'{badge}</span>'
    )


@login_required
@require_POST
def mark_read(request, pk):
    Notification.objects.filter(
        pk=pk, recipient=request.user
    ).update(is_read=True)
    return JsonResponse({'success': True})


@login_required
@require_POST
def mark_all_read(request):
    Notification.objects.filter(
        recipient=request.user, is_read=False
    ).update(is_read=True)
    return redirect('notifications:list')
