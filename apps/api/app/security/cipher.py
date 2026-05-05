import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from app.config import get_settings

_settings = get_settings()


def _derive_key(salt: bytes = b"davinci-credentials-v1") -> bytes:
    raw = _settings.credentials_key.encode()
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"creds")
    return hkdf.derive(raw)


_KEY = _derive_key()


def encrypt(plaintext: str) -> str:
    aes = AESGCM(_KEY)
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(token: str) -> str:
    raw = base64.urlsafe_b64decode(token.encode())
    nonce, ct = raw[:12], raw[12:]
    aes = AESGCM(_KEY)
    return aes.decrypt(nonce, ct, None).decode()
