from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError


class DataScope(models.TextChoices):
    OWN = 'own', 'Own Records'
    TEAM = 'team', 'Direct Team'
    DEPARTMENT = 'department', 'Department'
    BRANCH = 'branch', 'Branch'
    COMPANY = 'company', 'Company / All Branches'
    GLOBAL = 'global', 'Global Superuser'


class Module(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, default='box')
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class Action(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    is_destructive = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class Permission(models.Model):
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name='permissions')
    action = models.ForeignKey(Action, on_delete=models.CASCADE, related_name='permissions')
    codename = models.CharField(max_length=120, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['module__sort_order', 'module__name', 'action__name']
        unique_together = ['module', 'action']

    def save(self, *args, **kwargs):
        if not self.codename and self.module and self.action:
            self.codename = f"{self.module.code}.{self.action.code}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} [{self.codename}]"


class PermissionDependency(models.Model):
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name='dependencies')
    requires_permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name='required_by')

    class Meta:
        unique_together = ['permission', 'requires_permission']

    def clean(self):
        if self.permission_id == self.requires_permission_id:
            raise ValidationError("A permission cannot depend on itself.")

    def __str__(self):
        return f"{self.permission.codename} requires {self.requires_permission.codename}"


class Role(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    description = models.TextField(blank=True)
    is_system_protected = models.BooleanField(
        default=False,
        help_text="Protected bootstrap role (System Owner). Non-deletable, non-renamable."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"

    def clean(self):
        if self.pk:
            old = Role.objects.get(pk=self.pk)
            if old.is_system_protected:
                if old.code != self.code:
                    raise ValidationError("Protected System Owner role code cannot be changed.")
                if not self.is_active:
                    raise ValidationError("Protected System Owner role cannot be disabled.")

    def delete(self, *args, **kwargs):
        if self.is_system_protected:
            raise ValidationError("Protected System Owner role cannot be deleted.")
        super().delete(*args, **kwargs)


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='role_permissions')
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name='role_permissions')
    data_scope = models.CharField(
        max_length=20,
        choices=DataScope.choices,
        default=DataScope.GLOBAL,
        db_index=True
    )

    class Meta:
        unique_together = ['role', 'permission']
        indexes = [
            models.Index(fields=['role', 'permission']),
            models.Index(fields=['role', 'data_scope']),
        ]

    def __str__(self):
        return f"{self.role.code} -> {self.permission.codename} ({self.get_data_scope_display()})"


class UserRoleAssignment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='role_assignments')
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='user_assignments')
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_roles'
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'role']
        indexes = [
            models.Index(fields=['user', 'role']),
        ]

    def __str__(self):
        return f"{self.user.email} -> {self.role.name}"


class UserPermissionOverride(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='permission_overrides')
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name='user_overrides')
    is_granted = models.BooleanField(default=True, help_text="True = Grant, False = Revoke override")
    data_scope = models.CharField(
        max_length=20,
        choices=DataScope.choices,
        blank=True,
        null=True,
        help_text="Optional data scope override"
    )

    class Meta:
        unique_together = ['user', 'permission']
        indexes = [
            models.Index(fields=['user', 'permission']),
        ]

    def __str__(self):
        mode = "GRANT" if self.is_granted else "REVOKE"
        return f"{self.user.email} -> {mode} {self.permission.codename}"


class ApprovalPolicy(models.Model):
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name='approval_policies')
    name = models.CharField(max_length=150)
    min_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    steps_required = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"ApprovalPolicy: {self.name} for {self.permission.codename}"


class ApprovalChainStep(models.Model):
    policy = models.ForeignKey(ApprovalPolicy, on_delete=models.CASCADE, related_name='chain_steps')
    step_number = models.PositiveIntegerField()
    approver_role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='approval_steps')

    class Meta:
        ordering = ['step_number']
        unique_together = ['policy', 'step_number']

    def __str__(self):
        return f"{self.policy.name} - Step {self.step_number}: {self.approver_role.name}"
