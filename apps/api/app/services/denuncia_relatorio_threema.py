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
- Threema só leva texto (o gateway simples não manda arquivo): a mensagem traz os números e o
  link do relatório com o Excel no DaVinci. O Roma não tem login — pra ele vale o texto.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DenunciaRelatorio, ThreemaInformarConfig
from app.services import threema
from app.services.denuncia_relatorio import montar
from app.services.denuncia_robo import FUSO

logger = structlog.get_logger()

CONTEXTO = "denuncia_relatorio"
HORA_ENVIO = 7  # Brasília
MAX_OCORRENCIAS = 6
_DIA_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
_SITE_CURTO = {"Mercado Livre": "ML", "TikTok Shop": "TikTok"}
_SITUACAO = (
    ("Em tratamento", "na fiscalização"),
    ("Respondida — analisar", "Anatel respondeu"),
    ("Exigência", "pede complemento"),
    ("Recebida", "recebido(s)"),
    ("Enviada", "enviado(s)"),
)


def _n(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _por_site(por_site: dict, chaves: tuple[str, ...]) -> str:
    """'Shopee 58 · ML 40 · TikTok 4' — maior primeiro, sem os zerados."""
    tot = {
        _SITE_CURTO.get(s, s): sum(_n((v or {}).get(k)) for k in chaves)
        for s, v in (por_site or {}).items()
    }
    return " · ".join(f"{s} {q}" for s, q in sorted(tot.items(), key=lambda x: -x[1]) if q)


def _grupos(por_site: dict) -> tuple[int, int]:
    nosso = sum(_n((v or {}).get("Nosso")) for v in (por_site or {}).values())
    outros = sum(
        _n((v or {}).get("Diversos")) + _n((v or {}).get("Outros"))
        for v in (por_site or {}).values()
    )
    return nosso, outros


def texto(rel: dict, link: str) -> str:
    """A mensagem: os números do dia (mesmos da gaveta do relatório) + o link."""
    dia = date.fromisoformat(rel["dia"])
    n = rel.get("numeros") or {}
    achou, den = n.get("achou") or {}, n.get("denunciou") or {}
    anatel, resp = n.get("anatel") or {}, n.get("respostas") or {}
    conf, prints = n.get("conferidos") or {}, n.get("prints") or {}
    linhas = [f"📊 Robô de Denúncia — relatório de {_DIA_SEMANA[dia.weekday()]} {dia:%d/%m}", ""]

    nosso, outros = _grupos(achou.get("por_site") or {})
    linhas.append(
        f"🔎 Achou {_n(achou.get('total'))} anúncio(s) novo(s) (Nosso {nosso} · Diversos {outros})"
    )
    if s := _por_site(achou.get("por_site") or {}, ("Nosso", "Diversos", "Outros")):
        linhas.append(f"   {s}")

    extra = []
    if _n(den.get("replicas")):
        extra.append(f"{_n(den['replicas'])} réplica(s)")
    if _n(den.get("de_novo")):
        extra.append(f"{_n(den['de_novo'])} de novo")
    linhas.append(
        f"📣 Denunciou {_n(den.get('total'))} nas lojas"
        + (f" ({' · '.join(extra)})" if extra else "")
    )
    if s := _por_site(den.get("por_site") or {}, ("Nosso", "Diversos", "Outros")):
        linhas.append(f"   {s}")

    linhas.append(
        f"🏛️ Anatel: {_n(anatel.get('lojas'))} loja(s) peticionada(s) no SEI "
        f"({_n(anatel.get('anuncios'))} anúncio(s))"
    )
    situ = anatel.get("situacao") or {}
    if any(_n(situ.get(k)) for k, _ in _SITUACAO):
        linhas.append(
            "   processos: "
            + " · ".join(f"{_n(situ.get(k))} {nome}" for k, nome in _SITUACAO if _n(situ.get(k)))
        )
    if _n(len(anatel.get("movimentos") or [])):
        linhas.append(f"   {len(anatel['movimentos'])} processo(s) andaram na Anatel no dia")

    tot = {
        k: sum(_n((v or {}).get(k)) for v in (resp.get("por_site") or {}).values())
        for k in ("removidos", "recusados", "sem_resposta")
    }
    linhas.append(
        f"📬 Respostas das lojas: {_n(resp.get('total'))} — ✅ removidos {tot['removidos']} · "
        f"❌ recusados {tot['recusados']} · ⏳ sem resposta {tot['sem_resposta']}"
    )
    linhas.append(
        f"🗑️ Saíram do ar: {_n((n.get('sairam') or {}).get('total'))} "
        f"({_n(conf.get('anuncios'))} conferido(s))"
    )
    linhas.append(
        f"📸 Prints: {_n(prints.get('capturas'))} ({_n(prints.get('anuncios'))} anúncio(s))"
    )

    passos = rel.get("passos") or []
    erros = sorted({p["nome"] for p in passos if p.get("situacao") == "erro" and p.get("nome")})
    linhas.append("")
    linhas.append(f"⚙️ Passos: {len(passos)} rodada(s)" + ("" if erros else " · nenhum erro"))
    if erros:
        linhas.append(f"   com erro: {', '.join(erros)}")
    pessoa = [o for o in rel.get("ocorrencias") or [] if o.get("tipo") == "pessoa"]
    # "… das 13:00 não começou" aparece uma vez por horário: vira uma linha só
    atrasos = [o for o in pessoa if "não começou" in str(o.get("titulo") or "")]
    outras = [o for o in pessoa if o not in atrasos]
    if pessoa:
        linhas.append(f"⚠️ Precisou de alguém: {len(pessoa)}")
        for o in outras[:MAX_OCORRENCIAS]:
            vezes = f" ({o['vezes']}x)" if _n(o.get("vezes")) > 1 else ""
            linhas.append(f"   • {str(o.get('titulo') or '').strip()[:110]}{vezes}")
        if len(outras) > MAX_OCORRENCIAS:
            linhas.append(f"   … e mais {len(outras) - MAX_OCORRENCIAS}")
        if atrasos:
            linhas.append(f"   • {len(atrasos)} horário(s) da agenda não começaram na hora")
    if buracos := rel.get("sem_noticia") or []:
        linhas.append(f"📵 Mac mini sem notícia {len(buracos)} vez(es)")
    if not rel.get("anotado"):
        linhas.append("(sem anotação dos passos neste dia — só os números)")
    linhas += ["", f"Relatório completo e Excel: {link}"]
    return "\n".join(linhas)


async def destinatarios(session: AsyncSession) -> list[str]:
    row = (
        await session.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == CONTEXTO)
        )
    ).scalar_one_or_none()
    return threema.parse_recipients(row.recipients if row else "")


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
    base = (get_settings().app_url or "").rstrip("/")
    link = f"{base}/denuncia?aba=robo&relatorio={ontem.isoformat()}"
    msg = texto(montar(ontem, row.numeros, row, agora), link)
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
