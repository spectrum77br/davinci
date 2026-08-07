"""Auto-enfileirador de NF (sweep) — pedidos Shopee/TikTok "Em aberto".

Faz sozinho o que o botão "Enfileirar" do Painel Faturamento faz: a cada tick
varre os pedidos com situacao=6 ("Em aberto") de lojas Shopee/TikTok que têm
faturador atribuído, confere o ESTOQUE antes de tudo (regra do usuário: kit
confere o saldo do próprio SKU do kit; saldo virtual negativo = peça não
existe) e:

  - sem estoque  → Aguardando Cancelamento no Bling + status 'sem_estoque'
                   (não emite NF/etiqueta de peça que não existe);
  - com estoque  → gera a planilha por faturador e cria os NfCommand de
                   `import_avulsa` (source='auto') — daí a cadeia normal segue
                   (import → emitir_nf_upseller → imprimir_etiqueta).

Tentativa automática ÚNICA por pedido: quem já tem QUALQUER
`nf_faturamento.status_faturamento` (processando/ok/erro/sem_estoque) nunca é
re-pego pelo sweep — falha exige re-enfileirar humano pelo painel. Isso também
serve de trava operacional: pra EXCLUIR um pedido do automático basta ter uma
linha em nf_faturamento com status preenchido antes de ligar a flag.

Serializado por advisory lock transacional (mesmo desenho do
marketplace_shipment_check): dois workers nunca varrem juntos. Gated pela
flag `nf_auto_enfileirar` (checada no wrapper do worker) — default DESLIGADO.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select, text

from app.db import session_scope
from app.models import BlingOrder, NfCommand, NfFaturamento, StoreInfo
from app.services import nf_emissao_gerar
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

# Chave do advisory lock que serializa o sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x6E666165  # ascii "nfae"

# Só o "Em aberto" NATIVO do Bling (6) — é onde o pedido cai ao ser importado
# e onde fica até alguém faturar. O custom 83965 é assunto do shipment_check.
_SITUACAO_EM_ABERTO = "6"

# Plataformas que o fluxo automatizado cobre hoje (codes de store_info.platform).
_PLATAFORMAS: tuple[str, ...] = ("shopee", "tiktok")

# Pedido mais velho que isso não entra sozinho — se ficou pra trás, é caso de
# olhar no painel e enfileirar à mão (evita o sweep ressuscitar pedido antigo
# na primeira ativação da flag).
_CANDIDATE_WINDOW = timedelta(days=7)

# Teto de pedidos NOVOS por tick: não inunda a fila (nem o Upseller) de uma
# vez quando a flag liga com backlog acumulado; o resto entra nos próximos.
_MAX_POR_TICK = 30


async def run_auto_enfileirar_nf() -> dict:
    """Uma passada do sweep. Devolve um resumo com contadores."""
    summary: dict = {
        "candidatos": 0,
        "enfileirados": 0,
        "comandos": 0,
        "sem_estoque": 0,
        "pulados": 0,
    }
    async with session_scope() as session:
        got = (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        if not got:
            summary["skipped"] = "lock_busy"
            return summary

        numeros = await _candidatos(session)
        summary["candidatos"] = len(numeros)
        if not numeros:
            return summary

        # Helpers do endpoint manual — mesma regra, mesmo comportamento.
        from app.routers.nf import (
            _em_fila,
            _marcar_aguardando_cancelamento,
            _marcar_faturamento,
            _pedidos_sem_estoque,
        )

        # 1) Estoque primeiro: saldo virtual negativo → Aguardando Cancelamento.
        sem_estoque = await _pedidos_sem_estoque(session, numeros)
        if sem_estoque:
            await _marcar_aguardando_cancelamento(session, list(sem_estoque))
            await _marcar_faturamento(
                session,
                list(sem_estoque),
                status_txt="sem_estoque",
                erro="Aguardando Cancelamento — saldo negativo",
            )
            summary["sem_estoque"] = len(sem_estoque)
            logger.info(
                "nf_auto_enfileirar_sem_estoque",
                pedidos={n: skus for n, skus in sem_estoque.items()},
            )
        restantes = [n for n in numeros if n not in sem_estoque]
        if not restantes:
            return summary

        # 2) Planilha por faturador + comandos de import (cadeia normal).
        res = await nf_emissao_gerar.gerar_por_faturador(session, restantes)

        # Pulado (ex. loja sem faturador que escapou do filtro) vira 'erro' de
        # uma vez — sem isso o mesmo pedido voltaria a cada tick pra sempre.
        if res.pulados:
            for p in res.pulados:
                await _marcar_faturamento(
                    session, [p.numero], status_txt="erro", erro=p.motivo
                )
            summary["pulados"] = len(res.pulados)
            logger.warning(
                "nf_auto_enfileirar_pulados",
                pulados=[{"numero": p.numero, "motivo": p.motivo} for p in res.pulados],
            )

        em_fila = await _em_fila(session, "import_avulsa")
        total_ok = 0
        for bloco in res.blocos:
            criar = [n for n in bloco.numeros if n not in em_fila]
            if not criar:
                continue
            session.add(
                NfCommand(
                    faturador_id=bloco.faturador_id,
                    action="import_avulsa",
                    numeros=criar,
                    planilha=bloco.planilha,
                    nome_arquivo=bloco.nome_arquivo,
                    status="pending",
                    source="auto",
                )
            )
            await _marcar_faturamento(
                session, criar, status_txt="processando", erro=None
            )
            summary["comandos"] += 1
            total_ok += len(criar)
        summary["enfileirados"] = total_ok
    # session_scope commita na saída — tudo numa transação só (o advisory
    # xact lock vive até aqui).
    return summary


async def _candidatos(session) -> list[str]:
    """Pedidos elegíveis pro sweep, já deduplicados contra fila e histórico.

    Elegível = "Em aberto" (6), loja Shopee/TikTok ativa COM faturador
    atribuído, dentro da janela de 7 dias. `item_index==0` porque
    bling_orders tem uma linha por item. Dedupe duplo: comando ativo na fila
    OU qualquer status_faturamento já registrado (tentativa única).
    """
    cutoff = datetime.now(UTC) - _CANDIDATE_WINDOW
    numeros = (
        await session.execute(
            select(BlingOrder.numero)
            .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
            .where(
                BlingOrder.situacao == _SITUACAO_EM_ABERTO,
                BlingOrder.item_index == 0,
                BlingOrder.numero.is_not(None),
                BlingOrder.loja.is_not(None),
                BlingOrder.data >= cutoff,
                func.lower(StoreInfo.platform).in_(_PLATAFORMAS),
                StoreInfo.archived_at.is_(None),
                StoreInfo.nf_faturador_id.is_not(None),
            )
            .distinct()
            .order_by(BlingOrder.numero)
        )
    ).scalars().all()
    if not numeros:
        return []
    ja_tratados = set(
        (
            await session.execute(
                select(NfFaturamento.pedido_bling).where(
                    NfFaturamento.pedido_bling.in_(numeros),
                    NfFaturamento.status_faturamento.is_not(None),
                )
            )
        ).scalars().all()
    )
    from app.routers.nf import _em_fila

    em_fila = await _em_fila(session, "import_avulsa")
    return [
        n for n in numeros if n not in ja_tratados and n not in em_fila
    ][:_MAX_POR_TICK]
