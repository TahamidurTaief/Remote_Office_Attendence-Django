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
        group_perms = set(user.groups.values_list('permissions__codename', flat=True))
        user_perms = set(user.user_permissions.values_list('codename', flat=True))
        perms = (group_perms | user_perms) - {None}

    user._effective_perms = perms
    return perms


def has_perm_cached(user, perm_codename: str) -> bool:
    """
    Check if user has permission codename.
    Uses request-scoped caching on the user object.
    Supports 'app_label.codename' or just 'codename'.
    """
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    # Check cache dict on user object
    if not hasattr(user, '_has_perm_cache'):
        user._has_perm_cache = {}

    if perm_codename in user._has_perm_cache:
        return user._has_perm_cache[perm_codename]

    # Resolve permission
    if '.' in perm_codename:
        # Standard Django has_perm checks both group and user permissions natively
        result = user.has_perm(perm_codename)
    else:
        # Fallback to local codename set comparison
        result = perm_codename in get_effective_permissions(user)

    user._has_perm_cache[perm_codename] = result
    return result


def user_can_access_my_projects(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.role == 'admin':
        return True
    if user.role == 'manager' or user.groups.filter(name='Manager').exists():
        return True
    
    # Check if user has an EmployeeProfile with is_project_manager=True
    employee_profile = getattr(user, 'employee_profile', None)
    if employee_profile and employee_profile.is_active and employee_profile.is_project_manager:
        return True
            
    return False
