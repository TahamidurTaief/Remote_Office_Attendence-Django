from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.branches.models import Branch
from apps.employees.models import Bank, BankBranch, Employee, EmployeeBankAccount
from apps.employees.bank_crypto import (
    encrypt_account_number,
    decrypt_account_number,
    mask_account_number,
    normalize_account_number,
)
from apps.employees.bank_service import BankService, can_verify_bank_account
from apps.employees.forms import WizardStep3Form
from apps.employees.management.commands.seed_bangladesh_banks import seed_directory

User = get_user_model()


class BangladeshBankDirectoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Dhaka HQ",
            address="Gulshan, Dhaka",
            latitude=Decimal("23.810300"),
            longitude=Decimal("90.412500"),
        )
        cls.b_count, cls.br_count = seed_directory()

    def test_directory_seeding_and_integrity(self):
        """Verify official Bangladesh Bank directory models, codes, and 9-digit routing numbers."""
        self.assertGreaterEqual(Bank.objects.count(), 20)
        self.assertGreaterEqual(BankBranch.objects.count(), 30)

        dbbl = Bank.objects.get(code="DBBL")
        self.assertEqual(dbbl.name, "Dutch-Bangla Bank PLC")
        self.assertTrue(dbbl.is_active)

        branches = dbbl.branches.filter(is_active=True)
        self.assertGreaterEqual(branches.count(), 3)
        for br in branches:
            self.assertEqual(len(br.routing_number), 9, f"Routing number {br.routing_number} must be 9 digits")
            self.assertTrue(br.routing_number.isdigit())

    def test_account_encryption_and_masking(self):
        """Verify Fernet encryption at rest and UI/log masking."""
        raw_account = "15012012345678"
        encrypted = encrypt_account_number(raw_account)

        self.assertNotEqual(raw_account, encrypted)
        self.assertFalse(raw_account in encrypted)

        decrypted = decrypt_account_number(encrypted)
        self.assertEqual(raw_account, decrypted)

        masked = mask_account_number(raw_account)
        self.assertEqual(masked, "**********5678")

    def test_dependent_selection_and_forged_post_rejection(self):
        """Verify server rejects branch belonging to another bank and prevents forged routing."""
        dbbl = Bank.objects.get(code="DBBL")
        brac = Bank.objects.get(code="BRAC")

        dbbl_branch = dbbl.branches.first()
        brac_branch = brac.branches.first()

        emp = Employee.objects.create(
            employee_number="EMP-BANK-001",
            first_name="Rahim",
            last_name="Uddin",
            branch=self.branch,
            payment_method="bank",
        )

        # Forged combination: Bank DBBL with BRAC branch
        with self.assertRaises(ValidationError) as ctx:
            BankService.save_employee_bank_account(
                employee=emp,
                bank=dbbl,
                branch=brac_branch,
                account_holder_name="Rahim Uddin",
                account_number="1234567890",
            )
        self.assertIn("branch", str(ctx.exception).lower())

        # Valid combination: DBBL with DBBL branch
        account = BankService.save_employee_bank_account(
            employee=emp,
            bank=dbbl,
            branch=dbbl_branch,
            account_holder_name="Rahim Uddin",
            account_number="1234567890",
        )
        self.assertEqual(account.routing_number, dbbl_branch.routing_number)
        self.assertEqual(account.verification_status, EmployeeBankAccount.VerificationStatus.PENDING)
        self.assertFalse(account.is_payout_ready)

    def test_wizard_step_3_validation_and_backward_compatibility(self):
        """Verify Wizard Step 3 enforces bank rules on bank mode and keeps Employee fields synced."""
        dbbl = Bank.objects.get(code="DBBL")
        dbbl_branch = dbbl.branches.first()

        emp = Employee.objects.create(
            employee_number="EMP-BANK-002",
            first_name="Karim",
            last_name="Chowdhury",
            branch=self.branch,
        )

        # 1. Invalid: Bank Transfer without bank details
        form_invalid = WizardStep3Form(
            data={
                "payment_method": "bank",
                "basic_salary": "60000.00",
                "salary_structure": "Standard Salary Structure",
            },
            instance=emp,
        )
        self.assertFalse(form_invalid.is_valid())
        self.assertIn("bank", form_invalid.errors)

        # 2. Valid: Cash mode without bank details
        form_cash = WizardStep3Form(
            data={
                "payment_method": "cash",
                "basic_salary": "40000.00",
                "salary_structure": "Standard Salary Structure",
            },
            instance=emp,
        )
        self.assertTrue(form_cash.is_valid(), form_cash.errors)

        # 3. Valid: Bank mode with full details
        form_valid = WizardStep3Form(
            data={
                "payment_method": "bank",
                "bank": dbbl.pk,
                "branch": dbbl_branch.pk,
                "account_holder_name": "Karim Chowdhury",
                "bank_account": "998877665544",
                "basic_salary": "75000.00",
                "salary_structure": "Standard Salary Structure",
            },
            instance=emp,
        )
        self.assertTrue(form_valid.is_valid(), form_valid.errors)
        saved_emp = form_valid.save()

        # Check backward compatibility fields on Employee
        self.assertEqual(saved_emp.bank_name, dbbl.name)
        self.assertEqual(saved_emp.bank_account, "998877665544")

        # Check structured account model
        acc = saved_emp.primary_bank_account
        self.assertIsNotNone(acc)
        self.assertEqual(acc.bank, dbbl)
        self.assertEqual(acc.branch, dbbl_branch)
        self.assertEqual(acc.routing_number, dbbl_branch.routing_number)
        self.assertEqual(acc.get_account_number(), "998877665544")
        self.assertEqual(acc.masked_account_number, "•••• •••• •••• 5544")

    def test_verification_workflow_and_payout_gate(self):
        """Verify role authorization for verification and gating of unverified accounts."""
        city = Bank.objects.get(code="CITY")
        branch = city.branches.first()

        emp = Employee.objects.create(
            employee_number="EMP-BANK-003",
            first_name="Fatima",
            last_name="Begum",
            branch=self.branch,
            payment_method="bank",
        )
        account = BankService.save_employee_bank_account(
            employee=emp,
            bank=city,
            branch=branch,
            account_holder_name="Fatima Begum",
            account_number="2200334455",
        )

        # Unverified account is NOT payout ready
        self.assertFalse(account.is_payout_ready)

        # Unauthorized user (regular staff) attempts verification
        staff_user = User.objects.create_user(email="staff1@example.com", password="pw", role="staff")
        with self.assertRaises(PermissionDenied):
            BankService.verify_bank_account(account, actor=staff_user)

        # Authorized finance user verifies account
        finance_user = User.objects.create_user(email="fin1@example.com", password="pw", role="finance")
        verified_account = BankService.verify_bank_account(account, actor=finance_user, note="Verified via bank statement")

        self.assertEqual(verified_account.verification_status, EmployeeBankAccount.VerificationStatus.VERIFIED)
        self.assertEqual(verified_account.verified_by, finance_user)
        self.assertIsNotNone(verified_account.verified_at)
        self.assertTrue(verified_account.is_payout_ready)

    def test_legacy_bank_mapping_and_unmatched_preservation(self):
        """Verify legacy bank names map accurately and unmatched values are safely preserved."""
        # 1. Matched legacy record
        emp_legacy = Employee.objects.create(
            employee_number="EMP-LEG-001",
            first_name="Tanvir",
            last_name="Hasan",
            branch=self.branch,
            bank_name="Dutch-Bangla Bank Ltd",
            bank_account="1501209999",
            payment_method="bank",
        )
        success, msg = BankService.map_legacy_bank_account(emp_legacy)
        self.assertTrue(success)
        self.assertEqual(emp_legacy.primary_bank_account.bank.code, "DBBL")

        # 2. Unmatched legacy record
        emp_unmatched = Employee.objects.create(
            employee_number="EMP-LEG-002",
            first_name="Faruk",
            last_name="Ahmed",
            branch=self.branch,
            bank_name="Unknown Cooperative Society Bank",
            bank_account="777888999",
            payment_method="bank",
        )
        success_u, msg_u = BankService.map_legacy_bank_account(emp_unmatched)
        self.assertFalse(success_u)
        # Original values preserved untouched
        self.assertEqual(emp_unmatched.bank_name, "Unknown Cooperative Society Bank")
        self.assertEqual(emp_unmatched.bank_account, "777888999")
