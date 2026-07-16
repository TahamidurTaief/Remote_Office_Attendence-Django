from django.db import models

class Branch(models.Model):
    name = models.CharField(max_length=255)
    address = models.TextField()
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    radius_meters = models.IntegerField(default=100)
    wifi_ip = models.CharField(max_length=45, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class OfficeSchedule(models.Model):
    branch = models.OneToOneField(
        'Branch',
        on_delete=models.CASCADE,
        related_name='schedule'
    )
    # Office hours
    office_start_time = models.TimeField(
        default='09:00',
        help_text='Office opening time'
    )
    office_end_time = models.TimeField(
        default='18:00', 
        help_text='Office closing time'
    )
    # Late rule
    late_after_minutes = models.IntegerField(
        default=15,
        help_text='Minutes after start time = Late'
    )
    # Early checkout rule
    early_checkout_before_minutes = models.IntegerField(
        default=30,
        help_text='Minutes before end time = Early checkout'
    )
    # Overtime rule
    overtime_after_minutes = models.IntegerField(
        default=0,
        help_text='Minutes after end time = Overtime starts'
    )
    # Working days
    DAYS = [
        ('saturday', 'Saturday'),
        ('sunday', 'Sunday'),
        ('monday', 'Monday'),
        ('tuesday', 'Tuesday'),
        ('wednesday', 'Wednesday'),
        ('thursday', 'Thursday'),
        ('friday', 'Friday'),
    ]
    working_days = models.JSONField(
        default=list,
        help_text='List of working day names'
    )
    # Location tracking interval
    tracking_interval_minutes = models.IntegerField(
        default=10,
        help_text='How often to track location after check-in'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Schedule - {self.branch.name}"

    def get_late_threshold(self):
        from datetime import datetime, timedelta
        start = datetime.combine(
            datetime.today(), self.office_start_time)
        return (start + timedelta(
            minutes=self.late_after_minutes)).time()

    def get_early_checkout_threshold(self):
        from datetime import datetime, timedelta
        end = datetime.combine(
            datetime.today(), self.office_end_time)
        return (end - timedelta(
            minutes=self.early_checkout_before_minutes)).time()

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

@receiver(post_save, sender=Branch)
def create_branch_schedule(sender, instance, created, **kwargs):
    if created:
        OfficeSchedule.objects.get_or_create(
            branch=instance,
            defaults={
                'working_days': [
                    'saturday', 'sunday', 'monday',
                    'tuesday', 'wednesday', 'thursday'
                ]
            }
        )

@receiver(post_save, sender=Branch)
def clear_branch_cache_on_save(sender, instance, **kwargs):
    from django.core.cache import cache
    cache.delete('active_branches')

@receiver(post_delete, sender=Branch)
def clear_branch_cache_on_delete(sender, instance, **kwargs):
    from django.core.cache import cache
    cache.delete('active_branches')
