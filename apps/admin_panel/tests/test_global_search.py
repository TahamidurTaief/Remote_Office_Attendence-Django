from django.test import TestCase
from django.template.loader import render_to_string


class GlobalSearchTest(TestCase):
    """Test suite for the Global Search component."""

    def test_global_search_template_renders(self):
        """Verify cross-module global search tabs and search input."""
        rendered = render_to_string('components/global_search.html', {})
        self.assertIn('Customer', rendered)
        self.assertIn('Invoice', rendered)
        self.assertIn('Employee', rendered)
        self.assertIn('Product', rendered)
        self.assertIn('Project', rendered)
        self.assertIn('Workflow', rendered)
        self.assertIn('Search Customers, Invoices, Employees', rendered)
