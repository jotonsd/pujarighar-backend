from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        raise RuntimeError('FIELD_ENCRYPTION_KEY is not configured')
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(raw: str) -> str:
    if not raw:
        return ''
    return _fernet().encrypt(raw.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    if not encrypted:
        return ''
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken:
        return ''
