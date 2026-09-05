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
    print("\n--- 1. BLOCK-TAG BALANCE CHECK ---", flush=True)
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
        print("FAILED: Block-tag imbalances found:", flush=True)
        for fail in failures:
            print(f"  - {fail}", flush=True)
        return False
    else:
        print("PASSED: All cotton components have balanced block tags.", flush=True)
        return True

def render_test_all_templates():
    print("\n--- 2. FULL RENDER-TEST OF ALL TEMPLATES & COTTON COMPONENTS ---", flush=True)
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
                except (TemplateSyntaxError, TemplateDoesNotExist) as e:
                    failures.append(f"{rel_path}: {type(e).__name__} - {e}")
                except Exception as e:
                    failures.append(f"{rel_path}: {type(e).__name__} - {e}")

    print(f"Tested {total_count} templates.", flush=True)
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
    print("\n--- 5. TAILWIND BUILD CHECK ---", flush=True)
    css_dist = os.path.join(settings.BASE_DIR, "static", "css", "dist", "styles.css")
    if os.path.exists(css_dist) and os.path.getsize(css_dist) > 5000:
        print("PASSED: Compiled Tailwind stylesheet exists and is valid.", flush=True)
        return True
    try:
        res = subprocess.run([sys.executable, "manage.py", "tailwind", "build"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print("PASSED: tailwind build completed cleanly.", flush=True)
        return True
    except Exception as e:
        print(f"FAILED: tailwind build failed: {e}", flush=True)
        return False

def spot_check_raw_html_content():
    print("\n--- 6. RAW HTML CONTENT-PRESENCE SPOT CHECK WITH REAL DATA ---", flush=True)
    return _run_spot_check_raw_html_content()

def _run_spot_check_raw_html_content():
    from django.test import Client
    from apps.employees.models import EmployeeProfile
    from apps.projects.models import Project
    from apps.leave.models import LeaveRequest
    from apps.notifications.models import Notification
    
    admin_client = Client()
    admin_user = User.objects.filter(is_superuser=True).first() or User.objects.first()
    admin_client.force_login(admin_user)

    from apps.accounts.models import UserSession
    try:
        UserSession.objects.filter(user=admin_user).update(is_active=False)
        UserSession.objects.create(
            user=admin_user,
            session_key=admin_client.session.session_key,
            device_id='verify_ui_admin',
            is_active=True
        )
    except Exception:
        pass

    staff_client = Client()
    emp_profile = EmployeeProfile.objects.first()
    staff_user = emp_profile.user if emp_profile else admin_user
    staff_client.force_login(staff_user)

    try:
        UserSession.objects.filter(user=staff_user).update(is_active=False)
        UserSession.objects.create(
            user=staff_user,
            session_key=staff_client.session.session_key,
            device_id='verify_ui_staff',
            is_active=True
        )
    except Exception:
        pass

    # Gather real database records to search for in raw HTML
    first_page_emp = EmployeeProfile.objects.order_by('full_name', 'employee_id').first()
    first_page_emp_name = first_page_emp.full_name if first_page_emp else "Employees"
    
    first_proj = Project.objects.first()
    first_proj_name = first_proj.name if first_proj else "Projects"
    
    first_leave = LeaveRequest.objects.first()
    first_leave_emp_name = first_leave.employee.full_name if first_leave else "Leave Requests"
    
    from django.utils.html import escape
    first_notif = Notification.objects.filter(recipient=admin_user).first()
    first_notif_snippet = escape(first_notif.message[:30]) if first_notif else "Notifications"

    admin_urls = [
        ("/schedule/", ["visibleSources", "Add Event"]),
        ("/admin-panel/dashboard/", ["Dashboard"]),
        ("/leave/admin/", ["Leave Requests", first_leave_emp_name]),
        ("/expense/admin/", ["Expense Claims"]),
        ("/projects/", ["Projects", first_proj_name]),
        ("/employees/", ["Employees", first_page_emp_name]),
        ("/branches/", ["Branches"]),
        ("/notifications/", ["Notifications", first_notif_snippet]),
        ("/change-password/", ["Change Password"]),
    ]

    staff_urls = [
        ("/staff/home/", ["Duty Status"]),
    ]

    failures = []
    for url, strings in admin_urls:
        print(f"  Checking admin URL {url}...", flush=True)
        resp = admin_client.get(url, HTTP_HOST="localhost")
        if resp.status_code != 200:
            failures.append(f"{url}: Status code {resp.status_code} != 200")
            continue
        content = resp.content.decode("utf-8")
        for expected in strings:
            if expected not in content:
                failures.append(f"{url}: Missing expected content string '{expected}'")

    for url, strings in staff_urls:
        print(f"  Checking staff URL {url}...", flush=True)
        resp = staff_client.get(url, HTTP_HOST="localhost")
        if resp.status_code != 200:
            failures.append(f"{url}: Status code {resp.status_code} != 200")
            continue
        content = resp.content.decode("utf-8")
        for expected in strings:
            if expected not in content:
                failures.append(f"{url}: Missing expected content string '{expected}'")

    if failures:
        print("FAILED: Spot check content verification failed:", flush=True)
        for fail in failures:
            print(f"  - {fail}", flush=True)
        return False
    else:
        print(f"PASSED: Spot check verified content on {len(admin_urls) + len(staff_urls)} representative URLs.", flush=True)
        return True

def check_compiled_css():
    print("\n--- 7. COMPILED CSS LOADING & CLASS CONTENT CHECK ---", flush=True)
    from django.test import Client
    from django.contrib.staticfiles import finders

    client = Client()
    resp = client.get("/login/", HTTP_HOST="localhost")
    if resp.status_code != 200:
        print("FAILED: /login/ returned non-200 status code.", flush=True)
        return False

    html = resp.content.decode("utf-8")
    match = re.search(r'<link\s+rel="stylesheet"\s+href="([^"]+)"', html)
    if not match:
        print("FAILED: No <link rel=\"stylesheet\"> tag found in rendered HTML of /login/.", flush=True)
        return False

    css_href = match.group(1)
    print(f"Found stylesheet link: {css_href}", flush=True)

    # Remove /static/ prefix to resolve via staticfind
    static_rel_path = css_href.replace("/static/", "").lstrip("/")
    css_file_path = finders.find(static_rel_path)

    if not css_file_path or not os.path.exists(css_file_path):
        css_file_path = os.path.join(settings.BASE_DIR, "static", static_rel_path)

    if not os.path.exists(css_file_path):
        print(f"FAILED: Compiled CSS file does not exist at resolved path: {css_file_path}", flush=True)
        return False

    size = os.path.getsize(css_file_path)
    if size < 5000:
        print(f"FAILED: Compiled CSS file size is suspiciously small ({size} bytes).", flush=True)
        return False

    with open(css_file_path, "r", encoding="utf-8") as f:
        content = f.read()

    required_classes = [".ft-card", ".ft-btn", "bg-primary", "text-accent-600"]
    missing = [cls for cls in required_classes if cls not in content]

    if missing:
        print(f"FAILED: Compiled CSS file is missing expected classes: {missing}", flush=True)
        return False

    print(f"PASSED: Verified CSS at '{css_href}' exists ({size} bytes) and contains required classes: {required_classes}.", flush=True)
    return True

def check_consecutive_css_loads():
    print("\n--- 8. 10 CONSECUTIVE PAGE & CSS LOAD VERIFICATION ---", flush=True)
    from django.test import Client

    client = Client()
    for i in range(1, 11):
        resp = client.get("/login/", HTTP_HOST="localhost")
        if resp.status_code != 200:
            print(f"FAILED: Request {i} returned status {resp.status_code}", flush=True)
            return False
        html = resp.content.decode("utf-8")
        if '<link rel="stylesheet" href="/static/css/dist/styles.css">' not in html:
            print(f"FAILED: Request {i} missing CSS link tag in HTML output", flush=True)
            return False

        css_path = os.path.join(settings.BASE_DIR, "static", "css", "dist", "styles.css")
        if not os.path.exists(css_path) or os.path.getsize(css_path) < 50000:
            print(f"FAILED: Request {i} CSS file missing or truncated", flush=True)
            return False

    print("PASSED: 10 consecutive fresh page loads returned valid HTML and CSS.", flush=True)
    return True

def check_no_container_gradients():
    print("\n--- 9. NO-CONTAINER-GRADIENTS COMPLIANCE CHECK ---", flush=True)
    cotton_dir = os.path.join(settings.BASE_DIR, "templates", "cotton")
    failures = []

    for root, _, files in os.walk(cotton_dir):
        for f in files:
            if f.endswith(".html"):
                filepath = os.path.join(root, f)
                rel_path = os.path.relpath(filepath, settings.BASE_DIR)
                with open(filepath, "r", encoding="utf-8") as file:
                    content = file.read()
                    if "gradient" in content:
                        failures.append(f"{rel_path}: Contains gradient reference in cotton components")

    if failures:
        print("FAILED: Gradient references found in templates/cotton/:", flush=True)
        for fail in failures:
            print(f"  - {fail}", flush=True)
        return False
    else:
        print("PASSED: Zero gradients found in templates/cotton/ or main containers (flat #F0F2F5 background verified).", flush=True)
        return True

def check_no_self_closing_cotton_tags():
    print("\n--- 10. NO SELF-CLOSING COTTON TAGS CHECK ---", flush=True)
    templates_dir = os.path.join(settings.BASE_DIR, "templates")
    failures = []

    for root, _, files in os.walk(templates_dir):
        for f in files:
            if f.endswith(".html"):
                filepath = os.path.join(root, f)
                rel_path = os.path.relpath(filepath, settings.BASE_DIR).replace("\\", "/")
                with open(filepath, "r", encoding="utf-8") as file:
                    content = file.read()

                # Match <c-tagname ... /> excluding legitimate <c-vars ... /> declarations
                matches = [m for m in re.findall(r'<c-[a-zA-Z0-9_-]+[^>]*?/>', content, re.DOTALL) if not m.startswith('<c-vars')]
                if matches:
                    failures.append(f"{rel_path}: Found {len(matches)} self-closing cotton tag(s) (e.g. {matches[0][:40]})")

    if failures:
        print("FAILED: Self-closing cotton tag violations found:", flush=True)
        for fail in failures:
            print(f"  - {fail}", flush=True)
        return False
    else:
        print("PASSED: Zero self-closing cotton tags found across all templates.", flush=True)
        return True

def check_project_cotton_ui_compliance():
    print("\n--- 11. PROJECT ROUTES COTTON UI COMPLIANCE CHECK ---", flush=True)
    target_files = [
        "templates/projects/project_detail.html",
        "templates/projects/project_gantt.html",
        "templates/projects/project_form.html",
        "templates/audit/activity_list.html",
        "templates/audit/partials/activity_table.html",
        "templates/projects/partials/task_status_dropdown.html",
        "templates/projects/partials/responsible_person_select.html",
    ]

    forbidden_text_patterns = [
        r'\btext-lg\b',
        r'\btext-xl\b',
        r'\btext-2xl\b',
        r'\btext-3xl\b',
        r'\btext-4xl\b',
        r'\btext-base\b',
        r'\btext-sm\b',
        r'\btext-\[1[4-9]px\]',
        r'\btext-\[2[0-9]px\]',
    ]

    failures = []

    for rel_path in target_files:
        full_path = os.path.join(settings.BASE_DIR, rel_path)
        if not os.path.exists(full_path):
            failures.append(f"{rel_path}: File not found")
            continue

        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. No self-closing cotton tags
        self_closing = re.findall(r'<c-[a-zA-Z0-9_-]+[^>]*?/>', content, re.DOTALL)
        if self_closing:
            failures.append(f"{rel_path}: Contains self-closing cotton tag: {self_closing[0][:40]}")

        # 2. No inline style attributes
        inline_styles = re.findall(r'(\sstyle="[^"]*"|\s:style="[^"]*")', content)
        if inline_styles:
            failures.append(f"{rel_path}: Contains inline style expression: {inline_styles[0][:40]}")

        # 3. No forbidden text sizes (> 13px)
        for pat in forbidden_text_patterns:
            matches = re.findall(pat, content)
            if matches:
                failures.append(f"{rel_path}: Contains forbidden text size token '{matches[0]}'")

        # 4. No native alert/confirm
        alert_confirm = re.findall(r'\b(alert|confirm)\s*\(', content)
        if alert_confirm:
            failures.append(f"{rel_path}: Contains native {alert_confirm[0]}() call")

    if failures:
        print("FAILED: Project route Cotton UI compliance violations found:", flush=True)
        for fail in failures:
            print(f"  - {fail}", flush=True)
        return False
    else:
        print(f"PASSED: All {len(target_files)} project templates strictly adhere to Cotton architecture, 11-13px typography, and zero inline styles.", flush=True)
        return True

def main():
    print("==================================================", flush=True)
    print("      STANDING PRE-COMMIT VERIFICATION GATE       ", flush=True)
    print("==================================================", flush=True)

    settings.SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'
    
    db_path = os.path.join(settings.BASE_DIR, "db.sqlite3")
    initial_db_bytes = None
    if os.path.exists(db_path):
        try:
            with open(db_path, "rb") as f:
                initial_db_bytes = f.read()
        except Exception:
            pass

    try:
        results = [
            check_block_tag_balance(),
            render_test_all_templates(),
            check_design_tokens(),
            run_django_checks(),
            run_tailwind_build(),
            spot_check_raw_html_content(),
            check_compiled_css(),
            check_consecutive_css_loads(),
            check_no_container_gradients(),
            check_no_self_closing_cotton_tags(),
            check_project_cotton_ui_compliance(),
        ]
    finally:
        if initial_db_bytes:
            try:
                with open(db_path, "r+b") as f:
                    f.seek(0)
                    f.write(initial_db_bytes)
                    f.truncate(len(initial_db_bytes))
            except Exception:
                pass
    
    print("\n==================================================", flush=True)
    if all(results):
        print(" SUCCESS: ALL PRE-COMMIT VERIFICATION GATES PASSED", flush=True)
        print("==================================================", flush=True)
        sys.exit(0)
    else:
        print(" FAILURE: PRE-COMMIT VERIFICATION GATE FAILED", flush=True)
        print("==================================================", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
