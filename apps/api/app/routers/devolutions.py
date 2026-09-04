import re
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from openpyxl import Workbook
from sqlalchemy import desc, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    BlingOrder,
    Chamado,
    ChamadoMensagem,
    DevolucaoAnexo,
    DevolucaoRastreio,
    Devolution,
    Product,
    Refund,
    User,
)
from app.schemas.devolutions import (
    AcompanhamentoItemOut,
    AcompanhamentoOut,
    AcompanhamentoRastreioOut,
    AcompanhamentoRastreioPatch,
    BlingStockResultOut,
    DevolucaoAnexoOut,
    DevolutionCreate,
    DevolutionLookupOut,
    DevolutionOut,
    DevolutionPage,
    DevolutionPatch,
    DevolutionProductOut,
    SkuSuffixesOut,
    SkuSuffixVariant,
    StockCorrectionIn,
)
from app.services import chamados_devolucao
from app.services.devolution_stock_return import (
    _STOCK_TRIGGER_CONDICOES,
    _SUFFIX_TAGS,
    _sku_base,
    _sku_tag,
    apply_order_situacao,
    record_stock_movement,
    return_product_to_bling_stock,
    reverse_stock_movement,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/devolutions", tags=["devolutions"])

_REFUND_CONDICOES = {"Extraviado", "Manutenção"}
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def _custo_e_tecnico_preenchidos(row: Devolution) -> bool:
    """Custo de manutenção (> 0) E técnico preenchidos → a devolução vai direto
    pro reembolso (Eduardo 03/09: "quando for preenchido custo e tecnico ja vai
    direto para reebolso"). Só LIGA o checkbox — nunca desliga."""
    return bool(row.custo_manutencao) and bool((row.tecnico or "").strip())


def _maybe_create_refund(session: AsyncSession, row: Devolution, condicao: str) -> None:
    prejuizo = (
        row.custo_produto or 0
        if condicao == "Extraviado"
        else row.custo_manutencao or 0
    )
    refund = Refund(
        data=row.data,
        pedido_bling=row.pedido_bling,
        pedido_marketplace=row.pedido_marketplace,
        conta=row.conta,
        tipo=condicao,
        prejuizo=prejuizo,
        reembolso=0,
    )
    session.add(refund)
    logger.info("refund_auto_created", pedido_bling=row.pedido_bling, tipo=condicao)


async def _sync_manutencao_to_refund(
    session: AsyncSession, row: Devolution, delta: float
) -> None:
    """Aplica a variação do custo_manutencao da devolução no `reembolso` do refund de
    Manutenção correspondente — mesmo pedido + conta. O custo de manutenção é sempre
    um DÉBITO: entra negativo no reembolso (custo 30 -> reembolso -= 30), somando ao
    valor já existente (ex.: -10 + (-30) = -40). Trabalha por delta (`novo - anterior`),
    então reedições do custo só movem o reembolso pela diferença (não contam duas vezes).
    """
    if not delta:
        return
    conds = [
        Refund.tipo == "Manutenção",
        func.btrim(Refund.conta) == (row.conta or "").strip(),
    ]
    if row.pedido_bling:
        conds.append(Refund.pedido_bling == row.pedido_bling)
    elif row.pedido_marketplace:
        conds.append(Refund.pedido_marketplace == row.pedido_marketplace)
    else:
        return  # sem pedido não há como casar o refund
    refund = (
        (await session.execute(select(Refund).where(*conds).order_by(desc(Refund.created_at))))
        .scalars()
        .first()
    )
    if refund is None:
        return
    refund.reembolso = (refund.reembolso or 0) - delta
    logger.info(
        "refund_reembolso_synced",
        refund_id=str(refund.id),
        delta=delta,
        reembolso=refund.reembolso,
    )


settings = get_settings()
SCHEMA = settings.database_schema


def _search_clause(search: str):
    q = f"%{search}%"
    return or_(
        Devolution.pedido_bling.ilike(q),
        Devolution.pedido_marketplace.ilike(q),
        Devolution.conta.ilike(q),
        Devolution.sku.ilike(q),
        Devolution.tag.ilike(q),
        Devolution.produtos.ilike(q),
        Devolution.condicao_produto.ilike(q),
        Devolution.motivo_devolucao.ilike(q),
        Devolution.tecnico.ilike(q),
        Devolution.observacao.ilike(q),
    )


def _build_where(
    search: str | None,
    reembolso: bool | None,
    tag: str | None,
    data_inicio: date | None,
    data_fim: date | None,
    condicao: str | None,
    manutencao: bool | None = None,
    prazo_dias: int | None = None,
    prazo_vencido: bool | None = None,
) -> list:
    where = []
    if search and search.strip():
        where.append(_search_clause(search.strip()))
    if reembolso is not None:
        where.append(Devolution.reembolso.is_(reembolso))
    if manutencao:
        where.append(Devolution.manutencao.is_(True))
    if tag and tag.strip() and tag.strip().lower() != "all":
        where.append(Devolution.tag == tag.strip().lower().lstrip("."))
    if condicao and condicao.strip() and condicao.strip().lower() != "all":
        where.append(Devolution.condicao_produto == condicao.strip())
    # Prazo de manutenção (só linhas com prazo setado = condição Manutenção):
    #   prazo_vencido → só as JÁ vencidas (prazo no passado);
    #   prazo_dias    → vencendo em até N dias (7/15/30), incluindo as vencidas.
    if prazo_vencido:
        where.append(Devolution.prazo.is_not(None))
        where.append(Devolution.prazo < datetime.now(UTC))
    elif prazo_dias is not None and prazo_dias > 0:
        limite = datetime.now(UTC) + timedelta(days=prazo_dias)
        where.append(Devolution.prazo.is_not(None))
        where.append(Devolution.prazo <= limite)
    # "Data de devolução" = quando o registro entrou no sistema (created_at).
    # Período inclusivo: [data_inicio 00:00, data_fim 23:59:59] no fuso de SP.
    if data_inicio is not None:
        start = datetime.combine(data_inicio, time.min, tzinfo=SAO_PAULO)
        where.append(Devolution.created_at >= start.astimezone(UTC))
    if data_fim is not None:
        end = datetime.combine(data_fim, time.min, tzinfo=SAO_PAULO) + timedelta(days=1)
        where.append(Devolution.created_at < end.astimezone(UTC))
    return where


def _cliente_scalar_subquery():
    """Nome do cliente mora em bling_orders (nome_destinatario), não em
    devolutions — subquery correlacionada pelo número do pedido (grão de
    bling_orders é ITEM: MAX colapsa e ignora NULLs)."""
    return (
        select(func.max(BlingOrder.nome_destinatario))
        .where(BlingOrder.numero == Devolution.pedido_bling)
        .correlate(Devolution)
        .scalar_subquery()
    )


async def _chamados_por_pedido(
    session: AsyncSession, numeros: set[str | None]
) -> dict[str, Chamado]:
    """Chamado MAIS RECENTE de cada pedido Bling (qualquer origem) — alimenta a
    coluna "Chamado" da lista/exportação. Uma query em lote pra página toda."""
    limpos = {n.strip() for n in numeros if n and n.strip()}
    if not limpos:
        return {}
    rows = (
        (
            await session.execute(
                select(Chamado)
                .where(Chamado.pedido_bling.in_(list(limpos)))
                .order_by(Chamado.pedido_bling, desc(Chamado.created_at))
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, Chamado] = {}
    for ch in rows:
        out.setdefault(ch.pedido_bling, ch)
    return out


async def _aberturas_por_chamado(
    session: AsyncSession, chamados: dict[str, Chamado]
) -> dict[UUID, ChamadoMensagem]:
    """Mensagem `abertura` (chamado automático no ML) mais recente de cada
    chamado da página — alimenta o status "ML" da coluna Chamado."""
    ids = [ch.id for ch in chamados.values()]
    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                select(ChamadoMensagem)
                .where(
                    ChamadoMensagem.chamado_id.in_(ids),
                    ChamadoMensagem.tipo == chamados_devolucao.TIPO_ABERTURA,
                )
                .order_by(ChamadoMensagem.chamado_id, desc(ChamadoMensagem.created_at))
            )
        )
        .scalars()
        .all()
    )
    out: dict[UUID, ChamadoMensagem] = {}
    for m in rows:
        out.setdefault(m.chamado_id, m)
    return out


