"""Quanto cada vídeo publicado pelo DaVinci rendeu (Eduardo, 23/09/2026).

O pedido foi: ver views e interações POR MARCA, com o total somado e o detalhe
de cada rede. E — pedido explícito — por vídeo publicado pelo automático, não o
número geral do canal. É o que `marketing_postagens` já permite: ela só contém
o que saiu por aqui, com a conta e o link de cada publicação.

As três redes entregam coisas diferentes, e a diferença NÃO é detalhe:

    youtube    Data API v3, `videos.list?part=statistics`. O escopo
               `youtube.readonly` que os canais já têm basta — provado por
               sondagem em 23/09. Custa 1 unidade por chamada num teto de
               10.000/dia, e aceita até 50 ids de uma vez.

    instagram  `like_count` e `comments_count` saem do próprio nó da mídia,
               com o token que já publica. Views, alcance, compartilhamentos e
               salvamentos vêm de `/insights`, que exige
               `instagram_manage_insights` — o token de hoje NÃO tem (sondagem
               devolveu erro 10, "Application does not have permission"). Por
               isso o insights é BEST-EFFORT: falhou, grava o que deu e segue.
               No dia em que o token for regerado com a permissão, as views
               entram sozinhas, sem mudar uma linha daqui.

    tiktok     Não tem API pra nós (app recusado nas duas auditorias). Mas a
               página PÚBLICA do vídeo traz os números num JSON embutido, sem
               login — e responde do IP do servidor. Diferente da publicação,
               que precisa do Mac do Eduardo, a coleta roda no servidor.

Todo número é NULÁVEL, e a diferença importa: NULO quer dizer "esta rede não me
deu este número"; ZERO quer dizer "deu, e é zero". Somar tratando nulo como zero
é como a tela mentiria sem ninguém perceber.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    MarketingCreative,
    MarketingPostagem,
    MarketingPostagemMetrica,
    RedeSocialToken,
)
from app.models.marketing_postagem import STATUS_PUBLICADO
from app.security.cipher import decrypt_json
from app.services.marketing import meta_client, youtube_client

logger = structlog.get_logger()

BRT = ZoneInfo("America/Sao_Paulo")

# Até quando vale recoletar. Vídeo velho ainda ganha view, mas o ganho vira
# resíduo — e a Meta só guarda 90 dias de insight de qualquer forma.
JANELA_DIAS = 90

# A leitura da noite é às 23:47 BRT (02:47 UTC — o relógio do container é
# UTC). Às 23:47 o dia já quase fechou, então o retrato do dia D é o número do
# FIM de D, e "views ganhas por dia" cai no dia certo. Até 24/09/2026 era às
# 04:20, e o retrato de D era na prática o fim de D-1: o ganho de cada dia
# aparecia no dia seguinte. O worker e a tela importam daqui: horário escrito
# em dois lugares diverge.
HORA_NOTURNA_UTC = 2
MINUTO_COLETA = 47

# Respiro entre duas páginas do TikTok na mesma rodada. Sem API, a leitura é
# da página pública — rajada do mesmo IP é o jeito mais rápido de virar
# bloqueio, e aí o TikTok inteiro fica sem número.
PAUSA_TIKTOK_S = 1.0

# Teto de posts por rodada, por modo. `completo` é a noite (lê tudo, o mais
# atrasado primeiro); `recentes` é o passe de hora em hora (só o que nunca foi
# lido e falha recente); `agora` é o botão da tela. O teto protege de o cron
# virar rajada quando o volume crescer — e como a ordem é "o mais atrasado
# primeiro", o que não coube numa rodada é o primeiro da próxima.
CAP = {"completo": 600, "recentes": 40, "agora": 120}

# Chaves no Redis, lidas pelo worker E pela tela. `RODANDO` é a trava de uma
# rodada por vez (a da noite e a de hora em hora não podem ler o mesmo post ao
# mesmo tempo); `ULTIMA_RODADA` é o que a tela usa pra saber que o "Atualizar
# agora" terminou; `AGORA` segura o botão por 10 min — é ela, e não um id fixo
# de job, que evita a fila de leituras repetidas.
CHAVE_RODANDO = "mkt:metricas:rodando"
CHAVE_ULTIMA_RODADA = "mkt:metricas:ultima_rodada"
CHAVE_AGORA = "mkt:metricas:agora"

# O YouTube aceita vários ids na mesma chamada. 50 é o teto da API.
LOTE_YOUTUBE = 50

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# O JSON que o TikTok embute na página pública. É daqui que saem os números.
_RE_TIKTOK = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S
)


_SEGREDO = re.compile(
    r"(access_token|refresh_token|client_secret|token)=[^&\s\"']+", re.IGNORECASE
)


# Prefixo canônico para "o vídeo não existe mais".
#
# Não é falha de leitura: a leitura funcionou e a resposta foi "isto não está
# mais aqui". Tratar os dois como a mesma coisa põe um triângulo de alerta em
# cima de um post que o Eduardo apagou de propósito — e some com o alerta de
# verdade no meio do ruído. A tela lê este prefixo pra separar os dois.
REMOVIDO = "removido:"


def _msg(e: BaseException) -> str:
    """Mensagem de erro pra gravar, sem perder o marcador de removido.

    `f"{type(e).__name__}: {e}"` é bom pra diagnóstico — diz QUE tipo de coisa
    quebrou. Mas empurra o prefixo `removido:` pra longe do começo, e aí a tela
    deixa de reconhecer que o vídeo foi apagado e põe alerta em cima. Quando a
    mensagem já se identifica, ela vai crua.
    """
    texto = str(e)
    return sem_segredo(texto if texto.startswith(REMOVIDO) else f"{type(e).__name__}: {texto}")


def foi_removido(erro: str | None) -> bool:
    return bool(erro) and erro.startswith(REMOVIDO)


def sem_segredo(texto: str) -> str:
    """Tira credencial de mensagem de erro ANTES de ela ser gravada.

    Existe por um vazamento real de 23/09/2026: a Meta recebe o token na QUERY
    STRING, e o `HTTPStatusError` do httpx traz a URL inteira na mensagem. A
    primeira coleta gravou quatro tokens de produção em texto puro na coluna
    `erro` — que a tela mostra no tooltip.

    Duas camadas, porque uma só não basta: o token saiu da URL (vai no cabeçalho
    agora) E toda mensagem passa por aqui. A segunda cobre o erro que vier de
    biblioteca que a gente não controla.
    """
    return _SEGREDO.sub(r"\1=(removido)", texto or "")


def _dia(agora: datetime | None = None) -> datetime:
    """A data de hoje em BRT, à meia-noite.

    A tela é do Eduardo e o fuso é o dele: coleta das 23h de segunda e das 2h
    de terça, em UTC, cairiam em dias diferentes e criariam dois retratos do
    mesmo dia útil.
    """
    agora = agora or datetime.now(UTC)
    brt = agora.astimezone(BRT)
    return brt.replace(hour=0, minute=0, second=0, microsecond=0)


def dia_da_noite(inicio: datetime) -> datetime:
    """O dia do retrato da leitura da noite que começou em `inicio`.

    A da noite é marcada pras 23:47, 13 min antes de o dia virar, e roda na
    fila default — a que atrasa atrás dos webhooks, e que ainda tenta de novo
    em 5 min quando outra rodada está lendo. Começando 00:05, o retrato do fim
    de D ia pro dia D+1, D ficava sem o dele, e a leitura das 23:47 de D+1
    sobrescrevia: o fim de D se perdia (24/09/2026). Até as 6h de Brasília,
    rodada da noite ainda é a noite de ontem, atrasada.
    """
    return _dia(inicio - timedelta(hours=6))


# ─── coleta por rede ───────────────────────────────────────────────────


async def do_tiktok(post_url: str, *, client: httpx.AsyncClient) -> dict[str, Any]:
    """Números de um vídeo do TikTok, da página pública.

    Sem API e sem login: o TikTok embute um JSON no HTML da página do vídeo, e
    ele responde do servidor. Isso é o que permite a coleta do TikTok rodar
    aqui e não no Mac do Eduardo, ao contrário da publicação.

    NÃO É CONTRATO: não existe API, então o TikTok pode renomear a chave ou
    endurecer o bloqueio sem aviso. Por isso o erro é explícito em vez de virar
    zero — gravar zero calado é como a tela passaria a mentir.
    """
    r = await client.get(
        post_url, headers={"User-Agent": _UA, "Accept-Language": "pt-BR,pt;q=0.9"}
    )
    r.raise_for_status()
    m = _RE_TIKTOK.search(r.text)
    if not m:
        raise RuntimeError("a página do TikTok não trouxe o JSON embutido (layout mudou?)")
    dados = json.loads(m.group(1))
    detalhe = (dados.get("__DEFAULT_SCOPE__") or {}).get("webapp.video-detail") or {}
    if detalhe.get("statusCode"):
        # 10204/10231 = removido ou privado. É notícia, não falha de coleta.
        msg = str(detalhe.get("statusMsg") or detalhe.get("statusCode"))
        if "delet" in msg or "privacy" in msg or "unavailable" in msg:
            raise RuntimeError(f"{REMOVIDO} o vídeo não está mais no ar")
        raise RuntimeError(f"o TikTok recusou a página: {msg}")
    item = (detalhe.get("itemInfo") or {}).get("itemStruct") or {}
    st = item.get("statsV2") or item.get("stats") or {}
    if not st:
        raise RuntimeError("a página veio sem o bloco de estatísticas")

    def n(chave: str) -> int | None:
        v = st.get(chave)
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    bruto: dict[str, Any] = dict(st)
    autor = _autor_tiktok(item)
    if autor:
        bruto["autor"] = autor
    return {
        "views": n("playCount"),
        "curtidas": n("diggCount"),
        "comentarios": n("commentCount"),
        "compartilhamentos": n("shareCount"),
        "salvamentos": n("collectCount"),
        "bruto": bruto,
    }


def _autor_tiktok(item: dict[str, Any]) -> dict[str, str | None] | None:
    """De quem é o vídeo, segundo a própria página (24/09/2026).

    Só uma PISTA pra tela ("este vídeo está em @x, não na conta atual") — nunca
    tira nada do desempenho sozinha. Foi assim que os dois TikToks da conta
    antiga da Uranyx passaram despercebidos: o post dizia uma conta, o link
    dizia outra. O layout antigo da página traz `author` como texto e o id em
    `authorId`; sem autor nenhum, a leitura continua valendo, só sem a pista.
    """
    autor = item.get("author")
    if isinstance(autor, str):
        autor = {"uniqueId": autor, "id": item.get("authorId")}
    if not isinstance(autor, dict):
        return None
    handle = str(autor.get("uniqueId") or "").strip().lower() or None
    ident = str(autor.get("id") or "").strip() or None
    if handle is None and ident is None:
        return None
    return {"handle": handle, "id": ident}


async def do_youtube(ids: list[str], refresh_token: str) -> dict[str, dict[str, Any]]:
    """Números de VÁRIOS vídeos de uma vez, indexados pelo id.

    Em lote de propósito: a API aceita 50 ids por chamada, e cada chamada custa
    o mesmo 1 de cota. Um por um gastaria 50x sem ganhar nada.

    Id que não volta na resposta é vídeo apagado, privado ou de outro canal —
    quem chama trata como "não coletado", não como zero.
    """
    at = await youtube_client.access_token_de(refresh_token)
    saida: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=30) as c:
        for i in range(0, len(ids), LOTE_YOUTUBE):
            fatia = ids[i : i + LOTE_YOUTUBE]
            r = await c.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"id": ",".join(fatia), "part": "statistics"},
                headers={"Authorization": f"Bearer {at}"},
            )
            r.raise_for_status()
            for it in r.json().get("items") or []:
                st = it.get("statistics") or {}

                def n(chave: str) -> int | None:
                    v = st.get(chave)
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        return None

                saida[it["id"]] = {
                    "views": n("viewCount"),
                    "curtidas": n("likeCount"),
                    "comentarios": n("commentCount"),
                    # O YouTube não tem compartilhamento nem salvamento na Data
                    # API — ficam NULOS, que é diferente de zero.
                    "bruto": dict(st),
                }
    return saida


async def do_instagram(media_id: str, access_token: str) -> dict[str, Any]:
    """Números de um Reel. Duas chamadas, e a segunda pode falhar.

    A primeira lê campos do próprio nó da mídia (curtidas, comentários) e passa
    com o token que já publica. A segunda é `/insights`, que traz views,
    alcance, compartilhamentos e salvamentos — e exige uma permissão que o
    token de hoje não tem.

    A segunda é BEST-EFFORT de propósito: sem ela ainda sobram curtidas e
    comentários, que é melhor que linha vazia. Quando o token for regerado com
    `instagram_manage_insights`, as views entram sem mexer aqui.
    """
    base = f"{meta_client.GRAPH_HOST}/{meta_client.graph_version()}"
    out: dict[str, Any] = {"bruto": {}}
    async with httpx.AsyncClient(timeout=30) as c:
        # Cabeçalho, NÃO query string: a Graph aceita os dois, e na query o
        # token entra na mensagem de erro do httpx — foi assim que quatro
        # tokens de produção foram parar no banco em 23/09.
        cab = {"Authorization": f"Bearer {access_token}"}
        r = await c.get(f"{base}/{media_id}", params={"fields": "like_count,comments_count"}, headers=cab)
        if r.status_code != 200:
            e = (r.json().get("error") or {}) if r.headers.get("content-type", "").startswith("application/json") else {}
            # code 100 / subcode 33 = "Object with ID ... does not exist". É como
            # a Meta diz que o post foi APAGADO. Descoberto em 23/09/2026: com o
            # token antigo essa resposta vinha mascarada de erro de permissão
            # (code 10), e os posts apagados apareciam como falha de leitura.
            if e.get("code") == 100 and e.get("error_subcode") == 33:
                raise RuntimeError(f"{REMOVIDO} o post não está mais no Instagram")
            r.raise_for_status()
        d = r.json()
        out["curtidas"] = d.get("like_count")
        out["comentarios"] = d.get("comments_count")
        out["bruto"]["media"] = d

        try:
            r = await c.get(
                f"{base}/{media_id}/insights",
                params={"metric": "views,reach,shares,saved"},
                headers=cab,
            )
            r.raise_for_status()
            mapa = {
                it.get("name"): (it.get("values") or [{}])[0].get("value")
                for it in r.json().get("data") or []
            }
            out["views"] = mapa.get("views")
            out["alcance"] = mapa.get("reach")
            out["compartilhamentos"] = mapa.get("shares")
            out["salvamentos"] = mapa.get("saved")
            out["bruto"]["insights"] = mapa
        except httpx.HTTPStatusError as e:
            # Não é falha da coleta: é permissão que falta. Fica anotado no
            # bruto pra quem for investigar, e os outros números seguem.
            out["bruto"]["insights_erro"] = sem_segredo(str(e.response.text))[:300]
    return out


# ─── orquestração ──────────────────────────────────────────────────────


async def _alvos(
    session: AsyncSession, *, agora: datetime, modo: str = "completo"
) -> list[dict[str, Any]]:
    """As publicações que valem ler nesta rodada.

    Só `publicado`, só dentro da janela e nunca o que foi tirado do desempenho
    (Eduardo, 24/09/2026: tirado não é mais lido). Traz a marca junto porque a
    tela agrupa por ela — e a marca vai gravada na linha, em snapshot, pra que
    apagar a conta do cadastro não apague o histórico do que rendeu.

    Cada modo responde uma pergunta diferente:

      completo  a noite: tudo. É o retrato do fim do dia.
      recentes  de hora em hora: (a) o que NUNCA foi tentado e já tem 20 min
                de publicado — é o que faz vídeo novo ganhar número em até
                1 hora, em vez de esperar a noite —; e (b) a falha recente,
                de novo, 50 min depois. Removido não é falha: não volta.
      agora     o botão da tela: o nunca tentado, e o da última semana que
                não foi tentado nos últimos 10 min. Removido fica de fora.

    A ordem é "o mais atrasado primeiro" (`tentado_em` mais velho, nunca
    tentado antes de tudo). O `LIMIT 300` antigo, do mais NOVO pro mais velho,
    deixaria o post antigo sem leitura pra sempre quando o volume crescesse.
    """
    corte = agora - timedelta(days=JANELA_DIAS)
    # A linha MAIS NOVA de cada postagem: quando foi a última tentativa e se
    # ela deu erro. DISTINCT ON pega "a primeira de cada grupo" sem subquery
    # correlacionada.
    ultima = (
        select(
            MarketingPostagemMetrica.postagem_id.label("postagem_id"),
            MarketingPostagemMetrica.updated_at.label("tentado_em"),
            MarketingPostagemMetrica.erro.label("ultimo_erro"),
        )
        .order_by(MarketingPostagemMetrica.postagem_id, MarketingPostagemMetrica.dia.desc())
        .distinct(MarketingPostagemMetrica.postagem_id)
    ).subquery()
    q = (
        select(MarketingPostagem, MarketingCreative.marca_id)
        .join(MarketingCreative, MarketingCreative.id == MarketingPostagem.creative_id)
        .outerjoin(ultima, ultima.c.postagem_id == MarketingPostagem.id)
        .where(
            MarketingPostagem.status == STATUS_PUBLICADO,
            MarketingPostagem.publicado_em.isnot(None),
            MarketingPostagem.publicado_em >= corte,
            MarketingPostagem.fora_do_desempenho_em.is_(None),
        )
    )
    nunca_tentado = ultima.c.postagem_id.is_(None)
    nao_removido = or_(
        ultima.c.ultimo_erro.is_(None), ~ultima.c.ultimo_erro.startswith(REMOVIDO)
    )
    if modo == "recentes":
        q = q.where(
            or_(
                and_(
                    nunca_tentado,
                    MarketingPostagem.publicado_em <= agora - timedelta(minutes=20),
                ),
                and_(
                    ultima.c.ultimo_erro.isnot(None),
                    ~ultima.c.ultimo_erro.startswith(REMOVIDO),
                    MarketingPostagem.publicado_em >= agora - timedelta(days=14),
                    ultima.c.tentado_em <= agora - timedelta(minutes=50),
                ),
            )
        )
    elif modo == "agora":
        q = q.where(
            or_(
                nunca_tentado,
                and_(
                    MarketingPostagem.publicado_em >= agora - timedelta(days=7),
                    ultima.c.tentado_em <= agora - timedelta(minutes=10),
                ),
            ),
            nao_removido,
        )
    linhas = (
        await session.execute(
            q.order_by(
                ultima.c.tentado_em.asc().nulls_first(),
                MarketingPostagem.publicado_em.desc(),
            ).limit(CAP.get(modo, CAP["completo"]))
        )
    ).all()
    return [{"p": p, "marca_id": marca_id} for p, marca_id in linhas]


async def _token_de(session: AsyncSession, rede_social_id: UUID | None) -> dict[str, Any] | None:
    if rede_social_id is None:
        return None
    tok = (
        await session.execute(
            select(RedeSocialToken).where(RedeSocialToken.rede_social_id == rede_social_id)
        )
    ).scalar_one_or_none()
    if tok is None or not tok.token_enc:
        return None
    return decrypt_json(tok.token_enc)


async def _gravar(
    session: AsyncSession,
    alvo: dict[str, Any],
    dia: datetime,
    dados: dict[str, Any],
    *,
    momento: datetime,
) -> None:
    """Um retrato por (postagem, dia). Recoletar no mesmo dia ATUALIZA.

    Atualizar em vez de inserir de novo é de propósito: o número é acumulado,
    então duas coletas no mesmo dia são duas medições do MESMO ponto — a última
    é a boa. O que não pode é sobrescrever o dia ANTERIOR, e é isso que a chave
    (postagem, dia) garante.

    Mas só a leitura BOA sobrescreve número (24/09/2026). Com leitura de hora
    em hora, uma falha às 15h por cima de uma leitura boa às 13h apagava os
    números do dia inteiro — e a tela mostrava o vídeo sem número por causa de
    um soluço do TikTok. Agora o erro (falha ou `removido:`) só grava o erro e
    a hora da tentativa; os números, o `bruto` e o `lido_em` da leitura boa
    ficam. `updated_at` vai à mão porque o upsert não aplica o `onupdate` do
    ORM — sem isso ele ficava congelado no primeiro insert do dia.
    """
    p = alvo["p"]
    base = {
        "postagem_id": p.id,
        "dia": dia,
        "plataforma": p.plataforma,
        "conta": p.conta,
        "marca_id": alvo["marca_id"],
    }
    erro = dados.get("erro")
    if erro:
        st = pg_insert(MarketingPostagemMetrica).values(**base, erro=erro, updated_at=momento)
        st = st.on_conflict_do_update(
            constraint="uq_metrica_postagem_dia",
            set_={"erro": erro, "updated_at": momento},
        )
    else:
        valores = {
            **base,
            "views": dados.get("views"),
            "curtidas": dados.get("curtidas"),
            "comentarios": dados.get("comentarios"),
            "compartilhamentos": dados.get("compartilhamentos"),
            "salvamentos": dados.get("salvamentos"),
            "alcance": dados.get("alcance"),
            "bruto": dados.get("bruto"),
            "erro": None,
            "lido_em": momento,
            "updated_at": momento,
        }
        st = pg_insert(MarketingPostagemMetrica).values(**valores)
        st = st.on_conflict_do_update(
            constraint="uq_metrica_postagem_dia",
            set_={k: v for k, v in valores.items() if k not in ("postagem_id", "dia")},
        )
    await session.execute(st)


async def coletar(
    session: AsyncSession,
    *,
    agora: datetime | None = None,
    modo: str = "completo",
    dia: datetime | None = None,
) -> dict[str, int]:
    """Uma rodada: lê os números de cada publicação e grava o retrato do dia.

    Cada rede é isolada: se o TikTok mudar o layout ou a Meta recusar, as
    outras seguem e a que falhou grava o motivo NA LINHA. Coleta falha baixo —
    a tela mostraria número velho e ninguém notaria —, então o erro tem que
    ficar visível, e é daqui que a tela tira o "coletado em" por plataforma.

    `modo` escolhe QUEM é lido (ver `_alvos`); o resto é igual nos três. O dia
    do retrato sai do começo da rodada — a rodada da noite que atravessa a
    meia-noite não pode partir o mesmo dia em dois —, mas a hora de cada
    leitura (`lido_em`) é a do momento em que ela voltou: é ela que diz a idade
    do vídeo na hora da leitura, e a comparação "na mesma idade" depende disso.
    `dia` fixa o dia do retrato: o worker passa o da noite (`dia_da_noite`),
    pra leitura da noite que atrasou e começou depois da meia-noite.
    """
    dia = dia or _dia(agora)
    alvos = await _alvos(session, agora=agora or datetime.now(UTC), modo=modo)
    r = {"total": len(alvos), "ok": 0, "falhou": 0}
    if not alvos:
        return r

    # YouTube em lote POR CONTA: o token é por conta, e a API aceita 50 ids na
    # mesma chamada. Um por um gastaria cota à toa.
    por_rede: dict[UUID, list[dict[str, Any]]] = {}
    for a in alvos:
        if a["p"].plataforma == "youtube" and a["p"].post_external_id and a["p"].rede_social_id:
            por_rede.setdefault(a["p"].rede_social_id, []).append(a)

    prontos: dict[UUID, dict[str, Any]] = {}
    for rede_id, doGrupo in por_rede.items():
        try:
            seg = await _token_de(session, rede_id)
            refresh = (seg or {}).get("refresh_token") or (seg or {}).get("access_token")
            if not refresh:
                raise RuntimeError("a conta não tem token conectado")
            mapa = await do_youtube([a["p"].post_external_id for a in doGrupo], refresh)
            for a in doGrupo:
                achado = mapa.get(a["p"].post_external_id)
                prontos[a["p"].id] = (
                    achado
                    if achado
                    else {"erro": f"{REMOVIDO} o vídeo não está mais no canal"}
                )
        except Exception as e:  # noqa: BLE001 — a falha de uma conta não derruba as outras
            for a in doGrupo:
                prontos[a["p"].id] = {"erro": _msg(e)[:400]}

    primeira_do_tiktok = True
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cliente:
        for a in alvos:
            p = a["p"]
            try:
                if p.plataforma == "youtube":
                    dados = prontos.get(p.id) or {"erro": "sem id do vídeo na postagem"}
                elif p.plataforma == "tiktok":
                    if not p.post_url:
                        raise RuntimeError("a postagem não guardou o link do vídeo")
                    # Sem API, é a página pública: rajada do mesmo IP vira
                    # bloqueio. A primeira da rodada não espera.
                    if not primeira_do_tiktok:
                        await asyncio.sleep(PAUSA_TIKTOK_S)
                    primeira_do_tiktok = False
                    dados = await do_tiktok(p.post_url, client=cliente)
                elif p.plataforma in ("instagram", "facebook"):
                    if not p.post_external_id:
                        raise RuntimeError("a postagem não guardou o id da mídia")
                    seg = await _token_de(session, p.rede_social_id)
                    at = (seg or {}).get("page_access_token") or (seg or {}).get("access_token")
                    if not at:
                        raise RuntimeError("a conta não tem token conectado")
                    dados = await do_instagram(p.post_external_id, at)
                else:
                    dados = {"erro": f"não sei ler métrica de {p.plataforma}"}
            except Exception as e:  # noqa: BLE001 — um post ruim não derruba a rodada
                dados = {"erro": _msg(e)[:400]}

            await _gravar(session, a, dia, dados, momento=agora or datetime.now(UTC))
            r["falhou" if dados.get("erro") else "ok"] += 1

    await session.commit()
    logger.info("marketing_metricas_coletadas", modo=modo, **r)
    return r
