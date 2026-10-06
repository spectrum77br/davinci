"""Aviso da Conferência Shopee no Threema quando a rodada fecha (contrato §7).

Molde do relatório da Denúncia (services/denuncia_relatorio_threema):

  • só com `conferencia_shopee_threema` ligado (CONFERENCIA_SHOPEE_THREEMA);
  • quem recebe: cadastro `conferencia_shopee` do Informar (a migration 0377
    deixa a linha vazia: ninguém recebe até alguém escolher);
  • remetente: o ID do Threema de chamados (ThreemaClient(contexto="chamados"),
    o mesmo da Denúncia) — o Threema não tem remetente de Marketing;
  • `threema_enviado_em` na execução carimba o envio: manda UMA vez, mesmo com
    restart. Sem ninguém cadastrado ou com o envio falhando, não carimba — o
    varredor do worker tenta de novo enquanto a rodada for recente;
  • mensagem curta (≤ 3500 bytes, o limite do gateway básico): uma linha por
    grupo + Geral, quem ficou sem dados, afiliados incompletos, o link do
    Excel (baixa SEM login, assinado com o jwt_secret, vale 7 dias e só para
    aquela execução) e o link da aba no DaVinci.

Nada aqui faz commit: quem chama decide (rota do resultado ou worker).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ConferenciaShopeeExecucao, ThreemaInformarConfig
from app.services import threema
from app.services.conferencia_shopee import calculo, saida

logger = structlog.get_logger()

CONTEXTO = "conferencia_shopee"
VALE_LINK = timedelta(days=7)
# O varredor reenvia o que falhou enquanto a rodada for recente; rodada velha
# (Threema ligado depois, cadastro feito dias depois) não dispara aviso.
JANELA_REENVIO = timedelta(hours=6)
MAX_BYTES = 3500


# ─────────────────────────────────────────────── link do Excel (sem login)


def _assinatura(execucao_id: UUID | str, ate: int) -> str:
    chave = get_settings().jwt_secret.encode()
    msg = f"conferencia-shopee-excel:{execucao_id}:{ate}".encode()
    return hmac.new(chave, msg, hashlib.sha256).hexdigest()[:32]


def token_excel(execucao_id: UUID | str, agora: datetime) -> str:
    ate = int((agora + VALE_LINK).timestamp())
    return f"{ate}.{_assinatura(execucao_id, ate)}"


def confere_excel(execucao_id: UUID | str, token: str, agora: datetime) -> bool:
    """Token daquela execução, assinado aqui e ainda no prazo. O link é
    público (sem login): token torto é 403, nunca 500 — por isso só ASCII
    (o isdigit aceita "²" e o int() recusa; compare_digest de textos com
    acento levanta TypeError) e prazo de no máximo 12 dígitos (o int() recusa
    texto de mais de 4300)."""
    ate_txt, _, assinatura = (token or "").partition(".")
    if not (ate_txt.isascii() and ate_txt.isdigit() and len(ate_txt) <= 12):
        return False
    if not assinatura or not assinatura.isascii():
        return False
    if int(ate_txt) < agora.timestamp():
        return False
    return hmac.compare_digest(assinatura, _assinatura(execucao_id, int(ate_txt)))


def _base() -> str:
    return (get_settings().app_url or "").rstrip("/")


def link_excel(execucao_id: UUID | str, agora: datetime) -> str:
    return (
        f"{_base()}/api/marketing/conferencia-shopee/execucoes/{execucao_id}/excel/link"
        f"?t={token_excel(execucao_id, agora)}"
    )


def link_davinci(execucao_id: UUID | str) -> str:
    return f"{_base()}/marketing?aba=conferencia&execucao={execucao_id}"


# ─────────────────────────────────────────────── mensagem


def _entre_parenteses(var: Mapping[str, Any]) -> str:
    texto = var.get("texto") or "—"
    return "" if texto == "—" else f" ({texto})"


def _linha_grupo(rotulo: str, total: Mapping[str, Any]) -> str:
    """"Mala: vendas R$ 120.345 (▲ 8,2%) · invest. R$ 9.876 · 8,2% s/ vendas
    (▼ 0,4 p.p.)" — S1 comparada com S2."""
    semanas = list(total.get("semanas") or [])
    s1 = semanas[0] if semanas else {}
    var_vendas, _ = calculo.variacoes(semanas, "vendas")
    var_pct, _ = calculo.variacoes(semanas, "pct")
    return (
        f"{rotulo}: vendas {calculo.dinheiro(s1.get('vendas'), 0)}{_entre_parenteses(var_vendas)}"
        f" · invest. {calculo.dinheiro(calculo.investimento(s1), 0)}"
        f" · {calculo.percentual(s1.get('pct'))} s/ vendas{_entre_parenteses(var_pct)}"
    )


