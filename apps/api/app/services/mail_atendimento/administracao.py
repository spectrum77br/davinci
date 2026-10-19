"""Política única de leitura e lixeira local, sem mudar caixa, ponte ou Tuta.

As regras são por pessoa + caixa. Sem linha, o acesso herdado continua igual.
`selecionadas` também concede leitura da Central, sempre filtrada; `padrao`
apenas limita o acesso que a pessoa já tinha. Nenhuma regra amplia a equipe
do Atendimento. Administradores ignoram ACL, mas não a lixeira local.

Uma conversa pode conter mensagens de pedidos/tipos diferentes e prévias já
copiadas. Se alguma mensagem for negada ou excluída, a conversa inteira é
ocultada. E-mails individuais permitidos continuam na Central e no modal da
venda. Assim o histórico de um chamado misto nunca abre conteúdo proibido.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import String, and_, case, cast, false, func, literal, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import AtendimentoConversa, AtendimentoMensagem, Integration, Marca, StoreInfo
from app.models.enums import UserRole, UserStatus
from app.models.mail import MailMailbox, MailMessage, MailOutbox
from app.models.mail_admin import MailAcesso, MailAdminEvento, MailLixeira
from app.models.mail_atendimento import MailCaixaIndice, MailMailboxSettings, MailMessageMeta
from app.models.user import User
from app.schemas.mail_admin import AcessoPut
from app.services.atendimento import separa_email
from app.services.mail_atendimento import classificacao, indice, rotear
from app.services.mail_atendimento.constantes import FINALIDADES_PENDENTES


def _tem(lista, chave):
    # Operadores JSONB customizados precisam agrupar a concatenação da chave:
    # `json ? 'i:' || id` seria lido pelo Postgres como `(json ? 'i:') || id`.
    return func.coalesce(lista.bool_op("?")(chave.self_group()), false())


def _restritiva(p):
    return or_(
        p.modo != "padrao",
        func.jsonb_array_length(p.lojas_permitidas) > 0,
        func.jsonb_array_length(p.lojas_bloqueadas) > 0,
        func.jsonb_array_length(p.pedidos_permitidos) > 0,
        func.jsonb_array_length(p.tipos_permitidos) < 3,
    )


def filtro_mensagem(user: User, message_id=MailMessage.id):
    """Expressão SQL correlata: aplicar ANTES de contar/paginar/decifrar.

    É uma restrição, não uma concessão de caixa/equipe; a rota mantém sua
    autorização original e acrescenta esta expressão. Sem meta/índice que
    prove a loja, uma política por loja/pedido falha fechada.
    """
    tr = aliased(MailLixeira)
    excluida = (
        select(literal(1))
        .where(tr.message_id == message_id, tr.excluido_em.is_not(None))
        .correlate_except(tr)
        .exists()
    )
    if user.role == UserRole.ADMIN:
        return not_(excluida)
    if user.status != UserStatus.ACTIVE:
        return false()
    msg, p, m, i, a = (
        aliased(MailMessage),
        aliased(MailAcesso),
        aliased(MailMessageMeta),
        aliased(MailCaixaIndice),
        aliased(AtendimentoMensagem),
    )
    chave = indice.colunas_do_selo(i, m)["chave"]
    tipo_payload = a.payload["mail"]["tipo_email"].astext
    caso = func.coalesce(
        func.nullif(a.payload["mail"]["caso_id"].astext, ""),
        func.nullif(a.payload["mail"]["protocolo"].astext, ""),
        func.nullif(m.protocolo, ""),
    )
    tipo = case(
        (tipo_payload.in_(classificacao.TIPOS_EMAIL), tipo_payload),
        (or_(m.plataforma == "site", caso.is_not(None)), literal("chamado")),
        (m.finalidade == "vendas", literal("venda")),
        (m.finalidade.in_(FINALIDADES_PENDENTES), literal("chamado")),
        else_=literal("informativo"),
    )
    tem_pedidos = func.jsonb_array_length(p.pedidos_permitidos) > 0
    limita_loja = or_(
        p.modo == "selecionadas",
        tem_pedidos,
        func.jsonb_array_length(p.lojas_bloqueadas) > 0,
    )
    conhecida = or_(chave.like("i:%"), chave.like("f:%"), chave.like("m:%"))
    pedido = func.jsonb_build_array(
        func.jsonb_build_object("loja", chave, "numero", m.pedido_marketplace)
    )
    # Um e-mail que trata de X e Y exige TODAS as provas autorizadas,
    # incluindo número desconhecido/erro da ponte; jamais só o pedido principal.
    citado = func.jsonb_array_elements(m.pedidos_citados).table_valued("value").alias()
    numero_citado = citado.c.value.op("->>")("pedido")
    plataforma_citada = citado.c.value.op("->>")("plataforma")
    prova_permitida = p.pedidos_permitidos.bool_op("@>")(
        func.jsonb_build_array(func.jsonb_build_object("loja", chave, "numero", numero_citado))
    )
    prova_negada = (
        select(literal(1))
        .select_from(citado)
        .where(
            func.coalesce(numero_citado, "") != "",
            or_(
                not_(prova_permitida),
                and_(func.coalesce(plataforma_citada, "") != "", plataforma_citada != m.plataforma),
            ),
        )
        .correlate_except(citado)
        .exists()
    )
    permitido = and_(
        p.modo != "bloqueado",
        or_(
            p.modo != "selecionadas",
            func.coalesce(m.estado, "").notin_(("seguranca", "privado", "interno")),
        ),
        or_(not_(limita_loja), conhecida),
        or_(p.modo != "selecionadas", _tem(p.lojas_permitidas, chave)),
        not_(
            or_(
                _tem(p.lojas_bloqueadas, chave),
                _tem(p.lojas_bloqueadas, literal("i:") + cast(m.integration_id, String)),
                _tem(p.lojas_bloqueadas, literal("f:") + cast(m.store_info_id, String)),
                _tem(p.lojas_bloqueadas, literal("i:") + cast(i.integration_id, String)),
                _tem(p.lojas_bloqueadas, literal("f:") + cast(i.store_info_id, String)),
            )
        ),
        _tem(p.tipos_permitidos, tipo),
        # O índice não conhece o tipo: um filtro de tipo exige a meta da ponte.
        or_(func.jsonb_array_length(p.tipos_permitidos) == 3, m.message_id.is_not(None)),
        or_(
            not_(tem_pedidos),
            and_(
                m.pedido_marketplace.is_not(None),
                p.pedidos_permitidos.bool_op("@>")(pedido),
                not_(prova_negada),
            ),
        ),
    )
    negada = (
        select(literal(1))
        .select_from(msg)
        .join(p, p.mailbox_id == msg.mailbox_id)
        .outerjoin(m, m.message_id == msg.id)
        .outerjoin(i, i.message_id == msg.id)
        .outerjoin(a, a.id == m.mensagem_id)
        .where(msg.id == message_id, p.user_id == user.id, not_(func.coalesce(permitido, false())))
        .correlate_except(msg, p, m, i, a)
        .exists()
    )
    return and_(not_(excluida), not_(negada))


def filtro_conversa(user: User, c=AtendimentoConversa):
    """Não libera timeline/prévia de conversa com qualquer e-mail não visível.

    Confere também `payload.mail.message_id`: reprocessar/apagar meta não
    pode fazer reaparecer um corpo antigo ainda copiado no Atendimento.
    """
    m, a, origem, p = (
        aliased(MailMessageMeta),
        aliased(AtendimentoMensagem),
        aliased(MailMessage),
        aliased(MailAcesso),
    )
    meta_negada = (
        select(literal(1))
        .where(m.conversa_id == c.id, not_(filtro_mensagem(user, m.message_id)))
        .correlate_except(m)
        .exists()
    )
    ref = a.payload["mail"]["message_id"].astext
    existe_original = (
        select(literal(1)).where(cast(origem.id, String) == ref).correlate_except(origem).exists()
    )
    original_permitido = (
        select(literal(1))
        .where(cast(origem.id, String) == ref, filtro_mensagem(user, origem.id))
        .correlate_except(origem)
        .exists()
    )
    tem_restricao = (
        false()
        if user.role == UserRole.ADMIN
        else select(literal(1))
        .where(p.user_id == user.id, _restritiva(p))
        .correlate_except(p)
        .exists()
    )
    copia_negada = (
        select(literal(1))
        .where(
            a.conversa_id == c.id,
            func.coalesce(ref, "") != "",
            or_(
                and_(existe_original, not_(original_permitido)),
                and_(not_(existe_original), tem_restricao),
            ),
        )
        .correlate_except(a)
        .exists()
    )
    tem_meta = select(literal(1)).where(m.conversa_id == c.id).correlate_except(m).exists()
    tem_original = (
        select(literal(1))
        .where(a.conversa_id == c.id, existe_original)
        .correlate_except(a)
        .exists()
    )
    legado_sem_prova = and_(tem_restricao, not_(tem_meta), not_(tem_original))
    return or_(
        not_(separa_email.conversa_de_email_sql(c)),
        and_(not_(meta_negada), not_(copia_negada), not_(legado_sem_prova)),
    )


async def exigir_mensagem(session: AsyncSession, user: User, message_id: UUID) -> None:
    if not await session.scalar(
        select(MailMessage.id).where(MailMessage.id == message_id, filtro_mensagem(user))
    ):
        raise HTTPException(404, detail={"code": "email_nao_encontrado"})


def filtro_caixas(user: User):
    """Acesso herdado OU concessão selecionada; bloqueio vale até para dono."""
    if user.role == UserRole.ADMIN:
        return literal(True)
    if user.status != UserStatus.ACTIVE:
        return false()
    p = aliased(MailAcesso)
    leitores = select(MailMailboxSettings.mailbox_id).where(
        MailMailboxSettings.leitores.contains([str(user.id)])
    )
    herdado = or_(MailMailbox.owner_user_id == user.id, MailMailbox.id.in_(leitores))
    bloqueada = (
        select(literal(1))
        .where(p.mailbox_id == MailMailbox.id, p.user_id == user.id, p.modo == "bloqueado")
        .correlate_except(p)
        .exists()
    )
    concedida = (
        select(literal(1))
        .where(
            p.mailbox_id == MailMailbox.id,
            p.user_id == user.id,
            p.modo == "selecionadas",
            func.jsonb_array_length(p.lojas_permitidas) > 0,
            func.jsonb_array_length(p.tipos_permitidos) > 0,
        )
        .correlate_except(p)
        .exists()
    )
    return and_(not_(bloqueada), or_(herdado, concedida))


async def pode_ver_caixa(session: AsyncSession, mailbox: MailMailbox, user: User) -> bool:
    return bool(
        await session.scalar(
            select(MailMailbox.id).where(MailMailbox.id == mailbox.id, filtro_caixas(user))
        )
    )


async def tem_politica(session: AsyncSession, mailbox_id: UUID, user: User) -> bool:
    return user.role != UserRole.ADMIN and bool(
        await session.scalar(
            select(MailAcesso.user_id).where(
                MailAcesso.mailbox_id == mailbox_id, MailAcesso.user_id == user.id
            )
        )
    )


async def catalogo_lojas(session: AsyncSession):
    """IDs estáveis e chaves canônicas, incluindo contas sem integração e sites."""
    cad = await rotear.cadastro(session)
    conhecidas, itens = set(), {}
    for row in (
        await session.execute(select(StoreInfo.id, StoreInfo.account_name, StoreInfo.platform))
    ).all():
        chave = f"f:{row.id}"
        conhecidas.add(chave)
        itens[chave] = {
            "chave": chave,
            "nome": row.account_name or "Loja",
            "plataforma": rotear.plataforma_da_ficha(
                str(getattr(row.platform, "value", row.platform) or "")
            ),
        }
    for row in (
        await session.execute(select(Integration.id, Integration.name, Integration.platform))
    ).all():
        chave = f"i:{row.id}"
        conhecidas.add(chave)
        canon = indice.canonica(chave, cad)
        itens.setdefault(
            canon,
            {
                "chave": canon,
                "nome": row.name or "Loja",
                "plataforma": str(getattr(row.platform, "value", row.platform) or ""),
            },
        )
    for row in (await session.execute(select(Marca.id, Marca.nome))).all():
        chave = f"m:{row.id}"
        conhecidas.add(chave)
        itens[chave] = {"chave": chave, "nome": row.nome or "Site", "plataforma": "site"}
    return (
        cad,
        conhecidas,
        sorted(itens.values(), key=lambda x: (x["nome"].lower(), x["plataforma"])),
    )


def _canonicas(chaves, cad):
    return sorted({indice.canonica(c, cad) for c in chaves})


async def politica_visao(session: AsyncSession, p: MailAcesso, *, cad=None) -> dict:
    cad = cad or await rotear.cadastro(session)
    autor = await session.get(User, p.updated_by) if p.updated_by else None
    pedidos = {(indice.canonica(x["loja"], cad), x["numero"]) for x in p.pedidos_permitidos}
    return {
        "mailbox_id": str(p.mailbox_id),
        "user_id": str(p.user_id),
        "modo": p.modo,
        "lojas_permitidas": _canonicas(p.lojas_permitidas, cad),
        "lojas_bloqueadas": _canonicas(p.lojas_bloqueadas, cad),
        "tipos_permitidos": p.tipos_permitidos,
        "pedidos_permitidos": [{"loja": loja, "numero": n} for loja, n in sorted(pedidos)],
        "updated_at": p.updated_at,
        "updated_by_nome": (autor.name or autor.email.split("@", 1)[0]) if autor else None,
    }


def _erro(code: str, status=422):
    return HTTPException(status, detail={"code": code})


async def salvar_acesso(
    session: AsyncSession, mailbox_id: UUID, user_id: UUID, body: AcessoPut, autor: User
):
    caixa, alvo = await session.get(MailMailbox, mailbox_id), await session.get(User, user_id)
    if caixa is None:
        raise _erro("mailbox_not_found", 404)
    if alvo is None:
        raise _erro("usuario_nao_encontrado", 404)
    if alvo.role == UserRole.ADMIN:
        raise _erro("admin_ve_todas_as_lojas")
    if body.modo == "selecionadas" and alvo.status != UserStatus.ACTIVE:
        raise _erro("usuario_inativo")
    cad, conhecidas, _ = await catalogo_lojas(session)

    def normalizar(chaves, *, bloquear=False):
        saida = set()
        for raw in chaves:
            if not isinstance(raw, str) or len(raw) != 38 or raw[:2] not in {"i:", "f:", "m:"}:
                raise _erro("loja_invalida")
            try:
                chave = f"{raw[:2]}{UUID(raw[2:])}"
            except ValueError:
                raise _erro("loja_invalida") from None
            if chave not in conhecidas:
                raise _erro("loja_invalida")
            saida.update(indice.equivalentes(chave, cad))
            if bloquear:
                # A incerteza nunca amplia concessão; negação também cobre
                # o legado identificado só pela integração ambígua.
                for loja in cad.lojas:
                    ficha, integ = f"f:{loja.store_info_id}", f"i:{loja.integration_id}"
                    if chave == ficha and loja.integration_id is not None:
                        saida.add(integ)
                    if chave == integ:
                        saida.add(ficha)
        return sorted(saida)

    permitidas = normalizar(body.lojas_permitidas) if body.modo == "selecionadas" else []
    bloqueadas = normalizar(body.lojas_bloqueadas, bloquear=True)
    pedidos = sorted(
        {(loja, x.numero) for x in body.pedidos_permitidos for loja in normalizar([x.loja])}
    )
    # Serializa também a criação da linha ausente, sem sobrescrever a tela de outro admin.
    chave_lock = int.from_bytes(
        hashlib.blake2b(f"mail-acesso:{mailbox_id}:{user_id}".encode(), digest_size=8).digest(),
        signed=True,
    )
    await session.execute(select(func.pg_advisory_xact_lock(chave_lock)))
    p = await session.scalar(
        select(MailAcesso)
        .where(MailAcesso.mailbox_id == mailbox_id, MailAcesso.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (p is None and body.expected_updated_at is not None) or (
        p is not None and body.expected_updated_at != p.updated_at
    ):
        raise _erro("configuracao_mudou", 409)
    if p is None:
        p = MailAcesso(mailbox_id=mailbox_id, user_id=user_id)
        session.add(p)
    p.modo, p.lojas_permitidas, p.lojas_bloqueadas = body.modo, permitidas, bloqueadas
    p.tipos_permitidos = list(dict.fromkeys(body.tipos_permitidos))
    p.pedidos_permitidos = [{"loja": loja, "numero": n} for loja, n in pedidos]
    p.updated_at, p.updated_by = datetime.now(UTC), autor.id
    session.add(
        MailAdminEvento(
            mailbox_id=mailbox_id,
            autor_id=autor.id,
            acao="acesso_salvo",
            dados={
                "user_id": str(user_id),
                "modo": p.modo,
                "lojas_permitidas": permitidas,
                "lojas_bloqueadas": bloqueadas,
                "tipos_permitidos": p.tipos_permitidos,
                "pedidos_permitidos": p.pedidos_permitidos,
            },
        )
    )
    await session.flush()
    return await politica_visao(session, p, cad=cad)


async def mudar_lixeira(
    session: AsyncSession,
    message_id: UUID,
    autor: User,
    *,
    excluir: bool,
    motivo: str | None = None,
):
    message = await session.scalar(
        select(MailMessage).where(MailMessage.id == message_id).with_for_update()
    )
    if message is None:
        raise _erro("email_nao_encontrado", 404)
    item = await session.get(MailLixeira, message_id)
    estava = item is not None and item.excluido_em is not None
    if estava == excluir:
        return {"message_id": str(message_id), "excluido": excluir, "alterado": False}
    if excluir and await session.scalar(
        select(MailOutbox.id)
        .where(
            MailOutbox.message_id == message_id,
            MailOutbox.status.in_(("queued", "leased", "uncertain")),
        )
        .limit(1)
    ):
        raise _erro("email_envio_pendente", 409)
    agora = datetime.now(UTC)
    if item is None:
        item = MailLixeira(message_id=message_id)
        session.add(item)
    if excluir:
        item.excluido_em, item.excluido_por = agora, autor.id
        item.motivo = (motivo or "").replace("\x00", "").strip() or None
        item.restaurado_em, item.restaurado_por = None, None
    else:
        item.excluido_em = None
        item.restaurado_em, item.restaurado_por = agora, autor.id
    session.add(
        MailAdminEvento(
            mailbox_id=message.mailbox_id,
            autor_id=autor.id,
            acao="email_excluido" if excluir else "email_restaurado",
            dados={"message_id": str(message_id)},
        )
    )
    await session.flush()
    return {"message_id": str(message_id), "excluido": excluir, "alterado": True}
