/**
 * Dynamic RBAC Administration & Privilege Boundary Test Suite
 * 
 * Tests:
 * 1. Role name & code normalization and collision handling (casing/spacing)
 * 2. Role code immutability after user assignments or permissions exist
 * 3. System Owner protection (UI immutability, deletion/deactivation prevention)
 * 4. Super Admin privilege boundary (is_superuser=false, superuser-only management)
 * 5. Scope capping (prevent granting scope higher than actor's effective scope)
 * 6. Permission boundary (prevent granting permissions not held by actor)
 * 7. Administrative lockout prevention on role deactivation
 * 8. Audited soft deactivation preserving history and user assignments
 */

const assert = require('node:assert');

// --- In-Memory Simulated State & Logic (Mirroring Django RBAC Engine & Forms) ---

const SCOPE_HIERARCHY = {
  'own': 1,
  'team': 2,
  'department': 3,
  'branch': 4,
  'company': 5,
  'global': 6
};

function normalizeRoleCode(text) {
  if (!text) return '';
  return String(text)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');
}

class AuditService {
  constructor() {
    this.events = [];
  }

  logEvent({ actor, action, target, summary, before = null, after = null }) {
    const event = {
      id: this.events.length + 1,
      actor: actor ? actor.email : 'System',
      action,
      target: target ? target.code || target.email || String(target) : '',
      summary,
      before,
      after,
      timestamp: new Date().toISOString()
    };
    this.events.push(event);
    return event;
  }
}

class RBACAdministrationService {
  constructor(auditService) {
    this.audit = auditService;
    this.roles = new Map();
    this.rolePermissions = []; // { roleId, permCode, dataScope }
    this.userAssignments = []; // { userId, roleId }
    this.nextRoleId = 1;

    // Bootstrap initial System Owner role
    this._bootstrapSystemOwner();
  }

  _bootstrapSystemOwner() {
    const role = {
      id: this.nextRoleId++,
      name: 'System Owner',
      code: 'system_owner',
      description: 'Bootstrap superuser role',
      is_system_protected: true,
      is_active: true
    };
    this.roles.set(role.id, role);
  }

  createRole(actor, { name, code, description = '', is_active = true }) {
    const trimmedName = String(name || '').trim();
    if (!trimmedName) {
      throw new Error("Role name is required.");
    }

    const normalizedCode = code ? normalizeRoleCode(code) : normalizeRoleCode(trimmedName);
    if (!normalizedCode) {
      throw new Error("A valid alphanumeric role code could not be determined.");
    }

    // Boundary check: system_owner cannot be created via UI
    if (normalizedCode === 'system_owner') {
      this.audit.logEvent({
        actor,
        action: 'unauthorized_system_owner_create_attempt',
        summary: 'Attempted creation of system_owner role'
      });
      throw new Error("The 'system_owner' role is system-protected and cannot be created via UI.");
    }

    // Boundary check: super_admin can only be created by superuser
    if (normalizedCode === 'super_admin') {
      if (!actor || !actor.is_superuser) {
        this.audit.logEvent({
          actor,
          action: 'unauthorized_super_admin_create_attempt',
          summary: 'Non-superuser attempted to create super_admin role'
        });
        throw new Error("Only a System Owner (Django superuser) can create or configure the 'super_admin' role.");
      }
    }

    // Edge Case 1: Collision check across casing/spacing
    for (const r of this.roles.values()) {
      if (r.name.toLowerCase() === trimmedName.toLowerCase()) {
        throw new Error("A role with this name already exists.");
      }
      if (r.code.toLowerCase() === normalizedCode) {
        throw new Error(`A role with code '${normalizedCode}' already exists.`);
      }
    }

    const role = {
      id: this.nextRoleId++,
      name: trimmedName,
      code: normalizedCode,
      description,
      is_system_protected: false,
      is_active: Boolean(is_active)
    };

    this.roles.set(role.id, role);

    this.audit.logEvent({
      actor,
      action: 'role_created',
      target: role,
      summary: `Created dynamic role '${role.name}' (${role.code})`,
      after: { name: role.name, code: role.code, is_active: role.is_active }
    });

    return role;
  }

