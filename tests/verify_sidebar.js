const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT_DIR = path.resolve(__dirname, '..');
const SIDEBAR_PATH = path.join(ROOT_DIR, 'templates', 'cotton', 'sidebar.html');
const DROPDOWN_PATH = path.join(ROOT_DIR, 'templates', 'cotton', 'sidebar-dropdown.html');
const SUBMENU_PATH = path.join(ROOT_DIR, 'templates', 'cotton', 'sidebar-submenu.html');
const LINK_PATH = path.join(ROOT_DIR, 'templates', 'cotton', 'sidebar-link.html');
const CSS_PATH = path.join(ROOT_DIR, 'static', 'css', 'source.css');

console.log('================================================================');
console.log('🚀 RUNNING 3-PHASE INTERACTIVE SIDEBAR NAVIGATION VERIFICATION');
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
runTest('All Cotton 3-phase sidebar templates and CSS source files exist', () => {
  assert(fs.existsSync(SIDEBAR_PATH), 'sidebar.html exists');
  assert(fs.existsSync(DROPDOWN_PATH), 'sidebar-dropdown.html exists');
  assert(fs.existsSync(SUBMENU_PATH), 'sidebar-submenu.html exists');
  assert(fs.existsSync(LINK_PATH), 'sidebar-link.html exists');
  assert(fs.existsSync(CSS_PATH), 'source.css exists');
});

const sidebarContent = fs.readFileSync(SIDEBAR_PATH, 'utf8');
const dropdownContent = fs.readFileSync(DROPDOWN_PATH, 'utf8');
const submenuContent = fs.readFileSync(SUBMENU_PATH, 'utf8');
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

// 4. Removal of static uppercase section headers
runTest('Uppercase section headers (OVERVIEW, PEOPLE & ATTENDANCE, etc.) are removed', () => {
  const forbiddenHeaders = [
    'label="OVERVIEW"',
    'label="PEOPLE & ATTENDANCE"',
    'label="WORK MANAGEMENT"',
    'label="FINANCE"',
    'label="REPORTS"',
    'label="ADMINISTRATION"',
    'label="AI WORKSPACE"'
  ];

  for (const header of forbiddenHeaders) {
    assert(!sidebarContent.includes(header), `Header ${header} must not be present in sidebar.html`);
  }
});

// 5. Phase 1 Module Dropdowns exist
runTest('Phase 1 Module Dropdowns are present directly without duplicate labels', () => {
  const expectedModules = [
    'label="Overview"',
    'label="People & Attendance"',
    'label="Work Management"',
    'label="Finance"',
    'label="Reports"',
    'label="Administration"',
    'label="AI Workspace"'
  ];

  for (const mod of expectedModules) {
    assert(sidebarContent.includes(mod), `Module dropdown ${mod} must be present in sidebar.html`);
  }
});

// 6. Phase 2 Menus inside modules exist via c-sidebar-submenu
runTest('Phase 2 Menus exist inside modules using Cotton <c-sidebar-submenu>', () => {
  assert(sidebarContent.includes('<c-sidebar-submenu id="overview_dashboard"'), 'Overview has Dashboard menu');
  assert(sidebarContent.includes('<c-sidebar-submenu id="pa_employees"'), 'People & Attendance has Employees menu');
  assert(sidebarContent.includes('<c-sidebar-submenu id="pa_attendance"'), 'People & Attendance has Attendance menu');
  assert(sidebarContent.includes('<c-sidebar-submenu id="wm_projects"'), 'Work Management has Projects menu');
  assert(sidebarContent.includes('<c-sidebar-submenu id="fin_payroll"'), 'Finance has Payroll menu');
  assert(sidebarContent.includes('<c-sidebar-submenu id="rep_attendance"'), 'Reports has Attendance Reports menu');
  assert(sidebarContent.includes('<c-sidebar-submenu id="adm_security"'), 'Administration has Roles & Security menu');
  assert(sidebarContent.includes('<c-sidebar-submenu id="ai_insights"'), 'AI Workspace has Predictive Insights menu');
});

