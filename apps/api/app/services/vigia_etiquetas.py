"""Vigia de Etiquetas — o robô da Ouvidoria que cobra o pedido que caiu e não
teve a etiqueta no prazo.

Vinicius, 14/10/2026: "verificar se todas as etiquetas foram emitidas e
alertar". A regra é dele, conta a conta, pelo Horário Etiqueta da tela Lojas:

- **Loja com horário** (hoje: as ML agência, 12:00): o prazo é o primeiro
  horário cadastrado DEPOIS que o pedido caiu + 1h30 (`prazo_min`). Caiu às
  09:00 → cobra às 13:30; caiu às 12:20 → entra no lote do dia seguinte.
  Sábado vale só o horário de sábado e só pro pedido cujos itens saem todos
  dos estoques marcados (a mesma régua do automático da NF,
  `nf_auto_enfileirar.pedido_sai_no_sabado`); o resto vai pro próximo dia
  útil. Domingo não tem horário.
- **Sem horário (contínuo)**: Shopee, TikTok, ML correios, Temu, Shein,
  Magalu, AliExpress e Amazon — o prazo é 1h30 depois que o pedido caiu no
  DaVinci (`bling_orders.created_at`), todo dia. A Amazon fica contínua de
  propósito ("se não está emitindo sempre que cai, eu vou cobrar o menino").

Só olha pedido de loja com cadastro de Etiqueta (Carrefour/Netshoes ficam de
fora) que ainda está "Em aberto" (6): quando a etiqueta sobe, o pedido vai
pra "Em digitação"; cancelado, Aguardando Cancelamento (sem estoque, trava da
Margem) e o resto já não é assunto de etiqueta. "Etiqueta saiu" = PDF da
etiqueta no DaVinci (`nf_etiqueta_arquivo` com conteúdo — a linha pode nascer
só com a NF) ou `nf_faturamento.status_etiqueta = 'ok'`.

Ocorrência de ESTADO (`pedido:<numero>`): a rodada re-vê enquanto o pedido
continua sem etiqueta e fecha sozinha quando ela chega (ou o pedido sai de Em
aberto). Só lê banco e só AVISA: nada aqui mexe em pedido nem na fila.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import (
    BlingOrder,
    NfEtiquetaArquivo,
    NfFaturamento,
    OuvidoriaRobo,
    StoreInfo,
)
from app.services import ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.bling_situacoes import SITUACAO_EM_ABERTO
from app.services.nf_auto_enfileirar import pedido_sai_no_sabado, tags_de

logger = structlog.get_logger()

ROBO = "vigia_etiquetas"

# Advisory lock do sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x76657467  # ascii "vetg"

# Padrões quando a config do robô não tem a chave (a linha de ouvidoria_robos
# nasce com estes mesmos valores — services/ouvidoria.ROBOS).
_PRAZO_MIN = 90
_JANELA_DIAS = 7

_TZ_BR = ZoneInfo("America/Sao_Paulo")
_SABADO = 5
_DOMINGO = 6

LINK_PAINEL = "/nf-faturamento"

ACAO_COBRAR = (
    "Ver no Faturamento NF (Cadastros › NF Faturador › Faturamento NF) em que "
    "etapa o pedido parou e cobrar quem tira a etiqueta dessa conta"
)
ACAO_TRAVADO = (
    "Pedido travado — o automático não tenta de novo sozinho: resolver o motivo "
    "(estoque, erro da nota, restrição) e enfileirar de novo no Faturamento NF"
)

_ROTULO_PLATAFORMA = {
    "ml": "Mercado Livre",
    "shopee": "Shopee",
    "tiktok": "TikTok",
    "amazon": "Amazon",
    "magalu": "Magalu",
    "shein": "Shein",
    "temu": "Temu",
    "aliexpress": "AliExpress",
}

_CONTADORES = ("pedidos_sem_etiqueta", "no_prazo", "fora_do_prazo", "novas", "persistem", "sumiram")


def _br(dt: datetime) -> str:
    return dt.astimezone(_TZ_BR).strftime("%d/%m %H:%M")


def _hm(t: time) -> str:
    return t.strftime("%H:%M")


def _idade(minutos: int) -> str:
    if minutos < 90:
        return f"{minutos} min"
    if minutos < 48 * 60:
        return f"{minutos // 60} h"
    return f"{minutos // 1440} dias"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def horarios_de(txt: str | None) -> list[time]:
    """"12:00, 14:30" → [12:00, 14:30] (ordenados). Vazio/NULL → [] (contínuo).
    Pedaço que não é hora é ignorado — a tela só grava HH:MM."""
    out: list[time] = []
    for parte in (txt or "").split(","):
        parte = parte.strip()
        if not parte:
            continue
        try:
            hh, _, mm = parte.partition(":")
            out.append(time(int(hh), int(mm or 0)))
        except ValueError:
            continue
    return sorted(set(out))


@dataclass(frozen=True)
class Inicio:
    """De onde o prazo conta: o instante e a frase que explica a regra."""

    em: datetime
    regra: str


def inicio_da_contagem(
    caiu: datetime,
    *,
    horarios: list[time],
    sabado: time | None,
    sai_no_sabado: bool,
) -> Inicio | None:
    """Quando a etiqueta do pedido deveria começar a sair (o prazo soma
    `prazo_min` a isto). Sem horário = contínuo: conta de quando caiu.

    Com horário: o primeiro horário >= hora em que caiu, olhando até 8 dias
    pra frente — seg–sex os `horarios`; sábado só `sabado`, e só se o pedido
    sai no sábado; domingo nada. None = nenhum horário à frente (loja com só
    horário de sábado e pedido que não sai no sábado)."""
    if not horarios:
        return Inicio(em=caiu, regra="contínuo: 1h30 depois que caiu")
    caiu_br = caiu.astimezone(_TZ_BR)
    for d in range(8):
        dia: date = caiu_br.date() + timedelta(days=d)
        dia_semana = dia.weekday()
        if dia_semana == _DOMINGO:
            continue
        if dia_semana == _SABADO:
            slots = [sabado] if sabado is not None and sai_no_sabado else []
        else:
            slots = horarios
        for h in slots:
            em = datetime.combine(dia, h, tzinfo=_TZ_BR)
            if em >= caiu_br:
                if dia_semana == _SABADO:
                    regra = f"sábado {_hm(h)} + 1h30"
                else:
                    regra = f"horário {_hm(h)} + 1h30"
                return Inicio(em=em, regra=regra)
    return None


def etapa(
    status_faturamento: str | None,
    erro_faturamento: str | None,
    status_etiqueta: str | None,
    erro_etiqueta: str | None,
) -> tuple[str, str]:
    """(onde o pedido parou, ação) em linguagem de operação, pelo que o fluxo
    de NF gravou em `nf_faturamento`."""
    st_fat = (status_faturamento or "").strip().lower()
    st_etq = (status_etiqueta or "").strip().lower()
    if st_etq == "erro":
        return f"etiqueta deu erro: {erro_etiqueta or 'sem detalhe'}", ACAO_COBRAR
    if st_fat == "sem_estoque":
        return "sem estoque quando a nota ia sair", ACAO_TRAVADO
    if st_fat == "restricao":
        return f"restrição: {erro_faturamento or 'sem detalhe'}", ACAO_TRAVADO
    if st_fat == "erro":
        return f"nota deu erro: {erro_faturamento or 'sem detalhe'}", ACAO_TRAVADO
    if st_fat == "ok":
        return "nota emitida, etiqueta não chegou", ACAO_COBRAR
    if st_fat == "processando":
        return "na fila do robô", ACAO_COBRAR
    return "não passou pela fila da nota do DaVinci", ACAO_COBRAR


async def _registrar(r: ouvidoria.Rodada, agora: datetime, **campos) -> None:
    """`r.registrar` + os contadores novas/persistem (ocorrência que o núcleo
    devolveu fechada — ignorada, ou tratada há < 24 h — não conta)."""
    row = await r.registrar(agora=agora, **campos)
    if row.fechada_em is not None:
        return
    if row.aberta_em == agora:
        r.contadores["novas"] += 1
    else:
        r.contadores["persistem"] += 1


async def _pedidos_sem_etiqueta(session: AsyncSession, *, desde: datetime) -> list:
    """Pedidos Em aberto, de loja com cadastro de Etiqueta, que caíram depois
    de `desde` e não têm etiqueta. Uma linha por pedido (bling_orders tem uma
    por item: `created_at` mínimo e a lista dos SKUs)."""
    tem_pdf = (
        select(NfEtiquetaArquivo.id)
        .where(
            NfEtiquetaArquivo.pedido_bling == BlingOrder.numero,
            NfEtiquetaArquivo.size_bytes > 0,
        )
        .correlate(BlingOrder)
        .exists()
    )
    return (
        await session.execute(
            select(
                BlingOrder.numero,
                func.min(BlingOrder.created_at).label("caiu"),
                func.max(BlingOrder.numeroloja).label("numeroloja"),
                func.array_agg(BlingOrder.item_codigo).label("skus"),
                func.lower(StoreInfo.platform).label("plataforma"),
                StoreInfo.account_name,
                StoreInfo.etiqueta_horarios,
                StoreInfo.etiqueta_sabado_horario,
                StoreInfo.etiqueta_sabado_tags,
                NfFaturamento.status_faturamento,
                NfFaturamento.erro_faturamento,
                NfFaturamento.status_etiqueta,
                NfFaturamento.erro_etiqueta,
            )
            .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
            .join(
                NfFaturamento,
                NfFaturamento.pedido_bling == BlingOrder.numero,
                isouter=True,
            )
            .where(
                BlingOrder.situacao == str(SITUACAO_EM_ABERTO),
                BlingOrder.numero.is_not(None),
                StoreInfo.archived_at.is_(None),
                StoreInfo.nf_etiqueta_id.is_not(None),
                func.coalesce(NfFaturamento.status_etiqueta, "") != "ok",
                ~tem_pdf,
            )
            .group_by(
                BlingOrder.numero,
                StoreInfo.id,
                NfFaturamento.id,
            )
            .having(func.min(BlingOrder.created_at) >= desde)
            .order_by(func.min(BlingOrder.created_at))
        )
    ).all()


async def vigia_etiquetas_run(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    """Uma varredura dentro de uma `Rodada`. Erro derruba a rodada inteira
    (ok=False) sem fechar nada: "não consegui olhar" nunca vira "sumiu"."""
    async with ouvidoria.Rodada(session, ROBO) as r:
        for k in _CONTADORES:
            r.contadores[k] = 0
        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)
        prazo = timedelta(minutes=int(cfg.get("prazo_min") or _PRAZO_MIN))
        janela = timedelta(days=int(cfg.get("janela_dias") or _JANELA_DIAS))
        agora = agora or datetime.now(UTC)

        linhas = await _pedidos_sem_etiqueta(session, desde=agora - janela)
        r.contadores["pedidos_sem_etiqueta"] = len(linhas)
        for p in linhas:
            caiu = p.caiu if p.caiu.tzinfo else p.caiu.replace(tzinfo=UTC)
            inicio = inicio_da_contagem(
                caiu,
                horarios=horarios_de(p.etiqueta_horarios),
                sabado=next(iter(horarios_de(p.etiqueta_sabado_horario)), None),
                sai_no_sabado=pedido_sai_no_sabado(
                    list(p.skus or []), tags_de(p.etiqueta_sabado_tags)
                ),
            )
            if inicio is None or agora <= inicio.em + prazo:
                r.contadores["no_prazo"] += 1
                continue
            r.contadores["fora_do_prazo"] += 1
            vence = inicio.em + prazo
            atraso_min = int((agora - vence).total_seconds() // 60)
            onde, acao = etapa(
                p.status_faturamento, p.erro_faturamento, p.status_etiqueta, p.erro_etiqueta
            )
            rotulo = _ROTULO_PLATAFORMA.get(p.plataforma, (p.plataforma or "").title())
            await _registrar(
                r,
                agora,
                chave=f"pedido:{p.numero}",
                plataforma=p.plataforma,
                conta=f"{rotulo} {(p.account_name or '').strip()}".strip(),
                pedido=p.numero,
                titulo=f"Sem etiqueta — prazo {_br(vence)} passou há {_idade(atraso_min)}",
                detalhe=f"Caiu {_br(caiu)} · {inicio.regra} · {onde}",
                acao=acao,
                link=LINK_PAINEL,
                severidade="pessoa",
                precisa_pessoa=True,
                dados={
                    "pedido_marketplace": p.numeroloja,
                    "caiu_em": caiu.isoformat(),
                    "prazo": vence.isoformat(),
                    "regra": inicio.regra,
                    "etapa": onde,
                    "atraso_min": atraso_min,
                },
            )

        # Todas as ocorrências deste robô são de ESTADO: o que a rodada não
        # re-viu (etiqueta chegou, pedido saiu de Em aberto), sumiu.
        r.contadores["sumiram"] = await r.fechar_nao_vistas()

        partes = [_plural(len(linhas), "pedido sem etiqueta", "pedidos sem etiqueta")]
        if r.contadores["fora_do_prazo"]:
            partes.append(f"{r.contadores['fora_do_prazo']} fora do prazo")
        else:
            partes.append("todos no prazo")
        r.resumo = " · ".join(partes)

    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def vigia_etiquetas_sweep() -> dict:
    """Sweep do cron / "Rodar agora": sessão própria, serializado por advisory
    lock numa sessão SÓ do lock (a `Rodada` commita ao sair, e o commit
    soltaria o lock). O modo do robô é olhado no tick do worker."""
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
            return await vigia_etiquetas_run(session)
