"""Leitura diária da caixa do Tuta atrás dos códigos de devolução.

Eduardo (16/09/2026): "um agente que entra no tuta email, e pega os codigos de
devolução todo dia as 7:00 e encaminha a mensagem para o threema para o
usuário thatcher".

Por que passa pelo robô do Mac e não por IMAP: o Tuta NÃO oferece IMAP nem API
pública — é decisão de produto deles, por causa da criptografia ponta a ponta —
e as regras de caixa de entrada só movem para pasta, marcam spam ou descartam,
nunca encaminham para fora. Então a única porta é a tela, pelo mesmo executor
que já opera o Melhor Envio: perfil do AdsPower logado no Tuta, lê o que está
na caixa e devolve o TEXTO.

Divisão de trabalho de propósito: o robô devolve o texto cru da tela e quem
filtra é aqui, no servidor. Ajustar o que conta como "e-mail de devolução" vira
uma mudança de uma linha no Python, sem mexer no robô nem reiniciar o executor.

Ciclo: cron 10:00 UTC (07:00 BRT) → `enfileirar` grava o comando `pending` →
executor puxa no lease → lê a caixa → `registrar_resultado` chama
`entregar_resultado`, que manda no Threema do thatcher.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LogisticaRoboComando
from app.models.user import User
from app.services import threema

logger = structlog.get_logger()

ACAO = "tuta_devolucoes"

# Quem recebe o resumo. É o nome do usuário no DaVinci; o ID do Threema sai da
# ficha dele, então trocar de pessoa é mudar o cadastro, não o código.
DESTINATARIO = "thatcher"

# O que conta como e-mail de devolução. Proposital ser largo: é melhor o
# thatcher receber um e-mail a mais no primeiro dia do que perder um código.
# Depois de ver o que chega de verdade, isto aperta.
_RX_DEVOLUCAO = re.compile(
    r"devolu[çc][ãa]o|devolvid|logística revers|logistica revers|"
    r"c[óo]digo de postagem|autoriza[çc][ãa]o de postagem|reversa|return",
    re.IGNORECASE,
)

# Teto do que vai no Threema. A mensagem é lida no celular.
MAX_LINHAS = 25
MAX_CHARS = 3000


async def enfileirar(session: AsyncSession) -> LogisticaRoboComando | None:
    """Grava o comando do dia. Idempotente: se já existe um comando de hoje,
    não cria outro (o cron pode rodar duas vezes num deploy)."""
    inicio_do_dia = datetime.now(UTC) - timedelta(hours=20)
    ja = (
        await session.execute(
            select(func.count())
            .select_from(LogisticaRoboComando)
            .where(LogisticaRoboComando.acao == ACAO)
            .where(LogisticaRoboComando.created_at >= inicio_do_dia)
        )
    ).scalar_one()
    if ja:
        logger.info("tuta_devolucoes_ja_enfileirado", existentes=int(ja))
        return None
    cmd = LogisticaRoboComando(
        logistica_id=None,
        acao=ACAO,
        payload={"horas": 24},
        status="pending",
    )
    session.add(cmd)
    await session.commit()
    logger.info("tuta_devolucoes_enfileirado", comando=str(cmd.id))
    return cmd


def _linhas_de_devolucao(texto: str) -> list[str]:
    """Linhas da caixa que parecem e-mail de devolução, sem repetir."""
    vistas: set[str] = set()
    out: list[str] = []
    for linha in (texto or "").splitlines():
        limpa = " ".join(linha.split())
        if len(limpa) < 4 or limpa in vistas:
            continue
        if _RX_DEVOLUCAO.search(limpa):
            vistas.add(limpa)
            out.append(limpa)
    return out


def montar_mensagem(result: str | None) -> str:
    """Texto que vai pro Threema, a partir do que o robô devolveu."""
    hoje = datetime.now(UTC).astimezone().strftime("%d/%m")
    try:
        dados: dict[str, Any] = json.loads(result or "{}")
    except (ValueError, TypeError):
        dados = {}

    if not dados.get("ok"):
        motivo = str(dados.get("reason") or result or "sem detalhe")[:400]
        return f"Tuta {hoje} — não consegui ler a caixa.\n\n{motivo}"

    texto = str(dados.get("texto") or "")
    achados = _linhas_de_devolucao(texto)
    conta = str(dados.get("conta") or "").strip()
    cabecalho = f"Tuta {hoje}" + (f" ({conta})" if conta else "")

    if not achados:
        # Sem achado não é silêncio: mostra o que HAVIA, senão ninguém sabe se
        # o robô falhou ou se o dia foi vazio mesmo.
        amostra = [" ".join(ln.split()) for ln in texto.splitlines() if len(ln.split()) >= 2][:8]
        corpo = "\n".join(f"· {ln[:90]}" for ln in amostra) or "(caixa vazia)"
        return (
            f"{cabecalho} — nenhum e-mail de devolução hoje.\n\n"
            f"O que estava na caixa:\n{corpo}"
        )

    corpo = "\n".join(f"· {ln}" for ln in achados[:MAX_LINHAS])
    sobra = len(achados) - MAX_LINHAS
    if sobra > 0:
        corpo += f"\n… e mais {sobra}."
    return f"{cabecalho} — {len(achados)} e-mail(s) de devolução:\n\n{corpo}"[:MAX_CHARS]


async def entregar_resultado(session: AsyncSession, result: str | None) -> dict[str, Any]:
    """Manda o resumo no Threema do destinatário. Best-effort: falha de envio
    não derruba o registro do comando."""
    user = (
        await session.execute(
            select(User)
            .where(func.lower(User.name) == DESTINATARIO)
            .where(User.threema.is_not(None))
        )
    ).scalars().first()
    destinos = threema.parse_recipients(user.threema if user else None)
    if not destinos:
        logger.warning("tuta_devolucoes_sem_destinatario", usuario=DESTINATARIO)
        return {"enviado": False, "motivo": "destinatario_sem_threema"}

    texto = montar_mensagem(result)
    try:
        res = await threema.ThreemaClient(contexto="devolucoes").send_to_all(texto, destinos)
    except Exception as e:  # noqa: BLE001 — Threema fora não pode quebrar o ciclo
        logger.warning("tuta_devolucoes_threema_falhou", err=str(e)[:200])
        return {"enviado": False, "motivo": str(e)[:200]}
    logger.info("tuta_devolucoes_avisado", destinos=len(destinos))
    return {"enviado": True, "destinos": len(destinos), "resultado": res}
