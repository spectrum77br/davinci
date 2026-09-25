"""O que nunca entra no Histórico: senhas, tokens, chaves, arquivos.

A mesma regra existe em SQL (sql.py, `historico_e_segredo` e
`historico_limpa_texto`) para o gatilho do banco; esta é a do corpo dos
pedidos. `tests/test_historico_mascara.py` confere que as duas concordam.

Regra por NOME do campo (levantamento de 25/09/2026 sobre os 1.706 campos de
modelo e 3.178 de schema): pega senha, password, *_enc, *_hash, *_b64, token,
secret, credenciais, cookie etc.; deixa passar o que só FALA do segredo
(token_expires_at, has_senha, senha_status, senha_extra).

Regra por VALOR, para texto livre: em Devoluções há observação com "senha
4821" (senha do aparelho do comprador). Pega também até 3 palavras no meio
("senha do aparelho: 4821"). Só mascara quando o que vem depois tem número,
para "Senha extra" continuar legível.
"""

from __future__ import annotations

import json
import re
from typing import Any

OCULTO = {"_oculto": True}

_LIVRES = {"senha_extra", "session_user"}
_EXATOS = {
    "api_key", "apikey", "partner_key", "private_key", "privatekey", "secret_key",
    "access_key", "x_api_key", "client_secret", "app_secret", "lwa_client_secret",
    "proxy_user", "proxy_username", "card_number", "numero_cartao", "blob", "planilha",
    "planilha_b64", "xml", "logo", "pdf_arquivo", "di_pdf", "simulacao_pdf", "nf_pdf",
}
_NEUTRO = re.compile(
    r"^(has|tem)_|_(at|em|expires_at|expira_em|expires_in|status|origem|erro|error"
    r"|conta_externa|count|ok)$"
)
_PALAVRA = re.compile(
    r"(^|_)(senha|senhas|password|passwd|passphrase|pwd|pass|secret|secrets|segredo|token"
    r"|tokens|cookie|cookies|credential|credentials|credenciais|credencial|otp|totp|mfa|2fa"
    r"|cvv|cvc|nonce|verifier|signature|authorization|auth|jwt|bearer|session|sessionid"
    r"|pin)(_|$)"
)
_SUFIXO = re.compile(r"_(enc|hash|b64|base64)$")


def normalizar(k: str) -> str:
    """refreshToken -> refresh_token ; X-Agent-Token -> x_agent_token"""
    k = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", k)
    return re.sub(r"[^A-Za-z0-9]+", "_", k).strip("_").lower()


def e_segredo(nome: str) -> bool:
    n = normalizar(nome)
    if n in _LIVRES:
        return False
    if n in _EXATOS:
        return True
    if _NEUTRO.search(n):
        return False
    return bool(_PALAVRA.search(n) or _SUFIXO.search(n))


_TEXTO = [
    (re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I), "Bearer ***"),
    (
        re.compile(
            r"([?&](?:access_token|refresh_token|input_token|client_secret|partner_key|app_secret"
            r"|code|state|sign|signature|token|key)=)[^&#\s]+",
            re.I,
        ),
        r"\1***",
    ),
    (re.compile(r"://[^/\s:@]+:[^/\s@]+@"), "://***:***@"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), "***"),
    (re.compile(r"(APP_USR|TG)-[A-Za-z0-9-]{10,}"), r"\1-***"),
    (
        re.compile(
            r"\b(segredo do cadeado|c[oó]digo do cadeado|senha|segredo|pin)"
            r"((?:\s+[^\W\d_]+){0,3}\s*(?:é|:|=|-)?\s*)(?=[^\s,.;]*\d)[^\s,;]{3,}",
            re.I,
        ),
        r"\1\2***",
    ),
]


def limpar_texto(t: str) -> str:
    for padrao, troca in _TEXTO:
        t = padrao.sub(troca, t)
    return t


LIMITE_TEXTO = 500
LIMITE_CORPO = 8000


def limpar(valor: Any, nome: str | None = None) -> Any:
    """Copia o valor sem segredos: campo secreto vira OCULTO (ou None, se já
    era vazio — assim dá para dizer "removida"), texto longo é cortado."""
    if nome is not None and e_segredo(nome):
        return None if valor in (None, "") else OCULTO
    if isinstance(valor, dict):
        return {str(k): limpar(v, str(k)) for k, v in valor.items()}
    if isinstance(valor, list):
        return [limpar(v) for v in valor[:200]]
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return {"_arquivo": True, "bytes": len(valor)}
    if isinstance(valor, str):
        t = limpar_texto(valor)
        return t if len(t) <= LIMITE_TEXTO else t[:LIMITE_TEXTO] + "…"
    return valor


def resumir_corpo(corpo: bytes, content_type: str) -> Any:
    """Resumo do corpo do pedido para o evento. Formulário com arquivo nunca é
    aberto: guarda só o tamanho."""
    if not corpo:
        return None
    ct = (content_type or "").lower()
    if "multipart/" in ct:
        return {"_formulario": True, "bytes": len(corpo)}
    if "json" not in ct:
        return {"_corpo": True, "bytes": len(corpo)}
    try:
        dado = json.loads(corpo)
    except ValueError:
        return {"_corpo": True, "bytes": len(corpo)}
    limpo = limpar(dado)
    if len(json.dumps(limpo, ensure_ascii=False, default=str)) > LIMITE_CORPO:
        return {"_longo": True, "bytes": len(corpo)}
    return limpo
