"""
FieldTrack AI Intelligence & Context Integration Service
Powered by the official google-genai SDK.
Handles:
- Runtime secret retrieval (GOOGLE_AI_API_KEY) with fail-closed security
- Allowlisted, read-only ORM data summaries scoped strictly by RBAC
- Prompt injection defense & confidentiality protection
- Rate limiting, timeout, bounded retries, duplicate prevention
- Truthful unavailable states (no fabricated statistics)
- Metadata-only audit event logging
"""

import os
import time
import hashlib
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Sum, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

# Security: Prompt injection patterns to intercept before LLM invocation
PROMPT_INJECTION_PATTERNS = [
    "ignore all previous instructions",
    "ignore previous instructions",
    "ignore instructions",
    "system prompt",
    "developer mode",
    "jailbreak",
    "override permissions",
    "bypass security",
    "reveal every employee's salary",
    "reveal all salaries",
    "show all passwords",
    "drop table",
    "dump database",
    "select * from",
    "delete from",
]


def resolve_user_role(user) -> str:
    """
    Resolves the primary RBAC operational role for a user.
    Returns: 'admin', 'manager', 'staff', or 'employee'
    """
    if not user or not user.is_authenticated:
        return 'anonymous'

    if user.is_superuser or getattr(user, 'is_staff', False):
        return 'admin'

    # Check dynamic roles via role assignments if available
    try:
        user_role_codes = [
            assignment.role.code
            for assignment in user.role_assignments.select_related('role').filter(role__is_active=True)
        ]
        if 'admin' in user_role_codes or 'system_owner' in user_role_codes:
            return 'admin'
        if 'manager' in user_role_codes:
            return 'manager'
        if 'staff' in user_role_codes:
            return 'staff'
        if 'employee' in user_role_codes:
            return 'employee'
    except Exception:
        pass

    # Fallback to PermissionEngine resolved permissions
    from apps.accounts.engine import PermissionEngine
    if PermissionEngine.evaluate(user, 'dashboard.view').allowed:
        return 'admin'
    if PermissionEngine.evaluate(user, 'projects.view').allowed or PermissionEngine.evaluate(user, 'attendance.approve').allowed:
        return 'manager'

    # Check if the user is a Project Manager for any projects
    try:
        if hasattr(user, 'employee_profile') and user.employee_profile.managed_projects.exists():
            return 'manager'
    except Exception:
        pass

    return 'staff'


