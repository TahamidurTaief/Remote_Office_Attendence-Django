import logging
import os
import secrets
from datetime import timedelta
from email.mime.image import MIMEImage

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone

from apps.accounts.models import CustomUser, LoginProtection, PasswordResetOTP, UserSession
from apps.accounts.backends import get_identifier_query
from apps.notifications.models import log_audit

logger = logging.getLogger(__name__)

OTP_EXPIRY_MINUTES = 5
GRANT_EXPIRY_MINUTES = 5
MAX_VERIFY_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
THROTTLE_WINDOW_MINUTES = 15
MAX_REQUESTS_PER_WINDOW = 5
THROTTLE_FINGERPRINT = "password_reset"

# Precomputed dummy hash for comparable work when account is unknown/ineligible
_DUMMY_OTP_HASH = make_password("000000")


class SchemaMigrationRequiredError(RuntimeError):
    """Raised when PasswordResetOTP schema lacks hardened hash/grant columns."""


def _assert_schema_ready():
    otp_field = PasswordResetOTP._meta.get_field("otp_code")
    required_fields = {
        "failed_attempts",
        "verified_at",
        "grant_expires_at",
        "grant_consumed_at",
        "session_key",
        "last_sent_at",
    }
    existing = {f.name for f in PasswordResetOTP._meta.get_fields()}
    missing = sorted(required_fields - existing)
    if (otp_field.max_length or 0) < 64 or missing:
        raise SchemaMigrationRequiredError(
            f"PasswordResetOTP schema migration required: otp_code.max_length={otp_field.max_length} "
            f"(need >= 64), missing_fields={missing}."
        )


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.META.get("REMOTE_ADDR") or "0.0.0.0"


def _ensure_session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def _check_and_record_throttle(identifier_key, ip_addr):
    """
    Shared persistent DB-backed throttling via LoginProtection.
    Returns (allowed: bool, retry_after_seconds: int).
    """
    now = timezone.now()
    window_cutoff = now - timedelta(minutes=THROTTLE_WINDOW_MINUTES)

    for key_kwargs in (
        {"email": identifier_key[:255], "ip": ip_addr, "device_fingerprint": THROTTLE_FINGERPRINT},
        {"email": "__ip_only__", "ip": ip_addr, "device_fingerprint": THROTTLE_FINGERPRINT},
    ):
        prot, _ = LoginProtection.objects.get_or_create(**key_kwargs)
        if prot.is_locked():
            return False, max(1, prot.remaining_lock_seconds())
        if prot.last_attempt and prot.last_attempt < window_cutoff:
            prot.failed_attempts = 0
        prot.failed_attempts += 1
        if prot.failed_attempts > MAX_REQUESTS_PER_WINDOW:
            prot.locked_until = now + timedelta(minutes=THROTTLE_WINDOW_MINUTES)
            prot.save(update_fields=["failed_attempts", "locked_until", "last_attempt", "updated_at"])
            return False, THROTTLE_WINDOW_MINUTES * 60
        prot.save(update_fields=["failed_attempts", "last_attempt", "updated_at"])
    return True, 0


def _attach_cid_image(msg, file_path, cid_name):
    if not file_path or not os.path.isfile(file_path):
        return None
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    subtype = "jpeg" if ext in ("jpg", "jpeg") else ("png" if ext == "png" else None)
    if not subtype:
        return None
    with open(file_path, "rb") as f:
        img = MIMEImage(f.read(), _subtype=subtype)
    img.add_header("Content-ID", f"<{cid_name}>")
    img.add_header("Content-Disposition", "inline", filename=os.path.basename(file_path))
    msg.attach(img)
    return cid_name


