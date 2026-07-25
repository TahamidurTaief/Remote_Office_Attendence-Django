# Audit Report: Leave Module (FieldTrack ERP)

This audit report outlines the architecture, data schema, request flows, calculation logic, dashboards, and test status of the `apps.leave` module in the FieldTrack ERP system. 

---

## 1. Current Leave Model(s)

The leave module uses three primary models defined in [apps/leave/models.py](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/apps/leave/models.py):

### A. `LeaveType`
Represents the classification of leave:
*   **Fields**:
    *   `name`: CharField (max 100, unique)
    *   `default_days_per_year`: IntegerField
    *   `category`: CharField with choices `sick`, `casual`, `other` (defaults to `other`)
    *   `is_default`: BooleanField (Used for automated absence deductions; only one leave type can be marked as default)

### B. `LeaveBalance`
Tracks the allowance, usage, and remaining days per employee, per leave type, and per calendar year:
*   **Fields**:
    *   `employee`: ForeignKey to `EmployeeProfile`
    *   `leave_type`: ForeignKey to `LeaveType`
    *   `year`: IntegerField
    *   `total_days`: IntegerField (Defaults to `default_days_per_year` or overridden by custom rules)
    *   `used_days`: IntegerField (Days already used/deducted)
*   **Calculated Properties**:
    *   `remaining_days`: Computed dynamically as `total_days - used_days`. There is no `max(0, ...)` clamp, meaning negative balances are naturally calculated and allowed.
*   **Constraints**:
    *   `unique_together = ('employee', 'leave_type', 'year')`

### C. `LeaveRequest`
Represents individual leave applications:
*   **Fields**:
    *   `employee`: ForeignKey to `EmployeeProfile`
    *   `leave_type`: ForeignKey to `LeaveType`
    *   `start_date` / `end_date`: DateFields
    *   `number_of_days`: IntegerField (Auto-calculated on save)
    *   `reason`: TextField
    *   `status`: Choices: `pending`, `approved`, `rejected` (defaults to `pending`)
    *   `requested_at`: DateTimeField (auto_now_add=True)
    *   `sync_uuid`: UUIDField (idempotency token for sync / offline actions)
    *   `client_event_time`: DateTimeField (for offline events timestamp alignment)
    *   `synced_at`: DateTimeField (timestamp when request was synced)
    *   `reviewed_by`: ForeignKey to `AUTH_USER_MODEL`
    *   `reviewed_at`: DateTimeField

---

## 2. Request and Approval Flow

