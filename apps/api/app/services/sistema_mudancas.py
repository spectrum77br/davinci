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
Aprovação automática (0509, Eduardo 24/11/2026: "todas as tarefas que o harry
jogar em mudança é para fazer, está automaticamente aprovado"): o pedido de
quem está em `sistema_mudanca_auto_aprovacao` vai de `pronta` direto para
`aprovada` em nome de quem autorizou, enquanto essa pessoa continuar
aprovadora ativa, e só para pedido que chegou pelo conector do Claude que a
própria pessoa (ou um aprovador) gerou. Pedido que mexe na própria trava, em
login/permissão ou no Histórico (`arquivos_sensiveis`) espera o clique mesmo
assim. Os aprovadores recebem o aviso com o link (dá para "Cancelar
aprovação" até o robô começar a publicar).
Quem pede: admin ativo, pelo conector (`claude_tarefas.pedir_mudanca`).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ClaudeConector,
    SistemaMudanca,
    SistemaMudancaAprovador,
    SistemaMudancaAutoAprovacao,
    SistemaMudancaRobo,
    User,
    UserRole,
    UserStatus,
)
from app.models.alert import ESCOPO_ADM, ESCOPO_PESSOAL
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
    "respondida": "Respondida",
}
ABERTAS = ("pedida", "fazendo", "pronta", "aprovada", "publicando")
DO_ROBO = ("fazendo", "publicando")

# Prazo do robô por etapa (renovado pelo `vivo` enquanto ele trabalha).
PRAZO_FAZER = timedelta(minutes=45)
PRAZO_PUBLICAR = timedelta(minutes=40)
# Quantas vezes um pedido pode ser pego para FAZER (o robô parou no meio).
MAX_TENTATIVAS = 3
# Teto absoluto de um trabalho com o robô (retomadas incluídas): passou, falha.
TETO_TRABALHO = timedelta(hours=3)
# Sem sinal do robô há mais que isto com pedido esperando = "robô parado".
ROBO_SUMIDO = timedelta(minutes=10)
# Publicação adiada pelo robô (outro deploy rodando, disco do servidor): só
# volta a ser entregue depois disto — o robô roda a cada minuto.
ESPERA_ADIADA = timedelta(minutes=10)
# Adiada tantas vezes (~1 h): avisa os aprovadores e quem pediu (uma vez).
AVISO_ADIAMENTOS = 6
# Erro do ambiente (GitHub, login do Claude, uv) não gasta tentativa; mas o
# pedido não fica na fila para sempre: passou disto desde o pedido, falha.
TETO_FILA_AMBIENTE = timedelta(hours=12)
# O robô só manda commit de verdade e o ramo do próprio pedido.
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
# Trava do "pegar": dois pedidos de trabalho ao mesmo tempo nunca furam o
# "um de cada vez".
_TRAVA_PEGAR = 0x4D554441  # "MUDA"

MAX_TITULO = 200
MAX_DESCRICAO = 4000
MAX_ONDE = 500
MIN_MOTIVO = 3
MAX_MOTIVO = 1000
MAX_RESUMO = 8000
MAX_TESTES = 60_000
MAX_DIFF = 400_000
MAX_ERRO = 4000
MAX_PERGUNTA = 4000
MAX_RESPOSTA = 60_000
MAX_ARQUIVOS = 200


class MudancaError(Exception):
    """Pedido que a regra não deixa fazer (`code` vai para a tela/robô)."""

    def __init__(self, code: str, detalhe: str = "") -> None:
        super().__init__(detalhe or code)
        self.code = code
        self.detalhe = detalhe or code


def invisiveis(diff: str) -> list[str]:
    """Caracteres invisíveis/de controle nas linhas acrescentadas do diff
    (U+202E e cia. fazem a tela mostrar um código diferente do que roda —
    "Trojan Source"). Devolve os códigos achados, ex.: ["U+202E"]."""
    achados: set[str] = set()
    for linha in diff.splitlines():
        if not linha.startswith("+") or linha.startswith("+++"):
            continue
        for ch in linha[1:]:
            if ch == "\t":
                continue
            cat = unicodedata.category(ch)
            if cat in ("Cf", "Cc", "Co", "Cs", "Zl", "Zp") or (cat == "Zs" and ch != " "):
                achados.add(f"U+{ord(ch):04X}")
    return sorted(achados)


