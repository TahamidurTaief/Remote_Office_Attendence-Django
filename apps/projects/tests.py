from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from datetime import date
from apps.branches.models import Branch
from apps.projects.models import Project, TaskTemplate, TaskTemplateItem, ProjectTask, DailyProgressLog, ManpowerDeployment, ProjectMaterial, ProjectSignOff

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

    def test_hvac_template_seeding(self):
        # Verify that the HVAC standard template is seeded
        template = TaskTemplate.objects.filter(name="HVAC Installation - Standard (28 Step)").first()
        self.assertIsNotNone(template)
        self.assertEqual(template.items.count(), 28)
        
        # Verify the sequence of some key steps
        step_1 = template.items.get(order=1)
        self.assertEqual(step_1.activity, "Contract Award & Kick-off Meeting")
        self.assertEqual(step_1.default_responsible_role, "Project Manager")
        
        step_28 = template.items.get(order=28)
        self.assertEqual(step_28.activity, "Warranty & Maintenance Support")
        self.assertEqual(step_28.default_responsible_role, "Service Department")

    def test_apply_template_sequential_scheduling(self):
        self.client.login(username='+8801700000100', password=self.password)
        
        # Create project
        project = Project.objects.create(
            name='Test Project for Tasks',
            client_name='Test Client',
            location='Test Location',
            system_type='VRF',
            start_date=date(2026, 7, 15),
            branch=self.branch
        )
        
        template = TaskTemplate.objects.get(name="HVAC Installation - Standard (28 Step)")
        
        # Apply template
        response = self.client.post(
            reverse('projects:project_apply_template', kwargs={'project_id': project.id}),
            data={'template_id': template.id}
        )
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': project.id}))
        
        # Verify tasks created
        tasks = ProjectTask.objects.filter(project=project).order_by('order')
        self.assertEqual(tasks.count(), 28)
        
        # Check scheduling of first task (Seq 1): start should be project.start_date (2026-07-15)
        # Duration is 5 days (from seeding), so planned_finish should be start + 4 days (2026-07-19)
        task_1 = tasks.get(order=1)
        self.assertEqual(task_1.planned_start.isoformat(), "2026-07-15")
        self.assertEqual(task_1.planned_finish.isoformat(), "2026-07-19")
        self.assertEqual(task_1.duration_days, 5)
        
        # Check scheduling of second task (Seq 2): start should be task_1.finish + 1 day (2026-07-20)
        # planned_finish should be 2026-07-24
        task_2 = tasks.get(order=2)
        self.assertEqual(task_2.planned_start.isoformat(), "2026-07-20")
        self.assertEqual(task_2.planned_finish.isoformat(), "2026-07-24")

    def test_task_status_update_inline(self):
        self.client.login(username='+8801700000100', password=self.password)
        
        project = Project.objects.create(
            name='Test Project for Status',
            client_name='Test Client',
            location='Test Location',
            system_type='VRF',
            start_date=date.today(),
            branch=self.branch
        )
        
        task = ProjectTask.objects.create(
            project=project,
            order=1,
            activity='Duct Installation',
            status='Not Started'
        )
        
        response = self.client.post(
            reverse('projects:project_task_update_status', kwargs={'pk': task.pk}),
            data={'status': 'In Progress'}
        )
        self.assertEqual(response.status_code, 200)
        
        # Verify in database
        task.refresh_from_db()
        self.assertEqual(task.status, 'In Progress')
        # Check if returned HTML has the updated select option selected
        self.assertContains(response, 'value="In Progress" selected')

    def test_project_task_views_redirect_non_admin(self):
        # Try to post status update as staff user (should redirect/deny)
        project = Project.objects.create(
            name='Test Project non-admin',
            client_name='Test Client',
            location='Test Location',
            system_type='VRF',
            start_date=date.today(),
            branch=self.branch
        )
        
        task = ProjectTask.objects.create(
            project=project,
            order=1,
            activity='Duct Installation',
            status='Not Started'
        )
        
        self.client.login(username='+8801700000200', password=self.password)
        
        # Apply template
        template = TaskTemplate.objects.get(name="HVAC Installation - Standard (28 Step)")
        response = self.client.post(
            reverse('projects:project_apply_template', kwargs={'project_id': project.id}),
            data={'template_id': template.id}
        )
        self.assertRedirects(response, '/staff/home/')
        
        # Status update
        response_status = self.client.post(
            reverse('projects:project_task_update_status', kwargs={'pk': task.pk}),
            data={'status': 'In Progress'}
        )
        self.assertRedirects(response_status, '/staff/home/')

    def test_daily_progress_log_crud_and_permissions(self):
        # 1. Create a project
        project = Project.objects.create(
            name='Test Progress Project',
            client_name='Test Client',
            location='Test Location',
            system_type='VRF',
            start_date=date.today(),
            branch=self.branch
        )

        # 2. Test permission constraint for non-admin user
        self.client.login(username='+8801700000200', password=self.password) # staff user
        log_data = {
            'date': date.today().isoformat(),
            'planned_work': 'Install ducts',
            'completed_work': 'Main ducts installed',
            'manpower_count': 5,
            'delay_reason': '',
            'supervisor_name': 'Supervisor Staff'
        }
        
        # Adding log should redirect to staff home
        response = self.client.post(
            reverse('projects:progress_log_add', kwargs={'project_id': project.id}),
            data=log_data
        )
        self.assertRedirects(response, '/staff/home/')
        self.assertEqual(DailyProgressLog.objects.count(), 0)

        # 3. Log in as admin and create progress log
        self.client.login(username='+8801700000100', password=self.password) # admin user
        response = self.client.post(
            reverse('projects:progress_log_add', kwargs={'project_id': project.id}),
            data=log_data
        )
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertEqual(DailyProgressLog.objects.count(), 1)
        log = DailyProgressLog.objects.first()
        self.assertEqual(log.planned_work, 'Install ducts')
        self.assertEqual(log.logged_by, self.admin_user)
        self.assertEqual(log.project, project)

        # 4. Check if list displays correctly in reverse-chronological order on detail page
        # Let's create an older log and a newer log
        log_older = DailyProgressLog.objects.create(
            project=project,
            date=date(2026, 7, 10),
            planned_work='Old plan',
            completed_work='Old complete',
            manpower_count=2,
            supervisor_name='Old supervisor',
            logged_by=self.admin_user
        )
        log_newer = DailyProgressLog.objects.create(
            project=project,
            date=date(2026, 7, 20),
            planned_work='New plan',
            completed_work='New complete',
            manpower_count=3,
            supervisor_name='New supervisor',
            logged_by=self.admin_user
        )

        response = self.client.get(reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertEqual(response.status_code, 200)
        # Verify both logs are in response content
        self.assertContains(response, 'Old plan')
        self.assertContains(response, 'New plan')
        # Check ordering of logs in template context: reverse chronological by date
        logs = list(response.context['progress_logs'])
        # log_newer has date 2026-07-20, log has date.today() (2026-07-15), log_older has date 2026-07-10.
        # So newer first: log_newer, then log (date 15), then log_older (date 10).
        self.assertEqual(logs[0], log_newer)
        self.assertEqual(logs[1], log)
        self.assertEqual(logs[2], log_older)

        # 5. Update/Edit progress log as Admin
        edit_url = reverse('projects:progress_log_edit', kwargs={'pk': log.pk})
        edit_data = {
            'date': date.today().isoformat(),
            'planned_work': 'Updated plan',
            'completed_work': 'Updated complete',
            'manpower_count': 10,
            'delay_reason': 'Rain',
            'supervisor_name': 'Updated Supervisor'
        }
        response = self.client.post(edit_url, data=edit_data)
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': project.id}))
        log.refresh_from_db()
        self.assertEqual(log.planned_work, 'Updated plan')
        self.assertEqual(log.manpower_count, 10)
        self.assertEqual(log.delay_reason, 'Rain')

        # Try to edit as staff user
        self.client.login(username='+8801700000200', password=self.password)
        response = self.client.post(edit_url, data=edit_data)
        self.assertRedirects(response, '/staff/home/')

        # 6. Delete progress log as Admin
        self.client.login(username='+8801700000100', password=self.password)
        delete_url = reverse('projects:progress_log_delete', kwargs={'pk': log.pk})
        response = self.client.post(delete_url)
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertFalse(DailyProgressLog.objects.filter(pk=log.pk).exists())

        # Try to delete as staff
        self.client.login(username='+8801700000200', password=self.password)
        response = self.client.post(reverse('projects:progress_log_delete', kwargs={'pk': log_older.pk}))
        self.assertRedirects(response, '/staff/home/')
        self.assertTrue(DailyProgressLog.objects.filter(pk=log_older.pk).exists())

    def test_manpower_deployment_crud_and_autofill(self):
        from apps.attendance.models import Attendance
        from apps.employees.models import EmployeeProfile
        from django.utils import timezone
        
        # 1. Create a project
        project = Project.objects.create(
            name='Test Manpower Project',
            client_name='Test Client',
            location='Test Location',
            system_type='VRF',
            start_date=date.today(),
            branch=self.branch
        )

        # 2. Add requirement as admin
        self.client.login(username='+8801700000100', password=self.password) # admin user
        deployment_data = {
            'date': date.today().isoformat(),
            'trade': 'Duct Technician',
            'required_count': 5,
            'present_count': ''
        }
        
        add_url = reverse('projects:manpower_add', kwargs={'project_id': project.id})
        response = self.client.post(add_url, data=deployment_data)
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertEqual(ManpowerDeployment.objects.count(), 1)
        deployment = ManpowerDeployment.objects.first()
        self.assertEqual(deployment.required_count, 5)
        self.assertIsNone(deployment.present_count)

        # 3. Check shortage indicator logic in project detail page
        response = self.client.get(reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertEqual(response.status_code, 200)
        # present_count is None -> Shortage Status displays "Pending"
        self.assertContains(response, 'Pending')

        # Update present_count to 2 (which is less than 5 required) -> displays "Shortage"
        deployment.present_count = 2
        deployment.save()
        response = self.client.get(reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertContains(response, 'Shortage')

        # Update present_count to 5 -> displays "Sufficient"
        deployment.present_count = 5
        deployment.save()
        response = self.client.get(reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertContains(response, 'Sufficient')

        # 4. Test permission restrictions for non-admin
        self.client.login(username='+8801700000200', password=self.password) # staff user
        response = self.client.post(add_url, data=deployment_data)
        self.assertRedirects(response, '/staff/home/')

        # 5. Attendance Auto-link and Auto-fill verification
        # Let's create an EmployeeProfile with trade 'Duct Technician'
        duct_user = User.objects.create_user(
            email='duct_tech@example.com',
            phone='+8801700000300',
            password=self.password,
            role='staff'
        )
        duct_employee = EmployeeProfile.objects.create(
            user=duct_user,
            branch=self.branch,
            employee_id='EMP003',
            full_name='Duct Technician John',
            designation='Duct Technician',
            phone='+8801700000300',
            joined_date=date.today()
        )

        # Create check-in Attendance record linked to the project
        attendance = Attendance.objects.create(
            employee=duct_employee,
            project=project,
            date=date.today(),
            check_in_time=timezone.now(),
            attendance_type='check_in'
        )

        # Let's check auto-fill view (post to manpower_autofill as admin)
        self.client.login(username='+8801700000100', password=self.password)
        autofill_url = reverse('projects:manpower_autofill', kwargs={'pk': deployment.pk})
        response = self.client.post(autofill_url)
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': project.id}))
        
        deployment.refresh_from_db()
        # present_count should be updated to 1 (since 1 Duct Technician checked in today)
        self.assertEqual(deployment.present_count, 1)

        # 6. Delete deployment as admin
        delete_url = reverse('projects:manpower_delete', kwargs={'pk': deployment.pk})
        response = self.client.post(delete_url)
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertFalse(ManpowerDeployment.objects.filter(pk=deployment.pk).exists())

    def test_project_materials_crud_and_quick_action(self):
        # 1. Create a project
        project = Project.objects.create(
            name='Test Materials Project',
            client_name='Test Client',
            location='Test Location',
            system_type='VRF',
            start_date=date.today(),
            branch=self.branch
        )

        # 2. Add material as admin
        self.client.login(username='+8801700000100', password=self.password) # admin user
        material_data = {
            'material_name': 'Copper Pipe 1/2"',
            'unit': 'meter',
            'required_qty': '100.00',
            'received_qty': '0.00',
            'remarks': 'Required for HVAC piping'
        }
        
        add_url = reverse('projects:material_add', kwargs={'project_id': project.id})
        response = self.client.post(add_url, data=material_data)
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertEqual(ProjectMaterial.objects.count(), 1)
        material = ProjectMaterial.objects.first()
        self.assertEqual(material.material_name, 'Copper Pipe 1/2"')
        self.assertEqual(material.required_qty, 100)
        self.assertEqual(material.received_qty, 0)
        self.assertEqual(material.balance, 100)

        # 3. Check shortage / status badge indicators in project detail page
        response = self.client.get(reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertEqual(response.status_code, 200)
        # received_qty == 0 -> "Zero Received"
        self.assertContains(response, 'Zero Received')

        # Update received_qty to 40 (partial) -> "Partial"
        material.received_qty = 40
        material.save()
        response = self.client.get(reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertContains(response, 'Partial')

        # Update received_qty to 100 (fully received) -> "Fully Received"
        material.received_qty = 100
        material.save()
        response = self.client.get(reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertContains(response, 'Fully Received')

        # Reset received_qty to 0
        material.received_qty = 0
        material.save()

        # 4. Quick increment received qty
        increment_url = reverse('projects:material_increment', kwargs={'pk': material.pk})
        
        # Test valid increment
        response = self.client.post(increment_url, data={'increment_qty': '15.50'})
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': project.id}))
        material.refresh_from_db()
        self.assertEqual(material.received_qty, 15.5)
        self.assertEqual(material.balance, 84.5)

        # Test invalid negative increment
        response = self.client.post(increment_url, data={'increment_qty': '-5.00'})
        material.refresh_from_db()
        self.assertEqual(material.received_qty, 15.5) # Unchanged

        # 5. Permission checks for non-admin
        self.client.login(username='+8801700000200', password=self.password) # staff user
        response = self.client.post(add_url, data=material_data)
        self.assertRedirects(response, '/staff/home/')
        
        response = self.client.post(increment_url, data={'increment_qty': '10.00'})
        self.assertRedirects(response, '/staff/home/')

        # 6. Delete material as admin
        self.client.login(username='+8801700000100', password=self.password)
        delete_url = reverse('projects:material_delete', kwargs={'pk': material.pk})
        response = self.client.post(delete_url)
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': project.id}))
        self.assertFalse(ProjectMaterial.objects.filter(pk=material.pk).exists())

    def test_project_signoff_and_pdf_export(self):
        # 1. Create a project
        project = Project.objects.create(
            name='Test Sign-off Project',
            client_name='Test Client',
            location='Test Location',
            system_type='VRF',
            start_date=date.today(),
            branch=self.branch
        )

        # 2. Test sign-off confirmation as Admin
        self.client.login(username='+8801700000100', password=self.password) # admin user
        confirm_url = reverse('projects:confirm_signoff', kwargs={'project_id': project.id})
        
        # Sign off as Project Manager
        response = self.client.post(confirm_url, data={
            'role': 'project_manager',
            'name': 'PM Alice'
        })
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': project.id}))
        
        sign_off = ProjectSignOff.objects.get(project=project)
        self.assertEqual(sign_off.project_manager_name, 'PM Alice')
        self.assertIsNotNone(sign_off.project_manager_signed_at)

        # 3. Test non-admin permissions
        self.client.login(username='+8801700000200', password=self.password) # staff user
        response = self.client.post(confirm_url, data={
            'role': 'site_engineer',
            'name': 'Engineer Bob'
        })
        self.assertRedirects(response, '/staff/home/')
        
        sign_off.refresh_from_db()
        self.assertEqual(sign_off.site_engineer_name, '')
        self.assertIsNone(sign_off.site_engineer_signed_at)

        # Test PDF export returns valid response
        self.client.login(username='+8801700000100', password=self.password)
        export_url = reverse('projects:export_pdf', kwargs={'project_id': project.id})
        response = self.client.get(export_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(len(response.content) > 0)

    def test_project_detail_query_count(self):
        # Log in
        self.client.login(username='+8801700000100', password=self.password)
        
        # Create project
        project = Project.objects.create(
            name='Test Query Project',
            client_name='Test Client',
            location='Test Location',
            system_type='VRF',
            start_date=date.today(),
            branch=self.branch
        )
        
        # Create some tasks
        for i in range(5):
            ProjectTask.objects.create(
                project=project,
                order=i+1,
                activity=f'Activity {i}',
                status='Not Started'
            )
            
        # Create some progress logs
        for i in range(3):
            DailyProgressLog.objects.create(
                project=project,
                date=date.today(),
                planned_work=f'Planned {i}',
                completed_work=f'Completed {i}',
                supervisor_name='Alice'
            )
            
        # Create some manpower deployments
        for i, trade in enumerate(['Helper', 'Electrician']):
            ManpowerDeployment.objects.create(
                project=project,
                date=date.today(),
                trade=trade,
                required_count=5
            )
            
        # Create some materials
        for i in range(3):
            ProjectMaterial.objects.create(
                project=project,
                material_name=f'Material {i}',
                unit='pc',
                required_qty=10
            )
            
        # Create templates
        for i in range(3):
            t = TaskTemplate.objects.create(name=f'Template {i}')
            for j in range(2):
                TaskTemplateItem.objects.create(template=t, order=j+1, activity=f'Step {j}')
                
        # Capture queries
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        
        url = reverse('projects:project_detail', kwargs={'pk': project.id})
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
            
        self.assertEqual(response.status_code, 200)
        print(f"\n[QUERY_COUNT] Project Detail page executed {len(ctx)} queries.")
        # Print actual queries to help debug
        for q in ctx.captured_queries:
            print(f"  - {q['sql']}")

    def test_negative_boundary_value_form_validation(self):
        from apps.projects.forms import ProjectMaterialForm, ManpowerDeploymentForm, DailyProgressLogForm
        
        # 1. ProjectMaterialForm
        form = ProjectMaterialForm(data={
            'material_name': 'Copper Pipe',
            'unit': 'meter',
            'required_qty': '-10.00',
            'received_qty': '0.00'
        })
        self.assertFalse(form.is_valid())
        self.assertIn('required_qty', form.errors)
        
        form = ProjectMaterialForm(data={
            'material_name': 'Copper Pipe',
            'unit': 'meter',
            'required_qty': '10.00',
            'received_qty': '-2.50'
        })
        self.assertFalse(form.is_valid())
        self.assertIn('received_qty', form.errors)

        # 2. ManpowerDeploymentForm
        form = ManpowerDeploymentForm(data={
            'date': date.today().isoformat(),
            'trade': 'Electrician',
            'required_count': -5,
            'present_count': 0
        })
        self.assertFalse(form.is_valid())
        self.assertIn('required_count', form.errors)
        
        form = ManpowerDeploymentForm(data={
            'date': date.today().isoformat(),
            'trade': 'Electrician',
            'required_count': 5,
            'present_count': -1
        })
        self.assertFalse(form.is_valid())
        self.assertIn('present_count', form.errors)

        # 3. DailyProgressLogForm
        form = DailyProgressLogForm(data={
            'date': date.today().isoformat(),
            'planned_work': 'test planned',
            'completed_work': 'test completed',
            'manpower_count': -10,
            'supervisor_name': 'Test Supervisor'
        })
        self.assertFalse(form.is_valid())
        self.assertIn('manpower_count', form.errors)







