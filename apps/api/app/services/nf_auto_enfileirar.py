"""Auto-enfileirador de NF (sweep) — pedidos Shopee/TikTok/ML/Amazon "Em aberto".

Faz sozinho o que o botão "Enfileirar" do Painel Faturamento faz: a cada tick
varre os pedidos com situacao=6 ("Em aberto") de lojas com faturador
atribuído — Shopee/TikTok/Amazon contínuos; ML segue o HORÁRIO da tela Lojas
(correios contínuo, agência nos horários da loja, sábado uma vez só e domingo
nunca — ver `loja_emite_agora`) —, confere o ESTOQUE antes de tudo (kit
confere o saldo do próprio SKU do kit; saldo virtual negativo = peça não
existe) e:

  - restrição    → Shopee + produto Apple (pelo nome) + destino RJ não é
                   enviado: "restrição" nas Observações do pedido no Bling +
                   Aguardando Cancelamento + status 'restricao';
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
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, or_, select, text

from app.config import get_settings
from app.db import session_scope
from app.models import BlingOrder, NfCommand, NfFaturamento, NfImpressao, StoreInfo
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
# manda (ver `loja_emite_agora`).
_PLATAFORMAS_JANELA: tuple[str, ...] = ("ml", "amazon")
_TZ_BRT = ZoneInfo("America/Sao_Paulo")

_DIA_SABADO = 5
_DIA_DOMINGO = 6


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

async def run_auto_enfileirar_nf() -> dict:
    """Uma passada do sweep. Devolve um resumo com contadores."""
    summary: dict = {
        "candidatos": 0,
        "enfileirados": 0,
        "comandos": 0,
        "sem_estoque": 0,
        "restricao": 0,
        "restricao_loja": 0,
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
                await aplicar_prioridade_estoque(session, numeros)
            except Exception:  # noqa: BLE001
                logger.exception("nf_auto_enfileirar_prioridade_falhou")

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
                        func.max(StoreInfo.sales_team),
                    )
                    .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
                    .where(BlingOrder.numero.in_(list(sem_estoque)))
                    .group_by(BlingOrder.numero)
                )
            ).all()
        }

        def _texto(pedidos: dict[str, list[str]]) -> str:
            linhas = [
                f"Pedido {numero}"
                + (f" ({lojas[numero]})" if lojas.get(numero) else "")
                + ": " + ", ".join(skus)
                for numero, skus in sorted(pedidos.items())
            ]
            return (
                "Estoque negativo — movido pra Aguardando Cancelamento:\n"
                + "\n".join(linhas)
            )

        client = threema.ThreemaClient()
        result = await client.send_to_all(_texto(sem_estoque), gerais)
        logger.info(
            "nf_auto_enfileirar_threema",
            sent=result.get("sent"),
            failed=result.get("failed"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("nf_sem_estoque_threema_falhou", erro=str(exc))


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


async def _escrever_observacao_restricao(
    session, numeros: list[str]
) -> None:
    """Escreve "restrição" nas Observações do pedido no Bling.

    A API v3 não tem endpoint de Ocorrência (404 nos caminhos candidatos) —
    o usuário escolheu as Observações. Reusa o round-trip já validado da
    Mensagem Bling (GET → compose → sanitiza → PUT). BEST-EFFORT por pedido:
    falha só loga, nunca segura o bloqueio (a situação muda mesmo assim).
    Chamar ANTES do PATCH de situação — o PUT leva o body inteiro e um GET
    stale reverteria o 83955.
    """
    from app.services.logistica_bling import (
        build_observacoes_put_body,
        compose_observacoes,
    )

    client = await nf_emissao_gerar._bling_client_opt(session)
    if client is None:
        logger.warning("nf_restricao_observacao_sem_bling", pedidos=numeros)
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
            logger.warning("nf_restricao_observacao_sem_bling_id", pedido=numero)
            continue
        try:
            order = await client.get_order(int(bling_id))
            novo = compose_observacoes(
                order.get("observacoes"), _RESTRICAO_OBSERVACAO
            )
            if novo != (order.get("observacoes") or ""):
                body = build_observacoes_put_body(order, novo)
                await client.update_order(int(bling_id), body)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "nf_restricao_observacao_falhou", pedido=numero, erro=str(exc)
            )


async def _candidatos(session) -> list[str]:
    """Pedidos elegíveis pro sweep, já deduplicados contra fila e histórico.

    Elegível = "Em aberto" (6), loja ativa COM faturador atribuído, dentro
    da janela de 7 dias, e a LOJA emitindo neste instante (`loja_emite_agora`
    — horário da tela Lojas). ML/Amazon ainda dependem da flag mestre
    `nf_auto_ml_amazon`. `item_index==0` porque bling_orders tem uma linha
    por item. Dedupe duplo: comando ativo na fila OU qualquer
    status_faturamento já registrado (tentativa única).
    """
    cutoff = datetime.now(UTC) - _CANDIDATE_WINDOW
    # Flag mestre: com ela desligada o sweep segue só com Shopee/TikTok.
    plataformas = (
        _PLATAFORMAS
        if get_settings().nf_auto_ml_amazon
        else tuple(p for p in _PLATAFORMAS if p not in _PLATAFORMAS_JANELA)
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
    ][:_MAX_POR_TICK]


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