async def _anexos_por_devolucao(
    session: AsyncSession, ids: list[UUID]
) -> dict[UUID, list[DevolucaoAnexoOut]]:
    """Metadados dos anexos (sem o blob) das linhas da página."""
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(
                DevolucaoAnexo.id,
                DevolucaoAnexo.devolution_id,
                DevolucaoAnexo.filename,
                DevolucaoAnexo.content_type,
                DevolucaoAnexo.size_bytes,
                DevolucaoAnexo.ml_file_name,
                DevolucaoAnexo.created_at,
            )
            .where(DevolucaoAnexo.devolution_id.in_(ids))
            .order_by(DevolucaoAnexo.created_at)
        )
    ).all()
    out: dict[UUID, list[DevolucaoAnexoOut]] = {}
    for r in rows:
        out.setdefault(r.devolution_id, []).append(
            DevolucaoAnexoOut(
                id=r.id,
                filename=r.filename,
                content_type=r.content_type,
                size_bytes=r.size_bytes,
                ml_file_name=r.ml_file_name,
                created_at=r.created_at,
            )
        )
    return out


def _aplica_chamado(
    out: DevolutionOut, ch: Chamado | None, abertura: ChamadoMensagem | None = None
) -> DevolutionOut:
    if ch is None:
        return out
    out.tem_chamado = True
    out.chamado_numero = ch.chamado
    out.chamado_resolvido = ch.resolvido
    out.chamado_plataforma = chamados_devolucao.plataforma_de(ch.plataforma)
    if abertura is not None:
        out.chamado_ml_status = abertura.status
        out.chamado_ml_erro = abertura.erro if abertura.status != "enviada" else None
    return out


async def _completar_out(
    session: AsyncSession, row: Devolution, out: DevolutionOut
) -> DevolutionOut:
    """Chamado (+ status da abertura no ML) e anexos de UMA linha — pras
    respostas de create/patch/anexo, que o front usa pra trocar a linha."""
    chamados = await _chamados_por_pedido(session, {row.pedido_bling})
    ch = chamados.get((row.pedido_bling or "").strip())
    if ch is None:
        ch = await chamados_devolucao.chamados_svc.chamado_da_devolucao(session, row)
        if ch is not None:
            chamados = {ch.pedido_bling or "": ch}
    aberturas = await _aberturas_por_chamado(session, chamados) if ch is not None else {}
    _aplica_chamado(out, ch, aberturas.get(ch.id) if ch is not None else None)
    out.anexos = (await _anexos_por_devolucao(session, [row.id])).get(row.id, [])
    return out


async def _chamado_devolucao_apos_commit(
    session: AsyncSession, row: Devolution
) -> None:
    """Motivo que pede chamado → registra na aba e, se for ML, dispara a
    abertura (worker; inline sem fila). Chamado por create/patch/anexo DEPOIS
    do commit da linha."""
    ch = await chamados_devolucao.garantir_chamado(session, row)
    # Commita SEMPRE: o registro na aba vale pra qualquer plataforma, mesmo
    # quando não há disparo pro ML (Shopee/TikTok ficam canal manual).
    await session.commit()
    await session.refresh(row)
    if ch is None:
        return
    await chamados_devolucao.agendar_disparo(session, ch, row)


