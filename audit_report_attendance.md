# Audit Report: Attendance Module (apps/attendance)

## 1. Baseline System & Test Status
* **Test Count**: 18 tests total.
* **Execution Status**: OK (all 18 tests pass successfully).
* **Test Command**: `python manage.py test apps.attendance` (or `uv run manage.py test apps.attendance`).

## 2. Current Attendance Data Models
### Attendance (Check-in / Out & Field Visit Session)
* **Model**: `apps.attendance.models.Attendance`
* **Fields**:
  * `employee`: ForeignKey (`apps.employees.EmployeeProfile`)
  * `project`: ForeignKey (`apps.projects.Project`, nullable)
  * `date`: DateField (indexed)
  * `check_in_time`: DateTimeField (nullable)
  * `check_out_time`: DateTimeField (nullable)
  * `type`: CharField (`office`, `field`), default `office`
  * `attendance_type`: CharField (`check_in`, `field_visit`), default `check_in`
  * `status`: CharField (`on_time`, `late`, `absent`), default `on_time`
  * `note`: TextField
  * `total_hours`: DecimalField (5 digits, 2 places, nullable)
  * `visit_title`, `client_name`, `site_address` (for field visits)
  * `photo`: ProcessedImageField (WebP format, resized)
  * `overtime_minutes`: IntegerField (default 0)
  * `is_early_checkout`: BooleanField (default False)
  * `is_expired`: BooleanField (indexed, older than 3 months)
  * `expired_at`: DateTimeField (nullable)
  * `sync_uuid`: UUIDField (unique, indexed)
  * `client_event_time`: DateTimeField (nullable)
  * `synced_at`: DateTimeField (nullable)
  * `created_at`: DateTimeField (auto_now_add)
* **Session Handling**: Multi-session supported. Employees can have multiple check-in/out records per day.
* **Active Status Helper**: Property `is_active_session` returns true if checked in and not checked out.

### AttendanceLocation
* **Model**: `apps.attendance.models.AttendanceLocation`
* **Fields**: `attendance` (FK Attendance), `event` (`check_in`, `check_out`, `auto_track`), `is_expired` (Bool), `latitude` (Decimal), `longitude` (Decimal), `address` (Char), `accuracy` (Float), `event_photo` (ImageField, nullable), `timestamp` (DateTime), `sync_uuid` (UUID), `client_event_time` (DateTime), `synced_at` (DateTime).

### AttendanceAbsentLog
* **Model**: `apps.attendance.models.AttendanceAbsentLog`
* **Fields**: `employee` (FK `EmployeeProfile`), `date` (Date), `leave_type_deducted` (FK `leave.LeaveType`, nullable), `created_at` (DateTime).
* **Constraints**: Unique together (`employee`, `date`).

### SyncLog (Sync Tracking)
* **Model**: `apps.attendance.models.SyncLog`
* **Fields**: `sync_batch_id` (UUID), `employee` (FK `EmployeeProfile`), `started_at` (DateTime), `completed_at` (DateTime), `records_total` (Int), `records_success` (Int), `records_failed` (Int), `failure_reason` (Text).

## 3. Views & URLs Architecture
### URLs (`apps/attendance/urls.py`)
* `check-in/`
* `check-out/`
* `status/`
* `field-visit/`
* `location-sync/`
* `live-locations/`
* `tracking-config/`
* `save-location/`
* `save-location-mandatory/`

### Interaction Patterns
* **JSON APIs**: `check_in`, `check_out`, `field_visit_submit`, and location tracking endpoints return JSON response.
* **Frontend Requests**:
  * `/staff/check-in/` templates use custom Javascript `fetch()` to call `/attendance/check-in/`. On success, performs full page reload redirect to `/staff/home/`.
  * `/staff/attendance-card/` widget loads via HTMX. Checked-out action submits via `fetch()` to `/attendance/check-out/`, then triggers HTMX swap trigger of card widget, or falls back to window reload.
  * `/staff/attendance/` uses HTMX month pagination to request and swap `attendance_list.html` fragments.

## 4. Shift & Holiday Rules Integration
* **Current Status**: Employee `shift` and `weekly_holiday_policy` fields (from `apps.employees.models.Employee` master model) are currently **NOT** read or validated.
* **Schedule Source**: Enforces schedule using `OfficeSchedule` linked to the employee branch (`employee.branch.schedule`). Checks late / early status thresholds.
* **Holiday Policy**: Absence calculation and workday checks fall back to `Holiday` models and system working days settings.

## 5. Overtime (OT) Calculation Logic
* **Location**: `calculate_overtime` in `apps/attendance/schedule_utils.py`.
* **Rules**:
  * Triggers only if `employee.overtime_enabled` is True on `EmployeeProfile`.
  * Compares check-out time against `OfficeSchedule.office_end_time` plus `overtime_after_minutes` padding.
  * Returns overtime duration in minutes.

## 6. Correction / Forgot-Checkout request flow
* **Current Status**: No database schema, views, or logic exist for correction requests or forgot-checkout request workflow.

## 7. Attendance Dashboard Widgets
### Staff Widgets
* `templates/staff/home.html` & `templates/staff/partials/attendance_card.html` (active check-in status card, geolocation trackers, cameras, and checkout buttons).
* `templates/staff/attendance.html` & `templates/staff/partials/attendance_list.html` (history timeline log, status lists).
* `templates/staff/profile.html` (contains 30-day employee attendance log grid).

### Admin / Manager Widgets
* `templates/admin_panel/dashboard.html` (presents today's active attendances count, late counts, field visits, branch totals, map pings).
* `templates/admin_panel/admin_attendance.html` (attendance tabular log, filters, CSV exports).
* `templates/admin_panel/attendance_detail.html` (daily check-in details, mapped GPS auto-track timeline path).

## 8. Cotton Reusable Components
* `templates/cotton/badge.html`: Renders status badges (`late`, `on_time`, `absent`, session types) with variable colors and indicators.
* `templates/cotton/table.html`: Pagination, slot tags, data grids.
* `templates/cotton/profile-card.html`: Core details template (Employee ID, phone, email, shift description).
* `templates/cotton/audit-table.html`: Audit logs grid.
* `templates/cotton/timeline.html` / `timeline-item.html`: GPS track list timeline.
* `templates/cotton/kpi-widget.html`: Dashboard counters widget.
* `templates/cotton/modal.html`: Popups for submissions.

## 9. Downstream App Dependencies
| App | File / Location | Field References | Purpose |
|---|---|---|---|
| **Leave** | `apps/leave/models.py` | `Attendance`, `AttendanceAbsentLog` | Overlap checking. Absent log creation, deletion, and deduction of leave balances. |
| **Projects** | `apps/projects/views.py` | `Attendance.project`, `Attendance.date`, `Attendance.employee` | Manpower deployment auto-fills from actual records. |
| **Employees** | `apps/employees/views.py` | `Attendance` | Stats aggregation (present, late, field totals), history queries. |
