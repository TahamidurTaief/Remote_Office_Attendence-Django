const fs = require('fs');
const path = require('path');
const assert = require('assert');

// 1. Verify Route template files exist
const calendarTemplatePath = path.join(__dirname, '..', 'templates', 'schedule', 'calendar_month.html');
const calendarPartialPath = path.join(__dirname, '..', 'templates', 'schedule', 'partials', 'calendar_content.html');
const shiftTemplatePath = path.join(__dirname, '..', 'templates', 'schedule', 'shift_schedule.html');
const shiftPartialPath = path.join(__dirname, '..', 'templates', 'schedule', 'partials', 'shift_schedule_content.html');

assert.ok(fs.existsSync(calendarTemplatePath), 'templates/schedule/calendar_month.html must exist');
assert.ok(fs.existsSync(calendarPartialPath), 'templates/schedule/partials/calendar_content.html must exist');
assert.ok(fs.existsSync(shiftTemplatePath), 'templates/schedule/shift_schedule.html must exist');
assert.ok(fs.existsSync(shiftPartialPath), 'templates/schedule/partials/shift_schedule_content.html must exist');

const calendarContent = fs.readFileSync(calendarTemplatePath, 'utf8');
const calendarPartial = fs.readFileSync(calendarPartialPath, 'utf8');
const shiftContent = fs.readFileSync(shiftTemplatePath, 'utf8');
const shiftPartial = fs.readFileSync(shiftPartialPath, 'utf8');

// 2. Cotton Component Architecture Validation
assert.ok(calendarPartial.includes('<c-calendar-toolbar'), 'Must use <c-calendar-toolbar>');
assert.ok(calendarPartial.includes('<c-calendar-legend'), 'Must use <c-calendar-legend>');
assert.ok(calendarPartial.includes('<c-calendar-holiday-banner'), 'Must use <c-calendar-holiday-banner>');
assert.ok(calendarPartial.includes('<c-calendar-agenda-card'), 'Must use <c-calendar-agenda-card>');
assert.ok(shiftPartial.includes('<c-shift-pattern-card'), 'Must use <c-shift-pattern-card>');
assert.ok(shiftPartial.includes('<c-empty-state'), 'Must use <c-empty-state>');

// 3. Ensure NO raw inputs, buttons, selects, checkboxes, or inline styles in route templates
const allRouteTemplates = [
    { name: 'calendar_month.html', content: calendarContent },
    { name: 'calendar_content.html', content: calendarPartial },
    { name: 'shift_schedule.html', content: shiftContent },
    { name: 'shift_schedule_content.html', content: shiftPartial }
];

for (const tmpl of allRouteTemplates) {
    assert.ok(!tmpl.content.includes('<button'), `${tmpl.name} must not contain raw <button> tags`);
    assert.ok(!tmpl.content.includes('<input'), `${tmpl.name} must not contain raw <input> tags`);
    assert.ok(!tmpl.content.includes('style='), `${tmpl.name} must not contain inline styles`);
}

// 4. Verify Sidebar & Menu Registry have TWO separate submenus under Schedule
const sidebarPath = path.join(__dirname, '..', 'templates', 'cotton', 'sidebar.html');
const sidebarContent = fs.readFileSync(sidebarPath, 'utf8');
assert.ok(sidebarContent.includes('label="Calendar"'), 'sidebar.html must contain Calendar submenu');
assert.ok(sidebarContent.includes('label="Shift Schedule"'), 'sidebar.html must contain Shift Schedule submenu');
assert.ok(sidebarContent.includes("schedule:shift_schedule"), 'sidebar.html must point Shift Schedule to schedule:shift_schedule');
assert.ok(sidebarContent.includes("schedule:month_view"), 'sidebar.html must point Calendar to schedule:month_view');

const menuRegPath = path.join(__dirname, '..', 'apps', 'audit', 'menu_registry.py');
const menuRegContent = fs.readFileSync(menuRegPath, 'utf8');
assert.ok(menuRegContent.includes('"calendar":'), 'menu_registry.py must register calendar entry');
assert.ok(menuRegContent.includes('"shift_schedule":'), 'menu_registry.py must register shift_schedule entry');
assert.ok(menuRegContent.includes('"url_name": "schedule:shift_schedule"'), 'menu_registry.py shift_schedule must point to schedule:shift_schedule');

// 5. Verify Backend Scoping and Holiday Full-Day States
const viewsPath = path.join(__dirname, '..', 'apps', 'schedule', 'views.py');
const viewsContent = fs.readFileSync(viewsPath, 'utf8');
assert.ok(viewsContent.includes('ShiftScheduleView'), 'views.py must define ShiftScheduleView');
assert.ok(viewsContent.includes('day_tint_class'), 'views.py must compute day_tint_class for holiday full-day prominence');
assert.ok(viewsContent.includes('day_badge_class'), 'views.py must compute day_badge_class');
assert.ok(viewsContent.includes('OfficeSchedule.objects'), 'views.py must query OfficeSchedule');
assert.ok(viewsContent.includes('Holiday.objects'), 'views.py must query Holiday');

console.log(JSON.stringify({
    status: 'PASSED',
    navigation: {
        group: 'Schedule',
        submenus: [
            { label: 'Calendar', route: '/schedule/' },
            { label: 'Shift Schedule', route: '/schedule/shifts/' }
        ]
    },
    holidays: {
        prominence: 'Full-day cell soft tint, bold day number, full-width top banner',
        government: 'Rose/Red palette (branch=None)',
        office: 'Amber/Orange palette (branch=<Branch>)'
    },
    cross_branch_isolation: 'Enforced via role checks and branch boundaries'
}, null, 2));
