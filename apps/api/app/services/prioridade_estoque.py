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
PEDIDO NUM ESTOQUE SÓ (Vinicius, 23/09/2026 — pedido 298787 caiu com
dg054.ci+a001.ci e dg052.sp+a001.sp): antes de decidir item por item, o robô
olha o pedido inteiro e põe TODOS os itens no mesmo estoque, desde que esse
estoque tenha tudo. "Quando sp ou ci tem tudo ganha quem tiver na prioridade,
se nenhum item tem prioridade cadastrada ganha quem tem mais estoque";
"quando nenhum estoque tem tudo deixa como está" (= a regra de sempre, item a
item). Ver `_plano_estoque_unico`. Liga/desliga por
PRIORIDADE_PEDIDO_ESTOQUE_UNICO (ligado por padrão).
PEDIDO FLEX (projeto Flex, 02/10/2026 — procedimento-flex.md e
relatorios/Flex_analise_02-10-2026.md, críticas C1 e "problema 2"): o pedido
que sai pelo Flex (ML Envios Flex / Shopee Entrega Direta, registrado em
`flex_pedido` pelo shipment check ou marcado `envio_flex` na Logística) sai
FISICAMENTE de São Bernardo. Por isso a regra dele é OBRIGATÓRIA e vem antes
de todas: cada item vai para o lote .sp da mesma base (`sku_alvo(..., "sp")`),
sem olhar o mapa de prioridades, a trava anti-volta nem o pedido num estoque
só. Se o .sp não cobre (saldo virtual do Bling na hora, mesma régua de
`_lote_com_saldo`), o robô NÃO leva o pedido para outro lote: grava o
`flex_pedido.alerta`, a trilha em `flex_log` e um aviso no sino dos admins —
a decisão (separar em SP, transferir peça, cancelar) é de uma pessoa. Ver
`_decidir_flex`. Liga/desliga por FLEX_PEDIDO_NO_SP (ligado por padrão).
Revisão de 02/10/2026: (1) os pedidos Flex da rodada vêm ANTES de todos os
normais — um normal mais antigo com prioridade .sp não leva a última peça de
São Bernardo; (2) o .sp que um pedido Flex sem peça precisa fica segurado
para os normais (`_reserva_flex`); (3) pedido ML/Shopee recém-chegado sem o
tipo de envio lido espera (`_esperando_tipo_envio`, até
`flex_espera_envio_min`) — sem isso a NF automática o pegava como normal;
(4) pedido Flex reconhecido depois de entrar na fila da NF não é trocado
(aviso para uma pessoa).
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

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.models import (
    AlertSeverity,
    AlertType,
    BlingOrder,
    FlexLog,
    FlexPedido,
    Logistica,
    MargemAudit,
    NfFaturamento,
    PricingProduct,
    Product,
    StoreInfo,
    User,
    UserRole,
)
from app.services import (
    estoque_familia,
    flex_config,
    flex_envio,
    flex_local,
    nf_emissao_gerar,
)
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.alerts import emit_alert
from app.services.logistica_bling import build_observacoes_put_body, compose_observacoes
from app.services.margem_audit import record_margem_audit
from app.services.marketplaces.bling import BlingCloudflareError
from app.services.prioridade_estoque_movimentos import compensar_estoque_kits, pecas_que_mudam
from app.services.sku_tags import SUFFIX_TAGS

logger = structlog.get_logger()

# Advisory lock do sweep próprio (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x7072696F  # ascii "prio"

# Só o "Em aberto" nativo do Bling — é onde o pedido cai ao ser importado.
_SITUACAO_EM_ABERTO = "6"
# Janela do sweep: pedido sai daqui quando muda de situação; 7 dias cobre
# qualquer atraso de fila sem varrer histórico infinito.
_JANELA_DIAS = 7


# Vai-e-volta (auditoria de 28/09/2026: 2.517 trocas em 7 dias, ~92% desfeitas
# minutos depois; o 298543 trocou 486 vezes num dia). Três travas:
#   1. o lote ATUAL do item cobre com saldo virtual >= 0 — o Bling já desconta a
#      reserva do próprio pedido, então 0 é "só tem a peça deste pedido", não
#      "vazio" (mesma regra do `_cobertura`);
#   2. consulta ao Bling que FALHA (429 da madrugada, timeout) é "não sei", não
#      "sem peça": o pedido fica como está nesta rodada;
#   3. o robô não desfaz, dentro de `_JANELA_ANTI_VOLTA`, uma troca que ele
#      mesmo fez — rede de segurança para qualquer causa que sobrar.
_JANELA_ANTI_VOLTA = timedelta(minutes=60)
# Marca, no cache da rodada, a consulta que falhou (não repete o GET nesta
# rodada — em 429 isso só pioraria). Comparado por identidade.
_FALHOU: dict = {"__consulta_falhou__": True}


class _ConsultaFalhouError(Exception):
    """O Bling não respondeu sobre um SKU — decisão fica para a próxima rodada."""


def _chave(sku: str) -> str:
    """Chave do cache da rodada: sem caixa e sem espaço nas pontas — 'DG057.ci'
    e 'dg057.ci' são o mesmo produto e não podem ter dois saldos."""
    return (sku or "").strip().lower()


async def _buscar(client, cache: dict, sku: str) -> dict | None:
    """Produto do Bling pelo SKU, com cache da rodada. None = não existe;
    `_ConsultaFalhouError` = não deu para saber."""
    chave = _chave(sku)
    if chave not in cache:
        try:
            cache[chave] = await client.find_active_product_by_sku(sku, estrito=True)
        except Exception as exc:  # noqa: BLE001 — 429/timeout/5xx
            cache[chave] = _FALHOU
            logger.info("prioridade_estoque_consulta_falhou", sku=sku, erro=str(exc)[:200])
    prod = cache[chave]
    if prod is _FALHOU:
        raise _ConsultaFalhouError(sku)
    return prod


