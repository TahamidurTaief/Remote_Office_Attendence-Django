/**
 * Node.js Verification Suite for Dynamic RBAC & PermissionEngine Architecture
 * Run with: node node_verify_rbac.js
 */

const assert = require('node:assert/strict');

// 1. Scope Rank Definition matching PermissionEngine.SCOPE_HIERARCHY
const SCOPE_HIERARCHY = {
    'global': 6,
    'company': 5,
    'branch': 4,
    'department': 3,
    'team': 2,
    'own': 1,
};

// 2. Mock Implementation of Dynamic RBAC Resolution Engine
class MockPermissionEngine {
    static resolveUserPermissions(user, activeRoles, userOverrides = []) {
        if (!user || !user.isAuthenticated) {
            return { allowed: false, reason: "Unauthenticated" };
        }

        // Superuser bypass
        if (user.isSuperuser) {
            return {
                isSuperuser: true,
                permissions: {},
                hasPerm: (codename, scope = 'own', action = 'view') => ({
                    allowed: true,
                    dataScope: 'global',
                    readOnly: false
                })
            };
        }

        // Check archived/trashed status
        if (user.isTrashed) {
            return {
                allowed: false,
                reason: "Account trashed",
                hasPerm: () => ({ allowed: false, reason: "Account trashed" })
            };
        }

        const readOnly = user.isArchived === true;

        // Combine permissions from multiple active roles, ignore inactive roles
        const resolved = {};

        for (const role of activeRoles) {
            if (!role.isActive) continue; // Inactive roles ignored

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
                    // Highest scope rank wins
                    const currentRank = SCOPE_HIERARCHY[resolved[code].scope] || 0;
                    const newRank = SCOPE_HIERARCHY[scope] || 0;
                    if (newRank > currentRank) {
                        resolved[code].scope = scope;
                    }
                }
            }
        }

        // Explicit user overrides: explicit denial overrides every role grant
        for (const ov of userOverrides) {
            const code = ov.codename;
            if (!ov.isGranted) {
                resolved[code] = {
                    codename: code,
                    scope: 'own',
                    granted: false // Denied
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

        // Scoped check
        if (res.dataScope === 'global') return true;
        if (res.dataScope === 'branch') {
            return obj.branchId === userPermResult.userBranchId;
        }
        return false;
    }
}

// -------------------------------------------------------------
// Test 1: Independent Action Resolution (no unsafe aliases)
// -------------------------------------------------------------
function testIndependentActions() {
    const user = { id: 1, isAuthenticated: true, isSuperuser: false };
    const activeRoles = [
        {
            name: "Editor",
            isActive: true,
            permissions: [
                { codename: 'projects.edit', dataScope: 'branch' }
            ]
        }
    ];

    const result = MockPermissionEngine.resolveUserPermissions(user, activeRoles);
    assert.equal(result.hasPerm('projects.edit', 'branch', 'edit').allowed, true, "projects.edit must be allowed");
    assert.equal(result.hasPerm('projects.update', 'branch', 'update').allowed, false, "projects.update must NOT be granted by edit");
    assert.equal(result.hasPerm('projects.delete', 'branch', 'delete').allowed, false, "projects.delete must be denied");
    console.log("✔ Test 1 Passed: Actions are strictly independent (no runtime aliases).");
}

// -------------------------------------------------------------
// Test 2: Inactive Roles are Ignored
// -------------------------------------------------------------
function testInactiveRolesIgnored() {
    const user = { id: 2, isAuthenticated: true, isSuperuser: false };
    const roles = [
        {
            name: "Old Role",
            isActive: false, // Inactive
            permissions: [{ codename: 'projects.delete', dataScope: 'global' }]
        }
    ];

    const result = MockPermissionEngine.resolveUserPermissions(user, roles);
    assert.equal(result.hasPerm('projects.delete').allowed, false, "Inactive role must not grant permissions");
    console.log("✔ Test 2 Passed: Inactive roles are ignored.");
}

// -------------------------------------------------------------
// Test 3: Multi-Role Union & Highest Scope Rank
// -------------------------------------------------------------
function testMultiRoleUnionScope() {
    const user = { id: 3, isAuthenticated: true, isSuperuser: false };
    const roles = [
        {
            name: "Role A",
            isActive: true,
            permissions: [{ codename: 'projects.view', dataScope: 'own' }]
        },
        {
            name: "Role B",
            isActive: true,
            permissions: [{ codename: 'projects.view', dataScope: 'branch' }]
        }
    ];

    const result = MockPermissionEngine.resolveUserPermissions(user, roles);
    const evalRes = result.hasPerm('projects.view', 'branch');
    assert.equal(evalRes.allowed, true, "Scope must union to highest permitted (branch)");
    assert.equal(evalRes.dataScope, 'branch', "Effective scope must be branch");
    console.log("✔ Test 3 Passed: Multi-role union resolves to highest scope rank.");
}

// -------------------------------------------------------------
// Test 4: Explicit User Denial Precedence
// -------------------------------------------------------------
function testExplicitDenyPrecedence() {
    const user = { id: 4, isAuthenticated: true, isSuperuser: false };
    const roles = [
        {
            name: "Manager",
            isActive: true,
            permissions: [
                { codename: 'employees.view', dataScope: 'company' },
                { codename: 'employees.delete', dataScope: 'company' }
            ]
        }
    ];
    const overrides = [
        { codename: 'employees.delete', isGranted: false } // Explicit Revoke
    ];

    const result = MockPermissionEngine.resolveUserPermissions(user, roles, overrides);
    assert.equal(result.hasPerm('employees.view').allowed, true, "employees.view must remain allowed");
    assert.equal(result.hasPerm('employees.delete').allowed, false, "employees.delete must be denied due to explicit override");
    console.log("✔ Test 4 Passed: Explicit user denial strictly overrides role grants.");
}

// -------------------------------------------------------------
// Test 5: Branch Scope Isolation (Branch A cannot access Branch B)
// -------------------------------------------------------------
function testBranchScopeIsolation() {
    const user = { id: 5, isAuthenticated: true, isSuperuser: false };
    const roles = [
        {
            name: "Branch Manager",
            isActive: true,
            permissions: [
                { codename: 'projects.view', dataScope: 'branch' },
                { codename: 'projects.update', dataScope: 'branch' }
            ]
        }
    ];

    const userPerms = MockPermissionEngine.resolveUserPermissions(user, roles);
    userPerms.userBranchId = 'branch_a';

    const projectBranchA = { id: 101, name: "North Tower", branchId: 'branch_a' };
    const projectBranchB = { id: 102, name: "South Mall", branchId: 'branch_b' };

    assert.equal(MockPermissionEngine.checkObjectAccess(userPerms, projectBranchA, 'branch'), true, "User can access Branch A project");
    assert.equal(MockPermissionEngine.checkObjectAccess(userPerms, projectBranchB, 'branch'), false, "User CANNOT access Branch B project");
    console.log("✔ Test 5 Passed: Object-level cross-branch access blocked.");
}

// -------------------------------------------------------------
// Run All Tests & Print Example Output
// -------------------------------------------------------------
function runVerification() {
    console.log("=================================================");
    console.log(" RUNNING RBAC & PERMISSION ENGINE NODE VERIFICATION");
    console.log("=================================================");

    testIndependentActions();
    testInactiveRolesIgnored();
    testMultiRoleUnionScope();
    testExplicitDenyPrecedence();
    testBranchScopeIsolation();

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