@router.get("", response_model=DevolutionPage)
async def list_devolutions(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
    reembolso: bool | None = Query(None),
    tag: str | None = Query(None),
    data_inicio: date | None = Query(None),
    data_fim: date | None = Query(None),
    condicao: str | None = Query(None),
    manutencao: bool | None = Query(None),
    prazo_dias: int | None = Query(None, ge=1, le=365),
    prazo_vencido: bool | None = Query(None),
) -> DevolutionPage:
    where = _build_where(
        search, reembolso, tag, data_inicio, data_fim, condicao, manutencao,
        prazo_dias, prazo_vencido,
    )

    stmt = (
        select(Devolution, _cliente_scalar_subquery().label("cliente"))
        .where(*where)
        .order_by(desc(Devolution.data).nulls_last(), desc(Devolution.created_at))
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count()).select_from(Devolution).where(*where)

    rows = (await session.execute(stmt)).all()
    total = (await session.execute(count_stmt)).scalar_one()

    chamados = await _chamados_por_pedido(session, {dev.pedido_bling for dev, _ in rows})
    aberturas = await _aberturas_por_chamado(session, chamados)
    anexos = await _anexos_por_devolucao(session, [dev.id for dev, _ in rows])
    items: list[DevolutionOut] = []
    for dev, cliente in rows:
        out = DevolutionOut.model_validate(dev)
        out.cliente = cliente
        ch = chamados.get((dev.pedido_bling or "").strip())
        _aplica_chamado(out, ch, aberturas.get(ch.id) if ch is not None else None)
        out.anexos = anexos.get(dev.id, [])
        items.append(out)

    return DevolutionPage(
        items=items,
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


_SITUACAO_AGUARDANDO_DEVOLUCAO = "83957"

# "Data da última movimentação" AUTOMÁTICA (Eduardo 03/09: "a data da ult
# movimentação não está aparecendo"): o carimbo mais recente do
# `logistica.status_datas` (uma data por campo do status da plataforma — ver
# services/logistica_datas). Alias `l` = a linha da Logística no LATERAL.
_LOGISTICA_ULTIMA_MOVIMENTACAO_SQL = (
    "(SELECT MAX((e.v ->> 'em')::timestamptz) "
    "FROM jsonb_each(COALESCE(l.status_datas, '{}'::jsonb)) AS e(k, v))"
)


_FONTE_PLATAFORMA = {"tiktok": "tiktok", "shopee": "shopee", "ml": "mercado livre"}

# Dia do backfill da migration 0236: TODOS os pedidos que já estavam em 83957
# ganharam esta data (Eduardo 03/09: "em devolução desde todas as datas estão
# iguais"). Entrada carimbada em outro dia é de verdade (sync viu a transição).
_BACKFILL_0236 = date(2026, 9, 2)


def _data_entrada(
    *,
    entrada_bling: date | None,
    entrada_manual: date | None,
    devolucao_criada_em: datetime | None,
    plataforma: str | None,
    meli_status: dict | None,
    status_datas: dict | None,
) -> tuple[date | None, bool]:
    """"Em devolução desde" efetivo + se é ESTIMADO, em ordem: digitado na mão
    > dia em que o cliente abriu a devolução no marketplace > entrada em 83957
    carimbada de verdade pelo sync > carimbo do sinal de retorno na Logística
    (pacote voltando — logistica_rules.data_entrada_devolucao_estimada; este e
    o próximo são estimativas) > a data do backfill (último recurso). Caso
    287144: entrou em 19/08 pela Viena no Bling, que não expõe o histórico
    pela API → o operador digita."""
    from app.services import logistica_rules  # tardio: evita ciclo router↔services
    from app.services.devolucao_returns import iso_to_dt

    if entrada_manual:
        return entrada_manual, False
    if devolucao_criada_em:
        return devolucao_criada_em.astimezone(SAO_PAULO).date(), False
    if entrada_bling and entrada_bling != _BACKFILL_0236:
        return entrada_bling, False
    est = logistica_rules.data_entrada_devolucao_estimada(plataforma, meli_status, status_datas)
    dt = iso_to_dt(est) if est else None
    if dt:
        return dt.astimezone(SAO_PAULO).date(), True
    return entrada_bling, entrada_bling is not None


def _com_status_da_devolucao(
    d: dict,
    *,
    localizacao_manual: str | None,
    lg_plataforma: str | None,
    lg_meli_status: dict | None,
    status_auto: str | None = None,
    fonte_auto: str | None = None,
    localizacao_auto: str | None = None,
    localizacao_auto_data=None,
) -> dict:
    """Devolução VIVA → `localizacao` vira o status da devolução (+ o último
    evento do pacote de volta, quando o 17track já mandou) e a entrega original
    vai pra `entrega_localizacao` (Eduardo 03/09: "tem mais um monte de pedido
    entregue e só vem em acompanhamentos"). Fontes, em ordem: o status gravado
    pelo sync do retorno (`devolucao_rastreio.devolucao_status_auto`), senão o
    `return_status` da Logística. Localização MANUAL continua mandando; sem
    devolução viva, nada muda."""
    from app.services import logistica_rules  # tardio: evita ciclo router↔services

    d.setdefault("entrega_localizacao", None)
    if localizacao_manual:
        return d
    dev = None
    if status_auto and fonte_auto:
        dev = logistica_rules.devolucao_status_pt(
            _FONTE_PLATAFORMA.get(fonte_auto, fonte_auto), {"return_status": status_auto}
        )
    if dev is None:
        dev = logistica_rules.devolucao_status_pt(lg_plataforma, lg_meli_status or {})
    if dev:
        d["entrega_localizacao"] = d.get("localizacao")
        d["localizacao"] = f"{dev} · {localizacao_auto}" if localizacao_auto else dev
        if localizacao_auto_data is not None:
            d["localizacao_data"] = localizacao_auto_data
    return d


async def acompanhamento_rows(session: AsyncSession) -> list[dict]:
    """Linhas da aba Acompanhamento: TODOS os pedidos hoje em 'Aguardando
    Devolução' (83957) no Bling — uma linha por item, com nome do cliente, dia
    em que entrou na situação e rastreio/última localização.

    Rastreio/localização são AUTOMÁTICOS: vêm do painel Logística (mesma linha
    do pedido, onde o time já preenche/acompanha o pacote) e a edição manual da
    aba (devolucao_rastreio) SOBRESCREVE o automático quando preenchida —
    Eduardo 03/09: "nao esta puxando o rastreio, isso tem que ser automatico".
    `localizacao_data` só existe pra localização manual (a Logística não guarda
    a data da última movimentação). Compartilhada com o botão Informar."""
    rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    v.pedido_bling::text        AS pedido_bling,
                    v.pedido_marketplace::text  AS pedido_marketplace,
                    v.data,
                    -- "Em devolução desde" (decidido em _data_entrada): manual >
                    -- devolução aberta no marketplace > carimbo do sinal na
                    -- Logística > entrada em 83957 no Bling (a 0236 carimbou
                    -- 02/09 em todo mundo — Eduardo 03/09).
                    bo.aguardando_devolucao_data AS entrada_bling,
                    r.entrada_manual,
                    r.devolucao_criada_em,
                    lg.lg_status_datas,
                    r.rastreio_auto,
                    r.transportadora_auto,
                    r.localizacao_auto,
                    r.localizacao_auto_data,
                    r.devolucao_status_auto,
                    r.fonte_auto,
                    v.plataforma_bling          AS plataforma,
                    COALESCE(NULLIF(btrim(v.loja_nome), ''),
                             'Loja ' || v.bling_loja_id, 'Sem loja') AS loja,
                    v.nome_destinatario         AS cliente,
                    v.cidade_destino            AS cidade,
                    v.uf_destino                AS uf,
                    v.sku,
                    v.produto,
                    v.quantidade::int           AS quantidade,
                    -- Rastreio: manual > código do PACOTE DE VOLTA (sync do
                    -- retorno) > rastreio da entrega original (Logística).
                    COALESCE(NULLIF(btrim(r.rastreio), ''), r.rastreio_auto, lg.rastreio)
                        AS rastreio,
                    COALESCE(NULLIF(btrim(r.localizacao), ''), lg.localizacao)
                        AS localizacao,
                    -- Data da última movimentação: a do manual quando a
                    -- localização é manual; senão a da Logística (carimbo
                    -- mais recente do status_datas — ver logistica_datas).
                    CASE WHEN NULLIF(btrim(r.localizacao), '') IS NOT NULL
                         THEN r.localizacao_data
                         ELSE lg.ultima_movimentacao END AS localizacao_data,
                    -- Auxiliares do pós-processamento (status da devolução
                    -- viva no lugar da entrega — _com_status_da_devolucao):
                    NULLIF(btrim(r.localizacao), '') AS localizacao_manual,
                    lg.lg_plataforma,
                    lg.lg_meli_status,
                    EXISTS (
                        SELECT 1 FROM "{SCHEMA}".devolutions d
                        WHERE d.pedido_bling = v.pedido_bling::text
                    ) AS lancada
                FROM "{SCHEMA}".vw_devolucoes v
                JOIN "{SCHEMA}".bling_orders bo ON bo.id = v.bling_order_item_id
                LEFT JOIN "{SCHEMA}".devolucao_rastreio r
                       ON r.pedido_bling = v.pedido_bling::text
                LEFT JOIN LATERAL (
                    SELECT NULLIF(btrim(l.rastreio), '')    AS rastreio,
                           NULLIF(btrim(l.localizacao), '') AS localizacao,
                           {_LOGISTICA_ULTIMA_MOVIMENTACAO_SQL} AS ultima_movimentacao,
                           l.plataforma                     AS lg_plataforma,
                           l.meli_status                    AS lg_meli_status,
                           l.status_datas                   AS lg_status_datas
                    FROM "{SCHEMA}".logistica l
                    WHERE l.pedido_bling = v.pedido_bling::text
                      AND (NULLIF(btrim(l.rastreio), '') IS NOT NULL
                           OR NULLIF(btrim(l.localizacao), '') IS NOT NULL)
                    ORDER BY l.updated_at DESC NULLS LAST
                    LIMIT 1
                ) lg ON TRUE
                WHERE v.situacao = :situacao
                ORDER BY bo.aguardando_devolucao_data ASC NULLS FIRST,
                         v.pedido_bling, v.sku
                LIMIT 2000
                """  # noqa: S608
            ),
            {"situacao": _SITUACAO_AGUARDANDO_DEVOLUCAO},
        )
    ).mappings().all()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        lg_plataforma = d.pop("lg_plataforma", None)
        lg_meli_status = d.pop("lg_meli_status", None)
        d["aguardando_devolucao_data"], d["aguardando_devolucao_data_estimada"] = _data_entrada(
            entrada_bling=d.pop("entrada_bling", None),
            entrada_manual=d.pop("entrada_manual", None),
            devolucao_criada_em=d.pop("devolucao_criada_em", None),
            plataforma=lg_plataforma,
            meli_status=lg_meli_status,
            status_datas=d.pop("lg_status_datas", None),
        )
        out.append(
            _com_status_da_devolucao(
                d,
                localizacao_manual=d.pop("localizacao_manual", None),
                lg_plataforma=lg_plataforma,
                lg_meli_status=lg_meli_status,
                status_auto=d.pop("devolucao_status_auto", None),
                fonte_auto=d.pop("fonte_auto", None),
                localizacao_auto=d.pop("localizacao_auto", None),
                localizacao_auto_data=d.pop("localizacao_auto_data", None),
            )
        )
    return out


@router.get("/acompanhamento", response_model=AcompanhamentoOut)
async def list_acompanhamento(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
) -> AcompanhamentoOut:
    """Aba Acompanhamento (ver acompanhamento_rows). A lista volta inteira
    (teto de segurança de 2000 linhas); busca e filtros são do front, como na
    aba Pedidos do Controle de Estoque."""
    rows = await acompanhamento_rows(session)

    hoje_sp = datetime.now(SAO_PAULO).date()
    items: list[AcompanhamentoItemOut] = []
    for r in rows:
        d = dict(r)
        entrada = d.get("aguardando_devolucao_data")
        d["dias_em_devolucao"] = (hoje_sp - entrada).days if entrada else None
        items.append(AcompanhamentoItemOut.model_validate(d))
    total_pedidos = len({i.pedido_bling for i in items if i.pedido_bling})
    return AcompanhamentoOut(items=items, total_pedidos=total_pedidos)


@router.patch("/acompanhamento/{pedido_bling}", response_model=AcompanhamentoRastreioOut)
async def patch_acompanhamento_rastreio(
    pedido_bling: str,
    body: AcompanhamentoRastreioPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> AcompanhamentoRastreioOut:
    """Salva rastreio/última localização de um pedido em devolução (edição
    inline da aba Acompanhamento). `localizacao_data` é carimbada sozinha
    quando a localização MUDA — é a data da última movimentação vista."""
    pedido_bling = pedido_bling.strip()
    exists = (
        await session.execute(
            select(func.count()).select_from(BlingOrder).where(BlingOrder.numero == pedido_bling)
        )
    ).scalar_one()
    if not exists:
        raise HTTPException(404, detail={"code": "pedido_not_found"})

    row = await session.get(DevolucaoRastreio, pedido_bling)
    if row is None:
        row = DevolucaoRastreio(pedido_bling=pedido_bling)
        session.add(row)

    data = body.model_dump(exclude_unset=True)
    if "rastreio" in data:
        row.rastreio = (data["rastreio"] or "").strip() or None
    if "localizacao" in data:
        nova = (data["localizacao"] or "").strip() or None
        if nova != row.localizacao:
            row.localizacao = nova
            row.localizacao_data = datetime.now(UTC) if nova else None
    if "em_devolucao_desde" in data:
        # null explícito limpa (volta ao automático); omitido não mexe.
        row.entrada_manual = data["em_devolucao_desde"]
    row.updated_by = user.id
    await session.commit()
    await session.refresh(row)
    logger.info(
        "devolucao_rastreio_saved",
        pedido_bling=pedido_bling,
        rastreio=row.rastreio,
        localizacao=row.localizacao,
        entrada_manual=str(row.entrada_manual) if row.entrada_manual else None,
    )
    entrada_bling = (
        await session.execute(
            select(BlingOrder.aguardando_devolucao_data)
            .where(BlingOrder.numero == pedido_bling)
            .order_by(BlingOrder.aguardando_devolucao_data.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()
    # Resposta = valores EFETIVOS (manual → senão o automático da Logística),
    # a MESMA regra do GET — o front espelha a resposta na linha, então sem o
    # fallback aqui editar um campo apagaria da tela o automático do outro
    # (e limpar o manual deve REVELAR o automático de novo, não sumir).
    lg = (
        (
            await session.execute(
                text(
                    f"""
                SELECT NULLIF(btrim(l.rastreio), '')    AS rastreio,
                       NULLIF(btrim(l.localizacao), '') AS localizacao,
                       {_LOGISTICA_ULTIMA_MOVIMENTACAO_SQL} AS ultima_movimentacao,
                       l.plataforma                     AS lg_plataforma,
                       l.meli_status                    AS lg_meli_status,
                       l.status_datas                   AS lg_status_datas
                FROM "{SCHEMA}".logistica l
                WHERE l.pedido_bling = :pedido
                  AND (NULLIF(btrim(l.rastreio), '') IS NOT NULL
                       OR NULLIF(btrim(l.localizacao), '') IS NOT NULL)
                ORDER BY l.updated_at DESC NULLS LAST
                LIMIT 1
                """  # noqa: S608
                ),
                {"pedido": pedido_bling},
            )
        )
        .mappings()
        .first()
    )
    # Mesma regra do GET: manual manda; senão o automático da Logística — data
    # da última movimentação e, com devolução viva, o status da devolução no
    # lugar da entrega (_com_status_da_devolucao); "Em devolução desde" pela
    # mesma escada do GET (_data_entrada) + dias.
    entrada, estimada = _data_entrada(
        entrada_bling=entrada_bling,
        entrada_manual=row.entrada_manual,
        devolucao_criada_em=row.devolucao_criada_em,
        plataforma=lg["lg_plataforma"] if lg else None,
        meli_status=lg["lg_meli_status"] if lg else None,
        status_datas=lg["lg_status_datas"] if lg else None,
    )
    hoje_sp = datetime.now(SAO_PAULO).date()
    d = _com_status_da_devolucao(
        {
            "pedido_bling": row.pedido_bling,
            "rastreio": row.rastreio or row.rastreio_auto or (lg["rastreio"] if lg else None),
            "localizacao": row.localizacao or (lg["localizacao"] if lg else None),
            "localizacao_data": (
                row.localizacao_data
                if row.localizacao
                else (lg["ultima_movimentacao"] if lg else None)
            ),
            "aguardando_devolucao_data": entrada,
            "aguardando_devolucao_data_estimada": estimada,
            "dias_em_devolucao": (hoje_sp - entrada).days if entrada else None,
        },
        localizacao_manual=row.localizacao,
        lg_plataforma=lg["lg_plataforma"] if lg else None,
        lg_meli_status=lg["lg_meli_status"] if lg else None,
        status_auto=row.devolucao_status_auto,
        fonte_auto=row.fonte_auto,
        localizacao_auto=row.localizacao_auto,
        localizacao_auto_data=row.localizacao_auto_data,
    )
    return AcompanhamentoRastreioOut(**d)


def _fmt_dt_sp(dt: datetime | None) -> str:
    """Datetime → 'dd/mm/aaaa HH:MM' no fuso de São Paulo (vazio se None)."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(SAO_PAULO).strftime("%d/%m/%Y %H:%M")


_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("Data", "data"),
    ("Data Devolução", "created_at"),
    ("Pedido Bling", "pedido_bling"),
    ("Pedido Marketplace", "pedido_marketplace"),
    ("Conta", "conta"),
    ("Cliente", "cliente"),
    ("SKU", "sku"),
    ("Tag", "tag"),
    ("Produtos", "produtos"),
    ("Custo produto", "custo_produto"),
    ("Condição", "condicao_produto"),
    ("Link abertura", "link_abertura"),
    ("Reembolso", "reembolso"),
    ("Motivo", "motivo_devolucao"),
    ("Chamado", "chamado_info"),  # virtual: chamado mais recente do pedido
    ("Custo manutenção", "custo_manutencao"),
    ("Técnico", "tecnico"),
    ("Qtd", "quantidade"),
    ("Devolver estoque", "devolver_estoque"),
    ("SKU novo", "estoque_mov_sku"),
    ("Passou manutenção", "manutencao"),
    ("Data devolvido estoque", "data_devolvido_estoque"),
    ("Prazo", "prazo"),
    ("Observação", "observacao"),
]


