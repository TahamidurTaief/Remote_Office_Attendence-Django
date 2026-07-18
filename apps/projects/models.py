from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from apps.employees.models import EmployeeProfile
from apps.branches.models import Branch
import os
from django.core.exceptions import ValidationError

def validate_task_attachment(file):
    if not file:
        return
    ext = os.path.splitext(file.name)[1].lower()
    valid_extensions = ['.jpg', '.jpeg', '.png', '.pdf']
    if ext not in valid_extensions:
        raise ValidationError('Unsupported file extension. Only JPG, JPEG, PNG, and PDF files are allowed.')
    limit = 10 * 1024 * 1024
    if file.size > limit:
        raise ValidationError('File size too large. Maximum size allowed is 10MB.')

class ProjectType(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Project(models.Model):
    SYSTEM_TYPE_CHOICES = (
        ('VRF', 'VRF'),
        ('Chiller', 'Chiller'),
        ('Split', 'Split'),
        ('Package Unit', 'Package Unit'),
    )

    STATUS_CHOICES = (
        ('Not Started', 'Not Started'),
        ('In Progress', 'In Progress'),
        ('Delayed', 'Delayed'),
        ('Completed', 'Completed'),
    )

    name = models.CharField(max_length=255)
    client_name = models.CharField(max_length=255)
    client_email = models.EmailField(blank=True, null=True)
    consultant = models.CharField(max_length=255, blank=True)
    consultant_email = models.EmailField(blank=True, null=True)
    main_contractor = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255)
    
    project_type = models.ForeignKey(
        ProjectType,
        on_delete=models.PROTECT,
        related_name='projects'
    )
    project_managers = models.ManyToManyField(
        EmployeeProfile,
        blank=True,
        related_name='managed_projects'
    )
    site_engineers = models.ManyToManyField(
        EmployeeProfile,
        blank=True,
        related_name='site_engineer_projects'
    )
    project_members = models.ManyToManyField(
        EmployeeProfile,
        blank=True,
        related_name='member_projects'
    )
    
    hvac_capacity_tr = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Capacity in Tons of Refrigeration"
    )
    system_type = models.CharField(
        max_length=50,
        choices=SYSTEM_TYPE_CHOICES,
        null=True,
        blank=True
    )
    
    start_date = models.DateField()
    completion_date = models.DateField(null=True, blank=True)
    
    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default='Not Started'
    )
    
    progress_percent = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    
    branch = models.ForeignKey(
        Branch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='projects'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_projects'
    )

    def __str__(self):
        return self.name

    def recalculate_progress(self):
        total_tasks = self.tasks.all()
        if not total_tasks.exists():
            return
        total_points = sum(task.points for task in total_tasks)
        if total_points == 0:
            self.progress_percent = 0
        else:
            completed_points = sum(task.points for task in total_tasks if task.status == 'Completed')
            self.progress_percent = round((completed_points / total_points) * 100)
        self.save(update_fields=['progress_percent'])


class TaskTemplate(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class TaskTemplateItem(models.Model):
    template = models.ForeignKey(TaskTemplate, on_delete=models.CASCADE, related_name='items')
    order = models.PositiveIntegerField()
    activity = models.CharField(max_length=255)
    default_responsible_role = models.CharField(max_length=100, blank=True)
    default_duration_days = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.template.name} - {self.order}. {self.activity}"

class ProjectTask(models.Model):
    STATUS_CHOICES = Project.STATUS_CHOICES

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='tasks',
        null=True,
        blank=True
    )
    order = models.PositiveIntegerField()
    activity = models.CharField(max_length=255)
    responsible_person = models.ForeignKey(
        EmployeeProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_tasks'
    )
    planned_start = models.DateField(null=True, blank=True)
    planned_finish = models.DateField(null=True, blank=True)
    duration_days = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        default='Not Started'
    )
    remarks = models.TextField(blank=True)
    points = models.PositiveIntegerField(default=10)
    completed_at = models.DateTimeField(null=True, blank=True)
    employee_note = models.TextField(blank=True)
    assignment_attachment = models.FileField(
        upload_to='projects/task_attachments/%Y/%m/',
        null=True,
        blank=True,
        validators=[validate_task_attachment]
    )
    completion_attachment = models.FileField(
        upload_to='projects/task_attachments/%Y/%m/',
        null=True,
        blank=True,
        validators=[validate_task_attachment]
    )

    class Meta:
        ordering = ['order']
        permissions = [
            ('assign_projecttask', 'Can assign project task'),
        ]

    def save(self, *args, **kwargs):
        is_completed_transition = False
        if self.status == 'Completed':
            if self.pk:
                try:
                    old_status = ProjectTask.objects.get(pk=self.pk).status
                    if old_status != 'Completed':
                        is_completed_transition = True
                except ProjectTask.DoesNotExist:
                    is_completed_transition = True
            else:
                is_completed_transition = True

        if is_completed_transition and not self.completed_at:
            from django.utils import timezone
            self.completed_at = timezone.now()

        is_delayed = False
        if self.status == 'Delayed':
            if self.pk:
                try:
                    old_status = ProjectTask.objects.get(pk=self.pk).status
                    if old_status != 'Delayed':
                        is_delayed = True
                except ProjectTask.DoesNotExist:
                    is_delayed = True
            else:
                is_delayed = True
                
        super().save(*args, **kwargs)
        
        if is_delayed and self.project.project_managers.exists():
            from apps.notifications.dispatch import log_activity
            pms = self.project.project_managers.filter(is_active=True)
            for pm in pms:
                if pm.user:
                    pm_user = pm.user
                    subject = f"Task Delayed: {self.activity} in Project {self.project.name}"
                    message = (
                        f"Hello {pm.full_name},\n\n"
                        f"The task '{self.activity}' in project '{self.project.name}' has been marked as Delayed.\n"
                        f"Remarks: {self.remarks or 'None'}\n\n"
                        f"Regards,\nFieldTrack System"
                    )
                    log_activity(
                        actor=None,
                        verb='task_delayed',
                        target=self,
                        metadata={'title': subject, 'message': message, 'remarks': self.remarks or ''},
                        notify_users=[pm_user],
                        email_also=True
                    )

        if is_completed_transition and self.project.project_managers.exists():
            from apps.notifications.dispatch import log_activity
            pms = self.project.project_managers.filter(is_active=True)
            for pm in pms:
                if pm.user:
                    pm_user = pm.user
                    subject = f"Task Completed: {self.activity} in Project {self.project.name}"
                    message = (
                        f"Hello {pm.full_name},\n\n"
                        f"The task '{self.activity}' in project '{self.project.name}' has been marked as Completed.\n"
                        f"Employee Note: {self.employee_note or 'None'}\n"
                        f"Completed At: {self.completed_at}\n\n"
                    )
                    if self.completion_attachment:
                        message += f"See attached proof file: {self.completion_attachment.url}\n\n"
                    message += "Regards,\nFieldTrack System"
                    log_activity(
                        actor=None,
                        verb='task_completed',
                        target=self,
                        metadata={'title': subject, 'message': message, 'employee_note': self.employee_note or ''},
                        notify_users=[pm_user],
                        email_also=True
                    )


    def __str__(self):
        project_name = self.project.name if self.project_id else 'Standalone'
        return f"{project_name} - {self.order}. {self.activity}"


