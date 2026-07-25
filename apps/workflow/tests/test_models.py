from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta, date

from apps.workflow.models import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowDelegation,
    WorkflowInstance,
    WorkflowAction
)

User = get_user_model()


class WorkflowModelTestCase(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(
            email='user1@example.com',
            password='Password123!',
            role='staff'
        )
        self.user2 = User.objects.create_user(
            email='user2@example.com',
            password='Password123!',
            role='manager'
        )
        self.definition = WorkflowDefinition.objects.create(
            code='LEAVE_APPROVAL',
            module='leave',
            name='Leave Approval Workflow',
            description='Standard 2-step leave approval process'
        )
        self.step1 = WorkflowStep.objects.create(
            workflow=self.definition,
            step_number=1,
            name='Manager Review',
            from_status='pending',
            to_status='manager_approved',
            approver_role='manager',
            sla_hours=24,
            escalation_role='admin',
            allow_return=True,
            allow_rejection=True
        )
        self.step2 = WorkflowStep.objects.create(
            workflow=self.definition,
            step_number=2,
            name='HR Final Approval',
            from_status='manager_approved',
            to_status='approved',
            approver_role='hr',
            sla_hours=48,
            allow_return=True,
            allow_rejection=True
        )

    def test_workflow_definition_and_step_creation(self):
        self.assertEqual(self.definition.steps.count(), 2)
        self.assertEqual(self.step1.approver_role, 'manager')
        self.assertEqual(self.step1.sla_hours, 24)

    def test_workflow_instance_start_and_sla_deadline(self):
        instance = WorkflowInstance.objects.create(
            definition=self.definition,
            object_type='leave_request',
            object_id='101',
            initiated_by=self.user1
        )
        self.assertIsNone(instance.sla_deadline)

        # Call start_workflow()
        before_start = timezone.now()
        instance.start_workflow()
        after_start = timezone.now()

        self.assertEqual(instance.current_step, 1)
        self.assertEqual(instance.current_status, 'pending')
        self.assertIsNotNone(instance.sla_deadline)

        expected_deadline_min = before_start + timedelta(hours=24)
        expected_deadline_max = after_start + timedelta(hours=24)
        self.assertTrue(expected_deadline_min <= instance.sla_deadline <= expected_deadline_max)

    def test_workflow_delegation(self):
        delegation = WorkflowDelegation.objects.create(
            from_user=self.user1,
            to_user=self.user2,
            workflow_code='LEAVE_APPROVAL',
            start_date=date.today(),
            end_date=date.today() + timedelta(days=7),
            reason='Out of office on leave'
        )
        self.assertTrue(delegation.is_active)
        self.assertEqual(delegation.from_user, self.user1)

    def test_workflow_action(self):
        instance = WorkflowInstance.objects.create(
            definition=self.definition,
            object_type='leave_request',
            object_id='102',
            initiated_by=self.user1
        ).start_workflow()

        action = WorkflowAction.objects.create(
            instance=instance,
            step_number=1,
            actor=self.user2,
            action='approve',
            note='Looks good to me'
        )
        self.assertEqual(instance.actions.count(), 1)
        self.assertEqual(action.action, 'approve')