@router.get("/export.xlsx")
async def export_devolutions(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    search: str | None = Query(None),
    reembolso: bool | None = Query(None),
    tag: str | None = Query(None),
    data_inicio: date | None = Query(None),
    data_fim: date | None = Query(None),
    condicao: str | None = Query(None),
    manutencao: bool | None = Query(None),
    prazo_dias: int | None = Query(None, ge=1, le=365),
    prazo_vencido: bool | None = Query(None),
) -> StreamingResponse:
    """Exporta as devoluções (com os mesmos filtros da lista) em XLSX."""
    where = _build_where(
        search, reembolso, tag, data_inicio, data_fim, condicao, manutencao,
        prazo_dias, prazo_vencido,
    )
    rows = (
        await session.execute(
            select(Devolution, _cliente_scalar_subquery().label("cliente"))
            .where(*where)
            .order_by(desc(Devolution.data).nulls_last(), desc(Devolution.created_at))
        )
    ).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Devoluções"
    ws.append([label for label, _ in _EXPORT_COLUMNS])

    chamados = await _chamados_por_pedido(session, {r.pedido_bling for r, _ in rows})
    for r, cliente in rows:
        ch = chamados.get((r.pedido_bling or "").strip())
        line = []
        for _, field in _EXPORT_COLUMNS:
            value = cliente if field == "cliente" else getattr(r, field, None)
            if field == "chamado_info":
                # Nº do chamado quando a plataforma já deu um; senão Sim/Não.
                if ch is None:
                    line.append("Não")
                else:
                    valor = (ch.chamado or "").strip() or "Sim"
                    line.append(f"{valor} (resolvido)" if ch.resolvido else valor)
            elif field in ("data", "created_at", "data_devolvido_estoque", "prazo"):
                line.append(_fmt_dt_sp(value))
            elif field in ("reembolso", "devolver_estoque", "manutencao"):
                line.append("Sim" if value else "Não")
            elif field == "estoque_mov_sku":
                # SKU em que o item voltou ao estoque Bling; marca estornos.
                if value and r.estoque_mov_revertido_at is not None:
                    line.append(f"{value} (estornado)")
                else:
                    line.append(value or "")
            else:
                line.append(value if value is not None else "")
        ws.append(line)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = datetime.now(SAO_PAULO).strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="devolucoes_{stamp}.xlsx"'},
    )


