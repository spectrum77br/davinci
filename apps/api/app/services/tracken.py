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

QUEM CONSULTA É A TELA, NÃO O SERVIDOR. O Tracken fica atrás do Cloudflare e
responde 403 ao nosso servidor (Hetzner, fora do Brasil) — conferido no ar em
13/11/2026 ~15h, depois de funcionar deste Mac. Do navegador de quem usa o
painel ele responde (CORS liberado para qualquer origem; com o corpo em
`text/plain` não há "preflight"). Então a aba Flex pede ao DaVinci a lista do
que consultar (`alvos`: pedido, rastreio e CEP), consulta o Tracken e devolve
a resposta crua, que o DaVinci interpreta aqui (`interpretar`) e grava
(`gravar`). A tela faz isso ao abrir a aba Flex, de 30 em 30 min enquanto ela
fica aberta e nos botões "Atualizar Tracken" — devagar, uma consulta por vez
com folga entre elas: o Tracken bloqueia quem consulta rápido demais (ver
`RELER_A_CADA`).

Só pedido Flex (`logistica.envio_flex`) da Shopee/ML dos últimos
`JANELA_DIAS`; depois de entregue, relê por mais um dia (observação/recebedor
chegam depois) e para. O CEP vem do espelho do Bling (`bling_orders.cep_destino`).
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import redis_client
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
# O Tracken (Cloudflare) BLOQUEIA o IP depois de poucas consultas seguidas
# (16/11/2026: da 6ª em diante, 429 "error 1015", ~2 min bloqueado; no navegador vira
# "Failed to fetch"). Por isso a lista vem com quem está há mais tempo sem
# leitura PRIMEIRO (uma leitura cortada recomeça por quem ficou de fora) e a
# leitura sozinha da tela pula o que foi lido há pouco (outra aba aberta, até
# em outra máquina, acabou de ler).
RELER_A_CADA = timedelta(minutes=20)
# Pedido que a transportadora não tem, já com estes dias de venda: relê só de
# tempos em tempos (cada um gasta até 3 consultas — pedido, rastreio, pedido
# como rastreio — e o 306194, Flex de outubro que nunca passou por ela, comia
# 3 das 5 consultas de cada leitura). 5 dias: venda de sexta despachada na
# segunda (ou depois de feriado) ainda é lida no ritmo normal.
NAO_ACHADO_DIAS = 5
RELER_NAO_ACHADO = timedelta(hours=6)
# UMA leitura por vez no sistema todo: duas abas abertas (na mesma internet)
# somam consultas e o Tracken bloqueia as duas. A tela manda um código da
# leitura; quem pega a vez lê, as outras não (veem o resultado no refresco da
# lista). A vez se renova a cada pedido gravado e cai sozinha se a aba fechar
# no meio (folga para a pausa do bloqueio e para o navegador, que atrasa os
# relógios de aba escondida).
_CHAVE_VEZ = "tracken:leitura"
VEZ_SEGUNDOS = 6 * 60


async def pegar_vez(leitura: str) -> bool:
    """A leitura `leitura` pode consultar? (Sem Redis: pode, como antes.)"""
    try:
        r = redis_client.redis
        if await r.set(_CHAVE_VEZ, leitura, nx=True, ex=VEZ_SEGUNDOS):
            return True
        return await r.get(_CHAVE_VEZ) == leitura
    except Exception as e:  # noqa: BLE001 — sem trava não é motivo para não ler
        logger.warning("tracken_vez_sem_redis", err=str(e)[:200])
        return True


async def renovar_vez(leitura: str) -> None:
    try:
        r = redis_client.redis
        if await r.get(_CHAVE_VEZ) == leitura:
            await r.expire(_CHAVE_VEZ, VEZ_SEGUNDOS)
    except Exception as e:  # noqa: BLE001
        logger.warning("tracken_vez_sem_redis", err=str(e)[:200])


async def soltar_vez(leitura: str) -> None:
    try:
        r = redis_client.redis
        if await r.get(_CHAVE_VEZ) == leitura:
            await r.delete(_CHAVE_VEZ)
    except Exception as e:  # noqa: BLE001
        logger.warning("tracken_vez_sem_redis", err=str(e)[:200])


# Defesa: a resposta vem do navegador — nada de texto ou lista sem fim.
_MAX_TEXTO = 500
_MAX_EVENTOS = 200
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


def _sem_acento(v: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", v) if not unicodedata.combining(c)
    ).lower()


def _texto(v: Any) -> str | None:
    """Texto limpo: sem HTML (`&nbsp;`), sem espaço sobrando; vazio = None."""
    if v is None or isinstance(v, (dict, list)):
        return None
    s = html.unescape(str(v)).replace("\xa0", " ")
    s = re.sub(r"<[^>]+>", " ", s)
    s = " ".join(s.split())
    return s[:_MAX_TEXTO] or None


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
        return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=_FUSO).astimezone(UTC)
    except ValueError:
        return datetime(d.year, d.month, d.day, tzinfo=_FUSO).astimezone(UTC)


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
    for ev in (item.get("historico") or [])[:_MAX_EVENTOS]:
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


