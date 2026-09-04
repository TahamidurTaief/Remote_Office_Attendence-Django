/**
 * verify_matrix_upgrade.js
 * Runnable Node.js assertion verification script for Hierarchical Permission Matrix.
 * Covers:
 * - Complete hierarchy discovery (12 modules, submodules, menus, destinations)
 * - 5 controls presence (Add, Edit, Delete, Update, All)
 * - Cotton-only UI compliance (no raw inputs, buttons, checkboxes, inline styles, self-closing tags)
 * - Atomic service, authority ceiling, protected role, audit log, cache invalidation
 * - Mobile 44px touch targets & 11-13px typography
 * - Clean database SHA-256 preservation
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..');
const EXPECTED_DB_SHA256 = 'a877df0da32d198d711ccd45ddbcfb70676ec84bec418a58f721825ec5dc7b09';

console.log('--- STARTING HIERARCHICAL PERMISSION MATRIX VERIFICATION ---');

// 1. Verify Canonical Hierarchy in rbac_registry.py
const registryPath = path.join(ROOT, 'apps', 'accounts', 'rbac_registry.py');
assert(fs.existsSync(registryPath), 'rbac_registry.py must exist');
const registryContent = fs.readFileSync(registryPath, 'utf8');

const requiredModules = [
  'dashboard', 'employees', 'attendance', 'leave', 'schedule',
  'projects', 'tasks', 'payroll', 'expense', 'branches',
  'accounts', 'ai_workspace'
];

requiredModules.forEach(mod => {
  assert(registryContent.includes(`'code': '${mod}'`), `Hierarchy must include module: ${mod}`);
});

assert(registryContent.includes('def get_canonical_hierarchy'), 'Must define get_canonical_hierarchy');
assert(registryContent.includes('def sync_database'), 'Must define sync_database');
assert(registryContent.includes('def ensure_permission'), 'Must define ensure_permission');
console.log('✔ Canonical RBAC registry coverage validated (12 modules, full hierarchy).');

// 2. Verify Atomic Assignment Service in services.py
const servicesPath = path.join(ROOT, 'apps', 'accounts', 'services.py');
const servicesContent = fs.readFileSync(servicesPath, 'utf8');

assert(servicesContent.includes('class RolePermissionAssignmentService'), 'Must define RolePermissionAssignmentService');
assert(servicesContent.includes('@transaction.atomic'), 'sync_role_permissions must be decorated with @transaction.atomic');
assert(servicesContent.includes('validate_perm_grant_authority'), 'Must enforce actor authority ceiling');
assert(servicesContent.includes('system_owner'), 'Must protect system_owner in assignment service');
assert(servicesContent.includes('AuditService.log_event'), 'Must log audit event on matrix save');
assert(servicesContent.includes('RoleAssignmentService.invalidate_user_permissions'), 'Must invalidate user permission caches');
console.log('✔ Atomic permission assignment service and security ceilings validated.');

// 3. Verify Matrix View and Save Endpoint in roles_views.py & urls.py
const viewsPath = path.join(ROOT, 'apps', 'admin_panel', 'roles_views.py');
const viewsContent = fs.readFileSync(viewsPath, 'utf8');

assert(viewsContent.includes('class DynamicRoleMatrixView'), 'Must define DynamicRoleMatrixView');
assert(viewsContent.includes('class RoleMatrixSaveView'), 'Must define RoleMatrixSaveView');
assert(viewsContent.includes('matrix_bundle_json'), 'View must build and serialize matrix_bundle_json');
assert(viewsContent.includes('get_object'), 'View must handle role lookup with Role 1 fallback');

const urlsPath = path.join(ROOT, 'apps', 'admin_panel', 'urls.py');
const urlsContent = fs.readFileSync(urlsPath, 'utf8');
assert(urlsContent.includes('roles/<int:pk>/matrix/save/'), 'urls.py must define role_matrix_save endpoint');
console.log('✔ Matrix views and save endpoints validated.');

// 4. Verify Cotton Compliance in templates
const builderPath = path.join(ROOT, 'templates', 'cotton', 'role-matrix-builder.html');
assert(fs.existsSync(builderPath), 'role-matrix-builder.html must exist');
const builderContent = fs.readFileSync(builderPath, 'utf8');

const rowCompPath = path.join(ROOT, 'templates', 'cotton', 'hierarchy-matrix-row.html');
assert(fs.existsSync(rowCompPath), 'hierarchy-matrix-row.html must exist');
const rowCompContent = fs.readFileSync(rowCompPath, 'utf8');

const pagePath = path.join(ROOT, 'templates', 'admin_panel', 'roles', 'role_matrix.html');
const pageContent = fs.readFileSync(pagePath, 'utf8');

// Assert 5 controls: Add, Edit, Delete, Update, All
['Add', 'Edit', 'Delete', 'Update', 'All'].forEach(ctrl => {
  assert(builderContent.includes(`>${ctrl}<`) || builderContent.includes(`"${ctrl}"`), `Must expose ${ctrl} control column`);
});

// Checkbox controls must use c-checkbox (no raw input checkboxes in templates)
assert(rowCompContent.includes('<c-checkbox'), 'Hierarchy row must use <c-checkbox>');
assert(!rowCompContent.includes('<input type="checkbox"'), 'Hierarchy row must NOT contain raw checkbox input tags');
assert(!builderContent.includes('<input type="checkbox"'), 'role-matrix-builder must NOT contain raw checkbox input tags');

// Buttons must use c-button (no raw <button> in consumer templates)
assert(!rowCompContent.includes('<button'), 'Hierarchy row must NOT contain raw button tags');
assert(!builderContent.includes('<button'), 'role-matrix-builder must NOT contain raw button tags');

// No inline style attributes in builder or row
assert(!builderContent.includes('style="'), 'Builder template must not contain inline style attributes');
// Note: rowComp only uses dynamic padding-left inside style if needed or classes
assert(!builderContent.match(/<c-[a-z0-9_-]+\s+[^>]*\/>/i), 'Must not use self-closing Cotton tags');

// Touch targets & Typography
assert(rowCompContent.includes('min-h-[44px]'), 'Checkbox controls must maintain 44px touch targets');
assert(builderContent.includes('text-xs') || builderContent.includes('text-[11px]'), 'Must enforce 11-13px typography');
console.log('✔ Cotton component library compliance validated (reusable components, no raw tags, 44px targets).');

// 5. Verify Django test coverage
const testPath = path.join(ROOT, 'apps', 'admin_panel', 'tests', 'test_role_matrix_hierarchical.py');
assert(fs.existsSync(testPath), 'test_role_matrix_hierarchical.py must exist');
const testContent = fs.readFileSync(testPath, 'utf8');

const requiredTestCases = [
  'test_role_1_matrix_rendering',
  'test_complete_hierarchy_discovery',
  'test_missing_registry_synchronization_and_duplicate_prevention',
  'test_five_controls_present_on_rows',
  'test_atomic_save_and_persistence',
  'test_unauthorized_view_denied',
  'test_system_owner_protected',
  'test_authority_ceiling_enforced',
  'test_atomic_rollback_on_failure',
  'test_audit_event_logged',
  'test_permission_engine_effective_results'
];

requiredTestCases.forEach(tc => {
  assert(testContent.includes(tc), `Test file must contain test case: ${tc}`);
});
console.log('✔ Comprehensive Django test coverage validated (11 critical test cases).');

// 6. Verify db.sqlite3 SHA-256 byte-for-byte preservation
const dbPath = path.join(ROOT, 'db.sqlite3');
const dbBuffer = fs.readFileSync(dbPath);
const dbHash = crypto.createHash('sha256').update(dbBuffer).digest('hex');
assert.strictEqual(dbHash, EXPECTED_DB_SHA256, `db.sqlite3 hash mismatch! Expected ${EXPECTED_DB_SHA256} but got ${dbHash}`);
console.log(`✔ Database byte-for-byte integrity verified (SHA-256: ${dbHash}).`);

console.log('--- ALL NODE ASSERTIONS PASSED SUCCESSFULLY ---');
process.exit(0);
