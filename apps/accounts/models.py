from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.utils import timezone

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
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'user_session'
        ordering = ['-login_time']
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['session_key']),
            models.Index(fields=['device_id']),
        ]

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


