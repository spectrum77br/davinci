"""A SAÚDE das caixas de e-mail e a faixa "lojas sem ler" do /atendimento.

Uma linha por caixa, a partir do que a Central já grava (`mail_mailboxes`:
estado, último sinal do agente, última leitura) mais a configuração nossa e
o que a ponte decidiu (`mail_message_meta`). QUEM VÊ:

  • caixa `empresa` (a geral): toda pessoa do /atendimento — é a caixa das
    lojas, e a faixa vermelha do topo acende para ela;
  • caixa `privada` (a Goslin): só o dono dela e os admins (a regra da
    Central: quem não pode nem fica sabendo que a caixa existe — nunca entra
    na faixa vermelha, que todo mundo vê).

Para quem MEXE: as lojas cujo e-mail do cadastro não está em nenhuma caixa
com a ponte ligada ("loja sem caixa lida": os e-mails delas não chegam ao
/atendimento), o cadastro "sac@marca" que não dá para completar e as fichas
SEM integração (nem o FK nem o par nome × plataforma: o e-mail delas entra na
conversa da ficha, sem a API — o dono liga em Cadastros › Lojas).

A FAIXA VERMELHA (`caixas_paradas`) acende para a caixa `empresa` com a
ponte ligada quando (a crítica do .md, "alerta quando o leitor do Tuta ficar
parado"):
  • o Mac não dá sinal há mais de 30 min, ou o agente avisa "entrar de novo"/erro;
  • o Mac dá sinal mas NÃO LÊ há mais de 30 min: a última leitura é a mais
    nova entre `last_sync_at` (o ingest da Central) e o `/v2/sync` do nosso
    conector (`mail_agente_v2.visto_em`, a cada volta que leu as pastas do
    Tuta) — o ingest sozinho só anda quando chega e-mail, e acenderia toda
    madrugada calma;
  • a PONTE (o job `mail_ponte` do worker) não leva os e-mails: há e-mail da
    caixa esperando a ponte (sem meta, ou em `novo`) há mais de 15 min E a
    última volta da ponte (o carimbo no Redis, `carimbar_ponte`) é mais velha
    que isso. O carimbo separa a ponte parada do e-mail que espera DE
    PROPÓSITO (a Amazon esperando o Gmail, até 6 h; o recibo da nossa
    resposta): com a ponte girando, esperar não é estar parado.

Só leitura; nada aqui decifra e-mail (só o config das caixas, para os aliases).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MailMailbox, MailMessage, MailOutbox, User
from app.models.mail_atendimento import (
    MailAgenteV2,
    MailFolder,
    MailMailboxSettings,
    MailMessageMeta,
    MailOutboxMeta,
)
from app.redis_client import redis
from app.services import mail_central
from app.services.mail_atendimento import caixa as config_caixa
from app.services.mail_atendimento import ponte, rotear
from app.services.mail_atendimento.constantes import ESTADO_NOVO

logger = structlog.get_logger()

# A caixa com a ponte ligada sem sinal do agente há mais que isto: "sem ler"
# (o agente bate sinal a cada ≤ 3 min; o mesmo limite das lojas por API).
LIMITE_SEM_SINAL = timedelta(minutes=30)
# Sinal, mas sem LER (a última leitura) há mais que isto.
LIMITE_SEM_LEITURA = timedelta(minutes=30)
# E-mail esperando a ponte há mais que isto, com a ponte sem girar: parada.
LIMITE_PONTE = timedelta(minutes=15)
JANELA_PONTE = timedelta(days=1)
ESTADOS_RUINS = ("login_required", "error")
# O carimbo da última volta da ponte (o worker grava; a faixa lê).
CHAVE_PONTE_OK = "davinci:mail:ponte:ultimo_ok"

MOTIVO_LOGIN = "o Tuta pediu para entrar de novo"
MOTIVO_ERRO = "o agente do Mac avisou erro"
MOTIVO_SEM_SINAL = "o Mac não dá sinal"
MOTIVO_SEM_LEITURA = "o Mac dá sinal, mas não lê o Tuta"
MOTIVO_PONTE = "os e-mails não chegam ao /atendimento (a ponte do servidor parou)"
ACAO_PONTE = (
    "Avise o admin: o job mail_ponte do worker não está levando os e-mails da Central "
    "às conversas (ver os logs do davinci-worker)"
)


@dataclass(frozen=True)
class CaixaParada:
    """Uma caixa da EMPRESA com a ponte ligada que não está lendo (a faixa vermelha)."""

    mailbox_id: UUID
    nome: str
    motivo: str
    desde: datetime | None
    nunca_leu: bool
    minutos: int
    detalhe: str | None
    # O limite que esta parada passou (a faixa mostra "há mais de N min").
    limite: timedelta = LIMITE_SEM_SINAL
    # O que fazer, quando não é olhar o Mac (a ponte parada é do servidor).
    acao: str | None = None


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _minutos(agora: datetime, desde: datetime | None) -> int:
    return max(0, int((agora - desde).total_seconds() // 60)) if desde else 0


async def carimbar_ponte(*, agora: datetime | None = None) -> None:
    """O worker chama depois de cada volta da ponte que terminou. Nunca levanta
    (o carimbo é acessório: sem ele, a faixa só olha o e-mail que espera)."""
    momento = str(int((agora or datetime.now(UTC)).timestamp()))
    try:
        await redis.set(CHAVE_PONTE_OK, momento)
    except Exception as e:  # noqa: BLE001
        logger.warning("mail_ponte_carimbo_falhou", err=type(e).__name__)


async def ultima_volta_da_ponte() -> datetime | None:
    """A última volta da ponte que terminou (None = sem carimbo, ou sem Redis)."""
    try:
        bruto = await redis.get(CHAVE_PONTE_OK)
    except Exception as e:  # noqa: BLE001
        logger.warning("mail_ponte_carimbo_ilegivel", err=type(e).__name__)
        return None
    try:
        return datetime.fromtimestamp(int(bruto), UTC) if bruto else None
    except (TypeError, ValueError):
        return None


async def _esperando_a_ponte(
    session: AsyncSession, mailbox_id: UUID, ponte_desde: datetime | None, corte: datetime
) -> datetime | None:
    """Desde quando o e-mail MAIS VELHO da caixa espera a ponte (chegou à Central
    antes do `corte` e não tem meta, ou ficou em `novo`) — None = nenhum.

    "Sem meta OU meta em `novo`" = o estado da meta (uma por e-mail:
    `message_id` é a chave), sem meta valendo `novo`. Subconsulta escalar de
    propósito: o banco busca pela chave só os e-mails do último dia da caixa
    — o NOT EXISTS (e o OR de antes) viravam varredura da tabela de metas
    inteira (todas as caixas) a cada /resumo."""
    estado = (
        select(MailMessageMeta.estado)
        .where(MailMessageMeta.message_id == MailMessage.id)
        .scalar_subquery()
    )
    esperando = func.coalesce(estado, ESTADO_NOVO) == ESTADO_NOVO
    # Só o último dia (pelo índice caixa × recebido): a ponte parada sempre
    # deixa e-mail novo esperando, e a faixa não varre a caixa inteira.
    piso = corte - JANELA_PONTE
    if ponte_desde is not None and ponte_desde > piso:
        piso = ponte_desde
    consulta = select(func.min(MailMessage.created_at)).where(
        MailMessage.mailbox_id == mailbox_id,
        MailMessage.received_at >= piso,
        MailMessage.created_at < corte,
        esperando,
    )
    return _utc(await session.scalar(consulta))


async def caixas_paradas(
    session: AsyncSession, *, agora: datetime | None = None
) -> list[CaixaParada]:
    """As caixas `empresa` com ponte ligada que não leem: sem sinal (30 min), o
    agente avisando "entrar de novo"/erro, o Mac com sinal mas sem LER (30
    min), ou a ponte do servidor parada com e-mail esperando (15 min). Uma
    linha por caixa (vale o primeiro motivo, nessa ordem). As privadas NUNCA
    entram (todo o /atendimento vê esta lista)."""
    agora = agora or datetime.now(UTC)
    linhas = (
        await session.execute(
            select(MailMailbox, MailMailboxSettings, MailAgenteV2.visto_em)
            .join(MailMailboxSettings, MailMailboxSettings.mailbox_id == MailMailbox.id)
            .outerjoin(MailAgenteV2, MailAgenteV2.mailbox_id == MailMailbox.id)
            .where(
                MailMailboxSettings.visibilidade == "empresa",
                MailMailboxSettings.ponte_ligada.is_(True),
            )
            .order_by(MailMailbox.label)
        )
    ).all()
    saida: list[CaixaParada] = []
    volta_da_ponte: datetime | None = None
    olhou_a_ponte = False
    for mailbox, cfg, visto_v2 in linhas:
        visto = _utc(mailbox.last_seen_at)
        criada = _utc(mailbox.created_at)
        desde = visto or criada
        sem_sinal = desde is not None and agora - desde > LIMITE_SEM_SINAL
        ruim = mailbox.state in ESTADOS_RUINS
        if sem_sinal or ruim:
            if mailbox.state == "login_required":
                motivo = MOTIVO_LOGIN
            elif mailbox.state == "error":
                motivo = MOTIVO_ERRO
            else:
                motivo = MOTIVO_SEM_SINAL
            saida.append(
                CaixaParada(
                    mailbox_id=mailbox.id,
                    nome=mailbox.label,
                    motivo=motivo,
                    desde=desde,
                    nunca_leu=visto is None,
                    minutos=_minutos(agora, desde),
                    detalhe=mailbox.error_code,
                )
            )
            continue
        # Sinal, mas sem LER: a leitura mais nova (o ingest ou o /v2/sync).
        leituras = [d for d in (_utc(mailbox.last_sync_at), _utc(visto_v2)) if d is not None]
        leitura = max(leituras) if leituras else None
        desde_leitura = leitura or criada
        if desde_leitura is not None and agora - desde_leitura > LIMITE_SEM_LEITURA:
            saida.append(
                CaixaParada(
                    mailbox_id=mailbox.id,
                    nome=mailbox.label,
                    motivo=MOTIVO_SEM_LEITURA,
                    desde=desde_leitura,
                    nunca_leu=leitura is None,
                    minutos=_minutos(agora, desde_leitura),
                    detalhe=mailbox.error_code,
                    limite=LIMITE_SEM_LEITURA,
                )
            )
            continue
        # A ponte do servidor: e-mail esperando há mais de 15 min, sem volta recente.
        esperando = await _esperando_a_ponte(
            session, mailbox.id, _utc(cfg.ponte_desde), agora - LIMITE_PONTE
        )
        if esperando is None:
            continue
        if not olhou_a_ponte:
            volta_da_ponte = await ultima_volta_da_ponte()
            olhou_a_ponte = True
        if volta_da_ponte is not None and agora - volta_da_ponte <= LIMITE_PONTE:
            continue  # a ponte gira: o e-mail espera de propósito (o Gmail, o recibo)
        saida.append(
            CaixaParada(
                mailbox_id=mailbox.id,
                nome=mailbox.label,
                motivo=MOTIVO_PONTE,
                desde=esperando,
                nunca_leu=False,
                minutos=_minutos(agora, esperando),
                detalhe=None,
                limite=LIMITE_PONTE,
                acao=ACAO_PONTE,
            )
        )
    return saida


async def caixas_visiveis(session: AsyncSession, user: User) -> list[MailMailbox]:
    """As caixas que esta pessoa vê na Saúde: as da empresa e as que ela pode abrir."""
    caixas = (await session.execute(select(MailMailbox).order_by(MailMailbox.label))).scalars()
    saida: list[MailMailbox] = []
    for mailbox in caixas:
        cfg = await config_caixa.config_da_caixa(session, mailbox.id)
        if cfg.empresa or mail_central.allowed(mailbox, user):
            saida.append(mailbox)
    return saida


async def linha_da_caixa(session: AsyncSession, mailbox: MailMailbox) -> dict:
    """A linha da Saúde: estado, sinal, chaves e o que a ponte decidiu (contagens)."""
    cfg = await config_caixa.config_da_caixa(session, mailbox.id)
    agora = datetime.now(UTC)
    visto = _utc(mailbox.last_seen_at)
    online = visto is not None and visto > agora - timedelta(minutes=3)
    contagens = {
        estado: int(n)
        for estado, n in (
            await session.execute(
                select(MailMessageMeta.estado, func.count())
                .where(MailMessageMeta.mailbox_id == mailbox.id)
                .group_by(MailMessageMeta.estado)
            )
        ).all()
    }
    pastas_novas = int(
        await session.scalar(
            select(func.count()).where(
                MailFolder.mailbox_id == mailbox.id,
                MailFolder.revisada.is_(False),
                MailFolder.sumiu_em.is_(None),
            )
        )
        or 0
    )
    a_conferir = int(
        await session.scalar(
            select(func.count())
            .select_from(MailOutbox)
            .join(MailOutboxMeta, MailOutboxMeta.outbox_id == MailOutbox.id)
            .where(
                MailOutbox.mailbox_id == mailbox.id,
                MailOutboxMeta.origem == "conversa",
                or_(
                    MailOutbox.status == "uncertain",
                    and_(
                        MailOutbox.status == "leased",
                        MailOutbox.leased_at < agora - mail_central.LEASE_TIMEOUT,
                    ),
                ),
            )
        )
        or 0
    )
    # O conector v2 conta de si a cada volta (instância, versão, dois agentes).
    agente = await session.get(MailAgenteV2, mailbox.id)
    dois_agentes = _utc(agente.dois_agentes_em) if agente is not None else None
    return {
        "mailbox_id": str(mailbox.id),
        "nome": mailbox.label,
        "visibilidade": cfg.visibilidade,
        "estado": mailbox.state if online else "offline",
        "erro": mailbox.error_code,
        "ultimo_sinal_em": visto,
        "ultima_leitura_em": _utc(mailbox.last_sync_at),
        "agente_tipo": cfg.agente_tipo or ("v2" if agente is not None else None),
        "agente_versao": agente.versao_agente if agente is not None else None,
        "dois_agentes": dois_agentes is not None and dois_agentes > agora - timedelta(minutes=30),
        "ponte_ligada": cfg.ponte_ligada,
        "ponte_desde": cfg.ponte_desde,
        "envio": {
            "ligado": mailbox.send_enabled,
            "pronto": mail_central.send_ready(mailbox),
            "modo": cfg.envio_modo if cfg.empresa else "caixa",
            "pausado_ate": cfg.envio_pausado_ate,
        },
        "emails": contagens,
        "pastas_sem_revisar": pastas_novas,
        "envios_a_conferir": a_conferir,
    }


async def lojas_sem_caixa_lida(session: AsyncSession) -> dict:
    """Para quem MEXE: as lojas cujo e-mail não está em nenhuma caixa com ponte, e o
    cadastro de e-mail que não se lê (nem como "sac@marca")."""
    cad = await rotear.cadastro(session)
    lidos: set[str] = set()
    for mailbox, cfg in (
        await session.execute(
            select(MailMailbox, MailMailboxSettings).join(
                MailMailboxSettings, MailMailboxSettings.mailbox_id == MailMailbox.id
            )
        )
    ).all():
        if cfg.ponte_ligada:
            lidos |= ponte.aliases_da_caixa(mailbox)[1]
    fora = [
        {
            "store_info_id": str(lj.store_info_id),
            "nome": lj.nome,
            "plataforma": lj.plataforma,
            "endereco": lj.descricao,
        }
        for lj in cad.lojas
        if not any(lj.casa(e) for e in lidos)
    ]
    return {
        "lojas_sem_caixa_lida": fora,
        "cadastro_incompleto": [
            {"store_info_id": str(sid), "nome": nome} for sid, nome in cad.incompletas
        ],
        "lojas_sem_integracao": [
            {
                "store_info_id": str(lj.store_info_id),
                "nome": lj.nome,
                "plataforma": lj.plataforma,
                "endereco": lj.descricao,
            }
            for lj in cad.sem_integracao()
        ],
    }
