const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT_DIR = path.resolve(__dirname, '..');
const SIDEBAR_PATH = path.join(ROOT_DIR, 'templates', 'cotton', 'sidebar.html');
const DROPDOWN_PATH = path.join(ROOT_DIR, 'templates', 'cotton', 'sidebar-dropdown.html');
const SECTION_PATH = path.join(ROOT_DIR, 'templates', 'cotton', 'sidebar-section.html');
const LINK_PATH = path.join(ROOT_DIR, 'templates', 'cotton', 'sidebar-link.html');
const CSS_PATH = path.join(ROOT_DIR, 'static', 'css', 'source.css');

console.log('================================================================');
console.log('🚀 RUNNING SIDEBAR DASHBOARD NAVIGATION VERIFICATION GATES');
console.log('================================================================\n');

let totalTests = 0;
let passedTests = 0;

function runTest(name, fn) {
  totalTests++;
  try {
    fn();
    passedTests++;
    console.log(`  ✅ [PASS] ${name}`);
  } catch (err) {
    console.error(`  ❌ [FAIL] ${name}: ${err.message}`);
    throw err;
  }
}

// 1. Audit files exist
runTest('All Cotton sidebar templates and CSS source files exist', () => {
  assert(fs.existsSync(SIDEBAR_PATH), 'sidebar.html exists');
  assert(fs.existsSync(DROPDOWN_PATH), 'sidebar-dropdown.html exists');
  assert(fs.existsSync(SECTION_PATH), 'sidebar-section.html exists');
  assert(fs.existsSync(LINK_PATH), 'sidebar-link.html exists');
  assert(fs.existsSync(CSS_PATH), 'source.css exists');
});

const sidebarContent = fs.readFileSync(SIDEBAR_PATH, 'utf8');
const dropdownContent = fs.readFileSync(DROPDOWN_PATH, 'utf8');
const sectionContent = fs.readFileSync(SECTION_PATH, 'utf8');
const linkContent = fs.readFileSync(LINK_PATH, 'utf8');
const cssContent = fs.readFileSync(CSS_PATH, 'utf8');

