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


# Imagens inline (assinatura das marcas): {content_id: (mime, bytes)} — no
# HTML referencia-se `cid:<content_id>`. Vazio/None = e-mail sem imagem.
InlineImages = dict[str, tuple[str, bytes]]

# Anexos de verdade: [(nome do arquivo, mime, bytes)]. Diferente do inline, o
# arquivo aparece como anexo e NÃO precisa de HTML — é o único jeito de mandar
# imagem na mensagem ao comprador da Amazon, que sai em texto puro.
Attachments = list[tuple[str, str, bytes]]


class EmailSender(Protocol):
    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        from_email: str | None = None,
        from_name: str | None = None,
        reply_to: tuple[str, str] | None = None,
        inline_images: InlineImages | None = None,
        attachments: Attachments | None = None,
    ) -> None: ...


class ConsoleEmailSender:
    """Dev fallback. Logs the email envelope to stdout (never the HTML: os
    padrões de e-mail das marcas podem carregar dados de cliente)."""

    name = "console"

    async def send(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html: str = "",
        from_email: str | None = None,
        from_name: str | None = None,
        reply_to: tuple[str, str] | None = None,
        inline_images: InlineImages | None = None,
        attachments: Attachments | None = None,
    ) -> None:
        logger.info(
            "email_console_send",
            to=to,
            subject=subject,
            text=text,
            from_email=from_email,
            from_name=from_name,
            reply_to=reply_to,
            inline_images=sorted((inline_images or {}).keys()),
            attachments=[nome for nome, _mime, _raw in (attachments or [])],
            note="Mailjet keys missing — printing instead.",
        )


class MailjetEmailSender:
    URL = "https://api.mailjet.com/v3.1/send"
    name = "mailjet"

    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        from_email: str | None = None,
        from_name: str | None = None,
        reply_to: tuple[str, str] | None = None,
        inline_images: InlineImages | None = None,
        attachments: Attachments | None = None,
    ) -> None:
        default_addr, default_name = _parse_from(_settings.email_from, _settings.email_from_name)
        # Remetente explícito por marca (sac@marca): o endereço/domínio precisa
        # estar validado na conta Mailjet, senão a API recusa (o caller mostra
        # o erro). O padrão é sair do EMAIL_FROM com o NOME da marca e
        # Reply-To no e-mail do SAC — funciona sem validar domínio nenhum.
        from_addr = (from_email or "").strip() or default_addr
        from_name_final = (from_name or "").strip() or default_name
        message: dict = {
            "From": {"Email": from_addr, "Name": from_name_final},
            "To": [{"Email": to}],
            "Subject": subject,
            "TextPart": text,
        }
        # Mensagem ao comprador da Amazon vai em texto puro (a Amazon recusa
        # HTML): sem html, o e-mail sai só com TextPart.
        if html:
            message["HTMLPart"] = html
        if reply_to and reply_to[0]:
            message["ReplyTo"] = {"Email": reply_to[0], "Name": reply_to[1] or from_name_final}
        if inline_images:
            message["InlinedAttachments"] = [
                {
                    "ContentType": mime,
                    "Filename": f"{cid}.{_ext(mime)}",
                    "ContentID": cid,
                    "Base64Content": b64encode(data).decode(),
                }
                for cid, (mime, data) in inline_images.items()
            ]
        if attachments:
            message["Attachments"] = [
                {
                    "ContentType": mime,
                    "Filename": nome,
                    "Base64Content": b64encode(raw).decode(),
                }
                for nome, mime, raw in attachments
            ]
        payload = {"Messages": [message]}
        auth_raw = f"{_settings.mailjet_api_key}:{_settings.mailjet_secret_key}".encode()
        headers = {
            "Authorization": f"Basic {b64encode(auth_raw).decode()}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(self.URL, json=payload, headers=headers)
            r.raise_for_status()
        logger.info("email_mailjet_sent", to=to, subject=subject)


def _ext(mime: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}.get(
        mime, "bin"
    )


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
