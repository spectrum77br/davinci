"""Publicação de Shorts no YouTube (Eduardo, 18/09/2026).

Irmão do `meta_client.py`, e devolve o MESMO `ResultadoPublicacao` — o
despacho no worker não precisa saber de qual plataforma veio.

## O que descobrimos por teste, contra a documentação

A doc do YouTube diz que projeto de API criado depois de 28/07/2020 e não
auditado tem TODO upload forçado a privado, mesmo mandando `public`. **É
falso.** Em 18/09/2026 subimos um vídeo por um projeto criado em 2026, sem
verificação de OAuth, sem auditoria, com a tela de consentimento recém-saída
do modo de testes — e ele saiu público (`isPrivate=false`,
`playabilityStatus=OK`, oEmbed respondendo 200).

Mesmo padrão da Meta nesta semana: a doc descrevia uma trava que não é
aplicada. Se um dia ela passar a ser, o sintoma vai ser vídeo publicado com
`privacyStatus` diferente do pedido — por isso `publicar_video_youtube`
RELÊ o vídeo depois de subir e devolve erro quando a privacidade não é a que
foi pedida, em vez de reportar sucesso.

## Diferença estrutural para a Meta

Na Meta existe container: cria, espera transcodificar, publica. São duas
chamadas, e é por isso que `container_id` existe — dá pra perguntar "será que
saiu?" antes de retentar.

Aqui não tem esse meio: o upload resumável devolve o id do vídeo na mesma
chamada que envia os bytes. Então:

  - `container_id` recebe o id da SESSÃO de upload assim que ela abre, antes
    de um único byte subir. Serve pro mesmo propósito: se o processo morrer no
    meio, alguém sabe que havia algo em voo.
  - Timeout no PUT vira `ambiguo=True`, nunca falha. O YouTube pode ter
    recebido o arquivo inteiro e a resposta ter se perdido — republicar sem
    conferir criaria vídeo duplicado no canal, que não tem desfazer bonito.

## Shorts

Não existe campo "isShort" na API, e não adianta procurar. Quem decide é o
YouTube, olhando proporção vertical e duração até 3 min. Os criativos já são
9:16, então entram como Short sozinhos. `#Shorts` no título é só sinal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import structlog

from app.config import get_settings
from app.services.marketing.meta_client import ResultadoPublicacao

logger = structlog.get_logger()

PLATAFORMA_YOUTUBE = "youtube"

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (é URL, não segredo)
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# Trocar refresh por access é um payload minúsculo.
_TIMEOUT_API = httpx.Timeout(30.0, connect=10.0)
# Upload do arquivo. Mesmo teto do `meta_client`, pelo mesmo motivo: precisa
# caber COM FOLGA dentro do timeout do job, senão o arq mata o processo no meio
# da chamada que publica e ninguém sabe se o vídeo subiu.
_TIMEOUT_UPLOAD = httpx.Timeout(600.0, connect=15.0)

# Tetos do YouTube. O título é o que mais morde: a API recusa a chamada
# INTEIRA por um caractere a mais, depois de o arquivo já ter subido.
TITULO_MAX = 100
DESCRICAO_MAX = 5000
# `<` e `>` são recusados no título e na descrição, sem mensagem clara.
_PROIBIDOS_NO_TEXTO = str.maketrans({"<": "‹", ">": "›"})

# 22 = "People & Blogs". Categoria é obrigatória e essa é a neutra para vídeo
# de marca; 24 ("Entertainment") mudaria a vitrine sem a gente querer.
CATEGORIA_PADRAO = "22"


class YouTubeError(Exception):
    """Erro do YouTube já traduzido e SEM token no texto."""

    def __init__(self, mensagem: str, *, code: str | None = None) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.code = code


def _erro_do_google(resp: httpx.Response) -> YouTubeError:
    """Traduz a resposta de erro sem nunca ecoar o corpo cru.

    O corpo pode trazer de volta pedaço do que foi enviado, e o header de
    Authorization não aparece ali — mas `reason` e `message` bastam pra
    diagnosticar, e é só isso que vai pro banco e pro log.
    """
    try:
        d = resp.json().get("error") or {}
        msg = str(d.get("message") or "")[:300]
        detalhes = d.get("errors") or []
        code = str(detalhes[0].get("reason")) if detalhes else None
    except Exception:  # noqa: BLE001
        msg, code = f"HTTP {resp.status_code}", None
    return YouTubeError(msg or f"HTTP {resp.status_code}", code=code)


def _titulo_e_descricao(legenda: str) -> tuple[str, str]:
    """A legenda do criativo é UM texto; o YouTube quer dois campos.

    Primeira linha vira título (é como as pessoas escrevem legenda: a frase
    forte primeiro), e o texto inteiro vira descrição. Sem isso o título sairia
    truncado no meio de uma frase, que é o que mais parece robô.
    """
    limpo = (legenda or "").strip()
    primeira = next((x.strip() for x in limpo.splitlines() if x.strip()), "")
    titulo = (primeira or "Novo vídeo")[:TITULO_MAX].translate(_PROIBIDOS_NO_TEXTO)
    descricao = limpo[:DESCRICAO_MAX].translate(_PROIBIDOS_NO_TEXTO)
    return titulo, descricao


async def access_token_de(refresh_token: str) -> str:
    """Troca o refresh token (que não expira) pelo access token (1h).

    Guardar só o refresh é de propósito: access token vencido em banco é lixo
    que confunde quem for depurar, e a troca custa uma chamada de 200 bytes.
    """
    s = get_settings()
    if not s.youtube_client_id or not s.youtube_client_secret:
        raise YouTubeError("YOUTUBE_CLIENT_ID/SECRET não configurados no servidor")
    async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
        resp = await c.post(
            OAUTH_TOKEN_URL,
            data={
                "client_id": s.youtube_client_id,
                "client_secret": s.youtube_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        erro = _erro_do_google(resp)
        # `invalid_grant` aqui quase sempre é uma coisa só: a tela de
        # consentimento voltou pro modo de testes (onde o refresh morre em 7
        # dias) ou alguém revogou o acesso. Dizer isso poupa meia hora.
        if "invalid_grant" in str(erro.mensagem).lower():
            raise YouTubeError(
                "refresh token inválido — reconecte o canal "
                "(e confira se a tela de consentimento saiu do modo de testes)",
                code="invalid_grant",
            )
        raise erro
    return str(resp.json()["access_token"])


async def publicar_video_youtube(
    *,
    refresh_token: str,
    video_path: Path,
    legenda: str,
    privacidade: str | None = None,
    ao_abrir_sessao: Callable[[str], Awaitable[None]] | None = None,
) -> ResultadoPublicacao:
    """Sobe o vídeo e CONFERE o que o YouTube gravou. Nunca levanta."""
    s = get_settings()
    alvo = (privacidade or s.youtube_privacidade or "public").strip()
    titulo, descricao = _titulo_e_descricao(legenda)

    try:
        tamanho = video_path.stat().st_size
    except OSError as e:
        return ResultadoPublicacao(ok=False, erro=f"arquivo ilegível: {type(e).__name__}")
    if tamanho <= 0:
        return ResultadoPublicacao(ok=False, erro="arquivo do criativo está vazio")

    try:
        token = await access_token_de(refresh_token)
    except YouTubeError as e:
        return ResultadoPublicacao(ok=False, erro=e.mensagem)
    except Exception as e:  # noqa: BLE001
        return ResultadoPublicacao(ok=False, erro=f"falha ao renovar token ({type(e).__name__})")

    corpo: dict[str, Any] = {
        "snippet": {
            "title": titulo,
            "description": descricao,
            "categoryId": CATEGORIA_PADRAO,
        },
        "status": {
            "privacyStatus": alvo,
            # Obrigatório desde 2020: sem ele a API recusa a chamada inteira.
            "selfDeclaredMadeForKids": False,
        },
    }

    # ── 1. abre a sessão de upload ──────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            resp = await c.post(
                f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
                json=corpo,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Upload-Content-Length": str(tamanho),
                    "X-Upload-Content-Type": "video/*",
                },
            )
        if resp.status_code not in (200, 201):
            return ResultadoPublicacao(ok=False, erro=_erro_do_google(resp).mensagem)
        sessao = resp.headers.get("Location") or resp.headers.get("location")
        if not sessao:
            return ResultadoPublicacao(ok=False, erro="YouTube não devolveu a sessão de upload")
    except Exception as e:  # noqa: BLE001
        # Falhar ANTES de enviar byte nenhum é seguro: nada foi criado.
        return ResultadoPublicacao(ok=False, erro=f"falha ao abrir upload ({type(e).__name__})")

    if ao_abrir_sessao is not None:
        # Carimba que há algo em voo ANTES de enviar. Se o processo morrer no
        # meio do PUT, é este registro que impede alguém de republicar às cegas.
        try:
            await ao_abrir_sessao(sessao[-120:])
        except Exception:  # noqa: BLE001
            logger.warning("youtube_carimbo_sessao_falhou")

    # ── 2. envia o arquivo ──────────────────────────────────────────────
    try:
        conteudo = video_path.read_bytes()
        async with httpx.AsyncClient(timeout=_TIMEOUT_UPLOAD) as c:
            envio = await c.put(
                sessao,
                content=conteudo,
                headers={"Content-Type": "video/*", "Content-Length": str(tamanho)},
            )
        if envio.status_code not in (200, 201):
            return ResultadoPublicacao(ok=False, erro=_erro_do_google(envio).mensagem)
        vid = str(envio.json().get("id") or "")
        if not vid:
            return ResultadoPublicacao(ok=False, erro="upload aceito mas sem id de vídeo")
    except (httpx.TimeoutException, httpx.RemoteProtocolError) as e:
        # O YouTube pode ter recebido tudo e a resposta ter se perdido.
        # Republicar sem conferir criaria vídeo duplicado no canal.
        return ResultadoPublicacao(
            ok=False,
            ambiguo=True,
            erro=f"upload sem resposta ({type(e).__name__}) — confira o canal antes de republicar",
        )
    except Exception as e:  # noqa: BLE001
        return ResultadoPublicacao(ok=False, erro=f"falha no upload ({type(e).__name__})")

    url = f"https://youtu.be/{vid}"

    # ── 3. confere o que ele GRAVOU ─────────────────────────────────────
    # A resposta do upload ecoa o que foi PEDIDO, não o que valeu. Se a trava
    # do "nasce privado" um dia for aplicada, é aqui que a gente descobre — e
    # é melhor virar erro visível do que um "publicado" mentiroso no painel.
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_API) as c:
            conf = await c.get(
                f"{VIDEOS_URL}?id={vid}&part=status",
                headers={"Authorization": f"Bearer {token}"},
            )
        itens = conf.json().get("items") or [] if conf.status_code == 200 else []
        real = (itens[0].get("status") or {}).get("privacyStatus") if itens else None
    except Exception:  # noqa: BLE001
        real = None

    if real and real != alvo:
        return ResultadoPublicacao(
            ok=False,
            post_external_id=vid,
            post_url=url,
            erro=(
                f"vídeo subiu mas ficou '{real}' em vez de '{alvo}' — "
                "o projeto precisa passar pela auditoria do YouTube"
            ),
        )

    logger.info("youtube_publicado", video_id=vid, privacidade=real or alvo)
    return ResultadoPublicacao(ok=True, post_external_id=vid, post_url=url)
