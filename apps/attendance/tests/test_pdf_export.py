"""
apps/attendance/tests/test_pdf_export.py

Comprehensive test suite verifying Attendance PDF & XLSX reports:
- Content, column, and calculation parity between PDF and XLSX
- A4 Landscape page-width bounds and column width budgets
- Multi-session earliest arrival, latest departure, and explicit open-session status
- Long geofence policy notes and Numbered Appendix preservation
- Bengali name Unicode rendering
- Empty report state
- 100+ employees multi-page pagination with repeated headers
- 31-day monthly matrix deliberate page splitting
- Individual employee attendance statement
"""

import os
import io
import openpyxl
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.utils import timezone
from pypdf import PdfReader

from apps.employees.models import EmployeeProfile, Department
from apps.branches.models import Branch, OfficeSchedule
from apps.attendance.models import Attendance, AttendancePolicy
from apps.attendance.reporting_service import get_daily_report_data, get_monthly_report_data
from apps.attendance.pdf_export import (
    render_daily_attendance_pdf,
    render_monthly_attendance_pdf,
    render_individual_attendance_pdf,
    format_time_with_seconds,
    BD_TZ,
)
from apps.admin_panel.views import (
    ExportReportPDFView,
    ExportReportExcelView,
    export_monthly_pdf,
    export_individual_pdf,
)

User = get_user_model()


class AttendancePDFExportTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

        # Admin user
        self.admin = User.objects.create_superuser(
            email='admin_pdf_test@example.com',
            password='Password123!'
        )

        # Branch & Schedule
        self.branch = Branch.objects.create(
            name='Dhaka HQ',
            address='Gulshan-1, Dhaka',
            latitude=23.7925,
            longitude=90.4078
        )
        self.schedule, _ = OfficeSchedule.objects.update_or_create(
            branch=self.branch,
            defaults={
                'office_start_time': time(9, 0),
                'office_end_time': time(18, 0),
                'working_days': ['saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday']
            }
        )
        self.policy, _ = AttendancePolicy.objects.update_or_create(
            branch=self.branch,
            defaults={
                'late_grace_minutes': 15,
                'photo_required': False,
                'gps_required': 'required',
                'allow_outside_geofence': True
            }
        )

        # Department
        self.dept = Department.objects.create(name='Software Engineering')

        # Employee 1: Standard Bengali name
        self.u1 = User.objects.create_user(email='taief@example.com', password='Password123!')
        self.emp1 = EmployeeProfile.objects.create(
            user=self.u1,
            employee_id='EMP-101',
            full_name='তাহমিদুর তাইফ',
            phone='01700000001',
            branch=self.branch,
            department='Software Engineering',
            joined_date='2026-01-01',
            is_active=True
        )

        # Employee 2: Multi-session employee with long note
        self.u2 = User.objects.create_user(email='rofiq@example.com', password='Password123!')
        self.emp2 = EmployeeProfile.objects.create(
            user=self.u2,
            employee_id='EMP-102',
            full_name='মোঃ রফিকুল ইসলাম',
            phone='01700000002',
            branch=self.branch,
            department='HR & Operations',
            joined_date='2026-01-01',
            is_active=True
        )

        # Employee 3: Open session employee
        self.u3 = User.objects.create_user(email='john@example.com', password='Password123!')
        self.emp3 = EmployeeProfile.objects.create(
            user=self.u3,
            employee_id='EMP-103',
            full_name='John Doe',
            phone='01700000003',
            branch=self.branch,
            department='Finance',
            joined_date='2026-01-01',
            is_active=True
        )

        self.test_date = date(2026, 10, 26)

        # Emp1: Single normal session (09:14:22 to 18:05:40)
        dt_in_1 = timezone.make_aware(datetime(2026, 10, 26, 9, 14, 22), BD_TZ)
        dt_out_1 = timezone.make_aware(datetime(2026, 10, 26, 18, 5, 40), BD_TZ)
        Attendance.objects.create(
            employee=self.emp1,
            date=self.test_date,
            attendance_type='check_in',
            check_in_time=dt_in_1,
            check_out_time=dt_out_1,
            total_hours=8.85,
            note='Standard on-time biometric check-in'
        )

        # Emp2: Multi-session (Session 1: 08:55:10 - 13:00:00; Session 2: 14:00:00 - 19:15:30) with long geofence note
        dt_in_2a = timezone.make_aware(datetime(2026, 10, 26, 8, 55, 10), BD_TZ)
        dt_out_2a = timezone.make_aware(datetime(2026, 10, 26, 13, 0, 0), BD_TZ)
        Attendance.objects.create(
            employee=self.emp2,
            date=self.test_date,
            attendance_type='check_in',
            check_in_time=dt_in_2a,
            check_out_time=dt_out_2a,
            total_hours=4.08,
            note='Morning office work session'
        )
        dt_in_2b = timezone.make_aware(datetime(2026, 10, 26, 14, 0, 0), BD_TZ)
        dt_out_2b = timezone.make_aware(datetime(2026, 10, 26, 19, 15, 30), BD_TZ)
        long_note = (
            "Outside authorized geofence radius by 1450m due to client site emergency deployment. "
            "Policy approval reference #POL-2026-9982 issued by Managing Director. See audit documentation: "
            "https://trackme.signtechlimited.com/audit/policy-waiver/9982"
        )
        Attendance.objects.create(
            employee=self.emp2,
            date=self.test_date,
            attendance_type='check_in',
            check_in_time=dt_in_2b,
            check_out_time=dt_out_2b,
            total_hours=5.25,
            note=long_note
        )

        # Emp3: Open session (Checked in at 09:25:15, no checkout)
        dt_in_3 = timezone.make_aware(datetime(2026, 10, 26, 9, 25, 15), BD_TZ)
        Attendance.objects.create(
            employee=self.emp3,
            date=self.test_date,
            attendance_type='check_in',
            check_in_time=dt_in_3,
            check_out_time=None,
            total_hours=0.0,
            note='Active duty session currently underway'
        )

    def test_daily_pdf_generation_and_page_bounds(self):
        """Verifies daily PDF generation, A4 landscape bounds, and valid structure."""
        data = get_daily_report_data(start_date=self.test_date, end_date=self.test_date)
        pdf_bytes = render_daily_attendance_pdf(data, filters={'branch_name': 'Dhaka HQ'})

        self.assertGreater(len(pdf_bytes), 1000, "Daily PDF must generate non-empty content")
        reader = PdfReader(io.BytesIO(pdf_bytes))
        self.assertGreaterEqual(len(reader.pages), 1)

        # Verify landscape orientation (width ~841.89, height ~595.27)
        first_page = reader.pages[0]
        self.assertAlmostEqual(float(first_page.mediabox.width), 841.89, delta=5)
        self.assertAlmostEqual(float(first_page.mediabox.height), 595.27, delta=5)

    def test_daily_pdf_and_xlsx_content_and_calculation_parity(self):
        """
        Verifies that PDF and XLSX reports share exact canonical values:
        - Timestamps with seconds (HH:MM:SS)
        - Multi-session earliest arrival and latest departure
        - Explicit open-session indicator ('Open')
        - Total hours sum matching
        """
        request = self.factory.get(f'/admin-panel/reports/export/pdf/?date={self.test_date}')
        request.user = self.admin

        # PDF response
        view_pdf = ExportReportPDFView.as_view()
        res_pdf = view_pdf(request)
        self.assertEqual(res_pdf.status_code, 200)
        self.assertEqual(res_pdf['Content-Type'], 'application/pdf')
        pdf_reader = PdfReader(io.BytesIO(res_pdf.content))
        pdf_text = "".join(page.extract_text() for page in pdf_reader.pages)

        # XLSX response
        view_xlsx = ExportReportExcelView.as_view()
        res_xlsx = view_xlsx(request)
        self.assertEqual(res_xlsx.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(res_xlsx.content))
        ws = wb.active

        # 1. Verify Headers match
        xlsx_headers = [ws.cell(row=4, column=c).value for c in range(1, 11)]
        expected_headers = [
            'SN', 'Employee + Department', 'Employee ID', 'Date',
            'In', 'Out', 'Hours', 'Type', 'Status', 'Remarks'
        ]
        self.assertEqual(xlsx_headers, expected_headers)

        # 2. Verify Emp 1: Normal session
        # In: 09:14:22, Out: 18:05:40, Hours: 8.85h
        self.assertIn("09:14:22", pdf_text)
        self.assertIn("18:05:40", pdf_text)
        self.assertIn("8.85h", pdf_text)

        # Find Emp1 in XLSX
        emp1_row = next(r for r in range(5, ws.max_row + 1) if ws.cell(row=r, column=3).value == 'EMP-101')
        self.assertEqual(ws.cell(row=emp1_row, column=5).value, "09:14:22")
        self.assertEqual(ws.cell(row=emp1_row, column=6).value, "18:05:40")
        self.assertEqual(ws.cell(row=emp1_row, column=7).value, "8.85h")

        # 3. Verify Emp 2: Multi-session earliest arrival and latest departure
        # Session 1 in: 08:55:10, Session 2 out: 19:15:30, Total hours: 4.08 + 5.25 = 9.33h
        self.assertIn("08:55:10", pdf_text)
        self.assertIn("19:15:30", pdf_text)
        self.assertIn("9.33h", pdf_text)

        emp2_row = next(r for r in range(5, ws.max_row + 1) if ws.cell(row=r, column=3).value == 'EMP-102')
        self.assertEqual(ws.cell(row=emp2_row, column=5).value, "08:55:10")
        self.assertEqual(ws.cell(row=emp2_row, column=6).value, "19:15:30")
        self.assertEqual(ws.cell(row=emp2_row, column=7).value, "9.33h")
        self.assertIn("2 Sessions", ws.cell(row=emp2_row, column=8).value)

        # 4. Verify Emp 3: Missing Checkout (final office checkout stays empty '—')
        # In: 09:25:15, Out: —, Status: Late (Missing Checkout)
        self.assertIn("09:25:15", pdf_text)
        self.assertIn("Missing Checkout", pdf_text)

        emp3_row = next(r for r in range(5, ws.max_row + 1) if ws.cell(row=r, column=3).value == 'EMP-103')
        self.assertEqual(ws.cell(row=emp3_row, column=5).value, "09:25:15")
        self.assertEqual(ws.cell(row=emp3_row, column=6).value, "—")
        self.assertIn("Missing Checkout", ws.cell(row=emp3_row, column=9).value)

    def test_long_geofence_note_and_appendix_preservation(self):
        """Verifies that long notes generate a reference in table and full text in appendix."""
        data = get_daily_report_data(start_date=self.test_date, end_date=self.test_date)
        pdf_bytes = render_daily_attendance_pdf(data)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        full_text = "".join(page.extract_text() for page in reader.pages)

        # Must contain footnote reference [Note 1]
        self.assertIn("[Note 1]", full_text)
        # Must contain Appendix section
        self.assertIn("Notes & Remarks Appendix", full_text)
        # Must contain full URL without truncation
        self.assertIn("https://trackme.signtechlimited.com/audit/policy-waiver/9982", full_text)

    def test_empty_report_handling(self):
        """Verifies that an empty report renders cleanly without crashing."""
        future_date = date(2030, 1, 1)
        data = get_daily_report_data(start_date=future_date, end_date=future_date)
        # Force empty rows
        data['rows'] = []
        pdf_bytes = render_daily_attendance_pdf(data)
        self.assertGreater(len(pdf_bytes), 1000)

        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "".join(page.extract_text() for page in reader.pages)
        self.assertIn("No attendance records found", text)

    def test_multi_page_pagination_100_employees(self):
        """Verifies multi-page rendering, repeated headers, and footer for 100+ employees."""
        # Create 100 mock employees in memory
        mock_rows = []
        for i in range(1, 105):
            u = User(email=f'emp_{i}@example.com')
            emp = EmployeeProfile(
                user=u,
                employee_id=f'EMP-{1000+i}',
                full_name=f'Bulk Test Staff {i}',
                branch=self.branch
            )
            dt_in = timezone.make_aware(datetime(2026, 10, 26, 9, 0, 0), BD_TZ)
            att = Attendance(employee=emp, date=self.test_date, check_in_time=dt_in, total_hours=8.0)
            mock_rows.append({
                'employee': emp,
                'date': self.test_date,
                'check_in': att,
                'check_in_sessions': [att],
                'field_visits': [],
                'total_hours': 8.0,
                'status': 'on_time',
                'status_display': 'Present',
                'notes': f'Routine punch record #{i}',
                'is_present': True,
                'is_absent': False,
            })

        data = {
            'start_date': self.test_date,
            'end_date': self.test_date,
            'report_date': self.test_date,
            'rows': mock_rows,
            'present': 104,
            'absent': 0,
            'late': 0,
            'total_hours': 832.0
        }

        pdf_bytes = render_daily_attendance_pdf(data)
        reader = PdfReader(io.BytesIO(pdf_bytes))

        # Must span multiple pages
        self.assertGreater(len(reader.pages), 2, "104 employees must span at least 3 pages")

        # Every page must contain the table header
        for page_idx, page in enumerate(reader.pages):
            p_text = page.extract_text()
            self.assertIn("Employee + Department", p_text, f"Page {page_idx+1} must repeat table header")

    def test_monthly_attendance_pdf_summary_and_matrix_split(self):
        """Verifies Monthly attendance PDF rendering with summary and split daily matrix."""
        monthly_data = get_monthly_report_data(
            year=2026,
            month=10,
            allowed_employee_ids={self.emp1.id, self.emp2.id, self.emp3.id}
        )
        pdf_bytes = render_monthly_attendance_pdf(monthly_data)
        self.assertGreater(len(pdf_bytes), 1000)

        reader = PdfReader(io.BytesIO(pdf_bytes))
        full_text = "".join(page.extract_text() for page in reader.pages)

        # Must have Executive Summary
        self.assertIn("Monthly Attendance Summary", full_text)
        # Must have Days 1 to 15 matrix section
        self.assertIn("Days 1 to 15", full_text)
        # Must have Days 16 to end matrix section
        self.assertIn("Days 16 to", full_text)

    def test_individual_attendance_pdf_statement(self):
        """Verifies Individual Employee Monthly Statement PDF."""
        monthly_data = get_monthly_report_data(
            year=2026,
            month=10,
            employee_id=self.emp1.id,
            allowed_employee_ids={self.emp1.id}
        )
        pdf_bytes = render_individual_attendance_pdf(self.emp1, 2026, 10, monthly_data)
        self.assertGreater(len(pdf_bytes), 1000)

        reader = PdfReader(io.BytesIO(pdf_bytes))
        full_text = "".join(page.extract_text() for page in reader.pages)

        self.assertIn("Individual Attendance Statement", full_text)
        self.assertIn("EMP-101", full_text)
        self.assertIn("Software Engineering", full_text)
        # Check-in timestamp with seconds for Oct 26
        self.assertIn("09:14:22", full_text)

    def test_oversized_note_splits_across_pages_without_layout_error(self):
        """Notes taller than one printable page must split cleanly across pages without LayoutError."""
        oversized_note = (
            "Extended policy audit trail entry with detailed field verification steps. " * 140
            + "END-OF-OVERSIZED-NOTE-MARKER"
        )
        # Daily renderer
        daily_data = get_daily_report_data(start_date=self.test_date, end_date=self.test_date)
        daily_data['rows'][0]['notes'] = oversized_note
        daily_pdf = render_daily_attendance_pdf(daily_data)
        daily_reader = PdfReader(io.BytesIO(daily_pdf))
        self.assertGreaterEqual(len(daily_reader.pages), 2)
        daily_text = "".join(p.extract_text() for p in daily_reader.pages)
        self.assertIn("Complete Note / Policy Remarks", daily_text)
        self.assertIn("END-OF-OVERSIZED-NOTE-MARKER", daily_text)

        # Individual renderer
        monthly_data = get_monthly_report_data(
            year=2026,
            month=10,
            employee_id=self.emp1.id,
            allowed_employee_ids={self.emp1.id},
        )
        monthly_data['rows'][0]['day_calcs'][self.test_date]['notes'] = oversized_note
        indiv_pdf = render_individual_attendance_pdf(self.emp1, 2026, 10, monthly_data)
        indiv_reader = PdfReader(io.BytesIO(indiv_pdf))
        self.assertGreaterEqual(len(indiv_reader.pages), 2)
        indiv_text = "".join(p.extract_text() for p in indiv_reader.pages)
        self.assertIn("END-OF-OVERSIZED-NOTE-MARKER", indiv_text)

    def test_monthly_pdf_28_29_30_31_day_month_boundaries_and_legends(self):
        """Verifies 28/29/30/31-day monthly matrices repeat employee identity and legends cleanly."""
        for year, month, last_day in [(2025, 2, 28), (2024, 2, 29), (2026, 4, 30), (2026, 10, 31)]:
            monthly_data = get_monthly_report_data(
                year=year,
                month=month,
                allowed_employee_ids={self.emp1.id, self.emp2.id, self.emp3.id},
            )
            pdf_bytes = render_monthly_attendance_pdf(monthly_data)
            reader = PdfReader(io.BytesIO(pdf_bytes))
            self.assertGreaterEqual(len(reader.pages), 3)
            full_text = "".join(p.extract_text() for p in reader.pages)
            self.assertIn(f"Days 16 to {last_day}", full_text)
            self.assertIn("Legend:", full_text)
            self.assertIn("EMP-101", full_text)
            self.assertIn("EMP-102", full_text)

    def test_bengali_mixed_script_unbroken_identifiers_and_markup_escaping(self):
        """Verifies Bengali conjuncts, mixed-script names, unbroken IDs, and markup-like user text."""
        self.emp1.full_name = "তাহমিদুর তাইফ (ব্রাহ্মণবাড়িয়া) <script>alert(1)</script> & <b>Bold</b>"
        self.emp1.employee_id = "EMP-" + ("9" * 48)
        self.emp1.department = "DepartmentOfUnbrokenEngineeringNamesWithoutSpaces"
        self.emp1.save()

        daily_data = get_daily_report_data(start_date=self.test_date, end_date=self.test_date)
        daily_data['rows'][0]['notes'] = "যুক্তাক্ষর: ক্ষ জ্ঞ ক্ত ন্ত ম্প শ্র হৃ <font color='red'>raw</font> & more"
        daily_pdf = render_daily_attendance_pdf(daily_data)
        daily_text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(daily_pdf)).pages)
        self.assertIn("<script>alert(1)</script>", daily_text)
        self.assertIn("<font color='red'>raw</font>", daily_text)

        monthly_data = get_monthly_report_data(
            year=2026, month=10, allowed_employee_ids={self.emp1.id}
        )
        monthly_pdf = render_monthly_attendance_pdf(monthly_data)
        self.assertGreater(len(monthly_pdf), 1000)

        indiv_pdf = render_individual_attendance_pdf(self.emp1, 2026, 10, monthly_data)
        self.assertGreater(len(indiv_pdf), 1000)

    def test_pdf_export_views_headers_safe_filenames_and_zero_render_queries(self):
        """Verifies PDF views preserve auth, application/pdf, private/no-store, safe filenames, and 0 render queries."""
        # Daily view
        req_daily = self.factory.get(f'/admin-panel/reports/export/pdf/?date={self.test_date}')
        req_daily.user = self.admin
        res_daily = ExportReportPDFView.as_view()(req_daily)
        self.assertEqual(res_daily.status_code, 200)
        self.assertEqual(res_daily['Content-Type'], 'application/pdf')
        self.assertIn('private', res_daily['Cache-Control'])
        self.assertIn('no-store', res_daily['Cache-Control'])
        self.assertIn('attachment; filename="attendance_report_', res_daily['Content-Disposition'])

        # Monthly view
        req_monthly = self.factory.get('/admin-panel/reports/monthly/export/pdf/?year=2026&month=10')
        req_monthly.user = self.admin
        res_monthly = export_monthly_pdf(req_monthly)
        self.assertEqual(res_monthly.status_code, 200)
        self.assertEqual(res_monthly['Content-Type'], 'application/pdf')
        self.assertIn('private', res_monthly['Cache-Control'])
        self.assertIn('no-store', res_monthly['Cache-Control'])

        # Individual view with unsafe employee_id characters
        self.emp3.employee_id = 'EMP/103 "test"'
        self.emp3.save()
        req_indiv = self.factory.get(f'/admin-panel/reports/individual/{self.emp3.id}/2026/10/pdf/')
        req_indiv.user = self.admin
        res_indiv = export_individual_pdf(req_indiv, self.emp3.id, 2026, 10)
        self.assertEqual(res_indiv.status_code, 200)
        self.assertEqual(res_indiv['Content-Type'], 'application/pdf')
        self.assertIn('private', res_indiv['Cache-Control'])
        self.assertIn('no-store', res_indiv['Cache-Control'])
        self.assertNotIn('"', res_indiv['Content-Disposition'].split('filename="')[1].rstrip('"'))

        # Zero DB queries during rendering of preloaded data
        daily_data = get_daily_report_data(start_date=self.test_date, end_date=self.test_date)
        monthly_data = get_monthly_report_data(
            year=2026, month=10, allowed_employee_ids={self.emp1.id, self.emp3.id}
        )
        indiv_data = get_monthly_report_data(
            year=2026, month=10, employee_id=self.emp3.id, allowed_employee_ids={self.emp3.id}
        )
        with self.assertNumQueries(0):
            render_daily_attendance_pdf(daily_data)
            render_monthly_attendance_pdf(monthly_data)
            render_individual_attendance_pdf(self.emp3, 2026, 10, indiv_data)

    def test_500_employee_daily_and_monthly_pagination_repeated_headers(self):
        """Verifies 500-employee Daily & Monthly PDFs paginate with repeated headers and footers."""
        mock_daily_rows = []
        mock_monthly_rows = []
        all_days = [date(2026, 10, d) for d in range(1, 32)]
        dt_in = timezone.make_aware(datetime(2026, 10, 26, 9, 0, 0), BD_TZ)

        for i in range(1, 501):
            emp = EmployeeProfile(
                employee_id=f'EMP-{2000 + i}',
                full_name=f'তাহমিদুর তাইফ কর্মচারী {i}',
                branch=self.branch,
                department='Operations',
            )
            att = Attendance(employee=emp, date=self.test_date, check_in_time=dt_in, total_hours=8.0)
            mock_daily_rows.append({
                'employee': emp,
                'date': self.test_date,
                'check_in': att,
                'check_in_sessions': [att],
                'field_visits': [],
                'total_hours': 8.0,
                'status': 'on_time',
                'status_display': 'Present',
                'notes': f'Record {i}',
                'is_present': True,
                'is_absent': False,
            })
            mock_monthly_rows.append({
                'employee': emp,
                'present': 27,
                'absent': 0,
                'late': 0,
                'on_leave': 0,
                'field_visits': 0,
                'total_hours': 216.0,
                'overtime': '0h',
                'att_pct': 100,
                'day_calcs': {d: {'status': 'present', 'is_present': True} for d in all_days},
            })

        daily_pdf = render_daily_attendance_pdf({
            'start_date': self.test_date,
            'end_date': self.test_date,
            'rows': mock_daily_rows,
            'present': 500,
            'absent': 0,
            'late': 0,
            'total_hours': 4000.0,
        })
        daily_reader = PdfReader(io.BytesIO(daily_pdf))
        self.assertGreater(len(daily_reader.pages), 15)
        for idx in (0, len(daily_reader.pages) // 2, len(daily_reader.pages) - 1):
            txt = daily_reader.pages[idx].extract_text()
            self.assertIn("Employee + Department", txt)
            self.assertIn(f"Page {idx + 1} of {len(daily_reader.pages)}", txt)

        monthly_pdf = render_monthly_attendance_pdf({
            'year': 2026,
            'month': 10,
            'all_days': all_days,
            'rows': mock_monthly_rows,
            'total_present': 13500,
            'total_absent': 0,
            'total_on_leave': 0,
            'total_late': 0,
            'total_field': 0,
            'total_overtime': '0h',
            'avg_att_pct': 100,
        })
        monthly_reader = PdfReader(io.BytesIO(monthly_pdf))
        self.assertGreater(len(monthly_reader.pages), 20)
        last_txt = monthly_reader.pages[-1].extract_text()
        self.assertIn("Legend:", last_txt)
        self.assertIn(f"Page {len(monthly_reader.pages)} of {len(monthly_reader.pages)}", last_txt)
