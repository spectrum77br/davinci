"""Aviso no Threema quando o robô de Denúncia precisa de alguém (05/10/2026).

Cairo: "isso coloca para avisar no Threema … só o Cairo recebe, que sou eu … avisar os 3, tudo
que colocou ali" — captcha/verificação na tela do Mac mini, robô parado, Mac mini sem notícia e
SEI pedindo código/assinatura. De madrugada também: o caso que motivou foi o Mercado Livre pedindo
"não sou robô" à 01:00 de 05/10 — o robô espera 10 min na tela, ninguém viu, e o ML ficou parado
até de manhã.

Quem decide o que é ocorrência é o `montar_painel` (a mesma lista da aba Robô › Ocorrências); aqui
só se escolhe quais avisam e se manda UM Threema por volta com as novas. Roda no worker a cada
2 min (`denuncia_robo_aviso_tick`) — e não no recebimento do status, porque "mini sem notícia" é
justamente quando nada chega.

Repetição (marcas no Redis, sem tabela):
  - linha do robô (`prob:…`, do _canal/PROBLEMAS.jsonl): avisa uma vez e só se for recente
    (30 min — a lista do mini guarda 24 h: sem isso, a 1ª volta depois de um deploy mandaria o dia
    inteiro); e no máximo uma por hora do mesmo tipo (o código do SEI que não chega gera uma
    linha a cada tentativa);
  - estado do momento (`agora:…`: mini sem notícia, robô parado, SEI deslogado): avisa quando
    começa; a marca é renovada enquanto continua; se some por 30 min e volta, avisa de novo;
  - "Tratado" na aba Robô tira da lista → não avisa.
Quem recebe: cadastro `denuncia_robo` do Informar (botão "Quem recebe o aviso" em Robô ›
Ocorrências); sem cadastro salvo, o da IA de Chamado (`chamados_ia` — o Cairo). Canal do
Threema: o de chamados. Envio que falha não carimba (tenta de novo na volta seguinte); sem
ninguém cadastrado, carimba (não há a quem mandar e o log não repete a cada 2 min).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    DenunciaRoboAgenda,
    DenunciaRoboStatus,
    DenunciaRoboTratada,
    ThreemaInformarConfig,
)
from app.redis_client import redis
from app.services import threema
from app.services.denuncia_robo import PASSOS, montar_painel

logger = structlog.get_logger()

CONTEXTO = "denuncia_robo"
CONTEXTO_RESERVA = "chamados_ia"
FUSO = ZoneInfo("America/Sao_Paulo")
TRATADA_VALE = timedelta(days=3)  # o mesmo do GET /api/denuncia/robo
RECENTE = timedelta(minutes=30)
PREFIXO = "denuncia:robo_aviso:"
MARCA_ROBO_S = 3 * 24 * 3600
MARCA_AGORA_S = 30 * 60
MESMO_TIPO_S = 3600
MAX_NA_MENSAGEM = 6

ROTULO = {
    "captcha": ("🧩", "Captcha na tela"),
    "sei": ("🔐", "SEI / Anatel"),
    "parado": ("⛔", "Robô parado"),
    "mini": ("📡", "Mac mini sem notícia"),
}
_RE_CAPTCHA = re.compile(
    r"captcha|verifica[çc][ãa]o|n[ãa]o sou (um )?rob[ôo]|muro|quebra-cabe", re.I
)
_RE_SEI = re.compile(r"\bsei\b|c[óo]digo de acesso|assinatura", re.I)
_RE_PARADO = re.compile(r"perfil 50 .*n[ãa]o abre|adspower n[ãa]o", re.I)


def categoria(o: dict) -> str | None:
    """Em qual dos quatro avisos a ocorrência entra (None = não avisa). Só as que precisam de
    alguém."""
    if o.get("tipo") != "pessoa":
        return None
    chave = str(o.get("chave") or "")
    if chave == "agora:mini":
        return "mini"
    texto_ = " ".join(str(o.get(k) or "") for k in ("chave", "titulo", "detalhe", "o_que_fazer"))
    if _RE_CAPTCHA.search(texto_):
        return "captcha"
    if chave in ("agora:sei", "agora:sessao_sei") or _RE_SEI.search(texto_):
        return "sei"
    if (
        chave == "agora:agente"
        or chave.startswith(("agora:fila_", "agora:agenda_"))
        or _RE_PARADO.search(texto_)
    ):
        return "parado"
    return None


def _quando(v: Any) -> datetime | None:
    """"2026-10-05 06:24" do PROBLEMAS (sem fuso = Brasília) ou ISO."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=FUSO)


def escolher(painel: dict, agora: datetime) -> list[dict]:
    """As ocorrências do painel que viram aviso, com a categoria em `_cat`."""
    out = []
    for o in painel.get("ocorrencias") or []:
        cat = categoria(o)
        if not cat:
            continue
        if not str(o.get("chave") or "").startswith("agora:"):
            q = _quando(o.get("quando"))
            if q is None or agora - q > RECENTE:
                continue
        out.append({**o, "_cat": cat})
    return out


