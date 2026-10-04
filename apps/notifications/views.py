import json
import hashlib
from urllib.parse import urlparse
from django.conf import settings
from django.db import transaction, IntegrityError
from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Notification, WebPushSubscription


def _admin_required(view_func):
    """Decorator: allow only admin users."""
    from functools import wraps
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        from apps.accounts.engine import PermissionEngine
        if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'notifications.view').allowed):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden('Admins only.')
        return view_func(request, *args, **kwargs)
    return _wrapped


@login_required
def notification_list(request):
    from apps.accounts.engine import PermissionEngine
    filter_type = request.GET.get('type', 'all')
    notifs = Notification.objects.filter(
        recipient=request.user
    ).select_related('employee')

    is_admin = request.user.is_superuser or PermissionEngine.evaluate(request.user, 'notifications.view').allowed
    if not is_admin:
        allowed_types = ['task_assigned', 'task_completed', 'task_delayed', 'schedule_event']
        notifs = notifs.filter(notif_type__in=allowed_types)
        unread_count = Notification.objects.filter(
            recipient=request.user, is_read=False, notif_type__in=allowed_types
        ).count()
        filter_tabs = [
            ('all', 'All'),
            ('unread', 'Unread'),
            ('schedule_event', 'Schedule Alerts'),
            ('task_assigned', 'Task Assigned'),
            ('task_completed', 'Task Completed'),
            ('task_delayed', 'Task Delayed'),
        ]
    else:
        unread_count = Notification.objects.filter(
            recipient=request.user, is_read=False
        ).count()
        filter_tabs = [
            ('all', 'All'),
            ('unread', 'Unread'),
            ('check_in', 'Check-ins'),
            ('check_out', 'Check-outs'),
            ('field_visit', 'Field Visits'),
            ('schedule_event', 'Schedule Alerts'),
            ('late', 'Late Alerts'),
            ('missing', 'Missing'),
            ('task_assigned', 'Task Assigned'),
            ('task_completed', 'Task Completed'),
            ('task_delayed', 'Task Delayed'),
            ('role_changed', 'Role/Group Changed'),
            ('permission_changed', 'Permission Changed'),
        ]

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

    if request.GET.get('partial') == 'true' or request.headers.get('HX-Request') == 'true':
        return render(request, 'notifications/partials/list_partial.html', {
            'notifs': page_obj,
            'page_obj': page_obj,
            'is_paginated': page_obj.has_other_pages(),
            'filter_type': filter_type,
            'unread_count': unread_count,
            'filter_tabs': filter_tabs,
            'is_admin': is_admin,
        })

    base_template = 'base/admin_base.html' if is_admin else 'base/staff_base.html'
    return render(request, 'notifications/list.html', {
        'notifs': page_obj,
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'filter_type': filter_type,
        'unread_count': unread_count,
        'base_template': base_template,
        'filter_tabs': filter_tabs,
        'is_admin': is_admin,
    })


@login_required
def notification_count(request):
    from apps.accounts.engine import PermissionEngine
    count_query = Notification.objects.filter(
        recipient=request.user, is_read=False
    )
    is_admin = request.user.is_superuser or PermissionEngine.evaluate(request.user, 'notifications.view').allowed
    if not is_admin:
        count_query = count_query.filter(
            notif_type__in=['task_assigned', 'task_completed', 'task_delayed', 'schedule_event']
        )
    count = count_query.count()
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


def _safe_internal_redirect(path):
    if path and isinstance(path, str) and path.startswith('/') and not path.startswith('//') and '\\' not in path:
        return path
    return '/notifications/'


@login_required
def notification_feed(request):
    from django.urls import reverse

    allowed_types = ['schedule_event', 'task_assigned', 'task_completed', 'task_delayed']
    notifs = Notification.objects.filter(
        recipient=request.user,
        notif_type__in=allowed_types
    )

    after_raw = request.GET.get('after')
    if after_raw is None:
        latest_id = notifs.order_by('-id').values_list('id', flat=True).first() or 0
        response = JsonResponse({
            'notifications': [],
            'next_cursor': latest_id,
        })
        response['Cache-Control'] = 'no-store'
        return response

    if not after_raw.isdigit():
        response = JsonResponse({'error': 'Invalid cursor'}, status=400)
        response['Cache-Control'] = 'no-store'
        return response

    try:
        cursor = int(after_raw)
    except ValueError:
        response = JsonResponse({'error': 'Invalid cursor'}, status=400)
        response['Cache-Control'] = 'no-store'
        return response

    if cursor < 0 or cursor > 9223372036854775807:
        response = JsonResponse({'error': 'Cursor out of range'}, status=400)
        response['Cache-Control'] = 'no-store'
        return response

    batch = list(notifs.filter(id__gt=cursor).order_by('id')[:20])
    next_cursor = batch[-1].id if batch else cursor

    results = []
    for n in batch:
        if n.notif_type == 'schedule_event':
            redirect_url = reverse('schedule:month_view')
        else:
            redirect_url = reverse('staff:my_tasks')

        results.append({
            'id': n.id,
            'type': n.notif_type,
            'title': n.title,
            'message': n.message or n.title,
            'redirect_url': _safe_internal_redirect(redirect_url),
        })

    response = JsonResponse({
        'notifications': results,
        'next_cursor': next_cursor,
    })
    response['Cache-Control'] = 'no-store'
    return response


