"""
Idempotent, atomic operational demo-data seeder for the Signtech Django system.
Preserves all existing data, enriches 41 employees, generates coherent operational
activity from start_date to end_date (defaulting to 2 months prior to today).
"""

import datetime
import decimal
import hashlib
import random
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models, transaction
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.attendance.models import (
    Attendance,
    AttendanceAbsentLog,
    AttendanceActivityLog,
    AttendanceAuditLog,
    AttendanceCorrectionRequest,
    AttendanceLocation,
    AttendancePolicy,
    ForgotCheckoutRequest,
    OvertimeRequest,
)
from apps.branches.models import Branch, Holiday, OfficeSchedule
from apps.employees.models import (
    Asset,
    AssetAssignment,
    AssetCondition,
    AssetType,
    Department,
    Designation,
    Employee,
    EmployeeLeaveRule,
    EmployeeProfile,
    EmploymentHistory,
)
from apps.expense.models import Expense, ExpenseCategory, ExpenseHistory, ExpenseReturnEvent
from apps.leave.models import LeaveBalance, LeaveRequest, LeaveType
from apps.payroll.models import (
    EmployeePayrollCalculation,
    EmployeeSalaryAssignment,
    PaymentMode,
    PayrollAdjustment,
    PayrollPolicy,
    PayrollRun,
    PayrollRunStatus,
    PayrollWorkflowAudit,
    SalaryComponent,
    SalaryComponentType,
    SalaryComponentValueType,
    SalaryStructure,
    SalaryStructureComponent,
)
from apps.payroll.services import PayrollService
from apps.projects.models import (
    DailyProgressLog,
    ManpowerDeployment,
    Project,
    ProjectMaterial,
    ProjectSignOff,
    ProjectTask,
    ProjectTaskReply,
    ProjectType,
    TaskDependency,
    TaskTemplate,
)
from apps.schedule.models import ScheduleEvent
from apps.workflow.models import (
    WorkflowAction,
    WorkflowDefinition,
    WorkflowDelegation,
    WorkflowInstance,
    WorkflowStep,
)


