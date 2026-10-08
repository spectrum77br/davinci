"""TROCA DE LOTE AUTOMÁTICA no pedido em "Aguardando Cancelamento" (item 4, fase 4c, 08/10/2026).

Decisão (a) do Eduardo (07/10/2026): o pedido caiu em 83955 por falta de
estoque e o MESMO produto tem peça em outro lote (o nível 0 da 4b: dg053.ci →
dg053.sp) — o robô troca sozinho, sem aceite do cliente, como o robô de lote,
e o pedido volta 83955 → 9 → 6. Este módulo só ESCOLHE os pedidos e chama
`troca.executar(..., automatica=True)`: os passos, as travas (do banco e ao
vivo) e o estado de cada troca são os do clique (`services/atendimento/troca.py`).
A Margem não é aprovada (não há pessoa): o pino fica como estava.

AS CHAVES: só roda com `atendimento_troca_ativa` E `atendimento_troca_lote_auto`
(as duas nascem desligadas) E a lista piloto (`atendimento_troca_pedidos`)
ESCRITA — decisão (e) do dono: a primeira troca é acompanhada, e ligar as
duas chaves esquecendo a lista não pode soltar o robô em todos os pedidos
de uma vez (vazia, o cron só avisa no log; "*" = todos, dito com todas as
letras). Desligado não lê nem o banco — nenhum GET no Bling nem na Shopee.
Desligar no meio da rodada para antes do pedido seguinte.

A ESCOLHA (`pedidos_da_rodada`, só banco): os pedidos em 83955 dos últimos
`JANELA` (`etiqueta_fatos.pedidos_em_83955`, as mesmas funções da lista Ag.
cancelamento); o motivo (`ag_cancelamento.classificar`) `sem_estoque` com UM
item em falta (dois em falta: uma troca não resolve); e a sugestão de nível 0
COM estoque no DaVinci (`troca_sugestoes.candidatos`, `motivo_fora` None) — a
primeira na ordem da 4b, a mesma que a tela mostra. O estoque do DaVinci só
escolhe: quem confere o saldo é o `executar`, no Bling ao vivo. Só a loja
Shopee pelo cadastro de Lojas (a troca v1 só confere a Shopee ao vivo: o
pedido de outra plataforma tomaria uma vaga da rodada e uma leitura do
catálogo só para ouvir `plataforma_sem_conferencia`). O prazo de envio mais
curto vai primeiro. O nível 1 (inclusive o mesmo produto num lote
que o robô de lote não liberaria — `troca_sugestoes._lote_livre`) e o 2 nunca
entram: são da pessoa, com a caixinha do aceite.

UMA TROCA POR PEDIDO (idempotente — `_bloqueio`):
  • troca de PESSOA no pedido (qualquer estado), troca do robô aberta (parada
    no meio: a rodada a RETOMA — abaixo —, nunca começa outra), concluída ou
    abortada por um motivo que não passa sozinho → o robô não troca mais;
  • a do robô abortada por indisponibilidade (`TRANSITORIOS`: nada foi
    escrito) tenta de novo depois de `ESPERA_TRANSITORIA`, até
    `MAX_TENTATIVAS` vezes;
  • a recusa ANTES da linha (`TrocaRecusada` sem `troca_id`: as travas do
    banco — plataforma, prazo, NF na fila…) e o erro inesperado ficam na
    memória do processo por `ESPERA_RECUSA`: o `executar` relê o catálogo
    inteiro a cada chamada, e a mesma recusa não precisa voltar a cada 10 min.
  Na rodada, um pedido uma vez só; no máximo `MAX_POR_RODADA` chamadas, e
  nenhuma começa depois de `ORCAMENTO_S` (o arq não mata o job no meio de uma
  troca: o `timeout` do cron é maior).

A TROCA DO ROBÔ PARADA NO MEIO (o PATCH 6 que levou 429 deixa o pedido em
Atendido (9) — o limbo que nenhum robô pega —, o PUT sem resposta fica
`incerta`): cada rodada, ANTES dos pedidos novos, chama `troca.retomar`
(GET primeiro, só para frente, NUNCA PUT) nas trocas do robô sem ninguém
conduzindo, até `MAX_RETOMADAS` retomadas por troca (contando as de
pessoa). Esgotadas, ficam para a pessoa: a lista "Trocas paradas no meio"
da tela (GET /trocas?abertas=true) e o painel do pedido mostram o Retomar.

CADA PEDIDO NA SUA SESSÃO (`_trocar`): a troca faz commit a cada passo; a
recusa ou o erro de um pedido vai para o log e a rodada segue. Uma rodada por
vez (trava no Redis, por schema; Redis fora do ar = roda sem a trava: a troca
já segura uma aberta por pedido e o lock do pedido).

REGISTRO NO WORKER: `atendimento_troca_lote` em `cron_jobs` (e em
`functions`, para enfileirar uma rodada à mão), a cada 10 min no :03…
(`worker._ATENDIMENTO_TROCA_LOTE_MINUTOS`), `timeout=540` = a trava da rodada.

O log leva só números de pedido, SKUs, códigos e contagens — nada do comprador.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import AtendimentoTroca, BlingOrder, StoreInfo
from app.redis_client import redis
from app.services.atendimento import etiqueta_fatos, troca, troca_sugestoes
from app.services.atendimento.ag_cancelamento import SEM_ESTOQUE, classificar

logger = structlog.get_logger()

# Os pedidos em 83955 desta janela (pela `data` do pedido): depois disso o
# prazo de envio da Shopee já venceu há muito (a trava `prazo_vencido` recusa).
JANELA = timedelta(days=30)
# Chamadas ao `executar` por rodada (cada uma: umas 8 chamadas ao Bling e 1 à Shopee).
MAX_POR_RODADA = 10
# Nenhuma troca começa depois disto (s): a troca em curso termina antes do
# `timeout` do cron (540 s) — o arq nunca a mata no meio.
ORCAMENTO_S = 360.0
# Trava da rodada (por schema) == o `timeout` do cron: o job morto pelo arq
# não deixa a trava viva por cima da próxima rodada.
RODADA_TTL_S = 540
_CHAVE_RODADA = "atendimento:troca_lote:rodada:{}"

# A troca do robô abortada sem escrever nada no Bling, por indisponibilidade:
# tenta de novo depois da espera, até o teto (contando a primeira).
TRANSITORIOS = frozenset({"bling_indisponivel", "plataforma_indisponivel", "peca_em_disputa"})
ESPERA_TRANSITORIA = timedelta(minutes=30)
MAX_TENTATIVAS = 3
# A recusa antes da linha (ou o erro inesperado): o pedido descansa isto.
ESPERA_RECUSA = timedelta(hours=1)
# As recusas das chaves: desligaram no meio da rodada — para tudo, sem lembrar.
_CODIGOS_DAS_CHAVES = frozenset(
    {
        "troca_desligada",
        "troca_lote_auto_desligada",
        "pedido_fora_do_piloto",
        "piloto_vazio_no_automatico",
    }
)
# A troca do robô parada no meio: quantas vezes a rodada a retoma (contando
# as retomadas de pessoa) antes de deixá-la para a pessoa, e quantas por rodada.
MAX_RETOMADAS = 3
MAX_RETOMADAS_POR_RODADA = 5
ERRO_INTERNO = "erro_interno"

# ── Por que o pedido ficou fora da rodada (`puladas`) ─────────────────────
FORA_DO_PILOTO = "fora_do_piloto"
MAIS_DE_UM_ITEM = "mais_de_um_item"
TROCA_DE_PESSOA = "troca_de_pessoa"
TROCA_ABERTA = "troca_aberta"
JA_TROCADO = "ja_trocado"
ABORTADA = "abortada"
TENTATIVAS_ESGOTADAS = "tentativas_esgotadas"
ESPERANDO = "esperando"
RECUSA_RECENTE = "recusa_recente"
SEM_LOTE_COM_ESTOQUE = "sem_lote_com_estoque"
FORA_DA_SHOPEE = "fora_da_shopee"

# ── O que houve com cada chamada (as chaves do resumo) ────────────────────
CONCLUIDAS = "concluidas"
PARADAS = "paradas"
RECUSADAS = "recusadas"
FALHAS = "falhas"
ABORTADAS = "abortadas"

# {nº do Bling: (até quando descansa, o código)} — a memória do processo.
_RECUSAS: dict[str, tuple[datetime, str]] = {}


@dataclass(frozen=True)
class Candidato:
    """Um pedido da rodada: o item em falta e o mesmo produto em outro lote."""

    numero: str
    sku_antigo: str
    sku_novo: str
    quantidade: int
    prazo: datetime | None


def _agora() -> datetime:
    return datetime.now(UTC)


def _chaves_ligadas() -> bool:
    s = get_settings()
    return bool(s.atendimento_troca_ativa and s.atendimento_troca_lote_auto)


def ligado() -> bool:
    """As duas chaves (a da troca e a do robô) E a lista piloto escrita ("*" = todos).

    Desligado, nada aqui toca o banco.
    """
    return _chaves_ligadas() and troca.piloto_preenchido()


# ── A memória das recusas ─────────────────────────────────────────────────


def limpar_memoria() -> None:
    """Esquece as recusas guardadas (testes; o processo novo começa vazio)."""
    _RECUSAS.clear()


def _lembrar_recusa(numero: str, code: str, agora: datetime) -> None:
    _RECUSAS[numero] = (agora + ESPERA_RECUSA, code)


def _recusa_lembrada(numero: str, agora: datetime) -> str | None:
    """O código da recusa recente do pedido; a vencida é esquecida aqui."""
    guardada = _RECUSAS.get(numero)
    if guardada is None:
        return None
    ate, code = guardada
    if ate <= agora:
        _RECUSAS.pop(numero, None)
        return None
    return code


# ── Uma troca por pedido ──────────────────────────────────────────────────


def _bloqueio(trocas: Sequence[Any], agora: datetime) -> str | None:
    """Por que o robô não troca este pedido de novo (None = pode tentar). PURA.

    `trocas` = as linhas de `atendimento_trocas` do pedido (estado,
    automatica, codigo_erro, created_at).
    """
    if not trocas:
        return None
    if any(not t.automatica for t in trocas):
        return TROCA_DE_PESSOA
    if any(t.estado not in troca.FECHADOS for t in trocas):
        return TROCA_ABERTA
    if any(t.estado == troca.CONCLUIDA for t in trocas):
        return JA_TROCADO
    # Daqui para baixo, todas são do robô e abortadas.
    if any(t.codigo_erro not in TRANSITORIOS for t in trocas):
        return ABORTADA
    if len(trocas) >= MAX_TENTATIVAS:
        return TENTATIVAS_ESGOTADAS
    ultima = max((t.created_at for t in trocas if t.created_at is not None), default=None)
    if ultima is not None and ultima > agora - ESPERA_TRANSITORIA:
        return ESPERANDO
    return None


async def _bloqueados(
    session: AsyncSession, numeros: Sequence[str], agora: datetime
) -> dict[str, str]:
    """{nº do Bling: por que o robô não toca} — uma consulta para a rodada inteira."""
    lista = sorted({n for n in numeros if n})
    if not lista:
        return {}
    linhas = (
        await session.execute(
            select(
                AtendimentoTroca.pedido_bling,
                AtendimentoTroca.estado,
                AtendimentoTroca.automatica,
                AtendimentoTroca.codigo_erro,
                AtendimentoTroca.created_at,
            ).where(AtendimentoTroca.pedido_bling.in_(lista))
        )
    ).all()
    por_pedido: dict[str, list[Any]] = {}
    for r in linhas:
        por_pedido.setdefault(r.pedido_bling, []).append(r)
    saida: dict[str, str] = {}
    for numero, trocas in por_pedido.items():
        motivo = _bloqueio(trocas, agora)
        if motivo is not None:
            saida[numero] = motivo
    return saida


# ── A escolha (só banco) ──────────────────────────────────────────────────


async def _quantidades_e_prazos(
    session: AsyncSession, numeros: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """{nº do Bling: {qtds: {sku sem caixa: quantidade}, prazo}} — uma consulta."""
    lista = sorted({n for n in numeros if n})
    if not lista:
        return {}
    linhas = (
        await session.execute(
            select(
                BlingOrder.numero,
                BlingOrder.item_codigo,
                BlingOrder.item_quantidade,
                BlingOrder.marketplace_ship_deadline,
            ).where(BlingOrder.numero.in_(lista))
        )
    ).all()
    saida: dict[str, dict[str, Any]] = {}
    for r in linhas:
        fatos = saida.setdefault(r.numero, {"qtds": {}, "prazo": None})
        sku = (r.item_codigo or "").strip().lower()
        if sku:
            # O mesmo SKU em duas linhas soma (como `troca._carregar`).
            fatos["qtds"][sku] = fatos["qtds"].get(sku, 0) + int(r.item_quantidade or 1)
        if fatos["prazo"] is None and r.marketplace_ship_deadline is not None:
            fatos["prazo"] = r.marketplace_ship_deadline
    return saida


async def _plataformas(session: AsyncSession, lojas: set[str]) -> dict[str, str | None]:
    """{`bling_orders.loja`: plataforma} pelo cadastro de Lojas — uma consulta."""
    if not lojas:
        return {}
    linhas = (
        await session.execute(
            select(StoreInfo.bling_store_id, StoreInfo.platform).where(
                StoreInfo.bling_store_id.in_(sorted(lojas))
            )
        )
    ).all()
    saida: dict[str, str | None] = {}
    for r in linhas:
        saida.setdefault(str(r.bling_store_id), etiqueta_fatos.normalizar_plataforma(r.platform))
    return saida


def _mais_urgente_primeiro(c: Candidato) -> tuple:
    """O prazo de envio mais curto primeiro; sem prazo no fim; empate pelo número."""
    return (c.prazo is None, c.prazo.timestamp() if c.prazo is not None else 0.0, c.numero)


async def pedidos_da_rodada(
    session: AsyncSession, *, agora: datetime | None = None
) -> tuple[list[Candidato], Counter[str]]:
    """Os pedidos que o robô tenta nesta rodada e quantos ficaram de fora, por quê.

    Só banco — nenhum GET no Bling. O catálogo da troca só é lido se sobrar
    algum pedido depois das chaves, do piloto, do motivo e do bloqueio.
    """
    agora = agora or _agora()
    puladas: Counter[str] = Counter()
    em_falta: dict[str, str] = {}
    pedidos = await etiqueta_fatos.pedidos_em_83955(session, agora - JANELA)
    plataformas = await _plataformas(session, {str(p.loja) for p in pedidos if p.loja})
    for p in pedidos:
        m = classificar(p)
        if m is None or m.codigo != SEM_ESTOQUE or not m.skus:
            continue
        if len(m.skus) != 1:
            puladas[MAIS_DE_UM_ITEM] += 1
            continue
        if not troca.no_piloto(p.numero):
            puladas[FORA_DO_PILOTO] += 1
            continue
        # Sem cadastro da loja, quem decide é a troca (pela conversa).
        plataforma = plataformas.get(str(p.loja)) if p.loja else None
        if plataforma is not None and plataforma != "shopee":
            puladas[FORA_DA_SHOPEE] += 1
            continue
        em_falta[p.numero] = m.skus[0]
    if not em_falta:
        return [], puladas
    bloqueados = await _bloqueados(session, list(em_falta), agora)
    restantes: dict[str, str] = {}
    for numero, sku in em_falta.items():
        motivo = bloqueados.get(numero)
        if motivo is None and _recusa_lembrada(numero, agora) is not None:
            motivo = RECUSA_RECENTE
        if motivo is not None:
            puladas[motivo] += 1
            continue
        restantes[numero] = sku
    if not restantes:
        return [], puladas
    fatos = await _quantidades_e_prazos(session, list(restantes))
    cat = await troca_sugestoes.catalogo(session)
    candidatos: list[Candidato] = []
    for numero, sku in restantes.items():
        f = fatos.get(numero) or {"qtds": {}, "prazo": None}
        qtd = int(f["qtds"].get(sku.lower(), 1))
        # A ordem da 4b: o que não encarece antes, depois o maior estoque.
        lote = next(
            (
                c
                for c in troca_sugestoes.candidatos(cat, sku, qtd)
                if c.nivel == troca_sugestoes.NIVEL_LOTE and c.motivo_fora is None
            ),
            None,
        )
        if lote is None:
            puladas[SEM_LOTE_COM_ESTOQUE] += 1
            continue
        candidatos.append(
            Candidato(
                numero=numero, sku_antigo=sku, sku_novo=lote.sku, quantidade=qtd, prazo=f["prazo"]
            )
        )
    candidatos.sort(key=_mais_urgente_primeiro)
    return candidatos, puladas


# ── A troca do robô parada no meio ────────────────────────────────────────


def _retomadas(passos: Any) -> int:
    """Quantas vezes a troca já foi retomada (cada `retomar` grava um passo "retomar")."""
    return sum(1 for x in (passos or []) if isinstance(x, dict) and x.get("passo") == "retomar")


async def trocas_paradas(session: AsyncSession) -> list[tuple[Any, str, int]]:
    """As trocas do ROBÔ abertas, sem ninguém conduzindo: [(id, nº do Bling, retomadas)]."""
    linhas = (
        await session.execute(
            select(AtendimentoTroca.id, AtendimentoTroca.pedido_bling, AtendimentoTroca.passos)
            .where(
                AtendimentoTroca.automatica.is_(True),
                AtendimentoTroca.estado.not_in(tuple(troca.FECHADOS)),
                or_(
                    AtendimentoTroca.em_execucao_ate.is_(None),
                    AtendimentoTroca.em_execucao_ate < func.now(),
                ),
            )
            .order_by(AtendimentoTroca.created_at)
        )
    ).all()
    return [(r.id, r.pedido_bling, _retomadas(r.passos)) for r in linhas]


async def _retomar(troca_id: Any, numero: str) -> tuple[str, str | None]:
    """Uma retomada, na sessão dela → (o que houve, o código). Nunca levanta."""
    async with _db.SessionLocal() as session:
        try:
            t = await troca.retomar(session, None, troca_id)
        except troca.TrocaRecusada as e:
            logger.info(
                "atendimento_troca_lote_retomada_recusada",
                pedido=numero,
                troca=str(troca_id),
                code=e.code,
            )
            return RECUSADAS, e.code
        except Exception as e:  # noqa: BLE001 — uma troca nunca para a rodada
            logger.warning(
                "atendimento_troca_lote_retomada_falhou",
                pedido=numero,
                troca=str(troca_id),
                err=type(e).__name__,
            )
            return FALHAS, None
        if t.estado == troca.CONCLUIDA:
            logger.info("atendimento_troca_lote_retomou", pedido=numero, troca=str(troca_id))
            return CONCLUIDAS, None
        # Abortada (alguém mexeu no pedido: a nota já avisa) ou parada de novo.
        logger.warning(
            "atendimento_troca_lote_retomada_parou",
            pedido=numero,
            troca=str(troca_id),
            estado=t.estado,
            code=t.codigo_erro,
        )
        return (ABORTADAS if t.estado == troca.ABORTADA else PARADAS), t.codigo_erro


async def _retomar_paradas(inicio: float, orcamento_s: float) -> dict[str, int]:
    """As trocas do robô paradas no meio: retoma (até o teto) antes dos pedidos novos."""
    async with _db.SessionLocal() as session:
        paradas = await trocas_paradas(session)
        await session.rollback()
    resumo = {"tentadas": 0, CONCLUIDAS: 0, PARADAS: 0, ABORTADAS: 0, FALHAS: 0, RECUSADAS: 0}
    resumo["esgotadas"] = 0
    for troca_id, numero, retomadas in paradas:
        if retomadas >= MAX_RETOMADAS:
            # A pessoa decide (a lista "Trocas paradas no meio" mostra o Retomar).
            resumo["esgotadas"] += 1
            logger.warning(
                "atendimento_troca_lote_parada_para_a_pessoa", pedido=numero, troca=str(troca_id)
            )
            continue
        if (
            resumo["tentadas"] >= MAX_RETOMADAS_POR_RODADA
            or time.monotonic() - inicio >= orcamento_s
            or not ligado()
        ):
            break
        resumo["tentadas"] += 1
        resultado, _code = await _retomar(troca_id, numero)
        resumo[resultado] += 1
    return resumo


# ── A rodada ──────────────────────────────────────────────────────────────


async def _trocar(c: Candidato, *, agora: datetime) -> tuple[str, str | None]:
    """Uma troca, na sessão dela → (o que houve, o código). Nunca levanta."""
    # `_db.SessionLocal` lido na hora da chamada (os testes trocam o engine).
    async with _db.SessionLocal() as session:
        try:
            t = await troca.executar(
                session,
                None,
                numero_bling=c.numero,
                sku_antigo=c.sku_antigo,
                sku_novo=c.sku_novo,
                idem_key=uuid4(),
                automatica=True,
            )
        except troca.TrocaRecusada as e:
            # Sem linha, a recusa veio das travas do banco: descansa. Com a
            # linha (abortada), quem decide a próxima tentativa é `_bloqueio`.
            if e.troca_id is None and e.code not in _CODIGOS_DAS_CHAVES:
                _lembrar_recusa(c.numero, e.code, agora)
            logger.info(
                "atendimento_troca_lote_recusada",
                pedido=c.numero,
                de=c.sku_antigo,
                para=c.sku_novo,
                code=e.code,
                troca=str(e.troca_id) if e.troca_id else None,
            )
            return RECUSADAS, e.code
        except Exception as e:  # noqa: BLE001 — um pedido nunca para a rodada
            _lembrar_recusa(c.numero, ERRO_INTERNO, agora)
            # Só o tipo: a mensagem de erro de cliente HTTP pode trazer URL.
            logger.warning(
                "atendimento_troca_lote_pedido_falhou", pedido=c.numero, err=type(e).__name__
            )
            return FALHAS, None
        if t.estado == troca.CONCLUIDA:
            logger.info(
                "atendimento_troca_lote_trocou",
                pedido=c.numero,
                troca=str(t.id),
                de=t.sku_antigo,
                para=t.sku_novo,
            )
            return CONCLUIDAS, None
        # Parou no meio (incerta, PATCH que falhou…): o estado fica e uma
        # pessoa usa o Retomar — o robô não volta a este pedido.
        logger.warning(
            "atendimento_troca_lote_parou",
            pedido=c.numero,
            troca=str(t.id),
            estado=t.estado,
            code=t.codigo_erro,
        )
        return PARADAS, t.codigo_erro


async def rodada(
    *,
    agora: datetime | None = None,
    limite: int = MAX_POR_RODADA,
    orcamento_s: float = ORCAMENTO_S,
) -> dict[str, Any] | None:
    """Uma rodada: retoma as trocas do robô paradas no meio (`_retomar_paradas`),
    depois escolhe os pedidos (`pedidos_da_rodada`) e troca um por um.

    None com as chaves desligadas (nem o banco é lido). O resumo leva só
    contagens: `puladas` e `recusas` por código, e as `retomadas`.
    """
    if not ligado():
        return None
    agora = agora or _agora()
    inicio = time.monotonic()
    retomadas = await _retomar_paradas(inicio, orcamento_s)
    async with _db.SessionLocal() as session:
        candidatos, puladas = await pedidos_da_rodada(session, agora=agora)
        # Só leitura (o catálogo fica na memória do processo).
        await session.rollback()
    resumo: dict[str, Any] = {
        "candidatos": len(candidatos),
        "tentadas": 0,
        CONCLUIDAS: 0,
        PARADAS: 0,
        RECUSADAS: 0,
        FALHAS: 0,
        "cortadas": 0,
    }
    recusas: Counter[str] = Counter()
    for i, c in enumerate(candidatos):
        fim = (
            resumo["tentadas"] >= limite
            or time.monotonic() - inicio >= orcamento_s
            # Desligar é o freio: vale a partir do pedido seguinte.
            or not ligado()
        )
        if fim:
            resumo["cortadas"] = len(candidatos) - i
            break
        resumo["tentadas"] += 1
        resultado, code = await _trocar(c, agora=agora)
        resumo[resultado] += 1
        if resultado == RECUSADAS and code:
            recusas[code] += 1
        if code in _CODIGOS_DAS_CHAVES:
            resumo["cortadas"] = len(candidatos) - i - 1
            break
    resumo["puladas"] = dict(puladas)
    resumo["recusas"] = dict(recusas)
    resumo["retomadas"] = retomadas
    return resumo


# ── O cron ────────────────────────────────────────────────────────────────


async def _trava_da_rodada() -> tuple[bool, str | None]:
    """SET NX da rodada (por schema) → (pegou, token). Redis fora do ar = roda sem trava.

    Sem a trava, o pior caso são duas rodadas no mesmo pedido: a segunda
    recebe `troca_em_andamento` (uma troca aberta por pedido, com índice
    único, e o lock do pedido no banco).
    """
    chave = _CHAVE_RODADA.format(get_settings().database_schema)
    token = uuid4().hex
    try:
        pegou = await redis.set(chave, token, nx=True, ex=RODADA_TTL_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_troca_lote_trava_indisponivel", err=type(e).__name__)
        return True, None
    return bool(pegou), token


async def _soltar_rodada(token: str | None) -> None:
    if token is None:
        return
    chave = _CHAVE_RODADA.format(get_settings().database_schema)
    try:
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            chave,
            token,
        )
    except Exception:  # noqa: BLE001 — o TTL solta sozinho
        logger.warning("atendimento_troca_lote_trava_soltar_falhou")


async def atendimento_troca_lote(ctx: dict) -> dict[str, Any] | None:
    """A cada 10 min: a troca de LOTE automática dos pedidos em 83955 sem estoque.

    Só com `atendimento_troca_ativa` E `atendimento_troca_lote_auto` (as duas
    nascem desligadas: o deploy sozinho não liga) E a lista piloto escrita —
    as duas chaves ligadas com a lista vazia só deixam o aviso no log. Uma
    rodada por vez (trava no Redis). Nunca levanta.
    """
    if not ligado():
        if _chaves_ligadas():
            logger.warning("atendimento_troca_lote_sem_lista_piloto")
        return None
    pegou, token = await _trava_da_rodada()
    if not pegou:
        logger.info("atendimento_troca_lote_rodada_ocupada")
        return None
    try:
        resumo = await rodada()
    except Exception as e:  # noqa: BLE001
        logger.error("atendimento_troca_lote_falhou", err=type(e).__name__)
        return None
    finally:
        await _soltar_rodada(token)
    if resumo is None:
        return None
    if resumo["tentadas"] or resumo["cortadas"] or resumo["retomadas"]["tentadas"]:
        logger.info("atendimento_troca_lote_tick", **resumo)
    else:
        logger.debug("atendimento_troca_lote_tick", **resumo)
    return resumo
