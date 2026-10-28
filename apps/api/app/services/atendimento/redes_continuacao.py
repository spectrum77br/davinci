"""A resposta automática da CONTINUAÇÃO nos comentários do Instagram (28/10/2026).

Eduardo: "quando pedirem continuação ele responde no post que postará
continuação / temos os vídeos às 12 e 19 / se foi postado às 12 avisa que às
19 terá a continuação ... só no Instagram". Os Reels de microdrama da
@uranyx_br saem em série ("O Lacre", Ep. 1/6 a 6/6, um por vaga do robô) e
cada um junta dezenas de "Continua"/"Continuação"/"parte 2" sem resposta —
medido em 28/10: 104 pedidos nos Ep. 1–4, a maioria DEPOIS de a parte
seguinte já ter saído.

REGRA FIXA, sem IA. Roda logo depois da leitura de cada conta do Instagram
(cron `atendimento_redes`, a cada 15 min) e só onde o admin ligou na aba Lojas
(`atendimento_canais.auto_continuacao_desde`): o modo do canal (observar)
continua valendo para todo o resto. Responde em PÚBLICO, no próprio
comentário, pelo mesmo `redes.responder` do botão — as travas do envio, a
linha em voo commitada antes da rede, `falhou`/`revisar` —, com a origem
`davinci_auto`.

Quando responde (tudo precisa valer):
  • o comentário é de pessoa, visível, feito DEPOIS de ligar e há menos de
    `JANELA_COMENTARIO`, e o texto é SÓ pedido de continuação
    (`pede_continuacao`: "continua", "continuação", "parte 2", "cadê a
    parte 3?"…; uma palavra fora do vocabulário — "o meu continua
    ligando" — e não é pedido);
  • a publicação é episódio de série: a postagem com aquele id de mídia → o
    criativo → "<série> — Ep. N/M" no título (`episodio`). Post avulso, o
    último episódio ou série sem o N+1 cadastrado: não responde (fica para a
    equipe, como hoje);
  • o episódio seguinte (N+1, mesma série):
      – já saiu NESTA conta → "a parte N+1 já está no perfil";
      – está agendado com hora → essa hora;
      – é o próximo da FILA do robô (`autopostagem.proximo_criativo`, a
        mesma função que ele usa) → a próxima vaga da grade da conta
        (`autopostagem.horarios_do_dia`; o robô pega a vaga no minuto 2 —
        12:00 → "12h", 18:40 → "19h");
      – senão (não aprovado, outro vídeo na frente) → ESPERA: a rodada
        seguinte confere de novo, até o comentário ficar velho;
  • nos minutos em volta de cada vaga (`JANELA_POSTAGEM`) não decide nada:
    o episódio pode estar saindo agora.

Os limites (contra duplicar e contra rajada — o Instagram trata texto
repetido em massa como spam):
  • um comentário, uma resposta: a mensagem é commitada ANTES da rede e a
    decisão confere qualquer resposta da loja depois do comentário (a da
    equipe pelo app também conta);
  • uma por pessoa por publicação (a conversa é pessoa × publicação) e
    `MAX_POR_PESSOA_DIA` por pessoa em 24 h;
  • `MAX_POR_POST_HORA` por publicação, `MAX_POR_CONTA_HORA` por conta e
    `MAX_POR_RODADA` por rodada — o que passar ESPERA a rodada seguinte;
  • a frase varia entre três (escolhida pelo id do comentário);
  • a falha da rede para a conta na rodada (as outras nem tentam) e o
    comentário fica `falhou` — não volta sozinho.

O que foi decidido fica em `atendimento_comentarios.dados["continuacao"]`
({status, motivo, em, parte}); o log leva só ids, contagens e motivos.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoComentario,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPublicacao,
    MarketingCreative,
    MarketingPostagem,
    MarketingRoteiro,
    RedeSocial,
)
from app.models.marketing_postagem import STATUS_EM_VOO, STATUS_PUBLICADO
from app.services.atendimento import canais_externos, redes
from app.services.atendimento.constantes import (
    AUTOR_LOJA,
    CANAL_COMENTARIO,
    MSG_ENVIADA,
    MSG_FALHOU,
    ORIGEM_NOTA,
    PLATAFORMA_INSTAGRAM,
    PUBLICACAO_PROPRIA,
)
from app.services.atendimento.enviar import RECUSA_TEXTO_INVALIDO, EnvioRecusado
from app.services.marketing import autopostagem
from app.services.marketing.postagens import BRT

logger = structlog.get_logger()

# A marca no `dados` do comentário e no `payload` da resposta.
CHAVE = "continuacao"

# Comentário mais velho que isto não recebe mais (a fila do dia, não a história).
JANELA_COMENTARIO = timedelta(hours=24)
MAX_POR_RODADA = 10
MAX_POR_CONTA_HORA = 30
MAX_POR_POST_HORA = 12
MAX_POR_PESSOA_DIA = 3
# O cron do robô de postagem (`worker.marketing_autopostagem`) roda no minuto 2.
MINUTO_DO_ROBO = 2
# Em volta do minuto do robô (antes, depois): o episódio pode estar saindo.
JANELA_POSTAGEM = (timedelta(minutes=5), timedelta(minutes=25))

RESPONDIDO = "respondido"
IGNORADO = "ignorado"
FALHOU = "falhou"

RESPONDER = "responder"
ESPERAR = "esperar"
IGNORAR = "ignorar"

# A recusa do `redes.responder` que é do próprio comentário: não adianta tentar de novo.
_RECUSAS_DO_COMENTARIO = (
    redes.RECUSA_COMENTARIO_OCULTO,
    redes.RECUSA_COMENTARIO_DA_MARCA,
    redes.RECUSA_SEM_CONVERSA,
    RECUSA_TEXTO_INVALIDO,
)

FRASES_VAI_SAIR = (
    "@{u} Tem continuação, sim! A parte {n} sai {quando} aqui no perfil 🎬",
    "@{u} A parte {n} chega {quando} 🔔 Fica de olho no perfil!",
    "@{u} Calma que tem mais 😄 A parte {n} sai {quando}!",
)
FRASES_JA_SAIU = (
    "@{u} A parte {n} já está no nosso perfil! 🎬",
    "@{u} Já saiu! A parte {n} está lá no perfil 😉",
    "@{u} Corre no perfil que a parte {n} já chegou 🔥",
)


# ── O pedido (puro) ────────────────────────────────────────────────────────

MAX_CARACTERES = 120
MAX_PALAVRAS = 15
# A palavra que faz o pedido (sem acento, minúscula).
_CHAVES = frozenset({
    "cont", "prox", "ep", "eps", "cap", "final", "desfecho",
    "segunda", "terceira", "quarta", "quinta", "sexta",
})  # fmt: skip
_CHAVES_PREFIXO = ("contin", "proxim", "episod", "capitul", "sequenc")
_RE_PARTE = re.compile(r"^part(e+s?|s)?$")
_NUMEROS = frozenset(
    {"um", "uma", "dois", "duas", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez"}
)
# O que pode vir junto do pedido sem mudar o sentido. Fora daqui, não é pedido.
_RECHEIO = frozenset({
    "a", "o", "as", "os", "e", "da", "do", "das", "dos", "de", "na", "no", "nas", "nos",
    "em", "pra", "pro", "para", "por", "com", "eu", "me", "mim", "ai", "aqui", "la",
    "favor", "pf", "pfv", "pfvr", "pfvor", "porfavor", "pls", "plis", "plz", "please",
    "cade", "kd", "quero", "queremos", "quer", "ver", "ve", "assistir", "saber",
    "mais", "mt", "muito", "manda", "mande", "mandem", "posta", "poste", "postem",
    "postar", "solta", "solte", "soltem", "sai", "sair", "vai", "vem", "vou",
    "quando", "que", "q", "qual", "quais", "hora", "horas", "horario", "dia", "hoje",
    "amanha", "logo", "ja", "agora", "tem", "ter", "tera", "teria", "hein", "ein",
    "ne", "ue", "sim", "ou", "oq", "oque", "ta", "gente", "pessoal", "galera", "mds",
    "meu", "deus", "socorro", "nossa", "aff", "affs", "pelo", "amor", "ansiosa",
    "ansioso", "curiosa", "curioso", "aguardando", "esperando", "urgente", "bora",
    "outra", "outro", "resto", "onde", "video", "videos", "historia", "serie", "tbm",
    "tb", "tambem", "ainda", "so", "essa", "esse", "dessa", "desse",
})  # fmt: skip
_RE_RISO = re.compile(r"^(k{2,}|(ha){2,}|(he){2,}|(rs)+|a+h+|o+h+)$")


def _palavras(texto: str) -> list[str] | None:
    """Minúsculo, sem acento, número separado da letra ("parte2" → parte 2).

    Com @ (marcou alguém) ou link, None: não é o pedido de quem só quer a
    continuação.
    """
    t = unicodedata.normalize("NFKD", texto.casefold())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    if "@" in t or "http" in t or "www." in t:
        return None
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).split()


def pede_continuacao(texto: str | None) -> bool:
    """O comentário é SÓ o pedido da continuação ("Continua", "parte 2e3", "cadê a parte 3?")."""
    if not texto or len(texto) > MAX_CARACTERES:
        return False
    palavras = _palavras(texto)
    if not palavras or len(palavras) > MAX_PALAVRAS:
        return False
    pedido = False
    for i, p in enumerate(palavras):
        seguinte = palavras[i + 1] if i + 1 < len(palavras) else ""
        if p in _CHAVES or p.startswith(_CHAVES_PREFIXO) or _RE_PARTE.match(p):
            pedido = True
        elif p == "p" and (seguinte.isdigit() or seguinte in _NUMEROS):
            pedido = True  # "p 2", "p2"
        elif not (
            (p.isdigit() and len(p) <= 2) or p in _NUMEROS or p in _RECHEIO or _RE_RISO.match(p)
        ):
            return False
    return pedido


# ── O episódio (puro) ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Episodio:
    serie: str  # como está no título ('Microdrama "O Lacre"')
    chave: str  # para comparar: sem acento, minúscula, só letras e números
    n: int
    total: int | None


_RE_EPISODIO = re.compile(
    r"^(?P<serie>.*?\S)[\s—–:|\-]*\b(?:ep|epis[oó]dio|parte|cap[ií]tulo)\.?\s*(?P<n>\d{1,2})"
    r"(?:\s*/\s*(?P<total>\d{1,2}))?(?!\d)",
    re.IGNORECASE,
)


def _chave_da_serie(serie: str) -> str:
    t = unicodedata.normalize("NFKD", serie.casefold())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def episodio(titulo: str | None) -> Episodio | None:
    """'Microdrama "O Lacre" — Ep. 1/6: Caixa Lacrada' → (O Lacre, 1, 6); avulso → None."""
    m = _RE_EPISODIO.match((titulo or "").strip())
    if m is None:
        return None
    serie = m.group("serie").strip(" \t—–:|-")
    chave = _chave_da_serie(serie)
    n = int(m.group("n"))
    total = int(m.group("total")) if m.group("total") else None
    if not chave or n < 1 or (total is not None and total < n):
        return None
    return Episodio(serie=serie, chave=chave, n=n, total=total)


# ── A hora (pura) ──────────────────────────────────────────────────────────


def minuto_do_robo(vaga: datetime) -> datetime:
    """Quando o robô pega a vaga (cron no minuto 2): 12:00 → 12:02; 18:40 → 19:02."""
    base = vaga.replace(minute=MINUTO_DO_ROBO, second=0, microsecond=0)
    return base if base >= vaga else base + timedelta(hours=1)


def quando_sai(hora: datetime, agora: datetime) -> str:
    """'hoje às 19h', 'amanhã às 12h', 'dia 30/10 às 12h' (horário de Brasília)."""
    h, a = hora.astimezone(BRT), agora.astimezone(BRT)
    relogio = f"{h.hour}h" if h.minute < 5 else f"{h.hour}h{h.minute:02d}"
    as_ = "à" if h.hour in (0, 1) else "às"
    dias = (h.date() - a.date()).days
    if dias <= 0:
        return f"hoje {as_} {relogio}"
    if dias == 1:
        return f"amanhã {as_} {relogio}"
    return f"dia {h:%d/%m} {as_} {relogio}"


def _frase(frases: tuple[str, ...], comentario_id: str) -> str:
    return frases[int(hashlib.sha256(comentario_id.encode()).hexdigest(), 16) % len(frases)]


# ── A grade do robô ────────────────────────────────────────────────────────


def _robo_ligado(rede: RedeSocial) -> bool:
    return bool(rede.ativo and rede.postagem_auto and rede.postagem_hora_inicio is not None)


def na_janela_de_postagem(rede: RedeSocial, agora: datetime) -> bool:
    """Perto do minuto do robô numa vaga de hoje: o episódio pode estar saindo."""
    if not _robo_ligado(rede):
        return False
    antes, depois = JANELA_POSTAGEM
    for vaga in autopostagem.horarios_do_dia(rede, get_settings(), agora=agora):
        t = minuto_do_robo(vaga)
        if t - antes <= agora <= t + depois:
            return True
    return False


async def proxima_vaga(session: AsyncSession, rede: RedeSocial, agora: datetime) -> datetime | None:
    """A próxima vaga que o robô ainda vai preencher nesta conta (o minuto dele)."""
    if not _robo_ligado(rede):
        return None
    s = get_settings()
    atraso = timedelta(minutes=s.marketing_postagem_atraso_max_min)
    # A mesma conta do robô (`autopostagem.rodada`): a grade é sequencial (os
    # `ocupados` primeiros horários de hoje já saíram) e a vaga livre que já
    # chegou sai na passada seguinte dele, enquanto não passar do atraso máximo.
    ocupados = await autopostagem._ja_ocupados(session, rede, agora)
    for vaga in autopostagem.horarios_do_dia(rede, s, agora=agora)[ocupados:]:
        t = minuto_do_robo(vaga)
        while t <= agora:
            t += timedelta(hours=1)
        if t - vaga <= atraso:
            return t
    for dias in (1, 2):
        grade = autopostagem.horarios_do_dia(rede, s, agora=agora + timedelta(days=dias))
        if grade:
            return minuto_do_robo(grade[0])
    return None


# ── A decisão ──────────────────────────────────────────────────────────────


@dataclass
class Decisao:
    acao: str
    motivo: str
    parte: int | None = None
    # A hora da parte seguinte; None numa resposta = já saiu.
    quando: datetime | None = None


async def _titulos_da_midia(
    session: AsyncSession, rede_id: UUID, midia_id: str
) -> list[str] | None:
    """O título do criativo (e do roteiro) publicado com esta mídia nesta conta."""
    linha = (
        await session.execute(
            select(MarketingCreative.modelo, MarketingRoteiro.titulo)
            .join(MarketingPostagem, MarketingPostagem.creative_id == MarketingCreative.id)
            .outerjoin(MarketingRoteiro, MarketingRoteiro.id == MarketingCreative.roteiro_id)
            .where(
                MarketingPostagem.rede_social_id == rede_id,
                MarketingPostagem.post_external_id == midia_id,
            )
            .order_by(MarketingPostagem.publicado_em.desc().nulls_last())
            .limit(1)
        )
    ).first()
    return [t for t in linha if t] if linha else None


async def _criativos_do_episodio(
    session: AsyncSession, marca_id: UUID, ep: Episodio, n: int
) -> list[UUID]:
    """Os criativos da marca que são o episódio `n` da mesma série."""
    serie = ep.serie.lower()
    linhas = (
        await session.execute(
            select(MarketingCreative.id, MarketingCreative.modelo, MarketingRoteiro.titulo)
            .outerjoin(MarketingRoteiro, MarketingRoteiro.id == MarketingCreative.roteiro_id)
            .where(
                MarketingCreative.marca_id == marca_id,
                or_(
                    func.lower(MarketingCreative.modelo).contains(serie, autoescape=True),
                    func.lower(MarketingRoteiro.titulo).contains(serie, autoescape=True),
                ),
            )
        )
    ).all()
    ids = []
    for criativo_id, modelo, titulo in linhas:
        outro = episodio(modelo) or episodio(titulo)
        if (
            outro is not None
            and outro.chave == ep.chave
            and outro.n == n
            and (outro.total is None or ep.total is None or outro.total == ep.total)
        ):
            ids.append(criativo_id)
    return ids


async def proximo_episodio(
    session: AsyncSession, rede: RedeSocial, pub: AtendimentoPublicacao, agora: datetime
) -> Decisao:
    """O que dizer sobre a continuação DESTA publicação (vale para todos os pedidos nela)."""
    titulos = await _titulos_da_midia(session, rede.id, pub.externo_id)
    ep = next((e for e in map(episodio, titulos or []) if e is not None), None)
    if ep is None or rede.marca_id is None:
        return Decisao(IGNORAR, "nao_e_serie")
    if ep.total is not None and ep.n >= ep.total:
        return Decisao(IGNORAR, "ultimo_episodio")
    parte = ep.n + 1
    ids = await _criativos_do_episodio(session, rede.marca_id, ep, parte)
    if not ids:
        return Decisao(ESPERAR, "sem_proximo", parte)
    postagens = (
        (
            await session.execute(
                select(MarketingPostagem).where(
                    MarketingPostagem.creative_id.in_(ids),
                    MarketingPostagem.rede_social_id == rede.id,
                )
            )
        )
        .scalars()
        .all()
    )
    if any(p.status == STATUS_PUBLICADO for p in postagens):
        return Decisao(RESPONDER, "ja_saiu", parte)
    em_voo = [p for p in postagens if p.status in STATUS_EM_VOO]
    if em_voo:
        horas = [
            redes._utc(p.agendado_para)
            for p in em_voo
            if p.agendado_para is not None
            and redes._utc(p.agendado_para) > agora + JANELA_POSTAGEM[0]
        ]
        if horas:
            return Decisao(RESPONDER, "agendado", parte, min(horas))
        return Decisao(ESPERAR, "publicando", parte)
    proximo = await autopostagem.proximo_criativo(session, rede)
    if proximo is None or proximo[0].id not in ids:
        return Decisao(ESPERAR, "outro_na_frente", parte)
    vaga = await proxima_vaga(session, rede, agora)
    if vaga is None:
        return Decisao(ESPERAR, "sem_grade", parte)
    return Decisao(RESPONDER, "proximo_da_fila", parte, vaga)


def _automatica() -> object:
    return AtendimentoMensagem.payload.has_key(CHAVE)


def _conta_viva() -> object:
    return AtendimentoMensagem.status != MSG_FALHOU


def _quando_msg() -> object:
    return func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)


async def _contar(session: AsyncSession, *filtros: object) -> int:
    return int(
        await session.scalar(
            select(func.count(AtendimentoMensagem.id))
            .join(AtendimentoConversa, AtendimentoConversa.id == AtendimentoMensagem.conversa_id)
            .where(_automatica(), _conta_viva(), *filtros)
        )
        or 0
    )


async def decidir(
    session: AsyncSession,
    canal: AtendimentoCanal,
    rede: RedeSocial,
    c: AtendimentoComentario,
    pub: AtendimentoPublicacao,
    agora: datetime,
    cache: dict[UUID, Decisao],
) -> Decisao:
    """Responder, esperar a rodada seguinte ou deixar para a equipe."""
    conversa = await session.get(AtendimentoConversa, c.conversa_id) if c.conversa_id else None
    if conversa is None:
        return Decisao(IGNORAR, "sem_conversa")
    criado = redes._utc(c.criado_em) or agora
    # A loja já respondeu esta pessoa depois do comentário (a equipe pelo app, o DaVinci).
    ja = await session.scalar(
        select(func.count(AtendimentoMensagem.id)).where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.autor == AUTOR_LOJA,
            AtendimentoMensagem.origem != ORIGEM_NOTA,
            _conta_viva(),
            or_(AtendimentoMensagem.enviada_em.is_(None), AtendimentoMensagem.enviada_em >= criado),
        )
    )
    if ja:
        return Decisao(IGNORAR, "ja_respondido")
    if await _contar(session, AtendimentoMensagem.conversa_id == conversa.id):
        return Decisao(IGNORAR, "pessoa_ja_respondida")
    if conversa.comprador_id and (
        await _contar(
            session,
            AtendimentoConversa.canal_id == canal.id,
            AtendimentoConversa.comprador_id == conversa.comprador_id,
            _quando_msg() >= agora - timedelta(hours=24),
        )
        >= MAX_POR_PESSOA_DIA
    ):
        return Decisao(IGNORAR, "limite_pessoa")
    if pub.id not in cache:
        cache[pub.id] = await proximo_episodio(session, rede, pub, agora)
    d = cache[pub.id]
    if d.acao != RESPONDER:
        return d
    hora = agora - timedelta(hours=1)
    if (
        await _contar(
            session,
            AtendimentoConversa.canal_id == canal.id,
            AtendimentoConversa.anuncio_id == pub.externo_id,
            _quando_msg() >= hora,
        )
        >= MAX_POR_POST_HORA
    ):
        return Decisao(ESPERAR, "limite_post", d.parte)
    if (
        await _contar(session, AtendimentoConversa.canal_id == canal.id, _quando_msg() >= hora)
        >= MAX_POR_CONTA_HORA
    ):
        return Decisao(ESPERAR, "limite_conta", d.parte)
    return d


def texto_da_resposta(d: Decisao, c: AtendimentoComentario, agora: datetime) -> str:
    u = (c.autor_username or "").lstrip("@")
    if d.quando is None:
        return _frase(FRASES_JA_SAIU, c.externo_id).format(u=u, n=d.parte)
    return _frase(FRASES_VAI_SAIR, c.externo_id).format(
        u=u, n=d.parte, quando=quando_sai(d.quando, agora)
    )


def _marcar(c: AtendimentoComentario, status: str, motivo: str, agora: datetime, **extra) -> None:
    c.dados = {
        **(c.dados or {}),
        CHAVE: {"status": status, "motivo": motivo, "em": redes._iso(agora), **extra},
    }


# ── A rodada de uma conta ─────────────────────────────────────────────────


async def _rodar(
    session: AsyncSession,
    conta: redes.ContaRede,
    *,
    cliente: httpx.AsyncClient,
    agora: datetime,
) -> dict | None:
    ref = canais_externos.ref_da_rede(conta.plataforma, str(conta.conta_id))
    canal = (
        await session.execute(
            select(AtendimentoCanal).where(
                AtendimentoCanal.externo_ref == ref, AtendimentoCanal.canal == CANAL_COMENTARIO
            )
        )
    ).scalar_one_or_none()
    if canal is None or canal.auto_continuacao_desde is None:
        return None
    if redes.bloqueio_do_envio() is not None:
        return {"pulado": "envio_desligado"}
    rede = await session.get(RedeSocial, canal.rede_social_id) if canal.rede_social_id else None
    if rede is None:
        return {"pulado": "sem_conta"}
    if na_janela_de_postagem(rede, agora):
        return {"pulado": "janela_de_postagem"}
    canal_id = canal.id
    desde = max(redes._utc(canal.auto_continuacao_desde), agora - JANELA_COMENTARIO)
    linhas = (
        await session.execute(
            select(AtendimentoComentario, AtendimentoPublicacao)
            .join(
                AtendimentoPublicacao,
                AtendimentoPublicacao.id == AtendimentoComentario.publicacao_id,
            )
            .where(
                AtendimentoPublicacao.canal_id == canal.id,
                AtendimentoPublicacao.tipo == PUBLICACAO_PROPRIA,
                AtendimentoComentario.da_marca.is_(False),
                AtendimentoComentario.oculto.is_(False),
                AtendimentoComentario.conversa_id.isnot(None),
                AtendimentoComentario.autor_username.isnot(None),
                AtendimentoComentario.criado_em >= desde,
                AtendimentoComentario.criado_em <= agora,
                ~AtendimentoComentario.dados.has_key(CHAVE),
            )
            .order_by(AtendimentoComentario.criado_em.asc(), AtendimentoComentario.id)
            .limit(300)
        )
    ).all()
    r = {"pedidos": 0, "respondidos": 0, "esperando": 0, "ignorados": 0, "falhas": 0}
    motivos: dict[str, int] = {}
    cache: dict[UUID, Decisao] = {}
    for c, pub in linhas:
        if not pede_continuacao(c.texto):
            continue
        r["pedidos"] += 1
        if r["respondidos"] >= MAX_POR_RODADA:
            r["esperando"] += 1
            motivos["limite_rodada"] = motivos.get("limite_rodada", 0) + 1
            continue
        d = await decidir(session, canal, rede, c, pub, agora, cache)
        motivos[d.motivo] = motivos.get(d.motivo, 0) + 1
        if d.acao == ESPERAR:
            r["esperando"] += 1
            continue
        if d.acao == IGNORAR:
            _marcar(c, IGNORADO, d.motivo, agora)
            await session.commit()
            r["ignorados"] += 1
            continue
        comentario_id = c.id
        try:
            mensagem = await redes.responder(
                session,
                c,
                texto_da_resposta(d, c, agora),
                user=None,
                confirmar=True,
                cliente=cliente,
                agora=agora,
                automatica=True,
                payload_extra={CHAVE: {"parte": d.parte, "motivo": d.motivo}},
            )
        except EnvioRecusado as e:
            # Nada saiu. A sessão volta limpa (o rollback expira o que foi
            # lido) e a rodada para aqui: a seguinte confere de novo. A recusa
            # do COMENTÁRIO (oculto, da marca, sem conversa) não volta; a da
            # conta ou do sistema (sem token, envio desligado, uma em voo)
            # espera.
            await session.rollback()
            motivos[e.code] = motivos.get(e.code, 0) + 1
            if e.code in _RECUSAS_DO_COMENTARIO:
                linha = await session.get(AtendimentoComentario, comentario_id)
                if linha is not None:
                    _marcar(linha, IGNORADO, e.code, agora)
                    await session.commit()
                r["ignorados"] += 1
            else:
                r["esperando"] += 1
            break
        if mensagem.status == MSG_ENVIADA:
            _marcar(c, RESPONDIDO, d.motivo, agora, parte=d.parte, mensagem_id=str(mensagem.id))
            await session.commit()
            r["respondidos"] += 1
            continue
        # `falhou` (a rede recusou) ou `revisar` (sem resposta: pode ter saído).
        _marcar(c, FALHOU, mensagem.status, agora, parte=d.parte, mensagem_id=str(mensagem.id))
        await session.commit()
        r["falhas"] += 1
        break
    if r["pedidos"]:
        logger.info(
            "atendimento_continuacao_rodada",
            canal_id=str(canal_id),
            **r,
            motivos=motivos,
        )
    return {**r, "motivos": motivos}


async def responder_da_conta(
    conta: redes.ContaRede, *, cliente: httpx.AsyncClient, agora: datetime | None = None
) -> dict | None:
    """Uma conta do Instagram, na SUA sessão, logo depois da leitura. Nunca levanta."""
    if conta.plataforma != PLATAFORMA_INSTAGRAM or not conta.conta_id:
        return None
    agora = agora or datetime.now(UTC)
    try:
        async with _db.SessionLocal() as session:
            return await _rodar(session, conta, cliente=cliente, agora=agora)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "atendimento_continuacao_falhou",
            rede_id=str(conta.rede_social_id),
            err=type(exc).__name__,
        )
        return {"erro": type(exc).__name__}
