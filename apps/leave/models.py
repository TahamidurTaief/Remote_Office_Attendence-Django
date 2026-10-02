import uuid
from decimal import Decimal
from django.db import models
from django.db.models import F
from django.conf import settings
from apps.employees.models import EmployeeProfile

class LeaveType(models.Model):
    CATEGORY_CHOICES = (
        ('sick', 'Sick'),
        ('casual', 'Casual'),
        ('other', 'Other'),
    )
    name = models.CharField(max_length=100, unique=True)
    default_days_per_year = models.IntegerField(default=0)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    is_default = models.BooleanField(default=False, help_text="Set as default leave type for automated absence deductions")
    is_active = models.BooleanField(default=True)
    deduction_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Percentage of pay to deduct for leave under this type (0=fully paid, 100=fully unpaid)"
    )

    def save(self, *args, **kwargs):
        if self.is_default:
            LeaveType.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class LeaveBalance(models.Model):
    employee = models.ForeignKey(
        EmployeeProfile, 
        on_delete=models.CASCADE, 
        related_name='leave_balances'
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE)
    year = models.IntegerField()
    total_days = models.IntegerField(default=0)
    used_days = models.IntegerField(default=0)

    class Meta:
        unique_together = ('employee', 'leave_type', 'year')

    @property
    def remaining_days(self):
        # NOTE: confirmed via codebase grep check that remaining_days has no max(0, ...) clamp 
        # anywhere in the codebase. Negative balances are naturally calculated and allowed.
        return self.total_days - self.used_days

    def __str__(self):
        return f"{self.employee.full_name} - {self.leave_type.name} ({self.year}): {self.remaining_days}/{self.total_days} left"

class LeaveRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('manager_approved', 'Manager Approved'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('returned', 'Returned'),
        ('cancelled', 'Cancelled'),
    )

    employee = models.ForeignKey(
        EmployeeProfile, 
        on_delete=models.CASCADE, 
        related_name='leave_requests'
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()
    number_of_days = models.DecimalField(max_digits=4, decimal_places=1)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    requested_at = models.DateTimeField(auto_now_add=True)
    
    sync_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    client_event_time = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='reviewed_leave_requests'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']
        permissions = [
            ('approve_leaverequest', 'Can approve or reject leave requests'),
        ]

    @property
    def total_days(self):
        return self.number_of_days

    def overlapping_days_in(self, period_start, period_end):
        from decimal import Decimal
        from datetime import timedelta
        from apps.attendance.schedule_utils import is_employee_holiday
        from apps.attendance.models import Attendance

        start = max(self.start_date, period_start)
        end = min(self.end_date, period_end)

        if start > end:
            return Decimal('0.0')

        deductible_days = Decimal('0.0')
        curr = start
        while curr <= end:
            if is_employee_holiday(self.employee, curr):
                curr += timedelta(days=1)
                continue

            if Attendance.objects.filter(employee=self.employee, date=curr).exists():
                curr += timedelta(days=1)
                continue

            approved_leaves = LeaveRequest.objects.filter(
                employee=self.employee,
                status='approved',
                start_date__lte=curr,
                end_date__gte=curr
            )
            if self.pk:
                approved_leaves = approved_leaves.exclude(pk=self.pk)
            if approved_leaves.exists():
                curr += timedelta(days=1)
                continue

            deductible_days += Decimal('1.0')
            curr += timedelta(days=1)

        return deductible_days

    def calculate_deductible_days(self):
        return self.overlapping_days_in(self.start_date, self.end_date)

    def _deduct_retroactive_unexcused_absences(self):
        import datetime
        from django.utils import timezone
        from django.conf import settings
        from apps.attendance.models import Attendance, AttendanceAbsentLog, get_default_deduction_leave_type

        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)
        end_limit = min(self.end_date, yesterday)

        if self.start_date <= end_limit:
            from apps.attendance.schedule_utils import get_branch_schedule
            schedule = get_branch_schedule(self.employee)
            deduct_type = get_default_deduction_leave_type(self.employee)

            current_date = self.start_date
            while current_date <= end_limit:
                is_workday = False
                if schedule:
                    day_name = current_date.strftime('%A').lower()
                    if day_name in schedule.working_days:
                        is_workday = True
                else:
                    working_days = getattr(settings, 'WORKING_DAYS', [0, 1, 2, 3, 5, 6])
                    if current_date.weekday() in working_days:
                        is_workday = True

                if is_workday:
                    if not Attendance.objects.filter(employee=self.employee, date=current_date).exists():
                        if not AttendanceAbsentLog.objects.select_for_update().filter(employee=self.employee, date=current_date).exists():
                            if deduct_type:
                                from apps.employees.models import EmployeeLeaveRule
                                rule = EmployeeLeaveRule.objects.filter(employee=self.employee, leave_type=deduct_type).first()
                                limit = rule.days_per_year if rule else deduct_type.default_days_per_year
                                balance, _ = LeaveBalance.objects.get_or_create(
                                    employee=self.employee,
                                    leave_type=deduct_type,
                                    year=current_date.year,
                                    defaults={'total_days': limit}
                                )
                                LeaveBalance.objects.filter(pk=balance.pk).select_for_update().update(
                                    used_days=F('used_days') + 1
                                )
                                AttendanceAbsentLog.objects.create(
                                    employee=self.employee,
                                    date=current_date,
                                    leave_type_deducted=deduct_type
                                )
                current_date += datetime.timedelta(days=1)

    def _handle_balance_transition(self, old_instance):
        from decimal import Decimal
        from apps.employees.models import EmployeeLeaveRule
        from apps.attendance.models import AttendanceAbsentLog

        year = self.start_date.year if self.start_date else None
        old_status = old_instance.status if old_instance else None
        old_days = old_instance.number_of_days if old_instance else Decimal('0.0')
        old_type = old_instance.leave_type if old_instance else None
        old_year = old_instance.start_date.year if old_instance and old_instance.start_date else None
        old_start_date = old_instance.start_date if old_instance else None
        old_end_date = old_instance.end_date if old_instance else None

        # Case 1: Status transitioned to approved from a non-approved status
        if self.status == 'approved' and old_status != 'approved':
            rule = EmployeeLeaveRule.objects.filter(employee=self.employee, leave_type=self.leave_type).first()
            limit = rule.days_per_year if rule else self.leave_type.default_days_per_year
            balance, _ = LeaveBalance.objects.get_or_create(
                employee=self.employee,
                leave_type=self.leave_type,
                year=year,
                defaults={'total_days': limit}
            )
            LeaveBalance.objects.filter(pk=balance.pk).select_for_update().update(
                used_days=F('used_days') + self.number_of_days
            )

            # Clean up overlapping absent logs to prevent double deduction
            overlapping_logs = AttendanceAbsentLog.objects.select_for_update().filter(
                employee=self.employee,
                date__range=(self.start_date, self.end_date)
            )
            for log in overlapping_logs:
                lt = log.leave_type_deducted
                if lt:
                    LeaveBalance.objects.filter(
                        employee=self.employee,
                        leave_type=lt,
                        year=log.date.year
                    ).select_for_update().update(used_days=F('used_days') - 1)
                log.delete()

        # Case 2: Status transitioned from approved to something else (e.g. pending/rejected/cancelled/returned)
        elif old_status == 'approved' and self.status != 'approved':
            LeaveBalance.objects.filter(
                employee=self.employee,
                leave_type=old_type,
                year=old_year
            ).select_for_update().update(used_days=F('used_days') - old_days)

            if self.status == 'rejected':
                self._deduct_retroactive_unexcused_absences()

        # Case 3: Remains approved but details changed
        elif self.status == 'approved' and old_status == 'approved':
            details_changed = (
                old_type != self.leave_type or
                old_year != year or
                old_days != self.number_of_days or
                old_start_date != self.start_date or
                old_end_date != self.end_date
            )
            if details_changed:
                # Revert old balance
                LeaveBalance.objects.filter(
                    employee=self.employee,
                    leave_type=old_type,
                    year=old_year
                ).select_for_update().update(used_days=F('used_days') - old_days)

                # Apply new balance
                rule = EmployeeLeaveRule.objects.filter(employee=self.employee, leave_type=self.leave_type).first()
                limit = rule.days_per_year if rule else self.leave_type.default_days_per_year
                new_balance, _ = LeaveBalance.objects.get_or_create(
                    employee=self.employee,
                    leave_type=self.leave_type,
                    year=year,
                    defaults={'total_days': limit}
                )
                LeaveBalance.objects.filter(pk=new_balance.pk).select_for_update().update(
                    used_days=F('used_days') + self.number_of_days
                )

                # Clean up overlapping absent logs for the new dates
                overlapping_logs = AttendanceAbsentLog.objects.select_for_update().filter(
                    employee=self.employee,
                    date__range=(self.start_date, self.end_date)
                )
                for log in overlapping_logs:
                    lt = log.leave_type_deducted
                    if lt:
                        LeaveBalance.objects.filter(
                            employee=self.employee,
                            leave_type=lt,
                            year=log.date.year
                        ).select_for_update().update(used_days=F('used_days') - 1)
                    log.delete()

        # Case 4: Status transitioned to rejected from a non-approved status
        elif self.status == 'rejected' and old_status != 'rejected':
            self._deduct_retroactive_unexcused_absences()

    def save(self, *args, **kwargs):
        # Autocalculate number of days excluding weekends, holidays, and active attendance days
        if self.start_date and self.end_date:
            self.number_of_days = self.calculate_deductible_days()

        from django.db import transaction

        is_new = self.pk is None
        old_instance = None

        with transaction.atomic():
            if not is_new:
                try:
                    old_instance = LeaveRequest.objects.select_for_update().get(pk=self.pk)
                except LeaveRequest.DoesNotExist:
                    old_instance = None

            self._handle_balance_transition(old_instance)
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.full_name} - {self.leave_type.name} ({self.start_date} to {self.end_date})"

    @property
    def workflow_instance(self):
        from apps.workflow.models import WorkflowInstance
        return WorkflowInstance.objects.filter(
            object_type='leave_request',
            object_id=str(self.id)
        ).first()

    @property
    def workflow_timeline(self):
        from apps.workflow.services import get_workflow_timeline
        return get_workflow_timeline(self)


