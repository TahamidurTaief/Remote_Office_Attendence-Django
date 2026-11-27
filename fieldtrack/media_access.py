import mimetypes
import os
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote

from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.views.static import serve

from apps.accounts.engine import PermissionEngine
from apps.accounts.models import DataScope


# Explicit media inventory:
# - Public: intentionally public profile avatars.
# - Private: employee documents, attendance photos/corrections, expense receipts,
#   project task attachments, and any MediaAsset with is_private=True.
PUBLIC_MEDIA_PREFIXES = (
    'employees/photos/',
)

PRIVATE_MEDIA_PREFIXES = (
    'employees/documents/',
    'attendance/photos/',
    'attendance_corrections/',
    'expenses/',
    'projects/task_attachments/',
)

_UNSAFE_FILENAME_RE = re.compile(r'[\r\n\x00-\x1f\x7f"\\/:*?<>|]+')


def validate_media_relpath(raw_path):
    """
    Normalize and validate a relative media path against traversal,
    encoded traversal, backslashes, null bytes, and absolute paths.
    Returns a clean POSIX relative path or None if unsafe.
    """
    if not raw_path or not isinstance(raw_path, str):
        return None

    candidates = [raw_path]
    decoded = raw_path
    for _ in range(2):
        next_decoded = unquote(decoded)
        candidates.append(next_decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded

    for candidate in candidates:
        if '\x00' in candidate or '\\' in candidate or ':' in candidate:
            return None
        if candidate.startswith('/') or os.path.isabs(candidate):
            return None
        parts = candidate.split('/')
        if any(part in ('', '.', '..') for part in parts):
            return None

    norm_path = posixpath.normpath(decoded)
    if norm_path in ('', '.', '..') or norm_path.startswith('../') or norm_path.startswith('/'):
        return None

    media_root = Path(settings.MEDIA_ROOT).resolve()
    try:
        resolved = (media_root / norm_path).resolve()
    except (OSError, ValueError):
        return None

    if not resolved.is_relative_to(media_root) or resolved == media_root:
        return None

    return norm_path


def sanitize_download_filename(name, file_path='', fallback='download'):
    """
    Build a safe Content-Disposition filename without path separators,
    control characters, or header-breaking quotes, preserving extension.
    """
    base = os.path.basename(str(name or '')).strip()
    base = _UNSAFE_FILENAME_RE.sub('_', base).strip(' ._')
    if not base:
        base = fallback

    ext = os.path.splitext(os.path.basename(str(file_path or '')))[1]
    if ext and not base.lower().endswith(ext.lower()):
        safe_ext = _UNSAFE_FILENAME_RE.sub('', ext)
        if safe_ext.startswith('.'):
            base = f"{base}{safe_ext}"
    return base


def stream_private_file(file_obj, filename=None, as_attachment=False, content_type=None):
    """
    Stream a private file via FileResponse with non-public cache headers.
    """
    if content_type is None and filename:
        guessed, _ = mimetypes.guess_type(filename)
        content_type = guessed or 'application/octet-stream'

    response = FileResponse(
        file_obj,
        as_attachment=as_attachment,
        filename=filename,
        content_type=content_type or 'application/octet-stream',
    )
    response['Cache-Control'] = 'private, no-store, no-cache, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _resolve_branch_id(emp_master=None, emp_profile=None):
    if emp_profile and getattr(emp_profile, 'canonical_branch', None):
        return emp_profile.canonical_branch.id
    if emp_master and getattr(emp_master, 'branch_id', None):
        return emp_master.branch_id
    if emp_profile and getattr(emp_profile, 'branch_id', None):
        return emp_profile.branch_id
    return None


def _resolve_target_dept(emp_master=None, emp_profile=None):
    dept_id = None
    dept_name = None
    if emp_master:
        dept_id = getattr(emp_master, 'department_id', None)
        dept_obj = getattr(emp_master, 'department', None)
        if dept_obj and getattr(dept_obj, 'name', None):
            dept_name = dept_obj.name.strip().lower()
    if not dept_name and emp_profile:
        prof_dept = getattr(emp_profile, 'canonical_department', None) or getattr(emp_profile, 'department', None)
        if isinstance(prof_dept, str) and prof_dept.strip():
            dept_name = prof_dept.strip().lower()
    return dept_id, dept_name


def user_can_access_employee_target(user, permission_code, employee_master=None, employee_profile=None, action_type='view'):
    """
    Enforce ownership, permission codename, and canonical employee data scope.
    Fails closed when target or user canonical scope identity is missing.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    if employee_master is None and employee_profile is not None:
        employee_master = getattr(employee_profile, 'master_employee', None)

    is_owner = (
        (employee_master is not None and getattr(employee_master, 'user_id', None) == user.id)
        or (employee_profile is not None and getattr(employee_profile, 'user_id', None) == user.id)
    )
    if is_owner:
        return True

    eval_res = PermissionEngine.evaluate(user, permission_code, action_type=action_type)
    if not eval_res.allowed:
        return False

    scope = eval_res.data_scope
    if scope in (DataScope.GLOBAL, DataScope.COMPANY):
        return True

    if scope == DataScope.OWN:
        return False

    user_master = getattr(user, 'employee_master', None)
    user_profile = getattr(user, 'employee_profile', None)

    if scope == DataScope.BRANCH:
        user_branch_id = _resolve_branch_id(user_master, user_profile)
        target_branch_id = _resolve_branch_id(employee_master, employee_profile)
        return user_branch_id is not None and user_branch_id == target_branch_id

    if scope == DataScope.DEPARTMENT:
        from apps.attendance.scoping import _extract_user_department_identity
        user_dept_ids, user_dept_names = _extract_user_department_identity(user)
        if not user_dept_ids and not user_dept_names:
            return False
        target_dept_id, target_dept_name = _resolve_target_dept(employee_master, employee_profile)
        if target_dept_id and target_dept_id in user_dept_ids:
            return True
        if target_dept_name and target_dept_name in {n.lower() for n in user_dept_names}:
            return True
        return False

    if scope == DataScope.TEAM:
        actor_master = user_master or getattr(user_profile, 'master_employee', None)
        if not actor_master or not employee_master:
            return False
        if employee_master.id == actor_master.id:
            return True
        from apps.employees.hierarchy_services import OrgHierarchyService
        return OrgHierarchyService.get_all_subordinates(actor_master).filter(pk=employee_master.id).exists()

    return False


def user_can_download_document(user, doc):
    """
    Object-level permission + canonical scope check for EmployeeDocument.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    is_owner = (
        (doc.employee_master_id and getattr(doc.employee_master, 'user_id', None) == user.id)
        or (doc.employee_id and getattr(doc.employee, 'user_id', None) == user.id)
    )
    if is_owner:
        return True

    if not user_can_access_employee_target(
        user,
        'employees.view',
        employee_master=doc.employee_master,
        employee_profile=doc.employee,
        action_type='view',
    ):
        return False

    if doc.is_sensitive():
        return user_can_access_employee_target(
            user,
            'employees.download_sensitive_document',
            employee_master=doc.employee_master,
            employee_profile=doc.employee,
            action_type='view',
        )

    return True


def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        first = xff.split(',')[0].strip()
        if first:
            return first
    xri = request.META.get('HTTP_X_REAL_IP')
    if xri and xri.strip():
        return xri.strip()
    return request.META.get('REMOTE_ADDR') or ''


def _serve_employee_document(request, clean_path):
    from apps.employees.models import DocumentDownloadLog, EmployeeDocument
    from apps.notifications.models import log_audit

    doc = (
        EmployeeDocument.objects
        .select_related(
            'employee_master',
            'employee_master__branch',
            'employee_master__department',
            'employee',
            'employee__master_employee',
            'employee__branch',
        )
        .filter(file=clean_path)
        .first()
    )
    if not doc:
        raise Http404("File not found.")

    if not user_can_download_document(request.user, doc):
        log_audit(
            actor=request.user,
            action='document_access_denied',
            target=doc,
            summary=f"Unauthorized direct media download attempt for document pk={doc.pk}",
        )
        return HttpResponseForbidden("Permission Denied: You do not have permission to access this document.")

    if not doc.file or not doc.file.storage.exists(doc.file.name):
        raise Http404("File not found.")

    DocumentDownloadLog.objects.create(
        document=doc,
        downloaded_by=request.user,
        ip_address=_get_client_ip(request),
    )
    log_audit(
        actor=request.user,
        action='document_downloaded',
        target=doc,
        summary=f"Downloaded document '{doc.title or doc.get_document_type_display()}' (pk={doc.pk})",
    )
    safe_name = sanitize_download_filename(
        f"{doc.title or doc.get_document_type_display()}_{doc.pk}",
        file_path=doc.file.name,
    )
    return stream_private_file(doc.file.open('rb'), filename=safe_name, as_attachment=True)


def _serve_attendance_media(request, clean_path):
    from apps.attendance.models import Attendance, AttendanceCorrection, AttendanceLocation

    if clean_path.startswith('attendance_corrections/'):
        corr = (
            AttendanceCorrection.objects
            .select_related('employee', 'employee__master_employee', 'employee__branch')
            .filter(attachment=clean_path)
            .first()
        )
        if not corr:
            raise Http404("File not found.")
        if not user_can_access_employee_target(request.user, 'attendance.view', employee_profile=corr.employee):
            return HttpResponseForbidden("Permission Denied.")
        if not corr.attachment.storage.exists(corr.attachment.name):
            raise Http404("File not found.")
        return stream_private_file(
            corr.attachment.open('rb'),
            filename=sanitize_download_filename(corr.attachment.name),
        )

    att = (
        Attendance.objects
        .select_related('employee', 'employee__master_employee', 'employee__branch')
        .filter(photo=clean_path)
        .first()
    )
    if att:
        if not user_can_access_employee_target(request.user, 'attendance.view', employee_profile=att.employee):
            return HttpResponseForbidden("Permission Denied.")
        if not att.photo.storage.exists(att.photo.name):
            raise Http404("File not found.")
        return stream_private_file(
            att.photo.open('rb'),
            filename=sanitize_download_filename(att.photo.name),
        )

    loc = (
        AttendanceLocation.objects
        .select_related('attendance__employee', 'attendance__employee__master_employee', 'attendance__employee__branch')
        .filter(event_photo=clean_path)
        .first()
    )
    if not loc:
        raise Http404("File not found.")
    if not user_can_access_employee_target(request.user, 'attendance.view', employee_profile=loc.attendance.employee):
        return HttpResponseForbidden("Permission Denied.")
    if not loc.event_photo.storage.exists(loc.event_photo.name):
        raise Http404("File not found.")
    return stream_private_file(
        loc.event_photo.open('rb'),
        filename=sanitize_download_filename(loc.event_photo.name),
    )


def _serve_expense_media(request, clean_path):
    from apps.expense.models import Expense, ExpenseHistory, ExpenseReturnEvent

    emp = None
    file_field = None

    exp = (
        Expense.objects
        .select_related('employee', 'employee__master_employee', 'employee__branch')
        .filter(attachment=clean_path)
        .first()
    )
    if exp:
        emp = exp.employee
        file_field = exp.attachment
    else:
        ret = (
            ExpenseReturnEvent.objects
            .select_related('expense__employee', 'expense__employee__master_employee', 'expense__employee__branch')
            .filter(attachment=clean_path)
            .first()
        )
        if ret:
            emp = ret.expense.employee
            file_field = ret.attachment
        else:
            hist = (
                ExpenseHistory.objects
                .select_related('expense__employee', 'expense__employee__master_employee', 'expense__employee__branch')
                .filter(attachment=clean_path)
                .first()
            )
            if hist:
                emp = hist.expense.employee
                file_field = hist.attachment

    if not file_field or not emp:
        raise Http404("File not found.")

    if not user_can_access_employee_target(request.user, 'expense.view', employee_profile=emp):
        return HttpResponseForbidden("Permission Denied.")
    if not file_field.storage.exists(file_field.name):
        raise Http404("File not found.")

    return stream_private_file(
        file_field.open('rb'),
        filename=sanitize_download_filename(file_field.name),
    )


def _serve_project_media(request, clean_path):
    from apps.projects.models import ProjectTask, TaskAttachment

    task = None
    file_field = None

    att = (
        TaskAttachment.objects
        .select_related(
            'task',
            'task__responsible_person',
            'task__responsible_person__master_employee',
            'task__responsible_person__branch',
            'task__project',
        )
        .filter(file=clean_path)
        .first()
    )
    if att:
        task = att.task
        file_field = att.file
    else:
        task = (
            ProjectTask.objects
            .select_related(
                'responsible_person',
                'responsible_person__master_employee',
                'responsible_person__branch',
                'project',
            )
            .filter(Q(assignment_attachment=clean_path) | Q(completion_attachment=clean_path))
            .first()
        )
        if task:
            file_field = (
                task.assignment_attachment
                if task.assignment_attachment and task.assignment_attachment.name == clean_path
                else task.completion_attachment
            )

    if not task or not file_field:
        raise Http404("File not found.")

    is_task_participant = (
        task.created_by_id == request.user.id
        or (task.responsible_person_id and task.responsible_person.user_id == request.user.id)
    )
    if not is_task_participant and not PermissionEngine.check_object_scope(request.user, task, 'projects.view'):
        return HttpResponseForbidden("Permission Denied.")
    if not file_field.storage.exists(file_field.name):
        raise Http404("File not found.")

    return stream_private_file(
        file_field.open('rb'),
        filename=sanitize_download_filename(file_field.name),
    )


def protected_media_view(request, path):
    """
    Enforce traversal protection and object-level access control for private
    uploaded media in both DEBUG and non-DEBUG configurations.
    """
    clean_path = validate_media_relpath(path)
    if clean_path is None:
        return HttpResponseForbidden("Invalid media path.")

    if clean_path.startswith(PUBLIC_MEDIA_PREFIXES):
        return serve(request, path=clean_path, document_root=settings.MEDIA_ROOT)

    is_private_prefix = clean_path.startswith(PRIVATE_MEDIA_PREFIXES)
    private_asset = None
    if not is_private_prefix:
        from apps.audit.models import MediaAsset
        private_asset = (
            MediaAsset.objects
            .filter(is_private=True)
            .filter(Q(url_path=clean_path) | Q(provider_file_id=clean_path))
            .first()
        )
        if not private_asset:
            return serve(request, path=clean_path, document_root=settings.MEDIA_ROOT)

    if not request.user.is_authenticated:
        return HttpResponseForbidden("Authentication required.")

    if clean_path.startswith('employees/documents/'):
        return _serve_employee_document(request, clean_path)
    if clean_path.startswith(('attendance/photos/', 'attendance_corrections/')):
        return _serve_attendance_media(request, clean_path)
    if clean_path.startswith('expenses/'):
        return _serve_expense_media(request, clean_path)
    if clean_path.startswith('projects/task_attachments/'):
        return _serve_project_media(request, clean_path)

    if private_asset is not None:
        from apps.audit.media_service import MediaService
        try:
            MediaService.get_secure_url(private_asset, request.user)
        except PermissionError:
            return HttpResponseForbidden("Permission Denied.")
        asset_path = validate_media_relpath(private_asset.provider_file_id or clean_path)
        if not asset_path or not default_storage.exists(asset_path):
            raise Http404("File not found.")
        return stream_private_file(
            default_storage.open(asset_path),
            filename=sanitize_download_filename(private_asset.original_name or asset_path),
            content_type=private_asset.mime_type,
        )

    raise Http404("File not found.")
