import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.employees.models import EmployeeDocument
from apps.notifications.models import Notification
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = 'Daily automated job to check for expiring employee documents and notify admins'

    def handle(self, *args, **options):
        today = timezone.localdate()
        thirty_days_later = today + datetime.timedelta(days=30)
        
        # Query documents expiring within 30 days
        expiring_docs = EmployeeDocument.objects.filter(
            expiry_date__gte=today,
            expiry_date__lte=thirty_days_later
        ).select_related('employee')
        
        admins = User.objects.filter(role__in=['admin', 'hr'], is_active=True)
        if not admins.exists():
            self.stdout.write(self.style.WARNING("No active Admin or HR users found to notify."))
            return

        notifications_created = 0
        for doc in expiring_docs:
            title = f"Document Expiring: {doc.employee.full_name} ({doc.document_type})"
            message = f"The {doc.document_type} for {doc.employee.full_name} expires on {doc.expiry_date}."
            
            for admin in admins:
                # Check for existing identical unread notification to prevent duplication
                exists = Notification.objects.filter(
                    recipient=admin,
                    employee=doc.employee,
                    title=title,
                    is_read=False
                ).exists()
                
                if not exists:
                    Notification.objects.create(
                        recipient=admin,
                        employee=doc.employee,
                        title=title,
                        message=message,
                        notif_type='document_expiry'
                    )
                    notifications_created += 1

        # Query documents expiring within 7 days for email escalation
        seven_days_later = today + datetime.timedelta(days=7)
        expiring_docs_7 = EmployeeDocument.objects.filter(
            expiry_date__gte=today,
            expiry_date__lte=seven_days_later
        ).select_related('employee')
        
        emails_sent = 0
        for doc in expiring_docs_7:
            subject = f"URGENT: Document Expiring in 7 Days: {doc.employee.full_name}"
            message = (
                f"Escalation Alert:\n\n"
                f"The document '{doc.document_type}' for employee {doc.employee.full_name} "
                f"is expiring on {doc.expiry_date} (within 7 days).\n\n"
                f"Please update the document immediately.\n\n"
                f"Regards,\nFieldTrack System"
            )
            for admin in admins:
                from apps.notifications.dispatch import send_email_notification
                send_email_notification(admin, subject, message)
                emails_sent += 1

        self.stdout.write(self.style.SUCCESS(
            f"Document expiry check complete. Created {notifications_created} notifications. Sent {emails_sent} escalation emails."
        ))
