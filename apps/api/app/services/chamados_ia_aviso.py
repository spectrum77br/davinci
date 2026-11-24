"""Aviso no Threema quando a IA de Chamado para na tela da loja.

30/09/2026 (297130): a IA chegou no formulário "Fale conosco › E-mail" do
Mercado Livre e parou. O pedido do Cairo: "se travar não sou robô me avisa" —
a mensagem vem pra ele. A IA não tenta passar CAPTCHA nem login (CLAUDE.md da
IA): deixa a loja aberta naquela tela no Mac dos robôs (o mini da Makisa desde
13/10/2026; antes o Mac Santiago), decide `humano` com
`parado` ("captcha" | "login") e o DaVinci manda UM Threema pra quem recebe
o envio "IA de Chamado parada" em Cadastros › Envios Threema (tarefa 16,
F2/C9d; a semente copiou o cadastro `chamados_ia` do Informar — o grupo "IA
de Chamado", o mesmo do robô de Denúncia —, que fica congelado como reserva
de emergência). O botão "quem recebe o aviso" da aba Chamados › IA de Chamado
virou atalho: mostra quem recebe; só admin altera. A pessoa resolve na tela e
manda a instrução "continua" no chamado.

Canal do Threema: o de chamados (alias de `logistica` em services/threema).
Falha no envio não derruba a decisão da IA — vira evento no histórico. Envio
desligado, ou ninguém para receber, também: o aviso daquela parada se perde
(o `descarta` do catálogo) e o histórico do chamado diz por quê; no ninguém
válido do Cadastro o núcleo grava o registro `ok=false` e acende o sino dos
admins.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Chamado
from app.services import chamados as chamados_svc
from app.services import threema, threema_envios
from app.services.threema_catalogo import K

logger = structlog.get_logger()

# A linha do Informar que era a lista do aviso (hoje só a reserva de
# emergência do Cadastro: o `Legado.informar` do envio, no catálogo; o robô
# de Denúncia também a lê de reserva).
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
        "A loja ficou aberta nessa tela no Mac mini da Makisa (AdsPower). Resolva lá e "
        'mande a instrução "continua" no chamado.',
        link_chamado(ch),
    ]
    return "\n".join(linhas)


async def avisar(session: AsyncSession, ch: Chamado, parado: str, resumo: str) -> str:
    """Manda o Threema e registra o resultado no histórico do chamado. Devolve
    o resultado em texto de gente (vai de volta pra IA). NÃO commita."""
    motivo = MOTIVO.get(parado, parado)
    # O `client.disabled` de antes: o canal do remetente desligado no servidor
    # (THREEMA_CONTEXT_CHANNELS) — lido sem criar o cliente aqui.
    if threema_envios.canal("chamados") == "desativado":
        resultado = "aviso não saiu: o Threema de chamados está desativado"
    else:
        resultado = await _mandar(session, ch, texto(ch, parado, resumo))
    session.add(chamados_svc.registrar_sistema(ch, f"IA de Chamado parou {motivo} — {resultado}"))
    logger.info("chamados_ia_aviso", chamado_id=str(ch.id), parado=parado, resultado=resultado)
    return resultado


_DESLIGADO = "aviso não saiu: o aviso está desligado em Cadastros › Envios Threema"


async def _mandar(session: AsyncSession, ch: Chamado, msg: str) -> str:
    """Quem recebe vem de Cadastros › Envios Threema (`K.CHAMADOS_IA_PARADA`,
    remetente "chamados").

    * desligado: não manda;
    * ninguém pela configuração antiga (emergência ou sem semente, com a
      linha `chamados_ia` vazia): não manda, calado, como hoje;
    * ninguém válido no Cadastro: o núcleo grava o registro `ok=false` e
      acende o sino dos admins."""
    quem = await threema_envios.resolver(K.CHAMADOS_IA_PARADA, session=session)
    if not quem.ativo:
        return _DESLIGADO
    if not quem.ids and quem.motivo != threema_envios.NINGUEM_VALIDO:
        return "aviso não saiu: ninguém cadastrado em 'quem recebe o aviso' (aba IA de Chamado)"
    try:
        r = await threema_envios.enviar(K.CHAMADOS_IA_PARADA, msg, session=session)
    except threema.ThreemaConfigError as e:
        logger.warning("chamados_ia_aviso_sem_config", chamado_id=str(ch.id), erro=str(e))
        r = {"sent": [], "failed": list(quem.ids), "motivo": None}
    if r.get("motivo") == threema_envios.DESLIGADO:
        return _DESLIGADO
    if r.get("motivo"):
        return (
            "aviso não saiu: ninguém válido recebe este aviso em Cadastros › Envios Threema "
            "(os admins foram avisados)"
        )
    nomes = {d["id"]: d["nome"] for d in await threema.diretorio(session)}
    ok = [nomes.get(rid, rid) for rid in r["sent"]]
    if not ok:
        return "aviso não saiu: o envio no Threema falhou"
    resultado = f"aviso enviado no Threema pra {', '.join(ok)}"
    if r["failed"]:
        resultado += f" (falhou pra {', '.join(nomes.get(x, x) for x in r['failed'])})"
    return resultado
