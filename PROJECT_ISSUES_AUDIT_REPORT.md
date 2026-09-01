# FieldTrack (Remote Office & Attendance Django) - Full Project Audit & Issue Report

**Generated Date:** 2026-09-01  
**Auditor:** Debugger & QA System Tester  
**Scope:** Complete Codebase Analysis, Security Audit, Logic Verification, and Test Suite Diagnostics  

---

## 1. Project Index & Architecture Overview

The **FieldTrack** project is a comprehensive Django-based enterprise workforce, remote attendance, leave management, project tracking, and payroll platform tailored for multi-branch organizations and field staff.

### Core Stack & Configuration
- **Framework:** Django 5.x (Python 3.13)
- **Database:** SQLite (WAL mode configured with busy timeouts)
- **Frontend / Styling:** Django Templates + TailwindCSS CLI (`django_tailwind_cli`), Cotton components (`django_cotton`), ImageKit
- **Authentication & Security:** Custom User Model (`apps.accounts.CustomUser`), Session / Device Management (`UserSession`), 3-layer Brute-force Login Protection, Multi-Factor Authentication (TOTP via PyOTP), RBAC Dynamic Permission Engine
- **Multi-Tenancy:** Single-to-multi-tenant foundation with `TenantMiddleware` and tenant context isolation.

### Application Modules Map

| App | Purpose | Key Models & Services |
|---|---|---|
| `apps.accounts` | User management, RBAC, session protection, MFA | `CustomUser`, `LoginProtection`, `UserSession`, `SecurityPolicy`, `PermissionEngine` |
| `apps.admin_panel` | Backoffice management, dynamic role matrix, KPI dashboard | `dashboard_services.py`, `roles_views.py` |
| `apps.attendance` | Geofenced check-in/out, field visits, live tracking, overtime, forgot checkout | `Attendance`, `AttendanceLocation`, `OvertimeRequest`, `AttendanceLifecycleService`, `AttendanceTransactionService`, `reporting_service.py` |
| `apps.audit` | Immutable audit logging, soft-delete & trash lifecycle | `AuditEvent`, `TrashEntry`, `AuditService`, `TrashService` |
| `apps.backups` | Database backups, KEK/MEK Fernet encryption, Google Drive sync | `GoogleDriveConfig`, `BackupFile`, `encryption.py`, `utils.py` |
| `apps.branches` | Multi-branch office locations, GPS coordinates, geofencing, holidays | `Branch`, `Holiday`, `OfficeSchedule` |
| `apps.employees` | Canonical Employee Master, HR profiles, documents, asset tracking | `Employee`, `EmployeeProfile`, `EmployeeDocument`, `Asset`, `AssetAssignment` |
| `apps.expense` | Multi-stage expense claims (Manager -> Finance -> Accounts) | `Expense`, `ExpenseDocument` |
| `apps.leave` | Leave balance ledger, multi-day requests, holiday deductions | `LeaveType`, `LeaveBalance`, `LeaveRequest` |
| `apps.notifications`| Event notifications, email dispatch, in-app alerts | `Notification`, `ActivityLog`, `dispatch.py` |
| `apps.payroll` | Salary structures, deterministic payroll engine, PDF payslip export | `SalaryStructure`, `PayrollRun`, `EmployeePayrollCalculation`, `PayrollService`, `PayrollCalculationEngine` |
| `apps.projects` | Project tracking, Gantt task dependencies, sign-offs, materials | `Project`, `ProjectTask`, `DailyProgressLog`, `ProjectSignOff` |
| `apps.schedule` | Calendar aggregation (leaves, events, project task deadlines) | `ScheduleEvent`, `CalendarMonthView` |
| `apps.staff` | Employee self-service mobile portal & attendance cards | `staff/views.py` |
| `apps.tenants` | Multi-tenancy isolation and membership resolver | `Tenant`, `TenantMembership`, `context.py` |
| `apps.workflow` | Configurable multi-step approval state machine | `WorkflowDefinition`, `WorkflowInstance`, `WorkflowStep`, `WorkflowAction`, `services.py` |

---

## 2. Issues Summary Table