def build_otp_email(recipient_email, otp_code):
    """
    Build multipart/alternative password reset email with inline CSS and CID logos.
    """
    signtech_path = os.path.join(settings.BASE_DIR, "static", "images", "logo.png")
    taieflab_path = (getattr(settings, "TAIEFLAB_LOGO_PATH", "") or "").strip()

    signtech_cid = "signtech_logo" if os.path.isfile(signtech_path) else ""
    taieflab_cid = "taieflab_logo" if (taieflab_path and os.path.isfile(taieflab_path)) else ""

    ctx = {
        "otp_code": otp_code,
        "expiry_minutes": OTP_EXPIRY_MINUTES,
        "signtech_logo_cid": signtech_cid,
        "taieflab_logo_cid": taieflab_cid,
    }
    subject = "Signtech Trackme - Password Reset Verification Code"
    text_body = render_to_string("accounts/emails/password_reset_otp.txt", ctx)
    html_body = render_to_string("accounts/emails/password_reset_otp.html", ctx)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.mixed_subtype = "related"

    if signtech_cid:
        _attach_cid_image(msg, signtech_path, signtech_cid)
    if taieflab_cid:
        _attach_cid_image(msg, taieflab_path, taieflab_cid)

    return msg


def send_otp_email(msg):
    """
    Send email via configured backend outside DB transactions with bounded timeout.
    """
    timeout = int(getattr(settings, "EMAIL_TIMEOUT", 10) or 10)
    with get_connection(timeout=timeout, fail_silently=False) as conn:
        conn.send_messages([msg])


