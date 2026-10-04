from django.db.models import Q
from django.core.exceptions import PermissionDenied
from django.http import Http404
from apps.accounts.engine import PermissionEngine
from apps.accounts.models import DataScope
from apps.employees.models import EmployeeProfile


def _extract_user_department_identity(user):
    """
    Extracts canonical department IDs and names from user context.
    Supports both master Employee.department (FK) and EmployeeProfile.department (CharField).
    Returns (set(dept_ids), set(dept_names)).
    """
    emp_master = getattr(user, 'employee_master', None)
    emp_profile = getattr(user, 'employee_profile', None)

    dept_ids = set()
    dept_names = set()

    if emp_master:
        dept_fk = getattr(emp_master, 'department', None)
        if dept_fk is not None:
            if hasattr(dept_fk, 'id'):
                dept_ids.add(dept_fk.id)
                name = getattr(dept_fk, 'name', None)
                if name and isinstance(name, str) and name.strip():
                    dept_names.add(name.strip())
            elif isinstance(dept_fk, int):
                dept_ids.add(dept_fk)
            elif isinstance(dept_fk, str) and dept_fk.strip():
                dept_names.add(dept_fk.strip())
        dept_id_attr = getattr(emp_master, 'department_id', None)
        if dept_id_attr:
            dept_ids.add(dept_id_attr)

    if emp_profile:
        prof_dept = getattr(emp_profile, 'department', None)
        if prof_dept is not None:
            if hasattr(prof_dept, 'id'):
                dept_ids.add(prof_dept.id)
                name = getattr(prof_dept, 'name', None)
                if name and isinstance(name, str) and name.strip():
                    dept_names.add(name.strip())
            elif isinstance(prof_dept, int):
                dept_ids.add(prof_dept)
            elif isinstance(prof_dept, str) and prof_dept.strip():
                dept_names.add(prof_dept.strip())

    user_dept = getattr(user, 'department', None)
    if user_dept is not None:
        if hasattr(user_dept, 'id'):
            dept_ids.add(user_dept.id)
            name = getattr(user_dept, 'name', None)
            if name and isinstance(name, str) and name.strip():
                dept_names.add(name.strip())
        elif isinstance(user_dept, int):
            dept_ids.add(user_dept)
        elif isinstance(user_dept, str) and user_dept.strip():
            dept_names.add(user_dept.strip())

    if dept_names:
        from apps.employees.models import Department
        try:
            name_q = Q()
            for name in dept_names:
                name_q |= Q(name__iexact=name)
            matched = Department.objects.filter(name_q).values('id', 'name')
            for m in matched:
                dept_ids.add(m['id'])
                if m.get('name'):
                    dept_names.add(m['name'].strip())
        except Exception:
            pass

    if dept_ids and not dept_names:
        from apps.employees.models import Department
        try:
            matched = Department.objects.filter(id__in=dept_ids).values_list('name', flat=True)
            for n in matched:
                if n and n.strip():
                    dept_names.add(n.strip())
        except Exception:
            pass

    return dept_ids, dept_names


def get_scoped_employee_queryset(user, permission_code='reports.view', action='view', base_qs=None):
    """
    Authoritative database-scoped employee queryset supporting:
    'OWN', 'TEAM', 'DEPARTMENT', 'BRANCH', 'COMPANY', and 'GLOBAL'.
    Missing canonical identity under restricted scopes fails closed.
    """
    if base_qs is None:
        base_qs = EmployeeProfile.objects.filter(is_active=True)

    if not user or not getattr(user, 'is_authenticated', False):
        return base_qs.none()

    if user.is_superuser:
        return base_qs

    eval_res = PermissionEngine.evaluate(user, permission_code, action_type=action)
    if not eval_res.allowed:
        return base_qs.none()

    scope = eval_res.data_scope
    if scope in (DataScope.GLOBAL, DataScope.COMPANY):
        return base_qs

    emp_profile = getattr(user, 'employee_profile', None)
    emp_master = getattr(user, 'employee_master', None)

    if scope == DataScope.BRANCH:
        branch = None
        if emp_profile and getattr(emp_profile, 'canonical_branch', None):
            branch = emp_profile.canonical_branch
        elif emp_master and getattr(emp_master, 'branch', None):
            branch = emp_master.branch
        elif emp_profile and getattr(emp_profile, 'branch', None):
            branch = emp_profile.branch

        if not branch:
            return base_qs.none()

        branch_id = getattr(branch, 'id', branch)
        return base_qs.filter(
            Q(branch_id=branch_id) | Q(master_employee__branch_id=branch_id)
        ).distinct()

    if scope == DataScope.DEPARTMENT:
        dept_ids, dept_names = _extract_user_department_identity(user)
        if not dept_ids and not dept_names:
            return base_qs.none()

        dept_q = Q()
        if dept_ids:
            dept_q |= Q(master_employee__department_id__in=dept_ids)
        for name in dept_names:
            dept_q |= Q(department__iexact=name) | Q(master_employee__department__name__iexact=name)

        return base_qs.filter(dept_q).distinct()

    if scope == DataScope.TEAM:
        if not emp_profile and not emp_master:
            return base_qs.none()

        team_q = Q()
        if emp_profile:
            team_q |= Q(pk=emp_profile.pk)
        if emp_master:
            team_q |= Q(master_employee=emp_master)

        resolved_m = emp_master or getattr(emp_profile, 'master_employee', None)
        if resolved_m:
            team_q |= Q(master_employee__reporting_manager=resolved_m)
            try:
                from apps.employees.hierarchy_services import OrgHierarchyService
                sub_ids = list(OrgHierarchyService.get_all_subordinates(resolved_m).values_list('id', flat=True))
                if sub_ids:
                    team_q |= Q(master_employee_id__in=sub_ids)
            except Exception:
                pass

        team_q |= Q(master_employee__reporting_manager__user=user)

        try:
            from apps.projects.models import Project
            managed_projs = Project.objects.filter(
                Q(project_managers=emp_profile) | Q(created_by=user)
            ) if emp_profile else Project.objects.filter(created_by=user)
            team_q |= (
                Q(site_engineer_projects__in=managed_projs) |
                Q(assigned_tasks__project__in=managed_projs)
            )
        except Exception:
            pass

        if not team_q:
            return base_qs.none()

        return base_qs.filter(team_q).distinct()

    if scope == DataScope.OWN:
        if emp_profile:
            return base_qs.filter(pk=emp_profile.pk)
        elif emp_master:
            return base_qs.filter(master_employee=emp_master)
        else:
            return base_qs.none()

    return base_qs.none()


def get_scoped_employee_or_404(user, pk, permission_code='reports.view', action='view', base_qs=None):
    """
    Retrieve employee matching authorized scope or raise PermissionDenied / Http404.
    """
    if not EmployeeProfile.objects.filter(pk=pk).exists():
        raise Http404("Employee not found.")

    scoped_qs = get_scoped_employee_queryset(user, permission_code=permission_code, action=action, base_qs=base_qs)
    emp = scoped_qs.filter(pk=pk).first()
    if not emp:
        raise PermissionDenied("You do not have permission to access this employee's report.")
    return emp
