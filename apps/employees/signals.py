from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from apps.employees.models import Employee, EmployeeProfile

@receiver(post_save, sender=Employee)
def sync_employee_master_to_legacy_profile(sender, instance, **kwargs):
    """
    Reconciliation signal: Syncs Employee Master SSOT changes down to legacy EmployeeProfile
    if a linked CustomUser or legacy EmployeeProfile exists.
    """
    profile = getattr(instance, 'legacy_profile', None)
    if not profile and instance.user:
        profile = getattr(instance.user, 'employee_profile', None)

    if profile:
        update_fields = []
        if profile.master_employee_id != instance.pk:
            profile.master_employee = instance
            update_fields.append('master_employee')
        if profile.full_name != instance.get_full_name():
            profile.full_name = instance.get_full_name()
            update_fields.append('full_name')
        if instance.phone and profile.phone != instance.phone:
            profile.phone = instance.phone
            update_fields.append('phone')
        if instance.branch and profile.branch_id != instance.branch_id:
            profile.branch = instance.branch
            update_fields.append('branch')
        is_active_allowed = instance.is_login_allowed()
        if profile.is_active != is_active_allowed:
            profile.is_active = is_active_allowed
            update_fields.append('is_active')

        if update_fields:
            profile.save(update_fields=update_fields)
