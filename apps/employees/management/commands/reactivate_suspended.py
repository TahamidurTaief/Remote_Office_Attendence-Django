import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.employees.models import Employee, EmployeeSuspension, EmployeeStatus, EmployeeActivityLog, EmployeeAuditLog, EmploymentHistory
from apps.notifications.models import log_audit

class Command(BaseCommand):
    help = 'Safely reactivates suspended employees when suspension expiry passes and auto_reactivate is True.'

    def handle(self, *args, **options):
        today = timezone.localdate()
        active_suspensions = EmployeeSuspension.objects.filter(
            is_active=True,
            auto_reactivate=True,
            suspension_end_date__lte=today
        ).select_related('employee')

        count = 0
        for suspension in active_suspensions:
            employee = suspension.employee
            
            # Deactivate suspension
            suspension.is_active = False
            suspension.save()

            # Restore employee state
            old_status = employee.status
            target_status = suspension.previous_status or EmployeeStatus.ACTIVE
            if target_status == EmployeeStatus.SUSPENDED:
                target_status = EmployeeStatus.ACTIVE
                
            employee.status = target_status
            employee.is_suspended = False
            employee._bypass_lifecycle_validation = True
            employee.save()

            # Audit history logging
            EmploymentHistory.objects.create(
                employee=employee,
                field_changed='status',
                old_value=dict(EmployeeStatus.choices).get(old_status, old_status),
                new_value=dict(EmployeeStatus.choices).get(target_status, target_status),
                reason=f"Auto-reactivated following suspension expiry on {suspension.suspension_end_date}",
                approved_by=None,
                effective_date=today,
            )

            EmployeeActivityLog.objects.create(
                employee=employee,
                actor=None,
                action_description=f"Auto-reactivated following suspension expiry. Reason: {suspension.suspension_reason}",
                field_changed='status'
            )

            EmployeeAuditLog.objects.create(
                employee=employee,
                old_value={'status': old_status, 'is_suspended': True},
                new_value={'status': target_status, 'is_suspended': False},
                changed_by=None
            )

            log_audit(
                actor=None,
                action='lifecycle_transition_applied',
                target=employee,
                summary=f"Auto-reactivated: {old_status} → {target_status}"
            )
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Safely reactivated {count} employees."))
