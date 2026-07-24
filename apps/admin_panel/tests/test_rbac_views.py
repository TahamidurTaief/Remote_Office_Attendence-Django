from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.accounts.models import Role, Module, Action, Permission, RolePermission, UserRoleAssignment

User = get_user_model()


class DynamicRBACViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'Password123!'
        self.admin = User.objects.create_superuser(email='admin_rbac@example.com', password=self.password)
        self.target_user = User.objects.create_user(email='target_user@example.com', password=self.password)

        self.client.login(email='admin_rbac@example.com', password=self.password)

        self.mod = Module.objects.create(name='Projects', code='projects')
        self.act = Action.objects.create(name='View', code='view')
        self.perm = Permission.objects.create(module=self.mod, action=self.act, codename='projects.view')

        self.sys_role = Role.objects.create(
            name='System Owner',
            code='system_owner',
            is_system_protected=True,
            is_active=True
        )

        self.custom_role = Role.objects.create(
            name='Custom Role',
            code='custom_role',
            is_active=True
        )

    def test_role_list_view_renders(self):
        resp = self.client.get(reverse('admin_panel:role_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'System Owner')
        self.assertContains(resp, 'Custom Role')

    def test_role_create_view(self):
        resp = self.client.post(reverse('admin_panel:role_create'), {
            'name': 'HR Specialist',
            'code': 'hr_specialist',
            'description': 'HR manager role',
            'is_active': 'true'
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Role.objects.filter(code='hr_specialist').exists())

    def test_protected_role_deletion_blocked(self):
        resp = self.client.post(reverse('admin_panel:role_delete', kwargs={'pk': self.sys_role.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Role.objects.filter(pk=self.sys_role.pk).exists())

    def test_role_permission_toggle(self):
        url = reverse('admin_panel:role_perm_toggle', kwargs={'role_id': self.custom_role.id, 'perm_id': self.perm.id})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(RolePermission.objects.filter(role=self.custom_role, permission=self.perm).exists())

    def test_user_permissions_multi_role_assignment(self):
        url = reverse('admin_panel:user_permissions', kwargs={'pk': self.target_user.pk})
        resp = self.client.post(url, {
            'role_ids': [self.custom_role.id]
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(UserRoleAssignment.objects.filter(user=self.target_user, role=self.custom_role).exists())
