from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from datetime import date
from apps.branches.models import Branch
from apps.projects.models import Project

User = get_user_model()

class ProjectTests(TestCase):
    def setUp(self):
        self.password = 'testpassword123'
        self.admin_user = User.objects.create_user(
            email='admin@example.com',
            phone='+8801700000100',
            password=self.password,
            role='admin'
        )
        self.staff_user = User.objects.create_user(
            email='staff@example.com',
            phone='+8801700000200',
            password=self.password,
            role='staff'
        )
        self.branch = Branch.objects.create(
            name='Dhanmondi Branch',
            address='Dhanmondi, Dhaka',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        self.project_data = {
            'name': 'VRF HVAC Installation',
            'client_name': 'ACME Corp',
            'consultant': 'TechConsult Ltd',
            'main_contractor': 'Signtech Building',
            'location': 'Dhaka, Bangladesh',
            'hvac_capacity_tr': '150.00',
            'system_type': 'VRF',
            'start_date': date.today().isoformat(),
            'status': 'Not Started',
            'progress_percent': 0,
            'branch': self.branch.id
        }

    def test_project_list_view_loads_for_admin(self):
        # Log in as admin
        self.client.login(username='+8801700000100', password=self.password)
        
        # Create a project first
        Project.objects.create(
            name='Test Project',
            client_name='Test Client',
            location='Test Location',
            system_type='VRF',
            start_date=date.today(),
            branch=self.branch
        )
        
        response = self.client.get(reverse('projects:project_list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'projects/project_list.html')
        self.assertContains(response, 'Test Project')
        self.assertContains(response, 'Test Client')

    def test_project_create_works_for_admin(self):
        # Log in as admin
        self.client.login(username='+8801700000100', password=self.password)
        
        # Post project data
        response = self.client.post(reverse('projects:project_add'), data=self.project_data)
        
        # Verify redirect to list view
        self.assertRedirects(response, reverse('projects:project_list'))
        
        # Check database
        project = Project.objects.get(name='VRF HVAC Installation')
        self.assertEqual(project.client_name, 'ACME Corp')
        self.assertEqual(project.created_by, self.admin_user)
        self.assertEqual(project.branch, self.branch)

    def test_project_views_redirect_non_admin(self):
        # 1. Anonymous access
        response = self.client.get(reverse('projects:project_list'))
        # Should redirect to login page (dispatch handles this)
        self.assertEqual(response.status_code, 302)
        
        # 2. Staff user access (should redirect to /staff/home/)
        self.client.login(username='+8801700000200', password=self.password)
        response = self.client.get(reverse('projects:project_list'))
        self.assertRedirects(response, '/staff/home/')
        
        # Try to post project add
        response_post = self.client.post(reverse('projects:project_add'), data=self.project_data)
        self.assertRedirects(response_post, '/staff/home/')

