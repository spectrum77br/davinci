"""Vigia de Conciliação (robô da Ouvidoria, 15/10/2026).

Uma vez por dia, de manhã (07:00 BRT, depois da rodada de madrugada), pega a lista
"Onde agir hoje" do Resumo da Conciliação — a mesma que a tela mostra — e registra
cada item como ocorrência na Ouvidoria; a Ouvidoria manda UMA mensagem no Threema
com o que está pendente (destinatário definido no painel: só o Cairo, por ora) e
fecha sozinha o que sumiu. Item "ok" (dia pronto pra fechar) não vira ocorrência:
o robô da rodada fecha o dia sozinho.
"""

from __future__ import annotations

from datetime import UTC, datetime
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


async def vigia_conciliacao_run(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    from app.routers.conciliacao import montar_resumo

    agora = agora or datetime.now(UTC)
    async with ouvidoria.Rodada(session, ROBO) as r:
        for k in ("lojas", "itens", "novas", "persistem", "sumiram"):
            r.contadores[k] = 0
        resumo = await montar_resumo(session)
        plataforma_da_loja = {x["conta"]: x["platform"] for x in resumo["lojas"]}
        r.contadores["lojas"] = len(resumo["lojas"])
        for item in resumo["agir"]:
            if item.get("gravidade") == "ok":
                continue
            filtro = item.get("filtro") or {}
            chave = f"{item['loja']}:{item['acao']}:" + ",".join(
                f"{k}={v}" for k, v in sorted(filtro.items())
            )
            plat = plataforma_da_loja.get(item["loja"], "")
            row = await r.registrar(
                chave=chave,
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
        r.contadores["sumiram"] = await r.fechar_nao_vistas()
        n = r.contadores["itens"]
        r.resumo = (
            f"{r.contadores['lojas']} loja(s) · {n} pendência(s) pra agir"
            if n
            else f"{r.contadores['lojas']} loja(s) · nada pra agir"
        )
    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
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
