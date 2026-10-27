# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""O filtro de acesso RÁPIDO (`administracao.visao_rapida`, 27/10/2026) = o inteiro.

O Eduardo, 27/10: "otimiza pra abrir rápido" — a barra de lojas levava ~5 s
para quem não é admin (o JIT do Postgres compilando as subconsultas correlatas
de `filtro_conversa`). Quem não tem regra nenhuma em `mail_acessos` (o admin,
ou a pessoa ativa sem linha) vê tudo menos a LIXEIRA local: o atalho troca o
filtro por uma lista de ids pronta. Aqui: o MESMO resultado que o filtro
inteiro, conversa a conversa e e-mail a e-mail — com a lixeira vazia, com o
e-mail excluído (a meta), com a CÓPIA da mensagem que aponta para um original
excluído, com o restaurado — e quem tem regra (ou não está ativo) segue no
filtro inteiro.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.models import AtendimentoConversa, AtendimentoMensagem, MailMessage, UserRole
from app.models.enums import UserStatus
from app.models.mail_admin import MailLixeira
from app.models.mail_atendimento import MailMessageMeta
from app.security.cipher import encrypt_json
from app.services.atendimento import separa_email
from app.services.mail_atendimento import administracao
from tests.test_atendimento_separa_email import admin as admin
from tests.test_atendimento_separa_email_ui import _historico
from tests.test_mail_admin_atendimento import _salvar
from tests.test_mail_ponte import AGORA, _config, cena


async def _conversas(db, filtro) -> set:
    c = AtendimentoConversa
    q = select(c.id).where(separa_email.conversa_de_email_sql(), filtro)
    return {str(i) for i in (await db.execute(q)).scalars()}


async def _emails(db, filtro) -> set:
    return set((await db.execute(select(MailMessage.id).where(filtro))).scalars())


async def _confere(db, user) -> administracao.VisaoRapida:
    visao = await administracao.visao_rapida(db, user)
    assert await _conversas(db, visao.filtro_conversa()) == await _conversas(
        db, administracao.filtro_conversa(user)
    )
    assert await _emails(db, visao.filtro_mensagem()) == await _emails(
        db, administracao.filtro_mensagem(user)
    )
    m = MailMessageMeta
    a = set(
        (
            await db.execute(select(m.message_id).where(visao.filtro_mensagem(m.message_id)))
        ).scalars()
    )
    b = set(
        (
            await db.execute(
                select(m.message_id).where(administracao.filtro_mensagem(user, m.message_id))
            )
        ).scalars()
    )
    assert a == b
    return visao


async def _meta_da(db, conversa_id) -> MailMessageMeta:
    return await db.scalar(
        select(MailMessageMeta).where(MailMessageMeta.conversa_id == conversa_id).limit(1)
    )


async def test_o_atalho_da_o_mesmo_que_o_filtro_inteiro(db, cena, make_user):
    h = await _historico(db, cena)
    leitor = await make_user(role=UserRole.USER)
    todas = await _conversas(db, administracao.filtro_conversa(cena.dono))
    assert {h["ml"], h["oliveira"], h["novo"], h["velho"]} <= todas

    # Lixeira vazia: o atalho não filtra nada (e dá o mesmo).
    for user in (cena.dono, leitor):
        visao = await _confere(db, user)
        assert not visao.exato and not visao.excluidos

    # 1) O e-mail da conversa do ML na lixeira: a conversa some.
    meta_ml = await _meta_da(db, h["ml"])
    db.add(MailLixeira(message_id=meta_ml.message_id, excluido_em=datetime.now(UTC)))
    # 2) A CÓPIA: uma mensagem da conversa da Oliveira aponta para um e-mail
    # (sem meta) que está na lixeira — a conversa some também.
    solto = MailMessage(
        id=uuid4(),
        mailbox_id=cena.geral.id,
        source_id="tuta:SOLTO/1",
        direction="inbound",
        received_at=AGORA,
        content_enc=encrypt_json({"subject": "x", "text": "y"}),
        attachment_count=0,
    )
    db.add(solto)
    await db.flush()
    db.add(MailLixeira(message_id=solto.id, excluido_em=datetime.now(UTC)))
    copia = await db.scalar(
        select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == h["oliveira"]).limit(1)
    )
    copia.payload = {
        **copia.payload,
        "mail": {**copia.payload["mail"], "message_id": str(solto.id)},
    }
    # 3) O RESTAURADO (excluido_em vazio) não esconde nada.
    meta_novo = await _meta_da(db, h["novo"])
    db.add(
        MailLixeira(
            message_id=meta_novo.message_id, excluido_em=None, restaurado_em=datetime.now(UTC)
        )
    )
    await db.commit()

    for user in (cena.dono, leitor):
        visao = await _confere(db, user)
        assert not visao.exato
        assert visao.excluidos == {meta_ml.message_id, solto.id}
        assert visao.conversas_escondidas == {meta_ml.conversa_id, copia.conversa_id}
        assert {str(meta_ml.conversa_id), str(copia.conversa_id)} == {h["ml"], h["oliveira"]}
        vistas = await _conversas(db, visao.filtro_conversa())
        assert h["ml"] not in vistas and h["oliveira"] not in vistas
        assert h["novo"] in vistas


async def test_quem_tem_regra_ou_nao_esta_ativo_segue_no_filtro_inteiro(
    db, cena, client, auth_as, make_user, admin
):
    h = await _historico(db, cena)
    leitor = await make_user(role=UserRole.USER)
    await _salvar(client, auth_as, cena, leitor, lojas_bloqueadas=[f"i:{cena.ml.id}"])
    visao = await _confere(db, leitor)
    assert visao.exato, "com regra em mail_acessos: o filtro inteiro, sem atalho"
    assert h["ml"] not in await _conversas(db, visao.filtro_conversa())
    parado = await make_user(role=UserRole.USER)
    parado.status = UserStatus.SUSPENDED
    await db.commit()
    assert (await _confere(db, parado)).exato
    # O admin nunca tem regra: sempre o atalho.
    assert not (await _confere(db, cena.dono)).exato
