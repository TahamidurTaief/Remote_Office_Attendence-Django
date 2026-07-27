from django.utils import timezone
from django.db.models import Q
from apps.workflow.models import WorkflowDelegation, WorkflowAction, WorkflowStep
from apps.notifications.dispatch import log_activity

def resolve_approver(step, workflow_instance, target_date=None):
    """
    Checks active delegations for the expected approver of a step.
    Returns the delegate (User) if a valid active delegation is found,
    otherwise returns None.
    """
    if not target_date:
        target_date = timezone.localdate()

    # Find active delegations from users with the matching step.approver_role
    delegations = WorkflowDelegation.objects.filter(
        from_user__role=step.approver_role,
        is_active=True,
        start_date__lte=target_date,
        end_date__gte=target_date,
    )
    
    # Specific workflow delegation takes precedence over global (empty workflow_code)
    w_code = workflow_instance.definition.code
    del_obj = delegations.filter(workflow_code=w_code).first()
    if not del_obj:
        del_obj = delegations.filter(workflow_code='').first()

    return del_obj.to_user if del_obj else None

def record_action(instance, actor, action, note='', return_to_initiator=False):
    """
    Records a WorkflowAction for an instance.
    Checks if the action is done on behalf of a delegated user.
    """
    from apps.workflow.models import WorkflowStep
    step = WorkflowStep.objects.filter(workflow=instance.definition, step_number=instance.current_step).first()
    
    delegated_by = None
    if step:
        now_date = timezone.localdate()
        delegation = WorkflowDelegation.objects.filter(
            to_user=actor,
            from_user__role=step.approver_role,
            is_active=True,
            start_date__lte=now_date,
            end_date__gte=now_date,
        )
        
        w_code = instance.definition.code
        del_obj = delegation.filter(workflow_code=w_code).first()
        if not del_obj:
            del_obj = delegation.filter(workflow_code='').first()
            
        if del_obj:
            delegated_by = del_obj.from_user

    # Create the action
    wf_action = WorkflowAction.objects.create(
        instance=instance,
        step_number=instance.current_step,
        actor=actor,
        action=action,
        note=note,
        delegated_by=delegated_by
    )

    # Process state transition if approved
    if action == 'approve':
        next_step = WorkflowStep.objects.filter(workflow=instance.definition, step_number=instance.current_step + 1).first()
        if next_step:
            instance.current_step = next_step.step_number
            instance.current_status = next_step.from_status
            if next_step.sla_hours:
                instance.sla_deadline = timezone.now() + timezone.timedelta(hours=next_step.sla_hours)
            else:
                instance.sla_deadline = None
        else:
            instance.current_status = step.to_status
            instance.completed_at = timezone.now()
            instance.sla_deadline = None
        instance.save()
    elif action == 'reject':
        instance.current_status = 'rejected'
        instance.completed_at = timezone.now()
        instance.sla_deadline = None
        instance.save()
    elif action == 'return' and step.allow_return:
        if return_to_initiator:
            instance.current_status = 'returned'
            instance.sla_deadline = None
        else:
            prev_step = WorkflowStep.objects.filter(workflow=instance.definition, step_number=instance.current_step - 1).first()
            if prev_step:
                instance.current_step = prev_step.step_number
                instance.current_status = prev_step.from_status
                if prev_step.sla_hours:
                    instance.sla_deadline = timezone.now() + timezone.timedelta(hours=prev_step.sla_hours)
                else:
                    instance.sla_deadline = None
            else:
                instance.current_status = 'returned'
                instance.sla_deadline = None
        instance.save()

    return wf_action

