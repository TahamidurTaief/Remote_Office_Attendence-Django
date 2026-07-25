# Attendance Module Audit Report

This report presents a read-only audit of the `apps/attendance` module in the FieldTrack ERP codebase.

## 1. Attendance Model & Session Handling
- **Attendance Model (`apps.attendance.models.Attendance`)**:
  - Represents individual attendance sessions (check-in / check-out pairs) or field visits.
  - Supports multiple check-in/out sessions per day.
  - Fields: `employee` (FK to `EmployeeProfile`), `project` (FK to `Project`), `date`, `check_in_time`, `check_out_time`, `type` (office/field), `attendance_type` (check_in/field_visit), `status` (on_time, late, absent, holiday_attendance), `total_hours`, `overtime_minutes`, `ot_status`, `is_expired`, `sync_uuid`, `client_event_time`, `synced_at`.
- **Location Tracking (`apps.attendance.models.AttendanceLocation`)**:
  - Links to `Attendance`. Tracks coordinates (`latitude`, `longitude`), `address`, `accuracy`, and `event` type (check_in, check_out, auto_track).
- **Offline / Sync Capabilities**:
  - Utilizes `sync_uuid`, `client_event_time`, and `synced_at` to validate offline queueing and sync conflicts.
  - `SyncLog` tracks sync batch totals, successes, and failures per employee.

## 2. Check-In & Check-Out Views + URLs
- **Endpoints**:
  - `attendance:check_in` (`/attendance/check-in/`): POST endpoint processing new check-ins, performing geofence verification and status calculation.
  - `attendance:check_out` (`/attendance/check-out/`): POST endpoint to check out of the active session.
  - `staff:check_in` (`/staff/check-in/`): GET view rendering the `templates/staff/check_in.html` camera & GPS page.
- **Frontend/HTMX integration**:
  - Dashboard widgets load and refresh via HTMX polling or manual triggers (e.g. `hx-get="{% url 'dashboard' %}?widget=employee_attendance"`).

## 3. Shift & WeeklyHolidayPolicy Integration
- **Shift Resolution**:
  - `get_branch_schedule(employee)` returns a `DynamicSchedule` wrapper. Resolves shift times from the employee's branch schedule, overriding with individual `Employee.shift` properties if defined.
- **WeeklyHolidayPolicy**:
  - Resolved via `is_employee_holiday(employee, target_date)` in `schedule_utils.py` checking the hierarchical holiday rules.

## 4. Overtime Calculation
- Overtime is computed on check-out or check-out requests.
- Tracked via `overtime_minutes` and approved/rejected via `ot_status` (none, pending, approved, rejected).

## 5. Correction & Forgot-Checkout Flows
- **Forgot Checkout**:
  - Submits `ForgotCheckoutRequest` targeting the unclosed session.
  - Approved hierarchically (manager → HR).
- **Corrections**:
  - Submits `AttendanceCorrectionRequest` modifying `check_in_time` or `check_out_time` with optional reason and attachments.

## 6. Dashboard Widgets
- **Staff**: [widget_employee_attendance.html](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/templates/dashboard/widgets/widget_employee_attendance.html)
- **Manager**: [widget_manager_attendance.html](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/templates/dashboard/widgets/widget_manager_attendance.html)
- **HR**: [hr_dashboard.html](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/templates/dashboard/hr_dashboard.html) (Breakdown graphs)
- **Admin**: [admin_dashboard.html](file:///c:/Users/ISSL/Desktop/Remote_Office_Attendence-Django/templates/dashboard/admin_dashboard.html) (Operations & telemetry panel)

## 7. Reusable Cotton Components
- Located in `templates/cotton/`:
  - `badge.html`: Used for status pills.
  - `table.html`: Reused for logs and lists.
  - `avatar.html`: Reused for employee profile icons in team lists.

## 8. Cross-App Dependencies
The following apps read or modify `Attendance` fields directly:
- **`leave`**: Validates absent logs and resolves default leaves for unexcused absences.
- **`projects`**: Links tasks to attendance project context.
- **`admin_panel`**: Governs approvals, reports, and dashboards.
- **`staff`**: Renders employee portals.

## 9. Baseline Test Count
- The `apps.attendance` module contains **24 baseline tests** covering check-in, check-out, sync logic, and approvals.
