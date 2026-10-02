"""Devoluções da Amazon na Logística, pelo relatório da SP-API (02/10/2026).

701-7824777-7251447 (KFA, envio pelo vendedor): a cliente pediu devolução em
29/09 ("Não é mais necessário"), a Amazon reembolsou no primeiro escaneamento e
a mala voltou pro galpão — e o DaVinci não sabia de nada: a API de pedidos não
mostra devolução (o pedido segue "Entregue ao cliente"). Quem mostra é a tela
"Gerenciar devoluções" do Seller Central e o relatório dela,
GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE. O teste de 02/10 (12:26 UTC) provou
que ele vem nas 4 contas, KFA inclusive, sem o papel restrito.

Vinicius, 02/10: "consegue montar algo que troque o status na logística,
atualize o rastreio tudo certinho?" — com os nomes "Devolução solicitada /
a caminho / recebida" e a troca no Bling PELA REGRA que ele cria na aba Status.
Então este módulo só:

1. baixa o relatório (60 dias) de cada conta, de hora em hora, dentro da
   varredura da Amazon (`logistica_ingest.sweeps_pos_venda`) — quem mudou passa
   pelos executores da aba Status na mesma rodada;
2. grava na linha da Logística `return_status` (4ª parte da assinatura) e os
   detalhes `return_*` (balão), e a Localização passa a descrever a VOLTA;
3. entrega ao rastreio de devoluções (`devolucao_rastreio_sync`) o código da
   volta (`returns_por_pedido`), que o 17track segue como no ML.

Estado da devolução:
- **solicitada** (PENDING): pedido aprovado, pacote ainda não postado;
- **a caminho** (SHIPPED): a Amazon já reembolsou no 1º escaneamento (só
  acontece depois da postagem) ou os Correios já registraram a volta;
- **recebida** (DELIVERED): a Amazon deu a data de entrega da devolução ou o
  17track viu o pacote da volta entregue;
- **reembolso sem devolução** (REFUND_ONLY): `Returnless` — o cliente fica com
  o produto, não vem pacote.
Pedido recusado/cancelado pela Amazon tira a devolução da linha.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DevolucaoRastreio, Integration, IntegrationPlatform, Logistica
from app.services import logistica_datas, logistica_rules, logistica_track
from app.services.devolucao_returns import ReturnInfo
from app.services.logistica_amazon import _build_amazon_client

logger = structlog.get_logger(__name__)

RELATORIO = "GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE"
# Pela data do PEDIDO de devolução: 60 dias cobre a volta inteira (prazo da
# etiqueta + Correios). Devolução mais velha que isso fica como foi lida.
JANELA_DIAS = 60

_BRT = ZoneInfo("America/Sao_Paulo")

PEDIDA = logistica_rules.AMAZON_DEVOLUCAO_PEDIDA
A_CAMINHO = logistica_rules.AMAZON_DEVOLUCAO_A_CAMINHO
RECEBIDA = logistica_rules.AMAZON_DEVOLUCAO_RECEBIDA
SEM_PACOTE = logistica_rules.AMAZON_DEVOLUCAO_SEM_PACOTE
CAMPOS = logistica_rules.AMAZON_DEVOLUCAO_CAMPOS

# `Return Reason` do relatório → PT (o que a Amazon mostra em "Motivos da
# devolução"). Código desconhecido aparece cru — nunca some.
MOTIVOS_PT: dict[str, str] = {
    "CR-UNWANTED_ITEM": "Não é mais necessário",
    "CR-MISSING_PARTS": "Faltando peças",
    "CR-DEFECTIVE": "Com defeito",
    "CR-DAMAGED_BY_CARRIER": "Danificado no transporte",
    "CR-DAMAGED_BY_FC": "Danificado antes do envio",
    "CR-ORDERED_WRONG_ITEM": "Comprou o item errado",
    "CR-SWITCHEROO": "Item diferente do pedido",
    "CR-NOT_COMPATIBLE": "Não é compatível",
    "CR-QUALITY_UNACCEPTABLE": "Qualidade abaixo do esperado",
    "CR-FOUND_BETTER_PRICE": "Achou preço melhor",
    "CR-EXTRA_ITEM": "Recebeu item a mais",
    "CR-MISSED_ESTIMATED_DELIVERY": "Chegou atrasado",
    "CR-UNAUTHORIZED_PURCHASE": "Compra não autorizada",
    "CR-NO_REASON_GIVEN": "Sem motivo informado",
    "AMZ-PG-BAD-DESC": "Diferente da descrição do anúncio",
    "AMZ-PG-MISORDERED": "Comprou o item errado",
    "AMZ-PG-APP-TOO-LATE": "Chegou atrasado",
}
# `Resolution` → como o dinheiro volta pro cliente.
RESOLUCOES_PT: dict[str, str] = {
    "REFUNDATFIRSTSCAN": "Na postagem da volta",
    "STANDARDREFUND": "Quando o pacote chegar",
    "RETURNLESSREFUND": "Sem devolução do produto",
    "REPLACEMENT": "Troca",
}
ETIQUETA_PAGA_POR_PT: dict[str, str] = {"CUSTOMER": "Cliente", "SELLER": "Loja", "AMAZON": "Amazon"}
# `Return request status` que diz "não vai ter devolução".
_STATUS_SEM_DEVOLUCAO = ("REJECT", "CANCEL", "DENIED", "DECLINED")

_MESES = {
    "jan": 1, "feb": 2, "fev": 2, "mar": 3, "apr": 4, "abr": 4, "may": 5, "mai": 5,
    "jun": 6, "jul": 7, "aug": 8, "ago": 8, "sep": 9, "set": 9, "oct": 10, "out": 10,
    "nov": 11, "dec": 12, "dez": 12,
}


def data_do_relatorio(texto: str | None) -> date | None:
    """`29-Sep-2026` (o formato do relatório) ou ISO → data. Sem locale."""
    t = (texto or "").strip()
    if not t:
        return None
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})", t)
    if m and m.group(2).lower() in _MESES:
        try:
            return date(int(m.group(3)), _MESES[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _valor(texto: str | None) -> Decimal | None:
    t = (texto or "").strip().replace(",", ".")
    if not t:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _br(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _iso_dia(d: date | None) -> str | None:
    """Meio-dia de Brasília do dia (o relatório só dá o dia) → ISO UTC."""
    if d is None:
        return None
    return datetime.combine(d, time(12, 0), tzinfo=_BRT).astimezone(UTC).isoformat()


@dataclass
class Devolucao:
    """O que o relatório diz da devolução de UM pedido (várias linhas = vários
    itens; vale a mais recente pro status, o reembolso soma)."""

    pedido: str
    conta: str
    solicitada_em: date | None = None
    status_pedido: str = ""
    motivo: str = ""
    resolucao: str = ""
    rastreio: str = ""
    transportadora: str = ""
    etiqueta_paga_por: str = ""
    entregue_em: date | None = None
    reembolsado: Decimal = field(default_factory=lambda: Decimal("0"))
    rma: str = ""

    @property
    def cancelada(self) -> bool:
        st = self.status_pedido.upper()
        return any(p in st for p in _STATUS_SEM_DEVOLUCAO)

    @property
    def sem_pacote(self) -> bool:
        return "RETURNLESS" in self.resolucao.upper()


def ler_relatorio(tsv: str, conta: str) -> dict[str, Devolucao]:
    """TSV do relatório → {Order ID: Devolucao}."""
    out: dict[str, Devolucao] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        g = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        pedido = g.get("order id", "")
        if not pedido:
            continue
        pedida = data_do_relatorio(g.get("return request date"))
        d = out.get(pedido)
        if d is None:
            d = out[pedido] = Devolucao(pedido=pedido, conta=conta)
        d.reembolsado += _valor(g.get("refunded amount")) or Decimal("0")
        entregue = data_do_relatorio(g.get("return delivery date"))
        if entregue and (d.entregue_em is None or entregue > d.entregue_em):
            d.entregue_em = entregue
        # A linha mais recente manda no status/motivo/rastreio.
        if d.solicitada_em is None or (pedida and pedida >= d.solicitada_em):
            d.solicitada_em = pedida or d.solicitada_em
            d.status_pedido = g.get("return request status", "") or d.status_pedido
            d.motivo = g.get("return reason", "") or d.motivo
            d.resolucao = g.get("resolution", "") or d.resolucao
            d.rastreio = g.get("tracking id", "") or d.rastreio
            d.transportadora = g.get("return carrier", "") or d.transportadora
            d.etiqueta_paga_por = g.get("label to be paid by", "") or d.etiqueta_paga_por
            d.rma = g.get("amazon rma id", "") or d.rma
    return out


def estado(d: Devolucao, rastreio: DevolucaoRastreio | None) -> str | None:
    """PENDING/SHIPPED/DELIVERED/REFUND_ONLY, ou None (sem devolução)."""
    if d.cancelada:
        return None
    if d.sem_pacote:
        return SEM_PACOTE
    if d.entregue_em or (rastreio is not None and rastreio.pacote_entregue_em):
        return RECEBIDA
    reembolsou_na_postagem = "FIRSTSCAN" in d.resolucao.upper() and d.reembolsado > 0
    correios_viram = bool(
        rastreio is not None
        and rastreio.localizacao_auto
        and not logistica_track.evento_pre_postagem(rastreio.localizacao_auto)
    )
    if reembolsou_na_postagem or correios_viram:
        return A_CAMINHO
    return PEDIDA


def campos_da_devolucao(d: Devolucao, st: str) -> dict[str, str]:
    """As chaves `return_*` do `meli_status` (o que o balão mostra)."""
    motivo = d.motivo.strip()
    campos = {
        "return_status": st,
        "return_reason": MOTIVOS_PT.get(motivo.upper(), motivo),
        "return_resolution": RESOLUCOES_PT.get(d.resolucao.upper(), d.resolucao),
        "return_tracking": d.rastreio,
        "return_carrier": d.transportadora,
        "return_label_payer": ETIQUETA_PAGA_POR_PT.get(
            d.etiqueta_paga_por.upper(), d.etiqueta_paga_por
        ),
        "return_refunded": f"R$ {d.reembolsado:.2f}".replace(".", ",") if d.reembolsado else "",
        "return_requested_at": _br(d.solicitada_em),
        "return_delivered_at": _br(d.entregue_em),
    }
    return {k: v for k, v in campos.items() if v}


def localizacao(meli: dict[str, str], rastreio: DevolucaoRastreio | None) -> str:
    """A coluna Localização descreve a VOLTA: "Devolução a caminho · <último
    evento dos Correios>" ou, sem evento ainda, "· rastreio da volta <código>"."""
    st = (meli.get("return_status") or "").strip().upper()
    texto = logistica_rules.AMAZON_DEVOLUCAO_LABELS_PT.get(st, "")
    if not texto:
        return ""
    evento = (rastreio.localizacao_auto or "").strip() if rastreio is not None else ""
    codigo = (meli.get("return_tracking") or "").strip()
    if evento:
        return f"{texto} · {evento}"
    if codigo and st in (PEDIDA, A_CAMINHO):
        return f"{texto} · rastreio da volta {codigo}"
    return texto


