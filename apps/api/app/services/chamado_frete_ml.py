"""Chamado de frete do ML (robô da Ouvidoria, 17/11/2026).

Vinicius: "parou de abrir chamado cobrando ML do frete" — "pode fazer então no servidor".

O painel Reembolso recebe sozinho a diferença de frete do Mercado Livre (frete cobrado
maior que o frete do anúncio, ≥ R$ 5): `refunds_freight_sync` lança a linha tipo
`Logistica` (sem `created_by`). Até 27/09 quem cobrava o ML era o "runner de frete" do
computador do Eduardo, FORA deste repositório: abria no formulário de ajuda e registrava
o chamado pelo `/api/chamados/agent/registrar`. Em 30/09 os chamados do ML saíram do
Eduardo e a IA de Chamado (Mac mini) passou a abrir as consultas — mas ela só abre o que
já está na fila `abrir-ml`; ninguém criava o chamado a partir do Reembolso. De 27/09 a
17/11 nenhum chamado de frete nasceu (72 linhas do ML, R$ 1.183).

Este robô roda de hora em hora e faz duas coisas:

1. **Cria o chamado** de cada linha de frete do ML sem chamado com VENDA nos últimos
   `janela_dias` (o ML nega a diferença de frete depois de 30 dias da venda — 287893,
   10/11) e nunca de antes de 17/11/2026 (`INICIO`, pedido dele): origem `margem`, canal
   robô, abertura `pendente` com o mesmo texto que o robô do Eduardo mandava (frete do
   anúncio, frete cobrado, link do anúncio, medidas e peso).
   A IA de Chamado pega pela fila `abrir-ml` e abre pelo Fale conosco; o nº da consulta
   volta pra linha do Reembolso (`chamados.agent_resultado`). No máximo `por_rodada`
   chamados novos por rodada, das vendas mais velhas pras mais novas (a IA abre uma por
   rodada dela; criar tudo de uma vez só empurraria o resto da fila pra trás).
   Pula (sem criar): conta na lista `contas_fora` (conta do ML bloqueada), pedido
   cancelado no Bling, pedido sem nº da venda, venda abaixo de R$ 79 (o ML nega: a
   cotação do anúncio é mais ou menos metade do que ele cobra nessas vendas — ver
   `refunds_freight_sync.ML_VENDA_MINIMA`; protege as linhas lançadas antes da trava) e
   linha cujo frete, relido agora na conciliação, já não passa de R$ 5 (a linha do
   Reembolso nunca é atualizada depois de lançada — o chamado não pode cobrar o que o
   próprio DaVinci já não vê).
2. **Vigia**: linha de frete do ML ainda no prazo cuja consulta não abriu — a IA desistiu
   (`falhou`) ou o chamado está parado há mais de `horas_parado` sem nº de consulta —
   vira ocorrência na Ouvidoria (Threema no modo ligado). Fecha sozinha quando a
   consulta abre.

Modos: `desligado` não cria nada (nem pelo "Rodar agora": aí só vigia); `silencioso` cria
e registra, sem Threema; `ligado` cria, registra e avisa. Nasce `silencioso`.

Pra uma linha NÃO ganhar chamado: Conferido ou Chamado resolvido no Reembolso. Apagar o
chamado criado por este robô (aba Chamados) marca "Chamado resolvido" na linha
(`ao_apagar_chamado`) — senão a rodada seguinte criaria outro.
"""

from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import Text, and_, cast, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import BlingOrder, Chamado, ChamadoMensagem, OuvidoriaRobo, Refund
from app.models.enums import IntegrationPlatform
from app.models.marketplace_financial import (
    MarketplaceOrderFinancial,
    MarketplaceOrderFreightReconciliation,
)
from app.services import chamados as chamados_svc
from app.services import ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.logistica_meli import _ML_PLATAFORMAS
from app.services.refunds_freight_sync import _MIN_PREJUIZO, ML_VENDA_MINIMA

logger = structlog.get_logger()

