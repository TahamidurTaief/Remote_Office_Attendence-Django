from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.accounts.models import UserSession
from apps.branches.models import Branch
from apps.employees.models import Designation, Department, EmployeeProfile
import os

User = get_user_model()

class AIApplicationShellTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email="ai_admin@example.com",
            password="AdminPassword123!",
            role="admin"
        )
        self.staff = User.objects.create_user(
            email="ai_staff@example.com",
            password="StaffPassword123!",
            role="staff"
        )
        self.branch = Branch.objects.create(name="HQ Branch", latitude=23.77, longitude=90.41)
        self.dept = Department.objects.create(name="Engineering")
        self.desig = Designation.objects.create(name="AI Lead")
        EmployeeProfile.objects.create(
            user=self.admin,
            full_name="Admin AI Lead",
            employee_id="EMP-AI-01",
            phone="01700000099",
            joined_date="2025-01-01",
            is_active=True
        )

    def _login(self, user):
        self.client.force_login(user)
        UserSession.objects.filter(user=user).update(is_active=False)
        UserSession.objects.create(
            user=user,
            session_key=self.client.session.session_key,
            device_id=f"test-ai-device-{user.pk}",
            is_active=True
        )

    def test_ai_workspace_routes_render_pure_cotton_ui(self):
        self._login(self.admin)
        routes = [
            reverse('admin_panel:ai_assistant'),
            reverse('admin_panel:ai_attendance_insights'),
            reverse('admin_panel:ai_project_insights'),
            reverse('admin_panel:ai_payroll_insights'),
            reverse('admin_panel:ai_smart_reports'),
            reverse('admin_panel:ai_settings'),
        ]
        for url in routes:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, f"Route {url} must return 200")
            html = resp.content.decode('utf-8')
            self.assertIn("ai_icon.png", html)
            self.assertIn("Powered by TaiefLab", html)
            self.assertIn("https://www.taieflab.com", html)
            self.assertIn("ft-card", html)
            self.assertIn("ft-btn", html)
            # Verify no live external API call is made
            self.assertNotIn("generativelanguage.googleapis.com", html)

    def test_global_chatbot_launcher_rendered_in_app_shell(self):
        self._login(self.admin)
        resp = self.client.get(reverse('admin_panel:dashboard'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn("ft-ai-chatbot", html)
        self.assertIn("ai_icon.png", html)
        self.assertIn("Powered by TaiefLab", html)
        self.assertIn("ai-chat-input", html)
        self.assertIn("ai-chat-messages", html)

    def test_ai_chatbot_dummy_response_endpoint(self):
        self._login(self.admin)
        url = reverse('admin_panel:ai_chatbot_response')

        # 1. Empty message returns warning
        resp = self.client.post(url, {'message': ''})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Please enter a message", resp.content.decode('utf-8'))

        # 2. Valid attendance message returns dummy response with TaiefLab footer
        resp = self.client.post(url, {'message': 'What is the attendance status today?'})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn("attendance indicates", html)
        self.assertIn("Powered by TaiefLab", html)
        self.assertIn("Local AI Assistant (Demo Mode)", html)

        # 3. Project inquiry
        resp = self.client.post(url, {'message': 'How is the project progressing?'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Alpha Tower HVAC", resp.content.decode('utf-8'))

    def test_self_hosted_font_files_and_licenses_exist(self):
        from django.conf import settings
        font_dir = os.path.join(settings.BASE_DIR, 'static', 'fonts')
        self.assertTrue(os.path.exists(os.path.join(font_dir, 'Inter-VariableFont_opsz,wght.woff2')))
        self.assertTrue(os.path.exists(os.path.join(font_dir, 'noto-sans-bengali-bengali-wght-normal.woff2')))
        self.assertTrue(os.path.exists(os.path.join(font_dir, 'OFL-Inter.txt')))
        self.assertTrue(os.path.exists(os.path.join(font_dir, 'OFL-NotoSansBengali.txt')))

    def test_ai_icon_asset_properties(self):
        from django.conf import settings
        from PIL import Image
        icon_path = os.path.join(settings.BASE_DIR, 'static', 'icons', 'ai_icon.png')
        self.assertTrue(os.path.exists(icon_path))
        with Image.open(icon_path) as im:
            self.assertEqual(im.format, 'PNG')
            self.assertEqual(im.mode, 'RGBA')