def _lista(nomes: Sequence[str], limite: int | None) -> str:
    if limite is None or len(nomes) <= limite:
        return ", ".join(nomes)
    return ", ".join(nomes[:limite]) + f" e mais {len(nomes) - limite}"


def _ddmm(dia: str | None) -> str:
    d = dia or ""
    return f"{d[8:10]}/{d[5:7]}" if len(d) >= 10 else d


def texto(rel: Mapping[str, Any], link: str, link_davinci: str) -> str:
    """A mensagem inteira (≤ 3500 bytes: as listas encurtam se precisar)."""
    cabecalho = f"📊 {saida.titulo(rel)} ({saida.tipo_texto(rel)})"
    grupos = [
        _linha_grupo(g.get("rotulo") or "", g.get("total") or {})
        for g in rel.get("grupos") or []
        if (g.get("total") or {}).get("contas")
    ]
    grupos.append(_linha_grupo("Geral", rel.get("geral") or {}))
    sem_dados = [
        f"{d.get('conta')} ({saida.ROTULO_STATUS.get(d.get('status') or '', d.get('status'))})"
        for d in rel.get("contas_sem_dados") or []
    ]
    incompletos = [
        f"{d.get('conta')} (até {_ddmm(d.get('ate'))})"
        for d in rel.get("afiliados_incompletos") or []
    ]
    rodape = [f"📎 Excel: {link}", f"🔗 No DaVinci: {link_davinci}"]

    msg = ""
    for limite in (None, 8, 3, 0):
        linhas = [cabecalho, *grupos]
        if sem_dados:
            linhas.append(f"⚠️ Sem dados: {_lista(sem_dados, limite)}")
        if incompletos:
            linhas.append(f"⚠️ Afiliados incompletos: {_lista(incompletos, limite)}")
        msg = "\n".join([*linhas, *rodape])
        if len(msg.encode("utf-8")) <= MAX_BYTES:
            return msg
    # Não cabe nem com as listas cortadas (app_url gigante?): os links ficam.
    corpo = "\n".join(rodape)
    sobra = MAX_BYTES - len(corpo.encode("utf-8")) - 1
    inicio = msg.encode("utf-8")[: max(0, sobra)].decode("utf-8", "ignore")
    if inicio:
        return f"{inicio}\n{corpo}"
    return corpo.encode("utf-8")[:MAX_BYTES].decode("utf-8", "ignore")


# ─────────────────────────────────────────────── envio


async def destinatarios(session: AsyncSession) -> list[str]:
    row = (
        await session.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == CONTEXTO)
        )
    ).scalar_one_or_none()
    return threema.parse_recipients(row.recipients if row else "")


