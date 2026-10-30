"""Auto-enfileirador de NF (sweep) — pedidos Shopee/TikTok/ML/Amazon "Em aberto".

Faz sozinho o que o botão "Enfileirar" do Painel Faturamento faz: a cada tick
varre os pedidos com situacao=6 ("Em aberto") de lojas com faturador
atribuído — Shopee/TikTok/Amazon contínuos; ML segue o HORÁRIO da tela Lojas
(correios contínuo, agência nos horários da loja, sábado uma vez só e domingo
nunca — ver `loja_emite_agora`) —, confere o ESTOQUE antes de tudo (saldo
virtual negativo = falta peça; kit com composição no cache conta SEMPRE pelos
componentes; a peça fica com os pedidos mais ANTIGOS — FIFO, ver
`routers/nf.py::_sem_estoque_fifo`) e:

  - restrição    → Shopee + produto Apple (pelo nome) + destino RJ não é
                   enviado: "restrição" nas Observações do pedido no Bling +
                   Aguardando Cancelamento + status 'restricao';
  - trava do ML  → pedido ML "Em aberto" pra UF da Restrição da loja que não
                   casa nenhuma Exceção vai pra Aguardando Cancelamento do
                   mesmo jeito, MESMO com o automático do ML desligado (a NF
                   do ML sai pelo robô do Eduardo, fora desta fila);
  - sem estoque  → só os pedidos MAIS NOVOS que estouram o saldo: "Sem
                   estoque — saldo negativo: <SKUs>" nas Observações +
                   Aguardando Cancelamento no Bling + status 'sem_estoque'
                   só se o Bling ACEITOU a 83955 (senão fica sem marca e a
                   próxima passada tenta de novo) — não emite NF/etiqueta de
                   peça que não existe. Bling recusou a 83955 5 vezes no
                   dia → status 'erro' (sai do sweep) + aviso Threema.
                   Negativo que cai num pedido FORA da passada (ex.: ML/Amazon)
                   não é movido: vira aviso Threema;
  - sem leitura  → pedido que leva produto que o Bling não respondeu (consulta
                   falhou) é ADIADO: nem 83955, nem fila, nem marca — a
                   próxima passada confere de novo;
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

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, or_, select, text

from app.config import get_settings
from app.db import session_scope
from app.models import BlingOrder, NfCommand, NfFaturamento, NfImpressao, Product, StoreInfo
from app.redis_client import redis
from app.services import nf_emissao_gerar, threema
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.prioridade_estoque import aplicar_prioridade_estoque
from app.services.sku_tags import classify_sku_tag

logger = structlog.get_logger()

# Chave do advisory lock que serializa o sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x6E666165  # ascii "nfae"

# Só o "Em aberto" NATIVO do Bling (6) — é onde o pedido cai ao ser importado
# e onde fica até alguém faturar. A etiqueta enviada (21 "Em digitação";
# 83965 "Enviado Etiqueta" legado) é assunto do shipment_check — nunca entra aqui.
_SITUACAO_EM_ABERTO = "6"

# Plataformas que o fluxo automatizado cobre hoje (codes de store_info.platform).
_PLATAFORMAS: tuple[str, ...] = ("shopee", "tiktok", "ml", "amazon")

# ML e Amazon só entram no automático com a flag `nf_auto_ml_amazon` LIGADA
# (usuário: "nao e para ativar mercado livre ainda... vamos testar de noite").
# A flag é o interruptor mestre; QUANDO ligar, o horário de cada loja é quem
# manda (ver `loja_emite_agora`). `nf_auto_ml` liga SÓ o ML (Eduardo 15/09:
# "vamos ligar o automático igual da Shopee") — Amazon continua fora.
_PLATAFORMAS_JANELA: tuple[str, ...] = ("ml", "amazon")
_TZ_BRT = ZoneInfo("America/Sao_Paulo")

_DIA_SABADO = 5
_DIA_DOMINGO = 6


def _plataformas_ativas(*, ml_amazon: bool, so_ml: bool) -> tuple[str, ...]:
    """Plataformas que o sweep varre com as flags dadas (ordem de _PLATAFORMAS)."""
    liberadas = set(_PLATAFORMAS) - set(_PLATAFORMAS_JANELA)
    if ml_amazon:
        liberadas |= set(_PLATAFORMAS_JANELA)
    if so_ml:
        liberadas.add("ml")
    return tuple(p for p in _PLATAFORMAS if p in liberadas)


def _agora_brt() -> datetime:
    """Agora em Brasília (função separada pra teste monkeypatchar)."""
    return datetime.now(_TZ_BRT)


def horas_de(txt: str | None) -> tuple[int, ...]:
    """"10:00, 14:00" → (10, 14). Vazio/NULL → () (= nenhuma hora marcada)."""
    horas: list[int] = []
    for parte in (txt or "").split(","):
        parte = parte.strip()
        if not parte:
            continue
        try:
            horas.append(int(parte.split(":")[0]))
        except ValueError:
            continue
    return tuple(horas)


def loja_emite_agora(
    *,
    plataforma: str | None,
    impressao: str | None,
    etiqueta_horarios: str | None,
    sabado_horario: str | None,
    agora: datetime,
) -> bool:
    """A loja emite NESTE instante? (regra da tela Lojas, decidida em 21/08)

    - Shopee, TikTok e Amazon: contínuos ("amazon é continuo").
    - ML + Impressão **correios**: contínuo todo dia, inclusive sábado e
      domingo, e sem regra de estoque.
    - ML + Impressão **agência**: dia útil nos `etiqueta_horarios` da loja;
      **sábado** só na hora de `etiqueta_sabado_horario` e só pros estoques de
      `etiqueta_sabado_tags` (filtro à parte, ver `pedido_sai_no_sabado`);
      **domingo não emite**.

    Horário em branco = o automático NÃO age (usuário, 21/08: "as que estao
    sem horario etiqueta nao faça nada") — a loja segue no enfileiramento
    manual. É o INVERSO da leitura da tela Lojas, onde vazio quer dizer
    "imprime contínuo" (migration 0222): aqui, sem horário não há quando.

    Janela = a HORA cheia (10:00–10:59): o tick de 2min dá várias passadas
    com teto de 30, drenando o acumulado.
    """
    if (plataforma or "").strip().lower() != "ml":
        return True
    if (impressao or "").strip().lower() == "correios":
        return True

    dia = agora.weekday()
    if dia == _DIA_DOMINGO:
        return False
    if dia == _DIA_SABADO:
        return agora.hour in horas_de(sabado_horario)
    return agora.hour in horas_de(etiqueta_horarios)


def tags_de(txt: str | None) -> set[str]:
    """"pi, ra" → {"pi", "ra"}. Vazio/NULL → set() (= nenhum estoque)."""
    return {t.strip().lower() for t in (txt or "").split(",") if t.strip()}


def pedido_sai_no_sabado(skus: list[str | None], tags: set[str]) -> bool:
    """No sábado a agência só emite dos estoques marcados na loja.

    Conservador de propósito: TODO item do pedido tem que sair de um estoque
    marcado — um item de estoque fechado no sábado trava o pedido inteiro
    (não adianta emitir NF do que não vai ser despachado).
    """
    if not tags or not skus:
        return False
    return all(classify_sku_tag(sku) in tags for sku in skus)

# Pedido mais velho que isso não entra sozinho — se ficou pra trás, é caso de
# olhar no painel e enfileirar à mão (evita o sweep ressuscitar pedido antigo
# na primeira ativação da flag).
_CANDIDATE_WINDOW = timedelta(days=7)

# Teto de pedidos NOVOS por tick: não inunda a fila (nem o Upseller) de uma
# vez quando a flag liga com backlog acumulado; o resto entra nos próximos.
_MAX_POR_TICK = 30

# Marca do aviso de negativo FORA do lote (pedido/SKU/dia) — a chave já leva o
# dia; o TTL só limpa.
_AVISO_FORA_DO_LOTE_PREFIXO = "nf:sem_estoque:fora_do_lote:"
_AVISO_FORA_DO_LOTE_TTL_S = 36 * 3600
# Aviso de kit sem composição no cache que desligou o físico do sem estoque
# (kit/dia) — sem ele a conta conservadora repete em silêncio até a sync
# semanal dos kits.
_AVISO_KIT_SEM_COMPOSICAO_PREFIXO = "nf:sem_estoque:kit_sem_composicao:"
# PATCH 83955 recusado: a passada tenta de novo (sem marca), com TETO — na
# `_MAX_RECUSAS_83955`ª recusa do dia o pedido ganha 'erro' no painel (sai do
# sweep: tentativa única) + 1 aviso. Contador no Redis, chave com o dia.
_MAX_RECUSAS_83955 = 5
_RECUSA_83955_PREFIXO = "nf:sem_estoque:recusa_83955:"
_AVISO_RECUSA_83955_PREFIXO = "nf:sem_estoque:recusa_83955_aviso:"
_RECUSA_83955_TTL_S = 26 * 3600

# Restrição Shopee: produto Apple NÃO é enviado pro Rio de Janeiro. Pedido
# que casa (loja Shopee + destino RJ + nome do item com uma das palavras)
# não gera NF/etiqueta: vai pra Aguardando Cancelamento com "restrição" nas
# Observações do pedido no Bling. Detecção pelo NOME ("pelo nome ja da pra
# saber") — qualquer item que case bloqueia o pedido inteiro.
_RESTRICAO_UF = "RJ"
_RESTRICAO_PLATAFORMAS: tuple[str, ...] = ("shopee",)
_RESTRICAO_KEYWORDS: tuple[str, ...] = (
    "apple",
    "iphone",
    "ipad",
    "macbook",
    "airpod",
    "imac",
)
_RESTRICAO_OBSERVACAO = "restrição"

# Trava do ML (Vinicius 21/10: "nao quero enviar celular para RS"): a NF e a
# etiqueta do ML saem pelo robô do Eduardo, FORA desta fila, e o automático
# do ML (nf_auto_ml) está desligado — a Restrição da loja nunca agia no ML.
# Este passo aplica SÓ a restrição por loja (Restrição + Exceções) aos
# pedidos ML "Em aberto", sem enfileirar nada: o barrado vai pra Aguardando
# Cancelamento antes de o robô emitir. Não depende do horário da loja nem de
# faturador; tentativa única igual ao resto (status 'restricao').
_TRAVA_ML_PLATAFORMAS: tuple[str, ...] = ("ml", "mercadolivre")

async def run_auto_enfileirar_nf() -> dict:
    """Uma passada do sweep. Devolve um resumo com contadores."""
    summary: dict = {
        "candidatos": 0,
        "enfileirados": 0,
        "comandos": 0,
        "sem_estoque": 0,
        "restricao": 0,
        "restricao_loja": 0,
        "restricao_ml": 0,
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

        # 0) Trava do ML ANTES dos candidatos: o barrado aqui ganha status
        #    'restricao' e já não entra no `_candidatos` (tentativa única)
        #    quando o automático do ML estiver ligado. Falha nunca derruba o
        #    sweep da Shopee/TikTok (savepoint: erro de banco aqui não deixa a
        #    transação abortada pro resto).
        try:
            async with session.begin_nested():
                summary["restricao_ml"] = await _travar_ml(session)
        except Exception:  # noqa: BLE001
            logger.exception("nf_auto_enfileirar_trava_ml_falhou")

        numeros = await _candidatos(session)
        summary["candidatos"] = len(numeros)

        # Helpers do endpoint manual — mesma regra, mesmo comportamento.
        from app.routers.nf import (
            _em_fila,
            _marcar_aguardando_cancelamento,
            _marcar_faturamento,
            _sem_estoque_fifo,
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

        # 1a) Restrição Shopee Apple→RJ ANTES do check de estoque (não gasta
        #     chamada do Bling com pedido que já vai ser bloqueado). ORDEM:
        #     Observação (GET→PUT) PRIMEIRO, situação (PATCH 83955) DEPOIS —
        #     um PUT com body stale reverteria a situação recém-mudada.
        restricao = await _pedidos_restricao(session, numeros) if numeros else {}
        if restricao:
            await _escrever_observacao_restricao(session, list(restricao))
            await _marcar_aguardando_cancelamento(session, list(restricao))
            for numero, itens in restricao.items():
                await _marcar_faturamento(
                    session,
                    [numero],
                    status_txt="restricao",
                    erro=(
                        "Restrição Shopee — Apple não envia pro RJ: "
                        + ", ".join(itens)
                    ),
                )
            summary["restricao"] = len(restricao)
            logger.info(
                "nf_auto_enfileirar_restricao",
                pedidos={n: itens for n, itens in restricao.items()},
            )
        numeros = [n for n in numeros if n not in restricao]

        # 1b) Restrição POR LOJA (campos "Restrição" + "Exceções" da tela
        #     Lojas): a loja NÃO envia pras UFs de store_info.uf_restrictions;
        #     store_info.excecoes lista o que PODE ir mesmo assim. Pedido pra
        #     UF restrita que não casa nenhuma exceção é bloqueado, ADICIONAL
        #     à restrição hardcoded acima. Mesma mecânica: obs "restrição"
        #     (GET→PUT) ANTES do PATCH 83955.
        bloqueio = await _pedidos_restricao_loja(session, numeros) if numeros else {}
        if bloqueio:
            await _escrever_observacao_restricao(session, list(bloqueio))
            await _marcar_aguardando_cancelamento(session, list(bloqueio))
            for numero, motivo in bloqueio.items():
                await _marcar_faturamento(
                    session, [numero], status_txt="restricao", erro=motivo
                )
            summary["restricao_loja"] = len(bloqueio)
            logger.info(
                "nf_auto_enfileirar_restricao_loja",
                pedidos={n: motivo for n, motivo in bloqueio.items()},
            )
        numeros = [n for n in numeros if n not in bloqueio]

        # 1c) PRIORIDADE de estoque (Tabela de Preços → coluna Prioridade):
        #     troca o SKU do pedido pra tag prioritária ANTES do check de
        #     estoque — espelho atualizado na MESMA sessão, então o
        #     `_pedidos_sem_estoque` logo abaixo já confere o SKU novo e a
        #     NF sai com ele. Falha aqui nunca trava o enfileiramento.
        if numeros:
            try:
                prio = await aplicar_prioridade_estoque(session, numeros)
                # O Bling não respondeu sobre o estoque deste pedido: ele não
                # passa pelo check de estoque AGORA (sairia daqui para Aguardando
                # Cancelamento sem o robô ter decidido o lote); volta na próxima.
                adiados = set(prio.get("adiados") or [])
                if adiados:
                    logger.info("nf_auto_enfileirar_prioridade_adiados", pedidos=sorted(adiados))
                    summary["adiados_prioridade"] = len(adiados)
                    numeros = [n for n in numeros if n not in adiados]
            except Exception:  # noqa: BLE001
                logger.exception("nf_auto_enfileirar_prioridade_falhou")

        # 1) Estoque primeiro: saldo virtual negativo → Aguardando Cancelamento
        #    SÓ pros mais novos que estouram o saldo (FIFO: o antigo já tem a
        #    peça reservada e segue). Só os candidatos NOVOS — o backlog já
        #    passou no check ao entrar.
        #    Mesma ordem da restrição: recado nas Observações (GET→PUT)
        #    PRIMEIRO, situação (PATCH 83955) DEPOIS.
        conferencia = await _sem_estoque_fifo(session, numeros) if numeros else None
        sem_estoque = conferencia.lote if conferencia else {}
        # Sem leitura no Bling de algum produto do pedido: ADIADO (nem 83955,
        # nem fila, nem marca) — igual à prioridade quando o Bling não responde.
        adiados_estoque = set(conferencia.adiados) if conferencia else set()
        if adiados_estoque:
            summary["adiados_estoque"] = len(adiados_estoque)
            logger.info("nf_auto_enfileirar_estoque_adiados", pedidos=sorted(adiados_estoque))
        if conferencia and conferencia.kits_sem_composicao:
            try:
                async with session.begin_nested():
                    await avisar_kit_sem_composicao(session, conferencia.kits_sem_composicao)
            except Exception:  # noqa: BLE001
                logger.exception("nf_sem_estoque_kit_sem_composicao_aviso_falhou")
        if sem_estoque:
            await escrever_observacoes(
                session,
                {n: observacao_sem_estoque(skus) for n, skus in sem_estoque.items()},
                evento="nf_sem_estoque_observacao",
            )
            movidos = await _marcar_aguardando_cancelamento(session, list(sem_estoque))
            # Erro por pedido com os SKUs negativos — o painel mostra no
            # tooltip do badge "Sem estoque" QUAL peça travou. Só pra quem o
            # Bling ACEITOU (igual à trava do ML): quem falhou fica sem marca,
            # fora da fila, e a próxima passada tenta de novo.
            for numero, skus in sem_estoque.items():
                if numero not in movidos:
                    continue
                await _marcar_faturamento(
                    session,
                    [numero],
                    status_txt="sem_estoque",
                    erro=(
                        "Aguardando Cancelamento — saldo negativo: "
                        + ", ".join(skus)
                    ),
                )
            falharam = sorted(set(sem_estoque) - movidos)
            if falharam:
                summary["sem_estoque_pendente"] = len(falharam)
                logger.warning("nf_auto_enfileirar_sem_estoque_pendente", pedidos=falharam)
                # Teto: na 5ª recusa do dia sai do sweep com 'erro' (o painel
                # mostra o motivo) — não ocupa as vagas do tick pra sempre.
                recusas = await _contar_recusas_83955(falharam)
                esgotados = {
                    n: sem_estoque[n]
                    for n in falharam
                    if recusas.get(n, 0) >= _MAX_RECUSAS_83955
                }
                for numero, skus in esgotados.items():
                    await _marcar_faturamento(
                        session,
                        [numero],
                        status_txt="erro",
                        erro=(
                            f"Sem estoque — o Bling recusou Aguardando Cancelamento"
                            f" {recusas[numero]} vezes hoje (saldo negativo: "
                            + ", ".join(skus)
                            + "). Mude a situação à mão no Bling."
                        ),
                    )
                if esgotados:
                    summary["sem_estoque_recusado"] = len(esgotados)
                    logger.warning(
                        "nf_auto_enfileirar_sem_estoque_recusado", pedidos=esgotados
                    )
                    try:
                        async with session.begin_nested():
                            await _avisar_recusa_83955(session, esgotados)
                    except Exception:  # noqa: BLE001
                        logger.exception("nf_sem_estoque_recusa_aviso_falhou")
            summary["sem_estoque"] = len(movidos)
            if movidos:
                logger.info(
                    "nf_auto_enfileirar_sem_estoque",
                    pedidos={n: skus for n, skus in sem_estoque.items() if n in movidos},
                )
                await _notificar_sem_estoque(
                    session, {n: s for n, s in sem_estoque.items() if n in movidos}
                )
        if conferencia and conferencia.fora_do_lote:
            # O FIFO apontou um pedido que não está nesta passada (ex.: ML/Amazon
            # que o sweep não confere): não move — avisa quem decide. Savepoint:
            # erro aqui nunca deixa a transação do sweep abortada.
            try:
                async with session.begin_nested():
                    await _avisar_negativo_fora_do_lote(session, conferencia.fora_do_lote)
            except Exception:  # noqa: BLE001
                logger.exception("nf_sem_estoque_fora_do_lote_aviso_falhou")
        restantes = [
            n for n in numeros if n not in sem_estoque and n not in adiados_estoque
        ]
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

    Destinatários = SÓ o grupo `nf_sem_estoque_threema_recipients` (vazio =
    TUDO desligado); todos recebem a mensagem completa. BEST-EFFORT: falha
    no envio só loga, nunca quebra o sweep.
    """
    gerais = threema.parse_recipients(
        get_settings().nf_sem_estoque_threema_recipients
    )
    if not gerais or not sem_estoque:
        return
    try:
        texto = await _linhas_pedidos(
            session,
            sem_estoque,
            "Estoque negativo — movido pra Aguardando Cancelamento:",
        )
        client = threema.ThreemaClient(contexto="estoque")
        result = await client.send_to_all(texto, gerais)
        logger.info(
            "nf_auto_enfileirar_threema",
            sent=result.get("sent"),
            failed=result.get("failed"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("nf_sem_estoque_threema_falhou", erro=str(exc))


async def _linhas_pedidos(session, pedidos: dict[str, list[str]], titulo: str) -> str:
    """Texto do aviso: título + "Pedido N (loja): SKUs" por pedido."""
    # Rótulo = "plataforma conta equipe N" (ex. "shopee vortan equipe 2")
    # — nome da loja + equipe de vendas a que ela pertence (pedido do
    # usuário 17/08); equipe NULL fica de fora.
    lojas = {
        numero: " ".join(
            p
            for p in (
                plataforma,
                conta,
                f"equipe {equipe}" if equipe is not None else None,
            )
            if p
        )
        for numero, plataforma, conta, equipe in (
            await session.execute(
                select(
                    BlingOrder.numero,
                    func.max(func.lower(StoreInfo.platform)),
                    func.max(StoreInfo.account_name),
                    func.max(StoreInfo.commercial_team),
                )
                .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
                .where(BlingOrder.numero.in_(list(pedidos)))
                .group_by(BlingOrder.numero)
            )
        ).all()
    }
    linhas = [
        f"Pedido {numero}"
        + (f" ({lojas[numero]})" if lojas.get(numero) else "")
        + ": " + ", ".join(skus)
        for numero, skus in sorted(pedidos.items())
    ]
    return titulo + "\n" + "\n".join(linhas)


async def _marcar_hoje(chaves: list[str], ttl_s: int, *, evento: str) -> list[str] | None:
    """SET NX de cada chave (marca de "já avisei hoje"); devolve as que eram
    NOVAS. None = Redis fora (quem chama não avisa: melhor que repetir a cada
    passada)."""
    novas: list[str] = []
    try:
        for chave in chaves:
            if await redis.set(chave, "1", nx=True, ex=ttl_s):
                novas.append(chave)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"{evento}_dedupe_falhou", erro=str(exc)[:200])
        return None
    return novas


async def _desfazer_marcas(marcas: list[str], *, evento: str) -> None:
    try:
        if marcas:
            await redis.delete(*marcas)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"{evento}_marca_ficou", erro=str(exc)[:200])


async def _enviar_aviso(
    montar_texto: Callable[[], Awaitable[str]],
    gerais: list[str],
    marcas: list[str],
    *,
    evento: str,
) -> None:
    """Monta e manda o aviso pro grupo do sem estoque. Falhou → desfaz as
    marcas (a próxima passada tenta de novo); canal sem config → deixa as
    marcas (tentar a cada passada não adianta). Erro ao montar o texto (banco)
    sobe depois de desfazer as marcas — o savepoint de quem chama desfaz a
    transação. O envio é BEST-EFFORT."""
    try:
        texto = await montar_texto()
    except Exception:
        await _desfazer_marcas(marcas, evento=evento)
        raise
    try:
        result = await threema.ThreemaClient(contexto="estoque").send_to_all(texto, gerais)
        if not result.get("sent"):
            raise RuntimeError(f"nenhum envio: {result.get('failed')}")
        logger.info(f"{evento}_threema", sent=result.get("sent"), failed=result.get("failed"))
    except threema.ThreemaConfigError as exc:
        logger.warning(f"{evento}_threema_sem_config", erro=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"{evento}_threema_falhou", erro=str(exc)[:200])
        await _desfazer_marcas(marcas, evento=evento)


def _destinatarios_sem_estoque() -> list[str]:
    return threema.parse_recipients(get_settings().nf_sem_estoque_threema_recipients)


async def _avisar_negativo_fora_do_lote(session, fora: dict[str, list[str]]) -> None:
    """Aviso Threema (mesmo grupo do sem estoque) quando o FIFO aponta como
    negativo um pedido que NÃO está nesta passada — ex.: ML/Amazon, que o
    sweep não confere. Ele NÃO é movido: alguém decide (o warning
    `nf_sem_estoque_fifo_negativo_fora_do_lote` fica no log de qualquer jeito).

    Pedido que o próprio sweep ainda vai conferir (passou do teto do tick,
    adiado pela prioridade) fica de fora — ele vai pra 83955 sozinho. Um aviso
    por pedido/SKU/dia (marca no Redis, a passada é a cada 2 min); Redis fora
    = não avisa (melhor que repetir a cada passada). BEST-EFFORT."""
    gerais = _destinatarios_sem_estoque()
    if not gerais or not fora:
        return
    vai_conferir = set(await _candidatos(session, limite=None))
    pendentes = {n: s for n, s in fora.items() if n not in vai_conferir}
    if not pendentes:
        return
    dia = _agora_brt().strftime("%Y%m%d")
    chave_de = {
        f"{_AVISO_FORA_DO_LOTE_PREFIXO}{dia}:{numero}:{sku}": (numero, sku)
        for numero, skus in sorted(pendentes.items())
        for sku in skus
    }
    evento = "nf_sem_estoque_fora_do_lote"
    marcas = await _marcar_hoje(list(chave_de), _AVISO_FORA_DO_LOTE_TTL_S, evento=evento)
    if not marcas:
        return
    novos: dict[str, list[str]] = {}
    for chave in marcas:
        numero, sku = chave_de[chave]
        novos.setdefault(numero, []).append(sku)
    logger.info(f"{evento}_avisando", pedidos=novos)

    async def _texto() -> str:
        return await _linhas_pedidos(
            session,
            novos,
            "Estoque negativo — o pedido MAIS NOVO não está na fila automática."
            " NÃO foi movido; confira antes de emitir:",
        )

    await _enviar_aviso(_texto, gerais, marcas, evento=evento)


async def avisar_kit_sem_composicao(session, kits: list[int]) -> None:
    """Aviso Threema (grupo do sem estoque) quando um kit SEM composição no
    cache segura peça com saldo negativo e, por isso, o sem estoque ficou sem
    o saldo físico (`nf_sem_estoque_kit_sem_composicao`): a conta fica no
    virtual — o lado seguro, mas que pode mandar pra Aguardando Cancelamento
    pedido que tinha peça. Sem o aviso a regra repete isso em silêncio até a
    sync semanal dos kits. Um aviso por kit/dia (marca no Redis). BEST-EFFORT
    (vale pro sweep e pro botão Enfileirar)."""
    gerais = _destinatarios_sem_estoque()
    if not gerais or not kits:
        return
    dia = _agora_brt().strftime("%Y%m%d")
    chave_de = {f"{_AVISO_KIT_SEM_COMPOSICAO_PREFIXO}{dia}:{k}": k for k in sorted(set(kits))}
    evento = "nf_sem_estoque_kit_sem_composicao_aviso"
    marcas = await _marcar_hoje(list(chave_de), _AVISO_FORA_DO_LOTE_TTL_S, evento=evento)
    if not marcas:
        return
    novos = [chave_de[c] for c in marcas]
    logger.info(f"{evento}_avisando", kits=novos)

    async def _texto() -> str:
        skus = dict(
            (
                await session.execute(
                    select(Product.bling_product_id, func.min(Product.sku))
                    .where(Product.bling_product_id.in_(novos))
                    .group_by(Product.bling_product_id)
                )
            ).all()
        )
        return (
            "Sem estoque conferido SEM o saldo físico: kit sem composição no DaVinci"
            " segurando peça com saldo negativo (não dá pra saber qual peça ele"
            " prende). Pode ir pra Aguardando Cancelamento pedido que tinha peça."
            " Confira a composição no Bling e rode a sync dos kits:\n"
            + "\n".join(f"Kit {skus.get(k) or 'sem SKU no DaVinci'} (id Bling {k})" for k in novos)
        )

    await _enviar_aviso(_texto, gerais, marcas, evento=evento)


async def _contar_recusas_83955(numeros: list[str]) -> dict[str, int]:
    """numero → quantas vezes o Bling recusou a 83955 HOJE (INCR no Redis;
    chave com o dia, a validade só limpa). Redis fora = {} — ninguém atinge o
    teto e a passada segue tentando, como antes."""
    dia = _agora_brt().strftime("%Y%m%d")
    out: dict[str, int] = {}
    try:
        for numero in numeros:
            chave = f"{_RECUSA_83955_PREFIXO}{dia}:{numero}"
            n = int(await redis.incr(chave))
            # Validade no 1º do dia — e reposta se ficou sem (processo caiu
            # entre os dois comandos): contador sem validade nunca some.
            if n == 1 or await redis.ttl(chave) < 0:
                await redis.expire(chave, _RECUSA_83955_TTL_S)
            out[numero] = n
    except Exception as exc:  # noqa: BLE001
        logger.warning("nf_sem_estoque_recusa_contador_falhou", erro=str(exc)[:200])
    return out


async def _avisar_recusa_83955(session, pedidos: dict[str, list[str]]) -> None:
    """Aviso Threema (grupo do sem estoque): o Bling recusou a 83955 até o
    teto do dia e o pedido saiu da fila automática com 'erro' no painel —
    alguém muda a situação à mão. Um aviso por pedido/dia. BEST-EFFORT."""
    gerais = _destinatarios_sem_estoque()
    if not gerais or not pedidos:
        return
    dia = _agora_brt().strftime("%Y%m%d")
    chave_de = {f"{_AVISO_RECUSA_83955_PREFIXO}{dia}:{n}": n for n in sorted(pedidos)}
    evento = "nf_sem_estoque_recusa_83955"
    marcas = await _marcar_hoje(list(chave_de), _RECUSA_83955_TTL_S, evento=evento)
    if not marcas:
        return
    novos = {chave_de[c]: pedidos[chave_de[c]] for c in marcas}

    async def _texto() -> str:
        return await _linhas_pedidos(
            session,
            novos,
            f"Estoque negativo — o Bling recusou Aguardando Cancelamento {_MAX_RECUSAS_83955}"
            " vezes hoje. O pedido saiu da fila automática (Erro no painel); mude a"
            " situação à mão:",
        )

    await _enviar_aviso(_texto, gerais, marcas, evento=evento)


async def _pedidos_restricao(
    session, numeros: list[str]
) -> dict[str, list[str]]:
    """Pedidos que caem na restrição Shopee Apple→RJ.

    Olha TODOS os itens do pedido (não só item_index=0): um item Apple
    bloqueia o pedido inteiro. Retorna numero → nomes dos itens que casaram.
    """
    if not numeros:
        return {}
    rows = (
        await session.execute(
            select(BlingOrder.numero, BlingOrder.item_descricao)
            .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
            .where(
                BlingOrder.numero.in_(numeros),
                func.lower(StoreInfo.platform).in_(_RESTRICAO_PLATAFORMAS),
                func.upper(func.trim(BlingOrder.uf_destino)) == _RESTRICAO_UF,
                BlingOrder.item_descricao.is_not(None),
                or_(
                    *[
                        BlingOrder.item_descricao.ilike(f"%{kw}%")
                        for kw in _RESTRICAO_KEYWORDS
                    ]
                ),
            )
            .distinct()
            .order_by(BlingOrder.numero, BlingOrder.item_descricao)
        )
    ).all()
    restricao: dict[str, list[str]] = {}
    for numero, descricao in rows:
        itens = restricao.setdefault(numero, [])
        if descricao not in itens:
            itens.append(descricao)
    return restricao


def _brl(valor) -> str:
    """700 → "R$ 700,00" (formato BR pra mensagem de erro do painel)."""
    txt = f"{float(valor):,.2f}".replace(",", "_").replace(".", ",")
    return "R$ " + txt.replace("_", ".")


def avaliar_restricao_loja(
    regras: list[dict],
    *,
    uf_destino: str | None,
    ufs_restricao: list[str] | None,
    valor_total,
    itens: list[tuple[str | None, str | None]],
) -> str | None:
    """Motivo do bloqueio pela restrição da loja. None = pode faturar.

    `ufs_restricao` = store_info.uf_restrictions (campo "Restrição" da tela
    Lojas) = as UFs pras quais a loja NÃO envia. `regras` = store_info.
    excecoes (lista JSONB, ver schemas.StoreExcecao) = o que PODE ir mesmo
    assim; casou uma exceção, LIBERA. `itens` = [(sku, nome), ...] de TODOS
    os itens do pedido. Tipos:
      - "valor":   libera se o valor total do pedido < regra["valor"];
      - "sku":     libera se algum item tem SKU da lista (exato, casefold);
      - "palavra": libera se o nome de algum item contém um dos termos.
    Loja sem Restrição, UF fora dela ou loja sem exceção válida cadastrada
    ficam inertes (nunca bloqueiam). Regras antigas ainda podem carregar
    "uf" — a chave é IGNORADA (a UF vem da Restrição). Defensivo com dados
    sujos (regra não-dict, valor não-numérico, termos vazios) — regra
    malformada é ignorada, nunca derruba o sweep.
    """
    uf = (uf_destino or "").strip().upper()
    ufs = {
        str(u).strip().upper()
        for u in (ufs_restricao or [])
        if str(u).strip()
    }
    if not uf or uf not in ufs or not regras:
        return None
    validas = 0
    detalhes: list[str] = []
    termos_livres: set[str] = set()
    for regra in regras:
        if not isinstance(regra, dict):
            continue
        tipo = regra.get("tipo")
        if tipo == "valor":
            try:
                limite = float(regra.get("valor"))
            except (TypeError, ValueError):
                continue
            validas += 1
            if valor_total is None or float(valor_total) < limite:
                return None
            detalhes.append(f"valor {_brl(valor_total)} >= {_brl(limite)}")
        elif tipo in ("sku", "palavra"):
            termos = [
                str(t).strip().lower()
                for t in (regra.get("termos") or [])
                if str(t).strip()
            ]
            if not termos:
                continue
            validas += 1
            termos_livres.update(termos)
            if tipo == "sku":
                alvo = set(termos)
                casou = any(
                    sku and sku.strip().lower() in alvo for sku, _ in itens
                )
            else:
                casou = any(
                    nome and any(t in nome.lower() for t in termos)
                    for _, nome in itens
                )
            if casou:
                return None
    if not validas:
        return None
    if termos_livres:
        detalhes.append(
            "nenhum item na exceção (" + ", ".join(sorted(termos_livres)) + ")"
        )
    motivo = f"Restrição da loja: não envia pro {uf}"
    if detalhes:
        motivo += " — " + "; ".join(detalhes)
    return motivo


async def _pedidos_restricao_loja(
    session, numeros: list[str]
) -> dict[str, str]:
    """Pedidos bloqueados pela restrição de UF da loja (store_info).

    Só olha pedidos de loja COM exceções cadastradas (filtro no WHERE — loja
    com Restrição e sem exceção nenhuma fica inerte); a avaliação em si é da
    `avaliar_restricao_loja` (pura). `valorbase` = valor total do pedido,
    replicado em todas as linhas do mesmo bling_id. Retorna numero → motivo.
    """
    if not numeros:
        return {}
    rows = (
        await session.execute(
            select(
                BlingOrder.numero,
                BlingOrder.uf_destino,
                BlingOrder.valorbase,
                BlingOrder.item_codigo,
                BlingOrder.item_descricao,
                StoreInfo.excecoes,
                StoreInfo.uf_restrictions,
            )
            .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
            .where(
                BlingOrder.numero.in_(numeros),
                StoreInfo.excecoes.is_not(None),
            )
            .order_by(BlingOrder.numero, BlingOrder.item_index)
        )
    ).all()
    por_pedido: dict[str, dict] = {}
    for numero, uf, valor, sku, descricao, regras, ufs_restricao in rows:
        info = por_pedido.setdefault(
            numero,
            {
                "uf": uf,
                "valor": valor,
                "regras": regras,
                "ufs_restricao": ufs_restricao,
                "itens": [],
            },
        )
        info["itens"].append((sku, descricao))
    bloqueio: dict[str, str] = {}
    for numero, info in por_pedido.items():
        motivo = avaliar_restricao_loja(
            info["regras"] or [],
            uf_destino=info["uf"],
            ufs_restricao=info["ufs_restricao"],
            valor_total=info["valor"],
            itens=info["itens"],
        )
        if motivo:
            bloqueio[numero] = motivo
    return bloqueio


async def _candidatos_trava_ml(session) -> list[str]:
    """Pedidos ML "Em aberto" (janela de 7 dias) de loja ativa com Restrição
    e Exceções, ainda sem status_faturamento e fora da fila. Quem decide se
    barra é a `_pedidos_restricao_loja` (UF + exceções)."""
    cutoff = datetime.now(UTC) - _CANDIDATE_WINDOW
    numeros = (
        await session.execute(
            select(BlingOrder.numero)
            .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
            .where(
                BlingOrder.situacao == _SITUACAO_EM_ABERTO,
                BlingOrder.item_index == 0,
                BlingOrder.numero.is_not(None),
                BlingOrder.data >= cutoff,
                func.lower(StoreInfo.platform).in_(_TRAVA_ML_PLATAFORMAS),
                StoreInfo.archived_at.is_(None),
                StoreInfo.excecoes.is_not(None),
                StoreInfo.uf_restrictions.is_not(None),
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
    return [n for n in numeros if n not in ja_tratados and n not in em_fila]


async def _travar_ml(session) -> int:
    """Passo da trava do ML (ver `_TRAVA_ML_PLATAFORMAS`). Devolve quantos
    pedidos foram pra Aguardando Cancelamento nesta passada.

    Mesma mecânica da restrição por loja do sweep: obs "restrição" (GET→PUT)
    ANTES do PATCH 83955. Só marca 'restricao' no painel quem o Bling ACEITOU
    (o espelho na bling_orders só muda no sucesso): se o PATCH falhar, o
    pedido segue "Em aberto" e a próxima passada tenta de novo — no ML
    ninguém mais segura o pedido, então não dá pra desistir na 1ª falha. A
    obs não duplica (linha idêntica do dia é idempotente).
    """
    from app.routers.nf import (
        _SITUACAO_AGUARDANDO_CANCELAMENTO,
        _marcar_aguardando_cancelamento,
        _marcar_faturamento,
    )

    numeros = await _candidatos_trava_ml(session)
    bloqueio = await _pedidos_restricao_loja(session, numeros) if numeros else {}
    if not bloqueio:
        return 0
    bloqueio = dict(list(bloqueio.items())[:_MAX_POR_TICK])
    await _escrever_observacao_restricao(session, list(bloqueio))
    await _marcar_aguardando_cancelamento(session, list(bloqueio))
    travados = set(
        (
            await session.execute(
                select(BlingOrder.numero).where(
                    BlingOrder.numero.in_(list(bloqueio)),
                    BlingOrder.situacao == str(_SITUACAO_AGUARDANDO_CANCELAMENTO),
                )
            )
        ).scalars().all()
    )
    for numero, motivo in bloqueio.items():
        if numero in travados:
            await _marcar_faturamento(
                session, [numero], status_txt="restricao", erro=motivo
            )
    falharam = sorted(set(bloqueio) - travados)
    if falharam:
        logger.warning("nf_auto_enfileirar_trava_ml_pendente", pedidos=falharam)
    if travados:
        logger.info(
            "nf_auto_enfileirar_restricao_ml",
            pedidos={n: m for n, m in bloqueio.items() if n in travados},
        )
    return len(travados)


def observacao_sem_estoque(skus: list[str]) -> str:
    """Recado nas Observações do pedido segurado por falta de estoque (Vinicius
    13/10: "faz o sem estoque escrever também") — mesmo texto do badge."""
    return "Sem estoque — saldo negativo: " + ", ".join(skus)


async def escrever_observacoes(session, textos: dict[str, str], *, evento: str) -> None:
    """Escreve `textos[numero]` nas Observações do pedido no Bling (linha
    datada no topo, sem apagar o que já estava).

    A API v3 não tem endpoint de Ocorrência (404 nos caminhos candidatos) —
    o usuário escolheu as Observações. Reusa o round-trip já validado da
    Mensagem Bling (GET → compose → sanitiza → PUT). BEST-EFFORT por pedido:
    falha só loga (`<evento>_falhou`), nunca segura o bloqueio (a situação muda
    mesmo assim). Chamar ANTES do PATCH de situação — o PUT leva o body
    inteiro e um GET stale reverteria o 83955.
    """
    from app.services.logistica_bling import (
        build_observacoes_put_body,
        compose_observacoes,
    )

    numeros = list(textos)
    if not numeros:
        return
    client = await nf_emissao_gerar._bling_client_opt(session)
    if client is None:
        logger.warning(f"{evento}_sem_bling", pedidos=numeros)
        return
    bling_ids = dict(
        (
            await session.execute(
                select(BlingOrder.numero, func.max(BlingOrder.bling_id))
                .where(BlingOrder.numero.in_(numeros))
                .group_by(BlingOrder.numero)
            )
        ).all()
    )
    for numero in numeros:
        bling_id = bling_ids.get(numero)
        if not bling_id:
            logger.warning(f"{evento}_sem_bling_id", pedido=numero)
            continue
        try:
            order = await client.get_order(int(bling_id))
            novo = compose_observacoes(order.get("observacoes"), textos[numero])
            if novo != (order.get("observacoes") or ""):
                body = build_observacoes_put_body(order, novo)
                await client.update_order(int(bling_id), body)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{evento}_falhou", pedido=numero, erro=str(exc))


async def _escrever_observacao_restricao(
    session, numeros: list[str]
) -> None:
    """Escreve "restrição" nas Observações do pedido no Bling."""
    await escrever_observacoes(
        session,
        dict.fromkeys(numeros, _RESTRICAO_OBSERVACAO),
        evento="nf_restricao_observacao",
    )


async def _candidatos(session, *, limite: int | None = _MAX_POR_TICK) -> list[str]:
    """Pedidos elegíveis pro sweep, já deduplicados contra fila e histórico.

    Elegível = "Em aberto" (6), loja ativa COM faturador atribuído, dentro
    da janela de 7 dias, e a LOJA emitindo neste instante (`loja_emite_agora`
    — horário da tela Lojas). ML/Amazon ainda dependem da flag mestre
    `nf_auto_ml_amazon`. `item_index==0` porque bling_orders tem uma linha
    por item. Dedupe duplo: comando ativo na fila OU qualquer
    status_faturamento já registrado (tentativa única). `limite=None` = todos
    (sem o teto do tick).
    """
    cutoff = datetime.now(UTC) - _CANDIDATE_WINDOW
    # Flags: sem nenhuma, o sweep segue só com Shopee/TikTok; `nf_auto_ml`
    # soma o ML; `nf_auto_ml_amazon` (mestre) soma ML e Amazon.
    cfg = get_settings()
    plataformas = _plataformas_ativas(
        ml_amazon=bool(cfg.nf_auto_ml_amazon), so_ml=bool(getattr(cfg, "nf_auto_ml", False))
    )
    rows = (
        await session.execute(
            select(
                BlingOrder.numero,
                func.lower(StoreInfo.platform),
                func.lower(func.coalesce(NfImpressao.tipo, "")),
                StoreInfo.etiqueta_horarios,
                StoreInfo.etiqueta_sabado_horario,
                StoreInfo.etiqueta_sabado_tags,
            )
            .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
            .join(
                NfImpressao,
                NfImpressao.id == StoreInfo.nf_impressao_id,
                isouter=True,
            )
            .where(
                BlingOrder.situacao == _SITUACAO_EM_ABERTO,
                BlingOrder.item_index == 0,
                BlingOrder.numero.is_not(None),
                BlingOrder.loja.is_not(None),
                BlingOrder.data >= cutoff,
                func.lower(StoreInfo.platform).in_(plataformas),
                StoreInfo.archived_at.is_(None),
                StoreInfo.nf_faturador_id.is_not(None),
            )
            .distinct()
            .order_by(BlingOrder.numero)
        )
    ).all()

    agora = _agora_brt()
    numeros: list[str] = []
    # Sábado da agência: guarda os estoques marcados da loja pra filtrar os
    # itens depois (numero → tags).
    sabado: dict[str, set[str]] = {}
    for numero, plataforma, impressao, horarios, sab_hora, sab_tags in rows:
        if not loja_emite_agora(
            plataforma=plataforma,
            impressao=impressao,
            etiqueta_horarios=horarios,
            sabado_horario=sab_hora,
            agora=agora,
        ):
            continue
        numeros.append(numero)
        if (
            plataforma == "ml"
            and impressao != "correios"
            and agora.weekday() == _DIA_SABADO
        ):
            sabado[numero] = tags_de(sab_tags)
    if sabado:
        numeros = await _filtra_estoques_do_sabado(session, numeros, sabado)
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
    ][:limite]


async def _filtra_estoques_do_sabado(
    session, numeros: list[str], sabado: dict[str, set[str]]
) -> list[str]:
    """Tira do sábado da agência os pedidos fora dos estoques marcados.

    `sabado` é numero → tags da loja; pedido que não está no dict (correios,
    outra plataforma, dia útil) passa direto.
    """
    rows = (
        await session.execute(
            select(BlingOrder.numero, BlingOrder.item_codigo).where(
                BlingOrder.numero.in_(list(sabado))
            )
        )
    ).all()
    skus: dict[str, list[str | None]] = {}
    for numero, sku in rows:
        skus.setdefault(numero, []).append(sku)
    manter: list[str] = []
    for numero in numeros:
        tags = sabado.get(numero)
        if tags is None or pedido_sai_no_sabado(skus.get(numero, []), tags):
            manter.append(numero)
    return manter
