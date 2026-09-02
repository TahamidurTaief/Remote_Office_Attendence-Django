from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.utils import timezone
from .rbac_models import (
    DataScope, Module, Action, Permission, PermissionDependency,
    Role, RolePermission, UserRoleAssignment, UserPermissionOverride,
    ApprovalPolicy, ApprovalChainStep
)

class CustomUserManager(BaseUserManager):
    def create_user(self, email=None, phone=None, password=None, **extra_fields):
        if not email and not phone:
            raise ValueError('Either Email or Phone must be set')
        if email:
            email = self.normalize_email(email)
            if not email.strip():
                email = None
        else:
            email = None
            
        if phone:
            phone = phone.strip()
            if not phone:
                phone = None
        else:
            phone = None

        user = self.model(email=email, phone=phone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('role', 'admin')

        return self.create_user(email=email, password=password, **extra_fields)

class CustomUser(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('staff', 'Staff'),
        ('hr', 'HR'),
        ('finance', 'Finance'),
        ('accounts', 'Accounts'),
    )
    
    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    # Auth Security & Session Control Fields
    active_session_key = models.CharField(max_length=255, null=True, blank=True)
    failed_login_count = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(default=timezone.now)

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        permissions = [
            ('manage_roles', 'Can manage user roles and groups'),
        ]

    def __str__(self):
        return self.email or self.phone or "No Identifier"

    @property
    def security_policy(self):
        policy = SecurityPolicy.objects.filter(role=self.role).first()
        return policy

    @property
    def idle_timeout_minutes(self):
        policy = self.security_policy
        return policy.idle_timeout_minutes if policy else 30

    @property
    def display_name(self):
        emp = getattr(self, 'employee_master', None)
        if emp:
            full_name = f"{emp.first_name} {emp.last_name}".strip()
            if full_name:
                return full_name
        first_name = getattr(self, 'first_name', '')
        last_name = getattr(self, 'last_name', '')
        full_name = f"{first_name} {last_name}".strip()
        if full_name:
            return full_name
        return self.phone or self.email or "User"



class UserLoginActivity(models.Model):
    STATUS_CHOICES = (
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('locked', 'Account Locked'),
    )

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='login_activities'
    )
    identifier_entered = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='success')
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'User Login Activity'
        verbose_name_plural = 'User Login Activities'

    def __str__(self):
        user_str = self.user.email if (self.user and self.user.email) else (self.identifier_entered or 'Unknown')
        return f"{user_str} - {self.status} at {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"


class UserSession(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='sessions')
    device_id = models.CharField(max_length=255, db_index=True)
    session_key = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    browser = models.TextField(blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    login_time = models.DateTimeField(default=timezone.now)
    logout_time = models.DateTimeField(null=True, blank=True)
    last_activity = models.DateTimeField(default=timezone.now)
    last_reauth_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'user_session'
        ordering = ['-login_time']
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['session_key']),
            models.Index(fields=['device_id']),
        ]

    @property
    def device_display_name(self):
        from .utils import parse_user_agent
        if self.browser:
            parsed = parse_user_agent(self.browser)
            if parsed and parsed != "Unknown Device":
                return parsed
        return f"Device ({self.device_id[:8]})"

    def __str__(self):
        return f"{self.user} ({self.device_id[:8]}) - {'Active' if self.is_active else 'Expired'}"


class TrustedDevice(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='trusted_devices')
    device_hash = models.CharField(max_length=255, db_index=True)
    device_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expire_at = models.DateTimeField()

    class Meta:
        db_table = 'trusted_device'
        indexes = [
            models.Index(fields=['device_hash']),
            models.Index(fields=['user', 'device_hash']),
        ]

    def __str__(self):
        return f"{self.user} - {self.device_name or self.device_hash[:8]}"


class PasswordResetOTP(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='reset_otps')
    otp_code = models.CharField(max_length=6)
    reset_token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        db_table = 'password_reset_otp'
        ordering = ['-created_at']

    def is_valid(self):
        return not self.is_used and timezone.now() <= self.expires_at

    def __str__(self):
        return f"OTP for {self.user} - {'Used' if self.is_used else 'Valid'}"


class LoginProtection(models.Model):
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='login_protections'
    )
    email = models.CharField(max_length=255, db_index=True, blank=True)
    ip = models.GenericIPAddressField(db_index=True, null=True, blank=True)
    device_fingerprint = models.CharField(max_length=255, db_index=True, blank=True)

    failed_attempts = models.PositiveIntegerField(default=0)
    current_lock_level = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True, db_index=True)
    observation_ends_at = models.DateTimeField(null=True, blank=True)
    captcha_required = models.BooleanField(default=False)

    last_attempt = models.DateTimeField(auto_now=True)
    last_success = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'login_protection'
        indexes = [
            models.Index(fields=['user', 'ip', 'device_fingerprint']),
            models.Index(fields=['email', 'ip', 'device_fingerprint']),
            models.Index(fields=['ip', 'device_fingerprint']),
            models.Index(fields=['locked_until']),
        ]

    def is_locked(self):
        if self.locked_until:
            return timezone.now() < self.locked_until
        return False

    def remaining_lock_seconds(self):
        if self.locked_until and timezone.now() < self.locked_until:
            return int((self.locked_until - timezone.now()).total_seconds())
        return 0

    def reset_lock(self):
        self.failed_attempts = 0
        self.current_lock_level = 0
        self.locked_until = None
        self.observation_ends_at = None
        self.captcha_required = False
        self.save()

    def __str__(self):
        ident = self.user.email if self.user else (self.email or self.ip)
        return f"LoginProtection({ident}) - Fails: {self.failed_attempts}, Level: {self.current_lock_level}"