ROBO = "chamado_frete_ml"
_SWEEP_LOCK_KEY = 0x63666D6C  # ascii "cfml"
ORIGEM = "margem"
TIPO_REFUND = "Logistica"
_JANELA_DIAS = 30
_POR_RODADA = 5
_HORAS_PARADO = 24
SP = ZoneInfo("America/Sao_Paulo")
# Vinicius 17/11/2026, ao ligar o robô: "ele tenta todas [as contas] que cair pedido de
# hoje para frente" — as linhas antigas (a fila parada desde 27/09) ficam de fora; só
# venda a partir deste dia ganha chamado.
INICIO = datetime(2026, 11, 17, tzinfo=SP)
# Teto de linhas lidas por rodada (as puladas não gastam a cota de `por_rodada`).
_MAX_CANDIDATAS = 500
_MINIMO = Decimal(str(_MIN_PREJUIZO))
_VENDA_MINIMA = Decimal(str(ML_VENDA_MINIMA))

# Motivos de pular uma linha (contadores da rodada).
PULA_CONTA_FORA = "conta_fora"
PULA_CANCELADO = "cancelado"
PULA_SEM_VENDA = "sem_venda"
PULA_VENDA_BAIXA = "venda_abaixo_79"
PULA_SEM_DIFERENCA = "sem_diferenca"
PULA_REPETIDO = "repetido"
_PULOS = (
    PULA_CONTA_FORA,
    PULA_CANCELADO,
    PULA_SEM_VENDA,
    PULA_VENDA_BAIXA,
    PULA_SEM_DIFERENCA,
    PULA_REPETIDO,
)

LINK_ANUNCIO = "https://produto.mercadolivre.com.br/{}"
OBSERVACAO = "Aberto automaticamente: diferença de frete do ML (painel Reembolso)"
CABECALHO = (
    "Solicito revisão e reembolso da diferença de frete do pedido abaixo. "
    "O frete cobrado foi maior que a cotação do anúncio."
)


# ─── texto da abertura ─────────────────────────────────────────────────────


def _moeda(v: Decimal | float | None) -> str:
    if v is None:
        return "?"
    s = f"{Decimal(str(v)).quantize(Decimal('0.01')):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _num(v: Decimal | None) -> str:
    """30.0000 → "30"; 27.5 → "27,5"."""
    if v is None:
        return "?"
    s = f"{Decimal(str(v)).normalize():f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",")


def link_anuncio(item_id: str | None) -> str | None:
    """MLB4803162551 → https://produto.mercadolivre.com.br/MLB-4803162551."""
    item = (item_id or "").strip().upper()
    if not item:
        return None
    if item[:3].isalpha() and item[3:].isdigit():
        item = f"{item[:3]}-{item[3:]}"
    return LINK_ANUNCIO.format(item)


def _medidas(fr: MarketplaceOrderFreightReconciliation) -> str | None:
    """Medidas do pacote como o anúncio declara pro ML (`/shipments/{id}/items`:
    cm e peso em gramas): "30×27×27 cm (C×L×A), 4 kg"."""
    partes: list[str] = []
    if None not in (fr.dimension_length, fr.dimension_width, fr.dimension_height):
        partes.append(
            f"{_num(fr.dimension_length)}×{_num(fr.dimension_width)}×"
            f"{_num(fr.dimension_height)} cm (C×L×A)"
        )
    elif (fr.dimensions_text or "").strip():
        partes.append(fr.dimensions_text.strip())
    if fr.dimension_weight is not None and fr.dimension_weight > 0:
        partes.append(f"{_num(Decimal(fr.dimension_weight) / 1000)} kg")
    return ", ".join(partes) or None


@dataclass
class Abertura:
    """O que a IA vai mandar. `texto` None = o frete relido não passa mais do mínimo
    (não cobrar). `diferenca` None = sem conciliação de frete: vale a do Reembolso."""

    texto: str | None
    diferenca: Decimal | None


