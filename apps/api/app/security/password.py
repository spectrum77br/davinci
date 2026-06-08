import base64
import hashlib
import secrets

import bcrypt

# bcrypt silently truncates the input at 72 bytes. We pre-hash the
# password with SHA-256 so the WHOLE password always contributes to the
# final hash regardless of length — base64 of a 32-byte digest is 44
# bytes (well under 72) and contains no NUL bytes (which bcrypt also
# mishandles). Same construction as passlib's `bcrypt_sha256`.


def _prehash(password: str) -> bytes:
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode())
    except ValueError:
        return False


# Pre-computed hash of a random value. Verifying against it lets the login
# path spend the same bcrypt cost when the user does not exist (or has no
# password) as when they do — closing the timing side-channel that would
# otherwise leak which e-mails are registered.
_DECOY_HASH = bcrypt.hashpw(
    _prehash(secrets.token_urlsafe(32)), bcrypt.gensalt(rounds=12)
).decode()


def dummy_verify() -> None:
    """Run a throwaway bcrypt check to equalise login timing for unknown
    users. Result is intentionally discarded."""
    try:
        bcrypt.checkpw(b"decoy", _DECOY_HASH.encode())
    except ValueError:
        pass
