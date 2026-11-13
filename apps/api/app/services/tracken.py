"""TRACKen: o que a transportadora do Flex diz do pacote (coluna "Tracken" da
aba Flex — Vinicius, 13/11/2026).

A transportadora que entrega os Flex (TM Logística) usa o sistema TRACKen. Ele
tem um rastreio PÚBLICO, sem login (a página tracken.app.br/tracken/rastreio):

    POST https://tracken.app.br/tracken/api/getconsulta/rastreio
    {"notafiscal": <código da venda>, "pedidonf": <rastreio / id do envio ML>,
     "codpedido": "", "cpfcnpj": "", "cepdest": <CEP do cliente>}

O CEP é obrigatório e confere o pedido (CEP errado = "Nenhum registro
encontrado"). Conferido em 13/11/2026 com a Shopee 26111383SWQS3R (308933):
`notafiscal` = número do pedido na Shopee, `pedidonf` = rastreio
BR262058740263A — os dois acham; o CEP vale com ou sem traço. A resposta traz
status (`sts_nome`), o último evento, o histórico com data/hora, previsão,
entrega, recebedor e observação da entrega.

Quem chama: o cron do worker (`tracken_flex_tick`, de 30 em 30 min) e o botão
"Atualizar Tracken" do Raio-X. Só pedido Flex (`logistica.envio_flex`) da
Shopee/ML dos últimos `JANELA_DIAS`; depois de entregue, relê por mais um dia
(observação/recebedor chegam depois) e para. O CEP vem do espelho do Bling
(`bling_orders.cep_destino`).
"""

from __future__ import annotations

import asyncio
import html
import re
import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import BlingOrder, Logistica, LogisticaTracken
from app.services import logistica_rules

logger = structlog.get_logger()

URL = "https://tracken.app.br/tracken/api/getconsulta/rastreio"
_FUSO = ZoneInfo("America/Sao_Paulo")
# Pedido Flex dos últimos N dias (a entrega é no mesmo dia / no dia seguinte;
# folga para devolução e tentativa).
JANELA_DIAS = 20
# Depois de entregue, relê por mais este tempo e para.
RELER_ENTREGUE = timedelta(hours=24)
# Entre uma consulta e outra (site de terceiro: sem rajada).
_PAUSA_S = 0.5
_TIMEOUT_S = 20.0
# Evento que parece tentativa sem entrega (para o contador "Tentativas").
_TENTATIVA = (
    "ausente",
    "recus",
    "insucesso",
    "nao entreg",
    "endereco",
    "fechado",
    "nao localiz",
    "tentativa",
    "nao atend",
)
# Evento que pede atenção (vermelho na coluna).
_PROBLEMA = ("devol", "extravi", "avari", "roub", "sinistr", "retorn", *_TENTATIVA)


class TrackenError(Exception):
    """O Tracken respondeu algo que não é "achei" nem "não achei"."""


def _sem_acento(v: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", v) if not unicodedata.combining(c)
    ).lower()


def _texto(v: Any) -> str | None:
    """Texto limpo: sem HTML (`&nbsp;`), sem espaço sobrando; vazio = None."""
    if v is None:
        return None
    s = html.unescape(str(v)).replace("\xa0", " ")
    s = re.sub(r"<[^>]+>", " ", s)
    s = " ".join(s.split())
    return s or None


def _data(v: Any) -> date | None:
    s = _texto(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _quando(dia: Any, hora: Any) -> datetime | None:
    """Data + hora do Tracken (horário de Brasília) em UTC."""
    d = _data(dia)
    if d is None:
        return None
    h = _texto(hora) or "00:00:00"
    partes = (h.split(":") + ["0", "0"])[:3]
    try:
        hh, mm, ss = (int(p) for p in partes)
    except ValueError:
        hh = mm = ss = 0
    return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=_FUSO).astimezone(UTC)


def _cep(v: str | None) -> str | None:
    digitos = re.sub(r"\D", "", v or "")
    return f"{digitos[:5]}-{digitos[5:]}" if len(digitos) == 8 else None


def eh_tentativa(texto: str | None) -> bool:
    t = _sem_acento(texto or "")
    return any(p in t for p in _TENTATIVA)


def eh_problema(texto: str | None) -> bool:
    t = _sem_acento(texto or "")
    return any(p in t for p in _PROBLEMA)


