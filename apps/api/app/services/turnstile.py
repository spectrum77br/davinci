import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()
_settings = get_settings()
VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile(token: str | None, *, remote_ip: str | None = None) -> bool:
    if not _settings.turnstile_secret_key:
        return True
    if not token:
        return False
    data = {"secret": _settings.turnstile_secret_key, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.post(VERIFY_URL, data=data)
            j = r.json()
            return bool(j.get("success"))
        except Exception as e:
            logger.warning("turnstile_request_failed", err=str(e))
            return False