def _esquecer(cache: dict, skus: list[str]) -> None:
    """Depois de uma troca feita no Bling: tira do cache os SKUs envolvidos e
    todo kit que usa alguma das mesmas peças. O Bling já moveu a reserva, então
    o próximo pedido da rodada consulta o saldo REAL — em vez de um número que
    não sabe que o pedido saiu de um lote (os outros pedidos daquele lote
    fugiriam juntos) ou que a peça (o fone a001) foi usada por outro kit."""
    chaves = {_chave(s) for s in skus}
    pecas = {p.strip() for c in chaves for p in c.split("+") if p.strip()}
    for chave in list(cache):
        if not isinstance(chave, str) or chave.startswith("prod:"):
            continue  # ids da compensação (prioridade_estoque_movimentos)
        if chave in chaves or pecas & {p.strip() for p in chave.split("+")}:
            del cache[chave]


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
    existentes: set[str] | None = None,
    reserva: dict[str, int] | None = None,
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

    O lote ATUAL cobre com saldo >= 0 (o virtual já desconta a reserva deste
    pedido); lote para onde o item MUDA precisa de saldo >= quantidade. Se a
    consulta de um lote que vem antes na ordem falhar, levanta
    `_ConsultaFalhouError` — sem saber se a prioridade tem peça, não troca nada.

    `reserva`: peças do .sp seguradas para pedidos Flex sem peça (ver
    `_reserva_flex`) — o lote para onde o item MUDA precisa cobrir a
    quantidade E o que está segurado. O lote atual não muda de régua.
    """
    atual = (codigo or "").strip().lower()

    def _existe(sku: str) -> bool:
        # Irmão sem produto ativo no DaVinci não vale a consulta ao Bling (com a
        # soma para todas as linhas, eram até 4 consultas por item a lotes que
        # não existem). O lote ATUAL e o da PRIORIDADE são sempre consultados.
        return existentes is None or _chave(sku) in existentes

    candidatos: list[str] = []
    if prioridade and prioridade != tag_atual:
        candidatos.append(sku_alvo(codigo, tag_atual, prioridade))
    candidatos.append(atual)
    if redirecionar:
        for irmao in estoque_familia.irmaos(codigo):
            if irmao not in candidatos and _existe(irmao):
                candidatos.append(irmao)

    for alvo in candidatos:
        prod = await _buscar(client, cache, alvo)
        if not prod or not prod.get("id"):
            continue
        if (prod.get("sku") or "").strip().lower() != alvo.strip().lower():
            continue
        saldo = prod.get("stock")
        minimo = 0 if alvo.strip().lower() == atual else qtd + _reservado(alvo, reserva)
        if saldo is None or float(saldo) < minimo:
            continue
        return alvo, prod
    return None, None


async def _produto_exato(client, cache: dict, sku: str) -> dict | None:
    """Produto ativo do Bling com EXATAMENTE este SKU (sem ligar pra caixa),
    usando o cache do tick. None se não existe ou se a busca devolveu outro;
    `_ConsultaFalhouError` se o Bling não respondeu."""
    prod = await _buscar(client, cache, sku)
    if not prod or not prod.get("id"):
        return None
    if (prod.get("sku") or "").strip().lower() != sku.strip().lower():
        return None
    return prod


def _saldo(prod: dict | None) -> float | None:
    if not prod or prod.get("stock") is None:
        return None
    return float(prod["stock"])


async def _cobertura(
    client, cache: dict, itens: list[tuple], estoque: str, reserva: dict[str, int] | None = None
) -> dict | None:
    """O estoque `estoque` consegue atender o pedido INTEIRO? Devolve o plano
    (trocas, quanto estoque tem, o que consome) ou None se falta alguma coisa.

    O saldo virtual do Bling já desconta a reserva dos pedidos em aberto —
    inclusive a DESTE pedido (routers/nf.py::_pedidos_sem_estoque: "0 ainda é
    atendível"). Então:
      * item que já está nesse estoque precisa de saldo >= 0;
      * item que muda pra esse estoque precisa de saldo >= quantidade.
    Dois kits que mudam juntos e usam a mesma peça (o fone a001.sp) somam:
    o Bling mostra o kit pelo componente mais escasso, então 1 fone aparece
    como 1 em cada kit — a peça é conferida pela soma.
    `reserva`: peças do .sp seguradas para pedidos Flex sem peça (só para o
    item que MUDA de estoque — ver `_reserva_flex`).
    """
    total = 0.0
    demanda: dict[str, int] = {}
    alvos: dict[str, str] = {}
    for cod, _base, tag, qtd in itens:
        if tag == estoque:
            saldo = _saldo(await _produto_exato(client, cache, cod))
            if saldo is None or saldo < 0:
                return None
            total += saldo + qtd
            continue
        alvo = sku_alvo(cod, tag, estoque)
        alvos[cod] = alvo
        demanda[alvo] = demanda.get(alvo, 0) + qtd

    trocas: list[dict] = []
    consumo: dict[str, float] = {}
    produtos: dict[str, dict] = {}
    for alvo, qtd in demanda.items():
        prod = await _produto_exato(client, cache, alvo)
        saldo = _saldo(prod)
        if saldo is None or saldo < qtd + _reservado(alvo, reserva):
            return None
        total += saldo
        produtos[alvo] = prod
        consumo[alvo] = qtd

    pecas: Counter = Counter()
    fontes: dict[str, set[str]] = {}
    qtd_de = {cod: qtd for cod, _b, _t, qtd in itens}
    for cod, alvo in alvos.items():
        _saem, entram = pecas_que_mudam(cod, alvo)
        for peca, mult in entram.items():
            pecas[peca] += qtd_de[cod] * mult
            fontes.setdefault(peca, set()).add(alvo.strip().lower())
    for peca, qtd in pecas.items():
        if len(fontes[peca]) < 2:
            continue  # peça de um kit só: o saldo do kit já cobre
        saldo = _saldo(await _produto_exato(client, cache, peca))
        if saldo is None or saldo < qtd + _reservado(peca, reserva):
            return None
        consumo[peca] = qtd

    for cod, alvo in alvos.items():
        prod = produtos[alvo]
        trocas.append(
            {
                "antigo": cod,
                "alvo": alvo,
                "alvo_id": int(prod["id"]),
                "alvo_nome": prod.get("name"),
                "qtd": qtd_de[cod],
                "estoque_unico": estoque,
            }
        )
    return {"estoque": estoque, "total": total, "trocas": trocas, "consumo": consumo}


async def _plano_estoque_unico(
    client,
    cache: dict,
    *,
    qtd_por_codigo: dict[str, int],
    mapa: dict[str, str],
    redireciona,
    existentes: set[str] | None = None,
    reserva: dict[str, int] | None = None,
) -> dict | None:
    """Põe o pedido INTEIRO num estoque só (Vinicius, 23/09/2026).

    Vale para pedido com 2 ou mais itens em estoque de venda (ci/pi/ra/sa/sp).
    Usado (.us), CD (.cd), item sem estoque no SKU e kit misturado ficam de fora
    e seguem a regra item a item. Escolha, entre os estoques que têm TUDO:
      1. o da prioridade cadastrada (o que mais itens do pedido apontam);
      2. se o pedido já está todo num estoque que tem tudo, fica nele — sem
         isso, duas prioridades diferentes dividiriam um pedido inteiro;
      3. o que tem mais estoque dos produtos do pedido;
      4. o que pede menos trocas; por fim a ordem alfabética (só pra ser fixo).
    Estoques considerados: os que já estão no pedido + os das prioridades (+ os
    irmãos, quando a soma por família redireciona todos os itens).

    Devolve None quando não há o que decidir (a regra item a item segue);
    {"estoque": X, ...} com o plano (lista de trocas, que pode ser vazia =
    "já está certo"); ou {"sem_estoque": True, ...} quando nenhum estoque tem
    tudo — o caller cai na regra item a item, que é "deixa como está".
    """
    itens: list[tuple[str, str, str, int]] = []
    for cod, qtd in qtd_por_codigo.items():
        info = analisa_codigo(cod)
        if not info:
            continue
        base, tag = info
        if tag not in estoque_familia.LOTES_DE_VENDA:
            continue
        itens.append((cod, base, tag, int(qtd)))
    if len(itens) < 2:
        return None

    prios = {cod: mapa.get(base) for cod, base, _t, _q in itens}
    if any(p and p not in estoque_familia.LOTES_DE_VENDA for p in prios.values()):
        # Prioridade em CD/usado: fora do que esta regra sabe decidir.
        return None
    presentes = {tag for _c, _b, tag, _q in itens}
    votos = Counter(p for p in prios.values() if p)
    irmaos: set[str] = set()
    if all(redireciona(cod) for cod, _b, _t, _q in itens):
        # Só os lotes em que TODOS os itens existem como produto ativo — os
        # outros nunca cobririam o pedido e custariam consultas ao Bling.
        irmaos = {
            lote
            for lote in estoque_familia.LOTES_DE_VENDA
            if existentes is None
            or all(
                tag == lote or _chave(sku_alvo(cod, tag, lote)) in existentes
                for cod, _b, tag, _q in itens
            )
        }
    candidatos = presentes | set(votos) | irmaos
    if len(candidatos) < 2:
        return None  # tudo num estoque, sem prioridade apontando pra outro
    atual = next(iter(presentes)) if len(presentes) == 1 else None

    avaliado: dict[str, dict | None] = {}

    async def cobre(estoque: str) -> dict | None:
        if estoque not in avaliado:
            avaliado[estoque] = await _cobertura(client, cache, itens, estoque, reserva)
        return avaliado[estoque]

    def melhor(opcoes: list[dict]) -> dict:
        for o in opcoes:
            if o["estoque"] == atual:
                return o
        # `opcoes` vem em ordem alfabética; max devolve o primeiro empate.
        return max(opcoes, key=lambda o: (o["total"], -len(o["trocas"])))

    escolha: dict | None = None
    motivo = ""
    for n in sorted(set(votos.values()), reverse=True):
        grupo = sorted(e for e, v in votos.items() if v == n)
        viaveis = [c for e in grupo if (c := await cobre(e))]
        if viaveis:
            escolha, motivo = melhor(viaveis), "prioridade"
            break
    if escolha is None and atual is not None and (c := await cobre(atual)):
        escolha, motivo = c, "ja_estava"
    if escolha is None:
        resto = sorted(candidatos - set(votos) - ({atual} if atual else set()))
        viaveis = [c for e in resto if (c := await cobre(e))]
        if viaveis:
            escolha, motivo = melhor(viaveis), "mais_estoque"
    if escolha is None:
        return {
            "sem_estoque": True,
            "misturado": len(presentes) > 1,
            "estoques": sorted(candidatos),
        }
    # Necessária = o lote em que O ITEM está não atende (sair é obrigação);
    # opcional = atende e a troca é só preferência. Item por item, como na regra
    # item a item — pedido misturado não tem "estoque atual" único. Só troca
    # opcional pode ser segurada pela trava anti-volta.
    for t in escolha["trocas"]:
        t["necessaria"] = await _lote_atual_nao_cobre(client, cache, t["antigo"])
    return {
        **escolha,
        "motivo": motivo,
        "itens": {cod for cod, _b, _t, _q in itens},
        "misturado": len(presentes) > 1,
    }


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


async def _desfaz_troca_recente(session: AsyncSession, numero: str, trocas: list[dict]) -> bool:
    """Alguma destas trocas desfaz uma que o próprio robô fez neste pedido há
    menos de `_JANELA_ANTI_VOLTA`? (ci→sp agora depois de sp→ci há 10 min)."""
    desde = datetime.now(UTC) - _JANELA_ANTI_VOLTA
    feitas = (
        await session.execute(
            select(MargemAudit.valor_antigo, MargemAudit.valor_novo).where(
                MargemAudit.pedido_bling == str(numero),
                MargemAudit.origem == "prioridade_estoque",
                MargemAudit.acao == "sku",
                MargemAudit.created_at >= desde,
            )
        )
    ).all()
    recentes = {
        ((antigo or "").strip().lower(), (novo or "").strip().lower()) for antigo, novo in feitas
    }
    return any(
        (t["alvo"].strip().lower(), t["antigo"].strip().lower()) in recentes for t in trocas
    )


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


def _redireciona(cod: str) -> bool:
    """Com a soma por família ligada nesta linha, o robô também pode
    redirecionar a venda para um lote irmão — inclusive quando o item JÁ está
    no lote da prioridade, mas esse lote não tem a peça."""
    return bool(get_settings().estoque_familia_redireciona) and estoque_familia.familia_ligada(cod)


async def _lote_atual_nao_cobre(client, cache: dict, cod: str) -> bool:
    """O lote em que o item ESTÁ não atende (saldo virtual < 0 ou produto
    sumido)? Então sair dele é necessário, não preferência. Sem resposta do
    Bling levanta `_ConsultaFalhouError` — o pedido fica para a próxima rodada."""
    prod = await _buscar(client, cache, cod)
    if not prod or not prod.get("id") or _chave(prod.get("sku") or "") != _chave(cod):
        return True
    saldo = _saldo(prod)
    return saldo is None or saldo < 0


def _falha_passageira(exc: Exception) -> bool:
    """429, 5xx, timeout, rede: pode dar certo daqui a pouco. (A recusa de
    validação do Bling — erro 67 — é definitiva e não entra aqui.)"""
    if isinstance(exc, (BlingCloudflareError, httpx.TransportError, httpx.TimeoutException)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code == 429 or exc.response.status_code >= 500
    )


class _Descontos:
    """Saldo descontado do cache da rodada por troca PLANEJADA de um pedido;
    volta se o pedido acabar não sendo trocado (consulta falhou, anti-volta)."""

    def __init__(self) -> None:
        self._feitos: list[tuple[dict, float]] = []

    def descontar(self, prod: dict, qtd: float) -> None:
        prod["stock"] = float(prod["stock"]) - qtd
        self._feitos.append((prod, qtd))

    def devolver(self) -> None:
        for prod, qtd in self._feitos:
            prod["stock"] = float(prod["stock"]) + qtd
        self._feitos.clear()


async def _decidir_pedido(
    client,
    cache: dict,
    *,
    numero: str,
    qtd_por_codigo: dict[str, int],
    mapa: dict[str, str],
    estoque_unico: bool,
    summary: dict,
    descontos: _Descontos,
    existentes: set[str] | None = None,
    reserva: dict[str, int] | None = None,
) -> list[dict]:
    """Planeja as trocas de UM pedido (sem PUT). Pode levantar
    `_ConsultaFalhouError` — aí o caller devolve os descontos e adia o pedido.
    `reserva`: o .sp segurado para pedidos Flex sem peça (`_reserva_flex`).

    Pedido num estoque só: decide o pedido INTEIRO antes do item a item. Os
    itens que o plano cobre não passam pela regra item a item (senão duas
    prioridades diferentes voltariam a dividir o pedido)."""
    trocas: list[dict] = []
    decididos: set[str] = set()
    if estoque_unico:
        plano = await _plano_estoque_unico(
            client,
            cache,
            qtd_por_codigo=qtd_por_codigo,
            mapa=mapa,
            redireciona=_redireciona,
            existentes=existentes,
            reserva=reserva,
        )
        if plano and plano.get("estoque"):
            decididos = set(plano["itens"])
            summary["avaliados"] += len(decididos)
            for chave, qtd in plano["consumo"].items():
                prod = cache.get(_chave(chave))
                if prod and prod.get("stock") is not None:
                    descontos.descontar(prod, qtd)
            trocas.extend(plano["trocas"])
            if plano["trocas"]:
                summary["pedidos_estoque_unico"] = (
                    summary.get("pedidos_estoque_unico", 0) + 1
                )
                logger.info(
                    "prioridade_estoque_pedido_estoque_unico",
                    pedido=numero,
                    estoque=plano["estoque"],
                    motivo=plano["motivo"],
                    misturado=plano["misturado"],
                    trocas=[f"{t['antigo']} -> {t['alvo']}" for t in plano["trocas"]],
                )
            else:
                summary["ja_no_lote_certo"] = (
                    summary.get("ja_no_lote_certo", 0) + len(decididos)
                )
        elif plano and plano.get("sem_estoque") and plano.get("misturado"):
            summary["pedidos_sem_estoque_unico"] = (
                summary.get("pedidos_sem_estoque_unico", 0) + 1
            )
            logger.info(
                "prioridade_estoque_pedido_sem_estoque_unico",
                pedido=numero,
                estoques=plano["estoques"],
            )

    for cod, qtd in qtd_por_codigo.items():
        if cod in decididos:
            continue
        info = analisa_codigo(cod)
        if not info:
            continue
        base, tag_atual = info
        prio = mapa.get(base)
        redirecionar = _redireciona(cod)
        if not redirecionar and (not prio or prio == tag_atual):
            continue
        summary["avaliados"] += 1
        alvo, prod = await _lote_com_saldo(
            client,
            cache,
            codigo=cod,
            tag_atual=tag_atual,
            prioridade=prio,
            qtd=qtd,
            redirecionar=redirecionar,
            existentes=existentes,
            reserva=reserva,
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
        descontos.descontar(prod, qtd)
        trocas.append(
            {
                "antigo": cod,
                "alvo": alvo,
                "alvo_id": int(prod["id"]),
                "alvo_nome": prod.get("name"),
                "qtd": qtd,
                "necessaria": await _lote_atual_nao_cobre(client, cache, cod),
            }
        )
    return trocas


# ---- Pedido Flex: sai SEMPRE do .sp ------------------------------------------

# O lote do Flex (era "sp" fixo) vem do local de saída editável na aba Flex
# (services/flex_local, Eduardo 08/10/2026) — lido no começo da rodada.
def _lote_flex() -> str:
    return flex_local.lote()
_FALTA_NA_FILA_DA_NF = (
    "a planilha da NF já saiu com o lote antigo — troque o item para o .sp à mão e refaça a NF"
)
_ROTULO_PLATAFORMA = {
    flex_envio.PLATAFORMA_ML: "Mercado Livre",
    flex_envio.PLATAFORMA_SHOPEE: "Shopee",
}


def _reservado(sku: str, reserva: dict[str, int] | None) -> int:
    """Quanto do `sku` (lote de DESTINO) está segurado para pedidos Flex sem
    peça. Kit: a peça mais segurada (o saldo do kit já é o da mais escassa)."""
    if not reserva:
        return 0
    pecas = [p.strip() for p in (sku or "").lower().split("+") if p.strip()]
    return max((int(reserva.get(p, 0)) for p in pecas), default=0)


def _reserva_flex(qtd_por_codigo: dict[str, int]) -> Counter:
    """As peças do .sp que um pedido Flex SEM peça suficiente precisa (cada
    pedaço com lote de venda que ainda não está no .sp, pela quantidade).

    Ficam seguradas na rodada: o pedido normal não vai para o .sp com elas.
    A decisão do pedido Flex sem peça é de uma pessoa ("separar em SP,
    transferir a peça para o .sp ou cancelar") — se a última peça de São
    Bernardo for para um pedido que podia sair do CI, separar em SP deixa de
    ser opção."""
    reserva: Counter = Counter()
    for cod, qtd in qtd_por_codigo.items():
        for pedaco in (cod or "").lower().split("+"):
            p = pedaco.strip()
            tag = _tag_de(p) if p else None
            if tag in estoque_familia.LOTES_DE_VENDA and tag != _lote_flex():
                reserva[f"{p[: -(len(tag) + 1)]}.{_lote_flex()}"] += int(qtd or 1)
    return reserva


async def _reserva_flex_fora_da_rodada(session: AsyncSession, numeros: set[str]) -> Counter:
    """O .sp segurado pelos pedidos Flex sem peça que NÃO estão nesta rodada
    (o gancho do enfileirar só traz os seus pedidos): os que estão em aberto
    com o aviso gravado (`flex_pedido.alerta`) e ainda fora do .sp."""
    rows = await session.execute(
        select(BlingOrder.numero, BlingOrder.item_codigo, BlingOrder.item_quantidade)
        .join(FlexPedido, FlexPedido.bling_id == BlingOrder.bling_id)
        .where(
            FlexPedido.alerta.is_not(None),
            FlexPedido.no_sp.is_(False),
            BlingOrder.situacao == _SITUACAO_EM_ABERTO,
            BlingOrder.item_codigo.is_not(None),
        )
    )
    por_pedido: dict[str, dict[str, int]] = {}
    for numero, cod, qtd in rows.all():
        if numero in numeros:
            continue
        itens = por_pedido.setdefault(numero, {})
        itens[cod] = itens.get(cod, 0) + int(qtd or 1)
    reserva: Counter = Counter()
    for itens in por_pedido.values():
        reserva.update(_reserva_flex(itens))
    return reserva


async def _na_fila_da_nf(session: AsyncSession, numeros: list[str]) -> set[str]:
    """Pedidos já na fila da NF (planilha gerada, importação pendente:
    `nf_faturamento.status_faturamento = 'processando'`). Trocar o item agora
    deixaria a NF com o SKU antigo e o pedido com o novo."""
    if not numeros:
        return set()
    rows = await session.execute(
        select(NfFaturamento.pedido_bling).where(
            NfFaturamento.pedido_bling.in_(numeros),
            NfFaturamento.status_faturamento == "processando",
        )
    )
    return {str(n) for (n,) in rows.all()}


# Plataformas em que existe pedido Flex (código de `store_info.platform`).
_PLATAFORMAS_COM_FLEX = ("ml", "shopee")


async def _esperando_tipo_envio(
    session: AsyncSession, numeros: list[str], agora: datetime
) -> set[str]:
    """Pedidos ML/Shopee que acabaram de cair e cujo tipo de envio (Flex ou
    não) ainda ninguém leu — ficam para a próxima rodada.

    Quem reconhece o Flex é o shipment check (de minuto em minuto, ver
    services/flex_envio). Sem esta espera, a NF automática (minutos pares)
    pega o pedido Flex antes dele: o trata como normal, pode tirá-lo do .sp
    e gera a planilha com o lote errado. Lido = tem o prazo de despacho
    gravado (o shipment check grava o prazo e o `flex_pedido` na MESMA
    transação, a partir da mesma leitura) ou a Logística já tem o tipo.
    A espera tem teto (`flex_espera_envio_min`, contado de quando o pedido
    entrou no espelho): conta sem acesso à API não segura pedido para sempre."""
    minutos = int(getattr(get_settings(), "flex_espera_envio_min", 0) or 0)
    if minutos <= 0 or not numeros:
        return set()
    corte = agora - timedelta(minutes=minutos)
    rows = await session.execute(
        select(BlingOrder.numero)
        .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
        .where(
            BlingOrder.numero.in_(numeros),
            func.lower(StoreInfo.platform).in_(_PLATAFORMAS_COM_FLEX),
        )
        .group_by(BlingOrder.numero)
        .having(
            func.min(BlingOrder.created_at) >= corte,
            func.bool_and(BlingOrder.marketplace_ship_deadline.is_(None)),
        )
    )
    novos = {str(n) for (n,) in rows.all()}
    if not novos:
        return set()
    lidos = {
        str(n)
        for (n,) in (
            await session.execute(
                select(Logistica.pedido_bling).where(
                    Logistica.pedido_bling.in_(list(novos)), Logistica.envio_flex.is_not(None)
                )
            )
        ).all()
    }
    return novos - lidos


@dataclass
class _PedidoFlex:
    """O que o robô sabe do pedido Flex: a linha de `flex_pedido` ou, quando ela
    ainda não existe, o que a Logística marcou."""

    bling_id: int
    plataforma: str  # 'ml' | 'shopee'
    integration_id: UUID | None = None
    numeroloja: str | None = None
    envio_tipo: str | None = None
    existe: bool = False  # já tem linha em flex_pedido
    no_sp: bool = False
    alerta: str | None = None


async def _pedidos_flex(
    session: AsyncSession, por_pedido: dict[str, list]
) -> dict[str, _PedidoFlex]:
    """numero → pedido Flex, entre os pedidos desta rodada.

    Duas fontes, basta uma dizer Flex: `flex_pedido` (o shipment check registra
    de minuto em minuto, com o envio que já lê) e a Logística com `envio_flex`
    (o enriquecimento de hora em hora pode ter visto o envio antes)."""
    bling_de = {numero: int(itens[0].bling_id) for numero, itens in por_pedido.items()}
    if not bling_de:
        return {}
    numero_de = {b: n for n, b in bling_de.items()}
    out: dict[str, _PedidoFlex] = {}
    rows = await session.execute(
        select(
            FlexPedido.bling_id,
            FlexPedido.plataforma,
            FlexPedido.integration_id,
            FlexPedido.numeroloja,
            FlexPedido.envio_tipo,
            FlexPedido.no_sp,
            FlexPedido.alerta,
        ).where(FlexPedido.bling_id.in_(list(numero_de)))
    )
    for r in rows.all():
        out[numero_de[int(r.bling_id)]] = _PedidoFlex(
            bling_id=int(r.bling_id),
            plataforma=r.plataforma,
            integration_id=r.integration_id,
            numeroloja=r.numeroloja,
            envio_tipo=r.envio_tipo,
            existe=True,
            no_sp=bool(r.no_sp),
            alerta=r.alerta,
        )
    faltam = [n for n in bling_de if n not in out]
    if faltam:
        rows = await session.execute(
            select(
                Logistica.pedido_bling,
                Logistica.plataforma,
                Logistica.pedido_marketplace,
                Logistica.envio_tipo,
            ).where(Logistica.envio_flex.is_(True), Logistica.pedido_bling.in_(faltam))
        )
        for r in rows.all():
            numero = str(r.pedido_bling)
            plataforma = flex_envio.plataforma_flex(r.plataforma)
            if plataforma is None or numero in out or numero not in bling_de:
                continue
            out[numero] = _PedidoFlex(
                bling_id=bling_de[numero],
                plataforma=plataforma,
                numeroloja=r.pedido_marketplace,
                envio_tipo=r.envio_tipo,
            )
    return out


def _n(valor: float | None) -> str:
    """Saldo para o texto do aviso: 3 e não 3.0."""
    if valor is None:
        return "?"
    return str(int(valor)) if float(valor).is_integer() else f"{valor:g}"


async def _decidir_flex(client, cache: dict, *, qtd_por_codigo: dict[str, int]) -> dict:
    """Planeja o pedido Flex (sem PUT): TODOS os itens com lote vão para o .sp,
    ou nenhum vai.

    Devolve {"trocas", "consumo", "faltas"}. Com `faltas` vazia, `trocas` leva
    o pedido inteiro para o .sp (lista vazia = já está todo lá, com peça). Com
    `faltas`, nada é trocado e o caller avisa. Tudo ou nada pelo mesmo motivo
    do pedido num estoque só: pedido partido aparece para duas equipes — e o
    pedido Flex sem peça em SP é decisão de uma pessoa (D2 da análise), trocar
    metade só atrapalharia quem vai decidir.

    Cada item:
      * sem lote no SKU (malas `b…`, eletro `u…`) ou `fake.`: não existe .sp
        para ele — fica como está e não decide;
      * lote fora dos de venda (`.us` usado, `.cd`) ou kit com lotes
        misturados: falta. `sku_alvo` viraria o usado em novo (a017.us →
        a017.sp) — é o filtro por LOTES_DE_VENDA que a análise pede;
      * já no .sp: saldo >= 0 (o virtual já desconta a reserva deste pedido);
        indo para o .sp: saldo >= quantidade, com a peça que dois kits do
        pedido dividem (o fone a001.sp) conferida pela soma — a régua de
        `_lote_com_saldo`/`_cobertura`, que é quem calcula.
    Consulta ao Bling que falha levanta `_ConsultaFalhouError`: o pedido fica
    para a próxima rodada, como qualquer outro."""
    itens: list[tuple[str, str, str, int]] = []
    faltas: list[str] = []
    for cod, qtd in qtd_por_codigo.items():
        low = (cod or "").strip().lower()
        pedacos = [p.strip() for p in low.split("+") if p.strip()]
        if low.startswith("fake.") or not any(_tag_de(p) for p in pedacos):
            continue
        info = analisa_codigo(cod)
        if info is None:
            faltas.append(f"{cod}: kit com lotes misturados — trocar à mão")
            continue
        base, tag = info
        if tag not in estoque_familia.LOTES_DE_VENDA:
            faltas.append(f"{cod}: o lote .{tag} não tem .sp equivalente — trocar à mão")
            continue
        itens.append((cod, base, tag, int(qtd)))
    if faltas or not itens:
        return {"trocas": [], "consumo": {}, "faltas": faltas}

    plano = await _cobertura(client, cache, itens, _lote_flex())
    if plano is not None:
        trocas: list[dict] = []
        for t in plano["trocas"]:
            t = {k: v for k, v in t.items() if k != "estoque_unico"}
            # Obrigatória: para a trava anti-volta e para o "adia se o PUT
            # falhar de passagem", vale como a saída de um lote negativo.
            t["necessaria"] = True
            t["flex"] = True
            trocas.append(t)
        return {"trocas": trocas, "consumo": plano["consumo"], "faltas": []}

    # Não cobre: diz o quê, item a item, para quem vai decidir.
    demanda: Counter = Counter()
    for cod, _b, tag, qtd in itens:
        if tag != _lote_flex():
            demanda[sku_alvo(cod, tag, _lote_flex())] += qtd
    for cod, _b, tag, _qtd in itens:
        if tag == _lote_flex():
            prod = await _produto_exato(client, cache, cod)
            saldo = _saldo(prod)
            if prod is None:
                faltas.append(f"{cod}: não existe ativo no Bling")
            elif saldo is None or saldo < 0:
                faltas.append(f"{cod}: o .sp está com saldo {_n(saldo)} (já contando este pedido)")
            continue
        alvo = sku_alvo(cod, tag, _lote_flex())
        prod = await _produto_exato(client, cache, alvo)
        saldo = _saldo(prod)
        if prod is None:
            faltas.append(f"{cod}: o {alvo} não existe ativo no Bling")
        elif saldo is None or saldo < demanda[alvo]:
            faltas.append(
                f"{cod}: o {alvo} tem {_n(saldo)} livre e o pedido precisa de {demanda[alvo]}"
            )
    if not faltas:
        faltas.append("a peça que os kits do pedido dividem não cobre todos juntos no .sp")
    return {"trocas": [], "consumo": {}, "faltas": faltas}


async def _flex_gravar_estado(
    session: AsyncSession,
    pf: _PedidoFlex,
    *,
    no_sp: bool,
    alerta: str | None,
    sp_em: datetime | None = None,
) -> None:
    """`flex_pedido.no_sp`/`alerta`, só quando mudou: o sweep passa pelo mesmo
    pedido a cada rodada. Pedido marcado só pela Logística ganha a linha aqui
    (o shipment check completa conta/prazo quando ler o envio). `sp_em`: o
    robô ACABOU de levar o pedido ao .sp — o motor do Flex segue descontando
    o pedido até o produto .sp ser atualizado depois disso (o webhook do
    Bling que traz a reserva às vezes não vem)."""
    if pf.existe and pf.no_sp == no_sp and pf.alerta == alerta and sp_em is None:
        return
    if not pf.existe and not no_sp and alerta is None:
        return  # nada a registrar
    valores = {
        "bling_id": pf.bling_id,
        "plataforma": pf.plataforma,
        "integration_id": pf.integration_id,
        "numeroloja": pf.numeroloja,
        "envio_tipo": pf.envio_tipo,
        "no_sp": no_sp,
        "alerta": alerta,
    }
    if sp_em is not None:
        valores["sp_em"] = sp_em
    stmt = pg_insert(FlexPedido).values(**valores)
    mudar = {
        "no_sp": stmt.excluded.no_sp,
        "alerta": stmt.excluded.alerta,
        "atualizado_em": func.now(),
    }
    if sp_em is not None:
        mudar["sp_em"] = stmt.excluded.sp_em
    stmt = stmt.on_conflict_do_update(index_elements=[FlexPedido.bling_id], set_=mudar)
    await session.execute(stmt)


async def _flex_avisar_pessoas(
    session: AsyncSession,
    pf: _PedidoFlex,
    *,
    numero: str,
    faltas: list[str],
    na_fila_da_nf: bool = False,
) -> int:
    """Sino dos admins (o mesmo dos outros robôs; sem Threema e sem Telegram).
    Um aviso por pedido: o dedupe segura a repetição a cada rodada."""
    admins = (
        (await session.execute(select(User.id).where(User.role == UserRole.ADMIN))).scalars().all()
    )
    rotulo = _ROTULO_PLATAFORMA.get(pf.plataforma, pf.plataforma)
    loja = f"{rotulo} {pf.numeroloja}" if pf.numeroloja else rotulo
    local = flex_local.atual()
    if na_fila_da_nf:
        mensagem = (
            f"O pedido {numero} ({loja}) sai pelo Flex, de {local.cidade}, mas só foi "
            "reconhecido como Flex DEPOIS de entrar na fila da NF — a planilha saiu com o lote "
            f"antigo. O robô NÃO trocou o lote. Troque o item para o .{local.lote} à mão e refaça "
            f"a NF (ou separe em {local.cidade} e acerte o estoque no Bling depois)."
        )
    else:
        mensagem = (
            f"O pedido {numero} ({loja}) sai pelo Flex, de {local.cidade}, mas o estoque "
            f".{local.lote} não cobre: {'; '.join(faltas)}. O robô NÃO trocou o lote. Decida: "
            f"separar em {local.cidade}, transferir a peça para o .{local.lote} ou cancelar o "
            "pedido."
        )
    avisados = 0
    for uid in admins:
        criado = await emit_alert(
            session,
            user_id=uid,
            type=AlertType.GENERIC,
            severity=AlertSeverity.ERROR,
            title=f"Pedido Flex {numero} sem peça no .sp",
            message=mensagem,
            payload={
                "pedido_bling": numero,
                "bling_id": pf.bling_id,
                "plataforma": pf.plataforma,
                "numeroloja": pf.numeroloja,
                "faltas": faltas,
            },
            dedupe_key=f"flex_sem_sp:{pf.bling_id}",
            notify_telegram=False,
        )
        avisados += criado is not None
    return avisados


async def _flex_registrar(
    session: AsyncSession,
    pf: _PedidoFlex,
    *,
    numero: str,
    no_sp: bool,
    alerta: str | None = None,
    faltas: list[str] | None = None,
    acao: str | None = None,
    resultado: str = "ok",
    sku: str | None = None,
    erro: str | None = None,
    sp_em: datetime | None = None,
    na_fila_da_nf: bool = False,
) -> None:
    """Estado do pedido Flex + trilha (`flex_log`) + aviso, quando o alerta é
    NOVO. Num SAVEPOINT: o registro nunca desfaz o que já foi feito no Bling
    nem o espelho e a auditoria da mesma transação (commit é do caller)."""
    alerta_novo = alerta is not None and alerta != pf.alerta
    try:
        async with session.begin_nested():
            await _flex_gravar_estado(session, pf, no_sp=no_sp, alerta=alerta, sp_em=sp_em)
            # O aviso repetido a cada rodada não vira linha nova na trilha.
            if acao and (acao != "pedido_sem_sp" or alerta_novo):
                session.add(
                    FlexLog(
                        integration_id=pf.integration_id,
                        plataforma=pf.plataforma,
                        bling_id=pf.bling_id,
                        sku=sku,
                        acao=acao,
                        modo=flex_config.modo(),
                        resultado=resultado,
                        erro=erro if erro is not None else alerta,
                    )
                )
            if alerta_novo:
                await _flex_avisar_pessoas(
                    session, pf, numero=numero, faltas=faltas or [], na_fila_da_nf=na_fila_da_nf
                )
    except Exception as exc:  # noqa: BLE001 — registro é best-effort
        logger.warning(
            "prioridade_estoque_flex_registro_falhou", pedido=numero, erro=str(exc)[:200]
        )
        return
    pf.existe = pf.existe or no_sp or alerta is not None
    pf.no_sp, pf.alerta = no_sp, alerta


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
    estoque_unico = bool(getattr(get_settings(), "prioridade_pedido_estoque_unico", True))
    flex_ligado = bool(getattr(get_settings(), "flex_pedido_no_sp", True))
    if flex_ligado:
        # O lote do Flex (o do local de saída da aba Flex) vale a rodada.
        await flex_local.carregar(session)
    mapa = await _mapa_prioridades(session)
    if not mapa and not estoque_unico and not flex_ligado:
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
    # Sempre na mesma ordem (do pedido mais antigo para o mais novo): quem
    # chegou antes pega a peça do lote da prioridade primeiro, e o resultado da
    # rodada não depende da ordem em que o banco devolve as linhas.
    q = q.order_by(BlingOrder.numero, BlingOrder.item_index)
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

    # Pedido Flex: regra própria, obrigatória (ver `_decidir_flex`). Sem
    # prioridade cadastrada e sem o pedido num estoque só, só ele tem o que
    # decidir — os outros nem consultam o Bling.
    flex = await _pedidos_flex(session, por_pedido) if flex_ligado else {}
    if flex_ligado:
        # Pedido ML/Shopee recém-chegado sem o tipo de envio lido: pode ser
        # Flex — fica para a próxima rodada (o enfileirar também o segura).
        esperando = await _esperando_tipo_envio(
            session, [n for n in por_pedido if n not in flex], datetime.now(UTC)
        )
        if esperando:
            for n in sorted(esperando):
                summary.setdefault("adiados", []).append(n)
                summary.setdefault("adiados_envio", []).append(n)
                por_pedido.pop(n, None)
            logger.info("prioridade_estoque_esperando_tipo_envio", pedidos=sorted(esperando))
    if not mapa and not estoque_unico:
        por_pedido = {n: itens for n, itens in por_pedido.items() if n in flex}
    if not por_pedido:
        return summary
    # Pedido Flex PRIMEIRO, todos, antes de qualquer pedido normal (a ordem
    # do número vale dentro de cada grupo): a regra dele é obrigatória, e um
    # pedido normal mais antigo com prioridade .sp pegaria a última peça de
    # São Bernardo que o Flex precisa — o normal pode sair do CI, o Flex não.
    por_pedido = dict(sorted(por_pedido.items(), key=lambda kv: kv[0] not in flex))
    # O .sp que os pedidos Flex sem peça precisam fica segurado para os
    # pedidos normais (ver `_reserva_flex`): os desta rodada entram no laço;
    # os de fora (o gancho do enfileirar só traz os seus) vêm do banco.
    reserva: Counter = Counter()
    if flex_ligado:
        reserva.update(await _reserva_flex_fora_da_rodada(session, set(por_pedido)))
    # Pedido Flex reconhecido DEPOIS de entrar na fila da NF (o tipo de envio
    # chegou além da espera — ver `_esperando_tipo_envio`): não troca, avisa.
    flex_na_fila = await _na_fila_da_nf(session, [n for n in por_pedido if n in flex])

    client = await nf_emissao_gerar._bling_client_opt(session)
    if client is None:
        logger.warning("prioridade_estoque_sem_bling")
        return summary

    # SKUs de produto ativo no DaVinci (1 consulta ao banco por rodada): lote
    # irmão que não existe aqui não é consultado no Bling.
    existentes = {
        _chave(sku)
        for (sku,) in (
            await session.execute(
                select(Product.sku).where(Product.situacao == "A", Product.sku.is_not(None))
            )
        ).all()
    }

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

        pf = flex.get(numero)
        descontos = _Descontos()
        faltas_flex: list[str] = []
        try:
            if pf is not None and numero in flex_na_fila and not all(
                flex_envio.item_no_sp(c) for c in qtd_por_codigo
            ):
                # A planilha da NF já saiu com o lote antigo: trocar agora
                # deixaria a NF e o pedido com SKUs diferentes. Uma pessoa
                # decide (troca à mão e refaz a NF, ou acerta o estoque).
                summary["flex_pedidos"] = summary.get("flex_pedidos", 0) + 1
                faltas_flex = [_FALTA_NA_FILA_DA_NF]
                trocas = []
            elif pf is not None:
                # Pedido Flex: o mapa, o estoque único e os irmãos não entram.
                summary["flex_pedidos"] = summary.get("flex_pedidos", 0) + 1
                plano_flex = await _decidir_flex(
                    client, alvo_cache, qtd_por_codigo=qtd_por_codigo
                )
                faltas_flex = plano_flex["faltas"]
                trocas = plano_flex["trocas"]
                for chave, qtd in plano_flex["consumo"].items():
                    prod = alvo_cache.get(_chave(chave))
                    if prod and prod.get("stock") is not None:
                        descontos.descontar(prod, qtd)
            else:
                trocas = await _decidir_pedido(
                    client,
                    alvo_cache,
                    numero=numero,
                    qtd_por_codigo=qtd_por_codigo,
                    mapa=mapa,
                    estoque_unico=estoque_unico,
                    summary=summary,
                    descontos=descontos,
                    existentes=existentes,
                    reserva=dict(reserva),
                )
        except _ConsultaFalhouError as exc:
            descontos.devolver()
            summary["consulta_falhou"] = summary.get("consulta_falhou", 0) + 1
            summary.setdefault("adiados", []).append(numero)
            logger.info("prioridade_estoque_pedido_adiado", pedido=numero, sku=str(exc))
            continue

        if pf is not None and (faltas_flex or not trocas):
            # Nada a trocar no Bling: ou o .sp não cobre (aviso, e o pedido fica
            # no lote em que está — nunca vai para outro), ou já está todo no
            # .sp com peça (apaga o aviso de uma rodada anterior, se houver).
            no_sp = all(flex_envio.item_no_sp(c) for c in qtd_por_codigo)
            if faltas_flex:
                na_fila = faltas_flex == [_FALTA_NA_FILA_DA_NF]
                summary["flex_sem_sp"] = summary.get("flex_sem_sp", 0) + 1
                if na_fila:
                    summary["flex_na_fila_da_nf"] = summary.get("flex_na_fila_da_nf", 0) + 1
                reserva.update(_reserva_flex(qtd_por_codigo))
                logger.warning("prioridade_estoque_flex_sem_sp", pedido=numero, faltas=faltas_flex)
                await _flex_registrar(
                    session,
                    pf,
                    numero=numero,
                    no_sp=no_sp,
                    alerta=(
                        "Pedido Flex reconhecido depois de entrar na fila da NF e ele NÃO foi "
                        "trocado de lote: " + _FALTA_NA_FILA_DA_NF
                        if na_fila
                        else "O .sp não cobre este pedido Flex e ele NÃO foi trocado de lote: "
                        + "; ".join(faltas_flex)
                    ),
                    faltas=faltas_flex,
                    acao="pedido_sem_sp",
                    resultado="pendente",
                    sku=", ".join(qtd_por_codigo),
                    na_fila_da_nf=na_fila,
                )
            else:
                await _flex_registrar(session, pf, numero=numero, no_sp=no_sp)
            continue

        # A trava só segura troca OPCIONAL (o lote atual atende): sair de lote
        # negativo nunca espera — senão o check de estoque do enfileirar mandaria
        # o pedido para Aguardando Cancelamento com peça no irmão. Pedido Flex
        # nunca espera: ir para o .sp é obrigação.
        if (
            trocas
            and pf is None
            and not any(t.get("necessaria") for t in trocas)
            and await _desfaz_troca_recente(session, numero, trocas)
        ):
            descontos.devolver()
            summary["anti_vai_e_volta"] = summary.get("anti_vai_e_volta", 0) + 1
            logger.info(
                "prioridade_estoque_anti_vai_e_volta",
                pedido=numero,
                trocas=[f"{t['antigo']} -> {t['alvo']}" for t in trocas],
            )
            continue

        if not trocas:
            continue
        necessaria = any(t.get("necessaria") for t in trocas)

        try:
            order = await client.get_order(int(bling_id))
            body = build_observacoes_put_body(order, order.get("observacoes") or "")
            body["itens"], aplicadas = aplicar_trocas_nos_itens(
                body.get("itens") or [], trocas, substituir=substituir_item
            )
            if len(aplicadas) < len(trocas):
                # Espelho local não bate com o Bling (item trocado à mão?) — não
                # arrisca o PUT, nem pela metade (dividiria o pedido). Se sair do
                # lote era obrigação, o check de estoque do gancho leria o SKU
                # velho do espelho: fica para depois do próximo sync.
                descontos.devolver()
                if necessaria:
                    summary.setdefault("adiados", []).append(numero)
                logger.info(
                    "prioridade_estoque_espelho_divergente",
                    pedido=numero,
                    planejadas=len(trocas),
                    casaram=len(aplicadas),
                )
                continue
            # Registro humano da troca nas Observações do pedido — pedido do
            # Eduardo (28/08): "quando alterar, você tem que adicionar no
            # campo observação". compose_observacoes põe "dd/mm - " na frente
            # e não duplica a mesma linha no mesmo dia.
            nota = "; ".join(f"{t['antigo']} -> {t['alvo']}" for t in aplicadas)
            if pf is not None:
                linha = f"SKU trocado para o estoque SP (pedido Flex): {nota}"
            else:
                unico = next(
                    (t["estoque_unico"] for t in aplicadas if t.get("estoque_unico")), None
                )
                onde = f" (pedido todo no estoque {unico.upper()})" if unico else ""
                linha = f"SKU trocado pela prioridade de estoque{onde}: {nota}"
            body["observacoes"] = compose_observacoes(order.get("observacoes"), linha)
            await client.update_order(int(bling_id), body)
            # Depois do PUT: o que o produto .sp receber do Bling a partir
            # daqui já traz a reserva deste pedido (ver flex_motor).
            trocado_em = datetime.now(UTC)
        except Exception as exc:  # noqa: BLE001 — PUT revalida a venda inteira
            descontos.devolver()
            # Timeout/504 pode ter sido processado pelo Bling: o próximo pedido
            # da rodada relê o saldo em vez de confiar no desconto devolvido.
            _esquecer(alvo_cache, [t["antigo"] for t in trocas] + [t["alvo"] for t in trocas])
            if necessaria and _falha_passageira(exc):
                summary.setdefault("adiados", []).append(numero)
            summary["falhas"] += 1
            logger.warning(
                "prioridade_estoque_put_falhou",
                pedido=numero,
                erro=str(exc),
            )
            if pf is not None:
                # A trilha guarda a tentativa: o pedido Flex ainda está fora do
                # .sp e a próxima rodada tenta de novo.
                await _flex_registrar(
                    session,
                    pf,
                    numero=numero,
                    no_sp=pf.no_sp,
                    alerta=pf.alerta,
                    acao="pedido_sp",
                    resultado="erro",
                    sku="; ".join(f"{t['antigo']} -> {t['alvo']}" for t in trocas),
                    erro=str(exc)[:500],
                )
            continue

        _esquecer(alvo_cache, [t["antigo"] for t in aplicadas] + [t["alvo"] for t in aplicadas])

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
                flex=pf is not None,
            )

        if pf is not None:
            # O pedido Flex agora reserva no .sp: `no_sp` sai do saldo Flex da
            # família e o aviso de uma rodada anterior, se houver, apaga. Mesma
            # régua do shipment check (flex_envio.item_no_sp), que recalcula do
            # espelho a cada minuto — os dois concordam.
            novo_de = {t["antigo"]: t["alvo"] for t in aplicadas}
            finais = [novo_de.get(c, c) for c in qtd_por_codigo]
            summary["flex_trocados"] = summary.get("flex_trocados", 0) + 1
            no_sp_final = all(flex_envio.item_no_sp(c) for c in finais)
            await _flex_registrar(
                session,
                pf,
                numero=numero,
                no_sp=no_sp_final,
                alerta=None,
                acao="pedido_sp",
                resultado="ok",
                sku=nota,
                sp_em=trocado_em if no_sp_final else None,
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
