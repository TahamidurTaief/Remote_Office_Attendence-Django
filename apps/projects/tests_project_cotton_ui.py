import json
from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.accounts.models import UserSession
from apps.projects.models import Project, ProjectType, ProjectTask
from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile, Designation, Department
from apps.audit.models import AuditEvent

User = get_user_model()


class ProjectCottonUITests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email="cotton_admin@example.com",
            password="AdminPass123!",
            role="admin"
        )
        self.staff = User.objects.create_user(
            email="cotton_staff@example.com",
            password="StaffPass123!",
            role="staff"
        )
        self.branch = Branch.objects.create(name="HQ Branch", latitude=23.77, longitude=90.41)
        self.dept = Department.objects.create(name="Engineering")
        self.desig = Designation.objects.create(name="HVAC Engineer")
        self.ptype, _ = ProjectType.objects.get_or_create(name="HVAC Installation")

        self.emp1 = EmployeeProfile.objects.create(
            user=self.admin,
            full_name="Alice Admin",
            employee_id="EMP-001",
            phone="01700000001",
            joined_date=date(2025, 1, 1),
            is_active=True,
            is_project_manager=True
        )
        self.emp2 = EmployeeProfile.objects.create(
            user=self.staff,
            full_name="Bob Staff",
            employee_id="EMP-002",
            phone="01700000002",
            joined_date=date(2025, 1, 1),
            is_active=True
        )

        self.project = Project.objects.create(
            name="Alpha Tower HVAC",
            project_type=self.ptype,
            client_name="Alpha Corp",
            client_email="client@alpha.com",
            location="Downtown Center",
            start_date=date(2026, 1, 1),
            completion_date=date(2026, 6, 30),
            status="In Progress",
            progress_percent=45,
            branch=self.branch,
            created_by=self.admin
        )
        self.project.project_managers.add(self.emp1)
        self.project.site_engineers.add(self.emp2)

        self.task1 = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity="Duct Layout & Sizing",
            responsible_person=self.emp2,
            planned_start=date(2026, 1, 5),
            planned_finish=date(2026, 1, 20),
            status="Completed",
            progress_percent=100
        )
        self.task2 = ProjectTask.objects.create(
            project=self.project,
            order=2,
            activity="Chiller Piping",
            responsible_person=self.emp2,
            planned_start=date(2026, 1, 22),
            planned_finish=date(2026, 2, 15),
            status="In Progress",
            progress_percent=50
        )

        # Other project for isolation tests
        self.other_project = Project.objects.create(
            name="Beta Plant",
            project_type=self.ptype,
            client_name="Beta Ltd",
            location="Industrial Area",
            start_date=date(2026, 2, 1),
            completion_date=date(2026, 8, 30),
            status="Not Started",
            created_by=self.admin
        )

        # Audit events for project and other project
        AuditEvent.objects.create(
            module="projects",
            action="created",
            object_type="Project",
            object_id=str(self.project.pk),
            object_label=self.project.name,
            actor_user=self.admin,
            actor_role="admin"
        )
        AuditEvent.objects.create(
            module="projects",
            action="updated",
            object_type="Project",
            object_id=str(self.project.pk),
            object_label=self.project.name,
            actor_user=self.admin,
            actor_role="admin"
        )
        AuditEvent.objects.create(
            module="projects",
            action="created",
            object_type="Project",
            object_id=str(self.other_project.pk),
            object_label=self.other_project.name,
            actor_user=self.admin,
            actor_role="admin"
        )

    def _login(self, user):
        self.client.force_login(user)
        UserSession.objects.filter(user=user).update(is_active=False)
        UserSession.objects.create(
            user=user,
            session_key=self.client.session.session_key,
            device_id=f"test-device-{user.pk}",
            is_active=True
        )

    # 1. Route Permissions
    def test_routes_require_authentication(self):
        routes = [
            reverse("projects:project_detail", kwargs={"pk": self.project.pk}),
            reverse("projects:project_gantt", kwargs={"pk": self.project.pk}),
            reverse("projects:project_edit", kwargs={"pk": self.project.pk}),
            f"{reverse('audit:activity_list')}?module=projects&object_id={self.project.pk}",
        ]
        for url in routes:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, f"Unauthenticated access to {url} must redirect")

    # 2. Project Detail Route
    def test_project_detail_view_renders_cotton_ui(self):
        self._login(self.admin)
        url = reverse("projects:project_detail", kwargs={"pk": self.project.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # Verify key project information is present
        self.assertIn("Alpha Tower HVAC", html)
        self.assertIn("Alpha Corp", html)
        self.assertIn("Duct Layout &amp; Sizing", html)
        self.assertIn("Alice Admin", html)
        self.assertIn("Bob Staff", html)

        # Verify cotton component classes rendered
        self.assertIn("ft-card", html)
        self.assertIn("ft-btn", html)
        self.assertIn("ft-badge", html)

    # 3. Project Gantt Route
    def test_project_gantt_view_renders_deterministic_data(self):
        self._login(self.admin)
        url = reverse("projects:project_gantt", kwargs={"pk": self.project.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        self.assertIn("Gantt: Alpha Tower HVAC", html)
        self.assertIn("gantt_tasks_json", resp.context)
        tasks_data = json.loads(resp.context["gantt_tasks_json"])
        self.assertEqual(len(tasks_data), 2)
        self.assertEqual(tasks_data[0]["activity"], "Duct Layout & Sizing")
        self.assertEqual(tasks_data[1]["activity"], "Chiller Piping")

    # 4. Audit Activity Route with Object ID Filtering
    def test_audit_activity_filters_exact_project_object_id(self):
        self._login(self.admin)
        url = f"{reverse('audit:activity_list')}?module=projects&object_id={self.project.pk}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        events = resp.context["events"]
        # Must only return events for project 1, not project 2
        self.assertGreaterEqual(len(events), 2)
        for ev in events:
            self.assertEqual(ev.object_id, str(self.project.pk))
            self.assertEqual(ev.module.lower(), "projects")
            self.assertNotEqual(ev.object_id, str(self.other_project.pk))

        html = resp.content.decode("utf-8")
        self.assertIn("Back to Project", html)
        self.assertIn(f'value="{self.project.pk}"', html)

    def test_audit_activity_htmx_partial_preserves_object_id(self):
        self._login(self.admin)
        url = f"{reverse('audit:activity_list')}?module=projects&object_id={self.project.pk}&q=alpha"
        resp = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # In HTMX partial, table or cards rendered with data-object-id
        self.assertIn(f'data-object-id="{self.project.pk}"', html)

    # 5. Project Form Create & Edit Validation
    def test_project_edit_invalid_date_renders_error_summary_and_preserves_values(self):
        self._login(self.admin)
        url = reverse("projects:project_edit", kwargs={"pk": self.project.pk})

        post_data = {
            "name": "Alpha Tower Modified",
            "project_type": self.ptype.pk,
            "client_name": "Alpha Corp",
            "location": "Downtown Center",
            "start_date": "2026-06-01",
            "completion_date": "2026-01-01",  # Invalid: completion before start
            "status": "In Progress",
            "progress_percent": "50",
            "system_type": "VRF System",
            "project_managers": [self.emp1.pk],
            "site_engineers": [self.emp2.pk],
        }

        resp = self.client.post(url, post_data)
        # Form error should return 200 re-rendering the form
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["form"].errors)
        self.assertIn("completion_date", resp.context["form"].errors)

        html = resp.content.decode("utf-8")
        # Ensure error summary and submitted values are rendered
        self.assertIn("Completion date cannot be before the start date", html)
        self.assertIn("Alpha Tower Modified", html)

    def test_project_edit_valid_submission_updates_project(self):
        self._login(self.admin)
        url = reverse("projects:project_edit", kwargs={"pk": self.project.pk})

        post_data = {
            "name": "Alpha Tower Updated Name",
            "project_type": self.ptype.pk,
            "client_name": "Alpha Corp",
            "location": "Downtown Center",
            "start_date": "2026-01-01",
            "completion_date": "2026-07-01",
            "status": "In Progress",
            "progress_percent": "60",
            "system_type": "Chiller",
            "branch": self.branch.pk,
            "project_managers": [self.emp1.pk],
            "site_engineers": [self.emp2.pk],
        }

        resp = self.client.post(url, post_data)
        self.assertEqual(resp.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, "Alpha Tower Updated Name")
        self.assertEqual(self.project.progress_percent, 60)

    # 6. Query Count Regression Gates
    def test_project_detail_query_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._login(self.admin)
        url = reverse("projects:project_detail", kwargs={"pk": self.project.pk})
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(len(ctx.captured_queries), 35)

    def test_project_gantt_query_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._login(self.admin)
        url = reverse("projects:project_gantt", kwargs={"pk": self.project.pk})
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(len(ctx.captured_queries), 25)

    # 7. Verification of Zero Raw Controls and Cotton Component Usage in Rendered Templates
    def test_templates_render_cotton_components_and_no_raw_controls(self):
        import re
        self._login(self.admin)

        routes = [
            reverse("projects:project_detail", kwargs={"pk": self.project.pk}),
            reverse("projects:project_gantt", kwargs={"pk": self.project.pk}),
            reverse("projects:project_edit", kwargs={"pk": self.project.pk}),
            f"{reverse('audit:activity_list')}?module=projects&object_id={self.project.pk}",
        ]

        for url in routes:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, f"Route {url} must return 200")
            html = resp.content.decode("utf-8")

            # Must contain core Cotton component markers
            self.assertIn("ft-card", html, f"{url} missing ft-card")
            self.assertIn("ft-btn", html, f"{url} missing ft-btn")

            # Check that within the main content container, all inputs are hidden or Cotton components
            main_content = html.split("<main", 1)[-1].split("</main>", 1)[0] if "<main" in html else html
            # Ignore search inputs inside c-select dropdown and global shell inputs
            raw_unwrapped = [
                inp for inp in re.findall(r'<input\b[^>]*>', main_content)
                if not any(allowed in inp for allowed in ['type="hidden"', "type='hidden'", "searchInput", "opacity-0", "ft-input", "checkbox"])
            ]
            self.assertEqual(raw_unwrapped, [], f"{url} contains unwrapped raw input: {raw_unwrapped}")

    def test_rendered_html_preserves_alpine_and_htmx_attributes(self):
        self._login(self.admin)
        detail_url = reverse("projects:project_detail", kwargs={"pk": self.project.pk})
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # Tab switching & filter Alpine attributes survive
        self.assertIn("activeTab = 'tasks'", html)
        self.assertIn("statusFilter = 'all'", html)
        self.assertIn("selectedTasks", html)
        self.assertIn("toggleAll()", html)

        # HTMX attributes survive
        self.assertIn("hx-post=", html)
        self.assertIn("hx-headers=", html)

    def test_project_form_field_level_accessible_red_errors_rendered(self):
        self._login(self.admin)
        url = reverse("projects:project_edit", kwargs={"pk": self.project.pk})

        # Submit invalid data (empty name, invalid date range)
        post_data = {
            "name": "",
            "project_type": self.ptype.pk,
            "client_name": "Alpha Corp",
            "location": "Downtown Center",
            "start_date": "2026-06-01",
            "completion_date": "2026-01-01",
            "status": "In Progress",
            "progress_percent": "50",
            "system_type": "VRF System",
            "project_managers": [self.emp1.pk],
            "site_engineers": [self.emp2.pk],
        }

        resp = self.client.post(url, post_data)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # Check accessible aria-invalid and red error text / classes
        self.assertIn("aria-invalid=\"true\"", html)
        self.assertIn("ft-error-text", html)
        self.assertIn("border-red-500", html)
