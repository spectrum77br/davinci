"""Aprovar pelo celular — página pública do link dos avisos da Margem.

GET /api/aprovar/{token} mostra a confirmação (pedido, conta, situação) e um
botão; o POST aprova usando O MESMO fluxo da aba Margem
(routers/margens._apply_bling_decision_by_pedido: Bling Atendido→Aprovado +
bling_orders + snapshot), INCLUSIVE o fallback da aba: se o Bling recusar a
transição de situação (ex.: pedido "Em andamento", que não aceita ir pra
Atendido), aprova só no DaVinci — espelho do `isBlingPatchError` +
`call(true)` do margem.vue, "sem pedir confirmação ao usuário". Sem login:
o gate é o token assinado de services/aprovar_link.py, que só circula no
Threema pra quem estiver nas listas do Informar. A aprovação fica atribuída
ao usuário-sistema "Aprovação via Threema" (não dá pra saber quem tocou no
link). Pedido já aprovado só informa (idempotente); outras falhas viram
página de erro amigável sem mudar nada.
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

# Situação "Aguardando Cancelamento" (= routers/nf._SITUACAO_AGUARDANDO_
# CANCELAMENTO): só nela a aprovação de fato move o pedido no Bling.
_SITUACAO_AGUARDANDO = "83955"

# Mesmos códigos do isBlingPatchError do margem.vue: nesses casos a aba
# refaz a decisão com local_only=true — o link faz igual.
_CODES_FALLBACK_LOCAL = {"bling_patch_failed", "bling_integration_missing"}

# Identidade do usuário-sistema que assina as aprovações feitas pelo link.
# ATENÇÃO: o domínio precisa ser um e-mail VÁLIDO pro EmailStr do UserOut —
# "@davinci.local" (reservado) derrubava a listagem /api/users com 500.
_OPEN_ID_SISTEMA = "system:aprovacao-threema"
_EMAIL_SISTEMA = "aprovacao-threema@hadken.com"
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
    return {
        "status": order.status,
        "situacao": str(order.situacao or ""),
        "loja": loja,
    }


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
    elif u.email != _EMAIL_SISTEMA:
        # Auto-conserto: linha antiga com "@davinci.local" quebrava o
        # /api/users (500) — atualiza pro e-mail válido na passada.
        u.email = _EMAIL_SISTEMA
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
    # Texto honesto por situação: só quem está segurado em Aguardando
    # Cancelamento "sai" de algum lugar no Bling; os demais são só análise.
    if dados["situacao"] == _SITUACAO_AGUARDANDO:
        detalhe = (
            "Aprovar devolve o pedido ao fluxo normal no Bling (sai de Aguardando Cancelamento)."
        )
    else:
        detalhe = "Aprovar marca o pedido como aprovado na aba Margem do DaVinci."
    corpo = (
        f"<p>{quem}</p>"
        f"<p>{detalhe}</p>"
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

    async def _aprovar(update_bling: bool) -> None:
        await _apply_bling_decision_by_pedido(
            session,
            usuario.id,
            pedido_bling=pedido,
            sku=None,
            new_status="Aprovado",
            update_bling=update_bling,
        )

    def _codigo(e: HTTPException) -> str:
        return e.detail.get("code", "") if isinstance(e.detail, dict) else ""

    bling_ok = True
    try:
        await _aprovar(update_bling=True)
    except HTTPException as e:
        if _codigo(e) not in _CODES_FALLBACK_LOCAL:
            logger.warning("aprovar_link_falhou", pedido_bling=pedido, detail=e.detail)
            return _pagina(
                "Falha ao aprovar",
                f"<p class='erro'>Não consegui aprovar o pedido {_e(pedido)}"
                " agora.</p>"
                "<p>Tente de novo em instantes ou aprove pela aba Margem.</p>",
                status_code=502,
            )
        # Bling recusou a transição de situação (ex.: "Em andamento" não vai
        # pra Atendido). Mesmo fallback automático da aba Margem: aprova só
        # no DaVinci, sem tocar na situação do Bling. O raise acontece antes
        # de qualquer escrita, então a sessão está limpa pra segunda tentativa.
        logger.info("aprovar_link_fallback_local", pedido_bling=pedido, detail=e.detail)
        bling_ok = False
        try:
            await _aprovar(update_bling=False)
        except HTTPException as e2:
            logger.warning("aprovar_link_falhou", pedido_bling=pedido, detail=e2.detail)
            return _pagina(
                "Falha ao aprovar",
                f"<p class='erro'>Não consegui aprovar o pedido {_e(pedido)}"
                " agora.</p>"
                "<p>Tente de novo em instantes ou aprove pela aba Margem.</p>",
                status_code=502,
            )
    await session.commit()
    logger.info("aprovar_link_ok", pedido_bling=pedido, bling=bling_ok)
    if bling_ok and dados["situacao"] == _SITUACAO_AGUARDANDO:
        depois = "Ele volta ao fluxo normal no Bling."
    else:
        depois = "A aprovação ficou registrada na aba Margem do DaVinci."
    return _pagina(
        "Pedido aprovado",
        f"<p class='ok'>Pedido {_e(pedido)} aprovado ✓</p><p>{depois} Pode fechar esta página.</p>",
    )
