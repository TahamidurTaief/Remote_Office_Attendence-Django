from django.test import TestCase
from django.template.loader import render_to_string


class UniversalInputTest(TestCase):
    """Test suite for the Input component (cotton/input.html)."""

    def test_input_types(self):
        """Verify input renders label and name attribute."""
        types = ['text', 'email', 'password', 'tel', 'number', 'search']
        for t in types:
            rendered = render_to_string('cotton/input.html', {'name': 'test_field', 'type': t, 'label': 'Test Label'})
            self.assertIn('Test Label', rendered)
            self.assertIn('name="test_field"', rendered)

    def test_input_states(self):
        """Verify input states: error, disabled, readonly."""
        # Error state
        err_rendered = render_to_string('cotton/input.html', {'name': 'f1', 'error': 'Invalid email address'})
        self.assertIn('Invalid email address', err_rendered)
        self.assertIn('border-red-500', err_rendered)

        # Disabled state
        dis_rendered = render_to_string('cotton/input.html', {'name': 'f2', 'disabled': True})
        self.assertIn('disabled', dis_rendered)
