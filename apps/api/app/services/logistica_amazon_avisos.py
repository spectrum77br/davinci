"""Amazon Envio próprio — avisos Threema do robô + linhas do botão Informar.

Projeto Amazon (Vinicius, 15/09/2026): no Envio próprio o vendedor posta nos
Correios e RESPONDE pela entrega. Passada a data máxima da Amazon
(`LatestDeliveryDate`, o "Prazo para entrega" do Seller Central) a Amazon
reembolsa o cliente — e aí não dá mais tempo de acionar os Correios nem de
pedir o pacote de volta. Por isso o robô avisa no Threema, uma vez por
pedido e por tipo:

- `previsao_vencida`: a previsão dos Correios (objeto de postagem do Bling)
  passou e o pacote não chegou — é o alerta mais cedo;
- `prazo_3d`: faltam DIAS_ANTES_PRAZO dias para a data máxima da Amazon;
- `prazo_vencido`: a data máxima passou sem entrega.

Quem recebe é o cadastro `logistica_amazon_auto` do modal Informar da aba
Amazon (threema_informar_config) — mesmo mecanismo do aviso automático da
Margem. O botão também manda, sob demanda, a lista dos pedidos de Envio
próprio em trânsito (`linhas_informar`, contexto `logistica_amazon`).

Temu (26/11/2026): a mesma regra vale lá — passado o prazo de entrega a Temu
reembolsa o cliente sem devolução (306833: prazo 17/11, reembolso 18/11, pacote
parado na Anjun desde 07/11). O `logistica_temu` grava a data na mesma coluna
(`prazo_entrega_amazon`) e o robô avisa as mesmas pessoas, com o texto da
Temu. A lista do botão Informar da aba Amazon continua só Amazon.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica, ThreemaInformarConfig
from app.services import logistica_amazon_canal, logistica_rules, threema, threema_registro
from app.services.threema_catalogo import K

logger = structlog.get_logger()

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

CONTEXTO_MANUAL = "logistica_amazon"
CONTEXTO_AUTO = "logistica_amazon_auto"

DIAS_ANTES_PRAZO = 3
JANELA_DIAS = 60
SITUACOES_ENCERRADAS = frozenset({"entregue", "cancelado", "resolvido", "perdimento"})

TIPO_PREVISAO = "previsao_vencida"
TIPO_PRAZO_3D = "prazo_3d"
TIPO_PRAZO_VENCIDO = "prazo_vencido"
# tipo -> coluna-carimbo na linha (um aviso por pedido e por tipo)
_CARIMBO = {
    TIPO_PREVISAO: "aviso_previsao_correios_at",
    TIPO_PRAZO_3D: "aviso_prazo_amazon_3d_at",
    TIPO_PRAZO_VENCIDO: "aviso_prazo_amazon_vencido_at",
}


def hoje_brt() -> date:
    return datetime.now(SAO_PAULO).date()


def _fmt(d: date | None) -> str:
    return d.strftime("%d/%m") if d else "—"


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(SAO_PAULO).strftime("%d/%m %H:%M")


# Temu: entregue (todos os pacotes) ou cancelado. "Entregue em parte" segue
# avisando — o pacote que falta pode estourar o prazo.
_TEMU_ORDER_FIM = frozenset({"CANCELED", "RECEIPTED"})
# Temu já tratada por gente: reembolsada/extraviada vai pra Aguardando
# Devolução ou Problemas no Bling (306833) — o "VENCEU" ali só atrapalha.
_TEMU_SITUACOES_TRATADAS = frozenset({"aguardando devolução", "problemas"})
# "VENCEU" velho não ajuda: a Temu reembolsa logo depois do prazo. Na 1ª rodada
# depois de ligar, prazo vencido há mais que isto não avisa (revisão 26/11).
TEMU_VENCIDO_ATE_DIAS = 3


def eh_temu(row: Logistica) -> bool:
    return (row.plataforma or "").strip().lower() in logistica_rules._TEMU_PLATAFORMAS


def em_transito(row: Logistica) -> bool:
    """Pedido ainda não entregue nem encerrado: Amazon de Envio próprio, ou
    Temu que a Temu não deu como cancelado/entregue."""
    if eh_temu(row):
        ordem = str((row.meli_status or {}).get("order_status") or "").strip().upper()
        if ordem in _TEMU_ORDER_FIM:
            return False
        if (row.status_bling or "").strip().lower() in _TEMU_SITUACOES_TRATADAS:
            return False
    elif (row.amazon_canal or "") != logistica_amazon_canal.CANAL_PROPRIO:
        return False
    if row.entregue_em is not None:
        return False
    return (row.status_bling or "").strip().lower() not in SITUACOES_ENCERRADAS


async def pedidos_em_transito(
    session: AsyncSession, *, temu: bool = False
) -> list[Logistica]:
    """Linhas Amazon de Envio próprio em trânsito na janela (e, com `temu`, as
    da Temu), data máxima mais próxima primeiro (sem data por último)."""
    corte = date.today() - timedelta(days=JANELA_DIAS)
    plataforma = func.lower(func.trim(Logistica.plataforma))
    amazon_proprio = (plataforma.in_(tuple(logistica_rules._AMAZON_PLATAFORMAS))) & (
        Logistica.amazon_canal == logistica_amazon_canal.CANAL_PROPRIO
    )
    quem = (
        or_(amazon_proprio, plataforma.in_(tuple(logistica_rules._TEMU_PLATAFORMAS)))
        if temu
        else amazon_proprio
    )
    rows = (
        await session.execute(
            select(Logistica).where(
                quem,
                Logistica.entregue_em.is_(None),
                or_(Logistica.data.is_(None), Logistica.data >= corte),
            )
        )
    ).scalars().all()
    vivos = [r for r in rows if em_transito(r)]
    vivos.sort(key=lambda r: (r.prazo_entrega_amazon is None, r.prazo_entrega_amazon or date.max))
    return vivos


def _dias_restantes(prazo: date | None, hoje: date) -> str:
    if prazo is None:
        return "sem data"
    d = (prazo - hoje).days
    if d < 0:
        return f"vencido há {-d} dia(s)"
    if d == 0:
        return "vence hoje"
    return f"faltam {d} dia(s)"


def linha_informar(row: Logistica, hoje: date | None = None) -> str:
    """`pedido amazon - conta - rastreio (serviço) - Transportadora: previsão dd/mm -
    Amazon até dd/mm (faltam N dias) - última posição`."""
    hoje = hoje or hoje_brt()
    pedido = (row.pedido_marketplace or "").strip() or (row.pedido_bling or "").strip() or "-"
    rastreio = (row.rastreio or "").strip() or "sem rastreio"
    if row.servico_envio:
        rastreio += f" ({row.servico_envio})"
    partes = [
        pedido,
        (row.conta or "").strip() or "-",
        rastreio,
        f"Transportadora: previsão {_fmt(row.previsao_correios)}",
        f"Amazon até {_fmt(row.prazo_entrega_amazon)} "
        f"({_dias_restantes(row.prazo_entrega_amazon, hoje)})",
    ]
    if row.localizacao:
        partes.append(f"última posição: {row.localizacao}")
    return " - ".join(partes)


def linhas_informar(rows: list[Logistica], hoje: date | None = None) -> list[str]:
    return [linha_informar(r, hoje) for r in rows]


def avisos_devidos(row: Logistica, hoje: date | None = None) -> list[str]:
    """Tipos de aviso que este pedido deve receber HOJE (ainda não carimbados).
    Passada a data máxima só vale `prazo_vencido` (não adianta mais avisar que
    faltam 3 dias)."""
    hoje = hoje or hoje_brt()
    if not em_transito(row):
        return []
    out: list[str] = []
    if (
        row.previsao_correios is not None
        and row.previsao_correios < hoje
        and row.aviso_previsao_correios_at is None
    ):
        out.append(TIPO_PREVISAO)
    prazo = row.prazo_entrega_amazon
    if prazo is not None:
        if prazo < hoje:
            velho = eh_temu(row) and prazo < hoje - timedelta(days=TEMU_VENCIDO_ATE_DIAS)
            if row.aviso_prazo_amazon_vencido_at is None and not velho:
                out.append(TIPO_PRAZO_VENCIDO)
        elif (
            prazo - timedelta(days=DIAS_ANTES_PRAZO) <= hoje
            and row.aviso_prazo_amazon_3d_at is None
        ):
            out.append(TIPO_PRAZO_3D)
    return out


def _cabecalho_temu(tipo: str, row: Logistica, hoje: date) -> str:
    if tipo == TIPO_PRAZO_VENCIDO:
        return "🚨 DaVinci — Temu: prazo de entrega da Temu VENCEU sem entrega"
    d = (row.prazo_entrega_amazon - hoje).days if row.prazo_entrega_amazon else DIAS_ANTES_PRAZO
    if d <= 0:
        return "🚨 DaVinci — Temu: prazo de entrega da Temu vence HOJE"
    return f"⚠️ DaVinci — Temu: faltam {d} dia(s) para o prazo de entrega da Temu"


def mensagem_aviso_temu(row: Logistica, tipo: str, hoje: date | None = None) -> str:
    hoje = hoje or hoje_brt()
    rastreio = (row.rastreio or "").strip() or "sem rastreio"
    posicao = (row.localizacao or "").strip() or "sem evento de rastreio ainda"
    if row.localizacao_at:
        posicao += f" ({_fmt_dt(row.localizacao_at)})"
    linhas = [
        _cabecalho_temu(tipo, row, hoje),
        f"Pedido {row.pedido_bling or '?'} ({row.conta or '?'}) · "
        f"Temu {row.pedido_marketplace or '?'}",
        f"Rastreio: {rastreio}",
        f"Última posição: {posicao}",
        f"Entregar até (Temu): {_fmt(row.prazo_entrega_amazon)} · ainda não entregue",
    ]
    if tipo == TIPO_PRAZO_VENCIDO:
        linhas.append(
            "A Temu reembolsa o cliente sem devolução logo depois do prazo. Cobre a "
            "transportadora e, se a Temu reembolsar, abra a reclamação (resgate) na tela."
        )
    else:
        linhas.append(
            "Passado o prazo a Temu reembolsa o cliente sem devolução. Cobre a "
            "transportadora agora."
        )
    return "\n".join(linhas)


def _cabecalho(tipo: str, row: Logistica, hoje: date) -> str:
    if tipo == TIPO_PREVISAO:
        return (
            "⚠️ DaVinci — Amazon Envio próprio: previsão da transportadora passou "
            "e o pacote não chegou"
        )
    if tipo == TIPO_PRAZO_VENCIDO:
        return "🚨 DaVinci — Amazon Envio próprio: prazo de entrega da Amazon VENCEU sem entrega"
    d = (row.prazo_entrega_amazon - hoje).days if row.prazo_entrega_amazon else DIAS_ANTES_PRAZO
    if d <= 0:
        return "🚨 DaVinci — Amazon Envio próprio: prazo de entrega da Amazon vence HOJE"
    return f"⚠️ DaVinci — Amazon Envio próprio: faltam {d} dia(s) para o prazo de entrega da Amazon"


def mensagem_aviso(row: Logistica, tipo: str, hoje: date | None = None) -> str:
    hoje = hoje or hoje_brt()
    if eh_temu(row):
        return mensagem_aviso_temu(row, tipo, hoje)
    cliente = " · ".join(
        p for p in ((row.cliente_nome or "").strip(), (row.localizacao or "").strip()) if p
    )
    rastreio = (row.rastreio or "").strip() or "sem rastreio"
    if row.servico_envio:
        rastreio += f" ({row.servico_envio})"
    correios = row.localizacao or "sem leitura dos Correios ainda"
    if row.localizacao_at:
        correios += f" ({_fmt_dt(row.localizacao_at)})"
    linhas = [
        _cabecalho(tipo, row, hoje),
        f"Pedido {row.pedido_bling or '?'} ({row.conta or '?'}) · "
        f"Amazon {row.pedido_marketplace or '?'}",
    ]
    if row.cliente_nome:
        linhas.append(f"Cliente: {row.cliente_nome}")
    linhas.append(f"Rastreio: {rastreio}")
    linhas.append(f"Correios: {correios}")
    linhas.append(
        f"Previsão transportadora: {_fmt(row.previsao_correios)} · Entregar até (Amazon): "
        f"{_fmt(row.prazo_entrega_amazon)} · ainda não entregue"
    )
    if tipo == TIPO_PRAZO_VENCIDO:
        linhas.append(
            "A Amazon pode reembolsar o cliente a qualquer momento. Confira nos Correios e "
            "trate o pedido agora."
        )
    else:
        linhas.append(
            "Passado o prazo a Amazon reembolsa o cliente. Acione os Correios ou peça o "
            "retorno do pacote agora."
        )
    del cliente  # (cliente + posição já aparecem em linhas próprias)
    return "\n".join(linhas)


async def recipients_auto(session: AsyncSession) -> list[str]:
    row = (
        await session.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == CONTEXTO_AUTO)
        )
    ).scalar_one_or_none()
    return threema.parse_recipients(row.recipients if row else "")


async def run(
    session: AsyncSession, *, hoje: date | None = None, client: Any = None
) -> dict[str, int]:
    """Uma rodada do robô: manda os avisos devidos e carimba cada um. Sem
    destinatários cadastrados não manda (e não carimba — quando alguém for
    cadastrado, os avisos pendentes saem na rodada seguinte)."""
    hoje = hoje or hoje_brt()
    resumo = {"pedidos": 0, "avisos": 0, "enviados": 0, "falhas": 0, "sem_destinatarios": 0}
    rows = await pedidos_em_transito(session, temu=True)
    resumo["pedidos"] = len(rows)
    pendentes = [(r, t) for r in rows for t in avisos_devidos(r, hoje)]
    resumo["avisos"] = len(pendentes)
    if not pendentes:
        return resumo
    recipients = await recipients_auto(session)
    if not recipients:
        resumo["sem_destinatarios"] = 1
        logger.warning("logistica_amazon_avisos_sem_destinatarios", pendentes=len(pendentes))
        return resumo
    client = client or threema.ThreemaClient(contexto="logistica")
    agora = datetime.now(UTC)
    for row, tipo in pendentes:
        texto = mensagem_aviso(row, tipo, hoje)
        try:
            with threema_registro.envio(K.LOGISTICA_AMAZON_PRAZOS):
                res = await client.send_to_all(texto, recipients)
        except Exception as e:  # noqa: BLE001 — Threema fora do ar: tenta na próxima
            resumo["falhas"] += 1
            logger.warning(
                "logistica_amazon_aviso_falhou",
                pedido=row.pedido_bling,
                tipo=tipo,
                err=str(e)[:200],
            )
            continue
        if res.get("sent"):
            setattr(row, _CARIMBO[tipo], agora)
            resumo["enviados"] += 1
            logger.info(
                "logistica_amazon_aviso_enviado",
                pedido=row.pedido_bling,
                tipo=tipo,
                sent=res.get("sent"),
                failed=res.get("failed"),
            )
        else:
            resumo["falhas"] += 1
    await session.commit()
    return resumo
