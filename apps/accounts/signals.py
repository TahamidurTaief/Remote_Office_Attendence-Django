import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from apps.accounts.rbac_models import RolePermission, UserRoleAssignment, UserPermissionOverride, Role
from apps.accounts.engine import PermissionEngine

logger = logging.getLogger(__name__)


@receiver([post_save, post_delete], sender=UserRoleAssignment)
def invalidate_user_role_assignment_cache(sender, instance, **kwargs):
    if instance and instance.user:
        PermissionEngine.invalidate_user_cache(instance.user)


@receiver([post_save, post_delete], sender=UserPermissionOverride)
def invalidate_user_permission_override_cache(sender, instance, **kwargs):
    if instance and instance.user:
        PermissionEngine.invalidate_user_cache(instance.user)


@receiver([post_save, post_delete], sender=RolePermission)
def invalidate_role_permission_cache(sender, instance, **kwargs):
    if instance and instance.role:
        from apps.accounts.models import CustomUser
        users = CustomUser.objects.filter(role_assignments__role=instance.role)
        for user in users:
            PermissionEngine.invalidate_user_cache(user)


@receiver([post_save, post_delete], sender=Role)
def invalidate_role_cache(sender, instance, **kwargs):
    if instance:
        from apps.accounts.models import CustomUser
        users = CustomUser.objects.filter(role_assignments__role=instance)
        for user in users:
            PermissionEngine.invalidate_user_cache(user)
