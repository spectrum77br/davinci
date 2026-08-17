"""Auto-enfileirador de NF (sweep) — pedidos Shopee/TikTok/ML/Amazon "Em aberto".

Faz sozinho o que o botão "Enfileirar" do Painel Faturamento faz: a cada tick
varre os pedidos com situacao=6 ("Em aberto") de lojas com faturador
atribuído — Shopee/TikTok contínuos; ML/Amazon SÓ nas janelas das 10h e das
14h BRT —, confere o ESTOQUE antes de tudo (regra do usuário: kit
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
_PLATAFORMAS: tuple[str, ...] = ("shopee", "tiktok", "ml", "amazon")

# ML e Amazon NÃO faturam o tempo todo: só nas janelas das 10h e das 14h de
# Brasília (decisão do usuário, 15/08: "ao invés de faturar o tempo todo,
# vamos faturar somente às 10:00 e às 14:00"). Shopee/TikTok seguem
# contínuos a cada tick. Janela = a HORA cheia (10:00–10:59 / 14:00–14:59):
# o tick de 2min dá várias passadas com teto de 30, drenando o acumulado.
# ALÉM da janela, ML/Amazon exigem a flag `nf_auto_ml_amazon` LIGADA
# (usuário: "nao e para ativar mercado livre ainda... vamos testar de noite").
_PLATAFORMAS_JANELA: tuple[str, ...] = ("ml", "amazon")
_HORAS_JANELA: tuple[int, ...] = (10, 14)
_TZ_BRT = ZoneInfo("America/Sao_Paulo")


def _hora_brt() -> int:
    """Hora atual em Brasília (função separada pra teste monkeypatchar)."""
    return datetime.now(_TZ_BRT).hour

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
        "excecao": 0,
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

        # 1b) Exceções POR LOJA (campo "Exceções" da tela Lojas, store_info.
        #     excecoes) — regras de valor/sku/palavra que valem pras UFs do
        #     campo "Restrição" (store_info.uf_restrictions) da loja,
        #     ADICIONAIS à restrição hardcoded acima. Mesma mecânica: obs
        #     "restrição" (GET→PUT) ANTES do PATCH 83955.
        excecao = await _pedidos_excecao(session, numeros) if numeros else {}
        if excecao:
            await _escrever_observacao_restricao(session, list(excecao))
            await _marcar_aguardando_cancelamento(session, list(excecao))
            for numero, motivos in excecao.items():
                await _marcar_faturamento(
                    session,
                    [numero],
                    status_txt="restricao",
                    erro="Exceção da loja: " + "; ".join(motivos),
                )
            summary["excecao"] = len(excecao)
            logger.info(
                "nf_auto_enfileirar_excecao",
                pedidos={n: motivos for n, motivos in excecao.items()},
            )
        numeros = [n for n in numeros if n not in excecao]

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
        # Rótulo = "plataforma conta" (ex. "shopee vortan") — só o nome da
        # conta não diz de qual marketplace o pedido veio.
        lojas = {
            numero: " ".join(p for p in (plataforma, conta) if p)
            for numero, plataforma, conta in (
                await session.execute(
                    select(
                        BlingOrder.numero,
                        func.max(func.lower(StoreInfo.platform)),
                        func.max(StoreInfo.account_name),
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


def avaliar_excecoes(
    regras: list[dict],
    *,
    uf_destino: str | None,
    ufs_restricao: list[str] | None,
    valor_total,
    itens: list[tuple[str | None, str | None]],
) -> list[str]:
    """Motivos das regras de exceção da loja que o pedido casa. [] = passa.

    `regras` = store_info.excecoes (lista JSONB, ver schemas.StoreExcecao);
    `ufs_restricao` = store_info.uf_restrictions (campo "Restrição" da tela
    Lojas) — as regras só valem quando o destino do pedido está nessas UFs
    (loja sem Restrição = exceção nunca casa);
    `itens` = [(sku, nome), ...] de TODOS os itens do pedido. Tipos:
      - "valor":   bloqueia se o valor total do pedido >= regra["valor"];
      - "sku":     bloqueia se algum item tem SKU da lista (exato, casefold);
      - "palavra": bloqueia se o nome de algum item contém um dos termos.
    Regras antigas ainda podem carregar "uf" — a chave é IGNORADA (a UF vem
    da Restrição). Defensivo com dados sujos (regra não-dict, valor
    não-numérico, termos vazios) — regra malformada é ignorada, nunca
    derruba o sweep.
    """
    uf = (uf_destino or "").strip().upper()
    ufs = {
        str(u).strip().upper()
        for u in (ufs_restricao or [])
        if str(u).strip()
    }
    if not uf or uf not in ufs or not regras:
        return []
    motivos: list[str] = []
    for regra in regras:
        if not isinstance(regra, dict):
            continue
        tipo = regra.get("tipo")
        if tipo == "valor":
            try:
                limite = float(regra.get("valor"))
            except (TypeError, ValueError):
                continue
            if valor_total is not None and float(valor_total) >= limite:
                motivos.append(
                    f"valor {_brl(valor_total)} >= {_brl(limite)} pra {uf}"
                )
        elif tipo == "sku":
            termos = {
                str(t).strip().lower()
                for t in (regra.get("termos") or [])
                if str(t).strip()
            }
            casados = sorted(
                {
                    sku
                    for sku, _ in itens
                    if sku and sku.strip().lower() in termos
                }
            )
            if casados:
                motivos.append(
                    f"SKU bloqueado pra {uf}: " + ", ".join(casados)
                )
        elif tipo == "palavra":
            termos = [
                str(t).strip().lower()
                for t in (regra.get("termos") or [])
                if str(t).strip()
            ]
            casados: list[str] = []
            for _, nome in itens:
                if not nome or nome in casados:
                    continue
                baixo = nome.lower()
                if any(t in baixo for t in termos):
                    casados.append(nome)
            if casados:
                motivos.append(
                    f"palavra bloqueada pra {uf}: " + ", ".join(casados)
                )
    return motivos


async def _pedidos_excecao(
    session, numeros: list[str]
) -> dict[str, list[str]]:
    """Pedidos que caem numa exceção configurada na loja (store_info.excecoes).

    Só olha pedidos de loja COM exceções (filtro no WHERE); a avaliação em si
    é da `avaliar_excecoes` (pura). `valorbase` = valor total do pedido,
    replicado em todas as linhas do mesmo bling_id. Retorna numero → motivos.
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
    excecao: dict[str, list[str]] = {}
    for numero, info in por_pedido.items():
        motivos = avaliar_excecoes(
            info["regras"] or [],
            uf_destino=info["uf"],
            ufs_restricao=info["ufs_restricao"],
            valor_total=info["valor"],
            itens=info["itens"],
        )
        if motivos:
            excecao[numero] = motivos
    return excecao


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
    da janela de 7 dias; Shopee/TikTok sempre, ML/Amazon só com a flag
    nf_auto_ml_amazon ligada E nas horas da janela (10h/14h BRT).
    `item_index==0` porque
    bling_orders tem uma linha por item. Dedupe duplo: comando ativo na fila
    OU qualquer status_faturamento já registrado (tentativa única).
    """
    cutoff = datetime.now(UTC) - _CANDIDATE_WINDOW
    # ML/Amazon só entram com a flag nf_auto_ml_amazon LIGADA e dentro das
    # janelas das 10h e das 14h BRT; fora disso o sweep segue só com
    # Shopee/TikTok (contínuos).
    plataformas = (
        _PLATAFORMAS
        if get_settings().nf_auto_ml_amazon and _hora_brt() in _HORAS_JANELA
        else tuple(p for p in _PLATAFORMAS if p not in _PLATAFORMAS_JANELA)
    )
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
                func.lower(StoreInfo.platform).in_(plataformas),
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
