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
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )

    employee = models.ForeignKey(
        EmployeeProfile, 
        on_delete=models.CASCADE, 
        related_name='leave_requests'
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()
    number_of_days = models.IntegerField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    requested_at = models.DateTimeField(auto_now_add=True)
    
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

    def save(self, *args, **kwargs):
        # Autocalculate number of days inclusive of start and end date
        if self.start_date and self.end_date:
            self.number_of_days = (self.end_date - self.start_date).days + 1
            
        is_new = self.pk is None
        old_status = None
        old_days = 0
        old_type = None
        old_year = None
        
        if not is_new:
            try:
                old_instance = LeaveRequest.objects.get(pk=self.pk)
                old_status = old_instance.status
                old_days = old_instance.number_of_days
                old_type = old_instance.leave_type
                old_year = old_instance.start_date.year
            except LeaveRequest.DoesNotExist:
                pass
                
        super().save(*args, **kwargs)
        
        year = self.start_date.year
        
        # Case 1: Status transitioned to approved
        if self.status == 'approved' and old_status != 'approved':
            from apps.employees.models import EmployeeLeaveRule
            rule = EmployeeLeaveRule.objects.filter(employee=self.employee, leave_type=self.leave_type).first()
            limit = rule.days_per_year if rule else self.leave_type.default_days_per_year
            balance, created = LeaveBalance.objects.get_or_create(
                employee=self.employee,
                leave_type=self.leave_type,
                year=year,
                defaults={'total_days': limit}
            )
            if not created and rule:
                balance.total_days = limit
                balance.save()
            balance.used_days = F('used_days') + self.number_of_days
            balance.save()

            # Clean up overlapping absent logs to prevent double deduction
            from apps.attendance.models import AttendanceAbsentLog
            overlapping_logs = AttendanceAbsentLog.objects.filter(
                employee=self.employee,
                date__range=(self.start_date, self.end_date)
            )
            for log in overlapping_logs:
                lt = log.leave_type_deducted
                if lt:
                    try:
                        bal = LeaveBalance.objects.get(
                            employee=self.employee,
                            leave_type=lt,
                            year=log.date.year
                        )
                        bal.used_days = F('used_days') - 1
                        bal.save()
                    except LeaveBalance.DoesNotExist:
                        pass
                log.delete()
            
        # Case 2: Status transitioned from approved to something else (e.g. pending/rejected)
        elif old_status == 'approved' and self.status != 'approved':
            try:
                balance = LeaveBalance.objects.get(
                    employee=self.employee,
                    leave_type=old_type,
                    year=old_year
                )
                balance.used_days = F('used_days') - old_days
                balance.save()
            except LeaveBalance.DoesNotExist:
                pass
                
        # Case 3: Remains approved but details changed
        elif self.status == 'approved' and old_status == 'approved':
            if old_type != self.leave_type or old_year != year or old_days != self.number_of_days:
                # Revert old balance
                try:
                    old_balance = LeaveBalance.objects.get(
                        employee=self.employee,
                        leave_type=old_type,
                        year=old_year
                    )
                    old_balance.used_days = F('used_days') - old_days
                    old_balance.save()
                except LeaveBalance.DoesNotExist:
                    pass
                
                # Apply new balance
                from apps.employees.models import EmployeeLeaveRule
                rule = EmployeeLeaveRule.objects.filter(employee=self.employee, leave_type=self.leave_type).first()
                limit = rule.days_per_year if rule else self.leave_type.default_days_per_year
                new_balance, created = LeaveBalance.objects.get_or_create(
                    employee=self.employee,
                    leave_type=self.leave_type,
                    year=year,
                    defaults={'total_days': limit}
                )
                if not created and rule:
                    new_balance.total_days = limit
                    new_balance.save()
                new_balance.used_days = F('used_days') + self.number_of_days
                new_balance.save()

                # Clean up overlapping absent logs to prevent double deduction
                from apps.attendance.models import AttendanceAbsentLog
                overlapping_logs = AttendanceAbsentLog.objects.filter(
                    employee=self.employee,
                    date__range=(self.start_date, self.end_date)
                )
                for log in overlapping_logs:
                    lt = log.leave_type_deducted
                    if lt:
                        try:
                            bal = LeaveBalance.objects.get(
                                employee=self.employee,
                                leave_type=lt,
                                year=log.date.year
                            )
                            bal.used_days = F('used_days') - 1
                            bal.save()
                        except LeaveBalance.DoesNotExist:
                            pass
                    log.delete()

        # Case 4: Status transitioned to rejected
        if self.status == 'rejected' and old_status != 'rejected':
            import datetime
            from django.utils import timezone
            from django.conf import settings
            from apps.attendance.models import Attendance, AttendanceAbsentLog, get_default_deduction_leave_type
            from django.db import transaction

            today = timezone.localdate()
            yesterday = today - datetime.timedelta(days=1)
            end_limit = min(self.end_date, yesterday)

            if self.start_date <= end_limit:
                from apps.attendance.schedule_utils import get_branch_schedule
                schedule = get_branch_schedule(self.employee)
                deduct_type = get_default_deduction_leave_type()

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
                        # Check attendance & absence logs
                        if not Attendance.objects.filter(employee=self.employee, date=current_date).exists():
                            if not AttendanceAbsentLog.objects.filter(employee=self.employee, date=current_date).exists():
                                if deduct_type:
                                    with transaction.atomic():
                                        from apps.employees.models import EmployeeLeaveRule
                                        rule = EmployeeLeaveRule.objects.filter(employee=self.employee, leave_type=deduct_type).first()
                                        limit = rule.days_per_year if rule else deduct_type.default_days_per_year
                                        balance, created = LeaveBalance.objects.get_or_create(
                                            employee=self.employee,
                                            leave_type=deduct_type,
                                            year=current_date.year,
                                            defaults={'total_days': limit}
                                        )
                                        if not created and rule:
                                            balance.total_days = limit
                                            balance.save()
                                        balance.used_days = F('used_days') + 1
                                        balance.save()

                                        AttendanceAbsentLog.objects.create(
                                            employee=self.employee,
                                            date=current_date,
                                            leave_type_deducted=deduct_type
                                        )
                    current_date += datetime.timedelta(days=1)

    def delete(self, *args, **kwargs):
        if self.status == 'approved':
            year = self.start_date.year
            try:
                balance = LeaveBalance.objects.get(
                    employee=self.employee,
                    leave_type=self.leave_type,
                    year=year
                )
                balance.used_days = F('used_days') - self.number_of_days
                balance.save()
            except LeaveBalance.DoesNotExist:
                pass
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.full_name} - {self.leave_type.name} ({self.start_date} to {self.end_date})"

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
            leave_types = LeaveType.objects.all()
            from apps.employees.models import EmployeeLeaveRule
            for lt in leave_types:
                balance = LeaveBalance.objects.filter(employee=self.employee, leave_type=lt, year=year).first()
                if balance:
                    total_remaining += balance.remaining_days
                else:
                    rule = EmployeeLeaveRule.objects.filter(employee=self.employee, leave_type=lt).first()
                    total_remaining += rule.days_per_year if rule else lt.default_days_per_year
            return total_remaining

    def __getitem__(self, year):
        return self.get(year)

EmployeeProfile.total_leave_left_by_year = property(lambda self: YearLeaveHelper(self))