def escalate_instance(instance):
    """
    Escalates an active workflow instance that has breached its SLA deadline.
    Advances the instance's state to 'escalated' and sets up the escalation action.
    """
    step = WorkflowStep.objects.filter(workflow=instance.definition, step_number=instance.current_step).first()
    if not step or not step.escalation_role:
        return None

    from django.contrib.auth import get_user_model
    User = get_user_model()
    system_user = User.objects.filter(is_superuser=True).first() or instance.initiated_by

    action = WorkflowAction.objects.create(
        instance=instance,
        step_number=instance.current_step,
        actor=system_user,
        action='delegate',
        note=f"SLA Breached. Escalated to role: {step.escalation_role}."
    )

    instance.current_status = 'escalated'
    instance.save()

    notify_escalation(instance, step.escalation_role)
    return action

def notify_escalation(instance, escalation_role):
    """
    Dispatches a notification to all active users with the escalation role.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    target_users = User.objects.filter(role=escalation_role, is_active=True)
    if target_users.exists():
        metadata = {
            'title': f"Escalation: SLA Breached",
            'message': f"Workflow instance #{instance.id} has breached SLA.",
            'notif_type': 'task_delayed'
        }
        log_activity(
            actor=instance.initiated_by,
            verb='task_delayed',
            target=instance,
            metadata=metadata,
            notify_users=list(target_users),
            email_also=True
        )

def get_timeline(instance):
    """
    Returns ordered queryset of WorkflowActions for backwards compatibility.
    """
    from apps.workflow.models import WorkflowAction
    return WorkflowAction.objects.filter(instance=instance).order_by('timestamp')

def get_workflow_history(target_object):
    """
    Returns a read-only sequence of history events (WorkflowActions)
    for a given target business object (e.g. LeaveRequest).
    """
    from apps.workflow.models import WorkflowInstance, WorkflowAction
    
    model_name = target_object._meta.model_name
    object_type = 'leave_request' if model_name == 'leaverequest' else model_name
    object_id = str(target_object.id)
    
    instance = WorkflowInstance.objects.filter(object_type=object_type, object_id=object_id).first()
    if not instance:
        return []
        
    return list(WorkflowAction.objects.filter(instance=instance).order_by('timestamp'))

def get_workflow_timeline(target_object):
    """
    Returns a structured, display-ready list of timeline steps and their statuses.
    Each step dictionary contains:
        - step_number: int
        - name: str
        - status: 'completed' | 'current' | 'pending' | 'skipped'
        - approver_role: str
        - actions: list of dicts detailing actions matching this step
    """
    from apps.workflow.models import WorkflowInstance, WorkflowAction
    
    model_name = target_object._meta.model_name
    object_type = 'leave_request' if model_name == 'leaverequest' else model_name
    object_id = str(target_object.id)
    
    instance = WorkflowInstance.objects.filter(object_type=object_type, object_id=object_id).first()
    if not instance:
        return []
        
    steps = list(instance.definition.steps.all().order_by('step_number'))
    actions = list(WorkflowAction.objects.filter(instance=instance).order_by('timestamp'))
    
    timeline = []
    
    for step in steps:
        step_actions = [
            {
                'actor': action.actor.email or action.actor.phone,
                'action_taken': action.action,
                'note': action.note,
                'timestamp': action.timestamp,
                'delegated_by': action.delegated_by.email if action.delegated_by else None
            }
            for action in actions if action.step_number == step.step_number
        ]
        
        # Determine status
        status = 'pending'
        if instance.completed_at:
            if instance.current_status == 'rejected':
                has_reject = any(a['action_taken'] == 'reject' for a in step_actions)
                has_any_action = len(step_actions) > 0
                if has_reject:
                    status = 'completed'
                elif step.step_number < instance.current_step or (step.step_number == instance.current_step and has_any_action):
                    status = 'completed'
                else:
                    status = 'skipped'
            elif instance.current_status == 'approved':
                status = 'completed'
            else:
                status = 'completed'
        else:
            if step.step_number < instance.current_step:
                status = 'completed'
            elif step.step_number == instance.current_step:
                status = 'current'
            else:
                status = 'pending'
                
        timeline.append({
            'step_number': step.step_number,
            'name': step.name,
            'status': status,
            'approver_role': step.approver_role,
            'actions': step_actions
        })
        
    return timeline

