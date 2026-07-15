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

## 2. Issues, Warnings, and Mismatches

During our deep dive code review, we identified the following warnings, inconsistencies, and potential logic bugs:

### ⚠️ 1. Working Days Configuration Mismatch
* **Location**: `fieldtrack/settings.py` vs. `apps/branches/models.py` (lines 95-98)
* **What's wrong**: In `settings.py`, `WORKING_DAYS` is set to `[0, 1, 2, 3, 4, 5]` (Monday to Saturday, Sunday excluded), which is used by the `mark_daily_absences` management command. However, the default `OfficeSchedule` created via django signals defines working days as `['saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday']` (Sunday included, Friday excluded).
* **Why it matters**: If a branch operates on the default schedule, the automated daily absence cron job will not align with local weekend rules, leading to potential false absence marks on Sundays and missed absence logs on Fridays.

### ⚠️ 2. Overnight / Midnight Check-out Overtime Bug
* **Location**: `apps/attendance/schedule_utils.py` (lines 46-54)
* **What's wrong**: The `calculate_overtime` function determines overtime by combining the check-out time with `datetime.today()`.
* **Why it matters**: If an employee checks out after midnight (e.g. 01:00 AM on the following day), the check-out is combined with today's date, causing the checkout time (01:00 AM) to evaluate as *before* the office end time (e.g. 06:00 PM today). This deprives the employee of overtime and erroneously marks the session as an early checkout.

### ⚠️ 3. Non-existent Manager Role in Mixins
* **Location**: `apps/leave/views.py` (lines 12-14)
* **What's wrong**: `StaffOrManagerMixin` explicitly lists `'manager'` as an allowed role (`allowed_roles = ['staff', 'manager']`). However, the `CustomUser.ROLE_CHOICES` only lists `'admin'` and `'staff'`.
* **Why it matters**: A role named `'manager'` cannot be assigned to user records under the current schema, making the inclusion of `'manager'` in `allowed_roles` redundant. If a new manager role is introduced in the future, it might be allowed into staff-facing views without additional restrictions.

### ⚠️ 4. Trailing Slashes Behavior
* **Location**: `fieldtrack/settings.py` (line 130)
* **What's wrong**: `APPEND_SLASH` is explicitly set to `False`.
* **Why it matters**: Django will not automatically append slashes to request URLs. Standard URLs configured with trailing slashes (e.g., `/backups/settings/`) will return a 404 error if requested without the trailing slash, demanding strict URL formatting by front-end clients and HTMX targets.

### ⚠️ 5. PDF Generation Null Dates Formatting
* **Location**: `apps/projects/views.py` (lines 625-626)
* **What's wrong**: In the PDF generation logic for HVAC project plan sheets, `task.planned_start` and `task.planned_finish` are directly cast to strings (`str(task.planned_start)`).
* **Why it matters**: If a task has not been planned yet (meaning the dates are null in the database), the PDF output will print the literal text `"None"` in the columns instead of a cleaner placeholder like `"—"` or empty space.