  updateRole(actor, roleId, { name, code, description, is_active }) {
    const role = this.roles.get(roleId);
    if (!role) throw new Error("Role not found.");

    if (role.is_system_protected || role.code === 'system_owner') {
      this.audit.logEvent({
        actor,
        action: 'unauthorized_system_owner_edit_attempt',
        target: role,
        summary: 'Attempted edit on protected system_owner'
      });
      throw new Error("Protected System Owner role cannot be modified via UI.");
    }

    if (role.code === 'super_admin' && (!actor || !actor.is_superuser)) {
      this.audit.logEvent({
        actor,
        action: 'unauthorized_super_admin_edit_attempt',
        target: role,
        summary: 'Non-superuser attempted edit on super_admin'
      });
      throw new Error("Only a System Owner (superuser) can configure the Super Admin role.");
    }

    const newName = name !== undefined ? String(name).trim() : role.name;
    const newCode = code !== undefined ? normalizeRoleCode(code) : role.code;

    // Check code immutability
    const hasAssignments = this.userAssignments.some(a => a.roleId === role.id);
    const hasPermissions = this.rolePermissions.some(p => p.roleId === role.id);
    if ((hasAssignments || hasPermissions) && newCode !== role.code) {
      throw new Error("Role code cannot be modified once permissions or users are assigned.");
    }

    const before = { ...role };
    role.name = newName;
    role.code = newCode;
    if (description !== undefined) role.description = description;
    if (is_active !== undefined) role.is_active = Boolean(is_active);

    this.audit.logEvent({
      actor,
      action: 'role_updated',
      target: role,
      summary: `Updated role '${role.name}' (${role.code})`,
      before,
      after: { ...role }
    });

    return role;
  }

  deactivateRole(actor, roleId, activeUsersList = []) {
    const role = this.roles.get(roleId);
    if (!role) throw new Error("Role not found.");

    if (role.is_system_protected || role.code === 'system_owner') {
      this.audit.logEvent({
        actor,
        action: 'unauthorized_system_owner_deactivation_attempt',
        target: role,
        summary: 'Attempted deactivation on protected system_owner'
      });
      throw new Error("Protected System Owner role cannot be disabled.");
    }

    if (role.code === 'super_admin' && (!actor || !actor.is_superuser)) {
      this.audit.logEvent({
        actor,
        action: 'unauthorized_super_admin_deactivation_attempt',
        target: role,
        summary: 'Non-superuser attempted deactivation on super_admin'
      });
      throw new Error("Only a System Owner (superuser) can deactivate the Super Admin role.");
    }

    // Lockout check: cannot deactivate if it leaves 0 active privileged roles / users
    const isPrivileged = ['admin', 'super_admin', 'system_owner'].includes(role.code);
    if (isPrivileged) {
      const otherActivePrivilegedRoles = Array.from(this.roles.values()).filter(
        r => r.id !== role.id && r.is_active && ['admin', 'super_admin', 'system_owner'].includes(r.code)
      );

      const hasActiveSuperuser = activeUsersList.some(u => u.is_superuser && u.is_active);
      const hasOtherPrivilegedUser = activeUsersList.some(
        u => u.is_active && this.userAssignments.some(a => a.userId === u.id && otherActivePrivilegedRoles.some(opr => opr.id === a.roleId))
      );

      if (!hasActiveSuperuser && !hasOtherPrivilegedUser) {
        this.audit.logEvent({
          actor,
          action: 'role_lockout_prevented',
          target: role,
          summary: `Deactivation blocked on role '${role.code}' to prevent administrative lockout`
        });
        throw new Error("Cannot deactivate role: it is the last effective privileged role (lockout prevention).");
      }
    }

    role.is_active = false;

    this.audit.logEvent({
      actor,
      action: 'role_deactivated',
      target: role,
      summary: `Deactivated dynamic role '${role.name}' (${role.code})`
    });

    return role;
  }

