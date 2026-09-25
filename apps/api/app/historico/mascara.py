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


# Cada regra em duas versões, Python (corpo do pedido) e Postgres (gatilho):
# (regex Python, troca Python, padrão Postgres, troca Postgres, flags Postgres).
# O SQL do gatilho é GERADO desta lista (sql.py) e o teste confere que as duas
# dão o mesmo resultado. No Postgres: \m = começo de palavra (\b lá é
# backspace) e sem "(?:" — o text() do SQLAlchemy leria ":nome" como parâmetro.
_PALAVRAS_SENHA = "segredo do cadeado|c[oó]digo do cadeado|senha|password|passwd|pwd|segredo|pin"
_PALAVRAS_SEPARADOR = "segredo do cadeado|c[oó]digo do cadeado|senha|password|passwd|pwd|segredo"
_CHAVES_URL = (
    "access_token|refresh_token|input_token|client_secret|partner_key|app_secret|code|state"
    "|sign|signature|token|key|password|passwd|senha|api_key|apikey|secret"
)
_CHAVES_JSON = "password|passwd|senha|secret|client_secret|token|access_token|refresh_token|api_key|apikey"

REGRAS_TEXTO = [
    # "token": vale também para nome/rótulo (JWT, link com chave…).
    # "palavra": só para texto livre — num nome de produto "Camiseta Basic
    # Feminina" ou "PIN-001" ela esconderia o que a busca precisa achar.
    # Authorization: Bearer <token> / Basic <base64>
    (r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer ***",
     r"\mbearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer ***", "gi", "token"),
    (r"(authorization\W{0,3}basic\s+)[A-Za-z0-9+/=]{8,}", r"\1***",
     r"(authorization[^[:alnum:]]{0,3}basic[[:space:]]+)[A-Za-z0-9+/=]{8,}", r"\1***", "gi", "token"),
    (r"\bbasic\s+(?=[A-Za-z0-9+/=]*[0-9+/=])[A-Za-z0-9+/=]{12,}", "Basic ***",
     r"\mbasic[[:space:]]+(?=[A-Za-z0-9+/=]*[0-9+/=])[A-Za-z0-9+/=]{12,}", "Basic ***", "gi", "token"),
    # ?code=…&state=…&password=…
    (r"([?&](?:" + _CHAVES_URL + r")=)[^&#\s]+", r"\1***",
     r"([?&](" + _CHAVES_URL + r")=)[^&#[:space:]]+", r"\1***", "gi", "token"),
    # socks5://usuario:senha@host
    (r"://[^/\s:@]+:[^/\s@]+@", "://***:***@",
     r"://[^/[:space:]:@]+:[^/[:space:]@]+@", "://***:***@", "g", "token"),
    # JWT e token do Mercado Livre
    (r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}", "***",
     r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}", "***", "g", "token"),
    (r"(APP_USR|TG)-[A-Za-z0-9-]{10,}", r"\1-***",
     r"(APP_USR|TG)-[A-Za-z0-9-]{10,}", r"\1-***", "g", "token"),
    # JSON colado em texto: "api_key": "abc"
    (r'("(?:' + _CHAVES_JSON + r')"\s*:\s*)"[^"]*"', r'\1"***"',
     r'("(' + _CHAVES_JSON + r')"[[:space:]]*:[[:space:]]*)"[^"]*"', r'\1"***"', "gi", "token"),
    # "senha: Abacaxi!", "Password = Loja2024" — com ":" ou "=" esconde o que
    # vier, mesmo sem número (até 3 palavras no meio). Não vale para "pin"
    # nem para "senha extra" (nome da trava das telas).
    (r"\b(" + _PALAVRAS_SEPARADOR + r")(?!\s+extra\b)((?:\s+[^\W\d_]+){0,3}\s*[:=]\s*)[^\s,;]{2,}",
     r"\1\2***",
     r"\m(" + _PALAVRAS_SEPARADOR + r")(?![[:space:]]+extra\M)(([[:space:]]+[[:alpha:]]+){0,3}[[:space:]]*[:=][[:space:]]*)[^[:space:],;]{2,}",
     r"\1\2***", "gi", "palavra"),
    # "a senha é 4821", "senha 4821", "pin: 1234" — sem ":" só quando o valor
    # tem número, para "Senha extra liberada" continuar legível
    (r"\b(" + _PALAVRAS_SENHA + r")((?:\s+[^\W\d_]+){0,3}\s*(?:é|:|=|-)?\s*)(?=[^\s,.;]*\d)[^\s,;]{3,}",
     r"\1\2***",
     r"\m(" + _PALAVRAS_SENHA + r")(([[:space:]]+[[:alpha:]]+){0,3}[[:space:]]*(é|:|=|-)?[[:space:]]*)(?=[^[:space:],.;]*[0-9])[^[:space:],;]{3,}",
     r"\1\2***", "gi", "palavra"),
]
_TEXTO = [
    (re.compile(py, re.I if "i" in flags else 0), troca)
    for py, troca, _pg, _tpg, flags, _tipo in REGRAS_TEXTO
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
