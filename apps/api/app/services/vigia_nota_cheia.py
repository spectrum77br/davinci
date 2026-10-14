"""Vigia de Nota Cheia — o robô da Ouvidoria que confere se TODO pedido enviado
teve a nota cheia emitida e no painel Notas fiscais.

Vinicius, 14/10/2026: "o que importa pra mim é saber se tudo foi emitido nota
cheia — 100% dos pedidos". A nota cheia é a coluna "NF produto" do painel
(Pós Vendas): nas contas de faturador 1%/2% é a segunda nota, do lote
"bling 100%" da noite; nas de faturador 100% (ML marquezini/kfa/forpaper,
Amazon) é a única nota; na poofy (70%) é a de 70% — a mesma régua do painel
(`pos_vendas._eh_embalagem`). A nota baixa (embalagem) não é cobrada aqui.

## A regra

- Universo: pedidos enviados (com etiqueta) de loja com faturador na tela
  Lojas — Temu, Shein e Amazon inclusive. Cancelado / Aguardando
  Cancelamento fica de fora.
- Dia do envio = dia da etiqueta; etiqueta depois das 22:00 (`corte_hora`)
  é do dia seguinte (o lote da noite começa às 22h). Pedido sem etiqueta no
  DaVinci usa a data de envio do Bling.
- Prazo: o dia do envio é conferido às 07:00 do dia seguinte
  (`hora_conferencia`) — depois do lote da noite e da repescagem das 05h.
- **Uma ocorrência por DIA** (`dia:<aaaa-mm-dd>`), não por pedido: "Envios de
  10/10 (sáb) — 59 de 316 pedidos sem nota cheia", com a lista por conta no
  detalhe. Ele roda de hora em hora: o número vai baixando e a ocorrência
  fecha sozinha quando o último pedido ganha a nota. "Tratado" = "cobrei o
  pessoal": some por 24 h e, se ainda faltar nota, volta no dia seguinte só
  com o que falta.

Só lê banco (o mesmo cálculo da tela, `routers.notas_fiscais.montar_pos_vendas`)
e só AVISA.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import BlingOrder, OuvidoriaRobo, StoreInfo
from app.services import ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.bling_situacoes import (
    SITUACAO_AGUARDANDO_CANCELAMENTO_STR,
    SITUACAO_CANCELADO,
)

logger = structlog.get_logger()

ROBO = "vigia_nota_cheia"

# Advisory lock do sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x766E6368  # ascii "vnch"

# Padrões quando a config do robô não tem a chave (a linha de ouvidoria_robos
# nasce com estes mesmos valores — services/ouvidoria.ROBOS).
_HORA_CONFERENCIA = 7
_CORTE_HORA = 22
_JANELA_DIAS = 7

_TZ_BR = ZoneInfo("America/Sao_Paulo")
_DIA_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")

_FORA = {str(SITUACAO_CANCELADO), SITUACAO_AGUARDANDO_CANCELAMENTO_STR, "excluido"}

# Teto de pedidos escritos no detalhe/dados (o resto vira "… e mais N").
_MAX_PEDIDOS_LISTADOS = 400

ACAO = (
    "Cobrar quem roda o lote da nota cheia (\"bling 100%\", à noite) e conferir "
    "no painel Notas fiscais › filtro Sem NF produto"
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

_CONTADORES = ("dias_conferidos", "envios", "sem_nota", "novas", "persistem", "sumiram")


def _dd_mm(d: date) -> str:
    return d.strftime("%d/%m")


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def dia_do_envio(
    etiqueta_em: datetime | None, envio: date | None, *, corte_hora: int
) -> date | None:
    """De que dia é o envio: o dia da etiqueta em Brasília, e etiqueta a partir
    de `corte_hora` vai pro dia seguinte (o lote da noite já começou). Sem
    etiqueta no DaVinci, a data de envio do Bling."""
    if etiqueta_em is not None:
        if etiqueta_em.tzinfo is None:
            etiqueta_em = etiqueta_em.replace(tzinfo=UTC)
        br = etiqueta_em.astimezone(_TZ_BR)
        return br.date() + timedelta(days=1) if br.hour >= corte_hora else br.date()
    return envio


def conferencia_do_dia(dia: date, *, hora: int) -> datetime:
    """Quando os envios de `dia` passam a ser cobrados: `hora` do dia seguinte."""
    return datetime.combine(dia + timedelta(days=1), time(hora), tzinfo=_TZ_BR)


def _iso(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _detalhe(por_conta: dict[str, list[str]]) -> str:
    """Uma linha por conta (a que mais falta primeiro) com os pedidos."""
    linhas: list[str] = []
    restam = _MAX_PEDIDOS_LISTADOS
    for conta, pedidos in sorted(por_conta.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        mostrar = pedidos[: max(restam, 0)]
        restam -= len(mostrar)
        texto = ", ".join(mostrar) if mostrar else ""
        if len(mostrar) < len(pedidos):
            texto += f"{', ' if mostrar else ''}… e mais {len(pedidos) - len(mostrar)}"
        linhas.append(f"{conta} ({len(pedidos)}): {texto}")
    return "\n".join(linhas)


async def _lojas_e_situacoes(
    session: AsyncSession, numeros: list[str]
) -> tuple[dict[str, StoreInfo], dict[str, str | None]]:
    """pedido → loja (só as que têm faturador, ativas) e pedido → situação."""
    if not numeros:
        return {}, {}
    pedidos = (
        await session.execute(
            select(BlingOrder.numero, BlingOrder.loja, BlingOrder.situacao)
            .where(BlingOrder.numero.in_(numeros))
            .distinct()
        )
    ).all()
    lojas_ids = {loja for _, loja, _ in pedidos if loja}
    lojas: dict[str, StoreInfo] = {}
    if lojas_ids:
        for si in (
            await session.execute(
                select(StoreInfo).where(
                    StoreInfo.bling_store_id.in_(lojas_ids),
                    StoreInfo.archived_at.is_(None),
                    StoreInfo.nf_faturador_id.is_not(None),
                )
            )
        ).scalars():
            lojas[si.bling_store_id] = si
    por_pedido: dict[str, StoreInfo] = {}
    situacao: dict[str, str | None] = {}
    for numero, loja, sit in pedidos:
        if loja in lojas:
            por_pedido[numero] = lojas[loja]
        situacao[numero] = sit
    return por_pedido, situacao


async def vigia_nota_cheia_run(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    """Uma varredura dentro de uma `Rodada`. Erro derruba a rodada inteira
    (ok=False) sem fechar nada: "não consegui olhar" nunca vira "sumiu"."""
    from app.routers.notas_fiscais import montar_pos_vendas

    async with ouvidoria.Rodada(session, ROBO) as r:
        for k in _CONTADORES:
            r.contadores[k] = 0
        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)
        hora = int(cfg.get("hora_conferencia") if cfg.get("hora_conferencia") is not None
                   else _HORA_CONFERENCIA)
        corte = int(cfg.get("corte_hora") or _CORTE_HORA)
        janela = int(cfg.get("janela_dias") or _JANELA_DIAS)
        agora = agora or datetime.now(UTC)

        # Dias já vencidos (a conferência deles já passou), os `janela` mais
        # recentes.
        ultimo = agora.astimezone(_TZ_BR).date()
        while conferencia_do_dia(ultimo, hora=hora) > agora:
            ultimo -= timedelta(days=1)
        primeiro = ultimo - timedelta(days=janela - 1)
        dias = {primeiro + timedelta(days=i) for i in range(janela)}

        # Data de envio do Bling pode ser o dia da etiqueta ou o seguinte (a
        # agência confirma depois), e a etiqueta depois das 22h é do dia
        # seguinte: um dia de folga de cada lado.
        linhas = await montar_pos_vendas(
            session, primeiro - timedelta(days=1), ultimo + timedelta(days=1)
        )
        lojas, situacoes = await _lojas_e_situacoes(session, [x.pedido_bling for x in linhas])

        envios: Counter[date] = Counter()
        faltando: dict[date, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for x in linhas:
            si = lojas.get(x.pedido_bling)
            if si is None or situacoes.get(x.pedido_bling) in _FORA:
                continue
            envio = date.fromisoformat(x.data_envio[:10]) if x.data_envio else None
            dia = dia_do_envio(
                _iso(x.etiqueta_emissao),
                None if x.envio_com_hora else envio,
                corte_hora=corte,
            )
            if dia not in dias:
                continue
            envios[dia] += 1
            if x.nf_produto is None:
                rotulo = _ROTULO_PLATAFORMA.get(
                    (si.platform or "").lower(), (si.platform or "").title()
                )
                conta = f"{rotulo} {(si.account_name or '').strip()}".strip()
                faltando[dia][conta].append(x.pedido_bling)

        r.contadores["dias_conferidos"] = len(dias)
        r.contadores["envios"] = sum(envios.values())
        for dia in sorted(faltando, reverse=True):
            por_conta = {c: sorted(p) for c, p in faltando[dia].items()}
            n = sum(len(p) for p in por_conta.values())
            r.contadores["sem_nota"] += n
            row = await r.registrar(
                chave=f"dia:{dia.isoformat()}",
                titulo=(
                    f"Envios de {_dd_mm(dia)} ({_DIA_SEMANA[dia.weekday()]}) — "
                    f"{n} de {envios[dia]} pedidos sem nota cheia"
                ),
                detalhe=_detalhe(por_conta),
                acao=ACAO,
                link=(
                    f"/notas-fiscais?de={dia.isoformat()}"
                    f"&ate={(dia + timedelta(days=1)).isoformat()}&nf=sem_produto"
                ),
                severidade="pessoa",
                precisa_pessoa=True,
                dados={
                    "dia": dia.isoformat(),
                    "envios": envios[dia],
                    "sem_nota": n,
                    "por_conta": {c: len(p) for c, p in por_conta.items()},
                    "pedidos": [p for ps in por_conta.values() for p in ps][
                        :_MAX_PEDIDOS_LISTADOS
                    ],
                },
                agora=agora,
            )
            if row.fechada_em is None:
                r.contadores["novas" if row.aberta_em == agora else "persistem"] += 1

        # Dia que não faltou mais nada (ou saiu da janela): fechou.
        r.contadores["sumiram"] = await r.fechar_nao_vistas()

        partes = [
            _plural(len(dias), "dia conferido", "dias conferidos"),
            _plural(r.contadores["envios"], "envio", "envios"),
        ]
        if faltando:
            partes.append(
                _plural(len(faltando), "dia", "dias")
                + f" com pedido sem nota cheia ({r.contadores['sem_nota']})"
            )
        else:
            partes.append("todos com nota cheia")
        r.resumo = " · ".join(partes)

    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def vigia_nota_cheia_sweep() -> dict:
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
            return await vigia_nota_cheia_run(session)
