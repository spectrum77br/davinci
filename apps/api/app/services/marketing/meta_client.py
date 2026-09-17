"""Graph API da Meta — publica Reels no Facebook e no Instagram.

Eduardo, 15/09/2026: "um robô que fará a postagem desses vídeos do criativo
automaticamente". Quem publica é o SERVIDOR, pela API oficial — não o executor
do Mac: a Graph API é aberta (diferente da API de Ads da Shopee, que o
`apps/executor` existe pra contornar) e o vídeo já está no disco, em
`settings.uploads_dir`.

Este módulo é só o BRAÇO HTTP: não abre sessão de banco, não decide nada, não
retenta. Quem decide o que publicar, quando e o que fazer com a falha é o
serviço `postagens.py` + os crons do worker. Assim o cliente pode ser fakado
inteiro nos testes (respx) sem tocar no banco.

Três regras que valem pro arquivo todo:

1. **O token NUNCA aparece.** Ele viaja no header `Authorization: OAuth …`
   (nunca na query string — a URL entra em log de exceção do httpx) e todo
   texto que sai daqui — log, `erro` do resultado, mensagem de exceção —
   passa por `_redigir()`, que troca o token por "OAuth ***".
2. **Publicar NÃO é idempotente.** Por isso o `container_id`/`video_id` é
   devolvido ao chamador ASSIM QUE EXISTE (callback `ao_criar_container`),
   antes do passo que publica de verdade: se o worker morrer no meio, a
   reconciliação CONSULTA esse id em vez de tentar de novo às cegas.
3. **Timeout generoso no upload.** O criativo tem 26-41 MB; 30s (o timeout
   das chamadas de controle) derrubaria o envio no meio.

A versão da Graph vem de `settings.meta_graph_version` — um lugar só, porque
subir de versão é uma decisão (a Meta aposenta cada versão em ~2 anos).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

GRAPH_HOST = "https://graph.facebook.com"
# Host da trilha "Instagram API with Instagram Login" (conta SEM Página do
# Facebook). Mesma Graph, host diferente — e um token de uma trilha mandado
# pro host da outra volta "permissão negada", erro que parece conta errada.
GRAPH_HOST_IG = "https://graph.instagram.com"
PROVEDOR_FACEBOOK = "facebook"
PROVEDOR_INSTAGRAM = "instagram"
# Host separado da Meta só pra upload binário (o graph.facebook.com não
# recebe o arquivo): é o mesmo endereço pro Reels do FB e pro Reels do IG,
# mudando só o caminho (video-upload vs ig-api-upload).
RUPLOAD_HOST = "https://rupload.facebook.com"

# Chamadas de controle (start/finish/status/publish) são payloads minúsculos:
# se a Meta não responde em 30s, algo está errado e o próximo tick tenta.
_TIMEOUT_API = httpx.Timeout(30.0, connect=10.0)
# Upload do arquivo: 26-41 MB por criativo. 10 min cobre link ruim COM folga
# dentro do job (timeout de 20 min no cron): se o teto do upload fosse igual
# ao do job, o arq mataria o processo no meio da chamada que publica — e aí
# ninguém saberia se o Reel saiu.
_TIMEOUT_UPLOAD = httpx.Timeout(600.0, connect=15.0)

# Poll do container do Instagram: a Meta transcodifica o Reels de forma
# assíncrona e recomenda consultar no máximo 1×/min. 5 min é o teto do ciclo —
# além disso a postagem volta pro worker como "ainda processando" e a
# reconciliação (que consulta pelo container_id) decide, sem republicar.
IG_POLL_INTERVALO_S = 60.0
IG_POLL_MAX_S = 300.0

# Teto de legenda do Instagram. O router já valida; aqui é cinto de segurança —
# a Meta recusa a chamada INTEIRA por causa de um caractere a mais.
IG_LEGENDA_MAX = 2200

# Janela do agendamento NATIVO do Facebook (`video_state=SCHEDULED`): 10 min a
# 29 dias. Fora disso a Meta recusa — melhor dizer isso em português antes de
# gastar o upload de 41 MB.
FB_AGENDA_MIN = timedelta(minutes=10)
FB_AGENDA_MAX = timedelta(days=29)

PLATAFORMA_FACEBOOK = "facebook"
PLATAFORMA_INSTAGRAM = "instagram"
# As únicas duas que este cliente publica hoje. TikTok (exige consentimento
# humano por upload) e YouTube (exige app verificado) ficaram fora de escopo.
PLATAFORMAS_SUPORTADAS: tuple[str, ...] = (PLATAFORMA_FACEBOOK, PLATAFORMA_INSTAGRAM)


@dataclass(slots=True)
class ResultadoPublicacao:
    """O que o publicador precisa gravar na linha de `marketing_postagens`.

    `container_id` vem preenchido MESMO quando `ok=False`: é com ele que a
    reconciliação pergunta "será que saiu?" antes de qualquer retry.
    """

    ok: bool
    post_external_id: str | None = None
    post_url: str | None = None
    container_id: str | None = None
    erro: str | None = None
    # AMBÍGUO = a chamada que publica saiu e a resposta se perdeu (timeout,
    # queda de rede). NÃO dá pra dizer que falhou: o post pode estar no ar.
    # Quem recebe isto manda a linha pra `revisar` e CONSULTA antes de
    # qualquer retentativa — republicar não tem desfazer.
    ambiguo: bool = False


class MetaError(Exception):
    """Erro devolvido pela Graph API, já traduzido e SEM token.

    `code`/`subcode` ficam acessíveis pra quem quiser decidir por código (ex.:
    190 = reconectar a conta, não adianta retentar).
    """

    def __init__(self, mensagem: str, *, code: int | None = None, subcode: int | None = None):
        self.code = code
        self.subcode = subcode
        super().__init__(mensagem)


# `error_subcode` é mais específico que o `code` — quando existe, ganha. A
# tabela cobre o que a documentação da Meta lista e o que costuma aparecer no
# Reels; qualquer código de fora cai no fallback, que carrega a mensagem crua
# da Meta + o par (code/subcode) pra ninguém ficar sem diagnóstico.
_ERRO_POR_SUBCODE: dict[int, str] = {
    458: "o app foi removido da conta — reconecte a conta",
    460: "a senha da conta mudou — reconecte a conta",
    463: "o token da conta expirou — reconecte a conta",
    467: "o token da conta foi invalidado — reconecte a conta",
    2207001: "o Instagram restringiu esta conta",
    2207003: "a Meta não conseguiu baixar o vídeo",
    2207004: "a Meta estourou o tempo ao buscar o vídeo",
    2207005: "a Meta não encontrou o vídeo enviado",
    2207020: "a Meta não conseguiu ler o vídeo enviado",
    2207026: "formato de vídeo não suportado (use MP4/MOV com H.264 + AAC)",
    2207032: "a Meta falhou ao criar o container do Reels",
    2207042: "proporção do vídeo fora do aceito pelo Reels (use 9:16)",
    2207050: "a Meta não conseguiu subir o vídeo",
    2207051: "a Meta classificou a publicação como spam",
    2207053: "erro desconhecido no upload da Meta",
    2207057: "thumb_offset fora da duração do vídeo",
}

_ERRO_POR_CODE: dict[int, str] = {
    1: "erro desconhecido da Meta",
    2: "a Meta está temporariamente indisponível",
    4: "limite de chamadas do app atingido",
    9: "limite de publicações da conta atingido",
    10: "falta permissão no token pra esta ação",
    17: "limite de chamadas do usuário atingido",
    32: "limite de chamadas da Página atingido",
    100: "parâmetro inválido na chamada à Meta",
    190: "token inválido ou expirado — reconecte a conta",
    200: "o token não tem permissão pra publicar nesta conta",
    368: "a conta está temporariamente bloqueada por violação de política",
    613: "limite de chamadas atingido",
}

# Redação do token em texto livre. Três formas: o valor literal, o header
# `OAuth <token>` que a Meta às vezes ecoa e o `access_token=` de qualquer URL
# que caia num log.
_RE_ACCESS_TOKEN_QS = re.compile(r"(access_token=)[^&\s\"']+", re.IGNORECASE)
_RE_OAUTH_HEADER = re.compile(r"(OAuth|Bearer)\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE)


# Qualquer URL nossa de criativo com assinatura — pega também a versão
# escapada que a Meta devolve dentro da mensagem de erro.
_RE_LINK_ASSINADO = re.compile(
    r"https?:[^\s\"']*?/marketing/creatives/video/[A-Za-z0-9._%~\\/-]+", re.IGNORECASE
)


def _eh_ambiguo(exc: BaseException) -> bool:
    """A chamada saiu e NÃO voltou uma recusa explícita — pode ter publicado.

    Timeout e erro de conexão são o caso óbvio. `MetaError` sem `code` também
    entra: é 5xx cru ou resposta que não é JSON, e nada ali diz que a Meta
    recusou. Só `MetaError` COM código (`code`/`subcode` que a Meta mandou) é
    resposta definitiva de "não publiquei".
    """
    if isinstance(exc, MetaError):
        return exc.code is None and exc.subcode is None
    return isinstance(exc, httpx.TimeoutException | httpx.TransportError)


def _redigir(texto: str | None, token: str | None = None, *, tambem: str | None = None) -> str:
    """Tira o token de QUALQUER texto que vá virar log, `result` ou exceção.

    Os padrões com contexto (`OAuth …`, `access_token=…`) vêm ANTES da troca
    literal pra a frase continuar legível ("OAuth ***", e não "OAuth OAuth ***").

    `tambem` é o segundo segredo do Instagram: o link assinado do criativo. A
    Meta devolve a `video_url` inteira dentro da mensagem de erro, e o
    `result` da postagem aparece na tela — sem isto o link (que baixa o vídeo
    sem sessão) ficaria salvo no banco e visível.
    """
    out = texto or ""
    out = _RE_ACCESS_TOKEN_QS.sub(r"\1***", out)
    out = _RE_OAUTH_HEADER.sub("OAuth ***", out)
    if token:
        out = out.replace(token, "***")
    if tambem:
        out = out.replace(tambem, "<link do criativo>")
        # O link também pode voltar escapado/urlencoded dentro do JSON de erro.
        out = _RE_LINK_ASSINADO.sub("<link do criativo>", out)
    return out


def graph_version() -> str:
    """Versão da Graph API — `settings.meta_graph_version`, um lugar só.

    Lida na hora da chamada (e não no import) pra o teste conseguir apontar
    pra outra versão sem recarregar o módulo.
    """
    return get_settings().meta_graph_version


def _graph(caminho: str, provedor: str = PROVEDOR_FACEBOOK) -> str:
    host = GRAPH_HOST_IG if provedor == PROVEDOR_INSTAGRAM else GRAPH_HOST
    return f"{host}/{graph_version()}/{caminho.lstrip('/')}"


def _headers(token: str) -> dict[str, str]:
    """Token no HEADER, nunca na query string.

    A URL vai parar em mensagem de exceção do httpx, em log de proxy e no
    Sentry; o header não. É também o formato que a própria documentação do
    Reels usa (`Authorization: OAuth <token>`).
    """
    return {"Authorization": f"OAuth {token}"}


def _erro_da_resposta(resp: httpx.Response, *, token: str) -> MetaError:
    """Traduz o corpo de erro da Meta numa frase curta em pt-BR."""
    corpo: Any = None
    try:
        corpo = resp.json()
    except ValueError:
        corpo = None
    erro = (corpo or {}).get("error") if isinstance(corpo, dict) else None
    erro = erro if isinstance(erro, dict) else {}
    code = erro.get("code")
    subcode = erro.get("error_subcode")
    code = int(code) if isinstance(code, int | str) and str(code).isdigit() else None
    subcode = int(subcode) if isinstance(subcode, int | str) and str(subcode).isdigit() else None

    traduzida = None
    if subcode is not None:
        traduzida = _ERRO_POR_SUBCODE.get(subcode)
    if traduzida is None and code is not None:
        traduzida = _ERRO_POR_CODE.get(code)

    # `error_user_msg` é a mensagem que a Meta escreveu PRO USUÁRIO (vem
    # traduzida e costuma ser a mais útil das três).
    bruta = erro.get("error_user_msg") or erro.get("message") or (resp.text or "")[:300]
    if traduzida is None:
        traduzida = f"a Meta recusou: {bruta}"
    marcas = []
    if code is not None:
        marcas.append(f"code {code}")
    if subcode is not None:
        marcas.append(f"subcode {subcode}")
    if not marcas:
        marcas.append(f"HTTP {resp.status_code}")
    return MetaError(
        _redigir(f"{traduzida} ({', '.join(marcas)})", token),
        code=code,
        subcode=subcode,
    )


def _resposta_json(resp: httpx.Response, *, token: str) -> dict[str, Any]:
    """Corpo da resposta como dict — ou MetaError já traduzido.

    A Meta às vezes devolve HTTP 200 com `{"error": {...}}` dentro; por isso a
    checagem não é só pelo status.
    """
    if resp.status_code >= 400:
        raise _erro_da_resposta(resp, token=token)
    try:
        dados = resp.json()
    except ValueError:
        raise MetaError(
            _redigir(f"a Meta respondeu algo que não é JSON (HTTP {resp.status_code})", token)
        ) from None
    if not isinstance(dados, dict):
        raise MetaError(_redigir("a Meta respondeu num formato inesperado", token))
    if dados.get("error"):
        raise _erro_da_resposta(resp, token=token)
    return dados


async def _enviar_binario(
    upload_url: str, *, token: str, video_path: Path, tamanho: int
) -> dict[str, Any]:
    """Sobe o arquivo pro rupload.facebook.com (fase 2 do FB e do IG).

    Headers exigidos pela Meta: `Authorization: OAuth <token>`, `offset` (de
    onde começar — sempre 0 aqui, upload de uma tacada) e `file_size`.

    A leitura dos 26-41 MB vai pra uma thread: `read_bytes()` no loop do worker
    seguraria TODOS os outros crons (o worker é single-threaded por processo).
    """
    conteudo = await asyncio.to_thread(video_path.read_bytes)
    headers = {
        **_headers(token),
        "offset": "0",
        "file_size": str(tamanho),
        "Content-Type": "application/octet-stream",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_UPLOAD) as c:
        resp = await c.post(upload_url, headers=headers, content=conteudo)
    return _resposta_json(resp, token=token)


def _epoch_utc(quando: datetime) -> int:
    """Epoch em segundos. Datetime sem fuso é tratado como UTC — é assim que a
    coluna `agendado_para` guarda (a borda converte de BRT); usar o fuso da
    máquina aqui jogaria o post 3h pra frente."""
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    return int(quando.astimezone(UTC).timestamp())


def _tamanho_do_arquivo(video_path: Path) -> int:
    if not video_path.is_file():
        raise MetaError(f"arquivo do criativo não está no disco: {video_path.name}")
    tamanho = video_path.stat().st_size
    if tamanho <= 0:
        raise MetaError(f"arquivo do criativo está vazio: {video_path.name}")
    return tamanho


async def contas_do_token(token: str) -> list[dict[str, str | None]]:
    """O que este token enxerga: cada Página do portfólio e a conta do
    Instagram ligada a ela (`GET /me/accounts?fields=…,instagram_business_account`).

    É o que permite o operador COLAR só o token na tela: o DaVinci descobre
    sozinho o `page_id` e o `ig_user_id` — ninguém precisa caçar id numérico
    no Business Suite. Só leitura; não publica nada.

    Vem também o `page_token` de cada Página. Ele é o token que a doc de
    Content Publishing pede de verdade ("A Page access token requested from
    your app user who can perform the CREATE_CONTENT task on the Page") — o
    token do usuário do sistema serve pra DESCOBRIR, e o da Página pra
    PUBLICAR. Derivado de system user ele herda a não-expiração.

    **`page_token` é segredo.** Quem chama tem que separá-lo do resto antes de
    devolver qualquer coisa pra tela: o schema `ContaExternaOut` não tem esse
    campo de propósito.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
        resp = await c.get(
            _graph("me/accounts"),
            params={
                "fields": "id,name,access_token,instagram_business_account{id,username}",
                "limit": "100",
            },
            headers=_headers(token),
        )
    dados = _resposta_json(resp, token=token)
    out: list[dict[str, str | None]] = []
    for pagina in dados.get("data") or []:
        ig = pagina.get("instagram_business_account") or {}
        out.append(
            {
                "page_id": str(pagina.get("id") or "") or None,
                "page_nome": pagina.get("name") or None,
                "ig_user_id": str(ig.get("id") or "") or None,
                "ig_username": ig.get("username") or None,
                "page_token": pagina.get("access_token") or None,
            }
        )
    return out


async def validade_do_token(token: str) -> datetime | None:
    """Quando este token expira — `GET /debug_token`, best-effort.

    Sem isto a coluna `token_expires_at` fica NULL e o cron de renovação não
    tem o que achar: o token morre calado e o robô só descobre na primeira
    postagem recusada. `expires_at = 0` quer dizer "não expira" (System User
    token permanente) e vira None, que é o mesmo NULL — a diferença aparece no
    `status` da conta, não aqui.

    Devolve None quando a Meta não responde ou o app não pode inspecionar o
    token: é informação extra, não pode derrubar a conexão que já funcionou.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            resp = await c.get(
                _graph("debug_token"),
                params={"input_token": token},
                headers=_headers(token),
            )
        dados = _resposta_json(resp, token=token).get("data") or {}
    except Exception as exc:  # noqa: BLE001
        logger.info("meta_debug_token_indisponivel", erro=_redigir(str(exc), token)[:200])
        return None
    if not isinstance(dados, dict):
        return None
    epoch = dados.get("expires_at") or dados.get("data_access_expires_at")
    try:
        epoch = int(epoch)
    except (TypeError, ValueError):
        return None
    if epoch <= 0:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC)


async def conta_instagram_direta(token: str) -> dict[str, str | None] | None:
    """Fallback da trilha "Instagram Login" (conta SEM Página): o token é da
    própria conta, então `GET /me?fields=user_id,username` já identifica.
    Devolve None quando o token não é desse tipo."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            resp = await c.get(
                _graph("me", PROVEDOR_INSTAGRAM),
                params={"fields": "user_id,username"},
                headers=_headers(token),
            )
        dados = _resposta_json(resp, token=token)
    except MetaError:
        return None
    uid = str(dados.get("user_id") or dados.get("id") or "").strip()
    if not uid:
        return None
    return {"ig_user_id": uid, "ig_username": dados.get("username") or None}