async def _baixar(
    session: AsyncSession, contas: list[Integration]
) -> tuple[dict[str, Devolucao], list[str]]:
    """Relatório de cada conta, ao mesmo tempo. Devolve (devoluções, contas que
    responderam) — conta que falhou não mexe nas linhas dela nesta rodada."""
    fim = datetime.now(UTC).replace(microsecond=0)
    inicio = fim - timedelta(days=JANELA_DIAS)
    trava = asyncio.Lock()  # token renovado: um flush por vez na mesma sessão

    async def _uma(integ: Integration) -> tuple[str, dict[str, Devolucao] | None]:
        conta = (integ.name or "").strip().lower() or str(integ.id)
        try:
            client = _build_amazon_client(session, integ, lock=trava)
            tsv = await client.baixar_relatorio(
                RELATORIO, data_inicio=inicio, data_fim=fim, max_poll_attempts=60
            )
            return conta, ler_relatorio(tsv, conta)
        except Exception as e:  # noqa: BLE001 — uma conta não derruba as outras
            logger.warning("amazon_devolucoes_conta_falhou", conta=conta, erro=str(e)[:300])
            return conta, None

    devolucoes: dict[str, Devolucao] = {}
    ok: list[str] = []
    for conta, lidas in await asyncio.gather(*(_uma(i) for i in contas)):
        if lidas is None:
            continue
        ok.append(conta)
        devolucoes.update(lidas)
    return devolucoes, ok


