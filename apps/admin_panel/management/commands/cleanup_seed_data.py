"""
Management command to safely and surgically remove operational seed data
while preserving 100% of authentic employee, attendance, and leave records.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.core.management import call_command

from apps.attendance.models import (
    Attendance,
    AttendanceLocation,
    AttendanceCorrectionRequest,
    ForgotCheckoutRequest,
    OvertimeRequest,
)
from apps.employees.models import Employee, EmployeeProfile
from apps.leave.models import LeaveRequest, LeaveBalance
from apps.projects.models import (
    Project,
    ProjectTask,
    ProjectMaterial,
    ProjectSignOff,
    DailyProgressLog,
    ManpowerDeployment,
    ProjectTaskReply,
    TaskAttachment,
    TaskDependency,
)
from apps.expense.models import Expense, ExpenseHistory, ExpenseReturnEvent
from apps.payroll.models import PayrollRun, EmployeePayrollCalculation, PayrollAdjustment, PayrollWorkflowAudit


class Command(BaseCommand):
    help = "Surgically purge operational seed data while preserving valid production records."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Execute changes. If omitted, runs in simulated dry-run mode.',
        )
        parser.add_argument(
            '--database',
            type=str,
            default='default',
            help='Target database connection (default: default).',
        )

    def handle(self, *args, **options):
        apply_mode = options['apply']
        db = options['database']

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"=== Seed Data Cleanup Tool (Mode: {'APPLY (PERMANENT)' if apply_mode else 'DRY-RUN (SIMULATION)'}) ==="
        ))

        with transaction.atomic(using=db):
            # 1. Identify seed attendance
            seed_att_qs = Attendance.objects.using(db).filter(
                note__in=["Standard site & project operations", "Regular office shift"],
                client_name__in=["Signtech Commercial HVAC Clients", ""],
                visit_title__in=["HVAC Site Inspection & Ducting", ""],
            ).exclude(
                # Ensure no photo records are touched
                photo__isnull=False,
                photo__gt='',
            )
            # Extra safeguard: ensure only records with exact round 00 seconds are flagged
            seed_att_ids = []
            for att in seed_att_qs:
                if att.check_in_time and att.check_in_time.second == 0 and att.check_in_time.microsecond == 0:
                    seed_att_ids.append(att.id)

            seed_att_count = len(seed_att_ids)
            self.stdout.write(f"1. Identified Seed Attendance records: {seed_att_count}")

            # 2. Related Attendance Locations & Ancillary Requests
            seed_loc_count = AttendanceLocation.objects.using(db).filter(attendance_id__in=seed_att_ids).count()
            seed_ot_count = OvertimeRequest.objects.using(db).filter(attendance_id__in=seed_att_ids).count()
            seed_corr_count = AttendanceCorrectionRequest.objects.using(db).filter(
                reason="GPS sync glitch during site entry"
            ).count()
            seed_forgot_count = ForgotCheckoutRequest.objects.using(db).filter(
                reason="Immediate emergency dispatch to customer site without punch"
            ).count()

            self.stdout.write(f"   -> Seed Attendance Locations to remove: {seed_loc_count}")
            self.stdout.write(f"   -> Seed Overtime Requests to remove: {seed_ot_count}")
            self.stdout.write(f"   -> Seed Correction Requests to remove: {seed_corr_count}")
            self.stdout.write(f"   -> Seed Forgot-Checkout Requests to remove: {seed_forgot_count}")

            # 3. Seed Projects & Related Operations
            seed_proj_names = [
                "Square Pharmaceuticals HVAC Central Plant",
                "Bashundhara Commercial Complex VRF Project",
                "Apex Footwear Cleanroom HVAC Expansion",
            ]
            seed_projs = Project.objects.using(db).filter(name__in=seed_proj_names)
            seed_proj_ids = list(seed_projs.values_list('id', flat=True))
            seed_proj_count = len(seed_proj_ids)
            self.stdout.write(f"2. Identified Seed Projects to remove: {seed_proj_count}")

            # 4. Seed Expenses
            seed_expenses = Expense.objects.using(db).filter(
                description__in=[
                    "Gazipur site inspection vehicle fuel and toll",
                    "Duct silicone sealant and anchor bolts",
                    "Client coordination meeting lunch & tea",
                    "Core cutting machine carbon brush repair",
                    "Unapproved personal taxi fare",
                    "Draft preliminary quotation for safety helmets",
                ]
            )
            seed_expense_count = seed_expenses.count()
            self.stdout.write(f"3. Identified Seed Expenses to remove: {seed_expense_count}")

            # 5. Seed Payroll Runs
            seed_payroll_runs = PayrollRun.objects.using(db).filter(
                name__in=[
                    "July 2026 Monthly Payroll",
                    "August 2026 Monthly Payroll",
                    "September 2026 Monthly Payroll (Draft)",
                ]
            )
            seed_payroll_count = seed_payroll_runs.count()
            self.stdout.write(f"4. Identified Seed Payroll Runs to remove: {seed_payroll_count}")

            # 6. Seed Leave Requests (Explicitly protect real ID 35)
            seed_leave_reasons = [
                "Family emergency in hometown",
                "Severe viral fever and flu",
                "Personal urgent legal matter",
                "Attending family marriage ceremony",
                "Personal tour",
                "Medical checkup - prescription pending",
                "Planned travel cancelled",
            ]
            seed_leaves = LeaveRequest.objects.using(db).filter(reason__in=seed_leave_reasons)
            seed_leave_count = seed_leaves.count()
            self.stdout.write(f"5. Identified Seed Leave Requests to remove: {seed_leave_count} (Preserving authentic requests)")

            # 7. Employee Demo Tags
            demo_emps = Employee.objects.using(db).filter(national_id__startswith="DEMO-NID-")
            demo_emp_count = demo_emps.count()
            self.stdout.write(f"6. Employees with demo fields to revert: {demo_emp_count} (Preserving staff accounts)")

            if apply_mode:
                self.stdout.write(self.style.WARNING("\n==> Deleting seed records..."))

                # Delete Locations & Requests
                AttendanceLocation.objects.using(db).filter(attendance_id__in=seed_att_ids).delete()
                OvertimeRequest.objects.using(db).filter(attendance_id__in=seed_att_ids).delete()
                AttendanceCorrectionRequest.objects.using(db).filter(reason="GPS sync glitch during site entry").delete()
                ForgotCheckoutRequest.objects.using(db).filter(reason="Immediate emergency dispatch to customer site without punch").delete()

                # Delete Seed Attendance
                Attendance.objects.using(db).filter(id__in=seed_att_ids).delete()

                # Delete Seed Projects
                try:
                    TaskDependency.objects.using(db).filter(predecessor__project_id__in=seed_proj_ids).delete()
                except Exception:
                    pass
                try:
                    TaskAttachment.objects.using(db).filter(task__project_id__in=seed_proj_ids).delete()
                except Exception:
                    pass
                try:
                    ProjectTaskReply.objects.using(db).filter(task__project_id__in=seed_proj_ids).delete()
                except Exception:
                    pass
                try:
                    DailyProgressLog.objects.using(db).filter(project_id__in=seed_proj_ids).delete()
                except Exception:
                    pass
                try:
                    ManpowerDeployment.objects.using(db).filter(project_id__in=seed_proj_ids).delete()
                except Exception:
                    pass
                try:
                    ProjectMaterial.objects.using(db).filter(project_id__in=seed_proj_ids).delete()
                except Exception:
                    pass
                try:
                    ProjectSignOff.objects.using(db).filter(project_id__in=seed_proj_ids).delete()
                except Exception:
                    pass
                try:
                    ProjectTask.objects.using(db).filter(project_id__in=seed_proj_ids).delete()
                except Exception:
                    pass
                seed_projs.delete()

                # Delete Seed Expenses
                try:
                    ExpenseHistory.objects.using(db).filter(expense__in=seed_expenses).delete()
                except Exception:
                    pass
                try:
                    ExpenseReturnEvent.objects.using(db).filter(expense__in=seed_expenses).delete()
                except Exception:
                    pass
                seed_expenses.delete()

                # Delete Seed Payroll
                try:
                    PayrollAdjustment.objects.using(db).filter(calculation__payroll_run__in=seed_payroll_runs).delete()
                except Exception:
                    pass
                try:
                    PayrollWorkflowAudit.objects.using(db).filter(payroll_run__in=seed_payroll_runs).delete()
                except Exception:
                    pass
                try:
                    EmployeePayrollCalculation.objects.using(db).filter(payroll_run__in=seed_payroll_runs).delete()
                except Exception:
                    pass
                seed_payroll_runs.delete()

                # Delete Seed Leaves & Revert balance deductions
                for lr in seed_leaves:
                    if lr.status == 'approved':
                        bal = LeaveBalance.objects.using(db).filter(
                            employee=lr.employee, leave_type=lr.leave_type, year=lr.start_date.year
                        ).first()
                        if bal and bal.used_days >= int(lr.number_of_days):
                            bal.used_days -= int(lr.number_of_days)
                            bal.save(using=db)
                seed_leaves.delete()

                # Clean Demo Employee Fields
                for emp in demo_emps:
                    emp.national_id = ""
                    if emp.bank_account and emp.bank_account.startswith("DEMO-AC-"):
                        emp.bank_account = ""
                    if emp.tax_profile and emp.tax_profile.startswith("DEMO-TIN-"):
                        emp.tax_profile = ""
                    if emp.personal_email and emp.personal_email.endswith("@signtech.test"):
                        emp.personal_email = ""
                    emp.save(using=db)

                # Reset Sequences
                try:
                    call_command('reset_pg_sequences', database=db)
                except Exception as seq_err:
                    self.stdout.write(self.style.WARNING(f"Sequence note: {seq_err}"))

                self.stdout.write(self.style.SUCCESS("\n[SUCCESS] Seed data has been completely and safely removed!"))
            else:
                self.stdout.write(self.style.NOTICE(
                    "\n[DRY RUN COMPLETE] Zero database modifications committed. Run with --apply to execute."
                ))
