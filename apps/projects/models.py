from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from apps.employees.models import EmployeeProfile
from apps.branches.models import Branch

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
    consultant = models.CharField(max_length=255, blank=True)
    main_contractor = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255)
    
    project_manager = models.ForeignKey(
        EmployeeProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='managed_projects'
    )
    site_engineer = models.ForeignKey(
        EmployeeProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='site_engineer_projects'
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
        choices=SYSTEM_TYPE_CHOICES
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

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
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

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.project.name} - {self.order}. {self.activity}"


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






