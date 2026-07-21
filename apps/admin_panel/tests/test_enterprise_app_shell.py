from django.test import TestCase
from django.template.loader import render_to_string
from django.contrib.auth import get_user_model

User = get_user_model()


class EnterpriseAppShellTest(TestCase):
    """Test suite for the Enterprise App Shell component and templates."""

    def setUp(self):
        self.user = User.objects.create_user(email='admin@issl.com', password='password123', phone='+8801700000000')

    def test_app_shell_template_renders_slots(self):
        """Verify that enterprise_app_shell.html renders required layout slots."""
        rendered = render_to_string('components/enterprise_app_shell.html', {'user': self.user})
        self.assertIn('id="enterprise-sidebar"', rendered)
        self.assertIn('<header', rendered)
        self.assertIn('<main', rendered)
        self.assertIn('pb-16 lg:pb-0', rendered)
        self.assertIn('Enterprise App Shell Ready', rendered)

    def test_app_shell_includes_mobile_bottom_nav(self):
        """Verify that mobile bottom navigation is present."""
        rendered = render_to_string('components/enterprise_app_shell.html', {'user': self.user})
        self.assertIn('<nav class="lg:hidden fixed bottom-0', rendered)
        self.assertIn('<span>Home</span>', rendered)
        self.assertIn('<span>Alerts</span>', rendered)
        self.assertIn('<span>Menu</span>', rendered)

    def test_app_shell_includes_toast_and_drawers(self):
        """Verify global search, command palette, right drawer, and toast slots."""
        rendered = render_to_string('components/enterprise_app_shell.html', {'user': self.user})
        self.assertIn('toast.show', rendered)
        self.assertIn('commandPaletteOpen', rendered)
        self.assertIn('rightDrawerOpen', rendered)
