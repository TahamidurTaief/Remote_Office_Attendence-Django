import datetime
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.messages import get_messages
from apps.branches.models import Branch, OfficeSchedule

User = get_user_model()

class BranchModelAndSignalTests(TestCase):
    def test_branch_creation_and_auto_schedule_signal(self):
        # 1. Create a Branch
        branch = Branch.objects.create(
            name='Dhaka HQ',
            address='Gulshan, Dhaka',
            latitude=23.7925,
            longitude=90.4078,
            radius_meters=150,
            wifi_ip='192.168.1.1',
            is_active=True
        )

        # 2. Verify string representation
        self.assertEqual(str(branch), 'Dhaka HQ')

        # 3. Verify OfficeSchedule was automatically created via post_save signal
        schedule = OfficeSchedule.objects.filter(branch=branch).first()
        self.assertIsNotNone(schedule)
        self.assertEqual(schedule.office_start_time, datetime.time(9, 0))
        self.assertEqual(schedule.office_end_time, datetime.time(18, 0))
        
        # Verify default working days Saturday to Thursday
        expected_working_days = [
            'saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday'
        ]
        self.assertEqual(schedule.working_days, expected_working_days)
        self.assertEqual(str(schedule), 'Schedule - Dhaka HQ')

    def test_office_schedule_threshold_methods(self):
        branch = Branch.objects.create(
            name='Chittagong Branch',
            address='Agrabad, CTG',
            latitude=22.3233,
            longitude=91.8083,
            radius_meters=100,
            wifi_ip='192.168.2.1',
            is_active=True
        )
        schedule = branch.schedule
        schedule.office_start_time = datetime.time(9, 30)
        schedule.office_end_time = datetime.time(17, 30)
        schedule.late_after_minutes = 20
        schedule.early_checkout_before_minutes = 45
        schedule.save()

        # Late threshold: 09:30 + 20 minutes = 09:50
        late_threshold = schedule.get_late_threshold()
        self.assertEqual(late_threshold, datetime.time(9, 50))

        # Early checkout threshold: 17:30 - 45 minutes = 16:45
        early_threshold = schedule.get_early_checkout_threshold()
        self.assertEqual(early_threshold, datetime.time(16, 45))


class BranchCRUDViewTests(TestCase):
    def setUp(self):
        # Create users
        self.admin_user = User.objects.create_user(
            phone='+8801700000001',
            password='adminpassword123',
            role='admin'
        )
        self.staff_user = User.objects.create_user(
            phone='+8801700000002',
            password='staffpassword123',
            role='staff'
        )

        # Create initial Branch
        self.branch = Branch.objects.create(
            name='Sylhet Branch',
            address='Sylhet Sadar',
            latitude=24.8949,
            longitude=91.8687,
            radius_meters=120,
            wifi_ip='192.168.3.1',
            is_active=True
        )

    def test_branch_list_view_access_and_search(self):
        # Guest gets redirected to login page
        url = reverse('branches:branch_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

        # Staff user gets redirected to staff home
        self.client.login(username='+8801700000002', password='staffpassword123')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/staff/home/', response.url)
        self.client.logout()

        # Admin user gets 200 OK
        self.client.login(username='+8801700000001', password='adminpassword123')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sylhet Branch')

        # Test search filter
        response = self.client.get(url + '?search=Sylhet')
        self.assertContains(response, 'Sylhet Branch')

        response = self.client.get(url + '?search=Dhaka')
        self.assertNotContains(response, 'Sylhet Branch')

        # Test status filters
        response = self.client.get(url + '?status=active')
        self.assertContains(response, 'Sylhet Branch')

        response = self.client.get(url + '?status=inactive')
        self.assertNotContains(response, 'Sylhet Branch')

    def test_branch_create_view(self):
        self.client.login(username='+8801700000001', password='adminpassword123')
        url = reverse('branches:branch_add')
        
        # GET form
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # POST valid data
        post_data = {
            'name': 'Rajshahi Branch',
            'address': 'Rajshahi Town',
            'latitude': 24.3745,
            'longitude': 88.6042,
            'radius_meters': 200,
            'wifi_ip': '192.168.4.1',
            'is_active': True
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Branch.objects.filter(name='Rajshahi Branch').exists())

        # Verify success message
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertIn('Branch created successfully.', messages)

    def test_branch_edit_view(self):
        self.client.login(username='+8801700000001', password='adminpassword123')
        url = reverse('branches:branch_edit', args=[self.branch.id])

        # GET form
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # POST edit
        post_data = {
            'name': 'Sylhet Branch Edited',
            'address': 'Sylhet Sadar New Office',
            'latitude': 24.8950,
            'longitude': 91.8688,
            'radius_meters': 150,
            'wifi_ip': '192.168.3.99',
            'is_active': True
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)
        
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.name, 'Sylhet Branch Edited')
        self.assertEqual(self.branch.wifi_ip, '192.168.3.99')

        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertIn('Branch updated successfully.', messages)

    def test_branch_delete_view_deactivates(self):
        self.client.login(username='+8801700000001', password='adminpassword123')
        url = reverse('branches:branch_delete', args=[self.branch.id])

        # Deactivate branch (represented as a soft delete post action)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)

        self.branch.refresh_from_db()
        self.assertFalse(self.branch.is_active)

        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertIn(f'Branch "{self.branch.name}" was deactivated.', messages)
