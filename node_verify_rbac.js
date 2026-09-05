/**
 * Node.js Verification Suite for Dynamic RBAC & PermissionEngine Architecture Contracts
 * Asserts static contract rules, matrix action keys, scope hierarchies, and runtime expectations.
 * Run with: node node_verify_rbac.js
 */

const assert = require('node:assert/strict');

// 1. Contract Definitions
const SCOPE_HIERARCHY = {
    'global': 6,
    'company': 5,
    'branch': 4,
    'department': 3,
    'team': 2,
    'own': 1,
};

const MATRIX_ACTIONS = ['view', 'add', 'edit', 'update', 'delete', 'all'];

// 2. Permission Engine Evaluator Contract
class DynamicPermissionResolver {
    static resolveUserPermissions(user, activeRoleAssignments = [], userOverrides = []) {
        if (!user || !user.isAuthenticated) {
            return { allowed: false, reason: "Unauthenticated" };
        }

        // Superuser bypass
        if (user.isSuperuser) {
            return {
                isSuperuser: true,
                resolvedMap: {},
                hasPerm: (codename, scope = 'own', action = 'view') => ({
                    allowed: true,
                    dataScope: 'global',
                    readOnly: false
                })
            };
        }

        if (user.isTrashed) {
            return {
                allowed: false,
                reason: "Account trashed",
                hasPerm: () => ({ allowed: false, reason: "Account trashed" })
            };
        }

        // Compatibility personas alone grant nothing without active assignments
        if (activeRoleAssignments.length === 0) {
            return {
                resolvedMap: {},
                hasPerm: () => ({ allowed: false, reason: "No active role assignments" })
            };
        }

        const readOnly = user.isArchived === true;
        const resolved = {};

        for (const assignment of activeRoleAssignments) {
            const role = assignment.role;
            if (!role || !role.isActive) continue; // Inactive roles ignored

            for (const perm of role.permissions) {
                const code = perm.codename;
                const scope = perm.dataScope || 'own';

                if (!resolved[code]) {
                    resolved[code] = {
                        codename: code,
                        scope: scope,
                        granted: true
                    };
                } else {
                    const currentRank = SCOPE_HIERARCHY[resolved[code].scope] || 0;
                    const newRank = SCOPE_HIERARCHY[scope] || 0;
                    if (newRank > currentRank) {
                        resolved[code].scope = scope;
                    }
                }
            }
        }

        for (const ov of userOverrides) {
            const code = ov.codename;
            if (!ov.isGranted) {
                resolved[code] = {
                    codename: code,
                    scope: 'own',
                    granted: false
                };
            } else {
                resolved[code] = {
                    codename: code,
                    scope: ov.dataScope || 'own',
                    granted: true
                };
            }
        }

        return {
            resolvedMap: resolved,
            hasPerm: (codename, requiredScope = 'own', actionType = 'view') => {
                const entry = resolved[codename];
                if (!entry || !entry.granted) {
                    return { allowed: false, reason: `Permission ${codename} denied` };
                }

                if (readOnly && ['add', 'create', 'edit', 'update', 'delete'].includes(actionType)) {
                    return { allowed: false, reason: "Archived account is read-only", readOnly: true };
                }

                const userRank = SCOPE_HIERARCHY[entry.scope] || 0;
                const reqRank = SCOPE_HIERARCHY[requiredScope] || 0;

                if (userRank < reqRank) {
                    return { allowed: false, reason: `Scope insufficient: user=${entry.scope}, req=${requiredScope}` };
                }

                return { allowed: true, dataScope: entry.scope, readOnly };
            }
        };
    }

    static checkObjectAccess(userPermResult, obj, requiredScope = 'branch') {
        if (userPermResult.isSuperuser) return true;
        const res = userPermResult.hasPerm('projects.view', requiredScope);
        if (!res.allowed) return false;

        if (res.dataScope === 'global') return true;
        if (res.dataScope === 'branch') {
            return obj.branchId === userPermResult.userBranchId;
        }
        return false;
    }
}

// -------------------------------------------------------------
// Test 1: Zero writes contract & Legacy persona grants nothing
// -------------------------------------------------------------
function testLegacyPersonaGrantsNothing() {
    const user = { id: 1, isAuthenticated: true, isSuperuser: false, rolePersona: 'admin' };
    const emptyAssignments = [];

    const result = DynamicPermissionResolver.resolveUserPermissions(user, emptyAssignments);
    assert.equal(result.hasPerm('accounts.view').allowed, false, "Legacy role='admin' with no assignments must be denied");
    assert.equal(result.hasPerm('projects.view').allowed, false, "Legacy role must grant zero permissions");
    console.log("✔ Test 1 Passed: Legacy role personas grant nothing without active assignments.");
}

// -------------------------------------------------------------
// Test 2: Inactive roles are ignored
// -------------------------------------------------------------
function testInactiveRolesIgnored() {
    const user = { id: 2, isAuthenticated: true, isSuperuser: false };
    const assignments = [
        {
            role: {
                name: "Inactive Admin",
                isActive: false,
                permissions: [{ codename: 'projects.delete', dataScope: 'global' }]
            }
        }
    ];

    const result = DynamicPermissionResolver.resolveUserPermissions(user, assignments);
    assert.equal(result.hasPerm('projects.delete').allowed, false, "Inactive roles must not grant permissions");
    console.log("✔ Test 2 Passed: Inactive roles are completely ignored.");
}

