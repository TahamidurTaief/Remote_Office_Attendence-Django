from django.test import TestCase, RequestFactory
from django.db import IntegrityError, models
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

from apps.tenants.models import Tenant, TenantBaseModel, TenantMembership, BaseTimestampModel, TenantTestModel
from apps.tenants.context import (
    get_default_tenant,
    get_current_tenant,
    get_user_tenant,
    get_request_tenant,
    set_current_tenant,
    clear_current_tenant
)
from apps.tenants.middleware import TenantMiddleware

User = get_user_model()


class TenantCoreTests(TestCase):
    def setUp(self):
        clear_current_tenant()
        # Clean up any tenants created by data migrations to start fresh in tests
        Tenant.objects.all().delete()
        self.default_tenant = Tenant.objects.create(
            name="Signtech",
            slug="signtech",
            status="active"
        )
        self.user = User.objects.create_user(
            email="test@signtech.com",
            password="testpassword123"
        )

    def test_tenant_creation(self):
        """Test tenant fields, uuid and timestamp creation."""
        tenant = Tenant.objects.create(
            name="Another Company",
            slug="another-company",
            status="active"
        )
        self.assertIsNotNone(tenant.uuid)
        self.assertEqual(tenant.status, "active")
        self.assertIsNotNone(tenant.created_at)
        self.assertIsNotNone(tenant.updated_at)

    def test_slug_uniqueness(self):
        """Test that tenant slug must be unique."""
        with self.assertRaises(IntegrityError):
            Tenant.objects.create(
                name="Duplicate",
                slug="signtech",
                status="active"
            )

    def test_timestamp_base_inheritance(self):
        """Test inheritance of BaseTimestampModel."""
        self.assertTrue(issubclass(Tenant, BaseTimestampModel))
        self.assertTrue(issubclass(TenantTestModel, BaseTimestampModel))

    def test_tenant_base_model_inheritance(self):
        """Test TenantBaseModel PROTECT deletion and fields."""
        test_obj = TenantTestModel.objects.create(
            tenant=self.default_tenant,
            name="Test Object"
        )
        self.assertEqual(test_obj.tenant, self.default_tenant)
        self.assertIsNotNone(test_obj.created_at)

        # Test deletion protection
        with self.assertRaises(models.ProtectedError):
            self.default_tenant.delete()

    def test_default_tenant_resolution(self):
        """Test that get_default_tenant resolves to the configured slug."""
        tenant = get_default_tenant()
        self.assertEqual(tenant, self.default_tenant)

    def test_request_tenant_middleware(self):
        """Test that TenantMiddleware correctly populates request.tenant."""
        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.user

        middleware = TenantMiddleware(lambda r: None)
        middleware.process_request(request)

        self.assertEqual(request.tenant, self.default_tenant)
        self.assertEqual(get_current_tenant(), self.default_tenant)

    def test_anonymous_request_behavior(self):
        """Test that anonymous requests fall back to default tenant."""
        factory = RequestFactory()
        request = factory.get('/')
        request.user = AnonymousUser()

        middleware = TenantMiddleware(lambda r: None)
        middleware.process_request(request)

        self.assertEqual(request.tenant, self.default_tenant)

    def test_missing_default_tenant_behavior(self):
        """Test behavior when the default tenant is missing."""
        # Delete default tenant
        TenantTestModel.objects.all().delete()
        self.default_tenant.delete()

        # Should fall back to None or next active, but not crash
        tenant = get_default_tenant()
        self.assertIsNone(tenant)

    def test_inactive_tenant_behavior(self):
        """Test resolving an inactive tenant does not break but resolves properly."""
        self.default_tenant.status = 'inactive'
        self.default_tenant.save()

        # Should still resolve or fall back safely
        tenant = get_default_tenant()
        self.assertIsNone(tenant)

        # Create another active tenant
        active_tenant = Tenant.objects.create(
            name="Active Corp",
            slug="active-corp",
            status="active"
        )
        tenant = get_default_tenant()
        self.assertEqual(tenant, active_tenant)

    def test_tenant_membership_user_isolation(self):
        """Test that users belong to specific tenants via TenantMembership."""
        new_tenant = Tenant.objects.create(
            name="New Corp",
            slug="new-corp",
            status="active"
        )
        TenantMembership.objects.create(
            tenant=new_tenant,
            user=self.user,
            is_active=True
        )

        resolved_tenant = get_user_tenant(self.user)
        self.assertEqual(resolved_tenant, new_tenant)

    def test_no_client_provided_tenant_spoofing(self):
        """Verify client-supplied GET/POST parameters do not override resolved tenant."""
        factory = RequestFactory()
        # Client tries to pass a different tenant slug or id
        request = factory.get('/?tenant=another-slug', data={'tenant_id': 999})
        request.user = self.user

        middleware = TenantMiddleware(lambda r: None)
        middleware.process_request(request)

        # Should resolve to the default tenant
        self.assertEqual(request.tenant, self.default_tenant)

    def test_existing_authentication_still_works(self):
        """Ensure auth still works and sets the correct context."""
        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.user

        # Simulate full middleware stack resolution
        middleware = TenantMiddleware(lambda r: None)
        middleware.process_request(request)

        self.assertTrue(request.user.is_authenticated)
        self.assertEqual(request.tenant, self.default_tenant)