async def _permalink(
    node_id: str, *, token: str, campo: str, provedor: str = PROVEDOR_FACEBOOK
) -> str | None:
    """Link público do post — best-effort.

    Falhar aqui NÃO pode derrubar uma publicação que já saiu: sem o link a
    postagem continua publicada, só fica sem o atalho na tela.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            resp = await c.get(
                _graph(node_id, provedor), params={"fields": campo}, headers=_headers(token)
            )
        return _resposta_json(resp, token=token).get(campo) or None
    except Exception as exc:  # noqa: BLE001 — link é enfeite, não resultado
        logger.info("meta_permalink_indisponivel", node=node_id, erro=_redigir(str(exc), token))
        return None


# ───────────────────────────────── Facebook ─────────────────────────────────


async def publicar_reel_facebook(
    *,
    page_id: str,
    token: str,
    video_path: Path,
    legenda: str,
    agendado_para: datetime | None = None,
    ao_criar_container: Callable[[str], Awaitable[None]] | None = None,
) -> ResultadoPublicacao:
    """Publica um Reel numa Página do Facebook, nas 3 fases da Meta.

        1. start  → POST /{page_id}/video_reels?upload_phase=start
                    devolve `video_id` + `upload_url` do rupload
        2. upload → POST <upload_url> com o arquivo (Authorization/offset/file_size)
        3. finish → POST /{page_id}/video_reels?upload_phase=finish
                    com video_state=PUBLISHED (ou SCHEDULED + scheduled_publish_time)

    Nenhum byte precisa ir pra uma URL pública: o Facebook recebe o arquivo
    direto. Foi por isso que o FB entrou antes do Instagram.

    `agendado_para` usa o agendamento NATIVO do Facebook. Na prática o robô
    publica na hora (o cron `marketing_postagens_promover` é quem segura a
    agenda, porque o Instagram não tem equivalente e os dois precisam se
    comportar igual) — este caminho existe pra quando o operador quiser que a
    Meta guarde o post, e é o único jeito de agendar além do nosso worker.

    `ao_criar_container` é chamado com o `video_id` assim que a fase 1 termina,
    ANTES do upload: é o gancho que grava o id no banco pra reconciliação.
    """
    video_id: str | None = None
    fase_final = False
    try:
        tamanho = _tamanho_do_arquivo(video_path)

        if agendado_para is not None:
            # `_epoch_utc` já resolve o naive-como-UTC; comparar por epoch evita
            # o clássico "datetime com e sem fuso não se subtraem".
            quando = datetime.fromtimestamp(_epoch_utc(agendado_para), tz=UTC)
            distancia = quando - datetime.now(UTC)
            if distancia < FB_AGENDA_MIN or distancia > FB_AGENDA_MAX:
                return ResultadoPublicacao(
                    ok=False,
                    erro=(
                        "o Facebook só agenda entre 10 minutos e 29 dias à frente — "
                        "publique agora ou escolha outra data"
                    ),
                )

        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            resp = await c.post(
                _graph(f"{page_id}/video_reels"),
                data={"upload_phase": "start"},
                headers=_headers(token),
            )
        inicio = _resposta_json(resp, token=token)
        video_id = str(inicio.get("video_id") or "").strip() or None
        if not video_id:
            raise MetaError("a Meta não devolveu o video_id na abertura do upload")
        upload_url = (
            inicio.get("upload_url")
            or f"{RUPLOAD_HOST}/video-upload/{graph_version()}/{video_id}"
        )
        if ao_criar_container is not None:
            # Grava o id ANTES de gastar o upload: se o processo cair no meio,
            # a reconciliação tem por onde perguntar em vez de republicar.
            await ao_criar_container(video_id)

        await _enviar_binario(upload_url, token=token, video_path=video_path, tamanho=tamanho)

        # `description` (não `caption`) é o campo do Reels do Facebook.
        fim: dict[str, str] = {
            "upload_phase": "finish",
            "video_id": video_id,
            "description": legenda or "",
        }
        if agendado_para is not None:
            fim["video_state"] = "SCHEDULED"
            fim["scheduled_publish_time"] = str(_epoch_utc(agendado_para))
        else:
            fim["video_state"] = "PUBLISHED"
        # Daqui pra frente um erro NÃO prova que o Reel não saiu.
        fase_final = True
        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            resp = await c.post(
                _graph(f"{page_id}/video_reels"), data=fim, headers=_headers(token)
            )
        _resposta_json(resp, token=token)

        url = None
        if agendado_para is None:
            url = await _permalink(video_id, token=token, campo="permalink_url")
            if url and url.startswith("/"):
                url = f"https://www.facebook.com{url}"
        logger.info(
            "meta_reel_facebook_publicado",
            page_id=page_id,
            video_id=video_id,
            agendado=agendado_para is not None,
        )
        return ResultadoPublicacao(
            ok=True, post_external_id=video_id, post_url=url, container_id=video_id
        )
    except Exception as exc:  # noqa: BLE001
        # Tudo vira `erro` redigido: este cliente não levanta pro worker, que
        # só precisa saber "saiu ou não" e o porquê, em texto pro operador.
        msg = _redigir(str(exc), token) or exc.__class__.__name__
        ambiguo = fase_final and _eh_ambiguo(exc)
        logger.warning(
            "meta_reel_facebook_falhou", page_id=page_id, erro=msg[:300], ambiguo=ambiguo
        )
        return ResultadoPublicacao(
            ok=False, container_id=video_id, erro=msg[:500], ambiguo=ambiguo
        )


# ──────────────────────────────── Instagram ─────────────────────────────────


async def publicar_reel_instagram(
    *,
    ig_user_id: str,
    token: str,
    video_path: Path,
    legenda: str,
    share_to_feed: bool = True,
    video_url: str | None = None,
    provedor: str = PROVEDOR_FACEBOOK,
    poll_intervalo_s: float = IG_POLL_INTERVALO_S,
    poll_max_s: float = IG_POLL_MAX_S,
    ao_criar_container: Callable[[str], Awaitable[None]] | None = None,
) -> ResultadoPublicacao:
    """Publica um Reel no Instagram: container → upload → poll → publish.

        1. POST /{ig_user_id}/media  (media_type=REELS, upload_type=resumable)
           devolve o `id` do container + a `uri` do rupload
        2. POST <uri> com o arquivo (mesmos headers do Facebook)
        3. GET /{container_id}?fields=status_code até FINISHED
           (IN_PROGRESS = ainda transcodificando; ERROR/EXPIRED = acabou mal)
        4. POST /{ig_user_id}/media_publish  (creation_id=<container>)

    Dois caminhos pro arquivo, e a escolha não é de gosto:
      • `upload_type=resumable` (passos 1-2 acima) manda o binário direto — é o
        preferido, porque o criativo não aparece na internet. Só existe na
        trilha "Instagram API with Facebook Login", que EXIGE a conta vinculada
        a uma Página do Facebook.
      • `video_url` (quando `video_url` vem preenchido): a Meta BAIXA o vídeo
        da URL ("we cURL the video using the passed-in URL, so it must be on a
        public server"). É o caminho de quem não tem Página — o DaVinci passa
        um link assinado de 15 min (services/marketing/link_criativo.py), e o
        upload binário é pulado.

    `provedor` diz de QUAL trilha o token veio, e é ele (não a plataforma da
    linha) que escolhe o host: "instagram" fala com graph.instagram.com e NÃO
    tem upload binário — nessa trilha o único caminho é `video_url`, então sem
    link a publicação é recusada aqui mesmo, antes de criar container.

    O passo 4 é o único que não tem volta: por isso o container é gravado no
    banco (via `ao_criar_container`) antes dele, e o poll tem teto de
    `poll_max_s`. Estourou o teto, NÃO publicamos: devolvemos o container pro
    worker e a reconciliação decide depois — republicar por engano custa um
    Reel duplicado na conta da marca.
    """
    container_id: str | None = None
    fase_final = False
    try:
        if provedor == PROVEDOR_INSTAGRAM and not video_url:
            # rupload é exclusivo da trilha com Facebook Login. Tentar assim
            # mesmo gastaria um container e voltaria um erro de permissão que
            # não diz nada — melhor a frase que aponta o setting.
            raise MetaError(
                "esta conta está conectada pela trilha Instagram Login (sem Página): "
                "o vídeo só chega por link. Ligue MARKETING_POSTAGEM_UPLOAD=link "
                "ou vincule a conta a uma Página do Facebook."
            )
        # No caminho por URL o arquivo nem é aberto aqui — mas continua sendo
        # conferido (existe? tamanho?) pra falhar cedo, antes de criar container.
        tamanho = _tamanho_do_arquivo(video_path)
        legenda = (legenda or "")[:IG_LEGENDA_MAX]

        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            dados: dict[str, str] = {
                "media_type": "REELS",
                "caption": legenda,
            }
            if video_url:
                # Sem Página: a Meta baixa o arquivo do link assinado.
                dados["video_url"] = video_url
            else:
                dados["upload_type"] = "resumable"
            resp = await c.post(
                _graph(f"{ig_user_id}/media", provedor),
                data={
                    **dados,
                    # O Reels aparecer também no feed é escolha da marca
                    # (opcoes.share_to_feed no modal).
                    "share_to_feed": "true" if share_to_feed else "false",
                },
                headers=_headers(token),
            )
        criado = _resposta_json(resp, token=token)
        container_id = str(criado.get("id") or "").strip() or None
        if not container_id:
            raise MetaError("a Meta não devolveu o id do container do Reels")
        upload_url = (
            criado.get("uri") or f"{RUPLOAD_HOST}/ig-api-upload/{graph_version()}/{container_id}"
        )
        if ao_criar_container is not None:
            await ao_criar_container(container_id)

        if not video_url:
            await _enviar_binario(upload_url, token=token, video_path=video_path, tamanho=tamanho)

        estado = await _aguardar_container_instagram(
            container_id,
            token=token,
            intervalo_s=poll_intervalo_s,
            max_s=poll_max_s,
            provedor=provedor,
        )
        if estado != "FINISHED":
            return ResultadoPublicacao(
                ok=False,
                container_id=container_id,
                erro=_MOTIVO_CONTAINER.get(
                    estado, f"container do Reels em estado inesperado ({estado})"
                ),
            )

        # Daqui pra frente um erro NÃO prova que o Reel não saiu.
        fase_final = True
        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            resp = await c.post(
                _graph(f"{ig_user_id}/media_publish", provedor),
                data={"creation_id": container_id},
                headers=_headers(token),
            )
        publicado = _resposta_json(resp, token=token)
        media_id = str(publicado.get("id") or "").strip() or None
        url = (
            await _permalink(media_id, token=token, campo="permalink", provedor=provedor)
            if media_id
            else None
        )
        logger.info(
            "meta_reel_instagram_publicado",
            ig_user_id=ig_user_id,
            container_id=container_id,
            media_id=media_id,
        )
        return ResultadoPublicacao(
            ok=True, post_external_id=media_id, post_url=url, container_id=container_id
        )
    except Exception as exc:  # noqa: BLE001 — ver comentário gêmeo no Facebook
        msg = _redigir(str(exc), token, tambem=video_url) or exc.__class__.__name__
        ambiguo = fase_final and _eh_ambiguo(exc)
        logger.warning(
            "meta_reel_instagram_falhou", ig_user_id=ig_user_id, erro=msg[:300], ambiguo=ambiguo
        )
        return ResultadoPublicacao(
            ok=False, container_id=container_id, erro=msg[:500], ambiguo=ambiguo
        )


# Estados terminais do container. EXPIRED é o mais importante: o container do
# Instagram morre em 24h e nenhum retry o ressuscita — a postagem tem que ser
# refeita do zero (container novo), nunca "publicada de novo".
_MOTIVO_CONTAINER: dict[str, str] = {
    "ERROR": "a Meta falhou ao processar o vídeo do Reels",
    "EXPIRED": "o container do Reels expirou (24h) — refaça a postagem",
    "IN_PROGRESS": "a Meta ainda está processando o vídeo",
    "PUBLISHED": "este container já foi publicado",
}


async def _aguardar_container_instagram(
    container_id: str,
    *,
    token: str,
    intervalo_s: float,
    max_s: float,
    provedor: str = PROVEDOR_FACEBOOK,
) -> str:
    """Poll do `status_code` — 1×/min, teto de 5 min (parâmetros pro teste).

    A primeira consulta é IMEDIATA (vídeo curto costuma ficar pronto em
    segundos); só depois dela entra a cadência de 1 minuto.
    """
    limite = max(1, int(max_s // intervalo_s) + 1) if intervalo_s > 0 else 1
    estado = "IN_PROGRESS"
    for tentativa in range(limite):
        if tentativa:
            await asyncio.sleep(intervalo_s)
        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            resp = await c.get(
                _graph(container_id, provedor),
                params={"fields": "status_code"},
                headers=_headers(token),
            )
        estado = str(_resposta_json(resp, token=token).get("status_code") or "").upper()
        if estado != "IN_PROGRESS":
            return estado
    return estado or "IN_PROGRESS"


async def content_publishing_limit(
    *, ig_user_id: str, token: str, provedor: str = PROVEDOR_FACEBOOK
) -> dict[str, Any]:
    """Quantos posts a conta já gastou da cota de 24h do Instagram.

    Uma chamada barata (`GET /{ig_id}/content_publishing_limit`) que evita
    queimar um upload de 41 MB pra tomar "limite atingido" no fim. Best-effort:
    erro aqui devolve `{}` e o publicador segue — o nosso próprio teto
    (`marketing_postagem_max_dia`) já é bem mais apertado que o da Meta.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            resp = await c.get(
                _graph(f"{ig_user_id}/content_publishing_limit", provedor),
                params={"fields": "config,quota_usage"},
                headers=_headers(token),
            )
        dados = _resposta_json(resp, token=token)
    except Exception as exc:  # noqa: BLE001
        logger.info("meta_quota_indisponivel", erro=_redigir(str(exc), token)[:200])
        return {}
    linhas = dados.get("data")
    linha = linhas[0] if isinstance(linhas, list) and linhas else {}
    if not isinstance(linha, dict):
        return {}
    config = linha.get("config") if isinstance(linha.get("config"), dict) else {}
    return {
        "quota_usage": linha.get("quota_usage"),
        "quota_total": (config or {}).get("quota_total"),
    }


# ───────────────────────────── reconciliação ────────────────────────────────


async def consultar_publicacao(
    *,
    plataforma: str,
    token: str,
    container_id: str | None = None,
    post_external_id: str | None = None,
    provedor: str = PROVEDOR_FACEBOOK,
) -> ResultadoPublicacao:
    """"Será que saiu?" — CONSULTA usada antes de qualquer retry.

    Publicar não é idempotente: uma postagem presa em `publicando` pode ser
    uma chamada que deu timeout DEPOIS da Meta ter aceitado. Retentar às cegas
    duplicaria o Reel na conta da marca. Então primeiro se pergunta pelo id que
    já está no banco, e só com `ok=False` (estado não-terminal claro) o
    operador/cron decide o que fazer.
    """
    ident = post_external_id or container_id
    if not ident:
        return ResultadoPublicacao(ok=False, erro="sem id externo pra consultar")
    try:
        if plataforma == PLATAFORMA_FACEBOOK:
            async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
                resp = await c.get(
                    _graph(ident),
                    params={"fields": "permalink_url,status"},
                    headers=_headers(token),
                )
            dados = _resposta_json(resp, token=token)
            status = dados.get("status") if isinstance(dados.get("status"), dict) else {}
            fase = (status or {}).get("publishing_phase") or {}
            url = dados.get("permalink_url")
            if url and str(url).startswith("/"):
                url = f"https://www.facebook.com{url}"
            # `permalink_url` SOZINHO não prova nada: um vídeo agendado (ou
            # ainda em processamento) já tem link. Só `publishing_phase.status
            # = complete` é prova de que saiu. Com link mas sem "complete" o
            # caso é AMBÍGUO — quem consulta não pode tratar como "não saiu" e
            # republicar.
            fase_status = str(fase.get("status") or "").lower()
            saiu = fase_status == "complete"
            fase_video = (status or {}).get("video_status") or "processamento"
            return ResultadoPublicacao(
                ok=saiu,
                post_external_id=ident,
                post_url=url or None,
                container_id=container_id,
                erro=None if saiu else f"vídeo ainda em {fase_video}",
                ambiguo=not saiu and bool(url),
            )

        if plataforma == PLATAFORMA_INSTAGRAM:
            if post_external_id:
                # Aqui NÃO serve o `_permalink` best-effort: ele engole
                # qualquer erro e devolve None, e um None por timeout viraria
                # "não publicou" — exatamente a leitura que duplica o Reel. A
                # consulta é direta e a exceção sobe pro `except` de baixo,
                # que marca ambíguo.
                async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
                    resp = await c.get(
                        _graph(post_external_id, provedor),
                        params={"fields": "permalink"},
                        headers=_headers(token),
                    )
                url = _resposta_json(resp, token=token).get("permalink") or None
                return ResultadoPublicacao(
                    ok=True,
                    post_external_id=post_external_id,
                    post_url=url,
                    container_id=container_id,
                )
            async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
                resp = await c.get(
                    _graph(ident, provedor),
                    params={"fields": "status_code"},
                    headers=_headers(token),
                )
            estado = str(_resposta_json(resp, token=token).get("status_code") or "").upper()
            # PUBLISHED no container = o Reel SAIU (mesmo sem termos guardado o
            # media_id): é exatamente o caso em que um retry duplicaria o post.
            return ResultadoPublicacao(
                ok=estado == "PUBLISHED",
                container_id=container_id or ident,
                erro=None if estado == "PUBLISHED" else _MOTIVO_CONTAINER.get(
                    estado, f"container em {estado or 'estado desconhecido'}"
                ),
            )

        return ResultadoPublicacao(ok=False, erro=f"plataforma sem consulta: {plataforma}")
    except Exception as exc:  # noqa: BLE001
        msg = _redigir(str(exc), token) or exc.__class__.__name__
        # Consulta que não respondeu é DÚVIDA, nunca "não publicou": o `ok=False`
        # sozinho seria lido como permissão pra republicar. A única resposta
        # definitiva de ausência é a Meta dizendo que o objeto não existe
        # (code 803 / 100), e essa vem com código.
        ambiguo = _eh_ambiguo(exc) or getattr(exc, "code", None) not in (803, 100)
        logger.warning(
            "meta_consulta_falhou", plataforma=plataforma, erro=msg[:300], ambiguo=ambiguo
        )
        return ResultadoPublicacao(
            ok=False, container_id=container_id, erro=msg[:500], ambiguo=ambiguo
        )


# ─────────────────────── Mensagem direta (DM do Instagram) ───────────────────
#
# O espelho invertido de publicar: aqui não existe `container_id`, então não
# existe o passo "será que saiu?". Publicar errado se apaga; mensagem enviada
# chega no celular da pessoa e não se desvê. Por isso `ambiguo` aqui é mais
# grave que na postagem — quem recebe manda pra revisão HUMANA e não retenta.


@dataclass(frozen=True)
class ResultadoMensagem:
    ok: bool
    # O `mid` que a Meta devolve para a mensagem que ACABOU de sair. É o que
    # permite reconhecer o eco dela voltando pelo webhook.
    mid: str | None = None
    erro: str | None = None
    code: int | None = None
    ambiguo: bool = False


async def enviar_dm(
    token: str,
    *,
    ig_id: str,
    destinatario: str,
    texto: str,
    provedor: str = PROVEDOR_INSTAGRAM,
) -> ResultadoMensagem:
    """Responde uma DM.

    `ig_id` é o id da conta profissional (17841...), `destinatario` é o IGSID
    de quem escreveu — os dois vêm do próprio webhook. `provedor` decide o
    host: a trilha Instagram Login fala com graph.instagram.com, a trilha
    Facebook Login com graph.facebook.com e token de Página.

    A janela de 24h NÃO é conferida aqui: quem conhece o relógio da conversa
    é o serviço, e checar duas vezes esconderia de qual lado veio a recusa.
    """
    corpo = {"recipient": {"id": destinatario}, "message": {"text": texto}}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            resp = await c.post(
                _graph(f"{ig_id}/messages", provedor),
                json=corpo,
                headers=_headers(token),
            )
        dados = _resposta_json(resp, token=token)
    except MetaError as e:
        # Erro COM código é resposta da Meta: ela recebeu e recusou, então a
        # mensagem não saiu. Dá pra tratar como falha de verdade.
        logger.warning(
            "dm_envio_recusado", ig_id=ig_id, code=e.code, erro=_redigir(str(e), token)
        )
        return ResultadoMensagem(ok=False, erro=str(e), code=e.code, ambiguo=_eh_ambiguo(e))
    except Exception as exc:  # noqa: BLE001
        # Timeout / queda de rede: a chamada PODE ter chegado. Não dá pra
        # dizer que falhou, e não dá pra perguntar. Vai pra humano.
        ambiguo = _eh_ambiguo(exc)
        logger.warning(
            "dm_envio_incerto", ig_id=ig_id, ambiguo=ambiguo, erro=_redigir(str(exc), token)
        )
        return ResultadoMensagem(
            ok=False, erro=_redigir(str(exc), token), ambiguo=ambiguo
        )

    mid = dados.get("message_id") or dados.get("mid")
    logger.info("dm_enviada", ig_id=ig_id, tem_mid=bool(mid))
    return ResultadoMensagem(ok=True, mid=str(mid) if mid else None)
