# ruff: noqa: F401, F811 — fixtures compartilhadas, nenhum envio/provedor real
"""Reproduções dos casos revisados com o Manual completo e o caminho de envio."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models import AtendimentoAvaliacao, AtendimentoRascunho, BlingOrder
from app.services.atendimento import ficha, gravar, ia, politicas_kia
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


@pytest.fixture(autouse=True)
def ficha_local(monkeypatch):
    async def local(session, conversa, mensagens, textos):
        if conversa.anuncio_id not in {politicas_kia.ANUNCIO_WATCH, politicas_kia.ANUNCIO_OCULOS}:
            return []
        return [
            {
                "fonte": "loja (Shopee), há menos de um dia",
                "de_onde": "anúncio ligado à conversa",
                "titulo": "Apple Watch Series 11"
                if conversa.anuncio_id == politicas_kia.ANUNCIO_WATCH
                else "Uranyx Óculos Inteligentes AI",
                "atributos": [],
                "descricao": "",
            }
        ]

    monkeypatch.setattr(ficha, "fichas_da_conversa", local)


async def cenario(db, make_user, ia_ligada, monkeypatch, *, item=None, pedido=None):
    await _importar_manual_base(db)
    ia_ligada.atendimento_auto_ativo = True
    c = await _conversa(
        db,
        make_user,
        pedido=pedido,
        modo="auto",
        auto_categorias=[
            "espec_outros",
            "pedido_link_midia",
            "prazo_envio",
            "saudacao_ou_vaga",
            "originalidade_condicao",
        ],
    )
    monkeypatch.setattr(politicas_kia, "INTEGRATION_KIA", str(c.integration_id))
    c.anuncio_id = item
    c.anuncio_titulo = None
    await db.commit()
    return c


@pytest.mark.parametrize(
    "item,pergunta,categoria,trecho",
    [
        ("22299473628", "Olá. A pulseira é P/M ou M/G?", "espec_outros", "tamanho padrão"),
        (
            "58269229175",
            "Opa teria como mandar foto do jeito que e o modelo",
            "pedido_link_midia",
            "fotos do anúncio mostram o modelo",
        ),
    ],
)
async def test_politicas_aprovadas_respondem_com_menu_sem_modelo(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, item, pergunta, categoria, trecho
):
    c = await cenario(db, make_user, ia_ligada, monkeypatch, item=item)
    await _msg(db, c, pergunta, ha=timedelta(minutes=8))
    await _msg(db, c, MENU, autor="loja", ha=timedelta(minutes=7))
    await _msg(db, c, "6")
    r = await ia.gerar_rascunho(db, c)
    assert r.categoria == categoria and r.validador_ok and not r.precisa_humano, r.motivo
    assert trecho in r.texto
    assert r.fatos["politica_kia_aprovada"]["regra_id"]
    assert len(envios) == 1 and modelo.chamadas == []


async def test_compra_confirmada_responde_envio_em_vez_de_estoque_antigo(
    db, make_user, ia_ligada, monkeypatch, envios, modelo
):
    await _pedido_completo(db)
    await db.execute(update(BlingOrder).values(situacao="21", status="Em digitação"))
    c = await cenario(db, make_user, ia_ligada, monkeypatch, item="22299473628", pedido=PEDIDO_MKT)
    c.dados = {
        "pedido_mkt": {
            "fonte": "shopee",
            "pedido": PEDIDO_MKT,
            "status": "PROCESSED",
            "pago_em": datetime.now(UTC).isoformat(),
            "atualizado_em": datetime.now(UTC).isoformat(),
            "itens": [{"item_id": "22299473628"}],
        }
    }
    await db.commit()
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
    await gravar.gravar_mensagem(
        db,
        c,
        externo_id="pedido-confirmado",
        autor="loja",
        tipo="pedido",
        texto=None,
        enviada_em=datetime.now(UTC) - timedelta(minutes=8),
        origem="externo",
        payload={"source": "openapi", "message_type": "order", "content": {"order_sn": PEDIDO_MKT}},
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
    assert "prazo informado pela Shopee" in r.texto and "São Paulo" in r.texto
    assert "estoque" not in r.texto and "número" not in r.texto
    assert r.fatos["lacunas"]["data_envio"] is None
    assert len(envios) == 1 and modelo.chamadas == []


@pytest.mark.parametrize("opcao", ["2", "2i5", "2 e 5"])
async def test_menu_nf_nao_inventa_pedido_de_documento(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, opcao
):
    c = await cenario(db, make_user, ia_ligada, monkeypatch)
    await _msg(db, c, MENU, autor="loja", ha=timedelta(minutes=8))
    await _msg(db, c, opcao)
    r = await ia.gerar_rascunho(db, c)
    assert not r.precisa_humano and r.validador_ok, r.motivo
    assert "acompanham nota fiscal" in r.texto
    assert "número" not in r.texto and "garantia" not in r.texto
    assert "arquivo" not in r.texto
    assert modelo.chamadas == [] and len(envios) == 1


@pytest.mark.parametrize("protecao", ["nenhuma", "tela", "avaliacao"])
async def test_reavalia_so_draft_antigo_coberto_pela_politica(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, protecao
):
    c = await cenario(db, make_user, ia_ligada, monkeypatch, item="22299473628")
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
    n = await ia.reavaliar_politicas_kia(db)
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
    resumo = await ia.manter_fila_kia(db)
    assert resumo["respondidas"] == 1 and not c.aguardando_resposta
    assert r.status == "substituido" and c.ultima_da_loja_em is not None
    assert envios == [] and modelo.chamadas == []


async def test_pergunta_condicao_sem_confirmacao_pede_modelo_sem_inventar_novo(
    db, make_user, ia_ligada, monkeypatch, envios, modelo
):
    c = await cenario(db, make_user, ia_ligada, monkeypatch, item="58207425520")
    await _msg(db, c, "este produto é novo ou recondicionado?")
    r = await ia.gerar_rascunho(db, c)
    assert r.categoria == "originalidade_condicao" and not r.precisa_humano, r.motivo
    assert "confirmar qual é o modelo" in r.texto and "produto é novo" not in r.texto
    assert len(envios) == 1 and modelo.chamadas == []


@pytest.mark.parametrize("protecao", ["pausa_30min", "atribuida"])
async def test_resposta_aprovada_preserva_pausa_humana_e_atribuicao(
    db, make_user, ia_ligada, monkeypatch, envios, modelo, protecao
):
    c = await cenario(db, make_user, ia_ligada, monkeypatch, item="22299473628")
    if protecao == "pausa_30min":
        await _msg(
            db,
            c,
            "Bom dia! Estamos por aqui.",
            autor="loja",
            origem="davinci_humano",
            ha=timedelta(minutes=20),
        )
    else:
        c.atribuido_a = (await make_user()).id
        await db.commit()
    await _msg(db, c, "A pulseira é P/M ou M/G?")
    r = await ia.gerar_rascunho(db, c)
    assert r is not None and not r.precisa_humano
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] in {"pausa_humana", "atribuida"}
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
    assert await ia.manter_fila_kia(db) == {"respondidas": 0, "sem_demanda": 0}
