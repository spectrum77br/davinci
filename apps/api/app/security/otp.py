import secrets

import bcrypt

from app.config import get_settings

_settings = get_settings()

# Avoid 0/O/1/I/L
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_code(length: int | None = None) -> str:
    n = length or _settings.otp_code_len
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def generate_prefix(length: int | None = None) -> str:
    n = length or _settings.otp_prefix_len
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def generate_nonce() -> str:
    return secrets.token_urlsafe(32)


def hash_code(code: str) -> str:
    return bcrypt.hashpw(code.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_code(code: str, code_hash: str) -> bool:
    try:
        return bcrypt.checkpw(code.encode(), code_hash.encode())
    except ValueError:
        return False
