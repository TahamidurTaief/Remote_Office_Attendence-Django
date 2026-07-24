from apps.accounts.engine import PermissionEngine


def get_effective_permissions(user) -> set[str]:
    """
    Get the set of all effective permission codenames for the user.
    Uses request-scoped caching on the user object.
    - Superusers return the sentinel {"all"}.
    - Regular users return a union of Group permissions and direct permissions.
    """
    if not user or not user.is_authenticated:
        return set()
        
    if hasattr(user, '_effective_perms'):
        return user._effective_perms

    if user.is_superuser:
        perms = {"all"}
    else:
        resolved = PermissionEngine.get_user_resolved_permissions(user)
        perms = {code for code, data in resolved.items() if data.get('granted')}

    user._effective_perms = perms
    return perms


def clear_user_perm_cache(user):
    """
    Clears request-scoped permission cache on user object.
    """
    if hasattr(user, '_effective_perms'):
        delattr(user, '_effective_perms')
    if hasattr(user, '_has_perm_cache'):
        delattr(user, '_has_perm_cache')
    if hasattr(user, '_resolved_permissions_cache'):
        delattr(user, '_resolved_permissions_cache')


def has_perm_cached(user, perm_codename: str) -> bool:
    """
    Check if user has permission codename via PermissionEngine.
    """
    if not user or not user.is_authenticated:
        return False
    return PermissionEngine.evaluate(user, perm_codename).allowed


def user_can_access_my_projects(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if PermissionEngine.evaluate(user, 'projects.view').allowed:
        return True
    if getattr(user, 'role', '') in ('manager', 'admin'):
        return True
    
    employee_profile = getattr(user, 'employee_profile', None)
    if employee_profile and employee_profile.is_active and employee_profile.is_project_manager:
        return True
            
    return False