def interpretar(item: Mapping[str, Any]) -> dict[str, Any]:
    """Os campos que a Logística guarda, de UM pedido da resposta."""
    eventos = []
    for ev in item.get("historico") or []:
        if not isinstance(ev, Mapping):
            continue
        em = _quando(ev.get("hsp_dtstatus"), ev.get("hsp_hrstatus"))
        descricao = _texto(ev.get("deschistorico")) or _texto(ev.get("sts_nome"))
        if not descricao and em is None:
            continue
        eventos.append(
            {
                "em": em.isoformat() if em else None,
                "descricao": descricao,
                "observacao": _texto(ev.get("hsp_observacao")),
            }
        )
    eventos.sort(key=lambda e: e["em"] or "")
    ultimo = eventos[-1] if eventos else None
    ultimo_em = datetime.fromisoformat(ultimo["em"]) if ultimo and ultimo["em"] else None
    return {
        "remessa": _texto(item.get("codpedido") or item.get("ped_codpedido")),
        "status": _texto(item.get("sts_nome")),
        "ultimo_evento": _texto(item.get("statushsp")) or (ultimo or {}).get("descricao"),
        "ultimo_em": ultimo_em,
        "previsao": _data(item.get("ped_dtpreventrega")),
        "entregue_em": _quando(item.get("hsp_dtentrega"), item.get("hsp_hrentrega")),
        "recebedor": _texto(item.get("hsp_recebedor")),
        "observacao": _texto(item.get("hsp_observacao")),
        "tentativas": sum(1 for e in eventos if eh_tentativa(e["descricao"])),
        "historico": eventos,
    }


def _mais_novo(lista: list[Any]) -> Mapping[str, Any] | None:
    """O pedido mais novo (maior remessa) quando a busca acha mais de um."""
    itens = [i for i in lista if isinstance(i, Mapping)]
    if not itens:
        return None

    def _num(i: Mapping[str, Any]) -> int:
        try:
            return int(i.get("ped_codpedido") or i.get("codpedido") or 0)
        except (TypeError, ValueError):
            return 0

    return max(itens, key=_num)


async def consultar(
    cliente: httpx.AsyncClient, *, cep: str, notafiscal: str = "", pedidonf: str = ""
) -> Mapping[str, Any] | None:
    """Uma consulta. None = o Tracken não achou; TrackenError = outra resposta."""
    corpo = {
        "cpfcnpj": "",
        "notafiscal": notafiscal,
        "pedidonf": pedidonf,
        "codpedido": "",
        "cepdest": cep,
    }
    r = await cliente.post(URL, json=corpo)
    r.raise_for_status()
    dados = r.json()
    if not isinstance(dados, Mapping):
        raise TrackenError("resposta inesperada do Tracken")
    if dados.get("success"):
        return _mais_novo(dados.get("list") or [])
    erro = _texto(dados.get("error")) or "sem mensagem"
    if "nenhum registro" in _sem_acento(erro):
        return None
    raise TrackenError(erro[:300])


async def buscar(
    cliente: httpx.AsyncClient, *, cep: str, pedido: str | None, rastreio: str | None
) -> Mapping[str, Any] | None:
    """Pelo número da venda; senão pelo rastreio (ou id do envio no ML)."""
    tentativas: list[tuple[str, str]] = []
    for campo, valor in (("notafiscal", pedido), ("pedidonf", rastreio), ("pedidonf", pedido)):
        v = (valor or "").strip()
        if v and (campo, v) not in tentativas:
            tentativas.append((campo, v))
    for i, (campo, valor) in enumerate(tentativas):
        if i:
            await asyncio.sleep(_PAUSA_S)
        achado = await consultar(cliente, cep=cep, **{campo: valor})
        if achado is not None:
            return achado
    return None


@dataclass(frozen=True)
class Alvo:
    logistica_id: UUID
    pedido: str | None  # número na plataforma
    rastreio: str | None
    cep: str | None


async def _ceps(session: AsyncSession, numeros: Collection[str]) -> dict[str, str]:
    """CEP do cliente por número do pedido no Bling (espelho; uma linha por
    item — vale o primeiro CEP preenchido)."""
    if not numeros:
        return {}
    rows = await session.execute(
        select(BlingOrder.numero, func.min(BlingOrder.cep_destino))
        .where(BlingOrder.numero.in_(list(numeros)), BlingOrder.cep_destino.is_not(None))
        .group_by(BlingOrder.numero)
    )
    return {str(n): str(c) for n, c in rows.all() if n and c}


