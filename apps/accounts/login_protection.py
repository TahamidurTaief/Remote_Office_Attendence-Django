import time
from datetime import timedelta
from django.utils import timezone
from apps.accounts.models import LoginProtection, CustomUser
from apps.notifications.models import log_audit

LOCK_DURATIONS = {
    1: 60,     # Level 1: 1 min (60s)
    2: 120,    # Level 2: 2 min (120s)
    3: 240,    # Level 3: 4 min (240s)
    4: 480,    # Level 4: 8 min (480s)
    5: 600,    # Level 5+: 10 min (600s, capped)
}

def get_or_create_protection(user=None, email='', ip=None, device_id=''):
    """
    Look up or create LoginProtection record for 3-layer matching:
    1. user + ip + device_fingerprint
    2. email + ip + device_fingerprint
    3. ip + device_fingerprint
    """
    now = timezone.now()
    email_clean = (email or '').strip().lower()
    if not user and email_clean:
        user = CustomUser.objects.filter(email__iexact=email_clean).first()

    record = None
    if user and ip and device_id:
        record = LoginProtection.objects.filter(user=user, ip=ip, device_fingerprint=device_id).first()
    if not record and email_clean and ip:
        record = LoginProtection.objects.filter(email=email_clean, ip=ip).first()
    if not record and ip and device_id:
        record = LoginProtection.objects.filter(ip=ip, device_fingerprint=device_id).first()

    if not record:
        record = LoginProtection.objects.create(
            user=user,
            email=email_clean,
            ip=ip,
            device_fingerprint=device_id
        )
    else:
        # Check clean reset window (10 minutes with no fails after observation window)
        if record.observation_ends_at and now > record.observation_ends_at + timedelta(minutes=5):
            record.reset_lock()

    return record


def check_3layer_lock(user=None, email='', ip=None, device_id=''):
    """
    3-layer lock decision:
    Check user + IP + device, email + IP, IP + device.
    If ANY layer is currently locked (locked_until > now), return locked=True.
    """
    now = timezone.now()
    email_clean = (email or '').strip().lower()
    if not user and email_clean:
        user = CustomUser.objects.filter(email__iexact=email_clean).first()

    qs = LoginProtection.objects.filter(locked_until__gt=now)

    query = None
    if user:
        query = qs.filter(user=user)
    if not query or not query.exists():
        if email_clean:
            query = qs.filter(email=email_clean, ip=ip)
    if not query or not query.exists():
        if ip and device_id:
            query = qs.filter(ip=ip, device_fingerprint=device_id)

    locked_record = query.first() if query else None
    if locked_record:
        remaining_secs = int((locked_record.locked_until - now).total_seconds())
        return True, remaining_secs, locked_record

    return False, 0, None


def record_failed_attempt(user=None, email='', ip=None, device_id=''):
    """
    Records a failed login attempt according to state machine rules:
    - Fails 0-2: Normal
    - Fail 3: Captcha required ("2 attempts remaining before lock")
    - Fail 5: Trigger lock level 1 (1 min)
    - Subsequent fails in observation window: Escalate lock level (2m -> 4m -> 8m -> 10m max)
    """
    now = timezone.now()
    prot = get_or_create_protection(user=user, email=email, ip=ip, device_id=device_id)

    prot.failed_attempts += 1

    # Check captcha trigger at 3rd fail
    if prot.failed_attempts >= 3:
        prot.captcha_required = True
        log_audit(user or prot.user, 'captcha_triggered', summary=f"Captcha triggered on attempt #{prot.failed_attempts}", ip=ip)

    # Check lock trigger at 5th fail or observation escalation
    if prot.failed_attempts >= 5:
        next_level = min(prot.current_lock_level + 1, 5)
        if next_level == 0:
            next_level = 1
        prot.current_lock_level = next_level

        duration_sec = LOCK_DURATIONS.get(next_level, 600)
        prot.locked_until = now + timedelta(seconds=duration_sec)
        prot.observation_ends_at = prot.locked_until + timedelta(minutes=5)

        # Sync CustomUser fields for backwards compatibility
        target_u = user or prot.user
        if target_u:
            target_u.failed_login_count = prot.failed_attempts
            target_u.locked_until = prot.locked_until
            target_u.save(update_fields=['failed_login_count', 'locked_until'])

        log_audit(
            target_u,
            'account_locked',
            summary=f"Account locked: Level {next_level} ({duration_sec // 60} min lock)",
            ip=ip,
            metadata={'lock_level': next_level, 'duration_seconds': duration_sec}
        )

    prot.save()
    return prot


def record_successful_login(user=None, email='', ip=None, device_id=''):
    """
    Full reset on successful login.
    """
    prot = get_or_create_protection(user=user, email=email, ip=ip, device_id=device_id)
    prot.reset_lock()

    if user:
        user.failed_login_count = 0
        user.locked_until = None
        user.save(update_fields=['failed_login_count', 'locked_until'])

    return prot