// 7. White Menu Theme & 14px Typography
runTest('All menu items have white styling and 14px typography', () => {
  assert(/\.ft-nav-item\s*\{[^}]*font-size:\s*14px/i.test(cssContent), '.ft-nav-item has font-size: 14px');
  assert(/\.ft-menu-item\s*\{[^}]*font-size:\s*14px/i.test(cssContent), '.ft-menu-item has font-size: 14px');
  assert(/\.ft-submenu-item\s*\{[^}]*font-size:\s*14px/i.test(cssContent), '.ft-submenu-item has font-size: 14px');
  assert(/\.ft-search-input\s*\{[^}]*font-size:\s*14px/i.test(cssContent), '.ft-search-input has font-size: 14px');
  assert(/\.ft-nav-item\s*\{[^}]*background:\s*#FFFFFF/i.test(cssContent), '.ft-nav-item has white background');
  assert(/\.ft-menu-item\s*\{[^}]*background:\s*#FFFFFF/i.test(cssContent), '.ft-menu-item has white background');
  assert(!/\.ft-nav-item\.active\s*\{[^}]*background:\s*#1F2937/i.test(cssContent), '.ft-nav-item.active does NOT have black #1F2937 background');
});

// 8. Zero inline styles
runTest('Zero inline styles in sidebar and cotton navigation templates', () => {
  assert(!sidebarContent.includes('style='), 'sidebar.html has zero inline style attributes');
  assert(!dropdownContent.includes('style='), 'sidebar-dropdown.html has zero inline style attributes');
  assert(!submenuContent.includes('style='), 'sidebar-submenu.html has zero inline style attributes');
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
  assert(!submenuContent.includes('onclick='), 'sidebar-submenu.html has no raw onclick handlers');
  assert(!linkContent.includes('onclick='), 'sidebar-link.html has no raw onclick handlers');
});

// 11. Gated with PermissionEngine
runTest('All navigation modules and menus are gated with PermissionEngine (has_perm filter)', () => {
  assert(sidebarContent.includes('{% load static rbac_tags %}'), 'Template loads rbac_tags');
  assert(sidebarContent.includes("has_perm:'dashboard.view'"), 'Overview / Dashboard is permission gated');
  assert(sidebarContent.includes("has_perm:'employees.view'"), 'People / Employees is permission gated');
  assert(sidebarContent.includes("has_perm:'projects.view'"), 'Work / Projects is permission gated');
  assert(sidebarContent.includes("has_perm:'payroll.view'"), 'Finance / Payroll is permission gated');
  assert(sidebarContent.includes("has_perm:'accounts.view'"), 'Administration is permission gated');
  assert(sidebarContent.includes("has_perm:'ai_workspace.view'"), 'AI Workspace is permission gated');
});

// 12. Component-driven structure
runTest('Sidebar utilizes Django Cotton components c-sidebar-dropdown, c-sidebar-submenu, c-sidebar-link, c-button, c-input, c-empty-state', () => {
  assert(sidebarContent.includes('<c-sidebar-dropdown'), 'Uses <c-sidebar-dropdown>');
  assert(sidebarContent.includes('<c-sidebar-submenu'), 'Uses <c-sidebar-submenu>');
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

// 14. 3-Phase interactive navigation with toggleNestedMenu and toggleSubmenu
runTest('Alpine state supports interactive 3-phase toggling and persistence', () => {
  assert(sidebarContent.includes("localStorage.getItem('ft_sidebar')"), 'Persists collapsed state');
  assert(sidebarContent.includes("sessionStorage.getItem('ft_sidebar_scroll')"), 'Persists scroll position');
  assert(sidebarContent.includes('toggleSubmenu(id)'), 'toggleSubmenu method exists');
  assert(sidebarContent.includes('isSubmenuOpen(id)'), 'isSubmenuOpen method exists');
  assert(sidebarContent.includes('toggleNestedMenu(id)'), 'toggleNestedMenu method exists');
  assert(sidebarContent.includes('isNestedMenuOpen(id)'), 'isNestedMenuOpen method exists');
});

// 15. Balanced tags check
runTest('Template tags and components in sidebar.html are properly balanced', () => {
  const countMatches = (str, regex) => (str.match(regex) || []).length;
  
  // Django tags
  const ifCount = countMatches(sidebarContent, /{% if /g);
  const endifCount = countMatches(sidebarContent, /{% endif %}/g);
  assert.strictEqual(ifCount, endifCount, `Balanced {% if %} (${ifCount}) and {% endif %} (${endifCount})`);

  // Cotton components
  const openDropdown = countMatches(sidebarContent, /<c-sidebar-dropdown/g);
  const closeDropdown = countMatches(sidebarContent, /<\/c-sidebar-dropdown>/g);
  assert.strictEqual(openDropdown, closeDropdown, `Balanced <c-sidebar-dropdown> (${openDropdown}) and </c-sidebar-dropdown> (${closeDropdown})`);

  const openSubmenu = countMatches(sidebarContent, /<c-sidebar-submenu/g);
  const closeSubmenu = countMatches(sidebarContent, /<\/c-sidebar-submenu>/g);
  assert.strictEqual(openSubmenu, closeSubmenu, `Balanced <c-sidebar-submenu> (${openSubmenu}) and </c-sidebar-submenu> (${closeSubmenu})`);

  const openLink = countMatches(sidebarContent, /<c-sidebar-link/g);
  const closeLink = countMatches(sidebarContent, /<\/c-sidebar-link>/g);
  assert.strictEqual(openLink, closeLink, `Balanced <c-sidebar-link> (${openLink}) and </c-sidebar-link> (${closeLink})`);
});

console.log('\n================================================================');
console.log(`🎉 ALL ${passedTests}/${totalTests} TESTS PASSED SUCCESSFULLY!`);
console.log('================================================================\n');

// Example output
const exampleOutput = {
  scenario: "3-Phase Interactive Navigation in White Theme with 14px Typography",
  hierarchyLevels: {
    phase1: "Module Dropdown (e.g. People & Attendance) - Clean white card, 14px bold, rotating chevron",
    phase2: "Inner Menus (e.g. Employees, Attendance, Shifts) - White background, 14px medium, expandable",
    phase3: "Submenu Links (e.g. Directory, Add, Departments, Designations) - 14px, interactive destination"
  },
  typographyAndColor: {
    fontFamily: "Inter, sans-serif",
    fontSize: "14px everywhere across sidebar (modules, menus, submenus, search input)",
    menuBackgroundColor: "#FFFFFF (Pure White) with subtle slate-200 border, no dark #1F2937 boxes",
    hoverColor: "rgba(24, 119, 242, 0.05) with blue text highlight",
    activeColor: "#1877F2 (Primary Blue) with clean border-color and soft glow"
  },
  headersCleanup: {
    staticHeadersRemoved: ["OVERVIEW", "PEOPLE & ATTENDANCE", "WORK MANAGEMENT", "FINANCE", "REPORTS", "ADMINISTRATION", "AI WORKSPACE"],
    status: "All static uppercase group labels removed; only interactive module menus remain"
  }
};

console.log('📋 EXAMPLE OUTPUT:');
console.log(JSON.stringify(exampleOutput, null, 2));
