# ruff: noqa: F401, F811 — fixtures da ponte compartilhadas
"""A política administrativa protege também as cópias dos e-mails no Atendimento.

Estes cenários usam mensagens cifradas reais da Central e a ponte de verdade;
não substituem os filtros de autorização nem inferem acesso pelo comprador.
"""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    MailAttachment,
    MailMessage,
    MailOutbox,
    StoreInfo,
    UserRole,
)
from app.models.mail_atendimento import MailFolder, MailOutboxMeta
from app.security.cipher import encrypt_json
from tests.test_mail_ponte import _config, cena, entrar, meta_de, pedido_existe, rodar
from tests.test_mail_venda_rotas import X, Y, _caso, _chat

ATENDIMENTO = "/api/atendimento"
EMAIL = f"{ATENDIMENTO}/email"
ADMIN = f"{EMAIL}/admin"


async def _salvar(client, auth_as, cena, pessoa, **changes):
    auth_as(cena.dono)
    body = {
        "modo": "padrao",
        "lojas_permitidas": [],
        "lojas_bloqueadas": [],
        "tipos_permitidos": ["informativo", "chamado", "venda"],
        "pedidos_permitidos": [],
        "expected_updated_at": None,
        **changes,
    }
    r = await client.put(f"{ADMIN}/acessos/{cena.geral.id}/{pessoa.id}", json=body)
    assert r.status_code == 200, r.text
    auth_as(pessoa)
    return r.json()


async def _tipo(db, meta, tipo):
    mensagem = await db.get(AtendimentoMensagem, meta.mensagem_id)
    mensagem.payload = {
        **mensagem.payload,
        "mail": {**mensagem.payload["mail"], "tipo_email": tipo},
    }
    c = await db.get(AtendimentoConversa, meta.conversa_id)
    c.dados = {**c.dados, "mail": {**c.dados["mail"], "tipo_email": tipo}}
    await db.commit()


async def _id_anexo(db, mid):
    return await db.scalar(select(MailAttachment.id).where(MailAttachment.message_id == mid))


async def _negado_na_conversa(client, cid):
    for path in (
        f"{ATENDIMENTO}/conversas/{cid}",
        f"{ATENDIMENTO}/conversas/{cid}/abas",
        f"{EMAIL}/conversas/{cid}/emails",
        f"{EMAIL}/conversas/{cid}/emails-da-venda",
        f"{EMAIL}/conversas/{cid}/previa",
    ):
        r = await client.get(path)
        assert r.status_code == 404, (path, r.status_code, r.text)


async def test_sem_politica_mantem_leitura_do_atendimento_sem_liberar_caixa(
    db, cena, client, make_user, auth_as
):
    meta = await _caso(db, cena)
    leitor = await make_user(role=UserRole.USER)
    auth_as(leitor)

    cards = await client.get(f"{EMAIL}/conversas/{meta.conversa_id}/emails")
    assert cards.status_code == 200, cards.text
    assert [c["id"] for c in cards.json()["emails"]] == [str(meta.message_id)]
    assert (await client.get(f"{ATENDIMENTO}/conversas/{meta.conversa_id}")).status_code == 200
    assert (await client.get(f"/api/mail/messages/{meta.message_id}")).status_code == 404


async def test_bloquear_loja_tira_previa_historico_abas_contagens_e_anexos(
    db, cena, client, make_user, auth_as
):
    await pedido_existe(db, cena.ml, X)
    ml = await _caso(
        db, cena, numero=X, corpo="TEXTO BLOQUEADO ML", anexos=[("ml.txt", "text/plain", b"ML")]
    )
    shopee_id = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        para=["22max@tuta.com"],
        de="suporte@shopee.com.br",
        assunto="Chamado ABC12345 aberto na Shopee",
        corpo="TEXTO PERMITIDO SHOPEE",
    )
    await rodar(db)
    shopee = await meta_de(db, shopee_id)
    assert ml.integration_id == cena.ml.id and shopee.integration_id == cena.shopee.id
    leitor = await make_user(role=UserRole.USER)
    await _salvar(
        client,
        auth_as,
        cena,
        leitor,
        lojas_bloqueadas=[f"i:{cena.ml.id}"],
    )

    await _negado_na_conversa(client, ml.conversa_id)
    anexo = await _id_anexo(db, ml.message_id)
    assert (await client.get(f"{EMAIL}/anexos/{anexo}")).status_code == 404
    r = await client.get(f"{ATENDIMENTO}/conversas", params={"caixa": "email"})
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()["itens"]] == [str(shopee.conversa_id)]
    assert "TEXTO BLOQUEADO ML" not in r.text and str(ml.conversa_id) not in r.text
    counts = await client.get(f"{EMAIL}/contagens")
    assert counts.status_code == 200
    assert counts.json()["por_tipo_email"] == {"informativo": 0, "chamado": 1, "venda": 0}
    cards = await client.get(f"{EMAIL}/conversas/{shopee.conversa_id}/emails")
    assert cards.status_code == 200 and "TEXTO PERMITIDO SHOPEE" in cards.text
    # A loja continua sendo uma conversa de chat autorizada: a política é de e-mail.
    chat = await _chat(db, cena)
    sale = await client.get(f"{EMAIL}/venda/{chat.id}")
    assert sale.status_code == 200 and sale.json()["total"] == 0


