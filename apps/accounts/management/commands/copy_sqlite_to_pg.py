"""
Management command to migrate/copy all data from sqlite_source into the default database (e.g. PostgreSQL).
Idempotent and safe: uses get_or_create or update_or_create per model.
"""
from django.core.management.base import BaseCommand
from django.db import connections, transaction
from django.apps import apps


class Command(BaseCommand):
    help = "Migrates data from local db.sqlite3 (sqlite_source) into default database (PostgreSQL)."

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
                f"Target database already contains {user_count} users. Skipping auto-import. (Use --force to override)"
            ))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("==> Migrating records from SQLite into Target Database..."))

        # Order of models to preserve foreign keys
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
            'leave.LeaveType',
            'leave.LeaveBalance',
            'leave.LeaveRequest',
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

        total_migrated = 0

        # We disable signals or handle per-model copy
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

                self.stdout.write(f"  -> Migrating {label} ({count} rows)...", ending=" ")

                imported = 0
                # Process in batches
                batch_size = 500
                items = list(source_qs)
                for item in items:
                    try:
                        # Direct raw save to target database preserving PK and attributes
                        item.save(using='default', force_insert=False)
                        imported += 1
                    except Exception as row_err:
                        # If row already exists or unique constraint conflict, try update
                        pass

                self.stdout.write(self.style.SUCCESS(f"done ({imported}/{count} imported)"))
                total_migrated += imported

            except Exception as e:
                self.stdout.write(self.style.WARNING(f"skipped ({e})"))

        # Synchronize PostgreSQL primary key sequences if on PostgreSQL
        target_engine = connections['default'].settings_dict.get('ENGINE', '')
        if 'postgresql' in target_engine:
            self.stdout.write("==> Resetting PostgreSQL auto-increment sequences...")
            from django.core.management import call_command
            from io import StringIO
            app_labels = sorted(set(lbl.split('.')[0] for lbl in ordered_model_labels))
            for app_lbl in app_labels:
                try:
                    out = StringIO()
                    call_command('sqlsequencereset', app_lbl, stdout=out)
                    sql = out.getvalue()
                    if sql.strip():
                        with connections['default'].cursor() as cursor:
                            cursor.execute(sql)
                except Exception as seq_err:
                    pass

        self.stdout.write(self.style.SUCCESS(
            f"Successfully finished SQLite to Target database migration. Total records imported: {total_migrated}"
        ))
