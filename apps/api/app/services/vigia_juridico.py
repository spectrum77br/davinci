"""Vigia do Jurídico — as publicações dos processos, lidas no Diário oficial (11/11/2026).

Vinicius: "no jurídico vamos começar a vincular esse Jusbrasil … deixar ele atualizando os
últimos passos sozinho". O Jusbrasil só libera isso por contrato (API paga) e proíbe robô no
site; a fonte oficial e grátis é o **Diário de Justiça Eletrônico Nacional (DJEN)** do CNJ, onde
todos os tribunais publicam intimações, decisões, citações e distribuições — sem login, por nº.
Teste de 11/11 com o processo do Caso 006 (4206943-64.2026.8.26.0100): 3 publicações, com o
texto inteiro e o link do documento no eproc. (O DataJud, a outra API do CNJ, ainda não tinha o
processo: o TJSP manda os dados pra lá uma vez por mês.)

Uma vez por dia (07:30) — e no botão "buscar no Diário" da janela do processo — para cada
processo do painel Jurídico com nº de 20 dígitos: lê as comunicações no DJEN e lança no
histórico as que ainda não estão lá (`externo_id` = id da comunicação). A Vara vazia é
preenchida com o órgão da publicação mais nova. Não abre ocorrência nem manda Threema: o
painel Robôs só mostra a rodada.

Limite do DJEN: 20 consultas por minuto (cabeçalho x-ratelimit-limit) — 3,2 s entre processos.
O DJEN só tem o que é PUBLICADO; passo interno do cartório ("conclusos") não aparece.
"""

from __future__ import annotations

import asyncio
import html
import re
from datetime import date, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models.juridico import JuridicoMovimentacao, JuridicoProcesso
from app.services import ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

ROBO = "vigia_juridico"
_SWEEP_LOCK_KEY = 0x766A7572  # ascii "vjur"
URL_DJEN = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
POR_PAGINA = 100
PAUSA_S = 3.2  # 20 consultas/min no DJEN


def numero_20(numero: str | None) -> str | None:
    """O nº CNJ só com os 20 dígitos (4206943-64.2026.8.26.0100 → 42069436420268260100)."""
    d = re.sub(r"\D", "", numero or "")
    return d if len(d) == 20 else None


def texto_limpo(bruto: str | None) -> str:
    """O texto da publicação vem em HTML: vira texto com quebras de linha."""
    t = bruto or ""
    t = re.sub(r"<(style|script)\b.*?</\1>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<\s*(br|/p|/tr|/section|/div|/h\d)\b[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<\s*/?\s*td\b[^>]*>", " ", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t ]+", " ", t)
    linhas = [ln.strip() for ln in t.splitlines()]
    return "\n".join(ln for ln in linhas if ln).strip()


def resumo(item: dict) -> str:
    """O que a tabela e a linha do tempo mostram: o tipo da publicação e do documento."""
    tipo = (item.get("tipoComunicacao") or "Publicação").strip()
    doc = (item.get("tipoDocumento") or "").strip()
    if tipo.lower().startswith("lista de distribui"):
        orgao = (item.get("nomeOrgao") or "").strip()
        return f"Distribuído para {orgao}" if orgao else "Distribuído"
    if doc and doc.lower() not in ("outros", tipo.lower()):
        return f"{tipo} · {doc[:1].upper()}{doc[1:].lower()}"
    return tipo


def _data(item: dict) -> date | None:
    v = str(item.get("data_disponibilizacao") or item.get("datadisponibilizacao") or "")[:10]
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None


async def buscar_djen(client: httpx.AsyncClient, numero: str) -> list[dict]:
    """Todas as comunicações do processo no DJEN (paginado; espera e tenta de novo no 429)."""
    itens: list[dict] = []
    pagina = 1
    while True:
        params = {"numeroProcesso": numero, "pagina": pagina, "itensPorPagina": POR_PAGINA}
        r = await client.get(URL_DJEN, params=params)
        if r.status_code == 429:
            await asyncio.sleep(61)
            r = await client.get(URL_DJEN, params=params)
        r.raise_for_status()
        d = r.json()
        lote = d.get("items") or []
        itens.extend(lote)
        if not lote or len(itens) >= int(d.get("count") or 0) or len(lote) < POR_PAGINA:
            return itens
        pagina += 1
        await asyncio.sleep(PAUSA_S)


