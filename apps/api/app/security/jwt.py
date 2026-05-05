from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt

from app.config import get_settings

_settings = get_settings()
ALGO = "HS256"


def issue_session_token(*, sub: str, role: str) -> tuple[str, datetime, str]:
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=_settings.jwt_ttl_seconds)
    jti = uuid4().hex
    payload = {
        "sub": sub,
        "role": role,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, _settings.jwt_secret, algorithm=ALGO)
    return token, exp, jti


def decode_session_token(token: str) -> dict:
    return jwt.decode(token, _settings.jwt_secret, algorithms=[ALGO])
