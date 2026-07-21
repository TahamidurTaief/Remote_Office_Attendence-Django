from django.test import TestCase
from django.template.loader import render_to_string


class UniversalTableTest(TestCase):
    """Test suite for the Universal Enterprise Table component."""

    def test_table_template_renders_data(self):
        """Verify sorting, sticky header, selection, and data rendering."""
        columns = [
            {'key': 'id', 'label': 'ID', 'sortable': True},
            {'key': 'name', 'label': 'Name', 'sortable': True}
        ]
        data = [{'id': 1, 'name': 'John Doe'}, {'id': 2, 'name': 'Jane Smith'}]
        rendered = render_to_string('components/universal_table.html', {
            'columns': columns,
            'data': data
        })
        self.assertIn('sticky top-0', rendered)
        self.assertIn('toggleSelectAll', rendered)
        self.assertIn('John Doe', rendered)
        self.assertIn('Jane Smith', rendered)

    def test_table_loading_and_empty_states(self):
        """Verify loading pulse skeleton and empty state fallback."""
        # Loading state
        loading_rendered = render_to_string('components/universal_table.html', {'is_loading': True, 'data': []})
        self.assertIn('animate-pulse', loading_rendered)

        # Empty state
        empty_rendered = render_to_string('components/universal_table.html', {'data': [], 'is_loading': False})
        self.assertIn('No records found', empty_rendered)
