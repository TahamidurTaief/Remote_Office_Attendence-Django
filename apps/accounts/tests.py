from django.test import TestCase, Client
from django.contrib.auth import get_user_model, authenticate
from django.urls import reverse

User = get_user_model()

class AuthenticationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'password123'
        
        # User 1: Email and Phone
        self.user_both = User.objects.create_user(
            email='user_both@example.com',
            phone='+8801700000001',
            password=self.password,
            role='staff'
        )
        
        # User 2: Email only
        self.user_email = User.objects.create_user(
            email='user_email@example.com',
            password=self.password,
            role='staff'
        )

        # User 3: Phone only
        self.user_phone = User.objects.create_user(
            phone='+8801700000003',
            password=self.password,
            role='staff'
        )

    def test_authenticate_by_email(self):
        # Test authentication with email for user with both
        user = authenticate(username='user_both@example.com', password=self.password)
        self.assertIsNotNone(user)
        self.assertEqual(user, self.user_both)

        # Test authentication with email for user with email only
        user = authenticate(username='user_email@example.com', password=self.password)
        self.assertIsNotNone(user)
        self.assertEqual(user, self.user_email)

    def test_authenticate_by_phone(self):
        # Test authentication with phone for user with both
        user = authenticate(username='+8801700000001', password=self.password)
        self.assertIsNotNone(user)
        self.assertEqual(user, self.user_both)

        # Test authentication with phone for user with phone only
        user = authenticate(username='+8801700000003', password=self.password)
        self.assertIsNotNone(user)
        self.assertEqual(user, self.user_phone)

    def test_authenticate_invalid_credentials(self):
        # Invalid password
        user = authenticate(username='user_both@example.com', password='wrongpassword')
        self.assertIsNone(user)

        # Non-existent identifier
        user = authenticate(username='nonexistent@example.com', password=self.password)
        self.assertIsNone(user)

    def test_login_view_email(self):
        response = self.client.post(reverse('accounts:login'), {
            'email': 'user_both@example.com',
            'password': self.password
        })
        # Since self.user_both is a staff, it should redirect to '/staff/home/'
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/staff/home/')

    def test_login_view_phone(self):
        response = self.client.post(reverse('accounts:login'), {
            'email': '+8801700000001',
            'password': self.password
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/staff/home/')

