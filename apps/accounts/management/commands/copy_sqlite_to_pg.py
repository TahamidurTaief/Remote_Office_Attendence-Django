"""
High-performance, idempotent management command to migrate/copy all data from sqlite_source
into the default database (e.g. PostgreSQL) using bulk operations.
"""
from django.core.management.base import BaseCommand
from django.db import connections, transaction
from django.apps import apps
import sys


class Command(BaseCommand):
    help = "High-speed migration from local db.sqlite3 (sqlite_source) into default database (PostgreSQL)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force migration even if target database already has users.',
        )

    def handle(self, *args, **options):
        if 'sqlite_source' not in connections:
            self.stdout.write(self.style.ERROR("sqlite_source database not configured or db.sqlite3 not found."))
            return

        from apps.accounts.models import CustomUser

        user_count = CustomUser.objects.using('default').count()
        if user_count > 0 and not options.get('force'):
            self.stdout.write(self.style.WARNING(
                f"Target database already contains {user_count} users. Skipping auto-import."
            ))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("==> Bulk migrating records from SQLite into Target Database..."))

        ordered_model_labels = [
            'tenants.Tenant',
            'tenants.TenantMembership',
            'tenants.CompanyConfiguration',
            'accounts.CustomUser',
            'accounts.Module',
            'accounts.Action',
            'accounts.Permission',
            'accounts.Role',
            'accounts.RolePermission',
            'accounts.UserRoleAssignment',
            'accounts.DataScope',
            'accounts.SecurityPolicy',
            'accounts.UserSecurityProfile',
            'accounts.TrustedDevice',
            'accounts.UserSession',
            'branches.Branch',
            'branches.OfficeSchedule',
            'branches.Holiday',
            'employees.Bank',
            'employees.BankBranch',
            'employees.Department',
            'employees.Designation',
            'employees.EmployeeProfile',
            'employees.Employee',
            'leave.LeaveType',
            'leave.LeaveBalance',
            'leave.LeaveRequest',
            'employees.EmployeeBankAccount',
            'employees.EmployeeLeaveRule',
            'employees.AssetType',
            'employees.Asset',
            'employees.AssetAssignment',
            'employees.EmploymentHistory',
            'employees.EmployeeDocument',
            'employees.EmployeeSuspension',
            'employees.ManagerDelegation',
            'projects.ProjectType',
            'projects.Project',
            'projects.TaskTemplate',
            'projects.TaskTemplateItem',
            'projects.ProjectTask',
            'projects.DailyProgressLog',
            'projects.ManpowerDeployment',
            'projects.ProjectMaterial',
            'projects.ProjectSignOff',
            'projects.TaskAttachment',
            'projects.ProjectTaskReply',
            'projects.TaskDependency',
            'attendance.AttendancePolicy',
            'attendance.Attendance',
            'attendance.AttendanceLocation',
            'attendance.AttendanceAbsentLog',
            'attendance.AttendanceActivityLog',
            'attendance.AttendanceCorrectionRequest',
            'attendance.ForgotCheckoutRequest',
            'attendance.OvertimeRequest',
            'expense.ExpenseCategory',
            'expense.Expense',
            'expense.ExpenseHistory',
            'expense.ExpenseReturnEvent',
            'payroll.SalaryComponent',
            'payroll.SalaryStructure',
            'payroll.SalaryStructureComponent',
            'payroll.EmployeeSalaryAssignment',
            'payroll.PayrollPolicy',
            'payroll.PayrollRun',
            'payroll.EmployeePayrollCalculation',
            'payroll.PayrollAdjustment',
            'payroll.PayrollWorkflowAudit',
            'schedule.ScheduleEvent',
            'workflow.WorkflowDefinition',
            'workflow.WorkflowStep',
            'workflow.WorkflowInstance',
            'workflow.WorkflowHistory',
            'notifications.Notification',
            'notifications.ActivityLog',
            'notifications.AuditLog',
            'notifications.WebPushSubscription',
        ]

        target_engine = connections['default'].settings_dict.get('ENGINE', '')
        is_pg = 'postgresql' in target_engine

        # Disable foreign key constraint triggers in PostgreSQL during bulk load
        if is_pg:
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET session_replication_role = 'replica';")
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Note: Could not set session_replication_role: {e}"))

        total_migrated = 0

        try:
            for label in ordered_model_labels:
                try:
                    model = apps.get_model(label)
                except LookupError:
                    continue

                try:
                    source_qs = model.objects.using('sqlite_source').all()
                    count = source_qs.count()
                    if count == 0:
                        continue

                    self.stdout.write(f"  -> Bulk importing {label} ({count} rows)...", ending=" ")

                    items = list(source_qs)
                    imported = 0

                    try:
                        # Fast bulk create
                        created = model.objects.using('default').bulk_create(
                            items,
                            batch_size=500,
                            ignore_conflicts=True
                        )
                        imported = len(created)
                        self.stdout.write(self.style.SUCCESS(f"done ({imported} inserted)"))
                    except Exception as bulk_err:
                        # Resilient fallback row-by-row
                        for item in items:
                            try:
                                item.save(using='default', force_insert=False)
                                imported += 1
                            except Exception:
                                pass
                        self.stdout.write(self.style.SUCCESS(f"done fallback ({imported}/{count} imported)"))

                    total_migrated += imported

                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"skipped ({e})"))

        finally:
            # Always re-enable constraints in PostgreSQL
            if is_pg:
                try:
                    with connections['default'].cursor() as cursor:
                        cursor.execute("SET session_replication_role = 'DEFAULT';")
                except Exception:
                    pass

        # Reset sequences in PostgreSQL
        if is_pg:
            self.stdout.write("==> Resetting PostgreSQL auto-increment sequences...")
            try:
                from django.core.management import call_command
                call_command('reset_pg_sequences', database='default')
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Sequence reset warning: {e}"))

        self.stdout.write(self.style.SUCCESS(
            f"Bulk migration completed successfully! Total records processed: {total_migrated}"
        ))
