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
- Quem recebe: o envio "Relatório da Denúncia" de Cadastros › Envios Threema (tarefa 16, F2/C9d;
  a semente copiou o cadastro `denuncia_relatorio` do Informar — a migração 0373 deixou Cairo,
  harry potter e Roma —, que fica congelado como reserva de emergência). O botão "Quem recebe o
  relatório" em Robô › Ocorrências virou atalho: mostra quem recebe; só admin altera. O
  liga/desliga continua sendo o modo do robô na Ouvidoria (a tela do Cadastro grava lá). Sem
  ninguém pela configuração antiga, não manda e não carimba, calado (quem for cadastrado ainda no
  dia recebe); ninguém válido no Cadastro também não carimba, mas a rodada sai com falha e o
  núcleo grava o registro `ok=false` e acende o sino dos admins.
- O teste da tela (um destinatário escolhido) sai pelo mesmo envio, origem `teste`.
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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.models import DenunciaRelatorio
from app.services import ouvidoria, threema, threema_envios
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.denuncia_robo import FUSO
from app.services.threema_catalogo import K

logger = structlog.get_logger()

# A linha do Informar que era a lista do relatório (hoje só a reserva de
# emergência do Cadastro: o `Legado.informar` do envio, no catálogo).
CONTEXTO = "denuncia_relatorio"
ROBO = "denuncia_relatorio_threema"  # chave na Ouvidoria › Robôs (12/11/2026)


_SWEEP_LOCK_KEY = 0x64726C74  # ascii "drlt" — advisory lock do tick/"Rodar agora"


class _EnvioFalhouError(Exception):
    """Ninguém recebeu: a rodada da Ouvidoria fica marcada como falha."""

    def __init__(self, detalhe: str, motivo: str = "envio falhou") -> None:
        super().__init__(detalhe)
        self.motivo = motivo


async def denuncia_relatorio_threema_sweep() -> dict:
    """Tick do worker (:10 de toda hora) e "Rodar agora" da Ouvidoria: sessão própria,
    serializado por advisory lock. Quem decide se manda é o `enviar_pendente` (ligado,
    depois das 7h, relatório de ontem fechado e ainda não enviado)."""
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
            return await enviar_pendente(session)
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
    """Token daquele dia, assinado aqui e ainda no prazo. Link sem login:
    token torto é 403, nunca 500 — só ASCII (o isdigit aceita "²" e o int()
    recusa; compare_digest de textos com acento levanta TypeError) e prazo de
    no máximo 12 dígitos (o int() recusa texto de mais de 4300)."""
    ate_txt, _, assinatura = (token or "").partition(".")
    if not (ate_txt.isascii() and ate_txt.isdigit() and len(ate_txt) <= 12):
        return False
    if not assinatura or not assinatura.isascii():
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


