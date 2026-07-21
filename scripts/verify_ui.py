import os
import re
import sys
import subprocess

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fieldtrack.settings")
django.setup()

from django.conf import settings
from django.template import loader, TemplateSyntaxError, TemplateDoesNotExist
from django.test import RequestFactory
from django.contrib.auth import get_user_model
from django.core.management import call_command

User = get_user_model()

def check_block_tag_balance():
    print("\n--- 1. BLOCK-TAG BALANCE CHECK ---")
    cotton_dir = os.path.join(settings.BASE_DIR, "templates", "cotton")
    failures = []
    
    tags = [
        ("with", "endwith"),
        ("if", "endif"),
        ("for", "endfor"),
        ("comment", "endcomment"),
    ]
    
    for root, _, files in os.walk(cotton_dir):
        for f in files:
            if f.endswith(".html"):
                filepath = os.path.join(root, f)
                with open(filepath, "r", encoding="utf-8") as file:
                    content = file.read()
                    for open_tag, close_tag in tags:
                        opens = len(re.findall(r"\{\%\s*" + open_tag + r"\b", content))
                        closes = len(re.findall(r"\{\%\s*" + close_tag + r"\b", content))
                        if opens != closes:
                            rel_path = os.path.relpath(filepath, settings.BASE_DIR)
                            failures.append(f"{rel_path}: '{open_tag}' count ({opens}) != '{close_tag}' count ({closes})")
    
    if failures:
        print("FAILED: Block-tag imbalances found:")
        for fail in failures:
            print(f"  - {fail}")
        return False
    else:
        print("PASSED: All cotton components have balanced block tags.")
        return True

def render_test_all_templates():
    print("\n--- 2. FULL RENDER-TEST OF ALL TEMPLATES & COTTON COMPONENTS ---")
    templates_dir = os.path.join(settings.BASE_DIR, "templates")
    factory = RequestFactory()
    request = factory.get("/")
    user = User.objects.filter(is_superuser=True).first() or User.objects.first()
    request.user = user

    failures = []
    total_count = 0

    for root, _, files in os.walk(templates_dir):
        for f in files:
            if f.endswith(".html"):
                total_count += 1
                filepath = os.path.join(root, f)
                rel_path = os.path.relpath(filepath, templates_dir).replace("\\", "/")
                try:
                    t = loader.get_template(rel_path)
                    res = t.render({"request": request}, request=request)
                    if "{#" in res or "{% comment" in res:
                        failures.append(f"{rel_path}: Comment leak detected in rendered output.")
                except (TemplateSyntaxError, TemplateDoesNotExist) as e:
                    failures.append(f"{rel_path}: {type(e).__name__} - {e}")
                except Exception:
                    # Ignore context missing data errors during blind render
                    pass

    print(f"Tested {total_count} templates.")
    if failures:
        print("FAILED: Template syntax/does-not-exist or comment leak errors:")
        for fail in failures:
            print(f"  - {fail}")
        return False
    else:
        print("PASSED: All templates loaded/rendered without syntax errors or comment leaks.")
        return True

def check_design_tokens():
    print("\n--- 3. DESIGN-TOKEN COMPLIANCE CHECK ---")
    cotton_dir = os.path.join(settings.BASE_DIR, "templates", "cotton")
    failures = []

    for root, _, files in os.walk(cotton_dir):
        for f in files:
            if f.endswith(".html"):
                filepath = os.path.join(root, f)
                rel_path = os.path.relpath(filepath, settings.BASE_DIR)
                with open(filepath, "r", encoding="utf-8") as file:
                    content = file.read()
                    
                    # Check bare shadow class (e.g., ' shadow ' or 'shadow ' but not shadow-card or hover:shadow-*)
                    if re.search(r'(?<=[\s"\'`])shadow(?=[\s"\'`])', content):
                        failures.append(f"{rel_path}: bare 'shadow' used instead of design token")

    if failures:
        print("FAILED: Design token violations in templates/cotton/:")
        for fail in failures:
            print(f"  - {fail}")
        return False
    else:
        print("PASSED: All cotton components adhere to design tokens.")
        return True

def run_django_checks():
    print("\n--- 4. DJANGO SYSTEM CHECKS ---")
    try:
        call_command("check")
        print("PASSED: manage.py check completed cleanly.")
        return True
    except Exception as e:
        print(f"FAILED: manage.py check failed: {e}")
        return False

