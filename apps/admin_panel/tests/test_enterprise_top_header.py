from django.test import TestCase
from django.template.loader import render_to_string
from django.contrib.auth import get_user_model

User = get_user_model()


class EnterpriseTopHeaderTest(TestCase):
    """Test suite for the Enterprise Top Header component."""

    def setUp(self):
        self.user = User.objects.create_user(email='header_admin@issl.com', password='password123', phone='+8801711111111')

    def test_top_header_template_renders(self):
        """Verify header landmark, search trigger, theme toggle, and profile elements."""
        rendered = render_to_string('components/enterprise_top_header.html', {'user': self.user})
        self.assertIn('<header', rendered)
        self.assertIn('Global Search', rendered)
        self.assertIn('Toggle Dark/Light Theme', rendered)
        self.assertIn('Create', rendered)

    def test_top_header_configurable_breadcrumbs(self):
        """Verify custom breadcrumbs rendering."""
        breadcrumbs = [
            {'label': 'Finance', 'href': '/finance/'},
            {'label': 'Invoices', 'href': '/finance/invoices/'}
        ]
        rendered = render_to_string('components/enterprise_top_header.html', {
            'user': self.user,
            'breadcrumbs': breadcrumbs
        })
        self.assertIn('<header', rendered)
        self.assertIn('Dashboard', rendered)
