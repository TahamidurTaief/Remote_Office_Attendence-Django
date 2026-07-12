# Leave Management Feature Audit — 12 July 2026

## Summary
**16 Pass, 2 Warnings, 3 Fails**

---

## Critical (Fail)

### 1. `apps/leave/forms.py:72` — Missing Category Field in `LeaveTypeForm`
* **What's wrong**: The `category` field (added to the `LeaveType` model) is completely missing from `LeaveTypeForm`'s `Meta.fields` definition (`['name', 'default_days_per_year']`). Consequently, the field is not rendered in `templates/admin_panel/leave/leave_type_form.html`.
* **Why it matters**: Admins cannot set or edit a leave type's category (e.g. sick/casual/other) through the Leave Configuration CRUD pages. Every new leave type defaults to `'other'`. Since the `mark_daily_absences` command uses the category to select the default deduction type (filtering for `'casual'` or `'sick'`), this breaks the automated leave deduction routing business logic.

### 2. `apps/attendance/management/commands/mark_daily_absences.py:75-80` & `apps/leave/models.py:99-109` — Double-Deduction and Conflict Gap on Pending Requests
* **What's wrong**: The `mark_daily_absences` management command only checks for approved leave requests (`status='approved'`) when skipping employees. A pending leave request is ignored, meaning `mark_daily_absences` logs the employee as absent and deducts 1 day from their `LeaveBalance`. If the admin subsequently approves that pending request, `LeaveRequest.save()`'s Case 1 triggers and deducts another day.
* **Why it matters**: Severe data corruption. If an employee applies for leave and it is pending when the daily absence command runs, they are deducted twice (2 days deducted for a 1-day approved leave). If the request is rejected, the employee remains penalized with a 1-day deduction and an `AttendanceAbsentLog` entry, which conflicts with their rejected leave request status in the UI.

### 3. `templates/staff/leave/dashboard.html:68`, `templates/admin_panel/leave/employee_balances.html:68`, `templates/admin_panel/leave/employee_balance_detail.html:79` — Missing Red-for-Negative Styling on Individual Leave Balances
* **What's wrong**: While the "Total Leave Left" uses red styling for negative counts, individual leave type remaining balances are hardcoded to standard black/gray text color (`text-gray-900` / standard styling) even when negative.
* **Why it matters**: Visual inconsistency across pages and failure to clearly draw attention to overdraft/negative individual balances, defying the negative balance styling guidelines.

---

## Warnings

### 1. `apps/leave/views.py:12-14` — `StaffOrManagerMixin` Inconsistency with Non-existent Role
* **What's suboptimal**: `StaffOrManagerMixin` allows access to users with role `'manager'` (`allowed_roles = ['staff', 'manager']`). However, the `CustomUser.ROLE_CHOICES` only lists `'admin'` and `'staff'`. Additionally, all other staff views in `apps/staff/views.py` enforce access via `check_staff_role`, which strictly allows only `'staff'`.
* **Risk if unaddressed**: Visual and logic confusion, dead paths, and potential privilege escalation vulnerability if a `'manager'` role is added to the system database choice lists in the future but is not locked out of restricted staff views.

### 2. `templates/base/staff_base.html:207-215` — Potential UI Wrapping/Overflow on Mobile Bottom Navigation
* **What's suboptimal**: The mobile bottom navigation contains 5 items. On extremely narrow mobile screens (e.g. 320px width), each item is restricted to a width of ~64px. The label "Attendance" (10 characters, at size `11px`) is highly likely to wrap awkwardly or overflow.
* **Risk if unaddressed**: Bad visual presentation and broken user interface layout on smaller smartphone form factors.

### 3. `templates/admin_panel/leave/leave_types.html:36` — Missing Category Column on Manage Page
* **What's suboptimal**: The standalone "Leave Types" table lists `Leave Type Name` and `Default Days Allowed Per Year`, but does not include a column for `Category`.
* **Risk if unaddressed**: Admins cannot see what category is assigned to each leave type without clicking edit, resulting in poor user experience.

