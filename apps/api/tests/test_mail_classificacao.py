# ruff: noqa: F401, F811 — fixtures da ponte compartilhadas
"""Tipos semânticos e um caso externo por plataforma/conta, sem fundir pelo pedido."""

from types import SimpleNamespace

import pytest

from app.models import AtendimentoMensagem
from app.services.mail_atendimento import classificacao, pedido, ponte
from tests.test_mail_ponte import (
    _config,
    cena,
    conversa,
    entrar,
    mensagens,
    meta_de,
    rodar,
)


@pytest.mark.parametrize(
    "plataforma,texto,esperado",
    [
        ("shopee", "Protocolo de atendimento: 1234567890", "1234567890"),
        ("shopee", "Número do protocolo nº SH-123456789", "SH-123456789"),
        ("ml", "Atualização do caso 123456789", "123456789"),
        ("ml", "Case #: 123456789", "123456789"),
        ("ml", "Case ID: 123456789", "123456789"),
        ("aliexpress", "Case No. 76543210", "76543210"),
        ("aliexpress", "Dispute ID: ALI-123456789", "ALI-123456789"),
        ("ml", "Pedido: 2000012345678901", None),
        ("shopee", "Pedido 261013F9WR4SFB. Protocolo aberto.", None),
        ("shopee", "Em caso de problema, consulte o suporte.", None),
        ("ml", "caso " + "1" * 33, None),
    ],
)
def test_caso_somente_com_rotulo_da_plataforma(plataforma, texto, esperado):
    assert classificacao.caso_explicito(plataforma, texto) == esperado


def test_casos_diferentes_no_mesmo_email_sao_ambiguos():
    c = classificacao.classificar(
        plataforma="ml", finalidade="vendas", assunto="Caso 1234567", texto="Caso 7654321"
    )
    assert c.tipo_email == "chamado" and c.caso_id is None and c.casos_ambiguos


def test_identificador_longo_nao_e_truncado_nem_liberado_para_agrupar_pelo_pedido():
    c = classificacao.classificar(plataforma="ml", finalidade="vendas", assunto="Caso: " + "1" * 33)
    assert c.tipo_email == "chamado" and c.caso_id is None and c.casos_ambiguos


@pytest.mark.parametrize(
    "assunto,finalidade,corpo,tipo",
    [
        ("Nova venda!", "problema", "Em caso de problema, abra um chamado.", "venda"),
        ("Pedido aprovado", "mensagens", "Para reclamação, fale com a Central de Ajuda.", "venda"),
        (
            "Novidades da semana",
            "reclamacao",
            "Em caso de problema, abra um chamado.",
            "informativo",
        ),
        ("Seu pedido foi enviado", "vendas", "Reclamações na Central de Ajuda.", "informativo"),
        ("Problema na entrega", "problema", "Seu pacote está atrasado.", "informativo"),
        ("Aviso de mercadoria retida", "problema", "A mercadoria está retida.", "informativo"),
        ("Sua solicitação foi aberta", "mensagens", "Aguarde a análise.", "chamado"),
        ("Atualização do seu chamado", "vendas", "A análise continua.", "chamado"),
        ("Notificação", "problema", "Aguardamos sua resposta para analisar.", "chamado"),
    ],
)
def test_tipo_nao_e_finalidade_nem_palavra_solta_no_rodape(assunto, finalidade, corpo, tipo):
    c = classificacao.classificar(
        plataforma="shopee", finalidade=finalidade, assunto=assunto, texto=corpo
    )
    assert c.tipo_email == tipo


def test_comprador_direto_mantem_atendimento_sem_inventar_caso():
    c = classificacao.classificar(
        plataforma="ml",
        finalidade="mensagens",
        assunto="Dúvida",
        texto="Quando chega?",
        remetente_tipo="comprador",
    )
    assert c.tipo_email == "chamado" and c.caso_id is None and c.tipo_inferido


