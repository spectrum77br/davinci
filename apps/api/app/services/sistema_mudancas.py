"""Sistema › Mudanças: o pedido de mudança no próprio DaVinci, do Claude ao ar.

Eduardo, 17/11/2026: "precisamos dar mais permissão para ele [o chefe] poder
fazer algumas correções … coisa rápida … fazer dentro do conector para ele
mexer e aí manda para a gente, a gente aprova para rodar". Decisões dele:
o que se pede é mudança NO PRÓPRIO SISTEMA (texto, regra, tela); quem faz é
um robô no Mac dele (Claude Code sem tela — apps/mudancas-robo); só ele e o
Thorfinn aprovam; o Mac fica sempre ligado.

O caminho de um pedido (`sistema_mudanca.status`):

    pedida ──(robô pega)──► fazendo ──► pronta ──(Aprovar)──► aprovada
       ▲                       │          │                      │
       └─(robô parou: volta)───┘          └─(Recusar)─► recusada │
                               ├──► precisa_equipe               ▼
                               └──► falhou ◄────────────── publicando ──► publicada

  • `fazendo`/`publicando` têm prazo (`prazo_robo`), renovado pelo robô
    enquanto trabalha. Passou do prazo: o `fazendo` volta para a fila (até
    `MAX_TENTATIVAS`, depois falha) e o `publicando` FALHA — a publicação pode
    ter ido em parte, uma pessoa confere (nunca repete sozinha).
  • Um trabalho de cada vez: o robô só ganha outro quando nada está em
    `fazendo`/`publicando` com prazo valendo.
  • A aprovação vale para O diff que a pessoa viu (`diff_sha`): se o robô
    refizer o pedido entre a tela abrir e o clique, o Aprovar é recusado.
  • Publicar vem antes de fazer (quem já foi aprovado não espera a fila).

Quem aprova: `sistema_mudanca_aprovador` (por user_id — ver o model).
Quem pede: admin ativo, pelo conector (`claude_tarefas.pedir_mudanca`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SistemaMudanca, SistemaMudancaAprovador, User, UserRole, UserStatus
from app.models.enums import AlertSeverity, AlertType
from app.services.alerts import emit_alert

logger = structlog.get_logger(__name__)
BRT = ZoneInfo("America/Sao_Paulo")

STATUS_TEXTO = {
    "pedida": "Na fila do robô",
    "fazendo": "Robô fazendo",
    "pronta": "Esperando aprovação",
    "aprovada": "Aprovada — vai para o ar",
    "publicando": "Publicando",
    "publicada": "No ar",
    "recusada": "Recusada",
    "precisa_equipe": "Precisa da equipe",
    "falhou": "Falhou",
}
ABERTAS = ("pedida", "fazendo", "pronta", "aprovada", "publicando")
DO_ROBO = ("fazendo", "publicando")

# Prazo do robô por etapa (renovado pelo `vivo` enquanto ele trabalha).
PRAZO_FAZER = timedelta(minutes=45)
PRAZO_PUBLICAR = timedelta(minutes=40)
# Quantas vezes um pedido pode ser pego para FAZER (o robô parou no meio).
MAX_TENTATIVAS = 2
# Publicação adiada pelo robô (outro deploy rodando, disco do servidor): só
# volta a ser entregue depois disto — o robô roda a cada minuto.
ESPERA_ADIADA = timedelta(minutes=10)

MAX_TITULO = 200
MAX_DESCRICAO = 4000
MAX_ONDE = 500
MIN_MOTIVO = 3
MAX_MOTIVO = 1000
MAX_RESUMO = 8000
MAX_TESTES = 60_000
MAX_DIFF = 400_000
MAX_ERRO = 4000
MAX_ARQUIVOS = 200


class MudancaError(Exception):
    """Pedido que a regra não deixa fazer (`code` vai para a tela/robô)."""

    def __init__(self, code: str, detalhe: str = "") -> None:
        super().__init__(detalhe or code)
        self.code = code
        self.detalhe = detalhe or code


def _agora() -> datetime:
    return datetime.now(UTC)


def codigo(m: SistemaMudanca) -> str:
    return f"M-{m.numero}"


def sha_do_diff(diff: str) -> str:
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def _limpo(valor: Any, campo: str, maximo: int, *, obrigatorio: bool = True) -> str | None:
    texto = str(valor or "").strip()
    if not texto:
        if obrigatorio:
            raise MudancaError("campo_vazio", f"falta {campo}")
        return None
    if len(texto) > maximo:
        raise MudancaError("campo_longo", f"{campo} passa de {maximo} letras")
    return texto


def _corta(valor: Any, maximo: int) -> str | None:
    texto = str(valor or "")
    if not texto.strip():
        return None
    if len(texto) > maximo:
        return texto[: maximo - 40] + "\n… (cortado: passou do tamanho máximo)"
    return texto


# ── quem pode ─────────────────────────────────────────────────────────────


async def aprovadores(session: AsyncSession) -> list[User]:
    """Os aprovadores ativos (quem recebe o aviso de "pronta")."""
    rows = await session.execute(
        select(User)
        .join(SistemaMudancaAprovador, SistemaMudancaAprovador.user_id == User.id)
        .where(User.status == UserStatus.ACTIVE, User.disabled_at.is_(None))
        .order_by(User.name)
    )
    return list(rows.scalars().all())


async def pode_aprovar(session: AsyncSession, user: User | None) -> bool:
    if user is None or user.status != UserStatus.ACTIVE or user.disabled_at is not None:
        return False
    return await session.get(SistemaMudancaAprovador, user.id) is not None


def pode_pedir(user: User) -> bool:
    return user.role == UserRole.ADMIN and user.status == UserStatus.ACTIVE


# ── avisos ────────────────────────────────────────────────────────────────


async def _avisar(
    session: AsyncSession,
    user_ids: Iterable[UUID | None],
    *,
    titulo: str,
    mensagem: str | None,
    m: SistemaMudanca,
    evento: str,
) -> None:
    """Pop-up no sino + Telegram para cada pessoa. Falha no aviso nunca
    desfaz a mudança de situação (savepoint por pessoa)."""
    vistos: set[UUID] = set()
    for uid in user_ids:
        if uid is None or uid in vistos:
            continue
        vistos.add(uid)
        try:
            async with session.begin_nested():
                await emit_alert(
                    session,
                    user_id=uid,
                    type=AlertType.GENERIC,
                    title=titulo,
                    severity=AlertSeverity.INFO,
                    message=mensagem,
                    payload={
                        "mudanca_id": str(m.id),
                        "codigo": codigo(m),
                        "evento": evento,
                        "link": f"/mudancas?id={m.id}",
                    },
                    dedupe_key=f"mudanca:{m.id}:{evento}:{m.tentativas}",
                    notify_telegram=True,
                )
        except Exception:  # noqa: BLE001
            logger.warning("mudanca_aviso_falhou", mudanca=codigo(m), evento=evento)


async def _avisar_aprovadores(session: AsyncSession, m: SistemaMudanca, **kw: Any) -> None:
    await _avisar(session, [u.id for u in await aprovadores(session)], m=m, **kw)


# ── pedido (conector) ─────────────────────────────────────────────────────


async def criar_pedido(
    session: AsyncSession, *, dono: User, titulo: Any, descricao: Any, onde: Any = None
) -> SistemaMudanca:
    if not pode_pedir(dono):
        raise MudancaError("so_admin", "pedir mudança no sistema é só para administradores")
    m = SistemaMudanca(
        pedido_por=dono.id,
        titulo=_limpo(titulo, "o título", MAX_TITULO),
        descricao=_limpo(descricao, "a descrição", MAX_DESCRICAO),
        onde=_limpo(onde, "o onde", MAX_ONDE, obrigatorio=False),
        status="pedida",
    )
    session.add(m)
    await session.flush()
    await session.refresh(m)
    logger.info("mudanca_pedida", mudanca=codigo(m), por=str(dono.id))
    return m


async def listar(
    session: AsyncSession, *, status: str | None = None, limite: int = 100
) -> list[SistemaMudanca]:
    q = select(SistemaMudanca)
    if status == "abertas":
        q = q.where(SistemaMudanca.status.in_(ABERTAS))
    elif status:
        if status not in STATUS_TEXTO:
            raise MudancaError("status_invalido", f"situação desconhecida: {status}")
        q = q.where(SistemaMudanca.status == status)
    q = q.order_by(SistemaMudanca.atualizado_em.desc()).limit(max(1, min(int(limite), 500)))
    return list((await session.execute(q)).scalars().all())


async def por_codigo(session: AsyncSession, texto: str) -> SistemaMudanca | None:
    """"M-12", "m12" ou "12" → o pedido."""
    digitos = "".join(ch for ch in str(texto or "") if ch.isdigit())
    if not digitos:
        return None
    return (
        await session.execute(select(SistemaMudanca).where(SistemaMudanca.numero == int(digitos)))
    ).scalar_one_or_none()


async def nomes(session: AsyncSession, ids: Iterable[UUID | None]) -> dict[UUID, str]:
    lista = sorted({i for i in ids if i is not None}, key=str)
    if not lista:
        return {}
    rows = await session.execute(select(User.id, User.name, User.email).where(User.id.in_(lista)))
    return {r.id: (r.name or r.email or "?") for r in rows.all()}


def resumo_dict(m: SistemaMudanca, nomes_: dict[UUID, str]) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "codigo": codigo(m),
        "titulo": m.titulo,
        "status": m.status,
        "status_texto": STATUS_TEXTO.get(m.status, m.status),
        "pedido_por_nome": nomes_.get(m.pedido_por) if m.pedido_por else None,
        "criado_em": m.criado_em.isoformat() if m.criado_em else None,
        "atualizado_em": m.atualizado_em.isoformat() if m.atualizado_em else None,
    }


def detalhe_dict(
    m: SistemaMudanca, nomes_: dict[UUID, str], *, pode_aprovar_: bool
) -> dict[str, Any]:
    def _nome(uid: UUID | None) -> str | None:
        return nomes_.get(uid) if uid else None

    def _iso(v: datetime | None) -> str | None:
        return v.isoformat() if v else None

    return {
        **resumo_dict(m, nomes_),
        "descricao": m.descricao,
        "onde": m.onde,
        "resumo": m.resumo,
        "testes": m.testes,
        "diff": m.diff,
        "diff_sha": m.diff_sha,
        "arquivos": m.arquivos or [],
        "branch": m.branch,
        "commit": m.commit,
        "aprovado_por_nome": _nome(m.aprovado_por),
        "aprovado_em": _iso(m.aprovado_em),
        "recusado_por_nome": _nome(m.recusado_por),
        "recusado_em": _iso(m.recusado_em),
        "motivo_recusa": m.motivo_recusa,
        "publicado_em": _iso(m.publicado_em),
        "erro": m.erro,
        "pode_aprovar": pode_aprovar_ and m.status == "pronta",
    }


# ── aprovar / recusar (tela) ──────────────────────────────────────────────


async def aprovar(
    session: AsyncSession, *, m: SistemaMudanca, user: User, diff_sha: Any
) -> SistemaMudanca:
    if m.status != "pronta":
        raise MudancaError(
            "nao_esta_pronta", f"{codigo(m)} está em '{STATUS_TEXTO.get(m.status, m.status)}'"
        )
    if not m.diff_sha or str(diff_sha or "") != m.diff_sha:
        raise MudancaError(
            "diff_mudou", "a mudança foi refeita depois que a tela abriu — abra de novo"
        )
    agora = _agora()
    m.status = "aprovada"
    m.aprovado_por = user.id
    m.aprovado_em = agora
    m.erro = None
    m.atualizado_em = agora
    await session.flush()
    logger.info("mudanca_aprovada", mudanca=codigo(m), por=str(user.id))
    await _avisar(
        session,
        [m.pedido_por],
        titulo=f"👍 {codigo(m)} aprovada — vai para o ar",
        mensagem=m.titulo,
        m=m,
        evento="aprovada",
    )
    return m


async def recusar(
    session: AsyncSession, *, m: SistemaMudanca, user: User, motivo: Any
) -> SistemaMudanca:
    if m.status != "pronta":
        raise MudancaError(
            "nao_esta_pronta", f"{codigo(m)} está em '{STATUS_TEXTO.get(m.status, m.status)}'"
        )
    texto = str(motivo or "").strip()
    if len(texto) < MIN_MOTIVO or len(texto) > MAX_MOTIVO:
        raise MudancaError(
            "motivo_invalido", f"o motivo precisa ter de {MIN_MOTIVO} a {MAX_MOTIVO} letras"
        )
    agora = _agora()
    m.status = "recusada"
    m.recusado_por = user.id
    m.recusado_em = agora
    m.motivo_recusa = texto
    m.atualizado_em = agora
    await session.flush()
    logger.info("mudanca_recusada", mudanca=codigo(m), por=str(user.id))
    await _avisar(
        session,
        [m.pedido_por],
        titulo=f"✋ {codigo(m)} recusada",
        mensagem=f"{m.titulo} — motivo: {texto}",
        m=m,
        evento="recusada",
    )
    return m


# ── o robô ────────────────────────────────────────────────────────────────


async def _soltar_presos(session: AsyncSession, agora: datetime) -> None:
    """O robô parou no meio (Mac reiniciou, processo morto): passou do prazo."""
    presos = (
        (
            await session.execute(
                select(SistemaMudanca)
                .where(
                    SistemaMudanca.status.in_(DO_ROBO),
                    SistemaMudanca.prazo_robo.is_not(None),
                    SistemaMudanca.prazo_robo < agora,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for m in presos:
        m.robo = None
        m.prazo_robo = None
        m.atualizado_em = agora
        if m.status == "fazendo" and m.tentativas < MAX_TENTATIVAS:
            m.status = "pedida"
            logger.warning("mudanca_robo_parou_volta_fila", mudanca=codigo(m))
            continue
        etapa = "a publicação" if m.status == "publicando" else "a mudança"
        m.status = "falhou"
        m.erro = f"o robô parou no meio de {etapa} e não voltou — uma pessoa precisa conferir"
        logger.warning("mudanca_robo_parou_falhou", mudanca=codigo(m))
        await _avisar_aprovadores(
            session,
            m,
            titulo=f"⚠️ {codigo(m)} falhou",
            mensagem=m.erro,
            evento="falhou",
        )


async def pegar_trabalho(session: AsyncSession, *, robo: str) -> SistemaMudanca | None:
    """O próximo trabalho do robô (publicar o aprovado antes de fazer um novo),
    ou None. Um de cada vez."""
    agora = _agora()
    await _soltar_presos(session, agora)
    ocupado = (
        await session.execute(
            select(SistemaMudanca.id).where(SistemaMudanca.status.in_(DO_ROBO)).limit(1)
        )
    ).first()
    if ocupado is not None:
        return None
    adiada_ate = agora - ESPERA_ADIADA
    for status, novo, prazo, ordem in (
        ("aprovada", "publicando", PRAZO_PUBLICAR, SistemaMudanca.aprovado_em),
        ("pedida", "fazendo", PRAZO_FAZER, SistemaMudanca.criado_em),
    ):
        filtro = SistemaMudanca.status == status
        if status == "aprovada":
            # adiada (o robô devolveu com o porquê em `erro`) espera um pouco
            filtro = filtro & (
                SistemaMudanca.erro.is_(None) | (SistemaMudanca.atualizado_em < adiada_ate)
            )
        m = (
            await session.execute(
                select(SistemaMudanca)
                .where(filtro)
                .order_by(ordem, SistemaMudanca.numero)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if m is None:
            continue
        m.status = novo
        m.robo = robo[:120]
        m.pego_em = agora
        m.prazo_robo = agora + prazo
        if novo == "fazendo":
            m.tentativas = int(m.tentativas or 0) + 1
        m.atualizado_em = agora
        await session.flush()
        logger.info("mudanca_pega", mudanca=codigo(m), etapa=novo, robo=robo)
        return m
    return None


def trabalho_dict(m: SistemaMudanca, nomes_: dict[UUID, str]) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "codigo": codigo(m),
        "acao": "publicar" if m.status == "publicando" else "fazer",
        "titulo": m.titulo,
        "descricao": m.descricao,
        "onde": m.onde,
        "pedido_por_nome": nomes_.get(m.pedido_por) if m.pedido_por else None,
        "tentativa": m.tentativas,
        "branch": m.branch,
        "commit": m.commit,
        "diff_sha": m.diff_sha,
    }


def _do_robo(m: SistemaMudanca, robo: str) -> None:
    if m.status not in DO_ROBO or (m.robo or "") != robo[:120]:
        raise MudancaError(
            "nao_e_seu", f"{codigo(m)} não está com este robô (situação '{m.status}')"
        )


async def renovar(session: AsyncSession, *, m: SistemaMudanca, robo: str) -> SistemaMudanca:
    _do_robo(m, robo)
    agora = _agora()
    m.prazo_robo = agora + (PRAZO_PUBLICAR if m.status == "publicando" else PRAZO_FAZER)
    m.atualizado_em = agora
    await session.flush()
    return m


def _arquivos(valor: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(valor, list):
        return out
    for item in valor[:MAX_ARQUIVOS]:
        if not isinstance(item, dict):
            continue
        caminho = str(item.get("caminho") or "").strip()[:500]
        if not caminho:
            continue
        try:
            mais = max(0, int(item.get("mais") or 0))
            menos = max(0, int(item.get("menos") or 0))
        except (TypeError, ValueError):
            mais = menos = 0
        out.append({"caminho": caminho, "mais": mais, "menos": menos})
    return out


async def registrar_resultado(
    session: AsyncSession, *, m: SistemaMudanca, robo: str, dados: dict[str, Any]
) -> SistemaMudanca:
    _do_robo(m, robo)
    novo = str(dados.get("status") or "")
    agora = _agora()
    if m.status == "fazendo":
        permitidos = ("pronta", "precisa_equipe", "falhou")
    else:  # publicando
        permitidos = ("publicada", "falhou", "aprovada")
    if novo not in permitidos:
        raise MudancaError(
            "status_invalido", f"de '{m.status}' o robô só pode ir para {', '.join(permitidos)}"
        )
    if m.status == "fazendo":
        m.resumo = _corta(dados.get("resumo"), MAX_RESUMO)
        m.testes = _corta(dados.get("testes"), MAX_TESTES)
    if novo == "pronta":
        diff = str(dados.get("diff") or "")
        if not diff.strip():
            raise MudancaError("sem_diff", "pronta precisa do diff")
        if len(diff) > MAX_DIFF:
            raise MudancaError("diff_grande", f"o diff passa de {MAX_DIFF} letras")
        commit = str(dados.get("commit") or "").strip()
        branch = str(dados.get("branch") or "").strip()
        if not commit or not branch:
            raise MudancaError("sem_commit", "pronta precisa da branch e do commit")
        m.diff = diff
        m.diff_sha = sha_do_diff(diff)
        m.arquivos = _arquivos(dados.get("arquivos"))
        m.commit = commit[:64]
        m.branch = branch[:500]
        m.erro = None
    elif novo == "publicada":
        m.publicado_em = agora
        commit = str(dados.get("commit") or "").strip()
        if commit:
            m.commit = commit[:64]
        m.erro = None
    elif novo == "aprovada":
        # Não deu para publicar AGORA (outro deploy no servidor, disco): volta
        # para a fila de publicar, com o porquê.
        m.erro = _corta(dados.get("erro"), MAX_ERRO)
    else:  # falhou / precisa_equipe
        m.erro = _corta(dados.get("erro"), MAX_ERRO) or _corta(dados.get("resumo"), MAX_ERRO)
    m.status = novo
    m.robo = None
    m.prazo_robo = None
    m.atualizado_em = agora
    await session.flush()
    logger.info("mudanca_resultado", mudanca=codigo(m), status=novo, robo=robo)

    if novo == "pronta":
        await _avisar_aprovadores(
            session,
            m,
            titulo=f"🛠️ {codigo(m)} pronta para aprovar",
            mensagem=m.titulo,
            evento="pronta",
        )
    elif novo == "publicada":
        await _avisar(
            session,
            [m.pedido_por, m.aprovado_por],
            titulo=f"✅ {codigo(m)} no ar",
            mensagem=m.titulo,
            m=m,
            evento="publicada",
        )
    elif novo in ("falhou", "precisa_equipe"):
        texto = "precisa da equipe" if novo == "precisa_equipe" else "falhou"
        aprov = [u.id for u in await aprovadores(session)]
        await _avisar(
            session,
            [*aprov, m.pedido_por],
            titulo=f"⚠️ {codigo(m)} {texto}",
            mensagem=f"{m.titulo} — {m.erro or ''}".strip(" —"),
            m=m,
            evento=novo,
        )
    return m


# ── o texto que o Claude do chefe lê ──────────────────────────────────────


def _quando(v: datetime | None) -> str:
    if v is None:
        return ""
    return v.astimezone(BRT).strftime("%d/%m %H:%M")


def linha_para_o_claude(m: SistemaMudanca, nomes_: dict[UUID, str]) -> str:
    partes = [f"{codigo(m)} — {m.titulo} — {STATUS_TEXTO.get(m.status, m.status)}"]
    if m.pedido_por and m.pedido_por in nomes_:
        partes.append(f"pedido por {nomes_[m.pedido_por]} em {_quando(m.criado_em)}")
    if m.status == "pronta" and m.resumo:
        partes.append(f"o que o robô fez: {m.resumo[:400]}")
    if m.status == "recusada":
        quem = nomes_.get(m.recusado_por) if m.recusado_por else None
        partes.append(f"recusada por {quem or '?'}: {m.motivo_recusa or ''}")
    if m.status == "publicada":
        partes.append(f"no ar desde {_quando(m.publicado_em)}")
    if m.status in ("falhou", "precisa_equipe") and (m.erro or m.resumo):
        partes.append(f"motivo: {(m.erro or m.resumo or '')[:400]}")
    return " — ".join(partes)
