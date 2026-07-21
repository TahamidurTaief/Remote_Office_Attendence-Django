from django.test import TestCase
from django.template.loader import render_to_string


class UniversalButtonTest(TestCase):
    """Test suite for the Universal Button component."""

    def test_button_variants(self):
        """Verify button variants: primary, secondary, outline, ghost, danger, success, link, icon."""
        variants = ['primary', 'secondary', 'outline', 'ghost', 'danger', 'success', 'link', 'icon']
        for v in variants:
            rendered = render_to_string('components/universal_button.html', {'label': 'TestLabel', 'variant': v})
            if v == 'icon':
                self.assertIn('button', rendered)
            else:
                self.assertIn('TestLabel', rendered)

    def test_button_sizes(self):
        """Verify button sizes: xs, sm, md, lg."""
        sizes = ['xs', 'sm', 'md', 'lg']
        for s in sizes:
            rendered = render_to_string('components/universal_button.html', {'label': 'SizeTest', 'size': s})
            self.assertIn('SizeTest', rendered)

    def test_button_loading_state(self):
        """Verify loading spinner rendering and disabled state when is_loading=True."""
        rendered = render_to_string('components/universal_button.html', {'label': 'Submit', 'is_loading': True})
        self.assertIn('animate-spin', rendered)
        self.assertIn('disabled', rendered)

    def test_button_disabled_state(self):
        """Verify disabled styling and attribute when is_disabled=True."""
        rendered = render_to_string('components/universal_button.html', {'label': 'Submit', 'is_disabled': True})
        self.assertIn('disabled', rendered)
        self.assertIn('opacity-50', rendered)
