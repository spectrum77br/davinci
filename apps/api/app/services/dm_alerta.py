"""Quando o robô de DM desiste, alguém precisa saber (Eduardo, 17/09/2026).

"sobe e daí depois começa a implementar o alerta". Este é o alerta.

O robô já sabia calar quando não tinha o que dizer com segurança — mas a
conversa ficava parada no banco esperando um humano que não tinha sido
chamado. Cliente esperando em silêncio é pior que cliente sem robô nenhum:
sem robô ele pelo menos não tinha a impressão de já ter sido atendido.

M-20 (23/11/2026): o aviso é um alerta ADM no sino (`alerts.avisar_administradores`)
— todo administrador vê em /alertas, com Equipe = ADM; antes ia ao Telegram. O
ajudante engole erro sozinho: o banco recusar o alerta não pode derrubar o
envio de DM.

DUAS TRAVAS, e a segunda é a que importa:

  1. Por conversa e por tipo de problema (24h). Vinte falhas seguidas do
     provedor na mesma conversa são UM aviso.
  2. Teto por hora. O cron roda a cada minuto pegando até 10 conversas: se o
     Groq cair, são até 600 escalações por hora. Sem teto, o celular vira
     alarme e o aviso que interessa — "alguém pediu atendente" — some no meio.
     Ao estourar o teto, sai UM alerta dizendo que silenciou, e silencia.

O dedup vive no Redis, não em memória: quem escala é a api (o webhook, quando
a pessoa pede atendente) E o worker (quando o cérebro falha) — processos
diferentes, que um dict local não conversaria.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.models import AlertSeverity, DmConversa
from app.redis_client import redis
from app.services.alerts import avisar_administradores
from app.services.dm_ia import mascarar

logger = structlog.get_logger()

DEDUP_TTL = 86_400  # 24h: o mesmo problema na mesma conversa avisa uma vez
TETO_POR_HORA = 15
JANELA_TTL = 3_600

# Quanto da mensagem do cliente cabe no aviso. O suficiente pra entender o
# assunto sem transformar a notificação num paredão de texto.
TRECHO = 180

# Os tipos conhecidos (o cabeçalho já diz o caso; os outros levam o motivo).
_ICONE = {
    "pediu_humano": "🙋",
    "ambiguo": "🚨",
}


def _cabecalho(chave: str, motivo: str) -> str:
    if chave == "pediu_humano":
        return "Pediu atendente"
    if chave == "ambiguo":
        return "Envio ambíguo — CONFIRA antes de responder"
    return motivo


async def _passa_nas_travas(conversa_id: str, chave: str) -> bool:
    """False = não avisa. Redis fora do ar nunca impede o aviso."""
    try:
        novo = await redis.set(f"dm:alerta:{conversa_id}:{chave}", "1", nx=True, ex=DEDUP_TTL)
        if not novo:
            return False

        hora = datetime.now(UTC).strftime("%Y%m%d%H")
        janela = f"dm:alerta:janela:{hora}"
        n = await redis.incr(janela)
        if n == 1:
            await redis.expire(janela, JANELA_TTL)
        if n > TETO_POR_HORA:
            if n == TETO_POR_HORA + 1:
                await avisar_administradores(
                    "DM: avisos silenciados nesta hora",
                    f"Mais de {TETO_POR_HORA} conversas caíram pra humano nesta hora. "
                    "Silenciando os avisos até a virada da hora — provavelmente é "
                    "uma queda, não casos separados.",
                    severity=AlertSeverity.ERROR,
                    payload={"dm": "silenciado"},
                    dedupe_key=f"dm_alerta_silencio:{hora}",
                )
            return False
        return True
    except Exception as e:  # noqa: BLE001
        # Sem Redis o risco é avisar duas vezes. Perder o aviso é pior.
        logger.warning("dm_alerta_dedup_falhou", err=type(e).__name__)
        return True


async def avisar(
    conversa: DmConversa,
    *,
    chave: str,
    motivo: str,
    texto: str | None = None,
) -> None:
    """Avisa que esta conversa precisa de gente. Best-effort, nunca levanta.

    Chamar SEMPRE depois do commit: aviso de coisa que deu rollback manda o
    Eduardo abrir uma conversa que não mudou.

    `chave` agrupa o dedup por TIPO de problema — assim "pediu atendente" na
    segunda-feira e "envio ambíguo" na terça, na mesma conversa, são dois
    avisos, mas vinte 429 do provedor são um só.
    """
    try:
        if not await _passa_nas_travas(str(conversa.id), chave):
            return

        conta = conversa.conta or "conta?"
        titulo = f"DM: {_cabecalho(chave, motivo)} · @{conta}"
        linhas: list[str] = []

        if chave not in _ICONE:
            linhas.append(f"motivo: {motivo}")

        if texto and texto.strip():
            # `mascarar` é o mesmo da ida pro provedor: CPF e cartão não
            # precisam ir pro sino pra alguém saber o que responder. Sem
            # `html.escape`: o sino mostra texto simples (a tela escapa).
            trecho = mascarar(texto.strip())[:TRECHO]
            linhas.append(f"“{trecho}”")

        linhas.append("— responda pela caixa de entrada do Instagram")
        await avisar_administradores(
            titulo,
            "\n".join(linhas),
            severity=AlertSeverity.ERROR if chave == "ambiguo" else AlertSeverity.WARNING,
            payload={"dm": chave, "conversa_id": str(conversa.id)},
        )
        logger.info("dm_alerta", conversa=str(conversa.id), chave=chave)
    except Exception as e:  # noqa: BLE001
        # Alerta que quebra o envio de DM seria o cúmulo.
        logger.warning("dm_alerta_falhou", err=type(e).__name__)