def texto_abertura(
    refund: Refund, fretes: list[MarketplaceOrderFreightReconciliation], venda: str
) -> Abertura:
    """O texto que a IA cola no formulário do Fale conosco — o mesmo formato do robô
    do Eduardo (297991). Um item por ENVIO: o frete cobrado é do envio (`MAX` por
    envio, como a view da Margem), o do anúncio é a soma dos itens."""
    envios: OrderedDict[str, list[MarketplaceOrderFreightReconciliation]] = OrderedDict()
    for fr in fretes:
        envios.setdefault(fr.shipping_id or fr.external_order_id, []).append(fr)
    linhas: list[str] = []
    total = Decimal("0")
    tem_frete = False
    for itens in envios.values():
        anuncio = [
            i.freight_promised_amount for i in itens if i.freight_promised_amount is not None
        ]
        cobrado = [i.freight_actual_amount for i in itens if i.freight_actual_amount is not None]
        if not anuncio or not cobrado:
            continue
        tem_frete = True
        v_anuncio = sum(anuncio, Decimal("0"))
        v_cobrado = max(cobrado)
        if v_cobrado <= v_anuncio:
            continue
        total += v_cobrado - v_anuncio
        pedidos = list(OrderedDict.fromkeys(i.external_order_id for i in itens))
        rotulo = (
            f"Pedido {pedidos[0]}"
            if len(pedidos) == 1
            else f"Pedidos {', '.join(pedidos)} (mesmo envio)"
        )
        linha = (
            f"- {rotulo}: frete anúncio {_moeda(v_anuncio)}; frete cobrado {_moeda(v_cobrado)}; "
            f"diferença {_moeda(v_cobrado - v_anuncio)}"
        )
        vistos: set[str] = set()
        for i in itens:
            link = link_anuncio(i.marketplace_item_id)
            if link is None or link in vistos:
                continue
            vistos.add(link)
            medidas = _medidas(i)
            linha += f"\n  link anúncio {link}" + (f", dimensões {medidas}" if medidas else "")
        linhas.append(linha)
    if tem_frete:
        if total < _MINIMO:
            return Abertura(texto=None, diferenca=total)
        return Abertura(texto=CABECALHO + "\n\n" + "\n".join(linhas), diferenca=total)
    return Abertura(
        texto=(
            f"{CABECALHO}\n\n- Pedido {venda}: o frete cobrado foi {_moeda(refund.prejuizo)} "
            "maior que o frete do anúncio"
        ),
        diferenca=None,
    )


async def fretes_do_pedido(
    session: AsyncSession, pedido_bling: str
) -> list[MarketplaceOrderFreightReconciliation]:
    """Itens da conciliação de frete do ML do pedido Bling (pelo `bling_id` do espelho)."""
    ids = select(BlingOrder.bling_id).where(
        BlingOrder.numero == pedido_bling, BlingOrder.bling_id.is_not(None)
    )
    fr = MarketplaceOrderFreightReconciliation
    return list(
        (
            await session.execute(
                select(fr)
                .where(fr.bling_id.in_(ids), fr.platform == IntegrationPlatform.ML)
                .order_by(fr.external_order_id, fr.item_index)
            )
        )
        .scalars()
        .all()
    )


async def bruto_da_venda(session: AsyncSession, pedido_bling: str) -> Decimal | None:
    """Valor bruto da venda do ML (todas as unidades) lido no financeiro do pedido Bling.
    None = financeiro ainda não lido. Com mais de uma leitura, vale a maior — na dúvida o
    chamado nasce."""
    ids = select(BlingOrder.bling_id).where(
        BlingOrder.numero == pedido_bling, BlingOrder.bling_id.is_not(None)
    )
    fin = MarketplaceOrderFinancial
    return (
        await session.execute(
            select(func.max(fin.gross_amount)).where(
                fin.bling_id.in_(ids), fin.platform == IntegrationPlatform.ML
            )
        )
    ).scalar_one_or_none()


# ─── quais linhas ──────────────────────────────────────────────────────────


def _ml_sql():
    return func.lower(func.trim(func.coalesce(Refund.plataforma, ""))).in_(sorted(_ML_PLATAFORMAS))


def _no_prazo_sql(agora: datetime, janela_dias: int):
    """Pela data da VENDA: o ML conta os 30 dias da venda. Linha lançada tarde (o ML
    fechou a cobrança semanas depois; o backfill de 90 dias acha) com venda velha fica
    de fora — o ML negaria. E nunca venda de antes de `INICIO`."""
    desde = max(agora - timedelta(days=janela_dias), INICIO)
    return func.coalesce(Refund.data, Refund.created_at) >= desde


def _chamado_do_refund_sql():
    """Chamado de frete (origem margem) já existente pra linha — o deste robô (pelo id)
    ou um do robô antigo / aberto à mão (pelo pedido Bling ou pelo nº da venda)."""
    return and_(
        Chamado.origem == ORIGEM,
        or_(
            Chamado.origem_ref == cast(Refund.id, Text),
            and_(Refund.pedido_bling.is_not(None), Chamado.pedido_bling == Refund.pedido_bling),
            and_(
                Refund.pedido_marketplace.is_not(None),
                func.trim(Refund.pedido_marketplace) != "",
                Chamado.pedido_marketplace == Refund.pedido_marketplace,
            ),
        ),
    )