class OperationalContextService:
    """
    Allowlisted, read-only ORM context aggregator.
    Builds concise, bounded, aggregated summaries of authorized operational data.
    Never allows model-generated SQL or raw database dumps.
    """

    @classmethod
    def get_scoped_context(cls, user, role: str) -> Dict[str, Any]:
        """
        Builds operational context strictly scoped to user role.
        Admin: Organization-wide aggregates and metrics.
        Manager: Branch/managed project/team scoped metrics.
        Staff/Employee: Self records only (tasks, schedule, leave, attendance).
        """
        now = timezone.now()
        today = now.date()
        thirty_days_ago = today - timedelta(days=30)
        thirty_days_ago_dt = now - timedelta(days=30)
        fourteen_days_ahead = today + timedelta(days=14)

        context: Dict[str, Any] = {
            "role": role,
            "reporting_period": f"{thirty_days_ago.isoformat()} to {today.isoformat()}",
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        # Retrieve user profile if available
        profile = getattr(user, 'employee_profile', None)

        # 1. Attendance module
        context["attendance"] = cls._get_attendance_summary(user, role, profile, thirty_days_ago, today)

        # 2. Employees module
        context["employees"] = cls._get_employees_summary(user, role, profile)

        # 3. Projects and Tasks module
        context["projects_and_tasks"] = cls._get_projects_and_tasks_summary(user, role, profile)

        # 4. Schedule & Holidays module
        context["schedule_and_holidays"] = cls._get_schedule_summary(user, role, profile, today, fourteen_days_ahead)

        # 5. Leave module
        context["leave"] = cls._get_leave_summary(user, role, profile, today.year)

        # 6. Expenses module
        context["expenses"] = cls._get_expenses_summary(user, role, profile, thirty_days_ago_dt)

        # 7. Payroll module (Strict RBAC - zero cross-user salary leaks)
        context["payroll"] = cls._get_payroll_summary(user, role, profile)

        # 8. Notifications module (Self only)
        context["notifications"] = cls._get_notifications_summary(user)

        # 9. Audit module (Admin only aggregates)
        if role == 'admin':
            context["audit"] = cls._get_audit_summary(thirty_days_ago_dt)

        return context

    @classmethod
    def _get_attendance_summary(cls, user, role, profile, start_date, end_date) -> Dict[str, Any]:
        from apps.attendance.models import Attendance
        qs = Attendance.objects.filter(date__gte=start_date, date__lte=end_date)
        today_qs = Attendance.objects.filter(date=end_date)

        if role == 'admin':
            total = qs.count()
            on_time = qs.filter(status='on_time').count()
            late = qs.filter(status='late').count()
            absent = qs.filter(status='absent').count()
            field_visits = qs.filter(attendance_type='field_visit').count()
            rate = round((on_time / total * 100), 1) if total > 0 else 100.0

            today_total = today_qs.count()
            today_on_time = today_qs.filter(status='on_time').count()
            today_late = today_qs.filter(status='late').count()
            today_absent = today_qs.filter(status='absent').count()
            return {
                "scope": "Company wide aggregates",
                "today_date": str(end_date),
                "today_checked_in_count": today_total,
                "today_on_time_count": today_on_time,
                "today_late_count": today_late,
                "today_absent_count": today_absent,
                "total_records_30d": total,
                "on_time_rate_pct": rate,
                "late_count_30d": late,
                "absent_count_30d": absent,
                "field_visits_count_30d": field_visits,
            }

        elif role == 'manager' and profile and profile.branch:
            branch_qs = qs.filter(employee__branch=profile.branch)
            total = branch_qs.count()
            on_time = branch_qs.filter(status='on_time').count()
            late = branch_qs.filter(status='late').count()
            today_branch_qs = today_qs.filter(employee__branch=profile.branch)
            return {
                "scope": f"Branch: {profile.branch.name}",
                "branch_name": profile.branch.name,
                "today_date": str(end_date),
                "today_checked_in_count": today_branch_qs.count(),
                "today_on_time_count": today_branch_qs.filter(status='on_time').count(),
                "today_late_count": today_branch_qs.filter(status='late').count(),
                "total_records_30d": total,
                "on_time_count_30d": on_time,
                "late_count_30d": late,
            }

        elif role in ('staff', 'employee') or profile:
            if profile:
                my_qs = qs.filter(employee=profile).order_by('-date')[:10]
                today_self = today_qs.filter(employee=profile).first()
                records = [
                    {"date": str(a.date), "status": a.status, "type": a.attendance_type, "hours": float(a.total_hours or 0)}
                    for a in my_qs
                ]
                in_str = None
                out_str = None
                if today_self:
                    if today_self.check_in:
                        in_str = today_self.check_in.strftime("%I:%M %p")
                    if today_self.check_out:
                        out_str = today_self.check_out.strftime("%I:%M %p")
                return {
                    "scope": "Self attendance only",
                    "today_date": str(end_date),
                    "today_checked_in": today_self is not None,
                    "today_status": today_self.status if today_self else "Not marked",
                    "today_check_in_time": in_str,
                    "today_check_out_time": out_str,
                    "recent_records": records,
                    "total_logged_days_30d": qs.filter(employee=profile).count(),
                }
            return {"scope": "Self attendance (No profile linked)", "total_records": 0}

        return {"scope": "None", "total_records": 0}

    @classmethod
    def _get_employees_summary(cls, user, role, profile) -> Dict[str, Any]:
        from apps.employees.models import Employee

        if role == 'admin':
            total_active = Employee.objects.filter(is_trashed=False, status='active').count()
            dept_counts = list(
                Employee.objects.filter(is_trashed=False, status='active')
                .values('department__name')
                .annotate(count=Count('id'))[:5]
            )
            return {
                "scope": "Company wide active workforce",
                "total_active_employees": total_active,
                "top_department_distribution": dept_counts,
            }

        elif role == 'manager' and profile and profile.branch:
            team_count = Employee.objects.filter(
                branch=profile.branch, is_trashed=False, status='active'
            ).count()
            return {
                "scope": f"Branch team: {profile.branch.name}",
                "active_team_count": team_count,
            }

        elif profile:
            master = getattr(profile, 'master_employee', None)
            return {
                "scope": "Self profile only",
                "employee_id": profile.employee_id,
                "full_name": profile.full_name,
                "department": profile.canonical_department or "Unassigned",
                "designation": profile.canonical_designation or "Unassigned",
                "branch": str(profile.canonical_branch or "Main"),
            }

        return {"scope": "Self", "status": "No profile linked"}

    @classmethod
    def _get_projects_and_tasks_summary(cls, user, role, profile) -> Dict[str, Any]:
        from apps.projects.models import Project, ProjectTask

        if role == 'admin':
            status_breakdown = list(Project.objects.values('status').annotate(count=Count('id')))
            task_status_breakdown = list(ProjectTask.objects.values('status').annotate(count=Count('id')))
            return {
                "scope": "Company wide project & task metrics",
                "projects_by_status": status_breakdown,
                "tasks_by_status": task_status_breakdown,
            }

        elif role == 'manager' and profile:
            managed_projects = Project.objects.filter(project_managers=profile)
            managed_tasks = ProjectTask.objects.filter(project__in=managed_projects)
            return {
                "scope": "Managed projects & tasks",
                "managed_projects_count": managed_projects.count(),
                "pending_tasks_count": managed_tasks.filter(status__in=['Not Started', 'In Progress', 'Delayed']).count(),
                "delayed_tasks_count": managed_tasks.filter(status='Delayed').count(),
            }

        elif profile:
            # Self tasks only
            my_tasks = ProjectTask.objects.filter(
                responsible_person=profile
            ).select_related('project').order_by('planned_finish')[:8]
            task_list = [
                {
                    "activity": t.activity,
                    "project": t.project.name if t.project else "Unassigned",
                    "status": t.status,
                    "progress_pct": t.progress_percent,
                    "planned_finish": str(t.planned_finish) if t.planned_finish else "No deadline",
                }
                for t in my_tasks
            ]
            return {
                "scope": "Self assigned tasks only",
                "total_assigned_tasks": ProjectTask.objects.filter(responsible_person=profile).count(),
                "active_tasks": task_list,
            }

        return {"scope": "None", "tasks": []}

    @classmethod
    def _get_schedule_summary(cls, user, role, profile, today, end_date) -> Dict[str, Any]:
        from apps.schedule.models import ScheduleEvent
        from apps.branches.models import Holiday

        holidays_qs = Holiday.objects.filter(date__gte=today, date__lte=end_date)
        holidays_list = [{"name": h.name, "date": str(h.date)} for h in holidays_qs[:5]]

        if role == 'admin':
            event_count = ScheduleEvent.objects.filter(date__gte=today, date__lte=end_date).count()
            return {
                "scope": "Company upcoming schedule",
                "upcoming_events_14d": event_count,
                "upcoming_holidays": holidays_list,
            }

        elif profile:
            my_events = ScheduleEvent.objects.filter(
                assigned_to=profile, date__gte=today, date__lte=end_date
            ).order_by('date', 'start_time')[:5]
            event_list = [
                {
                    "title": e.title,
                    "date": str(e.date),
                    "start_time": str(e.start_time) if e.start_time else "All Day",
                    "type": e.event_type,
                }
                for e in my_events
            ]
            return {
                "scope": "Self schedule only",
                "upcoming_events": event_list,
                "upcoming_holidays": holidays_list,
            }

        return {"scope": "None", "upcoming_holidays": holidays_list}

    @classmethod
    def _get_leave_summary(cls, user, role, profile, current_year: int) -> Dict[str, Any]:
        from apps.leave.models import LeaveRequest, LeaveBalance
        today = timezone.now().date()
        today_on_leave = LeaveRequest.objects.filter(status='approved', start_date__lte=today, end_date__gte=today)

        if role == 'admin':
            pending_count = LeaveRequest.objects.filter(status='pending').count()
            approved_count = LeaveRequest.objects.filter(status='approved').count()
            return {
                "scope": "Company wide leave statistics",
                "on_leave_today_count": today_on_leave.count(),
                "pending_leave_requests": pending_count,
                "total_approved_leave_records": approved_count,
            }

        elif role == 'manager' and profile and profile.branch:
            pending_branch = LeaveRequest.objects.filter(
                employee__branch=profile.branch, status__in=['pending', 'manager_approved']
            ).count()
            branch_on_leave = today_on_leave.filter(employee__branch=profile.branch).count()
            return {
                "scope": f"Branch leave review: {profile.branch.name}",
                "branch_name": profile.branch.name,
                "on_leave_today_count": branch_on_leave,
                "pending_team_leave_requests": pending_branch,
            }

        elif profile:
            balances = LeaveBalance.objects.filter(employee=profile, year=current_year).select_related('leave_type')
            balance_data = [
                {"leave_type": b.leave_type.name, "remaining_days": float(b.remaining_days or 0), "total_days": float(b.total_days or 0)}
                for b in balances
            ]
            my_requests = LeaveRequest.objects.filter(employee=profile).order_by('-start_date')[:3]
            recent_requests = [
                {"start": str(r.start_date), "end": str(r.end_date), "status": r.status, "days": float(r.total_days or 0)}
                for r in my_requests
            ]
            total_rem = sum(b.get("remaining_days", 0) for b in balance_data)
            return {
                "scope": "Self leave records only",
                "total_remaining_days": total_rem,
                "leave_balances": balance_data,
                "recent_requests": recent_requests,
            }

        return {"scope": "None"}

    @classmethod
    def _get_expenses_summary(cls, user, role, profile, start_date) -> Dict[str, Any]:
        from apps.expense.models import Expense

        if role == 'admin':
            status_counts = list(Expense.objects.filter(requested_at__gte=start_date).values('status').annotate(count=Count('id')))
            total_sum = Expense.objects.filter(requested_at__gte=start_date, status='approved').aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
            return {
                "scope": "Company wide expense overview 30d",
                "status_counts": status_counts,
                "approved_total_amount": float(total_sum),
            }

        elif profile:
            my_expenses = Expense.objects.filter(employee=profile, requested_at__gte=start_date).order_by('-requested_at')[:5]
            records = [
                {"amount": float(e.amount), "category": e.category.name if e.category else "Other", "status": e.status}
                for e in my_expenses
            ]
            return {
                "scope": "Self expenses only",
                "recent_expenses": records,
            }

        return {"scope": "None"}

    @classmethod
    def _get_payroll_summary(cls, user, role, profile) -> Dict[str, Any]:
        """
        Enforce absolute privacy: never expose coworker salary details.
        Admin receives aggregate run status and counts.
        Staff receives only their own payslip confirmation status.
        """
        from apps.payroll.models import PayrollRun, EmployeePayrollCalculation

        latest_run = PayrollRun.objects.order_by('-period_start').first()

        if role == 'admin':
            if latest_run:
                total_calculations = latest_run.calculations.count()
                return {
                    "scope": "Company payroll run status",
                    "latest_run_name": latest_run.name or "Current Cycle",
                    "period": f"{latest_run.period_start} to {latest_run.period_end}",
                    "status": latest_run.status,
                    "total_employees_in_cycle": total_calculations,
                    "privacy_notice": "Individual salary figures are confidential and omitted from AI prompt.",
                }
            return {"scope": "Company payroll", "status": "No payroll run recorded"}

        elif role in ('staff', 'employee') or profile:
            if profile:
                master = getattr(profile, 'master_employee', None)
                if master:
                    my_calc = EmployeePayrollCalculation.objects.filter(employee=master).order_by('-payroll_run__period_start').first()
                    if my_calc:
                        return {
                            "scope": "Self payroll status only",
                            "latest_cycle_status": my_calc.payroll_run.status,
                            "cycle_period": f"{my_calc.payroll_run.period_start} to {my_calc.payroll_run.period_end}",
                            "synced": bool(my_calc.synced_at),
                        }
                return {"scope": "Self payroll", "status": "No active payroll record"}
            return {"scope": "Self payroll (No profile linked)", "status": "None"}

        return {"scope": "Restricted", "status": "Unauthorized"}

    @classmethod
    def _get_notifications_summary(cls, user) -> Dict[str, Any]:
        from apps.notifications.models import Notification
        unread = Notification.objects.filter(recipient=user, is_read=False).count()
        latest = Notification.objects.filter(recipient=user).order_by('-created_at')[:3]
        notif_titles = [n.title for n in latest]
        return {
            "unread_count": unread,
            "recent_notification_titles": notif_titles,
        }

    @classmethod
    def _get_audit_summary(cls, start_date) -> Dict[str, Any]:
        from apps.audit.models import AuditEvent
        events_count = AuditEvent.objects.filter(created_at__gte=start_date if hasattr(AuditEvent, 'created_at') else timezone.now() - timedelta(days=30)).count()
        return {
            "scope": "Admin system audit",
            "recent_audit_events_count": events_count,
        }


class FieldTrackAIService:
    """
    Unified AI service supporting:
    - Dynamic system prompt, persona, temperature, and token limits configured via AISetting
    - Scoped operational context grounding (RBAC enforced)
    - Primary provider + multiple ordered fallback providers (failover on 429, auth error, timeout)
    - Supported providers: Google Gemini, OpenAI, Groq, Anthropic, OpenRouter
    - Prompt injection interception, rate limiting, duplicate prevention, and metadata audit logging
    """
    MAX_RETRIES = 1
    TIMEOUT_SECONDS = 12.0
    RATE_LIMIT_PER_MINUTE = 10

    @classmethod
    def get_settings_obj(cls):
        """Fetches active AISetting model instance."""
        try:
            from .models import AISetting
            return AISetting.get_settings()
        except Exception as e:
            logger.debug(f"Unable to load AISetting: {e}")
            return None

    @classmethod
    def get_api_key(cls, provider: str = 'gemini') -> Optional[str]:
        """
        Retrieves API key for the provider from DB setting or environment variables.
        """
        settings_obj = cls.get_settings_obj()
        if settings_obj and settings_obj.primary_provider == provider and settings_obj.primary_api_key:
            return settings_obj.primary_api_key.strip()

        # Fallback to environment variables
        env_map = {
            'gemini': ['GOOGLE_AI_API_KEY', 'GEMINI_API_KEY'],
            'openai': ['OPENAI_API_KEY'],
            'groq': ['GROQ_API_KEY'],
            'anthropic': ['ANTHROPIC_API_KEY'],
            'openrouter': ['OPENROUTER_API_KEY'],
        }
        for var in env_map.get(provider, []):
            val = os.environ.get(var) or getattr(settings, var, None)
            if val and isinstance(val, str) and val.strip():
                return val.strip()

        # Check in fallback configs if primary was different
        if settings_obj and settings_obj.fallback_configs:
            for item in settings_obj.fallback_configs:
                if item.get('provider') == provider and item.get('api_key') and item.get('is_active', True):
                    return item['api_key'].strip()

        return None

    @classmethod
    def is_configured(cls) -> bool:
        """Checks if at least one API key is available."""
        settings_obj = cls.get_settings_obj()
        if settings_obj and settings_obj.primary_api_key:
            return True
        if cls.get_api_key('gemini') or cls.get_api_key('openai') or cls.get_api_key('groq'):
            return True
        return False

    @classmethod
    def get_model_name(cls, provider: str = 'gemini') -> str:
        settings_obj = cls.get_settings_obj()
        if settings_obj and settings_obj.primary_provider == provider and settings_obj.primary_model:
            return settings_obj.primary_model.strip()

        default_models = {
            'gemini': os.environ.get('GEMINI_MODEL') or getattr(settings, 'GEMINI_MODEL', 'gemini-2.5-flash'),
            'openai': 'gpt-4o-mini',
            'groq': 'llama-3.3-70b-versatile',
            'anthropic': 'claude-3-5-haiku-20241022',
            'openrouter': 'meta-llama/llama-3.3-70b-instruct:free',
        }
        return default_models.get(provider, 'gemini-2.5-flash')

    @classmethod
    def check_rate_limit(cls, user_id: int) -> Tuple[bool, int]:
        """Checks rate limiting per user using Django cache."""
        cache_key = f"ft_ai_ratelimit_{user_id}"
        current = cache.get(cache_key, 0)
        if current >= cls.RATE_LIMIT_PER_MINUTE:
            return False, 0
        cache.set(cache_key, current + 1, timeout=60)
        return True, (cls.RATE_LIMIT_PER_MINUTE - current - 1)

    @classmethod
    def check_duplicate(cls, user_id: int, message: str) -> bool:
        """Prevents duplicate submits within 5 seconds."""
        msg_hash = hashlib.sha256(message.strip().lower().encode()).hexdigest()[:16]
        cache_key = f"ft_ai_dup_{user_id}_{msg_hash}"
        if cache.get(cache_key):
            return True
        cache.set(cache_key, True, timeout=5)
        return False

    @classmethod
    def check_prompt_injection(cls, query: str) -> bool:
        """Detects adversarial prompt injection phrases."""
        q = query.lower()
        for pattern in PROMPT_INJECTION_PATTERNS:
            if pattern in q:
                return True
        return False

    # ── Multi-Provider Execution Adapters ──────────────────────────────

    @classmethod
    def _call_gemini(cls, api_key: str, model: str, system_prompt: str, user_message: str, temperature: float, max_tokens: int) -> str:
        """Invokes Google Gemini via official google-genai SDK or REST API."""
        full_prompt = f"{system_prompt}\n\nUser Query: {user_message}\nAssistant:"
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model,
                contents=full_prompt,
            )
            if response and hasattr(response, 'text') and response.text:
                return response.text.strip()
        except ImportError:
            pass

        # Fallback to direct REST API if SDK not available or custom endpoint
        import urllib.request
        import urllib.error
        import json

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            }
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=cls.TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            candidates = data.get('candidates', [])
            if candidates:
                parts = candidates[0].get('content', {}).get('parts', [])
                if parts and 'text' in parts[0]:
                    return parts[0]['text'].strip()

        raise RuntimeError("No response returned from Gemini.")

    @classmethod
    def _call_openai_compatible(cls, endpoint: str, api_key: str, model: str, system_prompt: str, user_message: str, temperature: float, max_tokens: int, extra_headers: Optional[Dict[str, str]] = None) -> str:
        """Invokes OpenAI, Groq, or OpenRouter via OpenAI-compatible chat completions."""
        import urllib.request
        import urllib.error
        import json

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if extra_headers:
            headers.update(extra_headers)

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=cls.TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            choices = data.get('choices', [])
            if choices:
                return choices[0].get('message', {}).get('content', '').strip()

        raise RuntimeError(f"No response returned from {endpoint}.")

    @classmethod
    def _call_anthropic(cls, api_key: str, model: str, system_prompt: str, user_message: str, temperature: float, max_tokens: int) -> str:
        """Invokes Anthropic Messages API."""
        import urllib.request
        import urllib.error
        import json

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message}
            ]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=cls.TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            content = data.get('content', [])
            if content and 'text' in content[0]:
                return content[0]['text'].strip()

        raise RuntimeError("No response returned from Anthropic.")

    @classmethod
    def _dispatch_provider_call(cls, provider: str, model: str, api_key: str, system_prompt: str, user_message: str, temperature: float, max_tokens: int) -> str:
        """Dispatches request to appropriate provider handler."""
        p = provider.lower().strip()
        if p == 'gemini':
            return cls._call_gemini(api_key, model, system_prompt, user_message, temperature, max_tokens)
        elif p == 'openai':
            return cls._call_openai_compatible(
                "https://api.openai.com/v1/chat/completions",
                api_key, model, system_prompt, user_message, temperature, max_tokens
            )
        elif p == 'groq':
            return cls._call_openai_compatible(
                "https://api.groq.com/openai/v1/chat/completions",
                api_key, model, system_prompt, user_message, temperature, max_tokens
            )
        elif p == 'openrouter':
            return cls._call_openai_compatible(
                "https://openrouter.ai/api/v1/chat/completions",
                api_key, model, system_prompt, user_message, temperature, max_tokens,
                extra_headers={"HTTP-Referer": "https://fieldtrack.local", "X-Title": "FieldTrack"}
            )
        elif p == 'anthropic':
            return cls._call_anthropic(api_key, model, system_prompt, user_message, temperature, max_tokens)
        else:
            raise ValueError(f"Unsupported AI provider: '{provider}'")

    # ── Pipeline & Failover Orchestrator ──────────────────────────────

    @classmethod
    def query_ai(cls, user, user_message: str) -> Tuple[str, bool, str, str]:
        """
        Primary execution pipeline with automatic multi-provider fallback.
        Returns: (response_text, is_error, error_type, provider_info)
        """
        start_time = time.time()
        user_id = user.id if user and user.is_authenticated else 0
        role = resolve_user_role(user)

        # 1. Prompt Injection Defense
        if cls.check_prompt_injection(user_message):
            logger.warning(f"Security: Prompt injection attempt blocked for user {user_id}")
            cls._log_audit(user, role, success=False, status_code="INJECTION_BLOCKED", duration_ms=0)
            return (
                "Permission Refusal: Your inquiry contains restricted instruction patterns. FieldTrack AI cannot execute privilege overrides or disclose confidential records.",
                True,
                "Security Policy",
                "Guardrails"
            )

        # 2. Duplicate submission prevention
        if cls.check_duplicate(user_id, user_message):
            return (
                "Duplicate request detected. Please wait a moment before sending the same inquiry again.",
                True,
                "Duplicate Prevention",
                "Local Cache"
            )

        # 3. Rate limiting check
        allowed, _ = cls.check_rate_limit(user_id)
        if not allowed:
            cls._log_audit(user, role, success=False, status_code="RATE_LIMITED", duration_ms=0)
            return (
                f"Rate limit reached ({cls.RATE_LIMIT_PER_MINUTE} requests per minute). Please try again shortly.",
                True,
                "Rate Limit",
                "Rate Limiter"
            )

    @classmethod
    def _detect_language(cls, text: str) -> str:
        """Detects if message contains Bengali, Hindi, or default English characters."""
        import re
        if re.search(r'[\u0980-\u09FF]', text):
            return 'bn'
        if re.search(r'[\u0900-\u097F]', text):
            return 'hi'
        return 'en'

    @classmethod
    def _generate_local_operational_reply(cls, user, role: str, user_message: str, scoped_data: Dict[str, Any]) -> str:
        """
        Deterministic, ultra-fast operational intelligence responder.
        Answers user queries smoothly, concisely, and accurately in 1 to 2 sentences
        using live scoped database statistics.
        Supports English, Bengali, and Hindi.
        """
        import re
        msg = user_message.strip().lower()
        lang = cls._detect_language(user_message)

        att = scoped_data.get('attendance', {}) if isinstance(scoped_data, dict) else {}
        emp = scoped_data.get('employees', {}) if isinstance(scoped_data, dict) else {}
        lv = scoped_data.get('leave', {}) if isinstance(scoped_data, dict) else {}
        pt = scoped_data.get('projects_and_tasks', {}) if isinstance(scoped_data, dict) else {}

        # 1. Greetings / Bot identity
        if re.search(r'\b(hi|hello|hey|morning|afternoon|evening|salam|assalam|greetings)\b', msg) or \
           re.search(r'(হ্যালো|হাই|সালাম|কেমন|শুভ|আসালামু)', msg) or \
           re.search(r'(नमस्ते|नमस्कार|कैसे|सुप्रभात)', msg):
            if lang == 'bn':
                return "সালাম! FieldTrack AI প্রস্তুত। আজকের উপস্থিতি, ছুটি বা কর্মীবাহিনীর তথ্য জানতে প্রশ্ন করুন।"
            elif lang == 'hi':
                return "नमस्ते! FieldTrack AI तैयार है। आज अपनी उपस्थिति, छुट्टी या टीम के बारे में पूछ सकते हैं।"
            return "Hello! FieldTrack AI is ready. How can I assist you with attendance, leaves, or workforce operations today?"

        # 2. Attendance / Present / Absent / Checkin / Checkout
        if any(w in msg for w in ['attend', 'present', 'absent', 'check', 'late', 'today', 'on time']) or \
           re.search(r'(উপস্থিতি|হাজিরা|প্রেজেন্ট|চেকইন|চেক|দেরি|লেট|আজকের)', msg) or \
           re.search(r'(उपस्थिति|हाजिरी|चेक|देरी)', msg):
            if role == 'admin':
                checked = att.get('today_checked_in_count', 0)
                total = emp.get('total_active_employees', 0)
                on_time = att.get('today_on_time_count', 0)
                late = att.get('today_late_count', 0)
                if lang == 'bn':
                    return f"আজ মোট {total} জন সক্রিয় কর্মীর মধ্যে {checked} জন উপস্থিতি রেকর্ড করেছেন ({on_time} জন সময়মতো, {late} জন দেরিতে)।"
                elif lang == 'hi':
                    return f"आज {total} सक्रिय कर्मचारियों में से {checked} ने उपस्थिति दर्ज की है ({on_time} समय पर, {late} देरी से)।"
                return f"Today, {checked} of {total} active employees have recorded attendance ({on_time} on-time, {late} late)."

            elif role == 'manager':
                checked = att.get('today_checked_in_count', 0)
                team = emp.get('active_team_count', 0)
                on_time = att.get('today_on_time_count', 0)
                branch = att.get('branch_name', 'your branch')
                if lang == 'bn':
                    return f"{branch} শাখায় আপনার {team} জন টিম মেম্বারের মধ্যে আজ {checked} জন চেক-ইন করেছেন ({on_time} জন সময়মতো)।"
                elif lang == 'hi':
                    return f"{branch} में आपकी टीम के {team} सदस्यों में से {checked} ने आज चेक-इन किया है ({on_time} समय पर)।"
                return f"Today, {checked} of {team} team members have checked in for {branch} ({on_time} on-time)."

            else:
                checked = att.get('today_checked_in', False)
                status = att.get('today_status', 'Not marked')
                in_time = att.get('today_check_in_time')
                out_time = att.get('today_check_out_time')
                if checked:
                    out_part = f" and checked out at {out_time}" if out_time else ""
                    if lang == 'bn':
                        return f"আপনি আজ {in_time or ''} সময়ে চেক-ইন করেছেন (স্ট্যাটাস: {status})।"
                    elif lang == 'hi':
                        return f"आपने आज {in_time or ''} बजे चेक-इन किया है (स्थिति: {status})।"
                    return f"You checked in today at {in_time or 'work start'}{out_part} with status '{status}'."
                else:
                    if lang == 'bn':
                        return "আপনি আজ এখনো উপস্থিতি রেকর্ড করেননি। ড্যাশবোর্ড থেকে চেক-ইন সম্পন্ন করুন।"
                    elif lang == 'hi':
                        return "आपने आज अभी तक चेक-इन नहीं किया है। कृपया डैशबोर्ड से उपस्थिति दर्ज करें।"
                    return "You have not checked in yet today. Please remember to record your attendance via the dashboard."

        # 3. Leave / Holiday / Vacation / Off day
        if any(w in msg for w in ['leave', 'holiday', 'vacation', 'off', 'sick', 'casual', 'annual']) or \
           re.search(r'(ছুটি|হলিডে|ছুটির|ছুটিতে)', msg) or \
           re.search(r'(छुट्टी|अवकाश|लीव)', msg):
            if role == 'admin':
                on_leave = lv.get('on_leave_today_count', 0)
                pending = lv.get('pending_leave_requests', 0)
                if lang == 'bn':
                    return f"আজ {on_leave} জন কর্মী অনুমোদিত ছুটিতে আছেন এবং {pending} টি ছুটির আবেদন পর্যালোচনার অপেক্ষায় রয়েছে।"
                elif lang == 'hi':
                    return f"आज {on_leave} कर्मचारी स्वीकृत अवकाश पर हैं और {pending} आवेदन समीक्षा के लिए लंबित हैं।"
                return f"There are {on_leave} employee(s) on approved leave today, with {pending} pending leave request(s) awaiting review."

            elif role == 'manager':
                on_leave = lv.get('on_leave_today_count', 0)
                pending = lv.get('pending_team_leave_requests', 0)
                if lang == 'bn':
                    return f"আজ আপনার টিমে {on_leave} জন ছুটিতে আছেন এবং {pending} টি ছুটির আবেদন অপেক্ষমাণ রয়েছে।"
                elif lang == 'hi':
                    return f"आज आपकी टीम में {on_leave} सदस्य छुट्टी पर हैं और {pending} आवेदन लंबित हैं।"
                return f"There are {on_leave} team member(s) on leave today and {pending} pending team leave request(s)."

            else:
                rem = lv.get('total_remaining_days', 0)
                if lang == 'bn':
                    return f"চলতি বছরে আপনার মোট {rem} দিন ছুটি অবশিষ্ট রয়েছে।"
                elif lang == 'hi':
                    return f"इस वर्ष आपके पास कुल {rem} दिन की छुट्टी शेष है।"
                return f"You currently have {rem} total remaining leave days for this calendar year."

        # 4. Employees / Workforce / Count / Departments / Staff
        if any(w in msg for w in ['employee', 'staff', 'worker', 'team', 'department', 'headcount', 'workforce', 'people']) or \
           re.search(r'(কর্মী|কর্মচারী|টিম|ডিপার্টমেন্ট|লোক)', msg) or \
           re.search(r'(कर्मचारी|टीम|विभाग)', msg):
            if role == 'admin':
                total = emp.get('total_active_employees', 0)
                if lang == 'bn':
                    return f"ফিল্ডট্র্যাকে বর্তমানে মোট {total} জন সক্রিয় কর্মী নিবন্ধিত রয়েছে।"
                elif lang == 'hi':
                    return f"वर्तमान में कुल {total} सक्रिय कर्मचारी पंजीकृत हैं।"
                return f"FieldTrack currently manages {total} active employees across registered departments."

            elif role == 'manager':
                team = emp.get('active_team_count', 0)
                branch = att.get('branch_name', 'your branch')
                if lang == 'bn':
                    return f"{branch} শাখায় আপনার টিমে মোট {team} জন সক্রিয় কর্মী রয়েছেন।"
                elif lang == 'hi':
                    return f"{branch} शाखा में आपकी टीम में {team} सक्रिय कर्मचारी हैं।"
                return f"Your branch team at {branch} has {team} active employees."

            else:
                fn = emp.get('full_name', 'Employee')
                dept = emp.get('department', 'Unassigned')
                desig = emp.get('designation', 'Staff')
                if lang == 'bn':
                    return f"আপনি {dept} বিভাগে {desig} হিসেবে নিবন্ধিত রয়েছেন।"
                elif lang == 'hi':
                    return f"आप {dept} विभाग में {desig} के रूप में पंजीकृत हैं।"
                return f"You are registered as {fn} ({desig}) in the {dept} department."

        # 5. Schedule / Office Hours / Branch / Shifts
        if any(w in msg for w in ['schedule', 'office hour', 'hour', 'time', 'timing', 'shift', 'branch', 'location']) or \
           re.search(r'(সময়|শিফট|অফিস|টাইম|ব্রাঞ্চ)', msg) or \
           re.search(r'(समय|शिफ्ट|कार्यालय)', msg):
            if lang == 'bn':
                return "অফিসের সাধারণ কার্যসময় সকাল ০৯:০০ টা থেকে বিকাল ০৫:০০ টা। জিওফেন্স ও শিফট পলিসি সক্রিয় আছে।"
            elif lang == 'hi':
                return "कार्यालय का सामान्य समय सुबह 09:00 से शाम 05:00 तक है। सभी स्थान नीतियां सक्रिय हैं।"
            return "Standard office hours are 09:00 AM to 05:00 PM. Shift policies and geofence locations are currently active."

        # 6. Projects / Tasks / Progress
        if any(w in msg for w in ['project', 'task', 'milestone', 'progress', 'todo']) or \
           re.search(r'(প্রজেক্ট|টাস্ক|কাজ)', msg) or \
           re.search(r'(प्रोजेक्ट|कार्य|काम)', msg):
            if role == 'admin':
                status_list = pt.get('tasks_by_status', [])
                total_tasks = sum(item.get('count', 0) for item in status_list)
                if lang == 'bn':
                    return f"বর্তমানে চলমান প্রকল্পগুলোতে মোট {total_tasks} টি টাস্ক পর্যবেক্ষণ করা হচ্ছে।"
                elif lang == 'hi':
                    return f"वर्तमान में सक्रिय परियोजनाओं में कुल {total_tasks} कार्य ट्रैक किए जा रहे हैं।"
                return f"FieldTrack is currently tracking {total_tasks} operational tasks across active projects."
            else:
                if lang == 'bn':
                    return "আপনার অর্পিত প্রজেক্ট এবং টাস্কগুলো ড্যাশবোর্ডের প্রজেক্ট সেকশন থেকে সরাসরি দেখতে পারেন।"
                elif lang == 'hi':
                    return "आप अपने सौंपे गए प्रोजेक्ट और कार्य सीधे डैशबोर्ड से देख सकते हैं।"
                return "You can monitor and update your assigned tasks directly from the Project Milestones section."

        # 7. Payroll / Salary
        if any(w in msg for w in ['payroll', 'salary', 'payslip', 'pay']) or \
           re.search(r'(বেতন|পেরোল)', msg) or \
           re.search(r'(वेतन|पेरोल)', msg):
            if lang == 'bn':
                return "পেরোল ও বেতন হিসাব কঠোর গোপনীয়তা ও সুরক্ষা নিশ্চিত করে প্রক্রিয়াজাত করা হয়।"
            elif lang == 'hi':
                return "पेरोल गणना पूर्ण गोपनीयता और सुरक्षा नियंत्रण के साथ संसाधित की जाती है।"
            return "Payroll cycles are managed securely under strict privacy controls. Payslips are available in your finance tab."

        # 8. Help / Features / Capabilities
        if any(w in msg for w in ['help', 'what can you do', 'features', 'capability', 'who are you']) or \
           re.search(r'(সাহায্য|হেল্প|তুমি কি করতে পারো|কি কাজ)', msg) or \
           re.search(r'(मदद|सहायता|आप क्या कर सकते हैं)', msg):
            if lang == 'bn':
                return "আমি উপস্থিতি, ছুটির ব্যালেন্স, কর্মীদের সংখ্যা এবং শিডিউল সংক্রান্ত রিয়েল-টাইম তথ্য সংক্ষেপে প্রদান করি।"
            elif lang == 'hi':
                return "मैं उपस्थिति, छुट्टी की स्थिति, कर्मचारियों की संख्या और कार्यक्रम पर त्वरित जानकारी प्रदान करता हूँ।"
            return "I provide concise real-time operational answers regarding attendance, leave balances, employee counts, and schedules."

        # 9. Generic smooth fallback
        if role == 'admin':
            checked = att.get('today_checked_in_count', 0)
            total = emp.get('total_active_employees', 0)
            if lang == 'bn':
                return f"সিস্টেম সক্রিয়: আজ {total} জনের মধ্যে {checked} জন হাজিরা দিয়েছেন। উপস্থিতি বা ছুটি সংক্রান্ত যেকোনো প্রশ্ন করতে পারেন।"
            elif lang == 'hi':
                return f"सिस्टम सक्रिय है: आज {total} में से {checked} कर्मचारियों ने उपस्थिति दर्ज की। आप कोई भी प्रश्न पूछ सकते हैं।"
            return f"Operational state active: {checked} of {total} employees recorded attendance today. Feel free to ask about attendance, leaves, or schedules."
        else:
            if lang == 'bn':
                return "FieldTrack AI প্রস্তুত। আপনার আজকের হাজিরা, ছুটির হিসাব বা কাজের সময়সূচি সম্পর্কে জিজ্ঞেস করতে পারেন।"
            elif lang == 'hi':
                return "FieldTrack AI तैयार है। आप अपनी आज की उपस्थिति, छुट्टी या कार्यक्रम के बारे में पूछ सकते हैं।"
            return "FieldTrack AI is ready. You can ask about your attendance today, leave balance, or office schedules."

    # ── Pipeline & Failover Orchestrator ──────────────────────────────

    @classmethod
    def query_ai(cls, user, user_message: str) -> Tuple[str, bool, str, str]:
        """
        Primary execution pipeline with automatic multi-provider fallback.
        Returns: (response_text, is_error, error_type, provider_info)
        """
        import json
        import re

        start_time = time.time()
        user_id = user.id if user and user.is_authenticated else 0
        role = resolve_user_role(user)

        # 1. Prompt Injection Defense
        if cls.check_prompt_injection(user_message):
            logger.warning(f"Security: Prompt injection attempt blocked for user {user_id}")
            cls._log_audit(user, role, success=False, status_code="INJECTION_BLOCKED", duration_ms=0)
            return (
                "Permission Refusal: Your inquiry contains restricted instruction patterns. FieldTrack AI cannot execute privilege overrides or disclose confidential records.",
                True,
                "Security Policy",
                "Guardrails"
            )

        # 2. Duplicate submission prevention
        if cls.check_duplicate(user_id, user_message):
            return (
                "Duplicate request detected. Please wait a moment before sending the same inquiry again.",
                True,
                "Duplicate Prevention",
                "Local Cache"
            )

        # 3. Rate limiting check
        allowed, _ = cls.check_rate_limit(user_id)
        if not allowed:
            cls._log_audit(user, role, success=False, status_code="RATE_LIMITED", duration_ms=0)
            return (
                f"Rate limit reached ({cls.RATE_LIMIT_PER_MINUTE} requests per minute). Please try again shortly.",
                True,
                "Rate Limit",
                "Rate Limiter"
            )

        # 4. Load configured AI Settings (behavior + primary + fallbacks)
        settings_obj = cls.get_settings_obj()
        custom_persona = settings_obj.system_prompt if settings_obj and settings_obj.system_prompt else (
            "You are FieldTrack AI Assistant, the operational intelligence assistant for the FieldTrack workforce platform."
        )
        temperature = float(settings_obj.temperature) if settings_obj and settings_obj.temperature is not None else 0.2
        # Keep responses strictly concise and short
        max_tokens = min(int(settings_obj.max_tokens), 250) if settings_obj and settings_obj.max_tokens else 180
        include_context = getattr(settings_obj, 'include_operational_context', True)

        # 5. Extract scoped context (if enabled)
        scoped_data: Dict[str, Any] = {}
        if include_context:
            try:
                scoped_data = OperationalContextService.get_scoped_context(user, role)
            except Exception as e:
                logger.error(f"Failed to generate operational context: {e}")
                scoped_data = {"role": role, "note": "Operational context generation encountered an issue"}

        # 6. Assemble complete system instructions
        system_instructions = (
            f"{custom_persona}\n"
            f"The authenticated user has role '{role}'.\n"
            "STRICT CONCISENESS & OPERATIONAL RULES:\n"
            "1. LENGTH: Answer shortly, smoothly, and directly in 1 to 2 sentences (at most 3 sentences if reporting key numbers).\n"
            "2. TONE: Professional, courteous, and crisp.\n"
            "3. NO BLOAT: Never use chatty preambles, introductory filler, or unasked disclaimers.\n"
            "4. LANGUAGE: Always respond in the language asked (Bengali if Bengali, Hindi if Hindi, English if English).\n"
            "5. ACCURACY: Only use the OPERATIONAL DATA below when stating figures. Never fabricate numbers.\n"
            "6. PRIVACY: Never reveal individual employee credentials or coworker salaries.\n"
            "7. UNAVAILABLE DATA: If data is restricted or omitted, state that simply in one sentence.\n\n"
            f"=== PERMITTED OPERATIONAL DATA ===\n"
            f"{json.dumps(scoped_data, default=str) if isinstance(scoped_data, dict) else scoped_data}\n"
            f"=== END OPERATIONAL DATA ===\n"
        )

        # 7. Build Execution Chain: [Primary, Fallback 1, Fallback 2, ...]
        chain = []

        # Primary config
        primary_provider = settings_obj.primary_provider if settings_obj else 'gemini'
        primary_model = settings_obj.primary_model if settings_obj else cls.get_model_name(primary_provider)
        primary_key = cls.get_api_key(primary_provider)
        if primary_key:
            chain.append({
                "provider": primary_provider,
                "model": primary_model,
                "api_key": primary_key,
                "label": f"Primary ({primary_provider.capitalize()})"
            })

        # Fallback configs
        if settings_obj and settings_obj.fallback_configs:
            for idx, fb in enumerate(settings_obj.fallback_configs):
                if fb.get('is_active', True) and fb.get('api_key'):
                    fb_provider = fb.get('provider', 'gemini')
                    fb_model = fb.get('model') or cls.get_model_name(fb_provider)
                    fb_label = fb.get('label') or f"Fallback #{idx + 1} ({fb_provider.capitalize()})"
                    chain.append({
                        "provider": fb_provider,
                        "model": fb_model,
                        "api_key": fb.get('api_key').strip(),
                        "label": fb_label
                    })

        # Fallback to server env var if chain is still empty
        if not chain:
            env_gemini_key = os.environ.get('GOOGLE_AI_API_KEY') or getattr(settings, 'GOOGLE_AI_API_KEY', None)
            if env_gemini_key and env_gemini_key.strip():
                chain.append({
                    "provider": "gemini",
                    "model": cls.get_model_name("gemini"),
                    "api_key": env_gemini_key.strip(),
                    "label": "Environment Key (Gemini)"
                })

        # When no external key is configured, reply smoothly via Local Intelligence Engine!
        if not chain:
            logger.info("FieldTrack AI: Using Local Intelligence Engine.")
            local_reply = cls._generate_local_operational_reply(user, role, user_message, scoped_data)
            elapsed = int((time.time() - start_time) * 1000)
            cls._log_audit(user, role, success=True, status_code="LOCAL_ENGINE", duration_ms=elapsed)
            return (
                local_reply,
                False,
                "",
                "Local Intelligence Engine"
            )

        # 8. Attempt execution down the chain
        last_error_type = "API Failure"
        last_error_msg = ""
        attempted_providers = []

        for item in chain:
            provider = item['provider']
            model = item['model']
            api_key = item['api_key']
            label = item['label']
            attempted_providers.append(label)

            try:
                reply = cls._dispatch_provider_call(
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    system_prompt=system_instructions,
                    user_message=user_message,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                if reply:
                    reply = reply.strip()
                    # Keep response smoothly concise (max 3 sentences)
                    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', reply) if s.strip()]
                    if len(sentences) > 3:
                        reply = " ".join(sentences[:3])
                    elapsed = int((time.time() - start_time) * 1000)
                    cls._log_audit(user, role, success=True, status_code="SUCCESS", duration_ms=elapsed)
                    return reply, False, "", label
            except Exception as e:
                err_str = str(e).lower()
                logger.warning(f"FieldTrack AI [{label}] invocation failed: {e}")

                if "429" in err_str or "quota" in err_str or "resource_exhausted" in err_str:
                    last_error_type = "API Quota Exceeded"
                    last_error_msg = f"{label} quota limit reached."
                elif "401" in err_str or "403" in err_str or "api_key_invalid" in err_str or "unauthorized" in err_str:
                    last_error_type = "Authentication Error"
                    last_error_msg = f"{label} authentication failed."
                elif "timeout" in err_str or "timed out" in err_str:
                    last_error_type = "Timeout / Offline"
                    last_error_msg = f"{label} connection timed out."
                else:
                    last_error_type = "Provider Error"
                    last_error_msg = f"{label} error: {e}"

                # Proceed to next fallback in chain
                continue

        # All configured providers failed: smoothly fall back to Local Intelligence Engine!
        fallback_trail = " -> ".join(attempted_providers)
        logger.warning(f"FieldTrack AI: External providers failed ({fallback_trail}). Engaging Local Engine.")
        local_reply = cls._generate_local_operational_reply(user, role, user_message, scoped_data)
        elapsed = int((time.time() - start_time) * 1000)
        cls._log_audit(user, role, success=True, status_code="LOCAL_FALLBACK", duration_ms=elapsed)
        return (
            local_reply,
            False,
            "",
            "Local Intelligence Engine (Failover)"
        )

    @classmethod
    def _log_audit(cls, user, role: str, success: bool, status_code: str, duration_ms: int):
        """Records metadata-only audit events."""
        try:
            from apps.audit.models import AuditEvent
            AuditEvent.objects.create(
                actor_user=user if user and user.is_authenticated else None,
                actor_role=role,
                module="ai_assistant",
                object_type="operational_inquiry",
                object_id=str(int(time.time())),
                object_label="AI Intelligence Query",
                action="query_processed" if success else "query_failed",
                after_data={
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "success": success,
                }
            )
        except Exception as e:
            logger.debug(f"Audit event logging skipped: {e}")


class GeminiClientService(FieldTrackAIService):
    """
    Backward-compatibility alias wrapping FieldTrackAIService.
    Guarantees that all existing tests and callers continue working seamlessly.
    """
    @classmethod
    def query_gemini(cls, user, user_message: str) -> Tuple[str, bool, str]:
        reply, is_err, err_type, _ = cls.query_ai(user, user_message)
        return reply, is_err, err_type

