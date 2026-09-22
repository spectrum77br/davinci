"""Robô de prioridade de estoque — troca o SKU do pedido pra tag prioritária.

Eduardo (2026-08-27), Tabela de Preços → Produtos, coluna Prioridade:
"antes de validar margem, sistema verifica na tabela prioridades estoque do
produto em outro tag, por exemplo dg53.ci, e dg053.sp, a venda saiu para .ci
[...] mas a prioridade para aquele produto esta .sp ja troca para esse
estoque" / "a tag que eu colocar la, o sku com a tag, ja deve trocar, porque
a prioridade e ele".

Regra ABSOLUTA: se o produto (base do SKU, ex. dg053) tem prioridade_estoque
na Tabela de Preços e o item do pedido saiu com OUTRA tag, troca — kit
inteiro (dg053.ci+a001.ci → dg053.sp+a001.sp). GUARDA: só troca se o SKU
alvo EXISTE ativo no Bling e o saldo VIRTUAL cobre a quantidade do item;
senão não mexe em NADA (o fluxo sem_estoque existente segue decidindo).

Onde roda (sempre ANTES do check de estoque / emissão de NF):
  - sweep próprio (cron do worker, ~10min) — pega o pedido logo que cai;
  - nf_auto_enfileirar (sweep automático de NF);
  - botão manual "Enfileirar" do Painel Faturamento (routers/nf.py).

Só pedidos "Em aberto" (situacao 6) são tocados — quem já anda pela esteira
de NF nunca é alterado. O PUT do Bling revalida a venda inteira (caso
291676: erro 67); QUALQUER falha no PUT = loga e pula, sem efeito local.
KITS (Eduardo, 14/09: "continua tirando estoque do saldo de ra ao invés
de tirar do f105 de sp"): o Bling aceita a troca do item, mas na baixa da NF
continua descontando a composição ANTIGA do kit — comprovado no extrato de
estoque (dia 13: 18 trocas de kit dg053 → 30 baixas em dg053.ci e 6 em .sp);
produto simples ele baixa certo. Então, pra kit, o robô compensa na hora da
troca com POST /estoques (mesmo mecanismo da correção de estoque das
Devoluções): ENTRADA em cada componente antigo e SAÍDA em cada componente
novo — ver services/prioridade_estoque_movimentos.py (registro em
transação própria, retry, estorno em cancelamento, aviso Threema). Produto
simples: nenhum movimento.
DESDE 17/09 existe o modo `prioridade_substitui_item` (config): em vez de
EDITAR o item, o PUT SUBSTITUI (item novo, sem `id`) — aí o Bling refaz a
composição, baixa o kit certo e a compensação é desligada; quem confere é
services/prioridade_estoque_conferencia.py, que lê o extrato depois da
etiqueta e só compensa se o Bling tiver baixado o kit velho.
Toda troca vira linha no margem_audit (acao='sku',
origem='prioridade_estoque', mudado_por=None = robô) E linha datada nas
Observações do pedido no Bling ("dd/mm - SKU trocado pela prioridade de
estoque: antigo -> novo", via compose_observacoes — pedido do Eduardo
28/08), no MESMO PUT da troca de item. Idempotente: item já
na tag prioritária é ignorado; espelho local perdido num crash pós-PUT se
auto-corrige no próximo sync do Bling. Commit fica com o caller (mesmo
contrato do record_margem_audit) — o sweep próprio comita via session_scope.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.models import BlingOrder, PricingProduct
from app.services import estoque_familia, nf_emissao_gerar
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.logistica_bling import build_observacoes_put_body, compose_observacoes
from app.services.margem_audit import record_margem_audit
from app.services.prioridade_estoque_movimentos import compensar_estoque_kits
from app.services.sku_tags import SUFFIX_TAGS

logger = structlog.get_logger()

# Advisory lock do sweep próprio (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x7072696F  # ascii "prio"

# Só o "Em aberto" nativo do Bling — é onde o pedido cai ao ser importado.
_SITUACAO_EM_ABERTO = "6"
# Janela do sweep: pedido sai daqui quando muda de situação; 7 dias cobre
# qualquer atraso de fila sem varrer histórico infinito.
_JANELA_DIAS = 7


def _tag_de(pedaco: str) -> str | None:
    """Sufixo de estoque de UM pedaço do SKU (dg053.ci → ci), ou None.

    Mesma regra do classify_sku_tag: último segmento após '.' precisa estar
    em SUFFIX_TAGS — números (b009.8.12.20.24 = tamanhos, uaf001m1.110 =
    voltagem) não casam e ficam de fora.
    """
    if "." in pedaco:
        tail = pedaco.rsplit(".", 1)[1].strip().lower()
        if tail in SUFFIX_TAGS:
            return tail
    return None


def _base_de(pedaco: str) -> str:
    """Pedaço sem o sufixo de tag, lowercase (dg053.CI → dg053)."""
    p = pedaco.strip().lower()
    tag = _tag_de(p)
    return p[: -(len(tag) + 1)] if tag else p


def analisa_codigo(codigo: str | None) -> tuple[str, str] | None:
    """(base_para_lookup, tag_atual) do SKU do item, ou None se não dá pra
    trocar: sem sufixo de tag, kit com tags misturadas (conservador) ou
    SKU fake."""
    low = (codigo or "").strip().lower()
    if not low or low.startswith("fake."):
        return None
    pedacos = [p.strip() for p in low.split("+") if p.strip()]
    tags = {t for t in (_tag_de(p) for p in pedacos) if t}
    if len(tags) != 1:
        return None
    tag = next(iter(tags))
    base = next(_base_de(p) for p in pedacos if _tag_de(p))
    return base, tag


def sku_alvo(codigo: str, tag_atual: str, prioridade: str) -> str:
    """Troca `.{tag_atual}` por `.{prioridade}` em TODOS os pedaços que têm a
    tag, preservando o resto do texto (dg053.ci+a001.ci → dg053.sp+a001.sp;
    pedaço sem tag fica como está)."""
    out: list[str] = []
    for p in codigo.split("+"):
        raw = p.strip()
        if _tag_de(raw) == tag_atual:
            out.append(raw[: -len(tag_atual)] + prioridade)
        else:
            out.append(raw)
    return "+".join(out)


async def _lote_com_saldo(
    client,
    cache: dict,
    *,
    codigo: str,
    tag_atual: str,
    prioridade: str | None,
    qtd: int,
    redirecionar: bool,
) -> tuple[str | None, dict | None]:
    """De qual lote a peça vai sair, de verdade.

    Ordem de preferência, e é a regra que o Eduardo descreveu: primeiro o lote da
    prioridade; se lá não tiver peça, o próprio lote do anúncio; e só então os
    lotes irmãos. Devolve o SKU escolhido e o produto do Bling, ou (None, None)
    se nenhum lote cobre a quantidade.

    O terceiro passo só existe quando a soma por família está ligada para esta
    linha: o anúncio promete o total de todos os lotes, então a venda precisa
    saber sair de qualquer um deles. Sem isso, um anúncio que mostra 36 peças com
    o lote dele em zero derruba o saldo para negativo — foi o que aconteceu com o
    dg057.ci.
    """
    atual = (codigo or "").strip().lower()
    candidatos: list[str] = []
    if prioridade and prioridade != tag_atual:
        candidatos.append(sku_alvo(codigo, tag_atual, prioridade))
    candidatos.append(atual)
    if redirecionar:
        for irmao in estoque_familia.irmaos(codigo):
            if irmao not in candidatos:
                candidatos.append(irmao)

    for alvo in candidatos:
        if alvo not in cache:
            cache[alvo] = await client.find_active_product_by_sku(alvo)
        prod = cache[alvo]
        if not prod or not prod.get("id"):
            continue
        if (prod.get("sku") or "").strip().lower() != alvo.strip().lower():
            continue
        saldo = prod.get("stock")
        if saldo is None or float(saldo) < qtd:
            continue
        return alvo, prod
    return None, None


async def _mapa_prioridades(session: AsyncSession) -> dict[str, str]:
    """base do SKU (sem tag) → tag prioritária, a partir da Tabela de Preços.

    `pricing_products.sku` pode ser lista com vírgulas ("i203,i204,i205") e a
    entrada pode vir com ou sem tag (dg053 / dg053.ci) — normaliza pra base.
    Bases com prioridades CONFLITANTES (linhas de departamentos/usuários
    diferentes discordando) são descartadas com warning — melhor não trocar
    do que trocar pro lado errado.
    """
    rows = (
        await session.execute(
            select(PricingProduct.sku, PricingProduct.prioridade_estoque).where(
                PricingProduct.prioridade_estoque.is_not(None)
            )
        )
    ).all()
    mapa: dict[str, str] = {}
    conflito: set[str] = set()
    for sku_txt, prio in rows:
        if not prio:
            continue
        for entry in (sku_txt or "").split(","):
            entry = entry.strip().lower()
            if not entry:
                continue
            base = _base_de(entry.split("+")[0])
            if not base:
                continue
            if base in mapa and mapa[base] != prio:
                conflito.add(base)
                continue
            mapa[base] = prio
    for base in conflito:
        mapa.pop(base, None)
        logger.warning("prioridade_estoque_conflito", base=base)
    return mapa


def aplicar_trocas_nos_itens(
    itens: list[dict], trocas: list[dict], *, substituir: bool
) -> tuple[list[dict], list[dict]]:
    """Monta a lista de `itens` do PUT e diz quais trocas casaram.

    `substituir=False` (jeito antigo): EDITA o item no lugar, mantendo o `id`.
    O Bling trata isso como "mesmo item" e continua com a composição que
    fotografou quando o pedido entrou — por isso baixa o kit VELHO e o robô
    precisa compensar por fora (e sobra reserva órfã no componente novo).

    `substituir=True`: tira o item antigo e põe um item NOVO, sem `id` — o
    Bling refaz a composição, reserva e baixa o kit certo sozinho. É a
    correção de raiz; a compensação fica desligada e o conferente vigia.

    Copia rasa por item: nunca muda o dict que veio do Bling.
    """
    por_codigo = {t["antigo"].strip().lower(): t for t in trocas}
    novos: list[dict] = []
    aplicadas: list[dict] = []
    for bi in itens:
        t = por_codigo.get((bi.get("codigo") or "").strip().lower())
        if t is None:
            novos.append(bi)
            continue
        item = {k: v for k, v in bi.items() if not (substituir and k == "id")}
        item["codigo"] = t["alvo"]
        item["produto"] = {"id": t["alvo_id"]}
        if t.get("alvo_nome"):
            item["descricao"] = t["alvo_nome"]
        novos.append(item)
        if t not in aplicadas:
            aplicadas.append(t)
    return novos, aplicadas


async def aplicar_prioridade_estoque(
    session: AsyncSession, numeros: list[str] | None = None
) -> dict:
    """Aplica a troca de prioridade nos pedidos Em aberto.

    `numeros=None` = varre a janela dos últimos dias (sweep); lista = só
    esses pedidos (ganchos do enfileirar — o espelho bling_orders é
    atualizado NA MESMA sessão, então o `_pedidos_sem_estoque` logo depois
    já confere o SKU novo). Nunca levanta; commit é do caller.
    """
    summary = {
        "avaliados": 0,
        "trocados": 0,
        "sem_produto_alvo": 0,
        "sem_saldo_alvo": 0,
        "falhas": 0,
        "estoque_movimentos": 0,
        "estoque_falhas": 0,
        "sem_compensacao": 0,
    }
    if numeros is not None and not numeros:
        return summary
    substituir_item = bool(get_settings().prioridade_substitui_item)
    mapa = await _mapa_prioridades(session)
    if not mapa:
        return summary  # ninguém preencheu Prioridade — no-op barato

    q = select(
        BlingOrder.numero,
        BlingOrder.bling_id,
        BlingOrder.item_codigo,
        BlingOrder.item_quantidade,
    ).where(
        BlingOrder.situacao == _SITUACAO_EM_ABERTO,
        BlingOrder.item_codigo.is_not(None),
        BlingOrder.bling_id.is_not(None),
    )
    if numeros is not None:
        q = q.where(BlingOrder.numero.in_(numeros))
    else:
        corte = datetime.now(UTC) - timedelta(days=_JANELA_DIAS)
        q = q.where(BlingOrder.data >= corte)
    rows = (await session.execute(q)).all()

    por_pedido: dict[str, list] = {}
    for r in rows:
        por_pedido.setdefault(r.numero, []).append(r)
    if not por_pedido:
        return summary

    client = await nf_emissao_gerar._bling_client_opt(session)
    if client is None:
        logger.warning("prioridade_estoque_sem_bling")
        return summary

    # Cache alvo → resultado do Bling; o saldo em cache é DECREMENTADO a cada
    # troca planejada pra dois pedidos do mesmo tick não contarem a mesma peça.
    alvo_cache: dict[str, dict | None] = {}

    for numero, itens in por_pedido.items():
        bling_id = itens[0].bling_id
        # Mesmo SKU em mais de uma linha do pedido = soma as quantidades.
        qtd_por_codigo: dict[str, int] = {}
        for it in itens:
            cod = it.item_codigo
            qtd_por_codigo[cod] = qtd_por_codigo.get(cod, 0) + int(
                it.item_quantidade or 1
            )

        trocas: list[dict] = []
        for cod, qtd in qtd_por_codigo.items():
            info = analisa_codigo(cod)
            if not info:
                continue
            base, tag_atual = info
            prio = mapa.get(base)
            # Com a soma por família ligada nesta linha, o robô também pode
            # redirecionar a venda para um lote irmão — inclusive quando o item
            # JÁ está no lote da prioridade, mas esse lote está vazio.
            redirecionar = bool(
                get_settings().estoque_familia_redireciona
            ) and estoque_familia.familia_ligada(cod)
            if not redirecionar and (not prio or prio == tag_atual):
                continue
            summary["avaliados"] += 1
            alvo, prod = await _lote_com_saldo(
                client,
                alvo_cache,
                codigo=cod,
                tag_atual=tag_atual,
                prioridade=prio,
                qtd=qtd,
                redirecionar=redirecionar,
            )
            if alvo is None:
                summary["sem_saldo_alvo"] += 1
                logger.info(
                    "prioridade_estoque_sem_saldo_alvo",
                    pedido=numero,
                    de=cod,
                    prioridade=prio,
                    qtd=qtd,
                    redirecionar=redirecionar,
                )
                continue
            if alvo.strip().lower() == (cod or "").strip().lower():
                # Já está no lote que tem a peça: nada a fazer.
                summary["ja_no_lote_certo"] = summary.get("ja_no_lote_certo", 0) + 1
                continue
            if prio and alvo.strip().lower() != sku_alvo(
                cod, tag_atual, prio
            ).strip().lower():
                summary["redirecionados"] = summary.get("redirecionados", 0) + 1
                logger.info(
                    "prioridade_estoque_redirecionado",
                    pedido=numero,
                    de=cod,
                    para=alvo,
                    prioridade=prio,
                    motivo="lote da prioridade sem saldo",
                )
            prod["stock"] = float(prod["stock"]) - qtd
            trocas.append(
                {
                    "antigo": cod,
                    "alvo": alvo,
                    "alvo_id": int(prod["id"]),
                    "alvo_nome": prod.get("name"),
                    "qtd": qtd,
                }
            )

        if not trocas:
            continue

        try:
            order = await client.get_order(int(bling_id))
            body = build_observacoes_put_body(order, order.get("observacoes") or "")
            body["itens"], aplicadas = aplicar_trocas_nos_itens(
                body.get("itens") or [], trocas, substituir=substituir_item
            )
            if not aplicadas:
                # Espelho local não bate com o Bling — não arrisca o PUT.
                continue
            # Registro humano da troca nas Observações do pedido — pedido do
            # Eduardo (28/08): "quando alterar, você tem que adicionar no
            # campo observação". compose_observacoes põe "dd/mm - " na frente
            # e não duplica a mesma linha no mesmo dia.
            nota = "; ".join(f"{t['antigo']} -> {t['alvo']}" for t in aplicadas)
            body["observacoes"] = compose_observacoes(
                order.get("observacoes"),
                f"SKU trocado pela prioridade de estoque: {nota}",
            )
            await client.update_order(int(bling_id), body)
        except Exception as exc:  # noqa: BLE001 — PUT revalida a venda inteira
            summary["falhas"] += 1
            logger.warning(
                "prioridade_estoque_put_falhou",
                pedido=numero,
                erro=str(exc),
            )
            continue

        kits = [(t["antigo"], t["alvo"], int(t["qtd"])) for t in aplicadas if "+" in t["antigo"]]
        if kits and substituir_item:
            # Item novo = composição nova: o próprio Bling baixa o kit certo.
            # Compensar aqui baixaria DUAS vezes. O conferente
            # (prioridade_estoque_conferencia) olha o extrato depois da
            # etiqueta e compensa só se o Bling tiver baixado o kit velho.
            summary["sem_compensacao"] += 1
            kits = []
        if kits:
            # Um plano por pedido: kits que compartilham componente somam.
            mov = await compensar_estoque_kits(
                client, numero=numero, bling_id=int(bling_id), trocas=kits, cache=alvo_cache,
            )
            summary["estoque_movimentos"] += mov["ok"]
            summary["estoque_falhas"] += mov["falhas"]

        for t in aplicadas:
            valores: dict = {
                "item_codigo": t["alvo"],
                "item_produto_id": t["alvo_id"],
            }
            if t.get("alvo_nome"):
                valores["item_descricao"] = t["alvo_nome"]
            await session.execute(
                update(BlingOrder)
                .where(
                    BlingOrder.numero == numero,
                    BlingOrder.bling_id == int(bling_id),
                    BlingOrder.item_codigo == t["antigo"],
                )
                .values(**valores)
            )
            await record_margem_audit(
                session,
                acao="sku",
                pedido_bling=numero,
                bling_id=bling_id,
                sku=t["antigo"],
                valor_antigo=t["antigo"],
                valor_novo=t["alvo"],
                origem="prioridade_estoque",
                mudado_por=None,
            )
            summary["trocados"] += 1
            logger.info(
                "prioridade_estoque_trocado",
                pedido=numero,
                de=t["antigo"],
                para=t["alvo"],
            )

    return summary


async def prioridade_estoque_sweep() -> dict:
    """Sweep próprio (cron do worker): sessão e commit próprios, serializado
    por advisory lock transacional — dois workers nunca varrem juntos."""
    async with session_scope() as session:
        got = (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        if not got:
            return {"skipped": "lock_busy"}
        return await aplicar_prioridade_estoque(session)
