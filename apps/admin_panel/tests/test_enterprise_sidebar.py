from django.test import TestCase
from django.template.loader import render_to_string
from django.contrib.auth import get_user_model

User = get_user_model()


class EnterpriseSidebarTest(TestCase):
    """Test suite for the Enterprise Sidebar component."""

    def setUp(self):
        self.user = User.objects.create_user(email='admin@issl.com', password='password123', phone='+8801700000000')

    def test_sidebar_template_renders(self):
        """Verify that enterprise_sidebar.html renders logo and navigation landmarks."""
        rendered = render_to_string('components/enterprise_sidebar.html', {'user': self.user})
        self.assertIn('id="enterprise-sidebar"', rendered)
        self.assertIn('ISSL', rendered)

    def test_sidebar_config_driven_groups(self):
        """Verify dynamic config-driven navigation rendering."""
        nav_config = [
            {
                'title': 'Custom ERP Group',
                'items': [
                    {'id': 'custom1', 'label': 'Custom Module', 'href': '/custom/'}
                ]
            }
        ]
        rendered = render_to_string('components/enterprise_sidebar.html', {'nav_groups': nav_config, 'user': self.user})
        self.assertIn('Custom ERP Group', rendered)
        self.assertIn('Custom Module', rendered)
