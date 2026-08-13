import datetime
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.employees.models import EmployeeProfile, Branch
from apps.leave.models import LeaveType, LeaveBalance, LeaveRequest

User = get_user_model()


class LeaveReportsViewTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin_leave_reports@example.com',
            password='Password123!',
            role='admin'
        )
        self.staff_user = User.objects.create_user(
            email='staff_leave_reports@example.com',
            password='Password123!',
            role='staff'
        )

        self.branch = Branch.objects.create(name='Dhaka HQ', latitude=23.8103, longitude=90.4125)
        self.employee = EmployeeProfile.objects.create(
            user=self.staff_user,
            full_name='Test Staff Member',
            employee_id='EMP-LR-001',
            branch=self.branch,
            joined_date='2026-01-01'
        )

        self.sick_leave = LeaveType.objects.create(
            name='Sick Leave Test',
            default_days_per_year=10,
            category='sick'
        )
        self.casual_leave = LeaveType.objects.create(
            name='Casual Leave Test',
            default_days_per_year=14,
            category='casual'
        )

        self.balance = LeaveBalance.objects.create(
            employee=self.employee,
            leave_type=self.sick_leave,
            year=2026,
            total_days=10,
            used_days=2
        )

        self.leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.sick_leave,
            start_date=datetime.date(2026, 7, 10),
            end_date=datetime.date(2026, 7, 12),
            reason='Fever & Doctor rest',
            status='approved'
        )

    def test_leave_monthly_report_view_admin_access(self):
        self.client.force_login(self.admin_user)
        url = reverse('admin_panel:reports_leave_monthly')
        response = self.client.get(url, {'year': 2026, 'month': 7})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'admin_panel/reports/leave_monthly.html')
        self.assertIn('page_obj', response.context)
        self.assertIn('rows', response.context)
        self.assertIn('leave_types', response.context)
        self.assertEqual(response.context['total_approved_days'], 3)

    def test_leave_employee_report_view_admin_access(self):
        self.client.force_login(self.admin_user)
        url = reverse('admin_panel:reports_leave_employee', kwargs={'pk': self.employee.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'admin_panel/reports/leave_employee.html')
        self.assertEqual(response.context['employee'], self.employee)
        self.assertIn('leave_balances', response.context)
        self.assertIn('leave_requests', response.context)

    def test_leave_employee_report_view_month_variant(self):
        self.client.force_login(self.admin_user)
        url = reverse('admin_panel:reports_leave_employee_month', kwargs={'pk': self.employee.pk, 'year': 2026, 'month': 7})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['year'], 2026)
        self.assertEqual(response.context['month'], 7)

    def test_leave_export_csv(self):
        self.client.force_login(self.admin_user)
        url = reverse('admin_panel:reports_leave_export_csv')
        response = self.client.get(url, {'employee': self.employee.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('attachment; filename="leave_report_', response['Content-Disposition'])

    def test_leave_export_pdf(self):
        self.client.force_login(self.admin_user)
        url = reverse('admin_panel:reports_leave_export_pdf')
        response = self.client.get(url, {'employee': self.employee.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment; filename="leave_report_', response['Content-Disposition'])

    def test_leave_export_csv_clamping(self):
        # LeaveRequest spans 2026-07-10 to 2026-07-12 (3 days)
        # Request a CSV filtered to 2026-07-11 to 2026-07-20
        self.client.force_login(self.admin_user)
        url = reverse('admin_panel:reports_leave_export_csv')
        response = self.client.get(url, {
            'employee': self.employee.pk,
            'date_from': '2026-07-11',
            'date_to': '2026-07-20'
        })
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        # Row format: SN, Employee, Employee ID, Branch, Leave Type, Start Date, End Date, Days, Status...
        # Let's inspect the days value in the row. Since the range is July 11-20, only July 11 and July 12 should be counted.
        # July 10 is outside the range. July 11 & July 12 should yield 2 days.
        lines = [line for line in content.split('\r\n') if line]
        self.assertEqual(len(lines), 2)  # Header + 1 row
        self.assertIn(',2,Approved', lines[1])

    def test_leave_export_xlsx(self):
        self.client.force_login(self.admin_user)
        url = reverse('admin_panel:reports_leave_export_xlsx')
        response = self.client.get(url, {'year': 2026, 'month': 7})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        self.assertIn('attachment; filename="monthly_leave_report_', response['Content-Disposition'])

    def test_non_admin_access_denied(self):
        self.client.force_login(self.staff_user)
        url = reverse('admin_panel:reports_leave_monthly')
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 403])
