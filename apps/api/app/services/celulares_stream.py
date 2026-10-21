"""Ingresso de uso único para a tela; a chave nunca é a do login do DaVinci."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from urllib.parse import urlsplit

from app.config import get_settings


def _secret(value: object) -> str:
    getter = getattr(value, "get_secret_value", None)
    raw = getter() if callable(getter) else value
    return raw if isinstance(raw, str) else ""


def _https_origin(value: object) -> str | None:
    # urlsplit removes some control characters, so validate the original input.
    if (not isinstance(value, str) or not value.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or "\\" in value or "%" in value):
        return None
    try:
        url = urlsplit(value)
        host = url.hostname
        if (url.scheme != "https" or not host or url.username is not None
                or url.password is not None or url.path not in {"", "/"}
                or url.query or url.fragment or url.port not in (None, 443)):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if (len(host) > 253 or any(not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label,
            ) for label in host.split("."))):
                return None
            authority = host
        else:
            authority = f"[{address}]" if address.version == 6 else str(address)
    except ValueError:
        return None
    return "https://" + authority


def configuration() -> tuple[str, str] | None:
    settings = get_settings()
    origin = _https_origin(getattr(settings, "celulares_stream_origin", ""))
    key = _secret(getattr(settings, "celulares_stream_key", ""))
    login_key = _secret(getattr(settings, "jwt_secret", ""))
    if (origin is None or len(key.encode()) < 32
            or hmac.compare_digest(key.encode(), login_key.encode())):
        return None
    return origin, key


def issue_ticket(*, user_id: str, phone: str, udid: str, key: str, now: int | None = None) -> str:
    if (not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", user_id)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", phone)
            or not re.fullmatch(r"127\.0\.0\.1:[1-9][0-9]{3,4}", udid)
            or int(udid.rsplit(":", 1)[1]) > 65535 or len(key.encode()) < 32):
        raise ValueError("invalid stream configuration")
    issued = int(time.time()) if now is None else now
    payload = {"aud": "davinci-celulares", "sub": user_id, "phone": phone, "udid": udid,
               "iat": issued, "exp": issued + 60, "jti": secrets.token_urlsafe(24)}

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    body = "v1." + encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = encode(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())
    return body + "." + signature