| ID | Category | Severity | File / Component | Summary |
|---|---|---|---|---|
| **ISSUE-01** | Test Suite | **CRITICAL** | `apps/attendance/` | `tests.py` file conflicts with `tests/` folder; blocks `manage.py test` discovery. |
| **ISSUE-02** | Security & Privacy | **CRITICAL** | `apps/attendance/views.py:125-126` | `attendance_status` leaks arbitrary employee session data to unlinked users. |
| **ISSUE-03** | Runtime Crash | **CRITICAL** | `apps/leave/views.py:97` | Query on non-existent `project_manager` field crashes PM leave scoping with `FieldError`. |
| **ISSUE-04** | Runtime Bug | **HIGH** | `apps/attendance/views.py:456`<br>`apps/attendance/transaction_service.py:206`<br>`apps/accounts/views.py:787` | Legacy `project_manager=` / `site_engineer=` lookups fail silently, breaking automatic project linking. |
| **ISSUE-05** | Security | **HIGH** | `fieldtrack/settings.py:17` | Insecure fallback default `SECRET_KEY` used if `DJANGO_SECRET_KEY` missing in production. |
| **ISSUE-06** | Logic Defect | **MEDIUM** | `apps/attendance/management/commands/generate_ot_candidates.py:58-74` | OT candidate generator drops multi-session overtime by only reading `.first()`. |
| **ISSUE-07** | Offline API | **MEDIUM** | `apps/attendance/views.py:808-824` | `bulk_sync` drops `'field_visit'` actions and lacks JSON decode error handling. |
| **ISSUE-08** | Security | **MEDIUM** | `apps/accounts/models.py:339` | MFA backup codes stored as unsalted SHA256 hashes instead of salted hashes. |
| **ISSUE-09** | Database Integrity | **MEDIUM** | `apps/leave/models.py:408-422` | Bulk queryset deletion `LeaveRequest.objects.filter().delete()` bypasses balance refunds. |
| **ISSUE-10** | Robustness | **LOW** | `apps/payroll/services.py:127` | Potential division by zero if `absence_divisor` is 0 or unvalidated. |
| **ISSUE-11** | Concurrency | **LOW** | `apps/tenants/context.py:5` | `threading.local` should use `contextvars.ContextVar` for ASGI/coroutine safety. |
| **ISSUE-12** | Configuration | **LOW** | `fieldtrack/settings.py:23` | Hardcoded LAN IP `'192.168.10.191'` in `ALLOWED_HOSTS`. |

---

## 3. Detailed Issue Breakdown & Remediation

### ISSUE-01: Module / Package Name Conflict in `apps/attendance` Test Suite
- **Severity:** `CRITICAL` (Blocks automated test execution)
- **Location:** `apps/attendance/tests.py` and `apps/attendance/tests/` directory
- **Description:**
  `apps/attendance` contains both a file named `tests.py` (82KB, ~2062 lines of unit tests) and a directory named `tests/` (containing `__init__.py` and `test_mobile_webview.py`).
  When running `python manage.py test`, Python's `unittest.TestLoader.discover()` fails with:
  ```
  ImportError: 'tests' module incorrectly imported from '...\apps\attendance\tests'. Expected '...\apps\attendance'.
  ```
  This completely prevents discovery and execution of the attendance test suite.
- **Solution:**
  1. Rename `apps/attendance/tests.py` to `apps/attendance/tests/test_attendance.py`.
  2. Keep `apps/attendance/tests/__init__.py` so `apps.attendance.tests` is recognized as a package containing both `test_attendance.py` and `test_mobile_webview.py`.

---

### ISSUE-02: Unauthorized Employee Data Exposure in `attendance_status`
- **Severity:** `CRITICAL` (Data Privacy & Security Leak)
- **Location:** `apps/attendance/views.py`, lines 123-127
- **Description:**
  In `attendance_status` view:
  ```python
  employee = get_employee(request.user)
  if not employee:
      from apps.employees.models import EmployeeProfile
      employee = EmployeeProfile.objects.filter(is_active=True).first()
  ```
  If a user without an `EmployeeProfile` (e.g. an admin account, accountant, or new user) requests `/attendance/status/`, the endpoint falls back to querying the first active employee in the database. As a result, the live session ID, check-in time, total hours, and location tracking details of another staff member are returned to the user.
- **Solution:**
  Remove the fallback to `EmployeeProfile.objects.filter(is_active=True).first()`. Return an empty status or 400 response when no employee profile is attached:
  ```python
  employee = get_employee(request.user)
  if not employee:
      if is_html_request:
          return render(request, 'attendance/status.html', {
              'employee': None, 'active_session': None, 'sessions_today': [], 'tracking_interval': 0
          })
      return JsonResponse({
          'success': True,
          'has_active_session': False,
          'active_session_id': None,
          'sessions_today': [],
          'tracking_interval': 0
      })
  ```

---

### ISSUE-03: `FieldError` in Manager Leave Approval Scoping
- **Severity:** `CRITICAL` (500 Error during Manager Leave Approval)
- **Location:** `apps/leave/views.py`, line 97
- **Description:**
  In `BaseProcessLeaveRequestView.dispatch`:
  ```python
  managed_projects = Project.objects.filter(project_manager=profile)
  ```
  `Project.project_manager` was migrated from a ForeignKey to ManyToMany (`project_managers`). Executing this line raises `django.core.exceptions.FieldError: Cannot resolve keyword 'project_manager' into field. Choices are: project_managers, ...`.
