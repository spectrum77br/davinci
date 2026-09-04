# ruff: noqa: E501
"""Encaminhar um chamado ao JURÍDICO (Eduardo 04/09).

"vamos encaminhar toda a mensagem com as fotos para o jurídico … botão de
informar … aba jurídico com todos que mandarmos". O botão "Informar" do
DaVinci fala pelo Threema (gateway `send_simple`), que só manda TEXTO — então
o aviso vai com um LINK do dossiê: página pública por token secreto com o
cabeçalho do chamado, o histórico inteiro e todas as fotos (das mensagens, da
réplica automática e da devolução). O chamado guarda quem/quando encaminhou
(`juridico_*`), ganha um evento de sistema no histórico e passa a aparecer na
aba Jurídico. Destinatários: cadastro Informar do contexto `juridico`
(threema_informar_config).
"""

from __future__ import annotations

import html
import secrets
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    DevolucaoAnexo,
    Devolution,
    ThreemaInformarConfig,
    User,
)
from app.services import chamados as chamados_svc
from app.services import threema

logger = structlog.get_logger()

CONTEXTO = "juridico"
SAO_PAULO = chamados_svc.SAO_PAULO


def novo_token() -> str:
    return secrets.token_urlsafe(24)


def link_dossie(token: str) -> str:
    base = (get_settings().app_url or "").rstrip("/")
    return f"{base}/api/chamados/juridico/dossie/{token}"


def _fmt(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone(SAO_PAULO).strftime("%d/%m/%Y %H:%M")


async def recipients_juridico(session: AsyncSession) -> list[str]:
    row = (
        await session.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == CONTEXTO)
        )
    ).scalar_one_or_none()
    return threema.parse_recipients(row.recipients if row else "")


async def dados_dossie(session: AsyncSession, ch: Chamado) -> dict:
    """Tudo que o jurídico precisa ver: chamado, histórico com fotos, devolução."""
    msgs = list(
        (
            await session.execute(
                select(ChamadoMensagem)
                .where(ChamadoMensagem.chamado_id == ch.id)
                .order_by(ChamadoMensagem.created_at)
            )
        ).scalars().all()
    )
    anexos = list(
        (
            await session.execute(
                select(ChamadoAnexo)
                .where(ChamadoAnexo.chamado_id == ch.id)
                .order_by(ChamadoAnexo.created_at)
            )
        ).scalars().all()
    )
    por_msg: dict[UUID | None, list[ChamadoAnexo]] = {}
    for a in anexos:
        por_msg.setdefault(a.mensagem_id, []).append(a)
    dev: Devolution | None = None
    dev_anexos: list[DevolucaoAnexo] = []
    if ch.origem == "devolucao" and ch.origem_ref:
        try:
            dev = await session.get(Devolution, UUID(str(ch.origem_ref)))
        except ValueError:
            dev = None
        linhas_ids: list[UUID] = []
        if dev is not None:
            if (dev.pedido_bling or "").strip():
                linhas_ids = list(
                    (
                        await session.execute(
                            select(Devolution.id).where(
                                Devolution.pedido_bling == dev.pedido_bling.strip(),
                                Devolution.conta == dev.conta,
                            )
                        )
                    ).scalars().all()
                )
            else:
                linhas_ids = [dev.id]
            dev_anexos = list(
                (
                    await session.execute(
                        select(DevolucaoAnexo)
                        .where(DevolucaoAnexo.devolution_id.in_(linhas_ids))
                        .order_by(DevolucaoAnexo.created_at)
                    )
                ).scalars().all()
            )
    quem = None
    if ch.juridico_enviado_por:
        quem = (
            await session.execute(select(User).where(User.id == ch.juridico_enviado_por))
        ).scalar_one_or_none()
    return {
        "chamado": ch,
        "mensagens": msgs,
        "anexos_por_msg": por_msg,
        "devolucao": dev,
        "devolucao_anexos": dev_anexos,
        "quem": (quem.name or quem.email) if quem else None,
        "n_fotos": len(anexos) + len(dev_anexos),
    }