def _agora() -> datetime:
    return datetime.now(UTC)


def codigo(m: SistemaMudanca) -> str:
    """M-12 (pedido de mudança) ou P-12 (pergunta) — o número é o mesmo contador."""
    return f"{'P' if m.tipo == 'pergunta' else 'M'}-{m.numero}"


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


async def autorizado_automatico(
    session: AsyncSession, *, pedido_por: UUID | None, conector_id: UUID | None
) -> UUID | None:
    """Quem deixou os pedidos desta pessoa aprovados sozinhos (0509), se a
    regra vale para ESTE pedido: quem pede continua admin ativo, quem
    autorizou continua aprovador ativo e o pedido chegou por um conector do
    Claude da própria pessoa, não revogado, gerado por ela mesma ou por um
    aprovador — qualquer admin gera link em nome de outro (POST
    /api/claude-conector) e, sem essa trava, pediria em nome de quem tem a
    regra. Senão None (o pedido espera o Aprovar como sempre)."""
    if pedido_por is None or conector_id is None:
        return None
    regra = await session.get(SistemaMudancaAutoAprovacao, pedido_por)
    if regra is None or regra.autorizado_por is None:
        return None
    quem_pede = await session.get(User, pedido_por)
    if quem_pede is None or quem_pede.disabled_at is not None or not pode_pedir(quem_pede):
        return None
    quem_autorizou = await session.get(User, regra.autorizado_por)
    if not await pode_aprovar(session, quem_autorizou):
        return None
    conector = await session.get(ClaudeConector, conector_id)
    if conector is None or conector.revoked_at is not None or conector.user_id != pedido_por:
        return None
    if conector.created_by != pedido_por:
        criador = await session.get(User, conector.created_by) if conector.created_by else None
        if not await pode_aprovar(session, criador):
            return None
    return regra.autorizado_por


# Arquivos que decidem quem aprova, quem entra, quem vê o quê e o que fica
# registrado: pedido que mexe neles nunca é aprovado sozinho, nem para quem
# tem a regra — senão um pedido aprovado sozinho poderia afrouxar a própria
# trava ou o login (revisões de 24/11/2026: ex. tirar o `_dev_guard()` do
# /api/dev/mock-login viraria sessão ADMIN para qualquer um). Vale o caminho
# tirado do DIFF (o que vai para o ar), não a lista `arquivos` que o robô
# manda. Nome de arquivo também pega a pasta de mesmo nome.
_SENSIVEIS = re.compile(
    r"^apps/(?:api/(?:alembic/|app/(?:"
    # a própria trava (Sistema › Mudanças) e o conector do Claude
    r"(?:services|routers)/sistema_mudancas[^/]*|models/sistema_mudanca"
    r"|services/claude_tarefas|(?:routers|models)/claude_conector"
    # quem entra: login, sessão, usuários, papéis, ERP, limites, robô de verificação
    r"|routers/(?:auth|oauth|users|dev|erp_gate)|models/(?:user|auth_code|enums|__init__)"
    r"|services/(?:erp_acesso|bootstrap|rate_limit|turnstile)"
    r"|services/(?:atendimento|app_uranyx)/acesso|deps|security"
    # o que fica registrado (Histórico) e a montagem do app
    r"|routers/historico|models/historico|historico|config|main|db|__init__"
    r")(?:\.py$|/))"
    r"|web/(?:pages/(?:mudancas|login|historico)\.vue$|middleware/|stores/auth\.ts$))"
)
# Linha mexida (acrescentada OU tirada) no código do servidor que fala de
# permissão, sessão, papel, Histórico ou da própria trava: também espera o
# clique — a lista de arquivos nunca fica completa (qualquer rota pode ganhar
# um `require_admin` a menos).
_PALAVRAS_SENSIVEIS = re.compile(
    r"require_(?:admin|owner|permission|user|active_user)|get_current_user|user_scope"
    r"|issue_session_token|_dev_guard|set_cookie|delete_cookie"
    r"|app\.(?:security|deps|historico)|HistoricoAcesso|historico_acesso|\bidentificar\("
    r"|DISABLE TRIGGER|session_replication_role"
    r"|UserRole\.|UserStatus\.|\.permissions\b|\.role\b"
    r"|sistema_mudanca|SistemaMudanca|autorizado_automatico|arquivos_sensiveis|pode_aprovar"
    r"|erp_acesso|LIBERADOS_POR_ID"
)
_CODIGO_SERVIDOR = re.compile(r"^apps/api/app/")
_DIFF_GIT = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
_DIFF_CAMINHO = re.compile(
    r"^(?:--- a/|\+\+\+ b/|rename from |rename to |copy from |copy to )(.+)$"
)