@pytest.mark.parametrize("dimensao", ["pedido", "tipo"])
async def test_caso_misto_nao_libera_historico_y_mas_venda_x_mantem_email_x(
    db, cena, client, make_user, auth_as, dimensao
):
    await pedido_existe(db, cena.ml, X)
    await pedido_existe(db, cena.ml, Y)
    x = await _caso(db, cena, numero=X, corpo="CORPO X", anexos=[("x.txt", "text/plain", b"X")])
    y = await _caso(
        db, cena, numero=Y, corpo="CORPO Y PRIVADO", anexos=[("y.txt", "text/plain", b"Y")]
    )
    assert x.conversa_id == y.conversa_id
    if dimensao == "tipo":
        await _tipo(db, x, "informativo")
        await _tipo(db, y, "venda")
    chat_x, chat_y = await _chat(db, cena, X), await _chat(db, cena, Y)
    leitor = await make_user(role=UserRole.USER)
    restrictions = (
        {"pedidos_permitidos": [{"loja": f"i:{cena.ml.id}", "numero": X}]}
        if dimensao == "pedido"
        else {"tipos_permitidos": ["informativo"]}
    )
    await _salvar(
        client,
        auth_as,
        cena,
        leitor,
        modo="selecionadas",
        lojas_permitidas=[f"i:{cena.ml.id}"],
        **restrictions,
    )

    # A cópia já gravada do caso contém X e Y; ela inteira fica oculta.
    await _negado_na_conversa(client, x.conversa_id)
    assert (await client.get(f"{ATENDIMENTO}/conversas", params={"caixa": "email"})).json()[
        "itens"
    ] == []
    # A venda X e a Central continuam liberando só o e-mail autorizado X.
    sale_x = await client.get(f"{EMAIL}/venda/{chat_x.id}")
    assert sale_x.status_code == 200, sale_x.text
    assert [i["id"] for i in sale_x.json()["itens"]] == [str(x.message_id)]
    sale_y = await client.get(f"{EMAIL}/venda/{chat_y.id}")
    assert sale_y.status_code == 200 and sale_y.json()["total"] == 0
    for base in ("/api/mail/messages", f"{EMAIL}/venda/{chat_x.id}/emails"):
        permitted = await client.get(f"{base}/{x.message_id}")
        denied = await client.get(f"{base}/{y.message_id}")
        assert permitted.status_code == 200, permitted.text
        assert "CORPO X" in permitted.text
        assert denied.status_code == 404 and "CORPO Y PRIVADO" not in denied.text
    for mid, expected in ((x.message_id, 200), (y.message_id, 404)):
        aid = await _id_anexo(db, mid)
        for base in ("/api/mail/attachments", f"{EMAIL}/anexos"):
            assert (await client.get(f"{base}/{aid}")).status_code == expected


