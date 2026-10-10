import logging
from django.db import transaction
from django.utils import timezone
from apps.employees.models import Employee, EmployeeProfile, Department, Designation, EmployeeStatus

logger = logging.getLogger(__name__)


def reconcile_all_employee_profiles():
    """
    Reconciliation service:
    Scans for any EmployeeProfile that has no linked Employee master record,
    or where the linked Employee record is missing/broken.
    Creates or links the canonical Employee master record so it is immediately
    visible in the Employee Directory.
    """
    try:
        profiles = list(EmployeeProfile.objects.select_related('user', 'branch', 'master_employee').all())
    except Exception as e:
        logger.warning(f"Could not load EmployeeProfiles during reconciliation: {e}")
        return 0

    reconciled_count = 0

    for profile in profiles:
        try:
            if profile.master_employee_id and Employee.objects.filter(pk=profile.master_employee_id).exists():
                continue

            with transaction.atomic():
                full_name = (profile.full_name or '').strip()
                if ' ' in full_name:
                    first_name, last_name = full_name.split(' ', 1)
                else:
                    first_name = full_name or (profile.user.first_name if profile.user else 'Employee')
                    last_name = (profile.user.last_name if (profile.user and profile.user.last_name) else 'Staff')

                dept_obj = None
                if profile.department and profile.department.strip():
                    dept_name = profile.department.strip()
                    dept_obj = Department.objects.filter(name=dept_name).first()
                    if not dept_obj:
                        dept_obj = Department.objects.create(
                            name=dept_name,
                            is_active=True,
                            branch=profile.branch
                        )

                desig_obj = None
                if profile.designation and profile.designation.strip():
                    desig_name = profile.designation.strip()
                    desig_obj = Designation.objects.filter(name=desig_name).first()
                    if not desig_obj:
                        desig_obj = Designation.objects.create(
                            name=desig_name,
                            is_active=True,
                            department=dept_obj
                        )

                emp_number = (profile.employee_id or f'EMP-{profile.pk:04d}').strip()

                emp = None
                if profile.user_id:
                    emp = Employee.objects.filter(user_id=profile.user_id).first()
                if not emp:
                    emp = Employee.objects.filter(employee_number=emp_number).first()

                if not emp:
                    emp = Employee.objects.create(
                        employee_number=emp_number,
                        first_name=first_name,
                        last_name=last_name,
                        user=profile.user,
                        phone=profile.phone or (profile.user.phone if profile.user else ''),
                        emergency_contact_phone=profile.emergency_contact or '',
                        branch=profile.branch,
                        department=dept_obj,
                        designation=desig_obj,
                        joined_date=profile.joined_date or timezone.localdate(),
                        status=EmployeeStatus.ACTIVE if profile.is_active else EmployeeStatus.INACTIVE,
                        is_trashed=False,
                    )
                else:
                    updates = []
                    if not emp.branch_id and profile.branch_id:
                        emp.branch = profile.branch
                        updates.append('branch')
                    if not emp.department_id and dept_obj:
                        emp.department = dept_obj
                        updates.append('department')
                    if not emp.designation_id and desig_obj:
                        emp.designation = desig_obj
                        updates.append('designation')
                    if emp.is_trashed:
                        emp.is_trashed = False
                        updates.append('is_trashed')
                    if updates:
                        emp.save(update_fields=updates)

                if profile.master_employee_id != emp.pk:
                    EmployeeProfile.objects.filter(pk=profile.pk).update(master_employee_id=emp.pk)
                    reconciled_count += 1

        except Exception as err:
            logger.error(f"Error reconciling profile {profile.pk} ({profile.employee_id}): {err}")

    return reconciled_count
