"""Pós-venda › Atendimento: o E-MAIL das lojas dentro do /atendimento (RF5 e RF6, 08/10/2026).

A Central de e-mail do outro dev (`/api/mail/*`) é a CAIXA INTEIRA: só o dono
dela e os admins. Aqui fica só o que a PONTE levou ao /atendimento
(`services/mail_atendimento/ponte.py`), com as regras do /atendimento (a
trava `_so_admin`, o `pode_ver`/`pode_mexer` e o escopo por equipe):

  quem VÊ o /atendimento (pela conversa, no escopo da equipe):
    GET   …/conversas/{id}/emails   — os cartões dos e-mails da conversa (texto
                                      inteiro PROTEGIDO: links de acesso e
                                      códigos fora; nunca HTML) + os outros
                                      chamados abertos da cliente (RF6, agrupar)
    GET   …/anexos/{id}             — o anexo (octet-stream, nosniff, CSP sandbox)
    GET   …/conversas/{id}/previa   — como a resposta vai sair e as travas de agora
    GET   …/saude                   — as caixas da EMPRESA (a privada só para o
                                      dono/admin) — e, para quem mexe, as lojas
                                      sem caixa lida

  só quem MEXE (as filas podem ter e-mail de qualquer loja e de endereço interno):
    GET   …/filas                   — as contagens
    GET   …/emails?fila=…           — sem_loja | sem_vinculo | suspeito | resumo | erro
    GET   …/emails/{id}             — o cartão de um e-mail da fila
    POST  …/emails/{id}/loja        — escolher a loja (e-mail sem loja, 1 clique)
    POST  …/emails/{id}/vincular    — ligar a um pedido/conversa (e-mail sem vínculo)
    POST  …/emails/{id}/ignorar     — tirar da fila (fica registrado)
    POST  …/emails/{id}/reprocessar — de novo pela ponte (sem loja, erro)
    POST  …/reprocessar             — a fila "sem loja" inteira (cadastro corrigido)
    POST  …/conversas/{id}/agrupar  — juntar o chamado do site em outro (RF6)
    GET   …/envios                  — as respostas pela fila da Central ("revisar envio")
    POST  …/envios/{job}/resolver   — saiu / não saiu (nunca reenvia)
    GET   …/pastas · PATCH …/pastas/{id} · GET …/regras · PUT …/regras

(… = /api/atendimento/email)

O e-mail de SEGURANÇA (código, senha, acesso), o PRIVADO (caixa só com
aliases de loja) e o da pasta que só se conta NUNCA aparecem aqui: só na
caixa inteira. A resposta sai pelo caminho único:
POST /api/atendimento/conversas/{id}/responder (com `mail_message_id` e
`confirmar_nao_responde`). Texto de e-mail nunca vai para o log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.team_scope import TeamScope, resolve_team_scope
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
    _conversa_ou_404,
    _nomes_das_lojas,
    _so_admin,
    _travar_ou_409,
    _view,
)
from app.security.cipher import decrypt_bytes, decrypt_json
from app.services import mail_central
from app.services.atendimento import acesso, gravar
from app.services.atendimento.constantes import AUTOR_SISTEMA, CANAL_EMAIL, FONTE_TUTA
from app.services.mail_atendimento import (
    chamados,
    codigos,
    ponte,
    regras,
    responder,
    rotear,
    saude,
    texto,
)
from app.services.mail_atendimento.constantes import (
    ESTADO_ERRO,
    ESTADO_GRAVADO,
    ESTADO_IGNORADO,
    ESTADO_RESUMO,
    ESTADO_SEM_LOJA,
    ESTADO_SEM_VINCULO,
    ESTADOS_COM_TEXTO,
    FILA_SUSPEITO,
    FILAS,
    FINALIDADES_DESTAQUE,
    IGNORADO_POR_PESSOA,
    LINK_TUTA,
    MOTIVOS_SEM_LOJA,
    PLATAFORMAS_PASTA,
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
# As LEITURAS que só quem mexe faz: a exceção ao "quem só lê lê tudo" do
# /atendimento (crítica de 08/10: as filas podem ter e-mail de qualquer loja e
# de endereço interno). `tests/test_atendimento_so_leitura.py` confere a lista.
LEITURAS_SO_DE_QUEM_MEXE = frozenset(
    {
        ("GET", f"{_PREFIXO}/filas"),
        ("GET", f"{_PREFIXO}/emails"),
        ("GET", f"{_PREFIXO}/emails/{{message_id}}"),
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


def _no_escopo(scope: TeamScope, integration_id: UUID | None) -> bool:
    return scope.unrestricted or (
        integration_id is not None and integration_id in scope.integration_ids
    )


async def _meta_ou_404(
    session: AsyncSession, message_id: UUID, user: User, *, estados: tuple[str, ...]
) -> tuple[MailMessageMeta, MailMessage]:
    """O e-mail da fila (para quem mexe). Segurança, privado, só contar: 404."""
    meta = await session.get(MailMessageMeta, message_id)
    message = await session.get(MailMessage, message_id) if meta is not None else None
    if meta is None or message is None or meta.estado not in estados:
        raise HTTPException(404, detail={"code": "email_nao_encontrado"})
    scope = await resolve_team_scope(session, user)
    if not _no_escopo(scope, meta.integration_id):
        raise HTTPException(404, detail={"code": "email_nao_encontrado"})
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


def _filtro_da_fila(fila: str):
    if fila == FILA_SUSPEITO:
        return (
            MailMessageMeta.suspeito.is_(True),
            MailMessageMeta.estado.in_(ESTADOS_COM_TEXTO),
        )
    return (MailMessageMeta.estado == fila,)


@router.get("/filas")
async def filas(session: Session, user: Leitor) -> dict[str, int]:
    _mexe(user)
    saida = {}
    for fila in FILAS:
        saida[fila] = int(
            await session.scalar(select(func.count()).where(*_filtro_da_fila(fila))) or 0
        )
    saida["revisar_envio"] = int(
        await session.scalar(
            select(func.count())
            .select_from(MailOutboxMeta)
            .join(MailOutbox, MailOutbox.id == MailOutboxMeta.outbox_id)
            # Só a resposta de conversa (a mesma lista de GET /envios): o job da
            # caixa crua da empresa também tem ligação (`caixa_empresa`).
            .where(MailOutboxMeta.origem == "conversa", _a_conferir())
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
) -> dict[str, Any]:
    """Uma fila: sem loja, sem vínculo, suspeitos, resumos ou erro (sem o texto)."""
    _mexe(user)
    if fila not in FILAS:
        raise HTTPException(422, detail={"code": "fila_invalida"})
    q = (
        select(MailMessageMeta, MailMessage)
        .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
        .where(*_filtro_da_fila(fila))
    )
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
    meta, _message = await _meta_ou_404(
        session, message_id, user, estados=(ESTADO_SEM_VINCULO, ESTADO_GRAVADO)
    )
    if meta.conversa_id is None:
        raise HTTPException(409, detail={"code": "email_sem_conversa"})
    scope = await resolve_team_scope(session, user)
    conversa = await _conversa_ou_404(session, str(meta.conversa_id), scope)
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
        await _mover(session, conversa, destino)
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
    await gravar._recalcular_etiqueta(
        session, alvo_final, motivo=gravar.MOTIVO_ETIQUETA_LEITURA, travar=False
    )
    await session.commit()
    logger.info("mail_atendimento_vinculado", message_id=str(message_id), user_id=str(user.id))
    return {"conversa_id": str(alvo_final.id), "pedido": pedido}


async def _mover(session: AsyncSession, de: AtendimentoConversa, para: AtendimentoConversa) -> None:
    """As mensagens da conversa sem vínculo vão para a conversa do pedido (MOVE, sem duplicar)."""
    await session.execute(
        update(AtendimentoMensagem)
        .where(AtendimentoMensagem.conversa_id == de.id)
        .values(conversa_id=para.id)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(MailOutboxMeta)
        .where(MailOutboxMeta.conversa_id == de.id)
        .values(conversa_id=para.id)
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    await gravar.recalcular_conversa(session, para)
    await gravar.recalcular_conversa(session, de)
    de.situacao = "fechada"
    de.sem_resposta_necessaria = True
    de.dados = {
        **(de.dados or {}),
        ponte.CHAVE: {**ponte.dados_mail(de), "movida_para": str(para.id)},
    }


@router.post("/emails/{message_id}/ignorar")
async def ignorar(message_id: UUID, session: Session, user: Leitor) -> dict[str, Any]:
    """Tira o e-mail da fila (não é de loja nenhuma). Fica registrado quem tirou."""
    _mexe(user)
    meta, _message = await _meta_ou_404(
        session, message_id, user, estados=(ESTADO_SEM_LOJA, ESTADO_RESUMO, ESTADO_ERRO)
    )
    meta.estado = ESTADO_IGNORADO
    meta.motivo = IGNORADO_POR_PESSOA
    alertas = list(meta.alertas or [])
    alertas.append(
        {"codigo": "ignorado_por_pessoa", "texto": f"ignorado por {user.name or user.email}"}
    )
    meta.alertas = alertas
    await session.commit()
    logger.info("mail_atendimento_ignorado", message_id=str(message_id), user_id=str(user.id))
    return {"id": str(message_id), "estado": meta.estado}


@router.post("/emails/{message_id}/reprocessar")
async def reprocessar_um(message_id: UUID, session: Session, user: Leitor) -> dict[str, Any]:
    """De novo pela ponte (cadastro corrigido): só sem loja ou com erro."""
    _mexe(user)
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


# ── Na conversa (quem vê) ─────────────────────────────────────────────────


@router.get("/conversas/{conversa_id}/emails")
async def emails_da_conversa(
    conversa_id: str, session: Session, user: Leitor, response: Response
) -> dict[str, Any]:
    """Os cartões dos e-mails da conversa — no escopo da equipe, texto protegido."""
    scope = await resolve_team_scope(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    linhas = (
        await session.execute(
            select(MailMessageMeta, MailMessage)
            .join(MailMessage, MailMessage.id == MailMessageMeta.message_id)
            .where(
                MailMessageMeta.conversa_id == conversa.id,
                MailMessageMeta.estado.in_(ESTADOS_COM_TEXTO),
            )
            .order_by(MailMessage.received_at)
            .limit(200)
        )
    ).all()
    nomes = await _nomes_das_lojas(session, {m.integration_id for m, _ in linhas})
    response.headers["Cache-Control"] = "no-store"
    return {
        "emails": [await _cartao(session, m, msg, nomes, com_texto=True) for m, msg in linhas],
        "chamado": chamados.resumo_do_chamado(conversa) if chamados.e_chamado(conversa) else None,
        "outros_chamados": await chamados.outros_chamados(session, conversa),
    }


@router.get("/anexos/{attachment_id}")
async def anexo(attachment_id: UUID, session: Session, user: Leitor) -> Response:
    """O anexo de um e-mail que a equipe vê (pela conversa; sem loja: quem mexe)."""
    attachment = await session.get(MailAttachment, attachment_id)
    meta = await session.get(MailMessageMeta, attachment.message_id) if attachment else None
    if attachment is None or meta is None or meta.estado not in ESTADOS_COM_TEXTO:
        raise HTTPException(404, detail={"code": "anexo_nao_encontrado"})
    scope = await resolve_team_scope(session, user)
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
    scope = await resolve_team_scope(session, user)
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
    """RF6: junta este chamado no chamado escolhido (mesma cliente, mesma marca), por clique."""
    _mexe(user)
    scope = await resolve_team_scope(session, user)
    origem = await _conversa_ou_404(session, conversa_id, scope)
    destino = await _conversa_ou_404(session, str(body.conversa_id), scope)
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
    return {"conversa_id": str(destino.id)}


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
    q = (
        select(MailOutbox, MailOutboxMeta)
        .join(MailOutboxMeta, MailOutboxMeta.outbox_id == MailOutbox.id)
        .where(MailOutboxMeta.origem == "conversa")
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


@router.get("/pastas")
async def listar_pastas(
    session: Session,
    user: Leitor,
    mailbox_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    _mexe(user)
    r = await regras.carregar(session)
    # As pastas de uma caixa PRIVADA (o conector v2 manda a lista inteira da
    # conta) só para quem abre a caixa (dono/admin) — as das caixas da empresa
    # e das com ponte, para quem mexe (é o que a ponte lê).
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
    q = (
        select(MailFolder)
        .where(MailFolder.mailbox_id.in_(visiveis))
        .order_by(MailFolder.mailbox_id, MailFolder.nome)
    )
    if mailbox_id is not None:
        q = q.where(MailFolder.mailbox_id == mailbox_id)
    itens = []
    for p in (await session.execute(q)).scalars():
        classe = regras.efetiva(p, r)
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
            }
        )
    return {
        "itens": itens,
        "plataformas": list(PLATAFORMAS_PASTA),
        "finalidades": list(regras.FINALIDADES_VALIDAS),
    }


class PastaPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plataforma: str | None = Field(default=None, max_length=16)
    finalidade: str | None = Field(default=None, max_length=16)
    ignorar: bool | None = None


@router.patch("/pastas/{pasta_id}")
async def alterar_pasta(
    pasta_id: UUID, body: PastaPatch, session: Session, user: Leitor
) -> dict[str, Any]:
    """Pasta nova: escolher a plataforma (e a finalidade) ou ignorar. Admin que mexe."""
    _mexe(user)
    _admin(user)
    pasta = await session.get(MailFolder, pasta_id)
    if pasta is None:
        raise HTTPException(404, detail={"code": "pasta_nao_encontrada"})
    campos = body.model_fields_set
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
    pasta.revisada = True
    regras.reclassificar(pasta, await regras.carregar(session))
    await session.commit()
    logger.info("mail_atendimento_pasta_alterada", pasta_id=str(pasta_id), user_id=str(user.id))
    return {"id": str(pasta.id), "ler": pasta.ler, "revisada": pasta.revisada}


@router.get("/regras")
async def listar_regras(session: Session, user: Leitor) -> dict[str, Any]:
    _mexe(user)
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
    """Uma linha por caixa: as da EMPRESA para todos; a privada só para o dono/admin."""
    caixas = await saude.caixas_visiveis(session, user)
    saida: dict[str, Any] = {"caixas": [await saude.linha_da_caixa(session, c) for c in caixas]}
    if acesso.pode_mexer(user):
        saida.update(await saude.lojas_sem_caixa_lida(session))
    response.headers["Cache-Control"] = "no-store"
    return saida