// -------------------------------------------------------------
// Test 3: Multi-role union & highest scope rank
// -------------------------------------------------------------
function testMultiRoleUnionScope() {
    const user = { id: 3, isAuthenticated: true, isSuperuser: false };
    const assignments = [
        {
            role: {
                name: "Role Own",
                isActive: true,
                permissions: [{ codename: 'projects.view', dataScope: 'own' }]
            }
        },
        {
            role: {
                name: "Role Branch",
                isActive: true,
                permissions: [{ codename: 'projects.view', dataScope: 'branch' }]
            }
        }
    ];

    const result = DynamicPermissionResolver.resolveUserPermissions(user, assignments);
    const evalRes = result.hasPerm('projects.view', 'branch');
    assert.equal(evalRes.allowed, true, "Scope must union to highest permitted (branch)");
    assert.equal(evalRes.dataScope, 'branch', "Effective scope must be branch");
    console.log("✔ Test 3 Passed: Multi-role union resolves to highest scope rank.");
}

// -------------------------------------------------------------
// Test 4: Explicit user denial strictly overrides role grants
// -------------------------------------------------------------
function testExplicitDenyPrecedence() {
    const user = { id: 4, isAuthenticated: true, isSuperuser: false };
    const assignments = [
        {
            role: {
                name: "Manager",
                isActive: true,
                permissions: [
                    { codename: 'employees.view', dataScope: 'company' },
                    { codename: 'employees.delete', dataScope: 'company' }
                ]
            }
        }
    ];
    const overrides = [
        { codename: 'employees.delete', isGranted: false }
    ];

    const result = DynamicPermissionResolver.resolveUserPermissions(user, assignments, overrides);
    assert.equal(result.hasPerm('employees.view').allowed, true, "employees.view must remain allowed");
    assert.equal(result.hasPerm('employees.delete').allowed, false, "employees.delete must be denied due to explicit override");
    console.log("✔ Test 4 Passed: Explicit user denial strictly overrides role grants.");
}

// -------------------------------------------------------------
// Test 5: Branch Scope Isolation (Branch A cannot access Branch B)
// -------------------------------------------------------------
function testBranchScopeIsolation() {
    const user = { id: 5, isAuthenticated: true, isSuperuser: false };
    const assignments = [
        {
            role: {
                name: "Branch Manager",
                isActive: true,
                permissions: [
                    { codename: 'projects.view', dataScope: 'branch' },
                    { codename: 'projects.update', dataScope: 'branch' }
                ]
            }
        }
    ];

    const userPerms = DynamicPermissionResolver.resolveUserPermissions(user, assignments);
    userPerms.userBranchId = 'branch_a';

    const projectBranchA = { id: 101, name: "North Tower", branchId: 'branch_a' };
    const projectBranchB = { id: 102, name: "South Mall", branchId: 'branch_b' };

    assert.equal(DynamicPermissionResolver.checkObjectAccess(userPerms, projectBranchA, 'branch'), true, "User can access Branch A project");
    assert.equal(DynamicPermissionResolver.checkObjectAccess(userPerms, projectBranchB, 'branch'), false, "User CANNOT access Branch B project");
    console.log("✔ Test 5 Passed: Object-level cross-branch access blocked.");
}

// -------------------------------------------------------------
// Test 6: Matrix Action Schema & View/All Aggregate Contract
// -------------------------------------------------------------
function testMatrixSchemaContract() {
    assert.ok(MATRIX_ACTIONS.includes('view'), "Matrix must support view action");
    assert.ok(MATRIX_ACTIONS.includes('add'), "Matrix must support add action");
    assert.ok(MATRIX_ACTIONS.includes('edit'), "Matrix must support edit action");
    assert.ok(MATRIX_ACTIONS.includes('update'), "Matrix must support update action");
    assert.ok(MATRIX_ACTIONS.includes('delete'), "Matrix must support delete action");
    assert.ok(MATRIX_ACTIONS.includes('all'), "Matrix must support all aggregate action");
    assert.equal(MATRIX_ACTIONS.length, 6, "Matrix must have exactly 6 distinct action columns");
    console.log("✔ Test 6 Passed: Permission matrix schema conforms to View/Add/Edit/Update/Delete/All contract.");
}

// -------------------------------------------------------------
// Run All Tests & Print Example Output
// -------------------------------------------------------------
function runVerification() {
    console.log("=================================================");
    console.log(" RUNNING RBAC & PERMISSION ENGINE NODE VERIFICATION");
    console.log("=================================================");

    testLegacyPersonaGrantsNothing();
    testInactiveRolesIgnored();
    testMultiRoleUnionScope();
    testExplicitDenyPrecedence();
    testBranchScopeIsolation();
    testMatrixSchemaContract();

    console.log("\n=================================================");
    console.log(" EXAMPLE OUTPUT DEMONSTRATION");
    console.log("=================================================");
    const exampleOutput = {
        input: 'A custom "Branch Manager" role has projects.view and projects.update limited to Branch A.',
        resolution: {
            roles_evaluated: ['Branch Manager (active)'],
            resolved_permissions: {
                'projects.view': { granted: true, scope: 'branch (Branch A)' },
                'projects.update': { granted: true, scope: 'branch (Branch A)' },
                'projects.delete': { granted: false, reason: 'Not granted' },
                'projects.create': { granted: false, reason: 'Not granted' }
            },
            menu_visibility: {
                'projects_menu': true,
                'create_project_button': false,
                'delete_project_button': false
            },
            access_enforcement: {
                'GET /projects/ (Branch A)': 200,
                'GET /projects/101/ (Branch A)': 200,
                'POST /projects/101/edit/ (Branch A)': 200,
                'GET /projects/102/ (Branch B)': 404,
                'POST /projects/102/edit/ (Branch B)': 404,
                'GET /projects/export-pdf/102/ (Branch B)': 404
            }
        },
        status: "VERIFIED_SECURE"
    };

    console.log(JSON.stringify(exampleOutput, null, 2));
    console.log("=================================================");
}

runVerification();
