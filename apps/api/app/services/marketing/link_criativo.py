"""Link público temporário do vídeo do criativo (Eduardo, 15/09/2026).

Por que existe: a API do Instagram, na trilha "Instagram Login" (a que vale
quando a marca NÃO tem Página do Facebook), publica Reels apontando para uma
URL do vídeo — a Meta baixa o arquivo (`video_url`; a doc é explícita: "we
cURL the video using the passed-in URL, so it must be on a public server").
O download normal do criativo (routers/marketing_creatives.py) exige sessão
de usuário, então a Meta não conseguiria puxar.

Desenho (mesmo molde de services/aprovar_link.py, que já faz isso pro link de
aprovar margem no Threema): token HMAC-SHA256 com a chave `jwt_secret`,
truncado a 128 bits, sobre "criativo-video:{file_id}:{exp}". Sem estado no
banco.

Cuidados, porque aqui o conteúdo é criativo NÃO lançado:
  • validade CURTA (VALIDADE_S, 15 min) — só o tempo de a Meta baixar;
  • o link é gerado só no momento de publicar, nunca fica guardado;
  • o endpoint devolve o arquivo e nada mais (sem listagem, sem metadado);
  • cada acesso vira log (quem baixou, quando).
Quando a marca tiver Página do Facebook, o publicador usa o upload BINÁRIO
(resumable) e este link deixa de ser usado — é o caminho preferido.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from uuid import UUID

from app.config import get_settings

# 15 minutos: a Meta baixa o vídeo em segundos; o resto é margem pra fila.
VALIDADE_S = 15 * 60

_PREFIXO = "criativo-video"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _assinar(payload: str) -> bytes:
    chave = get_settings().jwt_secret.encode()
    return hmac.new(chave, f"{_PREFIXO}:{payload}".encode(), hashlib.sha256).digest()[:16]


def gerar_token(file_id: UUID | str, *, agora: int | None = None) -> str:
    exp = (int(time.time()) if agora is None else agora) + VALIDADE_S
    payload = f"{file_id}:{exp}"
    return f"{_b64e(payload.encode())}.{_b64e(_assinar(payload))}"


def validar_token(token: str) -> UUID | None:
    """Devolve o file_id se o token é autêntico e não venceu; senão None."""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64d(payload_b64).decode()
        sig = _b64d(sig_b64)
        file_id_s, exp_s = payload.rsplit(":", 1)
        exp = int(exp_s)
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(sig, _assinar(payload)):
        return None
    if time.time() > exp:
        return None
    try:
        return UUID(file_id_s)
    except ValueError:
        return None


def url_video(file_id: UUID | str) -> str:
    """URL absoluta que a Meta vai baixar. Usa APP_URL (o domínio público que
    o Caddy serve); em dev local a Meta não alcança — por isso o publicador
    local roda em modo seco."""
    base = get_settings().app_url.rstrip("/")
    # O prefixo do router é em inglês (`creatives`) — errar aqui faz a Meta
    # receber 404 e o Reel nunca sair.
    return f"{base}/api/marketing/creatives/video/{gerar_token(file_id)}"
