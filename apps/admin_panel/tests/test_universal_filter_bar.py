from django.test import TestCase, RequestFactory
from django.template.loader import render_to_string


class UniversalFilterBarTest(TestCase):
    """Test suite for the Universal Filter Bar component."""

    def setUp(self):
        self.rf = RequestFactory()

    def test_filter_bar_template_renders(self):
        """Verify search input, status, branch, department, and reset controls."""
        request = self.rf.get('/')
        rendered = render_to_string('components/universal_filter_bar.html', {'request': request})
        self.assertIn('name="q"', rendered)
        self.assertIn('All Statuses', rendered)
        self.assertIn('All Branches', rendered)
        self.assertIn('All Departments', rendered)
        self.assertIn('resetFilters()', rendered)
        self.assertIn('start_date', rendered)

    def test_filter_bar_active_tags(self):
        """Verify removable active filter tags section."""
        request = self.rf.get('/')
        rendered = render_to_string('components/universal_filter_bar.html', {'request': request})
        self.assertIn('activeFilterCount', rendered)
        self.assertIn('Active:', rendered)
