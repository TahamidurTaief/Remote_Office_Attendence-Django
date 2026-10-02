from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.leave.models import LeaveRequest, LeaveBalance, LeaveType
from apps.attendance.models import Attendance
from apps.employees.models import EmployeeProfile
from django.utils import timezone
import datetime

User = get_user_model()

class AdminLeaveAttendanceCRUDTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'Password123!'
        self.admin = User.objects.create_superuser(email='admin_crud@example.com', password=self.password, role='admin')
        self.client.login(email='admin_crud@example.com', password=self.password)
        
        from apps.employees.models import Employee, EmployeeProfile
        user = User.objects.create_user(email='emp_crud@example.com', password=self.password)
        emp_master = Employee.objects.create(
            user=user,
            employee_number='EMP-MASTER-CRUD-123',
            first_name='Crud',
            last_name='Employee'
        )
        self.employee = EmployeeProfile.objects.create(
            user=user,
            master_employee=emp_master,
            full_name='Crud Employee',
            joined_date=timezone.now().date(),
            employee_id='EMP-CRUD-123',
            phone='1234567890'
        )
        
        self.leave_type = LeaveType.objects.create(
            name='Casual Leave',
            category='casual',
            default_days_per_year=15,
            is_default=True
        )
        
        # Create a balance
        self.balance = LeaveBalance.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            year=2026,
            total_days=15,
            used_days=0
        )
        
        self.leave_req = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + datetime.timedelta(days=2),
            reason='Test reason',
            status='pending'
        )

        self.attendance = Attendance.objects.create(
            employee=self.employee,
            date=timezone.now().date(),
            check_in_time=timezone.now(),
            type='office',
            status='on_time'
        )

    def test_leave_request_create_post(self):
        url = reverse('admin_panel:leave_request_create')
        data = {
            'employee': self.employee.id,
            'leave_type': self.leave_type.id,
            'start_date': (timezone.now().date() + datetime.timedelta(days=10)).isoformat(),
            'end_date': (timezone.now().date() + datetime.timedelta(days=12)).isoformat(),
            'reason': 'Vacation',
            'status': 'pending'
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(LeaveRequest.objects.filter(reason='Vacation').count(), 1)

    def test_leave_request_edit_post(self):
        url = reverse('admin_panel:leave_request_edit', kwargs={'pk': self.leave_req.pk})
        data = {
            'employee': self.employee.id,
            'leave_type': self.leave_type.id,
            'start_date': self.leave_req.start_date.isoformat(),
            'end_date': self.leave_req.end_date.isoformat(),
            'reason': 'Updated Reason',
            'status': 'approved'
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.leave_req.refresh_from_db()
        self.assertEqual(self.leave_req.reason, 'Updated Reason')
        self.assertEqual(self.leave_req.status, 'approved')

    def test_leave_request_delete_post(self):
        url = reverse('admin_panel:leave_request_delete', kwargs={'pk': self.leave_req.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(LeaveRequest.objects.filter(pk=self.leave_req.pk).exists())

    def test_leave_balance_edit_post(self):
        url = reverse('admin_panel:leave_balance_edit', kwargs={'pk': self.balance.pk})
        data = {
            'total_days': 20,
            'used_days': 2
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.total_days, 20)
        self.assertEqual(self.balance.used_days, 2)

    def test_attendance_create_post(self):
        url = reverse('admin_panel:attendance_create')
        tomorrow = timezone.now() + datetime.timedelta(days=1)
        data = {
            'employee': self.employee.id,
            'date': tomorrow.date().isoformat(),
            'check_in_time': tomorrow.replace(hour=9, minute=0, second=0).strftime('%Y-%m-%dT%H:%M'),
            'check_out_time': tomorrow.replace(hour=17, minute=0, second=0).strftime('%Y-%m-%dT%H:%M'),
            'type': 'office',
            'status': 'on_time',
            'ot_status': 'none'
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Attendance.objects.filter(employee=self.employee).count(), 2)

    def test_attendance_edit_post(self):
        url = reverse('admin_panel:attendance_edit', kwargs={'pk': self.attendance.pk})
        dt = timezone.now()
        data = {
            'employee': self.employee.id,
            'date': self.attendance.date.isoformat(),
            'check_in_time': dt.replace(hour=10, minute=0, second=0).strftime('%Y-%m-%dT%H:%M'),
            'check_out_time': dt.replace(hour=18, minute=0, second=0).strftime('%Y-%m-%dT%H:%M'),
            'type': 'field',
            'status': 'late',
            'ot_status': 'none'
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.type, 'field')
        self.assertEqual(self.attendance.status, 'late')

    def test_attendance_delete_post(self):
        url = reverse('admin_panel:attendance_delete', kwargs={'pk': self.attendance.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Attendance.objects.filter(pk=self.attendance.pk).exists())

    def test_attendance_list_query_bounded_growth(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from apps.branches.models import Branch

        branch_a = Branch.objects.create(name='Branch A', address='A', latitude=23.8, longitude=90.4)
        branch_b = Branch.objects.create(name='Branch B', address='B', latitude=22.8, longitude=91.4)
        self.employee.branch = branch_a
        self.employee.save()

        # Create 20 attendance records
        today = timezone.now().date()
        for i in range(20):
            Attendance.objects.create(
                employee=self.employee,
                date=today - datetime.timedelta(days=i),
                type='office' if i % 2 == 0 else 'field',
                status='on_time' if i % 2 == 0 else 'late',
                attendance_type='check_in'
            )

        list_url = reverse('admin_panel:attendance_list')
        with CaptureQueriesContext(connection) as ctx20:
            resp20 = self.client.get(list_url)
        self.assertEqual(resp20.status_code, 200)
        q20_count = len(ctx20.captured_queries)

        # Scale to 200 records across multiple employees
        more_employees = []
        for j in range(5):
            u = User.objects.create_user(email=f'emp_extra_{j}@example.com', password=self.password)
            emp = EmployeeProfile.objects.create(
                user=u,
                full_name=f'Extra Emp {j}',
                joined_date=today,
                employee_id=f'EMP-EXT-{j}',
                phone=f'98765432{j}',
                branch=branch_a
            )
            more_employees.append(emp)

        for i in range(20, 200):
            emp = more_employees[i % len(more_employees)]
            Attendance.objects.create(
                employee=emp,
                date=today - datetime.timedelta(days=(i % 30)),
                type='office' if i % 3 == 0 else 'field',
                status='on_time' if i % 2 == 0 else 'late',
                attendance_type='check_in'
            )

        with CaptureQueriesContext(connection) as ctx200:
            resp200 = self.client.get(list_url)
        self.assertEqual(resp200.status_code, 200)
        q200_count = len(ctx200.captured_queries)

        growth = q200_count - q20_count
        self.assertLessEqual(growth, 3, f"Query count growth {growth} exceeded bound (q20={q20_count}, q200={q200_count})")

    def test_attendance_list_filtered_results_and_summary_totals(self):
        from apps.branches.models import Branch
        branch = Branch.objects.create(name='Filter Branch', address='FB', latitude=23.8, longitude=90.4)
        self.employee.branch = branch
        self.employee.save()

        today = timezone.now().date()
        Attendance.objects.all().delete()

        # Create known set: 3 office on_time, 2 field late
        for i in range(3):
            Attendance.objects.create(
                employee=self.employee,
                date=today,
                type='office',
                status='on_time',
                attendance_type='check_in'
            )
        for i in range(2):
            Attendance.objects.create(
                employee=self.employee,
                date=today,
                type='field',
                status='late',
                attendance_type='check_in'
            )

        url = reverse('admin_panel:attendance_list') + f'?date_from={today.isoformat()}&date_to={today.isoformat()}&branch={branch.id}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_records'], 5)
        self.assertEqual(resp.context['total_field'], 2)
        self.assertEqual(resp.context['total_present'], 1)  # distinct (employee_id, date)
        self.assertEqual(resp.context['total_late'], 1)     # distinct (employee_id, date)

    def test_attendance_list_cross_branch_scoping_for_manager(self):
        from apps.branches.models import Branch
        from apps.accounts.rbac_models import Role, UserRoleAssignment
        from apps.accounts.models import UserSession

        branch_hq = Branch.objects.create(name='HQ', address='HQ', latitude=23.8, longitude=90.4)
        branch_other = Branch.objects.create(name='Other', address='Other', latitude=22.8, longitude=91.4)

        self.employee.branch = branch_hq
        self.employee.save()

        # Other employee in other branch
        other_user = User.objects.create_user(email='other_branch@example.com', password=self.password)
        other_emp = EmployeeProfile.objects.create(
            user=other_user,
            full_name='Other Branch Emp',
            joined_date=timezone.now().date(),
            employee_id='EMP-OTHER-456',
            phone='444555666',
            branch=branch_other
        )

        today = timezone.now().date()
        Attendance.objects.create(employee=self.employee, date=today, type='office', status='on_time', attendance_type='check_in')
        Attendance.objects.create(employee=other_emp, date=today, type='office', status='on_time', attendance_type='check_in')

        # Manager user (non-superuser with branch-scoped attendance.view)
        from apps.accounts.rbac_models import Module, Action, Permission, RolePermission, DataScope
        manager_user = User.objects.create_user(email='manager_test@example.com', password=self.password, role='manager', is_superuser=False)
        EmployeeProfile.objects.create(
            user=manager_user,
            full_name='Manager User',
            joined_date=today,
            employee_id='EMP-MGR-1',
            phone='999000111',
            branch=branch_hq
        )
        manager_role, _ = Role.objects.get_or_create(code='manager', defaults={'name': 'Manager'})
        UserRoleAssignment.objects.create(user=manager_user, role=manager_role)
        mod, _ = Module.objects.get_or_create(code='attendance', defaults={'name': 'Attendance'})
        act, _ = Action.objects.get_or_create(code='view', defaults={'name': 'View'})
        perm, _ = Permission.objects.get_or_create(module=mod, action=act, codename='attendance.view', defaults={'name': 'View Attendance'})
        RolePermission.objects.update_or_create(
            role=manager_role,
            permission=perm,
            defaults={'data_scope': DataScope.BRANCH}
        )

        self.client.login(email='manager_test@example.com', password=self.password)
        session = self.client.session
        session.save()
        UserSession.objects.create(user=manager_user, session_key=session.session_key, device_id='test_mgr', is_active=True)

        url = reverse('admin_panel:attendance_list') + f'?branch={branch_other.id}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # Context attendances must only belong to manager's branch
        for att in resp.context.get('attendances', []):
            emp = getattr(att, 'employee', None)
            if emp:
                self.assertEqual(emp.branch_id, branch_hq.id)

    def test_attendance_list_missing_optional_relationships_safe(self):
        # Create attendance record with minimal fields (no photo, total_hours None, no project)
        Attendance.objects.all().delete()
        emp_no_branch = EmployeeProfile.objects.create(
            user=User.objects.create_user(email='nobranch@example.com', password=self.password),
            full_name='No Branch Emp',
            joined_date=timezone.now().date(),
            employee_id='EMP-NOBRANCH',
            phone='000111222',
            branch=None,
            master_employee=None
        )
        Attendance.objects.create(
            employee=emp_no_branch,
            date=timezone.now().date(),
            check_in_time=None,
            check_out_time=None,
            total_hours=None,
            photo=None,
            type='office',
            status='on_time',
            attendance_type='check_in'
        )

        url = reverse('admin_panel:attendance_list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No Branch Emp')
        self.assertContains(resp, 'Unassigned')

    def test_attendance_list_realistic_query_bounded_growth(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from apps.branches.models import Branch
        from apps.accounts.rbac_models import Role, UserRoleAssignment, Module, Action, Permission, RolePermission, DataScope
        from apps.accounts.models import UserSession, SecurityPolicy, UserSecurityProfile
        from apps.branches.utils import get_cached_branches
        from apps.accounts.engine import PermissionEngine

        # Build realistic non-superuser admin
        admin_role, _ = Role.objects.get_or_create(code='admin', defaults={'name': 'Admin'})
        mod, _ = Module.objects.get_or_create(code='attendance', defaults={'name': 'Attendance'})
        act, _ = Action.objects.get_or_create(code='view', defaults={'name': 'View'})
        perm, _ = Permission.objects.get_or_create(module=mod, action=act, codename='attendance.view', defaults={'name': 'View Attendance'})
        RolePermission.objects.update_or_create(role=admin_role, permission=perm, defaults={'data_scope': DataScope.GLOBAL})

        real_admin = User.objects.create_user(email='real_admin@example.com', password=self.password, role='admin', is_superuser=False)
        UserRoleAssignment.objects.create(user=real_admin, role=admin_role)
        SecurityPolicy.objects.get_or_create(role='admin', mfa_required=False)
        UserSecurityProfile.objects.get_or_create(user=real_admin, mfa_enabled=True)

        branch1 = Branch.objects.create(name='HQ Branch', latitude=23.8, longitude=90.4)
        branch2 = Branch.objects.create(name='North Branch', latitude=24.0, longitude=90.5)

        EmployeeProfile.objects.create(
            user=real_admin,
            full_name='Realistic Admin',
            branch=branch1,
            joined_date=datetime.date(2026, 1, 1),
            employee_id='ADM-REAL-01',
            phone='+10000000000'
        )

        lt_casual, _ = LeaveType.objects.get_or_create(name='Casual', defaults={'category': 'casual', 'default_days_per_year': 14})
        lt_sick, _ = LeaveType.objects.get_or_create(name='Sick', defaults={'category': 'sick', 'default_days_per_year': 10})

        employees = []
        for i in range(37):
            u = User.objects.create_user(email=f'emp_perf_{i}@example.com', password=self.password, role='staff')
            br = branch1 if i % 2 == 0 else branch2
            ep = EmployeeProfile.objects.create(
                user=u,
                full_name=f'Employee {i:02d}',
                branch=br,
                joined_date=datetime.date(2026, 1, 1),
                employee_id=f'EMP-P-{i:03d}',
                phone=f'+1999{i:07d}'
            )
            LeaveBalance.objects.create(employee=ep, leave_type=lt_casual, year=2026, total_days=14, used_days=2)
            LeaveBalance.objects.create(employee=ep, leave_type=lt_sick, year=2026, total_days=10, used_days=1)
            employees.append(ep)

        now = timezone.now()
        today = now.date()

        # Seed initial 20 attendance records
        for i in range(20):
            emp = employees[i % len(employees)]
            Attendance.objects.create(
                employee=emp,
                date=today - datetime.timedelta(days=i // 37),
                check_in_time=now,
                type='office',
                status='on_time',
                attendance_type='check_in'
            )

        client = Client()
        client.login(email='real_admin@example.com', password=self.password)
        session = client.session
        session.save()
        UserSession.objects.create(user=real_admin, session_key=session.session_key, device_id='perf_dev', is_active=True)

        url = reverse('admin_panel:attendance_list')

        # 1. Cold profile measurement
        with CaptureQueriesContext(connection) as ctx_cold:
            resp_cold = client.get(url)
        self.assertEqual(resp_cold.status_code, 200)
        qcount_cold = len(ctx_cold.captured_queries)

        # 2. Warm request measurement
        get_cached_branches()
        PermissionEngine.evaluate(real_admin, 'attendance.view')

        # Baseline capture with 20 records
        with CaptureQueriesContext(connection) as ctx_20:
            resp_20 = client.get(url)
        self.assertEqual(resp_20.status_code, 200)
        qcount_20 = len(ctx_20.captured_queries)

        # Now add 180 more records -> 200 total records
        for i in range(20, 200):
            emp = employees[i % len(employees)]
            Attendance.objects.create(
                employee=emp,
                date=today - datetime.timedelta(days=i // 37),
                check_in_time=now,
                type='office',
                status='on_time',
                attendance_type='check_in'
            )

        with CaptureQueriesContext(connection) as ctx_200:
            resp_200 = client.get(url)
        self.assertEqual(resp_200.status_code, 200)
        qcount_200 = len(ctx_200.captured_queries)

        import collections
        counts = collections.Counter(q['sql'] for q in ctx_200.captured_queries)
        print(f"\n[PROFILE RESULT] Cold: {qcount_cold} | Warm (20): {qcount_20} | Warm (200): {qcount_200} | Unique SQL: {len(counts)}")
        print("ALL QUERIES in Warm (200):")
        for idx, q in enumerate(ctx_200.captured_queries):
            print(f"[{idx+1}] {q['sql']}")

        # Assertions:
        # - Query growth from 20 to 200 records no greater than 3
        growth = qcount_200 - qcount_20
        self.assertLessEqual(
            growth, 3,
            f"Query growth from 20 records ({qcount_20}) to 200 records ({qcount_200}) was {growth}, expected <= 3"
        )
        self.assertLessEqual(
            qcount_200, 27,
            f"Realistic 200-record query count {qcount_200} exceeded optimized threshold 27"
        )
