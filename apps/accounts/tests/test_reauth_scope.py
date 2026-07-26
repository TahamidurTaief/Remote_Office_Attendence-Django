from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.accounts.models import UserSession, SecurityPolicy

User = get_user_model()

class ReauthScopeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'password123'
        self.user = User.objects.create_superuser(
            email='admin_reauth@example.com',
            password=self.password,
            role='admin'
        )

    def test_user_permissions_view_reauth_on_post_only(self):
        """
        Verify that GET requests to UserPermissionsView do not trigger or require
        reauth, but POST requests do.
        """
        # Bypass the 'test' in sys.argv check in decorators.py by mocking sys.argv if needed,
        # or checking that without reauth data it behaves differently.
        # Actually, decorators.py checks:
        # if 'test' in sys.argv: return view_func(request, *args, **kwargs)
        # So we mock sys.argv or bypass it so the decorator is actually exercised.
        import sys
        original_argv = sys.argv
        sys.argv = [a for a in sys.argv if a != 'test']
        try:
            self.client.post(reverse('accounts:login'), {'email': 'admin_reauth@example.com', 'password': self.password})

            # Create UserSession but last_reauth_at is NULL (meaning reauth is required)
            sess = UserSession.objects.filter(user=self.user, is_active=True).first()
            self.assertIsNotNone(sess)
            sess.last_reauth_at = None
            sess.save()

            # Ensure policy reauth interval is active
            pol, _ = SecurityPolicy.objects.get_or_create(role='admin')
            pol.reauth_interval_hours = 4
            pol.save()

            # GET should load fine (since require_reauth is now name='post')
            url = reverse('admin_panel:user_permissions', kwargs={'pk': self.user.pk})
            resp_get = self.client.get(url)
            self.assertEqual(resp_get.status_code, 200)

            # POST should require reauth (either redirect to reauth page or render modal/interstitial)
            resp_post = self.client.post(url, {'role_ids': []})
            self.assertIn(resp_post.status_code, [200, 302])
            # If redirected, should be targeting a reauth page or showing reauth template
            self.assertTrue(
                resp_post.status_code == 200 and 'reauth' in resp_post.content.decode().lower() or
                resp_post.status_code == 302 and 'reauth' in resp_post['Location']
            )
        finally:
            sys.argv = original_argv
