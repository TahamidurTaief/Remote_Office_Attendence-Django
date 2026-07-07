# FieldTrack — Smart Attendance & Field Workforce Management System
### Project Documentation & Proposal | Prepared by: Signtech Solutions

---

> **Prepared for:** Client Review  
> **Date:** May 23, 2026  
> **Project Name:** FieldTrack — Remote Office Attendance System  
> **Platform:** Web Application + Android App (PWA/TWA)

---

## 📋 Table of Contents

1. [Project Summary](#1-project-summary)
2. [Technology Stack](#2-technology-stack)
3. [System Overview — Who Uses What](#3-system-overview--who-uses-what)
4. [Admin Features](#4-admin-features-বিস্তারিত)
5. [Staff / Employee Features](#5-staff--employee-features-বিস্তারিত)
6. [Activity Guide — কে কী করতে পারবে](#6-activity-guide--কে-কী-করতে-পারবে)
7. [Automation & Background Jobs](#7-automation--background-jobs)
8. [Security & Data Management](#8-security--data-management)
9. [Conclusion](#9-conclusion)
10. [Quotation](#10-quotation)

---

## 1. Project Summary

**FieldTrack** একটি পূর্ণাঙ্গ Attendance ও Field Workforce Management System যেটি দিয়ে একটি কোম্পানির Admin তার সকল Employee-দের Real-time উপস্থিতি, GPS Location, Field Visit, Late/Overtime ট্র্যাক করতে পারবে।

System টি Web Browser এবং Android Phone উভয়ে কাজ করে। Employee দের জন্য একটি Mobile-friendly App আছে যেটি GPS দিয়ে Check-in/Check-out করে এবং Selfie Photo সহ Attendance নেয়।

**মূল সমস্যা যা এই সিস্টেম সমাধান করে:**

| সমস্যা | FieldTrack এর সমাধান |
|--------|----------------------|
| Employee কোথায় আছে জানা যায় না | Real-time GPS Tracking |
| ভুয়া Attendance দেওয়া সম্ভব | Selfie + GPS Location বাধ্যতামূলক |
| কাগজে Attendance রাখা ঝামেলার | Digital, Auto-Calculated |
| দেরিতে আসা ধরা যায় না | Auto Late Detection |
| Report বানাতে সময় লাগে | One-click PDF/Excel/CSV Export |
| Field Employee এর কাজ ট্র্যাক নেই | Field Visit Logging System |
| Data হারিয়ে যাওয়ার ভয় | Auto Google Drive Backup |

---

## 2. Technology Stack

### Backend
| Technology | বিবরণ |
|-----------|-------|
| **Django 5.x** (Python) | মূল Framework — Fast, Secure, Scalable |
| **SQLite / PostgreSQL** | Database — Dev এ SQLite, Production এ PostgreSQL |
| **django-environ** | Environment Variable Management (Secure Config) |
| **django-imagekit + Pillow** | Image Processing — Photo auto-resize ও WEBP convert |
| **python-dateutil** | Date/Time calculation (Late, Overtime logic) |

### Frontend
| Technology | বিবরণ |
|-----------|-------|
| **Tailwind CSS** | Modern, Responsive UI Design |
| **HTMX** | Page reload ছাড়াই Live Data Update |
| **Alpine.js** | Interactive UI Components |
| **Service Worker (PWA)** | Offline Support, App-like experience |

### Reports & Export
| Technology | বিবরণ |
|-----------|-------|
| **ReportLab** | Professional PDF Report Generation |
| **openpyxl** | Excel (.xlsx) Report Export |
| **CSV Export** | Standard Data Export |

### Integration & Automation
| Technology | বিবরণ |
|-----------|-------|
| **Google Drive API** | Auto Cloud Backup |
| **Cron Jobs** | Scheduled Auto Tasks (Daily Backup, Retention) |
| **Android TWA** | Native Android App (.APK) from Web App |
| **Progressive Web App (PWA)** | Install-able on any phone/desktop |

---

## 3. System Overview — Who Uses What

```
FieldTrack System
│
├── 👑 ADMIN (কোম্পানির মালিক / HR Manager)
│   ├── সব Employee দেখতে পারবে
│   ├── সব Branch manage করবে
│   ├── Reports দেখবে ও Download করবে
│   ├── Notifications পাবে (Late/Missing Alert)
│   └── System Settings Control করবে
│
└── 👤 STAFF (Employee / Field Worker)
    ├── নিজের Attendance দিতে পারবে
    ├── Field Visit log করতে পারবে
    ├── নিজের History দেখতে পারবে
    └── Profile ও Password Update করতে পারবে
```

---

## 4. Admin Features (বিস্তারিত)

### 4.1 Dashboard — Real-time Overview
Admin Login করলে সাথে সাথে আজকের পুরো Picture দেখতে পাবে:

- ✅ **আজকে কতজন Present** (On Time + Late)
- ❌ **আজকে কতজন Absent** (Check-in করেনি)
- ⏰ **কতজন Late এসেছে** (Auto Calculated)
- 🏢 **কতজন Field এ আছে** (Field Visit Active)
- 📋 **Live Attendance Table** — কে কখন Check-in/Out করল
- 🔄 **Auto Refresh** — Page reload ছাড়াই Live Update হয়

---

### 4.2 Attendance Management
সব Employee র সব Attendance একটি জায়গায়:

- **Filter করা যাবে:** Date Range, Employee, Branch, Type (Office/Field), Status (On Time/Late/Absent)
- **Attendance Detail:** প্রতিটি Attendance এ Click করলে GPS Map, Selfie Photo, Check-in/Out Time দেখা যাবে
- **Location Track:** Attendance এর সময় GPS path দেখা যাবে
- **Pagination:** বড় data সহজে browse করা যাবে

---

### 4.3 Manual Attendance Entry
যদি কোনো Employee এর Attendance system এ না ওঠে বা ভুল হয়:

- Admin নিজে Manual Entry দিতে পারবে
- Override Reason লিখতে হবে (Audit Trail)
- Employee, Date, Check-in/Out Time, Type সব manually set করা যাবে

---

### 4.4 Reports System — সব ধরনের Report

#### Daily Report
- নির্দিষ্ট দিনের সব Employee এর Attendance Summary
- Branch ও Date filter সহ

#### Monthly Report
- পুরো মাসের Attendance — কতদিন Present, Absent, Late
- প্রতিটি Employee এর Working Hours Summary

#### Employee-wise Report
- একজন নির্দিষ্ট Employee এর সব Records
- মাস ও দিন অনুযায়ী Detail drill-down

#### Export Options
| Format | কাজ |
|--------|-----|
| **CSV** | Excel এ খুলে edit করা যাবে |
| **PDF** | Professional Report, Print-ready |
| **XLSX (Excel)** | Formatted Monthly Report |

---

### 4.5 Employee Management
সব Employee Manage করার Full Control:

- **Employee যোগ করা** — Name, ID, Department, Designation, Phone, Emergency Contact, Photo
- **Branch Assignment** — কোন Branch এ কাজ করে
- **Tracking Interval** — কত মিনিট পর পর Location Auto-sync হবে (5/10/15/30/60 min বা Disabled)
- **Overtime Enable/Disable** — নির্দিষ্ট Employee এর জন্য Overtime counting on/off
- **Active/Inactive Toggle** — Employee কে System এ Enable/Disable করা
- **Employee Profile View** — সব তথ্য ও Attendance History একসাথে দেখা

---

### 4.6 Branch Management (Geofencing)
প্রতিটি Office Branch এর জন্য:

- **Branch যোগ করা** — Name, Address, GPS Coordinates
- **Geofence Radius** — কত Meter এর মধ্যে থাকলে "Office" Attendance count হবে (default: 100m)
- **WiFi IP** — Office WiFi IP দিয়ে verification option
- **Branch Enable/Disable**

> **Geofencing কী:** Employee Check-in দিলে তার GPS Location আর Branch এর Location compare হয়। যদি সে Branch এর নির্দিষ্ট দূরত্বের মধ্যে থাকে → **Office Attendance**, না থাকলে → **Field Attendance** হিসেবে record হয়।

---

### 4.7 Office Schedule Settings
প্রতিটি Branch এর Working Rules সেট করা যাবে:

| Setting | বিবরণ |
|---------|-------|
| **Office Start Time** | কখন থেকে Office শুরু (e.g., 9:00 AM) |
| **Office End Time** | কখন পর্যন্ত Office (e.g., 6:00 PM) |
| **Late After (Minutes)** | Start এর কত মিনিট পরে আসলে Late (default: 15 min) |
| **Early Checkout (Minutes)** | End এর কত মিনিট আগে বের হলে Early Checkout |
| **Overtime Starts After (Minutes)** | End এর কত মিনিট পরে Overtime শুরু |
| **Working Days** | কোন কোন দিন Working (Sat-Thu, Mon-Fri ইত্যাদি) |

---

### 4.8 Notification System
Admin রা Real-time Notifications পাবে:

| Notification | কখন আসবে |
|-------------|-----------|
| ✅ **Check In** | কোনো Employee Check-in করলে |
| 🚪 **Check Out** | কোনো Employee Check-out করলে |
| ⚠️ **Late Alert** | কেউ Late Check-in করলে |
| 🌍 **Field Visit** | কেউ Field Visit শুরু করলে |
| ❓ **Missing Employee** | কেউ Check-in করেনি সেই Alert |

---

### 4.9 Backup System
Data সুরক্ষিত রাখার জন্য:

- **Manual Backup** — যেকোনো সময় Backup নেওয়া যাবে
- **Auto Daily Backup** — প্রতিদিন রাত ১টায় Auto Backup
- **Auto 3-Day Backup** — প্রতি ৩ দিনে একবার
- **Google Drive Upload** — Backup File সরাসরি Google Drive এ যাবে
- **Download Backup** — Local এ Download করা যাবে
- **Backup Delete** — পুরনো Backup মুছে ফেলা যাবে
- **Drive Connection Test** — Google Drive Setup ঠিকঠাক আছে কিনা Test

---

### 4.10 Expired Data Management
Data Retention Policy:

- **৩ মাস পুরনো** Records → Automatically "Expired" Mark হয়
- **৫ মাস পুরনো** Expired Records → Automatically Delete হয়
- Admin Panel এ Expired Records দেখা যাবে
- Selected বা সব Expired Data একসাথে Delete করা যাবে

---

## 5. Staff / Employee Features (বিস্তারিত)

### 5.1 Home Dashboard
Employee Login করলে দেখতে পাবে:

- আজকের Attendance Status (Checked In / Not Checked In)
- আজকের Field Visits তালিকা
- Quick Action Buttons (Check In / Check Out / Field Visit)

---

### 5.2 Check-In System
**Attendance দেওয়ার নিয়ম:**

1. Employee "Check In" Button এ Click করবে
2. **GPS Location** Auto-detect হবে (বাধ্যতামূলক)
3. **Selfie Photo** তুলতে হবে (বাধ্যতামূলক)
4. Submit করলে:
   - যদি Branch এর 100m এর মধ্যে → **Office Attendance**
   - দূরে থাকলে → **Field Attendance**
   - Late হলে → **Late Status** + Admin কে Alert
5. একই দিনে Multiple Check-in/Out করা যাবে (যেমন বাইরে গিয়ে আবার আসলে)

---

### 5.3 Check-Out System
- Active Session থাকলে "Check Out" Button দেখাবে
- GPS Location Auto-detect হবে
- Check-out করলে **Total Working Hours** Auto Calculate হবে
- Overtime calculate হবে (যদি Enabled থাকে)

---

### 5.4 Field Visit Logging
Field Employee দের জন্য বিশেষ Feature:

Employee যখন Client বা Site এ যাবে:

| Field | বিবরণ |
|-------|-------|
| **Visit Title** | কী কাজে গেছে |
| **Client Name** | কোন Client এর কাছে |
| **Site Address** | কোথায় গেছে |
| **GPS Location** | Auto-detect |
| **Selfie Photo** | যাওয়ার সময় Photo |
| **Note** | অতিরিক্ত তথ্য |

---

### 5.5 Attendance History
নিজের সব Attendance দেখার সুবিধা:

- **মাস অনুযায়ী** দেখা যাবে (আগের মাস/পরের মাস Navigate)
- **Statistics:** সে মাসে কতদিন Present, Absent, Late, Field Visit
- প্রতিটি দিনের Detail (Check-in/Out Time, Type, Status)

---

### 5.6 Profile & Settings
- নিজের Profile Info দেখা (Name, ID, Department, Designation, Branch)
- এই মাসের Attendance Summary
- **Password Change** করার সুবিধা

---

## 6. Activity Guide — কে কী করতে পারবে

### 👑 Admin যা করতে পারবে

```
✅ Login/Logout
✅ Dashboard এ Real-time Attendance দেখা
✅ সব Employee এর Attendance Browse করা
✅ Filter করে নির্দিষ্ট Data খোঁজা
✅ যেকোনো Attendance এর GPS Map ও Photo দেখা
✅ Manual Attendance Entry দেওয়া
✅ Daily/Monthly/Employee Report দেখা
✅ Report PDF/Excel/CSV তে Export করা
✅ নতুন Employee যোগ করা
✅ Employee Info Edit করা
✅ Employee Active/Inactive করা
✅ Branch যোগ/Edit/Delete করা
✅ Geofence Radius সেট করা
✅ Office Schedule সেট করা (সময়, ছুটির দিন)
✅ Late ও Overtime Rules সেট করা
✅ Notifications দেখা (Check-in, Late Alert)
✅ Manual Backup নেওয়া
✅ Google Drive Backup Configure করা
✅ Expired Data দেখা ও Delete করা
✅ Backup Download করা
```

---

### 👤 Staff (Employee) যা করতে পারবে

```
✅ Login/Logout
✅ নিজের Home Dashboard দেখা
✅ GPS + Selfie দিয়ে Check-In করা
✅ Check-Out করা
✅ Field Visit Log করা (Client, Site, Photo)
✅ নিজের Attendance History দেখা (মাস অনুযায়ী)
✅ নিজের Profile দেখা
✅ Password Change করা
✅ App Install করা (Android/iPhone Home Screen এ)

❌ অন্য Employee এর Data দেখতে পারবে না
❌ Admin Panel Access নেই
❌ Report Export করতে পারবে না
❌ Branch/Schedule পরিবর্তন করতে পারবে না
```

---

### 📱 Mobile App এ যা পাওয়া যাবে

**Android APK (TWA — Trusted Web Activity):**
- Play Store ছাড়াই Install করা যাবে
- Native App এর মতো Full Screen Experience
- GPS ও Camera Permission সহ কাজ করে

**PWA (Progressive Web App):**
- যেকোনো Phone এর Browser থেকে "Add to Home Screen"
- Offline Support (Service Worker)
- Push Notification Ready

---

## 7. Automation & Background Jobs

System টিতে নিচের কাজ গুলো Automatically হয়:

| কাজ | সময় | বিবরণ |
|-----|------|-------|
| **Daily Backup** | রাত ১:০০ AM | সব Data এর Backup নেওয়া |
| **3-Day Backup** | রাত ১:৩০ AM | প্রতি ৩ দিনে |
| **Data Retention Check** | রাত ২:০০ AM | ৩ মাস+ পুরনো Data Expire Mark |
| **Auto Drive Upload** | Backup এর পরে | Google Drive এ Upload (যদি Enable) |

---

## 8. Security & Data Management

| Feature | বিবরণ |
|---------|-------|
| **Email-based Login** | Username নয়, Email দিয়ে Login |
| **Role-based Access** | Admin আর Staff আলাদা Panel |
| **CSRF Protection** | Form Submission Secure |
| **Photo Validation** | শুধু Image File Allow, Max 10MB |
| **GPS Validation** | Location ছাড়া Attendance নেওয়া যাবে না |
| **Session Security** | Django Secure Session Management |
| **Data Retention** | ৩ মাস পর Auto Expire, ৫ মাস পর Auto Delete |
| **Backup Encryption** | JSON Format এ Secure Backup |
| **HTTPS Ready** | Production এ SSL Certificate সহ Deploy |

---

## 9. Conclusion

**FieldTrack** একটি **Complete, Production-Ready** Attendance Management System যেটি:

- ✅ **Office Staff** এবং **Field Worker** উভয়ের জন্য কাজ করে
- ✅ **GPS Geofencing** দিয়ে ভুয়া Attendance রোধ করে
- ✅ **Selfie Photo** দিয়ে Identity Verify করে
- ✅ **Real-time Dashboard** এ Admin সব কিছু Monitor করতে পারে
- ✅ **Professional Reports** (PDF/Excel/CSV) মিনিটেই Generate হয়
- ✅ **Google Drive** এ Auto Backup দিয়ে Data সুরক্ষিত থাকে
- ✅ **Android App** হিসেবে Install করা যায়
- ✅ **Automated Cron Jobs** দিয়ে System নিজেই Maintain হয়

এটি একটি **Mid-to-Large Size Company** এর জন্য পূর্ণাঙ্গ সমাধান যেখানে Multiple Branch, Multiple Employee এবং Field Workforce Management করতে হয়।

---

## 10. Quotation

<div align="center">

```
╔══════════════════════════════════════════════════════════════╗
║           PROJECT QUOTATION — FIELDTRACK SYSTEM             ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  FieldTrack — Smart Attendance & Field Workforce System      ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║  ITEM                                          AMOUNT (BDT) ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  ✅ Complete Web Application (Admin + Staff)      ———        ║
║  ✅ Android App (.APK)                            ———        ║
║  ✅ GPS Geofencing System                         ———        ║
║  ✅ Selfie Attendance System                      ———        ║
║  ✅ Reports (PDF + Excel + CSV)                   ———        ║
║  ✅ Google Drive Auto Backup                      ———        ║
║  ✅ Notification System                           ———        ║
║  ✅ Multi-Branch Management                       ———        ║
║  ✅ Overtime & Late Tracking                      ———        ║
║  ✅ Data Retention Automation                     ———        ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║           TOTAL PROJECT COST:  ৳ 25,000 BDT                 ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║  Payment Terms:                                              ║
║    • 50% Advance        →  ৳ 12,500 BDT                     ║
║    • 50% on Delivery    →  ৳ 12,500 BDT                     ║
╠══════════════════════════════════════════════════════════════╣
║  Includes:                                                   ║
║    ✅ Full Source Code                                        ║
║    ✅ Deployment Assistance                                   ║
║    ✅ 30-day Bug Fix Support                                  ║
║    ✅ Admin Training (Online / In-person)                    ║
╚══════════════════════════════════════════════════════════════╝
```

</div>

---

### Terms & Conditions

| বিষয় | বিবরণ |
|------|-------|
| **Validity** | এই Quotation ৩০ দিনের জন্য Valid |
| **Hosting** | Hosting/Domain খরচ আলাদা (Client এর) |
| **Support** | Delivery পরে ৩০ দিন Bug Fix Free |
| **Additional Feature** | নতুন Feature add করতে হলে আলাদা Cost |
| **Source Code** | Full Source Code Delivery দেওয়া হবে |

---

> **Contact for further details and to proceed with the project.**

---

*Document prepared by: Signtech Solutions Development Team*  
*Date: May 23, 2026*
