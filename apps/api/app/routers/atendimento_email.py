"""Pós-venda › Atendimento: o E-MAIL das lojas dentro do /atendimento (RF5 e RF6, 08/10/2026).

A Central de e-mail do outro dev (`/api/mail/*`) é a CAIXA INTEIRA: só o dono
dela e os admins. Aqui fica só o que a PONTE levou ao /atendimento
(`services/mail_atendimento/ponte.py`), com as regras do /atendimento (a
trava `_so_admin`, o `pode_ver`/`pode_mexer` e o escopo por equipe):

  quem VÊ o /atendimento (pela conversa, no escopo da equipe):
    GET   …/contagens               — os números das entradas da aba E-mail que
                                      vêm das CONVERSAS de e-mail (13/10/2026:
                                      o e-mail saiu da Caixa): falta responder e
                                      os chamados abertos dos sites (por tipo);
                                      as listas vêm de GET /api/atendimento/
                                      conversas?caixa=email
    GET   …/conversas/{id}/emails   — os cartões dos e-mails da conversa (texto
                                      inteiro PROTEGIDO: links de acesso e
                                      códigos fora; nunca HTML) + os outros
                                      chamados abertos da cliente (RF6, agrupar)
    GET   …/anexos/{id}             — o anexo (octet-stream, nosniff, CSP sandbox)
    GET   …/conversas/{id}/emails-da-venda — o resumo dos e-mails da venda (a
                                      conversa e a família dela): caixa → loja,
                                      pastas e quantos (o bloco do painel ④);
                                      no chat, só os números e o id das
                                      conversas de e-mail da venda
    GET   …/venda/{id}              — metadados dos e-mails da venda exata do
                                      chat, por plataforma + loja + pedido
    GET   …/venda/{id}/emails/{mid} — cartão protegido individual, revalidando
                                      vínculo com a venda; também para Leitor
    GET   …/conversas/{id}/previa   — como a resposta vai sair e as travas de agora
    GET   …/saude                   — as caixas da EMPRESA (a privada só para o
                                      dono/admin) — e, para quem mexe, as lojas
                                      sem caixa lida. Desde 27/10/2026 só de quem
                                      vê a aba E-mail (é só dela)

  A ABA E-MAIL é só de ADMIN (27/10/2026, o dono): quem não é admin vê e-mail
  SÓ pela barra de lojas da Caixa — GET …/contagens?por_loja=true (os números
  de cada loja nos GRUPOS de `GRUPOS_EMAIL_NA_BARRA_DE_LOJAS`: as pastas
  Problemas · Envios · Devolução, e o porquê da caixa vazia), a lista da loja
  (GET /api/atendimento/conversas?caixa=email com a loja e `grupo_email`), os
  E-MAILS da loja no grupo (GET …/lojas/emails, …/lojas/emails/{id} e
  …/lojas/anexos/{id}: todo e-mail das pastas, também o que a ponte segurou
  como segurança) e a conversa de e-mail (as rotas "pela conversa" acima).
  As rotas que só a aba usa recusam quem não é admin com 403
  `aba_email_so_admin` (`_aba_inteira`). Desde 28/10/2026 a aba também é de
  quem tem a permissão `atendimento_email` view (`acesso.ve_aba_email`; o
  Eduardo: "bote o user marrocos no davinci pra poder ver tambem a aba de
  emails") — só VER: as filas e as ações continuam de quem mexe.

  só quem MEXE (as filas podem ter e-mail de qualquer loja e de endereço interno):
    GET   …/filas                   — as contagens
    GET   …/emails?fila=…           — sem_loja | sem_vinculo | suspeito | resumo | erro |
                                      informativo (`&pasta=<id>`: as linhas de UM item)
    GET   …/informativos            — os itens das pastas INFORMATIVAS (21/10/2026):
                                      um por pasta, com total, último e as lojas
    GET   …/emails/{id}             — o cartão de um e-mail da fila
    POST  …/emails/{id}/loja        — escolher a loja (e-mail sem loja, 1 clique)
    POST  …/emails/{id}/vincular    — ligar a um pedido/conversa (e-mail sem vínculo)
    POST  …/conversas/{id}/vincular — o mesmo, pela conversa de e-mail aberta na
                                      aba E-mail (13/10/2026)
    POST  …/emails/{id}/ignorar     — tirar da fila (fica registrado)
    POST  …/emails/{id}/reprocessar — de novo pela ponte (sem loja, erro)
    POST  …/reprocessar             — a fila "sem loja" inteira (cadastro corrigido)
    POST  …/conversas/{id}/agrupar  — juntar dois chamados do site (RF6; fica o
                                      mais antigo)
    POST  …/conversas/{id}/nao-agrupar — "Não agrupar": a sugestão do par some
                                      (lembrado nos dois, com quem e quando)
    GET   …/envios                  — as respostas pela fila da Central ("revisar envio")
    POST  …/envios/{job}/resolver   — saiu / não saiu (nunca reenvia)
    GET   …/pastas · PATCH …/pastas/{id} · GET …/regras · PUT …/regras
                                      (a CATEGORIA da pasta criada — Venda · Chamado
                                      (Pós-venda) · Informativa · Site (como hoje) —
                                      21/10/2026: só admin escolhe; a regra só sugere)

(… = /api/atendimento/email)

E-MAIL × CHAT (13/10/2026, `services/atendimento/separa_email.py`): os
cartões e o resumo "E-mails da venda" são só da conversa de E-MAIL — numa
conversa de chat (o aviso que a ponte gravava lá até 13/10) os cartões
voltam vazios, como a própria conversa, que não mostra mais o e-mail. O
resumo legado "E-mails da venda" do chat diz só QUANTOS e-mails a venda tem nas conversas
de e-mail, quantas esperam resposta e o id delas (o link "abrir na aba
E-mail"): nunca caixa, pasta, assunto, remetente nem texto. O botão do
cabeçalho abre uma lista restrita à venda e lê cada cartão no modal, sem
incluir o corpo na linha do tempo do chat nem abrir casos de outras vendas.

O e-mail de SEGURANÇA (código, senha, acesso), o PRIVADO (caixa só com
aliases de loja) e o da pasta que só se conta NUNCA aparecem aqui: só na
caixa inteira. A resposta sai pelo caminho único:
POST /api/atendimento/conversas/{id}/responder (com `mail_message_id` e
`confirmar_nao_responde`). Texto de e-mail nunca vai para o log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, case, exists, func, literal, not_, or_, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db import get_session
from app.deps.team_scope import TeamScope
from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    MailAttachment,
    MailMailbox,
    MailMessage,
    MailOutbox,
    Marca,
    StoreInfo,
    User,
    UserRole,
)
from app.models.mail_atendimento import (
    AtendimentoRegraPastaEmail,
    MailFolder,
    MailMailboxSettings,
    MailMessageMeta,
    MailOutboxMeta,
)
from app.routers.atendimento import (
    ABA_EMAIL_SO_ADMIN,
    TIPOS_CHAMADO,
    _clausula_escopo,
    _conversa_ou_404,
    _nomes_das_lojas,
    _so_admin,
    _tipo_do_chamado,
    _tipo_do_email_sql,
    _tipo_email_sql,
    _travar_ou_409,
    _view,
)
from app.security.cipher import decrypt_bytes, decrypt_json
from app.services import mail_central
from app.services.atendimento import abas, acesso, gravar, separa_email
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_SISTEMA,
    CANAL_EMAIL,
    CONVERSA_FECHADA,
    FONTE_TUTA,
    PLATAFORMA_SITE,
)
from app.services.atendimento.escopo import escopo_do_atendimento
from app.services.mail_atendimento import (
    administracao,
    barra_lojas,
    chamados,
    classificacao,
    codigos,
    html_caixa,
    ponte,
    regras,
    responder,
    rotear,
    saude,
    texto,
    venda,
)
from app.services.mail_atendimento import constantes as mail_constantes
from app.services.mail_atendimento import pastas as pastas_mail
from app.services.mail_atendimento.constantes import (
    CATEGORIA_CHAMADO,
    CATEGORIA_VENDA,
    CATEGORIAS_PASTA,
    ESTADO_ERRO,
    ESTADO_GRAVADO,
    ESTADO_IGNORADO,
    ESTADO_INFORMATIVO,
    ESTADO_RESUMO,
    ESTADO_SEGURANCA,
    ESTADO_SEM_LOJA,
    ESTADO_SEM_VINCULO,
    ESTADOS_COM_TEXTO,
    FILA_SUSPEITO,
    FILAS,
    FINALIDADES_DESTAQUE,
    IGNORADO_CHAMADO_SEM_CHAMADO,
    IGNORADO_CHAMADO_SEM_PEDIDO,
    IGNORADO_POR_PESSOA,
    IGNORADOS_RELIDOS,
    LINK_TUTA,
    MOTIVOS_SEM_LOJA,
    PLATAFORMAS_PASTA,
    RELER_DIAS,
    ROTULO_CATEGORIA,
    ROTULO_FINALIDADE,
    ROTULO_TIPO_CAIXA,
)
from app.services.mail_atendimento.suspeito import FRASES as FRASES_SUSPEITO

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/atendimento/email", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)
Session = Annotated[AsyncSession, Depends(get_session)]
Leitor = Annotated[User, Depends(_view)]

POR_PAGINA = 50
_PREFIXO = "/api/atendimento/email"
_SP = ZoneInfo("America/Sao_Paulo")
# As LEITURAS que só quem mexe faz: a exceção ao "quem só lê lê tudo" do
# /atendimento (crítica de 08/10: as filas podem ter e-mail de qualquer loja e
# de endereço interno). `tests/test_atendimento_so_leitura.py` confere a lista.
LEITURAS_SO_DE_QUEM_MEXE = frozenset(
    {
        ("GET", f"{_PREFIXO}/filas"),
        ("GET", f"{_PREFIXO}/emails"),
        ("GET", f"{_PREFIXO}/emails/{{message_id}}"),
        ("GET", f"{_PREFIXO}/informativos"),
        ("GET", f"{_PREFIXO}/envios"),
        ("GET", f"{_PREFIXO}/pastas"),
        ("GET", f"{_PREFIXO}/regras"),
    }
)
SO_QUEM_MEXE = {
    "code": "atendimento_so_quem_mexe",
    "detail": "Só quem cuida do Atendimento vê as filas de e-mail (podem ter e-mail interno).",
}


def _mexe(user: User) -> None:
    """As filas, as pastas e as ações: só quem MEXE no /atendimento."""
    if not acesso.pode_mexer(user):
        raise HTTPException(403, detail=SO_QUEM_MEXE)


def _admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, detail={"code": "so_admin"})


# A ABA E-MAIL INTEIRA é só de admin (27/10/2026) — o Eduardo: quem não é admin
# vê e-mail SÓ pela barra de lojas da Caixa (o ampliável de cada loja). Desde
# 28/10/2026 também de quem tem a permissão `atendimento_email` view
# (`acesso.ve_aba_email`), sem virar admin. As rotas que só a aba usa recusam
# os outros com este 403; as que a barra e a conversa de e-mail usam (as
# contagens por loja, a lista da loja em GET /conversas?caixa=email, a conversa
# e os cartões dela) continuam para quem vê a loja, como antes.
ABA_SO_ADMIN = ABA_EMAIL_SO_ADMIN
# As LEITURAS que só a aba E-mail faz e que, até 27/10/2026, quem só lê também
# fazia (as outras já eram só de quem mexe — `LEITURAS_SO_DE_QUEM_MEXE`, e quem
# mexe é sempre admin). O nome é de 27/10 (de 28 a 30/10 a permissão
# `atendimento_email` view também lia; desde 30/10, de novo só admin).
# `tests/test_atendimento_so_leitura.py` confere a lista.
LEITURAS_SO_DE_ADMIN = frozenset({("GET", f"{_PREFIXO}/saude")})


def _aba_inteira(user: User) -> None:
    """A rota é só da aba E-mail: admin ou quem tem `atendimento_email` view
    (`acesso.ve_aba_email`, a mesma conta do `useCan` do web). Só ver: as
    ações seguem com `_mexe`."""
    if not acesso.ve_aba_email(user):
        raise HTTPException(403, detail=ABA_SO_ADMIN)


def _no_escopo(scope: TeamScope, integration_id: UUID | None) -> bool:
    return scope.unrestricted or (
        integration_id is not None and integration_id in scope.integration_ids
    )


def _escopo_do_email(scope: TeamScope, integ, ficha):
    """WHERE da loja de um e-mail no escopo da pessoa; None = sem filtro.

    A loja do e-mail é a integração ou — sem ela — a FICHA que a ponte ligou
    (30/10/2026: a Shopee "luno", as Temu, o ML "eron2" só têm ficha). Quem não
    é admin vê as das lojas dele (`escopo_do_atendimento`); sem loja, nada."""
    if scope.unrestricted:
        return None
    ou = []
    if scope.integration_ids:
        ou.append(integ.in_(sorted(scope.integration_ids)))
    if scope.store_info_ids:
        ou.append(and_(integ.is_(None), ficha.in_(sorted(scope.store_info_ids))))
    if not ou:
        return literal(False)
    return ou[0] if len(ou) == 1 else or_(*ou)


async def _meta_ou_404(
    session: AsyncSession, message_id: UUID, user: User, *, estados: tuple[str, ...]
) -> tuple[MailMessageMeta, MailMessage]:
    """O e-mail da fila (para quem mexe). Segurança, privado, só contar: 404."""
    meta = await session.get(MailMessageMeta, message_id)
    message = await session.get(MailMessage, message_id) if meta is not None else None
    if meta is None or message is None or meta.estado not in estados:
        raise HTTPException(404, detail={"code": "email_nao_encontrado"})
    scope = await escopo_do_atendimento(session, user)
    if not _no_escopo(scope, meta.integration_id):
        raise HTTPException(404, detail={"code": "email_nao_encontrado"})
    await administracao.exigir_mensagem(session, user, message_id)
    return meta, message


def _link_tuta(meta: MailMessageMeta) -> str | None:
    if not meta.tuta_id:
        return None
    lista, _, elemento = meta.tuta_id.partition("/")
    return LINK_TUTA.format(lista=lista, elemento=elemento)


async def _cartao(
    session: AsyncSession,
    meta: MailMessageMeta,
    message: MailMessage,
    nomes: dict[UUID, str],
    *,
    com_texto: bool,
) -> dict[str, Any]:
    """O cartão de um e-mail: o que a equipe pode ver (texto PROTEGIDO, nunca HTML)."""
    pasta = await session.get(MailFolder, meta.folder_id) if meta.folder_id else None
    loja = nomes.get(meta.integration_id) if meta.integration_id else None
    if loja is None and meta.store_info_id is not None:
        # A loja SEM integração (a ficha): o nome do cadastro de Lojas.
        ficha = await session.get(StoreInfo, meta.store_info_id)
        loja = (" ".join((ficha.account_name or "").split()) or None) if ficha else None
    mensagem = (
        await session.get(AtendimentoMensagem, meta.mensagem_id) if meta.mensagem_id else None
    )
    tipos = classificacao.tipos_de_meta(meta, mensagem.payload if mensagem else None)
    saida: dict[str, Any] = {
        "id": str(meta.message_id),
        "estado": meta.estado,
        "direcao": message.direction,
        "recebido_em": gravar._utc(message.received_at),
        "pasta": pasta.nome if pasta is not None else None,
        "plataforma": meta.plataforma,
        "finalidade": meta.finalidade,
        "finalidade_rotulo": ROTULO_FINALIDADE.get(meta.finalidade or "", meta.finalidade),
        "destaque": meta.finalidade in FINALIDADES_DESTAQUE,
        "so_historico": meta.finalidade == "vendas",
        "alias": meta.alias_recebido,
        "loja": loja,
        "integration_id": str(meta.integration_id) if meta.integration_id else None,
        "store_info_id": str(meta.store_info_id) if meta.store_info_id else None,
        "sem_integracao": meta.store_info_id is not None and meta.integration_id is None,
        "motivo": meta.motivo,
        "motivo_texto": MOTIVOS_SEM_LOJA.get(meta.motivo or ""),
        "sugestoes": meta.sugestoes or [],
        "suspeito": meta.suspeito,
        "suspeito_motivos": [FRASES_SUSPEITO.get(m, m) for m in (meta.suspeito_motivos or [])],
        "conversa_id": str(meta.conversa_id) if meta.conversa_id else None,
        "pedido": meta.pedido_marketplace,
        "pedidos_citados": meta.pedidos_citados or [],
        "protocolo": meta.protocolo,
        **tipos,
        "tipo_caixa": meta.tipo_caixa,
        "tipo_caixa_rotulo": ROTULO_TIPO_CAIXA.get(meta.tipo_caixa or ""),
        "vinculado_por": meta.vinculado_por,
        "alertas": meta.alertas or [],
        "codigo_mascarado": meta.codigo_mascarado,
        "abrir_no_tuta": _link_tuta(meta),
    }
    if not com_texto or meta.estado not in ESTADOS_COM_TEXTO:
        return saida
    email = ponte.decifrar(message)
    protegido = codigos.proteger(email.assunto, texto.legivel(email.texto))
    anexos = (
        await session.scalars(select(MailAttachment).where(MailAttachment.message_id == message.id))
    ).all()
    saida.update(
        {
            "de": email.de,
            "de_nome": email.de_nome,
            # O Reply-To só quando é OUTRO endereço (o sinal de golpe).
            "reply_to": [r for r in email.reply_to if r != email.de],
            "assunto": protegido.assunto,
            "texto": protegido.texto,
            "links_removidos": protegido.links_removidos,
            "codigo_mascarado": meta.codigo_mascarado or protegido.codigo_mascarado,
            "anexos": [
                {
                    "id": str(a.id),
                    "tamanho": a.size,
                    **{
                        k: v
                        for k, v in decrypt_json(a.metadata_enc).items()
                        if k in ("filename", "content_type")
                    },
                }
                for a in anexos
            ],
        }
    )
    return saida


# ── As filas (só quem mexe) ───────────────────────────────────────────────


def _tipo_da_meta_sql():
    m = MailMessageMeta
    tipo = (
        select(AtendimentoMensagem.payload[ponte.CHAVE]["tipo_email"].astext)
        .where(AtendimentoMensagem.id == m.mensagem_id)
        .scalar_subquery()
    )
    return case(
        # A linha informativa e o e-mail da Pós-venda não têm mensagem: o tipo é
        # o da categoria da pasta (21/10/2026; `classificacao.tipos_de_meta`).
        (m.estado == ESTADO_INFORMATIVO, literal("informativo")),
        (
            or_(
                m.chamado_id.is_not(None),
                and_(
                    m.estado == ESTADO_IGNORADO,
                    m.motivo.in_((IGNORADO_CHAMADO_SEM_PEDIDO, IGNORADO_CHAMADO_SEM_CHAMADO)),
                ),
            ),
            literal("chamado"),
        ),
        else_=_tipo_email_sql(tipo, m.plataforma, m.finalidade, func.nullif(m.protocolo, "")),
    )


def _da_loja(
    integration_id: UUID | None, store_info_id: UUID | None, fichas: set[UUID] | None = None
):
    """WHERE da loja de um e-mail (`mail_message_meta`): a integração (mais o que
    a ponte ligou só às `fichas` dela — `_fichas_da_integracao`), ou a ficha SEM
    integração (a mesma chave das contagens por loja)."""
    if integration_id is not None:
        cond = MailMessageMeta.integration_id == integration_id
        if fichas:
            cond = or_(
                cond,
                and_(
                    MailMessageMeta.integration_id.is_(None),
                    MailMessageMeta.store_info_id.in_(fichas),
                ),
            )
        return cond
    return and_(
        MailMessageMeta.integration_id.is_(None),
        MailMessageMeta.store_info_id == store_info_id,
    )


async def _fichas_da_integracao(session: AsyncSession, integration_id: UUID) -> set[UUID]:
    """As fichas cuja integração (o FK ativo, senão o par nome + plataforma) é
    ESTA (27/10/2026): o e-mail que a ponte ligou só à ficha — a Magalu "vortan"
    com o FK vazio — entra na loja da integração, na barra e na lista dela."""
    return {
        f for f, i in (await rotear.integracoes_das_fichas(session)).items() if i == integration_id
    }


async def _pastas_informativas(session: AsyncSession, user: User) -> list[MailFolder]:
    """As pastas INFORMATIVAS das caixas que a pessoa vê — a régua de GET
    /informativos (a pasta criada com a categoria que vale, nas caixas de
    `_caixas_das_pastas`). A barra de lojas conta e lista as linhas por ela
    (27/10/2026): a pasta que deixou de ser informativa, ou a caixa cuja ponte
    desligou, some dos dois lugares juntos."""
    visiveis = await _caixas_das_pastas(session, user)
    lista = (
        await session.execute(
            select(MailFolder)
            .where(
                MailFolder.mailbox_id.in_(visiveis),
                MailFolder.categoria == ESTADO_INFORMATIVO,
            )
            .order_by(MailFolder.nome)
        )
    ).scalars()
    return [p for p in lista if regras.categoria_que_vale(p) is not None]


def _filtro_da_fila(fila: str):
    if fila == FILA_SUSPEITO:
        return (
            MailMessageMeta.suspeito.is_(True),
            MailMessageMeta.estado.in_(ESTADOS_COM_TEXTO),
        )
    if fila == ESTADO_SEM_VINCULO:
        return (
            MailMessageMeta.estado == fila,
            _tipo_da_meta_sql() == "chamado",
            func.nullif(MailMessageMeta.protocolo, "").is_(None),
        )
    return (MailMessageMeta.estado == fila,)


@router.get("/filas")
async def filas(session: Session, user: Leitor) -> dict[str, int]:
    _mexe(user)
    _aba_inteira(user)
    saida = {}
    for fila in FILAS:
        saida[fila] = int(
            await session.scalar(
                select(func.count()).where(
                    *_filtro_da_fila(fila),
                    administracao.filtro_mensagem(user, MailMessageMeta.message_id),
                )
            )
            or 0
        )
    saida["revisar_envio"] = int(
        await session.scalar(
            select(func.count())
            .select_from(MailOutboxMeta)
            .join(MailOutbox, MailOutbox.id == MailOutboxMeta.outbox_id)
            # Só a resposta de conversa (a mesma lista de GET /envios): o job da
            # caixa crua da empresa também tem ligação (`caixa_empresa`).
            .where(
                MailOutboxMeta.origem == "conversa",
                _a_conferir(),
                administracao.filtro_mensagem(user, MailOutbox.message_id),
            )
        )
        or 0
    )
    return saida


def _a_conferir():
    """O job que uma pessoa precisa conferir no Tuta: `uncertain`, ou `leased`
    há mais que o LEASE_TIMEOUT (o Mac sumiu no meio; nada sai duas vezes)."""
    return or_(
        MailOutbox.status == "uncertain",
        and_(
            MailOutbox.status == "leased",
            MailOutbox.leased_at < datetime.now(UTC) - mail_central.LEASE_TIMEOUT,
        ),
    )


@router.get("/emails")
async def listar(
    session: Session,
    user: Leitor,
    response: Response,
    fila: Annotated[str, Query()] = ESTADO_SEM_LOJA,
    antes_de: Annotated[datetime | None, Query()] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = POR_PAGINA,
    tipo_email: Annotated[str | None, Query(max_length=16)] = None,
    pasta: Annotated[UUID | None, Query()] = None,
    integration_id: Annotated[UUID | None, Query()] = None,
    store_info_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    """Uma fila: sem loja, sem vínculo, suspeitos, resumos, erro ou as linhas
    informativas (sem o texto). `pasta`: só os e-mails desta pasta — com
    `fila=informativo`, as linhas de UM item da aba E-mail (21/10/2026).

    `integration_id` / `store_info_id` (27/10/2026, o "ampliável" de e-mails
    da barra de lojas da Caixa): só os e-mails que a ponte ligou a ESTA loja
    (a integração, ou a ficha sem integração). Com a loja escolhida vale
    também o escopo da equipe (como nas contagens por loja): nunca mais do
    que a fila inteira já mostra."""
    _mexe(user)
    _aba_inteira(user)
    if fila not in FILAS:
        raise HTTPException(422, detail={"code": "fila_invalida"})
    if tipo_email and tipo_email not in ("informativo", "chamado", "venda"):
        raise HTTPException(422, detail={"code": "tipo_email_invalido"})
    if integration_id is not None and store_info_id is not None:
        raise HTTPException(422, detail={"code": "loja_ambigua"})
    q = (
        select(MailMessageMeta, MailMessage)
        .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
        .where(
            *_filtro_da_fila(fila),
            administracao.filtro_mensagem(user, MailMessageMeta.message_id),
        )
    )
    if tipo_email:
        q = q.where(_tipo_da_meta_sql() == tipo_email)
    if pasta is not None:
        q = q.where(MailMessageMeta.folder_id == pasta)
    if integration_id is not None or store_info_id is not None:
        fichas = (
            await _fichas_da_integracao(session, integration_id)
            if integration_id is not None
            else None
        )
        q = q.where(_da_loja(integration_id, store_info_id, fichas))
        scope = await escopo_do_atendimento(session, user)
        m = MailMessageMeta
        cond = _escopo_do_email(scope, m.integration_id, m.store_info_id)
        if cond is not None:
            q = q.where(cond)
        if fila == ESTADO_INFORMATIVO:
            # As linhas da loja: só as das pastas informativas que valem (as
            # mesmas do item Informativos da aba e do número da barra).
            pastas = [p.id for p in await _pastas_informativas(session, user)]
            q = q.where(MailMessageMeta.folder_id.in_(pastas))
    if antes_de is not None:
        q = q.where(MailMessage.received_at < antes_de)
    linhas = (
        await session.execute(
            q.order_by(MailMessage.received_at.desc(), MailMessage.id.desc()).limit(limite + 1)
        )
    ).all()
    tem_mais = len(linhas) > limite
    linhas = linhas[:limite]
    nomes = await _nomes_das_lojas(session, {m.integration_id for m, _ in linhas})
    itens = [await _cartao(session, m, msg, nomes, com_texto=False) for m, msg in linhas]
    # O assunto (protegido) ajuda a escolher a loja; o texto só no cartão.
    for item, (meta, msg) in zip(itens, linhas, strict=True):
        if meta.estado in ESTADOS_COM_TEXTO:
            email = ponte.decifrar(msg)
            item["assunto"] = codigos.proteger(email.assunto, "").assunto
            item["de"] = email.de
    response.headers["Cache-Control"] = "no-store"
    return {
        "itens": itens,
        "proximo": gravar._utc(linhas[-1][1].received_at).isoformat()
        if tem_mais and linhas
        else None,
    }


@router.get("/emails/{message_id}")
async def detalhe(
    message_id: UUID, session: Session, user: Leitor, response: Response
) -> dict[str, Any]:
    _mexe(user)
    _aba_inteira(user)
    meta, message = await _meta_ou_404(
        session, message_id, user, estados=(*ESTADOS_COM_TEXTO, ESTADO_ERRO)
    )
    nomes = await _nomes_das_lojas(session, {meta.integration_id})
    response.headers["Cache-Control"] = "no-store"
    return await _cartao(session, meta, message, nomes, com_texto=True)


class LojaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A ficha do cadastro de Lojas (as sugestões), a loja conectada OU — na
    # marca ambígua (o mesmo domínio em duas marcas) — a marca do site.
    store_info_id: UUID | None = None
    integration_id: UUID | None = None
    marca_id: UUID | None = None


@router.post("/emails/{message_id}/loja")
async def escolher_loja(
    message_id: UUID, body: LojaIn, session: Session, user: Leitor
) -> dict[str, Any]:
    """ "E-mail sem loja": a pessoa escolhe a loja; o e-mail entra pela ponte normal.

    A ficha SEM integração também vale (Temu, AliExpress, Magalu, a ficha que
    não casa com nenhuma integração): o e-mail entra na conversa da ficha. A
    integração da ficha: o FK ativo, senão o par (nome, plataforma).
    """
    _mexe(user)
    _aba_inteira(user)
    meta, _message = await _meta_ou_404(session, message_id, user, estados=(ESTADO_SEM_LOJA,))
    # O endereço principal da conta (o login do Tuta) nunca é "o alias" da loja.
    mailbox = await session.get(MailMailbox, meta.mailbox_id)
    principal = ponte.aliases_da_caixa(mailbox)[0] if mailbox is not None else None
    alias = meta.alias_recebido if meta.alias_recebido != principal else None
    if body.marca_id is not None:
        marca = await session.get(Marca, body.marca_id)
        if marca is None or not marca.ativo:
            raise HTTPException(404, detail={"code": "marca_nao_encontrada"})
        rota = rotear.Rota(
            alias=alias,
            plataforma=rotear.PLATAFORMA_SITE,
            marca_id=marca.id,
            marca_slug=marca.slug,
            marca_nome=marca.nome,
            tipo_caixa=meta.tipo_caixa,
        )
    elif body.store_info_id is not None:
        loja = await session.get(StoreInfo, body.store_info_id)
        if loja is None or loja.archived_at is not None:
            raise HTTPException(404, detail={"code": "loja_nao_encontrada"})
        integration_id, _pelo_par = await rotear.integracao_da_ficha(session, loja)
        rota = rotear.Rota(
            alias=alias,
            store_info_id=loja.id,
            integration_id=integration_id,
            plataforma=rotear.plataforma_da_ficha(loja.platform),
            loja_nome=" ".join((loja.account_name or "").split()) or None,
        )
    elif body.integration_id is not None:
        integ = await session.get(Integration, body.integration_id)
        if integ is None or integ.archived_at is not None:
            raise HTTPException(404, detail={"code": "loja_nao_encontrada"})
        rota = rotear.Rota(
            alias=alias,
            integration_id=integ.id,
            plataforma=str(getattr(integ.platform, "value", integ.platform) or "").lower(),
        )
    else:
        raise HTTPException(422, detail={"code": "loja_vazia"})
    estado = await ponte.reprocessar(session, message_id, rota_forcada=rota)
    if estado != ESTADO_SEM_LOJA:
        # Auditoria (RF5, "toda ação registra quem e quando"): no cartão do
        # e-mail e, se ele entrou numa conversa do e-mail, uma nota nela.
        novo = await session.get(MailMessageMeta, message_id)
        if novo is not None:
            await _registrar_pessoa(
                session,
                novo,
                user,
                codigo="loja_escolhida",
                frase=f"loja escolhida à mão por {_nome(user)}",
                nota="E-mail ligado a esta loja à mão (fila sem loja) por {quem}.",
            )
    await session.commit()
    logger.info(
        "mail_atendimento_loja_escolhida",
        message_id=str(message_id),
        estado=estado,
        user_id=str(user.id),
    )
    meta = await session.get(MailMessageMeta, message_id)
    message = await session.get(MailMessage, message_id)
    nomes = await _nomes_das_lojas(session, {meta.integration_id})
    return await _cartao(session, meta, message, nomes, com_texto=False)


class VincularIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pedido: str | None = Field(default=None, max_length=64)
    conversa_id: UUID | None = None


@router.post("/emails/{message_id}/vincular")
async def vincular(
    message_id: UUID, body: VincularIn, session: Session, user: Leitor
) -> dict[str, Any]:
    """ "E-mail sem vínculo": ligar a conversa do e-mail a um pedido (ou ao de outra conversa)."""
    _mexe(user)
    _aba_inteira(user)
    meta, _message = await _meta_ou_404(
        session, message_id, user, estados=(ESTADO_SEM_VINCULO, ESTADO_GRAVADO)
    )
    if meta.conversa_id is None:
        raise HTTPException(409, detail={"code": "email_sem_conversa"})
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, str(meta.conversa_id), scope)
    return await _vincular(session, meta, conversa, body, user, scope)


@router.post("/conversas/{conversa_id}/vincular")
async def vincular_conversa(
    conversa_id: str, body: VincularIn, session: Session, user: Leitor
) -> dict[str, Any]:
    """A conversa de e-mail ligada a um pedido PELA CONVERSA (13/10/2026).

    O e-mail saiu da Caixa: a aba E-mail abre a conversa de e-mail pelo id
    (das Filas, das Caixas) e o "vincular ao pedido" fica nela, sem precisar
    do id de um e-mail. O mesmo `vincular` (só quem mexe, só na conversa da
    ponte, a mesma loja), pelo e-mail mais novo da conversa.
    """
    _mexe(user)
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    meta = (
        await session.execute(
            select(MailMessageMeta)
            .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
            .where(
                MailMessageMeta.conversa_id == conversa.id,
                MailMessageMeta.estado.in_((ESTADO_SEM_VINCULO, ESTADO_GRAVADO)),
            )
            .order_by(MailMessage.received_at.desc(), MailMessage.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if meta is None:
        raise HTTPException(409, detail={"code": "conversa_sem_email"})
    return await _vincular(session, meta, conversa, body, user, scope)


async def _vincular(
    session: AsyncSession,
    meta: MailMessageMeta,
    conversa: AtendimentoConversa,
    body: VincularIn,
    user: User,
    scope: TeamScope,
) -> dict[str, Any]:
    """O vínculo ao pedido (a conversa da ponte; a do pedido, se já existe, recebe tudo)."""
    message_id = meta.message_id
    if not ponte.e_conversa_da_ponte(conversa):
        raise HTTPException(409, detail={"code": "nao_e_conversa_de_email"})
    pedido = (body.pedido or "").strip()
    if body.conversa_id is not None:
        alvo = await _conversa_ou_404(session, str(body.conversa_id), scope)
        mesma = (
            alvo.integration_id == conversa.integration_id
            if conversa.integration_id is not None
            else ponte.ficha_da_conversa(conversa) is not None
            and ponte.ficha_da_conversa(alvo) == ponte.ficha_da_conversa(conversa)
        )
        if not mesma:
            raise HTTPException(409, detail={"code": "outra_loja"})
        if not alvo.pedido_marketplace:
            raise HTTPException(409, detail={"code": "conversa_sem_pedido"})
        pedido = alvo.pedido_marketplace
    if not pedido:
        raise HTTPException(422, detail={"code": "pedido_vazio"})
    await _travar_ou_409(session, conversa, "conversa_ocupada")
    if conversa.pedido_marketplace and conversa.pedido_marketplace != pedido:
        raise HTTPException(
            409,
            detail={"code": "conversa_ja_tem_pedido", "pedido": conversa.pedido_marketplace},
        )
    # Já existe a conversa de e-mail DESTE pedido na loja: as mensagens vão para ela.
    destino = (
        (
            await session.execute(
                select(AtendimentoConversa).where(
                    *ponte.da_mesma_loja_sql(conversa),
                    AtendimentoConversa.canal == CANAL_EMAIL,
                    AtendimentoConversa.pedido_marketplace == pedido,
                    AtendimentoConversa.dados["fonte"].astext == FONTE_TUTA,
                    AtendimentoConversa.id != conversa.id,
                )
            )
        )
        .scalars()
        .first()
    )
    if destino is not None:
        await _travar_ou_409(session, destino, "conversa_ocupada")
        await venda.mover_conversa(session, conversa, destino)
        alvo_final = destino
    else:
        conversa.pedido_marketplace = pedido[:64]
        alvo_final = conversa
    await session.execute(
        update(MailMessageMeta)
        .where(MailMessageMeta.conversa_id.in_({conversa.id, alvo_final.id}))
        .values(conversa_id=alvo_final.id, pedido_marketplace=pedido[:64])
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(MailMessageMeta)
        .where(
            MailMessageMeta.conversa_id == alvo_final.id,
            MailMessageMeta.estado == ESTADO_SEM_VINCULO,
        )
        .values(estado=ESTADO_GRAVADO, vinculado_por="manual")
        .execution_options(synchronize_session=False)
    )
    agora = datetime.now(UTC)
    await gravar.gravar_mensagem(
        session,
        alvo_final,
        externo_id=f"mail-vinculo:{message_id}:{int(agora.timestamp())}",
        autor=AUTOR_SISTEMA,
        texto=f"E-mail vinculado ao pedido {pedido} por {user.name or user.email}.",
        enviada_em=agora,
        payload={ponte.CHAVE: {"vinculado": True}},
    )
    # A mesma auditoria no cartão do e-mail (quem e quando, também fora da conversa).
    await session.refresh(meta)
    await _registrar_pessoa(
        session,
        meta,
        user,
        codigo="vinculado_por_pessoa",
        frase=f"vinculado ao pedido {pedido} por {_nome(user)}",
    )
    await gravar._recalcular_etiqueta(
        session, alvo_final, motivo=gravar.MOTIVO_ETIQUETA_LEITURA, travar=False
    )
    await session.commit()
    logger.info("mail_atendimento_vinculado", message_id=str(message_id), user_id=str(user.id))
    return {"conversa_id": str(alvo_final.id), "pedido": pedido}


def _nome(user: User) -> str:
    return user.name or user.email


async def _registrar_pessoa(
    session: AsyncSession,
    meta: MailMessageMeta,
    user: User,
    *,
    codigo: str,
    frase: str,
    nota: str | None = None,
) -> None:
    """Quem fez e quando, no cartão do e-mail (`meta.alertas`: código, frase, o
    id da pessoa e a hora) — e, com `nota`, uma nota de sistema na conversa
    do e-mail (só na conversa da ponte). Não commita."""
    agora = datetime.now(UTC)
    quando = agora.astimezone(_SP).strftime("%d/%m/%Y %H:%M")
    meta.alertas = [
        *(meta.alertas or []),
        {
            "codigo": codigo,
            "texto": f"{frase} em {quando}"[:300],
            "por": str(user.id),
            "em": agora.isoformat(),
        },
    ]
    if nota is None or meta.conversa_id is None:
        return
    conversa = await session.get(AtendimentoConversa, meta.conversa_id)
    if conversa is None or not ponte.e_conversa_da_ponte(conversa):
        return
    await gravar.gravar_mensagem(
        session,
        conversa,
        externo_id=f"mail-{codigo}:{meta.message_id}:{int(agora.timestamp())}",
        autor=AUTOR_SISTEMA,
        texto=nota.format(quem=_nome(user)),
        enviada_em=agora,
        payload={ponte.CHAVE: {codigo: True}},
    )


@router.post("/emails/{message_id}/ignorar")
async def ignorar(message_id: UUID, session: Session, user: Leitor) -> dict[str, Any]:
    """Tira o e-mail da fila (não é de loja nenhuma). Fica registrado quem tirou."""
    _mexe(user)
    _aba_inteira(user)
    meta, _message = await _meta_ou_404(
        session, message_id, user, estados=(ESTADO_SEM_LOJA, ESTADO_RESUMO, ESTADO_ERRO)
    )
    meta.estado = ESTADO_IGNORADO
    meta.motivo = IGNORADO_POR_PESSOA
    await _registrar_pessoa(
        session, meta, user, codigo="ignorado_por_pessoa", frase=f"ignorado por {_nome(user)}"
    )
    await session.commit()
    logger.info("mail_atendimento_ignorado", message_id=str(message_id), user_id=str(user.id))
    return {"id": str(message_id), "estado": meta.estado}


@router.post("/emails/{message_id}/reprocessar")
async def reprocessar_um(message_id: UUID, session: Session, user: Leitor) -> dict[str, Any]:
    """De novo pela ponte (cadastro corrigido): só sem loja ou com erro."""
    _mexe(user)
    _aba_inteira(user)
    await _meta_ou_404(session, message_id, user, estados=(ESTADO_SEM_LOJA, ESTADO_ERRO))
    estado = await ponte.reprocessar(session, message_id)
    await session.commit()
    return {"id": str(message_id), "estado": estado}


@router.post("/reprocessar")
async def reprocessar_fila(session: Session, user: Leitor) -> dict[str, int]:
    """A fila "sem loja" (e os com erro) de novo pela ponte. Só admin que mexe."""
    _mexe(user)
    _admin(user)
    ids = list(
        (
            await session.execute(
                select(MailMessageMeta.message_id)
                .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
                .where(MailMessageMeta.estado.in_((ESTADO_SEM_LOJA, ESTADO_ERRO)))
                .order_by(MailMessage.received_at)
                .limit(500)
            )
        ).scalars()
    )
    resolvidos = 0
    for message_id in ids:
        try:
            estado = await ponte.reprocessar(session, message_id)
            await session.commit()
            if estado not in (ESTADO_SEM_LOJA, ESTADO_ERRO):
                resolvidos += 1
        except Exception as e:  # noqa: BLE001 — um e-mail não derruba os outros
            await session.rollback()
            logger.warning("mail_atendimento_reprocessar_falhou", erro=type(e).__name__)
    logger.info(
        "mail_atendimento_reprocessado", total=len(ids), resolvidos=resolvidos, user_id=str(user.id)
    )
    return {"total": len(ids), "resolvidos": resolvidos}


# ── As conversas de e-mail (quem vê) ─────────────────────────────────────


@router.get("/contagens")
async def contagens(
    session: Session,
    user: Leitor,
    response: Response,
    por_loja: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """Os números da aba E-mail que vêm das CONVERSAS de e-mail (13/10/2026).

    O e-mail saiu da Caixa (`separa_email`): o que falta responder e os
    chamados abertos dos sites (RF6, por tipo) são contados aqui, para as
    entradas "Falta responder" e "Chamados dos sites" da aba E-mail. Quem vê
    o /atendimento, no escopo da equipe — as mesmas conversas que ela via na
    Caixa. Uma consulta; só quando a aba E-mail pede (não é a lista quente).

    `por_loja` (27/10/2026): também os números POR LOJA nos GRUPOS da barra de
    lojas da Caixa (as pastas Problemas · Envios · Devolução —
    `GRUPOS_EMAIL_NA_BARRA_DE_LOJAS`, que vão em `grupos_na_barra`) e o porquê
    da caixa vazia (`_contagens_por_loja`). A MESMA consulta, agrupada: os
    números de cima saem da soma dos grupos. A barra é o único caminho de quem
    não vê a aba E-mail até o e-mail (a aba é de admin e de quem tem a
    permissão `atendimento_email` view): esta rota segue para quem vê o
    /atendimento.

    Rápida (27/10/2026, "otimiza pra abrir rápido"): o filtro de acesso é
    decidido uma vez por pedido (`administracao.visao_rapida`, o mesmo
    resultado) e a consulta roda sem JIT (o Postgres gastava ~4 s compilando).
    """
    c = AtendimentoConversa
    aberta = c.situacao != CONVERSA_FECHADA
    scope = await escopo_do_atendimento(session, user)
    cond = _clausula_escopo(scope, c.integration_id)
    response.headers["Cache-Control"] = "no-store"
    visao = await administracao.visao_rapida(session, user)
    await session.execute(sql_text("SET LOCAL jit = off"))
    if por_loja:
        return await _contagens_por_loja(session, user, scope, visao)
    chamado = and_(c.plataforma == PLATAFORMA_SITE, aberta)
    tipo = _tipo_do_chamado()
    tipo_email = _tipo_do_email_sql()
    q = select(
        func.count().filter(c.aguardando_resposta.is_(True)),
        func.count().filter(chamado),
        *[func.count().filter(chamado, tipo == t) for t in TIPOS_CHAMADO],
        *[func.count().filter(aberta, tipo_email == t) for t in classificacao.TIPOS_EMAIL],
    ).where(separa_email.conversa_de_email_sql(), visao.filtro_conversa())
    if cond is not None:
        q = q.where(cond)
    aguardando, chamados, *resto = (await session.execute(q)).one()
    por_tipo, por_tipo_email = resto[: len(TIPOS_CHAMADO)], resto[len(TIPOS_CHAMADO) :]
    return {
        "aguardando": int(aguardando or 0),
        "chamados": int(chamados or 0),
        "chamados_por_tipo": {t: int(n or 0) for t, n in zip(TIPOS_CHAMADO, por_tipo, strict=True)},
        "por_tipo_email": {
            t: int(n or 0) for t, n in zip(classificacao.TIPOS_EMAIL, por_tipo_email, strict=True)
        },
    }


# As plataformas da ficha que não são loja (o CD, a venda presencial).
_FICHAS_QUE_NAO_SAO_LOJA = frozenset({"interno", "presencial"})


def _conta_vazia() -> dict[str, int]:
    return {"emails": 0, "abertas": 0, "aguardando": 0, "total": 0}


# O e-mail que não é de loja nenhuma para a barra (o diagnóstico `emails_30d`).
_ESTADOS_FORA_DA_LOJA = ("novo", "privado", "interno", "sem_loja", "erro")


async def _contagens_por_loja(
    session: AsyncSession, user: User, scope: TeamScope, visao: administracao.VisaoRapida
) -> dict[str, Any]:
    """Os números de cima E os de CADA loja, por GRUPO (pasta) — a barra de lojas da Caixa.

    O Eduardo, 27/10: embaixo de cada loja as pastas Problemas, Envios e
    Devolução (`GRUPOS_EMAIL_NA_BARRA_DE_LOJAS`, o único lugar da escolha, que
    vai em `grupos_na_barra` com o status de cada pasta na ponte), cada uma
    com os e-mails só daquela loja; e TODA loja tem as três caixas, mesmo
    vazias — a vazia diz por quê. A caixa mostra TODO e-mail das pastas do
    grupo ligado à loja (`barra_lojas.email_da_caixa_sql`: a conversa, a linha
    informativa, o que a ponte segurou como segurança, o guardado; a loja da
    meta, senão a do índice); a conversa entra no grupo pela pasta do e-mail
    RECEBIDO mais recente dela.

    `por_loja` traz TODA loja que a pessoa vê: as integrações e as fichas SEM
    integração (Temu, AliExpress, a Shopee "atlas" sem API…) do escopo dela
    (desde 30/10/2026 as lojas da pessoa, `escopo_do_atendimento`; o admin vê
    todas), mesmo sem e-mail nenhum; arquivada só com e-mail. A loja é a que
    a PONTE deu ao e-mail: a integração da conversa ou — sem integração — a
    ficha (`dados.mail.store_info_id`); a ficha cuja integração existe (o FK
    ativo, senão o par nome + plataforma: `rotear.integracoes_das_fichas`)
    soma na loja da integração. O chamado do site (sem loja) não entra.

    Por loja: `alias` (o endereço dela para a ponte — `barra_lojas.alias_das_lojas`),
    `emails_30d` (os e-mails da loja nos últimos `DIAS_DA_BARRA_DE_LOJAS` dias,
    em qualquer pasta: o diagnóstico da caixa vazia) e, por grupo:
      • `emails`     — os e-mails da caixa nesses dias (o número da caixa; a
        lista de GET …/lojas/emails mostra os mesmos);
      • `abertas`    — as conversas de e-mail abertas do grupo;
      • `aguardando` — esperando resposta (a bolinha vermelha);
      • `total`      — as conversas do grupo, as fechadas também.

    UMA consulta agrupada para as conversas e uma para os e-mails, com a régua
    de cima (e da aba E-mail): só as conversas de e-mail (`separa_email`), o
    filtro de acesso da pessoa (`visao`: o mesmo de `filtro_conversa`, decidido
    uma vez) e o escopo da equipe. Os números de cima (`aguardando`,
    `chamados`, `chamados_por_tipo`, `por_tipo_email`) são a soma dos grupos —
    os mesmos do caminho sem `por_loja`. Só números, ids, códigos e o nome da
    loja: nada de assunto, remetente, endereço ou texto.
    """
    c = AtendimentoConversa
    pastas = await barra_lojas.pastas_da_barra(session)
    ultima = barra_lojas.ultima_pasta_recebida()
    ficha_txt = func.nullif(c.dados[separa_email.CHAVE_MAIL]["store_info_id"].astext, "")
    # Numa subconsulta: os CASE com literais agrupam pelo nome da coluna.
    base = (
        select(
            c.integration_id.label("integration_id"),
            case((c.integration_id.is_(None), ficha_txt), else_=None).label("ficha"),
            c.plataforma.label("plataforma"),
            _tipo_do_email_sql().label("tipo"),
            case((c.plataforma == PLATAFORMA_SITE, _tipo_do_chamado()), else_=None).label(
                "tipo_chamado"
            ),
            (c.situacao != CONVERSA_FECHADA).label("aberta"),
            c.aguardando_resposta.is_(True).label("esperando"),
            pastas.grupo_sql(ultima.c.folder_id).label("grupo"),
        )
        .select_from(c)
        .outerjoin(ultima, ultima.c.conversa_id == c.id)
        .where(separa_email.conversa_de_email_sql(), visao.filtro_conversa())
    )
    cond = _clausula_escopo(scope, c.integration_id)
    if cond is not None:
        base = base.where(cond)
    b = base.subquery()
    colunas = (
        b.c.integration_id,
        b.c.ficha,
        b.c.plataforma,
        b.c.tipo,
        b.c.tipo_chamado,
        b.c.aberta,
        b.c.esperando,
        b.c.grupo,
    )
    linhas = (await session.execute(select(*colunas, func.count()).group_by(*colunas))).all()

    aguardando = chamados = 0
    por_tipo_chamado = dict.fromkeys(TIPOS_CHAMADO, 0)
    por_tipo_email = dict.fromkeys(classificacao.TIPOS_EMAIL, 0)
    na_barra = barra_lojas.chaves()
    # A soma por loja: (integração, ficha) → {plataforma, grupos: grupo → números}.
    brutos: dict[tuple[UUID | None, UUID | None], dict[str, Any]] = {}

    def conta(integration_id, store_info_id, plataforma) -> dict[str, Any]:
        return brutos.setdefault(
            (integration_id, store_info_id),
            {"plataforma": plataforma, "emails_30d": 0, "grupos": {}},
        )

    for integration_id, ficha_id, plataforma, t, t_chamado, e_aberta, esperando, g, n in linhas:
        n = int(n or 0)
        e_aberta = e_aberta is True
        if esperando is True:
            aguardando += n
        if plataforma == PLATAFORMA_SITE and e_aberta:
            chamados += n
            if t_chamado in por_tipo_chamado:
                por_tipo_chamado[t_chamado] += n
        if e_aberta and t in por_tipo_email:
            por_tipo_email[t] += n
        store_info_id = None if integration_id is not None else _uuid(ficha_id)
        if (integration_id is None and store_info_id is None) or g not in na_barra:
            continue
        numeros = conta(integration_id, store_info_id, plataforma)["grupos"].setdefault(
            g, _conta_vazia()
        )
        numeros["total"] += n
        if e_aberta:
            numeros["abertas"] += n
        if esperando is True:
            numeros["aguardando"] += n

    # Os e-mails da loja nos últimos dias: em qualquer pasta (o diagnóstico) e,
    # por grupo, os que a caixa mostra (o número da caixa = a lista dela).
    m = MailMessageMeta
    i = barra_lojas.indice_alias()
    integ, ficha = barra_lojas.loja_do_email_sql(i, m)
    desde = datetime.now(UTC) - timedelta(days=mail_constantes.DIAS_DA_BARRA_DE_LOJAS)
    base_m = (
        select(
            integ.label("integration_id"),
            case((integ.is_(None), ficha), else_=None).label("ficha"),
            pastas.grupo_sql(m.folder_id).label("grupo"),
            and_(*barra_lojas.email_da_caixa_sql(pastas, None, m)).label("na_caixa"),
        )
        .join(MailMessage, MailMessage.id == m.message_id)
        .outerjoin(i, i.message_id == m.message_id)
        .where(
            MailMessage.received_at >= desde,
            or_(integ.is_not(None), ficha.is_not(None)),
            m.estado.notin_(_ESTADOS_FORA_DA_LOJA),
            visao.filtro_mensagem(m.message_id),
        )
    )
    cond_m = _escopo_do_email(scope, integ, ficha)
    if cond_m is not None:
        base_m = base_m.where(cond_m)
    bm = base_m.subquery()
    for integration_id, store_info_id, g, n, n_caixa in (
        await session.execute(
            select(
                bm.c.integration_id,
                bm.c.ficha,
                bm.c.grupo,
                func.count(),
                func.count().filter(bm.c.na_caixa.is_(True)),
            ).group_by(bm.c.integration_id, bm.c.ficha, bm.c.grupo)
        )
    ).all():
        item = conta(integration_id, store_info_id, None)
        item["emails_30d"] += int(n or 0)
        if g in na_barra and n_caixa:
            item["grupos"].setdefault(g, _conta_vazia())["emails"] += int(n_caixa or 0)

    # A ficha cuja integração existe soma na loja da integração (a régua da ponte).
    da_integracao = await rotear.integracoes_das_fichas(session)
    lojas: dict[tuple[UUID | None, UUID | None], dict[str, Any]] = {}

    def item_da_loja(integration_id, store_info_id, plataforma) -> dict[str, Any]:
        item = lojas.setdefault(
            (integration_id, store_info_id),
            {
                "integration_id": str(integration_id) if integration_id else None,
                "store_info_id": str(store_info_id) if store_info_id else None,
                "plataforma": plataforma,
                "loja": None,
                "alias": barra_lojas.ALIAS_SEM_CADASTRO,
                "emails_30d": 0,
                "grupos": {g: _conta_vazia() for g in na_barra},
            },
        )
        if plataforma and not item["plataforma"]:
            item["plataforma"] = plataforma
        return item

    for (integration_id, store_info_id), dados in brutos.items():
        if integration_id is None and store_info_id in da_integracao:
            integration_id, store_info_id = da_integracao[store_info_id], None
        item = item_da_loja(integration_id, store_info_id, dados["plataforma"])
        item["emails_30d"] += dados["emails_30d"]
        for g, numeros in dados["grupos"].items():
            for k, v in numeros.items():
                item["grupos"][g][k] += v

    # TODA loja da pessoa, mesmo sem e-mail (a caixa vazia diz por quê): as
    # integrações no escopo e — para quem vê todas as equipes — as fichas sem
    # integração. A arquivada só entra com e-mail (já está em `lojas`).
    integracoes = select(Integration.id, Integration.platform).where(
        Integration.archived_at.is_(None), Integration.platform != "bling"
    )
    if not scope.unrestricted:
        integracoes = integracoes.where(Integration.id.in_(sorted(scope.integration_ids or ())))
    for iid, plataforma in (await session.execute(integracoes)).all():
        item_da_loja(iid, None, str(getattr(plataforma, "value", plataforma) or ""))
    nomes_fichas: dict[UUID, str | None] = {}
    fichas = select(StoreInfo.id, StoreInfo.account_name, StoreInfo.platform, StoreInfo.archived_at)
    for fid, nome, plataforma, arquivada in (await session.execute(fichas)).all():
        nomes_fichas[fid] = " ".join((nome or "").split()) or None
        plat = rotear.plataforma_da_ficha(str(getattr(plataforma, "value", plataforma) or ""))
        if (None, fid) in lojas:
            # A ficha com e-mail (mesmo arquivada): a plataforma do cadastro, se faltar.
            lojas[(None, fid)]["plataforma"] = lojas[(None, fid)]["plataforma"] or plat
            continue
        if fid in da_integracao or arquivada is not None:
            continue
        if not scope.unrestricted and fid not in scope.store_info_ids:
            # 30/10/2026: quem não é admin vê a ficha SEM integração das lojas dele.
            continue
        if plat not in _FICHAS_QUE_NAO_SAO_LOJA:
            item_da_loja(None, fid, plat)
    # O NOME da loja: o da integração (o do Duoke, como na aba E-mail) ou o da ficha.
    nomes = await _nomes_das_lojas(session, {i for (i, _f) in lojas if i is not None})
    alias_integ, alias_ficha = await barra_lojas.alias_das_lojas(session)
    saida = []
    for (integration_id, store_info_id), item in lojas.items():
        if integration_id is not None:
            item["loja"] = nomes.get(integration_id)
            item["alias"] = alias_integ.get(integration_id, barra_lojas.ALIAS_SEM_CADASTRO)
        else:
            item["loja"] = nomes_fichas.get(store_info_id)
            item["alias"] = alias_ficha.get(store_info_id, barra_lojas.ALIAS_SEM_CADASTRO)
        if item["plataforma"]:
            saida.append(item)
    return {
        "aguardando": aguardando,
        "chamados": chamados,
        "chamados_por_tipo": por_tipo_chamado,
        "por_tipo_email": por_tipo_email,
        "sites_na_barra": await _contagens_dos_sites(session, scope, visao),
        "grupos_na_barra": [
            {
                "chave": g.chave,
                "rotulo": g.rotulo,
                "pastas": list(g.pastas),
                "status": pastas.status.get(g.chave, barra_lojas.SEM_PASTA),
            }
            for g in barra_lojas.grupos()
        ],
        "dias": mail_constantes.DIAS_DA_BARRA_DE_LOJAS,
        "por_loja": sorted(
            saida,
            key=lambda i: (
                i["plataforma"] or "",
                (i["loja"] or "").casefold(),
                i["integration_id"] or "",
                i["store_info_id"] or "",
            ),
        ),
    }


async def _contagens_dos_sites(
    session: AsyncSession, scope: TeamScope, visao: administracao.VisaoRapida
) -> list[dict[str, Any]]:
    """As caixas SAC e Admin de cada SITE na barra de lojas (27/10/2026).

    O Eduardo, 27/10: a linha do site (Uranyx, Charlots — `externo_ref`
    "site:<site>") com o mesmo ampliável das lojas e DUAS caixas, separadas pelo
    endereço que RECEBEU (`barra_lojas.caixa_do_alias`: sac@ = SAC, o resto =
    Admin). Por caixa, como nas lojas: `emails` (os e-mails da pasta do site nos
    últimos `DIAS_DA_BARRA_DE_LOJAS` dias — `barra_lojas.email_do_site_sql`, a
    mesma régua da lista), e as conversas de e-mail cujo RECEBIDO mais recente
    está na pasta do site, pela caixa dele: `abertas`, `aguardando` (a bolinha)
    e `total`. `status`: o da pasta do site na ponte (o porquê da caixa vazia).
    Só para quem vê todas as equipes (o chamado do site não tem loja): os outros
    recebem a lista vazia. Só números e códigos.

    `so_email` (27/10/2026: Makisa e 7buyers): o site sem a linha do carrinho
    no atendimento — a barra desenha uma linha só de e-mail. O e-mail que o SQL
    não separa (a pasta sem categoria guarda sem o endereço que recebeu; o
    guardado sem a régua de segurança) é contado pelo e-mail decifrado
    (`_classes_no_site`, lembrado), com a mesma régua da lista."""
    if not scope.unrestricted:
        return []
    pastas = await barra_lojas.pastas_dos_sites(session)
    lista = barra_lojas.sites()
    chaves_caixa = [k for k, _r in barra_lojas.caixas_do_site()]
    # `nao_lidas` (27/10/2026): a bolinha vermelha do site — a conversa não lida
    # (`_nao_lida_sql`); `aguardando` continua o "falta responder".
    numeros = {
        st.chave: {k: {**_conta_vazia(), "nao_lidas": 0} for k in chaves_caixa} for st in lista
    }
    todas = pastas.ids()
    if todas:
        c = AtendimentoConversa
        u = barra_lojas.ultima_pasta_recebida()
        q = (
            select(
                pastas.site_sql(u.c.folder_id).label("site"),
                barra_lojas.caixa_do_alias_sql(u.c.alias).label("caixa"),
                (c.situacao != CONVERSA_FECHADA).label("aberta"),
                c.aguardando_resposta.is_(True).label("esperando"),
                _nao_lida_sql(c).label("nao_lida"),
            )
            .select_from(c)
            .join(u, u.c.conversa_id == c.id)
            .where(
                u.c.folder_id.in_(todas),
                separa_email.conversa_de_email_sql(),
                visao.filtro_conversa(),
            )
        )
        cond = _clausula_escopo(scope, c.integration_id)
        if cond is not None:
            q = q.where(cond)
        sq = q.subquery()
        cols = (sq.c.site, sq.c.caixa, sq.c.aberta, sq.c.esperando, sq.c.nao_lida)
        for site, caixa, aberta, esperando, nao_lida, n in (
            await session.execute(select(*cols, func.count()).group_by(*cols))
        ).all():
            alvo = numeros.get(site, {}).get(caixa)
            if alvo is None:
                continue
            alvo["total"] += int(n or 0)
            if aberta is True:
                alvo["abertas"] += int(n or 0)
            if esperando is True:
                alvo["aguardando"] += int(n or 0)
            if nao_lida is True:
                alvo["nao_lidas"] += int(n or 0)
        m = MailMessageMeta
        desde = datetime.now(UTC) - timedelta(days=mail_constantes.DIAS_DA_BARRA_DE_LOJAS)
        precisa = barra_lojas.precisa_ler_no_site_sql(m)
        do_site = (
            *barra_lojas.email_do_site_sql(pastas, None, None, m),
            MailMessage.received_at >= desde,
            visao.filtro_mensagem(m.message_id),
        )
        qm = (
            select(
                pastas.site_sql(m.folder_id).label("site"),
                barra_lojas.caixa_do_alias_sql(m.alias_recebido).label("caixa"),
            )
            .join(MailMessage, MailMessage.id == m.message_id)
            .where(*do_site, not_(precisa))
        ).subquery()
        for site, caixa, n in (
            await session.execute(
                select(qm.c.site, qm.c.caixa, func.count()).group_by(qm.c.site, qm.c.caixa)
            )
        ).all():
            alvo = numeros.get(site, {}).get(caixa)
            if alvo is not None:
                alvo["emails"] += int(n or 0)
        # O que o SQL não separa (sem o endereço que recebeu, ou guardado sem a
        # régua de segurança): pelo e-mail decifrado, lembrado por e-mail.
        lidas = (
            await session.execute(
                select(
                    pastas.site_sql(m.folder_id),
                    m.message_id,
                    MailMessage.mailbox_id,
                    m.estado,
                    m.alias_recebido,
                )
                .join(MailMessage, MailMessage.id == m.message_id)
                .where(*do_site, precisa)
            )
        ).all()
        classes = await _classes_no_site(session, [tuple(r[1:]) for r in lidas])
        for site, mid, *_r in lidas:
            caixa, escondido = classes.get(mid, (None, True))
            alvo = numeros.get(site, {}).get(caixa)
            if alvo is not None and not escondido:
                alvo["emails"] += 1
    return [
        {
            "site": st.chave,
            "externo_ref": st.externo_ref,
            "nome": st.nome,
            "pastas": list(st.pastas),
            # Makisa e 7buyers (27/10/2026): sem a linha do site no atendimento —
            # a barra desenha uma linha só de e-mail com as caixas.
            "so_email": st.so_email,
            "status": pastas.status.get(st.chave, barra_lojas.SEM_PASTA),
            "caixas": [{"chave": k, "rotulo": r} for k, r in barra_lojas.caixas_do_site()],
            "emails_30d": sum(g["emails"] for g in numeros[st.chave].values()),
            "grupos": numeros[st.chave],
        }
        for st in lista
    ]


async def _emails_do_site(
    session: AsyncSession,
    user: User,
    *,
    site: str | None,
    caixa: str | None,
    message_id: UUID | None = None,
):
    """A consulta dos e-mails da caixa de um SITE (ou de um e-mail de qualquer site):
    `barra_lojas.email_do_site_sql`, o filtro de acesso da pessoa e a janela. Quem
    não vê todas as equipes: nada (o chamado do site não tem loja). As colunas são
    as da caixa da loja (a meta, o e-mail, a integração e a ficha da meta, a conversa)."""
    scope = await escopo_do_atendimento(session, user)
    pastas = await barra_lojas.pastas_dos_sites(session)
    visao = await administracao.visao_rapida(session, user)
    m = MailMessageMeta
    c = AtendimentoConversa
    q = (
        select(m, MailMessage, m.integration_id.label("integ"), m.store_info_id.label("ficha"), c)
        .join(MailMessage, MailMessage.id == m.message_id)
        .outerjoin(c, c.id == m.conversa_id)
        .where(
            *barra_lojas.email_do_site_sql(pastas, site, caixa, m),
            visao.filtro_mensagem(m.message_id),
        )
    )
    if not scope.unrestricted:
        q = q.where(literal(False))
    if message_id is not None:
        q = q.where(m.message_id == message_id)
    else:
        desde = datetime.now(UTC) - timedelta(days=mail_constantes.DIAS_DA_BARRA_DE_LOJAS)
        q = q.where(MailMessage.received_at >= desde)
    return q


# (e-mail, estado) → (caixa, esconder): o e-mail da pasta do site que o SQL não
# separa (`barra_lojas.precisa_ler_no_site_sql`). Nunca muda para o mesmo e-mail
# no mesmo estado: lembrado no processo (a contagem é lida a cada volta da tela).
_CLASSES_NO_SITE: dict[tuple[UUID, str], tuple[str, bool]] = {}
_CLASSES_NO_SITE_MAX = 20_000


async def _classes_no_site(
    session: AsyncSession, linhas: list[tuple[UUID, UUID, str | None, str | None]]
) -> dict[UUID, tuple[str, bool]]:
    """(message_id, mailbox_id, estado, alias) → {message_id: (caixa, escondido)}.

    A caixa: a do alias gravado; sem ele, a do 1º endereço da caixa que recebeu
    (`barra_lojas.caixa_do_email`). Escondido: `barra_lojas.escondido_no_site`
    (a régua estrita da segurança). Só decifra o que ainda não foi lembrado."""
    saida: dict[UUID, tuple[str, bool]] = {}
    faltam = []
    for mid, mailbox_id, estado, alias in linhas:
        lembrada = _CLASSES_NO_SITE.get((mid, estado or ""))
        if lembrada is not None:
            saida[mid] = lembrada
        else:
            faltam.append((mid, mailbox_id, estado, alias))
    if not faltam:
        return saida
    mensagens = {
        mm.id: mm
        for mm in (
            await session.execute(
                select(MailMessage).where(MailMessage.id.in_([f[0] for f in faltam]))
            )
        ).scalars()
    }
    aliases: dict[UUID, set[str]] = {}
    for mid, mailbox_id, estado, alias in faltam:
        message = mensagens.get(mid)
        if message is None:
            continue
        email = ponte.decifrar(message)
        if alias:
            caixa = barra_lojas.caixa_do_alias(alias)
        else:
            if mailbox_id not in aliases:
                mailbox = await session.get(MailMailbox, mailbox_id)
                aliases[mailbox_id] = ponte.aliases_da_caixa(mailbox)[1] if mailbox else set()
            caixa = barra_lojas.caixa_do_email(email.recebeu, aliases[mailbox_id])
        escondido = barra_lojas.escondido_no_site(estado, email.assunto, texto.legivel(email.texto))
        if len(_CLASSES_NO_SITE) >= _CLASSES_NO_SITE_MAX:
            _CLASSES_NO_SITE.clear()
        _CLASSES_NO_SITE[(mid, estado or "")] = saida[mid] = (caixa, escondido)
    return saida


async def _fica_no_site(session: AsyncSession, linhas: list, caixa: str | None) -> list:
    """As linhas da consulta do site (meta, e-mail, …) que a caixa mostra: o que o SQL
    não separa passa por `_classes_no_site` (a caixa certa, sem o código)."""
    precisam = [
        (meta.message_id, message.mailbox_id, meta.estado, meta.alias_recebido)
        for meta, message, *_r in linhas
        if meta.alias_recebido is None or meta.estado in barra_lojas.ESTADOS_SEM_A_REGUA
    ]
    if not precisam:
        return list(linhas)
    classes = await _classes_no_site(session, precisam)
    saida = []
    for linha in linhas:
        meta = linha[0]
        if meta.alias_recebido is None or meta.estado in barra_lojas.ESTADOS_SEM_A_REGUA:
            da_linha, escondido = classes.get(meta.message_id, (None, True))
            if escondido or (caixa is not None and da_linha != caixa):
                continue
        saida.append(linha)
    return saida


def _uuid(valor: Any) -> UUID | None:
    """O id da ficha que veio do JSON da conversa (legado malformado = None)."""
    if valor is None:
        return None
    if isinstance(valor, UUID):
        return valor
    try:
        return UUID(str(valor))
    except ValueError:
        return None


# ── Os E-MAILS de uma loja num grupo da barra (quem vê) ───────────────────


async def _emails_da_caixa(
    session: AsyncSession,
    user: User,
    *,
    grupo: str | None,
    integration_id: UUID | None = None,
    store_info_id: UUID | None = None,
    message_id: UUID | None = None,
):
    """A consulta dos e-mails da caixa (uma loja num grupo, ou um e-mail de qualquer
    caixa): a régua de `barra_lojas.email_da_caixa_sql`, a loja (a da meta, senão
    a do índice), o filtro de acesso da pessoa, o escopo da equipe e a janela."""
    pastas = await barra_lojas.pastas_da_barra(session)
    visao = await administracao.visao_rapida(session, user)
    scope = await escopo_do_atendimento(session, user)
    m = MailMessageMeta
    i = barra_lojas.indice_alias()
    integ, ficha = barra_lojas.loja_do_email_sql(i, m)
    c = AtendimentoConversa
    q = (
        select(m, MailMessage, integ.label("integ"), ficha.label("ficha"), c)
        .join(MailMessage, MailMessage.id == m.message_id)
        .outerjoin(i, i.message_id == m.message_id)
        .outerjoin(c, c.id == m.conversa_id)
        .where(
            *barra_lojas.email_da_caixa_sql(pastas, grupo, m),
            or_(integ.is_not(None), ficha.is_not(None)),
            visao.filtro_mensagem(m.message_id),
        )
    )
    if message_id is not None:
        q = q.where(m.message_id == message_id)
    else:
        desde = datetime.now(UTC) - timedelta(days=mail_constantes.DIAS_DA_BARRA_DE_LOJAS)
        q = q.where(MailMessage.received_at >= desde)
    if integration_id is not None:
        fichas = await _fichas_da_integracao(session, integration_id)
        q = q.where(
            or_(
                integ == integration_id,
                and_(integ.is_(None), ficha.in_(sorted(fichas))) if fichas else literal(False),
            )
        )
    elif store_info_id is not None:
        q = q.where(integ.is_(None), ficha == store_info_id)
    cond = _escopo_do_email(scope, integ, ficha)
    if cond is not None:
        q = q.where(cond)
    return q


async def _linha_da_caixa(
    session: AsyncSession, meta: MailMessageMeta, message: MailMessage, integ, ficha, conversa
) -> dict[str, Any]:
    """Uma linha da caixa: sem o texto — o assunto, o remetente, a pasta e a conversa."""
    pasta = await session.get(MailFolder, meta.folder_id) if meta.folder_id else None
    email = ponte.decifrar(message)
    assunto, _texto, _n = barra_lojas.texto_da_caixa(email.assunto, "")
    return {
        "id": str(meta.message_id),
        "estado": meta.estado,
        "recebido_em": gravar._utc(message.received_at),
        "pasta": pasta.nome if pasta is not None else None,
        "assunto": assunto,
        "de": email.de,
        "de_nome": email.de_nome,
        "integration_id": str(integ) if integ else None,
        "store_info_id": str(ficha) if ficha and not integ else None,
        "seguranca": meta.estado == ESTADO_SEGURANCA,
        "anexos": int(message.attachment_count or 0),
        # O e-mail de uma conversa abre a conversa (com a resposta e o vínculo).
        "conversa_id": str(conversa.id) if conversa is not None else None,
        "aguardando_resposta": bool(conversa.aguardando_resposta) if conversa else False,
        "situacao": conversa.situacao if conversa is not None else None,
        "comprador_nome": conversa.comprador_nome if conversa is not None else None,
    }


@router.get("/lojas/emails")
async def emails_da_caixa(
    session: Session,
    user: Leitor,
    response: Response,
    grupo: Annotated[str, Query(max_length=32)],
    integration_id: Annotated[UUID | None, Query()] = None,
    store_info_id: Annotated[UUID | None, Query()] = None,
    site: Annotated[str | None, Query(max_length=32)] = None,
    antes_de: Annotated[datetime | None, Query()] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = POR_PAGINA,
) -> dict[str, Any]:
    """Os E-MAILS de UMA loja num GRUPO da barra de lojas (27/10/2026), o mais novo primeiro.

    TODO e-mail das pastas do grupo ligado à loja (`barra_lojas.email_da_caixa_sql`):
    a conversa de e-mail, a linha informativa, o que a ponte segurou como
    segurança e o que ela guardou sem ligar — o duplicado, uma vez só. Quem vê
    o /atendimento (como a lista da loja em GET /conversas?caixa=email) — só a
    loja escolhida, no escopo da equipe e com o filtro de acesso da pessoa; só
    os últimos `DIAS_DA_BARRA_DE_LOJAS` dias (o número da caixa é o mesmo). Sem
    o texto: o assunto (sem link de acesso), o remetente e a conversa; o e-mail
    inteiro em GET …/lojas/emails/{id}.

    `site` (27/10/2026): a caixa de um SITE (`grupo` = `sac` ou `admin`, a
    caixa do endereço que recebeu) — os e-mails da pasta do site
    (`barra_lojas.email_do_site_sql`), só para quem vê todas as equipes (403
    `email_do_site_fora_do_escopo` para os outros)."""
    grupo = (grupo or "").strip().lower()
    site = (site or "").strip().lower() or None
    if site is not None:
        if site not in {st.chave for st in barra_lojas.sites()}:
            raise HTTPException(422, detail={"code": "site_invalido"})
        if grupo not in {k for k, _r in barra_lojas.caixas_do_site()}:
            raise HTTPException(422, detail={"code": "grupo_email_invalido"})
        if integration_id is not None or store_info_id is not None:
            raise HTTPException(422, detail={"code": "site_ou_loja"})
        if not (await escopo_do_atendimento(session, user)).unrestricted:
            raise HTTPException(403, detail={"code": "email_do_site_fora_do_escopo"})
    else:
        if grupo not in barra_lojas.chaves():
            raise HTTPException(422, detail={"code": "grupo_email_invalido"})
        if (integration_id is None) == (store_info_id is None):
            raise HTTPException(422, detail={"code": "loja_obrigatoria"})
    response.headers["Cache-Control"] = "no-store"
    await session.execute(sql_text("SET LOCAL jit = off"))
    if site is not None:
        q = await _emails_do_site(session, user, site=site, caixa=grupo)
    else:
        q = await _emails_da_caixa(
            session, user, grupo=grupo, integration_id=integration_id, store_info_id=store_info_id
        )
    if antes_de is not None:
        q = q.where(MailMessage.received_at < antes_de)
    q = q.order_by(MailMessage.received_at.desc(), MailMessage.id.desc())
    if site is None:
        linhas = (await session.execute(q.limit(limite + 1))).all()
    else:
        # O site: o que o SQL não separa (a caixa pelo e-mail decifrado, o código
        # guardado sem a régua) sai depois — lê em lotes até ter a página.
        linhas = []
        lote, pulo = max(limite + 1, 100), 0
        while len(linhas) <= limite:
            bruto = (await session.execute(q.offset(pulo).limit(lote))).all()
            linhas += await _fica_no_site(session, bruto, grupo)
            if len(bruto) < lote:
                break
            pulo += lote
    tem_mais = len(linhas) > limite
    linhas = linhas[:limite]
    itens = [await _linha_da_caixa(session, *linha) for linha in linhas]
    if site is not None:
        # A bolinha do site (27/10/2026): a conversa NÃO LIDA (`_nao_lida_sql`).
        ids = {UUID(i["conversa_id"]) for i in itens if i["conversa_id"]}
        nao_lidas = (
            set(
                (
                    await session.execute(
                        select(AtendimentoConversa.id).where(
                            AtendimentoConversa.id.in_(ids), _nao_lida_sql()
                        )
                    )
                ).scalars()
            )
            if ids
            else set()
        )
        for i in itens:
            i["nao_lida"] = bool(i["conversa_id"]) and UUID(i["conversa_id"]) in nao_lidas
    return {
        "itens": itens,
        "proximo": gravar._utc(linhas[-1][1].received_at).isoformat()
        if tem_mais and linhas
        else None,
    }


def _nao_lida_sql(c=AtendimentoConversa):
    """A conversa (do site) NÃO LIDA — a bolinha vermelha da caixa do site (27/10/2026).

    O Eduardo: "e-mail novo que chega vira bolinha vermelha, e ao clicar some",
    como o não lido do Duoke. Falta responder E chegou e-mail do CLIENTE depois
    do "visto no DaVinci" (`visto_em`: a abertura da conversa grava, POST /visto
    ou /lida — o mesmo relógio, a CHEGADA da mensagem); nunca aberta = não lida."""
    mm = aliased(AtendimentoMensagem)
    nova = exists(
        select(literal(1)).where(
            mm.conversa_id == c.id,
            mm.autor == AUTOR_CLIENTE,
            or_(c.visto_em.is_(None), mm.created_at > c.visto_em),
        )
    )
    return and_(c.aguardando_resposta.is_(True), nova)


async def _email_da_caixa_ou_404(
    session: AsyncSession, user: User, message_id: UUID
) -> tuple[MailMessageMeta, MailMessage, Any, Any]:
    q = await _emails_da_caixa(session, user, grupo=None, message_id=message_id)
    achada = (await session.execute(q)).first()
    if achada is None:
        # A caixa de um SITE (27/10/2026): a mesma régua da lista dele.
        q = await _emails_do_site(session, user, site=None, caixa=None, message_id=message_id)
        achada = (await session.execute(q)).first()
        if achada is not None and not await _fica_no_site(session, [achada], None):
            achada = None
    if achada is None:
        raise HTTPException(404, detail={"code": "email_nao_encontrado"})
    meta, message, integ, ficha, _conversa = achada
    return meta, message, integ, ficha


@router.get("/lojas/emails/{message_id}")
async def email_da_caixa(
    message_id: UUID, session: Session, user: Leitor, response: Response
) -> dict[str, Any]:
    """O e-mail inteiro de UMA linha da caixa da barra.

    A mesma régua da lista (um e-mail das pastas da barra, ligado a uma loja, no
    escopo da equipe, com o filtro de acesso da pessoa); fora dela, 404 (nunca
    diz que o e-mail existe). O texto: os links de acesso removidos e a senha
    escrita mascarada; o código fica (`barra_lojas.texto_da_caixa`, o dono).
    O HTML (30/10/2026, a tela igual à aba E-mail): a mesma régua no HTML, ou
    None — e a tela mostra o texto (`html_caixa.limpar`). A imagem embutida
    (cid:) vem pela rota do anexo daqui, pelo `content_id`; nunca pelo /api/mail."""
    meta, message, integ, ficha = await _email_da_caixa_ou_404(session, user, message_id)
    response.headers["Cache-Control"] = "no-store"
    pasta = await session.get(MailFolder, meta.folder_id) if meta.folder_id else None
    loja = (await _nomes_das_lojas(session, {integ})).get(integ) if integ else None
    if loja is None and ficha is not None:
        f = await session.get(StoreInfo, ficha)
        loja = (" ".join((f.account_name or "").split()) or None) if f else None
    if loja is None and pasta is not None:
        # O e-mail da pasta de um SITE: o nome do site.
        do_site = barra_lojas.site_da_pasta(pasta.nome, pasta.caminho, pasta.chave)
        loja = next((st.nome for st in barra_lojas.sites() if st.chave == do_site), None)
    email = ponte.decifrar(message)
    legivel = texto.legivel(email.texto)
    assunto, corpo, links = barra_lojas.texto_da_caixa(email.assunto, legivel)
    bruto = email.conteudo.get("html")
    html, links_html = html_caixa.limpar(
        email.assunto, legivel, bruto if isinstance(bruto, str) else None
    )
    anexos = (
        await session.scalars(select(MailAttachment).where(MailAttachment.message_id == message.id))
    ).all()
    return {
        "id": str(meta.message_id),
        "estado": meta.estado,
        "recebido_em": gravar._utc(message.received_at),
        "pasta": pasta.nome if pasta is not None else None,
        "loja": loja,
        "conversa_id": str(meta.conversa_id) if meta.conversa_id else None,
        "seguranca": meta.estado == ESTADO_SEGURANCA,
        "de": email.de,
        "de_nome": email.de_nome,
        "reply_to": [r for r in email.reply_to if r != email.de],
        "assunto": assunto,
        "texto": corpo,
        "links_removidos": links,
        "html": html,
        "html_links_removidos": links_html,
        "anexos": [
            {
                "id": str(a.id),
                "tamanho": a.size,
                **{
                    k: v
                    for k, v in decrypt_json(a.metadata_enc).items()
                    if k in ("filename", "content_type", "content_id")
                },
            }
            for a in anexos
        ],
    }


@router.get("/lojas/anexos/{attachment_id}")
async def anexo_da_caixa(attachment_id: UUID, session: Session, user: Leitor) -> Response:
    """O anexo de um e-mail da caixa da barra (a mesma régua do e-mail: 404 fora dela).

    A etiqueta de postagem da devolução vem em anexo: quem vê a loja baixa."""
    attachment = await session.get(MailAttachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, detail={"code": "anexo_nao_encontrado"})
    try:
        await _email_da_caixa_ou_404(session, user, attachment.message_id)
    except HTTPException:
        raise HTTPException(404, detail={"code": "anexo_nao_encontrado"}) from None
    metadata = decrypt_json(attachment.metadata_enc)
    nome = str(metadata.get("filename") or "anexo").replace("\\", "/").rsplit("/", 1)[-1]
    nome = nome or "anexo"
    return Response(
        content=decrypt_bytes(attachment.content_enc),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


# ── Na conversa (quem vê) ─────────────────────────────────────────────────


@router.get("/conversas/{conversa_id}/emails")
async def emails_da_conversa(
    conversa_id: str, session: Session, user: Leitor, response: Response
) -> dict[str, Any]:
    """Os cartões dos e-mails da conversa — no escopo da equipe, texto protegido.

    Só a conversa de E-MAIL tem cartões (13/10/2026): a de chat volta vazia —
    o e-mail que a ponte gravou nela não aparece mais (`separa_email`)."""
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    response.headers["Cache-Control"] = "no-store"
    if not separa_email.e_conversa_de_email(conversa):
        return {"emails": [], "chamado": None, "outros_chamados": []}
    linhas = (
        await session.execute(
            select(MailMessageMeta, MailMessage)
            .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
            .where(
                MailMessageMeta.conversa_id == conversa.id,
                MailMessageMeta.estado.in_(ESTADOS_COM_TEXTO),
                administracao.filtro_mensagem(user, MailMessageMeta.message_id),
            )
            .order_by(MailMessage.received_at)
            .limit(200)
        )
    ).all()
    nomes = await _nomes_das_lojas(session, {m.integration_id for m, _ in linhas})
    response.headers["Cache-Control"] = "no-store"
    chamado = None
    if chamados.e_chamado(conversa):
        chamado = {
            **chamados.resumo_do_chamado(
                conversa, resposta_na_fila=await chamados.resposta_na_fila(session, conversa)
            ),
            # O pedido escrito no formulário, no Bling: só SUGESTÃO (nunca liga sozinho).
            "pedido_sugerido": await chamados.pedido_sugerido(session, conversa, scope),
        }
    return {
        "emails": [await _cartao(session, m, msg, nomes, com_texto=True) for m, msg in linhas],
        "chamado": chamado,
        "outros_chamados": await chamados.outros_chamados(session, conversa, user=user),
    }


@router.get("/conversas/{conversa_id}/emails-da-venda")
async def emails_da_venda(
    conversa_id: str, session: Session, user: Leitor, response: Response
) -> dict[str, Any]:
    """O bloco "E-mails da venda" do painel ④ (RF1/RF5, 09/10/2026).

    Os e-mails que a ponte ligou à conversa aberta OU às conversas da mesma
    venda (a família das abas: o mesmo comprador e pedido, na mesma loja —
    `abas.familia`), resumidos por CAIXA (o endereço que recebeu → a loja do
    cadastro) e PASTA, com quantos e o último. Sem texto, assunto nem
    remetente: só o que o cartão de cada e-mail já mostra (pasta, endereço,
    loja). No escopo da equipe, como a conversa.

    Assim só na conversa de E-MAIL (13/10/2026, `separa_email`): o chat não
    mostra e-mail. Na conversa de CHAT volta só QUANTOS e-mails as conversas
    de e-mail da mesma venda têm, quantas esperam resposta e o id delas
    (`_emails_da_venda_no_chat`, o link "abrir na aba E-mail"); sem nenhuma,
    o zero de sempre (`{"total": 0, "caixas": []}`).
    """
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    response.headers["Cache-Control"] = "no-store"
    familia = await abas.familia(session, conversa)
    if not separa_email.e_conversa_de_email(conversa):
        return await _emails_da_venda_no_chat(session, conversa, familia, scope)
    ids = {c.id for c in familia.conversas} | {conversa.id}
    linhas = (
        await session.execute(
            select(
                MailMessageMeta.alias_recebido,
                MailMessageMeta.integration_id,
                MailMessageMeta.store_info_id,
                MailMessageMeta.plataforma,
                MailMessageMeta.folder_id,
                MailMessageMeta.finalidade,
                func.count(),
                func.max(MailMessage.received_at),
            )
            .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
            .where(
                MailMessageMeta.conversa_id.in_(ids),
                MailMessageMeta.estado.in_(ESTADOS_COM_TEXTO),
                administracao.filtro_mensagem(user, MailMessageMeta.message_id),
            )
            .group_by(
                MailMessageMeta.alias_recebido,
                MailMessageMeta.integration_id,
                MailMessageMeta.store_info_id,
                MailMessageMeta.plataforma,
                MailMessageMeta.folder_id,
                MailMessageMeta.finalidade,
            )
        )
    ).all()
    nomes = await _nomes_das_lojas(session, {i for _, i, *_ in linhas if i is not None})
    fichas: dict[UUID, str | None] = {}
    pastas: dict[UUID, str] = {}
    caixas: dict[tuple, dict[str, Any]] = {}
    for linha in linhas:
        alias, integration_id, store_info_id, plataforma, folder_id, finalidade, n, ultimo = linha
        loja = nomes.get(integration_id) if integration_id else None
        if loja is None and store_info_id is not None:
            if store_info_id not in fichas:
                ficha = await session.get(StoreInfo, store_info_id)
                nome = " ".join((ficha.account_name or "").split()) if ficha else ""
                fichas[store_info_id] = nome or None
            loja = fichas[store_info_id]
        if folder_id is not None and folder_id not in pastas:
            pasta = await session.get(MailFolder, folder_id)
            pastas[folder_id] = pasta.nome if pasta is not None else ""
        chave = (alias, integration_id, store_info_id, plataforma)
        caixa = caixas.setdefault(
            chave,
            {
                "caixa": alias,
                "loja": loja,
                "plataforma": plataforma,
                "quantidade": 0,
                "ultimo_em": None,
                "pastas": [],
            },
        )
        ultimo = gravar._utc(ultimo)
        caixa["quantidade"] += int(n)
        if ultimo is not None and (caixa["ultimo_em"] is None or ultimo > caixa["ultimo_em"]):
            caixa["ultimo_em"] = ultimo
        caixa["pastas"].append(
            {
                "pasta": (pastas.get(folder_id) or None) if folder_id is not None else None,
                "finalidade": finalidade,
                "destaque": finalidade in FINALIDADES_DESTAQUE,
                "quantidade": int(n),
            }
        )
    saida = sorted(
        caixas.values(),
        key=lambda c: c["ultimo_em"] or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    for c in saida:
        c["pastas"].sort(key=lambda p: (-p["quantidade"], p["pasta"] or ""))
    return {"total": sum(c["quantidade"] for c in saida), "caixas": saida}


async def _emails_da_venda_no_chat(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    familia: abas.Familia,
    scope: TeamScope,
) -> dict[str, Any]:
    """Resumo legado, com a mesma seleção estrita de e-mails individuais da venda.

    `familia` permanece no contrato do painel; comprador ou outras vendas da
    família não autorizam um vínculo. Um caso X/Y conta só os e-mails X.
    """
    resultado = await venda.listar(session, conversa, scope)
    if not resultado.linhas:
        return {"total": 0, "caixas": []}
    grupos: dict[UUID, dict[str, Any]] = {}
    for _meta, _message, grupo in resultado.linhas:
        item = grupos.setdefault(
            grupo.id,
            {"id": str(grupo.id), "emails": 0, "aguardando": bool(grupo.aguardando_resposta)},
        )
        item["emails"] += 1
    conversas = sorted(grupos.values(), key=lambda x: not x["aguardando"])
    return {
        "total": len(resultado.linhas),
        "caixas": [],
        "aguardando": sum(1 for x in conversas if x["aguardando"]),
        "conversas": conversas,
    }


@router.get("/venda/{conversa_id}")
async def emails_desta_venda(
    conversa_id: str, session: Session, user: Leitor, response: Response
) -> dict[str, Any]:
    """Cartões sem corpo, somente desta venda; total zero oculta o botão."""
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    response.headers["Cache-Control"] = "no-store"
    resultado = await venda.listar(session, conversa, scope)
    nomes = await _nomes_das_lojas(
        session, {meta.integration_id for meta, _message, _grupo in resultado.linhas}
    )
    return {
        "estado": resultado.estado,
        "pedido": resultado.pedido,
        "total": len(resultado.linhas),
        "itens": [
            await _cartao(session, meta, message, nomes, com_texto=False)
            for meta, message, _grupo in resultado.linhas
        ],
    }


@router.get("/venda/{conversa_id}/emails/{message_id}")
async def email_desta_venda(
    conversa_id: str, message_id: UUID, session: Session, user: Leitor, response: Response
) -> dict[str, Any]:
    """Leitura protegida do e-mail selecionado; o caso inteiro não é liberado."""
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    response.headers["Cache-Control"] = "no-store"
    resultado = await venda.listar(session, conversa, scope)
    for meta, message, _grupo in resultado.linhas:
        if meta.message_id == message_id:
            return await _cartao(
                session,
                meta,
                message,
                await _nomes_das_lojas(session, {meta.integration_id}),
                com_texto=True,
            )
    raise HTTPException(404, detail={"code": "email_nao_encontrado"})


@router.get("/anexos/{attachment_id}")
async def anexo(attachment_id: UUID, session: Session, user: Leitor) -> Response:
    """O anexo de um e-mail que a equipe vê (pela conversa; sem loja: quem mexe)."""
    attachment = await session.get(MailAttachment, attachment_id)
    meta = await session.get(MailMessageMeta, attachment.message_id) if attachment else None
    if attachment is None or meta is None or meta.estado not in ESTADOS_COM_TEXTO:
        raise HTTPException(404, detail={"code": "anexo_nao_encontrado"})
    await administracao.exigir_mensagem(session, user, attachment.message_id)
    scope = await escopo_do_atendimento(session, user)
    if meta.conversa_id is not None:
        conversa = await session.get(AtendimentoConversa, meta.conversa_id)
        if conversa is None or not _no_escopo(scope, conversa.integration_id):
            raise HTTPException(404, detail={"code": "anexo_nao_encontrado"})
    elif not acesso.pode_mexer(user):
        raise HTTPException(404, detail={"code": "anexo_nao_encontrado"})
    metadata = decrypt_json(attachment.metadata_enc)
    nome = str(metadata.get("filename") or "anexo").replace("\\", "/").rsplit("/", 1)[-1]
    nome = nome or "anexo"
    # Os mesmos cabeçalhos da rota da Central: baixa, nunca abre no navegador.
    return Response(
        content=decrypt_bytes(attachment.content_enc),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.get("/conversas/{conversa_id}/previa")
async def previa(
    conversa_id: str,
    session: Session,
    user: Leitor,
    response: Response,
    mail_message_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    """Como a resposta vai sair (De, Para, Assunto, assinatura, citação) e as travas de agora."""
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    if not ponte.e_conversa_da_ponte(conversa):
        raise HTTPException(409, detail={"code": "nao_e_conversa_de_email"})
    p = await responder.previa(session, conversa, mail_message_id=mail_message_id)
    response.headers["Cache-Control"] = "no-store"
    return {
        "mail_message_id": str(p.message.id) if p.message else None,
        "de": p.de,
        "para": p.para,
        "formulario": p.formulario,
        "assunto": p.assunto,
        "assinatura": p.assinatura,
        "assinatura_html": p.assinatura_html,
        "citacao_cabeca": p.citacao_cabeca,
        "citacao": p.citacao,
        "modo": p.modo,
        "pode_mexer": acesso.pode_mexer(user),
        "bloqueios": [
            {"codigo": b.codigo, "texto": b.texto, "confirmavel": b.confirmavel}
            for b in p.bloqueios
        ],
        "avisos": p.avisos,
        "abrir_no_tuta": p.abrir_no_tuta,
    }


class AgruparIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversa_id: UUID


@router.post("/conversas/{conversa_id}/agrupar")
async def agrupar(
    conversa_id: str, body: AgruparIn, session: Session, user: Leitor
) -> dict[str, Any]:
    """RF6: junta este chamado e o escolhido (mesma cliente, mesma marca), por clique.

    FICA o mais antigo dos dois (o protocolo principal), qualquer que seja o
    lado de onde a pessoa clicou; a resposta diz qual ficou."""
    _mexe(user)
    scope = await escopo_do_atendimento(session, user)
    este = await _conversa_ou_404(session, conversa_id, scope)
    outro = await _conversa_ou_404(session, str(body.conversa_id), scope)
    origem, destino = await chamados.ordem_do_agrupamento(session, este, outro)
    await _travar_ou_409(session, origem, "conversa_ocupada")
    await _travar_ou_409(session, destino, "conversa_ocupada")
    try:
        await chamados.agrupar(session, origem, destino, user)
    except chamados.AgruparError as erro:
        await session.rollback()
        raise HTTPException(erro.status, detail={"code": erro.codigo}) from None
    await session.commit()
    logger.info(
        "mail_atendimento_chamados_agrupados",
        origem=str(origem.id),
        destino=str(destino.id),
        user_id=str(user.id),
    )
    return {
        "conversa_id": str(destino.id),
        "protocolo": ponte.dados_mail(destino).get("protocolo"),
        "protocolos": chamados.protocolos(destino),
    }


@router.post("/conversas/{conversa_id}/nao-agrupar")
async def nao_agrupar(
    conversa_id: str, body: AgruparIn, session: Session, user: Leitor
) -> dict[str, Any]:
    """RF6: "Não agrupar" — a sugestão deste par some (lembrado nos dois chamados,
    com quem e quando). Agrupar à mão continua possível."""
    _mexe(user)
    scope = await escopo_do_atendimento(session, user)
    este = await _conversa_ou_404(session, conversa_id, scope)
    outro = await _conversa_ou_404(session, str(body.conversa_id), scope)
    await _travar_ou_409(session, este, "conversa_ocupada")
    await _travar_ou_409(session, outro, "conversa_ocupada")
    try:
        await chamados.recusar_agrupar(session, este, outro, user)
    except chamados.AgruparError as erro:
        await session.rollback()
        raise HTTPException(erro.status, detail={"code": erro.codigo}) from None
    await session.commit()
    logger.info(
        "mail_atendimento_agrupar_recusado",
        conversa_id=str(este.id),
        outro=str(outro.id),
        user_id=str(user.id),
    )
    return {
        "conversa_id": str(este.id),
        "outros_chamados": await chamados.outros_chamados(session, este, user=user),
    }


# ── Revisar envio (quem mexe) ─────────────────────────────────────────────


@router.get("/envios")
async def envios(
    session: Session,
    user: Leitor,
    response: Response,
    estado: Annotated[str | None, Query(max_length=16)] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """As respostas pela fila da Central que saíram de conversa ("revisar envio": uncertain)."""
    _mexe(user)
    _aba_inteira(user)
    q = (
        select(MailOutbox, MailOutboxMeta)
        .join(MailOutboxMeta, MailOutboxMeta.outbox_id == MailOutbox.id)
        .where(
            MailOutboxMeta.origem == "conversa",
            administracao.filtro_mensagem(user, MailOutbox.message_id),
        )
    )
    if estado == "revisar":
        q = q.where(_a_conferir())
    elif estado:
        q = q.where(MailOutbox.status == estado)
    linhas = (await session.execute(q.order_by(MailOutbox.created_at.desc()).limit(limite))).all()
    limite_do_lease = datetime.now(UTC) - mail_central.LEASE_TIMEOUT
    itens = []
    for job, liga in linhas:
        vista = mail_central.outbox_view(job)
        itens.append(
            {
                "id": str(job.id),
                "status": job.status,
                "codigo": job.error_code,
                "de": vista["from_address"],
                "para": vista["to"],
                "conversa_id": str(liga.conversa_id) if liga.conversa_id else None,
                "mensagem_id": (
                    str(liga.atendimento_mensagem_id) if liga.atendimento_mensagem_id else None
                ),
                "criado_em": job.created_at,
                "concluido_em": job.completed_at,
                "resolucao": liga.resolucao,
                "resolvido_em": liga.resolvido_em,
                "revisar": job.status == "uncertain"
                or (
                    job.status == "leased"
                    and job.leased_at is not None
                    and gravar._utc(job.leased_at) < limite_do_lease
                ),
                "lease_vencido": job.status == "leased"
                and job.leased_at is not None
                and gravar._utc(job.leased_at) < limite_do_lease,
                "recibo_tardio": liga.recibo_tardio,
            }
        )
    response.headers["Cache-Control"] = "no-store"
    return {"itens": itens}


class ResolverIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    saiu: bool


@router.post("/envios/{job_id}/resolver")
async def resolver_envio(
    job_id: UUID, body: ResolverIn, session: Session, user: Leitor
) -> dict[str, Any]:
    """Uma pessoa conferiu no Tuta: saiu ou não saiu. Nunca reenvia (a regra DA Central)."""
    _mexe(user)
    _aba_inteira(user)
    liga = await session.get(MailOutboxMeta, job_id)
    job = await session.get(MailOutbox, job_id) if liga is not None else None
    if liga is None or job is None or liga.origem != "conversa":
        raise HTTPException(404, detail={"code": "envio_nao_encontrado"})
    try:
        await responder.resolver_job(session, job, user, saiu=body.saiu)
    except mail_central.MailError as erro:
        await session.rollback()
        raise HTTPException(409, detail={"code": erro.code}) from None
    await session.commit()
    logger.info(
        "mail_atendimento_envio_resolvido", job_id=str(job_id), saiu=body.saiu, user_id=str(user.id)
    )
    return {"id": str(job_id), "status": job.status, "resolucao": liga.resolucao}


# ── Pastas e regras (quem mexe; mudar = admin) ────────────────────────────


async def _caixas_das_pastas(session: AsyncSession, user: User) -> set[UUID]:
    """As caixas cujas pastas esta pessoa (que mexe) vê: as que ela vê na Saúde e
    as com ponte (é o que a ponte lê). As pastas de uma caixa PRIVADA (o
    conector v2 manda a lista inteira da conta) só para quem abre a caixa."""
    visiveis = {m.id for m in await saude.caixas_visiveis(session, user)}
    visiveis |= {
        mid
        for (mid,) in (
            await session.execute(
                select(MailMailboxSettings.mailbox_id).where(
                    MailMailboxSettings.ponte_ligada.is_(True)
                )
            )
        ).all()
    }
    return visiveis


@router.get("/pastas")
async def listar_pastas(
    session: Session,
    user: Leitor,
    mailbox_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    _mexe(user)
    _aba_inteira(user)
    r = await regras.carregar(session)
    visiveis = await _caixas_das_pastas(session, user)
    q = (
        select(MailFolder)
        .where(MailFolder.mailbox_id.in_(visiveis))
        .order_by(MailFolder.mailbox_id, MailFolder.nome)
    )
    if mailbox_id is not None:
        q = q.where(MailFolder.mailbox_id == mailbox_id)
    itens = []
    # O nome de cada caixa (a tela Pastas e regras agrupa por caixa).
    caixas = {
        m.id: m.label
        for m in (
            await session.execute(select(MailMailbox).where(MailMailbox.id.in_(visiveis)))
        ).scalars()
    }
    linhas = list((await session.execute(q)).scalars())
    # Quantos e-mails a ponte viu em cada pasta nos últimos 30 dias, e quantos
    # estão GUARDADOS sem ligar por falta de categoria nos últimos `RELER_DIAS`
    # dias (21/10/2026: a tela avisa que escolher a categoria liga esses também
    # — os mais antigos ficam guardados, `pastas.reler_ignorados`).
    ids = [p.id for p in linhas]
    contas: dict[UUID, tuple[int, int]] = {}
    if ids:
        desde = datetime.now(UTC) - timedelta(days=30)
        guardado = and_(
            MailMessageMeta.estado == ESTADO_IGNORADO,
            MailMessageMeta.motivo.in_(IGNORADOS_RELIDOS),
            MailMessageMeta.conversa_id.is_(None),
            MailMessageMeta.mensagem_id.is_(None),
            MailMessageMeta.chamado_id.is_(None),
            MailMessage.received_at >= pastas_mail.desde_os_relidos(),
        )
        contas = {
            folder_id: (int(total or 0), int(guardados or 0))
            for folder_id, total, guardados in (
                await session.execute(
                    select(
                        MailMessageMeta.folder_id,
                        func.count().filter(MailMessage.received_at >= desde),
                        func.count().filter(guardado),
                    )
                    .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
                    .where(MailMessageMeta.folder_id.in_(ids))
                    .group_by(MailMessageMeta.folder_id)
                )
            ).all()
        }
    quem = {p.categoria_por for p in linhas if p.categoria_por is not None}
    nomes_de_quem = {
        u.id: (u.name or u.email)
        for u in (
            (await session.execute(select(User).where(User.id.in_(quem)))).scalars() if quem else []
        )
    }
    for p in linhas:
        classe = regras.efetiva(p, r)
        travada = not regras.e_criada(p.tipo_tuta)
        categoria = regras.categoria_que_vale(p)
        total_30d, guardados = contas.get(p.id, (0, 0))
        itens.append(
            {
                "id": str(p.id),
                "mailbox_id": str(p.mailbox_id),
                "chave": p.chave,
                "nome": p.nome,
                "caminho": p.caminho,
                "tipo": regras.tipo_da_pasta(p.tipo_tuta),
                "plataforma": classe.plataforma,
                "finalidade": classe.finalidade,
                "marca": classe.marca,
                "ler": p.ler,
                "plataforma_manual": p.plataforma_manual,
                "finalidade_manual": p.finalidade_manual,
                "ignorar": p.ignorar,
                "revisada": p.revisada,
                "vista_em": gravar._utc(p.vista_em),
                "sumiu_em": gravar._utc(p.sumiu_em),
                # 21/10/2026: a categoria (só de pessoa) e a sugestão da regra.
                "categoria": categoria,
                "categoria_rotulo": ROTULO_CATEGORIA.get(categoria or ""),
                "categoria_sugerida": regras.categoria_sugerida(classe, p.tipo_tuta),
                "categoria_por": nomes_de_quem.get(p.categoria_por) if p.categoria_por else None,
                "categoria_em": gravar._utc(p.categoria_em),
                # Entrada, Enviados, Lixeira, Spam…: nunca têm categoria.
                "travada": travada,
                "total_30d": total_30d,
                "guardados": guardados,
            }
        )
    usadas = {i["mailbox_id"] for i in itens}
    return {
        "itens": itens,
        "caixas": [
            {"id": str(mid), "nome": nome} for mid, nome in caixas.items() if str(mid) in usadas
        ],
        "plataformas": list(PLATAFORMAS_PASTA),
        "finalidades": list(regras.FINALIDADES_VALIDAS),
        "categorias": [{"valor": c, "rotulo": ROTULO_CATEGORIA[c]} for c in CATEGORIAS_PASTA],
        # Escolher a categoria relê os guardados só destes últimos dias.
        "reler_dias": RELER_DIAS,
    }


class PastaPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plataforma: str | None = Field(default=None, max_length=16)
    finalidade: str | None = Field(default=None, max_length=16)
    ignorar: bool | None = None
    # 21/10/2026: a categoria da pasta criada (só vale se vier no corpo);
    # null = "não vincular".
    categoria: str | None = Field(default=None, max_length=12)


@router.patch("/pastas/{pasta_id}")
async def alterar_pasta(
    pasta_id: UUID, body: PastaPatch, session: Session, user: Leitor
) -> dict[str, Any]:
    """Pasta: a plataforma (e a finalidade), ignorar e a CATEGORIA. Admin que mexe.

    A categoria (21/10/2026, decisões do dono) só numa pasta criada (a Entrada,
    os Enviados, a Lixeira e o Spam são travados); `venda` exige a plataforma
    de marketplace da pasta; `chamado` nunca numa pasta de SITE (o formulário
    do site não tem pedido de marketplace: na Pós-venda tudo ficaria guardado
    como `chamado_sem_pedido` — o site liga como antes com "Site (como hoje)"). Ao
    ganhar categoria, o que estava guardado sem ligar nesta pasta nos últimos
    `RELER_DIAS` dias é relido pela ponte (depois dos e-mails que acabaram de
    chegar; nada tinha conversa: não duplica). Quem escolheu e quando ficam na
    pasta e no log.
    """
    _mexe(user)
    _admin(user)
    pasta = await session.get(MailFolder, pasta_id)
    if pasta is None:
        raise HTTPException(404, detail={"code": "pasta_nao_encontrada"})
    campos = body.model_fields_set
    if "categoria" in campos and body.categoria is not None:
        if body.categoria not in CATEGORIAS_PASTA:
            raise HTTPException(422, detail={"code": "categoria_invalida"})
        if not regras.e_criada(pasta.tipo_tuta):
            raise HTTPException(422, detail={"code": "categoria_em_pasta_do_sistema"})
    if "plataforma" in campos:
        if body.plataforma and body.plataforma not in PLATAFORMAS_PASTA:
            raise HTTPException(422, detail={"code": "plataforma_invalida"})
        pasta.plataforma_manual = body.plataforma or None
    if "finalidade" in campos:
        if body.finalidade and body.finalidade not in regras.FINALIDADES_VALIDAS:
            raise HTTPException(422, detail={"code": "finalidade_invalida"})
        pasta.finalidade_manual = body.finalidade or None
    if "ignorar" in campos and body.ignorar is not None:
        pasta.ignorar = body.ignorar
    r = await regras.carregar(session)
    de = pasta.categoria
    para = body.categoria if "categoria" in campos else de
    if para == CATEGORIA_VENDA:
        # A conversa da venda é da loja de UMA plataforma (o site nunca é venda).
        plataforma = regras.efetiva(pasta, r).plataforma
        if plataforma not in PLATAFORMAS_PASTA or plataforma == PLATAFORMA_SITE:
            await session.rollback()
            raise HTTPException(422, detail={"code": "categoria_venda_sem_plataforma"})
    if para == CATEGORIA_CHAMADO and regras.efetiva(pasta, r).plataforma == PLATAFORMA_SITE:
        # O chamado da Pós-venda é de um pedido de marketplace: o formulário do
        # site nunca liga (ficaria tudo guardado, sem tela para ligar à mão).
        await session.rollback()
        raise HTTPException(422, detail={"code": "categoria_chamado_em_site"})
    if para != de:
        pasta.categoria = para
        pasta.categoria_por = user.id
        pasta.categoria_em = datetime.now(UTC)
    pasta.revisada = True
    regras.reclassificar(pasta, r)
    relidos = await pastas_mail.reler_ignorados(session, pasta) if para != de and para else 0
    await session.commit()
    logger.info("mail_atendimento_pasta_alterada", pasta_id=str(pasta_id), user_id=str(user.id))
    if para != de:
        logger.info(
            "mail_atendimento_pasta_categoria",
            pasta_id=str(pasta_id),
            de=de,
            para=para,
            relidos=relidos,
            user_id=str(user.id),
        )
    return {
        "id": str(pasta.id),
        "ler": pasta.ler,
        "revisada": pasta.revisada,
        "categoria": pasta.categoria,
        "relidos": relidos,
    }


# ── As pastas INFORMATIVAS (quem mexe) ────────────────────────────────────


@router.get("/informativos")
async def informativos(session: Session, user: Leitor, response: Response) -> dict[str, Any]:
    """Os itens das pastas INFORMATIVAS (21/10/2026, decisão 4 do dono).

    Um item por pasta de categoria `informativo` das caixas que a pessoa vê,
    com quantas linhas (e-mails), o último e as lojas — contando SÓ os e-mails
    que ela pode ver (`administracao.filtro_mensagem`). As linhas do item vêm
    de GET /emails?fila=informativo&pasta=<id> (com a paginação `antes_de`); o
    e-mail inteiro, de GET /emails/{id}. Sem texto aqui."""
    _mexe(user)
    _aba_inteira(user)
    lista = await _pastas_informativas(session, user)
    caixas = {
        m.id: m.label
        for m in (
            await session.execute(
                select(MailMailbox).where(MailMailbox.id.in_({p.mailbox_id for p in lista}))
            )
        ).scalars()
    }
    grupos = (
        (
            await session.execute(
                select(
                    MailMessageMeta.folder_id,
                    MailMessageMeta.integration_id,
                    MailMessageMeta.store_info_id,
                    func.count(),
                    func.max(MailMessage.received_at),
                )
                .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
                .where(
                    MailMessageMeta.folder_id.in_([p.id for p in lista]),
                    MailMessageMeta.estado == ESTADO_INFORMATIVO,
                    administracao.filtro_mensagem(user, MailMessageMeta.message_id),
                )
                .group_by(
                    MailMessageMeta.folder_id,
                    MailMessageMeta.integration_id,
                    MailMessageMeta.store_info_id,
                )
            )
        ).all()
        if lista
        else []
    )
    nomes = await _nomes_das_lojas(session, {i for _, i, *_ in grupos if i is not None})
    fichas: dict[UUID, str | None] = {}
    por_pasta: dict[UUID, dict[str, Any]] = {}
    for folder_id, integration_id, store_info_id, n, ultimo in grupos:
        loja = nomes.get(integration_id) if integration_id else None
        if loja is None and store_info_id is not None:
            if store_info_id not in fichas:
                ficha = await session.get(StoreInfo, store_info_id)
                nome = " ".join((ficha.account_name or "").split()) if ficha else ""
                fichas[store_info_id] = nome or None
            loja = fichas[store_info_id]
        item = por_pasta.setdefault(folder_id, {"total": 0, "ultimo_em": None, "lojas": set()})
        item["total"] += int(n)
        ultimo = gravar._utc(ultimo)
        if ultimo is not None and (item["ultimo_em"] is None or ultimo > item["ultimo_em"]):
            item["ultimo_em"] = ultimo
        if loja:
            item["lojas"].add(loja)
    itens = []
    for p in lista:
        conta = por_pasta.get(p.id, {"total": 0, "ultimo_em": None, "lojas": set()})
        itens.append(
            {
                "pasta_id": str(p.id),
                "nome": p.nome,
                "mailbox_id": str(p.mailbox_id),
                "caixa": caixas.get(p.mailbox_id),
                "total": conta["total"],
                "ultimo_em": conta["ultimo_em"],
                "lojas": sorted(conta["lojas"]),
            }
        )
    # O item com e-mail mais novo em cima; os vazios no fim, pelo nome.
    itens.sort(
        key=lambda i: (
            i["ultimo_em"] is None,
            -i["ultimo_em"].timestamp() if i["ultimo_em"] else 0.0,
            i["nome"].lower(),
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return {"itens": itens}


@router.get("/regras")
async def listar_regras(session: Session, user: Leitor) -> dict[str, Any]:
    _mexe(user)
    _aba_inteira(user)
    linhas = (
        await session.execute(
            select(AtendimentoRegraPastaEmail).order_by(
                AtendimentoRegraPastaEmail.tipo, AtendimentoRegraPastaEmail.palavra
            )
        )
    ).scalars()
    itens = [
        {"id": str(r.id), "tipo": r.tipo, "palavra": r.palavra, "valor": r.valor, "ativa": r.ativa}
        for r in linhas
    ]
    if not itens:
        # Tabela vazia: vale a semente (a mesma da migration).
        itens = [
            {"id": None, "tipo": t, "palavra": p, "valor": v, "ativa": True}
            for t, p, v in regras.SEMENTE
        ]
    return {"itens": itens}


class RegraIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: str = Field(max_length=16)
    palavra: str = Field(min_length=1, max_length=64)
    valor: str = Field(min_length=1, max_length=64)
    ativa: bool = True


@router.put("/regras")
async def salvar_regra(body: RegraIn, session: Session, user: Leitor) -> dict[str, Any]:
    """Nova palavra (nova plataforma = nova linha, sem mexer no código). Admin que mexe."""
    _mexe(user)
    _admin(user)
    if body.tipo not in regras.TIPOS:
        raise HTTPException(422, detail={"code": "tipo_invalido"})
    palavra = regras.normalizar_palavra(body.palavra)
    if not palavra:
        raise HTTPException(422, detail={"code": "palavra_invalida"})
    valor = body.valor.strip().lower()
    if body.tipo == regras.TIPO_PLATAFORMA and valor not in PLATAFORMAS_PASTA:
        raise HTTPException(422, detail={"code": "plataforma_invalida"})
    if body.tipo == regras.TIPO_FINALIDADE and valor not in regras.FINALIDADES_VALIDAS:
        raise HTTPException(422, detail={"code": "finalidade_invalida"})
    vazia = not await session.scalar(select(func.count()).select_from(AtendimentoRegraPastaEmail))
    if vazia:
        # Primeira palavra pela tela com a tabela vazia: a semente entra junto
        # (senão a tabela com UMA linha trocaria todas as regras por ela).
        for t, p, v in regras.SEMENTE:
            session.add(AtendimentoRegraPastaEmail(tipo=t, palavra=p, valor=v, ativa=True))
        await session.flush()
    regra = (
        await session.execute(
            select(AtendimentoRegraPastaEmail).where(
                AtendimentoRegraPastaEmail.tipo == body.tipo,
                AtendimentoRegraPastaEmail.palavra == palavra,
            )
        )
    ).scalar_one_or_none()
    if regra is None:
        regra = AtendimentoRegraPastaEmail(
            tipo=body.tipo, palavra=palavra, valor=valor, ativa=body.ativa
        )
        session.add(regra)
    else:
        regra.valor = valor
        regra.ativa = body.ativa
    await session.flush()
    regras.esquecer(session)
    # As pastas se reclassificam com a regra nova.
    r = await regras.carregar(session)
    for p in (await session.execute(select(MailFolder))).scalars():
        regras.reclassificar(p, r)
    await session.commit()
    return {
        "id": str(regra.id),
        "tipo": regra.tipo,
        "palavra": regra.palavra,
        "valor": regra.valor,
        "ativa": regra.ativa,
    }


# ── Saúde ─────────────────────────────────────────────────────────────────


@router.get("/saude")
async def saude_das_caixas(session: Session, user: Leitor, response: Response) -> dict[str, Any]:
    """Uma linha por caixa: as da EMPRESA para todos; a privada só para o dono/admin.

    Só a aba E-mail mostra (Filas): desde 27/10/2026, só quem vê a aba
    (`_aba_inteira`: admin ou `atendimento_email` view)."""
    _aba_inteira(user)
    caixas = await saude.caixas_visiveis(session, user)
    saida: dict[str, Any] = {
        "caixas": [await saude.linha_da_caixa(session, c, user=user) for c in caixas]
    }
    if acesso.pode_mexer(user):
        saida.update(await saude.lojas_sem_caixa_lida(session))
    response.headers["Cache-Control"] = "no-store"
    return saida
