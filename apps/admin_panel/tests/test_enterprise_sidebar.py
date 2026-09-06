from django.test import TestCase
from django.template.loader import render_to_string
from django.contrib.auth import get_user_model

User = get_user_model()


class EnterpriseSidebarTest(TestCase):
    """Test suite for the Sidebar component (cotton/sidebar.html)."""

    def setUp(self):
        self.user = User.objects.create_user(email='admin@issl.com', password='password123', phone='+8801700000000')

    def test_sidebar_template_renders(self):
        """Verify that sidebar.html renders the sidebar aside and nav landmarks."""
        from django.test import RequestFactory
        rf = RequestFactory().get('/')
        rf.user = self.user
        rendered = render_to_string('cotton/sidebar.html', {'user': self.user}, request=rf)
        self.assertIn('id="ft-sidebar"', rendered)
        self.assertIn('aria-label="Main Navigation"', rendered)

    def test_sidebar_config_driven_groups(self):
        """Verify dynamic nav labels exist in the sidebar."""
        from django.test import RequestFactory
        rf = RequestFactory().get('/')
        rf.user = self.user
        rendered = render_to_string('cotton/sidebar.html', {'user': self.user}, request=rf)
        # Sidebar contains navigation link labels
        self.assertIn('Dashboard', rendered)
        self.assertIn('Employees', rendered)