def mais_novo(lista: Sequence[Any]) -> Mapping[str, Any] | None:
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


def _esperar(
    log: Logistica, tk: LogisticaTracken | None, hoje: date, tudo: bool
) -> timedelta | None:
    """Quanto tempo depois da última leitura o pedido volta à lista (None = já)."""
    nao_tem = tk is not None and tk.lido_em is not None and not tk.achado and tk.erro is None
    if nao_tem and log.data is not None and (hoje - log.data).days >= NAO_ACHADO_DIAS:
        return RELER_NAO_ACHADO
    return None if tudo else RELER_A_CADA


async def alvos(
    session: AsyncSession,
    agora: datetime,
    somente: Collection[UUID] | None = None,
    *,
    tudo: bool = False,
) -> list[Alvo]:
    """Pedidos Flex (ML/Shopee) a consultar, quem está há mais tempo sem
    leitura primeiro. `somente`: esses, sem réguas (botão "Atualizar" do
    Raio-X). `tudo` (botão "Atualizar Tracken"): também o que foi lido há
    pouco — o pedido que a transportadora não tem continua de 6 em 6 h."""
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
        .order_by(
            LogisticaTracken.lido_em.asc().nulls_first(),
            Logistica.data.desc().nulls_last(),
            Logistica.id,
        )
    )
    if somente is not None:
        stmt = stmt.where(Logistica.id.in_(list(somente)))
    else:
        corte = (agora - timedelta(days=JANELA_DIAS)).date()
        stmt = stmt.where(or_(Logistica.data.is_(None), Logistica.data >= corte))
    hoje = agora.astimezone(_FUSO).date()
    linhas = []
    for log, tk in (await session.execute(stmt)).all():
        if somente is None and tk is not None:
            if tk.entregue_em is not None and tk.entregue_em < agora - RELER_ENTREGUE:
                continue
            espera = _esperar(log, tk, hoje, tudo)
            if espera is not None and tk.lido_em is not None and tk.lido_em > agora - espera:
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


async def gravar(
    session: AsyncSession,
    logistica_id: UUID,
    agora: datetime,
    dados: dict[str, Any] | None,
    erro: str | None,
) -> None:
    """Grava uma leitura. `dados` = achado; `dados` None e `erro` None =
    consultou e não achou (o pedido ainda não chegou à transportadora); `erro`
    = não deu para consultar. Não faz commit."""
    tk = await session.get(LogisticaTracken, logistica_id)
    if tk is None:
        # Duas gravações do mesmo pedido ao mesmo tempo (leitura de todos + o
        # "Atualizar" do Raio-X): a segunda espera a primeira em vez de dar erro.
        await session.execute(
            pg_insert(LogisticaTracken)
            .values(logistica_id=logistica_id, achado=False, tentativas=0, historico=[])
            .on_conflict_do_nothing(index_elements=[LogisticaTracken.logistica_id])
        )
        tk = await session.get(LogisticaTracken, logistica_id, populate_existing=True)
        assert tk is not None
    tk.lido_em = agora
    tk.atualizado_em = agora
    tk.erro = erro
    if dados is not None:
        tk.achado = True
        for campo, valor in dados.items():
            setattr(tk, campo, valor)
    elif erro is None:
        # O que já se sabia fica.
        tk.achado = bool(tk.remessa)
        if tk.remessa:
            tk.erro = "o Tracken não achou o pedido nesta leitura"
    # A Localização que as regras do Flex olham (`Logistica.localizacao_tracken`).
    log = await session.get(Logistica, logistica_id)
    if log is not None:
        log.localizacao_tracken = localizacao_para_regra(tk)


def localizacao_para_regra(tk: LogisticaTracken | None) -> str | None:
    """O texto da coluna Tracken que vale como Localização nas regras do Flex
    (aba Status, "Localização contém"): status + último evento da
    transportadora — "MANUSEIO · COLETADO". Só o que ela diz AGORA: o histórico
    fica de fora (uma palavra antiga não pode continuar casando depois que o
    pacote andou). Pedido que a transportadora ainda não tem = None.

    Vinicius, 17/11/2026 (309413): regra Shopee Flex · Processado · Em
    digitação com "coletado" não casava — olhava a Localização da Shopee, que a
    aba Flex nem mostra; o "COLETADO" estava no Tracken. A migration 0496
    preenche os já lidos com a mesma conta."""
    if tk is None or not tk.achado:
        return None
    partes = [p.strip() for p in (tk.status, tk.ultimo_evento) if p and p.strip()]
    return " · ".join(partes) or None
