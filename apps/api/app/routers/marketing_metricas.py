"""Quanto cada vídeo rendeu nas redes — a tela Marketing › Desempenho.

Pedido do Eduardo (23/09/2026): ver as views e interações POR MARCA, com o
total somado e o detalhe de cada rede. E em 24/09/2026: "trackear o que cada
vídeo deu de retorno pra saber o que investir", "esses vídeos ainda não
aparecem… será que demora?" e "tirar aqueles 2 tiktoks da outra conta da
uranyx".

As contas moram em `services/marketing/desempenho.py`, sem banco; aqui só se
carrega o universo e se escreve. Três mudanças de 24/09 que valem dizer:

  A TELA PARTE DA POSTAGEM, não do retrato. Antes, post sem retrato não
  existia — e o retrato só nascia na leitura da madrugada, até 16 h depois de
  publicar. Agora o post aparece na hora, como "aguardando 1ª leitura".

  ESCOPO DE EQUIPE, como em Criativos: usuário de agência vê só os criativos
  da equipe dele. Até 23/09 este endpoint não tinha escopo nenhum.

  NINGUÉM EDITA O BANCO pra tirar um post do desempenho: o PATCH marca, e
  "Voltar a contar" desmarca.

E a ressalva que a tela carrega, não esconde em rodapé: "view" não quer dizer
a mesma coisa nas três redes — o limiar de segundos é diferente em cada uma.

06/10/2026: a tela passou a mostrar views somadas (sem o índice "× o normal"),
os vídeos mais vistos e, em "Onde vale investir", as views ganhas na semana e
no mês. O produto é agrupado por aparelho, e cada SKU do criativo ganha o
nome do produto pelo SKU exato ou, sem ele, pela base (`_nomes_dos_produtos`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    Marca,
    MarketingCreative,
    MarketingPostagem,
    MarketingPostagemMetrica,
    MarketingRoteiro,
    Product,
    RedeSocial,
    User,
)
from app.models.marketing_postagem import STATUS_PUBLICADO
from app.redis_client import redis
from app.routers.marketing_creatives import _ensure_equipe, _user_equipes
from app.services.marketing import desempenho
from app.services.marketing.metricas import (
    CHAVE_AGORA,
    CHAVE_RODANDO,
    CHAVE_ULTIMA_RODADA,
)
from app.worker_pool import get_arq_ui_pool

logger = structlog.get_logger()

router = APIRouter(prefix="/api/marketing/metricas", tags=["marketing_metricas"])

# O "Atualizar agora" fica travado por 10 min depois de um clique: a leitura
# da página do TikTok é do IP do servidor, e clique repetido é rajada.
TRAVA_AGORA_S = 600

_NUMEROS = ("views", "curtidas", "comentarios", "compartilhamentos", "salvamentos", "alcance")


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _estado_da_coleta(agora: datetime) -> dict[str, Any]:
    """O que o Redis sabe da coleta. Falha BRANDA: sem Redis a tela ainda
    mostra os números, só sem "rodando agora" e sem a hora do botão."""
    out: dict[str, Any] = {"em_andamento": False, "pode_atualizar_em": None, "ultima_rodada": None}
    try:
        out["em_andamento"] = bool(await redis.exists(CHAVE_RODANDO))
        ttl = await redis.ttl(CHAVE_AGORA)
        if ttl and ttl > 0:
            out["pode_atualizar_em"] = _iso(agora + timedelta(seconds=ttl))
        bruto = await redis.get(CHAVE_ULTIMA_RODADA)
        if bruto:
            out["ultima_rodada"] = json.loads(bruto)
    except Exception as e:  # noqa: BLE001 — Redis fora não derruba a tela
        logger.warning("marketing_metricas_redis_indisponivel", err=str(e)[:200])
    return out


async def _nomes_dos_produtos(
    session: AsyncSession, posts: list[dict[str, Any]]
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Os nomes que dão o APARELHO de cada SKU dos criativos (ver
    `desempenho.grupo_produto`): (SKU exato → nome, base → nomes).

    Exato: o produto ATIVO com aquele SKU (sem diferenciar maiúscula) — é o
    que diz que "b055.20" é a mala de tamanho 20, e não a de 8. Base ("dg017"
    de "dg017.pi"): os produtos ATIVOS e SIMPLES com essa base (kit, com "+"
    no SKU ou formato E, não é o aparelho). Sem isso a tela dizia "SKU dg017
    (sem produto ligado)"."""
    skus: set[str] = set()
    for p in posts:
        skus.update(desempenho.skus_digitados(p["sku"]))
        if p.get("produto_sku"):
            skus.add(p["produto_sku"].strip().lower())
    if not skus:
        return {}, {}
    bases = {b for t in skus if (b := desempenho.base_sku(t))}
    sku_col = func.lower(Product.sku)
    por_sku: dict[str, str] = {}
    for sku, nome in (
        await session.execute(
            select(sku_col, Product.name).where(sku_col.in_(skus), Product.situacao == "A")
        )
    ).all():
        atual = por_sku.get(sku)
        if nome and (atual is None or (len(nome), nome) < (len(atual), atual)):
            por_sku[sku] = nome
    base_col = func.lower(func.split_part(Product.sku, ".", 1))
    por_base: dict[str, set[str]] = {}
    for base, nome in (
        await session.execute(
            select(base_col, Product.name).where(
                base_col.in_(bases),
                Product.situacao == "A",
                ~Product.sku.contains("+"),
                or_(Product.formato.is_(None), Product.formato == "S"),
            )
        )
    ).all():
        if nome:
            por_base.setdefault(base, set()).add(nome)
    return por_sku, {b: sorted(ns) for b, ns in por_base.items()}


