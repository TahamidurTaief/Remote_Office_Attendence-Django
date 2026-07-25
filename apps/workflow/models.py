from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta


class WorkflowDefinition(models.Model):
    code = models.CharField(max_length=50, unique=True, help_text="Unique identifier e.g. LEAVE_APPROVAL")
    module = models.CharField(max_length=50, help_text="Target module e.g. leave, expense")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'workflow_definition'
        ordering = ['module', 'code']

    def __str__(self):
        return f"{self.name} ({self.code})"


class WorkflowStep(models.Model):
    workflow = models.ForeignKey(WorkflowDefinition, on_delete=models.CASCADE, related_name='steps')
    step_number = models.PositiveIntegerField()
    name = models.CharField(max_length=100)
    from_status = models.CharField(max_length=50)
    to_status = models.CharField(max_length=50)
    approver_role = models.CharField(max_length=50, blank=True, default='manager', help_text="Static config role for approval")
    sla_hours = models.PositiveIntegerField(null=True, blank=True, help_text="Static config SLA hours for step completion")
    escalation_role = models.CharField(max_length=50, blank=True)
    allow_return = models.BooleanField(default=True)
    allow_rejection = models.BooleanField(default=True)
    notification_triggers = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'workflow_step'
        ordering = ['workflow', 'step_number']
        unique_together = ('workflow', 'step_number')

    def __str__(self):
        return f"{self.workflow.code} Step {self.step_number}: {self.name}"


class WorkflowDelegation(models.Model):
    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='delegations_given')
    to_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='delegations_received')
    workflow_code = models.CharField(max_length=50, blank=True, help_text="Empty means all workflows")
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'workflow_delegation'
        ordering = ['-created_at']

    def __str__(self):
        return f"Delegation from {self.from_user} to {self.to_user} ({self.start_date} to {self.end_date})"


class WorkflowInstance(models.Model):
    definition = models.ForeignKey(WorkflowDefinition, on_delete=models.PROTECT, related_name='instances')
    object_type = models.CharField(max_length=50, help_text="Model type e.g. leave_request, expense_claim")
    object_id = models.CharField(max_length=50, help_text="Primary key of target object")
    current_step = models.PositiveIntegerField(default=1)
    current_status = models.CharField(max_length=50, default='pending')
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workflows_initiated')
    initiated_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    sla_deadline = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'workflow_instance'
        ordering = ['-initiated_at']

    def start_workflow(self):
        """
        Initializes the workflow instance, sets status to step 1 from_status,
        and calculates initial sla_deadline based on step 1 sla_hours.
        """
        first_step = self.definition.steps.filter(step_number=1).first()
        if first_step:
            self.current_step = first_step.step_number
            self.current_status = first_step.from_status
            if first_step.sla_hours:
                now = timezone.now()
                self.sla_deadline = now + timedelta(hours=first_step.sla_hours)
        self.save()
        return self

    def __str__(self):
        return f"WorkflowInstance({self.definition.code} #{self.id} for {self.object_type}:{self.object_id})"


class WorkflowAction(models.Model):
    ACTION_CHOICES = (
        ('submit', 'Submit'),
        ('approve', 'Approve'),
        ('reject', 'Reject'),
        ('return', 'Return'),
        ('delegate', 'Delegate'),
    )

    instance = models.ForeignKey(WorkflowInstance, on_delete=models.CASCADE, related_name='actions')
    step_number = models.PositiveIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workflow_actions')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    note = models.TextField(blank=True)
    delegated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='delegated_actions')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'workflow_action'
        ordering = ['timestamp']

    def __str__(self):
        return f"Action {self.action} by {self.actor} on {self.instance}"