async def test_loja_do_pedido_e_obrigatoria_e_bloqueio_equivalente_prevalece(
    db, cena, client, make_user, auth_as
):
    await pedido_existe(db, cena.ml, X)
    await pedido_existe(db, cena.oliveira_ml, X)
    a = await _caso(db, cena)
    b = await _caso(db, cena, caixa=cena.goslin, para=["mia30@tuta.com"])
    reader = await make_user(role=UserRole.USER)
    # Ter o mesmo número em outra loja não satisfaz a permissão de venda.
    await _salvar(
        client,
        auth_as,
        cena,
        reader,
        pedidos_permitidos=[{"loja": f"i:{cena.oliveira_ml.id}", "numero": X}],
    )
    await _negado_na_conversa(client, a.conversa_id)
    assert (await client.get(f"{EMAIL}/conversas/{b.conversa_id}/emails")).status_code == 200

    other = await make_user(role=UserRole.USER)
    await _salvar(
        client,
        auth_as,
        cena,
        other,
        modo="selecionadas",
        lojas_permitidas=[f"f:{cena.loja_ml.id}"],
        lojas_bloqueadas=[f"i:{cena.ml.id}"],
    )
    # Ficha e integração são a mesma loja; negação ganha da permissão.
    await _negado_na_conversa(client, a.conversa_id)
    assert (await client.get(f"/api/mail/messages/{a.message_id}")).status_code == 404


async def test_lixeira_local_tira_copias_anexos_e_contagens_e_restaura_sem_apagar(
    db, cena, client, make_user, auth_as
):
    await pedido_existe(db, cena.ml, X)
    meta = await _caso(
        db, cena, corpo="CORPO A RESTAURAR", anexos=[("nota.txt", "text/plain", b"NOTA")]
    )
    chat = await _chat(db, cena)
    msg = await db.get(MailMessage, meta.message_id)
    copia = await db.get(AtendimentoMensagem, meta.mensagem_id)
    original, texto_copiado, estado = msg.content_enc, copia.texto, meta.estado
    aid = await _id_anexo(db, meta.message_id)
    auth_as(cena.dono)
    excluded = await client.post(
        f"{ADMIN}/emails/{meta.message_id}/excluir", json={"motivo": "Email duplicado de teste"}
    )
    assert excluded.status_code == 200, excluded.text

    # Admin também não vê o conteúdo apagado pelas rotas normais.
    for user in (cena.dono, await make_user(role=UserRole.USER)):
        auth_as(user)
        await _negado_na_conversa(client, meta.conversa_id)
        assert (await client.get(f"{EMAIL}/anexos/{aid}")).status_code == 404
        assert (await client.get(f"/api/mail/messages/{meta.message_id}")).status_code == 404
        sale = await client.get(f"{EMAIL}/venda/{chat.id}")
        assert sale.status_code == 200 and sale.json()["total"] == 0
        counts = (await client.get(f"{EMAIL}/contagens")).json()
        assert counts["por_tipo_email"] == {"informativo": 0, "chamado": 0, "venda": 0}

    auth_as(cena.dono)
    trash = await client.get(f"{ADMIN}/lixeira")
    assert trash.status_code == 200, trash.text
    assert [i["message_id"] for i in trash.json()["itens"]] == [str(meta.message_id)]
    assert "CORPO A RESTAURAR" not in trash.text
    # Lixeira é lateral e reversível; conector, ponte e cópia não foram destruídos.
    await db.refresh(msg)
    await db.refresh(copia)
    await db.refresh(meta)
    assert (msg.content_enc, copia.texto, meta.estado) == (original, texto_copiado, estado)
    restored = await client.post(f"{ADMIN}/emails/{meta.message_id}/restaurar", json={})
    assert restored.status_code == 200, restored.text
    cards = await client.get(f"{EMAIL}/conversas/{meta.conversa_id}/emails")
    assert cards.status_code == 200 and "CORPO A RESTAURAR" in cards.text
    assert (await client.get(f"{EMAIL}/anexos/{aid}")).content == b"NOTA"
    assert (await client.get(f"{EMAIL}/venda/{chat.id}")).json()["total"] == 1


async def test_concessao_de_caixa_nao_aumenta_escopo_da_equipe_no_atendimento(
    db, cena, client, make_user, auth_as
):
    meta = await _caso(db, cena)
    leitor = await make_user(role=UserRole.USER)
    leitor.sales_teams = [7]
    db.add(
        StoreInfo(
            user_id=cena.dono.id,
            platform="shopee",
            account_name=f"loja-da-equipe-{uuid4()}",
            sales_team=7,
            integration_id=cena.shopee.id,
        )
    )
    await db.commit()
    await _salvar(
        client,
        auth_as,
        cena,
        leitor,
        modo="selecionadas",
        lojas_permitidas=[f"i:{cena.ml.id}"],
    )
    await _negado_na_conversa(client, meta.conversa_id)


