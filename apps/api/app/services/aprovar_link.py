"""Token do link "Aprovar pelo celular" (aviso automático da Margem).

O aviso que o auto-hold manda no Threema traz um link público
`{APP_URL}/api/aprovar/{token}`; abrir mostra a página de confirmação e o
botão Aprovar dispara o mesmo fluxo da aba Margem (routers/aprovar_margem.py).

O token é HMAC-SHA256 (chave = jwt_secret, truncado a 128 bits) sobre
"aprovar-margem:{pedido}:{exp}" — sem estado no banco: vale VALIDADE_S
segundos e aprovar de novo um pedido já aprovado só informa (idempotente).
Só circula dentro do Threema (criptografado) pra quem estiver na lista do
aviso automático.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from app.config import get_settings

# 7 dias — folga pro pedido segurado ser decidido; depois o link "vence" e a
# aprovação volta a ser só pela aba Margem.
VALIDADE_S = 7 * 24 * 3600


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _assinar(payload: str) -> bytes:
    chave = get_settings().jwt_secret.encode()
    msg = f"aprovar-margem:{payload}".encode()
    return hmac.new(chave, msg, hashlib.sha256).digest()[:16]


def gerar_token(pedido: str, *, agora: int | None = None) -> str:
    """Token "payload.assinatura" (base64url sem padding) pro pedido."""
    exp = (int(time.time()) if agora is None else agora) + VALIDADE_S
    payload = f"{pedido}:{exp}"
    return f"{_b64e(payload.encode())}.{_b64e(_assinar(payload))}"


def validar_token(token: str) -> str | None:
    """Devolve o pedido se o token é autêntico e não venceu; senão None."""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64d(payload_b64).decode()
        sig = _b64d(sig_b64)
        pedido, exp_s = payload.rsplit(":", 1)
        exp = int(exp_s)
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(sig, _assinar(payload)):
        return None
    if time.time() > exp:
        return None
    return pedido or None


def url_aprovar(pedido: str) -> str:
    base = get_settings().app_url.rstrip("/")
    return f"{base}/api/aprovar/{gerar_token(pedido)}"
