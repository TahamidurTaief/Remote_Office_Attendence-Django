from django.db.models import Q
from django.core.exceptions import PermissionDenied
from django.http import Http404
from apps.accounts.engine import PermissionEngine
from apps.accounts.models import DataScope


def get_scoped_project_queryset(user, codename='projects.view', action='view', base_qs=None):
    """
    Authoritative database-scoped queryset for Project.
    Accepts exact permission code and action type:
      'projects.view', 'projects.edit', 'projects.delete', 'projects.export',
      as well as 'projects.add' and 'projects.approve'.
    Enforces PermissionEngine, DataScope, branch context, employee profile,
    and project manager relations.
    Missing employee or branch context under restricted scopes fails closed.
    """
    from apps.projects.models import Project

    if base_qs is None:
        base_qs = Project.objects.all()

    if not user or not user.is_authenticated:
        return base_qs.none()

    if user.is_superuser:
        return base_qs

    # Allow passing action as first positional argument (e.g. get_scoped_project_queryset(user, 'edit'))
    if codename in ('view', 'edit', 'delete', 'export', 'add', 'approve'):
        action = codename
        codename = None

    if not codename:
        if action in ('edit', 'update'):
            codename = 'projects.edit'
        elif action == 'delete':
            codename = 'projects.delete'
        elif action == 'export':
            codename = 'projects.export'
        elif action == 'add':
            codename = 'projects.add'
        elif action == 'approve':
            codename = 'projects.approve'
        else:
            codename = 'projects.view'

    if not action:
        if codename in ('projects.edit', 'projects.update'):
            action = 'update'
        elif codename == 'projects.delete':
            action = 'delete'
        elif codename == 'projects.export':
            action = 'export'
        elif codename == 'projects.add':
            action = 'create'
        elif codename == 'projects.approve':
            action = 'approve'
        else:
            action = 'view'

    perm_action = action
    if perm_action == 'edit':
        perm_action = 'update'
    elif perm_action == 'add':
        perm_action = 'create'

    emp_profile = getattr(user, 'employee_profile', None)
    emp_master = getattr(user, 'employee_master', None)

    user_branch = None
    if emp_master and emp_master.branch:
        user_branch = emp_master.branch
    elif emp_profile and emp_profile.branch:
        user_branch = emp_profile.branch

    q_filter = Q()

    eval_res = PermissionEngine.evaluate(user, codename, action_type=perm_action)
    if eval_res.allowed:
        scope = eval_res.data_scope
        if scope in (DataScope.GLOBAL, DataScope.COMPANY):
            return base_qs
        elif scope == DataScope.BRANCH:
            # Must have canonical branch context; fails closed if missing
            if user_branch:
                q_filter |= Q(branch=user_branch)
        elif scope == DataScope.DEPARTMENT:
            user_dept = getattr(emp_master, 'department', None) or getattr(emp_profile, 'department', None)
            if user_dept:
                q_filter |= Q(branch__department=user_dept)
        elif scope == DataScope.TEAM:
            if emp_master:
                from apps.employees.hierarchy_services import OrgHierarchyService
                sub_ids = list(OrgHierarchyService.get_all_subordinates(emp_master).values_list('id', flat=True))
                sub_ids.append(emp_master.id)
                q_filter |= Q(project_managers__master_employee_id__in=sub_ids)
        elif scope == DataScope.OWN:
            if emp_profile:
                q_filter |= Q(project_managers=emp_profile) | Q(created_by=user)

    # Project-level role relationships
    if action == 'view':
        # Viewing is allowed for designated project roles
        if emp_profile:
            q_filter |= Q(project_managers=emp_profile)
            q_filter |= Q(project_members=emp_profile)
            q_filter |= Q(site_engineers=emp_profile)
    elif action in ('edit', 'update', 'approve', 'add'):
        # Project managers have authority to edit / manage child records in their assigned projects
        if emp_profile:
            q_filter |= Q(project_managers=emp_profile)

    if not q_filter:
        return base_qs.none()

    return base_qs.filter(q_filter).distinct()


def get_scoped_project_or_404(user, pk, codename='projects.view', action='view', select_for_update=False, base_qs=None):
    """
    Retrieve project matching authorized scope or raise PermissionDenied / Http404.
    """
    from apps.projects.models import Project

    scoped_qs = get_scoped_project_queryset(user, codename=codename, action=action, base_qs=base_qs)
    if not scoped_qs.filter(pk=pk).exists():
        if Project.objects.filter(pk=pk).exists():
            raise PermissionDenied("You do not have permission to access this project.")
        raise Http404("Project not found.")

    if select_for_update:
        return Project.objects.select_for_update().get(pk=pk)
    return scoped_qs.filter(pk=pk).first()


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


