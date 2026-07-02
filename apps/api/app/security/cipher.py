import base64
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

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


def encrypt_json(payload: dict) -> bytes:
    """Encrypt a JSON-serializable dict to raw bytes (`nonce || ct`).

    Used for `integrations.credentials` (BYTEA column).
    """
    aes = AESGCM(_KEY)
    nonce = os.urandom(12)
    pt = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ct = aes.encrypt(nonce, pt, None)
    return nonce + ct


def decrypt_json(blob: bytes) -> dict:
    if len(blob) <= 12:
        raise ValueError("ciphertext_too_short")
    aes = AESGCM(_KEY)
    nonce, ct = blob[:12], blob[12:]
    return json.loads(aes.decrypt(nonce, ct, None).decode())


def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt raw bytes to `nonce || ct` (for BYTEA columns — e.g. arquivos
    de certificado digital .p12/.pfx e a senha do certificado)."""
    aes = AESGCM(_KEY)
    nonce = os.urandom(12)
    return nonce + aes.encrypt(nonce, data, None)


def decrypt_bytes(blob: bytes) -> bytes:
    if len(blob) <= 12:
        raise ValueError("ciphertext_too_short")
    aes = AESGCM(_KEY)
    nonce, ct = blob[:12], blob[12:]
    return aes.decrypt(nonce, ct, None)
