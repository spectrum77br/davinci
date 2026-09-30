"""Aviso no Threema quando a IA de Chamado para na tela da loja.

30/09/2026 (297130): a IA chegou no formulário "Fale conosco › E-mail" do
Mercado Livre e parou. O pedido do Cairo: "se travar não sou robô me avisa" —
a mensagem vem pra ele. A IA não tenta passar CAPTCHA nem login (CLAUDE.md da
IA): deixa a loja aberta naquela tela no Mac Santiago, decide `humano` com
`parado` ("captcha" | "login") e o DaVinci manda UM Threema pra quem está no
cadastro `chamados_ia` (botão "quem recebe o aviso" na aba Chamados › IA de
Chamado). A pessoa resolve na tela e manda a instrução "continua" no chamado.

Canal do Threema: o de chamados (alias de `logistica` em services/threema).
Falha no envio não derruba a decisão da IA — vira evento no histórico.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Chamado, ThreemaInformarConfig
from app.services import chamados as chamados_svc
from app.services import threema

logger = structlog.get_logger()

CONTEXTO = "chamados_ia"

MOTIVO = {
    "captcha": 'no "não sou robô" (captcha/quebra-cabeça)',
    "login": "numa tela de login/código",
}


def link_chamado(ch: Chamado) -> str:
    """Aba Chamados já filtrada no pedido — o celular abre direto no caso."""
    base = (get_settings().app_url or "").rstrip("/")
    busca = ch.pedido_bling or ch.chamado or ch.pedido_marketplace or ""
    return f"{base}/chamados?search={busca}"


def texto(ch: Chamado, parado: str, resumo: str) -> str:
    plat = (ch.plataforma or "").strip() or "plataforma ?"
    linhas = [
        f"🤖 IA de Chamado parou {MOTIVO.get(parado, parado)} e precisa de você",
        f"Pedido {ch.pedido_bling or '-'} / {ch.pedido_marketplace or '-'} · {plat} · "
        f"conta {ch.conta or '-'}",
        f"Onde parou: {resumo.strip()[:400]}",
        "A loja ficou aberta nessa tela no Mac Santiago (AdsPower). Resolva lá e "
        'mande a instrução "continua" no chamado.',
        link_chamado(ch),
    ]
    return "\n".join(linhas)


async def destinatarios(session: AsyncSession) -> list[str]:
    row = (
        await session.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == CONTEXTO)
        )
    ).scalar_one_or_none()
    return threema.parse_recipients(row.recipients if row else "")


async def avisar(session: AsyncSession, ch: Chamado, parado: str, resumo: str) -> str:
    """Manda o Threema e registra o resultado no histórico do chamado. Devolve
    o resultado em texto de gente (vai de volta pra IA). NÃO commita."""
    motivo = MOTIVO.get(parado, parado)
    client = threema.ThreemaClient(contexto="chamados")
    alvos = await destinatarios(session)
    if client.disabled:
        resultado = "aviso não saiu: o Threema de chamados está desativado"
    elif not alvos:
        resultado = (
            "aviso não saiu: ninguém cadastrado em 'quem recebe o aviso' (aba IA de Chamado)"
        )
    else:
        try:
            r = await client.send_to_all(texto(ch, parado, resumo), alvos)
        except threema.ThreemaConfigError as e:
            logger.warning("chamados_ia_aviso_sem_config", chamado_id=str(ch.id), erro=str(e))
            r = {"sent": [], "failed": alvos}
        nomes = {d["id"]: d["nome"] for d in await threema.diretorio(session)}
        ok = [nomes.get(rid, rid) for rid in r["sent"]]
        if ok:
            resultado = f"aviso enviado no Threema pra {', '.join(ok)}"
            if r["failed"]:
                resultado += f" (falhou pra {', '.join(nomes.get(x, x) for x in r['failed'])})"
        else:
            resultado = "aviso não saiu: o envio no Threema falhou"
    session.add(chamados_svc.registrar_sistema(ch, f"IA de Chamado parou {motivo} — {resultado}"))
    logger.info("chamados_ia_aviso", chamado_id=str(ch.id), parado=parado, resultado=resultado)
    return resultado