@pytest.mark.parametrize("apos_reprocessar", ["ignorado", "sem_meta"])
async def test_lixeira_nao_reaparece_por_copia_apos_meta_ser_reprocessada(
    db, cena, client, auth_as, apos_reprocessar
):
    meta = await _caso(db, cena, corpo="COPIA QUE NAO PODE REAPARECER")
    cid, mid = meta.conversa_id, meta.message_id
    auth_as(cena.dono)
    r = await client.post(f"{ADMIN}/emails/{mid}/excluir", json={})
    assert r.status_code == 200, r.text
    if apos_reprocessar == "sem_meta":
        await db.delete(meta)
    else:
        meta.estado = "ignorado"
    await db.commit()

    # Mesmo sem a ligação auxiliar, payload.mail da cópia aponta ao original.
    await _negado_na_conversa(client, cid)
    r = await client.get(f"{ATENDIMENTO}/conversas", params={"caixa": "email"})
    assert r.status_code == 200, r.text
    assert str(cid) not in r.text and "COPIA QUE NAO PODE REAPARECER" not in r.text


async def test_categoria_legada_com_protocolo_vazio_nao_fura_restricao_de_tipo(
    db, cena, client, make_user, auth_as
):
    meta = await _caso(db, cena, corpo="AVISO LEGADO DE VENDA")
    mensagem = await db.get(AtendimentoMensagem, meta.mensagem_id)
    conversa = await db.get(AtendimentoConversa, meta.conversa_id)
    meta.protocolo, meta.finalidade = "", "vendas"
    for row, field in ((mensagem, "payload"), (conversa, "dados")):
        data = dict(getattr(row, field))
        mail = {k: v for k, v in data["mail"].items() if k not in {"tipo_email", "caso_id"}}
        mail.update({"protocolo": "", "finalidade": "vendas"})
        setattr(row, field, {**data, "mail": mail})
    await db.commit()
    auth_as(cena.dono)
    cards = await client.get(f"{EMAIL}/conversas/{meta.conversa_id}/emails")
    assert cards.status_code == 200, cards.text
    assert cards.json()["emails"][0]["tipo_email"] == "venda"

    leitor = await make_user(role=UserRole.USER)
    await _salvar(client, auth_as, cena, leitor, tipos_permitidos=["chamado"])
    await _negado_na_conversa(client, meta.conversa_id)


async def test_listagem_da_lixeira_nao_expoe_codigo_de_acesso_do_assunto(db, cena, client, auth_as):
    mid = await entrar(
        db,
        cena.geral,
        assunto="Seu código de verificação 482913",
        corpo="Use o código 482913 para entrar.",
    )
    await rodar(db)
    assert (await meta_de(db, mid)).estado == "seguranca"
    auth_as(cena.dono)
    r = await client.post(f"{ADMIN}/emails/{mid}/excluir", json={})
    assert r.status_code == 200, r.text

    r = await client.get(f"{ADMIN}/lixeira")
    assert r.status_code == 200, r.text
    assert [i["message_id"] for i in r.json()["itens"]] == [str(mid)]
    assert "482913" not in r.text and "Use o código" not in r.text


@pytest.mark.parametrize("outro_pedido_existe", [False, True])
async def test_email_unico_que_cita_pedidos_x_e_y_nao_e_liberado_so_por_x(
    db, cena, client, make_user, auth_as, outro_pedido_existe
):
    await pedido_existe(db, cena.ml, X)
    if outro_pedido_existe:
        await pedido_existe(db, cena.ml, Y)
    meta = await _caso(db, cena, numero=X, corpo=f"Pedido {X}; dados privados do pedido {Y}.")
    # Um vínculo manual ao pedido X não apaga as outras referências deste e-mail.
    meta.pedido_marketplace = X
    meta.pedidos_citados = [
        {"pedido": X, "existe": True, "plataforma": "ml"},
        {"pedido": Y, "existe": outro_pedido_existe, "plataforma": "ml"},
    ]
    await db.commit()
    chat = await _chat(db, cena)
    pessoa = await make_user(role=UserRole.USER)
    auth_as(pessoa)
    # Sem política restritiva, não se muda a leitura que a equipe já tinha.
    assert (await client.get(f"{EMAIL}/conversas/{meta.conversa_id}/emails")).status_code == 200
    await _salvar(
        client,
        auth_as,
        cena,
        pessoa,
        modo="selecionadas",
        lojas_permitidas=[f"i:{cena.ml.id}"],
        pedidos_permitidos=[{"loja": f"i:{cena.ml.id}", "numero": X}],
    )
    await _negado_na_conversa(client, meta.conversa_id)
    assert (await client.get(f"/api/mail/messages/{meta.message_id}")).status_code == 404
    sale = await client.get(f"{EMAIL}/venda/{chat.id}")
    assert sale.status_code == 200, sale.text
    assert sale.json()["total"] == 0


