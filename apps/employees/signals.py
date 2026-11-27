from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.employees.models import Employee, EmployeeProfile

_SYNC_TRIGGER_FIELDS = frozenset({
    'first_name', 'last_name', 'phone', 'branch', 'branch_id', 'status', 'user', 'user_id',
})


@receiver(post_save, sender=Employee)
def sync_employee_master_to_legacy_profile(sender, instance, **kwargs):
    """
    Reconciliation signal: Syncs Employee Master SSOT changes down to legacy EmployeeProfile
    if a linked CustomUser or legacy EmployeeProfile exists.
    Explicitly propagates allowed null/blank clears for phone and branch.
    """
    if getattr(instance, '_syncing_from_profile', False):
        return

    passed_fields = kwargs.get('update_fields')
    if passed_fields is not None and not (_SYNC_TRIGGER_FIELDS & set(passed_fields)):
        return

    profile = getattr(instance, 'legacy_profile', None)
    if not profile and instance.user_id:
        profile = EmployeeProfile.objects.filter(user_id=instance.user_id).first()

    if not profile:
        return

    check_all = passed_fields is None
    passed_set = set(passed_fields) if passed_fields is not None else set()
    update_fields = []

    if profile.master_employee_id != instance.pk:
        profile.master_employee = instance
        update_fields.append('master_employee')

    if check_all or 'first_name' in passed_set or 'last_name' in passed_set:
        full_name = instance.get_full_name()
        if profile.full_name != full_name:
            profile.full_name = full_name
            update_fields.append('full_name')

    if check_all or 'phone' in passed_set:
        norm_phone = (instance.phone or '').strip()
        if profile.phone != norm_phone:
            profile.phone = norm_phone
            update_fields.append('phone')

    if check_all or 'branch' in passed_set or 'branch_id' in passed_set:
        if profile.branch_id != instance.branch_id:
            profile.branch_id = instance.branch_id
            update_fields.append('branch')

    if check_all or 'status' in passed_set:
        is_active_allowed = instance.is_login_allowed()
        if profile.is_active != is_active_allowed:
            profile.is_active = is_active_allowed
            update_fields.append('is_active')

    if update_fields and profile.pk:
        with transaction.atomic():
            profile._syncing_from_master = True
            try:
                profile.save(update_fields=update_fields)
            finally:
                profile._syncing_from_master = False


@receiver(post_save, sender=EmployeeProfile)
def sync_legacy_profile_to_employee_master(sender, instance, created, **kwargs):
    """
    Ensures that when an EmployeeProfile is created or modified,
    only this single profile is reconciled without directory-wide scans.
    """
    if getattr(instance, '_syncing_from_master', False):
        return

    from apps.employees.reconciliation import reconcile_single_profile
    if not instance.master_employee_id or not Employee.objects.filter(pk=instance.master_employee_id).exists():
        reconcile_single_profile(instance)