class PasswordResetService:
    @staticmethod
    def generate_otp():
        return f"{secrets.randbelow(900000) + 100000}"

    @staticmethod
    def hash_otp(otp_code):
        return make_password(otp_code)

    @staticmethod
    def verify_otp_hash(otp_code, stored_hash):
        return check_password(otp_code, stored_hash)

    @classmethod
    def request_reset(cls, request, identifier):
        """
        Issue an OTP challenge to the account's stored registered email.
        Returns dict: {'ok': bool, 'reset_token': str, 'error': str|None, 'throttled': bool}.
        """
        _assert_schema_ready()

        norm_ident = (identifier or "").strip().lower()
        ip_addr = _client_ip(request)
        allowed, _retry = _check_and_record_throttle(norm_ident or "__empty__", ip_addr)
        if not allowed:
            return {
                "ok": False,
                "reset_token": "",
                "error": "Too many reset requests. Please try again later.",
                "throttled": True,
            }

        user = CustomUser.objects.filter(get_identifier_query(identifier)).first()
        registered_email = (getattr(user, "email", "") or "").strip() if user else ""

        if not user or not user.is_active or not registered_email:
            # Comparable work without leaking account existence
            check_password("000000", _DUMMY_OTP_HASH)
            return {
                "ok": True,
                "reset_token": secrets.token_urlsafe(32),
                "error": None,
                "throttled": False,
            }

        now = timezone.now()
        session_key = _ensure_session_key(request)

        with transaction.atomic():
            latest = (
                PasswordResetOTP.objects.select_for_update()
                .filter(user=user)
                .order_by("-created_at")
                .first()
            )
            if latest and latest.last_sent_at:
                elapsed = (now - latest.last_sent_at).total_seconds()
                if elapsed < RESEND_COOLDOWN_SECONDS:
                    return {
                        "ok": False,
                        "reset_token": latest.reset_token,
                        "error": f"Please wait {int(RESEND_COOLDOWN_SECONDS - elapsed) + 1}s before requesting another code.",
                        "throttled": True,
                    }

            # Invalidate all prior active challenges and unconsumed grants for this user
            PasswordResetOTP.objects.filter(user=user, is_used=False).update(is_used=True)
            PasswordResetOTP.objects.filter(user=user, grant_consumed_at__isnull=True).update(
                grant_consumed_at=now
            )

            otp_code = cls.generate_otp()
            reset_token = secrets.token_urlsafe(32)
            challenge = PasswordResetOTP.objects.create(
                user=user,
                otp_code=cls.hash_otp(otp_code),
                reset_token=reset_token,
                expires_at=now + timedelta(minutes=OTP_EXPIRY_MINUTES),
                last_sent_at=now,
                session_key=session_key,
                request_ip=ip_addr,
            )

        # SMTP delivery strictly outside database lock/transaction
        try:
            email_msg = build_otp_email(registered_email, otp_code)
            send_otp_email(email_msg)
        except Exception:
            PasswordResetOTP.objects.filter(pk=challenge.pk).update(
                is_used=True,
                grant_consumed_at=timezone.now(),
            )
            logger.warning("Password reset email delivery failed for user_id=%s", user.pk)
            return {
                "ok": False,
                "reset_token": "",
                "error": "Unable to deliver verification email right now. Please try again later.",
                "throttled": False,
            }

        logger.info("Password reset OTP email dispatched for user_id=%s", user.pk)
        return {
            "ok": True,
            "reset_token": reset_token,
            "error": None,
            "throttled": False,
        }

    @classmethod
    def verify_challenge(cls, request, reset_token, otp_code):
        _assert_schema_ready()

        if not reset_token or not otp_code:
            return False, "Invalid or expired OTP code. Please try again."

        now = timezone.now()
        session_key = _ensure_session_key(request)

        with transaction.atomic():
            challenge = (
                PasswordResetOTP.objects.select_for_update()
                .filter(reset_token=reset_token)
                .first()
            )
            if not challenge or challenge.is_used or now > challenge.expires_at:
                check_password("000000", _DUMMY_OTP_HASH)
                return False, "Invalid or expired OTP code. Please try again."

            if challenge.session_key and challenge.session_key != session_key:
                return False, "Invalid reset session. Please request a new OTP."

            if challenge.failed_attempts >= MAX_VERIFY_ATTEMPTS:
                challenge.is_used = True
                challenge.save(update_fields=["is_used"])
                return False, "Too many failed attempts. Please request a new OTP."

            if not cls.verify_otp_hash(otp_code, challenge.otp_code):
                new_attempts = challenge.failed_attempts + 1
                challenge.failed_attempts = new_attempts
                if new_attempts >= MAX_VERIFY_ATTEMPTS:
                    challenge.is_used = True
                challenge.save(update_fields=["failed_attempts", "is_used"])
                return False, "Invalid or expired OTP code. Please try again."

            challenge.is_used = True
            challenge.verified_at = now
            challenge.grant_expires_at = now + timedelta(minutes=GRANT_EXPIRY_MINUTES)
            challenge.session_key = session_key
            challenge.save(update_fields=["is_used", "verified_at", "grant_expires_at", "session_key"])
            return True, None

    @classmethod
    def consume_grant_and_reset_password(cls, request, reset_token, new_password):
        _assert_schema_ready()

        now = timezone.now()
        session_key = request.session.session_key or ""

        with transaction.atomic():
            challenge = (
                PasswordResetOTP.objects.select_for_update()
                .select_related("user")
                .filter(reset_token=reset_token)
                .first()
            )
            if (
                not challenge
                or not challenge.verified_at
                or challenge.grant_consumed_at is not None
                or not challenge.grant_expires_at
                or now > challenge.grant_expires_at
                or not challenge.session_key
                or challenge.session_key != session_key
            ):
                return False, "Invalid or expired reset session. Please request a new OTP."

            user = challenge.user
            try:
                validate_password(new_password, user=user)
            except ValidationError as exc:
                return False, " ".join(exc.messages)

            challenge.grant_consumed_at = now
            challenge.save(update_fields=["grant_consumed_at"])

            user.set_password(new_password)
            user.save(update_fields=["password"])

            UserSession.objects.filter(user=user, is_active=True).update(
                is_active=False, logout_time=now
            )
            session_keys = list(
                UserSession.objects.filter(user=user, session_key__isnull=False)
                .values_list("session_key", flat=True)
            )
            if session_keys:
                Session.objects.filter(session_key__in=session_keys).delete()

            log_audit(user, "password_reset", summary="Password reset via verified OTP", ip=_client_ip(request))
            return True, None
