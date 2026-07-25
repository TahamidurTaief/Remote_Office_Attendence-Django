"""
Backup encryption helpers for FieldTrack.

Key hierarchy
─────────────
  SECRET_KEY (Django setting, never stored)
      │  PBKDF2-HMAC-SHA256, fixed salt "fieldtrack-backup-kek", 200 000 iterations
      ▼
  KEK  (Key-Encryption-Key, 32 bytes — derived at runtime, never stored)
      │  Fernet(KEK).encrypt(MEK_bytes)   → master_key_wrapped  (stored in DB)
      ▼
  MEK  (Master Encryption Key, Fernet key, 32 bytes URL-safe-base64)
      │  Fernet(MEK).encrypt(plaintext)   → encrypted backup file
      ▼
  Encrypted backup file (.enc)

Rules
─────
  • The raw MEK is NEVER written to disk or logged.
  • Rotating Django SECRET_KEY invalidates all wrapped MEKs (existing backups
    become unreadable without the old key — admin is warned in the UI).
  • If encryption_enabled is False, utils.py writes plaintext as before.
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


# ── KEK derivation ────────────────────────────────────────────────────────────

_KEK_SALT = b"fieldtrack-backup-kek"
_KEK_ITERATIONS = 200_000


def _derive_kek() -> bytes:
    """
    Derive a 32-byte Key-Encryption-Key from Django's SECRET_KEY.
    Returns raw bytes suitable for use as a Fernet key (after url-safe-b64 encoding).
    """
    secret = settings.SECRET_KEY
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", secret, _KEK_SALT, _KEK_ITERATIONS, dklen=32)
    return base64.urlsafe_b64encode(dk)  # Fernet needs url-safe-base64 encoded 32 bytes


def _kek_fernet() -> Fernet:
    return Fernet(_derive_kek())


# ── MEK management ────────────────────────────────────────────────────────────

def generate_wrapped_key() -> str:
    """
    Generate a new random Fernet MEK, wrap it with the KEK, and return the
    wrapped value as a str suitable for storage in GoogleDriveConfig.master_key_wrapped.
    """
    mek = Fernet.generate_key()          # 32 bytes, url-safe-base64 encoded
    wrapped = _kek_fernet().encrypt(mek) # Fernet token (bytes)
    return base64.b64encode(wrapped).decode("ascii")


def unwrap_key(master_key_wrapped: str) -> Fernet:
    """
    Unwrap the stored MEK and return a ready-to-use Fernet instance.

    Raises:
        ValueError  – if master_key_wrapped is empty or malformed.
        InvalidToken – if SECRET_KEY has changed since the key was wrapped.
    """
    if not master_key_wrapped:
        raise ValueError("No master_key_wrapped set — generate a key first.")
    try:
        wrapped_bytes = base64.b64decode(master_key_wrapped.encode("ascii"))
        mek = _kek_fernet().decrypt(wrapped_bytes)
        return Fernet(mek)
    except (InvalidToken, Exception) as exc:
        raise ValueError(
            "Failed to unwrap MEK. The SECRET_KEY may have changed since the key was "
            "generated, or the stored value is corrupt."
        ) from exc


# ── File-level encrypt / decrypt ──────────────────────────────────────────────

def encrypt_file(src_path: str, dst_path: str, master_key_wrapped: str) -> None:
    """
    Read src_path, encrypt with MEK, write Fernet token to dst_path.
    src_path and dst_path may be the same (in-place).
    """
    f = unwrap_key(master_key_wrapped)
    with open(src_path, "rb") as fh:
        plaintext = fh.read()
    ciphertext = f.encrypt(plaintext)
    with open(dst_path, "wb") as fh:
        fh.write(ciphertext)


def decrypt_file_to_bytes(path: str, master_key_wrapped: str) -> bytes:
    """
    Read an encrypted file and return plaintext bytes (never written to disk).
    """
    f = unwrap_key(master_key_wrapped)
    with open(path, "rb") as fh:
        ciphertext = fh.read()
    return f.decrypt(ciphertext)


def is_fernet_token(path: str) -> bool:
    """
    Quick heuristic: Fernet tokens start with 'gAAAAA' when the file is
    base64-encoded.  Used to detect legacy un-encrypted files.
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(6)
        return header == b"gAAAAA"
    except OSError:
        return False