class WorkspaceLockEvent(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='workspace_lock_events')
    session = models.ForeignKey(UserSession, on_delete=models.SET_NULL, null=True, blank=True)
    locked_at = models.DateTimeField(default=timezone.now)
    unlocked_at = models.DateTimeField(null=True, blank=True)
    lock_reason = models.CharField(max_length=50, default='idle')
    unlock_method = models.CharField(max_length=50, blank=True)

    class Meta:
        db_table = 'workspace_lock_event'
        ordering = ['-locked_at']

    def __str__(self):
        return f"WorkspaceLock({self.user}) - {self.lock_reason} at {self.locked_at}"


import pyotp
import hashlib
import secrets

class UserSecurityProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='security_profile')
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = models.CharField(max_length=255, blank=True)
    mfa_enabled_at = models.DateTimeField(null=True, blank=True)
    backup_codes = models.JSONField(default=list, blank=True)
    pin_hash = models.CharField(max_length=128, blank=True)
    workspace_password_hash = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_security_profile'

    def set_pin(self, pin):
        self.pin_hash = hashlib.sha256(str(pin).strip().encode('utf-8')).hexdigest()
        self.save(update_fields=['pin_hash'])

    def check_pin(self, pin):
        if not self.pin_hash or not pin:
            return False
        return self.pin_hash == hashlib.sha256(str(pin).strip().encode('utf-8')).hexdigest()

    def set_workspace_password(self, raw_password):
        from django.contrib.auth.hashers import make_password
        if raw_password:
            self.workspace_password_hash = make_password(str(raw_password).strip())
        else:
            self.workspace_password_hash = ''
        self.save(update_fields=['workspace_password_hash'])

    def check_workspace_password(self, raw_password):
        from django.contrib.auth.hashers import check_password
        if not self.workspace_password_hash or not raw_password:
            return False
        return check_password(str(raw_password).strip(), self.workspace_password_hash)

    def generate_new_secret(self):
        self.mfa_secret = pyotp.random_base32()
        return self.mfa_secret

    def get_totp_uri(self):
        issuer_name = "FieldTrack"
        user_identifier = self.user.email or self.user.phone or f"user_{self.user.id}"
        return pyotp.totp.TOTP(self.mfa_secret).provisioning_uri(name=user_identifier, issuer_name=issuer_name)

    def verify_totp(self, code):
        if not self.mfa_secret or not code:
            return False
        totp = pyotp.TOTP(self.mfa_secret)
        return totp.verify(str(code).strip(), valid_window=2)

    def generate_backup_codes(self):
        from django.contrib.auth.hashers import make_password
        raw_codes = []
        hashed_codes = []
        for _ in range(8):
            raw = secrets.token_hex(4).upper()
            raw_codes.append(raw)
            hashed_codes.append(make_password(raw))

        self.backup_codes = hashed_codes
        self.save(update_fields=['backup_codes'])
        return raw_codes

    def verify_backup_code(self, raw_code):
        if not self.backup_codes or not raw_code:
            return False
        from django.contrib.auth.hashers import check_password
        raw_clean = str(raw_code).strip().upper()
        legacy_hash = hashlib.sha256(raw_clean.encode('utf-8')).hexdigest()
        for idx, stored in enumerate(self.backup_codes):
            if stored == legacy_hash or check_password(raw_clean, stored):
                self.backup_codes.pop(idx)
                self.save(update_fields=['backup_codes'])
                return True
        return False

    def __str__(self):
        return f"SecurityProfile({self.user}) - MFA: {'Enabled' if self.mfa_enabled else 'Disabled'}"


class SecurityPolicy(models.Model):
    UNLOCK_CHOICES = (
        ('password', 'Password'),
        ('pin', 'PIN'),
        ('workspace_password', 'Dedicated Workspace Password'),
        ('mfa', 'MFA TOTP'),
    )

    role = models.CharField(max_length=50, unique=True)
    mfa_required = models.BooleanField(default=False)
    unlock_method = models.CharField(max_length=20, choices=UNLOCK_CHOICES, default='password')
    reauth_interval_hours = models.PositiveIntegerField(null=True, blank=True, default=4)
    trusted_device_days = models.PositiveIntegerField(default=30)
    idle_timeout_minutes = models.PositiveIntegerField(default=30, validators=[MinValueValidator(1), MaxValueValidator(240)])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'security_policy'

    def __str__(self):
        return f"SecurityPolicy({self.role}) - MFA Req: {self.mfa_required}, Unlock: {self.unlock_method}"







