import re

TESTS_PATH = 'apps/projects/tests.py'
with open(TESTS_PATH, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace lines 1-9 (custom TestCase)
content = re.sub(
    r"import io\s+from django\.test import TestCase as DjangoTestCase\s+from django\.core\.management import call_command\s+class TestCase\(DjangoTestCase\):\s+def _callSetUp\(self\):\s+super\(\)\._callSetUp\(\)\s+call_command\('migrate_legacy_roles', stdout=io\.StringIO\(\)\)",
    """from django.test import TestCase
from apps.accounts.rbac_models import Role, UserRoleAssignment
from apps.accounts.rbac_registry import RBACRegistryService
from apps.accounts.engine import PermissionEngine

def assign_role_fixture(user, role_code):
    role = Role.objects.filter(code=role_code).first()
    if not role:
        RBACRegistryService.sync_system_registry()
        role = Role.objects.filter(code=role_code).first()
    if role:
        UserRoleAssignment.objects.get_or_create(user=user, role=role)
        PermissionEngine.invalidate_user_cache(user)""",
    content
)

# 2. Add assign_role_fixture to all setup blocks where users are created
# ProjectTests setUp
content = content.replace(
    "        self.admin_user = User.objects.create_user(\n            email='admin@example.com',\n            phone='+8801700000100',\n            password=self.password,\n            role='admin'\n        )",
    "        self.admin_user = User.objects.create_user(\n            email='admin@example.com',\n            phone='+8801700000100',\n            password=self.password,\n            role='admin'\n        )\n        assign_role_fixture(self.admin_user, 'admin')"
)
content = content.replace(
    "        self.staff_user = User.objects.create_user(\n            email='staff@example.com',\n            phone='+8801700000200',\n            password=self.password,\n            role='staff'\n        )",
    "        self.staff_user = User.objects.create_user(\n            email='staff@example.com',\n            phone='+8801700000200',\n            password=self.password,\n            role='staff'\n        )\n        assign_role_fixture(self.staff_user, 'staff')"
)

# Phase2And3Tests setUp
content = content.replace(
    "        self.admin_user = User.objects.create_user(\n            email='admin_p23@example.com',\n            phone='+8801700000300',\n            password=self.password,\n            role='admin'\n        )",
    "        self.admin_user = User.objects.create_user(\n            email='admin_p23@example.com',\n            phone='+8801700000300',\n            password=self.password,\n            role='admin'\n        )\n        assign_role_fixture(self.admin_user, 'admin')"
)

# ProjectCSVExportTests setUp
content = content.replace(
    "        self.admin_user = User.objects.create_user(\n            email='admin@example.com',\n            phone='+8801700000100',\n            password=self.password,\n            role='admin'\n        )",
    "        self.admin_user = User.objects.create_user(\n            email='admin@example.com',\n            phone='+8801700000100',\n            password=self.password,\n            role='admin'\n        )\n        assign_role_fixture(self.admin_user, 'admin')"
)

# ProjectTaskShiftTests setUp
content = content.replace(
    "        self.admin_user = User.objects.create_user(\n            email='admin_shift@example.com',\n            phone='+8801700000500',\n            password=self.password,\n            role='admin'\n        )",
    "        self.admin_user = User.objects.create_user(\n            email='admin_shift@example.com',\n            phone='+8801700000500',\n            password=self.password,\n            role='admin'\n        )\n        assign_role_fixture(self.admin_user, 'admin')"
)

# ProjectNotificationEmailTests setUp
content = content.replace(
    "        self.admin_user = User.objects.create_user(\n            email='admin_notif@example.com',\n            phone='+8801700000600',\n            password=self.password,\n            role='admin'\n        )",
    "        self.admin_user = User.objects.create_user(\n            email='admin_notif@example.com',\n            phone='+8801700000600',\n            password=self.password,\n            role='admin'\n        )\n        assign_role_fixture(self.admin_user, 'admin')"
)

# ProjectTaskNewFeaturesTests setUp
content = content.replace(
    "        self.admin_user = User.objects.create_user(\n            email='admin_newfeat@example.com',\n            phone='+8801700000700',\n            password=self.password,\n            role='admin'\n        )",
    "        self.admin_user = User.objects.create_user(\n            email='admin_newfeat@example.com',\n            phone='+8801700000700',\n            password=self.password,\n            role='admin'\n        )\n        assign_role_fixture(self.admin_user, 'admin')"
)

# ProjectTemplateIntegrationTests setUp
content = content.replace(
    "        self.admin_user = User.objects.create_user(\n            email='admin_tmpl@example.com',\n            phone='+8801700000800',\n            password=self.password,\n            role='admin'\n        )",
    "        self.admin_user = User.objects.create_user(\n            email='admin_tmpl@example.com',\n            phone='+8801700000800',\n            password=self.password,\n            role='admin'\n        )\n        assign_role_fixture(self.admin_user, 'admin')"
)

# StaffTaskCompletePermissionsTests setUp
content = content.replace(
    "        self.pm_user = User.objects.create_user(email='pm@example.com', phone='+8801700000888', role='manager', password=self.password)",
    "        self.pm_user = User.objects.create_user(email='pm@example.com', phone='+8801700000888', role='manager', password=self.password)\n        assign_role_fixture(self.pm_user, 'manager')"
)

# ProjectGanttTests setUp
content = content.replace(
    "        self.admin_user = User.objects.create_user(\n            email='admin_gantt@example.com',\n            phone='+8801700000900',\n            password=self.password,\n            role='admin'\n        )",
    "        self.admin_user = User.objects.create_user(\n            email='admin_gantt@example.com',\n            phone='+8801700000900',\n            password=self.password,\n            role='admin'\n        )\n        assign_role_fixture(self.admin_user, 'admin')"
)

# GlobalTaskTwoModeFormTests setUp
content = content.replace(
    "        self.admin_user = User.objects.create_user(\n            email='admin_gt@example.com',\n            phone='+8801700001000',\n            password=self.password,\n            role='admin'\n        )",
    "        self.admin_user = User.objects.create_user(\n            email='admin_gt@example.com',\n            phone='+8801700001000',\n            password=self.password,\n            role='admin'\n        )\n        assign_role_fixture(self.admin_user, 'admin')"
)

# ProjectGanttExportTests setUp
content = content.replace(
    "        self.admin_user = User.objects.create_user(\n            email='admin_export@example.com',\n            phone='+8801700001100',\n            password=self.password,\n            role='admin'\n        )",
    "        self.admin_user = User.objects.create_user(\n            email='admin_export@example.com',\n            phone='+8801700001100',\n            password=self.password,\n            role='admin'\n        )\n        assign_role_fixture(self.admin_user, 'admin')"
)

# 3. Update test_project_views_redirect_non_admin
old_non_admin = """    def test_project_views_redirect_non_admin(self):
        # 1. Anonymous access
        response = self.client.get(reverse('projects:project_list'))
        # Should redirect to login page (dispatch handles this)
        self.assertEqual(response.status_code, 302)
        
        # 2. Staff user access (should redirect to /staff/home/)
        self.client.login(username='+8801700000200', password=self.password)
        response = self.client.get(reverse('projects:project_list'))
        self.assertRedirects(response, '/staff/home/')
        
        # Try to post project add
        response_post = self.client.post(reverse('projects:project_add'), data=self.project_data)
        self.assertRedirects(response_post, '/staff/home/')"""

new_non_admin = """    def test_project_views_redirect_non_admin(self):
        # 1. Anonymous access
        response = self.client.get(reverse('projects:project_list'))
        # Should redirect to login page (dispatch handles this)
        self.assertEqual(response.status_code, 302)
        
        # 2. Staff user access (authenticated denial must return exact HTTP 403)
        self.client.login(username='+8801700000200', password=self.password)
        response = self.client.get(reverse('projects:project_list'))
        self.assertEqual(response.status_code, 403)
        
        # Try to post project add
        response_post = self.client.post(reverse('projects:project_add'), data=self.project_data)
        self.assertEqual(response_post.status_code, 403)"""

content = content.replace(old_non_admin, new_non_admin)

# 4. Replace test_branch_scoping_todo_comments_present_in_views
old_todo_test = """    def test_branch_scoping_todo_comments_present_in_views(self):
        \"\"\"Verify TODO: branch-scoping deferred comments exist in views.py.\"\"\"
        import os
        views_path = os.path.join(os.path.dirname(__file__), 'views.py')
        with open(views_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn('TODO: branch-scoping deferred', content,
                      "Expected 'TODO: branch-scoping deferred' comment in views.py")"""

new_todo_test = """    def test_branch_scoping_isolation_enforced(self):
        \"\"\"Verify cross-branch access to project tasks is denied via scoped 404.\"\"\"
        branch_b = Branch.objects.create(
            name='Branch B',
            address='Gulshan, Dhaka',
            latitude=23.7925,
            longitude=90.4078,
            radius_meters=100
        )
        proj_b = Project.objects.create(
            name='Branch B Project',
            project_type=self.project_type,
            client_name='Branch B Client',
            location='Gulshan',
            system_type='VRF',
            start_date=date.today(),
            branch=branch_b
        )
        task_b = ProjectTask.objects.create(
            project=proj_b,
            activity='Task in Branch B',
            order=1
        )
        mgr_user = User.objects.create_user(
            email='branch_a_mgr@example.com',
            phone='+8801700000999',
            password=self.password,
        )
        from apps.accounts.rbac_models import Role, RolePermission, UserRoleAssignment, DataScope
        role_scoped, _ = Role.objects.get_or_create(code='scoped_mgr', defaults={'name': 'Scoped Role', 'is_active': True})
        perm = RBACRegistryService.ensure_permission('projects.delete')
        RolePermission.objects.get_or_create(role=role_scoped, permission=perm, defaults={'data_scope': DataScope.BRANCH})
        UserRoleAssignment.objects.get_or_create(user=mgr_user, role=role_scoped, branch=self.branch)
        PermissionEngine.invalidate_user_cache(mgr_user)

        self.client.login(username='+8801700000999', password=self.password)
        resp = self.client.post(reverse('projects:task_delete', kwargs={'pk': task_b.pk}))
        self.assertEqual(resp.status_code, 404)"""

content = content.replace(old_todo_test, new_todo_test)

with open(TESTS_PATH, 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated apps/projects/tests.py successfully.")
