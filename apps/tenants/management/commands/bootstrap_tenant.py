from django.core.management.base import BaseCommand
from django.conf import settings
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = 'Bootstraps the default tenant if it does not exist.'

    def handle(self, *args, **options):
        default_slug = getattr(settings, 'DEFAULT_TENANT_SLUG', 'signtech')
        tenant, created = Tenant.objects.get_or_create(
            slug=default_slug,
            defaults={
                'name': 'Signtech',
                'status': 'active'
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Successfully bootstrapped default tenant: {tenant.name} ({tenant.slug})"))
        else:
            self.stdout.write(self.style.WARNING(f"Default tenant already exists: {tenant.name} ({tenant.slug})"))
