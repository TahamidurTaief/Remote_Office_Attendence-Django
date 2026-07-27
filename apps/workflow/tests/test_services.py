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

    def test_workflow_timeline_and_history_and_immutability(self):
        """
        Tests history is complete and in order for a multi-step Leave request (approve -> return -> resubmit -> approve -> approve).
        Tests timeline correctly marks step status (completed/current/pending).
        Tests immutability check confirms no WorkflowAction row is ever updated after creation (only inserted).
        """
        from apps.leave.models import LeaveRequest, LeaveType
        from apps.employees.models import EmployeeProfile
        from apps.branches.models import Branch
        from apps.workflow.services import get_workflow_history, get_workflow_timeline
        from django.core.exceptions import ValidationError

        # Ensure seed definitions are loaded
        call_command('seed_workflow_definitions')

        branch = Branch.objects.create(
            name='Test Branch for Timeline',
            latitude=23.81,
            longitude=90.41,
            radius_meters=100
        )
        
        # Setup actors
        staff_u = User.objects.create_user(phone='+8801799999991', password='pass', role='staff')
        mgr_u = User.objects.create_user(phone='+8801799999992', password='pass', role='manager')
        hr_u = User.objects.create_user(phone='+8801799999993', password='pass', role='hr')

        emp = EmployeeProfile.objects.create(
            user=staff_u,
            employee_id='EMP-TEST-TL',
            full_name='Timeline Tester',
            phone='+8801799999991',
            joined_date=date(2026, 1, 1),
            branch=branch,
            is_active=True
        )

        lt = LeaveType.objects.create(name='Annual', default_days_per_year=20)

        # 1. Create a Leave Request -> automatically triggers signal to create & start workflow
        req = LeaveRequest.objects.create(
            employee=emp,
            leave_type=lt,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 5),
            reason='Vacation'
        )

        wf_instance = req.workflow_instance
        self.assertIsNotNone(wf_instance)
        self.assertEqual(wf_instance.current_step, 1)
        self.assertEqual(wf_instance.current_status, 'pending')

        # Check initial timeline
        tl = get_workflow_timeline(req)
        self.assertEqual(len(tl), 2)
        self.assertEqual(tl[0]['status'], 'current') # Manager Review
        self.assertEqual(tl[1]['status'], 'pending') # HR Final Approval

        # 2. Approve at Step 1 (Manager Review)
        record_action(wf_instance, mgr_u, 'approve', 'Approved by manager')
        wf_instance.refresh_from_db()
        self.assertEqual(wf_instance.current_step, 2)
        self.assertEqual(wf_instance.current_status, 'manager_approved')

        tl = get_workflow_timeline(req)
        self.assertEqual(tl[0]['status'], 'completed')
        self.assertEqual(tl[1]['status'], 'current')

        # 3. Return at Step 2 (HR Review) to initiator
        record_action(wf_instance, hr_u, 'return', 'Returned by HR', return_to_initiator=True)
        wf_instance.refresh_from_db()
        self.assertEqual(wf_instance.current_status, 'returned')

        # 4. Resubmit -> simulate resubmission by resetting to step 1 pending
        wf_instance.current_step = 1
        wf_instance.current_status = 'pending'
        wf_instance.save()

        # 5. Approve at Step 1 again
        record_action(wf_instance, mgr_u, 'approve', 'Approved by manager again')
        wf_instance.refresh_from_db()
        self.assertEqual(wf_instance.current_step, 2)
        self.assertEqual(wf_instance.current_status, 'manager_approved')

        # 6. Approve at Step 2 (HR Final Approval)
        record_action(wf_instance, hr_u, 'approve', 'Approved by HR finally')
        wf_instance.refresh_from_db()
        self.assertEqual(wf_instance.current_status, 'approved')
        self.assertIsNotNone(wf_instance.completed_at)

        # Verification of History
        history = get_workflow_history(req)
        self.assertEqual(len(history), 4) # approve -> return -> approve -> approve
        self.assertEqual(history[0].action, 'approve')
        self.assertEqual(history[1].action, 'return')
        self.assertEqual(history[2].action, 'approve')
        self.assertEqual(history[3].action, 'approve')

        # Verification of Timeline after completion
        tl = get_workflow_timeline(req)
        self.assertEqual(tl[0]['status'], 'completed')
        self.assertEqual(tl[1]['status'], 'completed')
        self.assertEqual(len(tl[0]['actions']), 2) # two approves for step 1
        self.assertEqual(len(tl[1]['actions']), 2) # one return + one approve for step 2

        # 7. Immutability check: verify updating any WorkflowAction throws
        action = history[0]
        action.note = "Try to update"
        with self.assertRaises(ValidationError):
            action.save()

        with self.assertRaises(ValidationError):
            action.delete()