def _base_sql(agora: datetime, janela_dias: int) -> list:
    """Linha de frete do ML lançada pelo robô do Reembolso (sem `created_by`), sem nº de
    chamado, que ninguém conferiu, reembolsou ou deu por resolvida, ≥ R$ 5, no prazo."""
    return [
        Refund.tipo == TIPO_REFUND,
        Refund.created_by.is_(None),
        _ml_sql(),
        or_(Refund.chamado.is_(None), func.trim(Refund.chamado) == ""),
        or_(Refund.chamado_url.is_(None), func.trim(Refund.chamado_url) == ""),
        Refund.chamado_resolvido.is_(False),
        Refund.conferido.is_(False),
        Refund.reembolso.is_(None),
        Refund.prejuizo >= _MIN_PREJUIZO,
        Refund.pedido_bling.is_not(None),
        func.trim(Refund.pedido_bling) != "",
        _no_prazo_sql(agora, janela_dias),
    ]


async def linhas_sem_chamado(
    session: AsyncSession, *, agora: datetime, janela_dias: int, limite: int = _MAX_CANDIDATAS
) -> list[Refund]:
    q = (
        select(Refund)
        .where(*_base_sql(agora, janela_dias), ~exists().where(_chamado_do_refund_sql()))
        .order_by(Refund.data.asc().nulls_last(), Refund.created_at.asc(), Refund.id)
        .limit(limite)
    )
    return list((await session.execute(q)).scalars().all())


