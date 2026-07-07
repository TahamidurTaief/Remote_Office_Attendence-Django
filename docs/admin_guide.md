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