async def lancar(session: AsyncSession, p: JuridicoProcesso, itens: list[dict]) -> int:
    """Lança as comunicações que ainda não estão no histórico; devolve quantas entraram."""
    novas = 0
    mais_nova: tuple[date, str] | None = None
    for it in itens:
        if it.get("ativo") is False or it.get("data_cancelamento"):
            continue  # publicação cancelada no Diário
        quando = _data(it)
        externo = str(it.get("id") or it.get("hash") or "").strip()
        if quando is None or not externo:
            continue
        orgao = (it.get("nomeOrgao") or "").strip() or None
        if orgao and (mais_nova is None or quando >= mais_nova[0]):
            mais_nova = (quando, orgao)
        ins = (
            pg_insert(JuridicoMovimentacao)
            .values(
                processo_id=p.id,
                data=quando,
                texto=resumo(it)[:500],
                fonte="djen",
                criado_por="Diário oficial (DJEN)",
                externo_id=externo,
                orgao=orgao,
                tipo=(it.get("tipoComunicacao") or None),
                texto_completo=texto_limpo(it.get("texto"))[:60000] or None,
                link=(it.get("link") or None),
            )
            .on_conflict_do_nothing(
                index_elements=["processo_id", "externo_id"],
                index_where=text("externo_id IS NOT NULL"),
            )
            .returning(JuridicoMovimentacao.id)
        )
        if (await session.execute(ins)).scalar_one_or_none() is not None:
            novas += 1
    if mais_nova and not (p.vara or "").strip():
        p.vara = mais_nova[1]
    return novas


async def atualizar_processo(
    session: AsyncSession, p: JuridicoProcesso, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """Um processo (botão "buscar no Diário"): lê no DJEN e lança o que é novo."""
    numero = numero_20(p.numero)
    if numero is None:
        return {"ok": False, "motivo": "sem_numero", "novas": 0, "publicacoes": 0}
    if client is None:
        async with httpx.AsyncClient(timeout=40.0) as c:
            itens = await buscar_djen(c, numero)
    else:
        itens = await buscar_djen(client, numero)
    novas = await lancar(session, p, itens)
    return {"ok": True, "novas": novas, "publicacoes": len(itens)}


async def vigia_juridico_run(session: AsyncSession) -> dict[str, Any]:
    procs = (
        await session.execute(
            select(JuridicoProcesso)
            .where(JuridicoProcesso.numero.is_not(None), JuridicoProcesso.situacao != "encerrado")
            .order_by(JuridicoProcesso.id)
        )
    ).scalars().all()
    com_numero = [p for p in procs if numero_20(p.numero)]
    async with ouvidoria.Rodada(session, ROBO) as r:
        falhas: list[str] = []
        async with httpx.AsyncClient(timeout=40.0) as client:
            for i, p in enumerate(com_numero):
                if i:
                    await asyncio.sleep(PAUSA_S)
                try:
                    res = await atualizar_processo(session, p, client)
                    r.contadores["processos"] += 1
                    r.contadores["publicacoes"] += res["publicacoes"]
                    r.contadores["novas"] += res["novas"]
                    await session.flush()
                except httpx.HTTPError as e:
                    falhas.append(p.numero or str(p.id))
                    r.contadores["falhas"] += 1
                    logger.warning("vigia_juridico_djen_falhou", processo=p.id, err=str(e)[:200])
        partes = [
            f"{r.contadores['processos']} processo(s) lido(s) no Diário",
            f"{r.contadores['novas']} publicação(ões) nova(s)",
        ]
        if falhas:
            partes.append(f"{len(falhas)} sem resposta do DJEN")
        r.resumo = " · ".join(partes)
        return dict(r.contadores)


async def vigia_juridico_sweep() -> dict:
    """Sweep do cron / "Rodar agora": sessão própria, serializado por advisory lock."""
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
            return await vigia_juridico_run(session)