def _norm_conta(s: str | None) -> str:
    """"ML Velasco" / "velasco" / "Mercado Livre - Velasco" → "velasco"."""
    t = unicodedata.normalize("NFD", s or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    t = re.sub(r"^(mercado ?livre|ml)\s+", "", t)
    return t


def contas_fora(cfg: dict) -> set[str]:
    """Lista da tela ("ML Injox, ML Inova; KFA 2") → nomes normalizados."""
    bruto = str(cfg.get("contas_fora") or "")
    return {n for n in (_norm_conta(p) for p in re.split(r"[,;\n]+", bruto)) if n}


# ─── criar ─────────────────────────────────────────────────────────────────


async def abrir_chamado_frete(session: AsyncSession, refund: Refund) -> Chamado | str:
    """Registra o chamado (canal robô) com a abertura `pendente` na fila da IA, ou devolve
    o motivo de pular (`PULA_*`). Não commita."""
    ch = Chamado(
        data=datetime.now(chamados_svc.SAO_PAULO).date(),
        pedido_bling=(refund.pedido_bling or "").strip() or None,
        pedido_marketplace=(refund.pedido_marketplace or "").strip() or None,
        plataforma="ml",
        conta=(refund.conta or "").strip() or None,
        origem=ORIGEM,
        origem_ref=str(refund.id),
        canal="robo",
        observacao=OBSERVACAO,
    )
    await chamados_svc.preencher_do_pedido(session, ch)
    if "cancel" in (ch.status_bling or "").lower():
        return PULA_CANCELADO
    venda = (ch.pedido_marketplace or "").strip()
    if not venda:
        return PULA_SEM_VENDA  # a IA abre pela página da venda
    bruto = await bruto_da_venda(session, ch.pedido_bling or "")
    if bruto is not None and bruto < _VENDA_MINIMA:
        return PULA_VENDA_BAIXA
    ab = texto_abertura(refund, await fretes_do_pedido(session, ch.pedido_bling or ""), venda)
    if ab.texto is None:
        return PULA_SEM_DIFERENCA
    session.add(ch)
    await session.flush()
    valor = ab.diferenca if ab.diferenca is not None else refund.prejuizo
    session.add(
        chamados_svc.registrar_sistema(
            ch,
            f"Diferença de frete do ML de {_moeda(valor)} lançada no Reembolso — "
            "chamado encaminhado à IA de Chamado pra abrir a consulta no Fale conosco",
        )
    )
    abertura = chamados_svc.nova_mensagem(
        ch,
        texto=ab.texto,
        tipo="abertura",
        direcao="enviada",
        autor_nome=chamados_svc.AUTOR_SISTEMA,
        status="pendente",
    )
    abertura.canal = "robo"
    session.add(abertura)
    await session.flush()
    logger.info(
        "chamado_frete_ml_criado",
        chamado_id=str(ch.id),
        refund_id=str(refund.id),
        pedido_bling=ch.pedido_bling,
        diferenca=str(valor),
    )
    return ch


# ─── vigia ─────────────────────────────────────────────────────────────────


async def _parados(
    session: AsyncSession, *, agora: datetime, janela_dias: int, horas_parado: int
) -> list[tuple[Refund, Chamado, ChamadoMensagem | None]]:
    """Linhas no prazo com chamado de frete aberto e SEM nº de consulta: a abertura
    falhou, ou está na fila há mais de `horas_parado`."""
    rows = (
        await session.execute(
            select(Refund, Chamado)
            .join(Chamado, _chamado_do_refund_sql())
            .where(
                *_base_sql(agora, janela_dias),
                Chamado.resolvido.is_(False),
                or_(Chamado.chamado.is_(None), func.trim(Chamado.chamado) == ""),
                Chamado.created_at < agora - timedelta(hours=horas_parado),
            )
            .order_by(Refund.data.asc().nulls_last())
        )
    ).all()
    out: list[tuple[Refund, Chamado, ChamadoMensagem | None]] = []
    vistos: set[UUID] = set()
    for refund, ch in rows:
        if refund.id in vistos:
            continue
        vistos.add(refund.id)
        out.append((refund, ch, await chamados_svc.abertura_do_chamado(session, ch)))
    return out


def _por_que(ch: Chamado, abertura: ChamadoMensagem | None, agora: datetime) -> str:
    if abertura is not None and abertura.status == "falhou":
        return f"a IA de Chamado não conseguiu abrir: {(abertura.erro or 'sem motivo')[:300]}"
    horas = int((agora - ch.created_at).total_seconds() // 3600)
    if abertura is not None and abertura.status in ("pendente", "enviando"):
        tentativas = f" ({abertura.tentativas} tentativa(s))" if abertura.tentativas else ""
        return f"na fila da IA de Chamado há {horas} h sem abrir{tentativas}"
    return f"chamado registrado há {horas} h e sem nº de consulta"


async def chamado_frete_ml_run(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(UTC)
    async with ouvidoria.Rodada(session, ROBO) as r:
        for k in ("criados", "erros", "na_fila", "parados", "novas", "persistem", *_PULOS):
            r.contadores[k] = 0
        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)
        janela = int(cfg.get("janela_dias") or _JANELA_DIAS)
        por_rodada = int(cfg.get("por_rodada") or _POR_RODADA)
        horas_parado = int(cfg.get("horas_parado") or _HORAS_PARADO)
        fora = contas_fora(cfg)
        # "Rodar agora" roda até desligado (routers/ouvidoria): aí só vigia, não cria.
        pode_criar = await ouvidoria.modo_efetivo(session, ROBO) != "desligado"

        # 1) criar os chamados que faltam (das vendas mais velhas pras mais novas)
        vistos: set[str] = set()
        for refund in await linhas_sem_chamado(session, agora=agora, janela_dias=janela):
            pedido = (refund.pedido_bling or "").strip()
            if pedido in vistos:
                # duas linhas do mesmo pedido (ex.: conta renomeada): um chamado só
                r.contadores[PULA_REPETIDO] += 1
                continue
            vistos.add(pedido)
            if _norm_conta(refund.conta) in fora:
                r.contadores[PULA_CONTA_FORA] += 1
                continue
            if not pode_criar or r.contadores["criados"] >= por_rodada:
                r.contadores["na_fila"] += 1
                continue
            try:
                async with session.begin_nested():
                    feito = await abrir_chamado_frete(session, refund)
            except Exception:  # noqa: BLE001 — uma linha ruim não segura as outras
                logger.exception("chamado_frete_ml_falhou", refund_id=str(refund.id))
                r.contadores["erros"] += 1
                continue
            if isinstance(feito, str):
                r.contadores[feito] += 1
            else:
                r.contadores["criados"] += 1

        # 2) vigia: consulta que não abriu
        for refund, ch, abertura in await _parados(
            session, agora=agora, janela_dias=janela, horas_parado=horas_parado
        ):
            r.contadores["parados"] += 1
            motivo = _por_que(ch, abertura, agora)
            row = await r.registrar(
                chave=f"refund:{refund.id}",
                titulo=(
                    f"Frete do ML sem consulta aberta: pedido {refund.pedido_bling} "
                    f"({refund.conta}, {_moeda(refund.prejuizo)})"
                )[:200],
                plataforma="ml",
                conta=refund.conta,
                detalhe=(
                    f"Diferença de frete de {_moeda(refund.prejuizo)} no pedido "
                    f"{refund.pedido_bling} (venda {refund.pedido_marketplace or '—'}): {motivo}. "
                    f"O ML não aceita a cobrança depois de 30 dias da venda."
                ),
                acao=(
                    "abrir o chamado na aba Chamados, ver o que a IA anotou e abrir a consulta "
                    "no Fale conosco do ML (ou mandar o texto de novo pro robô)"
                ),
                link=f"/chamados?search={refund.pedido_bling}",
                severidade="pessoa",
                precisa_pessoa=True,
                dados={
                    "refund_id": str(refund.id),
                    "chamado_id": str(ch.id),
                    "pedido_bling": refund.pedido_bling,
                    "prejuizo": refund.prejuizo,
                },
                agora=agora,
            )
            if row.fechada_em is None:
                r.contadores["novas" if row.aberta_em == agora else "persistem"] += 1
        r.contadores["sumiram"] = await r.fechar_nao_vistas()

        c = r.contadores
        partes = [
            f"{c['criados']} chamado(s) criado(s)" if pode_criar else "desligado: não cria"
        ]
        if c["na_fila"]:
            partes.append(f"{c['na_fila']} esperando a próxima rodada")
        if c[PULA_CONTA_FORA]:
            partes.append(f"{c[PULA_CONTA_FORA]} de conta fora")
        if c[PULA_CANCELADO]:
            partes.append(f"{c[PULA_CANCELADO]} cancelado(s) no Bling")
        if c[PULA_VENDA_BAIXA]:
            partes.append(f"{c[PULA_VENDA_BAIXA]} de venda abaixo de R$ 79")
        if c[PULA_SEM_DIFERENCA]:
            partes.append(f"{c[PULA_SEM_DIFERENCA]} sem diferença no frete relido")
        if c[PULA_SEM_VENDA]:
            partes.append(f"{c[PULA_SEM_VENDA]} sem nº da venda")
        if c["erros"]:
            partes.append(f"{c['erros']} com erro ao criar (ver log)")
        if c["parados"]:
            partes.append(f"{c['parados']} sem consulta aberta")
        r.resumo = " · ".join(partes)
    aviso = await ouvidoria.avisar_pendentes(session, ROBO, agora=agora)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def chamado_frete_ml_sweep() -> dict[str, Any]:
    """Tick do worker (de hora em hora) e "Rodar agora": sessão própria, serializado por
    advisory lock."""
    async with session_scope() as trava:
        got = (
            await trava.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        if not got:
            return {"skipped": "lock_busy"}
        async with session_scope() as session:
            return await chamado_frete_ml_run(session)


# ─── ganchos da aba Chamados ───────────────────────────────────────────────


async def refund_do_chamado(session: AsyncSession, ch: Chamado) -> Refund | None:
    """Linha do Reembolso que originou o chamado de frete (origem margem + id)."""
    if ch.origem != ORIGEM or not (ch.origem_ref or "").strip():
        return None
    try:
        rid = UUID(str(ch.origem_ref).strip())
    except ValueError:
        return None
    return await session.get(Refund, rid)


async def ao_apagar_chamado(session: AsyncSession, ch: Chamado, autor: str) -> None:
    """Chamado de frete apagado na aba Chamados (lixeira ou Excluir): marca "Chamado
    resolvido" na linha do Reembolso e anota quem apagou — senão a rodada seguinte do
    robô criaria outro chamado pra mesma linha. Não commita."""
    refund = await refund_do_chamado(session, ch)
    if refund is None or (refund.chamado or "").strip() or refund.chamado_resolvido:
        return
    refund.chamado_resolvido = True
    quando = datetime.now(chamados_svc.SAO_PAULO).strftime("%d/%m/%Y")
    nota = f"Chamado de frete apagado na aba Chamados por {autor} em {quando}"
    antes = (refund.observacao or "").strip()
    refund.observacao = f"{antes}\n{nota}" if antes else nota
