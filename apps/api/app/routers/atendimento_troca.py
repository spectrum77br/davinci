"""Atendimento › lista "Ag. cancelamento" e sugestões de troca (item 4, fase 4b, 05/10/2026).

  GET /api/atendimento/ag-cancelamento[?codigo=<motivo>&dias=60]
      os pedidos em "Aguardando Cancelamento" (83955) no Bling dos últimos
      `dias` (pela `data` do pedido), COM ou SEM conversa — só 27 dos 78
      pedidos sem estoque de 60 dias tinham conversa no DaVinci. Para cada
      um: o porquê (o mesmo bloco do painel, `painel.bloco_do_motivo`, com o
      classificador `ag_cancelamento.classificar`), os itens, o prazo de
      envio na plataforma (`marketplace_ship_deadline`), a conversa
      principal (`etiqueta_fatos.conversas_do_pedido_bling`) e, na falta de
      estoque com `atendimento_troca_sugestoes_ativa`, as sugestões de troca
      (`troca_sugestoes.sugestoes_do_pedido`). O catálogo da troca é lido
      UMA vez por requisição (no 1º pedido que precisa); se a leitura
      falha, todos os pedidos recebem `falhou` sem nova tentativa (a
      consulta é pesada: não repetir por pedido). `codigo` filtra pelo
      motivo; `por_codigo` conta antes do filtro.

SÓ LEITURA, SEM BLING: nenhum GET nem escrita no Bling (as Observações do
pedido ficam no painel da conversa). Cada pedido traz a troca de produto
aberta (`motivo.troca_aberta`, fase 4c).

A TROCA DE PRODUTO (fase 4c, 07/10/2026 — `services/atendimento/troca.py`):

  POST /api/atendimento/pedidos/{numero_bling}/troca/previa
      as travas (do banco e ao vivo: só GETs), o antes e o depois, os passos,
      os aceites possíveis e o `previa_hash`;
  POST /api/atendimento/pedidos/{numero_bling}/troca
      o clique "Trocar" (`TrocaIn`): 200 com o `TrocaOut` mesmo parada no
      meio (o `estado` diz onde); 409 `{code, detail, troca_id?}` quando uma
      trava recusa; 404 sem o pedido; 422 entrada inválida;
  POST /api/atendimento/trocas/{troca_id}/retomar
      segue de onde parou (GET primeiro, nunca PUT);
  GET  /api/atendimento/trocas?abertas=true&pedido=
      as trocas (abertas primeiro na tela: as paradas pedem Retomar).

A OFERTA PELO CHAT (fase 4d, 07/10/2026 — `services/atendimento/troca_oferta.py`):

  POST /api/atendimento/pedidos/{numero_bling}/troca/oferta
      o botão "Enviar oferta": o texto da oferta da 4b (ou o editado) ao
      comprador pela conversa do pedido, pelo `enviar.enviar_resposta` com
      todas as travas dele; 200 com a mensagem (`status` enviada, revisar ou
      falhou); 409 `{code, detail}` numa trava (nada saiu); 422 no texto
      reprovado. Cada pedido da lista (e o painel) traz `motivo.oferta_envio`:
      o botão pode, ou o porquê de não (e, podendo, o `ultima_mensagem_id`
      que a tela devolve como `ultima_vista_id`) — e `motivo.troca_envio`, o
      mesmo para o "Trocar" (a chave, o piloto, as travas do pedido).

ACESSO (Eduardo, 07/10/2026 — a caixa aberta para a equipe em só leitura):
a MESMA TRAVA do router da caixa (`_so_admin`: GET passa para quem
`acesso.pode_ver`; escrever, só quem `acesso.pode_mexer`). Ler (a lista, as
trocas) = `_view`; escrever (prévia, troca, retomar) = `_so_admin` +
`atendimento.edit` + `margem.edit` (a troca APROVA a Margem); a oferta, o
MESMO (decisão (g): ela promete a troca ao comprador). O escopo por
equipe: pedido só das lojas da equipe (`bling_store_ids`), conversa só das
integrações dela. Quem não vê a Margem (`painel.ve_margem`) não recebe custo
nem % e vê o motivo da Margem como "em análise" (`painel.mascarar_motivo`).
Router à parte (o teste das rotas do painel lista as dele); o `main.py` o
inclui.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.deps.team_scope import TeamScope, resolve_team_scope
from app.models import AtendimentoConversa, AtendimentoTroca, BlingOrder, StoreInfo, User
from app.routers.atendimento import _edit, _no_escopo, _so_admin, _view
from app.schemas.atendimento_troca import (
    ListaAgCancelamentoOut,
    ListaTrocasOut,
    OfertaTrocaIn,
    OfertaTrocaOut,
    PreviaTrocaIn,
    PreviaTrocaOut,
    TrocaIn,
    TrocaOut,
)
from app.services.atendimento import etiqueta_fatos, painel, troca, troca_oferta
from app.services.atendimento.ag_cancelamento import classificar, status_na_plataforma
from app.services.atendimento.troca_sugestoes import (
    Catalogo,
    catalogo,
    sugestoes_do_pedido,
    sugestoes_falhou,
)

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)

# A janela da lista (dias, pela `data` do pedido).
DIAS_PADRAO = 60
DIAS_MAX = 120

# Escrever na troca: a troca aprova a Margem (o recurso é `margem`, no
# singular — o mesmo da aba Margem, `routers/margens.py`).
_margem_edit = require_permission("margem", "edit")


async def _itens_e_prazos(session: AsyncSession, numeros: list[str]) -> dict[str, dict[str, Any]]:
    """{nº do Bling: itens (sku, descrição, quantidade), data e prazo de envio} — uma consulta."""
    if not numeros:
        return {}
    linhas = (
        await session.execute(
            select(
                BlingOrder.numero,
                BlingOrder.item_index,
                BlingOrder.item_codigo,
                BlingOrder.item_descricao,
                BlingOrder.item_quantidade,
                BlingOrder.data,
                BlingOrder.marketplace_ship_deadline,
            )
            .where(BlingOrder.numero.in_(numeros))
            .order_by(BlingOrder.numero, BlingOrder.item_index)
        )
    ).all()
    saida: dict[str, dict[str, Any]] = {}
    for r in linhas:
        d = saida.setdefault(r.numero, {"itens": [], "data": None, "prazo_envio": None})
        d["data"] = d["data"] or r.data
        d["prazo_envio"] = d["prazo_envio"] or r.marketplace_ship_deadline
        if (r.item_codigo or "").strip():
            d["itens"].append(
                {
                    "sku": r.item_codigo.strip(),
                    "descricao": r.item_descricao,
                    "quantidade": r.item_quantidade,
                }
            )
    return saida


async def _lojas(
    session: AsyncSession, lojas: set[str]
) -> dict[str, tuple[str | None, str | None]]:
    """{`bling_orders.loja`: (plataforma, conta)} pelo cadastro de Lojas."""
    if not lojas:
        return {}
    linhas = (
        await session.execute(
            select(StoreInfo.bling_store_id, StoreInfo.platform, StoreInfo.account_name).where(
                StoreInfo.bling_store_id.in_(sorted(lojas))
            )
        )
    ).all()
    saida: dict[str, tuple[str | None, str | None]] = {}
    for r in linhas:
        saida.setdefault(
            str(r.bling_store_id),
            (etiqueta_fatos.normalizar_plataforma(r.platform), r.account_name),
        )
    return saida


async def _conversa(
    session: AsyncSession, numero: str, scope: TeamScope
) -> AtendimentoConversa | None:
    """A conversa principal do pedido, dentro do escopo da equipe."""
    for c in await etiqueta_fatos.conversas_do_pedido_bling(session, numero):
        if _no_escopo(scope, c.integration_id):
            return c
    return None


def _urgente_primeiro(p: dict) -> tuple:
    """O prazo de envio mais curto primeiro (sem prazo no fim); empate: o pedido mais recente."""
    prazo: datetime | None = p["prazo_envio"]
    data: datetime | None = p["data"]
    return (
        prazo is None,
        prazo.timestamp() if prazo is not None else 0.0,
        -(data.timestamp()) if data is not None else 0.0,
    )


@router.get("/ag-cancelamento", response_model=ListaAgCancelamentoOut)
async def lista_ag_cancelamento(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    codigo: Annotated[str | None, Query(max_length=32)] = None,
    dias: Annotated[int, Query(ge=1, le=DIAS_MAX)] = DIAS_PADRAO,
) -> ListaAgCancelamentoOut:
    """Os pedidos em 83955 com o porquê e as sugestões de troca. Só leitura, sem Bling."""
    scope = await resolve_team_scope(session, user)
    agora = datetime.now(UTC)
    desde = agora - timedelta(days=dias)
    pedidos = await etiqueta_fatos.pedidos_em_83955(session, desde)
    if not scope.unrestricted:
        pedidos = [p for p in pedidos if p.loja and str(p.loja) in scope.bling_store_ids]
    extras = await _itens_e_prazos(session, [p.numero for p in pedidos])
    lojas = await _lojas(session, {str(p.loja) for p in pedidos if p.loja})
    ve_custo = painel.ve_margem(user)
    ativas = bool(get_settings().atendimento_troca_sugestoes_ativa)
    filtro = (codigo or "").strip() or None

    # A troca de produto aberta de cada pedido (fase 4c): uma consulta, num
    # SAVEPOINT — a tabela pode ainda não existir (deploy antes do alembic).
    abertas: dict[str, dict] = {}
    try:
        async with session.begin_nested():
            abertas = await troca.trocas_abertas_por_pedido(session, [p.numero for p in pedidos])
    except Exception as e:  # noqa: BLE001 — a troca fora nunca derruba a lista
        logger.warning("atendimento_troca_lista_abertas_falhou", err=type(e).__name__)

    por_codigo: Counter[str] = Counter()
    saida: list[dict] = []
    # O catálogo da troca: lido no 1º pedido que precisa; a falha não se repete.
    cat: Catalogo | None = None
    catalogo_falhou = False
    # As travas do pedido (só banco), uma leitura para o "Trocar" e a oferta.
    memo: dict[str, Any] = {}
    for p in pedidos:
        conversa = await _conversa(session, p.numero, scope)
        m = classificar(
            p, status_plataforma=status_na_plataforma(conversa.dados) if conversa else None
        )
        if m is None:
            continue
        # Quem não vê a Margem conta e filtra pelo motivo mascarado.
        motivo = painel.bloco_do_motivo(m, ve_margem=ve_custo)
        motivo["troca_aberta"] = abertas.get(p.numero)
        motivo["troca_envio"] = await _troca_envio(session, p.numero, motivo, user, memo)
        motivo["oferta_envio"] = await _oferta_envio(
            session, p.numero, motivo, conversa, user, memo
        )
        por_codigo[motivo["codigo"]] += 1
        if filtro and motivo["codigo"] != filtro:
            continue
        extra = extras.get(p.numero) or {"itens": [], "data": None, "prazo_envio": None}
        sugestoes = None
        if ativas and m.pode_sugerir_troca:
            if cat is None and not catalogo_falhou:
                try:
                    async with session.begin_nested():
                        cat = await catalogo(session)
                except Exception as e:  # noqa: BLE001 — o catálogo fora nunca derruba a lista
                    catalogo_falhou = True
                    logger.warning(
                        "atendimento_troca_lista_catalogo_falhou",
                        pedido=p.numero,
                        err=type(e).__name__,
                    )
            if cat is None:
                sugestoes = sugestoes_falhou(ve_custo=ve_custo)
            else:
                try:
                    # Com o catálogo em mãos, só Python (nenhuma consulta).
                    sugestoes = await sugestoes_do_pedido(
                        session, m.skus, extra["itens"], ve_custo=ve_custo, cat=cat
                    )
                except Exception as e:  # noqa: BLE001 — um pedido nunca derruba a lista
                    logger.warning(
                        "atendimento_troca_lista_falhou", pedido=p.numero, err=type(e).__name__
                    )
                    sugestoes = sugestoes_falhou(ve_custo=ve_custo)
        plataforma, conta = lojas.get(str(p.loja), (None, None)) if p.loja else (None, None)
        saida.append(
            {
                "numero": p.numero,
                "numeroloja": p.numeroloja,
                "bling_id": p.bling_id,
                "loja": p.loja,
                "plataforma": plataforma or (conversa.plataforma if conversa else None),
                "conta": conta,
                "data": extra["data"],
                "prazo_envio": extra["prazo_envio"],
                "motivo": motivo,
                "itens": extra["itens"],
                "conversa_id": str(conversa.id) if conversa is not None else None,
                "sugestoes_troca": sugestoes,
            }
        )
    saida.sort(key=_urgente_primeiro)
    # Só leitura: nada a gravar.
    await session.rollback()
    return ListaAgCancelamentoOut(
        pedidos=saida,
        total=len(saida),
        por_codigo=dict(por_codigo),
        desde=desde,
        sugestoes_ativas=ativas,
        ve_custo=ve_custo,
        gerado_em=agora,
    )


# ── A troca de produto (fase 4c) ──────────────────────────────────────────


def _recusa(e: troca.TrocaRecusada) -> HTTPException:
    detalhe: dict[str, Any] = {"code": e.code, "detail": e.detail}
    if e.troca_id is not None:
        detalhe["troca_id"] = str(e.troca_id)
    return HTTPException(e.status, detail=detalhe)


async def _no_escopo_do_pedido(session: AsyncSession, user: User, numero: str) -> None:
    """O pedido é de uma loja da equipe? Fora do escopo = 404 (como pedido que não existe)."""
    scope = await resolve_team_scope(session, user)
    if scope.unrestricted:
        return
    loja = await session.scalar(select(BlingOrder.loja).where(BlingOrder.numero == numero).limit(1))
    if not loja or str(loja) not in scope.bling_store_ids:
        raise HTTPException(404, detail={"code": "pedido_nao_encontrado"})


@router.post(
    "/pedidos/{numero_bling}/troca/previa",
    response_model=PreviaTrocaOut,
    dependencies=[Depends(_margem_edit)],
)
async def previa_da_troca(
    body: PreviaTrocaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
    numero_bling: Annotated[str, Path(min_length=1, max_length=32)],
) -> PreviaTrocaOut:
    """A prévia da troca: só GETs no Bling e na Shopee; nada da troca é gravado."""
    await _no_escopo_do_pedido(session, user, numero_bling.strip())
    try:
        dados = await troca.previa(
            session,
            user,
            numero_bling=numero_bling,
            sku_antigo=body.sku_antigo,
            sku_novo=body.sku_novo,
            conversa_id=body.conversa_id,
        )
    except troca.TrocaRecusada as e:
        raise _recusa(e) from e
    return PreviaTrocaOut(**dados)


@router.post(
    "/pedidos/{numero_bling}/troca",
    response_model=TrocaOut,
    dependencies=[Depends(_margem_edit)],
)
async def trocar(
    body: TrocaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
    numero_bling: Annotated[str, Path(min_length=1, max_length=32)],
) -> TrocaOut:
    """O clique "Trocar": 200 com a troca (o `estado` diz onde parou); 409 na trava."""
    await _no_escopo_do_pedido(session, user, numero_bling.strip())
    # Antes do serviço: um erro no meio faz rollback e expira o usuário.
    ve = painel.ve_margem(user)
    aceite = None
    if body.aceite is not None:
        aceite = troca.Aceite(
            mensagem_aceite_id=body.aceite.mensagem_aceite_id,
            fonte=body.aceite.fonte,
            texto=body.aceite.texto,
            em=body.aceite.em,
        )
    try:
        feita = await troca.executar(
            session,
            user,
            numero_bling=numero_bling,
            sku_antigo=body.sku_antigo,
            sku_novo=body.sku_novo,
            conversa_id=body.conversa_id,
            aceite=aceite,
            confirmar=body.confirmar,
            previa_hash=body.previa_hash,
            idem_key=body.idem_key,
        )
    except troca.TrocaRecusada as e:
        raise _recusa(e) from e
    return TrocaOut(**troca.troca_out(feita, ve_custo=ve))


@router.post(
    "/trocas/{troca_id}/retomar",
    response_model=TrocaOut,
    dependencies=[Depends(_margem_edit)],
)
async def retomar_troca(
    troca_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> TrocaOut:
    """Segue a troca de onde parou: GET primeiro, só para frente, nunca PUT."""
    try:
        troca.conferir_ativa()  # a chave antes da tabela (que pode nem existir ainda)
    except troca.TrocaRecusada as e:
        raise _recusa(e) from e
    numero = await session.scalar(
        select(AtendimentoTroca.pedido_bling).where(AtendimentoTroca.id == troca_id)
    )
    if numero is None:
        raise HTTPException(404, detail={"code": "troca_nao_encontrada"})
    await _no_escopo_do_pedido(session, user, numero)
    ve = painel.ve_margem(user)
    try:
        feita = await troca.retomar(session, user, troca_id)
    except troca.TrocaRecusada as e:
        raise _recusa(e) from e
    return TrocaOut(**troca.troca_out(feita, ve_custo=ve))


@router.get("/trocas", response_model=ListaTrocasOut)
async def lista_trocas(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    abertas: bool = True,
    pedido: Annotated[str | None, Query(max_length=32)] = None,
) -> ListaTrocasOut:
    """As trocas de produto (as abertas por padrão), da mais nova para a mais velha."""
    scope = await resolve_team_scope(session, user)
    itens: list[AtendimentoTroca] = []
    try:
        # Num SAVEPOINT: a tabela pode ainda não existir (deploy antes do alembic).
        async with session.begin_nested():
            itens = await troca.listar(session, abertas=abertas, pedido=pedido)
    except Exception as e:  # noqa: BLE001 — sem a tabela, a lista vem vazia
        logger.warning("atendimento_troca_lista_trocas_falhou", err=type(e).__name__)
    if not scope.unrestricted and itens:
        lojas = {
            r.numero: r.loja
            for r in (
                await session.execute(
                    select(BlingOrder.numero, BlingOrder.loja)
                    .where(BlingOrder.numero.in_({t.pedido_bling for t in itens}))
                    .distinct()
                )
            ).all()
        }
        itens = [t for t in itens if str(lojas.get(t.pedido_bling) or "") in scope.bling_store_ids]
    ve = painel.ve_margem(user)
    return ListaTrocasOut(itens=[TrocaOut(**troca.troca_out(t, ve_custo=ve)) for t in itens])


# ── A oferta de troca pelo chat (fase 4d) ─────────────────────────────────


async def _troca_envio(
    session: AsyncSession, numero: str, motivo: dict, user: User, memo: dict[str, Any]
) -> dict:
    """O `troca_envio` de um pedido da lista, num SAVEPOINT (nunca derruba a lista)."""
    try:
        async with session.begin_nested():
            return await troca.situacao_da_troca(
                session, numero=numero, motivo=motivo, user=user, memo=memo
            )
    except Exception as e:  # noqa: BLE001 — a conferência fora nunca derruba a lista
        logger.warning("atendimento_troca_lista_troca_falhou", pedido=numero, err=type(e).__name__)
        return dict(troca.TROCA_NAO_CONFERIDA)


async def _oferta_envio(
    session: AsyncSession,
    numero: str,
    motivo: dict,
    conversa: AtendimentoConversa | None,
    user: User,
    memo: dict[str, Any] | None = None,
) -> dict:
    """O `oferta_envio` de um pedido da lista, num SAVEPOINT (nunca derruba a lista)."""
    try:
        async with session.begin_nested():
            return await troca_oferta.situacao_da_oferta(
                session, numero=numero, motivo=motivo, conversa=conversa, user=user, memo=memo
            )
    except Exception as e:  # noqa: BLE001 — a conferência fora nunca derruba a lista
        logger.warning("atendimento_troca_lista_oferta_falhou", pedido=numero, err=type(e).__name__)
        return dict(troca_oferta.OFERTA_NAO_CONFERIDA)


@router.post(
    "/pedidos/{numero_bling}/troca/oferta",
    response_model=OfertaTrocaOut,
    dependencies=[Depends(_margem_edit)],
)
async def enviar_oferta_de_troca(
    body: OfertaTrocaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
    numero_bling: Annotated[str, Path(min_length=1, max_length=32)],
) -> OfertaTrocaOut:
    """O botão "Enviar oferta": o texto da oferta ao comprador pela conversa do pedido.

    `_so_admin` + `atendimento.edit` + `margem.edit`, como a troca (decisão
    (g) do dono): a oferta não fala com o Bling, mas promete a troca — quem
    não pode trocar não oferece. Erro da PLATAFORMA não é erro HTTP: 200 com
    a mensagem em `falhou`/`revisar`, como no responder.
    """
    numero = numero_bling.strip()
    await _no_escopo_do_pedido(session, user, numero)
    scope = await resolve_team_scope(session, user)
    try:
        dados = await troca_oferta.enviar_oferta(
            session,
            user,
            numero_bling=numero,
            sku_novo=body.sku_novo,
            sku_antigo=body.sku_antigo,
            conversa_id=body.conversa_id,
            texto=body.texto,
            ultima_vista_id=body.ultima_vista_id,
            confirmar=body.confirmar,
            integracoes=None if scope.unrestricted else scope.integration_ids,
        )
    except troca.TrocaRecusada as e:
        raise _recusa(e) from e
    return OfertaTrocaOut(**dados)
