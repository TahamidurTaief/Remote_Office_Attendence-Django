from django.test import TestCase
from django.template.loader import render_to_string
from django.contrib.auth import get_user_model

User = get_user_model()


class EnterpriseAppShellTest(TestCase):
    """Test suite for the App Shell component (cotton/app-shell.html)."""

    def setUp(self):
        self.user = User.objects.create_user(email='admin@issl.com', password='password123', phone='+8801700000000')

    def _make_request(self):
        from django.test import RequestFactory
        rf = RequestFactory()
        req = rf.get('/')
        req.user = self.user
        return req

    def test_app_shell_template_renders_slots(self):
        """Verify that app-shell.html renders required layout landmarks."""
        rendered = render_to_string('cotton/app-shell.html', {'user': self.user, 'request': self._make_request()})
        self.assertIn('id="ft-main-content"', rendered)
        self.assertIn('<main', rendered)
        self.assertIn('ft-page-bg', rendered)

    def test_app_shell_includes_mobile_bottom_nav(self):
        """Verify that sidebar mobile nav is present in the shell."""
        rendered = render_to_string('cotton/app-shell.html', {'user': self.user, 'request': self._make_request()})
        self.assertIn('lg:hidden', rendered)
        self.assertIn('id="ft-sidebar"', rendered)

    def test_app_shell_includes_toast_and_drawers(self):
        """Verify global toast and notification drawer elements are present."""
        rendered = render_to_string('cotton/app-shell.html', {'user': self.user, 'request': self._make_request()})
        self.assertIn('new-toast', rendered)
        self.assertIn('open-command-palette', rendered)
        self.assertIn('notifications-drawer', rendered)

    def test_staff_shell_renders_staff_navigation_without_admin_menu(self):
        """Verify that shell_type='staff' renders staff app navigation and no admin sections."""
        rendered = render_to_string(
            'cotton/app-shell.html',
            {'user': self.user, 'request': self._make_request(), 'shell_type': 'staff'}
        )
        # Staff specific links
        self.assertIn('/staff/home/', rendered)
        self.assertIn('/staff/check-in/', rendered)
        self.assertIn('/staff/my-tasks/', rendered)
        self.assertIn('Staff Workspace', rendered)
        self.assertIn('STAFF', rendered)

        # Must NOT contain admin-only sections/menus
        self.assertNotIn('Executive Dashboard', rendered)
        self.assertNotIn('Employee Directory', rendered)
        self.assertNotIn('System Roles & Access', rendered)
        self.assertNotIn('Salary Components', rendered)

    def test_admin_shell_renders_admin_menu(self):
        """Verify that default admin shell renders admin menu items."""
        rendered = render_to_string(
            'cotton/app-shell.html',
            {'user': self.user, 'request': self._make_request(), 'shell_type': 'admin'}
        )
        self.assertIn('Executive Dashboard', rendered)
        self.assertIn('Employee Directory', rendered)
        self.assertIn('System Roles', rendered)

