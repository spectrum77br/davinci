"""Compensação de estoque dos KITS trocados pelo robô de prioridade.

Eduardo (14/09): "ele está mudando mas continua tirando estoque do saldo de
ra ao invés de tirar do f105 de sp". Comprovado no extrato de estoque do
Bling (webhooks → stock_movements): para PRODUTO SIMPLES o Bling baixa o SKU
novo na NF; para KIT ele continua baixando a composição ANTIGA (dia 13: 18
trocas de kit dg053 → 30 baixas em dg053.ci e 6 em .sp). Então, ao trocar um
kit, o robô lança ele mesmo (POST /estoques — o mesmo mecanismo da correção
de estoque das Devoluções): ENTRADA em cada componente antigo (anula a baixa
que o Bling vai fazer neles) e SAÍDA em cada componente novo.

Regras (revisões de 14/09):
  * Um plano por PEDIDO (todas as trocas de kit somadas por componente) —
    dois kits do mesmo pedido que compartilham componente somam quantidade.
  * Cada lançamento é uma linha em `prioridade_estoque_movimentos`, gravada
    em TRANSAÇÃO PRÓPRIA antes do POST — o lançamento no Bling é um fato
    externo e não pode sumir num rollback do caller (o gancho manual da NF
    dá 422 depois do robô; o sweep automático roda numa transação só).
    Índice único parcial (pedido, sku, operação) com revertido_at nulo +
    INSERT ... ON CONFLICT DO NOTHING = trava contra lançar duas vezes
    (ganchos da NF e sweep podem passar pelo mesmo pedido).
  * Entradas primeiro, em ordem. A partir do primeiro problema, as linhas
    seguintes NÃO são lançadas (ficam `falhou` na fila) — nunca deixa o
    componente novo baixado sem a entrada no antigo.
  * `falhou` = o Bling RECUSOU (4xx, 429/Cloudflare) → o sweep retenta em
    ordem, até MAX_TENTATIVAS, só enquanto as linhas anteriores do pedido
    estiverem `ok`. `incerto` = não dá pra saber se entrou: transporte/
    timeout OU 5xx (caso real 15/09: HTTP 504 do gateway e o Bling tinha
    processado — retentar teria baixado 2x). `incerto` nunca é relançado
    às cegas: o sweep concilia pelo EXTRATO do Bling (webhook →
    stock_movements): se no intervalo da tentativa há mais movimentos
    daquele produto/operação/quantidade do que linhas `ok` nossas, o
    lançamento entrou → vira `ok`; senão fica `incerto` e avisa. `pendente`
    órfão (processo interrompido entre gravar e lançar) vira `incerto` após
    60 min. Aviso Threema uma vez por linha (grupo nf_sem_estoque), só se
    alguém recebeu.
  * Estorno: pedido em Cancelado (12) ou excluído no Bling que NÃO saiu
    (sem 21→15 na trilha, sem rastreio na Logística) → lança o oposto de
    cada linha `ok`. Carimba `revertido_at` ANTES do POST; se o Bling
    recusou, limpa o carimbo (retenta depois); se foi transporte/timeout,
    mantém e marca `incerto` (melhor não estornar do que estornar duas
    vezes). Pedido que saiu e voltou é assunto das Devoluções.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.models import (
    BlingOrder,
    Logistica,
    MargemAudit,
    PrioridadeEstoqueMovimento,
    StockMovement,
)
from app.services import nf_emissao_gerar, threema
from app.services.bling_situacoes import SITUACAO_CANCELADO
from app.services.marketplaces.bling import BlingCloudflareError

logger = structlog.get_logger()

MAX_TENTATIVAS = 5
ORFAO_MINUTOS = 60
AVISO_LOTE = 20
# Pedido "morto" pro estorno: cancelado no Bling ou excluído (espelho vira
# 'excluido' pelo webhook pedido.exclusao — services/bling_orders.py).
_SITUACOES_MORTAS = (str(SITUACAO_CANCELADO), "excluido")
_SITUACAO_EM_ANDAMENTO = "15"


def pecas_que_mudam(antigo: str, alvo: str) -> tuple[Counter, Counter]:
    """(componentes só do kit antigo, componentes só do kit novo), com
    multiplicidade. Pedaço igual nos dois (sem tag, ex.: brinde) se anula."""
    a = Counter(p.strip().lower() for p in antigo.split("+") if p.strip())
    n = Counter(p.strip().lower() for p in alvo.split("+") if p.strip())
    return a - n, n - a


def plano_do_pedido(trocas: list[tuple[str, str, int]]) -> list[tuple[str, str, int]]:
    """[(operacao, sku, quantidade)] somando todas as trocas de kit do pedido:
    entradas (componentes antigos) primeiro, depois saídas (novos)."""
    entradas: Counter = Counter()
    saidas: Counter = Counter()
    for antigo, alvo, qtd in trocas:
        saem, entram = pecas_que_mudam(antigo, alvo)
        for sku, mult in saem.items():
            entradas[sku] += qtd * mult
        for sku, mult in entram.items():
            saidas[sku] += qtd * mult
    plano = [("E", sku, q) for sku, q in sorted(entradas.items())]
    plano += [("S", sku, q) for sku, q in sorted(saidas.items())]
    return plano


def _transporte(exc: Exception) -> bool:
    """Erro em que NÃO dá pra saber se o Bling processou o POST: rede/timeout
    ou 5xx do gateway (caso real 15/09: 504 com o Bling tendo processado)."""
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


async def _produto_id(client, sku: str, cache: dict) -> int | None:
    """id do produto no Bling pelo SKU. Só cacheia SUCESSO: 'não achou' pode
    ser erro transitório do Bling (find_active_product_by_sku engole exceção
    e devolve None) e não pode envenenar o tick inteiro."""
    chave = f"prod:{sku}"
    if chave in cache:
        return cache[chave]
    prod = await client.find_active_product_by_sku(sku)
    ok = prod and prod.get("id") and (prod.get("sku") or "").strip().lower() == sku
    if not ok:
        return None
    cache[chave] = int(prod["id"])
    return cache[chave]


async def _gravar(mov_id: UUID, **campos) -> None:
    """Atualiza a linha em transação própria (sobrevive ao caller)."""
    async with session_scope() as s:
        m = await s.get(PrioridadeEstoqueMovimento, mov_id)
        if m is None:
            return
        for k, v in campos.items():
            setattr(m, k, v)


async def _carregar(mov_id: UUID) -> PrioridadeEstoqueMovimento | None:
    """Cópia DESTACADA da linha (sessão própria já fechada): nada fica sujo
    na sessão longa do caller/sweep — evita autoflush segurando lock da
    linha que o `_gravar` seguinte precisa."""
    async with session_scope() as s:
        m = await s.get(PrioridadeEstoqueMovimento, mov_id)
        if m is not None:
            s.expunge(m)
        return m


async def _lancar(client, m: PrioridadeEstoqueMovimento) -> str:
    """Faz o POST de UMA linha (cópia destacada) e grava o resultado.
    Devolve o status novo: ok | falhou | incerto."""
    agora = datetime.now(UTC)
    try:
        await client.update_stock_by_id(
            int(m.bling_product_id), int(m.quantidade),
            operation=m.operacao, observacao=m.observacao,
        )
    except Exception as exc:  # noqa: BLE001 — classificado abaixo
        if _transporte(exc):
            # Pode ter entrado (504 do gateway com o Bling processando — 15/09).
            erro = (
                f"HTTP {exc.response.status_code}: {(exc.response.text or '')[:200]}"
                if isinstance(exc, httpx.HTTPStatusError)
                else str(exc)[:300]
            )
            await _gravar(m.id, status="incerto", tentativas=m.tentativas + 1, erro=erro)
            logger.warning(
                "prioridade_estoque_movimento_incerto", pedido=m.pedido_bling, sku=m.sku,
                operacao=m.operacao, erro=str(exc)[:200],
            )
            return "incerto"
        # Bling recusou (4xx, 429/Cloudflare, token em cooldown): não entrou → retenta.
        if isinstance(exc, httpx.HTTPStatusError):
            erro = f"HTTP {exc.response.status_code}: {(exc.response.text or '')[:200]}"
        else:
            erro = f"{type(exc).__name__}: {str(exc)[:250]}"
        await _gravar(m.id, status="falhou", tentativas=m.tentativas + 1, erro=erro)
        logger.warning(
            "prioridade_estoque_movimento_falhou", pedido=m.pedido_bling, sku=m.sku,
            operacao=m.operacao, erro=erro[:200],
            cloudflare=isinstance(exc, BlingCloudflareError),
        )
        return "falhou"
    await _gravar(m.id, status="ok", lancado_at=agora, erro=None, tentativas=m.tentativas + 1)
    logger.info(
        "prioridade_estoque_movimento", pedido=m.pedido_bling, sku=m.sku,
        operacao=m.operacao, qtd=m.quantidade,
    )
    return "ok"


async def compensar_estoque_kits(
    client, *, numero: str, bling_id: int, trocas: list[tuple[str, str, int]], cache: dict,
) -> dict:
    """Kits trocados no pedido `numero` (lista de (antigo, alvo, qtd)): grava o
    plano somado e lança em ordem. Nunca levanta. Devolve {"ok", "falhas"}."""
    plano = plano_do_pedido(trocas)
    res = {"ok": 0, "falhas": 0}
    if not plano:
        return res
    obs = "Prioridade de estoque · pedido {n}: {t}".format(
        n=numero, t="; ".join(f"{a} -> {b}" for a, b, _ in trocas)
    )

    # 1) Grava as linhas (pendente) em transação própria, antes de qualquer
    #    POST. ON CONFLICT DO NOTHING = já em fila por outra passada.
    ids: list[tuple[UUID, str]] = []
    try:
        async with session_scope() as s:
            for ordem, (operacao, sku, quantidade) in enumerate(plano):
                pid = await _produto_id(client, sku, cache)
                stmt = (
                    pg_insert(PrioridadeEstoqueMovimento)
                    .values(
                        pedido_bling=numero, bling_id=bling_id, sku=sku,
                        bling_product_id=pid, operacao=operacao, quantidade=quantidade,
                        ordem=ordem, observacao=obs,
                        status="pendente" if pid else "falhou",
                        erro=None if pid else "produto não encontrado no Bling",
                    )
                    .on_conflict_do_nothing(index_elements=["pedido_bling", "sku", "operacao"],
                                            index_where=text("revertido_at IS NULL"))
                    .returning(PrioridadeEstoqueMovimento.id, PrioridadeEstoqueMovimento.status)
                )
                row = (await s.execute(stmt)).first()
                if row is not None:
                    ids.append((row[0], row[1]))
    except Exception as exc:  # noqa: BLE001 — sem registro não se lança nada
        logger.warning(
            "prioridade_estoque_movimento_registro_falhou", pedido=numero, erro=str(exc)[:200]
        )
        return res

    # 2) Lança em ordem; a partir do primeiro problema, o resto fica na fila.
    parou = False
    for mov_id, st in ids:
        if parou or st != "pendente":
            if st == "pendente":
                await _gravar(mov_id, status="falhou", erro="aguardando lançamento anterior")
            res["falhas"] += 1
            parou = True
            continue
        m = await _carregar(mov_id)
        if m is None:
            continue
        if await _lancar(client, m) == "ok":
            res["ok"] += 1
        else:
            res["falhas"] += 1
            parou = True
    return res


# ---------------------------------------------------------------- sweep


async def _pedido_saiu(session: AsyncSession, numero: str) -> bool:
    """True se há evidência de que o pacote saiu: 21→15 confirmado pela
    plataforma (margem_audit do robô de envio ou auditoria do banco) ou
    rastreio andando na Logística. Sem evidência = não saiu."""
    if (await session.execute(
        select(MargemAudit.id).where(
            MargemAudit.pedido_bling == numero,
            MargemAudit.acao == "situacao",
            MargemAudit.valor_novo == _SITUACAO_EM_ANDAMENTO,
        ).limit(1)
    )).first():
        return True
    if (await session.execute(
        select(Logistica.id).where(
            Logistica.pedido_bling == numero,
            or_(Logistica.localizacao_at.is_not(None), Logistica.rastreio_17track.is_not(None)),
        ).limit(1)
    )).first():
        return True
    try:
        async with session.begin_nested():
            hit = (await session.execute(
                text(
                    f"SELECT 1 FROM {get_settings().database_schema}.audit_em_andamento_data "  # noqa: S608
                    "WHERE numero = :n AND new_situacao = :s LIMIT 1"
                ),
                {"n": numero, "s": _SITUACAO_EM_ANDAMENTO},
            )).first()
    except Exception:  # noqa: BLE001 — tabela pode não existir (testes)
        return False
    return hit is not None


async def _situacao(session: AsyncSession, numero: str) -> str | None:
    return (await session.execute(
        select(BlingOrder.situacao).where(BlingOrder.numero == numero).limit(1)
    )).scalar_one_or_none()


async def retentar_falhas(client, session: AsyncSession) -> dict:
    """Retenta, em ordem, as linhas `falhou` de pedidos vivos — só quando
    todas as linhas anteriores do pedido já estão `ok`."""
    resumo = {"retentados": 0, "falhas": 0}
    linhas = (await session.execute(
        select(
            PrioridadeEstoqueMovimento.id, PrioridadeEstoqueMovimento.pedido_bling,
            PrioridadeEstoqueMovimento.ordem, PrioridadeEstoqueMovimento.status,
            PrioridadeEstoqueMovimento.tentativas,
        )
        .where(PrioridadeEstoqueMovimento.revertido_at.is_(None))
        .where(PrioridadeEstoqueMovimento.pedido_bling.in_(
            select(PrioridadeEstoqueMovimento.pedido_bling).where(
                PrioridadeEstoqueMovimento.status == "falhou",
                PrioridadeEstoqueMovimento.revertido_at.is_(None),
            )
        ))
        .order_by(PrioridadeEstoqueMovimento.pedido_bling, PrioridadeEstoqueMovimento.ordem)
    )).all()
    por_pedido: dict[str, list] = {}
    for r in linhas:
        por_pedido.setdefault(r.pedido_bling, []).append(r)
    cache: dict = {}
    for numero, rows in por_pedido.items():
        if (await _situacao(session, numero)) in _SITUACOES_MORTAS:
            continue  # o estorno cuida (ou não há o que estornar)
        for r in rows:
            if r.status == "ok":
                continue
            if r.status != "falhou" or r.tentativas >= MAX_TENTATIVAS:
                break  # incerto/pendente/esgotado na frente: não lança S sem o E
            m = await _carregar(r.id)
            if m is None:
                break
            if m.bling_product_id is None:
                pid = await _produto_id(client, m.sku, cache)
                if pid is None:
                    await _gravar(m.id, tentativas=m.tentativas + 1)
                    resumo["falhas"] += 1
                    break
                await _gravar(m.id, bling_product_id=pid)
                m.bling_product_id = pid  # cópia destacada: sem efeito na sessão do sweep
            if await _lancar(client, m) == "ok":
                resumo["retentados"] += 1
            else:
                resumo["falhas"] += 1
                break
    return resumo


async def estornar_cancelados(client, session: AsyncSession) -> dict:
    """Pedido morto (cancelado/excluído) que nunca saiu: lança o oposto de
    cada linha `ok`. Carimba `revertido_at` antes do POST."""
    resumo = {"estornados": 0, "falhas": 0}
    linhas = (await session.execute(
        select(PrioridadeEstoqueMovimento.id, PrioridadeEstoqueMovimento.pedido_bling)
        .where(
            PrioridadeEstoqueMovimento.status == "ok",
            PrioridadeEstoqueMovimento.revertido_at.is_(None),
            PrioridadeEstoqueMovimento.pedido_bling.in_(
                select(BlingOrder.numero).where(BlingOrder.situacao.in_(_SITUACOES_MORTAS))
            ),
        )
        .order_by(PrioridadeEstoqueMovimento.pedido_bling, PrioridadeEstoqueMovimento.ordem)
    )).all()
    saiu_cache: dict[str, bool] = {}
    for mov_id, numero in linhas:
        if numero not in saiu_cache:
            saiu_cache[numero] = await _pedido_saiu(session, numero)
        if saiu_cache[numero]:
            continue  # saiu e voltou: assunto das Devoluções
        m = await _carregar(mov_id)
        if m is None or m.revertido_at is not None:
            continue
        oposta = "S" if m.operacao == "E" else "E"
        await _gravar(m.id, revertido_at=datetime.now(UTC))  # antes do POST: nunca 2x
        try:
            await client.update_stock_by_id(
                int(m.bling_product_id), int(m.quantidade), operation=oposta,
                observacao=f"Estorno prioridade de estoque · pedido {numero} cancelado",
            )
        except Exception as exc:  # noqa: BLE001
            resumo["falhas"] += 1
            if _transporte(exc):
                # Pode ter entrado: mantém o carimbo e pede conferência.
                await _gravar(m.id, status="incerto", erro=f"estorno sem confirmação: {exc}"[:300])
            else:
                await _gravar(m.id, revertido_at=None, erro=f"estorno recusado: {exc}"[:300])
            logger.warning(
                "prioridade_estoque_estorno_falhou", pedido=numero, sku=m.sku,
                operacao=oposta, erro=str(exc)[:200],
            )
            continue
        resumo["estornados"] += 1
        logger.info("prioridade_estoque_estornado", pedido=numero, sku=m.sku, operacao=oposta)
    return resumo


JANELA_EXTRATO_ANTES = timedelta(minutes=3)
JANELA_EXTRATO_DEPOIS = timedelta(minutes=20)


async def resolver_incertos_pelo_extrato(session: AsyncSession) -> int:
    """Linha `incerto` (sem lancado_at): olha o extrato do Bling (webhook →
    stock_movements) na janela da tentativa. Movimentos do mesmo produto/
    operação/quantidade que EXCEDEM as linhas `ok` nossas na mesma janela =
    o lançamento incerto entrou → `ok`. Só resolve a favor com evidência;
    na dúvida fica `incerto` (aviso). Devolve quantos resolveu."""
    incertos = (await session.execute(
        select(PrioridadeEstoqueMovimento).where(
            PrioridadeEstoqueMovimento.status == "incerto",
            PrioridadeEstoqueMovimento.lancado_at.is_(None),
            PrioridadeEstoqueMovimento.revertido_at.is_(None),
            PrioridadeEstoqueMovimento.bling_product_id.is_not(None),
        ).order_by(PrioridadeEstoqueMovimento.updated_at)
    )).scalars().all()
    resolvidos = 0
    for m in incertos:
        ini = m.updated_at - JANELA_EXTRATO_ANTES
        fim = m.updated_at + JANELA_EXTRATO_DEPOIS
        movs = (await session.execute(
            select(StockMovement.date).where(
                StockMovement.bling_product_id == int(m.bling_product_id),
                StockMovement.tipo == m.operacao,
                StockMovement.quantidade == int(m.quantidade),
                StockMovement.date >= ini,
                StockMovement.date <= fim,
            ).order_by(StockMovement.date)
        )).scalars().all()
        if not movs:
            continue
        ok_na_janela = (await session.execute(
            select(PrioridadeEstoqueMovimento.id).where(
                PrioridadeEstoqueMovimento.bling_product_id == int(m.bling_product_id),
                PrioridadeEstoqueMovimento.operacao == m.operacao,
                PrioridadeEstoqueMovimento.quantidade == int(m.quantidade),
                PrioridadeEstoqueMovimento.status == "ok",
                PrioridadeEstoqueMovimento.lancado_at >= ini,
                PrioridadeEstoqueMovimento.lancado_at <= fim,
            )
        )).scalars().all()
        if len(movs) <= len(ok_na_janela):
            continue  # todos os movimentos já têm dono
        await _gravar(
            m.id, status="ok", lancado_at=movs[-1],
            erro=(
                f"conciliado pelo extrato do Bling ({movs[-1]:%d/%m %H:%M}); antes: {m.erro}"
            )[:300],
        )
        resolvidos += 1
        logger.info(
            "prioridade_estoque_incerto_conciliado", pedido=m.pedido_bling, sku=m.sku,
            operacao=m.operacao,
        )
    return resolvidos


async def marcar_pendentes_orfaos(session: AsyncSession, *, minutos: int = ORFAO_MINUTOS) -> int:
    """`pendente` esquecido (processo interrompido entre gravar e lançar)
    vira `incerto` — nunca é relançado sozinho; entra no aviso."""
    corte = datetime.now(UTC) - timedelta(minutes=minutos)
    ids = (await session.execute(
        select(PrioridadeEstoqueMovimento.id).where(
            PrioridadeEstoqueMovimento.status == "pendente",
            PrioridadeEstoqueMovimento.created_at < corte,
        )
    )).scalars().all()
    for mov_id in ids:
        await _gravar(
            mov_id, status="incerto", erro="pendente sem lançamento (processo interrompido)"
        )
    return len(ids)


async def avisar_falhas(session: AsyncSession) -> int:
    """Threema (grupo nf_sem_estoque) com as linhas `incerto` e as `falhou`
    esgotadas, em lotes, carimbando só o que alguém recebeu. Best-effort."""
    linhas = (await session.execute(
        select(
            PrioridadeEstoqueMovimento.id, PrioridadeEstoqueMovimento.pedido_bling,
            PrioridadeEstoqueMovimento.sku, PrioridadeEstoqueMovimento.operacao,
            PrioridadeEstoqueMovimento.quantidade, PrioridadeEstoqueMovimento.status,
            PrioridadeEstoqueMovimento.erro,
        ).where(
            PrioridadeEstoqueMovimento.avisado_at.is_(None),
            or_(
                PrioridadeEstoqueMovimento.status == "incerto",
                (PrioridadeEstoqueMovimento.status == "falhou")
                & (PrioridadeEstoqueMovimento.tentativas >= MAX_TENTATIVAS),
            ),
        ).order_by(PrioridadeEstoqueMovimento.created_at, PrioridadeEstoqueMovimento.ordem)
    )).all()
    if not linhas:
        return 0
    destinos = threema.parse_recipients(get_settings().nf_sem_estoque_threema_recipients)
    if not destinos:
        return 0
    avisados = 0
    for i in range(0, len(linhas), AVISO_LOTE):
        lote = linhas[i : i + AVISO_LOTE]
        cabecalho = "Prioridade de estoque — lançamento de kit precisa de conferência no Bling:\n"
        texto = cabecalho + "\n".join(
            f"Pedido {r.pedido_bling}: {r.operacao} {r.quantidade}× {r.sku} — "
            + ("não entrou no Bling" if r.status == "falhou" else "sem confirmação (conferir)")
            + (f" · {r.erro}" if r.erro else "")
            for r in lote
        )
        try:
            res = await threema.ThreemaClient().send_to_all(texto, destinos)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prioridade_estoque_threema_falhou", erro=str(exc)[:200])
            return avisados
        if not res.get("sent"):
            logger.warning("prioridade_estoque_threema_ninguem_recebeu", failed=res.get("failed"))
            return avisados
        agora = datetime.now(UTC)
        for r in lote:
            await _gravar(r.id, avisado_at=agora)
        avisados += len(lote)
    return avisados


async def manutencao_movimentos_sweep() -> dict:
    """Cron do worker (:16/:46): órfãos → retenta falhas → estorna cancelados → avisa."""
    resumo: dict = {
        "orfaos": 0, "conciliados": 0, "retentados": 0, "falhas": 0, "estornados": 0, "avisados": 0,
    }
    async with session_scope() as s:
        resumo["orfaos"] = await marcar_pendentes_orfaos(s)
        resumo["conciliados"] = await resolver_incertos_pelo_extrato(s)
        client = await nf_emissao_gerar._bling_client_opt(s)
        if client is None:
            logger.warning("prioridade_estoque_manutencao_sem_bling")
            resumo["avisados"] = await avisar_falhas(s)
            return resumo
        r1 = await retentar_falhas(client, s)
        r2 = await estornar_cancelados(client, s)
        resumo.update(
            retentados=r1["retentados"], falhas=r1["falhas"] + r2["falhas"],
            estornados=r2["estornados"],
        )
        resumo["avisados"] = await avisar_falhas(s)
    return resumo
