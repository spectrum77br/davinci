"""Aprovar pelo celular — página pública do link que vai no aviso automático.

GET /api/aprovar/{token} mostra a confirmação (pedido, conta, situação) e um
botão; o POST aprova usando O MESMO fluxo da aba Margem
(routers/margens._apply_bling_decision_by_pedido: Bling Atendido→Aprovado +
bling_orders + snapshot). Sem login: o gate é o token assinado de
services/aprovar_link.py, que só circula no Threema pra quem estiver na
lista do aviso automático. A aprovação fica atribuída ao usuário-sistema
"Aprovação via Threema" (não dá pra saber quem tocou no link). Pedido já
aprovado só informa (idempotente); falha no Bling vira página de erro
amigável sem mudar nada.
"""

from __future__ import annotations

import html as html_mod
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User, UserRole, UserStatus
from app.services import aprovar_link

logger = structlog.get_logger()

router = APIRouter(prefix="/api/aprovar", tags=["aprovar-margem"])

# Identidade do usuário-sistema que assina as aprovações feitas pelo link.
_OPEN_ID_SISTEMA = "system:aprovacao-threema"
_EMAIL_SISTEMA = "aprovacao-threema@davinci.local"
_NOME_SISTEMA = "Aprovação via Threema"


def _pagina(titulo: str, corpo: str, *, status_code: int = 200) -> HTMLResponse:
    """Página mínima, mobile-first, sem depender do front (o link abre fora
    do app logado)."""
    doc = f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_mod.escape(titulo)} — DaVinci</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0;
         background: #f4f4f5; display: flex; min-height: 100vh;
         align-items: center; justify-content: center; }}
  .card {{ background: #fff; border-radius: 12px; padding: 28px 24px;
           margin: 16px; max-width: 400px; width: 100%;
           box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
  h1 {{ font-size: 1.1rem; margin: 0 0 12px; }}
  p {{ color: #3f3f46; line-height: 1.5; margin: 8px 0; }}
  .ok {{ color: #059669; }} .erro {{ color: #dc2626; }}
  button {{ background: #059669; color: #fff; border: 0; width: 100%;
            border-radius: 8px; padding: 14px; font-size: 1rem;
            font-weight: 600; margin-top: 16px; cursor: pointer; }}
</style></head>
<body><div class="card"><h1>DaVinci — Margem</h1>{corpo}</div></body></html>"""
    return HTMLResponse(doc, status_code=status_code)


def _e(v: object) -> str:
    return html_mod.escape(str(v))


async def _dados_pedido(session: AsyncSession, pedido: str) -> dict | None:
    """Status atual + conta do pedido (bling_orders + snapshot da margem)."""
    from app.routers.margens import _find_bling_order_by_pedido

    order = await _find_bling_order_by_pedido(session, pedido, None)
    if order is None:
        return None
    row = (
        (
            await session.execute(
                text(
                    "SELECT MAX(COALESCE(plataforma_bling, plataforma_financeiro))"
                    " AS plataforma, MAX(loja_nome) AS conta"
                    " FROM verificar_margem WHERE pedido_bling = :p"
                ),
                {"p": pedido},
            )
        )
        .mappings()
        .first()
    )
    plataforma = (row["plataforma"] if row else "") or ""
    conta = (row["conta"] if row else "") or ""
    loja = " ".join(x for x in (plataforma, conta) if x)
    return {"status": order.status, "loja": loja}


async def _usuario_sistema(session: AsyncSession) -> User:
    """Get-or-create do usuário-sistema (sem senha → ninguém loga com ele)."""
    u = (
        await session.execute(select(User).where(User.open_id == _OPEN_ID_SISTEMA))
    ).scalar_one_or_none()
    if u is None:
        u = User(
            open_id=_OPEN_ID_SISTEMA,
            email=_EMAIL_SISTEMA,
            name=_NOME_SISTEMA,
            role=UserRole.USER,
            status=UserStatus.ACTIVE,
        )
        session.add(u)
        await session.flush()
    return u


_PAGINA_LINK_INVALIDO = (
    "<p class='erro'>Link inválido ou vencido.</p>"
    "<p>Peça um aviso novo ou aprove pela aba Margem do DaVinci.</p>"
)


@router.get("/{token}")
async def confirmar_aprovacao(
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    pedido = aprovar_link.validar_token(token)
    if pedido is None:
        return _pagina("Link inválido", _PAGINA_LINK_INVALIDO, status_code=404)
    dados = await _dados_pedido(session, pedido)
    if dados is None:
        return _pagina(
            "Pedido não encontrado",
            f"<p class='erro'>Não achei o pedido {_e(pedido)}.</p>",
            status_code=404,
        )
    quem = f"Pedido {_e(pedido)}" + (f" — {_e(dados['loja'])}" if dados["loja"] else "")
    if dados["status"] == "Aprovado":
        return _pagina("Já aprovado", f"<p class='ok'>{quem} já está aprovado. Nada a fazer.</p>")
    corpo = (
        f"<p>{quem}</p>"
        "<p>Aprovar devolve o pedido ao fluxo normal no Bling"
        " (sai de Aguardando Cancelamento).</p>"
        f"<form method='post' action='/api/aprovar/{_e(token)}'>"
        "<button type='submit'>Aprovar pedido</button></form>"
    )
    return _pagina("Aprovar pedido", corpo)


@router.post("/{token}")
async def aprovar(
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    # Import tardio: evita ciclo e reusa a decisão canônica da aba Margem.
    from app.routers.margens import _apply_bling_decision_by_pedido

    pedido = aprovar_link.validar_token(token)
    if pedido is None:
        return _pagina("Link inválido", _PAGINA_LINK_INVALIDO, status_code=404)
    dados = await _dados_pedido(session, pedido)
    if dados is None:
        return _pagina(
            "Pedido não encontrado",
            f"<p class='erro'>Não achei o pedido {_e(pedido)}.</p>",
            status_code=404,
        )
    if dados["status"] == "Aprovado":
        return _pagina(
            "Já aprovado",
            f"<p class='ok'>Pedido {_e(pedido)} já está aprovado. Nada a fazer.</p>",
        )

    usuario = await _usuario_sistema(session)
    try:
        await _apply_bling_decision_by_pedido(
            session,
            usuario.id,
            pedido_bling=pedido,
            sku=None,
            new_status="Aprovado",
            update_bling=True,
        )
    except HTTPException as e:  # ex.: 502 bling_patch_failed
        logger.warning("aprovar_link_falhou", pedido_bling=pedido, detail=e.detail)
        return _pagina(
            "Falha ao aprovar",
            f"<p class='erro'>Não consegui aprovar o pedido {_e(pedido)} agora"
            " (falha ao atualizar o Bling).</p>"
            "<p>Tente de novo em instantes ou aprove pela aba Margem.</p>",
            status_code=502,
        )
    await session.commit()
    logger.info("aprovar_link_ok", pedido_bling=pedido)
    return _pagina(
        "Pedido aprovado",
        f"<p class='ok'>Pedido {_e(pedido)} aprovado ✓</p>"
        "<p>Ele volta ao fluxo normal no Bling. Pode fechar esta página.</p>",
    )