async def alvos(
    session: AsyncSession, agora: datetime, somente: Collection[UUID] | None = None
) -> list[Alvo]:
    """Pedidos Flex (ML/Shopee) a consultar. `somente`: esses, sem as réguas
    de janela e de entregue (botão "Atualizar Tracken")."""
    plataformas = tuple(
        sorted(logistica_rules._ML_PLATAFORMAS | logistica_rules._SHOPEE_PLATAFORMAS)
    )
    stmt = (
        select(Logistica, LogisticaTracken)
        .outerjoin(LogisticaTracken, LogisticaTracken.logistica_id == Logistica.id)
        .where(
            Logistica.envio_flex.is_(True),
            func.lower(func.trim(Logistica.plataforma)).in_(plataformas),
        )
    )
    if somente is not None:
        stmt = stmt.where(Logistica.id.in_(list(somente)))
    else:
        corte = (agora - timedelta(days=JANELA_DIAS)).date()
        stmt = stmt.where(or_(Logistica.data.is_(None), Logistica.data >= corte))
    linhas = []
    for log, tk in (await session.execute(stmt)).all():
        if (
            somente is None
            and tk is not None
            and tk.entregue_em is not None
            and tk.entregue_em < agora - RELER_ENTREGUE
        ):
            continue
        linhas.append(log)
    ceps = await _ceps(session, {str(r.pedido_bling) for r in linhas if r.pedido_bling})
    return [
        Alvo(
            logistica_id=r.id,
            pedido=(r.pedido_marketplace or "").strip() or None,
            rastreio=(r.rastreio or "").strip() or None,
            cep=_cep(ceps.get(str(r.pedido_bling or ""))),
        )
        for r in linhas
    ]


async def _gravar(
    alvo: Alvo, agora: datetime, dados: dict[str, Any] | None, erro: str | None
) -> None:
    async with session_scope() as s:
        tk = await s.get(LogisticaTracken, alvo.logistica_id)
        if tk is None:
            tk = LogisticaTracken(logistica_id=alvo.logistica_id, achado=False, tentativas=0)
            tk.historico = []
            s.add(tk)
        tk.lido_em = agora
        tk.atualizado_em = agora
        tk.erro = erro
        if dados is not None:
            tk.achado = True
            for campo, valor in dados.items():
                setattr(tk, campo, valor)
        elif erro is None:
            # Consultou e não achou: o pedido ainda não chegou à transportadora
            # (ou o CEP do Bling não bate). O que já se sabia fica.
            tk.achado = bool(tk.remessa)
            if tk.remessa:
                tk.erro = "o Tracken não achou o pedido nesta leitura"


async def rodada(
    *, somente: Collection[UUID] | None = None, cliente: httpx.AsyncClient | None = None
) -> dict[str, int]:
    """Consulta os pedidos Flex no Tracken e grava. Erro num pedido não para
    os outros. `cliente`: os testes passam um falso."""
    agora = datetime.now(UTC)
    async with session_scope() as s:
        lista = await alvos(s, agora, somente)
    resumo = {"alvos": len(lista), "achados": 0, "nao_achados": 0, "sem_cep": 0, "erros": 0}
    if not lista:
        return resumo
    proprio = cliente is None
    cli = cliente or httpx.AsyncClient(timeout=_TIMEOUT_S, headers={"User-Agent": "DaVinci"})
    try:
        for i, alvo in enumerate(lista):
            if i:
                await asyncio.sleep(_PAUSA_S)
            dados: dict[str, Any] | None = None
            erro: str | None = None
            if not alvo.cep:
                resumo["sem_cep"] += 1
                erro = "sem o CEP do cliente no Bling"
            else:
                try:
                    item = await buscar(
                        cli, cep=alvo.cep, pedido=alvo.pedido, rastreio=alvo.rastreio
                    )
                except (httpx.HTTPError, TrackenError, ValueError) as e:
                    resumo["erros"] += 1
                    erro = f"Tracken: {str(e)[:250] or type(e).__name__}"
                    logger.warning(
                        "tracken_consulta_falhou", logistica_id=str(alvo.logistica_id), erro=erro
                    )
                else:
                    if item is None:
                        resumo["nao_achados"] += 1
                    else:
                        resumo["achados"] += 1
                        dados = interpretar(item)
            await _gravar(alvo, agora, dados, erro)
    finally:
        if proprio:
            await cli.aclose()
    logger.info("tracken_rodada", **resumo)
    return resumo
