const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { execSync, spawnSync } = require('child_process');

console.log('==================================================');
console.log('   SYSTEM ROLES & ACCOUNT VERIFICATION SCRIPT     ');
console.log('==================================================\n');

const projectRoot = path.resolve(__dirname, '..');

// 1. Audit Template Files for Cotton Architecture & Compliance
const targetTemplates = [
  'templates/accounts/partials/change_password_form.html',
  'templates/accounts/change_password.html',
  'templates/staff/change_password.html',
  'templates/accounts/security_settings.html',
  'templates/admin_panel/roles/role_list.html',
  'templates/admin_panel/roles/role_form.html',
  'templates/admin_panel/roles/role_matrix.html',
  'templates/admin_panel/roles/role_members.html',
  'templates/admin_panel/roles/role_permissions.html',
  'templates/admin_panel/roles/user_permissions.html',
  'templates/admin_panel/roles/permission_matrix.html',
  'templates/accounts/mfa_setup.html',
  'templates/accounts/mfa_backup_codes.html'
];

console.log('--- 1. STATIC TEMPLATE AUDIT: COTTON COMPLIANCE ---');

targetTemplates.forEach((relPath) => {
  const fullPath = path.join(projectRoot, relPath);
  assert(fs.existsSync(fullPath), `Template missing: ${relPath}`);
  const content = fs.readFileSync(fullPath, 'utf8');

  // Assert: zero inline styles (style="...")
  const styleMatches = content.match(/\bstyle=["'][^"']*["']/gi);
  assert(
    !styleMatches,
    `Violation in ${relPath}: Found inline style(s): ${styleMatches ? styleMatches.slice(0, 3).join(', ') : ''}`
  );

  // Assert: zero raw buttons with ft-btn
  const rawButtonMatches = content.match(/<button[^>]*class=["'][^"']*\bft-btn\b[^"']*["']/gi);
  assert(
    !rawButtonMatches,
    `Violation in ${relPath}: Found raw button(s) with ft-btn: ${rawButtonMatches ? rawButtonMatches.slice(0, 3).join(', ') : ''}`
  );

  // Assert: zero self-closing cotton tags
  const selfClosingMatches = content.match(/<c-[a-zA-Z0-9_-]+[^>]*?\/>/g);
  const nonVars = (selfClosingMatches || []).filter(tag => !tag.startsWith('<c-vars'));
  assert(
    nonVars.length === 0,
    `Violation in ${relPath}: Found self-closing cotton tags: ${nonVars.join(', ')}`
  );

  console.log(`  ✓ ${relPath}: Pure Cotton compliance verified (zero inline styles, zero raw buttons, valid tags)`);
});

// 2. Functional Assertions via Django Test Suite
console.log('\n--- 2. FUNCTIONAL BEHAVIORAL VERIFICATION ---');
console.log('Executing test suite: apps.accounts.tests.test_password_and_roles...');

const res = spawnSync('uv', ['run', 'manage.py', 'test', 'apps.accounts.tests.test_password_and_roles', '--verbosity=1'], {
  cwd: projectRoot,
  encoding: 'utf8',
});
const combinedOutput = (res.stdout || '') + '\n' + (res.stderr || '');
console.log(combinedOutput);
assert(res.status === 0, `Django test suite exited with status ${res.status}`);
assert(combinedOutput.includes('OK'), 'Django test suite did not exit with OK status.');
console.log('  ✓ Password change, multi-role selection, security boundaries, and cache invalidation verified.');

// 3. Verify Database Integrity
console.log('\n--- 3. DATABASE INTEGRITY HASH VERIFICATION ---');
const crypto = require('crypto');
const dbPath = path.join(projectRoot, 'db.sqlite3');
const dbBuffer = fs.readFileSync(dbPath);
const dbHash = crypto.createHash('sha256').update(dbBuffer).digest('hex');
const expectedHash = 'a877df0da32d198d711ccd45ddbcfb70676ec84bec418a58f721825ec5dc7b09';

console.log(`Current DB Hash : ${dbHash}`);
console.log(`Expected DB Hash: ${expectedHash}`);
assert.strictEqual(dbHash, expectedHash, 'db.sqlite3 was modified and does not match initial byte-for-byte SHA256.');
console.log('  ✓ db.sqlite3 byte-for-byte SHA256 preserved identically.');

console.log('\n==================================================');
console.log(' ALL NODE VERIFICATION ASSERTIONS PASSED (100%)   ');
console.log('==================================================');
process.exit(0);