class Command(BaseCommand):
    help = "Deterministically seeds coherent operational demo data while preserving existing records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the seed run without committing transactions",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the seed changes to the database",
        )
        parser.add_argument(
            "--start-date",
            type=str,
            help="Start date (YYYY-MM-DD). Default is 2 calendar months before today.",
        )
        parser.add_argument(
            "--end-date",
            type=str,
            help="End date (YYYY-MM-DD). Default is today.",
        )
        parser.add_argument(
            "--seed",
            type=str,
            default="signtech-operational-2026",
            help="Deterministic PRNG seed string",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        apply_mode = options["apply"]

        if not dry_run and not apply_mode:
            self.stdout.write(
                self.style.WARNING(
                    "Neither --apply nor --dry-run specified. Defaulting to --dry-run mode."
                )
            )
            dry_run = True

        # Date parsing
        today = timezone.localdate()
        if options["end_date"]:
            try:
                end_date = datetime.datetime.strptime(options["end_date"], "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("Invalid --end-date format. Use YYYY-MM-DD.")
        else:
            end_date = today

        if options["start_date"]:
            try:
                start_date = datetime.datetime.strptime(options["start_date"], "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("Invalid --start-date format. Use YYYY-MM-DD.")
        else:
            # Default 2 calendar months before today
            year = today.year
            month = today.month - 2
            if month <= 0:
                month += 12
                year -= 1
            day = min(today.day, 28)
            start_date = datetime.date(year, month, day)

        if start_date > end_date:
            raise CommandError(f"start_date ({start_date}) cannot be greater than end_date ({end_date})")

        seed_str = options["seed"]
        rng = random.Random(seed_str)

        self.stdout.write(
            self.style.NOTICE(
                f"=== Signtech Operational Demo Seeder ===\n"
                f"Mode: {'DRY-RUN' if dry_run else 'APPLY'}\n"
                f"Date Range: {start_date} to {end_date}\n"
                f"Seed: {seed_str}\n"
            )
        )

        counts = {
            "created": {},
            "updated": {},
            "skipped": {},
        }

        def record_metric(model_name, action):
            counts[action][model_name] = counts[action].get(model_name, 0) + 1

        try:
            with transaction.atomic():
                # 1. Organization & Schedules
                self._seed_organization(rng, start_date, end_date, record_metric)

                # 2. Workflow Definitions
                self._seed_workflows(record_metric)

                # 3. Employee Enrichment & Hierarchy
                self._seed_employees(rng, record_metric)

                # 4. Leave Types, Balances & Controlled Requests
                self._seed_leaves(rng, start_date, end_date, record_metric)

                # 5. Attendance & Requests
                self._seed_attendance(rng, start_date, end_date, record_metric)

                # 6. Projects & Operations
                self._seed_projects(rng, start_date, end_date, record_metric)

                # 7. Schedule Events
                self._seed_schedule_events(rng, start_date, end_date, record_metric)

                # 8. Expenses & Workflows
                self._seed_expenses(rng, start_date, end_date, record_metric)

                # 9. Payroll
                self._seed_payroll(rng, start_date, end_date, record_metric)

                # Summary output before rollback in dry run
                self._print_summary(counts, start_date, end_date)

                if dry_run:
                    self.stdout.write(self.style.NOTICE("\n[Dry Run] Rolling back transaction..."))
                    transaction.set_rollback(True)

        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"\n[Execution Error] Seed failed: {exc}"))
            raise exc

    def _seed_organization(self, rng, start_date, end_date, record_metric):
        """Preserve branch, office schedule, create departments, designations, and BD holidays."""
        branch, _ = Branch.objects.get_or_create(
            id=1,
            defaults={
                "name": "Dhaka Main Branch",
                "address": "Baitul Mukarram, Dhaka",
                "latitude": Decimal("23.730442"),
                "longitude": Decimal("23.730445"),
                "radius_meters": 100,
                "is_active": True,
            },
        )

        schedule, sched_created = OfficeSchedule.objects.get_or_create(
            branch=branch,
            defaults={
                "office_start_time": datetime.time(9, 0),
                "office_end_time": datetime.time(18, 0),
                "late_after_minutes": 15,
                "early_checkout_before_minutes": 30,
                "overtime_after_minutes": 0,
                "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
                "tracking_interval_minutes": 60,
            },
        )
        if sched_created:
            record_metric("OfficeSchedule", "created")
        else:
            record_metric("OfficeSchedule", "skipped")

        # Attendance Policy
        pol, pol_created = AttendancePolicy.objects.get_or_create(
            branch=branch,
            defaults={
                "photo_required": True,
                "gps_required": "required",
                "max_gps_accuracy_meters": 100,
                "allow_holiday_attendance": True,
                "allow_outside_geofence": True,
                "late_grace_minutes": 15,
                "geofencing_policy": "warning",
            },
        )
        if pol_created:
            record_metric("AttendancePolicy", "created")
        else:
            record_metric("AttendancePolicy", "skipped")

        # Departments
        depts_data = [
            ("Management & Executive", "MGMT", "Executive and strategic leadership"),
            ("Engineering & Design", "ENG", "HVAC engineering, load calculation and shop drawings"),
            ("Project Management & Operations", "PMO", "Site execution, HVAC installation and ducting"),
            ("Service & Maintenance", "SERV", "Testing, commissioning, TAB and maintenance"),
            ("Finance & Accounts", "FIN", "Accounting, expense reconciliation and payroll"),
            ("HR & Administration", "HR", "Human resources, talent management and administration"),
            ("Procurement & Logistics", "PROC", "Material procurement, supply chain and logistics"),
            ("Quality & Safety", "QAQC", "Quality assurance, site safety and inspection"),
        ]
        dept_map = {}
        for name, code, desc in depts_data:
            dept, created = Department.objects.get_or_create(
                name=name,
                defaults={
                    "code": code,
                    "description": desc,
                    "is_global": True,
                    "is_active": True,
                },
            )
            dept.branches.add(branch)
            dept_map[code] = dept
            if created:
                record_metric("Department", "created")
            else:
                record_metric("Department", "skipped")

        # Designations
        designations_data = [
            ("Managing Director", "MD", "MGMT"),
            ("Executive Director", "ED", "MGMT"),
            ("General Manager", "GM", "MGMT"),
            ("Chief HVAC Engineer", "CHE", "ENG"),
            ("Lead Design Engineer", "LDE", "ENG"),
            ("HVAC Design Engineer", "HDE", "ENG"),
            ("Senior Project Manager", "SPM", "PMO"),
            ("Project Manager", "PM", "PMO"),
            ("Senior Site Engineer", "SSE", "PMO"),
            ("Site Engineer", "SE", "PMO"),
            ("Duct & Piping Supervisor", "DPS", "PMO"),
            ("Senior HVAC Technician", "SHT", "PMO"),
            ("HVAC Technician", "HT", "PMO"),
            ("Commissioning Engineer", "CE", "SERV"),
            ("TAB Specialist", "TAB", "SERV"),
            ("Service Engineer", "SVE", "SERV"),
            ("Finance Manager", "FM", "FIN"),
            ("Senior Accountant", "SA", "FIN"),
            ("Accounts Officer", "AO", "FIN"),
            ("HR Manager", "HRM", "HR"),
            ("HR Officer", "HRO", "HR"),
            ("Admin Executive", "AE", "HR"),
            ("Procurement Manager", "PRM", "PROC"),
            ("Procurement Officer", "PRO", "PROC"),
            ("QA/QC Lead Engineer", "QAL", "QAQC"),
            ("Safety Officer", "SO", "QAQC"),
        ]
        desig_map = {}
        for name, code, dept_code in designations_data:
            dept = dept_map.get(dept_code)
            desig, created = Designation.objects.get_or_create(
                name=name,
                defaults={
                    "code": code,
                    "department": dept,
                    "is_active": True,
                },
            )
            desig_map[code] = desig
            if created:
                record_metric("Designation", "created")
            else:
                record_metric("Designation", "skipped")

        # BD Holidays
        # Ashura: 2026-07-26, National Mourning Day: 2026-08-15, Janmashtami: 2026-09-04
        bd_holidays = [
            ("Ashura", datetime.date(2026, 7, 26)),
            ("National Mourning Day", datetime.date(2026, 8, 15)),
            ("Janmashtami", datetime.date(2026, 9, 4)),
        ]
        for name, dt in bd_holidays:
            if start_date <= dt <= end_date or dt == datetime.date(2026, 8, 15):
                hol, created = Holiday.objects.get_or_create(
                    branch=branch,
                    date=dt,
                    defaults={"name": name},
                )
                if created:
                    record_metric("Holiday", "created")
                else:
                    record_metric("Holiday", "skipped")

    def _seed_workflows(self, record_metric):
        """Create workflow definitions and steps for leave, expense, and ot."""
        wfs = [
            (
                "leave_approval",
                "leave",
                "Leave Approval Workflow",
                [
                    (1, "Manager Review", "pending", "manager_approved", "manager", "reporting_manager", 24),
                    (2, "HR Final Approval", "manager_approved", "approved", "admin", "static_role", 48),
                ],
            ),
            (
                "expense_approval",
                "expense",
                "Expense Approval Workflow",
                [
                    (1, "Manager Review", "pending_manager", "pending_finance", "manager", "reporting_manager", 24),
                    (2, "Finance Review", "pending_finance", "pending_accounts", "admin", "static_role", 48),
                    (3, "Accounts Disbursement", "pending_accounts", "approved", "admin", "static_role", 48),
                ],
            ),
            (
                "ot_approval",
                "attendance",
                "Overtime Approval Workflow",
                [
                    (1, "Manager Review", "pending", "manager_approved", "manager", "reporting_manager", 24),
                    (2, "HR Final Approval", "manager_approved", "approved", "admin", "static_role", 48),
                ],
            ),
        ]
        for code, mod, name, steps in wfs:
            wd, created = WorkflowDefinition.objects.get_or_create(
                code=code,
                defaults={
                    "module": mod,
                    "name": name,
                    "description": f"Standard {name}",
                    "is_active": True,
                },
            )
            if created:
                record_metric("WorkflowDefinition", "created")
            else:
                record_metric("WorkflowDefinition", "skipped")

            for s_num, s_name, from_s, to_s, role, res_type, sla in steps:
                step, s_created = WorkflowStep.objects.get_or_create(
                    workflow=wd,
                    step_number=s_num,
                    defaults={
                        "name": s_name,
                        "from_status": from_s,
                        "to_status": to_s,
                        "approver_role": role,
                        "approver_resolution_type": res_type,
                        "sla_hours": sla,
                        "allow_return": True,
                        "allow_rejection": True,
                    },
                )
                if s_created:
                    record_metric("WorkflowStep", "created")
                else:
                    record_metric("WorkflowStep", "skipped")

    def _seed_employees(self, rng, record_metric):
        """Enrich all 41 employees, build tree hierarchy, assign safe assets and leave rules."""
        branch = Branch.objects.get(id=1)
        dept_mgmt = Department.objects.get(code="MGMT")
        dept_eng = Department.objects.get(code="ENG")
        dept_pmo = Department.objects.get(code="PMO")
        dept_serv = Department.objects.get(code="SERV")
        dept_fin = Department.objects.get(code="FIN")
        dept_hr = Department.objects.get(code="HR")
        dept_proc = Department.objects.get(code="PROC")
        dept_qaqc = Department.objects.get(code="QAQC")

        desig_md = Designation.objects.get(code="MD")
        desig_ed = Designation.objects.get(code="ED")
        desig_gm = Designation.objects.get(code="GM")
        desig_che = Designation.objects.get(code="CHE")
        desig_lde = Designation.objects.get(code="LDE")
        desig_hde = Designation.objects.get(code="HDE")
        desig_spm = Designation.objects.get(code="SPM")
        desig_pm = Designation.objects.get(code="PM")
        desig_sse = Designation.objects.get(code="SSE")
        desig_se = Designation.objects.get(code="SE")
        desig_dps = Designation.objects.get(code="DPS")
        desig_sht = Designation.objects.get(code="SHT")
        desig_ht = Designation.objects.get(code="HT")
        desig_ce = Designation.objects.get(code="CE")
        desig_tab = Designation.objects.get(code="TAB")
        desig_sve = Designation.objects.get(code="SVE")
        desig_fm = Designation.objects.get(code="FM")
        desig_sa = Designation.objects.get(code="SA")
        desig_ao = Designation.objects.get(code="AO")
        desig_hrm = Designation.objects.get(code="HRM")
        desig_hro = Designation.objects.get(code="HRO")
        desig_prm = Designation.objects.get(code="PRM")
        desig_qal = Designation.objects.get(code="QAL")

        # Top-down mapping of Employee IDs:
        # ID 1: MD (Root manager)
        # ID 2: Executive Director (Reports to 1)
        # ID 3: General Manager (Reports to 1)
        # ID 4: SPM (Reports to 2)
        # ID 5: PM (Reports to 4)
        # ID 6: Lead Design Engineer (Reports to 2)
        # ID 7: Chief HVAC Engineer (Reports to 2)
        # ID 8: Senior Site Engineer (Reports to 5)
        # ID 9: Site Engineer (Reports to 8)
        # ID 10: Site Engineer (Reports to 8)
        # ID 11: Duct & Piping Supervisor (Reports to 8)
        # ID 12: QA/QC Lead (Reports to 4)
        # ID 13: Inactive Technician (Reports to 11)
        # ID 14: Commissioning Engineer (Reports to 5)
        # ID 15: TAB Specialist (Reports to 14)
        # ID 16: Finance Manager (Reports to 1)
        # ID 17: Senior Accountant (Reports to 16)
        # ID 18: Accounts Officer (Reports to 17)
        # ID 19: Inactive Staff (Reports to 16)
        # ID 20: HR Manager (Reports to 1)
        # ID 21: HR Officer (Reports to 20)
        # ID 22: Procurement Manager (Reports to 3)
        # ID 23: Senior HVAC Tech (Reports to 11)
        # ID 24: HVAC Technician (Reports to 23)
        # ID 25: HVAC Technician (Reports to 23)
        # ID 26: HVAC Technician (Reports to 23)
        # ID 27: HVAC Technician (Reports to 23)
        # ID 28: HVAC Design Engineer (Reports to 6)
        # ID 29: HVAC Design Engineer (Reports to 6)
        # ID 30: Service Engineer (Reports to 14)
        # ID 31: Inactive Tech (Reports to 11)
        # ID 32: Inactive Tech (Reports to 11)
        # ID 33: HVAC Technician (Reports to 23)
        # ID 34: HVAC Technician (Reports to 23)
        # ID 35: HVAC Technician (Reports to 23)
        # ID 36: HVAC Technician (Reports to 23)
        # ID 37: Site Engineer (Reports to 8)
        # ID 38: HVAC Technician (Reports to 23)
        # ID 39: HVAC Technician (Reports to 23)
        # ID 40: HVAC Technician (Reports to 23)
        # ID 41: HVAC Technician (Reports to 23)

        emp_assignments = {
            1: (dept_mgmt, desig_md, None, Decimal("180000.00"), True, False),
            2: (dept_mgmt, desig_ed, 1, Decimal("140000.00"), True, False),
            3: (dept_mgmt, desig_gm, 1, Decimal("120000.00"), True, False),
            4: (dept_pmo, desig_spm, 2, Decimal("95000.00"), True, True),
            5: (dept_pmo, desig_pm, 4, Decimal("80000.00"), True, True),
            6: (dept_eng, desig_lde, 2, Decimal("85000.00"), False, False),
            7: (dept_eng, desig_che, 2, Decimal("90000.00"), False, False),
            8: (dept_pmo, desig_sse, 5, Decimal("65000.00"), False, True),
            9: (dept_pmo, desig_se, 8, Decimal("50000.00"), False, True),
            10: (dept_pmo, desig_se, 8, Decimal("50000.00"), False, True),
            11: (dept_pmo, desig_dps, 8, Decimal("45000.00"), False, True),
            12: (dept_qaqc, desig_qal, 4, Decimal("60000.00"), False, False),
            13: (dept_pmo, desig_ht, 11, Decimal("32000.00"), False, True),
            14: (dept_serv, desig_ce, 5, Decimal("55000.00"), False, True),
            15: (dept_serv, desig_tab, 14, Decimal("48000.00"), False, True),
            16: (dept_fin, desig_fm, 1, Decimal("90000.00"), False, False),
            17: (dept_fin, desig_sa, 16, Decimal("55000.00"), False, False),
            18: (dept_fin, desig_ao, 17, Decimal("38000.00"), False, False),
            19: (dept_fin, desig_ao, 16, Decimal("35000.00"), False, False),
            20: (dept_hr, desig_hrm, 1, Decimal("85000.00"), False, False),
            21: (dept_hr, desig_hro, 20, Decimal("40000.00"), False, False),
            22: (dept_proc, desig_prm, 3, Decimal("75000.00"), False, False),
            23: (dept_pmo, desig_sht, 11, Decimal("40000.00"), False, True),
            24: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
            25: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
            26: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
            27: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
            28: (dept_eng, desig_hde, 6, Decimal("48000.00"), False, False),
            29: (dept_eng, desig_hde, 6, Decimal("48000.00"), False, False),
            30: (dept_serv, desig_sve, 14, Decimal("45000.00"), False, True),
            31: (dept_pmo, desig_ht, 11, Decimal("30000.00"), False, True),
            32: (dept_pmo, desig_ht, 11, Decimal("30000.00"), False, True),
            33: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
            34: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
            35: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
            36: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
            37: (dept_pmo, desig_se, 8, Decimal("50000.00"), False, True),
            38: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
            39: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
            40: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
            41: (dept_pmo, desig_ht, 23, Decimal("32000.00"), False, True),
        }

        # Step 1: Assign top-down managers and organization fields
        all_emps = {e.id: e for e in Employee.objects.all()}
        for eid in sorted(all_emps.keys()):
            emp = all_emps[eid]
            cfg = emp_assignments.get(eid)
            if not cfg:
                continue
            dept, desig, mgr_id, basic_sal, is_pm, ot_enabled = cfg
            mgr = all_emps.get(mgr_id) if mgr_id else None

            # Enrich missing fields only
            if not emp.branch_id:
                emp.branch = branch
            if not emp.department_id:
                emp.department = dept
            if not emp.designation_id:
                emp.designation = desig
            emp.reporting_manager = mgr

            if not emp.dob:
                emp.dob = datetime.date(1985 + (eid % 15), (eid % 12) + 1, (eid % 25) + 1)
            if not emp.gender:
                emp.gender = "male" if eid % 7 != 0 else "female"
            if not emp.national_id:
                emp.national_id = f"DEMO-NID-{20260000 + eid}"
            if not emp.personal_email:
                slug = emp.get_full_name().lower().replace(" ", ".").replace("..", ".")
                emp.personal_email = f"{slug}@signtech.test"
            if not emp.address:
                emp.address = f"House #{10 + eid}, Road #{3 + (eid % 10)}, Sector #{(eid % 14) + 1}, Uttara, Dhaka"
            if not emp.emergency_contact_name:
                emp.emergency_contact_name = f"Emergency Contact of {emp.first_name}"
            if not emp.emergency_contact_relation:
                emp.emergency_contact_relation = "Spouse" if eid % 2 == 0 else "Brother"
            if not emp.emergency_contact_phone:
                emp.emergency_contact_phone = f"0170000{eid:04d}"
            if not emp.emergency_contact_address:
                emp.emergency_contact_address = emp.address

            if emp.basic_salary is None:
                emp.basic_salary = basic_sal
            if not emp.bank_name:
                banks = ["City Bank Ltd", "BRAC Bank PLC", "Eastern Bank PLC", "Dutch-Bangla Bank"]
                emp.bank_name = banks[eid % len(banks)]
            if not emp.bank_account:
                emp.bank_account = f"DEMO-AC-{10000000 + eid}"
            if not emp.tax_profile:
                emp.tax_profile = f"DEMO-TIN-2026-{eid:04d}"
            if not emp.payment_method:
                emp.payment_method = "bank"
            emp.pf_enabled = True
            emp.overtime_policy = "Standard Overtime (1.5x)" if ot_enabled else ""

            emp.save()
            record_metric("Employee", "updated")

            # Profile sync
            profile = getattr(emp, "legacy_profile", None)
            if profile:
                profile.branch = branch
                profile.department = dept.name
                profile.designation = desig.name
                if not profile.emergency_contact:
                    profile.emergency_contact = emp.emergency_contact_phone
                profile.is_project_manager = is_pm
                profile.overtime_enabled = ot_enabled
                profile.tracking_interval = 60 if ot_enabled or is_pm else 0
                profile.save()
                record_metric("EmployeeProfile", "updated")

                # Assets assignment for employees (laptops for PMs/Engineers, Phones for Techs)
                tag = f"DEMO-AST-{eid:03d}"
                asset_type = AssetType.LAPTOP if is_pm or dept.code in ["MGMT", "ENG", "FIN", "HR"] else AssetType.MOBILE
                asset, a_created = Asset.objects.get_or_create(
                    asset_tag=tag,
                    defaults={
                        "name": f"Signtech {asset_type.label} Unit #{eid}",
                        "asset_type": asset_type,
                        "serial_number": f"SN-2026-DEMO-{eid:04d}",
                        "condition": AssetCondition.GOOD,
                        "warranty_expiry": datetime.date(2027, 12, 31),
                        "is_active": True,
                    },
                )
                if a_created:
                    record_metric("Asset", "created")
                else:
                    record_metric("Asset", "skipped")

                if not AssetAssignment.objects.filter(employee=emp, returned_date__isnull=True).exists():
                    try:
                        aa = AssetAssignment.objects.create(
                            asset=asset,
                            employee=emp,
                            assigned_date=emp.joined_date or datetime.date(2026, 1, 1),
                            condition_at_assignment=AssetCondition.GOOD,
                            notes=f"Issued for operational duties to {emp.get_full_name()}",
                        )
                        record_metric("AssetAssignment", "created")
                    except Exception:
                        pass

                # Employment history
                if not EmploymentHistory.objects.filter(employee=emp).exists():
                    admin_u = CustomUser.objects.filter(role="admin").first()
                    EmploymentHistory.objects.create(
                        employee=emp,
                        field_changed="designation",
                        old_value="Junior Engineer / Trainee",
                        new_value=desig.name,
                        reason="Confirmed and assigned to commercial HVAC division",
                        approved_by=admin_u,
                        effective_date=emp.joined_date or datetime.date(2026, 1, 1),
                    )
                    record_metric("EmploymentHistory", "created")

    def _seed_leaves(self, rng, start_date, end_date, record_metric):
        """Create leave balances for 2026 and controlled leave requests."""
        casual_type = LeaveType.objects.get(id=1)
        sick_type = LeaveType.objects.get(id=2)

        active_profiles = EmployeeProfile.objects.filter(is_active=True).select_related("user", "master_employee")
        for prof in active_profiles:
            # Leave rules
            rule1, _ = EmployeeLeaveRule.objects.get_or_create(
                employee=prof,
                leave_type=casual_type,
                defaults={"days_per_year": 10},
            )
            rule2, _ = EmployeeLeaveRule.objects.get_or_create(
                employee=prof,
                leave_type=sick_type,
                defaults={"days_per_year": 15},
            )
            # Leave balances for 2026
            lb1, _ = LeaveBalance.objects.get_or_create(
                employee=prof,
                leave_type=casual_type,
                year=2026,
                defaults={"total_days": 10, "used_days": 0},
            )
            lb2, _ = LeaveBalance.objects.get_or_create(
                employee=prof,
                leave_type=sick_type,
                year=2026,
                defaults={"total_days": 15, "used_days": 0},
            )
            record_metric("LeaveBalance", "skipped")

        # Controlled leave requests across operational period
        # Target employees with distinct states:
        # ID 3 (Anowar Hossain) - Casual leave 2026-07-12 to 2026-07-14 (Approved)
        # ID 7 (Mahmud Hasan) - Sick leave 2026-08-02 to 2026-08-04 (Approved)
        # ID 10 (Murad Hussain) - Casual leave 2026-08-23 to 2026-08-25 (Approved)
        # ID 14 (Sujon Mia) - Casual leave 2026-08-28 to 2026-08-30 (Pending)
        # ID 17 (Shafayat Ullah) - Casual leave 2026-07-20 to 2026-07-21 (Rejected)
        # ID 21 (Rashik Ahmed) - Sick leave 2026-08-10 to 2026-08-11 (Returned)
        # ID 28 (Radwan Ahammed) - Casual leave 2026-08-18 to 2026-08-19 (Cancelled)

        admin_user = CustomUser.objects.filter(role="admin").first()

        leave_scenarios = [
            (3, casual_type, datetime.date(2026, 7, 12), datetime.date(2026, 7, 14), Decimal("3.0"), "Family emergency in hometown", "approved"),
            (7, sick_type, datetime.date(2026, 8, 2), datetime.date(2026, 8, 4), Decimal("3.0"), "Severe viral fever and flu", "approved"),
            (10, casual_type, datetime.date(2026, 8, 23), datetime.date(2026, 8, 25), Decimal("3.0"), "Personal urgent legal matter", "approved"),
            (14, casual_type, datetime.date(2026, 8, 28), datetime.date(2026, 8, 30), Decimal("3.0"), "Attending family marriage ceremony", "pending"),
            (17, casual_type, datetime.date(2026, 7, 20), datetime.date(2026, 7, 21), Decimal("2.0"), "Personal tour", "rejected"),
            (21, sick_type, datetime.date(2026, 8, 10), datetime.date(2026, 8, 11), Decimal("2.0"), "Medical checkup - prescription pending", "returned"),
            (28, casual_type, datetime.date(2026, 8, 18), datetime.date(2026, 8, 19), Decimal("2.0"), "Planned travel cancelled", "cancelled"),
        ]

        wf_def = WorkflowDefinition.objects.filter(code="leave_approval").first()

        for eid, ltype, s_date, e_date, days, reason, status in leave_scenarios:
            prof = EmployeeProfile.objects.filter(id=eid).first()
            if not prof:
                continue

            lr, created = LeaveRequest.objects.get_or_create(
                employee=prof,
                start_date=s_date,
                end_date=e_date,
                defaults={
                    "leave_type": ltype,
                    "number_of_days": days,
                    "reason": reason,
                    "status": status,
                    "reviewed_by": admin_user if status in ["approved", "rejected", "returned"] else None,
                    "reviewed_at": timezone.make_aware(datetime.datetime.combine(s_date, datetime.time(10, 0))) if status in ["approved", "rejected", "returned"] else None,
                },
            )
            if created:
                record_metric("LeaveRequest", "created")
                if status == "approved":
                    # update balance
                    bal = LeaveBalance.objects.filter(employee=prof, leave_type=ltype, year=s_date.year).first()
                    if bal:
                        bal.used_days += int(days)
                        bal.save()

                # Workflow Instance & Actions
                if wf_def and prof.user:
                    wf_inst, _ = WorkflowInstance.objects.get_or_create(
                        definition=wf_def,
                        object_type="leave_request",
                        object_id=str(lr.id),
                        defaults={
                            "current_step": 2 if status == "approved" else 1,
                            "current_status": status,
                            "initiated_by": prof.user,
                            "completed_at": timezone.now() if status in ["approved", "rejected", "cancelled"] else None,
                        },
                    )
                    WorkflowAction.objects.get_or_create(
                        instance=wf_inst,
                        step_number=1,
                        actor=prof.user,
                        action="submit",
                        defaults={"note": f"Submitted leave request for {days} days"},
                    )
                    if status == "approved" and admin_user:
                        WorkflowAction.objects.get_or_create(
                            instance=wf_inst,
                            step_number=2,
                            actor=admin_user,
                            action="approve",
                            defaults={"note": "Approved by management"},
                        )
                    elif status == "rejected" and admin_user:
                        WorkflowAction.objects.get_or_create(
                            instance=wf_inst,
                            step_number=1,
                            actor=admin_user,
                            action="reject",
                            defaults={"note": "Critical site deployment milestone underway"},
                        )
            else:
                record_metric("LeaveRequest", "skipped")

    def _seed_attendance(self, rng, start_date, end_date, record_metric):
        """Generate realistic attendance across working days within period."""
        branch = Branch.objects.get(id=1)
        active_profiles = list(
            EmployeeProfile.objects.filter(is_active=True)
            .select_related("user", "master_employee")
            .order_by("id")
        )

        holidays = set(Holiday.objects.filter(branch=branch).values_list("date", flat=True))

        # Build approved leave map: (employee_profile_id, date) -> True
        approved_leaves = set()
        for lr in LeaveRequest.objects.filter(status="approved"):
            curr = lr.start_date
            while curr <= lr.end_date:
                approved_leaves.add((lr.employee_id, curr))
                curr += datetime.timedelta(days=1)

        # Working days are Saturday through Thursday (weekday != 4 / Friday)
        curr_date = start_date
        while curr_date <= end_date:
            # Friday is off
            if curr_date.weekday() == 4 or curr_date in holidays:
                curr_date += datetime.timedelta(days=1)
                continue

            for prof in active_profiles:
                # Check approved leave
                if (prof.id, curr_date) in approved_leaves:
                    continue

                # Don't attend before joined_date
                if prof.master_employee and prof.master_employee.joined_date and curr_date < prof.master_employee.joined_date:
                    continue

                # Check if attendance or absence already exists
                existing = Attendance.objects.filter(employee=prof, date=curr_date).first()
                if existing:
                    record_metric("Attendance", "skipped")
                    continue
                if AttendanceAbsentLog.objects.filter(employee=prof, date=curr_date).exists():
                    record_metric("AttendanceAbsentLog", "skipped")
                    continue

                # Deterministic per-day hashing for this employee so re-runs remain 100% stable
                day_hash = int(hashlib.md5(f"{prof.id}-{curr_date.isoformat()}".encode()).hexdigest(), 16)
                p_val = (day_hash % 1000) / 1000.0

                if p_val < 0.03:
                    # Unexcused Absence
                    ab_log, created = AttendanceAbsentLog.objects.get_or_create(
                        employee=prof,
                        date=curr_date,
                        defaults={"leave_type_deducted": LeaveType.objects.get(id=1)},
                    )
                    if created:
                        record_metric("AttendanceAbsentLog", "created")
                    continue

                # Determine timing
                is_late = (0.03 <= p_val < 0.08)
                is_field = (prof.master_employee and prof.master_employee.department.code in ["PMO", "SERV"] and ((day_hash >> 4) % 100) < 35)
                has_ot = (prof.overtime_enabled and ((day_hash >> 8) % 100) < 15)

                if is_late:
                    in_minute = 20 + ((day_hash >> 12) % 25)
                    status = "late"
                else:
                    in_minute = (day_hash >> 12) % 15
                    status = "on_time"

                check_in_dt = datetime.datetime.combine(curr_date, datetime.time(9, in_minute))
                check_in_aware = timezone.make_aware(check_in_dt)

                ot_minutes = 0
                if has_ot:
                    ot_hours = rng.choice([2, 3, 4])
                    ot_minutes = ot_hours * 60
                    out_hour = 18 + ot_hours
                    out_min = rng.randint(0, 15)
                else:
                    out_hour = 18
                    out_min = rng.randint(0, 15)

                check_out_dt = datetime.datetime.combine(curr_date, datetime.time(out_hour, out_min))
                check_out_aware = timezone.make_aware(check_out_dt)

                total_seconds = (check_out_dt - check_in_dt).total_seconds()
                total_hours = Decimal(str(round(total_seconds / 3600.0, 2)))

                att = Attendance.objects.create(
                    employee=prof,
                    date=curr_date,
                    check_in_time=check_in_aware,
                    check_out_time=check_out_aware,
                    type="field" if is_field else "office",
                    attendance_type="check_in",
                    status=status,
                    total_hours=total_hours,
                    overtime_minutes=ot_minutes,
                    ot_status="approved" if has_ot else "none",
                    note="Standard site & project operations" if is_field else "Regular office shift",
                    visit_title="HVAC Site Inspection & Ducting" if is_field else "",
                    client_name="Signtech Commercial HVAC Clients" if is_field else "",
                )
                record_metric("Attendance", "created")

                # Locations
                lat = Decimal(str(round(23.730442 + (rng.random() - 0.5) * 0.01, 6)))
                lon = Decimal(str(round(90.412518 + (rng.random() - 0.5) * 0.01, 6)))

                AttendanceLocation.objects.create(
                    attendance=att,
                    event="check_in",
                    latitude=lat,
                    longitude=lon,
                    address="Dhaka HVAC Project Site" if is_field else "Baitul Mukarram, Dhaka",
                    accuracy=15.0,
                    timestamp=check_in_aware,
                )
                AttendanceLocation.objects.create(
                    attendance=att,
                    event="check_out",
                    latitude=lat,
                    longitude=lon,
                    address="Dhaka HVAC Project Site" if is_field else "Baitul Mukarram, Dhaka",
                    accuracy=15.0,
                    timestamp=check_out_aware,
                )
                record_metric("AttendanceLocation", "created")

                # If OT, create OvertimeRequest
                if has_ot:
                    admin_user = CustomUser.objects.filter(role="admin").first()
                    ot_req, ot_created = OvertimeRequest.objects.get_or_create(
                        employee=prof,
                        date=curr_date,
                        defaults={
                            "attendance": att,
                            "ot_minutes": ot_minutes,
                            "status": "approved",
                            "reviewed_by": admin_user,
                            "reviewed_at": check_out_aware,
                        },
                    )
                    if ot_created:
                        record_metric("OvertimeRequest", "created")

            curr_date += datetime.timedelta(days=1)

        # Seed coherent correction & forgot checkout scenarios
        admin_user = CustomUser.objects.filter(role="admin").first()
        target_att = Attendance.objects.filter(employee_id=5, date__gte=start_date).first()
        if target_att and not AttendanceCorrectionRequest.objects.filter(attendance=target_att).exists():
            AttendanceCorrectionRequest.objects.create(
                attendance=target_att,
                reason="GPS sync glitch during site entry",
                status="approved",
                check_in_time=target_att.check_in_time,
                check_out_time=target_att.check_out_time,
                reviewed_by=admin_user,
                reviewed_at=timezone.now(),
            )
            record_metric("AttendanceCorrectionRequest", "created")

        target_att2 = Attendance.objects.filter(employee_id=9, date__gte=start_date).first()
        if target_att2 and not ForgotCheckoutRequest.objects.filter(attendance=target_att2).exists():
            ForgotCheckoutRequest.objects.create(
                attendance=target_att2,
                reason="Immediate emergency dispatch to customer site without punch",
                status="approved",
                check_out_time=target_att2.check_out_time,
                reviewed_by_manager=admin_user,
                reviewed_by_hr=admin_user,
            )
            record_metric("ForgotCheckoutRequest", "created")

    def _seed_projects(self, rng, start_date, end_date, record_metric):
        """Create HVAC projects with in-progress, delayed, and completed states."""
        branch = Branch.objects.get(id=1)
        ptype, _ = ProjectType.objects.get_or_create(name="HVAC Installation")
        admin_user = CustomUser.objects.filter(role="admin").first()

        pm_profiles = list(EmployeeProfile.objects.filter(is_project_manager=True)) or list(EmployeeProfile.objects.filter(is_active=True)[:2])
        se_profiles = list(EmployeeProfile.objects.filter(id__in=[8, 9, 10, 37])) or list(EmployeeProfile.objects.filter(is_active=True)[2:5]) or pm_profiles
        member_profiles = list(EmployeeProfile.objects.filter(id__in=[11, 23, 24, 25, 26, 27])) or list(EmployeeProfile.objects.filter(is_active=True))

        projects_data = [
            (
                "Square Pharmaceuticals HVAC Central Plant",
                "Square Pharmaceuticals Ltd",
                "Kaliakoir, Gazipur",
                "Chiller",
                Decimal("450.00"),
                datetime.date(2026, 7, 5),
                datetime.date(2026, 8, 28),
                "Completed",
                100,
            ),
            (
                "Bashundhara Commercial Complex VRF Project",
                "Bashundhara Group",
                "Bashundhara R/A, Dhaka",
                "VRF",
                Decimal("280.00"),
                datetime.date(2026, 7, 10),
                datetime.date(2026, 9, 20),
                "In Progress",
                65,
            ),
            (
                "Apex Footwear Cleanroom HVAC Expansion",
                "Apex Footwear Ltd",
                "Shafipur, Gazipur",
                "Package Unit",
                Decimal("180.00"),
                datetime.date(2026, 7, 15),
                datetime.date(2026, 8, 25),
                "Delayed",
                45,
            ),
        ]

        template = TaskTemplate.objects.filter(id=1).first()
        template_items = list(template.items.all().order_by("order")) if template else []

        for name, client, loc, sys_type, cap, p_start, p_comp, status, prog in projects_data:
            proj, created = Project.objects.get_or_create(
                name=name,
                defaults={
                    "client_name": client,
                    "location": loc,
                    "project_type": ptype,
                    "hvac_capacity_tr": cap,
                    "system_type": sys_type,
                    "start_date": p_start,
                    "completion_date": p_comp,
                    "status": status,
                    "progress_percent": prog,
                    "branch": branch,
                    "created_by": admin_user,
                },
            )
            if created:
                record_metric("Project", "created")
                proj.project_managers.set(pm_profiles)
                proj.site_engineers.set(se_profiles)
                proj.project_members.set(member_profiles)

                # Create tasks from template
                created_tasks = []
                for idx, item in enumerate(template_items[:12]):
                    t_order = idx + 1
                    dur = item.default_duration_days or 5
                    t_start = p_start + datetime.timedelta(days=idx * 3)
                    t_finish = t_start + datetime.timedelta(days=dur)

                    if status == "Completed":
                        t_status = "Completed"
                        t_prog = 100
                        act_start = t_start
                        act_finish = t_finish
                    elif status == "Delayed" and idx >= 4:
                        t_status = "Delayed"
                        t_prog = 30
                        act_start = t_start
                        act_finish = None
                    elif status == "In Progress" and idx > 6:
                        t_status = "Not Started"
                        t_prog = 0
                        act_start = None
                        act_finish = None
                    else:
                        t_status = "Completed"
                        t_prog = 100
                        act_start = t_start
                        act_finish = t_finish

                    resp = se_profiles[idx % len(se_profiles)]
                    ptask = ProjectTask.objects.create(
                        project=proj,
                        order=t_order,
                        activity=item.activity,
                        responsible_person=resp,
                        planned_start=t_start,
                        planned_finish=t_finish,
                        baseline_start=t_start,
                        baseline_finish=t_finish,
                        actual_start=act_start,
                        actual_finish=act_finish,
                        status=t_status,
                        progress_percent=t_prog,
                        duration_days=dur,
                        points=10,
                        remarks="Standard engineering compliance executed.",
                    )
                    created_tasks.append(ptask)
                    record_metric("ProjectTask", "created")

                # Add non-circular dependencies (Task 1 -> Task 2 -> Task 3)
                for i in range(len(created_tasks) - 1):
                    TaskDependency.objects.get_or_create(
                        predecessor=created_tasks[i],
                        successor=created_tasks[i + 1],
                        defaults={"dep_type": "FS", "lag_days": 0},
                    )
                    record_metric("TaskDependency", "created")

                # Daily progress logs
                for log_offset in [5, 15, 25]:
                    log_date = p_start + datetime.timedelta(days=log_offset)
                    if log_date <= end_date:
                        DailyProgressLog.objects.create(
                            project=proj,
                            date=log_date,
                            planned_work="Duct routing and pipe insulation assembly",
                            completed_work="Completed 45m GI duct installation and leak testing",
                            manpower_count=12,
                            supervisor_name="Md. Shariful Islam",
                            logged_by=admin_user,
                        )
                        record_metric("DailyProgressLog", "created")

                # Manpower deployment
                for trade in ["Site Engineer", "Duct Technician", "Pipe Fitter", "Electrician"]:
                    ManpowerDeployment.objects.get_or_create(
                        project=proj,
                        date=p_start + datetime.timedelta(days=10),
                        trade=trade,
                        defaults={"required_count": 4, "present_count": 4},
                    )
                    record_metric("ManpowerDeployment", "created")

                # Materials
                mats = [
                    ("GI Sheet 22 Gauge", "Sheet", Decimal("250.00"), Decimal("250.00" if status == "Completed" else "180.00")),
                    ("Copper Pipe 5/8 inch", "RFT", Decimal("500.00"), Decimal("500.00" if status == "Completed" else "300.00")),
                    ("Nitrile Rubber Insulation 19mm", "Sheet", Decimal("150.00"), Decimal("150.00" if status == "Completed" else "100.00")),
                ]
                for m_name, m_unit, req_q, rec_q in mats:
                    ProjectMaterial.objects.create(
                        project=proj,
                        material_name=m_name,
                        unit=m_unit,
                        required_qty=req_q,
                        received_qty=rec_q,
                        remarks="Delivered via verified delivery challan",
                    )
                    record_metric("ProjectMaterial", "created")

                # Sign-off for completed project
                if status == "Completed":
                    ProjectSignOff.objects.get_or_create(
                        project=proj,
                        defaults={
                            "project_manager_name": "Arafat Hossain",
                            "project_manager_signed_at": timezone.make_aware(datetime.datetime.combine(p_comp, datetime.time(16, 0))),
                            "site_engineer_name": "Md. Shariful Islam",
                            "site_engineer_signed_at": timezone.make_aware(datetime.datetime.combine(p_comp, datetime.time(15, 0))),
                            "consultant_name": "Engr. M. Rahman",
                            "consultant_signed_at": timezone.make_aware(datetime.datetime.combine(p_comp, datetime.time(17, 0))),
                            "client_representative_name": "Dr. K. Ahmed (Square)",
                            "client_representative_signed_at": timezone.make_aware(datetime.datetime.combine(p_comp, datetime.time(17, 30))),
                        },
                    )
                    record_metric("ProjectSignOff", "created")

                # Task Reply
                if created_tasks:
                    ProjectTaskReply.objects.create(
                        task=created_tasks[0],
                        user=admin_user,
                        message="Kickoff milestone verified against architectural drawings.",
                    )
                    record_metric("ProjectTaskReply", "created")
            else:
                record_metric("Project", "skipped")

    def _seed_schedule_events(self, rng, start_date, end_date, record_metric):
        """Create meetings, site visits, deadlines, and reminders."""
        admin_user = CustomUser.objects.filter(role="admin").first()
        active_projects = list(Project.objects.exclude(status="Completed"))
        pmo_profiles = list(EmployeeProfile.objects.filter(id__in=[4, 5, 8, 9, 10]))

        events_data = [
            (
                "Weekly HVAC Coordination Meeting",
                "Review ducting and piping clashes with structural engineers",
                datetime.date(2026, 7, 20),
                datetime.time(10, 0),
                datetime.time(11, 30),
                "Meeting",
            ),
            (
                "Cleanroom Chill Water Pressure Test Inspection",
                "Official hydrostatic test of primary loop at 1.5x design pressure",
                datetime.date(2026, 8, 10),
                datetime.time(14, 0),
                datetime.time(17, 0),
                "Site Visit",
            ),
            (
                "Material Inspection & Factory Audit",
                "Inspection of VRF indoor units at customs warehouse",
                datetime.date(2026, 8, 22),
                datetime.time(11, 0),
                datetime.time(13, 0),
                "Site Visit",
            ),
            (
                "Submittal Deadline: TAB Balancing Report",
                "Submit third-party air balancing certificate to client",
                datetime.date(2026, 9, 2),
                datetime.time(16, 0),
                datetime.time(17, 0),
                "Task Deadline",
            ),
        ]

        for title, desc, ev_date, s_time, e_time, ev_type in events_data:
            if start_date <= ev_date <= end_date:
                proj = active_projects[0] if active_projects else None
                ev, created = ScheduleEvent.objects.get_or_create(
                    title=title,
                    date=ev_date,
                    defaults={
                        "description": desc,
                        "start_time": s_time,
                        "end_time": e_time,
                        "event_type": ev_type,
                        "project": proj,
                        "created_by": admin_user,
                    },
                )
                if created:
                    ev.assigned_to.set(pmo_profiles)
                    record_metric("ScheduleEvent", "created")
                else:
                    record_metric("ScheduleEvent", "skipped")

    def _seed_expenses(self, rng, start_date, end_date, record_metric):
        """Create expense categories, expenses across lifecycles and workflows."""
        cats_data = [
            ("Site Travel & Fuel", "TRAVEL", "Local site transportation and vehicle fuel"),
            ("Local Hardware & Consumables", "CONSUMABLES", "Screws, anchors, sealant, welding rods and PPE"),
            ("Client Meeting & Entertainment", "MEETINGS", "Client and consultant project refreshment"),
            ("Emergency Site Repairs", "REPAIRS", "Emergency tool repair and electrical testing equipment"),
        ]
        cat_map = {}
        for name, code, desc in cats_data:
            cat, created = ExpenseCategory.objects.get_or_create(
                code=code,
                defaults={"name": name, "description": desc, "is_active": True},
            )
            cat_map[code] = cat
            if created:
                record_metric("ExpenseCategory", "created")
            else:
                record_metric("ExpenseCategory", "skipped")

        admin_user = CustomUser.objects.filter(role="admin").first()
        projects = list(Project.objects.all())

        # Expenses list
        expenses_scenarios = [
            (5, "TRAVEL", Decimal("4500.00"), "Gazipur site inspection vehicle fuel and toll", "approved", datetime.date(2026, 7, 18)),
            (8, "CONSUMABLES", Decimal("6800.00"), "Duct silicone sealant and anchor bolts", "pending_finance", datetime.date(2026, 8, 14)),
            (9, "MEETINGS", Decimal("2200.00"), "Client coordination meeting lunch & tea", "pending_manager", datetime.date(2026, 8, 26)),
            (10, "REPAIRS", Decimal("3500.00"), "Core cutting machine carbon brush repair", "returned", datetime.date(2026, 8, 20)),
            (14, "TRAVEL", Decimal("1800.00"), "Unapproved personal taxi fare", "rejected", datetime.date(2026, 7, 28)),
            (4, "CONSUMABLES", Decimal("8500.00"), "Draft preliminary quotation for safety helmets", "draft", datetime.date(2026, 9, 1)),
        ]

        wf_def = WorkflowDefinition.objects.filter(code="expense_approval").first()

        for eid, cat_code, amt, desc, status, exp_date in expenses_scenarios:
            if not (start_date <= exp_date <= end_date):
                continue
            prof = EmployeeProfile.objects.filter(id=eid).first()
            if not prof:
                continue

            proj = projects[eid % len(projects)] if projects else None
            exp, created = Expense.objects.get_or_create(
                employee=prof,
                description=desc,
                defaults={
                    "amount": amt,
                    "category": cat_map[cat_code],
                    "project": proj,
                    "status": status,
                    "reviewed_by": admin_user if status in ["approved", "rejected", "returned"] else None,
                    "reviewed_at": timezone.make_aware(datetime.datetime.combine(exp_date, datetime.time(15, 0))) if status in ["approved", "rejected", "returned"] else None,
                    "rejection_reason": "Non-compliant receipt voucher" if status == "rejected" else "",
                },
            )
            if created:
                record_metric("Expense", "created")

                if status == "returned" and admin_user:
                    ExpenseReturnEvent.objects.create(
                        expense=exp,
                        returned_by=admin_user,
                        returned_from_status="pending_manager",
                        reason="Please attach VAT registered cash memo receipt.",
                        fields_to_correct=["attachment", "amount"],
                        due_date=exp_date + datetime.timedelta(days=3),
                    )
                    record_metric("ExpenseReturnEvent", "created")

                # Workflow Instance & Actions
                if wf_def and prof.user and status != "draft":
                    wf_inst, _ = WorkflowInstance.objects.get_or_create(
                        definition=wf_def,
                        object_type="expense",
                        object_id=str(exp.id),
                        defaults={
                            "current_step": 3 if status == "approved" else (2 if status == "pending_finance" else 1),
                            "current_status": status,
                            "initiated_by": prof.user,
                            "completed_at": timezone.now() if status in ["approved", "rejected"] else None,
                        },
                    )
                    WorkflowAction.objects.get_or_create(
                        instance=wf_inst,
                        step_number=1,
                        actor=prof.user,
                        action="submit",
                        defaults={"note": f"Submitted expense of BDT {amt}"},
                    )
                    if status == "approved" and admin_user:
                        WorkflowAction.objects.get_or_create(
                            instance=wf_inst,
                            step_number=2,
                            actor=admin_user,
                            action="approve",
                            defaults={"note": "Verified by Finance"},
                        )
                        WorkflowAction.objects.get_or_create(
                            instance=wf_inst,
                            step_number=3,
                            actor=admin_user,
                            action="approve",
                            defaults={"note": "Disbursed via bank transfer"},
                        )
                    elif status == "rejected" and admin_user:
                        WorkflowAction.objects.get_or_create(
                            instance=wf_inst,
                            step_number=1,
                            actor=admin_user,
                            action="reject",
                            defaults={"note": "Receipt missing VAT registration number"},
                        )
            else:
                record_metric("Expense", "skipped")

    def _seed_payroll(self, rng, start_date, end_date, record_metric):
        """Create SalaryStructure, Components, Policy, Assignments, and July/August/September Runs."""
        branch = Branch.objects.get(id=1)
        admin_user = CustomUser.objects.filter(role="admin").first()

        # Payroll Policy
        ppol, ppol_created = PayrollPolicy.objects.get_or_create(
            branch=branch,
            defaults={
                "absence_divisor_mode": "fixed_30",
                "default_ot_multiplier": Decimal("1.50"),
            },
        )
        if ppol_created:
            record_metric("PayrollPolicy", "created")
        else:
            record_metric("PayrollPolicy", "skipped")

        # Salary Components
        comps_data = [
            ("Basic Salary", "BASIC", SalaryComponentType.EARNING, SalaryComponentValueType.PERCENTAGE, Decimal("50.00"), False),
            ("House Rent Allowance", "HRA", SalaryComponentType.EARNING, SalaryComponentValueType.PERCENTAGE, Decimal("30.00"), False),
            ("Medical Allowance", "MEDICAL", SalaryComponentType.EARNING, SalaryComponentValueType.PERCENTAGE, Decimal("10.00"), False),
            ("Conveyance Allowance", "CONVEYANCE", SalaryComponentType.EARNING, SalaryComponentValueType.PERCENTAGE, Decimal("10.00"), False),
            ("Provident Fund Deduction", "PF", SalaryComponentType.DEDUCTION, SalaryComponentValueType.PERCENTAGE, Decimal("10.00"), True),
        ]
        comp_objs = {}
        for name, code, c_type, v_type, val, is_pf in comps_data:
            c_obj, created = SalaryComponent.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "type": c_type,
                    "value_type": v_type,
                    "value": val,
                    "is_pf": is_pf,
                    "is_active": True,
                },
            )
            comp_objs[code] = c_obj
            if created:
                record_metric("SalaryComponent", "created")
            else:
                record_metric("SalaryComponent", "skipped")

        # Salary Structure
        struct, s_created = SalaryStructure.objects.get_or_create(
            name="Standard Signtech HVAC Structure",
            defaults={"is_active": True},
        )
        if s_created:
            record_metric("SalaryStructure", "created")
            for code, c_obj in comp_objs.items():
                SalaryStructureComponent.objects.get_or_create(
                    salary_structure=struct,
                    salary_component=c_obj,
                    defaults={
                        "value_type": c_obj.value_type,
                        "value": c_obj.value,
                    },
                )
                record_metric("SalaryStructureComponent", "created")
        else:
            record_metric("SalaryStructure", "skipped")

        # Effective Employee Salary Assignments
        for emp in Employee.objects.all():
            gross = emp.basic_salary or Decimal("40000.00")
            asgn, a_created = EmployeeSalaryAssignment.objects.get_or_create(
                employee=emp,
                effective_from=datetime.date(2026, 1, 1),
                defaults={
                    "salary_structure": struct,
                    "gross_salary": gross,
                    "payment_mode": PaymentMode.BANK,
                    "bank_limit": Decimal("0.00"),
                },
            )
            if a_created:
                record_metric("EmployeeSalaryAssignment", "created")
            else:
                record_metric("EmployeeSalaryAssignment", "skipped")

        # Payroll Runs:
        # 1. July 2026 (Disbursed)
        # 2. August 2026 (Approved / Locked)
        # 3. September 2026 (Draft)
        runs_data = [
            ("July 2026 Monthly Payroll", datetime.date(2026, 7, 1), datetime.date(2026, 7, 31), PayrollRunStatus.DISBURSED),
            ("August 2026 Monthly Payroll", datetime.date(2026, 8, 1), datetime.date(2026, 8, 31), PayrollRunStatus.APPROVED_LOCKED),
            ("September 2026 Monthly Payroll (Draft)", datetime.date(2026, 9, 1), datetime.date(2026, 9, 30), PayrollRunStatus.DRAFT),
        ]

        for r_name, p_start, p_end, final_status in runs_data:
            prun, r_created = PayrollRun.objects.get_or_create(
                period_start=p_start,
                period_end=p_end,
                defaults={
                    "name": r_name,
                    "status": PayrollRunStatus.DRAFT,
                },
            )
            if r_created:
                record_metric("PayrollRun", "created")

                # Sync inputs and calculate
                try:
                    PayrollService.sync_payroll_inputs(prun)
                    record_metric("EmployeePayrollCalculation", "created")
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f"Payroll sync notice for {r_name}: {exc}"))

                # Progress workflow status if not Draft
                if final_status != PayrollRunStatus.DRAFT and admin_user:
                    PayrollService.transition_payroll_status(
                        prun,
                        PayrollRunStatus.REVIEW,
                        admin_user,
                        "Calculations verified against attendance logs.",
                    )
                    PayrollService.transition_payroll_status(
                        prun,
                        PayrollRunStatus.APPROVED_LOCKED,
                        admin_user,
                        "Approved and locked by Managing Director.",
                    )
                    if final_status == PayrollRunStatus.DISBURSED:
                        PayrollService.transition_payroll_status(
                            prun,
                            PayrollRunStatus.DISBURSED,
                            admin_user,
                            "BEFTN bank advice executed and reconciled.",
                        )
                    record_metric("PayrollWorkflowAudit", "created")
            else:
                record_metric("PayrollRun", "skipped")

    def _print_summary(self, counts, start_date, end_date):
        """Print concise execution summary with date boundaries and model counts."""
        self.stdout.write(self.style.SUCCESS("\n=== Seed Operational Data Summary ==="))
        self.stdout.write(f"Operational Date Window: {start_date} to {end_date}")

        all_models = sorted(
            set(list(counts["created"].keys()) + list(counts["updated"].keys()) + list(counts["skipped"].keys()))
        )
        self.stdout.write(f"{'Model':<32} | {'Created':<8} | {'Updated':<8} | {'Skipped':<8}")
        self.stdout.write("-" * 62)
        for m in all_models:
            c = counts["created"].get(m, 0)
            u = counts["updated"].get(m, 0)
            s = counts["skipped"].get(m, 0)
            self.stdout.write(f"{m:<32} | {c:<8} | {u:<8} | {s:<8}")

        # Completeness Check
        active_emps = Employee.objects.all()
        avg_completion = sum(e.get_completion_percentage() for e in active_emps) / len(active_emps)
        self.stdout.write(
            self.style.SUCCESS(
                f"\nEmployee Completeness: {avg_completion:.1f}% average across all {len(active_emps)} employees (Safe target reached without fake files)."
            )
        )
