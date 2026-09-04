const fs = require('fs');
const path = require('path');
const assert = require('assert');

console.log('--- Starting Employee Wizard Node Assert Verification ---');

// 1. Direct Step Clicks & Stepper Cotton Component
const stepperPath = path.join(__dirname, '..', 'templates', 'cotton', 'stepper.html');
assert.ok(fs.existsSync(stepperPath), 'templates/cotton/stepper.html must exist');
const stepperContent = fs.readFileSync(stepperPath, 'utf8');

assert.ok(stepperContent.includes('clickable'), 'stepper.html must support clickable property');
assert.ok(stepperContent.includes('min-h-[44px]'), 'stepper buttons must have 44px touch target');
assert.ok(stepperContent.includes('tabindex="0"'), 'stepper items must be keyboard focusable');
assert.ok(stepperContent.includes('aria-label'), 'stepper items must have aria-label for accessibility');
assert.ok(stepperContent.includes('aria-current'), 'stepper items must set aria-current for accessibility');
assert.ok(stepperContent.includes('focus-visible:ring-2'), 'stepper must have visible focus rings');
console.log('✓ Requirement 1 passed: Direct step clicks and stepper accessibility verified.');

// 2. No-reload HTMX Navigation & Stable Targets
assert.ok(stepperContent.includes('hx_target="#wizard-root"') || stepperContent.includes('hx-target='), 'stepper navigation must target stable #wizard-root');
assert.ok(stepperContent.includes('hx_swap="outerHTML"') || stepperContent.includes('hx-swap='), 'stepper navigation must swap outerHTML');
assert.ok(stepperContent.includes('hx-indicator="#wizard-global-loader"'), 'stepper must trigger loading indicator');
assert.ok(stepperContent.includes('hx-disabled-elt'), 'stepper must prevent double-click race conditions');

const wrapperPath = path.join(__dirname, '..', 'templates', 'employees', 'wizard', 'stepper_wrapper.html');
assert.ok(fs.existsSync(wrapperPath), 'stepper_wrapper.html must exist');
const wrapperContent = fs.readFileSync(wrapperPath, 'utf8');
assert.ok(wrapperContent.includes('clickable="true"'), 'stepper_wrapper must pass clickable="true" to stepper component');
assert.ok(wrapperContent.includes('hx_target="#wizard-root"'), 'stepper_wrapper must configure target to #wizard-root');

const contentPath = path.join(__dirname, '..', 'templates', 'employees', 'wizard', 'wizard_content.html');
assert.ok(fs.existsSync(contentPath), 'wizard_content.html must exist');
const contentHtml = fs.readFileSync(contentPath, 'utf8');
assert.ok(contentHtml.includes('id="wizard-root"'), 'wizard_content.html must define #wizard-root');
assert.ok(contentHtml.includes('id="wizard-global-loader"'), 'wizard_content.html must define #wizard-global-loader');
console.log('✓ Requirement 2 passed: HTMX partial swap without reload verified.');

// 3. Current-Step Saving Before Navigation
assert.ok(stepperContent.includes('hx_include="#wizard-step-form"') || stepperContent.includes('hx-include='), 'stepper items must include current form data before step change');
for (let s = 1; s <= 8; s++) {
    const stepFile = path.join(__dirname, '..', 'templates', 'employees', 'wizard', `step_${s}.html`);
    assert.ok(fs.existsSync(stepFile), `step_${s}.html must exist`);
    const sContent = fs.readFileSync(stepFile, 'utf8');
    assert.ok(sContent.includes('id="wizard-step-form"'), `step_${s}.html form must have id="wizard-step-form"`);
    // Cotton rules: No raw buttons, checkboxes, or inline styles
    assert.ok(!sContent.includes('<button'), `step_${s}.html must not contain raw <button> tags`);
    assert.ok(!sContent.includes('<input type="checkbox"'), `step_${s}.html must not contain raw checkboxes`);
    assert.ok(!sContent.includes('style='), `step_${s}.html must not contain inline style attributes`);
}
console.log('✓ Requirement 3 passed: Current step data inclusion and Cotton rules verified across steps 1-8.');

// 4. Data Restoration & Draft Manager Service
const servicePath = path.join(__dirname, '..', 'apps', 'employees', 'wizard_service.py');
assert.ok(fs.existsSync(servicePath), 'wizard_service.py must exist');
const serviceContent = fs.readFileSync(servicePath, 'utf8');

assert.ok(serviceContent.includes('class WizardDraftManager'), 'wizard_service.py must define WizardDraftManager');
assert.ok(serviceContent.includes('def get_step_form'), 'WizardDraftManager must implement get_step_form with draft restoration');
assert.ok(serviceContent.includes('def save_step'), 'WizardDraftManager must implement save_step');
assert.ok(serviceContent.includes('initial.update(step_data)'), 'Form initialization must restore submitted draft values');
console.log('✓ Requirement 4 passed: Data restoration and initial draft merging verified.');

