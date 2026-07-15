# FieldTrack Admin Documentation

Welcome to the FieldTrack Admin Guide. This document explains how administrators can moderate and manage the entire attendance system.

## 1. Getting Started
- **Admin Login URL:** `http://127.0.0.1:8000/login/`
- **Dashboard URL:** `http://127.0.0.1:8000/admin-panel/dashboard/`

### Default Credentials (Test Accounts)
- **Admin:** Email: `admin@fieldtrack.com` | Password: `admin123`
- **Staff:** Email: `staff1@fieldtrack.com` | Password: `staff123`
*(Note: Additional staff like staff2@ and staff3@ also use the password staff123)*
- **Manager:** Managers are not auto-created. As an admin, you can create a Manager account from the "Employees" tab and set their email/password there!

## 2. Dashboard Overview
Upon logging in, you will see the **Admin Dashboard**.
- **Live Metrics:** Shows total Present, Absent, Late, and On-Field employees for the current day.
- **Auto-Refresh:** The dashboard automatically updates every 60 seconds.
- **Today's Attendance:** A live feed of who has checked in today and their status.
- **Not Checked In:** A highlight list of employees who haven't reported yet.

## 3. Branch & Geofence Management
Before adding employees, you must set up **Branches**.
- Navigate to the **Branches** tab in the sidebar.
- Click **Add Branch**.
- **Geofencing:** You must provide the exact **Latitude** and **Longitude** of the office. 
- **Radius:** Set the radius (e.g., 100 meters). If a staff member checks in within this radius, they are marked as "Office". If outside, they are marked as "Field".

## 4. Employee Management
You have full control over staff accounts.
- Navigate to the **Employees** tab.
- Click **Add Employee**.
- Fill in the personal details, assign a **Branch**, and select the **Staff** role.
- *Note:* Creating an employee profile automatically generates their user account for logging in.
- You can activate/deactivate an employee if they leave the company.

## 5. Attendance Moderation
- **View Records:** Go to the **Attendance** tab to see all historical check-ins.
- **Filters & Export:** You can filter attendance by Date Range, Branch, or Employee. You can also export this data as a **CSV** file for payroll or HR.
- **Detailed View:** Click "Detail" on any record to see the exact map coordinates and time the employee checked in and out.
- **Manual Entry:** If an employee's phone dies or GPS fails, you can use the **Manual Entry** button to add their attendance. You are required to provide an "Admin Override Reason" for auditing purposes.

## 6. Tailwind CSS Build Steps
FieldTrack uses a compiled and purged Tailwind CSS build via `django-tailwind` instead of loading it from a CDN to optimize production loading performance.

### For Local Development:
To start the JIT watcher and rebuild CSS on the fly when template files are changed:
```bash
python manage.py tailwind start
```

### Before Production Deployment:
Always build the minified production stylesheet to bundle only used utility classes:
```bash
python manage.py tailwind build
python manage.py collectstatic --no-input
```

## 7. HVAC Projects Module

Administrators can track complex HVAC project lifecycles, task scheduling, manpower deployment, materials logs, and project sign-offs.

### 7.1 Creating a Project
1. Navigate to the **Projects** tab in the sidebar.
2. Click **Add Project** in the upper right.
3. Fill in details: project name, client name, consultant, main contractor, system type, capacity (TR), location, and start date.
4. Optionally assign a **Branch**, **Project Manager**, and/or **Site Engineer** from active employee profiles.
5. Click **Create Project**.

### 7.2 Creating and Applying Task Templates
- **Managing Templates**: Go to the **Task Templates** tab from the Projects dropdown to create or edit reusable task lists.
- **Applying a Template**:
  1. Open a project detail page.
  2. Select a template from the **Apply Template** dropdown and click **Apply**.
  3. **Note**: Applying a template sequential-schedules tasks using each task's `default_duration_days` starting from the project's start date, skipping weekends automatically. Any existing tasks will be replaced.

### 7.3 Logging Project Activities & Logs
On the project detail page, administrators can update the following:
- **Project Tasks Checklist**: Check off tasks, edit plans, assign individuals, or delete steps.
- **Daily Progress Logs**: Log daily supervisor names, planned vs. completed work, manpower counts, and delay reasons.
- **Manpower Deployment**: Define required trades and counts for specific dates. Click **Auto-fill** to dynamically count actual check-ins from employees checking in via the FieldTrack attendance scanner on-site.
- **Material Tracking**: Track project materials, total required quantity, received quantity, and balance. Use the **Quick Add** input to quickly increment received quantities.

### 7.4 Project Sign-off & PDF Export
- **Sign-off Blocks**: At the bottom of the project page, authorized parties (Project Manager, Site Engineer, Consultant, Client Representative) can register their sign-offs.
- **Export PDF Work Plan Sheet**: Click the **Export Work Plan Sheet (PDF)** button at the top of the project detail page to download a clean, production-ready, styled PDF summarizing the current status, tasks checklist, progress logs, manpower requirements, materials list, and sign-offs.

