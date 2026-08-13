def get_canonical_employee(user):
    """
    Resolves the canonical employee identity for a given user.
    Prefers the canonical Employee master record, then falls back to the legacy EmployeeProfile.
    """
    if not user or not user.is_authenticated:
        return None
    if hasattr(user, 'employee_master') and user.employee_master:
        return user.employee_master
    if hasattr(user, 'employee_profile') and user.employee_profile:
        profile = user.employee_profile
        if profile.master_employee:
            return profile.master_employee
        return profile
    return None