@router.get("/order-lookup", response_model=list[DevolutionLookupOut])
async def lookup_devolution_order(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    pedido: str = Query(..., min_length=1),
) -> list[DevolutionLookupOut]:
    pedido = pedido.strip()
    if not pedido:
        raise HTTPException(422, detail={"code": "pedido_required"})

    q_like = f"%{pedido}%"
    # Termo só de dígitos é nº de pedido/CEP: não buscar dentro do nome do
    # destinatário, pois nicknames de marketplace embutem datas/números que
    # casariam por substring e trariam pedidos de outros clientes.
    name_clause = "" if pedido.isdigit() else "OR v.nome_destinatario ILIKE :q_like"
    rows = (
        await session.execute(
            text(
                f"""
                SELECT t.* FROM (
                    SELECT
                        v.data,
                        v.pedido_bling::text AS pedido_bling,
                        v.pedido_marketplace::text AS pedido_marketplace,
                        COALESCE(NULLIF(btrim(v.loja_nome), ''), 'Loja ' || v.bling_loja_id, 'Sem loja') AS conta,
                        v.sku,
                        v.produto AS produtos,
                        1 AS quantidade,
                        COALESCE(bo.preco_custo::numeric, 0)::double precision AS custo_produto,
                        v.nome_destinatario,
                        v.cep_destino,
                        v.endereco_destino,
                        v.numero_destino,
                        v.complemento_destino,
                        v.bairro_destino,
                        v.cidade_destino,
                        v.uf_destino,
                        gs.unit_num AS _unit_num,
                        -- Limita por nº de PEDIDOS (proteção p/ buscas fuzzy por
                        -- CEP/nome), nunca por unidade: um pedido grande precisa
                        -- voltar com todas as unidades.
                        DENSE_RANK() OVER (
                            ORDER BY v.data DESC NULLS LAST, v.pedido_bling
                        ) AS _pedido_rank
                    FROM "{SCHEMA}".vw_devolucoes v
                    LEFT JOIN "{SCHEMA}".bling_orders bo ON bo.id = v.bling_order_item_id
                    CROSS JOIN generate_series(1, GREATEST(1, COALESCE(v.quantidade::int, 1))) gs(unit_num)
                    WHERE (
                        v.pedido_bling::text = :pedido
                        OR v.pedido_marketplace::text = :pedido
                        OR v.cep_destino ILIKE :q_like
                        {name_clause}
                    )
                ) t
                WHERE t._pedido_rank <= 50
                ORDER BY t.data DESC NULLS LAST, t.pedido_bling, t.sku, t._unit_num
                """  # noqa: S608
            ),
            {"pedido": pedido, "q_like": q_like},
        )
    ).mappings().all()

    exploded_kits = await _split_kit_composition(session, [dict(r) for r in rows])
    expanded = _split_mala_sizes(_split_compound_skus(exploded_kits))

    part_skus = sorted({
        s for r in expanded
        if (s := (r["sku"] or "").strip().lower())
    })
    base_skus = sorted({
        base for sku in part_skus
        if (base := _sku_base(sku).strip().lower()) and base != sku
    })
    products: dict[str, dict] = {}
    base_products: dict[str, dict] = {}
    if part_skus:
        prod_rows = (
            await session.execute(
                text(
                    f"""
                    SELECT DISTINCT ON (lower(btrim(sku)))
                        lower(btrim(sku)) AS k,
                        name,
                        cost_price::double precision AS cost_price
                    FROM "{SCHEMA}".products
                    WHERE lower(btrim(sku)) = ANY(:keys)
                    ORDER BY lower(btrim(sku)), situacao = 'A' DESC NULLS LAST
                    """  # noqa: S608
                ),
                {"keys": part_skus},
            )
        ).mappings().all()
        products = {p["k"]: dict(p) for p in prod_rows}
    if base_skus:
        base_rows = (
            await session.execute(
                text(
                    f"""
                    SELECT DISTINCT ON (lower(split_part(btrim(sku), '.', 1)))
                        lower(split_part(btrim(sku), '.', 1)) AS k,
                        name
                    FROM "{SCHEMA}".products
                    WHERE lower(split_part(btrim(sku), '.', 1)) = ANY(:keys)
                      AND btrim(sku) NOT LIKE '%+%'
                      AND (formato IS NULL OR formato <> 'E')
                    ORDER BY
                        lower(split_part(btrim(sku), '.', 1)),
                        situacao = 'A' DESC NULLS LAST,
                        (name ILIKE '%USADO%' OR name ILIKE '%AVULSO%') ASC,
                        sku
                    """  # noqa: S608
                ),
                {"keys": base_skus},
            )
        ).mappings().all()
        base_products = {p["k"]: dict(p) for p in base_rows}

    for r in expanded:
        sku_key = (r["sku"] or "").strip().lower()
        prod = products.get(sku_key)
        if prod:
            r["produtos"] = prod["name"]
            # Custo do catálogo é autoritativo p/ componentes de kit/split; p/ SKU
            # simples ele só preenche quando o pedido não trouxe custo (preco_custo
            # nulo/0 — ex.: pedidos de Manutenção criados sem custo no Bling).
            if prod["cost_price"] is not None and (
                r.get("_compound") or not r.get("custo_produto")
            ):
                r["custo_produto"] = prod["cost_price"]
        elif fallback := base_products.get(_sku_base(sku_key).strip().lower()):
            r["produtos"] = fallback["name"]

    # Marca itens que JÁ têm devolução lançada (por pedido+sku) para o front
    # esmaecer. Antes o "já feito" olhava só a página carregada da tabela, então
    # um item devolvido cedo (empurrado pra fora da página) reaparecia como
    # disponível — ex.: acessórios de um kit lançados antes do item principal.
    pedidos = sorted({p for r in expanded if (p := (r.get("pedido_bling") or "").strip())})
    devolvidos: set[tuple[str, str]] = set()
    if pedidos:
        dev_rows = (
            await session.execute(
                text(
                    f"""
                    SELECT DISTINCT pedido_bling::text AS pedido_bling,
                           lower(btrim(sku)) AS sku
                    FROM "{SCHEMA}".devolutions
                    WHERE pedido_bling::text = ANY(:pedidos)
                      AND sku IS NOT NULL
                    """  # noqa: S608
                ),
                {"pedidos": pedidos},
            )
        ).mappings().all()
        devolvidos = {(d["pedido_bling"], d["sku"]) for d in dev_rows}
    for r in expanded:
        r["ja_devolvido"] = (
            (r.get("pedido_bling") or "").strip(),
            (r.get("sku") or "").strip().lower(),
        ) in devolvidos

    return [DevolutionLookupOut.model_validate({k: v for k, v in r.items() if not k.startswith("_")}) for r in expanded]


