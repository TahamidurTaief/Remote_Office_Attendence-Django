import uuid
from django.db import migrations, models


def generate_uuids(apps, schema_editor):
    Employee = apps.get_model('employees', 'Employee')
    for emp in Employee.objects.all():
        emp.uuid = uuid.uuid4()
        emp.save(update_fields=['uuid'])


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0024_managerdelegation"),
    ]

    operations = [
        # Step 1: Add nullable, non-unique field
        migrations.AddField(
            model_name="employee",
            name="uuid",
            field=models.UUIDField(
                db_index=True, default=uuid.uuid4, editable=False, null=True
            ),
        ),
        # Step 2: Populate every row with a unique UUID
        migrations.RunPython(generate_uuids, migrations.RunPython.noop),
        # Step 3: Apply unique + not-null constraints
        migrations.AlterField(
            model_name="employee",
            name="uuid",
            field=models.UUIDField(
                db_index=True, default=uuid.uuid4, editable=False, unique=True
            ),
        ),
    ]