async def _enviar_um_a_um(
    client: threema.ThreemaClient, msg: str, alvos: list[str], execucao_id: UUID
) -> dict:
    """Um destinatário por vez (como o teste da Denúncia): o send_to_all só
    segura ThreemaSendError, e um timeout de rede no 2º escapava do laço
    depois de o 1º já ter recebido — sem carimbo, o varredor reenviava a
    todos de 10 em 10 min. Basta UM entregue para carimbar."""
    sent: list[str] = []
    failed: list[str] = []
    erro: str | None = None
    for rid in alvos:
        try:
            r = await client.send_to_all(msg, [rid])
        except threema.ThreemaConfigError as e:
            r, erro = {"sent": [], "failed": [rid]}, str(e)
        except Exception as e:  # rede, gateway fora: o aviso nunca derruba quem chamou
            logger.warning(
                "conferencia_shopee_threema_erro",
                execucao=str(execucao_id),
                para=rid,
                erro=f"{type(e).__name__}: {e}"[:200],
            )
            r, erro = {"sent": [], "failed": [rid]}, f"{type(e).__name__}: {e}"[:200]
        sent += r.get("sent") or []
        failed += r.get("failed") or []
    return {"sent": sent, "failed": failed, "erro": erro}


async def enviar_pendente(
    session: AsyncSession, execucao: ConferenciaShopeeExecucao, agora: datetime | None = None
) -> dict:
    """Manda o aviso da execução pronta, se ainda não foi. Trava a linha da
    execução enquanto manda: a rota e o varredor juntos não mandam dois."""
    agora = agora or datetime.now(UTC)
    if not get_settings().conferencia_shopee_threema:
        return {"enviado": False, "motivo": "aviso no Threema desligado"}
    row = (
        await session.execute(
            select(ConferenciaShopeeExecucao)
            .where(ConferenciaShopeeExecucao.id == execucao.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None or row.status != "pronto" or not row.relatorio:
        return {"enviado": False, "motivo": "sem relatório"}
    if row.threema_enviado_em is not None:
        return {"enviado": False, "motivo": "já enviado"}
    alvos = await destinatarios(session)
    client = threema.ThreemaClient(contexto="chamados")
    if client.disabled or not alvos:
        motivo = "Threema de chamados desativado" if client.disabled else "ninguém cadastrado"
        logger.warning(
            "conferencia_shopee_threema_sem_destino", execucao=str(row.id), motivo=motivo
        )
        return {"enviado": False, "motivo": motivo}
    msg = texto(row.relatorio, link_excel(row.id, agora), link_davinci(row.id))
    r = await _enviar_um_a_um(client, msg, alvos, row.id)
    if not r.get("sent"):
        logger.warning(
            "conferencia_shopee_threema_falhou",
            execucao=str(row.id),
            falhou=r.get("failed"),
            erro=r.get("erro"),
        )
        return {"enviado": False, "motivo": "envio falhou"}
    row.threema_enviado_em = agora
    await session.flush()
    logger.info(
        "conferencia_shopee_threema", execucao=str(row.id), sent=r["sent"], failed=r.get("failed")
    )
    return {
        "enviado": True,
        "execucao": str(row.id),
        "sent": r["sent"],
        "failed": r.get("failed") or [],
    }


async def enviar_pendentes(session: AsyncSession, agora: datetime | None = None) -> list[dict]:
    """O varredor: avisa as execuções prontas nas últimas horas que ainda não
    foram avisadas (o envio na hora falhou ou não tinha ninguém cadastrado)."""
    agora = agora or datetime.now(UTC)
    if not get_settings().conferencia_shopee_threema:
        return []
    pendentes = (
        await session.execute(
            select(ConferenciaShopeeExecucao)
            .where(
                ConferenciaShopeeExecucao.status == "pronto",
                ConferenciaShopeeExecucao.threema_enviado_em.is_(None),
                ConferenciaShopeeExecucao.finalizado_em >= agora - JANELA_REENVIO,
            )
            .order_by(ConferenciaShopeeExecucao.finalizado_em)
        )
    ).scalars().all()
    return [await enviar_pendente(session, ex, agora) for ex in pendentes]