async def _kit_components_for_skus(
    session: AsyncSession, skus: set[str]
) -> dict[str, list[dict]]:
    """Mapeia SKU de kit (lower/trim) → lista de componentes a partir do cache
    `bling_kit_components`. Cada componente: {sku, name, cost, qty}. Casado pelo
    `bling_product_id` do kit e do componente na tabela `products`."""
    if not skus:
        return {}
    rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    lower(btrim(kp.sku)) AS kit_sku,
                    cp.sku               AS comp_sku,
                    cp.name              AS comp_name,
                    cp.cost_price        AS comp_cost,
                    kc.quantidade        AS qty
                FROM "{SCHEMA}".bling_kit_components kc
                JOIN "{SCHEMA}".products kp ON kp.bling_product_id = kc.kit_bling_product_id
                JOIN "{SCHEMA}".products cp ON cp.bling_product_id = kc.component_bling_product_id
                WHERE lower(btrim(kp.sku)) = ANY(:skus)
                ORDER BY kit_sku, cp.sku
                """  # noqa: S608
            ),
            {"skus": sorted(skus)},
        )
    ).mappings().all()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["kit_sku"], []).append({
            "sku": r["comp_sku"],
            "name": r["comp_name"],
            "cost": r["comp_cost"],
            "qty": r["qty"],
        })
    return out


async def _split_kit_composition(
    session: AsyncSession, rows: list[dict]
) -> list[dict]:
    """Explode linhas cujo SKU é um kit composto (formato='E') nos componentes
    individuais, usando o cache `bling_kit_components`. Uma linha-unidade por
    componente × quantidade (ex.: `b011` → b011.8, b011.12, b011.12, b011.18,
    b011.20, b011.24). Faz loop pra resolver kits aninhados (componente que é
    ele mesmo um kit); kits ausentes do cache caem nos splitters de string.

    A composição de um SKU base (ex.: `b011`) ou nomeado (`b011.kit5`) não está
    na string — vive na estrutura do Bling — então esse passo roda ANTES dos
    splitters por string, que ficam no-op sobre os componentes simples."""
    work = list(rows)
    for _ in range(5):  # teto de profundidade p/ kits aninhados
        skus = {
            s for r in work
            if (s := (r.get("sku") or "").strip().lower())
        }
        comp_map = await _kit_components_for_skus(session, skus)
        if not comp_map:
            break
        out: list[dict] = []
        changed = False
        for r in work:
            comps = comp_map.get((r.get("sku") or "").strip().lower())
            if not comps:
                out.append(r)
                continue
            changed = True
            base_cost = r.get("custo_produto") or 0.0
            for c in comps:
                cost = float(c["cost"]) if c["cost"] is not None else base_cost
                for _u in range(max(1, int(c["qty"] or 1))):
                    out.append({
                        **r,
                        "sku": c["sku"],
                        "produtos": c["name"] or r.get("produtos"),
                        "custo_produto": cost,
                        "quantidade": 1,
                        "_compound": True,
                    })
        work = out
        if not changed:
            break
    return work


def _split_compound_skus(rows: list[dict]) -> list[dict]:
    """Expand rows whose SKU is a '+'-joined kit into one row per component SKU.

    Component name/cost are resolved later from the products table; the split
    cost here is only a fallback for components missing from the catalog.
    """
    out: list[dict] = []
    for row in rows:
        sku = (row.get("sku") or "").strip()
        if "+" in sku:
            parts = [p.strip() for p in sku.split("+") if p.strip()]
            if len(parts) > 1:
                base_cost = row.get("custo_produto") or 0.0
                split_cost = base_cost / len(parts) if base_cost else base_cost
                for part in parts:
                    out.append({**row, "sku": part, "custo_produto": split_cost, "_compound": True})
                continue
        out.append(row)
    return out


_MALA_BASE_RE = re.compile(r"^b[0-9]", re.IGNORECASE)
_MALA_SIZE_RE = re.compile(r"^[0-9]+$")


def _split_mala_sizes(rows: list[dict]) -> list[dict]:
    """Mala com vários tamanhos no SKU é, na verdade, várias malas distintas.

    `b004.12.18` = base `b004` + tamanhos `12` e `18` → duas malas separadas
    `b004.12` e `b004.18`, uma linha por tamanho. Só dispara quando a base é
    de mala (`b` + dígito) e TODOS os segmentos após a base são numéricos
    (tamanho) — assim sufixos regionais (`.sp`, `.us`…) nunca são divididos.
    Nome/custo de cada tamanho são resolvidos depois na tabela products; o
    custo dividido aqui é só fallback.
    """
    out: list[dict] = []
    for row in rows:
        sku = (row.get("sku") or "").strip()
        parts = sku.split(".")
        if (
            len(parts) >= 3
            and _MALA_BASE_RE.match(parts[0])
            and all(_MALA_SIZE_RE.match(p) for p in parts[1:])
        ):
            base = parts[0]
            sizes = parts[1:]
            base_cost = row.get("custo_produto") or 0.0
            split_cost = base_cost / len(sizes) if base_cost else base_cost
            for size in sizes:
                out.append({**row, "sku": f"{base}.{size}", "custo_produto": split_cost, "_compound": True})
            continue
        out.append(row)
    return out


@router.get("/product-search", response_model=list[DevolutionProductOut])
async def search_products(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
) -> list[DevolutionProductOut]:
    """Busca produtos por SKU/nome para o modal de troca (Modal 1).
    Só ativos (NULL=desconhecido passa); ordenado por SKU. Apenas produtos
    SIMPLES — kits/compostos (formato='E' ou SKU com '+') não voltam ao estoque
    como unidade, então ficam de fora da busca."""
    q = q.strip()
    if not q:
        return []
    like = f"%{q}%"
    rows = (
        await session.execute(
            select(
                Product.sku, Product.name, Product.cost_price,
                Product.saldo_virtual_total, Product.stock,
            )
            .where(or_(Product.sku.ilike(like), Product.name.ilike(like)))
            .where(or_(Product.situacao == "A", Product.situacao.is_(None)))
            # Só simples: exclui composto explícito (formato='E') e SKUs '+'
            # com flag de simples errada (NULL=desconhecido passa).
            .where(or_(Product.formato.is_(None), Product.formato != "E"))
            .where(Product.sku.notlike("%+%"))
            .order_by(Product.sku)
            .limit(limit)
        )
    ).all()
    return [
        DevolutionProductOut(
            sku=r.sku,
            name=r.name,
            cost_price=float(r.cost_price) if r.cost_price is not None else None,
            # `stock` (webhook) já é o saldoVirtualTotal; `saldo_virtual_total`
            # (refresh explícito) é NULL nos avulsos z criados na devolução —
            # cai no stock pra não exibir "—".
            saldo_virtual_total=(
                r.saldo_virtual_total if r.saldo_virtual_total is not None else r.stock
            ),
        )
        for r in rows
    ]


@router.get("/sku-suffixes", response_model=SkuSuffixesOut)
async def sku_suffixes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    sku: str = Query(..., min_length=1),
) -> SkuSuffixesOut:
    """Para o modal `.sp` (Modal 2): dado um SKU, devolve a base, os sufixos
    permitidos (já existentes no sistema — não cria novos) e quais variantes
    `base.<suffix>` já existem na tabela products."""
    base = _sku_base(sku.strip())
    candidates = {f"{base}.{s}": s for s in _SUFFIX_TAGS}
    rows = (
        await session.execute(
            select(Product.sku, Product.name)
            .where(func.lower(Product.sku).in_([k.lower() for k in candidates]))
            # Inativo/Excluído não pode receber entrada de estoque; NULL=desconhecido passa.
            .where(or_(Product.situacao == "A", Product.situacao.is_(None)))
        )
    ).all()
    found = {r.sku.lower(): r.name for r in rows}
    variants = [
        SkuSuffixVariant(
            suffix=suffix,
            sku=full_sku,
            name=found.get(full_sku.lower()),
            exists=full_sku.lower() in found,
        )
        for full_sku, suffix in candidates.items()
    ]
    return SkuSuffixesOut(base=base, allowed_suffixes=list(_SUFFIX_TAGS), variants=variants)


@router.post("/stock-correction", response_model=BlingStockResultOut)
async def stock_correction(
    body: StockCorrectionIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> BlingStockResultOut:
    """Correção manual de estoque: adiciona unidades de um SKU ao estoque Bling
    reaproveitando a MESMA lógica de devolução (Novo/Usado → bin existente ou
    criação de z000N.<tag>), porém SEM gravar registro de devolução nem alterar
    a situação de nenhum pedido."""
    condicao = body.condicao_produto
    if condicao not in _STOCK_TRIGGER_CONDICOES:
        raise HTTPException(
            422,
            detail={
                "code": "condicao_invalida",
                "message": f"Condição {condicao!r} não dispara estoque",
            },
        )
    # Linha transiente (NÃO adicionada à sessão): só carrega os campos que
    # return_product_to_bling_stock lê. Nada é persistido.
    row = Devolution(
        conta="Correção de Estoque",
        sku=body.sku,
        produtos=body.produtos,
        custo_produto=body.custo_produto,
        condicao_produto=condicao,
        quantidade=body.quantidade or 1,
        troca_sku=body.troca_sku,
        troca_condicao=body.troca_condicao,
        estoque_suffix=body.estoque_suffix,
        estoque_destino_sku=body.estoque_destino_sku,
        estoque_nova_tag=body.estoque_nova_tag,
        manutencao_destino=body.manutencao_destino,
    )
    sr = await return_product_to_bling_stock(session, row, obs_override=body.observacao)
    if sr is None:
        raise HTTPException(
            422,
            detail={"code": "no_stock_action", "message": "Nenhuma ação de estoque para essa condição"},
        )
    logger.info(
        "stock_correction_done",
        sku=body.sku, condicao=condicao, qty=body.quantidade,
        action=sr.get("action"), ok=sr.get("ok"),
    )
    return BlingStockResultOut(**sr)


@router.post("", response_model=DevolutionOut, status_code=status.HTTP_201_CREATED)
async def create_devolution(
    body: DevolutionCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> DevolutionOut:
    # Estoque no ADD: Novo/Usado/Trocado sempre processam (automático). Manutenção
    # só processa se o operador ligou o toggle no rascunho (já decidiu Novo/Usado/
    # Sucata no modal). Extraviado nunca devolve estoque.
    devolver_no_add = bool(body.devolver_estoque) and body.condicao_produto in (
        "Novo",
        "Usado",
        "Trocado",
        "Manutenção",
    )
    row = Devolution(
        data=body.data,
        pedido_bling=body.pedido_bling,
        pedido_marketplace=body.pedido_marketplace,
        conta=body.conta,
        sku=body.sku,
        produtos=body.produtos,
        custo_produto=body.custo_produto,
        condicao_produto=body.condicao_produto,
        link_abertura=body.link_abertura,
        reembolso=body.reembolso,
        motivo_devolucao=body.motivo_devolucao,
        video_url=body.video_url,
        custo_manutencao=body.custo_manutencao,
        tecnico=body.tecnico,
        devolver_estoque=devolver_no_add,
        observacao=body.observacao,
        troca_sku=body.troca_sku,
        troca_condicao=body.troca_condicao,
        estoque_suffix=body.estoque_suffix,
        quantidade=body.quantidade or 1,
        estoque_destino_sku=body.estoque_destino_sku,
        estoque_nova_tag=body.estoque_nova_tag,
        manutencao_destino=body.manutencao_destino,
        tag=_sku_tag(body.sku),
        data_devolvido_estoque=datetime.now(UTC) if devolver_no_add else None,
    )
    if _custo_e_tecnico_preenchidos(row):
        row.reembolso = True
    session.add(row)
    if body.condicao_produto in _REFUND_CONDICOES:
        _maybe_create_refund(session, row, body.condicao_produto)
    await session.commit()
    await session.refresh(row)
    # Custo de manutenção informado no ADD entra como débito (negativo) no reembolso
    # do refund recém-criado.
    if body.condicao_produto == "Manutenção" and (row.custo_manutencao or 0):
        await _sync_manutencao_to_refund(session, row, row.custo_manutencao or 0)
        await session.commit()
        await session.refresh(row)
    # Prazo (30 dias da inserção) só para Manutenção.
    if body.condicao_produto == "Manutenção" and row.prazo is None:
        row.prazo = row.created_at + timedelta(days=30)
        await session.commit()
        await session.refresh(row)
    # Motivo que pede chamado já no lançamento → registra o chamado sozinho e,
    # se for ML, abre no Mercado Livre (services/chamados_devolucao; dedupe por
    # pedido lá dentro).
    await _chamado_devolucao_apos_commit(session, row)
    logger.info("devolution_created", id=str(row.id), pedido_bling=row.pedido_bling)
    out = DevolutionOut.model_validate(row)

    # Gatilho no ADD: Novo/Usado/Trocado processam estoque sempre. Manutenção só
    # quando o toggle veio ligado (devolver_no_add). Extraviado não mexe no estoque.
    condicao = body.condicao_produto
    should_stock = condicao in ("Novo", "Usado", "Trocado") or (
        condicao == "Manutenção" and devolver_no_add
    )
    if should_stock:
        changed = False
        if not row.devolver_estoque:
            row.devolver_estoque = True
            changed = True
        if row.data_devolvido_estoque is None:
            row.data_devolvido_estoque = datetime.now(UTC)
            changed = True
        # Manutenção que volta ao estoque já passou em manutenção (inclui Sucata).
        if condicao == "Manutenção" and not row.manutencao:
            row.manutencao = True
            changed = True
        if changed:
            await session.commit()
            await session.refresh(row)
            out = DevolutionOut.model_validate(row)
    if should_stock:
        sr = await return_product_to_bling_stock(session, row)
        if sr is not None:
            out.bling_stock_result = BlingStockResultOut(**sr)
            record_stock_movement(row, sr)  # persiste p/ permitir estorno depois
            await session.commit()
            await session.refresh(row)
            out = DevolutionOut.model_validate(row)
            out.bling_stock_result = BlingStockResultOut(**sr)
    # Situação do pedido: Extraviado e Manutenção patcham já no add; Novo/Usado/
    # Trocado quando processam o estoque.
    if (condicao in ("Extraviado", "Manutenção") or should_stock) and row.pedido_bling:
        await apply_order_situacao(session, row.pedido_bling, actor_id=user.id)
        await session.commit()  # persiste a linha de auditoria de situação
    return await _completar_out(session, row, out)


@router.patch("/{devolution_id}", response_model=DevolutionOut)
async def patch_devolution(
    devolution_id: UUID,
    body: DevolutionPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> DevolutionOut:
    row = (
        await session.execute(select(Devolution).where(Devolution.id == devolution_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "devolution_not_found"})

    data = body.model_dump(exclude_unset=True)
    if data.get("conta") is None and "conta" in data:
        raise HTTPException(422, detail={"code": "conta_required"})

    prev_condicao = row.condicao_produto
    prev_devolver_estoque = row.devolver_estoque
    prev_custo_manutencao = row.custo_manutencao
    for key, value in data.items():
        setattr(row, key, value)
    if "sku" in data:
        row.tag = _sku_tag(row.sku)

    new_condicao = row.condicao_produto
    # Ao SAIR de Manutenção: custo de manutenção é obrigatório (registra o reparo)
    # e o pedido passa a constar como "passou em manutenção".
    if prev_condicao == "Manutenção" and new_condicao != "Manutenção":
        if not row.custo_manutencao:
            raise HTTPException(
                422,
                detail={
                    "code": "custo_manutencao_required",
                    "message": "Custo de manutenção obrigatório ao sair de Manutenção",
                },
            )
        row.manutencao = True

    if new_condicao in _REFUND_CONDICOES and new_condicao != prev_condicao:
        _maybe_create_refund(session, row, new_condicao)

    # Carimba a data quando o toggle "devolver estoque" passa a TRUE.
    if row.devolver_estoque and (not prev_devolver_estoque or row.data_devolvido_estoque is None):
        row.data_devolvido_estoque = datetime.now(UTC)

    # Prazo (30 dias da inserção) só para Manutenção; limpa se sair de Manutenção.
    if new_condicao == "Manutenção":
        if row.prazo is None:
            row.prazo = row.created_at + timedelta(days=30)
    elif row.prazo is not None:
        row.prazo = None

    # Custo de manutenção + técnico preenchidos → liga o "Reembolso" sozinho.
    # Só quando um dos dois foi tocado NESTE request (desmarcar na mão continua
    # possível: um PATCH que não mexe neles não religa o checkbox).
    if (
        ("custo_manutencao" in data or "tecnico" in data)
        and not row.reembolso
        and _custo_e_tecnico_preenchidos(row)
    ):
        row.reembolso = True

    await session.commit()
    await session.refresh(row)

    # Motivo trocado pra um que pede chamado (ou link do vídeo informado) →
    # registra o chamado sozinho e abre no ML (dedupe por pedido dentro do
    # helper — repetir o PATCH não duplica).
    if "motivo_devolucao" in data or "video_url" in data:
        await _chamado_devolucao_apos_commit(session, row)

    # Variação do custo de manutenção é refletida como débito no reembolso do refund
    # de Manutenção (subtrai a diferença, preservando o que já estava lá).
    delta_custo = (row.custo_manutencao or 0) - (prev_custo_manutencao or 0)
    if delta_custo:
        await _sync_manutencao_to_refund(session, row, delta_custo)
        await session.commit()
        await session.refresh(row)

    condicao_changed = new_condicao != prev_condicao
    toggle_turned_on = row.devolver_estoque and not prev_devolver_estoque
    toggle_turned_off = prev_devolver_estoque and not row.devolver_estoque
    should_stock = (
        new_condicao in _STOCK_TRIGGER_CONDICOES
        and row.devolver_estoque
        and (condicao_changed or toggle_turned_on)
    )

    final_sr: dict | None = None
    has_pending_mov = (
        row.estoque_mov_bling_id is not None and row.estoque_mov_revertido_at is None
    )
    # Estorna a entrada anterior quando o operador desliga "devolver estoque" OU
    # quando vamos relançar por mudança de condição/destino — evita estoque
    # duplicado e tira de venda o item que, p.ex., entrou Usado e virou Sucata.
    if has_pending_mov and (toggle_turned_off or should_stock):
        rev = await reverse_stock_movement(session, row)
        await session.commit()
        await session.refresh(row)
        if toggle_turned_off:
            final_sr = rev

    if should_stock:
        # Manutenção que volta ao estoque já passou em manutenção (inclui Sucata).
        if new_condicao == "Manutenção" and not row.manutencao:
            row.manutencao = True
        sr = await return_product_to_bling_stock(session, row)
        if sr is not None:
            recorded = bool(sr.get("ok") and sr.get("bling_product_id"))
            record_stock_movement(row, sr)
            await session.commit()
            await session.refresh(row)
            if recorded or final_sr is None:
                final_sr = sr
        else:
            # Sem ação de estoque (ex.: Sucata), mas persiste o flag de manutenção.
            await session.commit()
            await session.refresh(row)

    # Extraviado e Manutenção patcham a situação já na mudança de condição
    # (sem depender do toggle) — Manutenção precisa refletir no Bling na hora.
    # Se a API recusar a transição direta, apply_order_situacao desvia por
    # Aguardando Devolução; se nem o desvio passar, alerta o operador.
    extraviado_now = new_condicao == "Extraviado" and condicao_changed
    manutencao_now = new_condicao == "Manutenção" and condicao_changed
    if (extraviado_now or manutencao_now or should_stock) and row.pedido_bling:
        await apply_order_situacao(session, row.pedido_bling, actor_id=user.id)
        await session.commit()  # persiste a linha de auditoria de situação

    out = DevolutionOut.model_validate(row)
    if final_sr is not None:
        out.bling_stock_result = BlingStockResultOut(**final_sr)
    return await _completar_out(session, row, out)


# ------------------------------------------------------------ anexos (fotos/vídeo)

# Foto vai pro ML como evidência do chamado de devolução (JPG/PNG/PDF; acima de
# 5 MB é reduzida antes de subir). Vídeo só fica guardado na linha — a API do
# ML não aceita vídeo.
_ANEXO_TIPOS_FOTO = {"image/jpeg", "image/png", "application/pdf"}
_ANEXO_TIPOS_VIDEO = {"video/mp4", "video/quicktime", "video/webm"}
_ANEXO_MAX_FOTO = 15 * 1024 * 1024
_ANEXO_MAX_VIDEO = 80 * 1024 * 1024


@router.post(
    "/{devolution_id}/anexos",
    response_model=DevolutionOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_anexo(
    devolution_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
    file: Annotated[UploadFile, File(...)],
) -> DevolutionOut:
    """Anexa foto/vídeo à linha da devolução. Se o motivo pede chamado e a
    conta é ML, (re)dispara a abertura automática — é assim que um chamado
    "aguardando foto" (Danificado / produto diferente) sai."""
    row = (
        await session.execute(select(Devolution).where(Devolution.id == devolution_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "devolution_not_found"})
    ctype = (file.content_type or "").lower()
    if ctype not in _ANEXO_TIPOS_FOTO | _ANEXO_TIPOS_VIDEO:
        raise HTTPException(400, detail={"code": "devolucao_anexo_tipo_invalido"})
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail={"code": "devolucao_anexo_vazio"})
    limite = _ANEXO_MAX_VIDEO if ctype in _ANEXO_TIPOS_VIDEO else _ANEXO_MAX_FOTO
    if len(raw) > limite:
        raise HTTPException(413, detail={"code": "devolucao_anexo_muito_grande"})
    session.add(
        DevolucaoAnexo(
            devolution_id=row.id,
            filename=(file.filename or "anexo").strip() or "anexo",
            content_type=ctype,
            size_bytes=len(raw),
            blob=raw,
            created_by=user.id,
        )
    )
    await session.commit()
    await session.refresh(row)
    await _chamado_devolucao_apos_commit(session, row)
    logger.info(
        "devolucao_anexo_upload", devolution_id=str(row.id), content_type=ctype, bytes=len(raw)
    )
    return await _completar_out(session, row, DevolutionOut.model_validate(row))


@router.get("/anexos/{anexo_id}")
async def get_anexo(
    anexo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
) -> Response:
    a = await session.get(DevolucaoAnexo, anexo_id)
    if a is None:
        raise HTTPException(404, detail={"code": "devolucao_anexo_not_found"})
    return Response(
        content=a.blob,
        media_type=a.content_type,
        headers={"Content-Disposition": f'inline; filename="{a.filename}"'},
    )


@router.delete("/anexos/{anexo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_anexo(
    anexo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> Response:
    a = await session.get(DevolucaoAnexo, anexo_id)
    if a is None:
        raise HTTPException(404, detail={"code": "devolucao_anexo_not_found"})
    await session.delete(a)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/backfill-addresses", status_code=status.HTTP_200_OK)
async def backfill_addresses(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Re-fetch delivery addresses from Bling for devolution orders that have
    NULL nome_destinatario. Calls GET /pedidos/vendas/{bling_id} individually.
    Returns counts of processed / updated / failed records.
    """
    from app.services.bling_orders import _melhor_nome_destinatario
    from app.services.devolution_stock_return import _get_bling_client

    _SITUACOES = ("83957", "83960", "83961", "83966", "84677", "545902")

    # Find orders that need backfill. nome ILIKE 'amazon%' pega pedidos
    # gravados antes do fix do nome genérico (transporte.contato.nome era a
    # CONTA "Amazon DBA"/"Amazon KFA", não o cliente) — o botão "atualizar
    # clientes" da aba re-busca e conserta esses também.
    rows = (
        await session.execute(
            select(BlingOrder.id, BlingOrder.bling_id, BlingOrder.numero)
            .where(
                BlingOrder.situacao.in_(_SITUACOES),
                or_(
                    BlingOrder.nome_destinatario.is_(None),
                    BlingOrder.nome_destinatario.ilike("amazon%"),
                ),
                BlingOrder.bling_id.is_not(None),
            )
            .order_by(desc(BlingOrder.data))
            .limit(limit)
        )
    ).all()

    if not rows:
        return {"processed": 0, "updated": 0, "failed": 0, "message": "Nenhum pedido sem endereço encontrado"}

    client = await _get_bling_client(session)
    if client is None:
        raise HTTPException(503, detail={"code": "no_bling_integration"})

    processed = updated = failed = 0

    for row_id, bling_id, numero in rows:
        processed += 1
        try:
            raw = await client.get_order(bling_id)
            if not raw:
                failed += 1
                continue

            # Extract address using same logic as bling_orders.py
            transporte = raw.get("transporte") or {}
            transporte = transporte if isinstance(transporte, dict) else {}
            t_contato = transporte.get("contato") or {}
            t_contato = t_contato if isinstance(t_contato, dict) else {}
            t_endereco = transporte.get("enderecoEntrega") or {}
            t_endereco = t_endereco if isinstance(t_endereco, dict) else {}
            # v3 traz o endereço (e o nome real do cliente na Amazon) em
            # transporte.etiqueta — mesma cadeia do parse principal.
            t_etiqueta = transporte.get("etiqueta") or {}
            t_etiqueta = t_etiqueta if isinstance(t_etiqueta, dict) else {}
            buyer = raw.get("contato") or {}
            buyer = buyer if isinstance(buyer, dict) else {}
            buyer_end = buyer.get("endereco") or {}
            buyer_end = buyer_end if isinstance(buyer_end, dict) else {}

            def _v(
                tp_f: str,
                buyer_f: str | None = None,
                *,
                _en: dict = t_endereco,
                _et: dict = t_etiqueta,
                _ben: dict = buyer_end,
            ) -> str | None:
                # defaults amarram os dicts DESTA iteração (closure em loop).
                return (
                    _en.get(tp_f)
                    or _et.get(tp_f)
                    or _ben.get(buyer_f or tp_f)
                    or None
                )

            values: dict = {}
            nome = _melhor_nome_destinatario(
                t_contato.get("nome"), t_etiqueta.get("nome"), buyer.get("nome")
            )
            if nome:
                values["nome_destinatario"] = nome
            for col, tp_f in [
                ("cep_destino", "cep"),
                ("endereco_destino", "endereco"),
                ("numero_destino", "numero"),
                ("complemento_destino", "complemento"),
                ("bairro_destino", "bairro"),
                ("cidade_destino", "municipio"),
                ("uf_destino", "uf"),
            ]:
                val = _v(tp_f)
                if val:
                    values[col] = val

            if values:
                await session.execute(
                    update(BlingOrder).where(BlingOrder.id == row_id).values(**values)
                )
                updated += 1
                logger.info("devolution_address_backfill_ok", numero=numero, fields=list(values))
            else:
                logger.info("devolution_address_backfill_no_data", numero=numero)

        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("devolution_address_backfill_error", numero=numero, error=str(exc))

    await session.commit()
    logger.info("devolution_address_backfill_done", processed=processed, updated=updated, failed=failed)
    return {
        "processed": processed,
        "updated": updated,
        "failed": failed,
        "message": f"{updated} de {processed} pedidos atualizados com endereço",
    }


