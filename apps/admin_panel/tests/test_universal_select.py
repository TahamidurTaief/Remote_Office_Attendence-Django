from django.test import TestCase
from django.template.loader import render_to_string


class UniversalSelectTest(TestCase):
    """Test suite for the Universal Select component."""

    def test_select_template_renders(self):
        """Verify single select rendering."""
        options = [{'value': 'val1', 'label': 'Option 1'}, {'value': 'val2', 'label': 'Option 2'}]
        rendered = render_to_string('components/universal_select.html', {
            'name': 'category',
            'label': 'Category Select',
            'options': options
        })
        self.assertIn('Category Select', rendered)
        self.assertIn('name="category"', rendered)
        self.assertIn('Select option...', rendered)

    def test_select_multi_and_create(self):
        """Verify multi-select and option creation tags."""
        rendered = render_to_string('components/universal_select.html', {
            'name': 'tags',
            'is_multi': True,
            'can_create': True
        })
        self.assertIn('createNewOption()', rendered)
        self.assertIn('selectedValues.includes(opt.value)', rendered)