def texto_threema(
    ch: Chamado, *, link: str, n_msgs: int, n_fotos: int, quem: str, obs: str | None
) -> str:
    plat = (ch.plataforma or "").upper()
    linhas = [
        f"⚖️ JURÍDICO — chamado {ch.chamado or '(sem nº)'} · pedido "
        f"{ch.pedido_bling or '-'} / {ch.pedido_marketplace or '-'}",
        f"{plat or 'plataforma ?'} · conta {ch.conta or '-'} · origem {ch.origem}",
    ]
    if ch.produto or ch.sku:
        linhas.append(f"Produto: {ch.produto or '-'} (SKU {ch.sku or '-'})")
    if ch.status_bling:
        linhas.append(f"Status Bling: {ch.status_bling}")
    if (obs or "").strip():
        linhas.append(f"Obs.: {obs.strip()}")
    linhas.append(f"Histórico completo ({n_msgs} mensagens, {n_fotos} fotos): {link}")
    linhas.append(f"Encaminhado por {quem} em {_fmt(datetime.now(UTC))}")
    return "\n".join(linhas)


def render_html(d: dict, *, link: str) -> str:
    """Dossiê em HTML simples (abre no celular pelo link do Threema)."""
    ch: Chamado = d["chamado"]
    e = html.escape

    def img(tipo: str, aid: UUID, nome: str) -> str:
        src = f"{link}/anexo/{tipo}/{aid}"
        return (
            f'<a href="{src}" target="_blank"><img src="{src}" alt="{e(nome)}" '
            f'style="max-width:220px;max-height:220px;border:1px solid #ddd;border-radius:6px;margin:4px"></a>'
        )

    partes = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Dossiê jurídico — chamado {e(ch.chamado or '')}</title>"
        "<style>body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:900px;margin:0 auto;padding:16px;color:#222}"
        ".msg{border:1px solid #e5e5e5;border-radius:8px;padding:10px 12px;margin:10px 0}"
        ".env{background:#f2f7ff}.rec{background:#fff6ee}.sis{background:#f5f5f5;color:#666;font-style:italic}"
        ".meta{font-size:12px;color:#666;margin-bottom:6px}.txt{white-space:pre-wrap;font-size:14px}"
        "table{border-collapse:collapse}td{padding:2px 8px;vertical-align:top;font-size:14px}"
        "h1{font-size:20px}h2{font-size:16px;margin-top:22px}</style></head><body>",
        f"<h1>⚖️ Dossiê jurídico — chamado {e(ch.chamado or '(sem nº)')}</h1>",
        "<table>",
        f"<tr><td><b>Pedido Bling</b></td><td>{e(ch.pedido_bling or '-')}</td></tr>",
        f"<tr><td><b>Pedido marketplace</b></td><td>{e(ch.pedido_marketplace or '-')}</td></tr>",
        f"<tr><td><b>Plataforma / conta</b></td><td>{e((ch.plataforma or '-').upper())} · {e(ch.conta or '-')}</td></tr>",
        f"<tr><td><b>Produto</b></td><td>{e(ch.produto or '-')} (SKU {e(ch.sku or '-')})</td></tr>",
        f"<tr><td><b>Origem</b></td><td>{e(ch.origem)}</td></tr>",
        f"<tr><td><b>Status Bling</b></td><td>{e(ch.status_bling or '-')}</td></tr>",
        f"<tr><td><b>Resolvido</b></td><td>{'sim' if ch.resolvido else 'não'}</td></tr>",
    ]
    if ch.chamado_url:
        partes.append(
            f"<tr><td><b>Na plataforma</b></td><td><a href='{e(ch.chamado_url)}'>{e(ch.chamado_url)}</a></td></tr>"
        )
    if ch.observacao:
        partes.append(f"<tr><td><b>Observação</b></td><td>{e(ch.observacao)}</td></tr>")
    if ch.juridico_enviado_at:
        partes.append(
            f"<tr><td><b>Encaminhado ao jurídico</b></td><td>{e(_fmt(ch.juridico_enviado_at))}"
            f"{(' por ' + e(d['quem'])) if d.get('quem') else ''}</td></tr>"
        )
    if ch.juridico_obs:
        partes.append(f"<tr><td><b>Obs. ao jurídico</b></td><td>{e(ch.juridico_obs)}</td></tr>")
    partes.append("</table>")

    dev: Devolution | None = d.get("devolucao")
    if dev is not None:
        partes.append("<h2>Devolução</h2><table>")
        for rot, val in (
            ("Motivo", dev.motivo_devolucao),
            ("Condição", dev.condicao_produto),
            ("Link de envio (prova da expedição)", dev.link_envio),
            ("Link de abertura", dev.link_abertura),
            ("Observação", dev.observacao),
            ("Data", _fmt(dev.data) if dev.data else None),
        ):
            if val:
                v = e(str(val))
                if str(val).startswith("http"):
                    v = f"<a href='{v}'>{v}</a>"
                partes.append(f"<tr><td><b>{rot}</b></td><td>{v}</td></tr>")
        partes.append("</table>")
        if d["devolucao_anexos"]:
            partes.append("<div>")
            for a in d["devolucao_anexos"]:
                if (a.content_type or "").startswith("video/"):
                    partes.append(
                        f"<a href='{link}/anexo/d/{a.id}' target='_blank'>🎬 {e(a.filename)}</a> "
                    )
                else:
                    partes.append(img("d", a.id, a.filename))
            partes.append("</div>")

    partes.append(f"<h2>Histórico ({len(d['mensagens'])} mensagens)</h2>")
    for m in d["mensagens"]:
        cls = {"enviada": "env", "recebida": "rec"}.get(m.direcao, "sis")
        quem = m.autor_nome or ("plataforma" if m.direcao == "recebida" else "-")
        rot = {"enviada": "enviada", "recebida": "recebida da plataforma", "sistema": "sistema"}.get(
            m.direcao, m.direcao
        )
        partes.append(
            f"<div class='msg {cls}'><div class='meta'>{e(_fmt(m.created_at))} · <b>{e(quem)}</b> · "
            f"{e(rot)} · {e(m.tipo)} · {e(m.status)}{(' — ' + e(m.erro)) if m.erro else ''}</div>"
            f"<div class='txt'>{e(m.texto)}</div>"
        )
        for a in d["anexos_por_msg"].get(m.id, []):
            partes.append(img("c", a.id, a.filename))
        partes.append("</div>")
    autos = d["anexos_por_msg"].get(None, [])
    if autos:
        partes.append("<h2>Fotos da réplica automática</h2><div>")
        partes.extend(img("c", a.id, a.filename) for a in autos)
        partes.append("</div>")
    partes.append(
        f"<p style='margin-top:24px;font-size:12px;color:#888'>Gerado pelo DaVinci em {e(_fmt(datetime.now(UTC)))}. "
        "Este link é privado: não repasse.</p></body></html>"
    )
    return "".join(partes)