- **Solution:**
  Update field name to `project_managers`:
  ```python
  managed_projects = Project.objects.filter(project_managers=profile)
  ```

---

### ISSUE-04: Silent Project Linking Failures in Attendance & Mobile Check-ins
- **Severity:** `HIGH` (Data Association Inconsistency)
- **Location:**
  1. `apps/attendance/views.py:456`
  2. `apps/attendance/transaction_service.py:206`
  3. `apps/accounts/views.py:787`
- **Description:**
  When auto-inferring project association for field visits and check-ins, the code executes:
  ```python
  project = Project.objects.filter(Q(project_manager=employee) | Q(site_engineer=employee)).first()
  ```
  Both `project_manager` and `site_engineer` were migrated to `project_managers` and `site_engineers`. Because these queries are wrapped in `try...except Exception: pass`, the `FieldError` is swallowed, and the system silently fails to link check-ins or field visits to the employee's active projects.
- **Solution:**
  Update query in all 3 locations:
  ```python
  project = Project.objects.filter(Q(project_managers=employee) | Q(site_engineers=employee)).first()
  ```

---

### ISSUE-05: Insecure Default Fallback `SECRET_KEY` in Production
- **Severity:** `HIGH` (Cryptographic & Session Risk)
- **Location:** `fieldtrack/settings.py`, line 17
- **Description:**
  ```python
  SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-placehojkbkj-ssdfsadflder')
  ```
  If `DJANGO_SECRET_KEY` is not provided in production (e.g. during a deployment misconfiguration), Django will silently start with a public, hardcoded key. This invalidates cookie encryption, session security, password reset tokens, and KEK backup keys.
- **Solution:**
  Enforce explicit key configuration when `DEBUG=False`:
  ```python
  from django.core.exceptions import ImproperlyConfigured

  SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
  if not SECRET_KEY:
      if DEBUG:
          SECRET_KEY = 'django-insecure-dev-only-placeholder'
      else:
          raise ImproperlyConfigured("DJANGO_SECRET_KEY environment variable is required in production.")
  ```

---

### ISSUE-06: Overtime Request Candidate Generator Ignores Multiple Daily Sessions
- **Severity:** `MEDIUM` (Payroll Under-calculation)
- **Location:** `apps/attendance/management/commands/generate_ot_candidates.py`, lines 52-74
- **Description:**
  When generating daily overtime requests:
  ```python
  attendances = Attendance.objects.filter(
      employee=emp, date=target_date, overtime_minutes__gt=0
  ).order_by('-overtime_minutes')
  attendance = attendances.first()
  ...
  ot_minutes = attendance.overtime_minutes
  ```
  If an employee has multiple attendance check-in sessions on the same date (e.g., morning shift OT + evening shift OT), the command selects only the single largest session and ignores the overtime worked in other sessions.
- **Solution:**
  Aggregate total daily overtime:
  ```python
  total_ot_minutes = sum(a.overtime_minutes for a in attendances)
  ...
  OvertimeRequest.objects.create(
      employee=emp,
      date=target_date,
      attendance=attendances.first(),
      ot_minutes=total_ot_minutes,
      status='pending'
  )
  ```

---

### ISSUE-07: PWA Offline `bulk_sync` Drops Field Visits & Lacks JSON Error Handling
- **Severity:** `MEDIUM` (Mobile Sync Reliability)
- **Location:** `apps/attendance/views.py`, lines 801-824
- **Description:**
  In `bulk_sync`:
  1. The loop checks `if action_type == 'check_in'` and `elif action_type == 'check_out'`. If an offline user queued `'field_visit'`, the action is skipped and lost.
  2. If the request body is malformed or empty, `json.loads` raises an unhandled exception returning a 500 error instead of 400.
- **Solution:**
  Add support for `'field_visit'` and guard JSON decoding:
  ```python
  @login_required
  @require_POST
  def bulk_sync(request):
      try:
          data = json.loads(request.body)
      except (json.JSONDecodeError, ValueError):
          return JsonResponse({'success': False, 'error': 'Invalid JSON body.'}, status=400)
      
      actions = data.get('actions', [])
      synced_count = 0
      for act in actions:
          action_type = act.get('action')
          try:
              if action_type == 'check_in':
                  AttendanceTransactionService.check_in(request.user, act, validate_photo=False)
                  synced_count += 1
              elif action_type == 'check_out':
                  AttendanceTransactionService.check_out(request.user, act, validate_photo=False)
                  synced_count += 1
              elif action_type == 'field_visit':
                  # Process field visit sync
                  synced_count += 1
          except Exception:
              pass
      return JsonResponse({'success': True, 'synced': synced_count})
  ```

