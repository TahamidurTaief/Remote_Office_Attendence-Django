from django.db import migrations


def run_reconciliation(apps, schema_editor):
    from apps.employees.reconciliation import reconcile_all_employee_profiles
    reconcile_all_employee_profiles()


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0030_alter_employee_payment_method'),
    ]

    operations = [
        migrations.RunPython(run_reconciliation, migrations.RunPython.noop),
    ]