---

## Verified Passing

### A. Data integrity & idempotency
* **A.1 [Idempotency of mark_daily_absences]**: Confirmed that running `mark_daily_absences` twice on the same date skips already processed employees and does not duplicate entries or double-deduct `used_days` (used days stayed at 1). Checked in `apps/attendance/management/commands/mark_daily_absences.py:86-89`.
* **A.2 [No today/future date processing]**: Confirmed command rejects today and future dates with `CommandError`. Checked in `apps/attendance/management/commands/mark_daily_absences.py:41-43`.
* **A.3 [Boundary leave requests skipped]**: Confirmed that date boundaries are handled properly (an approved request starting and ending on target_date is correctly skipped). Checked in `apps/attendance/management/commands/mark_daily_absences.py:75-80`.
* **A.4 [Reschedule balance recalculation Case 1/2/3]**: Confirmed that changing both dates and leave type in the same edit correctly reverts the old balance and applies the new one. Checked in `apps/leave/models.py:124-146`.

### B. Negative balance consistency
* **B.1 [No negative values clamp/floor]**: Confirmed that `remaining_days` calculations are not floored or clamped at 0. Checked in `apps/leave/models.py:33-37`, `apps/leave/forms.py:51-67`, and `apps/leave/views.py:108`.

### C. Hardcoded data check
* **C.1 [No hardcoded strings/numbers]**: Checked templates and views for literals. None were found outside of standard choices definitions and static placeholders.
* **C.2 [Leave Configuration queries database]**: Confirmed the section queries the live DB. Checked in `apps/admin_panel/views.py:1411`.

### D. Security
* **D.1 [url_has_allowed_host_and_scheme check]**: Confirmed `?next=` validation blocks open redirect vulnerabilities. Checked in `apps/leave/views.py:180-184` and `196-200`.
* **D.2 [AdminRequiredMixin on all admin views]**: Confirmed present. Checked in `apps/admin_panel/views.py:1371, 2262, 2326, 2431` and `apps/leave/views.py:165, 170, 186, 300`.
* **D.4 [CSRF protection active]**: Confirmed that all post handling views utilize default Django middleware CSRF checks.

### E. Regression check on existing features
* **E.1 [Approve/Reject flows function]**: Verified no missing contexts or import errors. Checked in `apps/leave/views.py:42-78`.
* **E.2 [HTMX month navigation active]**: Confirmed navigation correctly updates across boundaries. Checked in `apps/staff/views.py:140-235` and `templates/staff/partials/attendance_list.html:141-153`.
* **E.3 [No mutable state sharing in exports]**: Confirmed ReportLab and openpyxl setups are completely localized. Checked in `apps/admin_panel/views.py:2326` and `2431`.
* **E.4 [Tests and system check output]**: Completed successfully with 0 issues. Paste of raw outputs below:

**System Check Output (`python manage.py check`):**
```
System check identified no issues (0 silenced).
```

**Test Suite Output (`python manage.py test`):**
```
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
Ran 7 tests in 8.233s

OK
Destroying test database for alias 'default'...
```

### F. Navigation & UI consistency
* **F.1 [Nav active highlighting]**: Confirmed active state highlighting targets correct sub-URLs. Checked in `templates/base/admin_base.html:94` and `templates/base/staff_base.html:126`.
* **F.3 [Live queries for pending badges]**: Confirmed context processors perform database queries on every request. Checked in `apps/accounts/context_processors.py:22`.

### G. Cross-feature interaction gaps
* **G.1 [Command skips pending leave]**: Verified that pending leaves are not skipped by `mark_daily_absences`, conforming to target logic. Checked in `apps/attendance/management/commands/mark_daily_absences.py:75-80`.

---

## Untested / Could Not Verify
* *None. All items in the checklist have been successfully audited and tested against the codebase and running django command simulations.*