@router.delete("/{devolution_id}")
async def delete_devolution(
    devolution_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "delete"))],
) -> dict:
    """Exclui o lançamento. Se ele já devolveu estoque ao Bling (movimento
    registrado em estoque_mov_* e ainda não estornado), dá BAIXA da mesma
    quantidade no Bling ANTES de excluir — Eduardo 03/09: "quando eu clicar em
    excluir você lança um estoque de saída e remove o estoque que foi lançado".
    Se o Bling recusar, NÃO exclui (502) — senão sobraria estoque fantasma.
    Lançamento antigo sem registro do movimento não tem como ser estornado
    daqui (o front avisa: ajustar direto no Bling)."""
    row = (
        await session.execute(select(Devolution).where(Devolution.id == devolution_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "devolution_not_found"})

    estorno: dict | None = None
    if row.estoque_mov_bling_id and row.estoque_mov_revertido_at is None:
        rev = await reverse_stock_movement(session, row)
        if rev is not None and not rev.get("ok"):
            await session.rollback()
            raise HTTPException(
                502,
                detail={
                    "code": "estoque_estorno_falhou",
                    "message": (
                        "Não consegui estornar o estoque devolvido no Bling — o "
                        f"lançamento NÃO foi excluído. {rev.get('message') or ''}"
                    ).strip(),
                },
            )
        estorno = rev

    await session.delete(row)
    await session.commit()
    logger.info(
        "devolution_deleted",
        id=str(devolution_id),
        pedido_bling=row.pedido_bling,
        estoque_estornado=bool(estorno and estorno.get("ok")),
    )
    return {
        "ok": True,
        "estoque_estornado": bool(estorno and estorno.get("ok")),
        "mensagem": (estorno or {}).get("message"),
    }