async def encaminhar(
    session: AsyncSession, ch: Chamado, user: User, obs: str | None
) -> dict:
    """Manda o aviso no Threema (destinatários do contexto `juridico`), carimba
    o chamado e registra no histórico. Levanta ChamadoError sem destinatário /
    sem Threema configurado. NÃO commita."""
    recipients = await recipients_juridico(session)
    if not recipients:
        raise chamados_svc.ChamadoError("sem_destinatarios")
    if not ch.juridico_token:
        ch.juridico_token = novo_token()
    link = link_dossie(ch.juridico_token)
    d = await dados_dossie(session, ch)
    quem = user.name or user.email
    texto = texto_threema(
        ch, link=link, n_msgs=len(d["mensagens"]), n_fotos=d["n_fotos"], quem=quem, obs=obs
    )
    try:
        result = await threema.ThreemaClient().send_to_all(texto, recipients)
    except threema.ThreemaConfigError as e:
        raise chamados_svc.ChamadoError("threema_nao_configurado") from e
    sent = [r for r in result.get("sent", []) if r not in result.get("failed", [])]
    if not sent:
        raise chamados_svc.ChamadoError("threema_envio_falhou")
    ch.juridico_enviado_at = datetime.now(UTC)
    ch.juridico_enviado_por = user.id
    ch.juridico_obs = (obs or "").strip() or None
    ch.juridico_destinatarios = ",".join(sent)
    session.add(
        chamados_svc.registrar_sistema(
            ch,
            f"Encaminhado ao jurídico por {quem} ({len(sent)} destinatário(s) no Threema; "
            f"{len(d['mensagens'])} mensagens, {d['n_fotos']} fotos)"
            + (f" — obs.: {obs.strip()}" if (obs or '').strip() else ""),
        )
    )
    logger.info(
        "chamado_juridico_encaminhado",
        chamado_id=str(ch.id),
        sent=sent,
        failed=result.get("failed", []),
    )
    return {"sent": sent, "failed": result.get("failed", []), "link": link}
