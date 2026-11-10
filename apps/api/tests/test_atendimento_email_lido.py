# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""Leitura manual nas filas: chegada do e-mail, corrida e escopo da pessoa."""

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.models import AtendimentoConversa, AtendimentoMensagem, MailMessage, UserRole
from tests.test_atendimento_email_sites import _cena_da_barra
from tests.test_atendimento_separa_email import URL, admin
from tests.test_mail_ponte import _config, cena, entrar, meta_de, rodar


def _data(valor: str) -> datetime:
    return datetime.fromisoformat(valor.replace("Z", "+00:00"))


async def _linha(client, cid: str) -> dict:
    r = await client.get(f"{URL}/conversas", params={"caixa": "email", "plataforma": "site"})
    assert r.status_code == 200, r.text
    return next(i for i in r.json()["itens"] if i["id"] == cid)


async def test_marcar_lido_respeita_a_chegada_e_nao_resolve_o_chamado(db, cena, client, admin):
    ids = await _cena_da_barra(db, cena)
    meta = await meta_de(db, UUID(ids["sac"]))
    cid = str(meta.conversa_id)
    linha = await _linha(client, cid)
    chegada = await db.scalar(
        select(func.max(AtendimentoMensagem.created_at)).where(
            AtendimentoMensagem.conversa_id == meta.conversa_id
        )
    )
    assert _data(linha["email_visto_ate"]) == chegada
    assert _data(linha["ultima_mensagem_em"]) < chegada
    assert linha["email_lido"] is False
    contagens = (await client.get(f"{URL}/email/contagens")).json()
    original = (await db.get(MailMessage, UUID(ids["sac"]))).content_enc

    r = await client.post(
        f"{URL}/conversas/{cid}/visto", json={"ate": linha["email_visto_ate"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["conversa"]["email_lido"] is True
    assert _data(r.json()["conversa"]["email_visto_ate"]) == chegada
    assert (await _linha(client, cid))["email_lido"] is True
    depois = (await client.get(f"{URL}/email/contagens")).json()
    assert {k: v for k, v in depois.items() if k != "nao_lidas"} == {
        k: v for k, v in contagens.items() if k != "nao_lidas"
    }
    assert r.json()["conversa"]["aguardando_resposta"] is True
    assert r.json()["conversa"]["situacao"] == linha["situacao"]
    assert (await db.get(MailMessage, UUID(ids["sac"]))).content_enc == original

    # Uma importação atrasada pode trazer e-mail enviado ANTES do visto.
    # A confirmação da lista antiga não pode consumir a chegada nova.
    mid = await entrar(
        db,
        cena.geral,
        pasta="*uranyx",
        de="maria@gmail.com",
        para=["sac@uranyx.com.br"],
        assunto="Re: [US-26-0123] SAC — A17 — Maria",
        corpo="Mensagem importada depois de carregar a fila.",
        recebido_em=chegada - timedelta(days=1),
    )
    await rodar(db)
    assert (await meta_de(db, mid)).conversa_id == meta.conversa_id
    nova = await _linha(client, cid)
    assert _data(nova["email_visto_ate"]) > chegada
    assert nova["email_lido"] is False
    r = await client.post(
        f"{URL}/conversas/{cid}/visto", json={"ate": linha["email_visto_ate"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["conversa"]["email_lido"] is False
    assert (await _linha(client, cid))["email_lido"] is False
    r = await client.post(
        f"{URL}/conversas/{cid}/visto", json={"ate": nova["email_visto_ate"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["conversa"]["email_lido"] is True


@pytest.mark.lojas_por_pessoa
async def test_marcar_lido_nao_libera_chamado_fora_do_escopo(
    db, cena, client, admin, make_user, auth_as
):
    ids = await _cena_da_barra(db, cena)
    meta = await meta_de(db, UUID(ids["sac"]))
    cid = meta.conversa_id
    linha = await _linha(client, str(cid))
    auth_as(await make_user(role=UserRole.USER))
    r = await client.post(
        f"{URL}/conversas/{cid}/visto", json={"ate": linha["email_visto_ate"]}
    )
    assert r.status_code == 404, r.text
    assert await db.scalar(
        select(AtendimentoConversa.visto_em).where(AtendimentoConversa.id == cid)
    ) is None