async def enviar_teste(session: AsyncSession, dia: date, para: str, agora: datetime) -> dict:
    """06/10 ("manda só no Cairo um teste… para eu aprovar"): a mensagem de um dia fechado pra UM
    destinatário do diretório do Threema. Não carimba o dia (o envio de verdade segue igual).
    Sai pelo envio do relatório no Cadastro, origem `teste`, só para quem foi escolhido
    (`forcar_para`: vale com o robô desligado, como antes)."""
    row = await session.get(DenunciaRelatorio, dia)
    if row is None or row.numeros is None:
        return {"enviado": False, "motivo": "relatório do dia ainda não fechado"}
    rid = (para or "").strip().upper()
    if rid not in {d["id"] for d in await threema.diretorio(session)}:
        return {"enviado": False, "motivo": "destinatário fora do diretório do Threema"}
    try:
        r = await threema_envios.enviar(
            K.DENUNCIA_RELATORIO_DIARIO,
            texto(dia, row.numeros, link_excel(dia, agora)),
            origem="teste",
            forcar_para=[rid],
            session=session,
        )
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
    # 12/11/2026 (Vinicius): o envio virou o robô "Relatório da Denúncia" da Ouvidoria ›
    # Robôs, que nasceu desligado. Só manda ligado; silencioso/desligado não manda nada
    # (o relatório do dia continua sendo fechado pelo worker, só a mensagem para).
    if await ouvidoria.modo_efetivo(session, ROBO) != "ligado":
        return {"enviado": False, "motivo": "desligado na Ouvidoria › Robôs"}
    local = agora.astimezone(FUSO)
    if local.hour < HORA_ENVIO:
        return {"enviado": False, "motivo": "antes das 7h"}
    ontem = local.date() - timedelta(days=1)
    row = await session.get(DenunciaRelatorio, ontem)
    if row is None or row.numeros is None:
        return {"enviado": False, "motivo": "relatório de ontem ainda não fechado"}
    if row.threema_enviado_em is not None:
        return {"enviado": False, "motivo": "já enviado"}
    # Quem recebe: Cadastros › Envios Threema (`K.DENUNCIA_RELATORIO_DIARIO`, remetente
    # "chamados"). O `client.disabled` de antes é o canal do remetente desligado no servidor.
    motivo = None
    alvos: list[str] = []
    if threema_envios.canal("chamados") == "desativado":
        motivo = "Threema de chamados desativado"
    else:
        quem = await threema_envios.resolver(K.DENUNCIA_RELATORIO_DIARIO, session=session)
        alvos = list(quem.ids)
        if not quem.ativo:
            motivo = "desligado na Ouvidoria › Robôs"
        elif not quem.ids and quem.motivo != threema_envios.NINGUEM_VALIDO:
            # ninguém pela configuração antiga (emergência ou sem semente, linha vazia)
            motivo = "ninguém cadastrado"
    if motivo:
        logger.warning(
            "denuncia_relatorio_threema_sem_destino", dia=ontem.isoformat(), motivo=motivo
        )
        return {"enviado": False, "motivo": motivo}
    msg = texto(ontem, row.numeros, link_excel(ontem, agora))
    # A rodada (só quando de fato tenta mandar) é o que a coluna Saúde do painel lê;
    # envio que não chegou a ninguém sai como rodada com falha — também o ninguém válido
    # no Cadastro (o núcleo grava o registro `ok=false` e acende o sino dos admins).
    try:
        async with ouvidoria.Rodada(session, ROBO) as rodada:
            try:
                r = await threema_envios.enviar(K.DENUNCIA_RELATORIO_DIARIO, msg, session=session)
            except threema.ThreemaConfigError as e:
                r = {"sent": [], "failed": alvos, "erro": str(e)}
            rodada.contadores["enviados"] = len(r.get("sent") or [])
            rodada.contadores["falharam"] = len(r.get("failed") or [])
            if r.get("motivo"):
                # ninguém válido (ou desligou entre a leitura e o envio): não carimba
                nao_saiu = (
                    "desligado"
                    if r["motivo"] == threema_envios.DESLIGADO
                    else "ninguém válido para receber"
                )
                rodada.resumo = (
                    f"relatório de {ontem:%d/%m}: {nao_saiu} (Cadastros › Envios Threema)"
                )
                raise _EnvioFalhouError(r["motivo"], nao_saiu)
            if not r.get("sent"):
                rodada.resumo = f"relatório de {ontem:%d/%m}: ninguém recebeu"
                raise _EnvioFalhouError(str(r.get("erro") or r.get("failed") or "")[:300])
            rodada.resumo = f"relatório de {ontem:%d/%m} para {len(r['sent'])} pessoa(s)"
            row.threema_enviado_em = agora
            await session.flush()
    except _EnvioFalhouError as e:
        logger.warning("denuncia_relatorio_threema_falhou", dia=ontem.isoformat(), erro=str(e))
        return {"enviado": False, "motivo": e.motivo}
    logger.info(
        "denuncia_relatorio_threema", dia=ontem.isoformat(), sent=r["sent"], failed=r.get("failed")
    )
    return {
        "enviado": True,
        "dia": ontem.isoformat(),
        "sent": r["sent"],
        "failed": r.get("failed") or [],
    }
