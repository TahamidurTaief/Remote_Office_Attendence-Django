import os
import json
import time
import base64
import struct
import logging
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .models import WebPushSubscription

logger = logging.getLogger(__name__)


def b64url_decode(s: str) -> bytes:
    if isinstance(s, bytes):
        s = s.decode('ascii')
    padding = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode('ascii').rstrip('=')


def encrypt_aes128gcm(payload: bytes, p256dh: str, auth: str) -> bytes:
    remote_pub_bytes = b64url_decode(p256dh)
    remote_auth = b64url_decode(auth)
    remote_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), remote_pub_bytes)

    local_priv = ec.generate_private_key(ec.SECP256R1())
    local_pub = local_priv.public_key()
    local_pub_bytes = local_pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)

    shared_secret = local_priv.exchange(ec.ECDH(), remote_pub)

    prk_hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=remote_auth,
        info=b'WebPush: info\x00' + remote_pub_bytes + local_pub_bytes
    )
    ikm = prk_hkdf.derive(shared_secret)

    salt = os.urandom(16)
    cek = HKDF(
        algorithm=hashes.SHA256(),
        length=16,
        salt=salt,
        info=b'Content-Encoding: aes128gcm\x00'
    ).derive(ikm)
    nonce = HKDF(
        algorithm=hashes.SHA256(),
        length=12,
        salt=salt,
        info=b'Content-Encoding: nonce\x00'
    ).derive(ikm)

    record = payload + b'\x02'
    aesgcm = AESGCM(cek)
    ciphertext = aesgcm.encrypt(nonce, record, None)

    return salt + struct.pack('>I', 4096) + struct.pack('B', len(local_pub_bytes)) + local_pub_bytes + ciphertext


def generate_vapid_headers(endpoint: str) -> dict:
    private_key_raw = getattr(settings, 'WEB_PUSH_PRIVATE_KEY', '') or ''
    public_key_raw = getattr(settings, 'WEB_PUSH_PUBLIC_KEY', '') or ''
    claim_email = getattr(settings, 'WEB_PUSH_VAPID_CLAIM_EMAIL', 'admin@fieldtrack.com')

    if not private_key_raw:
        return {}

    parsed = urlparse(endpoint)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    try:
        if private_key_raw.startswith('-----BEGIN'):
            vapid_priv = serialization.load_pem_private_key(private_key_raw.encode('ascii'), password=None)
        else:
            raw_priv_bytes = b64url_decode(private_key_raw)
            vapid_priv = ec.derive_private_key(int.from_bytes(raw_priv_bytes, 'big'), ec.SECP256R1())

        if not public_key_raw:
            pub_bytes = vapid_priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
            public_key_raw = b64url_encode(pub_bytes)

        header_b64 = b64url_encode(json.dumps({'typ': 'JWT', 'alg': 'ES256'}).encode('utf-8'))
        claims_b64 = b64url_encode(json.dumps({
            'aud': origin,
            'exp': int(time.time()) + 86400,
            'sub': f'mailto:{claim_email}'
        }).encode('utf-8'))

        token_input = f"{header_b64}.{claims_b64}".encode('ascii')
        signature = vapid_priv.sign(token_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(signature)
        raw_sig = r.to_bytes(32, 'big') + s.to_bytes(32, 'big')
        sig_b64 = b64url_encode(raw_sig)

        jwt = f"{header_b64}.{claims_b64}.{sig_b64}"
        return {
            'Authorization': f'vapid t={jwt}, k={public_key_raw}'
        }
    except Exception as e:
        logger.warning("Failed to generate VAPID headers for web push: %s", type(e).__name__)
        return {}


def send_web_push(subscription: WebPushSubscription, payload_data, ttl: int = 86400, timeout: int = 10) -> bool:
    if not subscription.is_active or not subscription.endpoint:
        return False

    if isinstance(payload_data, dict):
        payload_bytes = json.dumps(payload_data).encode('utf-8')
    elif isinstance(payload_data, str):
        payload_bytes = payload_data.encode('utf-8')
    elif isinstance(payload_data, bytes):
        payload_bytes = payload_data
    else:
        payload_bytes = b''

    headers = {
        'TTL': str(ttl),
    }

    if payload_bytes and subscription.p256dh and subscription.auth:
        try:
            body = encrypt_aes128gcm(payload_bytes, subscription.p256dh, subscription.auth)
            headers['Content-Encoding'] = 'aes128gcm'
            headers['Content-Type'] = 'application/octet-stream'
        except Exception as e:
            logger.warning("Failed to encrypt web push payload: %s", type(e).__name__)
            return False
    else:
        body = b''
        headers['Content-Length'] = '0'

    vapid_headers = generate_vapid_headers(subscription.endpoint)
    headers.update(vapid_headers)

    try:
        response = requests.post(subscription.endpoint, data=body, headers=headers, timeout=timeout)
        if response.status_code in (200, 201, 202):
            subscription.last_seen_at = timezone.now()
            subscription.save(update_fields=['last_seen_at'])
            return True
        elif response.status_code in (404, 410):
            subscription.is_active = False
            subscription.save(update_fields=['is_active'])
            return False
        else:
            logger.warning("Web push provider returned status %s", response.status_code)
            return False
    except Exception as e:
        logger.warning("Web push delivery failed: %s", type(e).__name__)
        return False


def deliver_notification_web_push(notification) -> int:
    if not notification or not notification.recipient_id:
        return 0

    recipient = notification.recipient
    subscriptions = WebPushSubscription.objects.filter(
        user=recipient,
        is_active=True
    )

    if not subscriptions.exists():
        return 0

    if notification.notif_type == 'schedule_event':
        try:
            redirect_url = reverse('schedule:month_view')
        except Exception:
            redirect_url = '/schedule/'
    else:
        try:
            redirect_url = reverse('staff:my_tasks')
        except Exception:
            redirect_url = '/notifications/'

    payload = {
        'id': notification.id,
        'type': notification.notif_type,
        'title': notification.title,
        'message': notification.message or notification.title,
        'body': notification.message or notification.title,
        'redirect_url': redirect_url,
        'url': redirect_url,
        'tag': f'ft-notif-{notification.id}',
        'icon': '/static/icons/icon.png',
    }

    success_count = 0
    for sub in subscriptions:
        if send_web_push(sub, payload):
            success_count += 1

    return success_count