def _get_canonical_request_tenant(request):
    """
    Resolve active tenant strictly via get_request_tenant helper.
    Never accepts tenant IDs from client payloads. Fails closed if missing or inactive.
    """
    from apps.tenants.context import get_request_tenant
    tenant = get_request_tenant(request)
    if tenant and getattr(tenant, 'status', None) == 'active':
        return tenant
    return None


@login_required
def push_config(request):
    public_key = getattr(settings, 'WEB_PUSH_PUBLIC_KEY', '') or ''
    response = JsonResponse({
        'public_key': public_key,
        'csrf_token': get_token(request),
    })
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    return response


@login_required
def push_subscription(request):
    if request.method not in ('POST', 'PUT', 'DELETE'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    canonical_tenant = _get_canonical_request_tenant(request)
    if not canonical_tenant:
        return JsonResponse({'error': 'No active tenant context'}, status=400)

    try:
        data = json.loads(request.body.decode('utf-8')) if request.body else {}
    except Exception:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    if not isinstance(data, dict):
        return JsonResponse({'error': 'Invalid payload format'}, status=400)

    # Reject forbidden identity keys by presence regardless of value
    forbidden_keys = {'tenant', 'tenant_id', 'user', 'user_id'}
    if forbidden_keys.intersection(data.keys()):
        return JsonResponse({'error': 'Client-supplied identity parameters are forbidden'}, status=400)

    endpoint = (data.get('endpoint') or '').strip()

    # Handle Unsubscribe
    if request.method == 'DELETE' or data.get('action') == 'unsubscribe':
        if not endpoint:
            return JsonResponse({'error': 'Endpoint required'}, status=400)

        endpoint_hash = hashlib.sha256(endpoint.encode('utf-8')).hexdigest()
        with transaction.atomic():
            sub = WebPushSubscription.objects.select_for_update().filter(
                endpoint_hash=endpoint_hash,
                user=request.user,
                tenant=canonical_tenant
            ).first()
            if sub:
                sub.is_active = False
                sub.last_seen_at = timezone.now()
                sub.save(update_fields=['is_active', 'last_seen_at', 'updated_at'])

        response = JsonResponse({'status': 'ok', 'active': False})
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
        return response

    # Validate HTTPS endpoint length and format
    if not endpoint or len(endpoint) < 10 or len(endpoint) > 2048:
        return JsonResponse({'error': 'Invalid endpoint length'}, status=400)

    try:
        parsed = urlparse(endpoint)
        if parsed.scheme.lower() != 'https' or not parsed.netloc:
            return JsonResponse({'error': 'Endpoint must be a valid HTTPS URL'}, status=400)
    except Exception:
        return JsonResponse({'error': 'Malformed endpoint URL'}, status=400)

    # Validate browser keys (must match model max_length=255)
    keys = data.get('keys') if isinstance(data.get('keys'), dict) else {}
    p256dh = (keys.get('p256dh') or data.get('p256dh') or '').strip()
    auth = (keys.get('auth') or data.get('auth') or '').strip()

    if not p256dh or len(p256dh) < 10 or len(p256dh) > 255:
        return JsonResponse({'error': 'Invalid p256dh key'}, status=400)

    if not auth or len(auth) < 6 or len(auth) > 255:
        return JsonResponse({'error': 'Invalid auth key'}, status=400)

    endpoint_hash = hashlib.sha256(endpoint.encode('utf-8')).hexdigest()

    with transaction.atomic():
        existing = WebPushSubscription.objects.select_for_update().filter(
            endpoint_hash=endpoint_hash
        ).first()

        if existing:
            if existing.user_id != request.user.id:
                # Cross-user duplicate conflict: reject with 409 and preserve existing
                return JsonResponse({'error': 'Subscription conflict'}, status=409)

            # Same user: refresh existing subscription
            existing.tenant = canonical_tenant
            existing.endpoint = endpoint
            existing.p256dh = p256dh
            existing.auth = auth
            existing.is_active = True
            existing.last_seen_at = timezone.now()
            existing.save(update_fields=[
                'tenant', 'endpoint', 'p256dh', 'auth', 'is_active', 'last_seen_at', 'updated_at'
            ])
            created = False
        else:
            try:
                WebPushSubscription.objects.create(
                    tenant=canonical_tenant,
                    user=request.user,
                    endpoint=endpoint,
                    endpoint_hash=endpoint_hash,
                    p256dh=p256dh,
                    auth=auth,
                    is_active=True,
                    last_seen_at=timezone.now(),
                )
                created = True
            except IntegrityError:
                # Concurrency safety: check if another thread inserted
                race_sub = WebPushSubscription.objects.select_for_update().filter(
                    endpoint_hash=endpoint_hash
                ).first()
                if race_sub and race_sub.user_id != request.user.id:
                    return JsonResponse({'error': 'Subscription conflict'}, status=409)
                elif race_sub:
                    race_sub.tenant = canonical_tenant
                    race_sub.endpoint = endpoint
                    race_sub.p256dh = p256dh
                    race_sub.auth = auth
                    race_sub.is_active = True
                    race_sub.last_seen_at = timezone.now()
                    race_sub.save(update_fields=[
                        'tenant', 'endpoint', 'p256dh', 'auth', 'is_active', 'last_seen_at', 'updated_at'
                    ])
                    created = False
                else:
                    return JsonResponse({'error': 'Database conflict'}, status=409)

    response = JsonResponse({
        'status': 'ok',
        'active': True,
        'action': 'created' if created else 'refreshed'
    })
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    return response