def test_fallback_legado_e_rotulado_como_inferido_sem_mudar_estado():
    meta = SimpleNamespace(finalidade="problema", plataforma="ml", protocolo=None)
    assert classificacao.tipos_de_meta(meta) == {
        "tipo_email": "chamado",
        "caso_id": None,
        "tipo_email_inferido": True,
    }
    assert classificacao.tipos_de_meta(meta, {"mail": {"tipo_email": "informativo"}}) == {
        "tipo_email": "informativo",
        "caso_id": None,
        "tipo_email_inferido": False,
    }


async def _email_caso(
    db,
    cena,
    *,
    assunto="Protocolo: 123456789",
    corpo="Aguardamos sua resposta.",
    pasta="problema shopee",
    para=None,
    **kwargs,
):
    mid = await entrar(
        db,
        kwargs.pop("caixa", cena.geral),
        pasta=pasta,
        de=kwargs.pop("de", "support@shopee.com.br"),
        assunto=assunto,
        corpo=corpo,
        para=para,
        **kwargs,
    )
    await rodar(db)
    return await meta_de(db, mid)


async def test_mesmo_protocolo_em_fios_diferentes_vira_um_chamado_sem_pedido(db, cena):
    a = await _email_caso(db, cena, tuta={"conversation_id": "fio-primeiro"})
    b = await _email_caso(db, cena, tuta={"conversation_id": "fio-segundo"})
    assert a.conversa_id == b.conversa_id
    assert a.estado == b.estado == "gravado"
    assert a.protocolo == b.protocolo == "123456789"
    assert b.vinculado_por == "caso" and b.pedido_marketplace is None
    c = await conversa(db, a.conversa_id)
    assert c.dados["fonte"] == "tuta" and c.canal == "email"
    assert c.dados["mail"]["tipo_email"] == "chamado"
    assert c.dados["mail"]["caso_id"] == "123456789"
    assert len(await mensagens(db, c.id)) == 2


async def test_casos_diferentes_mesmo_pedido_fio_e_referencia_nao_se_fundem(db, cena, monkeypatch):
    async def existe(*args, **kwargs):
        return True

    monkeypatch.setattr(pedido, "existe_na_loja", existe)
    a = await _email_caso(
        db,
        cena,
        corpo="Protocolo: 1111111. Pedido 261013F9WR4SFB.",
        assunto="Atendimento",
        message_id="<caso1@exemplo.test>",
        tuta={"conversation_id": "mesmo-fio"},
    )
    b = await _email_caso(
        db,
        cena,
        corpo="Protocolo: 2222222. Pedido 261013F9WR4SFB.",
        assunto="Atendimento",
        in_reply_to="<caso1@exemplo.test>",
        tuta={"conversation_id": "mesmo-fio"},
    )
    assert a.conversa_id != b.conversa_id
    assert a.pedido_marketplace == b.pedido_marketplace == "261013F9WR4SFB"
    assert (await conversa(db, a.conversa_id)).dados["mail"]["caso_id"] == "1111111"
    assert (await conversa(db, b.conversa_id)).dados["mail"]["caso_id"] == "2222222"


async def test_caso_igual_em_contas_ou_plataformas_diferentes_nao_se_funde(db, cena):
    a = await _email_caso(db, cena)
    b = await _email_caso(db, cena, caixa=cena.goslin, para=["mia30@tuta.com"])
    c = await _email_caso(
        db,
        cena,
        pasta="problema ml",
        assunto="Case: 123456789",
        de="nao-responder@mercadolivre.com.br",
    )
    assert all(m.conversa_id is not None and not m.suspeito for m in (a, b, c))
    assert len({a.conversa_id, b.conversa_id, c.conversa_id}) == 3


async def test_aviso_do_pedido_nao_entra_no_caso_do_mesmo_pedido(db, cena, monkeypatch):
    async def existe(*args, **kwargs):
        return True

    monkeypatch.setattr(pedido, "existe_na_loja", existe)
    caso = await _email_caso(
        db,
        cena,
        corpo="Protocolo: 1111111. Pedido 261013F9WR4SFB.",
        assunto="Atendimento",
    )
    aviso = await _email_caso(
        db,
        cena,
        corpo="Pedido 261013F9WR4SFB.",
        assunto="Seu pedido foi enviado",
    )
    assert aviso.conversa_id != caso.conversa_id
    assert (await conversa(db, aviso.conversa_id)).dados["mail"]["tipo_email"] == "informativo"


