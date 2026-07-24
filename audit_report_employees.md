# Audit Report: Employee Module (apps/employees)

## 1. Baseline System & Test Status
- **Repository Branch / HEAD**: Baseline commit verified at HEAD.
- **PermissionEngine Baseline**: 9-Layer RBAC engine active (`apps/accounts/engine.py`, `rbac_models.py`).
- **Role Assignment Pattern**: Uses `UserRoleAssignment`, no direct `CustomUser.role` write (migrated in `forms.py` `ea955f0`).

## 2. Current Employee Data Models Audit

### Employee (Master Model - SSOT)
- **Model**: `apps.employees.models.Employee`
- **Fields**:
  - `employee_number` (CharField, max_length=50, unique=True, db_index=True)
  - `first_name` (CharField, max_length=100)
  - `last_name` (CharField, max_length=100)
  - `dob` (DateField, null=True, blank=True)
  - `gender` (CharField, max_length=20, choices=GENDER_CHOICES)
  - `national_id` (CharField, max_length=100, blank=True)
  - `phone` (CharField, max_length=30, blank=True)
  - `personal_email` (EmailField, blank=True)
  - `address` (TextField, blank=True)
  - `emergency_contact_name` (CharField, max_length=255, blank=True)
  - `emergency_contact_phone` (CharField, max_length=50, blank=True)
  - `branch` (FK `apps.branches.Branch`, SET_NULL, null=True, db_index=True)
  - `department` (FK `apps.employees.Department`, SET_NULL, null=True, db_index=True)
  - `designation` (FK `apps.employees.Designation`, SET_NULL, null=True, db_index=True)
  - `reporting_manager` (FK `self`, SET_NULL, null=True, db_index=True)
  - `status` (CharField, max_length=30, choices=EmployeeStatus, default='draft', db_index=True)
  - `joined_date` (DateField, null=True, blank=True)
  - `user` (OneToOne `settings.AUTH_USER_MODEL`, SET_NULL, null=True, related_name='employee_master')
  - `created_at` (DateTimeField, auto_now_add=True)
  - `updated_at` (DateTimeField, auto_now=True)
- **Indexes**: `employee_number`, `status`, `department`, `designation`, `reporting_manager`, `branch`.

### EmployeeProfile (Legacy Profile Bridge Model)
- **Model**: `apps.employees.models.EmployeeProfile`
- **Fields**: `user` (OneToOne), `master_employee` (OneToOne to Employee), `branch` (FK Branch), `employee_id` (CharField, unique), `full_name`, `department` (CharField), `designation` (CharField), `phone`, `emergency_contact`, `profile_photo`, `is_active` (Bool), `joined_date`, `tracking_interval`, `overtime_enabled`, `is_project_manager`.
- **Sync Signal**: `sync_employee_master_to_legacy_profile` in `apps/employees/signals.py` mirrors updates from `Employee` master model down to `EmployeeProfile`.

### Auxiliary & Sub-Models
1. **`Department` & `Designation`**: FK lookup models for organizational structure.
2. **`EmployeeDocument`**: Versioned document storage (`document_type`, `title`, `file`, `version`, `expiry_date`, `is_active`). Sensitive doc types flagged via `SENSITIVE_DOCUMENT_TYPES` (`nid`, `passport`, `medical`, `police_clearance`).
3. **`DocumentDownloadLog`**: Audit logger for document downloads.
4. **`Asset` & `AssetAssignment`**: Hardware tracking (Laptop, Mobile, SIM, etc.). Enforces non-duplicate active assignments in `clean()`.
5. **`EmploymentHistory`**: Immutable history log tracking field changes (`field_changed`, `old_value`, `new_value`, `reason`, `approved_by`, `effective_date`). Update/delete blocked on model level.
6. **`LifecycleTransitionRequest`**: Approval queue for `HIGH_RISK` status transitions (`from_status`, `to_status`, `reason`, `new_department`, `new_designation`, `review_status`).

## 3. Current Create/Edit Views & Forms Structure
- `EmployeeCreateView` (`apps/employees/views.py`): Flat form rendering `EmployeeCreateForm`. Creates `EmployeeProfile` and linked `CustomUser`.
- `EmployeeEditView` (`apps/employees/views.py`): Edits `EmployeeProfile` and updates role via `UserRoleAssignment.objects.update_or_create(user=user, defaults={'role': role_obj})`.
- `EmployeeMasterForm`: ModelForm for `Employee` master model with circular reporting validation.

## 4. Lifecycle Status Handling Audit
- **Statuses**: `draft`, `pending_approval`, `active`, `probation`, `confirmed`, `transferred`, `promoted`, `demoted`, `notice_period`, `resigned`, `terminated`, `retired`, `archived`.
- **State Machine**: Defined in `apps/employees/lifecycle.py`.
- **Risk Tiers**:
  - `LOW_RISK_TRANSITIONS`: Immediate execution (`draft` -> `pending_approval`, `probation` -> `confirmed`, `notice_period` -> `resigned`, `resigned`/`terminated`/`retired` -> `archived`).
  - `HIGH_RISK`: Requires `LifecycleTransitionRequest` admin approval queue.
- **Login Permission**: Enforced by `ALLOWED_LOGIN_STATUSES` (`active`, `probation`, `confirmed`, `transferred`, `promoted`, `demoted`, `notice_period`).

## 5. Downstream Modules Reading Employee Data
| Module | Model References | Key Fields Read | Risk Area |
|---|---|---|---|
| **Attendance** (`apps/attendance`) | `Attendance.employee` -> `EmployeeProfile` | `user`, `branch`, `department`, `overtime_enabled` | Ensure `Employee` <-> `EmployeeProfile` sync remains seamless. |
| **Leave** (`apps/leave`) | `LeaveBalance.employee`, `LeaveRequest.employee` -> `EmployeeProfile` | `user`, `branch`, `reporting_manager` | Reporting manager approval chain routing. |
| **Expense** (`apps/expense`) | `Expense.employee` -> `EmployeeProfile` | `user`, `branch`, `reporting_manager` | Expense approval chain routing. |
| **Projects** (`apps/projects`) | `Project.project_managers` M2M -> `EmployeeProfile` | `user`, `is_project_manager` | Project manager selection & assignment. |
| **Staff** (`apps/staff`) | Reads `request.user.employee_profile` | Profile details, leave balance, attendance history | Self-service profile dashboard. |
| **Admin Panel** (`apps/admin_panel`) | `dashboard_services.py`, `search-modal.html` | Status counts, completion %, search indexing | Global search & admin dashboard widgets. |

## 6. Audit Gap Findings & Recommendations
1. **Document RBAC Gate**: Sensitive document downloads currently lack explicit `PermissionEngine.has_permission()` verification in the download view endpoint. Must enforce in Step 5 document handlers.
2. **Multi-Step Progressive Completion**: Current creation form requires immediate User creation with password. Wizard architecture must support progressive saving (Minimum Required: Step 1 + Step 2 creating `Draft` Employee; User created in Step 4 Security).
3. **Single Source of Truth (SSOT)**: Ensure `Employee` (Master) is updated at each wizard step, keeping `EmployeeProfile` legacy bridge in sync via signals.
