# ruff: noqa: F401, F811 — fixtures compartilhadas, sem rede/modelo/envio real
"""Cartão já acolhido pode sair da fila; dúvida, foto e ação humana permanecem."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoMensagem,
    AtendimentoRascunho,
    AtendimentoRegra,
    IntegrationPlatform,
)
from app.services.atendimento import gravar, ia, sem_demanda
from app.services.atendimento.constantes import ETIQUETA_AG_CANCELAMENTO, ORIGEM_NOTA
from tests.test_atendimento_ia import (
    PEDIDO_MKT,
    _conversa,
    _msg,
    _pedido_completo,
    envios,
    ia_ligada,
    modelo,
)

MENU = "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!"
AGORA = datetime(2026, 10, 19, 18, tzinfo=UTC)


def _mensagem(autor="cliente", texto=None, *, tipo="produto", minutos=5, payload=None, **outros):
    return SimpleNamespace(
        **{
            "id": uuid4(),
            "autor": autor,
            "texto": texto,
            "tipo": tipo,
            "payload": payload if payload is not None else {"message_type": "item"},
            "origem": "cliente" if autor == "cliente" else "externo",
            "status": "recebida" if autor == "cliente" else "enviada",
            "enviada_em": AGORA - timedelta(minutes=minutos),
            **outros,
        }
    )


def _card_e_bot():
    card = _mensagem()
    return card, _mensagem("loja", MENU, tipo="texto", payload={}, minutos=4)


def test_so_cartao_acolhido_pelo_menu_pode_ser_processado():
    card, bot = _card_e_bot()
    assert sem_demanda.cartao_ja_acolhido([card, bot], card)
    assert not sem_demanda.cartao_ja_acolhido([card], card)


@pytest.mark.parametrize("texto", ["Qual o tamanho?", "6", "bom dia", "Não recebi meu pedido"])
def test_bot_e_card_nao_apagam_texto_pendente(texto):
    card, bot = _card_e_bot()
    pergunta = _mensagem(texto=texto, tipo="texto", payload={}, minutos=6)
    assert not sem_demanda.cartao_ja_acolhido([pergunta, card, bot], card)
    assert not sem_demanda.cartao_ja_acolhido([card, bot, pergunta], pergunta)


@pytest.mark.parametrize("tipo", ["imagem", "video", "audio", "arquivo"])
def test_foto_ou_anexo_mesmo_muito_antigo_nao_desaparece_com_novo_card(tipo):
    card, bot = _card_e_bot()
    foto = _mensagem(tipo=tipo, payload={}, minutos=60 * 24 * 90)
    assert not sem_demanda.cartao_ja_acolhido([foto, card, bot], card)


@pytest.mark.parametrize("dias", [0, 1, 6, 7, 90])
def test_so_figurinha_estruturada_de_visita_antiga_pode_ser_ignorada(dias):
    card, bot = _card_e_bot()
    figurinha = _mensagem(
        texto="[Figurinha]",
        tipo="outro",
        payload={"message_type": "sticker", "content": {"sticker_id": "0001"}},
        minutos=5 + 60 * 24 * dias,
    )
    assert sem_demanda.cartao_ja_acolhido([figurinha, card, bot], card) is (dias >= 7)
    figurinha.texto = "Quero devolver"
    assert not sem_demanda.cartao_ja_acolhido([figurinha, card, bot], card)


def test_historico_incompleto_e_menu_falhado_nao_autorizam():
    card, bot = _card_e_bot()
    recentes = ia.HistoricoMensagens([card, bot], turno_incompleto=True)
    assert not sem_demanda.cartao_ja_acolhido(recentes, card)
    bot.status = "falhou"
    assert not sem_demanda.cartao_ja_acolhido([card, bot], card)


def test_pergunta_ja_respondida_nao_contamina_visita_nova_so_com_card():
    card, bot = _card_e_bot()
    pergunta = _mensagem(texto="Meu pedido não chegou", tipo="texto", payload={}, minutos=60)
    resposta = _mensagem("loja", "O pedido foi entregue.", payload={}, minutos=50)
    assert sem_demanda.cartao_ja_acolhido([pergunta, resposta, card, bot], card)


async def _cenario(
    db,
    make_user,
    ia_ligada,
    monkeypatch,
    *,
    plataforma="shopee",
    canal="chat",
    modo="auto",
    categorias=("saudacao_ou_vaga",),
    criar_regras=True,
    externo_id="conv-1",
):
    ia_ligada.atendimento_auto_ativo = True
    conversa = await _conversa(
        db,
        make_user,
        plataforma=plataforma,
        canal=canal,
        platform=IntegrationPlatform(plataforma),
        pedido=None,
        modo=modo,
        auto_categorias=list(categorias),
        externo_id=externo_id,
    )
    aprovada = AtendimentoRegra(
        tipo="categoria",
        categoria="saudacao_ou_vaga",
        quando=sem_demanda.REGRA_QUANDO,
        faca=sem_demanda.REGRA_FACA,
        ativa=True,
    )
    if criar_regras:
        db.add(aprovada)
    else:
        aprovada = await db.scalar(
            select(AtendimentoRegra).where(AtendimentoRegra.quando == sem_demanda.REGRA_QUANDO)
        )
    card, _ = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="card-sozinho",
        autor="cliente",
        texto=None,
        tipo="produto",
        enviada_em=datetime.now(UTC) - timedelta(minutes=5),
        payload=(
            {"type": "PRODUCT_CARD", "content": {"product_id": "123"}}
            if plataforma == "tiktok"
            else {"message_type": "item", "content": {"item_id": 123}}
        ),
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="bot-menu",
        autor="loja",
        texto=MENU,
        tipo="texto",
        origem="externo",
        enviada_em=datetime.now(UTC) - timedelta(minutes=4),
    )
    await db.commit()
    return conversa, card, aprovada


async def test_processa_sem_modelo_sem_envio_e_nova_pergunta_reabre(
    db, make_user, ia_ligada, modelo, envios, monkeypatch
):
    conversa, card, _ = await _cenario(db, make_user, ia_ligada, monkeypatch)
    antiga = AtendimentoRascunho(
        conversa_id=conversa.id,
        mensagem_gatilho_id=card.id,
        status="bloqueado",
        motivo=sem_demanda.MOTIVO_CARD_ANTIGO,
        precisa_humano=True,
        validador_ok=False,
        fatos={},
    )
    db.add(antiga)
    await db.commit()
    assert await sem_demanda.processar(db, conversa.id, canal_autorizado_id=conversa.canal_id)
    assert conversa.sem_resposta_necessaria and not conversa.aguardando_resposta
    assert antiga.status == "substituido"
    assert conversa.dados[sem_demanda.CHAVE_AUDITORIA]["gatilho_id"] == str(card.id)
    assert conversa.dados["humano"]["motivos"] == []
    assert modelo.chamadas == [] and envios == []
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AtendimentoMensagem)
            .where(AtendimentoMensagem.conversa_id == conversa.id)
        )
        == 2
    )
    await _msg(db, conversa, "Qual o tamanho da pulseira?", ha=timedelta(seconds=1))
    assert not conversa.sem_resposta_necessaria and conversa.aguardando_resposta
    assert not await sem_demanda.processar(db, conversa.id, canal_autorizado_id=conversa.canal_id)


@pytest.mark.parametrize("trava", ["pausada", "atribuida", "reclamacao", "fechada", "outro_canal"])
async def test_preserva_intervencao_humana_e_estado_da_conversa(
    db, make_user, ia_ligada, trava, monkeypatch
):
    conversa, _, _ = await _cenario(db, make_user, ia_ligada, monkeypatch)
    canal_id = conversa.canal_id
    if trava == "pausada":
        conversa.ia_pausada = True
    elif trava == "atribuida":
        conversa.atribuido_a = (await make_user()).id
    elif trava == "reclamacao":
        conversa.etiqueta = ETIQUETA_AG_CANCELAMENTO
    elif trava == "fechada":
        conversa.situacao = "fechada"
    else:
        canal_id = uuid4()
    await db.commit()
    assert not await sem_demanda.processar(db, conversa.id, canal_autorizado_id=canal_id)
    assert not conversa.sem_resposta_necessaria


@pytest.mark.parametrize("alteracao", ["inativa", "faca", "quando", "geral_editada", "duplicada"])
async def test_manual_alterado_ou_ambiguo_desliga_processamento(
    db, make_user, ia_ligada, alteracao, monkeypatch
):
    conversa, _, regra = await _cenario(db, make_user, ia_ligada, monkeypatch)
    if alteracao == "inativa":
        regra.ativa = False
    elif alteracao == "faca":
        regra.faca += " Manda para pessoa."
    elif alteracao == "quando":
        regra.quando += " Outro caso."
    elif alteracao == "geral_editada":
        geral = await db.scalar(
            select(AtendimentoRegra).where(
                AtendimentoRegra.plataforma.is_(None),
                AtendimentoRegra.categoria == "saudacao_ou_vaga",
            )
        )
        geral.faca = "Sempre mandar para pessoa."
    else:
        db.add(
            AtendimentoRegra(
                tipo="categoria",
                categoria="saudacao_ou_vaga",
                plataforma="shopee",
                canal="chat",
                quando="Outra regra",
                faca="Manda para pessoa.",
                ativa=True,
            )
        )
    await db.commit()
    assert not await sem_demanda.processar(db, conversa.id, canal_autorizado_id=conversa.canal_id)
    assert not conversa.sem_resposta_necessaria


@pytest.mark.parametrize("protecao", ["avaliada", "texto", "outro_motivo", "tela", "ligada"])
async def test_nao_sobrescreve_sugestao_humana_ou_que_ja_virou_envio(
    db, make_user, ia_ligada, protecao, monkeypatch
):
    conversa, card, _ = await _cenario(db, make_user, ia_ligada, monkeypatch)
    antigo = AtendimentoRascunho(
        conversa_id=conversa.id,
        mensagem_gatilho_id=card.id,
        status="bloqueado",
        motivo=sem_demanda.MOTIVO_CARD_ANTIGO,
        precisa_humano=True,
        validador_ok=False,
        fatos={},
    )
    db.add(antigo)
    await db.flush()
    if protecao == "avaliada":
        db.add(AtendimentoAvaliacao(rascunho_id=antigo.id, acao="descartou", nota="erro"))
    elif protecao == "texto":
        antigo.texto = "Preciso de uma pessoa."
    elif protecao == "outro_motivo":
        antigo.motivo = "cliente insistiu em falar com pessoa"
    elif protecao == "tela":
        antigo.fatos = {"pedida_pela_tela": True}
    else:
        card.rascunho_id = antigo.id
    await db.commit()
    assert not await sem_demanda.processar(db, conversa.id, canal_autorizado_id=conversa.canal_id)
    assert antigo.status == "bloqueado" and not conversa.sem_resposta_necessaria


async def test_mudanca_de_turno_entre_selecao_e_processamento_nao_e_encerrada(
    db, make_user, ia_ligada, monkeypatch
):
    conversa, _, _ = await _cenario(db, make_user, ia_ligada, monkeypatch)
    turno_anterior = conversa.ultima_do_cliente_em
    await _msg(db, conversa, "Preciso de ajuda", ha=timedelta(seconds=1))
    assert not await sem_demanda.processar(
        db, conversa.id, canal_autorizado_id=conversa.canal_id, turno_esperado=turno_anterior
    )
    assert conversa.aguardando_resposta and not conversa.sem_resposta_necessaria


async def test_pausa_humana_de_30min_continua_valendo(db, make_user, ia_ligada, monkeypatch):
    conversa, _, _ = await _cenario(db, make_user, ia_ligada, monkeypatch)
    await _msg(
        db,
        conversa,
        "Tudo certo.",
        autor="loja",
        origem="davinci_humano",
        ha=timedelta(minutes=20),
    )
    assert not await sem_demanda.processar(db, conversa.id, canal_autorizado_id=conversa.canal_id)
    assert not conversa.sem_resposta_necessaria


async def test_visita_nova_so_com_card_apos_figurinha_de_junho_e_pedido_antigo(
    db, make_user, ia_ligada, modelo, envios, monkeypatch
):
    conversa, _, _ = await _cenario(db, make_user, ia_ligada, monkeypatch)
    await _pedido_completo(db)
    conversa.pedido_marketplace = PEDIDO_MKT
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="figurinha-visita-antiga",
        autor="cliente",
        texto="[Figurinha]",
        tipo="outro",
        enviada_em=datetime.now(UTC) - timedelta(days=90),
        payload={
            "message_type": "sticker",
            "status": "user_chat",
            "content": {"sticker_id": "0001", "sticker_package_id": "br_shoppito"},
        },
    )
    await db.commit()
    assert await sem_demanda.processar(db, conversa.id, canal_autorizado_id=conversa.canal_id)
    assert not conversa.aguardando_resposta and conversa.sem_resposta_necessaria
    assert modelo.chamadas == [] and envios == []


@pytest.mark.parametrize(
    ("plataforma", "payload"),
    [
        ("shopee", {"message_type": "order", "content": {"order_sn": "123"}}),
        ("tiktok", {"type": "PRODUCT_CARD", "content": {"product_id": "123"}}),
        ("tiktok", {"type": "ORDER_CARD", "content": {"order_id": "123"}}),
    ],
)
async def test_mesma_regra_global_processa_lojas_distintas_e_cartoes_reconhecidos(
    db, make_user, ia_ligada, monkeypatch, modelo, envios, plataforma, payload
):
    primeira, _, regra = await _cenario(db, make_user, ia_ligada, monkeypatch)
    segunda, card, mesma_regra = await _cenario(
        db,
        make_user,
        ia_ligada,
        monkeypatch,
        plataforma=plataforma,
        criar_regras=False,
        externo_id="outra-loja",
    )
    card.payload = payload
    await db.commit()
    assert primeira.integration_id != segunda.integration_id
    assert regra.id == mesma_regra.id
    for conversa in (primeira, segunda):
        assert await sem_demanda.processar(db, conversa.id)
        assert conversa.sem_resposta_necessaria and not conversa.aguardando_resposta
        assert conversa.dados[sem_demanda.CHAVE_AUDITORIA]["regra_id"] == str(regra.id)
    assert modelo.chamadas == [] and envios == []


@pytest.mark.parametrize(
    ("modo", "categorias"),
    [("observar", ["saudacao_ou_vaga"]), ("copiloto", ["saudacao_ou_vaga"]), ("auto", [])],
)
async def test_ativacao_e_allowlist_de_cada_canal_sao_obrigatorias(
    db, make_user, ia_ligada, monkeypatch, modo, categorias, modelo, envios
):
    conversa, _, _ = await _cenario(db, make_user, ia_ligada, monkeypatch)
    canal = await db.get(AtendimentoCanal, conversa.canal_id)
    canal.modo = modo
    canal.auto_categorias = categorias
    await db.commit()
    assert not await sem_demanda.processar(db, conversa.id)
    assert not conversa.sem_resposta_necessaria
    assert modelo.chamadas == [] and envios == []


@pytest.mark.parametrize("protecao", ["afiliado", "canal_incompativel", "canal_desvinculado"])
async def test_ativacao_nao_contorna_capacidade_do_canal(
    db, make_user, ia_ligada, monkeypatch, protecao
):
    conversa, _, _ = await _cenario(db, make_user, ia_ligada, monkeypatch)
    if protecao == "afiliado":
        conversa.dados = {"afiliado": True}
    elif protecao == "canal_incompativel":
        conversa.canal = "pergunta"
    else:
        conversa.integration_id = None
    await db.commit()
    assert not await sem_demanda.processar(db, conversa.id)
    assert not conversa.sem_resposta_necessaria


def _regras_de_menu():
    return [
        SimpleNamespace(
            id=uuid4(),
            tipo="categoria",
            categoria=categoria,
            plataforma=None,
            canal=None,
            quando=quando,
            faca=faca,
            ativa=True,
        )
        for categoria, quando, faca in (
            ("saudacao_ou_vaga", sem_demanda.REGRA_QUANDO, sem_demanda.REGRA_FACA),
            ("nota_fiscal", "Nota fiscal", ia._REGRA_NF_APROVADA),
        )
    ]


@pytest.mark.parametrize(
    ("plataforma", "canal"), [("shopee", "chat"), ("tiktok", "chat"), ("ml", "pos_venda")]
)
@pytest.mark.parametrize("selecao", ["2", "2i5"])
def test_nota_fiscal_por_menu_reconhecido_nao_depende_da_loja(plataforma, canal, selecao):
    regras = _regras_de_menu()
    menu = _mensagem("loja", f"{MENU}\n2 - Nota Fiscal\n5 - Garantia", payload={}, minutos=6)
    gatilho = _mensagem(texto=selecao, tipo="texto", payload={})
    ids = [uuid4(), uuid4()]
    for integration_id in ids:
        conversa = SimpleNamespace(
            integration_id=integration_id,
            plataforma=plataforma,
            canal=canal,
            pedido_marketplace=None,
        )
        resposta = sem_demanda.resposta_de_menu(conversa, [menu, gatilho], gatilho, regras)
        assert isinstance(resposta, sem_demanda.RespostaEntrada)
        assert resposta.categoria == "saudacao_ou_vaga"
        assert "Os pedidos acompanham nota fiscal" in resposta.texto
        assert ("qual é a sua dúvida" in resposta.texto) is (selecao == "2i5")
        assert resposta.fato["regra_id"] == str(regras[0].id)
        assert resposta.fato["integration_id"] == str(integration_id)


@pytest.mark.parametrize(
    "protecao",
    [
        "pedido_identificado",
        "pergunta_real",
        "manual_editado",
        "sem_menu",
        "historico_incompleto",
        "nota_interna",
    ],
)
def test_nota_fiscal_generica_nao_substitui_demanda_nem_depende_de_menu_inferido(protecao):
    conversa = SimpleNamespace(
        integration_id=uuid4(), plataforma="tiktok", canal="chat", pedido_marketplace=None
    )
    regras = _regras_de_menu()
    menu = _mensagem("loja", f"{MENU}\n2 - Nota Fiscal\n5 - Garantia", payload={}, minutos=6)
    gatilho = _mensagem(texto="2", tipo="texto", payload={})
    mensagens = [menu, gatilho]
    if protecao == "pedido_identificado":
        conversa.pedido_marketplace = "pedido-123"
    elif protecao == "pergunta_real":
        mensagens.insert(
            0, _mensagem(texto="Pode mandar a segunda via da nota?", payload={}, minutos=7)
        )
    elif protecao == "manual_editado":
        regras[0].faca += " Outra política."
    elif protecao == "sem_menu":
        menu.texto = "Olá!"
    elif protecao == "historico_incompleto":
        mensagens = ia.HistoricoMensagens(mensagens, turno_incompleto=True)
    else:
        menu.origem = ORIGEM_NOTA
    assert sem_demanda.resposta_de_menu(conversa, mensagens, gatilho, regras) is None


def test_opt_in_global_exige_regra_literal_e_legado_kia_nao_autoriza():
    regra = _regras_de_menu()[0]
    assert sem_demanda.regra_aprovada(regra)
    assert sem_demanda.regra_aprovada(vars(regra))
    regra.categoria = "saudacao_ou_vaga"
    regra.plataforma = "shopee"
    regra.canal = "chat"
    regra.quando = sem_demanda.LEGADO_QUANDO
    regra.faca = sem_demanda.LEGADO_FACA
    assert sem_demanda.regra_especifica_kia(regra)
    assert sem_demanda.regra_especifica_kia(vars(regra))
    assert not sem_demanda.regra_aprovada(regra)