def texto(itens: list[dict], link: str) -> str:
    linhas = ["🤖 Robô de Denúncia precisa de você"]
    for o in itens[:MAX_NA_MENSAGEM]:
        emoji, nome = ROTULO[o["_cat"]]
        linhas.append(f"{emoji} {nome}: {str(o.get('titulo') or '').strip()[:160]}")
        if o.get("detalhe"):
            linhas.append(f"   {str(o['detalhe']).strip()[:220]}")
        if o.get("o_que_fazer"):
            linhas.append(f"   O que fazer: {str(o['o_que_fazer']).strip()[:220]}")
    if len(itens) > MAX_NA_MENSAGEM:
        linhas.append(f"… e mais {len(itens) - MAX_NA_MENSAGEM} na aba Robô")
    if any(o["_cat"] == "captcha" for o in itens):
        linhas.append(
            "O robô espera uns 10 min com a tela aberta no Mac mini (perfil 50) — resolva lá."
        )
    linhas.append(link)
    return "\n".join(linhas)


async def destinatarios(session: AsyncSession) -> list[str]:
    """Cadastro `denuncia_robo`; sem cadastro salvo, o da IA de Chamado. Cadastro salvo vazio =
    ninguém."""
    for ctx in (CONTEXTO, CONTEXTO_RESERVA):
        row = (
            await session.execute(
                select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == ctx)
            )
        ).scalar_one_or_none()
        if row is not None:
            return threema.parse_recipients(row.recipients)
    return []


async def _painel(session: AsyncSession, agora: datetime) -> dict:
    """O mesmo painel do GET /api/denuncia/robo (status mais novo, tratadas de 3 dias, agenda)."""
    st = (
        await session.execute(
            select(DenunciaRoboStatus).order_by(DenunciaRoboStatus.recebido_em.desc()).limit(1)
        )
    ).scalar_one_or_none()
    tratadas = set(
        (
            await session.execute(
                select(DenunciaRoboTratada.chave).where(
                    DenunciaRoboTratada.tratada_em >= agora - TRATADA_VALE
                )
            )
        ).scalars().all()
    )
    agenda = {
        r.acao: {"ligado": bool(r.ligado), "horarios": list(r.horarios or [])}
        for r in (await session.execute(select(DenunciaRoboAgenda))).scalars().all()
        if r.acao in PASSOS
    }
    return montar_painel(
        st.dados if st else None, st.recebido_em if st else None, agora,
        tratadas=tratadas, agenda=agenda,
    )


def _chave_tipo(o: dict) -> str:
    return PREFIXO + "tipo:" + re.sub(r"\W+", "_", str(o.get("titulo") or "").lower())[:80]


async def _novas(itens: list[dict]) -> tuple[list[dict], list[str]]:
    """Carimba no Redis e devolve as que ainda não foram avisadas (+ as marcas novas, pra desfazer
    se o envio falhar)."""
    novas: list[dict] = []
    marcas: list[str] = []
    for o in itens:
        k = PREFIXO + str(o["chave"])
        if str(o["chave"]).startswith("agora:"):
            if await redis.set(k, "1", nx=True, ex=MARCA_AGORA_S):
                novas.append(o)
                marcas.append(k)
            else:
                await redis.expire(k, MARCA_AGORA_S)   # continua: não avisa de novo
            continue
        if not await redis.set(k, "1", nx=True, ex=MARCA_ROBO_S):
            continue
        marcas.append(k)
        kt = _chave_tipo(o)
        if not await redis.set(kt, "1", nx=True, ex=MESMO_TIPO_S):
            continue   # o mesmo tipo já avisou na última hora (esta linha fica carimbada)
        marcas.append(kt)
        novas.append(o)
    return novas, marcas


async def rodar(session: AsyncSession, agora: datetime | None = None) -> dict:
    """Uma volta: monta o painel, escolhe, carimba e manda. Devolve o que fez (para o log do
    worker)."""
    agora = agora or datetime.now(UTC)
    candidatas = escolher(await _painel(session, agora), agora)
    if not candidatas:
        return {"avisadas": 0}
    novas, marcas = await _novas(candidatas)
    if not novas:
        return {"avisadas": 0}
    client = threema.ThreemaClient(contexto="chamados")
    alvos = await destinatarios(session)
    if client.disabled or not alvos:
        motivo = "Threema de chamados desativado" if client.disabled else "ninguém cadastrado"
        logger.warning("denuncia_robo_aviso_sem_destino", motivo=motivo,
                       chaves=[o["chave"] for o in novas])
        return {"avisadas": 0, "erro": motivo}
    link = f"{(get_settings().app_url or '').rstrip('/')}/denuncia?aba=robo"
    try:
        r = await client.send_to_all(texto(novas, link), alvos)
    except threema.ThreemaConfigError as e:
        r = {"sent": [], "failed": alvos, "erro": str(e)}
    if not r.get("sent"):
        for k in marcas:   # não carimba: tenta de novo na próxima volta
            await redis.delete(k)
        logger.warning("denuncia_robo_aviso_falhou", falhou=r.get("failed"), erro=r.get("erro"))
        return {"avisadas": 0, "erro": "envio falhou"}
    logger.info("denuncia_robo_aviso", chaves=[o["chave"] for o in novas], sent=r["sent"],
                failed=r.get("failed"))
    return {"avisadas": len(novas), "chaves": [o["chave"] for o in novas]}
