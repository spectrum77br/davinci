"""Threema Gateway (Basic mode) — envia notificação de um caso de logística.

Basic mode: o servidor do Threema criptografa a mensagem pro destinatário; a
gente só faz um POST com `from`/`to`/`text`/`secret`. Uma chamada por
destinatário (Basic não faz broadcast). Config vem do `.env`
(`threema_gateway_id`/`threema_gateway_secret`/`threema_recipients`); sem isso
`send_to_all` levanta ThreemaConfigError.

Doc: https://gateway.threema.ch/en/developer/api — `POST /send_simple`
(form-urlencoded). Sucesso = 200 com o message id no corpo. Erros mapeados:
401 auth, 402 sem crédito, 404 destinatário inexistente, 413 texto grande.
"""

from __future__ import annotations

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

THREEMA_API_BASE = "https://msgapi.threema.ch"
# Basic mode aceita até 3500 bytes de texto por mensagem.
_MAX_TEXT_BYTES = 3500


class ThreemaConfigError(RuntimeError):
    """Gateway ID / secret / destinatários ausentes."""


class ThreemaSendError(RuntimeError):
    """Falha ao enviar uma mensagem (status != 200)."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"threema_send_{status}: {body[:120]}")


def parse_recipients(raw: str | None) -> list[str]:
    """IDs separados por vírgula/espaço/; → lista limpa (8 chars, upper)."""
    if not raw:
        return []
    out: list[str] = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        rid = part.strip().upper()
        if rid:
            out.append(rid)
    return out


class ThreemaClient:
    def __init__(
        self,
        gateway_id: str | None = None,
        secret: str | None = None,
    ) -> None:
        s = get_settings()
        self.gateway_id = (gateway_id or s.threema_gateway_id or "").strip()
        self.secret = (secret or s.threema_gateway_secret or "").strip()

    def _require_config(self) -> None:
        if not self.gateway_id:
            raise ThreemaConfigError("threema_gateway_id_missing")
        if not self.secret:
            raise ThreemaConfigError("threema_gateway_secret_missing")

    async def send_simple(self, to: str, text: str) -> str:
        """Envia pra 1 destinatário. Retorna o message id do Threema."""
        self._require_config()
        body = text.encode("utf-8")[:_MAX_TEXT_BYTES].decode("utf-8", "ignore")
        payload = {
            "from": self.gateway_id,
            "to": to.strip().upper(),
            "text": body,
            "secret": self.secret,
        }
        async with httpx.AsyncClient(timeout=15) as cli:
            resp = await cli.post(f"{THREEMA_API_BASE}/send_simple", data=payload)
        if resp.status_code != 200:
            logger.warning(
                "threema_send_failed", status=resp.status_code, body=resp.text[:200]
            )
            raise ThreemaSendError(resp.status_code, resp.text)
        return resp.text.strip()

    async def send_to_all(
        self, text: str, recipients: list[str] | None = None
    ) -> dict[str, list[str]]:
        """Envia o mesmo texto pra cada destinatário configurado.

        Retorna `{"sent": [ids ok], "failed": [ids que falharam]}`. Não
        interrompe no 1º erro — tenta todos.
        """
        self._require_config()
        alvos = recipients or parse_recipients(get_settings().threema_recipients)
        if not alvos:
            raise ThreemaConfigError("threema_recipients_missing")
        sent: list[str] = []
        failed: list[str] = []
        for rid in alvos:
            try:
                mid = await self.send_simple(rid, text)
                sent.append(rid)
                logger.info("threema_sent", to=rid, message_id=mid)
            except ThreemaSendError as e:
                failed.append(rid)
                logger.warning("threema_recipient_failed", to=rid, status=e.status)
        return {"sent": sent, "failed": failed}
