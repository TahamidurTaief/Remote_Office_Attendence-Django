from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from datetime import date, timedelta
from apps.branches.models import Branch
from apps.projects.models import Project, ProjectType, TaskTemplate, TaskTemplateItem, ProjectTask, DailyProgressLog, ManpowerDeployment, ProjectMaterial, ProjectSignOff

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
        # Create default ProjectType
        self.project_type, _ = ProjectType.objects.get_or_create(name='HVAC Installation')
        self.project_data = {
            'name': 'VRF HVAC Installation',
            'project_type': self.project_type.id,
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
            project_type=self.project_type,
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
            project_type=self.project_type,
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
            project_type=self.project_type,
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
            project_type=self.project_type,
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
            project_type=self.project_type,
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
            project_type=self.project_type,
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
            project_type=self.project_type,
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
            project_type=self.project_type,
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
            project_type=self.project_type,
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

    def test_dynamic_project_types_and_hvac_validation(self):
        electrical_type = ProjectType.objects.create(name='Electrical Installation')
        non_hvac_data = {
            'name': 'Electrical Project',
            'project_type': electrical_type.id,
            'client_name': 'ACME Corp',
            'location': 'Dhaka',
            'start_date': date.today().isoformat(),
            'status': 'Not Started',
            'progress_percent': 0,
        }
        self.client.login(username='+8801700000100', password=self.password)
        response = self.client.post(reverse('projects:project_add'), data=non_hvac_data)
        self.assertRedirects(response, reverse('projects:project_list'))
        project = Project.objects.get(name='Electrical Project')
        self.assertEqual(project.project_type, electrical_type)
        self.assertEqual(project.system_type, '')
        self.assertIsNone(project.hvac_capacity_tr)

        bad_hvac_data = {
            'name': 'Failing HVAC Project',
            'project_type': self.project_type.id,
            'client_name': 'ACME Corp',
            'location': 'Dhaka',
            'start_date': date.today().isoformat(),
            'status': 'Not Started',
            'progress_percent': 0,
        }
        response = self.client.post(reverse('projects:project_add'), data=bad_hvac_data)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'system_type', 'System type is required for HVAC Installation projects.')

    def test_project_type_crud_views(self):
        self.client.login(username='+8801700000100', password=self.password)
        response = self.client.get(reverse('projects:project_type_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'HVAC Installation')

        response = self.client.post(reverse('projects:project_type_create'), data={'name': 'Plumbing'})
        self.assertRedirects(response, reverse('projects:project_type_list'))
        self.assertTrue(ProjectType.objects.filter(name='Plumbing').exists())

        plumbing = ProjectType.objects.get(name='Plumbing')
        response = self.client.post(reverse('projects:project_type_edit', kwargs={'pk': plumbing.pk}), data={'name': 'Sanitary & Plumbing'})
        self.assertRedirects(response, reverse('projects:project_type_list'))
        self.assertTrue(ProjectType.objects.filter(name='Sanitary & Plumbing').exists())

        sanitary = ProjectType.objects.get(name='Sanitary & Plumbing')
        response = self.client.post(reverse('projects:project_type_delete', kwargs={'pk': sanitary.pk}))
        self.assertRedirects(response, reverse('projects:project_type_list'))
        self.assertFalse(ProjectType.objects.filter(name='Sanitary & Plumbing').exists())

        # Create a referencing project to prevent deletion
        Project.objects.create(
            name='Test HVAC Project',
            project_type=self.project_type,
            client_name='Test Client',
            location='Test Location',
            system_type='VRF',
            start_date=date.today(),
            branch=self.branch
        )
        response = self.client.post(reverse('projects:project_type_delete', kwargs={'pk': self.project_type.pk}))
        self.assertRedirects(response, reverse('projects:project_type_list'))
        self.assertTrue(ProjectType.objects.filter(name='HVAC Installation').exists())






class Phase2And3Tests(TestCase):
    """Tests covering all Phase 2 fixes and Phase 3 feature additions."""

    def setUp(self):
        self.password = 'testpassword123'
        self.admin_user = User.objects.create_user(
            email='admin2@example.com',
            phone='+8801700001100',
            password=self.password,
            role='admin'
        )
        self.branch = Branch.objects.create(
            name='Test Branch',
            address='Dhaka',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        self.project_type, _ = ProjectType.objects.get_or_create(name='HVAC Installation')
        self.non_hvac_type = ProjectType.objects.create(name='Electrical Installation')
        self.project = Project.objects.create(
            name='Phase 2 Test Project',
            project_type=self.project_type,
            client_name='Test Client',
            location='Test Location',
            system_type='VRF',
            start_date=date(2026, 1, 1),
            branch=self.branch
        )
        self.client.login(username='+8801700001100', password=self.password)

    # ------------------------------------------------------------------ #
    # #3 — ProjectForm: completion_date < start_date                       #
    # ------------------------------------------------------------------ #
    def test_project_form_rejects_completion_before_start(self):
        data = {
            'name': 'Bad Date Project',
            'project_type': self.project_type.id,
            'client_name': 'Client',
            'location': 'Dhaka',
            'start_date': '2026-06-01',
            'completion_date': '2026-01-01',   # <-- before start_date
            'status': 'Not Started',
            'progress_percent': 0,
        }
        response = self.client.post(reverse('projects:project_add'), data=data)
        self.assertEqual(response.status_code, 200)  # stays on form
        self.assertFormError(
            response.context['form'], 'completion_date',
            'Completion date cannot be before the start date.'
        )
        self.assertFalse(Project.objects.filter(name='Bad Date Project').exists())

    def test_project_form_accepts_valid_dates(self):
        data = {
            'name': 'Good Date Project',
            'project_type': self.project_type.id,
            'client_name': 'Client',
            'location': 'Dhaka',
            'start_date': '2026-01-01',
            'completion_date': '2026-06-01',   # <-- after start_date, valid
            'status': 'Not Started',
            'progress_percent': 0,
            'system_type': 'VRF',
        }
        response = self.client.post(reverse('projects:project_add'), data=data)
        self.assertRedirects(response, reverse('projects:project_list'))
        self.assertTrue(Project.objects.filter(name='Good Date Project').exists())

    # ------------------------------------------------------------------ #
    # #4 — ProjectTaskForm: planned_finish < planned_start                  #
    # ------------------------------------------------------------------ #
    def test_task_form_rejects_finish_before_start(self):
        from apps.projects.forms import ProjectTaskForm
        form = ProjectTaskForm(data={
            'order': 1,
            'activity': 'Test Task',
            'planned_start': '2026-06-10',
            'planned_finish': '2026-06-01',   # <-- before planned_start
            'status': 'Not Started',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('planned_finish', form.errors)
        self.assertIn('cannot be before', form.errors['planned_finish'][0])

    def test_task_form_accepts_valid_dates(self):
        from apps.projects.forms import ProjectTaskForm
        form = ProjectTaskForm(data={
            'order': 1,
            'activity': 'Test Task',
            'planned_start': '2026-06-01',
            'planned_finish': '2026-06-10',   # <-- after planned_start, valid
            'status': 'Not Started',
        })
        self.assertTrue(form.is_valid(), msg=form.errors)

    # ------------------------------------------------------------------ #
    # #5 — ProjectMaterialForm: over-delivery warning (non-blocking)       #
    # ------------------------------------------------------------------ #
    def test_material_form_warns_on_over_delivery(self):
        from apps.projects.forms import ProjectMaterialForm
        form = ProjectMaterialForm(data={
            'material_name': 'Copper Pipe',
            'unit': 'meter',
            'required_qty': '10.00',
            'received_qty': '15.00',   # <-- exceeds required
        })
        # Form is valid (not hard-blocked) but has a field error warning
        self.assertFalse(form.is_valid())  # add_error makes it invalid
        self.assertIn('received_qty', form.errors)
        self.assertIn('over-delivery', form.errors['received_qty'][0].lower())

    def test_material_form_accepts_equal_quantities(self):
        from apps.projects.forms import ProjectMaterialForm
        form = ProjectMaterialForm(data={
            'material_name': 'Copper Pipe',
            'unit': 'meter',
            'required_qty': '10.00',
            'received_qty': '10.00',   # exact match — should be fine
        })
        self.assertTrue(form.is_valid(), msg=form.errors)

    # ------------------------------------------------------------------ #
    # #9 — Apply template server guard: blocks without force=true          #
    # ------------------------------------------------------------------ #
    def test_apply_template_blocked_without_force_when_tasks_exist(self):
        """Server must reject apply when tasks exist unless force=true sent."""
        # Create a pre-existing task
        ProjectTask.objects.create(
            project=self.project, order=1, activity='Existing Task', status='Not Started'
        )
        template = TaskTemplate.objects.get(name='HVAC Installation - Standard (28 Step)')

        # Post WITHOUT force=true — should redirect back with error, no deletion
        response = self.client.post(
            reverse('projects:project_apply_template', kwargs={'project_id': self.project.id}),
            data={'template_id': template.id}
        )
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': self.project.id}))
        # Task should still exist — NOT deleted
        self.assertEqual(ProjectTask.objects.filter(project=self.project).count(), 1)

    def test_apply_template_succeeds_with_force_true(self):
        """Server allows apply and deletes old tasks when force=true is present."""
        # Create a pre-existing task
        ProjectTask.objects.create(
            project=self.project, order=1, activity='Existing Task', status='Not Started'
        )
        template = TaskTemplate.objects.get(name='HVAC Installation - Standard (28 Step)')

        # Post WITH force=true — should clear old tasks and apply new template
        response = self.client.post(
            reverse('projects:project_apply_template', kwargs={'project_id': self.project.id}),
            data={'template_id': template.id, 'force': 'true'}
        )
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': self.project.id}))
        # New 28 tasks should now exist (old one was deleted)
        self.assertEqual(ProjectTask.objects.filter(project=self.project).count(), 28)

    def test_apply_template_succeeds_without_force_when_no_tasks(self):
        """When no tasks exist, no force parameter needed."""
        self.assertEqual(ProjectTask.objects.filter(project=self.project).count(), 0)
        template = TaskTemplate.objects.get(name='HVAC Installation - Standard (28 Step)')

        response = self.client.post(
            reverse('projects:project_apply_template', kwargs={'project_id': self.project.id}),
            data={'template_id': template.id}
        )
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': self.project.id}))
        self.assertEqual(ProjectTask.objects.filter(project=self.project).count(), 28)

    # ------------------------------------------------------------------ #
    # #6 — PDF title reflects project type                                 #
    # ------------------------------------------------------------------ #
    def test_pdf_title_reflects_project_type(self):
        """PDF export uses project_type.name in the title, not hardcoded 'HVAC'."""
        elec_project = Project.objects.create(
            name='Elec Project PDF',
            project_type=self.non_hvac_type,
            client_name='Client',
            location='Dhaka',
            start_date=date(2026, 1, 1),
        )
        response = self.client.get(
            reverse('projects:export_pdf', kwargs={'project_id': elec_project.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        # PDF bytes should contain the project type name (case-insensitive search in PDF raw bytes)
        self.assertIn(b'ELECTRICAL INSTALLATION', response.content.upper())

    # ------------------------------------------------------------------ #
    # #15/#16 — N+1 fix: project_type in select_related                   #
    # ------------------------------------------------------------------ #
    def test_project_list_select_related_includes_project_type(self):
        """Project list queryset should join project_type to avoid N+1."""
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        # Create two projects of different types
        Project.objects.create(
            name='Project A', project_type=self.project_type,
            client_name='Client A', location='Loc A', start_date=date(2026, 1, 1)
        )
        Project.objects.create(
            name='Project B', project_type=self.non_hvac_type,
            client_name='Client B', location='Loc B', start_date=date(2026, 1, 1)
        )
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('projects:project_list'))
        self.assertEqual(response.status_code, 200)
        # project_type for all projects should be fetched in a single JOIN query,
        # not N separate queries. Check by confirming no extra queries for project_type.
        project_type_queries = [q for q in ctx.captured_queries if "projects_projecttype" in q['sql'] and "project_type_id" not in q['sql']]
        self.assertLessEqual(len(project_type_queries), 1, "Expected project_type to be JOIN'd, not N separate queries")

    # ------------------------------------------------------------------ #
    # Phase 3 — Task reorder                                               #
    # ------------------------------------------------------------------ #
    def test_task_reorder_moves_task_up(self):
        task1 = ProjectTask.objects.create(project=self.project, order=1, activity='First', status='Not Started')
        task2 = ProjectTask.objects.create(project=self.project, order=2, activity='Second', status='Not Started')

        response = self.client.post(
            reverse('projects:project_task_reorder', kwargs={'pk': task2.pk}),
            data={'direction': 'up'}
        )
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': self.project.pk}))

        task1.refresh_from_db()
        task2.refresh_from_db()
        # task2 should now have order 1, task1 should have order 2
        self.assertEqual(task2.order, 1)
        self.assertEqual(task1.order, 2)

    def test_task_reorder_moves_task_down(self):
        task1 = ProjectTask.objects.create(project=self.project, order=1, activity='First', status='Not Started')
        task2 = ProjectTask.objects.create(project=self.project, order=2, activity='Second', status='Not Started')

        response = self.client.post(
            reverse('projects:project_task_reorder', kwargs={'pk': task1.pk}),
            data={'direction': 'down'}
        )
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': self.project.pk}))

        task1.refresh_from_db()
        task2.refresh_from_db()
        self.assertEqual(task1.order, 2)
        self.assertEqual(task2.order, 1)

    def test_task_reorder_noop_at_boundary(self):
        """Moving first task up or last task down should silently no-op."""
        task1 = ProjectTask.objects.create(project=self.project, order=1, activity='Only Task', status='Not Started')

        # Move the only task up — no change
        response = self.client.post(
            reverse('projects:project_task_reorder', kwargs={'pk': task1.pk}),
            data={'direction': 'up'}
        )
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': self.project.pk}))
        task1.refresh_from_db()
        self.assertEqual(task1.order, 1)  # unchanged

    # ------------------------------------------------------------------ #
    # Phase 3 — Bulk status update                                         #
    # ------------------------------------------------------------------ #
    def test_bulk_status_update(self):
        task1 = ProjectTask.objects.create(project=self.project, order=1, activity='T1', status='Not Started')
        task2 = ProjectTask.objects.create(project=self.project, order=2, activity='T2', status='Not Started')
        task3 = ProjectTask.objects.create(project=self.project, order=3, activity='T3', status='Not Started')

        response = self.client.post(
            reverse('projects:project_task_bulk_status', kwargs={'project_id': self.project.id}),
            data={
                'task_ids': [str(task1.pk), str(task2.pk)],
                'new_status': 'In Progress'
            }
        )
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': self.project.pk}))

        task1.refresh_from_db()
        task2.refresh_from_db()
        task3.refresh_from_db()
        self.assertEqual(task1.status, 'In Progress')
        self.assertEqual(task2.status, 'In Progress')
        self.assertEqual(task3.status, 'Not Started')  # not updated

    def test_bulk_status_rejects_invalid_status(self):
        task1 = ProjectTask.objects.create(project=self.project, order=1, activity='T1', status='Not Started')

        response = self.client.post(
            reverse('projects:project_task_bulk_status', kwargs={'project_id': self.project.id}),
            data={
                'task_ids': [str(task1.pk)],
                'new_status': 'INVALID_STATUS'
            }
        )
        # Should redirect back (not crash) and task should remain unchanged
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': self.project.pk}))
        task1.refresh_from_db()
        self.assertEqual(task1.status, 'Not Started')

    def test_bulk_status_idor_protection(self):
        """Bulk status update must not update tasks from a different project."""
        other_project = Project.objects.create(
            name='Other Project',
            project_type=self.non_hvac_type,
            client_name='Other Client',
            location='Other Loc',
            start_date=date(2026, 1, 1),
        )
        other_task = ProjectTask.objects.create(
            project=other_project, order=1, activity='Other Task', status='Not Started'
        )

        # Try to bulk-update other_task via self.project's endpoint
        response = self.client.post(
            reverse('projects:project_task_bulk_status', kwargs={'project_id': self.project.id}),
            data={
                'task_ids': [str(other_task.pk)],
                'new_status': 'Completed'
            }
        )
        # It should redirect (no crash) but other_task should NOT be changed
        self.assertRedirects(response, reverse('projects:project_detail', kwargs={'pk': self.project.pk}))
        other_task.refresh_from_db()
        self.assertEqual(other_task.status, 'Not Started')

    # ------------------------------------------------------------------ #
    # Phase 3 — Delay warning banner visible in template                   #
    # ------------------------------------------------------------------ #
    def test_delay_banner_shown_when_task_is_delayed(self):
        ProjectTask.objects.create(
            project=self.project, order=1, activity='Delayed Task', status='Delayed'
        )
        response = self.client.get(reverse('projects:project_detail', kwargs={'pk': self.project.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Delay alert')
        self.assertContains(response, 'Delayed')

    def test_delay_banner_not_shown_when_no_delayed_tasks(self):
        ProjectTask.objects.create(
            project=self.project, order=1, activity='OK Task', status='In Progress'
        )
        response = self.client.get(reverse('projects:project_detail', kwargs={'pk': self.project.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Delay alert')

    # ------------------------------------------------------------------ #
    # Audit gap #24a — empty project renders without error                 #
    # ------------------------------------------------------------------ #
    def test_empty_project_detail_renders_without_error(self):
        """A project with zero tasks/logs/materials/manpower should render cleanly."""
        empty_project = Project.objects.create(
            name='Completely Empty Project',
            project_type=self.non_hvac_type,
            client_name='Client',
            location='Dhaka',
            start_date=date(2026, 1, 1),
        )
        response = self.client.get(
            reverse('projects:project_detail', kwargs={'pk': empty_project.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No tasks found')

    # ------------------------------------------------------------------ #
    # Audit gap #24b — project deletion cascades child records             #
    # ------------------------------------------------------------------ #
    def test_project_delete_cascades_all_children(self):
        task = ProjectTask.objects.create(project=self.project, order=1, activity='T', status='Not Started')
        log = DailyProgressLog.objects.create(
            project=self.project, date=date.today(),
            planned_work='plan', completed_work='done', supervisor_name='Bob'
        )
        material = ProjectMaterial.objects.create(
            project=self.project, material_name='Pipe', unit='m', required_qty=10
        )

        response = self.client.post(
            reverse('projects:project_delete', kwargs={'pk': self.project.pk})
        )
        self.assertRedirects(response, reverse('projects:project_list'))

        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
        self.assertFalse(ProjectTask.objects.filter(pk=task.pk).exists())
        self.assertFalse(DailyProgressLog.objects.filter(pk=log.pk).exists())
        self.assertFalse(ProjectMaterial.objects.filter(pk=material.pk).exists())

    # ------------------------------------------------------------------ #
    # Audit gap #24c — deferred branch-scoping TODO comments present       #
    # ------------------------------------------------------------------ #
    def test_branch_scoping_todo_comments_present_in_views(self):
        """Verify TODO: branch-scoping deferred comments exist in views.py."""
        import os
        views_path = os.path.join(os.path.dirname(__file__), 'views.py')
        with open(views_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn('TODO: branch-scoping deferred', content,
                      "Expected 'TODO: branch-scoping deferred' comment in views.py")


class ProjectCSVExportTests(TestCase):
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
            name='Test Branch',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        self.project_type = ProjectType.objects.create(name='Test Project Type')
        
        self.project = Project.objects.create(
            name='Test Project',
            client_name='Test Client',
            location='Dhaka',
            project_type=self.project_type,
            start_date=date.today(),
            branch=self.branch
        )
        
        # Create some tasks
        self.task1 = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Task 1 Activity',
            status='In Progress',
            remarks='First task remark'
        )
        
        # Create some manpower deployments
        self.manpower1 = ManpowerDeployment.objects.create(
            project=self.project,
            date=date.today(),
            trade='Electrician',
            required_count=5,
            present_count=4
        )
        
        # Create some materials
        self.material1 = ProjectMaterial.objects.create(
            project=self.project,
            material_name='Copper Wire',
            unit='meters',
            required_qty=100,
            received_qty=60,
            remarks='Need urgently'
        )

    def test_export_tasks_csv_access_and_data(self):
        url = reverse('projects:export_tasks_csv', kwargs={'pk': self.project.pk})
        
        # Staff is redirected
        self.client.login(username='staff@example.com', password=self.password)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        
        # Admin gets 200 and correct CSV data
        self.client.login(username='admin@example.com', password=self.password)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn(f'attachment; filename="project_{self.project.id}_tasks.csv"', response['Content-Disposition'])
        
        content = response.content.decode('utf-8')
        lines = content.strip().split('\r\n')
        self.assertEqual(len(lines), 2) # header + 1 row
        self.assertEqual(lines[0], 'Order,Activity,Responsible Person,Planned Start,Planned Finish,Duration (Days),Status,Remarks')
        self.assertIn('1,Task 1 Activity,-,-,-,-,In Progress,First task remark', lines[1])

    def test_export_manpower_csv_access_and_data(self):
        url = reverse('projects:export_manpower_csv', kwargs={'pk': self.project.pk})
        
        self.client.login(username='admin@example.com', password=self.password)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn(f'attachment; filename="project_{self.project.id}_manpower.csv"', response['Content-Disposition'])
        
        content = response.content.decode('utf-8')
        lines = content.strip().split('\r\n')
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], 'Date,Trade,Required Count,Present Count')
        self.assertIn(f'{date.today()},Electrician,5,4', lines[1])

    def test_export_materials_csv_access_and_data(self):
        url = reverse('projects:export_materials_csv', kwargs={'pk': self.project.pk})
        
        self.client.login(username='admin@example.com', password=self.password)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn(f'attachment; filename="project_{self.project.id}_materials.csv"', response['Content-Disposition'])
        
        content = response.content.decode('utf-8')
        lines = content.strip().split('\r\n')
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], 'Material Name,Unit,Required Qty,Received Qty,Balance,Remarks')
        self.assertIn('Copper Wire,meters,100.00,60.00,40.00,Need urgently', lines[1])


class ProjectTaskShiftTests(TestCase):
    def setUp(self):
        self.password = 'testpassword123'
        self.admin_user = User.objects.create_user(
            email='admin@example.com',
            phone='+8801700000100',
            password=self.password,
            role='admin'
        )
        self.branch = Branch.objects.create(
            name='Test Branch',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        self.project_type = ProjectType.objects.create(name='Test Project Type')
        self.project = Project.objects.create(
            name='Test Project',
            client_name='Test Client',
            location='Dhaka',
            project_type=self.project_type,
            start_date=date(2026, 7, 1),
            branch=self.branch
        )

        # Create three tasks in sequence
        self.task1 = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Task 1',
            planned_start=date(2026, 7, 1),
            planned_finish=date(2026, 7, 5),
            status='In Progress'
        )
        self.task2 = ProjectTask.objects.create(
            project=self.project,
            order=2,
            activity='Task 2',
            planned_start=date(2026, 7, 6),
            planned_finish=date(2026, 7, 10),
            status='Not Started'
        )
        self.task3 = ProjectTask.objects.create(
            project=self.project,
            order=3,
            activity='Task 3',
            planned_start=date(2026, 7, 11),
            planned_finish=date(2026, 7, 15),
            status='Not Started'
        )

    def test_shift_subsequent_tasks_success(self):
        self.client.login(username='admin@example.com', password=self.password)
        url = reverse('projects:project_task_shift_subsequent', kwargs={'pk': self.task2.pk})

        # Post update for Task 2 to move planned_finish from 2026-07-10 to 2026-07-15 (+5 days)
        # and status to "Delayed" with confirm_shift = "true"
        post_data = {
            'order': 2,
            'activity': 'Task 2 Updated',
            'planned_start': '2026-07-06',
            'planned_finish': '2026-07-15', # +5 days
            'status': 'Delayed',
            'confirm_shift': 'true'
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)

        # Task 2 itself should be saved
        self.task2.refresh_from_db()
        self.assertEqual(self.task2.status, 'Delayed')
        self.assertEqual(self.task2.planned_finish, date(2026, 7, 15))

        # Task 1 (order 1 < 2) should NOT be affected
        self.task1.refresh_from_db()
        self.assertEqual(self.task1.planned_start, date(2026, 7, 1))
        self.assertEqual(self.task1.planned_finish, date(2026, 7, 5))

        # Task 3 (order 3 > 2) SHOULD be shifted by 5 days:
        # planned_start: 2026-07-11 + 5 days = 2026-07-16
        # planned_finish: 2026-07-15 + 5 days = 2026-07-20
        self.task3.refresh_from_db()
        self.assertEqual(self.task3.planned_start, date(2026, 7, 16))
        self.assertEqual(self.task3.planned_finish, date(2026, 7, 20))

    def test_no_shift_without_explicit_confirm_param(self):
        self.client.login(username='admin@example.com', password=self.password)
        url = reverse('projects:project_task_shift_subsequent', kwargs={'pk': self.task2.pk})

        post_data = {
            'order': 2,
            'activity': 'Task 2 Updated',
            'planned_start': '2026-07-06',
            'planned_finish': '2026-07-15',
            'status': 'Delayed',
            'confirm_shift': 'false'
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)

        # Task 2 itself should be saved
        self.task2.refresh_from_db()
        self.assertEqual(self.task2.status, 'Delayed')
        self.assertEqual(self.task2.planned_finish, date(2026, 7, 15))

        # Task 3 should NOT be shifted
        self.task3.refresh_from_db()
        self.assertEqual(self.task3.planned_start, date(2026, 7, 11))


class ProjectNotificationEmailTests(TestCase):
    def setUp(self):
        self.password = 'testpassword123'
        self.admin_user = User.objects.create_user(
            email='admin@example.com',
            phone='+8801700000100',
            password=self.password,
            role='admin'
        )
        self.branch = Branch.objects.create(
            name='Test Branch',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        self.pm_user = User.objects.create_user(
            email='pm@example.com',
            phone='+8801700000300',
            password=self.password,
            role='manager'
        )
        # Create profile for PM
        from apps.employees.models import EmployeeProfile
        self.pm_profile = EmployeeProfile.objects.create(
            user=self.pm_user,
            full_name='Project Manager',
            branch=self.branch,
            employee_id='EMP_PM',
            phone='+8801700000300',
            joined_date=date.today(),
            is_active=True,
            is_project_manager=True
        )

        self.project_type = ProjectType.objects.create(name='Test Project Type')
        self.project = Project.objects.create(
            name='Test Project',
            client_name='Test Client',
            client_email='client@example.com',
            consultant='Test Consultant',
            consultant_email='consultant@example.com',
            location='Dhaka',
            project_type=self.project_type,
            start_date=date(2026, 7, 1),
            completion_date=date.today() + timedelta(days=5),
            branch=self.branch,
        )
        self.project.project_managers.add(self.pm_profile)

    def test_delayed_task_sends_email_to_pm(self):
        from django.core import mail
        mail.outbox = []

        task = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Test Task',
            planned_start=date(2026, 7, 1),
            planned_finish=date(2026, 7, 5),
            status='Not Started'
        )
        
        # Mark delayed
        task.status = 'Delayed'
        task.save()

        # Email sent to PM
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['pm@example.com'])
        self.assertIn("Task Delayed: Test Task", mail.outbox[0].subject)

    def test_material_reaches_zero_received_with_near_deadline_emails_pm(self):
        from django.core import mail
        mail.outbox = []

        # Create material with received_qty > 0
        material = ProjectMaterial.objects.create(
            project=self.project,
            material_name='Copper Wire',
            unit='meters',
            required_qty=100,
            received_qty=10
        )
        self.assertEqual(len(mail.outbox), 0)

        # Update received_qty to 0
        material.received_qty = 0
        material.save()

        # Email sent to PM
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['pm@example.com'])
        self.assertIn("URGENT: Material Zero-Received: Copper Wire", mail.outbox[0].subject)

    def test_request_signoff_emails_stakeholder(self):
        from django.core import mail
        mail.outbox = []

        self.client.login(username='admin@example.com', password=self.password)
        
        # Consultant sign-off request
        url = reverse('projects:request_signoff', kwargs={'project_id': self.project.pk})
        response = self.client.post(url, data={'role': 'consultant'})
        self.assertEqual(response.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['consultant@example.com'])
        self.assertIn(f"Sign-off Requested: {self.project.name}", mail.outbox[0].subject)

        # Client sign-off request
        mail.outbox = []
        response = self.client.post(url, data={'role': 'client_representative'})
        self.assertEqual(response.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['client@example.com'])

    def test_email_failure_does_not_break_underlying_action(self):
        # Mock send_mail to raise an exception
        from unittest.mock import patch
        
        task = ProjectTask.objects.create(
            project=self.project,
            order=2,
            activity='Fail Safe Task',
            status='Not Started'
        )

        with patch('apps.notifications.dispatch.send_mail', side_effect=RuntimeError("SMTP connection timed out")):
            task.status = 'Delayed'
            # Should not raise exception
            task.save()
            
        self.assertEqual(task.status, 'Delayed')


class ProjectTaskNewFeaturesTests(TestCase):
    def setUp(self):
        self.password = 'testpassword123'
        self.admin_user = User.objects.create_user(
            email='admin@example.com',
            phone='+8801700000100',
            password=self.password,
            role='admin'
        )
        self.branch = Branch.objects.create(
            name='Test Branch',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        self.project_type = ProjectType.objects.create(name='Test Project Type')
        self.project = Project.objects.create(
            name='Test Project',
            client_name='Test Client',
            location='Dhaka',
            project_type=self.project_type,
            start_date=date(2026, 7, 1),
            branch=self.branch,
        )

    def test_progress_recalculation_points(self):
        # Create a project with 2 tasks (points=10 and points=30)
        task1 = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Task 1',
            points=10,
            status='Not Started'
        )
        task2 = ProjectTask.objects.create(
            project=self.project,
            order=2,
            activity='Task 2',
            points=30,
            status='Not Started'
        )
        
        # Initially progress should be 0 since both are Not Started
        self.project.refresh_from_db()
        self.assertEqual(self.project.progress_percent, 0)

        # Mark one Completed, assert progress_percent == 25
        task1.status = 'Completed'
        task1.save()

        self.project.refresh_from_db()
        self.assertEqual(self.project.progress_percent, 25)

    def test_completed_at_automated_and_post_ignore(self):
        self.client.login(email='admin@example.com', password=self.password)
        
        # Create task
        task = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Task 1',
            points=10,
            status='Not Started'
        )

        # Confirm completed_at is None initially
        self.assertIsNone(task.completed_at)

        # Update via POST trying to supply a fake completed_at
        fake_time = "2020-01-01 12:00:00"
        url = reverse('projects:project_task_edit', kwargs={'pk': task.pk})
        
        # Send POST with status=Completed and fake completed_at
        data = {
            'order': task.order,
            'activity': task.activity,
            'points': task.points,
            'status': 'Completed',
            'completed_at': fake_time,
        }
        
        response = self.client.post(url, data=data)
        # Should redirect on success
        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        self.assertEqual(task.status, 'Completed')
        self.assertIsNotNone(task.completed_at)
        # Verify that completed_at is NOT the fake time we posted
        self.assertNotEqual(task.completed_at.strftime('%Y-%m-%d'), '2020-01-01')

    def test_staff_task_complete_endpoint_success(self):
        from apps.employees.models import EmployeeProfile
        # Create an employee user
        staff_user = User.objects.create_user(
            email='staff@example.com',
            phone='+8801700000200',
            password=self.password,
            role='staff'
        )
        staff_profile = EmployeeProfile.objects.create(
            user=staff_user,
            full_name='Staff Member',
            branch=self.branch,
            employee_id='EMP_STAFF',
            phone='+8801700000200',
            joined_date=date.today(),
            is_active=True
        )

        task = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Staff Task',
            points=20,
            status='Not Started',
            responsible_person=staff_profile
        )

        self.client.login(username='+8801700000200', password=self.password)
        url = reverse('projects:staff_task_complete', kwargs={'pk': task.pk})
        
        response = self.client.post(url, data={'note': 'All done and dusted'})
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['progress_percent'], 100)

        task.refresh_from_db()
        self.assertEqual(task.status, 'Completed')
        self.assertEqual(task.employee_note, 'All done and dusted')
        self.assertIsNotNone(task.completed_at)

    def test_staff_task_complete_endpoint_forbidden_for_other_employee(self):
        from apps.employees.models import EmployeeProfile
        # Create two employee users
        staff_user1 = User.objects.create_user(
            email='staff1@example.com',
            phone='+8801700000201',
            password=self.password,
            role='staff'
        )
        staff_profile1 = EmployeeProfile.objects.create(
            user=staff_user1,
            full_name='Staff 1',
            branch=self.branch,
            employee_id='EMP_S1',
            phone='+8801700000201',
            joined_date=date.today(),
            is_active=True
        )
        staff_user2 = User.objects.create_user(
            email='staff2@example.com',
            phone='+8801700000202',
            password=self.password,
            role='staff'
        )
        staff_profile2 = EmployeeProfile.objects.create(
            user=staff_user2,
            full_name='Staff 2',
            branch=self.branch,
            employee_id='EMP_S2',
            phone='+8801700000202',
            joined_date=date.today(),
            is_active=True
        )

        task = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Staff Task',
            points=20,
            status='Not Started',
            responsible_person=staff_profile1
        )

        # Login as staff2, who is not assigned to the task
        self.client.login(username='+8801700000202', password=self.password)
        url = reverse('projects:staff_task_complete', kwargs={'pk': task.pk})
        
        response = self.client.post(url, data={'note': 'I am trying to complete someone else task'})
        self.assertEqual(response.status_code, 403)

        task.refresh_from_db()
        self.assertNotEqual(task.status, 'Completed')

    def test_staff_task_complete_endpoint_unauthorized(self):
        task = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Staff Task',
            points=20,
            status='Not Started'
        )

        # Anonymous request
        url = reverse('projects:staff_task_complete', kwargs={'pk': task.pk})
        response = self.client.post(url, data={'note': 'Anonymous note'})
        # Should redirect to login (since login_required decorator is used)
        self.assertEqual(response.status_code, 302)

    def test_completed_task_sends_email_to_pm(self):
        from django.core import mail
        from apps.employees.models import EmployeeProfile
        mail.outbox = []

        pm_user = User.objects.create_user(
            email='pm@example.com',
            phone='+8801700000300',
            password=self.password,
            role='manager'
        )
        pm_profile = EmployeeProfile.objects.create(
            user=pm_user,
            full_name='Project Manager',
            branch=self.branch,
            employee_id='EMP_PM',
            phone='+8801700000300',
            joined_date=date.today(),
            is_active=True,
            is_project_manager=True
        )
        self.project.project_managers.add(pm_profile)

        task = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Test Email Completion Task',
            points=10,
            status='Not Started'
        )

        mail.outbox = []
        task.status = 'Completed'
        task.employee_note = 'Fully done!'
        task.save()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['pm@example.com'])
        self.assertIn("Task Completed: Test Email Completion Task in Project Test Project", mail.outbox[0].subject)
        self.assertIn("Fully done!", mail.outbox[0].body)


class ProjectTemplateIntegrationTests(TestCase):
    def setUp(self):
        self.password = 'testpassword123'
        self.admin_user = User.objects.create_user(
            email='admin_template@example.com',
            phone='+8801700000999',
            password=self.password,
            role='admin'
        )
        self.branch = Branch.objects.create(
            name='Dhanmondi Branch',
            address='Dhanmondi, Dhaka',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        self.project_type, _ = ProjectType.objects.get_or_create(name='HVAC Installation')
        self.project_data = {
            'name': 'VRF HVAC Installation',
            'project_type': self.project_type.id,
            'client_name': 'ACME Corp',
            'location': 'Dhaka, Bangladesh',
            'hvac_capacity_tr': '150.00',
            'system_type': 'VRF',
            'start_date': date.today().isoformat(),
            'status': 'Not Started',
            'progress_percent': 0,
            'branch': self.branch.id
        }
        self.project = Project.objects.create(
            name='Test Project for Apply',
            client_name='Test Client',
            location='Dhaka',
            project_type=self.project_type,
            start_date=date.today(),
            branch=self.branch,
        )

    def test_project_create_with_template(self):
        self.client.login(username='+8801700000999', password=self.password)
        
        template = TaskTemplate.objects.create(name='Test Creation Template')
        TaskTemplateItem.objects.create(template=template, order=1, activity='Step 1', default_duration_days=5)
        TaskTemplateItem.objects.create(template=template, order=2, activity='Step 2', default_duration_days=3)
        
        data = self.project_data.copy()
        data['task_template'] = template.id
        
        response = self.client.post(reverse('projects:project_add'), data=data)
        self.assertRedirects(response, reverse('projects:project_list'))
        
        project = Project.objects.get(name='VRF HVAC Installation')
        tasks = project.tasks.all().order_by('order')
        self.assertEqual(tasks.count(), 2)
        self.assertEqual(tasks[0].activity, 'Step 1')
        self.assertEqual(tasks[0].duration_days, 5)
        self.assertEqual(tasks[1].activity, 'Step 2')
        self.assertEqual(tasks[1].duration_days, 3)

    def test_project_apply_template_referer_redirect(self):
        self.client.login(username='+8801700000999', password=self.password)
        
        template = TaskTemplate.objects.create(name='Test Apply Template')
        TaskTemplateItem.objects.create(template=template, order=1, activity='Step A')
        
        edit_url = reverse('projects:project_edit', kwargs={'pk': self.project.id})
        apply_url = reverse('projects:project_apply_template', kwargs={'project_id': self.project.id})
        
        response = self.client.post(
            apply_url, 
            data={'template_id': template.id, 'force': 'true'},
            HTTP_REFERER=edit_url
        )
        self.assertRedirects(response, edit_url)





