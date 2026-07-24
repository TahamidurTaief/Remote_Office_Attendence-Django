from django.test import TestCase
from django.template.loader import render_to_string


class UniversalButtonTest(TestCase):
    """Test suite for the Button component (cotton/button.html)."""

    def test_button_variants(self):
        """Verify button variants render correctly."""
        variants = ['primary', 'secondary', 'ghost', 'danger', 'accent']
        for v in variants:
            rendered = render_to_string('cotton/button.html', {'label': 'TestLabel', 'variant': v})
            self.assertIn('TestLabel', rendered)
            self.assertIn(f'ft-btn-{v}', rendered)

    def test_button_sizes(self):
        """Verify button sizes: sm, md, lg."""
        sizes = ['sm', 'md', 'lg']
        for s in sizes:
            rendered = render_to_string('cotton/button.html', {'label': 'SizeTest', 'size': s})
            self.assertIn('SizeTest', rendered)
            self.assertIn(f'ft-btn-{s}', rendered)

    def test_button_loading_state(self):
        """Verify loading spinner rendering and disabled state when loading=True."""
        rendered = render_to_string('cotton/button.html', {'label': 'Submit', 'loading': True})
        self.assertIn('animate-spin', rendered)
        self.assertIn('disabled', rendered)

    def test_button_disabled_state(self):
        """Verify disabled attribute rendered when disabled=True."""
        rendered = render_to_string('cotton/button.html', {'label': 'Submit', 'disabled': True})
        self.assertIn('disabled', rendered)
