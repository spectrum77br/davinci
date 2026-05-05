from base64 import b64encode
from pathlib import Path
from typing import Protocol

import httpx
import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import get_settings

logger = structlog.get_logger()
_settings = get_settings()

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "email_templates"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "htm"]),
    enable_async=False,
)


def render_otp_html(*, prefix: str, code: str, ttl_minutes: int) -> str:
    return _env.get_template("otp.html").render(
        prefix=prefix, code=code, ttl_minutes=ttl_minutes, app_name="DaVinci"
    )


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, html: str, text: str) -> None: ...


class ConsoleEmailSender:
    """Dev fallback. Logs full email payload to stdout."""

    async def send(self, *, to: str, subject: str, html: str, text: str) -> None:
        logger.info(
            "email_console_send",
            to=to,
            subject=subject,
            text=text,
            note="Mailjet keys missing — printing instead.",
        )


class MailjetEmailSender:
    URL = "https://api.mailjet.com/v3.1/send"

    async def send(self, *, to: str, subject: str, html: str, text: str) -> None:
        from_addr, from_name = _parse_from(_settings.email_from, _settings.email_from_name)
        payload = {
            "Messages": [{
                "From": {"Email": from_addr, "Name": from_name},
                "To": [{"Email": to}],
                "Subject": subject,
                "TextPart": text,
                "HTMLPart": html,
            }]
        }
        auth_raw = f"{_settings.mailjet_api_key}:{_settings.mailjet_secret_key}".encode()
        headers = {
            "Authorization": f"Basic {b64encode(auth_raw).decode()}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(self.URL, json=payload, headers=headers)
            r.raise_for_status()
        logger.info("email_mailjet_sent", to=to, subject=subject)


def _parse_from(raw: str, default_name: str) -> tuple[str, str]:
    raw = raw.strip()
    if "<" in raw and raw.endswith(">"):
        name, addr = raw.split("<", 1)
        return addr[:-1].strip(), name.strip().strip('"') or default_name
    return raw, default_name


def get_email_sender() -> EmailSender:
    if _settings.mailjet_api_key and _settings.mailjet_secret_key:
        return MailjetEmailSender()
    return ConsoleEmailSender()
