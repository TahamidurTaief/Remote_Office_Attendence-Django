# FieldTrack Staff Documentation

Welcome to the FieldTrack Staff Guide. This document explains how field and office staff will use the system to log their attendance and how location tracking works.

## 1. Getting Started
- **Staff Login URL:** `http://127.0.0.1:8000/login/`
- **Home URL:** `http://127.0.0.1:8000/staff/home/`
- The system is built "Mobile-First", meaning it looks and works perfectly on a smartphone browser.

## 2. The Staff Dashboard
Once logged in, the staff will see their daily summary.
- **Status Card:** Clearly indicates whether they are "Not Checked In", "Checked In", or if the "Shift Ended".
- **Total Hours:** Displays the exact hours worked today.
- **History:** Shows their historical attendance record (Present, Absent, Late).

## 3. How to Check-In / Check-Out
1. **Check-In:** 
   - Click the big **Check In** button on the home screen.
   - The browser will ask for **Location Permissions**. The staff *must* click "Allow".
   - The system will acquire their high-accuracy GPS coordinates.
   - Once coordinates are locked, the staff confirms the check-in.
2. **Check-Out:**
   - At the end of the day, click the **Check Out** button.
   - The system will grab their GPS location one more time to record where the shift ended.

## 4. How Location & Attendance Works
- **Geofencing (Office vs Field):** 
  When the staff checks in, the system compares their current GPS coordinates with their assigned Branch coordinates.
  - If they are **inside** the branch radius (e.g., within 100 meters), their attendance type is logged as **"Office"**.
  - If they are **outside** the branch radius, their attendance type is logged as **"Field"**.
- **Late Detection:**
  The standard office start time is configured in the system (e.g., 9:00 AM) with a 15-minute grace period. If a staff member checks in after 9:15 AM, the system automatically flags them as **"Late"**.
- **Location Tracking:**
  The system *only* tracks location at the exact moment of Check-In and Check-Out. It does not track the staff's location continuously throughout the day, preserving their battery and privacy.
