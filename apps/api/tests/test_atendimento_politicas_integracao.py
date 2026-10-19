# ruff: noqa: F401, F811 — fixtures compartilhadas, nenhum envio/provedor real
"""Políticas gerais aprovadas, Manual completo e caminho de envio em várias lojas."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoRascunho,
    BlingOrder,
    IntegrationPlatform,
)
from app.services.atendimento import ficha, gravar, ia
from tests.test_atendimento_ia import (
    PEDIDO_MKT,
    _conversa,
    _importar_manual_base,
    _msg,
    _pedido_completo,
    envios,
    ia_ligada,
    modelo,
)

MENU = (
    "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!\n"
    "1 - Previsão de entrega / envio\n2 - Nota fiscal\n3 - Encerramento da compra\n"
    "4 - Troca de endereço\n5 - Garantia\n6 - Falar com Atendente"
)
WATCH = "watch-anuncio-proprio"
OCULOS = "oculos-anuncio-proprio"
CANAIS = [("shopee", "chat"), ("tiktok", "chat"), ("ml", "pergunta"), ("ml", "pos_venda")]
PLATAFORMAS = {"shopee": "Shopee", "tiktok": "TikTok Shop", "ml": "Mercado Livre"}


@pytest.fixture(autouse=True)
def ficha_local(monkeypatch):
    async def local(session, conversa, mensagens, textos):
        if conversa.anuncio_id not in {WATCH, OCULOS}:
            return []
        return [
            {
                "fonte": f"loja ({PLATAFORMAS[conversa.plataforma]}), há menos de um dia",
                "de_onde": "anúncio da pergunta"
                if conversa.canal == "pergunta"
                else "anúncio ligado à conversa",
                "titulo": "Apple Watch Series 11"
                if conversa.anuncio_id == WATCH
                else "Uranyx Óculos Inteligentes AI",
                "atributos": ["Tamanho da pulseira: padrão"]
                if conversa.anuncio_id == WATCH
                else [],
                "descricao": "",
            }
        ]

    monkeypatch.setattr(ficha, "fichas_da_conversa", local)


async def cenario(
    db,
    make_user,
    ia_ligada,
    monkeypatch,
    *,
    item=None,
    pedido=None,
    plataforma="shopee",
    canal="chat",
    modo="auto",
    criar_manual=True,
):
    if criar_manual:
        await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(
        db,
        make_user,
        pedido=pedido,
        plataforma=plataforma,
        canal=canal,
        platform=IntegrationPlatform(plataforma),
        modo=modo,
        auto_categorias=[
            "espec_outros",
            "pedido_link_midia",
            "prazo_envio",
            "saudacao_ou_vaga",
            "originalidade_condicao",
        ],
    )
    c.anuncio_id = item
    c.anuncio_titulo = None
    await db.commit()
    return c


@pytest.mark.parametrize(
    "item,pergunta,categoria,trecho",
    [
        (WATCH, "Olá. A pulseira é P/M ou M/G?", "espec_outros", "tamanho da pulseira é padrão"),
        (
            OCULOS,
            "Opa teria como mandar foto do jeito que e o modelo",
            "pedido_link_midia",
            "como é o modelo nas fotos do próprio anúncio",
        ),
    ],
)
@pytest.mark.parametrize(("plataforma", "canal"), CANAIS)
async def test_politicas_aprovadas_respondem_com_menu_sem_modelo(
    db,
    make_user,
    ia_ligada,
    monkeypatch,
    envios,
    modelo,
    item,
    pergunta,
    categoria,
    trecho,
    plataforma,
    canal,
):
    c = await cenario(
        db, make_user, ia_ligada, monkeypatch, item=item, plataforma=plataforma, canal=canal
    )
    await _msg(db, c, pergunta, ha=timedelta(minutes=8))
    if canal != "pergunta":
        await _msg(db, c, MENU, autor="loja", ha=timedelta(minutes=7))
        await _msg(db, c, "6")
    r = await ia.gerar_rascunho(db, c)
    assert r.categoria == categoria and r.validador_ok and not r.precisa_humano, r.motivo
    assert trecho in r.texto
    assert r.fatos["politica_atendimento_aprovada"]["regra_id"]
    if canal == "pergunta":
        assert "card" not in r.texto
    assert len(envios) == 1 and modelo.chamadas == []


@pytest.mark.parametrize(("plataforma", "canal"), [c for c in CANAIS if c[1] != "pergunta"])
async def test_compra_confirmada_responde_envio_em_vez_de_estoque_antigo(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, plataforma, canal
):
    await _pedido_completo(db)
    await db.execute(update(BlingOrder).values(situacao="21", status="Em digitação"))
    c = await cenario(
        db,
        make_user,
        ia_ligada,
        monkeypatch,
        item=WATCH,
        pedido=PEDIDO_MKT,
        plataforma=plataforma,
        canal=canal,
    )
    c.dados = {
        "pedido_mkt": {
            "fonte": plataforma,
            "pedido": PEDIDO_MKT,
            "status": "paid" if plataforma == "ml" else "PROCESSED",
            "logistica": {"status": "handling"} if plataforma == "ml" else {},
            "pago_em": datetime.now(UTC).isoformat(),
            "atualizado_em": datetime.now(UTC).isoformat(),
            "itens": [{"item_id": WATCH}],
        }
    }
    await db.commit()
    if plataforma == "shopee":
        await _msg(
            db, c, "Quantos series 11 cinza vocês ainda tem em estoque?", ha=timedelta(minutes=20)
        )
        await _msg(
            db,
            c,
            "Só para eu me planejar, quando vc acha que acaba? Estou ainda conseguindo o dinheiro",
            ha=timedelta(minutes=19),
        )
    await _msg(
        db, c, "Ja realizei o pedido, qual a previsão de entrega/envio?", ha=timedelta(minutes=10)
    )
    await _msg(db, c, "De onde estão enviando?", ha=timedelta(minutes=9))
    if plataforma != "ml":
        await gravar.gravar_mensagem(
            db,
            c,
            externo_id="pedido-confirmado",
            autor="loja",
            tipo="pedido",
            texto=None,
            enviada_em=datetime.now(UTC) - timedelta(minutes=8),
            origem="externo",
            payload=(
                {
                    "type": "ORDER_CARD",
                    "sender": {"role": "CUSTOMER_SERVICE"},
                    "content": {"order_id": PEDIDO_MKT},
                }
                if plataforma == "tiktok"
                else {
                    "source": "openapi",
                    "message_type": "order",
                    "content": {"order_sn": PEDIDO_MKT},
                }
            ),
        )
        await _msg(db, c, "1")

    # O contexto real semeado tem entrega concluída; esta reprodução mede o
    # caso em preparação, tal como o snapshot do pedido auditado.
    async def contexto_local(*args):
        return {
            "pedido": {"situacao": "21", "data": "2026-10-19", "itens": []},
            "logistica": {
                "status": "Pedido em preparação",
                "rastreio": "BR123",
                "data_envio": None,
            },
        }

    monkeypatch.setattr(ia.contexto_svc, "contexto_da_conversa", contexto_local)
    r = await ia.gerar_rascunho(db, c)
    assert not r.precisa_humano and r.validador_ok, r.motivo
    assert r.categoria == "prazo_envio"
    assert "prazo informado" in r.texto and PLATAFORMAS[plataforma] in r.texto
    assert "origem do envio não está informada" in r.texto and "São Paulo" not in r.texto
    assert "estoque" not in r.texto and "número" not in r.texto
    assert r.fatos["lacunas"]["data_envio"] is None
    assert len(envios) == 1 and modelo.chamadas == []


@pytest.mark.parametrize("opcao", ["2", "2i5", "2 e 5"])
@pytest.mark.parametrize(("plataforma", "canal"), [c for c in CANAIS if c[1] != "pergunta"])
async def test_menu_nf_nao_inventa_pedido_de_documento(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, opcao, plataforma, canal
):
    c = await cenario(db, make_user, ia_ligada, monkeypatch, plataforma=plataforma, canal=canal)
    await _msg(db, c, MENU, autor="loja", ha=timedelta(minutes=8))
    await _msg(db, c, opcao)
    r = await ia.gerar_rascunho(db, c)
    assert not r.precisa_humano and r.validador_ok, r.motivo
    assert "acompanham nota fiscal" in r.texto
    assert "número" not in r.texto and "garantia" not in r.texto
    assert "arquivo" not in r.texto
    assert modelo.chamadas == [] and len(envios) == 1


@pytest.mark.parametrize("protecao", ["nenhuma", "tela", "avaliacao"])
@pytest.mark.parametrize(("plataforma", "canal"), CANAIS)
async def test_reavalia_so_draft_antigo_coberto_pela_politica(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, protecao, plataforma, canal
):
    c = await cenario(
        db, make_user, ia_ligada, monkeypatch, item=WATCH, plataforma=plataforma, canal=canal
    )
    m = await _msg(db, c, "A pulseira é P/M ou M/G?")
    antigo = AtendimentoRascunho(
        conversa_id=c.id,
        mensagem_gatilho_id=m.id,
        status="pendente",
        texto="Para verificarmos a garantia, informe o pedido.",
        categoria="pede_atendente",
        motivo="a IA mudou de assunto; a resposta fala de garantia",
        prompt_versao="v7",
        precisa_humano=True,
        validador_ok=True,
        fatos={"pedida_pela_tela": True} if protecao == "tela" else {},
    )
    db.add(antigo)
    await db.commit()
    if protecao == "avaliacao":
        user = await make_user()
        db.add(
            AtendimentoAvaliacao(
                rascunho_id=antigo.id, user_id=user.id, acao="observou", nota="erro"
            )
        )
        await db.commit()
    n = await ia.reavaliar_politicas(db)
    assert n == (1 if protecao == "nenhuma" else 0)
    assert len(envios) == n and modelo.chamadas == []
    await db.refresh(antigo)
    assert antigo.status == ("substituido" if n else "pendente")


async def test_fila_reconcilia_resposta_de_pessoa_sem_reenviar_saudacao(
    db, make_user, ia_ligada, monkeypatch, envios, modelo
):
    c = await cenario(db, make_user, ia_ligada, monkeypatch)
    gatilho = await _msg(db, c, "Olá", ha=timedelta(days=20))
    await _msg(
        db,
        c,
        "Bom dia! Ficou alguma dúvida em que eu possa te ajudar?",
        autor="loja",
        ha=timedelta(days=19),
    )
    c.aguardando_resposta, c.ultima_da_loja_em = True, None
    r = AtendimentoRascunho(
        conversa_id=c.id,
        mensagem_gatilho_id=gatilho.id,
        status="pendente",
        texto="Olá! Como podemos ajudar?",
        categoria="saudacao_ou_vaga",
        fatos={"pedida_pela_tela": True},
    )
    db.add(r)
    await db.commit()
    resumo = await ia.manter_fila(db)
    assert resumo["respondidas"] == 1 and not c.aguardando_resposta
    assert r.status == "substituido" and c.ultima_da_loja_em is not None
    assert envios == [] and modelo.chamadas == []


@pytest.mark.parametrize(("plataforma", "canal"), CANAIS)
async def test_pergunta_condicao_sem_confirmacao_pede_modelo_sem_inventar_novo(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, plataforma, canal
):
    c = await cenario(
        db,
        make_user,
        ia_ligada,
        monkeypatch,
        item="anuncio-sem-ficha",
        plataforma=plataforma,
        canal=canal,
    )
    await _msg(db, c, "este produto é novo ou recondicionado?")
    r = await ia.gerar_rascunho(db, c)
    assert r.categoria == "originalidade_condicao" and not r.precisa_humano, r.motivo
    assert "confirmar qual é o modelo" in r.texto and "produto é novo" not in r.texto
    if canal == "pergunta":
        assert "card" not in r.texto
    assert len(envios) == 1 and modelo.chamadas == []


@pytest.mark.parametrize("protecao", ["pausa_30min", "atribuida", "categoriaoff"])
@pytest.mark.parametrize(("plataforma", "canal"), CANAIS)
async def test_resposta_aprovada_preserva_pausa_humana_e_atribuicao(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, protecao, plataforma, canal
):
    c = await cenario(
        db, make_user, ia_ligada, monkeypatch, item=WATCH, plataforma=plataforma, canal=canal
    )
    if protecao == "pausa_30min":
        await _msg(
            db,
            c,
            "Bom dia! Estamos por aqui.",
            autor="loja",
            origem="davinci_humano",
            ha=timedelta(minutes=20),
        )
    elif protecao == "atribuida":
        c.atribuido_a = (await make_user()).id
        await db.commit()
    else:
        cfg = await db.get(AtendimentoCanal, c.canal_id)
        cfg.auto_categorias = []
        await db.commit()
    await _msg(db, c, "A pulseira é P/M ou M/G?")
    r = await ia.gerar_rascunho(db, c)
    assert r is not None and not r.precisa_humano
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] in {
        "pausa_humana",
        "atribuida",
        "assunto_nao_liberado",
    }
    assert envios == [] and modelo.chamadas == []


async def test_falha_em_um_cartao_nao_interrompe_fila(db, make_user, ia_ligada, monkeypatch):
    from app.services.atendimento import sem_demanda

    c = await cenario(db, make_user, ia_ligada, monkeypatch)
    await gravar.gravar_mensagem(
        db,
        c,
        externo_id="card-fila",
        autor="cliente",
        texto=None,
        tipo="produto",
        enviada_em=datetime.now(UTC) - timedelta(minutes=5),
        payload={"message_type": "item"},
    )
    await db.commit()

    async def falhar(*args, **kwargs):
        raise ValueError("dado local inválido")

    monkeypatch.setattr(sem_demanda, "processar", falhar)
    assert await ia.manter_fila(db) == {"respondidas": 0, "sem_demanda": 0}


@pytest.mark.parametrize("opcao", ["3", "4", "5"])
async def test_opcao_sensivel_do_menu_nao_e_apagada_por_politica_de_produto(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, opcao
):
    c = await cenario(db, make_user, ia_ligada, monkeypatch, item=WATCH)
    await _msg(db, c, "A pulseira é P/M ou M/G?", ha=timedelta(minutes=8))
    await _msg(db, c, MENU, autor="loja", ha=timedelta(minutes=7))
    await _msg(db, c, opcao)
    r = await ia.gerar_rascunho(db, c)
    assert r.precisa_humano and "opção de menu de assunto só para pessoa" in r.motivo
    assert not r.fatos.get("politica_atendimento_aprovada") and envios == []


async def test_duas_lojas_usam_a_mesma_regra_com_a_ficha_de_cada_conversa(
    db, make_user, ia_ligada, monkeypatch, envios, modelo
):
    primeira = await cenario(db, make_user, ia_ligada, monkeypatch, item=WATCH)
    segunda = await cenario(
        db,
        make_user,
        ia_ligada,
        monkeypatch,
        item=WATCH,
        criar_manual=False,
        plataforma="tiktok",
    )
    assert primeira.integration_id != segunda.integration_id
    respostas = []
    for conversa in (primeira, segunda):
        await _msg(db, conversa, "A pulseira é P/M ou M/G?")
        resposta = await ia.gerar_rascunho(db, conversa)
        assert resposta.validador_ok and not resposta.precisa_humano, resposta.motivo
        assert "tamanho da pulseira é padrão" in resposta.texto
        fato = resposta.fatos["politica_atendimento_aprovada"]
        assert fato["integration_id"] == str(conversa.integration_id)
        respostas.append(fato)
    assert respostas[0]["regra_id"] == respostas[1]["regra_id"]
    assert len(envios) == 2 and modelo.chamadas == []


@pytest.mark.parametrize(("plataforma", "canal"), CANAIS)
async def test_loja_em_observacao_so_envia_apos_ativacao_do_automatico(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, plataforma, canal
):
    c = await cenario(
        db,
        make_user,
        ia_ligada,
        monkeypatch,
        item=WATCH,
        plataforma=plataforma,
        canal=canal,
        modo="observar",
    )
    await _msg(db, c, "A pulseira é P/M ou M/G?")
    r = await ia.gerar_rascunho(db, c)
    assert r.validador_ok and not r.precisa_humano and r.status == "pendente", r.motivo
    assert envios == [] and modelo.chamadas == []
    r.created_at = datetime.now(UTC) - ia.REENVIO_DEPOIS - timedelta(seconds=1)
    await db.commit()
    assert await ia.reenviar_pendentes(db) == 0
    cfg = await db.get(AtendimentoCanal, c.canal_id)
    cfg.modo = "auto"
    await db.commit()
    assert await ia.reenviar_pendentes(db) == 1
    assert len(envios) == 1 and modelo.chamadas == []


@pytest.mark.parametrize(("plataforma", "canal"), CANAIS)
async def test_recuperacao_busca_ficha_propria_so_apos_ativar_loja(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, plataforma, canal
):
    c = await cenario(
        db,
        make_user,
        ia_ligada,
        monkeypatch,
        item=WATCH,
        plataforma=plataforma,
        canal=canal,
        modo="observar",
    )
    gatilho = await _msg(db, c, "A pulseira é P/M ou M/G?")
    antigo = AtendimentoRascunho(
        conversa_id=c.id,
        mensagem_gatilho_id=gatilho.id,
        status="pendente",
        texto="Informe o pedido para consultar a garantia.",
        categoria="pede_atendente",
        prompt_versao="v7",
        precisa_humano=True,
        validador_ok=True,
        fatos={},
    )
    db.add(antigo)
    await db.commit()
    assert await ia.reavaliar_politicas(db) == 0
    cfg = await db.get(AtendimentoCanal, c.canal_id)
    cfg.modo = "auto"
    await db.commit()
    assert await ia.reavaliar_politicas(db) == 1
    await db.refresh(antigo)
    assert antigo.status == "substituido"
    atual = await db.scalar(
        select(AtendimentoRascunho)
        .where(AtendimentoRascunho.conversa_id == c.id)
        .order_by(AtendimentoRascunho.created_at.desc())
    )
    assert atual.prompt_versao == ia.PROMPT_VERSAO == "v10"
    assert "tamanho da pulseira é padrão" in atual.texto
    assert atual.fatos["politica_atendimento_aprovada"]["regra_id"]
    assert len(envios) == 1 and modelo.chamadas == []


async def test_reavaliacao_avanca_apos_dez_conversas_fora_da_politica(
    db, make_user, ia_ligada, monkeypatch, envios, modelo
):
    monkeypatch.setattr(ia, "_CURSOR_POLITICAS", {})
    antigos = []
    for indice in range(12):
        elegivel = indice == 11
        c = await cenario(
            db,
            make_user,
            ia_ligada,
            monkeypatch,
            criar_manual=indice == 0,
            plataforma="tiktok" if elegivel else "shopee",
            item=WATCH if elegivel else None,
        )
        gatilho = await _msg(
            db,
            c,
            "A pulseira é P/M ou M/G?"
            if elegivel
            else "Pode mandar um vídeo real e fotografar o número de série?",
        )
        antigo = AtendimentoRascunho(
            conversa_id=c.id,
            mensagem_gatilho_id=gatilho.id,
            status="pendente",
            texto="Precisa consultar a equipe.",
            categoria="pede_atendente",
            prompt_versao="v7",
            precisa_humano=True,
            validador_ok=True,
            fatos={},
            created_at=datetime.now(UTC) - timedelta(seconds=60 + indice),
        )
        db.add(antigo)
        antigos.append(antigo)
    await db.commit()
    assert await ia.reavaliar_politicas(db) == 0
    assert envios == [] and modelo.chamadas == []
    assert await ia.reavaliar_politicas(db) == 1
    for indice, antigo in enumerate(antigos):
        await db.refresh(antigo)
        assert antigo.status == ("substituido" if indice == 11 else "pendente")
    assert len(envios) == 1 and modelo.chamadas == []
