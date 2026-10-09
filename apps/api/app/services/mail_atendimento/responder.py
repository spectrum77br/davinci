"""A RESPOSTA de e-mail pela CONVERSA do /atendimento — saindo pela fila DELA (RF5/RF6).

    pessoa clica Enviar → `enviar.enviar_resposta` (o caminho ÚNICO) vê que a
    conversa é da ponte (`ponte.e_conversa_da_ponte`) e vem para cá, ANTES de
    qualquer adaptador de marketplace (nunca o SMTP da Amazon):

1. TRAVAS (`EnvioRecusado`, nada entra na fila), na ordem da explicação:
     envio_desligado        — o freio geral do .env (ATENDIMENTO_ENVIO_ATIVO);
     auto_desligado         — a IA nunca responde e-mail;
     conversa_bloqueada, sem_email;
     remetente_suspeito, email_historico (pasta vendas), resposta_pela_amazon
     (a Amazon continua pelo Gmail), encaminhado;
     sem_alias              — nenhum endereço da caixa recebeu (nunca cai no
                              principal: a resposta sai PELO ENDEREÇO QUE RECEBEU);
     so_endereco_principal  — numa caixa `empresa`, só o endereço PRINCIPAL (o
                              login da conta do Tuta) recebeu: ele nunca vai
                              para o cliente — responder pelo Tuta;
     sem_destinatario, para_e_nosso;
     caixa_sem_envio / mac_desconectado — as chaves da Central (o admin liga
                              o envio da caixa, o Mac confirma, sinal < 3 min);
     envio_pausado, fora_da_lista_de_teste, teto_da_hora, teto_do_dia,
     teto_da_conta          — numa caixa `empresa`, a pausa, o modo teste e os
                              tetos (`caixa.trava_de_envio`), NA HORA DE ENFILEIRAR;
     remetente_nao_responde — "não responder"/aviso: CONFIRMÁVEL ("enviar
                              mesmo assim", decisão padrão de 08/10/2026);
     ja_respondido_pela_caixa — o MESMO e-mail já tem resposta pela caixa crua
                              da Central (E-mail › Caixas): CONFIRMÁVEL;
     e as de sempre: texto_invalido, envio_repetido, conversa_mudou,
     envio_em_andamento.
2. A RESPOSTA: De = o endereço da caixa que RECEBEU (no site, RF6, o e-mail
   do TIPO do chamado na marca — `marca_emails` — quando a caixa tem ele);
   Para = o que a Central calcula (Reply-To, senão o remetente; nunca um
   endereço nosso) — no formulário do site (DE sac@ PARA sac@) o cliente
   lido do corpo, no SERVIDOR; Assunto = "Re: " + o original (+ o
   [protocolo] no site); Texto = o da pessoa + a assinatura da loja/marca +
   a citação curta ("Em DD/MM/AAAA HH:MM, Fulano escreveu:"), com o texto
   citado já protegido (links de acesso e códigos fora).
3. A mensagem nasce `enviando` e é COMMITADA junto do job da fila DELA
   (`mail_central.queue_reply`, request_id = o id da mensagem: o mesmo
   clique repetido nunca vira 2 envios; a Central só deixa 1 resposta viva
   por e-mail) e da ligação `mail_outbox_meta`. Quem envia é o Mac (lease,
   recibo); o worker da ponte passa o status do job para a mensagem
   (`ponte.sincronizar_envios`): enviada, falhou ou revisar.

Texto (do cliente e o nosso) nunca vai para o log: só ids, estados e códigos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import is_unique_violation
from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    Company,
    Integration,
    MailMailbox,
    MailMessage,
    MailOutbox,
    Marca,
    MarcaEmail,
    User,
)
from app.models.mail_atendimento import MailMessageMeta, MailOutboxMeta
from app.schemas.mail import ReplyIn
from app.security.cipher import decrypt_json
from app.services import mail_central
from app.services.atendimento import gravar, lojas
from app.services.atendimento.constantes import (
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CONVERSA_BLOQUEADA,
    MSG_ENVIANDO,
    ORIGEM_HUMANO,
    ORIGEM_IA,
)
from app.services.mail_atendimento import caixa as config_caixa
from app.services.mail_atendimento import codigos, enderecos, ponte, texto
from app.services.mail_atendimento.constantes import (
    ASSINATURA_LOJA,
    CITACAO_MAX_CARACTERES,
    CITACAO_MAX_LINHAS,
    ESTADO_GRAVADO,
    ESTADO_SEM_VINCULO,
    FINALIDADE_VENDAS,
    JOBS_VIVOS,
    LINK_TUTA,
    PLATAFORMA_SITE,
    REMETENTE_NOSSO,
    RESPOSTA_MAX_CARACTERES,
    SEM_LOJA_ENCAMINHADO,
)

logger = structlog.get_logger()

_SP = ZoneInfo("America/Sao_Paulo")

# Códigos de recusa próprios do e-mail (os outros são os de `enviar`).
RECUSA_SEM_EMAIL = "sem_email"
RECUSA_SUSPEITO = "remetente_suspeito"
RECUSA_HISTORICO = "email_historico"
RECUSA_AMAZON = "resposta_pela_amazon"
RECUSA_ENCAMINHADO = "encaminhado"
RECUSA_SEM_ALIAS = "sem_alias"
RECUSA_SEM_DESTINATARIO = "sem_destinatario"
RECUSA_PARA_NOSSO = "para_e_nosso"
RECUSA_CAIXA_SEM_ENVIO = "caixa_sem_envio"
RECUSA_MAC_DESCONECTADO = "mac_desconectado"
RECUSA_PAUSADO = "envio_pausado"
RECUSA_FORA_DO_TESTE = "fora_da_lista_de_teste"
RECUSA_TETO_HORA = "teto_da_hora"
RECUSA_TETO_DIA = "teto_do_dia"
RECUSA_TETO_CONTA = "teto_da_conta"
RECUSA_NAO_RESPONDE = "remetente_nao_responde"
RECUSA_SO_PRINCIPAL = "so_endereco_principal"
RECUSA_JA_RESPONDIDO = "ja_respondido_pela_caixa"
# As recusas que a pessoa pode passar por cima confirmando ("enviar mesmo
# assim" — o `confirmar_nao_responde` do corpo vale para as duas).
CONFIRMAVEIS = frozenset({RECUSA_NAO_RESPONDE, RECUSA_JA_RESPONDIDO})

MOTIVO_IA = "A IA não envia e-mail: a resposta é sempre de uma pessoa."
MOTIVO_SEM_EMAIL = "Não há e-mail para responder nesta conversa."
MOTIVO_SUSPEITO = (
    "Remetente pode ser falso: a resposta está bloqueada. Confira no Tuta antes de qualquer coisa."
)
MOTIVO_HISTORICO = (
    "E-mail de vendas (histórico da plataforma): não é para responder. Use o canal da plataforma."
)
MOTIVO_AMAZON = (
    "E-mail da Amazon: responda pela conversa da Amazon (pelo Gmail, o caminho de hoje), "
    "não pelo Tuta."
)
MOTIVO_ENCAMINHADO = (
    "Chegou encaminhado de um endereço que não é da caixa: a resposta fica bloqueada até decisão."
)
MOTIVO_SEM_ALIAS = (
    "Nenhum endereço da caixa recebeu este e-mail: não há de onde responder (a resposta nunca "
    "sai pelo endereço principal no lugar do que recebeu)."
)
MOTIVO_SO_PRINCIPAL = (
    "Este e-mail chegou só no endereço PRINCIPAL da conta do Tuta (o de login): ele nunca "
    "aparece para o cliente. Responda pelo Tuta, por um alias."
)
MOTIVO_JA_RESPONDIDO = (
    "Este e-mail já tem resposta pela caixa da Central de e-mail ({quando}, {status}). "
    'Confira antes de responder de novo — ou confirme "enviar mesmo assim".'
)
_STATUS_DA_CRUA = {
    "sent": "saiu",
    "queued": "na fila do Mac",
    "leased": "saindo pelo Mac",
    "uncertain": "pode ter saído",
}
MOTIVO_SEM_DESTINATARIO = "Não há para quem responder (sem remetente nem Reply-To válido)."
MOTIVO_PARA_NOSSO = (
    "A resposta iria para um endereço NOSSO. No formulário do site, o e-mail do cliente "
    "não foi achado no corpo: responda pelo Tuta."
)
MOTIVO_CAIXA_SEM_ENVIO = (
    "O envio desta caixa está desligado na Central de e-mail (o admin liga em E-mail › Caixas)."
)
MOTIVO_MAC_DESCONECTADO = (
    "O Mac que envia por esta caixa está desconectado (sem sinal há mais de 3 min) ou não "
    "liberou o envio: a resposta não entra na fila agora."
)
MOTIVO_TRAVA = {
    "sending_paused": (RECUSA_PAUSADO, "O envio desta caixa está pausado."),
    "test_mode_recipient": (
        RECUSA_FORA_DO_TESTE,
        "Modo de teste: a resposta só sai para os endereços de teste desta caixa. Este "
        "destinatário não está na lista.",
    ),
    "hourly_limit": (RECUSA_TETO_HORA, "Teto de respostas da hora desta caixa atingido."),
    "daily_limit": (RECUSA_TETO_DIA, "Teto de respostas do dia desta caixa atingido."),
    "account_hourly_limit": (
        RECUSA_TETO_CONTA,
        "A conta do Tuta chegou ao teto de envios da hora (somando tudo).",
    ),
}
MOTIVO_NAO_RESPONDE = (
    'A resposta iria para um endereço "não responder"/aviso da plataforma ({endereco}): '
    'provavelmente ninguém lê. Use o canal da plataforma — ou confirme "enviar mesmo assim".'
)
MOTIVO_TEXTO_VAZIO = "A resposta está vazia."
MOTIVO_TEXTO_LONGO = "A resposta passou de {n} caracteres."
MOTIVO_LACUNA = "Há uma lacuna não preenchida no texto ({lacuna})."
_RE_LACUNA = re.compile(r"\{[a-z_]{2,40}\}")

# O código da Central (queue_reply) → a recusa nossa (o que a tela mostra).
_DA_CENTRAL: dict[str, tuple[str, str]] = {
    "sending_disabled": (
        "envio_desligado",
        "O envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO).",
    ),
    "sending_unavailable": (RECUSA_MAC_DESCONECTADO, MOTIVO_MAC_DESCONECTADO),
    "message_not_replyable": (RECUSA_PARA_NOSSO, MOTIVO_PARA_NOSSO),
    "form_recipient_not_allowed": (RECUSA_PARA_NOSSO, MOTIVO_PARA_NOSSO),
    "sender_not_authorized": (RECUSA_SEM_ALIAS, MOTIVO_SEM_ALIAS),
    "no_receiving_alias": (RECUSA_SEM_ALIAS, MOTIVO_SEM_ALIAS),
    "sender_not_receiving_alias": (RECUSA_SEM_ALIAS, MOTIVO_SEM_ALIAS),
    "main_address_not_allowed": (RECUSA_SO_PRINCIPAL, MOTIVO_SO_PRINCIPAL),
    **MOTIVO_TRAVA,
}


@dataclass
class Bloqueio:
    codigo: str
    texto: str
    confirmavel: bool = False


@dataclass
class Previa:
    """Como a resposta vai sair (a tela mostra antes do Enviar) e as travas de agora."""

    meta: MailMessageMeta | None = None
    message: MailMessage | None = None
    mailbox: MailMailbox | None = None
    cfg: config_caixa.ConfigCaixa | None = None
    de: str | None = None
    para: str | None = None
    formulario: bool = False
    assunto: str = ""
    tag: str | None = None
    assinatura: str = ""
    citacao_cabeca: str = ""
    citacao: str = ""
    bloqueios: list[Bloqueio] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    modo: str = "caixa"
    abrir_no_tuta: str | None = None

    def corpo_texto(self, texto_pessoa: str) -> str:
        partes = [texto_pessoa.strip()]
        if self.assinatura:
            partes.append(self.assinatura)
        if self.citacao_cabeca:
            citado = "\n".join(f"> {linha}" for linha in self.citacao.splitlines())
            partes.append(f"{self.citacao_cabeca}\n{citado}")
        return "\n\n".join(p for p in partes if p)


# ── O e-mail respondido ───────────────────────────────────────────────────


async def email_a_responder(
    session: AsyncSession, conversa: AtendimentoConversa, mail_message_id: UUID | None = None
) -> tuple[MailMessageMeta, MailMessage] | None:
    """O e-mail que a resposta responde: o pedido, ou o mais novo que não é nosso."""
    q = (
        select(MailMessageMeta, MailMessage)
        .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
        .where(
            MailMessageMeta.conversa_id == conversa.id,
            MailMessageMeta.estado.in_((ESTADO_GRAVADO, ESTADO_SEM_VINCULO)),
            MailMessage.direction == "inbound",
        )
    )
    if mail_message_id is not None:
        q = q.where(MailMessageMeta.message_id == mail_message_id)
    else:
        q = q.where(
            or_(
                MailMessageMeta.remetente_tipo.is_(None),
                MailMessageMeta.remetente_tipo != REMETENTE_NOSSO,
            )
        ).order_by(MailMessage.received_at.desc())
    linha = (await session.execute(q.limit(1))).first()
    return (linha[0], linha[1]) if linha is not None else None


def assunto_da_resposta(original: str | None, protocolo: str | None) -> str:
    """ "Re: " + o assunto (se ainda não tiver); no site, com o [protocolo] (a prévia)."""
    base = " ".join((original or "").split()) or "Sua mensagem"
    if protocolo and protocolo.upper() not in base.upper():
        base = f"[{protocolo}] {base}"
    if not re.match(r"^\s*(re|res|aw|sv|antw)\s*(\[\d+\])?\s*:", base, re.IGNORECASE):
        base = f"Re: {base}"
    return base[:400]


def citacao(email: ponte.Email) -> tuple[str, str]:
    """(cabeça, linhas citadas) — "Em 08/10/2026 10:15, Fulano <x@y> escreveu:".

    O texto citado já vai PROTEGIDO (links de acesso e códigos fora).
    """
    quando = gravar._utc(email.message.received_at) or datetime.now(UTC)
    quem = email.de_nome or email.de or "o remetente"
    if email.de_nome and email.de:
        quem = f"{email.de_nome} <{email.de}>"
    cabeca = f"Em {quando.astimezone(_SP):%d/%m/%Y %H:%M}, {quem} escreveu:"
    protegido = codigos.proteger("", texto.texto_novo(email.texto)).texto.strip()
    linhas = protegido.splitlines()[:CITACAO_MAX_LINHAS]
    citado = "\n".join(linhas)[:CITACAO_MAX_CARACTERES]
    if len(protegido) > len(citado):
        citado = citado.rstrip() + "\n[…]"
    return cabeca, citado


async def assinatura(session: AsyncSession, conversa: AtendimentoConversa) -> str:
    """A assinatura da loja (o nome dela) ou da marca (o cadastro: site, Zap, e-mail SAC)."""
    mail = ponte.dados_mail(conversa)
    if conversa.plataforma == PLATAFORMA_SITE:
        slug = mail.get("marca")
        marca = (
            (await session.execute(select(Marca).where(Marca.slug == slug))).scalar_one_or_none()
            if slug
            else None
        )
        if marca is not None:
            from app.services.email_marca import variaveis_da_marca

            company = await session.get(Company, marca.company_id) if marca.company_id else None
            v = variaveis_da_marca(marca, company)
            linhas = ["Atenciosamente,", f"Equipe {v['marca']}"]
            if v["site"]:
                linhas.append(v["site"])
            if v["whatsapp"]:
                linhas.append(f"WhatsApp {v['whatsapp']}")
            if v["email_sac"]:
                linhas.append(v["email_sac"])
            return "\n".join(linhas)
        return "Atenciosamente,\n" + (mail.get("marca_nome") or conversa.conta or "Equipe")
    integration = (
        await session.get(Integration, conversa.integration_id) if conversa.integration_id else None
    )
    nome = await lojas.nome_da_loja(session, integration) if integration is not None else ""
    return ASSINATURA_LOJA.format(loja=nome or conversa.conta or "Equipe")


async def de_alias(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    meta: MailMessageMeta,
    *,
    aliases: set[str],
    recebidos: list[str],
    estrito: bool,
    principal: str | None = None,
) -> str | None:
    """O endereço que envia: o que RECEBEU (RF5); no site, o do TIPO do chamado (RF6).

    O do tipo (sac@/atacado@/duvidas@ da marca, `marca_emails`) só vale se é
    da caixa — e, com o remetente estrito ligado, se também recebeu o e-mail
    (a Central recusaria outro). Nunca o endereço principal no lugar do que
    recebeu; `principal` (a caixa `empresa`): nem quando foi ELE que recebeu
    (o login da conta do Tuta nunca vai para o cliente).
    """
    aliases = {a for a in aliases if a != principal}
    recebidos = [a for a in recebidos if a != principal]
    if conversa.plataforma == PLATAFORMA_SITE and meta.marca_id is not None:
        tipo = ponte.dados_mail(conversa).get("caixa") or meta.tipo_caixa
        if tipo:
            achado = enderecos.normalizar(
                await session.scalar(
                    select(MarcaEmail.email)
                    .where(MarcaEmail.marca_id == meta.marca_id, MarcaEmail.tipo == tipo)
                    .limit(1)
                )
            )
            if achado and achado in aliases and (not estrito or achado in recebidos):
                return achado
    if meta.alias_recebido and meta.alias_recebido in aliases:
        if not estrito or meta.alias_recebido in recebidos:
            return meta.alias_recebido
    return recebidos[0] if recebidos else None


# ── A prévia e as travas ──────────────────────────────────────────────────


async def previa(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    mail_message_id: UUID | None = None,
    origem: str = ORIGEM_HUMANO,
) -> Previa:
    """A resposta montada + TODAS as travas de agora (sem o texto). Não grava nada."""
    from app.services.atendimento import enviar

    p = Previa()
    if not get_settings().atendimento_envio_ativo:
        p.bloqueios.append(
            Bloqueio(
                enviar.RECUSA_ENVIO_DESLIGADO,
                "O envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO).",
            )
        )
    if origem == ORIGEM_IA:
        p.bloqueios.append(Bloqueio(enviar.RECUSA_AUTO_DESLIGADO, MOTIVO_IA))
    if conversa.situacao == CONVERSA_BLOQUEADA:
        p.bloqueios.append(
            Bloqueio(
                enviar.RECUSA_CONVERSA_BLOQUEADA,
                conversa.bloqueio_motivo or "A conversa está bloqueada.",
            )
        )
    achado = await email_a_responder(session, conversa, mail_message_id)
    if achado is None:
        p.bloqueios.append(Bloqueio(RECUSA_SEM_EMAIL, MOTIVO_SEM_EMAIL))
        return p
    meta, message = achado
    mailbox = await session.get(MailMailbox, message.mailbox_id)
    if mailbox is None:
        p.bloqueios.append(Bloqueio(RECUSA_SEM_EMAIL, MOTIVO_SEM_EMAIL))
        return p
    cfg = await config_caixa.config_da_caixa(session, mailbox.id)
    p.meta, p.message, p.mailbox, p.cfg = meta, message, mailbox, cfg
    p.modo = cfg.envio_modo if cfg.empresa else "caixa"
    email = ponte.decifrar(message)
    principal, aliases = ponte.aliases_da_caixa(mailbox)
    envelope = mail_central.reply_envelope(mailbox, message, strict=cfg.remetente_estrito)
    recebidos = list(envelope["receiving_aliases"])
    p.de = await de_alias(
        session,
        conversa,
        meta,
        aliases=aliases,
        recebidos=recebidos,
        estrito=cfg.remetente_estrito,
        principal=principal if cfg.empresa else None,
    )
    so_principal = cfg.empresa and not p.de and principal in recebidos
    # O formulário do site (DE um endereço nosso): o cliente vem do corpo, aqui.
    if conversa.plataforma == PLATAFORMA_SITE and email.de in aliases:
        p.formulario = True
        p.para = enderecos.email_do_formulario(texto.legivel(email.texto), aliases) or None
    else:
        p.para = enderecos.normalizar(envelope["to"]) or None
    p.tag = meta.protocolo if conversa.plataforma == PLATAFORMA_SITE else None
    p.assunto = assunto_da_resposta(codigos.proteger(email.assunto, "").assunto, p.tag)
    p.assinatura = await assinatura(session, conversa)
    p.citacao_cabeca, p.citacao = citacao(email)
    if meta.tuta_id:
        lista, _, elemento = meta.tuta_id.partition("/")
        p.abrir_no_tuta = LINK_TUTA.format(lista=lista, elemento=elemento)

    if meta.suspeito:
        p.bloqueios.append(Bloqueio(RECUSA_SUSPEITO, MOTIVO_SUSPEITO))
    if meta.finalidade == FINALIDADE_VENDAS:
        p.bloqueios.append(Bloqueio(RECUSA_HISTORICO, MOTIVO_HISTORICO))
    if conversa.plataforma == "amazon":
        p.bloqueios.append(Bloqueio(RECUSA_AMAZON, MOTIVO_AMAZON))
    if meta.motivo == SEM_LOJA_ENCAMINHADO:
        p.bloqueios.append(Bloqueio(RECUSA_ENCAMINHADO, MOTIVO_ENCAMINHADO))
    if so_principal:
        p.bloqueios.append(Bloqueio(RECUSA_SO_PRINCIPAL, MOTIVO_SO_PRINCIPAL))
    elif not p.de:
        p.bloqueios.append(Bloqueio(RECUSA_SEM_ALIAS, MOTIVO_SEM_ALIAS))
    if not p.para:
        if p.formulario:
            p.bloqueios.append(Bloqueio(RECUSA_PARA_NOSSO, MOTIVO_PARA_NOSSO))
        else:
            p.bloqueios.append(Bloqueio(RECUSA_SEM_DESTINATARIO, MOTIVO_SEM_DESTINATARIO))
    elif not p.formulario and p.para in aliases:
        p.bloqueios.append(Bloqueio(RECUSA_PARA_NOSSO, MOTIVO_PARA_NOSSO))
    if not mailbox.send_enabled:
        p.bloqueios.append(Bloqueio(RECUSA_CAIXA_SEM_ENVIO, MOTIVO_CAIXA_SEM_ENVIO))
    elif not mail_central.send_ready(mailbox):
        p.bloqueios.append(Bloqueio(RECUSA_MAC_DESCONECTADO, MOTIVO_MAC_DESCONECTADO))
    if p.para:
        trava = await config_caixa.trava_de_envio(session, cfg, p.para)
        if trava and trava != "sending_disabled":  # o freio já está acima
            codigo, frase = MOTIVO_TRAVA.get(trava, (trava, trava))
            p.bloqueios.append(Bloqueio(codigo, frase))
    if p.para and (enderecos.nao_responde(p.para) or enderecos.e_aviso(p.para)):
        p.bloqueios.append(
            Bloqueio(
                RECUSA_NAO_RESPONDE,
                MOTIVO_NAO_RESPONDE.format(endereco=p.para),
                confirmavel=True,
            )
        )
    crua = await resposta_pela_caixa(session, message.id, conversa.id)
    if crua is not None:
        quando = gravar._utc(crua.created_at) or datetime.now(UTC)
        p.bloqueios.append(
            Bloqueio(
                RECUSA_JA_RESPONDIDO,
                MOTIVO_JA_RESPONDIDO.format(
                    quando=f"{quando.astimezone(_SP):%d/%m %H:%M}",
                    status=_STATUS_DA_CRUA.get(crua.status, crua.status),
                ),
                confirmavel=True,
            )
        )
    if cfg.empresa and cfg.envio_modo != "real":
        p.avisos.append("Modo de teste: só os endereços de teste desta caixa recebem.")
    if meta.codigo_mascarado:
        p.avisos.append("O e-mail tinha código de verificação: a citação vai com ele mascarado.")
    return p


async def resposta_pela_caixa(
    session: AsyncSession, message_id: UUID, conversa_id: UUID
) -> MailOutbox | None:
    """A resposta ao MESMO e-mail dada fora desta conversa (a caixa crua da Central),
    que saiu ou ainda pode sair — a mais nova. A que comprovadamente não saiu
    (`failed`) não conta; a da própria conversa também não."""
    return (
        await session.execute(
            select(MailOutbox)
            .outerjoin(MailOutboxMeta, MailOutboxMeta.outbox_id == MailOutbox.id)
            .where(
                MailOutbox.message_id == message_id,
                MailOutbox.status.in_(("sent", "queued", "leased", "uncertain")),
                or_(
                    MailOutboxMeta.outbox_id.is_(None),
                    MailOutboxMeta.conversa_id.is_(None),
                    MailOutboxMeta.conversa_id != conversa_id,
                ),
            )
            .order_by(MailOutbox.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def primeira_trava(p: Previa, *, confirmar_nao_responde: bool) -> Bloqueio | None:
    for b in p.bloqueios:
        if b.confirmavel and confirmar_nao_responde:
            continue
        return b
    return None


async def motivo_sem_envio(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    origem: str = ORIGEM_HUMANO,
    mail_message_id: UUID | None = None,
    confirmar_nao_responde: bool = False,
):
    """A recusa que o envio daria AGORA (sem olhar o texto); None = pode."""
    from app.services.atendimento.enviar import EnvioRecusado

    p = await previa(session, conversa, mail_message_id=mail_message_id, origem=origem)
    trava = primeira_trava(p, confirmar_nao_responde=confirmar_nao_responde)
    return EnvioRecusado(trava.codigo, trava.texto) if trava else None


def preparar_texto(texto_pessoa: str | None) -> str:
    """Normaliza e valida o texto da pessoa; levanta `texto_invalido` com os motivos."""
    from app.services.atendimento.enviar import RECUSA_TEXTO_INVALIDO, EnvioRecusado

    normalizado = "\n".join(
        linha.rstrip() for linha in (texto_pessoa or "").replace("\r\n", "\n").split("\n")
    )
    normalizado = gravar.sem_nul(normalizado).strip()
    motivos: list[str] = []
    if not normalizado:
        motivos.append(MOTIVO_TEXTO_VAZIO)
    if len(normalizado) > RESPOSTA_MAX_CARACTERES:
        motivos.append(MOTIVO_TEXTO_LONGO.format(n=RESPOSTA_MAX_CARACTERES))
    lacuna = _RE_LACUNA.search(normalizado)
    if lacuna:
        motivos.append(MOTIVO_LACUNA.format(lacuna=lacuna.group(0)))
    if motivos:
        raise EnvioRecusado(RECUSA_TEXTO_INVALIDO, motivos)
    return normalizado


# ── Enfileirar ────────────────────────────────────────────────────────────


async def _travar_email_e_caixa(
    session: AsyncSession, message: MailMessage
) -> tuple[MailMessage, MailMailbox]:
    """As mesmas travas da rota /reply da Central: o e-mail e depois a caixa (FOR UPDATE).

    Dois cliques ao mesmo tempo não chegam juntos ao índice de "1 resposta
    viva" (que daria 500); o lease do Mac espera a gravação acabar.
    """
    message = await session.scalar(
        select(MailMessage)
        .where(MailMessage.id == message.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    mailbox = await session.scalar(
        select(MailMailbox)
        .where(MailMailbox.id == message.mailbox_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return message, mailbox


async def enfileirar_resposta(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    texto_pessoa: str,
    *,
    user: User | None,
    rascunho_id: UUID | None = None,
    origem: str = ORIGEM_HUMANO,
    ultima_vista_id: UUID | None = None,
    confirmar: bool = False,
    confirmar_nao_responde: bool = False,
    mail_message_id: UUID | None = None,
) -> AtendimentoMensagem:
    """A resposta na FILA da Central: travas, mensagem `enviando` + job + ligação.

    Commita uma vez (tudo junto). Recusa = `EnvioRecusado` (nada gravado; a
    trava da conversa solta na hora). Quem envia é o Mac.
    """
    from app.services.atendimento import enviar

    if user is None or origem == ORIGEM_IA:
        raise enviar.EnvioRecusado(enviar.RECUSA_AUTO_DESLIGADO, MOTIVO_IA)
    ponto = await session.begin_nested()
    try:
        await enviar.travar_conversa(session, conversa)
        recusa = await motivo_sem_envio(
            session,
            conversa,
            origem=origem,
            mail_message_id=mail_message_id,
            confirmar_nao_responde=confirmar_nao_responde,
        )
        if recusa is not None:
            raise recusa
        normalizado = preparar_texto(texto_pessoa)
        p = await previa(session, conversa, mail_message_id=mail_message_id, origem=origem)
        if p.meta is None or p.message is None or not p.de or not p.para:
            raise enviar.EnvioRecusado(RECUSA_SEM_EMAIL, MOTIVO_SEM_EMAIL)
        rascunho = await enviar._rascunho_da_conversa(session, conversa, rascunho_id)
        await enviar._conferir_repetido(session, conversa, normalizado)
        if ultima_vista_id is not None and not confirmar:
            await enviar._conferir_mudou(session, conversa, ultima_vista_id)
        await enviar.aposentar_envios_presos(session, conversa_id=conversa.id)
        mensagem = AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id=None,
            autor=AUTOR_LOJA,
            origem=origem,
            autor_user_id=user.id,
            tipo="texto",
            texto=normalizado,
            anexos=[],
            enviada_em=None,
            status=MSG_ENVIANDO,
            rascunho_id=rascunho.id if rascunho is not None else None,
            payload={
                "mail_envio": {
                    "de": p.de,
                    "para": p.para,
                    "assunto": p.assunto,
                    "message_id": str(p.message.id),
                    "modo": p.modo,
                }
            },
        )
        await session.flush()
        try:
            async with session.begin_nested():
                session.add(mensagem)
                await session.flush()
        except IntegrityError as e:
            if not is_unique_violation(e):
                raise
            raise enviar.EnvioRecusado(
                enviar.RECUSA_ENVIO_EM_ANDAMENTO, "Há uma resposta sendo enviada nesta conversa."
            ) from e
        message, mailbox = await _travar_email_e_caixa(session, p.message)
        try:
            job = await mail_central.queue_reply(
                session,
                mailbox,
                message,
                user,
                ReplyIn(
                    request_id=mensagem.id,
                    text=p.corpo_texto(normalizado),
                    from_address=p.de,
                ),
                form_recipient=p.para if p.formulario else None,
                subject_tag=p.tag,
            )
        except mail_central.MailError as erro:
            if erro.code == "reply_already_pending":
                raise enviar.EnvioRecusado(
                    enviar.RECUSA_ENVIO_EM_ANDAMENTO,
                    "Já há uma resposta na fila para este e-mail (ou um envio a conferir no Tuta).",
                ) from None
            codigo, frase = _DA_CENTRAL.get(erro.code, (erro.code, "A Central recusou o envio."))
            raise enviar.EnvioRecusado(codigo, frase) from None
        mensagem.payload = {
            **mensagem.payload,
            "mail_envio": {**mensagem.payload["mail_envio"], "job_id": str(job.id)},
        }
        # O `queue_reply` já pode ter criado a ligação (caixa `empresa`:
        # `caixa_empresa`); a resposta pela conversa a completa.
        liga = await session.get(MailOutboxMeta, job.id)
        if liga is None:
            liga = MailOutboxMeta(outbox_id=job.id)
            session.add(liga)
        liga.atendimento_mensagem_id = mensagem.id
        liga.conversa_id = conversa.id
        liga.origem = "conversa"
        liga.status_visto = job.status
        await session.flush()
    except BaseException:
        await ponto.rollback()
        raise
    await ponto.commit()
    gravar.recalcular(conversa, [mensagem])
    await session.commit()
    logger.info(
        "mail_resposta_na_fila",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        job_id=str(job.id),
        modo=p.modo,
    )
    return mensagem


# ── A fila viva (o `enviar` não aposenta) e a conferência por pessoa ──────


def fila_viva_existe():
    """SQL: a mensagem tem job VIVO na fila da Central (não é "enviando parado").

    Vivo = `queued`, ou `leased` há menos que o LEASE_TIMEOUT da Central (15
    min). O `leased` mais velho é de um Mac que sumiu no meio: a mensagem
    deixa de estar "em voo", o `aposentar_envios_presos` a põe em `revisar` e
    o "Conferir" da conversa resolve o job (nunca reenvia).
    """
    queued, leased = JOBS_VIVOS
    return (
        exists()
        .where(
            MailOutboxMeta.atendimento_mensagem_id == AtendimentoMensagem.id,
            MailOutboxMeta.outbox_id == MailOutbox.id,
            or_(
                MailOutbox.status == queued,
                and_(
                    MailOutbox.status == leased,
                    MailOutbox.leased_at >= datetime.now(UTC) - mail_central.LEASE_TIMEOUT,
                ),
            ),
        )
        .correlate(AtendimentoMensagem)
    )


async def ligacao_da_mensagem(
    session: AsyncSession, mensagem_id: UUID
) -> tuple[MailOutboxMeta, MailOutbox] | None:
    linha = (
        await session.execute(
            select(MailOutboxMeta, MailOutbox)
            .join(MailOutbox, MailOutbox.id == MailOutboxMeta.outbox_id)
            .where(MailOutboxMeta.atendimento_mensagem_id == mensagem_id)
        )
    ).first()
    return (linha[0], linha[1]) if linha is not None else None


def registrar_resolucao(liga: MailOutboxMeta, user: User, *, saiu: bool) -> None:
    liga.resolvido_por = user.id
    liga.resolvido_em = datetime.now(UTC)
    liga.resolucao = "saiu" if saiu else "nao_saiu"


async def resolver_job(
    session: AsyncSession, job: MailOutbox, user: User, *, saiu: bool
) -> MailOutboxMeta:
    """Uma pessoa conferiu no Tuta: o job incerto sai da trava (nunca reenvia).

    Usa a regra DA Central (`resolve_uncertain`: só `uncertain` ou lease
    vencido) e grava quem resolveu na ligação (`mail_outbox_meta`); a
    mensagem da conversa, se houver, segue o status novo. Não commita.
    """
    job = await session.scalar(
        select(MailOutbox)
        .where(MailOutbox.id == job.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    mail_central.resolve_uncertain(job, user, sent=saiu)
    liga = await registrar_na_ligacao(session, job, user, saiu=saiu)
    await ponte.aplicar_status(session, liga, job)
    return liga


async def registrar_na_ligacao(
    session: AsyncSession, job: MailOutbox, user: User, *, saiu: bool
) -> MailOutboxMeta:
    """Quem resolveu o job (cria a ligação `caixa` quando o job não veio de conversa)."""
    liga = await session.get(MailOutboxMeta, job.id)
    if liga is None:
        liga = MailOutboxMeta(outbox_id=job.id, origem="caixa")
        session.add(liga)
    registrar_resolucao(liga, user, saiu=saiu)
    await session.flush()
    return liga


async def apos_resolver_na_caixa(
    session: AsyncSession, job: MailOutbox, user: User, *, saiu: bool
) -> None:
    """A rota DA Central (`POST /api/mail/outbox/{job}/resolve`) resolveu um job:
    grava quem resolveu e, se o job veio de uma conversa, passa o status para a
    mensagem dela (a mesma coisa que a ponte faria na volta seguinte). Não commita."""
    liga = await registrar_na_ligacao(session, job, user, saiu=saiu)
    await ponte.aplicar_status(session, liga, job)


async def registrar_recibo_tardio(session: AsyncSession, job: MailOutbox, *, status: str) -> None:
    """O Mac mandou o recibo DEPOIS de uma pessoa resolver o job (a Central
    devolve o 409 `receipt_conflict` de sempre e não muda nada nela).

    Aqui fica guardado À PARTE (`mail_outbox_meta.recibo_tardio`) e, se o job
    veio de uma conversa, uma nota de sistema avisa nela: "saiu depois da
    conferência" (o cliente pode ter recebido duas respostas). Só ids e
    status. Não commita (a rota do agente commita antes do 409).
    """
    recibo = decrypt_json(job.receipt_enc) if job.receipt_enc else {}
    if not isinstance(recibo, dict) or not recibo.get("resolved_by"):
        return  # não foi pessoa que resolveu: o 409 é o conflito normal do contrato
    agora = datetime.now(UTC)
    liga = await session.get(MailOutboxMeta, job.id)
    if liga is None:
        liga = MailOutboxMeta(outbox_id=job.id, origem="caixa")
        session.add(liga)
    if liga.recibo_tardio is not None:
        return  # o mesmo recibo repetido (o Mac tenta de novo): uma nota só
    liga.recibo_tardio = status[:16]
    liga.recibo_tardio_em = agora
    await session.flush()
    if liga.atendimento_mensagem_id is None or status != "sent":
        return
    mensagem = await session.get(AtendimentoMensagem, liga.atendimento_mensagem_id)
    conversa = await session.get(AtendimentoConversa, mensagem.conversa_id) if mensagem else None
    if mensagem is None or conversa is None:
        return
    await gravar.travar_linha(session, conversa)
    mensagem.payload = {
        **(mensagem.payload or {}),
        "mail_envio": {
            **((mensagem.payload or {}).get("mail_envio") or {}),
            "recibo_tardio": status,
        },
    }
    feita_em = gravar._utc(mensagem.created_at) or agora
    await gravar.gravar_mensagem(
        session,
        conversa,
        externo_id=f"mail-recibo-tardio:{job.id}",
        autor=AUTOR_SISTEMA,
        texto=(
            f"O Mac confirmou DEPOIS da conferência que a resposta de "
            f"{feita_em.astimezone(_SP):%d/%m %H:%M} saiu. Se alguém respondeu de novo, o "
            "cliente pode ter recebido as duas: confira no Tuta."
        ),
        enviada_em=agora,
        payload={ponte.CHAVE: {"recibo_tardio": True}},
    )
    await gravar.recalcular_conversa(session, conversa)