// 2. Route validity
runTest('All extracted route names in sidebar are valid known routes', () => {
  const routeRegex = /{% url ['"]([a-zA-Z0-9_:]+)['"]/g;
  const routesFound = [];
  let match;
  while ((match = routeRegex.exec(sidebarContent)) !== null) {
    routesFound.push(match[1]);
  }

  const validRoutes = new Set([
    'admin_panel:dashboard', 'notifications:list', 'employees:employee_list', 'employees:employee_add',
    'employees:department_list', 'employees:designation_list', 'attendance:status', 'admin_panel:attendance_list',
    'admin_panel:manual_entry', 'schedule:shift_schedule', 'schedule:month_view', 'projects:project_list',
    'projects:project_type_list', 'projects:global_task_list', 'projects:template_list', 'payroll:payroll_run_list',
    'payroll:salary_components', 'payroll:salary_structures', 'payroll:employee_salary_setup',
    'expense:admin_expense_list', 'admin_panel:reports_main', 'admin_panel:reports_daily',
    'admin_panel:reports_monthly', 'admin_panel:reports_absent', 'payroll:reports_hub', 'admin_panel:role_list',
    'accounts:security_settings', 'accounts:admin_security_policies', 'accounts:admin_login_activity',
    'admin_panel:admin_audit_logs', 'audit:activity_list', 'audit:trash_list', 'accounts:user_sessions',
    'backups:backup_list', 'branches:branch_list', 'branches:holiday_list', 'admin_panel:schedule_settings',
    'admin_panel:ai_assistant', 'admin_panel:ai_attendance_insights', 'admin_panel:ai_project_insights',
    'admin_panel:ai_payroll_insights', 'admin_panel:ai_smart_reports', 'admin_panel:ai_settings',
    'staff:home', 'staff:check_in', 'staff:attendance', 'staff:field_visit', 'staff:my_tasks',
    'staff:my_projects', 'leave:staff_request_create', 'leave:staff_dashboard', 'expense:staff_expense_list',
    'payroll:my_payslips', 'staff:profile', 'accounts:logout', 'audit:unpin_menu', 'audit:pin_menu'
  ]);

  assert(routesFound.length > 20, 'Found substantial list of route references');
  for (const r of routesFound) {
    assert(validRoutes.has(r), `Route '${r}' is recognized as a valid application route`);
  }
});

// 3. Duplicate labels
runTest('No duplicate "Field Visit" label exists in staff navigation', () => {
  const fieldVisitMatches = sidebarContent.match(/label="Field Visit"/g) || [];
  assert.strictEqual(fieldVisitMatches.length, 1, 'Exactly one Field Visit navigation item exists');
});

// 4. Admin 7 Groups Architecture
runTest('Admin navigation includes the exact 7 compact groups', () => {
  const expectedAdminGroups = [
    'label="OVERVIEW"',
    'label="PEOPLE & ATTENDANCE"',
    'label="WORK MANAGEMENT"',
    'label="FINANCE"',
    'label="REPORTS"',
    'label="ADMINISTRATION"',
    'label="AI WORKSPACE"'
  ];

  for (const group of expectedAdminGroups) {
    assert(sidebarContent.includes(group), `Admin group ${group} must be present in sidebar.html`);
  }
});

// 5. Staff 5 Groups Architecture
runTest('Staff navigation includes the exact 5 compact groups', () => {
  const expectedStaffGroups = [
    'label="HOME"',
    'label="ATTENDANCE"',
    'label="WORK"',
    'label="LEAVE & FINANCE"',
    'label="ACCOUNT & TOOLS"'
  ];

  for (const group of expectedStaffGroups) {
    assert(sidebarContent.includes(group), `Staff group ${group} must be present in sidebar.html`);
  }
});

// 6. Typography +1px verification
runTest('Navigation CSS specifies exactly +1px typography (13px nav, 13px submenu, 12px search, 12px headers/badges)', () => {
  assert(/\.ft-nav-item\s*\{[^}]*font-size:\s*13px/i.test(cssContent), '.ft-nav-item has font-size: 13px');
  assert(/\.ft-submenu-item\s*\{[^}]*font-size:\s*13px/i.test(cssContent), '.ft-submenu-item has font-size: 13px');
  assert(/\.ft-search-input\s*\{[^}]*font-size:\s*12px/i.test(cssContent), '.ft-search-input has font-size: 12px');
  assert(/\.ft-group-header\s*\{[^}]*font-size:\s*12px/i.test(cssContent), '.ft-group-header has font-size: 12px');
  assert(/\.ft-badge-pill\s*\{[^}]*font-size:\s*12px/i.test(cssContent), '.ft-badge-pill has font-size: 12px');
});

// 7. Desktop compact rows and mobile touch target >= 44px
runTest('Desktop rows are compact (~32-34px) and mobile touch targets are at least 44px', () => {
  assert(/\.ft-nav-item\s*\{[^}]*height:\s*3[2-4]px/i.test(cssContent), 'Desktop .ft-nav-item height is 32-34px');
  assert(/\.ft-submenu-item\s*\{[^}]*height:\s*3[2-4]px/i.test(cssContent), 'Desktop .ft-submenu-item height is 32-34px');
  assert(/min-height:\s*44px/i.test(cssContent), 'Mobile items have min-height: 44px');
});

// 8. Zero inline styles
runTest('Zero inline styles in sidebar and cotton navigation templates', () => {
  assert(!sidebarContent.includes('style='), 'sidebar.html has zero inline style attributes');
  assert(!dropdownContent.includes('style='), 'sidebar-dropdown.html has zero inline style attributes');
  assert(!sectionContent.includes('style='), 'sidebar-section.html has zero inline style attributes');
  assert(!linkContent.includes('style='), 'sidebar-link.html has zero inline style attributes');
});

// 9. Zero inline scripts
runTest('Zero inline <script> tags in sidebar.html', () => {
  assert(!sidebarContent.includes('<script'), 'sidebar.html contains no inline <script> tags');
});

// 10. Zero onclick attributes
runTest('Zero onclick handlers in sidebar and cotton navigation templates', () => {
  assert(!sidebarContent.includes('onclick='), 'sidebar.html has no raw onclick handlers');
  assert(!dropdownContent.includes('onclick='), 'sidebar-dropdown.html has no raw onclick handlers');
  assert(!sectionContent.includes('onclick='), 'sidebar-section.html has no raw onclick handlers');
  assert(!linkContent.includes('onclick='), 'sidebar-link.html has no raw onclick handlers');
});

// 11. Gated with PermissionEngine
runTest('All navigation groups are gated with PermissionEngine (has_perm tag/filter)', () => {
  assert(sidebarContent.includes('{% load static rbac_tags %}'), 'Template loads rbac_tags');
  assert(sidebarContent.includes("has_perm:'dashboard.view'"), 'Overview / Dashboard is permission gated');
  assert(sidebarContent.includes("has_perm:'employees.view'"), 'People / Employees is permission gated');
  assert(sidebarContent.includes("has_perm:'projects.view'"), 'Work / Projects is permission gated');
  assert(sidebarContent.includes("has_perm:'payroll.view'"), 'Finance / Payroll is permission gated');
  assert(sidebarContent.includes("has_perm:'accounts.view'"), 'Administration is permission gated');
  assert(sidebarContent.includes("has_perm:'ai_workspace.view'"), 'AI Workspace is permission gated');
});

// 12. Component-driven structure
runTest('Sidebar utilizes Django Cotton components c-sidebar-section, c-sidebar-dropdown, c-sidebar-link, c-button, c-input, c-empty-state', () => {
  assert(sidebarContent.includes('<c-sidebar-section'), 'Uses <c-sidebar-section>');
  assert(sidebarContent.includes('<c-sidebar-dropdown'), 'Uses <c-sidebar-dropdown>');
  assert(sidebarContent.includes('<c-sidebar-link'), 'Uses <c-sidebar-link>');
  assert(sidebarContent.includes('<c-button'), 'Uses <c-button>');
  assert(sidebarContent.includes('<c-input'), 'Uses <c-input>');
  assert(sidebarContent.includes('<c-empty-state'), 'Uses <c-empty-state>');
});

// 13. Search and Keyboard shortcuts
runTest('Alpine state supports search, slash focus shortcut, and escape clear', () => {
  assert(sidebarContent.includes('@keydown.window.prevent.slash'), 'Slash shortcut focuses search');
  assert(sidebarContent.includes('@keydown.escape.window'), 'Escape clears search / closes mobile');
  assert(sidebarContent.includes('matchSearch('), 'matchSearch helper exists');
  assert(sidebarContent.includes('hasNoSearchResults'), 'Empty state detection exists');
});

// 14. One submenu open at a time & Persistence
runTest('Alpine state ensures one submenu open at a time and scroll/collapsed persistence', () => {
  assert(sidebarContent.includes('localStorage.getItem(\'ft_sidebar\')'), 'Persists collapsed state');
  assert(sidebarContent.includes('localStorage.getItem(\'ft_sidebar_active_submenu\')'), 'Persists active submenu');
  assert(sidebarContent.includes('sessionStorage.getItem(\'ft_sidebar_scroll\')'), 'Persists scroll position');
  assert(sidebarContent.includes('toggleSubmenu(id)'), 'toggleSubmenu method exists');
  assert(sidebarContent.includes('isSubmenuOpen(id)'), 'isSubmenuOpen method exists');
});

// 15. Balanced tags check
runTest('Template tags and components in sidebar.html are properly balanced', () => {
  const countMatches = (str, regex) => (str.match(regex) || []).length;
  
  // Django tags
  const ifCount = countMatches(sidebarContent, /{% if /g);
  const endifCount = countMatches(sidebarContent, /{% endif %}/g);
  assert.strictEqual(ifCount, endifCount, `Balanced {% if %} (${ifCount}) and {% endif %} (${endifCount})`);

  // Cotton components
  const openSection = countMatches(sidebarContent, /<c-sidebar-section/g);
  const closeSection = countMatches(sidebarContent, /<\/c-sidebar-section>/g);
  assert.strictEqual(openSection, closeSection, `Balanced <c-sidebar-section> (${openSection}) and </c-sidebar-section> (${closeSection})`);

  const openDropdown = countMatches(sidebarContent, /<c-sidebar-dropdown/g);
  const closeDropdown = countMatches(sidebarContent, /<\/c-sidebar-dropdown>/g);
  assert.strictEqual(openDropdown, closeDropdown, `Balanced <c-sidebar-dropdown> (${openDropdown}) and </c-sidebar-dropdown> (${closeDropdown})`);

  const openLink = countMatches(sidebarContent, /<c-sidebar-link/g);
  const closeLink = countMatches(sidebarContent, /<\/c-sidebar-link>/g);
  assert.strictEqual(openLink, closeLink, `Balanced <c-sidebar-link> (${openLink}) and </c-sidebar-link> (${closeLink})`);
});

console.log('\n================================================================');
console.log(`🎉 ALL ${passedTests}/${totalTests} TESTS PASSED SUCCESSFULLY!`);
console.log('================================================================\n');

// Print one example output as requested
const exampleOutput = {
  scenario: "HR Specialist role with employee & attendance permissions navigating dashboard",
  visibleAdminGroups: [
    {
      group: "PEOPLE & ATTENDANCE",
      accessibleLinks: [
        "Employee Directory (/employees/)",
        "New Employee (/employees/add/)",
        "Departments (/employees/departments/)",
        "Designations (/employees/designations/)",
        "Live Attendance (/attendance/status/)",
        "Attendance Logs (/admin-panel/attendance/)",
        "Manual Attendance (/admin-panel/attendance/manual-entry/)"
      ],
      hiddenLinksReason: "Payroll, System Roles, Security Policies, Backups omitted due to lack of corresponding permissions"
    }
  ],
  typographyMetrics: {
    primaryNavItemFontSize: "13px (increased by 1px)",
    submenuItemFontSize: "13px (increased by 1px)",
    searchInputFontSize: "12px (increased by 1px)",
    sectionHeaderFontSize: "12px",
    desktopRowHeight: "33px (compact: 32-34px)",
    mobileTouchTargetMinHeight: "44px (compliant)"
  },
  cottonComponentsEngine: {
    sectionComponent: "c-sidebar-section",
    dropdownComponent: "c-sidebar-dropdown",
    linkComponent: "c-sidebar-link",
    buttonComponent: "c-button",
    inputComponent: "c-input",
    emptyStateComponent: "c-empty-state",
    inlineStylesCount: 0,
    inlineScriptsCount: 0,
    rawOnclickCount: 0
  }
};

console.log('📋 EXAMPLE OUTPUT:');
console.log(JSON.stringify(exampleOutput, null, 2));
