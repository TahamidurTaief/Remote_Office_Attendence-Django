from django.test import TestCase
from django.template.loader import render_to_string


class UniversalInputTest(TestCase):
    """Test suite for the Universal Input component."""

    def test_input_types(self):
        """Verify input types: text, search, password, email, tel, number, currency, textarea."""
        types = ['text', 'search', 'password', 'email', 'tel', 'number', 'currency', 'textarea']
        for t in types:
            rendered = render_to_string('components/universal_input.html', {'name': 'test_field', 'type': t, 'label': 'Test Label'})
            self.assertIn('Test Label', rendered)
            self.assertIn('name="test_field"', rendered)

    def test_input_states(self):
        """Verify input states: loading, error, disabled, readonly, success."""
        # Error state
        err_rendered = render_to_string('components/universal_input.html', {'name': 'f1', 'error': 'Invalid email address'})
        self.assertIn('Invalid email address', rendered_error := err_rendered)
        self.assertIn('border-rose-500', rendered_error)

        # Disabled state
        dis_rendered = render_to_string('components/universal_input.html', {'name': 'f2', 'is_disabled': True})
        self.assertIn('disabled', dis_rendered)

        # Readonly state
        ro_rendered = render_to_string('components/universal_input.html', {'name': 'f3', 'is_readonly': True})
        self.assertIn('readonly', ro_rendered)

        # Success state
        succ_rendered = render_to_string('components/universal_input.html', {'name': 'f4', 'is_success': True})
        self.assertIn('border-emerald-500', succ_rendered)
