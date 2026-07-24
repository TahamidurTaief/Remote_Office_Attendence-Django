from django.test import TestCase
from django.template.loader import render_to_string


class UniversalSelectTest(TestCase):
    """Test suite for the Select component (cotton/select.html)."""

    def test_select_template_renders(self):
        """Verify single select rendering with label and name."""
        rendered = render_to_string('cotton/select.html', {
            'name': 'category',
            'label': 'Category Select',
        })
        self.assertIn('Category Select', rendered)
        self.assertIn('name="category"', rendered)
        self.assertIn('Select option...', rendered)

    def test_select_dropdown_renders(self):
        """Verify select dropdown structure is present."""
        rendered = render_to_string('cotton/select.html', {
            'name': 'tags',
        })
        self.assertIn('filteredOptions', rendered)
        self.assertIn('selectOption', rendered)
