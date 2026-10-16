"""Vigia de Conciliação (robô da Ouvidoria, 15/10/2026).

Uma vez por dia, de manhã (07:00 BRT, depois da rodada de madrugada), pega a lista
"Onde agir hoje" do Resumo da Conciliação — a mesma que a tela mostra — e registra
cada item como ocorrência na Ouvidoria; a Ouvidoria manda UMA mensagem no Threema
com o que está pendente (destinatário definido no painel: só o Cairo, por ora) e
fecha sozinha o que sumiu. Item "ok" (dia pronto pra fechar) não vira ocorrência:
o robô da rodada fecha o dia sozinho.

16/10, revisão: além de "ontem" (a lista "Onde agir"), o vigia olha os dias mais
antigos ainda abertos do bloco "Dias" do Resumo (linha sem dono, saldo que não bate):
a ocorrência do dia só fecha como "sumiu" quando o dia deixa de aparecer pendente. A
chave da ocorrência de extrato leva o dia, e o carimbo do aviso usa a hora do começo da
rodada (o re-aviso de 24 h não depende de quanto o Resumo demorou).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.services import ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

ROBO = "vigia_conciliacao"
_SWEEP_LOCK_KEY = 0x76636F6E  # ascii "vcon"
_ACAO = {
    "extrato": "abrir o Extrato do dia e dar destino às linhas sem dono",
    "sincronizar": "sincronizar a loja e, se continuar, ver o erro da API",
    "pedidos": "abrir a aba Pedidos filtrada e decidir: finalizar com motivo ou abrir chamado",
}
_PLATAFORMA = {"ml": "Mercado Livre", "shopee": "Shopee", "amazon": "Amazon", "tiktok": "TikTok"}
# motivos do bloco "Dias" que são trabalho de pessoa no Extrato daquele dia (os outros —
# extrato/fatura não lida, repasse da TikTok, pronto pra fechar — não são do dia)
_MOTIVOS_DIA_ABERTO = ("sem dono", "saldo não bate")


def _link(item: dict[str, Any]) -> str:
    partes = [
        f"loja={quote(str(item['loja']))}",
        f"visao={'extrato' if item['acao'] == 'extrato' else 'pedidos'}",
    ]
    if item.get("dia"):
        partes.append(f"dia={item['dia']}")
    for k, v in sorted((item.get("filtro") or {}).items()):
        partes.append(f"{k}={quote(str(v))}")
    return "/conciliacao?" + "&".join(partes)


def _chave(item: dict[str, Any]) -> str:
    """loja:acao:filtro — o extrato leva o dia (16/10: sem ele, todo dia aberto caía na
    mesma ocorrência e o dia antigo "sumia" quando ontem ficava limpo)."""
    partes = dict(item.get("filtro") or {})
    if item["acao"] == "extrato" and item.get("dia"):
        partes["dia"] = item["dia"]
    return f"{item['loja']}:{item['acao']}:" + ",".join(
        f"{k}={v}" for k, v in sorted(partes.items())
    )


def _dias_antigos_abertos(resumo: dict[str, Any]) -> list[dict[str, Any]]:
    """Itens no formato do "Onde agir" pros dias ANTES de ontem que o bloco "Dias" ainda
    mostra pendentes por trabalho de pessoa no Extrato (16/10, revisão: o cashback de
    14/10 do mini sumia da Ouvidoria no dia seguinte sem ninguém ter resolvido)."""
    itens: list[dict[str, Any]] = []
    for bloco in resumo.get("dias") or []:
        dia = str(bloco.get("dia") or "")
        if not dia or dia >= str(resumo.get("dia") or ""):
            continue  # ontem já veio pelo "Onde agir"
        for pend in bloco.get("pendentes") or []:
            motivo = str(pend.get("motivo") or "")
            if not any(x in motivo for x in _MOTIVOS_DIA_ABERTO):
                continue
            d = date.fromisoformat(dia)
            itens.append(
                {
                    "gravidade": "bad",
                    "loja": pend["conta"],
                    "titulo": f"{pend['conta']} · dia {d:%d/%m} ainda aberto · {motivo}",
                    "detalhe": f"extrato de {d:%d/%m} não fechou: {motivo}",
                    "acao": "extrato",
                    "dia": dia,
                }
            )
    return itens


async def vigia_conciliacao_run(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    from app.routers.conciliacao import montar_resumo

    # a hora da rodada é marcada ANTES de montar o Resumo (que demora): é ela que
    # carimba a abertura e o aviso — o re-aviso de 24 h compara carimbo com carimbo
    agora = agora or datetime.now(UTC)
    async with ouvidoria.Rodada(session, ROBO) as r:
        for k in ("lojas", "itens", "novas", "persistem", "sumiram"):
            r.contadores[k] = 0
        resumo = await montar_resumo(session)
        plataforma_da_loja = {x["conta"]: x["platform"] for x in resumo["lojas"]}
        r.contadores["lojas"] = len(resumo["lojas"])
        itens = [x for x in resumo["agir"] if x.get("gravidade") != "ok"]
        itens += _dias_antigos_abertos(resumo)
        for item in itens:
            filtro = item.get("filtro") or {}
            plat = plataforma_da_loja.get(item["loja"], "")
            row = await r.registrar(
                chave=_chave(item),
                titulo=item["titulo"][:200],
                detalhe=item.get("detalhe"),
                plataforma=_PLATAFORMA.get(plat, plat) or None,
                conta=item["loja"],
                acao=_ACAO.get(item["acao"]),
                link=_link(item),
                severidade="pessoa",
                precisa_pessoa=True,
                dados={"gravidade": item.get("gravidade"), "dia": item.get("dia"), **filtro},
                agora=agora,
            )
            r.contadores["itens"] += 1
            if row.fechada_em is None:
                r.contadores["novas" if row.aberta_em == agora else "persistem"] += 1
        # dia antigo ainda aberto continua "visto": não fecha como "sumiu"
        r.contadores["sumiram"] = await r.fechar_nao_vistas()
        n = r.contadores["itens"]
        r.resumo = (
            f"{r.contadores['lojas']} loja(s) · {n} pendência(s) pra agir"
            if n
            else f"{r.contadores['lojas']} loja(s) · nada pra agir"
        )
    aviso = await ouvidoria.avisar_pendentes(session, ROBO, agora=agora)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def vigia_conciliacao_sweep() -> dict:
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
            return await vigia_conciliacao_run(session)