class DailyProgressLog(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='progress_logs'
    )
    date = models.DateField()
    planned_work = models.TextField()
    completed_work = models.TextField()
    manpower_count = models.PositiveIntegerField(null=True, blank=True)
    delay_reason = models.TextField(blank=True)
    supervisor_name = models.CharField(max_length=255)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='progress_logs'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.project.name} - {self.date}"


class ManpowerDeployment(models.Model):
    TRADE_CHOICES = (
        ('Project Engineer', 'Project Engineer'),
        ('Site Engineer', 'Site Engineer'),
        ('Supervisor', 'Supervisor'),
        ('Duct Technician', 'Duct Technician'),
        ('Pipe Fitter', 'Pipe Fitter'),
        ('Electrician', 'Electrician'),
        ('Insulation Team', 'Insulation Team'),
        ('Helper', 'Helper'),
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='manpower_logs'
    )
    date = models.DateField()
    trade = models.CharField(max_length=50, choices=TRADE_CHOICES)
    required_count = models.PositiveIntegerField()
    present_count = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ('project', 'date', 'trade')

    def __str__(self):
        return f"{self.project.name} - {self.date} - {self.trade}"


class ProjectMaterial(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='materials'
    )
    material_name = models.CharField(max_length=255)
    unit = models.CharField(max_length=50)
    required_qty = models.DecimalField(max_digits=10, decimal_places=2)
    received_qty = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    remarks = models.TextField(blank=True)

    @property
    def balance(self):
        return self.required_qty - self.received_qty

    def save(self, *args, **kwargs):
        is_trigger = False
        if self.received_qty == 0:
            if self.pk:
                try:
                    old_received = ProjectMaterial.objects.get(pk=self.pk).received_qty
                    if old_received > 0:
                        is_trigger = True
                except ProjectMaterial.DoesNotExist:
                    is_trigger = True
            else:
                is_trigger = True
                
        super().save(*args, **kwargs)
        
        if is_trigger and self.project.completion_date and self.project.project_managers.exists():
            from datetime import date
            days_left = (self.project.completion_date - date.today()).days
            if days_left <= 7:
                from apps.notifications.dispatch import send_email_notification
                pms = self.project.project_managers.filter(is_active=True)
                for pm in pms:
                    if pm.user:
                        subject = f"URGENT: Material Zero-Received: {self.material_name} in Project {self.project.name}"
                        message = (
                            f"Hello {pm.full_name},\n\n"
                            f"The material '{self.material_name}' in project '{self.project.name}' has 0 received quantity, "
                            f"and the project completion deadline is approaching on {self.project.completion_date} "
                            f"(in {days_left} days).\n\n"
                            f"Regards,\nFieldTrack System"
                        )
                        send_email_notification(pm.user, subject, message)

    def __str__(self):
        return f"{self.project.name} - {self.material_name}"


class ProjectSignOff(models.Model):
    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name='sign_off'
    )
    project_manager_name = models.CharField(max_length=255, blank=True)
    project_manager_signed_at = models.DateTimeField(null=True, blank=True)

    site_engineer_name = models.CharField(max_length=255, blank=True)
    site_engineer_signed_at = models.DateTimeField(null=True, blank=True)

    consultant_name = models.CharField(max_length=255, blank=True)
    consultant_signed_at = models.DateTimeField(null=True, blank=True)

    client_representative_name = models.CharField(max_length=255, blank=True)
    client_representative_signed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Sign-off for {self.project.name}"