### A. Request Submission
*   **View**: `StaffLeaveRequestCreateView` ([apps/leave/views.py#L293](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/apps/leave/views.py#L293))
*   **Suspension Check**: Checked via the request's user association. Suspended employees (`master_employee.is_suspended`) are prevented from submitting leave requests (raises `PermissionDenied`).
*   **Forms**: `LeaveRequestForm` checks that `end_date` is not before `start_date`, and that the request does not cross calendar year boundaries (must submit separate requests for each year).

### B. Reporting Manager Approval Wiring
The approval/rejection endpoints are handled via:
*   `ApproveLeaveRequestView` ([apps/leave/views.py#L88](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/apps/leave/views.py#L88))
*   `RejectLeaveRequestView` ([apps/leave/views.py#L104](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/apps/leave/views.py#L104))

Both views inherit from `BaseProcessLeaveRequestView` ([apps/leave/views.py#L45](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/apps/leave/views.py#L45)). 
*   **Wiring/Scope Check**: Yes, it is fully connected.
*   **Verification**: The view checks whether the user is a superuser, an admin, or has `leave.approve` permission. If the permission's data scope is not `global` (e.g., manager/team scope), it checks:
    1.  If the reviewing user is the reporting manager of the employee's `master_employee`.
    2.  Failing that, if the employee belongs to the same branch as the reviewing user.
    3.  Failing that, if the employee is assigned to a project managed by the reviewing user.

---

## 3. Leave Balance Calculation Logic

The business logic resides primarily in the overridden `save()` and `delete()` methods of `LeaveRequest`:

*   **Accrual & Balance Deductions**: Leave balances are deducted and updated when a `LeaveRequest` status transitions to `approved`.
*   **Custom Rules**: The total allowance is determined by fetching `EmployeeLeaveRule` (from the employees module) for the specific employee and leave type. If no rule exists, it falls back to `LeaveType.default_days_per_year`.
*   **Negative Balances**: Allowed and naturally computed by the system.
*   **Overlapping Absences Handling**: When a leave request is approved or modified, any existing `AttendanceAbsentLog` for that employee during the overlapping start/end date range is deleted to prevent double deduction.
*   **Rejection Retroactive Logging**: If a request is transitioned to `rejected`, the system retroactively checks workdays (excluding off-days/holidays) from `start_date` to yesterday and logs `AttendanceAbsentLog` with a default deduction leave type if the employee has no attendance.

---

## 4. Approval Chain Level

The approval chain is **single-level** (Manager/Admin):
*   There is no multi-stage workflow (e.g., Manager $\rightarrow$ HR). Any user with the correct role (`admin`/`manager`/superuser) or permission (`leave.approve` scope-validated) can directly approve or reject a pending request.

---

## 5. Reusable Cotton Components

The following components from `templates/cotton/` are reusable or directly touch UI/UX conventions:
*   [badge.html](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/templates/cotton/badge.html) / `<c-badge>`: Used for rendering the request status pill in lists.
*   [card.html](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/templates/cotton/card.html) / `<c-card>`: Container style wrapper.
*   [sidebar.html](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/templates/cotton/sidebar.html) / `<c-sidebar>`: For navigation linking.
*   [status-pill.html](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/templates/cotton/status-pill.html): General status layouts.
*   [audit-table.html](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/templates/cotton/audit-table.html) / `<c-audit-table>`: Suitable for layout transitions of log lists.

---

## 6. Existing Dashboard Widgets

The following widget template files touch or visualize leave data:
*   [templates/dashboard/widgets/widget_employee_leaves.html](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/templates/dashboard/widgets/widget_employee_leaves.html): Visualizes the current user's recent leave applications and statuses.
*   [templates/dashboard/widgets/widget_manager_approvals.html](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/templates/dashboard/widgets/widget_manager_approvals.html): Displays pending team approvals for managers, including leave requests and expense claims.

---

## 7. PermissionEngine Integration

The leave app endpoints are integrated with `PermissionEngine`:
*   `BaseProcessLeaveRequestView` utilizes `PermissionEngine.evaluate(request.user, 'leave.approve')` to grant approval access and check structural data scope (`global` vs `scoped` team/reporting-manager bounds).
*   Other views use `AdminRequiredMixin` or `StaffOrManagerMixin` (subclass of `RoleRequiredMixin`).

---

## 8. Test Baseline

Running tests for the leave app completes successfully:
*   **Command**: `uv run manage.py test apps.leave --verbosity=2`
*   **Results**: **Ran 33 tests**, all passed (`OK`).

---

## 9. Cross-App Dependencies

The Leave app interacts directly with the following models/apps:
*   **`apps.employees`**:
    *   Refers to `EmployeeProfile` and `EmployeeLeaveRule`.
    *   Checks `master_employee.is_suspended` to prevent submissions.
    *   Resolves `reporting_manager` relationships.
*   **`apps.attendance`**:
    *   Imports `Attendance`, `AttendanceAbsentLog`, and `get_default_deduction_leave_type()`.
    *   Deletes overlapping `AttendanceAbsentLog` rows when a leave request is approved.
    *   Retroactively logs absences when a leave request is rejected.
    *   Absence tracking command `mark_daily_absences` checks if the employee has approved/pending leave requests before logging them absent.

---

## 10. Existing Leave Policy Configuration & Future Layers

*   **Config Model**: There is currently no standalone `LeavePolicy` configuration model (like `AttendancePolicy`). 
*   **Gap Identified**: The parameters for leave types (allowance, types, carry-forward, negative balance permissions) are currently scattered between `LeaveType` default properties and `EmployeeLeaveRule` overrides. 
*   **Future Improvement**: An architectural layer mapping to a `LeavePolicy` config could consolidate rules such as carry-forward limits, maximum consecutive days, negative balance permissions, and multi-tier approval configurations.
