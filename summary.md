# FieldTrack Project Summary

Welcome to the comprehensive feature list, audit, and issues log for the **FieldTrack (Remote Office Attendance System)** built with Django.

---

## 1. Complete Feature List by Module

### 📱 1. PWA & Mobile-First Integration
* **PWA Manifest & Configuration**: Serves `/manifest.json` defining colors, names, icons, and display parameters.
* **Service Worker Cache**: Leverages `/sw.js` to manage background cache and offline capabilities.
* **TWA Integration (Digital Asset Links)**: Endpoint `.well-known/assetlinks.json` verifies the SHA-256 fingerprint for full-screen integration inside an Android Trusted Web Activity (TWA) container.
* **Mobile-Optimized Interface**: Unified mobile-first bottom navigation bar for staff interfaces (`Home`, `Check In`, `Attend.`, `Leave`, `Profile`).
* **Web Audio Sound Effects**: Integrated browser-synthesized audio cues (using Web Audio API, no external MP3s needed) for UI events, with persistent mute/unmute settings saved in `localStorage`.

### 🔐 2. Authentication & User Management (`apps.accounts`)
* **Custom User Model**: Custom `CustomUser` model supporting authentication by either email or phone numbers via a custom authentication backend (`PhoneOrEmailBackend`).
* **Role-Based Authorization**: Distinct roles for `'admin'` and `'staff'`.
* **Access Control Mixins**: Secure views with mixins (`AdminRequiredMixin`, `StaffRequiredMixin`, `RoleRequiredMixin`).
* **Security Hardening**: Enforces SSL redirect, secure session and CSRF cookies, and HSTS when `DEBUG=False`.
* **Password Utility**: Support for password updates for both administrators and employees.

### 👥 3. Employee Profile Management (`apps.employees`)
* **Employee Profiles**: Connects users to `EmployeeProfile` documenting branch assignments, department, designation, and active status.
* **Profile Photos**: Employs `django-imagekit` to automatically transpose and resize photos into web-optimized WEBP formats.
* **Custom Location Sync Rules**: Assigns individual background location tracking intervals (e.g. 5, 10, 15 mins, or disabled).
* **Overtime Flags**: Option to toggle overtime calculations on a per-employee basis.
* **Custom Employee Leave Rules**: Ability to override default leave allocations per employee.

### 📍 4. Attendance & Geofenced Tracking (`apps.attendance` & `apps.staff`)
* **Dual Attendance Types**: Tracks regular check-in sessions or standalone field visits.
* **Geofencing Engine**: Automatically detects whether an employee checked in from the **"Office"** (within branch geofence radius) or the **"Field"** (outside branch radius).
* **Late Check-In Detection**: Triggers automated "Late" markers on the day's first check-in if it occurs past the office start time plus the configured grace period.
* **Real-time Map Visuals**: Detailed check-in views mapping the exact coordinates and photos of employees.
* **Manual Attendance Overrides**: Allows administrators to log manual check-ins with audit notes ("Admin Override Reason").
* **Background Tracking**: Auto-sync pings periodic background location logs while an employee is actively checked in.

### 🏢 5. Branch & Office Schedule Management (`apps.branches`)
* **Branch Geofencing**: Administrators configure physical branch locations with Latitude, Longitude, Geofencing Radius (in meters), and office Wi-Fi IP address.
* **Office Schedule Settings**: Customized schedules per branch defining standard start/end hours, late thresholds, early checkouts, and required background tracking frequencies.
* **Signal Automation**: Django `post_save` signals automatically provision an `OfficeSchedule` when a new Branch is created.

### 🛠️ 6. HVAC Projects Module (`apps.projects`)
* **Project CRUD**: Complete pipeline management for HVAC installations, detailing system capacities, contractor networks, and status.
* **Reusable Task Templates**: Reusable templates containing standard HVAC project steps (e.g., 28-step installation checklist).
* **Sequential Scheduling**: Applying a template automatically sequences planned start and end dates beginning from the project start date, skipping weekends.
* **Daily Progress Logs**: Keeps track of daily planned vs. completed work, supervisor logs, delays, and manpower counts.
* **Manpower Requirements**: Custom trades required per date (e.g., Project Engineer, Duct Tech, Insulation Team).
* **Smart Manpower Auto-fill**: Scans physical site attendance logs to dynamically count matching trade profiles currently active on-site.
* **Materials Log & Tracker**: Tracks ordered vs. received units with quick-increment buttons.
* **Digital Project Sign-offs**: Formalized approvals across four parties (Project Manager, Site Engineer, Consultant, Client Rep).
* **Styled PDF Export**: Generates production-ready project reports compiling schedules, tasks, logs, materials, and digital sign-offs.