def arquivos_sensiveis(diff: str) -> list[str]:
    """O que no diff impede a aprovação automática: caminho sensível, ou
    "caminho (palavra)" quando uma linha mexida do código do servidor fala de
    permissão/sessão/Histórico/trava. Lido das linhas do próprio diff. Linha
    `diff --git` que não dá para ler (caminho com espaço ou entre aspas) conta
    como sensível: na dúvida, espera o clique."""
    achados: list[str] = []
    pelo_caminho: set[str] = set()
    pela_palavra: set[str] = set()
    atuais: tuple[str, ...] = ()

    def _marca(texto: str) -> None:
        if texto not in achados:
            achados.append(texto)

    for linha in (diff or "").splitlines():
        if linha.startswith("diff --git "):
            casou = _DIFF_GIT.match(linha)
            if casou is None:
                atuais = ()
                _marca(linha[len("diff --git ") :])
                continue
            atuais = casou.groups()
            caminhos: tuple[str, ...] = atuais
        elif linha.startswith(("--- ", "+++ ", "rename ", "copy ")):
            casou = _DIFF_CAMINHO.match(linha)
            if casou is None:
                continue
            caminhos = (casou.group(1),)
            atuais = (*atuais, casou.group(1))
        else:
            if linha[:1] in ("+", "-"):
                achou = _PALAVRAS_SENSIVEIS.search(linha[1:])
                if achou:
                    for c in atuais:
                        if (
                            _CODIGO_SERVIDOR.match(c)
                            and c not in pelo_caminho
                            and c not in pela_palavra
                        ):
                            pela_palavra.add(c)
                            _marca(f"{c} ({achou.group(0)})")
            continue
        for c in caminhos:
            if _SENSIVEIS.match(c) and c not in pelo_caminho:
                pelo_caminho.add(c)
                _marca(c)
    return achados


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
    """Pop-up no sino para cada pessoa (M-20: sem Telegram). Falha no aviso nunca
    desfaz a mudança de situação (savepoint por pessoa). Para quem aprova, o
    texto diz onde abrir (e o link vai junto); para quem pediu, não — a tela
    não existe para ele (acompanha pelo Claude: listar_mudancas)."""
    aprovadores_ids = {u.id for u in await aprovadores(session)}
    # M-19 (23/11/2026): aviso de mudança é ADM, dirigido a cada pessoa (o
    # texto é dela). Quem não é administrador não vê ADM: recebe como pessoal.
    ids = [uid for uid in user_ids if uid is not None]
    admins = set(
        (
            await session.execute(
                select(User.id).where(User.id.in_(ids), User.role == UserRole.ADMIN)
            )
        )
        .scalars()
        .all()
    )
    vistos: set[UUID] = set()
    for uid in ids:
        if uid in vistos:
            continue
        vistos.add(uid)
        aprova = uid in aprovadores_ids
        texto = mensagem or ""
        if aprova:
            texto = f"{texto} — abra Sistema › Mudanças".strip(" —")
        else:
            texto = f"{texto} — pergunte ao Claude: como está a {codigo(m)}?".strip(" —")
        payload: dict[str, Any] = {"mudanca_id": str(m.id), "codigo": codigo(m), "evento": evento}
        if aprova:
            payload["link"] = f"/mudancas?id={m.id}"
        try:
            async with session.begin_nested():
                await emit_alert(
                    session,
                    user_id=uid,
                    type=AlertType.GENERIC,
                    title=titulo,
                    severity=AlertSeverity.INFO,
                    message=texto,
                    payload=payload,
                    dedupe_key=f"mudanca:{m.id}:{evento}:{m.tentativas}:{m.adiamentos}",
                    escopo=ESCOPO_ADM if uid in admins else ESCOPO_PESSOAL,
                    dirigido=True,
                )
        except Exception:  # noqa: BLE001
            logger.warning("mudanca_aviso_falhou", mudanca=codigo(m), evento=evento)