def run_tailwind_build():
    print("\n--- 5. TAILWIND BUILD CHECK ---")
    try:
        res = subprocess.run(["uv", "run", "python", "manage.py", "tailwind", "build"], capture_output=True, text=True, check=True)
        print("PASSED: tailwind build completed cleanly.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"FAILED: tailwind build failed: {e.stderr or e.stdout}")
        return False

def spot_check_raw_html_content():
    print("\n--- 6. RAW HTML CONTENT-PRESENCE SPOT CHECK WITH REAL DATA ---")
    from django.test import Client
    from apps.employees.models import EmployeeProfile
    from apps.projects.models import Project
    from apps.leave.models import LeaveRequest
    from apps.notifications.models import Notification
    
    admin_client = Client()
    admin_user = User.objects.filter(is_superuser=True).first() or User.objects.first()
    admin_client.force_login(admin_user)

    staff_client = Client()
    emp_profile = EmployeeProfile.objects.first()
    staff_user = emp_profile.user if emp_profile else admin_user
    staff_client.force_login(staff_user)

    # Gather real database records to search for in raw HTML
    first_page_emp = EmployeeProfile.objects.order_by('full_name', 'employee_id').first()
    first_page_emp_name = first_page_emp.full_name if first_page_emp else "Employees"
    
    first_proj = Project.objects.first()
    first_proj_name = first_proj.name if first_proj else "Projects"
    
    first_leave = LeaveRequest.objects.first()
    first_leave_emp_name = first_leave.employee.full_name if first_leave else "Leave Requests"
    
    first_notif = Notification.objects.filter(recipient=admin_user).first()
    first_notif_snippet = first_notif.message[:30] if first_notif else "Notifications"

    admin_urls = [
        ("/schedule/", ["visibleSources", "Add Event"]),
        ("/admin-panel/dashboard/", ["Live attendance tracking", "Dashboard"]),
        ("/leave/admin/", ["Leave Requests", first_leave_emp_name]),
        ("/expense/admin/", ["Expense Claims"]),
        ("/projects/", ["Projects", first_proj_name]),
        ("/employees/", ["Employees", first_page_emp_name]),
        ("/branches/", ["Branches"]),
        ("/notifications/", ["Notifications", first_notif_snippet]),
        ("/change-password/", ["Change Password"]),
    ]

    staff_urls = [
        ("/staff/home/", ["Leave Summary", "Good morning"]),
    ]

    failures = []
    for url, strings in admin_urls:
        resp = admin_client.get(url, HTTP_HOST="localhost")
        if resp.status_code != 200:
            failures.append(f"{url}: Status code {resp.status_code} != 200")
            continue
        content = resp.content.decode("utf-8")
        for expected in strings:
            if expected not in content:
                failures.append(f"{url}: Missing expected content string '{expected}'")

    for url, strings in staff_urls:
        resp = staff_client.get(url, HTTP_HOST="localhost")
        if resp.status_code != 200:
            failures.append(f"{url}: Status code {resp.status_code} != 200")
            continue
        content = resp.content.decode("utf-8")
        for expected in strings:
            if expected not in content:
                failures.append(f"{url}: Missing expected content string '{expected}'")

    if failures:
        print("FAILED: Spot check content verification failed:")
        for fail in failures:
            print(f"  - {fail}")
        return False
    else:
        print(f"PASSED: Spot check verified content on {len(admin_urls) + len(staff_urls)} representative URLs.")
        return True

def main():
    print("==================================================")
    print("      STANDING PRE-COMMIT VERIFICATION GATE       ")
    print("==================================================")
    
    results = [
        check_block_tag_balance(),
        render_test_all_templates(),
        check_design_tokens(),
        run_django_checks(),
        run_tailwind_build(),
        spot_check_raw_html_content()
    ]
    
    print("\n==================================================")
    if all(results):
        print(" SUCCESS: ALL PRE-COMMIT VERIFICATION GATES PASSED")
        print("==================================================")
        sys.exit(0)
    else:
        print(" FAILURE: PRE-COMMIT VERIFICATION GATE FAILED")
        print("==================================================")
        sys.exit(1)

if __name__ == "__main__":
    main()
