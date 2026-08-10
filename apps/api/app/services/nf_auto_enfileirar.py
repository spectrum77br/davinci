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

MERGE do backlog: comandos `import_avulsa` pending (source='auto') que o
executor ainda não leaseou são FUNDIDOS com os candidatos novos do tick — o
faturador recebe UM xlsx com tudo em vez de N arquivos de 1 pedido ("se tiver
5 importa 5, se tiver 1 importa um"). FOR UPDATE SKIP LOCKED espelha o
/agent/lease: comando em lease é pulado aqui e vice-versa. Comando fundido é
DELETADO (nunca 'done' — done encadearia a etiqueta); bloco idêntico a um
pending existente reusa o comando antigo (anti-churn).

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

from app.config import get_settings
from app.db import session_scope
from app.models import BlingOrder, NfCommand, NfFaturamento, StoreInfo
from app.services import nf_emissao_gerar, threema
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
        "fundidos": 0,
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

        # Helpers do endpoint manual — mesma regra, mesmo comportamento.
        from app.routers.nf import (
            _em_fila,
            _marcar_aguardando_cancelamento,
            _marcar_faturamento,
            _pedidos_sem_estoque,
        )

        # 0) Backlog fundível: pendings do próprio sweep que o executor ainda
        #    não leaseou. SKIP LOCKED = comando no meio de um /agent/lease é
        #    invisível aqui (e o lease pula os que travamos agora).
        pendings = (
            await session.execute(
                select(NfCommand)
                .where(
                    NfCommand.action == "import_avulsa",
                    NfCommand.status == "pending",
                    NfCommand.source == "auto",
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        pendings_por_fat: dict = {}
        for cmd in pendings:
            pendings_por_fat.setdefault(cmd.faturador_id, []).append(cmd)
        # Fragmentado = algum faturador com MAIS de um pending (vale fundir
        # mesmo sem candidato novo). 1 pending por faturador já está ótimo.
        fragmentado = any(len(v) > 1 for v in pendings_por_fat.values())
        if not numeros and not fragmentado:
            return summary

        # 1) Estoque primeiro: saldo virtual negativo → Aguardando Cancelamento.
        #    Só os candidatos NOVOS — o backlog já passou no check ao entrar.
        sem_estoque = await _pedidos_sem_estoque(session, numeros) if numeros else {}
        if sem_estoque:
            await _marcar_aguardando_cancelamento(session, list(sem_estoque))
            # Erro por pedido com os SKUs negativos — o painel mostra no
            # tooltip do badge "Sem estoque" QUAL peça travou.
            for numero, skus in sem_estoque.items():
                await _marcar_faturamento(
                    session,
                    [numero],
                    status_txt="sem_estoque",
                    erro=(
                        "Aguardando Cancelamento — saldo negativo: "
                        + ", ".join(skus)
                    ),
                )
            summary["sem_estoque"] = len(sem_estoque)
            logger.info(
                "nf_auto_enfileirar_sem_estoque",
                pedidos={n: skus for n, skus in sem_estoque.items()},
            )
            await _notificar_sem_estoque(session, sem_estoque)
        restantes = [n for n in numeros if n not in sem_estoque]
        if not restantes and not fragmentado:
            return summary

        # 2) Planilha por faturador + comandos de import (cadeia normal).
        #    União: candidatos novos + números do backlog pending — o gerador
        #    reagrupa por faturador e sai UM arquivo por faturador/chunk.
        pend_numeros = [
            n
            for cmd in pendings
            for n in (cmd.numeros or [])
            if n not in restantes
        ]
        uniao = restantes + list(dict.fromkeys(pend_numeros))
        res = await nf_emissao_gerar.gerar_por_faturador(session, uniao)

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

        # 3) Anti-churn: bloco idêntico a UM pending do mesmo faturador reusa
        #    o comando antigo (não deleta/recria a cada tick). O resto do
        #    backlog lockado é fundido nos blocos novos e DELETADO — nunca
        #    'done' (done encadearia etiqueta como se tivesse importado).
        fundir = list(pendings)
        blocos_novos = []
        for bloco in res.blocos:
            alvo = set(bloco.numeros)
            match = next(
                (
                    c
                    for c in pendings_por_fat.get(bloco.faturador_id, [])
                    if c in fundir and set(c.numeros or []) == alvo
                ),
                None,
            )
            if match is not None:
                fundir.remove(match)
                continue
            blocos_novos.append(bloco)
        for cmd in fundir:
            await session.delete(cmd)
        if fundir:
            summary["fundidos"] = len(fundir)
            logger.info(
                "nf_auto_enfileirar_fundidos",
                comandos=[str(c.id) for c in fundir],
            )

        # Recalcula a fila DEPOIS dos deletes (autoflush emite os DELETEs
        # antes do SELECT) — sobram claimed + pendings mantidos/skip-locked.
        em_fila = await _em_fila(session, "import_avulsa")
        total_ok = 0
        for bloco in blocos_novos:
            if any(n in em_fila for n in bloco.numeros):
                # NUNCA criar comando cujos números divergem da planilha
                # congelada (o Upseller importaria o pedido extra). Conflito
                # aqui é raro (número reapareceu num comando claimed no meio
                # do tick) — fica pro próximo tick / varredura de órfãos.
                logger.warning(
                    "nf_auto_enfileirar_bloco_conflito", numeros=bloco.numeros
                )
                continue
            session.add(
                NfCommand(
                    faturador_id=bloco.faturador_id,
                    action="import_avulsa",
                    numeros=bloco.numeros,
                    planilha=bloco.planilha,
                    nome_arquivo=bloco.nome_arquivo,
                    status="pending",
                    source="auto",
                )
            )
            await _marcar_faturamento(
                session, bloco.numeros, status_txt="processando", erro=None
            )
            summary["comandos"] += 1
            total_ok += len(bloco.numeros)
        summary["enfileirados"] = total_ok
    # session_scope commita na saída — tudo numa transação só (o advisory
    # xact lock vive até aqui).
    return summary


async def _notificar_sem_estoque(
    session, sem_estoque: dict[str, list[str]]
) -> None:
    """Aviso Threema quando o sweep move pedido pra Aguardando Cancelamento.

    Destinatários em `nf_sem_estoque_threema_recipients` (vazio = desligado).
    UMA mensagem agregada por tick, uma linha por pedido com loja + SKUs
    negativos. BEST-EFFORT: falha no envio só loga, nunca quebra o sweep.
    """
    recipients = threema.parse_recipients(
        get_settings().nf_sem_estoque_threema_recipients
    )
    if not recipients or not sem_estoque:
        return
    try:
        lojas = dict(
            (
                await session.execute(
                    select(BlingOrder.numero, func.max(StoreInfo.account_name))
                    .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
                    .where(BlingOrder.numero.in_(list(sem_estoque)))
                    .group_by(BlingOrder.numero)
                )
            ).all()
        )
        linhas = [
            f"Pedido {numero}"
            + (f" ({lojas[numero]})" if lojas.get(numero) else "")
            + ": " + ", ".join(skus)
            for numero, skus in sorted(sem_estoque.items())
        ]
        texto = (
            "Estoque negativo — movido pra Aguardando Cancelamento:\n"
            + "\n".join(linhas)
        )
        result = await threema.ThreemaClient().send_to_all(texto, recipients)
        logger.info(
            "nf_auto_enfileirar_threema",
            sent=result.get("sent"),
            failed=result.get("failed"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("nf_sem_estoque_threema_falhou", erro=str(exc))


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
