from django.db import migrations


def migrate_schedule_alerts_to_events(apps, schema_editor):
    Notification = apps.get_model('notifications', 'Notification')
    Notification.objects.filter(notif_type='schedule_alert').update(notif_type='schedule_event')


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0008_alter_notification_notif_type'),
    ]

    operations = [
        migrations.RunPython(
            migrate_schedule_alerts_to_events,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
