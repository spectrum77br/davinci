"""O repasse do DaVinci para o painel da API do app (contrato, seção 6).

/api/app-uranyx/<resto> → {APP_URANYX_API_URL}/admin/<resto>, SÓ quando o 1º
pedaço de <resto> está em `ROTAS`. O token da equipe entra aqui, no servidor; do
pedido do navegador só passam o corpo e o `Content-Type` (nunca o cookie do
DaVinci nem outro cabeçalho).

O caminho vai CRU (ainda codificado): o código-base de SKU viaja no caminho com
`encodeURIComponent` e pode ter barra ("dg300/azul" → `dg300%2Fazul`), espaço
ou acento. Pedaço "." ou ".." (também codificado, `%2e%2e`) é recusado: o httpx
resolveria o ".." e o pedido sairia de /admin.

Respostas: o status e o corpo da API do app passam como vieram (o erro é
`{erro, mensagem, campos}`), menos estes casos, que viram `Indisponivel` (503
`app_uranyx_indisponivel` no router):
  • sem configuração (URL ou token vazios, URL sem http/https);
  • sem conexão ou sem resposta no tempo (10 s; 60 s para upload e sincronizar);
  • 401 da API do app (ela recusou o NOSSO token: é configuração, e um 401
    chegando ao navegador pareceria a sessão do DaVinci caindo) e o 429
    `bloqueado_temporariamente` que vem depois de 10 desses 401 (a API do app
    bloqueia o IP deste servidor por 15 min: é o mesmo token errado, não
    "muitas tentativas" de quem está na tela);
  • 404 que não é JSON (a API do app sempre responde 404 em JSON: sem JSON é
    o Caddy do endereço PÚBLICO, que esconde o /admin — APP_URANYX_API_URL
    errada) e 502/503/504 que não são JSON (o proxy na frente dela dizendo que
    ela caiu).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

# O 1º pedaço do caminho que pode ser repassado (contrato, seção 6). O resto da
# API do app — inclusive o próprio painel fora desta lista — fica inacessível.
ROTAS = frozenset(
    {
        "catalogo",
        "skus-sem-mapa",
        "skus-ignorados",
        "arquivos",
        "manuais",
        "receitas",
        "apps",
        "contas",
        "reclamacoes",
        "fila",
    }
)

TEMPO_S = 10.0
TEMPO_LONGO_S = 60.0
# Rotas que demoram de propósito: a cópia do catálogo lê o site e confere no
# DaVinci antes de responder.
ROTAS_LONGAS = frozenset({"catalogo/sincronizar"})

# Da resposta da API do app só voltam cabeçalhos de DADO (nunca Set-Cookie,
# Server etc.). O Content-Length o próprio DaVinci recalcula.
CABECALHOS_RESPOSTA = (
    "content-type",
    "content-disposition",
    "retry-after",
    "cache-control",
    "etag",
)
# Content-Type fora do ASCII (o Starlette só codifica latin-1: acima disso, 500)
# vira este; o nosniff do router impede o navegador de adivinhar outro.
TIPO_SEGURO = "application/octet-stream"

MSG_SEM_CONFIGURACAO = (
    "O módulo App Uranyx não está configurado neste servidor "
    "(APP_URANYX_API_URL e APP_URANYX_ADMIN_TOKEN)."
)
MSG_FORA_DO_AR = "A API do app Uranyx está fora do ar. Tente de novo em alguns minutos."
MSG_DEMOROU = (
    "A API do app Uranyx não respondeu a tempo. Se era uma alteração, confira se ela "
    "foi feita antes de repetir."
)
MSG_DAVINCI_RECUSADO = (
    "A API do app Uranyx recusou o token do DaVinci (APP_URANYX_ADMIN_TOKEN). "
    'Ele é o token "uxadm_…" (não o sha256 dele, que fica na API do app). Depois de 10 '
    "tentativas erradas, a API do app bloqueia este servidor por 15 min (o token certo "
    "passa mesmo assim). Avise quem cuida do servidor."
)
MSG_ENDERECO_ERRADO = (
    "O endereço da API do app Uranyx (APP_URANYX_API_URL) não responde ao painel. "
    "Em produção vai o endereço interno http://uranyx-api:8000/api/app/v1, não o público. "
    "Avise quem cuida do servidor."
)
# O erro do 429 que a API do app dá ao IP bloqueado por token errado.
ERRO_BLOQUEADO = "bloqueado_temporariamente"


class Indisponivel(Exception):  # noqa: N818 — o nome é o do código de erro
    """A API do app não pode atender: vira 503 `app_uranyx_indisponivel`."""

    def __init__(self, mensagem: str) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem


@dataclass(frozen=True)
class Resposta:
    status: int
    conteudo: bytes
    cabecalhos: dict[str, str]


def caminho_permitido(caminho_cru: str) -> bool:
    """`caminho_cru`: o que vem depois de /api/app-uranyx/, ainda codificado.

    O 1º pedaço tem de estar em `ROTAS` exatamente como veio (sem codificação:
    `cat%61logo` não vale) e nenhum pedaço, decodificado, pode ser "." ou "..".
    """
    if not caminho_cru:
        return False
    if caminho_cru.split("/", 1)[0] not in ROTAS:
        return False
    return not any(p in (".", "..") for p in unquote(caminho_cru).split("/"))


def e_upload(tipo: str | None) -> bool:
    return (tipo or "").split(";", 1)[0].strip().lower() == "multipart/form-data"


def e_json(tipo: str | None) -> bool:
    base = (tipo or "").split(";", 1)[0].strip().lower()
    return base == "application/json" or base.endswith("+json")


def tempo_limite(caminho_cru: str, tipo: str | None) -> float:
    if e_upload(tipo) or unquote(caminho_cru).strip("/") in ROTAS_LONGAS:
        return TEMPO_LONGO_S
    return TEMPO_S


def _configuracao() -> tuple[str, str]:
    s = get_settings()
    base = (s.app_uranyx_api_url or "").strip().rstrip("/")
    token = s.app_uranyx_admin_token.get_secret_value().strip()
    if not base or not token or not base.lower().startswith(("http://", "https://")):
        raise Indisponivel(MSG_SEM_CONFIGURACAO)
    return base, token


def _cliente(tempo: float) -> httpx.AsyncClient:
    """Um cliente por pedido, como no resto do DaVinci. Os testes trocam este."""
    return httpx.AsyncClient(timeout=tempo, follow_redirects=False)


def _caiu(r: httpx.Response) -> bool:
    return r.status_code in (502, 503, 504) and not e_json(r.headers.get("content-type"))


def _token_recusado(r: httpx.Response) -> bool:
    """401, ou o 429 `bloqueado_temporariamente` que vem depois de 10 deles (hoje o único
    429 do /admin da API do app). Qualquer outro 429, se um dia existir, passa como veio."""
    if r.status_code == 401:
        return True
    if r.status_code != 429 or not e_json(r.headers.get("content-type")):
        return False
    try:
        dados = r.json()
    except ValueError:
        return False
    return isinstance(dados, dict) and dados.get("erro") == ERRO_BLOQUEADO


def _endereco_errado(r: httpx.Response) -> bool:
    """404 sem JSON: não foi a API do app (o 404 dela é `nao_encontrado` em JSON)."""
    return r.status_code == 404 and not e_json(r.headers.get("content-type"))


def _cabecalhos_resposta(r: httpx.Response) -> dict[str, str]:
    """Os `CABECALHOS_RESPOSTA` que vieram. Valor fora do ASCII não volta (no
    Content-Type, vira `TIPO_SEGURO`)."""
    saida: dict[str, str] = {}
    for k in CABECALHOS_RESPOSTA:
        valor = r.headers.get(k)
        if valor is None:
            continue
        if valor.isascii():
            saida[k] = valor
        elif k == "content-type":
            saida[k] = TIPO_SEGURO
    return saida


async def repassar(
    metodo: str,
    caminho_cru: str,
    query: str,
    *,
    corpo: bytes = b"",
    tipo: str | None = None,
) -> Resposta:
    """Faz o pedido no painel da API do app e devolve a resposta dela.

    Quem chama já conferiu o acesso, o `caminho_permitido` e o tamanho do corpo.
    """
    base, token = _configuracao()
    url = f"{base}/admin/{caminho_cru}"
    if query:
        url = f"{url}?{query}"
    cabecalhos = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if corpo and tipo:
        cabecalhos["Content-Type"] = tipo
    tempo = tempo_limite(caminho_cru, tipo)
    try:
        async with _cliente(tempo) as c:
            r = await c.request(metodo, url, content=corpo or None, headers=cabecalhos)
    except httpx.TimeoutException:
        logger.warning("app_uranyx_sem_resposta", metodo=metodo, tempo_s=tempo)
        raise Indisponivel(MSG_DEMOROU) from None
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        # Só o tipo do erro: a mensagem do httpx pode trazer a URL interna.
        logger.warning("app_uranyx_fora_do_ar", metodo=metodo, erro=type(e).__name__)
        raise Indisponivel(MSG_FORA_DO_AR) from None
    if _token_recusado(r):
        logger.error("app_uranyx_token_recusado", status=r.status_code)
        raise Indisponivel(MSG_DAVINCI_RECUSADO)
    if _endereco_errado(r):
        logger.error("app_uranyx_endereco_errado", metodo=metodo, status=r.status_code)
        raise Indisponivel(MSG_ENDERECO_ERRADO)
    if _caiu(r):
        logger.warning("app_uranyx_fora_do_ar", metodo=metodo, status=r.status_code)
        raise Indisponivel(MSG_FORA_DO_AR)
    return Resposta(status=r.status_code, conteudo=r.content, cabecalhos=_cabecalhos_resposta(r))
