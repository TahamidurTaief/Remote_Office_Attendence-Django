import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

_BANK_SALT = b"fieldtrack-bank-account-v1"
_BANK_ITERATIONS = 150_000


def _derive_bank_kek() -> bytes:
    secret = settings.SECRET_KEY
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", secret, _BANK_SALT, _BANK_ITERATIONS, dklen=32)
    return base64.urlsafe_b64encode(dk)


def _bank_fernet() -> Fernet:
    return Fernet(_derive_bank_kek())


def normalize_account_number(account_number: str) -> str:
    if not account_number:
        return ""
    # Strip spaces and dashes
    return str(account_number).strip().replace(" ", "").replace("-", "")


def mask_account_number(account_number: str) -> str:
    cleaned = normalize_account_number(account_number)
    if not cleaned:
        return ""
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    return f"{'*' * (len(cleaned) - 4)}{cleaned[-4:]}"


def encrypt_account_number(account_number: str) -> str:
    cleaned = normalize_account_number(account_number)
    if not cleaned:
        return ""
    fernet = _bank_fernet()
    token = fernet.encrypt(cleaned.encode("utf-8"))
    return token.decode("ascii")


def decrypt_account_number(encrypted_token: str) -> str:
    if not encrypted_token:
        return ""
    try:
        fernet = _bank_fernet()
        decrypted = fernet.decrypt(encrypted_token.encode("ascii"))
        return decrypted.decode("utf-8")
    except (InvalidToken, Exception):
        # Fallback if unencrypted legacy token was passed
        return encrypted_token
