from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta, date
from django.core.management import call_command

from apps.workflow.models import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowDelegation,
    WorkflowInstance,
    WorkflowAction
)
from apps.workflow.services import resolve_approver, record_action, escalate_instance
from apps.notifications.models import ActivityLog, Notification

User = get_user_model()

class WorkflowServicesTestCase(TestCase):
    def setUp(self):
        # Create roles/users
        self.staff_user = User.objects.create_user(
            email='staff@example.com',
            password='Password123!',
            role='staff'
        )
        self.manager_user = User.objects.create_user(
            email='manager@example.com',
            password='Password123!',
            role='manager'
        )
        self.delegate_user = User.objects.create_user(
            email='delegate@example.com',
            password='Password123!',
            role='manager'
        )
        self.admin_user = User.objects.create_user(
            email='admin_wf@example.com',
            password='Password123!',
            role='admin'
        )

        # Definitions
        self.definition = WorkflowDefinition.objects.create(
            code='LEAVE_APPROVAL',
            module='leave',
            name='Leave Approval'
        )
        self.step1 = WorkflowStep.objects.create(
            workflow=self.definition,
            step_number=1,
            name='Manager Review',
            from_status='pending',
            to_status='manager_approved',
            approver_role='manager',
            sla_hours=24,
            escalation_role='admin'
        )

    def test_resolve_approver_active_delegation(self):
        """Active delegation within range redirects expected approver role to delegate."""
        # Create active delegation from manager to delegate
        WorkflowDelegation.objects.create(
            from_user=self.manager_user,
            to_user=self.delegate_user,
            workflow_code='LEAVE_APPROVAL',
            start_date=date.today(),
            end_date=date.today() + timedelta(days=2),
            is_active=True
        )

        instance = WorkflowInstance.objects.create(
            definition=self.definition,
            object_type='leave_request',
            object_id='101',
            initiated_by=self.staff_user
        ).start_workflow()

        resolved = resolve_approver(self.step1, instance)
        self.assertEqual(resolved, self.delegate_user)

    def test_resolve_approver_expired_delegation(self):
        """Expired or inactive delegation does not resolve to the delegate."""
        WorkflowDelegation.objects.create(
            from_user=self.manager_user,
            to_user=self.delegate_user,
            workflow_code='LEAVE_APPROVAL',
            start_date=date.today() - timedelta(days=5),
            end_date=date.today() - timedelta(days=1),
            is_active=True
        )

        instance = WorkflowInstance.objects.create(
            definition=self.definition,
            object_type='leave_request',
            object_id='101',
            initiated_by=self.staff_user
        ).start_workflow()

        resolved = resolve_approver(self.step1, instance)
        self.assertIsNone(resolved)

    def test_record_action_delegation_audit(self):
        """Approving via a delegated user records delegated_by relationship in WorkflowAction."""
        WorkflowDelegation.objects.create(
            from_user=self.manager_user,
            to_user=self.delegate_user,
            workflow_code='LEAVE_APPROVAL',
            start_date=date.today(),
            end_date=date.today() + timedelta(days=2),
            is_active=True
        )

        instance = WorkflowInstance.objects.create(
            definition=self.definition,
            object_type='leave_request',
            object_id='101',
            initiated_by=self.staff_user
        ).start_workflow()

        action = record_action(instance, self.delegate_user, 'approve', 'Approved on behalf of manager')
        self.assertEqual(action.actor, self.delegate_user)
        self.assertEqual(action.delegated_by, self.manager_user)
        self.assertEqual(instance.current_status, 'manager_approved')

    def test_sla_escalation_triggers(self):
        """Management command escalates breached SLA, transitions status, and notifies escalation role."""
        instance = WorkflowInstance.objects.create(
            definition=self.definition,
            object_type='leave_request',
            object_id='101',
            initiated_by=self.staff_user
        ).start_workflow()

        instance.sla_deadline = timezone.now() - timedelta(hours=2)
        instance.save()

        # Run command
        call_command('check_workflow_sla')

        instance.refresh_from_db()
        self.assertEqual(instance.current_status, 'escalated')
        
        self.assertTrue(WorkflowAction.objects.filter(instance=instance, action='delegate').exists())

        self.assertTrue(ActivityLog.objects.filter(verb='task_delayed').exists())
        self.assertTrue(Notification.objects.filter(recipient=self.admin_user, notif_type='task_delayed').exists())

    def test_sla_escalation_no_double_escalate(self):
        """Management command does not escalate already escalated instances."""
        instance = WorkflowInstance.objects.create(
            definition=self.definition,
            object_type='leave_request',
            object_id='101',
            initiated_by=self.staff_user
        ).start_workflow()

        instance.sla_deadline = timezone.now() - timedelta(hours=2)
        instance.current_status = 'escalated'
        instance.save()

        # Run command
        call_command('check_workflow_sla')

        self.assertFalse(WorkflowAction.objects.filter(instance=instance, action='delegate').exists())
