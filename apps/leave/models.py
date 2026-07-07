from django.db import models
from django.db.models import F
from django.conf import settings
from apps.employees.models import EmployeeProfile

class LeaveType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    default_days_per_year = models.IntegerField(default=0)

    def __str__(self):
        return self.name

class LeaveBalance(models.Model):
    employee = models.ForeignKey(
        EmployeeProfile, 
        on_delete=models.CASCADE, 
        related_name='leave_balances'
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE)
    year = models.IntegerField()
    total_days = models.IntegerField(default=0)
    used_days = models.IntegerField(default=0)

    class Meta:
        unique_together = ('employee', 'leave_type', 'year')

    @property
    def remaining_days(self):
        return self.total_days - self.used_days

    def __str__(self):
        return f"{self.employee.full_name} - {self.leave_type.name} ({self.year}): {self.remaining_days}/{self.total_days} left"

class LeaveRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )

    employee = models.ForeignKey(
        EmployeeProfile, 
        on_delete=models.CASCADE, 
        related_name='leave_requests'
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()
    number_of_days = models.IntegerField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    requested_at = models.DateTimeField(auto_now_add=True)
    
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='reviewed_leave_requests'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def save(self, *args, **kwargs):
        # Autocalculate number of days inclusive of start and end date
        if self.start_date and self.end_date:
            self.number_of_days = (self.end_date - self.start_date).days + 1
            
        is_new = self.pk is None
        old_status = None
        old_days = 0
        old_type = None
        old_year = None
        
        if not is_new:
            try:
                old_instance = LeaveRequest.objects.get(pk=self.pk)
                old_status = old_instance.status
                old_days = old_instance.number_of_days
                old_type = old_instance.leave_type
                old_year = old_instance.start_date.year
            except LeaveRequest.DoesNotExist:
                pass
                
        super().save(*args, **kwargs)
        
        year = self.start_date.year
        
        # Case 1: Status transitioned to approved
        if self.status == 'approved' and old_status != 'approved':
            balance, _ = LeaveBalance.objects.get_or_create(
                employee=self.employee,
                leave_type=self.leave_type,
                year=year,
                defaults={'total_days': self.leave_type.default_days_per_year}
            )
            balance.used_days = F('used_days') + self.number_of_days
            balance.save()
            
        # Case 2: Status transitioned from approved to something else (e.g. pending/rejected)
        elif old_status == 'approved' and self.status != 'approved':
            try:
                balance = LeaveBalance.objects.get(
                    employee=self.employee,
                    leave_type=old_type,
                    year=old_year
                )
                balance.used_days = F('used_days') - old_days
                balance.save()
            except LeaveBalance.DoesNotExist:
                pass
                
        # Case 3: Remains approved but details changed
        elif self.status == 'approved' and old_status == 'approved':
            if old_type != self.leave_type or old_year != year or old_days != self.number_of_days:
                # Revert old balance
                try:
                    old_balance = LeaveBalance.objects.get(
                        employee=self.employee,
                        leave_type=old_type,
                        year=old_year
                    )
                    old_balance.used_days = F('used_days') - old_days
                    old_balance.save()
                except LeaveBalance.DoesNotExist:
                    pass
                
                # Apply new balance
                new_balance, _ = LeaveBalance.objects.get_or_create(
                    employee=self.employee,
                    leave_type=self.leave_type,
                    year=year,
                    defaults={'total_days': self.leave_type.default_days_per_year}
                )
                new_balance.used_days = F('used_days') + self.number_of_days
                new_balance.save()

    def delete(self, *args, **kwargs):
        if self.status == 'approved':
            year = self.start_date.year
            try:
                balance = LeaveBalance.objects.get(
                    employee=self.employee,
                    leave_type=self.leave_type,
                    year=year
                )
                balance.used_days = F('used_days') - self.number_of_days
                balance.save()
            except LeaveBalance.DoesNotExist:
                pass
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.full_name} - {self.leave_type.name} ({self.start_date} to {self.end_date})"