  assignPermission(actor, roleId, permCode, dataScope, actorPermissions) {
    const role = this.roles.get(roleId);
    if (!role) throw new Error("Role not found.");

    if (role.is_system_protected || role.code === 'system_owner') {
      throw new Error("System Owner permissions cannot be modified via UI.");
    }

    if (role.code === 'super_admin' && (!actor || !actor.is_superuser)) {
      throw new Error("Only a System Owner (superuser) can configure Super Admin permissions.");
    }

    if (!actor.is_superuser) {
      // Must hold permission
      const actorPerm = actorPermissions[permCode];
      if (!actorPerm || !actorPerm.granted) {
        this.audit.logEvent({
          actor,
          action: 'unauthorized_perm_grant_attempt',
          target: role,
          summary: `Attempted to grant unheld permission '${permCode}'`
        });
        throw new Error(`Cannot grant permission '${permCode}': you do not possess this permission.`);
      }

      // Must not exceed data scope
      const actorScopeRank = SCOPE_HIERARCHY[actorPerm.scope] || 1;
      const requestedScopeRank = SCOPE_HIERARCHY[dataScope] || 1;
      if (requestedScopeRank > actorScopeRank) {
        this.audit.logEvent({
          actor,
          action: 'unauthorized_scope_elevation_attempt',
          target: role,
          summary: `Scope elevation: requested '${dataScope}' (rank ${requestedScopeRank}) exceeds actor '${actorPerm.scope}' (rank ${actorPerm.scope})`
        });
        throw new Error(`Privilege violation: Cannot grant data scope '${dataScope}' which exceeds your effective scope '${actorPerm.scope}'.`);
      }
    }

    let rp = this.rolePermissions.find(p => p.roleId === roleId && p.permCode === permCode);
    if (!rp) {
      rp = { roleId, permCode, dataScope };
      this.rolePermissions.push(rp);
    } else {
      rp.dataScope = dataScope;
    }

    this.audit.logEvent({
      actor,
      action: 'role_permission_granted',
      target: role,
      summary: `Granted '${permCode}' with scope '${dataScope}' on role '${role.code}'`
    });

    return rp;
  }
}

// --- Test Runner ---