async def _avisar_aprovadores(session: AsyncSession, m: SistemaMudanca, **kw: Any) -> None:
    await _avisar(session, [u.id for u in await aprovadores(session)], m=m, **kw)


# ── pedido (conector) ─────────────────────────────────────────────────────


async def criar_pedido(
    session: AsyncSession,
    *,
    dono: User,
    titulo: Any,
    descricao: Any,
    onde: Any = None,
    conector_id: UUID | None = None,
) -> SistemaMudanca:
    if not pode_pedir(dono):
        raise MudancaError("so_admin", "pedir mudança no sistema é só para administradores")
    m = SistemaMudanca(
        pedido_por=dono.id,
        conector_id=conector_id,
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


async def criar_pergunta(
    session: AsyncSession, *, dono: User, pergunta: Any, onde: Any = None
) -> SistemaMudanca:
    """Pergunta sobre como o sistema funciona (Eduardo, 17/11: a resposta e o
    código podem ir direto para quem perguntou). O robô só LÊ o código."""
    if not pode_pedir(dono):
        raise MudancaError("so_admin", "perguntar sobre o sistema é só para administradores")
    texto = _limpo(pergunta, "a pergunta", MAX_PERGUNTA)
    titulo = " ".join(texto.split())
    if len(titulo) > MAX_TITULO:
        titulo = titulo[: MAX_TITULO - 1].rstrip() + "…"
    m = SistemaMudanca(
        pedido_por=dono.id,
        tipo="pergunta",
        titulo=titulo,
        descricao=texto,
        onde=_limpo(onde, "o onde", MAX_ONDE, obrigatorio=False),
        status="pedida",
    )
    session.add(m)
    await session.flush()
    await session.refresh(m)
    logger.info("pergunta_sistema", pergunta=codigo(m), por=str(dono.id))
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
        "tipo": m.tipo,
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

    aprovado_por_nome = _nome(m.aprovado_por)
    if m.aprovado_automatico:
        aprovado_por_nome = f"{aprovado_por_nome or '?'} (automático)"

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
        "aprovado_por_nome": aprovado_por_nome,
        "aprovado_automatico": bool(m.aprovado_automatico),
        "aprovado_em": _iso(m.aprovado_em),
        "recusado_por_nome": _nome(m.recusado_por),
        "recusado_em": _iso(m.recusado_em),
        "motivo_recusa": m.motivo_recusa,
        "publicado_em": _iso(m.publicado_em),
        "erro": m.erro,
        "resposta": m.resposta,
        "pode_aprovar": pode_aprovar_ and m.status == "pronta",
        "pode_cancelar": pode_aprovar_ and m.status == "aprovada",
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
    m.aprovado_automatico = False
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


async def cancelar(
    session: AsyncSession, *, m: SistemaMudanca, user: User, motivo: Any
) -> SistemaMudanca:
    """Desfaz uma aprovação que ainda não foi ao ar (a publicação ficou parada
    e o pedido não vale mais). Só antes de o robô começar a publicar."""
    if m.status != "aprovada":
        raise MudancaError(
            "nao_esta_aprovada",
            f"{codigo(m)} está em '{STATUS_TEXTO.get(m.status, m.status)}' — só dá para cancelar "
            "a aprovação enquanto a publicação espera",
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
    m.motivo_recusa = f"Aprovação cancelada: {texto}"
    m.atualizado_em = agora
    await session.flush()
    logger.info("mudanca_aprovacao_cancelada", mudanca=codigo(m), por=str(user.id))
    await _avisar(
        session,
        [m.pedido_por, *(u.id for u in await aprovadores(session) if u.id != user.id)],
        titulo=f"✋ {codigo(m)}: aprovação cancelada",
        mensagem=f"{m.titulo} — motivo: {texto}",
        m=m,
        evento="cancelada",
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
        await _avisar(
            session,
            [*(u.id for u in await aprovadores(session)), m.pedido_por],
            titulo=f"⚠️ {codigo(m)} falhou",
            mensagem=m.erro,
            m=m,
            evento="falhou",
        )


async def _presenca(session: AsyncSession, robo: str, agora: datetime) -> None:
    linha = await session.get(SistemaMudancaRobo, robo[:120])
    if linha is None:
        session.add(SistemaMudancaRobo(robo=robo[:120], visto_em=agora))
    else:
        linha.visto_em = agora


async def robo_visto_em(session: AsyncSession) -> datetime | None:
    """A última vez que algum robô de mudanças deu sinal (pegar ou vivo)."""
    return (
        await session.execute(
            select(SistemaMudancaRobo.visto_em)
            .order_by(SistemaMudancaRobo.visto_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def robo_parado(session: AsyncSession) -> bool:
    """Há pedido esperando o robô (fila ou com ele) e ele sumiu."""
    esperando = (
        await session.execute(
            select(SistemaMudanca.id)
            .where(SistemaMudanca.status.in_(("pedida", "aprovada", *DO_ROBO)))
            .limit(1)
        )
    ).first()
    if esperando is None:
        return False
    visto = await robo_visto_em(session)
    return visto is None or visto < _agora() - ROBO_SUMIDO


async def pegar_trabalho(session: AsyncSession, *, robo: str) -> SistemaMudanca | None:
    """O próximo trabalho do robô (publicar o aprovado antes de fazer um novo),
    ou None. Um de cada vez (trava da transação: dois "pegar" ao mesmo tempo
    esperam um pelo outro). O trabalho que JÁ está com este robô volta para
    ele (o Mac reiniciou no meio, a resposta se perdeu): retoma."""
    agora = _agora()
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _TRAVA_PEGAR})
    await _presenca(session, robo[:120], agora)
    await _soltar_presos(session, agora)
    em_andamento = (
        (
            await session.execute(
                select(SistemaMudanca).where(SistemaMudanca.status.in_(DO_ROBO)).limit(1)
            )
        )
        .scalars()
        .first()
    )
    if em_andamento is not None:
        if (em_andamento.robo or "") != robo[:120]:
            return None
        # Retomar conta como tentativa (o robô que cai sempre no mesmo ponto
        # não fica em laço) e tem teto absoluto.
        if em_andamento.status == "fazendo":
            em_andamento.tentativas = int(em_andamento.tentativas or 0) + 1
        estourou = (
            em_andamento.pego_em is not None
            and em_andamento.pego_em < agora - TETO_TRABALHO
        )
        if estourou or int(em_andamento.tentativas or 0) > MAX_TENTATIVAS:
            em_andamento.status = "falhou"
            em_andamento.robo = None
            em_andamento.prazo_robo = None
            em_andamento.erro = (
                "o robô recomeçou este pedido vezes demais — uma pessoa precisa conferir"
            )
            em_andamento.atualizado_em = agora
            await _avisar(
                session,
                [*(u.id for u in await aprovadores(session)), em_andamento.pedido_por],
                titulo=f"⚠️ {codigo(em_andamento)} falhou",
                mensagem=em_andamento.erro,
                m=em_andamento,
                evento="falhou",
            )
            await session.flush()
            return None
        em_andamento.prazo_robo = agora + (
            PRAZO_PUBLICAR if em_andamento.status == "publicando" else PRAZO_FAZER
        )
        em_andamento.atualizado_em = agora
        await session.flush()
        logger.info("mudanca_retomada", mudanca=codigo(em_andamento), robo=robo)
        return em_andamento
    adiada_ate = agora - ESPERA_ADIADA
    # Na fila, a PERGUNTA vem antes do pedido de mudança: ela leva ~1 min e o
    # conector promete "alguns minutos" (no Postgres, false vem antes de true).
    for status, novo, prazo, ordem in (
        ("aprovada", "publicando", PRAZO_PUBLICAR, (SistemaMudanca.aprovado_em,)),
        (
            "pedida",
            "fazendo",
            PRAZO_FAZER,
            (SistemaMudanca.tipo != "pergunta", SistemaMudanca.criado_em),
        ),
    ):
        filtro = SistemaMudanca.status == status
        if status in ("aprovada", "pedida"):
            # adiada/devolvida (o robô voltou com o porquê em `erro`) espera um pouco
            filtro = filtro & (
                SistemaMudanca.erro.is_(None) | (SistemaMudanca.atualizado_em < adiada_ate)
            )
        m = (
            await session.execute(
                select(SistemaMudanca)
                .where(filtro)
                .order_by(*ordem, SistemaMudanca.numero)
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
        "acao": (
            "publicar"
            if m.status == "publicando"
            else ("responder" if m.tipo == "pergunta" else "fazer")
        ),
        "tipo": m.tipo,
        "titulo": m.titulo,
        "descricao": m.descricao,
        "onde": m.onde,
        "pedido_por_nome": nomes_.get(m.pedido_por) if m.pedido_por else None,
        "tentativa": m.tentativas,
        "branch": m.branch,
        "commit": m.commit,
        "diff_sha": m.diff_sha,
        "adiamentos": m.adiamentos,
    }


def _do_robo(m: SistemaMudanca, robo: str) -> None:
    if m.status not in DO_ROBO or (m.robo or "") != robo[:120]:
        raise MudancaError(
            "nao_e_seu", f"{codigo(m)} não está com este robô (situação '{m.status}')"
        )


async def renovar(session: AsyncSession, *, m: SistemaMudanca, robo: str) -> SistemaMudanca:
    _do_robo(m, robo)
    agora = _agora()
    await _presenca(session, robo, agora)
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
    automatica = False
    segurada: list[str] = []  # tinha a regra, mas mexe em arquivo sensível
    if m.status == "fazendo" and m.tipo == "pergunta":
        permitidos = ("respondida", "falhou", "pedida")
    elif m.status == "fazendo":
        permitidos = ("pronta", "precisa_equipe", "falhou", "pedida")
    else:  # publicando
        permitidos = ("publicada", "falhou", "aprovada")
    if novo not in permitidos:
        raise MudancaError(
            "status_invalido", f"de '{m.status}' o robô só pode ir para {', '.join(permitidos)}"
        )
    if m.status == "fazendo" and m.tipo != "pergunta":
        m.resumo = _corta(dados.get("resumo"), MAX_RESUMO)
        m.testes = _corta(dados.get("testes"), MAX_TESTES)
    if novo == "respondida":
        resposta = str(dados.get("resposta") or "").strip()
        if not resposta:
            raise MudancaError("sem_resposta", "respondida precisa da resposta")
        if len(resposta) > MAX_RESPOSTA:
            corte = "\n\n[resposta cortada: ficou longa demais]"
            resposta = resposta[: MAX_RESPOSTA - len(corte)].rstrip() + corte
        m.resposta = resposta
        m.erro = None
    elif novo == "pronta":
        diff = str(dados.get("diff") or "")
        if not diff.strip():
            raise MudancaError("sem_diff", "pronta precisa do diff")
        if len(diff) > MAX_DIFF:
            raise MudancaError("diff_grande", f"o diff passa de {MAX_DIFF} letras")
        escondidos = invisiveis(diff)
        if escondidos:
            raise MudancaError(
                "diff_invisivel",
                "o diff tem caracteres invisíveis/de controle: " + ", ".join(escondidos[:10]),
            )
        commit = str(dados.get("commit") or "").strip()
        branch = str(dados.get("branch") or "").strip()
        if not _COMMIT.match(commit) or branch != f"mudanca/{codigo(m)}":
            raise MudancaError(
                "sem_commit",
                f"pronta precisa do commit (40 letras hex) e do ramo mudanca/{codigo(m)}",
            )
        m.diff = diff
        m.diff_sha = sha_do_diff(diff)
        m.arquivos = _arquivos(dados.get("arquivos"))
        m.commit = commit[:64]
        m.branch = branch[:500]
        m.erro = None
        if m.tipo == "mudanca":
            autorizou = await autorizado_automatico(
                session, pedido_por=m.pedido_por, conector_id=m.conector_id
            )
            if autorizou is not None:
                segurada = arquivos_sensiveis(diff)
            if autorizou is not None and not segurada:
                # 0509: ninguém precisa clicar — vai direto para a fila de publicar.
                novo = "aprovada"
                automatica = True
                m.aprovado_por = autorizou
                m.aprovado_em = agora
                m.aprovado_automatico = True
    elif novo == "publicada":
        m.publicado_em = agora
        commit = str(dados.get("commit") or "").strip()
        if _COMMIT.match(commit):
            m.commit = commit
        m.erro = None
    elif novo == "aprovada":
        # Não deu para publicar AGORA (outro deploy no servidor, disco, o main
        # com mudança de outra pessoa): volta para a fila de publicar, com o
        # porquê; o robô só tenta de novo depois de ESPERA_ADIADA.
        m.erro = _corta(dados.get("erro"), MAX_ERRO) or "publicação adiada"
        m.adiamentos = int(m.adiamentos or 0) + 1
    elif novo == "pedida":
        # Erro do ambiente no "fazer" (rede, GitHub, login do Claude): volta
        # para a fila SEM gastar tentativa (uma queda do GitHub não derruba a
        # fila inteira) e espera ESPERA_ADIADA; falha só se o pedido passar de
        # TETO_FILA_AMBIENTE sem o robô conseguir trabalhar.
        m.tentativas = max(0, int(m.tentativas or 0) - 1)
        m.erro = _corta(dados.get("erro"), MAX_ERRO)
        if m.criado_em is not None and m.criado_em < agora - TETO_FILA_AMBIENTE:
            novo = "falhou"
            m.erro = (
                f"o robô não conseguiu trabalhar neste pedido em "
                f"{int(TETO_FILA_AMBIENTE.total_seconds() // 3600)} h: {m.erro or ''}"
            ).strip(": ")
    else:  # falhou / precisa_equipe
        m.erro = _corta(dados.get("erro"), MAX_ERRO) or _corta(dados.get("resumo"), MAX_ERRO)
    m.status = novo
    m.robo = None
    m.prazo_robo = None
    m.atualizado_em = agora
    await session.flush()
    logger.info(
        "mudanca_resultado", mudanca=codigo(m), status=novo, robo=robo, automatica=automatica
    )

    if novo == "aprovada" and m.adiamentos and m.adiamentos % AVISO_ADIAMENTOS == 0:
        await _avisar(
            session,
            [*(u.id for u in await aprovadores(session)), m.pedido_por],
            titulo=f"⏳ {codigo(m)} aprovada, mas a publicação está parada",
            mensagem=f"{m.titulo} — {m.erro or ''}".strip(" —"),
            m=m,
            evento="adiada_muito",
        )
    if automatica:
        await _avisar(
            session,
            [*(u.id for u in await aprovadores(session)), m.pedido_por],
            titulo=f"🤖 {codigo(m)} aprovada automaticamente — vai para o ar",
            mensagem=m.titulo,
            m=m,
            evento="aprovada_auto",
        )
    elif novo == "respondida":
        await _avisar(
            session,
            [m.pedido_por],
            titulo=f"💬 {codigo(m)} respondida",
            mensagem=f"{m.titulo} — peça ao Claude: \"qual a resposta da {codigo(m)}?\"",
            m=m,
            evento="respondida",
        )
    elif novo == "falhou" and m.tipo == "pergunta":
        await _avisar(
            session,
            [m.pedido_por],
            titulo=f"⚠️ {codigo(m)}: o robô não conseguiu responder",
            mensagem=f"{m.titulo} — {m.erro or ''}".strip(" —"),
            m=m,
            evento="falhou",
        )
    elif novo == "pronta":
        motivo = ""
        if segurada:
            motivo = f"não foi aprovada sozinha: mexe em {', '.join(segurada[:3])}"
            if len(segurada) > 3:
                motivo += f" e mais {len(segurada) - 3}"
        await _avisar_aprovadores(
            session,
            m,
            titulo=f"🛠️ {codigo(m)} pronta para aprovar",
            mensagem=f"{m.titulo} — {motivo}".strip(" —"),
            evento="pronta",
        )
        if segurada:
            await _avisar(
                session,
                [m.pedido_por],
                titulo=f"✋ {codigo(m)} espera o clique do Eduardo ou do Thorfinn",
                mensagem=(
                    f"{m.titulo} — mexe em permissão, login, Histórico ou na própria "
                    "aprovação; essas não vão ao ar sozinhas"
                ),
                m=m,
                evento="segurada",
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
    rotulo = f"{codigo(m)} (pergunta)" if m.tipo == "pergunta" else codigo(m)
    partes = [f"{rotulo} — {m.titulo} — {STATUS_TEXTO.get(m.status, m.status)}"]
    if m.pedido_por and m.pedido_por in nomes_:
        partes.append(f"pedido por {nomes_[m.pedido_por]} em {_quando(m.criado_em)}")
    if m.tipo == "pergunta":
        if m.status == "respondida":
            partes.append(f"resposta pronta — use `ver_resposta_do_sistema` com {codigo(m)}")
        elif m.status == "falhou" and m.erro:
            partes.append(f"motivo: {m.erro[:400]}")
        return " — ".join(partes)
    if m.status == "pronta" and m.resumo:
        partes.append(f"o que o robô fez: {m.resumo[:400]}")
    if m.aprovado_automatico and m.status in ("aprovada", "publicando", "publicada"):
        partes.append("aprovada automaticamente")
        if m.resumo:
            partes.append(f"o que o robô fez: {m.resumo[:400]}")
    if m.status == "recusada":
        quem = nomes_.get(m.recusado_por) if m.recusado_por else None
        partes.append(f"recusada por {quem or '?'}: {m.motivo_recusa or ''}")
    if m.status == "publicada":
        partes.append(f"no ar desde {_quando(m.publicado_em)}")
    if m.status == "aprovada" and m.erro:
        partes.append(f"publicação adiada: {m.erro[:300]} (o robô tenta de novo a cada 10 min)")
    if m.status in ("falhou", "precisa_equipe") and (m.erro or m.resumo):
        partes.append(f"motivo: {(m.erro or m.resumo or '')[:400]}")
    return " — ".join(partes)


def resposta_para_o_claude(m: SistemaMudanca, nomes_: dict[UUID, str]) -> str:
    """O texto inteiro que o Claude de quem perguntou recebe."""
    if m.tipo != "pergunta":
        return linha_para_o_claude(m, nomes_)
    if m.status == "respondida" and m.resposta:
        return f"{codigo(m)} — {m.titulo}\n\n{m.resposta}"
    if m.status == "falhou":
        return f"{codigo(m)}: o robô não conseguiu responder. {m.erro or ''}".strip()
    return (
        f"{codigo(m)} ainda não foi respondida ({STATUS_TEXTO.get(m.status, m.status)}). "
        "O robô lê o código e responde — costuma levar alguns minutos."
    )
