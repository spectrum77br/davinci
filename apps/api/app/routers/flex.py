"""Flex por anúncio — a tela (Pós-venda → Logística, aba Flex › Anúncios).

Projeto Flex, etapa 3 (services/flex_motor). Mesmo recurso da Logística:
`logistica:view` para ver, `logistica:edit` para agir (sincronizar, aprovar,
emergência); admin passa sempre (`require_permission`).

  GET  /api/flex/config        modo, contas, números (nada de segredo)
  GET  /api/flex/anuncios      estado por anúncio (filtros, busca e o resumo
                               do topo da tela)
  POST /api/flex/sincronizar   roda o motor agora (job no worker), no modo atual
  POST /api/flex/anuncios/{conta}/{anuncio}/aprovar   aprova LIGAR
  POST /api/flex/emergencia    desliga tudo das contas permitidas
                               (observar/desligado: só simula)
  GET  /api/flex/log           últimas linhas da trilha
  GET  /api/flex/pedidos       pedidos Flex detectados (com o aviso do .sp;
                               `abertos=true` tira os que já saíram)
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker_pool
from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    BlingOrder,
    FlexAnuncioEstado,
    FlexLog,
    FlexPedido,
    Integration,
    ProductLink,
    User,
)
from app.models.flex import FLEX_ACOES
from app.schemas.flex import (
    FlexAnuncioOut,
    FlexAnunciosOut,
    FlexAprovarOut,
    FlexConfigOut,
    FlexContaOut,
    FlexLogOut,
    FlexPedidoOut,
    FlexResumoOut,
    FlexSincronizarOut,
)
from app.services import flex_config, flex_envio, flex_motor, flex_textos

logger = structlog.get_logger()
router = APIRouter(prefix="/api/flex", tags=["flex"])

Ver = Annotated[User, Depends(require_permission("logistica", "view"))]
Agir = Annotated[User, Depends(require_permission("logistica", "edit"))]
Sessao = Annotated[AsyncSession, Depends(get_session)]


def _plataforma_txt(integ: Integration | None) -> str | None:
    if integ is None:
        return None
    return getattr(integ.platform, "value", str(integ.platform))


@router.get("/config", response_model=FlexConfigOut)
async def config(session: Sessao, _user: Ver) -> FlexConfigOut:
    s = get_settings()
    cfg = flex_motor.ConfigFlex.das_configuracoes()
    ids = sorted(flex_config.contas(), key=str)
    permitidas = await flex_motor.integracoes_permitidas(session, ids)
    contas = [
        FlexContaOut(
            id=i,
            nome=permitidas[i].name if i in permitidas else None,
            plataforma=_plataforma_txt(permitidas.get(i)),
            existe=i in permitidas,
        )
        for i in ids
    ]
    m = flex_config.modo()
    return FlexConfigOut(
        modo=m,
        pode_escrever=flex_config.pode_escrever(m),
        contas=contas,
        n_liga=cfg.n_liga,
        n_desliga=cfg.n_desliga,
        kits=cfg.kits,
        max_anuncios_por_familia=cfg.max_anuncios_por_familia,
        teto_escritas_por_rodada=max(0, int(s.flex_teto_escritas_por_rodada or 0)),
        shopee_canais=sorted(flex_envio.canais_shopee_flex()),
        shopee_escrita=bool(s.flex_shopee_escrita),
        intervalo_min=max(1, int(s.flex_intervalo_min or 15)),
        pedido_no_sp=bool(s.flex_pedido_no_sp),
    )


def _titulo_subq():
    """Título do anúncio (o primeiro vínculo que tiver) — a tela mostra."""
    return (
        select(func.min(ProductLink.listing_title))
        .where(
            ProductLink.integration_id == FlexAnuncioEstado.integration_id,
            ProductLink.external_id == FlexAnuncioEstado.external_id,
        )
        .correlate(FlexAnuncioEstado)
        .scalar_subquery()
    )


def _anuncio_out(est: FlexAnuncioEstado, conta: str | None, titulo: str | None) -> FlexAnuncioOut:
    out = FlexAnuncioOut.model_validate(est)
    out.conta = conta
    out.titulo = titulo
    out.motivo_claro = flex_textos.motivo_claro(est.motivo)
    return out


async def _resumo(session: AsyncSession) -> FlexResumoOut:
    """O quadro do topo da tela: todos os anúncios avaliados, sem os filtros
    da lista (o dono vê de cara quantos esperam por ele)."""
    est = FlexAnuncioEstado
    row = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(est.observado == "ligado"),
                func.count().filter(est.aguardando_aprovacao.is_(True)),
                func.count().filter(est.observado == "ligado", est.desejado != "ligado"),
                func.count().filter(est.observado.is_(None)),
            ).select_from(est)
        )
    ).one()
    return FlexResumoOut(
        avaliados=int(row[0] or 0),
        ligados=int(row[1] or 0),
        aguardando=int(row[2] or 0),
        desligar=int(row[3] or 0),
        nao_lidos=int(row[4] or 0),
    )


@router.get("/anuncios", response_model=FlexAnunciosOut)
async def anuncios(
    session: Sessao,
    _user: Ver,
    integration_id: UUID | None = None,
    plataforma: Literal["ml", "shopee"] | None = None,
    desejado: Literal["ligado", "desligado", "inelegivel"] | None = None,
    # O que a plataforma disse da última leitura; `nao_lido` = ainda não leu.
    observado: Literal["ligado", "desligado", "nao_lido"] | None = None,
    aguardando: bool | None = None,
    # `desligar=true`: ligado na plataforma, mas a regra não quer — o mesmo
    # número do resumo ("ligados que deveriam desligar").
    desligar: bool = False,
    # Busca da tela: id do anúncio, família ou título (sem curinga: `%`/`_`
    # digitados são letras).
    busca: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FlexAnunciosOut:
    filtros = []
    if integration_id is not None:
        filtros.append(FlexAnuncioEstado.integration_id == integration_id)
    if plataforma is not None:
        filtros.append(FlexAnuncioEstado.plataforma == plataforma)
    if desejado is not None:
        filtros.append(FlexAnuncioEstado.desejado == desejado)
    if observado == "nao_lido":
        filtros.append(FlexAnuncioEstado.observado.is_(None))
    elif observado is not None:
        filtros.append(FlexAnuncioEstado.observado == observado)
    if aguardando is not None:
        filtros.append(FlexAnuncioEstado.aguardando_aprovacao.is_(aguardando))
    if desligar:
        filtros.append(FlexAnuncioEstado.observado == "ligado")
        filtros.append(FlexAnuncioEstado.desejado != "ligado")
    termo = (busca or "").strip()
    if termo:
        filtros.append(
            or_(
                FlexAnuncioEstado.external_id.icontains(termo, autoescape=True),
                FlexAnuncioEstado.familias.icontains(termo, autoescape=True),
                exists().where(
                    ProductLink.integration_id == FlexAnuncioEstado.integration_id,
                    ProductLink.external_id == FlexAnuncioEstado.external_id,
                    ProductLink.listing_title.icontains(termo, autoescape=True),
                ),
            )
        )
    total = int(
        await session.scalar(select(func.count()).select_from(FlexAnuncioEstado).where(*filtros))
        or 0
    )
    rows = await session.execute(
        select(FlexAnuncioEstado, Integration.name, _titulo_subq())
        .outerjoin(Integration, Integration.id == FlexAnuncioEstado.integration_id)
        .where(*filtros)
        # Quem espera uma pessoa primeiro; depois o que está/quer ligado.
        .order_by(
            FlexAnuncioEstado.aguardando_aprovacao.desc(),
            case((FlexAnuncioEstado.observado == "ligado", 0), else_=1),
            case((FlexAnuncioEstado.desejado == "ligado", 0), else_=1),
            FlexAnuncioEstado.atualizado_em.desc(),
            FlexAnuncioEstado.external_id,
        )
        .limit(limit)
        .offset(offset)
    )
    return FlexAnunciosOut(
        total=total,
        itens=[_anuncio_out(est, nome, titulo) for est, nome, titulo in rows.all()],
        resumo=await _resumo(session),
    )


@router.post("/sincronizar", response_model=FlexSincronizarOut)
async def sincronizar(user: Agir) -> FlexSincronizarOut:
    """Roda o motor agora, no modo em vigor (o worker pega o job em
    segundos — a rodada pode levar minutos com muitos anúncios, por isso não
    roda dentro do pedido HTTP). Modo desligado: não faz nada."""
    m = flex_config.modo()
    if m == flex_config.MODO_DESLIGADO:
        return FlexSincronizarOut(enfileirado=False, modo=m, motivo="flex_modo=desligado")
    if not flex_config.contas():
        return FlexSincronizarOut(enfileirado=False, modo=m, motivo="nenhuma conta em flex_contas")
    pool = await worker_pool.get_arq_pool()
    # Um clique por minuto vira UMA rodada (id do job pelo minuto).
    job = await pool.enqueue_job(
        "flex_motor_run", str(user.id), _job_id=f"flex_motor_manual:{int(time.time() // 60)}"
    )
    return FlexSincronizarOut(
        enfileirado=job is not None,
        modo=m,
        motivo=None if job is not None else "já há uma rodada pedida neste minuto",
    )


async def _estado_out(session: AsyncSession, integration_id: UUID, external_id: str):
    row = (
        await session.execute(
            select(FlexAnuncioEstado, Integration.name, _titulo_subq())
            .outerjoin(Integration, Integration.id == FlexAnuncioEstado.integration_id)
            .where(
                FlexAnuncioEstado.integration_id == integration_id,
                FlexAnuncioEstado.external_id == external_id,
            )
        )
    ).first()
    if row is None:
        return None
    est, nome, titulo = row
    return _anuncio_out(est, nome, titulo)


_ERRO_HTTP = {
    "conta_nao_permitida": status.HTTP_403_FORBIDDEN,
    "nao_avaliado": status.HTTP_404_NOT_FOUND,
}


@router.post(
    "/anuncios/{integration_id}/{external_id}/aprovar", response_model=FlexAprovarOut
)
async def aprovar(
    integration_id: UUID, external_id: str, session: Sessao, user: Agir
) -> FlexAprovarOut:
    """Aprova LIGAR o Flex neste anúncio (a orientação do ML é que a ativação
    seja decisão deliberada do vendedor). Em piloto/ativo o motor aplica na
    hora; em observar fica registrada. Depois de uma recusa da plataforma,
    é também o "tente de novo"."""
    try:
        res = await flex_motor.aprovar(integration_id, external_id.strip(), por=user.id)
    except flex_motor.FlexRegraError as exc:
        raise HTTPException(
            _ERRO_HTTP.get(exc.codigo, status.HTTP_409_CONFLICT),
            detail={"code": exc.codigo, "detalhe": exc.detalhe},
        ) from exc
    session.expire_all()
    return FlexAprovarOut(
        aprovado=bool(res.get("aprovado")),
        modo=str(res.get("modo")),
        aplicado=bool(res.get("aplicado")),
        motor=res.get("motor"),
        estado=await _estado_out(session, integration_id, external_id.strip()),
    )


@router.post("/emergencia")
async def emergencia(user: Agir) -> dict:
    """Desliga o Flex de tudo nas contas permitidas e tira toda aprovação.
    Em observar (ou desligado) só simula — a resposta diz o que faria."""
    logger.warning("flex_emergencia_pedida", user_id=str(user.id))
    return await flex_motor.emergencia(por=user.id)


@router.get("/log", response_model=list[FlexLogOut])
async def log(
    session: Sessao,
    _user: Ver,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    integration_id: UUID | None = None,
    external_id: str | None = None,
    acao: str | None = None,
) -> list[FlexLogOut]:
    if acao is not None and acao not in FLEX_ACOES:
        raise HTTPException(422, detail={"code": "acao_invalida"})
    filtros = []
    if integration_id is not None:
        filtros.append(FlexLog.integration_id == integration_id)
    if external_id:
        filtros.append(FlexLog.external_id == external_id.strip())
    if acao:
        filtros.append(FlexLog.acao == acao)
    rows = await session.execute(
        select(FlexLog, Integration.name)
        .outerjoin(Integration, Integration.id == FlexLog.integration_id)
        .where(*filtros)
        .order_by(FlexLog.criado_em.desc(), FlexLog.id.desc())
        .limit(limit)
    )
    out: list[FlexLogOut] = []
    for linha, nome in rows.all():
        item = FlexLogOut.model_validate(linha)
        item.conta = nome
        out.append(item)
    return out


@router.get("/pedidos", response_model=list[FlexPedidoOut])
async def pedidos(
    session: Sessao,
    _user: Ver,
    so_alerta: bool = False,
    # Só os que ainda esperam alguém: some o pedido que no Bling já saiu (em
    # andamento), foi atendido, cancelado ou excluído — a régua do saldo Flex.
    # Pedido que o espelho ainda não tem continua (não se sabe: mostra).
    abertos: bool = False,
    dias: Annotated[int, Query(ge=1, le=90)] = 30,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> list[FlexPedidoOut]:
    """Pedidos Flex detectados nos últimos `dias`, os com aviso (o .sp não
    cobre — etapa 2) primeiro, depois pelo prazo de despacho."""
    corte = datetime.now(UTC) - timedelta(days=dias)
    filtros = [FlexPedido.detectado_em >= corte]
    if so_alerta:
        filtros.append(FlexPedido.alerta.is_not(None))
    if abertos:
        filtros.append(
            ~exists().where(
                BlingOrder.bling_id == FlexPedido.bling_id,
                BlingOrder.situacao.in_(flex_motor.SITUACOES_FECHADAS),
            )
        )
    rows = await session.execute(
        select(FlexPedido, Integration.name)
        .outerjoin(Integration, Integration.id == FlexPedido.integration_id)
        .where(and_(*filtros))
        .order_by(
            case((FlexPedido.alerta.is_not(None), 0), else_=1),
            FlexPedido.prazo.asc().nulls_last(),
            FlexPedido.detectado_em.desc(),
        )
        .limit(limit)
    )
    linhas = rows.all()
    ids = [fp.bling_id for fp, _ in linhas]
    itens: dict[int, dict] = {}
    if ids:
        for r in (
            await session.execute(
                select(
                    BlingOrder.bling_id,
                    BlingOrder.numero,
                    BlingOrder.situacao,
                    BlingOrder.item_codigo,
                )
                .where(BlingOrder.bling_id.in_(ids))
                .order_by(BlingOrder.bling_id, BlingOrder.item_index)
            )
        ).all():
            d = itens.setdefault(
                int(r.bling_id), {"numero": r.numero, "situacao": r.situacao, "skus": []}
            )
            if r.item_codigo:
                d["skus"].append(r.item_codigo)
    return [
        FlexPedidoOut(
            bling_id=fp.bling_id,
            numero=itens.get(fp.bling_id, {}).get("numero"),
            plataforma=fp.plataforma,
            integration_id=fp.integration_id,
            conta=nome,
            numeroloja=fp.numeroloja,
            envio_tipo=fp.envio_tipo,
            prazo=fp.prazo,
            detectado_em=fp.detectado_em,
            no_sp=fp.no_sp,
            alerta=fp.alerta,
            situacao=itens.get(fp.bling_id, {}).get("situacao"),
            skus=itens.get(fp.bling_id, {}).get("skus", []),
        )
        for fp, nome in linhas
    ]