function runTests() {
  const audit = new AuditService();
  const rbac = new RBACAdministrationService(audit);

  const superuser = { id: 1, email: 'system.owner@company.com', is_superuser: true, is_active: true };
  const branchAdmin = { id: 2, email: 'branch.admin@company.com', is_superuser: false, is_active: true };

  const branchAdminPerms = {
    'projects.view': { granted: true, scope: 'branch' },
    'projects.edit': { granted: true, scope: 'branch' },
    'schedule.view': { granted: true, scope: 'branch' }
  };

  console.log("================================================================================");
  console.log("             DYNAMIC RBAC ADMINISTRATION VERIFICATION SUITE                     ");
  console.log("================================================================================\n");

  // Test 1: Role Normalization
  console.log("Test 1: Code normalization from name (e.g. 'Project Supervisor' -> 'project_supervisor')");
  const supervisorRole = rbac.createRole(branchAdmin, {
    name: "Project Supervisor",
    description: "Supervises project execution and assigned staff schedules"
  });
  assert.strictEqual(supervisorRole.code, 'project_supervisor');
  assert.strictEqual(supervisorRole.is_active, true);
  console.log("  [PASS] Successfully generated normalized code 'project_supervisor'\n");

  // Test 2: Edge Case 1 - Casing & Spacing collision detection
  console.log("Test 2: Edge Case 1 - Collision detection for differing casing and extra whitespace");
  assert.throws(
    () => {
      rbac.createRole(branchAdmin, { name: "  pRoJeCt   SuPeRvIsOr  " });
    },
    /already exists/i,
    "Should reject collision on existing role name"
  );
  assert.throws(
    () => {
      rbac.createRole(branchAdmin, { name: "Different Name", code: "  PROJECT_SUPERVISOR  " });
    },
    /already exists/i,
    "Should reject collision on normalized existing code"
  );
  console.log("  [PASS] Collision prevented: zero duplicate roles created\n");

  // Test 3: System Owner protection
  console.log("Test 3: System Owner UI immutability (cannot create, edit, or deactivate)");
  assert.throws(
    () => {
      rbac.createRole(branchAdmin, { name: "System Owner", code: "system_owner" });
    },
    /system-protected/i,
    "Cannot create system_owner"
  );
  assert.throws(
    () => {
      rbac.updateRole(branchAdmin, 1, { name: "Hacked Owner" });
    },
    /Protected System Owner role cannot be modified/i,
    "Cannot edit system_owner"
  );
  assert.throws(
    () => {
      rbac.deactivateRole(branchAdmin, 1);
    },
    /Protected System Owner role cannot be disabled/i,
    "Cannot deactivate system_owner"
  );
  console.log("  [PASS] System Owner protected against unauthorized creation, mutation, deactivation\n");

  // Test 4: Super Admin boundary (only superuser can create/configure/deactivate)
  console.log("Test 4: Super Admin privilege boundary (is_superuser=false, superuser-only access)");
  assert.throws(
    () => {
      rbac.createRole(branchAdmin, { name: "Super Admin", code: "super_admin" });
    },
    /Only a System Owner \(Django superuser\) can create/i,
    "Non-superuser cannot create super_admin"
  );

  const superAdminRole = rbac.createRole(superuser, {
    name: "Super Admin",
    code: "super_admin",
    description: "Non-superuser executive with dynamic high privileges"
  });
  assert.strictEqual(superAdminRole.code, 'super_admin');
  assert.strictEqual(superAdminRole.is_system_protected, false); // Dynamic role!
  console.log("  [PASS] Super Admin created by System Owner with is_superuser=false\n");

  // Test 5: Role code immutability once assigned or permissions attached
  console.log("Test 5: Code immutability on role with user assignments or permissions");
  rbac.userAssignments.push({ userId: 3, roleId: supervisorRole.id });
  assert.throws(
    () => {
      rbac.updateRole(branchAdmin, supervisorRole.id, { code: 'new_supervisor_code' });
    },
    /Role code cannot be modified once permissions or users are assigned/i,
    "Locked code cannot be altered"
  );
  console.log("  [PASS] Code locked from alteration after assignments are attached\n");

  // Test 6: Hierarchical scope capping & permission boundaries
  console.log("Test 6: Scope capping & permission boundary enforcement");
  // 6a: Attempting to grant unheld permission
  assert.throws(
    () => {
      rbac.assignPermission(branchAdmin, supervisorRole.id, 'payroll.process', 'branch', branchAdminPerms);
    },
    /you do not possess this permission/i,
    "Cannot grant permission not held by actor"
  );

  // 6b: Attempting to grant scope higher than actor's scope (Branch -> Global)
  assert.throws(
    () => {
      rbac.assignPermission(branchAdmin, supervisorRole.id, 'projects.edit', 'global', branchAdminPerms);
    },
    /exceeds your effective scope/i,
    "Cannot grant Global scope when actor only has Branch scope"
  );

  // 6c: Permitted scope grant (Branch -> Branch)
  const validPerm = rbac.assignPermission(branchAdmin, supervisorRole.id, 'projects.edit', 'branch', branchAdminPerms);
  assert.strictEqual(validPerm.dataScope, 'branch');
  console.log("  [PASS] Scope capping and unheld permission grants strictly rejected\n");

  // Test 7: Lockout prevention on last effective privileged role
  console.log("Test 7: Administrative lockout prevention during deactivation");
  // Only 1 active superuser in activeUsersList
  const usersList = [
    { id: 1, email: 'system.owner@company.com', is_superuser: false, is_active: true } // not superuser
  ];
  rbac.userAssignments.push({ userId: 1, roleId: superAdminRole.id });

  assert.throws(
    () => {
      rbac.deactivateRole(superuser, superAdminRole.id, usersList);
    },
    /last effective privileged role/i,
    "Cannot deactivate last active privileged role"
  );
  console.log("  [PASS] Lockout prevention successfully blocked deactivation\n");

  // Test 8: Audited soft deactivation
  console.log("Test 8: Audited soft deactivation preserves assignments and logs audit trail");
  // Add a second superuser to allow safe deactivation
  usersList.push({ id: 99, email: 'admin2@company.com', is_superuser: true, is_active: true });
  rbac.deactivateRole(superuser, superAdminRole.id, usersList);
  assert.strictEqual(superAdminRole.is_active, false);

  // Assert user assignment still intact
  const assignmentStillExists = rbac.userAssignments.some(a => a.roleId === superAdminRole.id);
  assert.strictEqual(assignmentStillExists, true);

  // Assert audit events logged
  const deactivateAudit = audit.events.find(e => e.action === 'role_deactivated' && e.target === 'super_admin');
  assert.ok(deactivateAudit, "Audit event must be logged for role deactivation");
  console.log("  [PASS] Role deactivated softly with assignments and audit log intact\n");

  console.log("================================================================================");
  console.log("                        ALL 8 RBAC INVARIANTS PASSED                            ");
  console.log(`  Total Audit Events Recorded: ${audit.events.length}`);
  console.log("================================================================================");
}

runTests();
