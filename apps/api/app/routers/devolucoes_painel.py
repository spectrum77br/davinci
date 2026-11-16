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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.deps.team_scope import resolve_team_scope
from app.models import Chamado, DevolucaoAnexo, DevolucaoRastreio, Devolution, User, UserRole
from app.routers import devolutions as antigo
from app.schemas.devolutions import AcompanhamentoItemOut
from app.services import devolucao_painel as contas
from app.services import devolucao_prazo_acompanhamento
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
    mostrar: Literal["abertas", "resolvidas", "todas"],
    lojas: set[str] | None,
) -> list[PainelItemOut]:
    rows = await antigo.acompanhamento_rows(session, lojas=lojas, painel=mostrar)
    pedidos = {(r.get("pedido_bling") or "").strip() for r in rows} - {""}
    lancs = await _lancamentos_por_pedido(session, pedidos)
    anexos = await _anexos_por_lancamento(session, [d.id for ds in lancs.values() for d in ds])
    chamados = await _chamados_por_pedido(session, pedidos)
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
                id=ch.id, numero=ch.chamado, resolvido=ch.resolvido, resolvido_at=ch.resolvido_at
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
) -> PainelOut:
    """A lista única do painel novo. Volta inteira (teto de 4000 linhas);
    busca, filtros, cartões e "Onde agir hoje" são contas do navegador sobre
    ela. Quem tem equipe de vendas só recebe as lojas da equipe."""
    scope = await resolve_team_scope(session, user)
    lojas = antigo._lojas_da_equipe(scope)
    items = await montar_itens(session, user, mostrar=mostrar, lojas=lojas)
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
    if len(como) < 2 or como == antigo.PAINEL_LARGADA:
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
        pedido_bling=pedido, como=row.resolvido_como, situacao=sit_id,
        via_desvio=via_desvio, user_id=str(user.id),
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
    logger.info(
        "devolucao_painel_reaberto", pedido_bling=pedido, era=como, user_id=str(user.id)
    )
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
    """"Meses — como ficou cada mês": por dia, a partir da VIRADA (o dia em
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
                text(_RESUMO_RESOLVIDAS_SQL), {"desde": desde, "largada": antigo.PAINEL_LARGADA}
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
            for x in (
                await session.execute(text(_RESUMO_CHAMADO_SQL), {"desde": desde})
            ).mappings()
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
