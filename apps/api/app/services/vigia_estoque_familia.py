"""Vigia da soma por família — anúncio preso no número antigo.

Eduardo (22/09/2026): com a soma ligada, o anúncio mostra o total de todos os
lotes de venda. Só que o número publicado passou a depender do estoque do
IRMÃO, e nada no Bling avisa o irmão quando o outro lote vende. Isso já foi
corrigido nos dois caminhos que republicam (webhook de produto e varredura
diária), mas o desenho ficou com uma dependência nova e silenciosa: se um
desses caminhos falhar, o anúncio segue vendendo com um número que não é mais
verdade e ninguém percebe.

O vigia olha pelo lado do resultado, não do mecanismo: para cada anúncio das
linhas com a soma ligada, compara o que ESTÁ publicado (`product_links.stock`,
o espelho do que foi empurrado) com o que DEVERIA estar (`saldo_publicavel`).
Anúncio parado fora da tolerância vira aviso no Threema.

POR QUE A TOLERÂNCIA EM HORAS: logo depois de ligar a soma, ou depois de uma
venda, é normal um anúncio ficar alguns minutos com o número velho até a fila
de sync alcançá-lo. Acusar isso seria só barulho. O que interessa é o anúncio
que está errado há horas — esse não vai se resolver sozinho.

DESLIGADO POR PADRÃO: sem `VIGIA_ESTOQUE_FAMILIA_THREEMA_RECIPIENTS` o vigia
roda, mede e loga, mas não manda mensagem. Assim dá para acompanhar o número
por alguns dias antes de começar a acordar alguém.

ANTI-SPAM: o cron roda 2x por dia. Não há tabela de estado — o aviso repete
enquanto o problema existir, o que é o comportamento certo para uma coisa que
alguém precisa ir consertar. Some sozinho quando os anúncios voltam a bater.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.models import Product, ProductLink
from app.services import threema
from app.services.estoque_familia import familia_ligada, saldo_publicavel

logger = structlog.get_logger()

# Status de link que representam anúncio vivo — os mesmos que o orquestrador
# considera ao empurrar estoque.
_STATUS_VIVOS = ("ok", "pending", "requires_review")

# Quanto tempo um anúncio pode ficar com o número velho antes de virar aviso.
# A fila de sync leva minutos; horas significa que alguma coisa não rodou.
_TOLERANCIA_HORAS = 6

# Acima disto o aviso vira "a soma parou", não "um anúncio atrasou".
_LIMITE_ALARME = 20


def _texto_aviso(atrasados: list[dict], total: int) -> str:
    linhas = [
        f"⚠️ Estoque somado: {len(atrasados)} de {total} anúncios estão com o "
        f"número antigo há mais de {_TOLERANCIA_HORAS}h.",
        "",
        "O anúncio mostra o total de todos os lotes. Estes ficaram parados no "
        "número de antes, então estão vendendo com estoque desatualizado:",
        "",
    ]
    for item in atrasados[:15]:
        linhas.append(
            f"• {item['sku']} ({item['plataforma']}): mostra {item['publicado']}, "
            f"deveria mostrar {item['esperado']} — parado desde {item['desde']}"
        )
    if len(atrasados) > 15:
        linhas.append(f"• ... e mais {len(atrasados) - 15}")
    linhas += [
        "",
        "Para resolver: Sincronizar Todos, ou aguardar a varredura diária de "
        "quem é dono desses produtos.",
    ]
    return "\n".join(linhas)


async def _medir(session: AsyncSession) -> dict:
    """Compara publicado × esperado em todos os anúncios das linhas somadas."""
    produtos = {
        p.id: p
        for p in (
            await session.execute(
                select(Product).where(Product.situacao == "A", Product.sku.is_not(None))
            )
        )
        .scalars()
        .all()
    }
    links = (
        (
            await session.execute(
                select(ProductLink).where(ProductLink.last_sync_status.in_(_STATUS_VIVOS))
            )
        )
        .scalars()
        .all()
    )

    corte = datetime.now(UTC) - timedelta(hours=_TOLERANCIA_HORAS)
    cache: dict[str, int] = {}
    total = certos = 0
    atrasados: list[dict] = []
    recentes = 0

    for link in links:
        produto = produtos.get(link.product_id)
        if produto is None or not familia_ligada(produto.sku):
            continue
        total += 1
        esperado = await saldo_publicavel(session, produto, cache=cache)
        publicado = int(link.stock) if link.stock is not None else None
        if publicado == esperado:
            certos += 1
            continue
        quando = link.last_sync_at
        if quando is not None and quando.tzinfo is None:
            quando = quando.replace(tzinfo=UTC)
        if quando is not None and quando > corte:
            # Ainda dentro da janela normal da fila de sync.
            recentes += 1
            continue
        atrasados.append(
            {
                "sku": produto.sku,
                "plataforma": getattr(link.platform, "value", str(link.platform)),
                "publicado": publicado,
                "esperado": esperado,
                "desde": quando.strftime("%d/%m %H:%M") if quando else "nunca sincronizado",
            }
        )

    atrasados.sort(key=lambda x: (x["sku"], x["plataforma"]))
    return {
        "total": total,
        "certos": certos,
        "aguardando_fila": recentes,
        "atrasados": atrasados,
    }


async def vigia_estoque_familia_sweep() -> dict:
    """Mede e, se houver anúncio parado fora da tolerância, avisa no Threema."""
    settings = get_settings()
    if not getattr(settings, "estoque_familia_ativo", False):
        return {"skipped": "soma_desligada"}

    async with session_scope() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        medida = await _medir(session)

    atrasados = medida["atrasados"]
    resumo = {
        "anuncios": medida["total"],
        "certos": medida["certos"],
        "aguardando_fila": medida["aguardando_fila"],
        "atrasados": len(atrasados),
        "alarme": len(atrasados) >= _LIMITE_ALARME,
    }
    if not atrasados:
        return resumo

    recipients = threema.parse_recipients(
        getattr(settings, "vigia_estoque_familia_threema_recipients", "")
    )
    if not recipients:
        resumo["aviso"] = "recipients_vazio"
        return resumo

    try:
        envio = await threema.ThreemaClient(contexto="estoque").send_to_all(
            _texto_aviso(atrasados, medida["total"]), recipients
        )
        resumo["avisados"] = len(envio.get("sent", []))
        resumo["falharam"] = len(envio.get("failed", []))
    except Exception as exc:  # noqa: BLE001
        # Best-effort: falhar o aviso não pode derrubar o tick; o próximo
        # avisa de novo, porque o problema continua lá.
        logger.warning("vigia_estoque_familia_threema_falhou", error=str(exc)[:300])
        resumo["aviso"] = "threema_falhou"
    return resumo
