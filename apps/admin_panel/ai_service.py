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

    # Fallback to direct role attribute
    role = getattr(user, 'role', None)
    if role in ('admin', 'system_owner'):
        return 'admin'
    if role == 'manager':
        return 'manager'
    if role in ('staff', 'employee'):
        return role

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

        if role == 'admin':
            total = qs.count()
            on_time = qs.filter(status='on_time').count()
            late = qs.filter(status='late').count()
            absent = qs.filter(status='absent').count()
            field_visits = qs.filter(attendance_type='field_visit').count()
            rate = round((on_time / total * 100), 1) if total > 0 else 100.0
            return {
                "scope": "Company wide aggregates",
                "total_records_30d": total,
                "on_time_rate_pct": rate,
                "late_count": late,
                "absent_count": absent,
                "field_visits_count": field_visits,
            }

        elif role == 'manager' and profile and profile.branch:
            branch_qs = qs.filter(employee__branch=profile.branch)
            total = branch_qs.count()
            on_time = branch_qs.filter(status='on_time').count()
            late = branch_qs.filter(status='late').count()
            return {
                "scope": f"Branch: {profile.branch.name}",
                "total_records_30d": total,
                "on_time_count": on_time,
                "late_count": late,
            }

        elif role in ('staff', 'employee') or profile:
            if profile:
                my_qs = qs.filter(employee=profile).order_by('-date')[:10]
                records = [
                    {"date": str(a.date), "status": a.status, "type": a.attendance_type, "hours": float(a.total_hours or 0)}
                    for a in my_qs
                ]
                return {
                    "scope": "Self attendance only",
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

        if role == 'admin':
            pending_count = LeaveRequest.objects.filter(status='pending').count()
            approved_count = LeaveRequest.objects.filter(status='approved').count()
            return {
                "scope": "Company wide leave statistics",
                "pending_leave_requests": pending_count,
                "total_approved_leave_records": approved_count,
            }

        elif role == 'manager' and profile and profile.branch:
            pending_branch = LeaveRequest.objects.filter(
                employee__branch=profile.branch, status__in=['pending', 'manager_approved']
            ).count()
            return {
                "scope": f"Branch leave review: {profile.branch.name}",
                "pending_team_leave_requests": pending_branch,
            }

        elif profile:
            balances = LeaveBalance.objects.filter(employee=profile, year=current_year).select_related('leave_type')
            balance_data = [
                {"leave_type": b.leave_type.name, "remaining_days": b.remaining_days, "total_days": b.total_days}
                for b in balances
            ]
            my_requests = LeaveRequest.objects.filter(employee=profile).order_by('-start_date')[:3]
            recent_requests = [
                {"start": str(r.start_date), "end": str(r.end_date), "status": r.status, "days": float(r.total_days or 0)}
                for r in my_requests
            ]
            return {
                "scope": "Self leave records only",
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

        # 4. Load configured AI Settings (behavior + primary + fallbacks)
        settings_obj = cls.get_settings_obj()
        custom_persona = settings_obj.system_prompt if settings_obj and settings_obj.system_prompt else (
            "You are FieldTrack AI Assistant, the operational intelligence assistant for the FieldTrack workforce platform."
        )
        temperature = float(settings_obj.temperature) if settings_obj and settings_obj.temperature is not None else 0.3
        max_tokens = int(settings_obj.max_tokens) if settings_obj and settings_obj.max_tokens else 800
        include_context = getattr(settings_obj, 'include_operational_context', True)

        # 5. Extract scoped context (if enabled)
        scoped_data = ""
        if include_context:
            try:
                scoped_data = OperationalContextService.get_scoped_context(user, role)
            except Exception as e:
                logger.error(f"Failed to generate operational context: {e}")
                scoped_data = {"role": role, "note": "Operational context generation encountered an issue"}

        # 6. Assemble complete system instructions
        system_instructions = (
            f"{custom_persona}\n"
            f"The authenticated user has the role '{role}'.\n"
            "STRICT PRIVACY RULES:\n"
            "1. ONLY answer using the PERMITTED OPERATIONAL DATA supplied below when relevant.\n"
            "2. DO NOT fabricate or assume numbers or metrics not present in the data.\n"
            "3. NEVER reveal confidential salary values, credentials, or personal passwords.\n"
            "4. For staff/employees, do not expose coworker personal details or company-wide payroll.\n"
            "5. If requested data is restricted or omitted, state that clearly and truthfully.\n"
            "6. Answer concisely and professionally in 2 to 4 sentences.\n\n"
            f"=== PERMITTED OPERATIONAL DATA ===\n"
            f"{scoped_data}\n"
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

        if not chain:
            logger.info("FieldTrack AI: No API keys configured in AI Settings or environment.")
            cls._log_audit(user, role, success=False, status_code="KEY_MISSING", duration_ms=0)
            return (
                "FieldTrack AI Assistant is currently offline. A server runtime secret (GOOGLE_AI_API_KEY) must be configured to enable live operational intelligence. No simulated statistics are returned.",
                True,
                "Service Offline",
                "None"
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

        # All configured providers failed
        elapsed = int((time.time() - start_time) * 1000)
        cls._log_audit(user, role, success=False, status_code="ALL_PROVIDERS_FAILED", duration_ms=elapsed)

        fallback_trail = " -> ".join(attempted_providers)
        if last_error_type == "API Quota Exceeded" and len(chain) == 1:
            return (
                "Google AI API quota limit reached. Please retry in a few moments.",
                True,
                "API Quota Exceeded",
                fallback_trail
            )

        return (
            f"All configured AI providers failed ({fallback_trail}). Last error: {last_error_msg or 'Request failed'}. Please check your API keys or quota.",
            True,
            last_error_type,
            fallback_trail
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

