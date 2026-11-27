from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.branches.models import Branch
from apps.employees.forms import EmployeeMasterForm
from apps.employees.models import Department, Designation, Employee, EmployeeProfile, EmployeeStatus

User = get_user_model()


class CanonicalProfileSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="Sync Branch", latitude=23.81, longitude=90.41, radius_meters=200)
        cls.dept_global = Department.objects.create(name="Global Dept", is_global=True)
        cls.dept_branch = Department.objects.create(name="Branch Dept", is_global=False)
        cls.dept_branch.branches.add(cls.branch)
        cls.desig = Designation.objects.create(name="Analyst")
        cls.admin = User.objects.create_superuser(email="sync_adm@example.com", password="pass", role="admin")

    def _make_linked_emp(self, code, phone="01711112222", dept=None):
        u = User.objects.create_user(email=f"{code.lower()}@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u,
            employee_number=code,
            first_name="Sync",
            last_name=code,
            phone=phone,
            branch=self.branch,
            department=dept or self.dept_global,
            designation=self.desig,
            joined_date=date(2024, 1, 1),
            status=EmployeeStatus.ACTIVE,
        )
        p = EmployeeProfile.objects.create(
            user=u,
            master_employee=m,
            employee_id=code,
            full_name=f"Sync {code}",
            phone=phone,
            branch=self.branch,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )
        return m, p

    def test_1_canonical_phone_and_branch_clear_syncs_to_profile(self):
        master, profile = self._make_linked_emp("SYNC-1", phone="01788889999")
        master.phone = ""
        master.branch = None
        master.save()

        profile.refresh_from_db()
        self.assertEqual(profile.phone, "")
        self.assertIsNone(profile.branch_id)

    def test_2_invalid_branch_clear_fails_validation_without_partial_persistence(self):
        master, profile = self._make_linked_emp("SYNC-2", phone="01777776666", dept=self.dept_branch)
        form = EmployeeMasterForm(
            data={
                "employee_number": master.employee_number,
                "first_name": "UpdatedName",
                "last_name": master.last_name,
                "phone": master.phone,
                "branch": "",
                "department": str(self.dept_branch.id),
                "designation": str(self.desig.id),
                "joined_date": "2024-01-01",
                "employment_type": master.employment_type,
                "data_scope": master.data_scope,
            },
            instance=master,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("branch", form.errors)
        master.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(master.first_name, "Sync")
        self.assertEqual(master.branch_id, self.branch.id)
        self.assertEqual(profile.branch_id, self.branch.id)

    def test_3_no_linked_profile_and_no_directory_wide_reconcile(self):
        # Employee without linked profile
        emp_only = Employee.objects.create(
            employee_number="SYNC-NOPROF",
            first_name="No",
            last_name="Profile",
            phone="01700001111",
            branch=self.branch,
            joined_date=date(2024, 1, 1),
        )
        emp_only.phone = ""
        emp_only.branch = None
        emp_only.save()
        emp_only.refresh_from_db()
        self.assertEqual(emp_only.phone, "")

        # Saving EmployeeProfile must never trigger reconcile_all_employee_profiles
        _, profile = self._make_linked_emp("SYNC-NOREC", phone="01722223333")
        with patch("apps.employees.reconciliation.reconcile_all_employee_profiles", side_effect=AssertionError("directory scan should not run")):
            profile.department = "Updated"
            profile.save(update_fields=["department"])

    def test_4_unrelated_update_fields_and_bounded_edit_queries(self):
        master, profile = self._make_linked_emp("SYNC-UF", phone="01733334444")
        with CaptureQueriesContext(connection) as ctx_uf:
            master.personal_email = "uf@example.com"
            master.save(update_fields=["personal_email"])
        self.assertLessEqual(len(ctx_uf), 15)
        self.assertFalse(
            any("employees_employeeprofile" in q["sql"].lower() and "update" in q["sql"].lower() for q in ctx_uf.captured_queries)
        )

        self.client.force_login(self.admin)
        url = reverse("employees:employee_edit", kwargs={"pk": master.pk})
        with patch("apps.employees.reconciliation.reconcile_all_employee_profiles", side_effect=AssertionError("directory scan should not run")), \
             CaptureQueriesContext(connection) as ctx_edit:
            resp = self.client.post(
                url,
                {
                    "employee_number": master.employee_number,
                    "first_name": "Sync",
                    "last_name": "SYNC-UF",
                    "phone": "",
                    "branch": "",
                    "department": str(self.dept_global.id),
                    "designation": str(self.desig.id),
                    "joined_date": "2024-01-01",
                    "employment_type": master.employment_type,
                    "data_scope": master.data_scope,
                },
            )
        self.assertIn(resp.status_code, (200, 302))
        self.assertLessEqual(len(ctx_edit), 65)
        profile.refresh_from_db()
        self.assertEqual(profile.phone, "")
        self.assertIsNone(profile.branch_id)