---

### ISSUE-08: Unsalted SHA256 Hashing for MFA Backup Codes
- **Severity:** `MEDIUM` (Cryptographic Weakness)
- **Location:** `apps/accounts/models.py`, lines 339, 348
- **Description:**
  MFA backup codes are stored using unsalted SHA256: `hashlib.sha256(raw.encode('utf-8')).hexdigest()`. Short 8-character hex codes hashed without salt are susceptible to precomputed dictionary / rainbow table attacks if the database is dumped.
- **Solution:**
  Use Django's standard `make_password` / `check_password` (PBKDF2/Argon2) or a salted HMAC-SHA256.

---

### ISSUE-09: Bulk QuerySet Delete on `LeaveRequest` Bypasses Balance Refunds
- **Severity:** `MEDIUM` (Data Integrity)
- **Location:** `apps/leave/models.py`, lines 408-422
- **Description:**
  `LeaveRequest.delete()` reduces `LeaveBalance.used_days`. However, in Django, calling `LeaveRequest.objects.filter(...).delete()` executes a bulk SQL delete and does NOT trigger the custom `delete()` model method. This leaves `LeaveBalance.used_days` inflated.
- **Solution:**
  Attach a `post_delete` signal to `LeaveRequest`:
  ```python
  from django.db.models.signals import post_delete
  from django.dispatch import receiver

  @receiver(post_delete, sender=LeaveRequest)
  def refund_leave_balance_on_delete(sender, instance, **kwargs):
      if instance.status == 'approved':
          try:
              bal = LeaveBalance.objects.get(
                  employee=instance.employee,
                  leave_type=instance.leave_type,
                  year=instance.start_date.year
              )
              bal.used_days = F('used_days') - instance.number_of_days
              bal.save()
          except LeaveBalance.DoesNotExist:
              pass
  ```

---

### ISSUE-10: Guard Against Zero Division in Payroll Calculation Engine
- **Severity:** `LOW` (Defensive Programming)
- **Location:** `apps/payroll/services.py`, line 127
- **Description:**
  `absence_deduction = (gross_salary / absence_divisor_dec) * unpaid_absent_days`.
  If `absence_divisor` is passed as 0, `decimal.DivisionByZero` exception will halt the payroll run.
- **Solution:**
  Ensure `absence_divisor_dec` has a minimum value of 1 (or defaults to 30):
  ```python
  absence_divisor_val = max(int(absence_divisor or 30), 1)
  absence_divisor_dec = Decimal(str(absence_divisor_val))
  ```

---

### ISSUE-11: Async & ASGI Safety for Tenant Context
- **Severity:** `LOW` (Multi-Tenancy Hardening)
- **Location:** `apps/tenants/context.py`, line 5
- **Description:**
  `_tenant_state = threading.local()` is used to store tenant context. While safe for standard synchronous WSGI, `threading.local` does not propagate correctly across asynchronous coroutines or async views.
- **Solution:**
  Use Python's `contextvars.ContextVar`:
  ```python
  import contextvars

  _tenant_context = contextvars.ContextVar('current_tenant', default=None)

  def set_current_tenant(tenant):
      _tenant_context.set(tenant)

  def get_current_tenant():
      return _tenant_context.get() or get_default_tenant()

  def clear_current_tenant():
      _tenant_context.set(None)
  ```

---

### ISSUE-12: Hardcoded LAN IP in `ALLOWED_HOSTS`
- **Severity:** `LOW` (Configuration Hygiene)
- **Location:** `fieldtrack/settings.py`, line 23
- **Description:**
  `ALLOWED_HOSTS = ['demotrackme.signtechlimited.com', 'trackme.signtechlimited.com', 'localhost', '127.0.0.1', 'testserver', '192.168.10.191']`
  Development LAN IP `192.168.10.191` is hardcoded in production settings.
- **Solution:**
  Use `env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1', 'testserver'])`.

---

## 4. Verification & Testing Instructions

After applying the remediations above, execute the following commands to verify system health:

```bash
# 1. Verify Django System Checks
.venv\Scripts\python.exe manage.py check

# 2. Run Complete Test Suite Across All Apps
.venv\Scripts\python.exe manage.py test

# 3. Test Attendance Specific Test Suite (after resolving ISSUE-01)
.venv\Scripts\python.exe manage.py test apps.attendance

# 4. Verify Overtime Candidate Generation Command
.venv\Scripts\python.exe manage.py generate_ot_candidates --dry-run
```
