"""Aba E-mail › Caixas: as PASTAS (como no Tuta) e a LOJA de cada e-mail (09/10/2026).

O dono (print da aba, que mostra só remetente, assunto e data): "visualmente
na aba de e-mail poderia trazer o nome da loja aqui também; deixar separado e
visualmente igual ao Tuta".

  GET /api/mail/mailboxes/{id}/pastas?loja=&pasta=
      As pastas da caixa com a quantidade — "Todas" no topo, as de sistema na
      ordem do Tuta (Entrada, Rascunhos, Enviados, Lixeira, Arquivo, Spam), em
      português, e as pessoais A–Z — e as LOJAS que aparecem na caixa, com a
      quantidade (+ sem loja, segurança, privado). Com `loja`, a quantidade
      das pastas é só a daquela loja; com `pasta`, a das lojas é só a daquela
      pasta (`total` = sem o filtro).
  GET /api/mail/mailboxes/{id}/lista?pasta=&loja=&antes=&limite=
      Uma página de resumos, o mais novo primeiro: data, direção, remetente,
      assunto, anexos, a pasta e o SELO da loja — nunca o texto. O de
      SEGURANÇA não traz o assunto ("e-mail de acesso/código"). `antes` = o
      `proximo` da página anterior. `conversa_id` (13/10/2026) = a conversa
      de E-MAIL da ponte onde está o e-mail (`separa_email`), só para quem vê
      o /atendimento e no escopo da equipe — a aba E-mail abre a conversa por
      ela (vincular ao pedido, responder) sem passar pela Caixa do chat. O
      que a ponte ligou a uma conversa de CHAT (o aviso de até 13/10, a mesma
      mensagem da Amazon pelo Gmail) não leva: o chat não mostra e-mail.

A permissão de LER a caixa da Central (`routers/mail.readable_mailbox`: o dono da
caixa, um admin ou um leitor ativo de "Quem mais vê"; quem não pode recebe 404 —
nem fica sabendo que a caixa existe). O conteúdo continua cifrado: o índice
(`services/mail_atendimento/indice.py`) guarda só a pasta e a loja; o
assunto, o remetente e o nome da pasta são decifrados aqui, na hora, só os da
página. A rota completa o índice quando falta pouco (o resto é do job). Nada
disto muda a ponte nem a Central. Texto de e-mail nunca vai para o log.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import DateTime, Uuid, func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user
from app.deps.team_scope import resolve_team_scope
from app.models import AtendimentoConversa, Integration, Marca, StoreInfo
from app.models.mail import MailMessage
from app.models.mail_atendimento import MailCaixaIndice
from app.models.user import User
from app.routers.atendimento import _nomes_das_lojas
from app.routers.mail import readable_mailbox
from app.services.atendimento import acesso, separa_email
from app.services.atendimento import lojas as lojas_svc
from app.services.mail_atendimento import administracao as mail_admin
from app.services.mail_atendimento import indice, pastas, ponte, regras, rotear

router = APIRouter(prefix="/api/mail", tags=["mail"])
Session = Annotated[AsyncSession, Depends(get_session)]
ActiveUser = Annotated[User, Depends(require_active_user)]

POR_PAGINA = 50
_RE_PASTA = re.compile(r"^(s\d{1,2}|p[0-9a-f]{31}|x)$")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_RE_LOJA = re.compile(rf"^([fim]:{_UUID}|sem_loja|seguranca|privado)$")
_RE_ANTES = re.compile(rf"^(\d{{1,20}})_({_UUID})$")
_EPOCA = datetime(1970, 1, 1, tzinfo=UTC)
_MICRO = timedelta(microseconds=1)
# A plataforma curta do selo ("JLAS2 · ML").
SIGLA = {
    "ml": "ML",
    "shopee": "Shopee",
    "tiktok": "TikTok",
    "amazon": "Amazon",
    "magalu": "Magalu",
    "temu": "Temu",
    "aliexpress": "AliExpress",
    "shein": "Shein",
}


def _invalido(campo: str) -> HTTPException:
    return HTTPException(422, detail={"code": "parametro_invalido", "campo": campo})


def _pasta(valor: str | None) -> str | None:
    if not valor:
        return None
    if not _RE_PASTA.match(valor):
        raise _invalido("pasta")
    return valor


def _loja(valor: str | None) -> str | None:
    if not valor:
        return None
    if not _RE_LOJA.match(valor):
        raise _invalido("loja")
    return valor


def cursor(recebido_em: datetime, message_id: UUID) -> str:
    """O `proximo` da página: microssegundos desde 1970 + o id (sem nada do e-mail)."""
    return f"{(recebido_em - _EPOCA) // _MICRO}_{message_id}"


def _antes(valor: str | None) -> tuple[datetime, UUID] | None:
    if not valor:
        return None
    m = _RE_ANTES.match(valor)
    if not m:
        raise _invalido("antes")
    try:
        return _EPOCA + timedelta(microseconds=int(m.group(1))), UUID(m.group(2))
    except (OverflowError, ValueError):
        raise _invalido("antes") from None


async def rotulos(session: AsyncSession, chaves: set[str], cad: rotear.Cadastro) -> dict[str, dict]:
    """O selo de cada chave de loja: tipo, rótulo ("JLAS2 · ML", "Site Uranyx"), plataforma.

    O nome da loja com integração é o mesmo do /atendimento (`_nomes_das_lojas`);
    a ficha sem integração, o nome do cadastro sem o prefixo da plataforma.
    """

    def ids(prefixo: str) -> set[UUID]:
        return {UUID(c[2:]) for c in chaves if c.startswith(prefixo)}

    fichas, integs, marcas = ids("f:"), ids("i:"), ids("m:")
    por_ficha = {lj.store_info_id: lj for lj in cad.lojas}
    integ_da_ficha = {
        f: por_ficha[f].integration_id
        for f in fichas
        if f in por_ficha and por_ficha[f].integration_id is not None
    }
    # Tuplas (não objetos): o SAVEPOINT de `_nomes_das_lojas` não expira nada daqui.
    linhas_fichas = (
        {
            r[0]: (r[1], r[2])
            for r in (
                await session.execute(
                    select(StoreInfo.id, StoreInfo.account_name, StoreInfo.platform).where(
                        StoreInfo.id.in_(fichas)
                    )
                )
            ).all()
        }
        if fichas
        else {}
    )
    todas_integs = integs | {i for i in integ_da_ficha.values() if i is not None}
    linhas_integs = (
        {
            r[0]: (r[1], str(getattr(r[2], "value", r[2]) or "").lower())
            for r in (
                await session.execute(
                    select(Integration.id, Integration.name, Integration.platform).where(
                        Integration.id.in_(todas_integs)
                    )
                )
            ).all()
        }
        if todas_integs
        else {}
    )
    nomes_marcas = (
        {
            r[0]: r[1]
            for r in (
                await session.execute(select(Marca.id, Marca.nome).where(Marca.id.in_(marcas)))
            ).all()
        }
        if marcas
        else {}
    )
    nomes = await _nomes_das_lojas(session, set(todas_integs))

    def selo_de_loja(nome: str, plataforma: str | None) -> dict:
        sigla = SIGLA.get(plataforma or "")
        return {
            "tipo": indice.SELO_LOJA,
            "rotulo": f"{nome} · {sigla}" if sigla else nome,
            "plataforma": plataforma or None,
            "loja": nome,
        }

    saida: dict[str, dict] = {}
    for chave in chaves:
        if chave in indice.CHAVES_FIXAS:
            saida[chave] = {
                "tipo": chave,
                "rotulo": indice.ROTULO_FIXO[chave],
                "plataforma": None,
                "loja": None,
            }
            continue
        alvo = UUID(chave[2:])
        if chave.startswith("f:"):
            conta, plat_bruta = linhas_fichas.get(alvo, (None, None))
            lj = por_ficha.get(alvo)
            plat_ficha = lj.plataforma if lj else rotear.plataforma_da_ficha(plat_bruta)
            integ = integ_da_ficha.get(alvo)
            nome_ficha = (nomes.get(integ) if integ else None) or lojas_svc.sem_prefixo(
                conta or "", plat_ficha or ""
            )
            saida[chave] = selo_de_loja(nome_ficha or "loja", plat_ficha)
        elif chave.startswith("i:"):
            nome_integ, plat_integ = linhas_integs.get(alvo, (None, None))
            saida[chave] = selo_de_loja(nomes.get(alvo) or nome_integ or "loja", plat_integ)
        else:
            nome_marca = nomes_marcas.get(alvo) or "marca"
            saida[chave] = {
                "tipo": indice.SELO_SITE,
                "rotulo": f"Site {nome_marca}",
                "plataforma": rotear.PLATAFORMA_SITE,
                "loja": nome_marca,
            }
    return saida


def _ordem_da_loja(item: dict) -> tuple:
    fixas = indice.CHAVES_FIXAS
    if item["chave"] in fixas:
        return (1, fixas.index(item["chave"]), "")
    return (0, 0, " ".join(regras.palavras(item["rotulo"])))


async def _contar_lojas(
    session: AsyncSession, mailbox_id: UUID, cad: rotear.Cadastro, *, pasta: str | None, user: User
) -> dict[str, int]:
    caixa = indice.linhas_da_caixa(mailbox_id)
    q = (
        select(caixa.c.chave, func.count())
        .where(mail_admin.filtro_mensagem(user, caixa.c.message_id))
        .group_by(caixa.c.chave)
    )
    if pasta is not None:
        q = q.where(caixa.c.pasta_chave == pasta)
    saida: dict[str, int] = {}
    for chave, n in (await session.execute(q)).all():
        canon = indice.canonica(chave, cad)
        saida[canon] = saida.get(canon, 0) + int(n)
    return saida


async def _contar_pastas(
    session: AsyncSession, mailbox_id: UUID, cad: rotear.Cadastro, *, loja: str | None, user: User
) -> dict[tuple[str, str], int]:
    if loja is None:
        # Sem filtro de loja: só o índice (sem a meta) — o mais barato.
        i = MailCaixaIndice
        q = (
            select(i.pasta_chave, i.pasta_tipo, func.count())
            .where(i.mailbox_id == mailbox_id, mail_admin.filtro_mensagem(user, i.message_id))
            .group_by(i.pasta_chave, i.pasta_tipo)
        )
    else:
        caixa = indice.linhas_da_caixa(mailbox_id)
        q = (
            select(caixa.c.pasta_chave, caixa.c.pasta_tipo, func.count())
            .where(caixa.c.chave.in_(indice.equivalentes(loja, cad)))
            .where(mail_admin.filtro_mensagem(user, caixa.c.message_id))
            .group_by(caixa.c.pasta_chave, caixa.c.pasta_tipo)
        )
    return {(c, t): int(n) for c, t, n in (await session.execute(q)).all()}


async def _nomes_das_pastas(
    session: AsyncSession, mailbox_id: UUID, chaves: set[str], user: User
) -> dict[str, tuple[str, str | None]]:
    """O nome (e o caminho) de cada pasta PESSOAL: do e-mail mais novo dela, decifrado aqui."""
    pessoais = [c for c in chaves if c.startswith("p")]
    if not pessoais:
        return {}
    i = MailCaixaIndice
    amostras = (
        await session.execute(
            select(i.pasta_chave, i.message_id)
            .distinct(i.pasta_chave)
            .where(
                i.mailbox_id == mailbox_id,
                i.pasta_chave.in_(pessoais),
                mail_admin.filtro_mensagem(user, i.message_id),
            )
            .order_by(i.pasta_chave, i.recebido_em.desc(), i.message_id.desc())
        )
    ).all()
    por_id = {mid: chave for chave, mid in amostras}
    saida: dict[str, tuple[str, str | None]] = {}
    for message in (
        await session.scalars(select(MailMessage).where(MailMessage.id.in_(list(por_id))))
    ).all():
        try:
            lida = pastas.do_conteudo(ponte.decifrar(message).conteudo)
            saida[por_id[message.id]] = (indice.nome_da_pasta(lida), lida.caminho)
        except Exception:  # noqa: BLE001 — sem o nome, a pasta continua na lista
            saida[por_id[message.id]] = ("(pasta)", None)
    return saida


@router.get("/mailboxes/{mailbox_id}/pastas")
async def pastas_da_caixa(
    mailbox_id: UUID,
    session: Session,
    user: ActiveUser,
    response: Response,
    loja: str | None = Query(default=None, max_length=40),
    pasta: str | None = Query(default=None, max_length=40),
) -> dict[str, Any]:
    mailbox = await readable_mailbox(session, mailbox_id, user)
    loja, pasta = _loja(loja), _pasta(pasta)
    base, faltam = await indice.preparar(session, mailbox)
    await session.commit()
    if faltam:
        faltando = (
            indice._faltando(base, so_urgente=True)
            .where(mail_admin.filtro_mensagem(user))
            .subquery()
        )
        faltam = int(await session.scalar(select(func.count()).select_from(faltando)) or 0)
    cad = base.cad

    todas = await _contar_pastas(session, mailbox.id, cad, loja=None, user=user)
    filtradas = (
        todas
        if loja is None
        else await _contar_pastas(session, mailbox.id, cad, loja=loja, user=user)
    )
    nomes = await _nomes_das_pastas(session, mailbox.id, {c for c, _t in todas}, user)
    itens = []
    for (chave, tipo_tuta), total in todas.items():
        tipo = indice.tipo_da_chave(chave)
        if tipo == "sistema":
            nome, caminho = indice.NOME_SISTEMA.get(tipo_tuta, tipo_tuta), None
        elif tipo == "ilegivel":
            nome, caminho = indice.NOME_ILEGIVEL, None
        else:
            nome, caminho = nomes.get(chave, ("(pasta)", None))
        itens.append(
            {
                "id": chave,
                "nome": nome,
                "caminho": caminho,
                "tipo": tipo,
                "quantidade": filtradas.get((chave, tipo_tuta), 0),
                "total": total,
                "chave": chave,
            }
        )
    # Duas pastas pessoais com o mesmo nome (pais diferentes): o caminho inteiro.
    repetidos = [i["nome"] for i in itens if i["tipo"] == "pessoal"]
    for item in itens:
        if item["tipo"] == "pessoal" and repetidos.count(item["nome"]) > 1 and item["caminho"]:
            item["nome"] = item["caminho"]
    itens.sort(key=indice.ordem_das_pastas)
    for item in itens:
        item.pop("chave")

    lojas_todas = await _contar_lojas(session, mailbox.id, cad, pasta=None, user=user)
    lojas_filtradas = (
        lojas_todas
        if pasta is None
        else await _contar_lojas(session, mailbox.id, cad, pasta=pasta, user=user)
    )
    selos = await rotulos(session, set(lojas_todas), cad)
    lojas = [
        {
            "chave": chave,
            **selos[chave],
            "quantidade": lojas_filtradas.get(chave, 0),
            "total": total,
        }
        for chave, total in lojas_todas.items()
    ]
    lojas.sort(key=_ordem_da_loja)
    response.headers["Cache-Control"] = "no-store"
    return {
        "pastas": [
            {
                "id": None,
                "nome": "Todas",
                "caminho": None,
                "tipo": "todas",
                "quantidade": sum(filtradas.values()),
                "total": sum(todas.values()),
            },
            *itens,
        ],
        "lojas": lojas,
        "faltam": faltam,
    }


async def _conversas_abriveis(session: AsyncSession, user: User, ids: set[UUID]) -> set[UUID]:
    """As conversas de E-MAIL da ponte destes ids que a pessoa abre no
    /atendimento (quem vê, no escopo da equipe) — uma consulta, só a página."""
    if not ids or not acesso.pode_ver(user):
        return set()
    scope = await resolve_team_scope(session, user)
    c = AtendimentoConversa
    linhas = (
        await session.execute(
            select(c.id, c.integration_id).where(
                c.id.in_(ids),
                separa_email.conversa_de_email_sql(c),
                mail_admin.filtro_conversa(user, c),
            )
        )
    ).all()
    return {
        i
        for i, integration_id in linhas
        if scope.unrestricted
        or (integration_id is not None and integration_id in scope.integration_ids)
    }


@router.get("/mailboxes/{mailbox_id}/lista")
async def lista_da_caixa(
    mailbox_id: UUID,
    session: Session,
    user: ActiveUser,
    response: Response,
    pasta: str | None = Query(default=None, max_length=40),
    loja: str | None = Query(default=None, max_length=40),
    antes: str | None = Query(default=None, max_length=64),
    limite: int = Query(default=POR_PAGINA, ge=1, le=100),
) -> dict[str, Any]:
    mailbox = await readable_mailbox(session, mailbox_id, user)
    pasta, loja, depois_de = _pasta(pasta), _loja(loja), _antes(antes)
    base, faltam = await indice.preparar(session, mailbox)
    await session.commit()
    if faltam:
        faltando = (
            indice._faltando(base, so_urgente=True)
            .where(mail_admin.filtro_mensagem(user))
            .subquery()
        )
        faltam = int(await session.scalar(select(func.count()).select_from(faltando)) or 0)

    caixa = indice.linhas_da_caixa(mailbox.id)
    q = (
        select(caixa)
        .where(mail_admin.filtro_mensagem(user, caixa.c.message_id))
        .order_by(caixa.c.recebido_em.desc(), caixa.c.message_id.desc())
    )
    if pasta is not None:
        q = q.where(caixa.c.pasta_chave == pasta)
    if loja is not None:
        q = q.where(caixa.c.chave.in_(indice.equivalentes(loja, base.cad)))
    if depois_de is not None:
        q = q.where(
            tuple_(caixa.c.recebido_em, caixa.c.message_id)
            < tuple_(
                literal(depois_de[0], DateTime(timezone=True)),
                literal(depois_de[1], Uuid()),
            )
        )
    linhas = (await session.execute(q.limit(limite + 1))).all()
    pagina = linhas[:limite]
    mensagens = (
        {
            m.id: m
            for m in (
                await session.scalars(
                    select(MailMessage).where(MailMessage.id.in_([r.message_id for r in pagina]))
                )
            ).all()
        }
        if pagina
        else {}
    )
    chaves = {indice.canonica(r.chave, base.cad) for r in pagina}
    selos = await rotulos(session, chaves, base.cad)
    abriveis = await _conversas_abriveis(
        session, user, {r.conversa_id for r in pagina if r.conversa_id is not None}
    )

    itens = []
    for r in pagina:
        message = mensagens.get(r.message_id)
        if message is None:  # apagado agora há pouco
            continue
        seguro = r.selo == indice.SELO_SEGURANCA
        chave = indice.canonica(r.chave, base.cad)
        item: dict[str, Any] = {
            "id": str(r.message_id),
            "recebido_em": message.received_at,
            "direcao": message.direction,
            "tem_anexos": message.attachment_count > 0,
            "ponte": r.estado,
            "conversa_id": str(r.conversa_id) if r.conversa_id in abriveis else None,
            "selo": {**selos[chave], "chave": chave, "provavel": r.fonte == indice.FONTE_INDICE},
        }
        try:
            email = ponte.decifrar(message)
            lida = pastas.do_conteudo(email.conteudo)
            pasta_id = indice.chave_da_pasta(message.mailbox_id, lida)
            item.update(
                de=email.de or None,
                de_nome=email.de_nome or None,
                assunto=indice.ASSUNTO_DE_SEGURANCA if seguro else email.assunto,
                assunto_oculto=seguro,
                pasta={
                    "id": pasta_id,
                    "nome": indice.nome_da_pasta(lida),
                    "tipo": indice.tipo_da_chave(pasta_id),
                },
            )
        except Exception:  # noqa: BLE001 — o e-mail ilegível continua na lista, sem nada dele
            item.update(
                de=None,
                de_nome=None,
                assunto=indice.ASSUNTO_DE_SEGURANCA if seguro else "(não foi possível ler)",
                assunto_oculto=True,
                pasta={"id": r.pasta_chave, "nome": indice.NOME_ILEGIVEL, "tipo": "ilegivel"},
            )
        itens.append(item)
    response.headers["Cache-Control"] = "no-store"
    ultimo = pagina[-1] if pagina else None
    return {
        "itens": itens,
        "proximo": cursor(ultimo.recebido_em, ultimo.message_id)
        if ultimo is not None and len(linhas) > limite
        else None,
        "faltam": faltam,
    }
