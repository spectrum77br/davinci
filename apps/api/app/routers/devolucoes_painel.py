"""Painel novo de Devoluções (Vinicius, 16/11/2026) — rota própria, ao lado
da antiga.

`/api/devolutions` (abas Acompanhamento / Fraude / Lançamentos) fica INTACTA:
é o painel de sempre e o caminho de volta se a equipe não gostar do novo.
Aqui só o que é novo:

- `GET  /api/devolucoes-painel` — a lista única: quem caiu em Aguardando
  Devolução e ainda não foi resolvido, em qualquer situação do Bling;
- `POST /api/devolucoes-painel/{pedido}/resolver` — tira da lista (como
  terminou + nova situação no Bling, opcional);
- `POST /api/devolucoes-painel/{pedido}/reabrir` — desfaz o Resolver;
- `GET  /api/devolucoes-painel/resumo` — o quadro "Meses — como ficou cada
  mês", contado a partir da virada.

Lançar, mover pra Fraude, pedir vídeo, rastreio e observação continuam nas
rotas antigas (a tela nova chama as mesmas). As contas da tela são funções
puras em services/devolucao_painel.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.deps.team_scope import resolve_team_scope
from app.models import (
    BlingOrder,
    Chamado,
    ChamadoMensagem,
    DevolucaoAnexo,
    DevolucaoRastreio,
    Devolution,
    User,
    UserRole,
)
from app.routers import devolutions as antigo
from app.schemas.devolutions import AcompanhamentoItemOut
from app.services import chamados as chamados_svc
from app.services import chamados_devolucao as cd
from app.services import (
    chamados_leitura,
    devolucao_mensagem_comprador,
    devolucao_prazo_acompanhamento,
)
from app.services import devolucao_conferencia as conferencia
from app.services import devolucao_painel as contas
from app.services.devolution_stock_return import status_estoque

logger = structlog.get_logger()
router = APIRouter(prefix="/api/devolucoes-painel", tags=["devolutions"])
SCHEMA = antigo.SCHEMA
SAO_PAULO = contas.SAO_PAULO


# ── Saída ────────────────────────────────────────────────────────────────


class PainelAnexoOut(BaseModel):
    id: UUID
    content_type: str | None = None
    filename: str | None = None


class PainelLancamentoOut(BaseModel):
    """Uma linha lançada (tabela devolutions) do pedido."""

    id: UUID
    sku: str | None = None
    produtos: str | None = None
    quantidade: int = 1
    # Valor gravado (um dos 7) e o rótulo da tela (Novo / Usado / Perdimento…).
    condicao: str | None = None
    condicao_tela: str | None = None
    motivo: str | None = None
    # SKU em que o produto entrou no estoque (z001, a001…) e o estado da
    # entrada no Bling: ok | falhou | conferindo | sem_confirmacao | None.
    sku_lancado: str | None = None
    estoque_status: str | None = None
    data_devolvido_estoque: datetime | None = None
    manutencao_pendente: bool = False
    tecnico: str | None = None
    prazo_manutencao: datetime | None = None
    link_envio: str | None = None
    link_abertura: str | None = None
    observacao: str | None = None
    # Anexos da linha: foto (image/*) vira miniatura; vídeo e PDF viram link.
    anexos: list[PainelAnexoOut] = Field(default_factory=list)
    created_at: datetime | None = None
    # Só admin (custo é coluna de admin no painel antigo também).
    custo_produto: float | None = None
    custo_manutencao: float | None = None


class PainelChamadoOut(BaseModel):
    """Chamado mais recente do pedido na aba Chamados."""

    id: UUID
    numero: str | None = None
    resolvido: bool = False
    resolvido_at: datetime | None = None
    # Estado da contestação (mensagem `abertura`): pendente, enviada, segurada
    # (a IA segurou — "Onde agir hoje"), registrada (cancelada / sem API)…
    abertura_status: str | None = None


class PainelContaOut(BaseModel):
    """Conta do bloco Reembolso, por PEDIDO, só pra admin."""

    recebido: float
    custo_produto: float
    custo_manutencao: float
    resultado: float


class PainelItemOut(AcompanhamentoItemOut):
    """Linha (por PRODUTO) do painel novo: tudo o que a aba Acompanhamento
    já devolvia + etapa, prazo da etapa, lançamento, chamado, conta e o
    estado do Resolver."""

    situacao_bling_id: str | None = None
    situacao_bling_nome: str | None = None
    etapa: Literal["aguardando", "chegou", "manutencao", "fraude"] = "aguardando"
    falta_lancar: bool = False
    # Um prazo só por linha: o mais próximo de vencer entre os que correm.
    prazo_em: datetime | None = None
    prazo_tipo: str | None = None
    prazo_estado: Literal["vencido", "urgente"] | None = None
    prazo_dica: str | None = None
    # Lançamentos do PEDIDO (repetidos em cada linha do pedido) e o que a
    # linha mostra na coluna Condição.
    lancamentos: list[PainelLancamentoOut] = Field(default_factory=list)
    condicao_tela: str | None = None
    sku_lancado: str | None = None
    lancamentos_a_mais: int = 0
    chamado: PainelChamadoOut | None = None
    tem_chamado_aberto: bool = False
    conta: PainelContaOut | None = None
    # Motivo pra aparecer em "Pronto pra resolver" (None = ainda não).
    pronto_resolver: str | None = None
    resolvido: bool = False
    resolvido_at: datetime | None = None
    resolvido_como: str | None = None
    resolvido_por_nome: str | None = None
    # "Abrir chamado: Sim/Não" da janela: None = automático pelo motivo.
    abrir_chamado: bool | None = None
    # 1ª linha do pedido na lista (as outras repetem os dados do pedido).
    primeira_do_pedido: bool = True
    linhas_do_pedido: int = 1


class PainelOut(BaseModel):
    items: list[PainelItemOut]
    total_pedidos: int
    # Opções do "Como terminou" do Resolver.
    como_terminou: list[str]
    pode_mover_painel: bool = False
    ve_custo: bool = False
    # A pessoa só enxerga as lojas da equipe dela (o resumo por mês é de
    # quem vê todas as lojas).
    com_cerca_de_equipe: bool = False


class ResolverIn(BaseModel):
    como_terminou: str = Field(min_length=2, max_length=80)
    # None = não mexe no Bling.
    situacao_id: int | None = None


class ResolverOut(BaseModel):
    pedido_bling: str
    resolvido: bool
    resolvido_at: datetime | None = None
    resolvido_como: str | None = None
    situacao_bling_id: str | None = None
    situacao_bling_nome: str | None = None
    via_desvio: bool = False


class ResumoDiaOut(BaseModel):
    dia: date
    abertas: int
    novas: int
    resolvidas: int
    estoque_novo: int
    estoque_usado: int
    perdimento: int
    manutencao: int
    # Só admin.
    recebido: float | None = None
    custos: float | None = None
    resultado: float | None = None


class ResumoOut(BaseModel):
    # Dia da virada: antes dele não existe data de saída, então não há conta.
    desde: date | None = None
    dias: list[ResumoDiaOut] = Field(default_factory=list)
    ve_custo: bool = False


# ── Montagem ─────────────────────────────────────────────────────────────


def _admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


def _conta_lanc(d: Devolution) -> contas.Lancamento:
    return contas.Lancamento(
        sku=d.sku,
        condicao=d.condicao_produto,
        motivo=d.motivo_devolucao,
        manutencao_destino=d.manutencao_destino,
        troca_condicao=d.troca_condicao,
        estoque_status=status_estoque(d),
        custo_produto=d.custo_produto,
        custo_manutencao=d.custo_manutencao,
        prazo_manutencao=d.prazo,
        prazo_contestacao=d.prazo_contestacao,
    )


def _sku_lancado(d: Devolution) -> str | None:
    """SKU em que o produto ENTROU no estoque: o do movimento registrado; sem
    movimento (linha antiga), o destino escolhido. Estornado não conta."""
    if d.data_devolvido_estoque is None or d.estoque_mov_revertido_at is not None:
        return None
    return (d.estoque_mov_sku or d.estoque_destino_sku or "").strip() or None


async def _lancamentos_por_pedido(
    session: AsyncSession, pedidos: set[str]
) -> dict[str, list[Devolution]]:
    if not pedidos:
        return {}
    rows = (
        (
            await session.execute(
                select(Devolution)
                .where(Devolution.pedido_bling.in_(sorted(pedidos)))
                .order_by(Devolution.created_at)
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, list[Devolution]] = defaultdict(list)
    for d in rows:
        out[(d.pedido_bling or "").strip()].append(d)
    return out


async def _anexos_por_lancamento(
    session: AsyncSession, ids: list[UUID]
) -> dict[UUID, list[PainelAnexoOut]]:
    """Só os dados do anexo (sem o arquivo): a tela precisa do tipo pra não
    tentar mostrar vídeo ou PDF como foto."""
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(
                DevolucaoAnexo.devolution_id,
                DevolucaoAnexo.id,
                DevolucaoAnexo.content_type,
                DevolucaoAnexo.filename,
            )
            .where(DevolucaoAnexo.devolution_id.in_(ids))
            .order_by(DevolucaoAnexo.created_at)
        )
    ).all()
    out: dict[UUID, list[PainelAnexoOut]] = defaultdict(list)
    for dev_id, anexo_id, tipo, nome in rows:
        out[dev_id].append(PainelAnexoOut(id=anexo_id, content_type=tipo, filename=nome))
    return out


async def _chamados_por_pedido(
    session: AsyncSession, pedidos: set[str]
) -> dict[str, list[Chamado]]:
    """Todos os chamados de cada pedido, do mais novo pro mais antigo."""
    if not pedidos:
        return {}
    rows = (
        (
            await session.execute(
                select(Chamado)
                .where(Chamado.pedido_bling.in_(sorted(pedidos)))
                .order_by(Chamado.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, list[Chamado]] = defaultdict(list)
    for ch in rows:
        out[(ch.pedido_bling or "").strip()].append(ch)
    return out


async def _status_das_aberturas(session: AsyncSession, chamado_ids: list[UUID]) -> dict[UUID, str]:
    """Status da contestação (a `abertura` mais recente) de cada chamado."""
    if not chamado_ids:
        return {}
    rows = (
        await session.execute(
            select(ChamadoMensagem.chamado_id, ChamadoMensagem.status)
            .where(
                ChamadoMensagem.chamado_id.in_(chamado_ids),
                ChamadoMensagem.tipo == cd.TIPO_ABERTURA,
            )
            .order_by(ChamadoMensagem.created_at)
        )
    ).all()
    return dict(rows)


def _chamado_da_conta(chamados: list[Chamado]) -> tuple[bool, float | None]:
    """(tem chamado concluído, valor do concluído mais recente com valor)."""
    for ch in chamados:
        if ch.resolvido and ch.valor_recuperado is not None:
            return True, float(ch.valor_recuperado)
    return any(ch.resolvido for ch in chamados), None


async def montar_itens(
    session: AsyncSession,
    user: User,
    *,
    mostrar: Literal["abertas", "resolvidas", "todas", "pedido"],
    lojas: set[str] | None,
    pedido: str | None = None,
    desde: date | None = None,
) -> list[PainelItemOut]:
    rows = await antigo.acompanhamento_rows(
        session, lojas=lojas, painel=mostrar, pedido=pedido, resolvidas_desde=desde
    )
    pedidos = {(r.get("pedido_bling") or "").strip() for r in rows} - {""}
    lancs = await _lancamentos_por_pedido(session, pedidos)
    anexos = await _anexos_por_lancamento(session, [d.id for ds in lancs.values() for d in ds])
    chamados = await _chamados_por_pedido(session, pedidos)
    aberturas = await _status_das_aberturas(
        session, [chs[0].id for chs in chamados.values() if chs]
    )
    admin = _admin(user)
    agora = datetime.now(UTC)
    hoje = agora.astimezone(SAO_PAULO).date()

    linhas_por_pedido: dict[str, int] = defaultdict(int)
    for r in rows:
        linhas_por_pedido[(r.get("pedido_bling") or "").strip()] += 1

    vistos: set[str] = set()
    out: list[PainelItemOut] = []
    for r in rows:
        d = dict(r)
        pedido = (d.get("pedido_bling") or "").strip()
        do_pedido = lancs.get(pedido, [])
        da_conta = [_conta_lanc(x) for x in do_pedido]
        chs = chamados.get(pedido, [])
        aberto = any(not ch.resolvido for ch in chs)
        concluido, valor = _chamado_da_conta(chs)

        entrada = d.get("aguardando_devolucao_data")
        d["dias_em_devolucao"] = (hoje - entrada).days if entrada else None
        situacao = (d.get("situacao_bling_id") or "").strip() or None
        etapa_ = contas.etapa(
            fila=d.get("fila"),
            situacao_bling=situacao,
            chegou_em=d.get("devolucao_chegou_em"),
            lancamentos=da_conta,
        )
        d["etapa"] = etapa_
        d["falta_lancar"] = contas.falta_lancar(etapa_, da_conta)
        # A régua antiga só devolve a data dos 45 dias perto de vencer; a
        # coluna Prazo mostra sempre ("pacote chegar") enquanto o pacote não
        # chegou e ninguém lançou — a mesma condição da régua.
        prazo_45 = d.get("prazo_ate")
        if d.get("prazo_estado") == "excecao":
            # Passou de 45 dias mas fica fora da regra (extraviado, não
            # recebido): não é prazo vencido — o porquê segue em prazo_texto.
            prazo_45 = None
        elif (
            prazo_45 is None
            and etapa_ == contas.AGUARDANDO
            and entrada is not None
            and not do_pedido
        ):
            prazo_45 = entrada + timedelta(days=devolucao_prazo_acompanhamento.PRAZO_PADRAO_DIAS)
        d.update(
            contas.prazo_da_etapa(
                etapa_=etapa_,
                agora=agora,
                prazo_resposta=d.get("prazo_resposta"),
                acao_resposta=d.get("acao_resposta"),
                prazo_45_ate=prazo_45,
                prazo_45_estado=d.get("prazo_estado"),
                amazon_cancelar=bool(d.get("amazon_cancelar")),
                lancamentos=da_conta,
                situacao_bling=situacao,
                tem_chamado=bool(chs),
            )
        )
        # `prazo_estado` do painel antigo é do prazo de 45 dias ("vence_logo",
        # "excecao"…); aqui a coluna Prazo tem o estado dela — o de 45 dias
        # segue em prazo_texto/prazo_ate.
        if d["prazo_estado"] not in ("vencido", "urgente"):
            d["prazo_estado"] = None

        d["lancamentos"] = [
            PainelLancamentoOut(
                id=x.id,
                sku=x.sku,
                produtos=x.produtos,
                quantidade=x.quantidade or 1,
                condicao=x.condicao_produto,
                condicao_tela=contas.condicao_da_tela(c),
                motivo=x.motivo_devolucao,
                sku_lancado=_sku_lancado(x),
                estoque_status=c.estoque_status,
                data_devolvido_estoque=x.data_devolvido_estoque,
                manutencao_pendente=contas.manutencao_pendente(c),
                tecnico=x.tecnico,
                prazo_manutencao=x.prazo,
                link_envio=x.link_envio,
                link_abertura=x.link_abertura,
                observacao=x.observacao,
                anexos=anexos.get(x.id, []),
                created_at=x.created_at,
                custo_produto=x.custo_produto if admin else None,
                custo_manutencao=x.custo_manutencao if admin else None,
            )
            for x, c in zip(do_pedido, da_conta, strict=True)
        ]
        da_linha, a_mais = contas.lancamento_da_linha(
            d.get("sku"), da_conta, linhas_do_pedido=linhas_por_pedido[pedido]
        )
        if da_linha is not None:
            i = da_conta.index(da_linha)
            d["condicao_tela"] = contas.condicao_da_tela(da_linha)
            d["sku_lancado"] = _sku_lancado(do_pedido[i])
            d["lancamentos_a_mais"] = a_mais
        if chs:
            ch = chs[0]
            d["chamado"] = PainelChamadoOut(
                id=ch.id,
                numero=ch.chamado,
                resolvido=ch.resolvido,
                resolvido_at=ch.resolvido_at,
                abertura_status=aberturas.get(ch.id),
            )
        d["tem_chamado_aberto"] = aberto
        d["primeira_do_pedido"] = pedido not in vistos
        d["linhas_do_pedido"] = linhas_por_pedido[pedido]
        vistos.add(pedido)
        if admin and d["primeira_do_pedido"]:
            conta = contas.conta_do_reembolso(
                lancamentos=da_conta, chamado_resolvido=concluido, chamado_valor=valor
            )
            d["conta"] = PainelContaOut(**conta) if conta else None

        # Aberto ou resolvido é decidido UMA vez, no SQL (pela data de entrada
        # do Bling): a data mostrada na tela pode ser a digitada à mão ou a do
        # marketplace, e refazer a conta aqui com ela dava resposta diferente.
        d["resolvido"] = not bool(d.pop("aberto", True))
        if not d["resolvido"]:
            d["resolvido_at"] = d["resolvido_como"] = d["resolvido_por_nome"] = None
            d["pronto_resolver"] = contas.pronto_pra_resolver(
                situacao_bling=situacao,
                situacao_nome=d.get("situacao_bling_nome"),
                lancamentos=da_conta,
                tem_chamado_aberto=aberto,
                chamado_resolvido=concluido,
                chamado_valor=valor,
            )
        out.append(PainelItemOut.model_validate(d))
    return out


# ── Rotas ────────────────────────────────────────────────────────────────


@router.get("", response_model=PainelOut)
async def listar(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    mostrar: Annotated[Literal["abertas", "resolvidas", "todas"], Query()] = "abertas",
    pedido: Annotated[str | None, Query(max_length=60)] = None,
    desde: Annotated[date | None, Query()] = None,
) -> PainelOut:
    """A lista única do painel novo. Volta inteira (teto de 4000 linhas);
    busca, filtros, cartões e "Onde agir hoje" são contas do navegador sobre
    ela. Quem tem equipe de vendas só recebe as lojas da equipe.

    `pedido` (nº do Bling ou da plataforma) ignora o `mostrar`: traz só esse
    pedido, do histórico inteiro — a devolução antiga que o chamado aponta, ou
    o pedido que ainda não caiu em Aguardando Devolução e alguém quer lançar."""
    scope = await resolve_team_scope(session, user)
    lojas = antigo._lojas_da_equipe(scope)
    if pedido and pedido.strip():
        items = await montar_itens(
            session, user, mostrar="pedido", lojas=lojas, pedido=pedido.strip()
        )
    else:
        # `desde` (início do Período da tela): até onde ir buscar as Resolvidas —
        # sem ele, os últimos 60 dias (o histórico antigo vai até maio/2026).
        items = await montar_itens(session, user, mostrar=mostrar, lojas=lojas, desde=desde)
    return PainelOut(
        items=items,
        total_pedidos=len({i.pedido_bling for i in items if i.pedido_bling}),
        como_terminou=list(contas.COMO_TERMINOU),
        pode_mover_painel=antigo._pode_mover_painel(user),
        ve_custo=_admin(user),
        com_cerca_de_equipe=lojas is not None,
    )


async def _situacao_atual(session: AsyncSession, pedido: str) -> tuple[str | None, str | None]:
    sits = await antigo._situacoes_por_pedido(session, {pedido})
    sit = sits.get(pedido)
    if not sit:
        return None, None
    return (str(sit[0]) if sit[0] is not None else None), sit[1]


@router.post("/{pedido_bling}/resolver", response_model=ResolverOut)
async def resolver(
    pedido_bling: str,
    body: ResolverIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> ResolverOut:
    """Resolver ("igual do chamado"): o pedido sai da lista de abertas. Vale
    pro PEDIDO inteiro — a situação do Bling é do pedido, não do produto.
    Com `situacao_id`, muda a situação no Bling antes (mesma troca do seletor
    Status Bling: desvio, espelho local e auditoria); se o Bling recusar, nada
    é resolvido."""
    pedido = pedido_bling.strip()
    como = body.como_terminou.strip()
    # A marca da largada é do sistema: usada à mão, esconderia o pedido de
    # "Resolvidas" e do resumo.
    if len(como) < 2 or como in antigo.PAINEL_MARCAS:
        raise HTTPException(422, detail={"code": "como_terminou_invalido"})
    await antigo._exigir_pedido_da_equipe(session, await resolve_team_scope(session, user), pedido)
    row = await antigo._rastreio_do_pedido(session, pedido)
    via_desvio = False
    if body.situacao_id is not None:
        atual, _nome = await _situacao_atual(session, pedido)
        if atual != str(body.situacao_id):
            _n, via_desvio, _b, _a = await antigo.mudar_situacao_manual(
                session, user, pedido, int(body.situacao_id), origem="devolucoes_resolver"
            )
    row.resolvido_at = datetime.now(UTC)
    row.resolvido_por = user.id
    row.resolvido_como = como
    row.updated_by = user.id
    await session.commit()
    sit_id, sit_nome = await _situacao_atual(session, pedido)
    logger.info(
        "devolucao_painel_resolvido",
        pedido_bling=pedido,
        como=row.resolvido_como,
        situacao=sit_id,
        via_desvio=via_desvio,
        user_id=str(user.id),
    )
    return ResolverOut(
        pedido_bling=pedido,
        resolvido=True,
        resolvido_at=row.resolvido_at,
        resolvido_como=row.resolvido_como,
        situacao_bling_id=sit_id,
        situacao_bling_nome=sit_nome,
        via_desvio=via_desvio,
    )


@router.post("/{pedido_bling}/reabrir", response_model=ResolverOut)
async def reabrir(
    pedido_bling: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> ResolverOut:
    """Desfaz o Resolver: o pedido volta pra lista de abertas. Não mexe no
    Bling (quem resolveu pode ter mudado a situação — trocar de volta é no
    seletor Status Bling)."""
    pedido = pedido_bling.strip()
    await antigo._exigir_pedido_da_equipe(session, await resolve_team_scope(session, user), pedido)
    row = await session.get(DevolucaoRastreio, pedido)
    if row is None or row.resolvido_at is None:
        raise HTTPException(409, detail={"code": "devolucao_nao_resolvida"})
    como = row.resolvido_como
    row.resolvido_at = None
    row.resolvido_por = None
    row.resolvido_como = None
    row.updated_by = user.id
    await session.commit()
    sit_id, sit_nome = await _situacao_atual(session, pedido)
    logger.info("devolucao_painel_reaberto", pedido_bling=pedido, era=como, user_id=str(user.id))
    return ResolverOut(
        pedido_bling=pedido, resolvido=False, situacao_bling_id=sit_id, situacao_bling_nome=sit_nome
    )


# ── Resumo por mês ───────────────────────────────────────────────────────

_RESUMO_ESTADO_CTE = f"""
WITH casos AS (
    SELECT bo.numero AS pedido,
           max(bo.aguardando_devolucao_data) AS entrada
    FROM "{SCHEMA}".bling_orders bo
    WHERE bo.numero IS NOT NULL
      AND (bo.aguardando_devolucao_data IS NOT NULL OR bo.situacao IN ('83957', '84677'))
    GROUP BY bo.numero
), estado AS (
    SELECT c.pedido, c.entrada,
           CASE WHEN r.resolvido_at IS NOT NULL
                     AND NOT (c.entrada IS NOT NULL
                          AND (r.resolvido_at AT TIME ZONE 'America/Sao_Paulo')::date < c.entrada)
                THEN (r.resolvido_at AT TIME ZONE 'America/Sao_Paulo')::date END AS resolvido_dia,
           r.resolvido_como
    FROM casos c
    LEFT JOIN "{SCHEMA}".devolucao_rastreio r ON r.pedido_bling = c.pedido
)
"""  # noqa: S608
_RESUMO_ABERTAS_SQL = (
    _RESUMO_ESTADO_CTE + "SELECT count(*) FROM estado WHERE resolvido_dia IS NULL"  # noqa: S608
)
# A virada é a largada da 0483/0484 (o dia em que o painel novo entrou); o
# histórico antigo da 0485 tem marca própria e fica de fora.
_RESUMO_VIRADA_SQL = f"""
SELECT min((r.resolvido_at AT TIME ZONE 'America/Sao_Paulo')::date)
FROM "{SCHEMA}".devolucao_rastreio r WHERE r.resolvido_como = :largada
"""  # noqa: S608
_RESUMO_NOVAS_SQL = _RESUMO_ESTADO_CTE + (  # noqa: S608
    "SELECT entrada AS dia, count(*) AS n FROM estado WHERE entrada >= :desde GROUP BY entrada"
)
_RESUMO_RESOLVIDAS_SQL = _RESUMO_ESTADO_CTE + (  # noqa: S608
    "SELECT resolvido_dia AS dia, count(*) AS n FROM estado "
    "WHERE resolvido_dia >= :desde AND resolvido_como IS DISTINCT FROM :largada "
    "AND resolvido_como IS DISTINCT FROM :historico "
    "GROUP BY resolvido_dia"
)

_RESUMO_LANC_SQL = f"""
SELECT (d.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
       CASE WHEN d.condicao_produto = 'Trocado' THEN COALESCE(d.troca_condicao, 'Trocado')
            ELSE d.condicao_produto END AS condicao,
       COALESCE(sum(d.quantidade), 0)::int AS unidades,
       COALESCE(sum(CASE WHEN d.condicao_produto IN ('Sucata', 'Extraviado')
                         THEN d.custo_produto ELSE 0 END), 0)::float
         + COALESCE(sum(d.custo_manutencao), 0)::float AS custos
FROM "{SCHEMA}".devolutions d
WHERE (d.created_at AT TIME ZONE 'America/Sao_Paulo')::date >= :desde
GROUP BY 1, 2
"""  # noqa: S608

_RESUMO_CHAMADO_SQL = f"""
SELECT (c.resolvido_at AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
       COALESCE(sum(c.valor_recuperado), 0)::float AS recebido
FROM "{SCHEMA}".chamados c
WHERE c.resolvido AND c.valor_recuperado > 0
  AND (c.resolvido_at AT TIME ZONE 'America/Sao_Paulo')::date >= :desde
  AND EXISTS (SELECT 1 FROM "{SCHEMA}".bling_orders bo
               WHERE bo.numero = c.pedido_bling AND bo.aguardando_devolucao_data IS NOT NULL)
GROUP BY 1
"""  # noqa: S608


@router.get("/resumo", response_model=ResumoOut)
async def resumo(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "view"))],
) -> ResumoOut:
    """ "Meses — como ficou cada mês": por dia, a partir da VIRADA (o dia em
    que o painel novo entrou). Antes dela não existe data de saída — não dá
    pra dizer quantas estavam abertas em setembro ou outubro.

    Abertas = como o dia terminou (conta de trás pra frente a partir de hoje);
    Novas = caíram em Aguardando Devolução; Resolvidas = saíram no Resolver;
    estoque novo/usado, perdimento e manutenção = unidades lançadas no dia;
    Recebido / Custos / Resultado só pra admin. Quem tem equipe de vendas não
    recebe o resumo (a conta é de todas as lojas)."""
    scope = await resolve_team_scope(session, user)
    if antigo._lojas_da_equipe(scope) is not None:
        return ResumoOut()
    hoje = datetime.now(SAO_PAULO).date()
    piso = hoje - timedelta(days=120)
    virada: date | None = (
        await session.execute(text(_RESUMO_VIRADA_SQL), {"largada": antigo.PAINEL_LARGADA})
    ).scalar()
    if virada is None:
        return ResumoOut()
    desde = max(virada, piso)
    abertas_hoje = int((await session.execute(text(_RESUMO_ABERTAS_SQL))).scalar() or 0)
    novas = {
        x["dia"]: int(x["n"])
        for x in (await session.execute(text(_RESUMO_NOVAS_SQL), {"desde": desde})).mappings()
    }
    resolvidas = {
        x["dia"]: int(x["n"])
        for x in (
            await session.execute(
                text(_RESUMO_RESOLVIDAS_SQL),
                {
                    "desde": desde,
                    "largada": antigo.PAINEL_LARGADA,
                    "historico": antigo.PAINEL_LARGADA_HISTORICO,
                },
            )
        ).mappings()
    }
    lanc = (await session.execute(text(_RESUMO_LANC_SQL), {"desde": desde})).mappings().all()
    por_condicao: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    custos: dict[date, float] = defaultdict(float)
    for x in lanc:
        por_condicao[x["dia"]][x["condicao"] or ""] += int(x["unidades"])
        custos[x["dia"]] += float(x["custos"] or 0)
    admin = _admin(user)
    recebidos: dict[date, float] = {}
    if admin:
        recebidos = {
            x["dia"]: float(x["recebido"] or 0)
            for x in (await session.execute(text(_RESUMO_CHAMADO_SQL), {"desde": desde})).mappings()
        }

    dias: list[ResumoDiaOut] = []
    abertas = abertas_hoje
    dia = hoje
    while dia >= desde:
        cond = por_condicao.get(dia, {})
        linha = ResumoDiaOut(
            dia=dia,
            abertas=abertas,
            novas=novas.get(dia, 0),
            resolvidas=resolvidas.get(dia, 0),
            estoque_novo=cond.get("Novo", 0),
            estoque_usado=cond.get("Usado", 0),
            perdimento=cond.get("Sucata", 0),
            manutencao=cond.get("Manutenção", 0),
        )
        if admin:
            linha.recebido = round(recebidos.get(dia, 0.0), 2)
            linha.custos = round(custos.get(dia, 0.0), 2)
            linha.resultado = round(linha.recebido - linha.custos, 2)
        dias.append(linha)
        # Como terminou o dia ANTERIOR: desfaz o movimento de hoje.
        abertas = abertas - novas.get(dia, 0) + resolvidas.get(dia, 0)
        dia -= timedelta(days=1)
    return ResumoOut(desde=desde, dias=dias, ve_custo=admin)


# ── Chamado da devolução, na janela (2ª etapa, Vinicius 16/11/2026) ─────────
# "Abrir chamado: Sim ou Não", a conferência da IA antes de a disputa sair
# ("se não bate, segurar e perguntar"), a "Instrução IA" e a prévia do texto
# que vai pra plataforma. Tudo é do PEDIDO (o chamado é um por pedido).


class InstrucaoOut(BaseModel):
    texto: str
    autor_nome: str | None = None
    em: datetime | None = None
    # A IA de Chamado já fez uma análise depois dela.
    lida: bool = False
    # Escrita antes de o chamado existir: entra nele quando ele nascer.
    esperando_chamado: bool = False


class ChamadoJanelaOut(BaseModel):
    pedido_bling: str
    # Algum produto lançado tem motivo que abre chamado (Bloqueado, Golpe…).
    pede_chamado: bool = False
    motivo: str | None = None
    abrir_chamado: bool | None = None
    chamado_id: UUID | None = None
    numero: str | None = None
    plataforma: str | None = None
    status_plataforma: str | None = None
    resolvido: bool = False
    abertura_status: str | None = None
    abertura_erro: str | None = None
    enviada_at: datetime | None = None
    conferencia: dict | None = None
    conferencia_ligada: bool = False
    instrucoes: list[InstrucaoOut] = Field(default_factory=list)


class AbrirChamadoIn(BaseModel):
    abrir: bool


class DecisaoIn(BaseModel):
    acao: Literal["mandar", "trocar", "nao_abrir"]
    motivo: str | None = None


class InstrucaoIn(BaseModel):
    texto: str = Field(min_length=1, max_length=2000)


class PreviaLinhaIn(BaseModel):
    sku: str | None = None
    produtos: str | None = None
    quantidade: int = 1
    motivo_devolucao: str | None = None
    observacao: str | None = None


class PreviaIn(BaseModel):
    linhas: list[PreviaLinhaIn] = Field(default_factory=list, max_length=50)
    plataforma: str | None = None
    link_envio: str | None = None
    link_abertura: str | None = None
    fotos: int = Field(default=0, ge=0, le=50)


class PreviaOut(BaseModel):
    abre: bool = False
    motivo: str | None = None
    texto: str | None = None
    avisos: list[str] = Field(default_factory=list)


def _nome(user: User) -> str:
    return (user.name or user.email or "").strip() or "alguém"


async def _linhas(session: AsyncSession, pedido: str) -> list[Devolution]:
    return list(
        (
            await session.execute(
                select(Devolution)
                .where(func.btrim(Devolution.pedido_bling) == pedido)
                .order_by(Devolution.created_at)
            )
        )
        .scalars()
        .all()
    )


async def _travar_disparo(session: AsyncSession, ch: Chamado) -> None:
    """Pega a MESMA trava do job que manda a contestação (worker e cron das
    pendentes) antes de mexer na abertura. Se o job está no meio do envio — a
    conferência da IA pode levar dezenas de segundos —, o "Não" ou a decisão
    daqui não podem ser escritos por cima: 409, a pessoa tenta de novo em
    seguida e vê o que aconteceu."""
    pegou = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
            {"k": f"chamado_devolucao_disparar:{ch.id}"},
        )
    ).scalar()
    if not pegou:
        raise HTTPException(
            409,
            detail={
                "code": "chamado_sendo_enviado",
                "message": "A contestação está sendo enviada agora — "
                "espere alguns segundos e veja de novo.",
            },
        )


async def _rastreio(session: AsyncSession, pedido: str) -> DevolucaoRastreio:
    """A linha do pedido em devolucao_rastreio (cria se não existe), sem exigir
    o espelho do Bling — o chamado segurado de um pedido fora do espelho também
    precisa de saída."""
    row = await session.get(DevolucaoRastreio, pedido)
    if row is None:
        row = DevolucaoRastreio(pedido_bling=pedido)
        session.add(row)
    return row


async def _chamado_do_pedido(session: AsyncSession, pedido: str) -> Chamado | None:
    """O chamado de devolução mais recente do pedido."""
    return (
        await session.execute(
            select(Chamado)
            .where(Chamado.pedido_bling == pedido, Chamado.origem == "devolucao")
            .order_by(Chamado.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _janela(session: AsyncSession, pedido: str) -> ChamadoJanelaOut:
    linhas = await _linhas(session, pedido)
    pede = [d for d in linhas if chamados_svc.motivo_pede_chamado(d)]
    rast = await session.get(DevolucaoRastreio, pedido)
    ch = await _chamado_do_pedido(session, pedido)
    out = ChamadoJanelaOut(
        pedido_bling=pedido,
        pede_chamado=bool(pede),
        motivo=(pede[0].motivo_devolucao if pede else None),
        abrir_chamado=rast.abrir_chamado if rast is not None else None,
        conferencia=rast.conferencia if rast is not None else None,
        conferencia_ligada=conferencia.ligada(),
    )
    if ch is not None:
        abertura = await cd.mensagem_abertura(session, ch)
        out.chamado_id = ch.id
        out.numero = ch.chamado
        out.plataforma = ch.plataforma
        out.status_plataforma = ch.status_plataforma
        out.resolvido = ch.resolvido
        if abertura is not None:
            out.abertura_status = abertura.status
            out.abertura_erro = abertura.erro
            out.enviada_at = abertura.enviada_at
        msgs = (
            (
                await session.execute(
                    select(ChamadoMensagem)
                    .where(
                        ChamadoMensagem.chamado_id == ch.id,
                        ChamadoMensagem.tipo.in_(("instrucao", "analise")),
                    )
                    .order_by(ChamadoMensagem.created_at)
                )
            )
            .scalars()
            .all()
        )
        analises = [m.created_at for m in msgs if m.tipo == "analise" and m.created_at]
        out.instrucoes = [
            InstrucaoOut(
                texto=m.texto,
                autor_nome=m.autor_nome,
                em=m.created_at,
                lida=any(a >= m.created_at for a in analises) if m.created_at else False,
            )
            for m in msgs
            if m.tipo == "instrucao"
        ]
    if rast is not None and (rast.instrucao_ia_pendente or "").strip():
        autor = await session.get(User, rast.instrucao_ia_por) if rast.instrucao_ia_por else None
        out.instrucoes.append(
            InstrucaoOut(
                texto=rast.instrucao_ia_pendente,
                autor_nome=(autor.name or autor.email) if autor is not None else None,
                em=rast.instrucao_ia_em,
                esperando_chamado=True,
            )
        )
    return out


@router.get("/{pedido_bling}/chamado", response_model=ChamadoJanelaOut)
async def chamado_da_janela(
    pedido_bling: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "view"))],
) -> ChamadoJanelaOut:
    """O bloco Chamado da janela: Sim/Não, estado da contestação, o que a IA
    achou e as instruções dadas."""
    pedido = pedido_bling.strip()
    await antigo._exigir_pedido_da_equipe(session, await resolve_team_scope(session, user), pedido)
    return await _janela(session, pedido)


@router.post("/{pedido_bling}/abrir-chamado", response_model=ChamadoJanelaOut)
async def abrir_chamado(
    pedido_bling: str,
    body: AbrirChamadoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> ChamadoJanelaOut:
    """ "Abrir chamado: Sim ou Não" (vale pro pedido). Não: a contestação que
    ainda não saiu é cancelada (o chamado fica Encerrado, nada vai pra
    plataforma); depois que saiu, não dá — 409 `chamado_ja_saiu`. Sim (depois
    de um Não): volta ao automático e, se o motivo pede, a contestação sai."""
    pedido = pedido_bling.strip()
    await antigo._exigir_pedido_da_equipe(session, await resolve_team_scope(session, user), pedido)
    rast = await antigo._rastreio_do_pedido(session, pedido)
    ch = await _chamado_do_pedido(session, pedido)
    if ch is not None and not body.abrir:
        await _travar_disparo(session, ch)
    abertura = await cd.mensagem_abertura(session, ch) if ch is not None else None
    if abertura is not None:
        await session.refresh(abertura)
    nome = _nome(user)
    if not body.abrir:
        if abertura is not None and abertura.status in ("enviada", "enviando"):
            raise HTTPException(409, detail={"code": "chamado_ja_saiu"})
        rast.abrir_chamado = False
        rast.updated_by = user.id
        if (
            ch is not None
            and not ch.resolvido
            and ch.status_plataforma not in chamados_svc.STATUS_FINAIS
        ):
            await cd._encerrar_chamado(
                session,
                ch,
                evento=f"{nome} marcou “Abrir chamado: Não” na janela da devolução — "
                "encerrado sem contestar.",
            )
        await session.commit()
        logger.info("devolucao_abrir_chamado_nao", pedido_bling=pedido, user_id=str(user.id))
        return await _janela(session, pedido)

    era_nao = rast.abrir_chamado is False
    rast.abrir_chamado = None
    rast.updated_by = user.id
    if (
        era_nao
        and ch is not None
        and not ch.resolvido
        and ch.status_plataforma == chamados_svc.STATUS_ENCERRADO
        and (abertura is None or abertura.status != "enviada")
    ):
        ev = chamados_svc.sair_de_encerrado(
            ch, f"{nome} marcou “Abrir chamado: Sim” na janela da devolução"
        )
        if ev is not None:
            session.add(ev)
    await session.commit()
    dev = next(
        (d for d in await _linhas(session, pedido) if chamados_svc.motivo_pede_chamado(d)), None
    )
    if dev is not None:
        ch2 = await cd.garantir_chamado(session, dev)
        await session.commit()
        if ch2 is not None:
            await cd.agendar_disparo(session, ch2, dev)
    logger.info("devolucao_abrir_chamado_sim", pedido_bling=pedido, user_id=str(user.id))
    return await _janela(session, pedido)


@router.post("/{pedido_bling}/chamado/decisao", response_model=ChamadoJanelaOut)
async def decidir_conferencia(
    pedido_bling: str,
    body: DecisaoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> ChamadoJanelaOut:
    """A IA segurou a contestação (o motivo não bate): a pessoa decide.
    `mandar` = manda como está; `trocar` = troca o motivo das peças que abrem
    chamado pelo `motivo` e manda; `nao_abrir` = não contesta (Encerrado)."""
    pedido = pedido_bling.strip()
    await antigo._exigir_pedido_da_equipe(session, await resolve_team_scope(session, user), pedido)
    ch = await _chamado_do_pedido(session, pedido)
    if ch is not None:
        await _travar_disparo(session, ch)
    abertura = await cd.mensagem_abertura(session, ch) if ch is not None else None
    if abertura is not None:
        await session.refresh(abertura)
    if ch is None or abertura is None or abertura.status != conferencia.STATUS_SEGURADA:
        raise HTTPException(409, detail={"code": "nada_segurado"})
    if ch.resolvido or ch.status_plataforma == chamados_svc.STATUS_ENCERRADO:
        raise HTTPException(409, detail={"code": "chamado_encerrado"})
    rast = await _rastreio(session, pedido)
    linhas = await _linhas(session, pedido)
    pede = [d for d in linhas if chamados_svc.motivo_pede_chamado(d)]
    nome = _nome(user)
    agora = datetime.now(UTC)
    conf = dict(rast.conferencia or {})
    conf.update(decidido_por=nome, decidido_em=agora.isoformat(), decisao=body.acao)

    if body.acao == "nao_abrir" or not pede:
        rast.abrir_chamado = False
        rast.conferencia = conf
        await cd._encerrar_chamado(
            session,
            ch,
            evento=(
                f"{nome} viu o aviso da IA e escolheu não abrir chamado — encerrado sem contestar."
            ),
        )
        await session.commit()
        return await _janela(session, pedido)

    if body.acao == "trocar":
        novo = (body.motivo or "").strip()
        if (
            novo not in conferencia.MOTIVOS
            or novo.lower() not in chamados_svc.MOTIVOS_ABREM_CHAMADO
        ):
            raise HTTPException(422, detail={"code": "motivo_nao_abre_chamado"})
        anterior = (pede[0].motivo_devolucao or "").strip()
        for d in pede:
            d.motivo_devolucao = novo
            # As mesmas travas do PATCH de motivo (mala/eletro sem o vídeo do
            # envio, pedido da Fraude sem vídeo): 422 e nada muda.
            await antigo._exigir_video_fraude(session, d)
            antigo._exigir_link_envio(d)
        if (ch.observacao or "").startswith(chamados_svc.OBS_AUTO_PREFIXO):
            ch.observacao = f"{chamados_svc.OBS_AUTO_PREFIXO}{novo}"
        session.add(
            chamados_svc.registrar_sistema(
                ch,
                f'{nome} trocou o motivo de "{anterior}" para "{novo}" (sugestão da IA) e mandou.',
            )
        )
    else:
        session.add(
            chamados_svc.registrar_sistema(ch, f"{nome} viu o aviso da IA e mandou como está.")
        )
    # O que vale agora já foi visto pela pessoa: a IA não segura de novo. A chave
    # sai da MESMA leitura do disparo (as linhas do pedido como o worker vê).
    linhas_disparo = await cd._linhas_do_pedido(session, pede[0])
    conf["chave"] = conferencia.chave(
        conferencia.pecas_de(linhas_disparo or [pede[0]]), rast.observacao
    )
    conf["resultado"] = "aceito"
    rast.conferencia = conf
    abertura.status = "pendente"
    abertura.erro = None
    msg_comprador = None
    if body.acao == "trocar":
        # Virou "Bloqueado": a Shopee ganha o pedido de senha ao comprador, como no PATCH.
        msg_comprador = await devolucao_mensagem_comprador.garantir(session, pede[0])
    await session.commit()
    await cd.agendar_disparo(session, ch, pede[0])
    await devolucao_mensagem_comprador.agendar(session, msg_comprador)
    logger.info(
        "devolucao_conferencia_decidida", pedido_bling=pedido, acao=body.acao, user_id=str(user.id)
    )
    return await _janela(session, pedido)


@router.post("/{pedido_bling}/instrucao", response_model=ChamadoJanelaOut)
async def instruir_ia(
    pedido_bling: str,
    body: InstrucaoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> ChamadoJanelaOut:
    """ "Instrução IA": vai pro chamado como instrução (a IA de Chamado lê
    primeiro, não vai pra plataforma). Sem chamado ainda, espera e entra nele
    quando ele nascer."""
    pedido = pedido_bling.strip()
    texto = body.texto.strip()
    if not texto:
        raise HTTPException(422, detail={"code": "instrucao_vazia"})
    await antigo._exigir_pedido_da_equipe(session, await resolve_team_scope(session, user), pedido)
    ch = await _chamado_do_pedido(session, pedido)
    if ch is not None and ch.resolvido:
        raise HTTPException(422, detail={"code": "chamado_concluido"})
    if ch is not None:
        session.add(
            chamados_svc.nova_mensagem(
                ch,
                texto=texto,
                tipo="instrucao",
                direcao="sistema",
                autor_nome=f"Devoluções ({_nome(user)})",
                autor_id=user.id,
                status="registrada",
            )
        )
        chamados_leitura.ligar_consulta_do_texto(ch, texto)
    else:
        rast = await antigo._rastreio_do_pedido(session, pedido)
        atual = (rast.instrucao_ia_pendente or "").strip()
        rast.instrucao_ia_pendente = f"{atual}\n{texto}" if atual else texto
        rast.instrucao_ia_por = user.id
        rast.instrucao_ia_em = datetime.now(UTC)
    await session.commit()
    logger.info("devolucao_instrucao_ia", pedido_bling=pedido, com_chamado=ch is not None)
    return await _janela(session, pedido)


@router.post("/{pedido_bling}/chamado/previa", response_model=PreviaOut)
async def previa_do_chamado(
    pedido_bling: str,
    body: PreviaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "view"))],
) -> PreviaOut:
    """O texto que a contestação levaria com o que está na janela agora (as
    peças lançadas e as por lançar, os links e as fotos de Provas). Mesma
    função do disparo (`texto_padrao`); nada é gravado."""
    pedido = pedido_bling.strip()
    await antigo._exigir_pedido_da_equipe(session, await resolve_team_scope(session, user), pedido)
    pm = (
        await session.execute(
            select(BlingOrder.numeroloja).where(BlingOrder.numero == pedido).limit(1)
        )
    ).scalar_one_or_none()
    devs = [
        Devolution(
            pedido_bling=pedido,
            pedido_marketplace=pm,
            sku=(x.sku or "").strip() or None,
            produtos=(x.produtos or "").strip() or None,
            quantidade=max(1, x.quantidade or 1),
            motivo_devolucao=(x.motivo_devolucao or "").strip() or None,
            observacao=(x.observacao or "").strip() or None,
            link_envio=(body.link_envio or "").strip() or None,
            link_abertura=(body.link_abertura or "").strip() or None,
        )
        for x in body.linhas
    ]
    pede = [d for d in devs if chamados_svc.motivo_pede_chamado(d)]
    if not pede:
        return PreviaOut()
    dev = pede[0]
    plat = cd.plataforma_de(body.plataforma)
    avisos: list[str] = []
    if plat not in cd.COM_API:
        avisos.append(
            "Esta plataforma não tem API de contestação: "
            "o chamado fica registrado pra abrir na mão."
        )
    reason = cd.reason_para(dev)
    if plat == cd.PLAT_ML:
        reason = cd._reason_ml(dev, [None] * body.fotos)
    texto_ = cd.texto_padrao(
        dev,
        reason,
        fotos=body.fotos,
        link_envio=(body.link_envio or "").strip() or None,
        linhas=devs,
        link_abertura=cd.link_abertura_do_pedido(dev, devs) or None,
    )
    if plat == cd.PLAT_TIKTOK:
        avisos.append("A TikTok aceita só 500 caracteres: o fim do texto pode ser cortado.")
    if plat == cd.PLAT_SHOPEE and cd._motivo(dev) == cd.MOTIVO_NAO_RECEBIDO:
        avisos.append("Na Shopee, o “Não recebido” sai com um texto próprio, mais curto.")
    return PreviaOut(abre=True, motivo=dev.motivo_devolucao, texto=texto_, avisos=avisos)
