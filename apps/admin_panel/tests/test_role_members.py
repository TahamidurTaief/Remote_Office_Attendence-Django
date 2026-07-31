from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.accounts.models import Role, UserRoleAssignment
from apps.employees.models import EmployeeProfile, Branch
from datetime import date

User = get_user_model()


class RoleMembersViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'Password123!'
        self.admin = User.objects.create_superuser(email='admin_members@example.com', password=self.password, role='admin')
        self.non_admin = User.objects.create_user(email='staff_members@example.com', password=self.password, role='employee')

        self.branch = Branch.objects.create(name='Main HQ', address='HQ Address', latitude=23.81, longitude=90.41)

        self.user1 = User.objects.create_user(email='user1@example.com', password=self.password)
        self.emp1 = EmployeeProfile.objects.create(
            user=self.user1,
            branch=self.branch,
            employee_id='EMP001',
            full_name='User One',
            phone='1234567890',
            joined_date=date.today(),
            is_active=True
        )

        self.user2 = User.objects.create_user(email='user2@example.com', password=self.password)
        self.emp2 = EmployeeProfile.objects.create(
            user=self.user2,
            branch=self.branch,
            employee_id='EMP002',
            full_name='User Two',
            phone='1234567891',
            joined_date=date.today(),
            is_active=True
        )

        self.role = Role.objects.create(name='Project Managers', code='project_managers', is_active=True)

        # Assign user1 to role initially
        UserRoleAssignment.objects.create(user=self.user1, role=self.role, assigned_by=self.admin)

    def test_get_role_members_as_admin(self):
        self.client.login(email='admin_members@example.com', password=self.password)
        url = reverse('admin_panel:role_members', kwargs={'pk': self.role.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'admin_panel/roles/role_members.html')
        self.assertIn('members', response.context)
        self.assertIn('non_members', response.context)

        # user1 should be in members, user2 should be in non_members
        member_users = list(response.context['members'])
        non_member_profiles = list(response.context['non_members'])

        self.assertIn(self.user1, member_users)
        self.assertNotIn(self.user2, member_users)
        self.assertIn(self.emp2, non_member_profiles)
        self.assertNotIn(self.emp1, non_member_profiles)

    def test_post_add_members(self):
        self.client.login(email='admin_members@example.com', password=self.password)
        url = reverse('admin_panel:role_members', kwargs={'pk': self.role.pk})
        response = self.client.post(url, {
            'action': 'add',
            'user_ids': [self.user2.pk]
        })
        self.assertRedirects(response, url)
        self.assertTrue(UserRoleAssignment.objects.filter(user=self.user2, role=self.role).exists())

    def test_post_add_duplicate_member_is_noop(self):
        self.client.login(email='admin_members@example.com', password=self.password)
        url = reverse('admin_panel:role_members', kwargs={'pk': self.role.pk})
        # Add user1 which is already assigned
        response = self.client.post(url, {
            'action': 'add',
            'user_ids': [self.user1.pk, self.user2.pk]
        })
        self.assertRedirects(response, url)
        # Should have exactly 2 assignments total for this role
        self.assertEqual(UserRoleAssignment.objects.filter(role=self.role).count(), 2)

    def test_post_remove_member(self):
        self.client.login(email='admin_members@example.com', password=self.password)
        url = reverse('admin_panel:role_members', kwargs={'pk': self.role.pk})
        response = self.client.post(url, {
            'action': 'remove',
            'user_id': self.user1.pk
        })
        self.assertRedirects(response, url)
        self.assertFalse(UserRoleAssignment.objects.filter(user=self.user1, role=self.role).exists())

    def test_non_admin_access_forbidden(self):
        self.client.login(email='staff_members@example.com', password=self.password)
        url = reverse('admin_panel:role_members', kwargs={'pk': self.role.pk})
        response = self.client.get(url)
        # AdminRequiredMixin returns 403 or redirects unprivileged users
        self.assertIn(response.status_code, [302, 403])