def get_scoped_project_task_or_404(user, pk, action='view', select_for_update=False, base_qs=None):
    """
    Retrieve task matching authorized scope or raise PermissionDenied / Http404.
    """
    from apps.projects.models import ProjectTask

    scoped_qs = get_scoped_project_task_queryset(user, action=action, base_qs=base_qs)
    if not scoped_qs.filter(pk=pk).exists():
        if ProjectTask.objects.filter(pk=pk).exists():
            raise PermissionDenied("You do not have permission to access this task.")
        raise Http404("Task not found.")

    if select_for_update:
        return ProjectTask.objects.select_for_update().get(pk=pk)
    return scoped_qs.filter(pk=pk).first()


def get_scoped_project_material_queryset(user, codename='projects.view', action='view', base_qs=None):
    from apps.projects.models import ProjectMaterial

    if base_qs is None:
        base_qs = ProjectMaterial.objects.all()

    if not user or not user.is_authenticated:
        return base_qs.none()

    if user.is_superuser:
        return base_qs

    scoped_projects = get_scoped_project_queryset(user, codename=codename, action=action)
    return base_qs.filter(project__in=scoped_projects).distinct()


def get_scoped_project_material_or_404(user, pk, codename='projects.edit', action='edit', select_for_update=False):
    from apps.projects.models import ProjectMaterial

    scoped_qs = get_scoped_project_material_queryset(user, codename=codename, action=action)
    if not scoped_qs.filter(pk=pk).exists():
        if ProjectMaterial.objects.filter(pk=pk).exists():
            raise PermissionDenied("You do not have permission to access this material.")
        raise Http404("Material not found.")

    if select_for_update:
        return ProjectMaterial.objects.select_for_update().get(pk=pk)
    return scoped_qs.filter(pk=pk).first()


def get_scoped_progress_log_queryset(user, codename='projects.view', action='view', base_qs=None):
    from apps.projects.models import DailyProgressLog

    if base_qs is None:
        base_qs = DailyProgressLog.objects.all()

    if not user or not user.is_authenticated:
        return base_qs.none()

    if user.is_superuser:
        return base_qs

    scoped_projects = get_scoped_project_queryset(user, codename=codename, action=action)
    return base_qs.filter(project__in=scoped_projects).distinct()


def get_scoped_progress_log_or_404(user, pk, codename='projects.edit', action='edit', select_for_update=False):
    from apps.projects.models import DailyProgressLog

    scoped_qs = get_scoped_progress_log_queryset(user, codename=codename, action=action)
    if not scoped_qs.filter(pk=pk).exists():
        if DailyProgressLog.objects.filter(pk=pk).exists():
            raise PermissionDenied("You do not have permission to access this progress log.")
        raise Http404("Progress log not found.")

    if select_for_update:
        return DailyProgressLog.objects.select_for_update().get(pk=pk)
    return scoped_qs.filter(pk=pk).first()


def get_scoped_manpower_queryset(user, codename='projects.view', action='view', base_qs=None):
    from apps.projects.models import ManpowerDeployment

    if base_qs is None:
        base_qs = ManpowerDeployment.objects.all()

    if not user or not user.is_authenticated:
        return base_qs.none()

    if user.is_superuser:
        return base_qs

    scoped_projects = get_scoped_project_queryset(user, codename=codename, action=action)
    return base_qs.filter(project__in=scoped_projects).distinct()


def get_scoped_manpower_or_404(user, pk, codename='projects.edit', action='edit', select_for_update=False):
    from apps.projects.models import ManpowerDeployment

    scoped_qs = get_scoped_manpower_queryset(user, codename=codename, action=action)
    if not scoped_qs.filter(pk=pk).exists():
        if ManpowerDeployment.objects.filter(pk=pk).exists():
            raise PermissionDenied("You do not have permission to access this manpower log.")
        raise Http404("Manpower log not found.")

    if select_for_update:
        return ManpowerDeployment.objects.select_for_update().get(pk=pk)
    return scoped_qs.filter(pk=pk).first()


def get_scoped_task_dependency_or_404(user, pk, action='edit', select_for_update=False):
    from apps.projects.models import TaskDependency

    scoped_projects = get_scoped_project_queryset(user, codename='projects.edit', action=action)
    qs = TaskDependency.objects.filter(successor__project__in=scoped_projects)
    if not qs.filter(pk=pk).exists():
        if TaskDependency.objects.filter(pk=pk).exists():
            raise PermissionDenied("You do not have permission to access this dependency.")
        raise Http404("Dependency not found.")

    if select_for_update:
        return TaskDependency.objects.select_for_update().get(pk=pk)
    return qs.filter(pk=pk).first()