async def test_casos_ambiguos_nao_se_fundem_pelo_pedido_ou_fio(db, cena, monkeypatch):
    async def existe(*args, **kwargs):
        return True

    monkeypatch.setattr(pedido, "existe_na_loja", existe)
    a = await _email_caso(
        db,
        cena,
        corpo="Pedido 261013F9WR4SFB.",
        tuta={"conversation_id": "fio"},
    )
    b = await _email_caso(
        db,
        cena,
        assunto="Protocolo: 123456789 e Protocolo: 987654321",
        corpo="Pedido 261013F9WR4SFB.",
        tuta={"conversation_id": "fio"},
    )
    assert a.conversa_id != b.conversa_id
    assert b.protocolo is None
    assert any(x["codigo"] == "casos_ambiguos" for x in b.alertas)


@pytest.mark.parametrize(
    "assunto,tipo", [("Novidades da semana", "informativo"), ("Nova venda", "venda")]
)
async def test_info_e_venda_sem_pedido_nao_ficam_sem_vinculo(db, cena, assunto, tipo):
    m = await _email_caso(db, cena, assunto=assunto, corpo="Em caso de problema, abra um chamado.")
    assert m.estado == "gravado" and m.pedido_marketplace is None
    c = await conversa(db, m.conversa_id)
    assert c.dados["mail"]["tipo_email"] == tipo
    assert c.sem_resposta_necessaria and not c.aguardando_resposta
    mensagem = await db.get(AtendimentoMensagem, m.mensagem_id)
    assert mensagem.payload["mail"]["tipo_email"] == tipo
    assert mensagem.payload["mail"]["caso_id"] is None


async def test_reexecucao_nao_duplica_caso_nem_mensagem(db, cena):
    m = await _email_caso(db, cena)
    assert await ponte.processar(db, m.message_id) is None
    await db.commit()
    assert len(await mensagens(db, m.conversa_id)) == 1


async def test_reply_sem_numero_segue_referencia_do_caso_e_nao_fio_reutilizado(db, cena):
    a = await _email_caso(
        db,
        cena,
        assunto="Protocolo: 1111111",
        message_id="<casoA@exemplo.test>",
        tuta={"conversation_id": "fio-comum"},
    )
    b = await _email_caso(
        db,
        cena,
        assunto="Protocolo: 2222222",
        message_id="<casoB@exemplo.test>",
        tuta={"conversation_id": "fio-comum"},
    )
    resposta = await _email_caso(
        db,
        cena,
        assunto="Dados solicitados",
        corpo="Segue a informação.",
        in_reply_to="<casoB@exemplo.test>",
        tuta={"conversation_id": "fio-comum"},
    )
    assert resposta.conversa_id == b.conversa_id != a.conversa_id
    assert resposta.protocolo == "2222222"
    sem_ref = await _email_caso(
        db,
        cena,
        assunto="Dados solicitados",
        corpo="Segue a informação.",
        tuta={"conversation_id": "fio-comum"},
    )
    assert sem_ref.conversa_id not in (a.conversa_id, b.conversa_id)
    assert any(x["codigo"] == "casos_ambiguos" for x in sem_ref.alertas)


async def test_protocolo_so_no_historico_citado_nao_reclassifica_aviso(db, cena):
    m = await _email_caso(
        db,
        cena,
        assunto="Seu pedido foi enviado",
        corpo="Seu pedido foi enviado.\n\nEm 01/10/2026, Suporte escreveu:\n> Protocolo: 123456789",
    )
    assert m.protocolo is None
    assert (await conversa(db, m.conversa_id)).dados["mail"]["tipo_email"] == "informativo"