class YearLeaveHelper(dict):
    """
    Helper class that acts like a dictionary (via dictget filter) to calculate
    the combined total leave remaining for an employee in a given year.
    """
    def __init__(self, employee):
        self.employee = employee
        super().__init__()

    def get(self, year, default=None):
            try:
                year = int(year)
            except (ValueError, TypeError):
                return default

            total_remaining = 0
            leave_types = get_cached_leave_types()
            
            is_balances_prefetched = hasattr(self.employee, '_prefetched_objects_cache') and 'leave_balances' in self.employee._prefetched_objects_cache
            is_rules_prefetched = hasattr(self.employee, '_prefetched_objects_cache') and 'leave_rules' in self.employee._prefetched_objects_cache

            for lt in leave_types:
                if is_balances_prefetched:
                    balance = next((b for b in self.employee.leave_balances.all() if b.leave_type_id == lt.id and b.year == year), None)
                else:
                    balance = LeaveBalance.objects.filter(employee=self.employee, leave_type=lt, year=year).first()

                if balance:
                    total_remaining += balance.remaining_days
                else:
                    if is_rules_prefetched:
                        rule = next((r for r in self.employee.leave_rules.all() if r.leave_type_id == lt.id), None)
                    else:
                        from apps.employees.models import EmployeeLeaveRule
                        rule = EmployeeLeaveRule.objects.filter(employee=self.employee, leave_type=lt).first()
                    total_remaining += rule.days_per_year if rule else lt.default_days_per_year
            return total_remaining

    def __getitem__(self, year):
        return self.get(year)

EmployeeProfile.total_leave_left_by_year = property(lambda self: YearLeaveHelper(self))


def get_cached_leave_types():
    from django.core.cache import cache
    leave_types = cache.get('all_leave_types')
    if leave_types is None:
        leave_types = list(LeaveType.objects.all().order_by('name'))
        cache.set('all_leave_types', leave_types, 300)
    return leave_types


from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

@receiver(post_save, sender=LeaveType)
def clear_leave_type_cache_on_save(sender, instance, **kwargs):
    from django.core.cache import cache
    cache.delete('all_leave_types')

@receiver(post_delete, sender=LeaveType)
def clear_leave_type_cache_on_delete(sender, instance, **kwargs):
    from django.core.cache import cache
    cache.delete('all_leave_types')


from apps.workflow.models import WorkflowInstance

@receiver(post_save, sender=LeaveRequest)
def create_leave_workflow_instance(sender, instance, created, **kwargs):
    if created:
        from apps.workflow.models import WorkflowDefinition, WorkflowInstance
        definition = WorkflowDefinition.objects.filter(code='leave_approval').first()
        if definition:
            if not WorkflowInstance.objects.filter(object_type='leave_request', object_id=str(instance.id)).exists():
                user = getattr(instance.employee, 'user', None)
                wf_instance = WorkflowInstance.objects.create(
                    definition=definition,
                    object_type='leave_request',
                    object_id=str(instance.id),
                    initiated_by=user
                )
                wf_instance.start_workflow()


@receiver(post_save, sender=WorkflowInstance)
def sync_leave_request_status(sender, instance, **kwargs):
    if instance.object_type == 'leave_request':
        from apps.leave.models import LeaveRequest
        from django.db import transaction
        with transaction.atomic():
            try:
                leave_req = LeaveRequest.objects.select_for_update().get(pk=instance.object_id)
                if leave_req.status != instance.current_status:
                    leave_req.status = instance.current_status
                    last_action = instance.actions.order_by('-timestamp').first()
                    if last_action:
                        leave_req.reviewed_by = last_action.actor
                        leave_req.reviewed_at = last_action.timestamp
                    leave_req.save()
            except LeaveRequest.DoesNotExist:
                pass


@receiver(post_delete, sender=LeaveRequest)
def refund_leave_balance_on_delete(sender, instance, **kwargs):
    if instance.status == 'approved' and getattr(instance, 'employee_id', None) and getattr(instance, 'start_date', None):
        year = instance.start_date.year
        from django.db import transaction
        from django.db.models import F
        with transaction.atomic():
            LeaveBalance.objects.filter(
                employee_id=instance.employee_id,
                leave_type_id=instance.leave_type_id,
                year=year
            ).select_for_update().update(
                used_days=F('used_days') - instance.number_of_days
            )
