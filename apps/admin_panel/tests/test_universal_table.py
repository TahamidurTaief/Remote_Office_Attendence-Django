from django.test import TestCase
from django.template.loader import render_to_string


class UniversalTableTest(TestCase):
    """Test suite for the Table component (cotton/table.html)."""

    def test_table_template_renders(self):
        """Verify table renders wrapper and table element."""
        rendered = render_to_string('cotton/table.html', {})
        self.assertIn('class="ft-table"', rendered)
        self.assertIn('<table', rendered)
        self.assertIn('overflow-x-auto', rendered)

    def test_table_renders_header_and_body_slots(self):
        """Verify table renders header and body slot areas."""
        rendered = render_to_string('cotton/table.html', {
            'header': '<th>ID</th><th>Name</th>',
            'body': '<tr><td>1</td><td>John Doe</td></tr>'
        })
        self.assertIn('ft-table', rendered)
        self.assertIn('<tbody', rendered)

    def test_table_empty_state(self):
        """Verify empty slot is rendered when provided."""
        rendered = render_to_string('cotton/table.html', {
            'empty': 'No records found'
        })
        self.assertIn('No records found', rendered)