@router.get("")
async def metricas(
    session: Annotated[AsyncSession, Depends(get_session)],
    # Mesma permissão da aba Criativos: quem vê o que foi publicado vê o que rendeu.
    user: Annotated[User, Depends(require_permission("marketing_criativos", "view"))],
    dias: Annotated[int, Query(ge=1, le=365)] = 30,
    marco: Annotated[int, Query()] = desempenho.MARCO_PADRAO,
    marca_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    """Desempenho dos vídeos publicados — o contrato "versao": 2."""
    if marco not in desempenho.MARCOS:
        raise HTTPException(
            422,
            detail={"code": "marco_invalido", "aceitos": list(desempenho.MARCOS)},
        )
    agora = datetime.now(UTC)
    # O universo cobre a base da conta (90 dias) mesmo quando a janela é
    # menor: o "normal" de um vídeo de ontem são os vídeos do último trimestre.
    corte = desempenho.corte_universo(agora, dias)
    # E vai mais longe só pras somas por rede: o post que hoje passou de 90
    # dias ainda era lido — e rendia — no período anterior. Desses, só os que
    # contam (o tirado do desempenho não soma em lugar nenhum).
    no_universo = (
        MarketingPostagem.status == STATUS_PUBLICADO,
        MarketingPostagem.publicado_em.isnot(None),
        # O mês de "Onde vale investir" também soma o que post velho ganhou.
        MarketingPostagem.publicado_em
        >= desempenho.corte_soma(agora, max(dias, desempenho.MES_DIAS)),
        or_(
            MarketingPostagem.publicado_em >= corte,
            MarketingPostagem.fora_do_desempenho_em.is_(None),
        ),
    )

    posts_q = (
        select(
            MarketingPostagem,
            MarketingCreative,
            Marca.nome,
            Product.name,
            Product.sku,
            MarketingRoteiro.titulo,
            RedeSocial.conta,
        )
        .join(MarketingCreative, MarketingCreative.id == MarketingPostagem.creative_id)
        .outerjoin(Marca, Marca.id == MarketingCreative.marca_id)
        .outerjoin(Product, Product.id == MarketingCreative.product_id)
        .outerjoin(MarketingRoteiro, MarketingRoteiro.id == MarketingCreative.roteiro_id)
        .outerjoin(RedeSocial, RedeSocial.id == MarketingPostagem.rede_social_id)
        .where(*no_universo)
    )
    posts: list[dict[str, Any]] = []
    posts_antigos: list[dict[str, Any]] = []
    for p, c, marca_nome, produto_nome, produto_sku, roteiro_titulo, conta_atual in (
        await session.execute(posts_q)
    ).all():
        (posts if p.publicado_em >= corte else posts_antigos).append(
            {
                "id": p.id,
                "creative_id": p.creative_id,
                "plataforma": p.plataforma,
                "conta": p.conta,
                "conta_atual": conta_atual,
                "rede_social_id": p.rede_social_id,
                "post_url": p.post_url,
                "publicado_em": p.publicado_em,
                "origem": p.origem,
                "legenda": p.legenda,
                "fora_do_desempenho_em": p.fora_do_desempenho_em,
                "fora_do_desempenho_motivo": p.fora_do_desempenho_motivo,
                "marca_id": c.marca_id,
                "marca": marca_nome,
                "equipe": c.equipe,
                "sku": c.sku,
                "modelo": c.modelo,
                "product_id": c.product_id,
                "produto_nome": produto_nome,
                "produto_sku": produto_sku,
                "roteiro_id": c.roteiro_id,
                "roteiro_titulo": roteiro_titulo,
            }
        )

    # Os retratos, projetados: só o que a conta usa. O `bruto` inteiro (a
    # resposta crua da rede) pesaria à toa — dele só sai o autor do TikTok.
    met = MarketingPostagemMetrica
    linhas_q = (
        select(
            met.postagem_id,
            met.dia,
            met.lido_em,
            met.updated_at,
            met.erro,
            *(getattr(met, k) for k in _NUMEROS),
            met.bruto["autor"].label("autor"),
        )
        .join(MarketingPostagem, MarketingPostagem.id == met.postagem_id)
        .where(*no_universo)
    )
    linhas = [dict(r._mapping) for r in (await session.execute(linhas_q)).all()]
    # Desde quando existe leitura: sem isto a tela compararia o período com um
    # "anterior" em que ninguém lia nada.
    inicio = (await session.execute(select(func.min(met.dia)))).scalar_one_or_none()
    nomes_por_sku, nomes_por_base = await _nomes_dos_produtos(session, posts + posts_antigos)

    return desempenho.montar(
        posts,
        linhas,
        dias=dias,
        marco=marco,
        agora=agora,
        marca_id=marca_id,
        equipes_permitidas=_user_equipes(user),
        inicio_da_coleta=inicio,
        coleta=await _estado_da_coleta(agora),
        posts_antigos=posts_antigos,
        nomes_por_base=nomes_por_base,
        nomes_por_sku=nomes_por_sku,
    )


@router.post("/atualizar", status_code=202)
async def atualizar_agora(
    _u: Annotated[User, Depends(require_permission("marketing_criativos", "view"))],
) -> dict[str, Any]:
    """Pede uma leitura já, em vez de esperar o próximo :47.

    A trava de 10 min no Redis é o que impede o clique repetido de virar
    rajada — e NÃO um `_job_id` fixo: o worker da fila UI guarda o resultado
    por 1 hora, e com id fixo todo clique dessa hora sumiria calado.
    """
    agora = datetime.now(UTC)
    try:
        pegou = await redis.set(CHAVE_AGORA, _iso(agora), nx=True, ex=TRAVA_AGORA_S)
        if not pegou:
            ttl = await redis.ttl(CHAVE_AGORA)
            raise HTTPException(
                429,
                detail={
                    "code": "atualizacao_recente",
                    "pode_atualizar_em": _iso(agora + timedelta(seconds=max(ttl or 0, 0))),
                },
            )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("marketing_metricas_atualizar_redis_falhou", err=str(e)[:200])
        raise HTTPException(503, detail={"code": "fila_indisponivel"}) from e
    try:
        # Fila UI: a default pode estar horas atrás dos webhooks.
        await (await get_arq_ui_pool()).enqueue_job("marketing_postagens_metricas_agora")
    except Exception as e:  # noqa: BLE001
        logger.warning("marketing_metricas_atualizar_fila_falhou", err=str(e)[:200])
        # Não enfileirou: solta a trava, senão o botão fica 10 min travado
        # por um pedido que nunca existiu.
        try:
            await redis.delete(CHAVE_AGORA)
        except Exception:  # noqa: BLE001, S110 — o TTL solta sozinho
            pass
        raise HTTPException(503, detail={"code": "fila_indisponivel"}) from e
    return {
        "enfileirado": True,
        "pedido_em": _iso(agora),
        "pode_atualizar_em": _iso(agora + timedelta(seconds=TRAVA_AGORA_S)),
    }


class ContarNoDesempenho(BaseModel):
    contar: bool
    motivo: str | None = None


@router.patch("/postagens/{postagem_id}/desempenho")
async def contar_no_desempenho(
    postagem_id: UUID,
    payload: ContarNoDesempenho,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, Any]:
    """Tira um post do desempenho, ou devolve.

    Eduardo, 24/09/2026: "tirar aqueles 2 tiktoks da outra conta da uranyx".
    Da próxima vez é um clique, não um UPDATE no banco. Tirado sai de toda
    soma, média e comparação e deixa de ser lido; o histórico fica, e o motivo
    é obrigatório — daqui a um mês ninguém lembra por que o vídeo sumiu.
    """
    p = await session.get(MarketingPostagem, postagem_id)
    if p is None:
        raise HTTPException(404, detail={"code": "not_found"})
    criativo = await session.get(MarketingCreative, p.creative_id)
    if criativo is not None:
        _ensure_equipe(user, criativo)
    if p.status != STATUS_PUBLICADO:
        raise HTTPException(400, detail={"code": "nao_publicada"})

    if payload.contar:
        p.fora_do_desempenho_em = None
        p.fora_do_desempenho_motivo = None
        p.fora_do_desempenho_por = None
    else:
        motivo = (payload.motivo or "").strip()
        if not 3 <= len(motivo) <= 200:
            raise HTTPException(422, detail={"code": "motivo_obrigatorio"})
        p.fora_do_desempenho_em = datetime.now(UTC)
        p.fora_do_desempenho_motivo = motivo
        p.fora_do_desempenho_por = user.id
    await session.commit()
    logger.info(
        "marketing_desempenho_contar",
        user_id=str(user.id),
        postagem_id=str(postagem_id),
        contar=payload.contar,
    )
    return {
        "postagem_id": str(p.id),
        "contar": payload.contar,
        "motivo": p.fora_do_desempenho_motivo,
        "em": _iso(p.fora_do_desempenho_em) if p.fora_do_desempenho_em else None,
    }