async def aplicar(session: AsyncSession, devolucoes: dict[str, Devolucao]) -> list[UUID]:
    """Grava a devolução nas linhas Amazon dos pedidos; devolve os ids cuja
    assinatura/detalhe/localização mudou (esses passam pelas regras da aba
    Status)."""
    if not devolucoes:
        return []
    linhas = (
        await session.execute(
            select(Logistica).where(
                func.lower(func.trim(Logistica.plataforma)).in_(
                    tuple(logistica_rules._AMAZON_PLATAFORMAS)
                ),
                Logistica.pedido_marketplace.in_(list(devolucoes)),
            )
        )
    ).scalars().all()
    pedidos_bling = [lin.pedido_bling for lin in linhas if lin.pedido_bling]
    rastreios: dict[str, DevolucaoRastreio] = {}
    if pedidos_bling:
        rastreios = {
            r.pedido_bling: r
            for r in (
                await session.execute(
                    select(DevolucaoRastreio).where(
                        DevolucaoRastreio.pedido_bling.in_(pedidos_bling)
                    )
                )
            ).scalars().all()
        }

    mudaram: list[UUID] = []
    for lin in linhas:
        d = devolucoes.get((lin.pedido_marketplace or "").strip())
        if d is None:
            continue
        rastreio = rastreios.get(lin.pedido_bling or "")
        velho = dict(lin.meli_status or {})
        novo = {k: v for k, v in velho.items() if k not in CAMPOS}
        st = estado(d, rastreio)
        if st is not None:
            novo.update(campos_da_devolucao(d, st))
        loc = localizacao(novo, rastreio)
        if novo == velho and (not loc or loc == lin.localizacao):
            continue
        if novo != velho:
            # Data OFICIAL do estado quando o relatório dá: o dia do pedido de
            # devolução / o da entrega; "a caminho" é visto pelo DaVinci.
            dia = d.entregue_em if st == RECEBIDA else d.solicitada_em if st == PEDIDA else None
            propostas = (
                {"return_status": {"em": _iso_dia(dia), "fonte": logistica_datas.FONTE_PLATAFORMA}}
                if dia and novo.get("return_status") != velho.get("return_status")
                else None
            )
            lin.status_datas = logistica_datas.aplicar(lin, novo, propostas)
            lin.meli_status = novo
        if loc:
            # Igual ao ML com devolução: a coluna descreve a volta; a leitura
            # física e a divergência eram do envio de IDA.
            lin.localizacao = loc
            lin.localizacao_at = None
            lin.divergencia = None
        mudaram.append(lin.id)
    await session.flush()
    return mudaram


