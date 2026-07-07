from dataclasses import dataclass
from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.attendance.models import Attendance
from apps.attendance.schedule_utils import (
    calculate_attendance_status,
    calculate_early_checkout,
    calculate_overtime,
    get_branch_schedule,
)
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import EmployeeProfile


@dataclass
class DummyEmployee:
    overtime_enabled: bool
    branch: object = None


class Command(BaseCommand):
    help = "Run schedule logic assertions for office schedule settings."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("Schedule audit"))

        self._assert_model_fields()
        self._assert_branch_signal()
        self._assert_schedule_utils()
        self._assert_reporting_fields()

        self.stdout.write(self.style.SUCCESS("All schedule checks passed."))

    def _assert_model_fields(self):
        schedule_fields = [field.name for field in OfficeSchedule._meta.get_fields()]
        attendance_fields = [field.name for field in Attendance._meta.get_fields()]
        employee_fields = [field.name for field in EmployeeProfile._meta.get_fields()]

        self.stdout.write(f"OfficeSchedule fields: {', '.join(schedule_fields)}")
        self.stdout.write(f"Attendance fields: {', '.join(attendance_fields)}")
        self.stdout.write(f"EmployeeProfile fields: {', '.join(employee_fields)}")

        required_schedule_fields = {
            'branch', 'office_start_time', 'office_end_time',
            'late_after_minutes', 'early_checkout_before_minutes',
            'overtime_after_minutes', 'working_days', 'tracking_interval_minutes'
        }
        required_attendance_fields = {'overtime_minutes', 'is_early_checkout'}
        required_employee_fields = {'overtime_enabled'}

        assert required_schedule_fields.issubset(set(schedule_fields)), 'Missing OfficeSchedule fields'
        assert required_attendance_fields.issubset(set(attendance_fields)), 'Missing Attendance fields'
        assert required_employee_fields.issubset(set(employee_fields)), 'Missing EmployeeProfile fields'

    def _assert_branch_signal(self):
        with transaction.atomic():
            branch = Branch.objects.create(
                name='Schedule Audit Branch',
                address='Audit Address',
                latitude='0.000000',
                longitude='0.000000',
            )
            try:
                schedule = OfficeSchedule.objects.get(branch=branch)
                assert schedule.branch_id == branch.id, 'Branch signal did not create OfficeSchedule'
            finally:
                branch.delete()

    def _assert_schedule_utils(self):
        class Schedule:
            office_start_time = time(9, 0)
            office_end_time = time(18, 0)
            late_after_minutes = 15
            early_checkout_before_minutes = 30
            overtime_after_minutes = 0

            def get_late_threshold(self):
                return time(9, 15)

            def get_early_checkout_threshold(self):
                return time(17, 30)

        schedule = Schedule()
        employee = DummyEmployee(overtime_enabled=True)

        assert get_branch_schedule(DummyEmployee(overtime_enabled=True, branch=None)) is None
        assert calculate_attendance_status(datetime(2026, 1, 1, 9, 10), schedule) == 'on_time'
        assert calculate_attendance_status(datetime(2026, 1, 1, 9, 20), schedule) == 'late'
        assert calculate_attendance_status(datetime(2026, 1, 1, 9, 20), None) == 'on_time'
        assert calculate_overtime(datetime(2026, 1, 1, 17, 59), schedule, employee) == 0
        assert calculate_overtime(datetime(2026, 1, 1, 20, 0), schedule, employee) == 120
        assert calculate_overtime(datetime(2026, 1, 1, 20, 0), schedule, DummyEmployee(overtime_enabled=False)) == 0
        assert calculate_early_checkout(datetime(2026, 1, 1, 17, 0), schedule) is True
        assert calculate_early_checkout(datetime(2026, 1, 1, 18, 0), schedule) is False

    def _assert_reporting_fields(self):
        attendance_fields = [field.name for field in Attendance._meta.get_fields()]
        assert 'overtime_minutes' in attendance_fields
        assert 'is_early_checkout' in attendance_fields
        self.stdout.write('Reporting fields available for late/OT/early checkout summaries.')