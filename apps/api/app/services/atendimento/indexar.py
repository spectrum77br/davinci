"""O job `atendimento_indexar_pedidos`: pedidos e avaliações da Shopee por comprador.

A Shopee não filtra pedido nem avaliação por comprador — e o cartão "Cliente"
da caixa (`cliente.py`) precisa responder "já comprou? avaliou mal?" na hora
em que a conversa abre, sem varrer a loja. Então, de hora em hora
(`worker.atendimento_indexar_pedidos`), para cada loja da Shopee:

  1. `get_order_list` dos pedidos que MUDARAM nas últimas 2 h (por
     `update_time`: pedido novo e pedido que virou COMPLETED/CANCELLED/
     TO_RETURN entram do mesmo jeito) e `get_order_detail` em lotes de 50
     pedindo SÓ `buyer_user_id,order_status,total_amount,create_time,item_list`
     → `atendimento_pedidos_comprador`. Janela de 2 h num job de 1 h: uma
     rodada que atrasa ou falha não deixa buraco.
  2. `get_comment` do mais novo para trás, só até alcançar avaliações que o
     índice já tem → `atendimento_avaliacoes_loja`. Com o cron das
     avaliações ligado (`atendimento_avaliacoes_ativa`, 02/10/2026), esta
     parte fica com ELE (`avaliacoes.py`, a cada 30 min, que também relê as
     sem resposta): aqui ficam só os pedidos — duas leituras da mesma
     página por hora seriam cota gasta à toa.

Por que cada cuidado:

- DESLIGADO POR PADRÃO: só roda com `atendimento_leitura_ativa` (o mesmo
  interruptor da leitura das caixas). Deploy não liga nada. E a loja com o
  canal `desligado` fica de fora — é o interruptor que a equipe usa quando a
  Shopee começa a devolver 429 (o mesmo do painel Pedido e do sync).
- TETO POR RODADA: no máximo `MAX_PEDIDOS_RODADA` pedidos e
  `MAX_PAGINAS_AVALIACOES` páginas de avaliação por loja — um dia de promoção
  não pode prender o worker nem gastar a cota da API que os robôs de pedido
  e estoque usam. O que passar do teto de pedidos entra na próxima rodada
  (janela sobreposta); o histórico longo é da importação (`importar.py`).
- UMA RODADA POR VEZ (trava no Redis): o cron atrasado não roda por cima.
- CADA LOJA NA SUA SESSÃO, e pedidos e avaliações em transações separadas:
  a loja sem permissão de produto (get_comment) ainda indexa os pedidos.
- SÓ LEITURA na Shopee. Log só com ids, contagens e código de erro.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select

import app.db as _db
from app.config import get_settings
from app.models import AtendimentoCanal, Integration, IntegrationPlatform
from app.redis_client import redis
from app.services.atendimento import clientes, indice

logger = structlog.get_logger()

PLATAFORMA = "shopee"

# ── Pedidos ───────────────────────────────────────────────────────────────
JANELA_PEDIDOS = timedelta(hours=2)
LOTE_DETALHE = 50  # o máximo do `order_sn_list` da Shopee
MAX_PEDIDOS_RODADA = 500

# ── Avaliações ────────────────────────────────────────────────────────────
PAGINA_AVALIACOES = 50
MAX_PAGINAS_AVALIACOES = 6

# O status do canal que tira a loja da leitura (`sync.STATUS_DESLIGADO`).
CANAL_DESLIGADO = "desligado"

# ── Trava da rodada ───────────────────────────────────────────────────────
# Menor que o intervalo do cron (1 h): se o processo morrer, a próxima roda.
TRAVA_TTL_S = 50 * 60
CHAVE_TRAVA = "atendimento:indexar:rodada"

FabricaCliente = Callable[[Integration], Awaitable[Any]]


def _agora() -> datetime:
    """Relógio do módulo (os testes trocam)."""
    return datetime.now(UTC)


def _erro(exc: BaseException) -> str:
    """Texto de operação para o log: classe, HTTP e código — nunca o corpo."""
    # Import tardio: `sync` puxa o envio e os adaptadores; aqui só a regra.
    from app.services.atendimento.sync import erro_de_operacao

    return erro_de_operacao(exc)[0]


# ── Uma loja ──────────────────────────────────────────────────────────────


async def _desfazer(session: Any, integration: Integration) -> None:
    """Rollback da parte que falhou — e relê a loja, que o rollback expirou.

    Sem reler, a parte seguinte tocaria num objeto expirado, e numa sessão
    assíncrona isso é erro (carga preguiçosa fora do greenlet).
    """
    await session.rollback()
    await session.refresh(integration)


async def _indexar_pedidos(
    session: Any, integration: Integration, cliente: Any, agora: datetime
) -> tuple[int, bool]:
    """Pedidos que mudaram na janela → índice. (quantos gravou, a janela coube no teto?)"""
    pedidos, completo = await cliente.get_order_list_janela(
        time_from=int((agora - JANELA_PEDIDOS).timestamp()),
        time_to=int(agora.timestamp()),
        time_range_field="update_time",
        maximo=MAX_PEDIDOS_RODADA,
    )
    gravados = 0
    for inicio in range(0, len(pedidos), LOTE_DETALHE):
        for o in await cliente.get_order_detail_indice(pedidos[inicio : inicio + LOTE_DETALHE]):
            linha = indice.pedido_shopee(o)
            if linha is None:
                continue
            await indice.registrar_pedido(session, integration_id=integration.id, **linha)
            gravados += 1
    return gravados, completo


def _decrescente(horarios: list[datetime]) -> bool:
    return all(b <= a for a, b in zip(horarios, horarios[1:], strict=False))


@dataclass
class LeituraAvaliacoes:
    """O que uma leitura do `get_comment` fez — e de onde continuar."""

    paginas: int
    novas: int
    # O `next_cursor` da página seguinte à última lida ("" = do topo).
    cursor: str
    # True = chegou ao fim do que tinha de ler (sem `more`, só conhecidas, ou
    # já passou do começo da janela). False = parou no TETO de páginas: há
    # mais para trás, a partir de `cursor`.
    acabou: bool


async def indexar_avaliacoes(
    session: Any,
    integration: Integration,
    cliente: Any,
    *,
    max_paginas: int = MAX_PAGINAS_AVALIACOES,
    desde: datetime | None = None,
    parar_nas_conhecidas: bool = True,
    cursor: str = "",
    page_size: int = PAGINA_AVALIACOES,
) -> LeituraAvaliacoes:
    """Avaliações da loja, da mais nova para trás → índice.

    Para na primeira página em que TODAS já estão no índice — mas só se ela
    vier do mais novo para o mais velho: a ordem da `get_comment` não é
    documentada, e sem ela "achei conhecidas" não quer dizer "acabou" (aí a
    leitura vai até o teto de páginas). A importação desce o histórico
    inteiro (`parar_nas_conhecidas=False`: as de cima o job já indexou), para
    na página que já passou do começo da janela (`desde`) e, no teto de
    páginas, continua na execução seguinte do `cursor` devolvido.
    """
    paginas = 0
    novas = 0
    while paginas < max_paginas:
        resp = await cliente.get_comments(cursor=cursor, page_size=page_size)
        paginas += 1
        resp = resp if isinstance(resp, dict) else {}
        linhas = [
            linha
            for c in (resp.get("item_comment_list") or [])
            if (linha := indice.avaliacao_shopee(c)) is not None
        ]
        conhecidas = await indice.avaliacoes_conhecidas(
            session, integration.id, [linha["comentario_id"] for linha in linhas]
        )
        for linha in linhas:
            # A conhecida também é regravada: a resposta da loja (ou a edição
            # do cliente) chega depois, na mesma avaliação.
            await indice.registrar_avaliacao(session, integration_id=integration.id, **linha)
        novas += sum(1 for linha in linhas if linha["comentario_id"] not in conhecidas)
        horarios = [linha["criado_em"] for linha in linhas if linha["criado_em"] is not None]
        ordenada = _decrescente(horarios)
        if parar_nas_conhecidas and linhas and ordenada and len(conhecidas) == len(linhas):
            return LeituraAvaliacoes(paginas, novas, cursor, acabou=True)
        if desde is not None and horarios and ordenada and horarios[-1] < desde:
            # A página já passou do começo da janela: as próximas são mais velhas.
            return LeituraAvaliacoes(paginas, novas, cursor, acabou=True)
        proximo = str(resp.get("next_cursor") or "")
        if not resp.get("more") or not proximo or proximo == cursor:
            return LeituraAvaliacoes(paginas, novas, cursor, acabou=True)
        cursor = proximo
    return LeituraAvaliacoes(paginas, novas, cursor, acabou=False)


async def indexar_loja(
    integration_id: UUID, *, fabrica_cliente: FabricaCliente | None = None
) -> dict:
    """Uma loja da Shopee: pedidos e avaliações, cada parte na sua transação. Nunca levanta."""
    fabrica = fabrica_cliente or clientes.cliente_da_integracao
    resumo: dict[str, Any] = {"pedidos": 0, "avaliacoes": 0, "erros": 0, "teto": False}
    # `_db.SessionLocal` lido na hora da chamada (os testes trocam o engine).
    async with _db.SessionLocal() as session:
        integration = await session.get(Integration, integration_id)
        if integration is None or integration.archived_at is not None:
            return resumo
        try:
            cliente = await fabrica(integration)
        except Exception as exc:  # noqa: BLE001 — loja sem credencial não para as outras
            logger.warning(
                "atendimento_indexar_cliente_falhou",
                integration_id=str(integration_id),
                erro=_erro(exc),
            )
            resumo["erros"] += 1
            return resumo

        try:
            gravados, completo = await _indexar_pedidos(session, integration, cliente, _agora())
            await session.commit()
            resumo["pedidos"] = gravados
            resumo["teto"] = not completo
        except Exception as exc:  # noqa: BLE001
            await _desfazer(session, integration)
            resumo["erros"] += 1
            logger.warning(
                "atendimento_indexar_pedidos_falhou",
                integration_id=str(integration_id),
                erro=_erro(exc),
            )

        try:
            if get_settings().atendimento_avaliacoes_ativa:
                # O cron das avaliações lê (e relê) as avaliações: ver o topo.
                leitura = None
            else:
                leitura = await indexar_avaliacoes(session, integration, cliente)
                await session.commit()
            resumo["avaliacoes"] = leitura.novas if leitura is not None else 0
        except Exception as exc:  # noqa: BLE001
            await _desfazer(session, integration)
            resumo["erros"] += 1
            logger.warning(
                "atendimento_indexar_avaliacoes_falhou",
                integration_id=str(integration_id),
                erro=_erro(exc),
            )
    if resumo["teto"]:
        logger.info(
            "atendimento_indexar_teto",
            integration_id=str(integration_id),
            maximo=MAX_PEDIDOS_RODADA,
        )
    return resumo


# ── A rodada ──────────────────────────────────────────────────────────────


def canal_desligado(integration_id: Any, canal: str | None = None) -> Any:
    """EXISTS: a loja tem canal `desligado` (`canal` dá o nome; None = qualquer um).

    Na Shopee a loja tem um canal só (o chat): desligado, a loja inteira sai
    da leitura — índice e importação inclusive.
    """
    consulta = select(AtendimentoCanal.id).where(
        AtendimentoCanal.integration_id == integration_id,
        AtendimentoCanal.status == CANAL_DESLIGADO,
    )
    if canal is not None:
        consulta = consulta.where(AtendimentoCanal.canal == canal)
    return consulta.exists()



async def _pegar_trava() -> tuple[bool, str | None]:
    token = uuid4().hex
    try:
        pegou = await redis.set(CHAVE_TRAVA, token, nx=True, ex=TRAVA_TTL_S)
    except Exception as exc:  # noqa: BLE001
        # Sem Redis, roda sem trava: o índice é idempotente (upsert).
        logger.warning("atendimento_indexar_trava_indisponivel", err=type(exc).__name__)
        return True, None
    return bool(pegou), token


async def _soltar_trava(token: str | None) -> None:
    if token is None:
        return
    try:
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            CHAVE_TRAVA,
            token,
        )
    except Exception:  # noqa: BLE001 — o TTL solta sozinho
        logger.warning("atendimento_indexar_trava_soltar_falhou")


async def indexar_pedidos(*, fabrica_cliente: FabricaCliente | None = None) -> dict:
    """A rodada inteira: todas as lojas da Shopee não arquivadas. Devolve o resumo.

    Sai na hora com `atendimento_leitura_ativa` desligado (`{"ligado": False}`)
    ou com outra rodada em andamento (`{"pulado": True}`). A loja com o canal
    `desligado` não entra (nenhuma chamada). Nunca levanta por causa de uma
    loja.
    """
    if not get_settings().atendimento_leitura_ativa:
        return {"ligado": False}
    pegou, token = await _pegar_trava()
    if not pegou:
        logger.info("atendimento_indexar_ocupado")
        return {"pulado": True}
    try:
        async with _db.SessionLocal() as session:
            ids = list(
                (
                    await session.execute(
                        select(Integration.id).where(
                            Integration.platform == IntegrationPlatform(PLATAFORMA),
                            Integration.archived_at.is_(None),
                            ~canal_desligado(Integration.id),
                        )
                    )
                )
                .scalars()
                .all()
            )
        resumo: dict[str, Any] = {"lojas": len(ids), "pedidos": 0, "avaliacoes": 0, "erros": 0}
        for integration_id in ids:
            r = await indexar_loja(integration_id, fabrica_cliente=fabrica_cliente)
            resumo["pedidos"] += r["pedidos"]
            resumo["avaliacoes"] += r["avaliacoes"]
            resumo["erros"] += r["erros"]
        return resumo
    finally:
        await _soltar_trava(token)
