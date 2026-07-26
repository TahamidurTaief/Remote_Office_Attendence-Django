from django.core.management.base import BaseCommand
from apps.workflow.models import WorkflowDefinition, WorkflowStep

class Command(BaseCommand):
    help = 'Seeds workflow definitions and their steps'

    def handle(self, *args, **options):
        # Leave Approval Workflow
        leave_def, created = WorkflowDefinition.objects.update_or_create(
            code='leave_approval',
            defaults={
                'module': 'leave',
                'name': 'Leave Approval',
                'description': 'Two-step approval flow for employee leave requests (Manager -> HR)',
                'is_active': True,
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created WorkflowDefinition: leave_approval"))
        else:
            self.stdout.write(self.style.SUCCESS("Updated WorkflowDefinition: leave_approval"))

        # Step 1: Manager Approval
        step1, created1 = WorkflowStep.objects.update_or_create(
            workflow=leave_def,
            step_number=1,
            defaults={
                'name': 'Manager Review',
                'from_status': 'pending',
                'to_status': 'manager_approved',
                'approver_role': 'manager',
                'sla_hours': 24,
                'escalation_role': 'admin',
                'allow_return': True,
                'allow_rejection': True,
            }
        )
        if created1:
            self.stdout.write(self.style.SUCCESS("Created WorkflowStep 1: Manager Review"))
        else:
            self.stdout.write(self.style.SUCCESS("Updated WorkflowStep 1: Manager Review"))

        # Step 2: HR Final Approval
        step2, created2 = WorkflowStep.objects.update_or_create(
            workflow=leave_def,
            step_number=2,
            defaults={
                'name': 'HR Final Approval',
                'from_status': 'manager_approved',
                'to_status': 'approved',
                'approver_role': 'hr',
                'sla_hours': 48,
                'escalation_role': 'admin',
                'allow_return': True,
                'allow_rejection': True,
            }
        )
        if created2:
            self.stdout.write(self.style.SUCCESS("Created WorkflowStep 2: HR Final Approval"))
        else:
            self.stdout.write(self.style.SUCCESS("Updated WorkflowStep 2: HR Final Approval"))

        # Expense Approval Workflow
        expense_def, created_exp = WorkflowDefinition.objects.update_or_create(
            code='expense_approval',
            defaults={
                'module': 'expense',
                'name': 'Expense Approval',
                'description': 'Three-step approval flow for employee expense claims (Manager -> Finance -> Accounts)',
                'is_active': True,
            }
        )
        if created_exp:
            self.stdout.write(self.style.SUCCESS("Created WorkflowDefinition: expense_approval"))
        else:
            self.stdout.write(self.style.SUCCESS("Updated WorkflowDefinition: expense_approval"))

        # Step 1: Manager Review
        exp_step1, created_es1 = WorkflowStep.objects.update_or_create(
            workflow=expense_def,
            step_number=1,
            defaults={
                'name': 'Manager Review',
                'from_status': 'pending_manager',
                'to_status': 'pending_finance',
                'approver_role': 'manager',
                'sla_hours': 48,
                'escalation_role': 'admin',
                'allow_return': True,
                'allow_rejection': True,
            }
        )
        if created_es1:
            self.stdout.write(self.style.SUCCESS("Created WorkflowStep 1: Manager Review (Expense)"))
        else:
            self.stdout.write(self.style.SUCCESS("Updated WorkflowStep 1: Manager Review (Expense)"))

        # Step 2: Finance Review
        exp_step2, created_es2 = WorkflowStep.objects.update_or_create(
            workflow=expense_def,
            step_number=2,
            defaults={
                'name': 'Finance Review',
                'from_status': 'pending_finance',
                'to_status': 'pending_accounts',
                'approver_role': 'finance',
                'sla_hours': 48,
                'escalation_role': 'admin',
                'allow_return': True,
                'allow_rejection': True,
            }
        )
        if created_es2:
            self.stdout.write(self.style.SUCCESS("Created WorkflowStep 2: Finance Review (Expense)"))
        else:
            self.stdout.write(self.style.SUCCESS("Updated WorkflowStep 2: Finance Review (Expense)"))

        # Step 3: Accounts Review
        exp_step3, created_es3 = WorkflowStep.objects.update_or_create(
            workflow=expense_def,
            step_number=3,
            defaults={
                'name': 'Accounts Review',
                'from_status': 'pending_accounts',
                'to_status': 'approved',
                'approver_role': 'accounts',
                'sla_hours': 48,
                'escalation_role': 'admin',
                'allow_return': True,
                'allow_rejection': True,
            }
        )
        if created_es3:
            self.stdout.write(self.style.SUCCESS("Created WorkflowStep 3: Accounts Review (Expense)"))
        else:
            self.stdout.write(self.style.SUCCESS("Updated WorkflowStep 3: Accounts Review (Expense)"))
