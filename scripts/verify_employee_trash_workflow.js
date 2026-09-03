const fs = require('fs');
const path = require('path');
const assert = require('assert');

// 1. Verify Cotton Confirmation Modal contract & attributes
const modalPath = path.join(__dirname, '..', 'templates', 'cotton', 'modal.html');
assert.ok(fs.existsSync(modalPath), 'templates/cotton/modal.html must exist');
const modalContent = fs.readFileSync(modalPath, 'utf8');

assert.ok(!modalContent.includes('pb-24'), 'templates/cotton/modal.html must not contain pb-24');
assert.ok(modalContent.includes('trapFocus'), 'templates/cotton/modal.html must implement focus trapping');
assert.ok(modalContent.includes('@keydown.escape.window'), 'templates/cotton/modal.html must handle escape key to dismiss');
assert.ok(modalContent.includes('items-end sm:items-center'), 'templates/cotton/modal.html must be a bottom sheet on mobile and centered on desktop');
assert.ok(modalContent.includes('min-h-[44px]'), 'templates/cotton/modal.html must have minimum 44px touch targets for mobile accessibility');

// 2. Verify Employee Confirmation Partial Form Contract
const confModalPath = path.join(__dirname, '..', 'templates', 'employees', 'partials', 'confirmation_modal.html');
assert.ok(fs.existsSync(confModalPath), 'confirmation_modal.html must exist');
const confContent = fs.readFileSync(confModalPath, 'utf8');

assert.ok(confContent.includes('hx-post='), 'confirmation form must use hx-post');
assert.ok(confContent.includes('submitting'), 'confirmation form must track submitting state to prevent double submit');
assert.ok(confContent.includes('x-bind:disabled="submitting"'), 'submit button must be disabled when submitting via x-bind:disabled');
assert.ok(confContent.includes('min-h-[44px]'), 'action buttons must meet 44px touch target requirement');
assert.ok(confContent.includes('<c-button'), 'confirmation modal must use <c-button> components');
assert.ok(confContent.includes('<c-checkbox'), 'confirmation modal must use <c-checkbox> component');
assert.ok(confContent.includes('<c-slot name="footer">'), 'confirmation modal must use <c-slot name="footer"> for sticky actions');
assert.ok(confContent.includes('form="employee-confirm-form"'), 'submit button must link to form using form="employee-confirm-form"');

// Ensure NO raw buttons, checkboxes, or inline styles
assert.ok(!confContent.includes('<button'), 'confirmation_modal.html must not contain raw <button> tags');
assert.ok(!confContent.includes('<input type="checkbox"'), 'confirmation_modal.html must not contain raw <input type="checkbox"> tags');
assert.ok(!confContent.includes('style='), 'confirmation_modal.html must not contain inline style attributes');

// 3. Verify Master Table row ID contract
const tablePath = path.join(__dirname, '..', 'templates', 'employees', 'partials', 'master_table.html');
assert.ok(fs.existsSync(tablePath), 'master_table.html must exist');
const tableContent = fs.readFileSync(tablePath, 'utf8');
assert.ok(tableContent.includes('id="employee-row-{{ emp.pk }}"'), 'table rows must have id="employee-row-{{ emp.pk }}" for targeted OOB deletion');

// 4. Verify views.py EmployeeMasterDeleteView logic
const viewsPath = path.join(__dirname, '..', 'apps', 'employees', 'views.py');
const viewsContent = fs.readFileSync(viewsPath, 'utf8');
assert.ok(viewsContent.includes('class EmployeeMasterDeleteView'), 'EmployeeMasterDeleteView must exist');
assert.ok(viewsContent.includes('_has_delete_permission'), 'Must check delete permission via RBAC');
assert.ok(viewsContent.includes('hx-swap-oob="delete"'), 'Must return OOB row deletion');
assert.ok(viewsContent.includes('close-modal'), 'Must dispatch close-modal trigger');
assert.ok(viewsContent.includes('show-toast'), 'Must dispatch show-toast trigger');

// 5. Verify services.py TrashService soft_delete repair and profile metadata
const servicesPath = path.join(__dirname, '..', 'apps', 'audit', 'services.py');
const servicesContent = fs.readFileSync(servicesPath, 'utf8');
assert.ok(servicesContent.includes('"profile_is_active": profile_prev_active'), 'Must store profile_is_active in metadata');
assert.ok(servicesContent.includes('needs_repair = not getattr(obj, "is_trashed", False)'), 'Must detect inconsistent trash entry and repair');
assert.ok(servicesContent.includes('obj.status = "archived"'), 'Must set status to archived');

console.log('All real template and contract assertions in scripts/verify_employee_trash_workflow.js passed successfully.');