async def test_suspeito_nao_entra_em_caso_existente_nem_por_fio_ou_referencia(db, cena):
    oficial = await _email_caso(
        db, cena, message_id="<oficial@shopee.test>", tuta={"conversation_id": "fio"}
    )
    antes = list(await mensagens(db, oficial.conversa_id))
    suspeito = await _email_caso(
        db,
        cena,
        in_reply_to="<oficial@shopee.test>",
        tuta={"conversation_id": "fio", "phishing_status": "1"},
    )
    assert suspeito.suspeito and suspeito.conversa_id != oficial.conversa_id
    assert len(await mensagens(db, oficial.conversa_id)) == len(antes) == 1
    assert suspeito.vinculado_por == "suspeito"
    assert any(x["codigo"] == "suspeito_sem_agrupamento" for x in suspeito.alertas)
    # Mesmo sem número no texto novo, a thread não faz o suspeito entrar.
    sem_numero = await _email_caso(
        db,
        cena,
        assunto="Resposta",
        corpo="Segue informação.",
        in_reply_to="<oficial@shopee.test>",
        tuta={"conversation_id": "fio", "phishing_status": "1"},
    )
    assert sem_numero.conversa_id not in (oficial.conversa_id, suspeito.conversa_id)


async def test_caso_de_suspeito_nao_vira_destino_do_futuro_email_oficial(db, cena):
    suspeito = await _email_caso(db, cena, tuta={"phishing_status": "1"})
    oficial = await _email_caso(db, cena)
    assert suspeito.suspeito and not oficial.suspeito
    assert suspeito.protocolo == oficial.protocolo
    assert suspeito.conversa_id != oficial.conversa_id


async def test_comprador_citando_caso_oficial_fica_isolado(db, cena):
    oficial = await _email_caso(
        db, cena, message_id="<oficial@shopee.test>", tuta={"conversation_id": "fio"}
    )
    comprador = await _email_caso(
        db,
        cena,
        de="maria@gmail.com",
        in_reply_to="<oficial@shopee.test>",
        tuta={"conversation_id": "fio"},
    )
    assert comprador.remetente_tipo == "comprador" and not comprador.suspeito
    assert comprador.protocolo == oficial.protocolo
    assert comprador.conversa_id != oficial.conversa_id
    assert comprador.vinculado_por == "caso_nao_oficial"
    assert any(a["codigo"] == "caso_remetente_nao_oficial" for a in comprador.alertas)
    assert len(await mensagens(db, oficial.conversa_id)) == 1


async def test_caso_citado_por_comprador_nao_vira_destino_oficial(db, cena):
    comprador = await _email_caso(db, cena, de="maria@gmail.com")
    oficial = await _email_caso(db, cena)
    assert comprador.conversa_id != oficial.conversa_id


async def test_comprador_desconhecido_nao_entra_em_caso_por_referencia_sem_id(db, cena):
    oficial = await _email_caso(
        db, cena, message_id="<oficial@shopee.test>", tuta={"conversation_id": "fio"}
    )
    comprador = await _email_caso(
        db,
        cena,
        de="maria@gmail.com",
        assunto="Retorno",
        corpo="Tenho uma dúvida.",
        in_reply_to="<oficial@shopee.test>",
        tuta={"conversation_id": "fio"},
    )
    assert comprador.conversa_id != oficial.conversa_id
    assert comprador.vinculado_por == "caso_nao_oficial"


async def test_cliente_ja_conhecido_pode_responder_por_referencia_sem_id(db, cena):
    oficial = await _email_caso(db, cena, message_id="<oficial@shopee.test>")
    c = await conversa(db, oficial.conversa_id)
    c.comprador_id = "maria@gmail.com"  # contato já conferido da conversa
    await db.commit()
    retorno = await _email_caso(
        db,
        cena,
        de="maria@gmail.com",
        assunto="Retorno",
        corpo="Segue minha resposta.",
        in_reply_to="<oficial@shopee.test>",
    )
    assert retorno.conversa_id == oficial.conversa_id
    assert retorno.vinculado_por == "referencia"