async def sincronizar(session: AsyncSession) -> dict:
    """Uma rodada: relatório das contas → linhas da Logística. Commita.
    `ids` = linhas que mudaram (a varredura passa nas regras da aba Status)."""
    contas = (
        await session.execute(
            select(Integration)
            .where(
                Integration.platform == IntegrationPlatform.AMAZON,
                Integration.archived_at.is_(None),
            )
            .order_by(Integration.name)
        )
    ).scalars().all()
    devolucoes, ok = await _baixar(session, list(contas))
    ids = await aplicar(session, devolucoes)
    await session.commit()  # token renovado + linhas
    resumo = {
        "contas_ok": len(ok),
        "contas": len(contas),
        "devolucoes": len(devolucoes),
        "linhas_mudaram": len(ids),
    }
    logger.info("amazon_devolucoes_sync", **resumo)
    return {"ids": ids, **resumo}


# ── Rastreio da volta (devolucao_rastreio_sync) ────────────────────────────


def _dt(texto: str | None) -> datetime | None:
    try:
        d = datetime.strptime((texto or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None
    return datetime.combine(d, time(12, 0), tzinfo=_BRT).astimezone(UTC)


def _carimbo(lin: Logistica) -> datetime | None:
    em = ((lin.status_datas or {}).get("return_status") or {}).get("em")
    try:
        return datetime.fromisoformat(str(em)) if em else None
    except ValueError:
        return None


async def returns_por_pedido(
    session: AsyncSession, linhas: list[Logistica]
) -> dict[str, ReturnInfo]:
    """Contrato do `devolucao_rastreio_sync`: o que a linha da Logística já sabe
    da devolução (gravado por `sincronizar`) → ReturnInfo. Sem chamada à
    Amazon — o relatório já veio na varredura."""
    out: dict[str, ReturnInfo] = {}
    for lin in linhas:
        m = lin.meli_status or {}
        st = (m.get("return_status") or "").strip().upper()
        if not st or not lin.pedido_bling:
            continue
        valor = _valor((m.get("return_refunded") or "").replace("R$", "").strip())
        pedida = _dt(m.get("return_requested_at"))
        entregue = _dt(m.get("return_delivered_at"))
        resolucao = (m.get("return_resolution") or "").strip()
        out[lin.pedido_bling] = ReturnInfo(
            fonte="amazon",
            status=st,
            tracking=(m.get("return_tracking") or "").strip() or None,
            carrier=(m.get("return_carrier") or "").strip() or None,
            created_at=pedida,
            updated_at=entregue or _carimbo(lin) or pedida,
            entregue_em=entregue,
            return_type="REFUND" if st == SEM_PACOTE else None,
            reembolso=True if valor else None,
            reembolso_valor=valor or None,
            reembolso_detalhe=f"Amazon — reembolso: {resolucao.lower()}" if resolucao else None,
        )
    return out