async def test_saude_mantem_caixa_da_empresa_sem_politica_e_omite_caixa_bloqueada(
    db, cena, client, make_user, auth_as
):
    await _caso(db, cena)
    pessoa = await make_user(role=UserRole.USER)
    auth_as(pessoa)
    baseline = await client.get(f"{EMAIL}/saude")
    assert baseline.status_code == 200, baseline.text
    assert [c["mailbox_id"] for c in baseline.json()["caixas"]] == [str(cena.geral.id)]
    assert sum(baseline.json()["caixas"][0]["emails"].values()) == 1

    await _salvar(client, auth_as, cena, pessoa, modo="bloqueado")
    blocked = await client.get(f"{EMAIL}/saude")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["caixas"] == []
    assert str(cena.geral.id) not in blocked.text and cena.geral.label not in blocked.text


async def test_saude_filtra_tipo_pedido_envios_pastas_e_lixeira_inclusive_admin(
    db, cena, client, make_user, auth_as
):
    await pedido_existe(db, cena.ml, X)
    await pedido_existe(db, cena.ml, Y)
    info_x = await _caso(db, cena, caso="12345671", numero=X)
    venda_x = await _caso(db, cena, caso="12345672", numero=X)
    info_y = await _caso(db, cena, caso="12345673", numero=Y)
    for meta, tipo in ((info_x, "informativo"), (venda_x, "venda"), (info_y, "informativo")):
        await _tipo(db, meta, tipo)
    pasta = await db.get(MailFolder, info_x.folder_id)
    pasta.revisada, pasta.sumiu_em = False, None
    jobs = []
    for meta in (info_x, venda_x, info_y):
        job = MailOutbox(
            id=uuid4(),
            mailbox_id=cena.geral.id,
            message_id=meta.message_id,
            author_user_id=cena.dono.id,
            request_id=uuid4(),
            content_enc=encrypt_json({"text": "Resposta de teste"}),
            status="uncertain",
        )
        db.add(job)
        await db.flush()
        db.add(
            MailOutboxMeta(
                outbox_id=job.id,
                conversa_id=meta.conversa_id,
                origem="conversa",
            )
        )
        jobs.append(job)
    await db.commit()
    pessoa = await make_user(role=UserRole.USER)
    auth_as(pessoa)
    baseline = await client.get(f"{EMAIL}/saude")
    assert baseline.status_code == 200, baseline.text
    [caixa] = baseline.json()["caixas"]
    assert sum(caixa["emails"].values()) == 3
    assert caixa["envios_a_conferir"] == 3 and caixa["pastas_sem_revisar"] >= 1

    await _salvar(
        client,
        auth_as,
        cena,
        pessoa,
        tipos_permitidos=["informativo"],
        pedidos_permitidos=[{"loja": f"i:{cena.ml.id}", "numero": X}],
    )
    filtered = await client.get(f"{EMAIL}/saude")
    assert filtered.status_code == 200, filtered.text
    [caixa] = filtered.json()["caixas"]
    assert sum(caixa["emails"].values()) == 1
    assert caixa["envios_a_conferir"] == 1 and caixa["pastas_sem_revisar"] is None

    # Libera a exclusão depois de resolver o envio, sem nenhum envio externo.
    jobs[0].status = "sent"
    await db.commit()
    auth_as(cena.dono)
    excluded = await client.post(f"{ADMIN}/emails/{info_x.message_id}/excluir", json={})
    assert excluded.status_code == 200, excluded.text
    health_admin = await client.get(f"{EMAIL}/saude")
    assert health_admin.status_code == 200, health_admin.text
    caixa = next(c for c in health_admin.json()["caixas"] if c["mailbox_id"] == str(cena.geral.id))
    assert sum(caixa["emails"].values()) == 2
    assert caixa["envios_a_conferir"] == 2