### 🏖️ 7. Leave Management System (`apps.leave`)
* **Custom Leave Types**: CRUD configuration of Leave Types with default allowances and Category classification (`casual`, `sick`, `other`).
* **Absence Automation**: Daily automated cron checks (`mark_daily_absences`) log absent profiles and deduct 1 day of balance from their default leave type.
* **Pending Leave Safety**: Prevents double-deduction by skipping employees with pending/approved leave requests during daily absence runs.
* **Retroactive Deductions**: Automatically runs retrospective checks when a pending leave request is rejected, logging absences for skipped days.
* **Overlap Cleanup**: Approving a leave request automatically removes any overlapping absence logs and returns deducted days to prevent conflicts.
* **Negative Balance Carriage**: Prevents clamping remaining leave days at 0, allowing natural negative overdrafts.
* **Year-Bound Leave Balances**: Leaves are tracked, validated, and calculated per calendar year.

### 🔔 8. Notifications & Toast Logs (`apps.notifications`)
* **Real-Time Toasts**: Alpine.js-driven toast notification popups trigger audio cues on actions.
* **In-App Messaging Feed**: Keeps a list of actions and updates (e.g. check-ins, leaves, overrides).
* **Notification Badges**: Dynamic header counters highlighting unread notification counts.

### 📁 9. Database Backup & Google Drive Integration (`apps.backups`)
* **Manual & Auto Backups**: Exports SQLite database dumps.
* **Google Drive Sync**: Auto-uploads backup files to Google Drive using service accounts.
* **Connection Tester**: Includes an HTMX-based "Test Connection" button to test service account credentials and folder IDs in real-time.
* **Retention Cleanup**: Keeps local storage clean by limiting copies to the configured threshold (e.g. last 7 copies).

### 🧹 10. Data Retention Policy (`apps.attendance.retention`)
* **Retention Dashboard**: Highlights expired check-ins and locations older than 3 months.
* **Bulk Removal**: Allows administrators to clear expired records individually or run cleanups on all.
* **Retention command**: Command `run_retention` executes regular retention purging.

### 📊 11. Reports & Exports (`apps.admin_panel`)
* **Pre-made Reports**: Daily summaries, Monthly sheets, Employee history, and Absentee summaries.
* **Multi-Format Exports**: Downloads reports in PDF, CSV, and Excel (monthly XLSX).

---

## 2. Verified Resolved Issues & Warnings

All warnings, inconsistencies, and potential logic bugs identified in the deep dive audit have been resolved and verified against the test suite:

### ✅ 1. Working Days Configuration Mismatch — **RESOLVED**
* **Fix**: Updated `settings.py`'s `WORKING_DAYS` to `[0, 1, 2, 3, 5, 6]` to exclude only Friday (4) in accordance with the standard Bangladesh weekend default. Further, updated the automated absence deduction job (`mark_daily_absences.py`) and the retroactive absence logging on leave rejection (`leave/models.py`) to query each employee's branch-specific schedule (`OfficeSchedule`), falling back to settings only if no schedule is defined.
* **Verification**: Verified using management command dry-run logs and running the leave/attendance test suites.

### ✅ 2. Overnight / Midnight Check-out Overtime Bug — **RESOLVED**
* **Fix**: Modified `calculate_overtime` and `calculate_early_checkout` in `apps/attendance/schedule_utils.py` to accept and utilize the actual check-in session date (`attendance.date`) instead of `datetime.today()`. Standard office thresholds are combined with the session date and cast to timezone-aware local datetimes for robust time and date comparison across day boundaries.
* **Verification**: Tested checkout flow with cross-midnight simulations; all tests passed.

### ✅ 3. Non-existent Manager Role in Mixins — **RESOLVED**
* **Fix**: Modified `StaffOrManagerMixin` in `apps/leave/views.py` to restrict `allowed_roles` strictly to `['staff']`, removing the redundant `'manager'` entry.
* **Verification**: Confirmed that staff-facing leave request views remain accessible and safe.

### ✅ 4. Trailing Slashes Behavior — **RESOLVED**
* **Fix**: Enabled standard Django URL behavior by setting `APPEND_SLASH = True` in `fieldtrack/settings.py`.
* **Verification**: Requests made without trailing slashes are now automatically redirected with a trailing slash as expected.

### ✅ 5. PDF Generation Null Dates Formatting — **RESOLVED**
* **Fix**: Updated `apps/projects/views.py` to check if `task.planned_start` and `task.planned_finish` exist before converting them to strings, falling back to `"—"` if they are null.
* **Verification**: Checked PDF layout output with unscheduled task items; dates display cleanly as dashes.
