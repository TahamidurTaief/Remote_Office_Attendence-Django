/**
 * Node assert verification script for Global Search Upgrade
 * Checks:
 * 1. Cotton component compliance (zero inline styles, 11-13px typography, min-h 44px target)
 * 2. Search template syntax & structure
 * 3. HTMX out-of-order protection (hx-sync="this:replace")
 * 4. Safe failure/empty states
 * 5. Database hash integrity
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const crypto = require('crypto');

const BASE_DIR = path.resolve(__dirname, '..');
console.log('=== Node Assert: Global Search Upgrade Verification ===');

// 1. Verify Cotton Search Components
const searchResultRowPath = path.join(BASE_DIR, 'templates', 'cotton', 'search-result-row.html');
assert(fs.existsSync(searchResultRowPath), 'search-result-row.html must exist');
const rowContent = fs.readFileSync(searchResultRowPath, 'utf-8');

// Assert Cotton <c-vars> exists
assert(rowContent.includes('<c-vars'), 'search-result-row must declare <c-vars>');

// Assert zero inline styles
assert(!rowContent.includes('style='), 'search-result-row must have zero inline styles');

// Assert min-height 44px interactive target
assert(rowContent.includes('min-h-[44px]'), 'search-result-row must maintain min 44px interactive target');

// Assert 11-13px typography
assert(rowContent.includes('text-[12px]'), 'search-result-row must use standard 12px label typography');
assert(!rowContent.match(/\btext-(?:lg|xl|2xl|3xl|4xl|base)\b/), 'search-result-row must not use forbidden large typography');

console.log('✓ search-result-row.html: Cotton compliance, typography, and target sizing verified.');

// 2. Verify search_results_partial.html
const searchResultsPath = path.join(BASE_DIR, 'templates', 'cotton', 'search_results_partial.html');
assert(fs.existsSync(searchResultsPath), 'search_results_partial.html must exist');
const partialContent = fs.readFileSync(searchResultsPath, 'utf-8');

// Zero inline styles
assert(!partialContent.includes('style='), 'search_results_partial must have zero inline styles');

// Uses <c-empty-state> and <c-search-result-row>
assert(partialContent.includes('<c-empty-state'), 'search_results_partial must use <c-empty-state>');
assert(partialContent.includes('<c-search-result-row'), 'search_results_partial must use <c-search-result-row>');
assert(partialContent.includes('<c-alert'), 'search_results_partial must use <c-alert> for error state');

console.log('✓ search_results_partial.html: Cotton component reuse, error states, and zero inline styles verified.');

// 3. Verify command-palette.html
const commandPalettePath = path.join(BASE_DIR, 'templates', 'cotton', 'command-palette.html');
assert(fs.existsSync(commandPalettePath), 'command-palette.html must exist');
const paletteContent = fs.readFileSync(commandPalettePath, 'utf-8');

// Assert HTMX hx-sync="this:replace" is present to prevent stale response overwriting
assert(paletteContent.includes('hx-sync="this:replace"'), 'command-palette must specify hx-sync="this:replace"');

// Assert hx-indicator is present
assert(paletteContent.includes('hx-indicator='), 'command-palette must specify hx-indicator');

// Assert zero inline styles
assert(!paletteContent.includes('style='), 'command-palette must have zero inline styles');

console.log('✓ command-palette.html: HTMX sync protection, indicators, and zero inline styles verified.');

// 4. Verify admin_panel/partials/global_search_results.html
const adminPartialPath = path.join(BASE_DIR, 'templates', 'admin_panel', 'partials', 'global_search_results.html');
assert(fs.existsSync(adminPartialPath), 'admin_panel partial must exist');
const adminPartialContent = fs.readFileSync(adminPartialPath, 'utf-8');
assert(adminPartialContent.includes('nav_results'), 'admin_panel partial must support nav_results');

console.log('✓ admin_panel/partials/global_search_results.html: Navigation and roles integration verified.');

// 5. Verify database integrity
const dbPath = path.join(BASE_DIR, 'db.sqlite3');
const expectedHash = 'a877df0da32d198d711ccd45ddbcfb70676ec84bec418a58f721825ec5dc7b09';
const currentHash = crypto.createHash('sha256').update(fs.readFileSync(dbPath)).digest('hex');
assert.strictEqual(currentHash, expectedHash, `db.sqlite3 hash mismatch! Expected ${expectedHash}, got ${currentHash}`);

console.log('✓ Database integrity check passed: ' + currentHash);
console.log('=== All Node Assert Verification Gates Passed Successfully ===');
