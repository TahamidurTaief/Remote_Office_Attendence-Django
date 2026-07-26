from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.workflow.models import WorkflowInstance
from apps.workflow.services import escalate_instance

class Command(BaseCommand):
    help = 'Queries all WorkflowInstances that have breached their SLA deadlines and escalates them'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            help='Simulate the SLA checks and show who would be escalated without modifying records'
        )

    def handle(self, *args, **options):
        now = timezone.now()
        breached_instances = WorkflowInstance.objects.filter(
            completed_at__isnull=True,
            sla_deadline__lt=now
        ).exclude(
            current_status='escalated'
        )

        total_found = breached_instances.count()
        self.stdout.write(f"Found {total_found} active workflow instances with breached SLA deadlines.")

        escalated_count = 0
        for instance in breached_instances:
            self.stdout.write(f"Processing breach on Instance #{instance.id} (SLA Deadline: {instance.sla_deadline})")
            if options.get('dry_run'):
                self.stdout.write(f"[Dry Run] Would escalate Instance #{instance.id}")
            else:
                action = escalate_instance(instance)
                if action:
                    self.stdout.write(f"Successfully escalated Instance #{instance.id} via Action #{action.id}")
                    escalated_count += 1
                else:
                    self.stdout.write(f"Skipped escalation on Instance #{instance.id} (no valid next step or escalation role config)")

        self.stdout.write(f"SLA Check run completed. Escalated {escalated_count} instances.")
