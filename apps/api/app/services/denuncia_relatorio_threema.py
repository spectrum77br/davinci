"""Relatório do dia do robô de Denúncia no Threema (06/10/2026).

Vinicius: "esse relatório consegue enviar pelo Threema para Cairo, Hary e Roma? todo dia pode
enviar … os de hoje pode mandar". O relatório é o mesmo de Robô › Ocorrências (services/
denuncia_relatorio): fechado depois da meia-noite pelo worker `denuncia_relatorio_fechar` (:07 de
toda hora). Aqui só se escreve a mensagem e se manda UMA vez por dia, de manhã (a partir das 7h de
Brasília — fechar 00:07 e mandar na hora acordaria todo mundo).

- Só o relatório de ONTEM: dia mais velho não manda (deploy depois de dias parado não despeja a
  semana inteira); o worker roda de hora em hora, então o envio que falhou tenta de novo até
  meia-noite.
- `threema_enviado_em` na linha do dia carimba o envio (manda uma vez só, mesmo com restart).
  Carimba quando ao menos um recebeu — quem falhou não recebe de novo (o log diz quem).
- Quem recebe: cadastro `denuncia_relatorio` do Informar (botão "Quem recebe o relatório" em
  Robô › Ocorrências; a migração 0373 já deixa Cairo, harry potter e Roma). Sem ninguém salvo,
  não manda e não carimba (quem for cadastrado ainda no dia recebe).
- Mensagem CURTA (06/10, depois do 1º envio: "muito grande… coloca só o resumo pequeno e manda o
  Excel"): o resumo geral em 5 linhas + o link do Excel. O gateway do Threema daqui é o básico (só
  texto, não manda arquivo), então o Excel vai como LINK QUE BAIXA DIRETO, sem login (escolha dele:
  o Roma não tem login) — assinado com o jwt_secret, vale só pra aquele dia e por 7 dias
  (`link_excel` / `confere_excel`; rota GET /api/denuncia/relatorios/{dia}/excel/link).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DenunciaRelatorio, ThreemaInformarConfig
from app.services import threema
from app.services.denuncia_robo import FUSO

logger = structlog.get_logger()

CONTEXTO = "denuncia_relatorio"
HORA_ENVIO = 7  # Brasília
VALE_LINK = timedelta(days=7)
_DIA_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def _n(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


# ─────────────────────────────────────────────── link do Excel (sem login)


def _assinatura(dia: date, ate: int) -> str:
    chave = get_settings().jwt_secret.encode()
    msg = f"denuncia-relatorio-excel:{dia.isoformat()}:{ate}".encode()
    return hmac.new(chave, msg, hashlib.sha256).hexdigest()[:32]


def token_excel(dia: date, agora: datetime) -> str:
    ate = int((agora + VALE_LINK).timestamp())
    return f"{ate}.{_assinatura(dia, ate)}"


def confere_excel(dia: date, token: str, agora: datetime) -> bool:
    """Token daquele dia, assinado aqui e ainda no prazo."""
    ate_txt, _, assinatura = (token or "").partition(".")
    if not ate_txt.isdigit() or not assinatura:
        return False
    if int(ate_txt) < agora.timestamp():
        return False
    return hmac.compare_digest(assinatura, _assinatura(dia, int(ate_txt)))


def link_excel(dia: date, agora: datetime) -> str:
    base = (get_settings().app_url or "").rstrip("/")
    return (
        f"{base}/api/denuncia/relatorios/{dia.isoformat()}/excel/link?t={token_excel(dia, agora)}"
    )


# ─────────────────────────────────────────────── mensagem


def texto(dia: date, numeros: dict, link: str) -> str:
    """Resumo geral do dia (os números da gaveta do relatório) + o link do Excel."""
    n = numeros or {}
    anatel = n.get("anatel") or {}
    resp = (n.get("respostas") or {}).get("por_site") or {}
    tot = {
        k: sum(_n((v or {}).get(k)) for v in resp.values())
        for k in ("removidos", "recusados", "sem_resposta")
    }
    return "\n".join(
        [
            f"📊 Relatório geral do robô de Denúncia — {_DIA_SEMANA[dia.weekday()]} {dia:%d/%m}",
            f"• {_n((n.get('achou') or {}).get('total'))} anúncios novos",
            f"• {_n((n.get('denunciou') or {}).get('total'))} denúncias nas lojas",
            f"• {_n(anatel.get('lojas'))} lojas na Anatel ({_n(anatel.get('anuncios'))} anúncios)",
            f"• Respostas: {tot['removidos']} removidos · {tot['recusados']} recusados · "
            f"{tot['sem_resposta']} sem resposta",
            f"• {_n((n.get('sairam') or {}).get('total'))} saíram do ar",
            f"📎 Excel: {link}",
        ]
    )


async def destinatarios(session: AsyncSession) -> list[str]:
    row = (
        await session.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == CONTEXTO)
        )
    ).scalar_one_or_none()
    return threema.parse_recipients(row.recipients if row else "")


async def enviar_teste(session: AsyncSession, dia: date, para: str, agora: datetime) -> dict:
    """06/10 ("manda só no Cairo um teste… para eu aprovar"): a mensagem de um dia fechado pra UM
    destinatário do diretório do Threema. Não carimba o dia (o envio de verdade segue igual)."""
    row = await session.get(DenunciaRelatorio, dia)
    if row is None or row.numeros is None:
        return {"enviado": False, "motivo": "relatório do dia ainda não fechado"}
    rid = (para or "").strip().upper()
    if rid not in {d["id"] for d in await threema.diretorio(session)}:
        return {"enviado": False, "motivo": "destinatário fora do diretório do Threema"}
    client = threema.ThreemaClient(contexto="chamados")
    try:
        r = await client.send_to_all(texto(dia, row.numeros, link_excel(dia, agora)), [rid])
    except threema.ThreemaConfigError as e:
        r = {"sent": [], "failed": [rid], "erro": str(e)}
    logger.info(
        "denuncia_relatorio_threema_teste",
        dia=dia.isoformat(),
        sent=r.get("sent"),
        failed=r.get("failed"),
    )
    if not r.get("sent"):
        return {"enviado": False, "motivo": "envio falhou"}
    return {"enviado": True, "dia": dia.isoformat(), "sent": r["sent"]}


async def enviar_pendente(session: AsyncSession, agora: datetime | None = None) -> dict:
    """O relatório de ontem, se já fechado, ainda não enviado e já passou das 7h. Commit fica com
    o caller (o worker roda dentro do session_scope)."""
    agora = agora or datetime.now(UTC)
    local = agora.astimezone(FUSO)
    if local.hour < HORA_ENVIO:
        return {"enviado": False, "motivo": "antes das 7h"}
    ontem = local.date() - timedelta(days=1)
    row = await session.get(DenunciaRelatorio, ontem)
    if row is None or row.numeros is None:
        return {"enviado": False, "motivo": "relatório de ontem ainda não fechado"}
    if row.threema_enviado_em is not None:
        return {"enviado": False, "motivo": "já enviado"}
    alvos = await destinatarios(session)
    client = threema.ThreemaClient(contexto="chamados")
    if client.disabled or not alvos:
        motivo = "Threema de chamados desativado" if client.disabled else "ninguém cadastrado"
        logger.warning(
            "denuncia_relatorio_threema_sem_destino", dia=ontem.isoformat(), motivo=motivo
        )
        return {"enviado": False, "motivo": motivo}
    msg = texto(ontem, row.numeros, link_excel(ontem, agora))
    try:
        r = await client.send_to_all(msg, alvos)
    except threema.ThreemaConfigError as e:
        r = {"sent": [], "failed": alvos, "erro": str(e)}
    if not r.get("sent"):
        logger.warning(
            "denuncia_relatorio_threema_falhou",
            dia=ontem.isoformat(),
            falhou=r.get("failed"),
            erro=r.get("erro"),
        )
        return {"enviado": False, "motivo": "envio falhou"}
    row.threema_enviado_em = agora
    await session.flush()
    logger.info(
        "denuncia_relatorio_threema", dia=ontem.isoformat(), sent=r["sent"], failed=r.get("failed")
    )
    return {
        "enviado": True,
        "dia": ontem.isoformat(),
        "sent": r["sent"],
        "failed": r.get("failed") or [],
    }
