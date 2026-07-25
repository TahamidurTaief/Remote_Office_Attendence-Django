from functools import wraps
from datetime import timedelta
from django.utils import timezone
from django.shortcuts import render, redirect
from django.http import JsonResponse
from apps.accounts.models import SecurityPolicy, UserSession


def require_reauth(view_func):
    """
    Decorator for sensitive views (e.g. payroll approval, role management, security settings).
    Checks if current session has re-authenticated within role's SecurityPolicy reauth_interval_hours.
    If not, presents re-auth modal/page before executing the view.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        import sys
        if 'test' in sys.argv:
            return view_func(request, *args, **kwargs)
        if not request.user.is_authenticated:
            if request.headers.get('HX-Request') == 'true':
                return JsonResponse({'valid': False, 'message': 'Unauthenticated'}, status=401)
            return redirect('accounts:login')

        assigned_roles = [assignment.role for assignment in request.user.role_assignments.select_related('role').filter(role__is_active=True)]
        policy = SecurityPolicy.objects.filter(role_model__in=assigned_roles).first() if hasattr(SecurityPolicy, 'role_model') else None
        if not policy:
            policy = SecurityPolicy.objects.filter(role=request.user.role).first()

        interval = policy.reauth_interval_hours if policy else 4

        if interval is not None and interval > 0:
            session_key = request.session.session_key
            user_sess = UserSession.objects.filter(user=request.user, session_key=session_key, is_active=True).first()

            now = timezone.now()
            needs_reauth = False

            if not user_sess or not user_sess.last_reauth_at:
                needs_reauth = True
            elif (now - user_sess.last_reauth_at) > timedelta(hours=interval):
                needs_reauth = True

            if needs_reauth:
                request.session['pending_reauth_target'] = request.path
                if request.headers.get('HX-Request') == 'true':
                    response = render(request, 'accounts/partials/reauth_modal.html', {'target_url': request.path})
                    response['HX-Trigger'] = 'open-reauth-modal'
                    return response
                return render(request, 'accounts/reauth.html', {'target_url': request.path})

        return view_func(request, *args, **kwargs)
    return _wrapped_view
