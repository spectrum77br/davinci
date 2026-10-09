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

Só leitura; nada aqui decifra e-mail (só o config das caixas, para os aliases).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MailMailbox, MailOutbox, User
from app.models.mail_atendimento import (
    MailAgenteV2,
    MailFolder,
    MailMailboxSettings,
    MailMessageMeta,
    MailOutboxMeta,
)
from app.services import mail_central
from app.services.mail_atendimento import caixa as config_caixa
from app.services.mail_atendimento import ponte, rotear

# A caixa com a ponte ligada sem sinal do agente há mais que isto: "sem ler"
# (o agente bate sinal a cada ≤ 3 min; o mesmo limite das lojas por API).
LIMITE_SEM_SINAL = timedelta(minutes=30)
ESTADOS_RUINS = ("login_required", "error")


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


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def caixas_paradas(
    session: AsyncSession, *, agora: datetime | None = None
) -> list[CaixaParada]:
    """As caixas `empresa` com ponte ligada sem sinal há mais de 30 min, ou com o
    agente avisando "entrar de novo"/erro. As privadas NUNCA entram (todo o
    /atendimento vê esta lista)."""
    agora = agora or datetime.now(UTC)
    linhas = (
        await session.execute(
            select(MailMailbox, MailMailboxSettings)
            .join(MailMailboxSettings, MailMailboxSettings.mailbox_id == MailMailbox.id)
            .where(
                MailMailboxSettings.visibilidade == "empresa",
                MailMailboxSettings.ponte_ligada.is_(True),
            )
            .order_by(MailMailbox.label)
        )
    ).all()
    saida: list[CaixaParada] = []
    for mailbox, _cfg in linhas:
        visto = _utc(mailbox.last_seen_at)
        desde = visto or _utc(mailbox.created_at)
        sem_sinal = desde is not None and agora - desde > LIMITE_SEM_SINAL
        ruim = mailbox.state in ESTADOS_RUINS
        if not (sem_sinal or ruim):
            continue
        if mailbox.state == "login_required":
            motivo = "o Tuta pediu para entrar de novo"
        elif mailbox.state == "error":
            motivo = "o agente do Mac avisou erro"
        else:
            motivo = "o Mac não dá sinal"
        saida.append(
            CaixaParada(
                mailbox_id=mailbox.id,
                nome=mailbox.label,
                motivo=motivo,
                desde=desde,
                nunca_leu=visto is None,
                minutos=max(0, int((agora - desde).total_seconds() // 60)) if desde else 0,
                detalhe=mailbox.error_code,
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
