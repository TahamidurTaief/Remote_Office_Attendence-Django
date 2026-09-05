from django.test import TestCase, RequestFactory
from django.core.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from datetime import date
from apps.accounts.rbac_models import (
    Role, Module, Action, Permission, RolePermission,
    UserRoleAssignment, UserPermissionOverride, DataScope
)
from apps.accounts.engine import PermissionEngine
from apps.accounts.services import RolePermissionAssignmentService, RoleAssignmentService
from apps.accounts.rbac_registry import RBACRegistryService
from apps.branches.models import Branch
from apps.projects.models import Project, ProjectType
from apps.employees.models import Employee, EmployeeProfile

User = get_user_model()


class DynamicRBACEndToEndTests(TestCase):

    def setUp(self):
        # 1. Sync canonical permissions idempotently
        RBACRegistryService.sync_database()

        # 2. Setup Modules, Actions, and Permissions
        self.mod_projects = Module.objects.get(code='projects')
        self.act_view = Action.objects.get(code='view')
        self.act_add = Action.objects.get(code='add')
        self.act_edit = Action.objects.get(code='edit')
        self.act_update = Action.objects.get(code='update')
        self.act_delete = Action.objects.get(code='delete')

        self.perm_proj_view = Permission.objects.get(codename='projects.view')
        self.perm_proj_add = Permission.objects.get(codename='projects.add')
        self.perm_proj_edit = Permission.objects.get(codename='projects.edit')
        self.perm_proj_update = Permission.objects.get(codename='projects.update')
        self.perm_proj_delete = Permission.objects.get(codename='projects.delete')

        # 3. Setup Branches
        self.branch_a = Branch.objects.create(name="Branch North", address="123 North", latitude=23.81, longitude=90.41, is_active=True)
        self.branch_b = Branch.objects.create(name="Branch South", address="456 South", latitude=23.72, longitude=90.38, is_active=True)

        # 4. Setup Users
        self.superuser = User.objects.create_superuser(
            email="super@example.com",
            password="Password123!",
            is_staff=True,
            is_superuser=True
        )

        self.user_a = User.objects.create_user(
            email="user_a@example.com",
            password="Password123!"
        )
        self.profile_a = EmployeeProfile.objects.create(
            user=self.user_a,
            full_name="User Alpha",
            employee_id="EMP-001",
            phone="+8801700000001",
            branch=self.branch_a,
            joined_date=date.today(),
            is_active=True
        )

        self.user_b = User.objects.create_user(
            email="user_b@example.com",
            password="Password123!"
        )
        self.profile_b = EmployeeProfile.objects.create(
            user=self.user_b,
            full_name="User Beta",
            employee_id="EMP-002",
            phone="+8801700000002",
            branch=self.branch_b,
            joined_date=date.today(),
            is_active=True
        )

        # 5. Setup Projects
        self.pt = ProjectType.objects.create(name="General")
        self.project_a = Project.objects.create(
            name="Project Alpha North",
            client_name="Client A",
            start_date=date.today(),
            branch=self.branch_a,
            project_type=self.pt,
            status="In Progress",
            created_by=self.user_a
        )
        self.project_b = Project.objects.create(
            name="Project Beta South",
            client_name="Client B",
            start_date=date.today(),
            branch=self.branch_b,
            project_type=self.pt,
            status="In Progress",
            created_by=self.user_b
        )

    def test_multi_role_union_and_scope_hierarchy(self):
        """Permissions from multiple active roles must union and adopt the highest scope rank."""
        role1 = Role.objects.create(name="Role Branch", code="role_branch", is_active=True)
        RolePermission.objects.create(role=role1, permission=self.perm_proj_view, data_scope=DataScope.BRANCH)
        UserRoleAssignment.objects.create(user=self.user_a, role=role1)

        eval1 = PermissionEngine.evaluate(self.user_a, 'projects.view')
        self.assertTrue(eval1.allowed)
        self.assertEqual(eval1.data_scope, DataScope.BRANCH)

        # Add second role with GLOBAL scope
        role2 = Role.objects.create(name="Role Global", code="role_global", is_active=True)
        RolePermission.objects.create(role=role2, permission=self.perm_proj_view, data_scope=DataScope.GLOBAL)
        UserRoleAssignment.objects.create(user=self.user_a, role=role2)

        PermissionEngine.invalidate_user_cache(self.user_a)
        eval2 = PermissionEngine.evaluate(self.user_a, 'projects.view')
        self.assertTrue(eval2.allowed)
        self.assertEqual(eval2.data_scope, DataScope.GLOBAL)

    def test_inactive_roles_are_ignored(self):
        """Inactive roles must not grant any permissions."""
        role_inactive = Role.objects.create(name="Role Inactive", code="role_inactive", is_active=False)
        RolePermission.objects.create(role=role_inactive, permission=self.perm_proj_delete, data_scope=DataScope.GLOBAL)
        UserRoleAssignment.objects.create(user=self.user_a, role=role_inactive)

        eval_res = PermissionEngine.evaluate(self.user_a, 'projects.delete', action_type='delete')
        self.assertFalse(eval_res.allowed)

    def test_explicit_user_denial_overrides_role_grant(self):
        """An explicit denial on a user must strictly override any grant from assigned roles."""
        role = Role.objects.create(name="Project Admin", code="proj_admin", is_active=True)
        RolePermission.objects.create(role=role, permission=self.perm_proj_delete, data_scope=DataScope.GLOBAL)
        UserRoleAssignment.objects.create(user=self.user_a, role=role)

        eval_granted = PermissionEngine.evaluate(self.user_a, 'projects.delete', action_type='delete')
        self.assertTrue(eval_granted.allowed)

        # Explicit deny override
        UserPermissionOverride.objects.create(
            user=self.user_a,
            permission=self.perm_proj_delete,
            is_granted=False
        )
        PermissionEngine.invalidate_user_cache(self.user_a)

        eval_denied = PermissionEngine.evaluate(self.user_a, 'projects.delete', action_type='delete')
        self.assertFalse(eval_denied.allowed)

    def test_independent_action_resolution_no_unsafe_aliases(self):
        """Actions must be strictly independent: edit does NOT grant update, add does NOT grant edit."""
        role = Role.objects.create(name="Editor Only", code="editor_only", is_active=True)
        RolePermission.objects.create(role=role, permission=self.perm_proj_edit, data_scope=DataScope.GLOBAL)
        UserRoleAssignment.objects.create(user=self.user_a, role=role)

        eval_edit = PermissionEngine.evaluate(self.user_a, 'projects.edit', action_type='edit')
        self.assertTrue(eval_edit.allowed)

        # Update must be denied because it is an independent action and unconditional alias was removed
        eval_update = PermissionEngine.evaluate(self.user_a, 'projects.update', action_type='update')
        self.assertFalse(eval_update.allowed)

        # Delete must be denied
        eval_delete = PermissionEngine.evaluate(self.user_a, 'projects.delete', action_type='delete')
        self.assertFalse(eval_delete.allowed)

    def test_branch_scope_isolation_lists_and_objects(self):
        """Branch A user cannot access Branch B objects via queryset filtering or direct lookup."""
        role = Role.objects.create(name="Branch Project Manager", code="branch_pm", is_active=True)
        RolePermission.objects.create(role=role, permission=self.perm_proj_view, data_scope=DataScope.BRANCH)
        RolePermission.objects.create(role=role, permission=self.perm_proj_delete, data_scope=DataScope.BRANCH)
        UserRoleAssignment.objects.create(user=self.user_a, role=role)

        # Scoped queryset must contain only Branch A projects
        scoped_qs = PermissionEngine.filter_by_data_scope(
            self.user_a,
            Project.objects.all(),
            codename='projects.view',
            branch_field='branch'
        )
        self.assertIn(self.project_a, scoped_qs)
        self.assertNotIn(self.project_b, scoped_qs)

        # Object check: user_a has access to project_a but NOT project_b
        self.assertTrue(PermissionEngine.check_object_scope(self.user_a, self.project_a, codename='projects.view'))
        self.assertFalse(PermissionEngine.check_object_scope(self.user_a, self.project_b, codename='projects.view'))

        # get_scoped_object_or_404 on project_b must raise 404 (Http404)
        from django.http import Http404
        with self.assertRaises(Http404):
            PermissionEngine.get_scoped_object_or_404(
                Project,
                user=self.user_a,
                codename='projects.view',
                pk=self.project_b.pk,
                branch_field='branch'
            )

    def test_forged_matrix_post_privilege_escalation_rejected(self):
        """Actors cannot grant permissions or scopes they do not possess."""
        # Non-superuser user_a only holds projects.view at BRANCH scope
        role_base = Role.objects.create(name="Base Role", code="base_role", is_active=True)
        RolePermission.objects.create(role=role_base, permission=self.perm_proj_view, data_scope=DataScope.BRANCH)
        # Give user_a permission to administer roles
        perm_roles_edit = Permission.objects.get(codename='accounts.edit')
        RolePermission.objects.create(role=role_base, permission=perm_roles_edit, data_scope=DataScope.GLOBAL)
        UserRoleAssignment.objects.create(user=self.user_a, role=role_base)

        target_role = Role.objects.create(name="Custom Target", code="custom_target", is_active=True)

        # user_a attempts to grant projects.delete (which user_a does not hold)
        with self.assertRaises(PermissionDenied):
            RolePermissionAssignmentService.sync_role_permissions(
                role=target_role,
                selections={'mod_projects': {'delete': True}},
                actor=self.user_a
            )

        # user_a attempts to grant projects.view at GLOBAL scope (exceeding their BRANCH scope ceiling)
        with self.assertRaises(PermissionDenied):
            RolePermissionAssignmentService.sync_role_permissions(
                role=target_role,
                selections={'mod_projects': {'view': True}},
                data_scopes={'projects': DataScope.GLOBAL},
                actor=self.user_a
            )

    def test_protected_system_roles_cannot_be_mutated_or_locked_out(self):
        """system_owner is strictly protected from modification, and super_admin requires superuser."""
        sys_owner = Role.objects.get(code='system_owner')
        with self.assertRaises(PermissionDenied):
            RolePermissionAssignmentService.sync_role_permissions(
                role=sys_owner,
                selections={},
                actor=self.superuser
            )

        super_admin, _ = Role.objects.get_or_create(code='super_admin', defaults={'name': 'Super Admin', 'is_active': True})
        # Non-superuser attempt to modify super_admin must fail
        with self.assertRaises(PermissionDenied):
            RolePermissionAssignmentService.sync_role_permissions(
                role=super_admin,
                selections={},
                actor=self.user_a
            )

    def test_cache_invalidation_lifecycle(self):
        """User permission cache must immediately invalidate on role assignment or override changes."""
        role = Role.objects.create(name="Dynamic Role", code="dyn_role", is_active=True)
        RolePermission.objects.create(role=role, permission=self.perm_proj_add, data_scope=DataScope.GLOBAL)

        # Initial check: no permission
        self.assertFalse(PermissionEngine.evaluate(self.user_a, 'projects.add').allowed)
        # Ensure cached
        self.assertTrue(hasattr(self.user_a, '_resolved_permissions_cache'))

        # Assign role to user_a
        UserRoleAssignment.objects.create(user=self.user_a, role=role)

        # Cache should be invalidated by signal or service
        eval_after = PermissionEngine.evaluate(self.user_a, 'projects.add')
        self.assertTrue(eval_after.allowed)

    def test_permission_resolution_is_purely_read_only_with_zero_db_writes(self):
        """Evaluating permissions for a user must NOT write/create any Role or UserRoleAssignment records."""
        unassigned_user = User.objects.create_user(
            email="pure_readonly@example.com",
            password="Password123!",
            role="admin"  # legacy persona
        )

        role_count_before = Role.objects.count()
        assignment_count_before = UserRoleAssignment.objects.count()

        # Call get_user_resolved_permissions directly
        resolved = PermissionEngine.get_user_resolved_permissions(unassigned_user)
        self.assertEqual(resolved, {})

        # Call evaluate
        eval_res = PermissionEngine.evaluate(unassigned_user, 'projects.view')
        self.assertFalse(eval_res.allowed)

        # Assert ZERO records created
        self.assertEqual(Role.objects.count(), role_count_before)
        self.assertEqual(UserRoleAssignment.objects.count(), assignment_count_before)
        self.assertEqual(UserRoleAssignment.objects.filter(user=unassigned_user).count(), 0)

    def test_legacy_role_persona_grants_zero_access_without_assignment(self):
        """User with role='admin' but no active UserRoleAssignment gets 403 denied on protected routes."""
        from django.test import Client
        legacy_admin = User.objects.create_user(
            email="legacy_admin@example.com",
            password="Password123!",
            role="admin"
        )
        client = Client()
        client.force_login(legacy_admin)

        # Accessing admin panel dashboard or roles must return 403 Forbidden
        response = client.get('/admin-panel/roles/')
        self.assertEqual(response.status_code, 403)

    def test_filter_by_data_scope_invalid_field_fails_closed(self):
        """filter_by_data_scope returns queryset.none() when given invalid/non-existent fields."""
        role = Role.objects.create(name="Scoped Role", code="scoped_role", is_active=True)
        RolePermission.objects.create(role=role, permission=self.perm_proj_view, data_scope=DataScope.BRANCH)
        UserRoleAssignment.objects.create(user=self.user_a, role=role)

        qs = Project.objects.all()
        # Non-existent field lookup
        scoped_qs = PermissionEngine.filter_by_data_scope(
            self.user_a,
            qs,
            codename='projects.view',
            branch_field="non_existent_field__invalid_path"
        )
        self.assertEqual(scoped_qs.count(), 0)
        self.assertEqual(list(scoped_qs), [])

    def test_htmx_permission_denied_returns_cotton_alert(self):
        """HTMX request to a permission-protected endpoint returns 403 with Cotton alert partial and HX-Reswap none."""
        from django.test import Client
        client = Client()
        client.force_login(self.user_b)  # user_b has no roles

        response = client.get(
            '/admin-panel/roles/',
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers.get('HX-Reswap'), 'none')
        content = response.content.decode('utf-8')
        self.assertIn("Access Denied", content)

    def test_denied_role_mutation_records_audit_event(self):
        """Unauthorized role mutation rolls back and records a denied audit event."""
        from apps.audit.models import AuditEvent
        role_base = Role.objects.create(name="Base Role 2", code="base_role_2", is_active=True)
        perm_roles_edit = Permission.objects.get(codename='accounts.edit')
        RolePermission.objects.create(role=role_base, permission=perm_roles_edit, data_scope=DataScope.BRANCH)
        UserRoleAssignment.objects.create(user=self.user_a, role=role_base)

        target_role = Role.objects.create(name="Custom Target 2", code="custom_target_2", is_active=True)

        audit_count_before = AuditEvent.objects.filter(action="role_permissions_matrix_denied").count()

        with self.assertRaises(PermissionDenied):
            RolePermissionAssignmentService.sync_role_permissions(
                role=target_role,
                selections={'mod_projects': {'delete': True}},
                actor=self.user_a
            )

        audit_count_after = AuditEvent.objects.filter(action="role_permissions_matrix_denied").count()
        self.assertGreater(audit_count_after, audit_count_before)
