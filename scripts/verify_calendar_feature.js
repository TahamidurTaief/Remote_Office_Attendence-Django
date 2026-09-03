const fs = require('fs');
const path = require('path');
const assert = require('assert');

// 1. Verify Calendar route template files exist
const monthTemplatePath = path.join(__dirname, '..', 'templates', 'schedule', 'calendar_month.html');
const partialTemplatePath = path.join(__dirname, '..', 'templates', 'schedule', 'partials', 'calendar_content.html');
assert.ok(fs.existsSync(monthTemplatePath), 'templates/schedule/calendar_month.html must exist');
assert.ok(fs.existsSync(partialTemplatePath), 'templates/schedule/partials/calendar_content.html must exist');

const monthContent = fs.readFileSync(monthTemplatePath, 'utf8');
const partialContent = fs.readFileSync(partialTemplatePath, 'utf8');
const combinedContent = monthContent + '\n' + partialContent;

// 2. Cotton Component Architecture Validation
assert.ok(combinedContent.includes('<c-calendar-toolbar'), 'Must use <c-calendar-toolbar>');
assert.ok(combinedContent.includes('<c-calendar-legend'), 'Must use <c-calendar-legend>');
assert.ok(combinedContent.includes('<c-calendar-event-chip'), 'Must use <c-calendar-event-chip>');
assert.ok(combinedContent.includes('<c-calendar-agenda-card'), 'Must use <c-calendar-agenda-card>');
assert.ok(combinedContent.includes('<c-modal'), 'Must use <c-modal>');
assert.ok(combinedContent.includes('<c-button'), 'Must use <c-button>');
assert.ok(combinedContent.includes('<c-empty-state'), 'Must use <c-empty-state>');

// 3. Ensure NO raw inputs, buttons, checkboxes, selects, or inline styles in route templates
assert.ok(!monthContent.includes('<button'), 'calendar_month.html must not contain raw <button> tags');
assert.ok(!monthContent.includes('<input'), 'calendar_month.html must not contain raw <input> tags');
assert.ok(!monthContent.includes('<select'), 'calendar_month.html must not contain raw <select> tags');
assert.ok(!monthContent.includes('style='), 'calendar_month.html must not contain inline styles');

assert.ok(!partialContent.includes('<button'), 'calendar_content.html must not contain raw <button> tags');
assert.ok(!partialContent.includes('<input'), 'calendar_content.html must not contain raw <input> tags');
assert.ok(!partialContent.includes('<select'), 'calendar_content.html must not contain raw <select> tags');
assert.ok(!partialContent.includes('style='), 'calendar_content.html must not contain inline styles');

// 4. Verify Sidebar & Menu Registry Renaming
const sidebarPath = path.join(__dirname, '..', 'templates', 'cotton', 'sidebar.html');
const sidebarContent = fs.readFileSync(sidebarPath, 'utf8');
assert.ok(sidebarContent.includes('label="Calendar"'), 'sidebar.html must label schedule route as "Calendar"');
assert.ok(!sidebarContent.includes('label="Shift Schedule"'), 'sidebar.html must NOT contain misleading "Shift Schedule"');

const menuRegPath = path.join(__dirname, '..', 'apps', 'audit', 'menu_registry.py');
const menuRegContent = fs.readFileSync(menuRegPath, 'utf8');
assert.ok(menuRegContent.includes('"label": "Calendar"'), 'menu_registry.py must label schedule route as "Calendar"');

// 5. Verify Backend Scoping and Holiday Logic
const viewsPath = path.join(__dirname, '..', 'apps', 'schedule', 'views.py');
const viewsContent = fs.readFileSync(viewsPath, 'utf8');
assert.ok(viewsContent.includes('Holiday.objects.filter'), 'views.py must query Holiday model');
assert.ok(viewsContent.includes('gov_holiday'), 'views.py must classify government holidays (branch=None)');
assert.ok(viewsContent.includes('office_holiday'), 'views.py must classify office holidays (branch bound)');
assert.ok(viewsContent.includes('allowed_roles = [\'admin\', \'system_owner\', \'manager\', \'staff\', \'employee\']'), 'views.py must support employee role');
assert.ok(viewsContent.includes('events_qs.none()'), 'views.py must safely return empty for unlinked profiles');

console.log('Calendar verification passed: zero raw form controls, strict cotton components, verified holiday scoping.');
