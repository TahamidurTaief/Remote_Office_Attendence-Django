from django.test import TestCase
from django.template.loader import render_to_string
from django.contrib.auth import get_user_model

User = get_user_model()


class EnterpriseTopHeaderTest(TestCase):
    """Test suite for the Topbar component (cotton/topbar.html)."""

    def setUp(self):
        self.user = User.objects.create_user(email='header_admin@issl.com', password='password123', phone='+8801711111111')

    def _make_request(self):
        from django.test import RequestFactory
        rf = RequestFactory()
        req = rf.get('/')
        req.user = self.user
        return req

    def test_top_header_template_renders(self):
        """Verify header landmark, search trigger, theme toggle, and profile elements."""
        rendered = render_to_string('cotton/topbar.html', {'user': self.user, 'request': self._make_request()})
        self.assertIn('<header', rendered)
        # topbar contains command palette trigger (search)
        self.assertIn('open-command-palette', rendered)
        # topbar contains theme toggle (cotton renders it inline)
        self.assertIn('ft_theme', rendered)

    def test_top_header_configurable_breadcrumbs(self):
        """Verify breadcrumbs render inside the topbar header."""
        breadcrumbs = [
            {'label': 'Finance', 'href': '/finance/'},
            {'label': 'Invoices', 'href': '/finance/invoices/'}
        ]
        rendered = render_to_string('cotton/topbar.html', {
            'user': self.user,
            'request': self._make_request(),
            'breadcrumb': breadcrumbs
        })
        self.assertIn('<header', rendered)
        self.assertIn('Finance', rendered)
        self.assertIn('Invoices', rendered)
