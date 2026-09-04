import time
from datetime import date
from django.db import transaction
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from django.contrib.auth import get_user_model
from apps.employees.models import Employee, EmployeeStatus, EmployeeProfile
from apps.accounts.rbac_models import Role, UserRoleAssignment
from apps.accounts.services import RoleAssignmentService
from apps.notifications.models import log_audit
from apps.employees.forms import (
    WizardStep1Form, WizardStep2Form, WizardStep3Form, WizardStep4Form,
    WizardStep6Form, generate_employee_id
)

User = get_user_model()

DRAFT_SESSION_KEY = 'employee_wizard_drafts'
DRAFT_EXPIRY_SECONDS = 86400  # 24 hours


class WizardDraftManager:
    """
    Manages session-backed drafts, step saving, cross-user isolation,
    draft expiration, validation, and atomic final approval for Employee Wizard.
    """

    @staticmethod
    def _get_session(request_or_session):
        if hasattr(request_or_session, 'session'):
            return request_or_session.session
        return request_or_session

    @classmethod
    def _mark_session_modified(cls, request_or_session):
        session = cls._get_session(request_or_session)
        if hasattr(session, 'modified'):
            session.modified = True

    @classmethod
    def _get_drafts_dict(cls, request):
        session = cls._get_session(request)
        if DRAFT_SESSION_KEY not in session:
            session[DRAFT_SESSION_KEY] = {}
        return session[DRAFT_SESSION_KEY]

    @classmethod
    def get_draft_key(cls, user_id, employee_uuid=None):
        if employee_uuid:
            return f"emp_{employee_uuid}"
        return f"new_{user_id}"

    @classmethod
    def get_draft(cls, request, user_id, employee_uuid=None):
        drafts = cls._get_drafts_dict(request)
        key = cls.get_draft_key(user_id, employee_uuid)
        draft = drafts.get(key)
        if not draft:
            return None

        # Cross-user check
        if draft.get('user_id') != user_id:
            raise PermissionDenied("Unauthorized draft access: draft does not belong to the current user.")

        # Expiry check
        updated_at = draft.get('updated_at', 0)
        if time.time() - updated_at > DRAFT_EXPIRY_SECONDS:
            # Purge expired draft safely
            cls.clear_draft(request, user_id, employee_uuid)
            return {'expired': True}

        return draft

    @classmethod
    def get_or_create_draft(cls, request, user_id, employee_uuid=None):
        draft = cls.get_draft(request, user_id, employee_uuid)
        if draft and not draft.get('expired'):
            return draft

        drafts = cls._get_drafts_dict(request)
        key = cls.get_draft_key(user_id, employee_uuid)
        new_draft = {
            'user_id': user_id,
            'employee_uuid': str(employee_uuid) if employee_uuid else None,
            'created_at': time.time(),
            'updated_at': time.time(),
            'step_data': {},
            'step_statuses': {
                1: 'pending',
                2: 'pending',
                3: 'pending',
                4: 'pending',
                5: 'pending',
                6: 'pending',
                7: 'pending',
                8: 'pending',
            },
            'step_errors': {},
        }
        drafts[key] = new_draft
        cls._mark_session_modified(request)
        return new_draft

    @classmethod
    def clear_draft(cls, request, user_id, employee_uuid=None):
        drafts = cls._get_drafts_dict(request)
        key = cls.get_draft_key(user_id, employee_uuid)
        if key in drafts:
            del drafts[key]
            cls._mark_session_modified(request)

    @classmethod
    def save_step(cls, request, step, post_data, files=None, employee=None):
        """
        Safely saves current step data into session draft.
        If valid and step allows, persists to database.
        Always preserves submitted values, even if incomplete or erroring.
        """
        step = int(step)
        user_id = request.user.id
        emp_uuid = employee.uuid if employee else None
        draft = cls.get_or_create_draft(request, user_id, emp_uuid)

        raw_data = {}
        for k, v in post_data.items():
            if k in ('csrfmiddlewaretoken', 'next_step', 'target_step', 'action_type', 'action'):
                continue
            if k == 'roles' or k.endswith('[]') or isinstance(v, list) or (hasattr(post_data, 'getlist') and len(post_data.getlist(k)) > 1):
                raw_data[k] = post_data.getlist(k) if hasattr(post_data, 'getlist') else (v if isinstance(v, list) else [v])
            else:
                raw_data[k] = v

        draft['step_data'][str(step)] = raw_data
        draft['updated_at'] = time.time()

        is_valid = False
        saved_instance = employee
        field_errors = {}

        if step == 1:
            form = WizardStep1Form(post_data, files, instance=employee)
            if form.is_valid():
                emp = form.save(commit=False)
                if not emp.pk:
                    emp.status = EmployeeStatus.DRAFT
                emp.save()
                saved_instance = emp
                is_valid = True
                draft['step_statuses']['1'] = 'complete'
                draft['step_errors']['1'] = {}
                draft['employee_uuid'] = str(emp.uuid)
                # If key was new_*, migrate draft key to emp_*
                old_key = cls.get_draft_key(user_id, None)
                new_key = cls.get_draft_key(user_id, emp.uuid)
                drafts = cls._get_drafts_dict(request)
                drafts[new_key] = draft
                if old_key != new_key and old_key in drafts:
                    del drafts[old_key]
            else:
                field_errors = {f: [str(e) for e in errs] for f, errs in form.errors.items()}
                draft['step_statuses']['1'] = 'incomplete'
                draft['step_errors']['1'] = field_errors

        elif step == 2:
            form = WizardStep2Form(post_data, instance=employee)
            if form.is_valid():
                if employee:
                    form.save()
                is_valid = True
                draft['step_statuses']['2'] = 'complete'
                draft['step_errors']['2'] = {}
            else:
                field_errors = {f: [str(e) for e in errs] for f, errs in form.errors.items()}
                draft['step_statuses']['2'] = 'incomplete'
                draft['step_errors']['2'] = field_errors

        elif step == 3:
            form = WizardStep3Form(post_data, instance=employee)
            if form.is_valid():
                if employee:
                    form.save()
                is_valid = True
                draft['step_statuses']['3'] = 'complete'
                draft['step_errors']['3'] = {}
            else:
                field_errors = {f: [str(e) for e in errs] for f, errs in form.errors.items()}
                draft['step_statuses']['3'] = 'incomplete'
                draft['step_errors']['3'] = field_errors

        elif step == 4:
            form = WizardStep4Form(post_data, employee=employee, actor=request.user)
            if form.is_valid():
                if employee:
                    form.save()
                is_valid = True
                draft['step_statuses']['4'] = 'complete'
                draft['step_errors']['4'] = {}
            else:
                field_errors = {f: [str(e) for e in errs] for f, errs in form.errors.items()}
                draft['step_statuses']['4'] = 'incomplete'
                draft['step_errors']['4'] = field_errors

        elif step == 6:
            form = WizardStep6Form(post_data, instance=employee)
            if form.is_valid():
                if employee:
                    form.save()
                is_valid = True
                draft['step_statuses']['6'] = 'complete'
                draft['step_errors']['6'] = {}
            else:
                field_errors = {f: [str(e) for e in errs] for f, errs in form.errors.items()}
                draft['step_statuses']['6'] = 'incomplete'
                draft['step_errors']['6'] = field_errors

        elif step in (5, 7, 8):
            is_valid = True

        cls._mark_session_modified(request)
        return is_valid, saved_instance, field_errors

    @classmethod
    def get_step_form(cls, request, step, employee=None, post_data=None):
        """
        Returns instantiated form for step with restored draft data and initial values.
        """
        step = int(step)
        user_id = request.user.id
        emp_uuid = employee.uuid if employee else None
        draft = cls.get_draft(request, user_id, emp_uuid)

        step_data = {}
        if draft and not draft.get('expired'):
            step_data = draft.get('step_data', {}).get(str(step), {})

        data = post_data or None

        if step == 1:
            initial = {}
            if not employee and not step_data.get('employee_number'):
                initial['employee_number'] = generate_employee_id()
            initial.update(step_data)
            return WizardStep1Form(data=data, instance=employee, initial=initial)

        elif step == 2:
            initial = {}
            if not employee and not step_data.get('joined_date'):
                initial['joined_date'] = date.today()
            initial.update(step_data)
            return WizardStep2Form(data=data, instance=employee, initial=initial)

        elif step == 3:
            initial = {}
            initial.update(step_data)
            return WizardStep3Form(data=data, instance=employee, initial=initial)

        elif step == 4:
            initial = {}
            initial.update(step_data)
            return WizardStep4Form(data=data, employee=employee, actor=request.user, initial=initial)

        elif step == 6:
            initial = {}
            initial.update(step_data)
            return WizardStep6Form(data=data, instance=employee, initial=initial)

        return None

    @classmethod
    def compute_all_statuses(cls, request, employee=None):
        """
        Computes the visual status of each step (1 to 8):
        'complete', 'incomplete', 'error', 'pending', or 'active'.
        """
        user_id = request.user.id
        emp_uuid = employee.uuid if employee else None
        draft = cls.get_draft(request, user_id, emp_uuid)
        draft_statuses = draft.get('step_statuses', {}) if draft and not draft.get('expired') else {}
        draft_errors = draft.get('step_errors', {}) if draft and not draft.get('expired') else {}

        statuses = {}
        for s in range(1, 9):
            s_str = str(s)
            has_error = bool(draft_errors.get(s_str))
            if has_error:
                statuses[s] = 'error'
                continue

            status_val = draft_statuses.get(s_str, 'pending')
            if status_val == 'complete':
                statuses[s] = 'complete'
            elif status_val in ('incomplete', 'error'):
                statuses[s] = 'incomplete'
            else:
                # Infer from employee instance
                if employee:
                    if s == 1 and employee.employee_number and employee.first_name and employee.last_name:
                        statuses[s] = 'complete'
                    elif s == 2 and employee.department_id and employee.designation_id and employee.joined_date:
                        statuses[s] = 'complete'
                    elif s == 3 and employee.basic_salary is not None:
                        statuses[s] = 'complete'
                    elif s == 4 and employee.user_id:
                        statuses[s] = 'complete'
                    elif s == 5 and employee.documents.filter(is_active=True).exists():
                        statuses[s] = 'complete'
                    elif s == 6 and employee.emergency_contact_name and employee.emergency_contact_phone:
                        statuses[s] = 'complete'
                    elif s == 7 and employee.asset_assignments.filter(returned_date__isnull=True).exists():
                        statuses[s] = 'complete'
                    else:
                        statuses[s] = 'pending'
                else:
                    statuses[s] = 'pending'
        return statuses

    @classmethod
    def validate_entire_wizard(cls, request, employee):
        """
        Validates the entire employee lifecycle wizard across all steps.
        Returns (is_valid, errors_by_step, form_objects).
        """
        if not employee:
            return False, {1: ["Basic Employee record has not been created yet."]}, {}

        user_id = request.user.id
        draft = cls.get_draft(request, user_id, employee.uuid)
        step_data = draft.get('step_data', {}) if draft and not draft.get('expired') else {}

        errors_by_step = {}
        forms_by_step = {}

        # Validate Step 1 (Mandatory core fields)
        d1 = step_data.get('1') or {
            'employee_number': employee.employee_number,
            'first_name': employee.first_name,
            'last_name': employee.last_name,
            'personal_email': employee.personal_email,
            'phone': employee.phone,
            'dob': employee.dob,
            'gender': employee.gender,
        }
        f1 = WizardStep1Form(d1, instance=employee)
        if not f1.is_valid():
            errors_by_step[1] = f1.errors
        forms_by_step[1] = f1

        # Validate Step 2 if submitted in draft
        if step_data.get('2'):
            f2 = WizardStep2Form(step_data['2'], instance=employee)
            if not f2.is_valid():
                errors_by_step[2] = f2.errors
            forms_by_step[2] = f2

        # Validate Step 3 if submitted in draft
        if step_data.get('3'):
            f3 = WizardStep3Form(step_data['3'], instance=employee)
            if not f3.is_valid():
                errors_by_step[3] = f3.errors
            forms_by_step[3] = f3

        # Validate Step 4 if submitted in draft or if user account exists
        d4 = step_data.get('4')
        if d4:
            d4_copy = dict(d4)
            if 'roles' in d4_copy and not isinstance(d4_copy['roles'], list):
                d4_copy['roles'] = [d4_copy['roles']]
            f4 = WizardStep4Form(d4_copy, employee=employee, actor=request.user)
            if not f4.is_valid():
                errors_by_step[4] = f4.errors
            forms_by_step[4] = f4

        # Validate Step 6 if submitted in draft
        if step_data.get('6'):
            f6 = WizardStep6Form(step_data['6'], instance=employee)
            if not f6.is_valid():
                errors_by_step[6] = f6.errors
            forms_by_step[6] = f6

        # Check existing draft errors
        if draft and not draft.get('expired'):
            for s_str, errs in draft.get('step_errors', {}).items():
                if errs and int(s_str) not in errors_by_step:
                    errors_by_step[int(s_str)] = errs

        is_valid = len(errors_by_step) == 0
        return is_valid, errors_by_step, forms_by_step

    @classmethod
    def finalize_approval(cls, request, employee):
        """
        Executes final employee wizard activation with atomic rollback guarantee.
        Validates entire wizard first; rolls back if anything fails.
        """
        is_valid, errors_by_step, forms = cls.validate_entire_wizard(request, employee)
        if not is_valid:
            return False, errors_by_step

        with transaction.atomic():
            # Save any pending form changes from forms
            if forms.get(1):
                forms[1].save()
            if forms.get(2):
                forms[2].save()
            if forms.get(3):
                forms[3].save()
            if forms.get(4) and not getattr(employee, 'user_id', None):
                forms[4].save()

            old_status = employee.status
            employee.status = EmployeeStatus.ACTIVE
            employee.save()

            # Ensure EmployeeProfile is active and synchronized
            profile = getattr(employee, 'legacy_profile', None)
            if not profile and employee.user:
                profile = getattr(employee.user, 'employee_profile', None)
            if profile:
                profile.is_active = True
                profile.full_name = employee.get_full_name()
                profile.branch = employee.branch
                profile.save()

            log_audit(
                actor=request.user,
                action='employee_wizard_approval',
                target=employee,
                summary=f"Approved employee wizard: {employee.get_full_name()} status changed from {old_status} to Active."
            )

            # Clear wizard session draft on successful activation
            cls.clear_draft(request, request.user.id, employee.uuid)

        return True, {}