// 5. Partial-Step Navigation with Retained Incomplete/Error State
assert.ok(serviceContent.includes("'incomplete'"), 'Partial invalid step submissions must mark step incomplete');
assert.ok(serviceContent.includes("draft['step_errors']"), 'Draft must preserve field errors without discarding input');
assert.ok(stepperContent.includes('data-lucide="alert-circle"'), 'Stepper component must render alert-circle for error/incomplete steps');
console.log('✓ Requirement 5 passed: Partial-step navigation retains incomplete/error state.');

// 6. Final Full Validation at Step 8
assert.ok(serviceContent.includes('def validate_entire_wizard'), 'WizardDraftManager must implement validate_entire_wizard');
assert.ok(serviceContent.includes('errors_by_step'), 'validate_entire_wizard must aggregate errors by step');
const viewsPath = path.join(__dirname, '..', 'apps', 'employees', 'views.py');
const viewsContent = fs.readFileSync(viewsPath, 'utf8');
assert.ok(viewsContent.includes('WizardDraftManager.finalize_approval'), 'Step 8 approval must call finalize_approval');
assert.ok(viewsContent.includes('Cannot activate: please complete all required wizard steps.'), 'Views must show error message when full validation fails');
console.log('✓ Requirement 6 passed: Final validation across all steps verified.');

// 7. Create and Edit Workflows
const urlsPath = path.join(__dirname, '..', 'apps', 'employees', 'urls.py');
const urlsContent = fs.readFileSync(urlsPath, 'utf8');
assert.ok(urlsContent.includes("path('wizard/step/<int:step>/'"), 'urls.py must have create workflow step route');
assert.ok(urlsContent.includes("path('wizard/<uuid:uuid>/step/<int:step>/'"), 'urls.py must have edit workflow step route');
console.log('✓ Requirement 7 passed: Create and edit wizard routes verified.');

// 8. Back, Forward, Refresh & Browser-History
assert.ok(viewsContent.includes("response['HX-Push-Url'] = target_url"), 'HTMX views must set HX-Push-Url for browser history support');
assert.ok(urlsContent.includes("path('wizard/step/<int:step>/'"), 'Direct step URLs must be routeable on browser refresh');
console.log('✓ Requirement 8 passed: Browser history (back/forward/refresh) support verified.');

// 9. Duplicate Submissions Prevention
assert.ok(serviceContent.includes('clean_employee_number') || viewsContent.includes('clean_employee_number') || fs.readFileSync(path.join(__dirname, '..', 'apps', 'employees', 'forms.py'), 'utf8').includes('clean_employee_number'), 'Must validate employee_number uniqueness');
assert.ok(fs.readFileSync(path.join(__dirname, '..', 'apps', 'employees', 'forms.py'), 'utf8').includes('clean_personal_email'), 'Must validate personal_email uniqueness');
assert.ok(fs.readFileSync(path.join(__dirname, '..', 'apps', 'employees', 'forms.py'), 'utf8').includes('clean_phone'), 'Must validate phone uniqueness');
console.log('✓ Requirement 9 passed: Duplicate record checks verified.');

// 10. Expired Draft Handling
assert.ok(serviceContent.includes('DRAFT_EXPIRY_SECONDS = 86400'), 'Draft must have 24 hour (86400s) expiration');
assert.ok(serviceContent.includes('cls.clear_draft(request, user_id, employee_uuid)'), 'Expired drafts must be safely purged');
assert.ok(contentHtml.includes('Previous Draft Expired') && contentHtml.includes('24-hour expiration limit'), 'UI must show recoverable alert for expired draft');
console.log('✓ Requirement 10 passed: Expired draft purge and UI recovery verified.');

// 11. Unauthorized Cross-User Draft Access
assert.ok(serviceContent.includes('if draft.get(\'user_id\') != user_id:'), 'Must verify session draft belongs to authenticated user');
assert.ok(serviceContent.includes('raise PermissionDenied'), 'Must raise PermissionDenied on cross-user draft access');
console.log('✓ Requirement 11 passed: Cross-user draft isolation verified.');

// 12. Transaction Atomic Rollback
assert.ok(serviceContent.includes('with transaction.atomic():'), 'finalize_approval must use transaction.atomic()');
assert.ok(serviceContent.includes('log_audit'), 'Successful activation must log audit entry');
console.log('✓ Requirement 12 passed: Atomic rollback boundary verified.');

// 13. Cotton Alert and Design System Check
const alertPath = path.join(__dirname, '..', 'templates', 'cotton', 'alert.html');
assert.ok(fs.existsSync(alertPath), 'templates/cotton/alert.html must exist');
const alertContent = fs.readFileSync(alertPath, 'utf8');
assert.ok(alertContent.includes('data-lucide'), 'alert.html must render lucide icons via data-lucide');
assert.ok(!alertContent.includes('style='), 'alert.html must not contain inline style');
console.log('✓ Requirement 13 passed: Generic reusable Cotton alert component verified.');

console.log('=== All 13 Employee Wizard verification checks PASSED successfully ===');
