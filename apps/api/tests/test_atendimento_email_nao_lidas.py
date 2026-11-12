# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""Notificações das filas usam leitura local, separada da pendência operacional."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.models import AtendimentoConversa, AtendimentoMensagem
from app.models.mail_admin import MailLixeira
from app.models.mail_atendimento import MailMessageMeta
from tests.lojas_da_pessoa import dar_lojas
from tests.test_atendimento_email_lidos_lote import _conversa
from tests.test_atendimento_email_selecao import _raw
from tests.test_atendimento_separa_email import URL, admin
from tests.test_mail_ponte import _config, cena

CONTAGENS = f"{URL}/email/contagens"
pytestmark = pytest.mark.lojas_por_pessoa


def _workflow(r):
    return {k: r[k] for k in ("aguardando", "chamados", "chamados_por_tipo", "por_tipo_email")}


async def _contagens(client):
    normal = await client.get(CONTAGENS)
    lojas = await client.get(CONTAGENS, params={"por_loja": "true"})
    assert normal.status_code == lojas.status_code == 200, (normal.text, lojas.text)
    a, b = normal.json(), lojas.json()
    assert a["nao_lidas"] == b["nao_lidas"]
    assert _workflow(a) == _workflow(b)
    return a


async def test_nao_lidas_tem_a_mesma_regra_da_lista_sem_mudar_totais(db, cena, client, admin):
    await _conversa(db)
    await _conversa(db, caixa="atacado", aguardando=False)
    await _conversa(db, plataforma="ml", integration_id=cena.ml.id, tipo="informativo")
    await _conversa(db, caixa="duvidas", situacao="fechada")
    await _conversa(db, canal="chat")
    lida = await _conversa(db)
    lida.visto_em = datetime.now(UTC)
    sem_msg = await _conversa(db)
    sem_msg_lida = await _conversa(db)
    sem_msg_lida.visto_em = datetime.now(UTC)
    await db.execute(delete(AtendimentoMensagem).where(
        AtendimentoMensagem.conversa_id.in_([sem_msg.id, sem_msg_lida.id])
    ))
    await db.commit()
    r = await _contagens(client)
    assert r["nao_lidas"] == {
        "total": 4, "chamados": 3, "aguardando": 4,
        "chamados_por_tipo": {"sac": 2, "atacado": 1, "duvidas": 0},
        "por_tipo_email": {"informativo": 1, "chamado": 3, "venda": 0},
    }
    # Fechada ainda aguardando continua no mesmo recorte do contador operacional.
    assert r["chamados"] == 5 and r["aguardando"] == 6
    lista = await client.get(f"{URL}/conversas", params={"caixa": "email"})
    assert lista.status_code == 200, lista.text
    assert sum(c["email_lido"] is False for c in lista.json()["itens"]) == 4


async def test_individual_lote_e_importacao_nova_atualizam_so_notificacoes(
    db, cena, client, admin
):
    sac = await _conversa(db)
    await _conversa(db, caixa="atacado")
    await _conversa(db, plataforma="ml", integration_id=cena.ml.id, tipo="venda")
    # Várias mensagens da mesma conversa continuam sendo uma notificação.
    db.add(AtendimentoMensagem(
        conversa_id=sac.id, externo_id=uuid4().hex, autor="loja", origem="externo",
        texto="Outra mensagem", enviada_em=datetime.now(UTC)-timedelta(days=2),
    ))
    await db.commit()
    antes = await _contagens(client)
    assert antes["nao_lidas"]["total"] == 3
    lista = (await client.get(f"{URL}/conversas", params={"caixa": "email"})).json()["itens"]
    linha = next(c for c in lista if c["id"] == str(sac.id))
    r = await client.post(f"{URL}/conversas/{sac.id}/visto", json={"ate": linha["email_visto_ate"]})
    assert r.status_code == 200, r.text
    depois = await _contagens(client)
    assert depois["nao_lidas"]["total"] == 2 and depois["nao_lidas"]["chamados"] == 1
    assert _workflow(depois) == _workflow(antes)
    r = await client.post(f"{URL}/email/marcar-todos-lidos", json={"fila": "todas"})
    assert r.status_code == 200 and r.json() == {"conversas": 2}, r.text
    zeradas = await _contagens(client)
    assert zeradas["nao_lidas"] == {
        "total": 0, "chamados": 0, "aguardando": 0,
        "chamados_por_tipo": {"sac": 0, "atacado": 0, "duvidas": 0},
        "por_tipo_email": {"informativo": 0, "chamado": 0, "venda": 0},
    }
    assert _workflow(zeradas) == _workflow(antes)
    # A leitura considera a chegada até para autor loja e envio antigo.
    db.add(AtendimentoMensagem(
        conversa_id=sac.id, externo_id=uuid4().hex, autor="loja", origem="externo",
        texto="Importação atrasada", enviada_em=datetime.now(UTC)-timedelta(days=3),
    ))
    await db.commit()
    nova = await _contagens(client)
    assert nova["nao_lidas"]["total"] == nova["nao_lidas"]["chamados"] == 1
    assert nova["nao_lidas"]["chamados_por_tipo"]["sac"] == 1
    assert _workflow(nova) == _workflow(antes)


async def test_notificacoes_respeitam_lojas_acl_e_lixeira(
    db, cena, client, make_user, auth_as
):
    user = await make_user(permissions={"atendimento_email_aba": {"view": True}})
    await dar_lojas(db, user, cena.ml)
    minha = await _conversa(db, plataforma="ml", integration_id=cena.ml.id)
    apagada = await _conversa(db, plataforma="ml", integration_id=cena.ml.id)
    await _conversa(db, plataforma="shopee", integration_id=cena.shopee.id)
    await _conversa(db)
    await _conversa(db, plataforma="ml", canal="chat", integration_id=cena.ml.id)
    for c in (minha, apagada):
        mid = await _raw(db, cena, integration_id=cena.ml.id)
        meta = await db.get(MailMessageMeta, mid)
        meta.conversa_id = c.id
        if c.id == apagada.id:
            db.add(MailLixeira(message_id=mid, excluido_em=datetime.now(UTC)))
        await db.commit()
    auth_as(user)
    r = await _contagens(client)
    assert r["nao_lidas"]["total"] == r["nao_lidas"]["aguardando"] == 1
    assert r["nao_lidas"]["chamados"] == 0
    assert await db.scalar(select(AtendimentoConversa.visto_em).where(
        AtendimentoConversa.id == minha.id
    )) is None
