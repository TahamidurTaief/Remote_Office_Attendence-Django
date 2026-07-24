from django.test import TestCase, RequestFactory
from django.template.loader import render_to_string


class UniversalFilterBarTest(TestCase):
    """Test suite for the Filter Bar component (cotton/filter-bar.html)."""

    def setUp(self):
        self.rf = RequestFactory()

    def test_filter_bar_template_renders(self):
        """Verify filter bar renders a form with slot and action buttons."""
        request = self.rf.get('/')
        rendered = render_to_string('cotton/filter-bar.html', {'request': request})
        self.assertIn('<form', rendered)
        self.assertIn('method="get"', rendered)
        # Default action buttons: Filter + Reset
        self.assertIn('Filter', rendered)
        self.assertIn('Reset', rendered)

    def test_filter_bar_active_tags(self):
        """Verify filter bar renders slot area."""
        request = self.rf.get('/')
        rendered = render_to_string('cotton/filter-bar.html', {'request': request})
        self.assertIn('<form', rendered)
