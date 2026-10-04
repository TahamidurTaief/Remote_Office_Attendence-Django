from django.db.models import Q
from apps.accounts.engine import PermissionEngine
from apps.accounts.models import DataScope


def get_scoped_project_task_queryset(user, action='view', base_qs=None):
    """
    Authoritative database-scoped queryset for ProjectTask.
    Enforces PermissionEngine, DataScope, branch context, employee profile,
    project membership, and project manager relations.
    Missing employee or branch context fails closed.
    """
    from apps.projects.models import ProjectTask

    if base_qs is None:
        base_qs = ProjectTask.objects.all()

    if not user or not user.is_authenticated:
        return base_qs.none()

    if user.is_superuser:
        return base_qs

    emp_profile = getattr(user, 'employee_profile', None)
    emp_master = getattr(user, 'employee_master', None)

    user_branch = None
    if emp_master and emp_master.branch:
        user_branch = emp_master.branch
    elif emp_profile and emp_profile.branch:
        user_branch = emp_profile.branch

    q_filter = Q()

    if action == 'view':
        perm_code = 'projects.view'
        perm_action = 'view'
    elif action == 'delete':
        perm_code = 'projects.delete'
        perm_action = 'delete'
    else:  # 'edit', 'approve', 'complete', 'update'
        perm_code = 'projects.edit'
        perm_action = 'update'

    eval_res = PermissionEngine.evaluate(user, perm_code, action_type=perm_action)
    if eval_res.allowed:
        scope = eval_res.data_scope
        if scope in (DataScope.GLOBAL, DataScope.COMPANY):
            return base_qs
        elif scope == DataScope.BRANCH:
            if user_branch:
                q_filter |= Q(project__branch=user_branch) | (
                    Q(project__isnull=True) & Q(responsible_person__branch=user_branch)
                )
        elif scope == DataScope.DEPARTMENT:
            user_dept = getattr(emp_master, 'department', None) or getattr(emp_profile, 'department', None)
            if user_dept:
                q_filter |= Q(responsible_person__department=user_dept)
        elif scope == DataScope.TEAM:
            if emp_master:
                from apps.employees.hierarchy_services import OrgHierarchyService
                sub_ids = list(OrgHierarchyService.get_all_subordinates(emp_master).values_list('id', flat=True))
                sub_ids.append(emp_master.id)
                q_filter |= Q(responsible_person__master_employee_id__in=sub_ids)
        elif scope == DataScope.OWN:
            if emp_profile:
                q_filter |= Q(responsible_person=emp_profile)

    if action == 'view':
        # Relationship-based access for viewing
        if emp_profile:
            q_filter |= Q(responsible_person=emp_profile)
            q_filter |= Q(project__project_managers=emp_profile)
            q_filter |= Q(project__project_members=emp_profile)
            q_filter |= Q(project__site_engineers=emp_profile)
    elif action in ('edit', 'approve', 'complete'):
        # Project managers have authority to edit/approve/complete tasks in their projects
        if emp_profile:
            q_filter |= Q(project__project_managers=emp_profile)

    if not q_filter:
        return base_qs.none()

    return base_qs.filter(q_filter).distinct()
