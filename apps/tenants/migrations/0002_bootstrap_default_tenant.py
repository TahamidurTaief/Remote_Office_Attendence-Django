from django.db import migrations


def create_default_tenant(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    Tenant.objects.get_or_create(
        slug='signtech',
        defaults={
            'name': 'Signtech',
            'status': 'active'
        }
    )


def remove_default_tenant(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    Tenant.objects.filter(slug='signtech').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_default_tenant, reverse_code=remove_default_tenant),
    ]
