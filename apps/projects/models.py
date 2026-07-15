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

