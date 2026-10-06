"""O catálogo das mensagens automáticas — tudo puro, sem banco (05/10/2026).

Eduardo, 05/10/2026: recriar no DaVinci as mensagens automáticas do Duoke,
começando em modo seco (docs/atendimento-automacoes.md). Aqui:

- todo texto padrão passa no validador da plataforma (com e sem o nome do
  comprador), o ML fica com até 350 caracteres e só ISO-8859-1;
- a ASSINATURA de cada modelo do Duoke casa com o texto do levantamento (e
  não casa com o nosso texto novo, nem com resposta de pessoa), e a régua
  (`e_mensagem_automatica`) reconhece todos os textos que o motor manda;
- cada GATILHO que nasce da conversa (menu e sessão de 12 h, opção só com
  menu valendo, "aguarde" com o 2º no mesmo turno, convite uma vez, ciclo
  do "ficou alguma dúvida", o TikTok contando da última mensagem);
- cada CONDIÇÃO e EXCLUSÃO da decisão (`decidir`);
- o horário (09h–20h) e a validade; a semente (simular nas lojas do Duoke);
- as que faltavam do Duoke (05/10/2026, à noite), SÓ SIMULAÇÃO: o pedido não
  pago com o cupom pela faixa do valor (a tabela tirada dos textos do Duoke),
  a resposta da avaliação (pública + chat, 4–5★ e 1–3★) e o "pedido recebido"
  do TikTok — a assinatura, a decisão, a semente e o "só simula".
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento.constantes import (
    e_mensagem_automatica,
    e_resposta_automatica,
)

T0 = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)  # 12h00 em São Paulo

# Os textos do Duoke como o levantamento os mediu em produção (05/10/2026),
# com o nome do comprador onde o Duoke põe.
DUOKE = {
    (cat.TIPO_MENU, None): (
        "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!"
        "\n\n1 - Previsão de entrega / envio\n2 - Nota fiscal\n3 - Encerramento da compra\n"
        "4 - Troca de endereço\n5 - Garantia\n6 - Falar com Atendente"
    ),
    (cat.TIPO_OPCAO, 1): (
        "A entrega é feita pela Shopee, não temos acesso ao transporte, pode seguir o prazo "
        "informado na hora da compra por favor!"
    ),
    (cat.TIPO_OPCAO, 2): "Todos nossos produtos são enviados com nota fiscal que vai anexada",
    (cat.TIPO_OPCAO, 3): "O pedido pode ser encerrado a qualquer momento antes do envio pelo",
    (cat.TIPO_OPCAO, 5): "Por favor, descreva qual é o defeito do produto e envie-nos uma foto.",
    (cat.TIPO_OPCAO, 6): (
        "Descreva sua dúvida que assim que um atendente estiver disponível ele irá te responder!"
    ),
    (cat.TIPO_AGUARDE, None): (
        "Olá, a sua mensagem foi recebida. Há mais mensagens neste momento e a equipa de "
        "serviço ao cliente está ocupada, por favor aguarde um momento."
    ),
    (cat.TIPO_CONVITE, None): (
        "fulana.123 já segue nossa loja aqui na Shopee? Seguindo você recebe ofertas 🚀"
    ),
    (cat.TIPO_DUVIDA_1, None): "Ficou alguma dúvida sobre o produto? Estou aqui pra te ajudar!",
    (cat.TIPO_DUVIDA_2, None): (
        "Tudo bem? Caso ainda esteja em dúvida, posso te explicar melhor sobre o produto"
    ),
    (cat.TIPO_PEDIDO_RECEBIDO, None): (
        "Oi! Recebemos seu pedido e já estamos preparando pra envio. Em breve você receberá"
    ),
    (cat.TIPO_ENTREGUE, None): "Oi! Tudo bem? 😊 Confirmamos a entrega do seu pedido! Por se",
    (cat.TIPO_POS, None): (
        "Oi! Só passando para saber se está tudo certo com o seu produto. Se sim e puder"
    ),
    (cat.TIPO_NAO_PAGO, None): (
        "Oi! 👋 Notamos que você deixou alguns itens no carrinho e queremos te dar uma "
        "ajudinha para finalizar sua compra 😄\n\nPreparamos cupons exclusivos para você "
        "economizar:\n\n💸 R$20 OFF"
    ),
}
# A resposta da avaliação do Duoke (a pública e a do chat, com o usuário).
DUOKE_AVALIACAO = {
    cat.TIPO_AVALIACAO_BOA: (
        "Obrigado pela confiança! 🙏 Volte sempre que precisar!",
        "Obrigado pela confiança, fulana.123! 🙏 Volte sempre que precisar!",
    ),
    cat.TIPO_AVALIACAO_RUIM: (
        "Sentimos muito pela experiência 😔 Não foi o atendimento que buscamos oferecer. "
        "Estamos disponíveis pelo chat",
        "fulana.123 Sentimos muito pela experiência 😔 Não foi o atendimento que buscamos "
        "oferecer.",
    ),
}


def _automaticas() -> list[cat.Automacao]:
    return [a for a in cat.CATALOGO.values() if not a.travada]


# ── Textos ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("aut", _automaticas(), ids=lambda a: a.codigo)
@pytest.mark.parametrize("comprador", ["maria.silva", "Ana Paula", None, "5511999998888"])
def test_todo_texto_padrao_passa_no_validador(aut, comprador):
    for loja in ("barbosa", "inova"):
        partes, motivos = cat.renderizar(
            cat.partes_padrao(aut, loja),
            comprador=comprador,
            plataforma=aut.plataforma,
            canal=aut.canal,
            valores=cat.valores_de_exemplo(aut),
        )
        assert motivos == [], (aut.codigo, comprador, motivos)
        textos = [p["texto"] for p in partes if p["tipo"] in cat.TIPOS_COM_TEXTO]
        assert textos and all("{" not in t for t in textos)
        # O usuário que parece telefone sai do texto (o validador barraria).
        if comprador == "5511999998888":
            assert all(comprador not in t for t in textos)


def test_ml_cabe_em_350_e_so_iso_8859_1():
    for aut in cat.por_plataforma("ml"):
        for parte in cat.partes_padrao(aut):
            texto = parte.get("texto") or ""
            assert len(texto) <= 350, aut.codigo
            texto.encode("latin-1")
    # A opção 2 do Duoke (364) perdeu só o "Atenciosamente." do fim.
    assert len(cat.TEXTO_OPCAO_2) > 350
    assert cat.TEXTO_OPCAO_2.startswith(cat.TEXTO_OPCAO_2_ML)
    assert len(cat.TEXTO_OPCAO_2_ML) == 347


def test_nome_dentro_do_texto_e_sem_nome_a_frase_fica_inteira():
    assert cat.renderizar_texto(cat.TEXTO_ENTREGUE_CELULAR, "ana").startswith("Oi, ana! Tudo bem?")
    assert cat.renderizar_texto(cat.TEXTO_ENTREGUE_CELULAR, None).startswith("Oi! Tudo bem?")
    assert cat.renderizar_texto(cat.TEXTO_POS_CONCLUSAO, "  ").startswith("Oi! Só passando")
    assert cat.renderizar_texto(cat.TEXTO_CONVITE_SHOPEE, None).startswith("Já segue nossa loja")
    assert cat.renderizar_texto(cat.TEXTO_CONVITE_TIKTOK, "zé").startswith("zé já segue nossa loja")
    # O "aguarde" em português do Brasil (o do Duoke diz "equipa").
    assert "equipa" not in cat.TEXTO_AGUARDE


def test_o_entregue_muda_de_celular_para_mala_pela_loja():
    aut = cat.CATALOGO["shopee_entregue"]
    celular = cat.partes_padrao(aut, "Barbosa")
    mala = cat.partes_padrao(aut, " INOVA ")
    assert celular[0] == {"tipo": "cartao_pedido"} and mala[0] == {"tipo": "cartao_pedido"}
    assert "vídeo contínuo" in celular[1]["texto"]
    assert "sua mala já chegou" in mala[1]["texto"]


# ── Assinaturas do Duoke e a régua ────────────────────────────────────────


@pytest.mark.parametrize("chave", list(DUOKE), ids=lambda k: f"{k[0]}_{k[1]}")
def test_assinatura_casa_com_o_modelo_do_levantamento(chave):
    assert cat.assinatura(DUOKE[chave]) == chave
    # Todo modelo do Duoke é mensagem automática pela régua (fila, métrica, IA).
    assert e_mensagem_automatica(DUOKE[chave])


def test_assinatura_nao_pega_pessoa_nem_o_nosso_aguarde():
    for texto in (
        "bom dia tudo bem ?",
        "Olá! Seu pedido já foi enviado",
        "A entrega é feita pela Shopee",  # pessoa respondendo à mão, sem o resto
        cat.TEXTO_AGUARDE,  # o nosso: não é o do Duoke
        "",
        None,
    ):
        assert cat.assinatura(texto) is None, texto


def test_todo_texto_que_o_motor_manda_e_automatico_pela_regua():
    """A rede para quando a leitura não adota a nossa linha (volta como `externo`).

    Só as que ENVIAM: as que só simulam (`so_simular`) nunca saem — a régua não
    precisa delas (a resposta da avaliação de pessoa também diria "obrigado pela
    confiança"); no dia em que uma sair, este teste a cobra.
    """
    for aut in _automaticas():
        if aut.so_simular:
            continue
        for loja in ("barbosa", "inova"):
            for nome in ("maria.silva", None):
                partes, _ = cat.renderizar(
                    cat.partes_padrao(aut, loja),
                    comprador=nome,
                    plataforma=aut.plataforma,
                    canal=aut.canal,
                    valores=cat.valores_de_exemplo(aut),
                )
                for p in partes:
                    if p["tipo"] == "texto":
                        assert e_mensagem_automatica(p["texto"]), (aut.codigo, nome)


def test_regua_antiga_e_o_robo_e_nao_o_bom_dia_de_pessoa():
    assert e_resposta_automatica(DUOKE[(cat.TIPO_OPCAO, 1)])
    assert not e_resposta_automatica("bom dia! ficou alguma dúvida em que eu possa te ajudar?")
    assert not e_mensagem_automatica("bom dia! ficou alguma dúvida em que eu possa te ajudar?")


@pytest.mark.parametrize(
    "texto,digito",
    [
        ("6", 6),
        (" 1 ", 1),
        ("2.", 2),
        ("opção 5", 5),
        ("Opcao 3)", 3),
        ("7", None),
        ("quero 2", None),
        ("12", None),
        ("", None),
        (None, None),
    ],
)
def test_digito_da_opcao(texto, digito):
    assert cat.digito_opcao(texto) == digito


# ── Classificação (sem texto) ─────────────────────────────────────────────


def _m(autor="cliente", minutos=0, texto="oi", payload=None, origem=None, status=None, mid=None):
    origem = origem or ("cliente" if autor == "cliente" else "externo")
    status = status or ("recebida" if autor == "cliente" else "enviada")
    return cat.classificar(
        id=mid or uuid4(),
        autor=autor,
        origem=origem,
        status=status,
        texto=texto,
        payload=payload or {},
        em=T0 + timedelta(minutes=minutos),
    )


def test_classificar_separa_comprador_pessoa_duoke_e_nosso():
    c = _m(texto="6")
    assert c.do_comprador and c.digito == 6 and c.cartao is None
    produto = _m(texto="[Produto]", payload={"type": "PRODUCT_CARD"})
    assert produto.cartao == "produto" and produto.digito is None
    pessoa = _m(autor="loja", texto="Bom dia! Seu pedido saiu ontem.")
    assert pessoa.pessoa_da_loja and pessoa.tipo_auto is None
    duoke = _m(autor="loja", texto=DUOKE[(cat.TIPO_MENU, None)])
    assert not duoke.pessoa_da_loja and duoke.tipo_auto == cat.TIPO_MENU
    campanha = _m(autor="sistema", origem="sistema", texto=DUOKE[(cat.TIPO_DUVIDA_1, None)])
    assert campanha.tipo_duoke == cat.TIPO_DUVIDA_1
    nossa = _m(
        autor="loja",
        origem="davinci_auto",
        texto=cat.TEXTO_AGUARDE,
        payload={"automacao": {"codigo": "shopee_aguarde"}},
    )
    assert nossa.nossa == "shopee_aguarde" and nossa.tipo_auto == cat.TIPO_AGUARDE
    assert nossa.tipo_duoke is None and not nossa.pessoa_da_loja
    cartao = _m(
        autor="loja",
        texto=None,
        payload={
            "source": "openapi",
            "message_type": "order",
            "content": {"order_sn": "251005ABCDEFGH"},
        },
    )
    assert cartao.pedido == "251005ABCDEFGH" and not cartao.pessoa_da_loja
    falhou = _m(autor="loja", texto="Bom dia!", status="falhou")
    assert not falhou.pessoa_da_loja


# ── Gatilhos da conversa ──────────────────────────────────────────────────


def _regra(codigo, **cond):
    aut = cat.CATALOGO[codigo]
    return SimpleNamespace(
        condicoes={**aut.condicoes, **cond},
        atraso_min=aut.atraso_min,
        janela_inicio=None,
        janela_fim=None,
        partes=list(aut.partes),
    )


def _conv(
    msgs, ativas, *, plataforma="shopee", canal="chat", registro=(), desde=None, comprador="77"
):
    return cat.Conversa(
        id="c1",
        plataforma=plataforma,
        canal=canal,
        comprador_id=comprador,
        msgs=list(msgs),
        registro=list(registro),
        ativas={c: _regra(c) for c in ativas},
        desde=desde or T0 - timedelta(hours=48),
    )


def _cands(conv):
    return sorted(cat.gatilhos_da_conversa(conv), key=lambda c: (c.automacao, c.devido_em))


def test_menu_na_1a_mensagem_e_nao_de_novo_na_sessao_de_12h():
    b1 = _m(minutos=0, mid="b1")
    b2 = _m(minutos=5, mid="b2")
    b3 = _m(minutos=13 * 60, mid="b3")
    cs = _cands(_conv([b1, b2, b3], ["shopee_menu"]))
    assert [(c.chave, c.devido_em) for c in cs] == [
        ("conversa:c1:msg:b1", T0 + timedelta(minutes=1)),
        ("conversa:c1:msg:b3", T0 + timedelta(minutes=13 * 60 + 1)),
    ]


def test_menu_rajada_no_mesmo_minuto_ganha_um_menu_so():
    """A 2ª e a 3ª mensagem antes de o menu sair não ganham outro (o Duoke manda um só)."""
    b1 = _m(minutos=0, mid="b1")
    b2 = _m(minutos=0.2, mid="b2")
    b3 = _m(minutos=0.5, mid="b3")
    assert [c.chave for c in _cands(_conv([b1, b2, b3], ["shopee_menu"]))] == ["conversa:c1:msg:b1"]
    # Na rodada seguinte, com a linha da 1ª já agendada: a mesma chave, nada novo.
    linha = cat.Linha(
        "shopee_menu", "agendado", "conversa:c1:msg:b1", b1.em, b1.em + timedelta(minutes=1)
    )
    cs = _cands(_conv([b1, b2, b3], ["shopee_menu"], registro=[linha]))
    assert [c.chave for c in cs] == ["conversa:c1:msg:b1"]
    # E a decisão da 2ª (se existisse) veria a sessão aberta pela 1ª.
    aut = cat.CATALOGO["shopee_menu"]
    f = cat.fatos_da_conversa(
        aut,
        msgs=[b1, b2],
        registro=[linha],
        chave="conversa:c1:msg:b2",
        evento_em=b2.em,
        agora=b2.em + timedelta(minutes=1),
        regra=_regra("shopee_menu"),
    )
    assert f["ja_mandado"] is True


def test_menu_do_duoke_antes_da_mensagem_cala_o_nosso_mas_o_de_depois_nao():
    antes = _m(autor="loja", minutos=-60, texto=DUOKE[(cat.TIPO_MENU, None)])
    b1 = _m(minutos=0, mid="b1")
    assert _cands(_conv([antes, b1], ["shopee_menu"])) == []
    # O Duoke respondendo ESTA mensagem (1 min depois) não cala o modo seco.
    depois = _m(autor="loja", minutos=1, texto=DUOKE[(cat.TIPO_MENU, None)])
    assert [c.chave for c in _cands(_conv([b1, depois], ["shopee_menu"]))] == ["conversa:c1:msg:b1"]


def test_descoberta_e_idempotente_com_o_registro():
    b1 = _m(minutos=0, mid="b1")
    b2 = _m(minutos=5, mid="b2")
    linha = cat.Linha(
        "shopee_menu", "simulado", "conversa:c1:msg:b1", b1.em, b1.em + timedelta(minutes=1)
    )
    cs = _cands(_conv([b1, b2], ["shopee_menu"], registro=[linha]))
    # A mesma chave volta (o ON CONFLICT descarta); a 2ª mensagem não ganha outro.
    assert [c.chave for c in cs] == ["conversa:c1:msg:b1"]


def test_opcao_so_com_menu_valendo():
    menu = _m(autor="loja", minutos=1, texto=DUOKE[(cat.TIPO_MENU, None)])
    b0 = _m(minutos=0, mid="b0")
    digito = _m(minutos=3, texto="2", mid="d2")
    cs = [
        c
        for c in _cands(_conv([b0, menu, digito], ["shopee_menu", "shopee_opcao_2"]))
        if c.automacao == "shopee_opcao_2"
    ]
    assert [(c.chave, c.devido_em) for c in cs] == [
        ("conversa:c1:msg:d2", T0 + timedelta(minutes=4))
    ]
    # Sem menu (um "2" solto pode ser "quero 2"): nada.
    solto = _m(minutos=3, texto="2", mid="d2")
    assert not [
        c
        for c in _cands(_conv([solto], ["shopee_opcao_2"]))
        if c.automacao.startswith("shopee_opcao")
    ]
    # Menu de mais de 12 h atrás: como o Duoke ("robô liberado"), quem já conhece
    # o menu e digita a opção recebe a resposta na hora, SEM menu novo.
    velho = _m(autor="loja", minutos=-13 * 60, texto=DUOKE[(cat.TIPO_MENU, None)])
    conv = _conv([velho, solto], ["shopee_menu", "shopee_opcao_2"])
    assert [c.automacao for c in _cands(conv)] == ["shopee_opcao_2"]
    # Com a condição desligada, o menu velho não vale (e o dígito ganha o menu).
    conv = _conv([velho, solto], ["shopee_menu", "shopee_opcao_2"])
    conv.ativas["shopee_opcao_2"] = _regra("shopee_opcao_2", opcao_sem_sessao=False)
    assert [c.automacao for c in _cands(conv)] == ["shopee_menu"]


def test_menu_cartao_e_texto_no_mesmo_segundo_abrem_uma_sessao():
    cartao = _m(minutos=0, texto="[Pedido]", payload={"message_type": "order"}, mid="a")
    texto = _m(minutos=0, mid="b")
    cs = _cands(_conv([cartao, texto], ["shopee_menu"]))
    assert len(cs) == 1
    linha = cat.Linha("shopee_menu", "simulado", cs[0].chave, T0, T0 + timedelta(minutes=1))
    assert [c.chave for c in _cands(_conv([cartao, texto], ["shopee_menu"], registro=[linha]))] == [
        cs[0].chave
    ]


def test_aguarde_do_tiktok_so_com_o_comprador_falando_por_ultimo():
    """Medido: o Duoke não manda o "aguarde" quando a última fala é o "já segue"
    (1 min depois da 1ª mensagem); se o comprador escreve depois dele, manda, no
    mesmo horário (10 min depois da 1ª)."""
    b1 = _m(minutos=0, mid="m1")
    convite = _m(autor="loja", minutos=1, texto="zé já segue nossa loja aqui no TikTok?")
    aut = cat.CATALOGO["tiktok_aguarde"]
    regra = _regra("tiktok_aguarde")
    agora = T0 + timedelta(minutes=10)
    f = cat.fatos_da_conversa(
        aut, msgs=[b1, convite], registro=[], chave="k", evento_em=b1.em, agora=agora, regra=regra
    )
    assert cat.decidir(aut, f, regra) == "loja_respondeu"
    b2 = _m(minutos=3, mid="m2")
    cs = _cands(_conv([b1, convite, b2], ["tiktok_aguarde"], plataforma="tiktok"))
    assert [(c.chave, c.devido_em) for c in cs] == [("conversa:c1:msg:m1", agora)]
    f = cat.fatos_da_conversa(
        aut,
        msgs=[b1, convite, b2],
        registro=[],
        chave="k",
        evento_em=b1.em,
        agora=agora,
        regra=regra,
    )
    assert cat.decidir(aut, f, regra) is None
    # Na Shopee, não: lá o convite é `sistema` (e a condição vem desligada).
    shopee = cat.CATALOGO["shopee_aguarde"]
    f = cat.fatos_da_conversa(
        shopee,
        msgs=[b1, convite],
        registro=[],
        chave="k",
        evento_em=b1.em,
        agora=agora,
        regra=_regra("shopee_aguarde"),
    )
    assert cat.decidir(shopee, f, _regra("shopee_aguarde")) is None


def test_aguarde_10_min_depois_e_o_2o_no_mesmo_turno_quando_fecha_o_intervalo():
    """Como o Duoke (medido no TikTok): escreveu de novo dentro do intervalo,
    o próximo sai quando ele fecha; escreveu depois, sai 10 min depois."""
    b1 = _m(minutos=0, mid="m1")
    b2 = _m(minutos=30, mid="m2")  # mesmo turno, dentro das 4 h
    cs = _cands(_conv([b1, b2], ["shopee_aguarde"]))
    assert [(c.chave, c.devido_em) for c in cs] == [
        ("conversa:c1:msg:m1", T0 + timedelta(minutes=10)),
        ("conversa:c1:msg:m2", T0 + timedelta(minutes=10) + timedelta(hours=4)),
    ]
    # Escreveu só depois das 4 h: 10 min depois da mensagem.
    b3 = _m(minutos=5 * 60, mid="m3")
    cs = _cands(_conv([b1, b3], ["shopee_aguarde"]))
    assert [(c.chave, c.devido_em) for c in cs] == [
        ("conversa:c1:msg:m1", T0 + timedelta(minutes=10)),
        ("conversa:c1:msg:m3", T0 + timedelta(minutes=5 * 60 + 10)),
    ]
    # A chave é a 1ª mensagem DEPOIS do último "aguarde": a 3ª mensagem do
    # mesmo trecho não vira outra linha (e a re-descoberta dá as mesmas chaves).
    b4 = _m(minutos=40, mid="m4")
    cs = _cands(_conv([b1, b2, b4], ["shopee_aguarde"]))
    assert [c.chave for c in cs] == ["conversa:c1:msg:m1", "conversa:c1:msg:m2"]
    # Pessoa respondeu no meio: o turno recomeça na mensagem seguinte.
    pessoa = _m(autor="loja", minutos=20, texto="Já vou verificar!")
    b5 = _m(minutos=40, mid="m5")
    cs = _cands(_conv([b1, pessoa, b5], ["shopee_aguarde"]))
    assert [c.chave for c in cs] == ["conversa:c1:msg:m1", "conversa:c1:msg:m5"]
    assert cs[1].devido_em == T0 + timedelta(minutes=10) + timedelta(hours=4)
    # O "aguarde" do Duoke conta como o último (o comprador viu).
    duoke = _m(autor="loja", minutos=10, texto=DUOKE[(cat.TIPO_AGUARDE, None)])
    cs = _cands(_conv([b1, duoke, b2], ["shopee_aguarde"]))
    assert cs[1].devido_em == T0 + timedelta(minutes=10) + timedelta(hours=4)


def test_convite_uma_vez_por_comprador_e_tiktok_na_1a_da_conversa():
    b1 = _m(minutos=0, mid="b1")
    b2 = _m(minutos=10, mid="b2")
    cs = _cands(_conv([b1, b2], ["shopee_convite"]))
    assert [(c.chave, c.alvo) for c in cs] == [("comprador:77", cat.ALVO_COMPRADOR)]
    duoke = _m(
        autor="sistema", origem="sistema", minutos=-600, texto=DUOKE[(cat.TIPO_CONVITE, None)]
    )
    assert _cands(_conv([duoke, b1], ["shopee_convite"])) == []
    tt = _cands(_conv([b1, b2], ["tiktok_convite"], plataforma="tiktok"))
    assert [c.chave for c in tt] == ["conversa:c1"]


def test_convite_da_shopee_tambem_na_pergunta_pronta_do_chat():
    """O comprador tocou na pergunta pronta (o robô da Shopee respondeu): o convite
    do Duoke sai do mesmo jeito (medido: 31 dos 43 convites sem mensagem antes)."""
    bundle = cat.classificar(
        id="x1",
        autor="sistema",
        origem="sistema",
        status="recebida",
        texto="[Mensagem]",
        payload={"message_type": "bundle_message", "source": "server"},
        em=T0,
    )
    assert bundle.interacao and not bundle.do_comprador
    cs = _cands(_conv([bundle], ["shopee_convite", "shopee_menu"]))
    assert [c.automacao for c in cs] == ["shopee_convite"]


def test_pos_so_com_conversa():
    aut = cat.CATALOGO["shopee_pos_conclusao"]
    f = {"status_pedido": "COMPLETED", "tem_conversa": False}
    assert cat.decidir(aut, f, _regra("shopee_pos_conclusao")) == "sem_conversa"
    f["tem_conversa"] = True
    assert cat.decidir(aut, f, _regra("shopee_pos_conclusao")) is None


def test_duvida_conta_da_1a_mensagem_do_ciclo_de_7_dias():
    b1 = _m(minutos=0, mid="b1")
    b2 = _m(minutos=30, mid="b2")
    cs = _cands(_conv([b1, b2], ["shopee_duvida_2h"]))
    assert [(c.chave, c.devido_em) for c in cs] == [("conversa:c1:msg:b1", T0 + timedelta(hours=2))]
    # O Duoke mandou há 3 dias: ainda no ciclo, nada.
    duoke = _m(
        autor="sistema",
        origem="sistema",
        minutos=-3 * 24 * 60,
        texto=DUOKE[(cat.TIPO_DUVIDA_1, None)],
    )
    assert _cands(_conv([duoke, b1], ["shopee_duvida_2h"])) == []
    # Uma linha nossa (até pulada) de 2 dias atrás também segura o ciclo.
    linha = cat.Linha("shopee_duvida_2h", "pulado", "conversa:c1:msg:x", T0 - timedelta(days=2), T0)
    assert _cands(_conv([b1], ["shopee_duvida_2h"], registro=[linha])) == []


def _duoke_duvida(tipo, minutos):
    return _m(autor="sistema", origem="sistema", minutos=minutos, texto=DUOKE[(tipo, None)])


def _cartao(minutos, mid):
    return _m(minutos=minutos, mid=mid, texto="[Produto]", payload={"message_type": "item"})


def test_duvida_da_shopee_recomeca_no_cartao_depois_da_26h_do_duoke():
    """Medido no Duoke (06/10): depois da 26 h, o próximo cartão de produto abre outro ciclo.

    O cartão que o Duoke respondeu (2 h antes da 1ª dele) é do ciclo dele — não
    abre um de 7 dias por conta própria —, o texto solto depois da 26 h não
    recomeça, e o cartão depois da 26 h recomeça, contando 2 h dele.
    """
    gatilho = _cartao(-28 * 60, "b0")
    d1 = _duoke_duvida(cat.TIPO_DUVIDA_1, -26 * 60)
    d2 = _duoke_duvida(cat.TIPO_DUVIDA_2, -2 * 60)
    texto = _m(minutos=-60, mid="b1")
    cartao = _cartao(0, "b2")
    desde = T0 - timedelta(minutes=90)
    cs = _cands(_conv([gatilho, d1, d2, texto, cartao], ["shopee_duvida_2h"], desde=desde))
    assert [(c.chave, c.devido_em) for c in cs] == [("conversa:c1:msg:b2", T0 + timedelta(hours=2))]
    # Só texto depois da 26 h: nada (o ciclo de 7 dias continua).
    assert _cands(_conv([gatilho, d1, d2, texto], ["shopee_duvida_2h"], desde=desde)) == []
    # Sem a 26 h (o Duoke não mandou a 2ª): o cartão fica no ciclo de 7 dias.
    assert _cands(_conv([gatilho, d1, texto, cartao], ["shopee_duvida_2h"], desde=desde)) == []
    # O cartão ANTES da 26 h sair também fica no ciclo.
    cedo = _cartao(-3 * 60, "b3")
    assert _cands(_conv([gatilho, d1, cedo, d2], ["shopee_duvida_2h"], desde=desde)) == []
    # A condição desligada na regra: volta o ciclo de 7 dias.
    conv = _conv([gatilho, d1, d2, texto, cartao], ["shopee_duvida_2h"], desde=desde)
    conv.ativas["shopee_duvida_2h"] = _regra(
        "shopee_duvida_2h", recomeca_no_cartao_depois_da_segunda=False
    )
    assert _cands(conv) == []


def test_duvida_da_shopee_o_ciclo_nosso_termina_quando_a_26h_sai():
    """Modo seco: o ciclo é o das NOSSAS linhas — fecha na hora da 26 h que JÁ saiu."""
    cartao = _cartao(0, "b2")
    texto = _m(minutos=5, mid="b4")
    um = cat.Linha(
        "shopee_duvida_2h", "simulado", "conversa:c1:msg:b0", T0 - timedelta(hours=28), T0
    )

    def seguinte(estado, devido):
        return cat.Linha("shopee_duvida_26h", estado, "conversa:c1:msg:b0", um.evento_em, devido)

    def cands(*registro):
        return _cands(_conv([cartao, texto], ["shopee_duvida_2h"], registro=list(registro)))

    # A 26 h saiu 1 h antes do cartão: outro ciclo (a 2 h conta do cartão; o texto fica nele).
    for estado in ("simulado", "enviado", "enviando", "revisar"):
        cs = cands(um, seguinte(estado, T0 - timedelta(hours=1)))
        assert [(c.chave, c.devido_em) for c in cs] == [
            ("conversa:c1:msg:b2", T0 + timedelta(hours=2))
        ], estado
    # Agendada para depois do cartão (ainda não saiu): o ciclo continua.
    assert cands(um, seguinte("agendado", T0 + timedelta(minutes=30))) == []
    # Agendada e já vencida, mas ainda não decidida: também não fecha (a decisão
    # pode pular — teto, atrasado, regra desligada). A rodada seguinte, com a 26 h
    # decidida, revê o cartão (a descoberta relê 40 min; teste no motor).
    assert cands(um, seguinte("agendado", T0 - timedelta(minutes=1))) == []
    # A 26 h pulada (ou que falhou, ou nenhuma: a 2 h pulada) não fecha o ciclo: 7 dias.
    for estado in ("pulado", "falhou"):
        assert cands(um, seguinte(estado, T0 - timedelta(hours=1))) == [], estado
    assert cands(um) == []
    # Depois dos 7 dias, qualquer mensagem abre outro (como antes).
    velha = cat.Linha(
        "shopee_duvida_2h", "pulado", "conversa:c1:msg:b9", T0 - timedelta(days=8), T0
    )
    assert [c.chave for c in cands(velha)] == ["conversa:c1:msg:b2"]


def test_duvida_da_shopee_a_26h_simulada_nao_fecha_o_ciclo_da_1a_enviada():
    """Revisão de 06/10: a 2 h em `enviar` e a 26 h em `simular` (a 2 h deve passar no
    critério antes). O comprador recebeu a 1ª e NÃO recebeu a 2ª: a 26 h simulada
    não fecha o ciclo, e o cartão depois dela não ganha outra 1ª (seria "Ficou
    alguma dúvida?" a cada ~26 h sem a 2ª no meio). A 26 h enviada fecha; a 1ª
    simulada com a 26 h enviada (a 26 h trocada antes) também."""
    cartao = _cartao(0, "b2")
    chave = "conversa:c1:msg:b0"
    evento = T0 - timedelta(hours=28)

    def cands(estado_1a, estado_2a):
        registro = [
            cat.Linha("shopee_duvida_2h", estado_1a, chave, evento, evento + timedelta(hours=2)),
            cat.Linha("shopee_duvida_26h", estado_2a, chave, evento, T0 - timedelta(hours=1)),
        ]
        return [c.chave for c in _cands(_conv([cartao], ["shopee_duvida_2h"], registro=registro))]

    for estado_1a in ("enviado", "enviando", "revisar"):
        assert cands(estado_1a, "simulado") == [], estado_1a
        assert cands(estado_1a, "enviado") == ["conversa:c1:msg:b2"], estado_1a
    assert cands("simulado", "simulado") == ["conversa:c1:msg:b2"]
    assert cands("simulado", "enviado") == ["conversa:c1:msg:b2"]

    # Com as nossas mensagens que voltaram pela leitura (`davinci_auto`), o mesmo:
    # a 1ª enviada sem a 2ª de verdade segura o cartão; com a 2ª enviada, recomeça.
    def nossa(codigo, texto, minutos):
        m = _m(
            autor="loja",
            origem=cat.ORIGEM_AUTO,
            minutos=minutos,
            texto=texto,
            payload={"automacao": {"codigo": codigo}},
        )
        assert m.nossa == codigo
        return m

    d1 = nossa("shopee_duvida_2h", cat.TEXTO_DUVIDA_1, -26 * 60)
    d2 = nossa("shopee_duvida_26h", cat.TEXTO_DUVIDA_2, -60)

    def com_msgs(msgs, estado_2a):
        registro = [
            cat.Linha("shopee_duvida_2h", "enviado", chave, evento, evento + timedelta(hours=2)),
            cat.Linha("shopee_duvida_26h", estado_2a, chave, evento, T0 - timedelta(hours=1)),
        ]
        conv = _conv([*msgs, cartao], ["shopee_duvida_2h"], registro=registro)
        return [c.chave for c in _cands(conv)]

    assert com_msgs([d1], "simulado") == []
    assert com_msgs([d1, d2], "enviado") == ["conversa:c1:msg:b2"]


def test_duvida_da_shopee_o_so_duoke_conta_como_a_do_duoke():
    """No modo seco a mensagem do Duoke sai do estado e fica a linha "só Duoke" do
    comparador: a 1ª dela abre o ciclo (com o gatilho de 2 h antes) e a 2ª fecha."""
    gatilho = _cartao(-28 * 60, "b0")
    cartao = _cartao(0, "b2")

    def so_duoke(codigo, horas, mid):
        em = T0 - timedelta(hours=horas)
        return cat.Linha(codigo, "so_duoke", f"duoke:{mid}", em, em)

    um = so_duoke("shopee_duvida_2h", 26, "d1")
    dois = so_duoke("shopee_duvida_26h", 2, "d2")
    desde = T0 - timedelta(minutes=90)

    def cands(*registro):
        conv = _conv([gatilho, cartao], ["shopee_duvida_2h"], registro=list(registro), desde=desde)
        return [(c.chave, c.devido_em) for c in _cands(conv)]

    assert cands(um, dois) == [("conversa:c1:msg:b2", T0 + timedelta(hours=2))]
    assert cands(um) == [], "sem a 2ª do Duoke, o ciclo de 7 dias continua"
    # A 2ª de OUTRO ciclo (antes da 1ª) não fecha este (o gatilho aqui é texto).
    texto = _m(minutos=-28 * 60, mid="b0")
    antiga = so_duoke("shopee_duvida_26h", 30, "d0")
    conv = _conv([texto, cartao], ["shopee_duvida_2h"], registro=[um, antiga], desde=desde)
    assert _cands(conv) == []
    conv = _conv([texto, cartao], ["shopee_duvida_2h"], registro=[um, dois], desde=desde)
    assert [c.chave for c in _cands(conv)] == ["conversa:c1:msg:b2"]


def test_duvida_do_tiktok_continua_no_ciclo_de_7_dias():
    """No TikTok o recomeço não se confirmou (2 casos em 2 meses: 1 recomeçou, 1 não)."""
    assert "recomeca_no_cartao_depois_da_segunda" not in cat.CATALOGO["tiktok_duvida_2h"].condicoes
    cartao = _m(minutos=0, mid="b2", texto="[Produto]", payload={"type": "PRODUCT_CARD"})
    um = cat.Linha(
        "tiktok_duvida_2h", "simulado", "conversa:c1:ciclo:b0", T0 - timedelta(hours=28), T0
    )
    dois = cat.Linha(
        "tiktok_duvida_24h",
        "simulado",
        "conversa:c1:ciclo:b0",
        um.evento_em,
        T0 - timedelta(hours=1),
    )
    conv = _conv([cartao], ["tiktok_duvida_2h"], plataforma="tiktok", registro=[um, dois])
    assert _cands(conv) == []


def test_duvida_do_tiktok_anda_com_a_ultima_mensagem():
    b1 = _m(minutos=0, mid="b1")
    b2 = _m(minutos=50, mid="b2")
    cs = _cands(_conv([b1, b2], ["tiktok_duvida_2h"], plataforma="tiktok"))
    assert len(cs) == 1
    assert cs[0].chave == "conversa:c1:ciclo:b1"
    assert cs[0].devido_em == T0 + timedelta(minutes=50 + 120)
    assert cs[0].atualizar_agendado


def test_ml_menu_no_pos_venda_e_regra_de_outra_caixa_nao_dispara():
    b1 = _m(minutos=0, mid="b1")
    cs = _cands(_conv([b1], ["ml_menu"], plataforma="ml", canal="pos_venda"))
    assert [c.automacao for c in cs] == ["ml_menu"]
    assert _cands(_conv([b1], ["ml_menu"], plataforma="ml", canal="pergunta")) == []


def test_so_nasce_de_mensagem_vista_depois_de_ligar():
    b1 = _m(minutos=0, mid="b1")
    assert _cands(_conv([b1], ["shopee_menu"], desde=T0 + timedelta(minutes=5))) == []


# ── Fatos da conversa e decisão ───────────────────────────────────────────


def test_fatos_pessoa_respondeu_e_o_cartao_nao_e_pessoa():
    aut = cat.CATALOGO["shopee_aguarde"]
    b1 = _m(minutos=0, mid="b1")
    cartao = _m(
        autor="loja",
        minutos=2,
        texto=None,
        payload={"source": "openapi", "message_type": "order", "content": {"order_sn": "x"}},
    )
    menu = _m(autor="loja", minutos=1, texto=DUOKE[(cat.TIPO_MENU, None)])
    f = cat.fatos_da_conversa(
        aut,
        msgs=[b1, menu, cartao],
        registro=[],
        chave="k",
        evento_em=b1.em,
        agora=T0 + timedelta(minutes=10),
        regra=_regra("shopee_aguarde"),
    )
    assert f["pessoa_respondeu"] is False
    pessoa = _m(autor="loja", minutos=4, texto="Oi! Pode mandar foto?")
    f = cat.fatos_da_conversa(
        aut,
        msgs=[b1, pessoa],
        registro=[],
        chave="k",
        evento_em=b1.em,
        agora=T0 + timedelta(minutes=10),
        regra=_regra("shopee_aguarde"),
    )
    assert f["pessoa_respondeu"] is True
    assert cat.decidir(aut, f, _regra("shopee_aguarde")) == "pessoa_respondeu"


def test_fatos_o_duoke_depois_do_gatilho_so_aparece_em_duoke_depois():
    aut = cat.CATALOGO["shopee_menu"]
    b1 = _m(minutos=0, mid="b1")
    duoke = _m(autor="loja", minutos=1, texto=DUOKE[(cat.TIPO_MENU, None)])
    f = cat.fatos_da_conversa(
        aut,
        msgs=[b1, duoke],
        registro=[],
        chave="k",
        evento_em=b1.em,
        agora=T0 + timedelta(minutes=2),
        regra=_regra("shopee_menu"),
    )
    # O modo seco não se cala pelo menu que o Duoke mandou para a MESMA mensagem...
    assert f["ja_mandado"] is False
    assert cat.decidir(aut, f, _regra("shopee_menu")) is None
    # ...mas o modo enviar vê (e não manda em dobro).
    assert f["duoke_depois"] == [duoke.em]


# ── O modo seco mede o DaVinci sozinho (crítica de 05/10) ─────────────────


def test_automacao_do_modelo_do_duoke():
    assert cat.automacao_do_modelo(cat.TIPO_MENU, None, "shopee", "chat") == "shopee_menu"
    assert cat.automacao_do_modelo(cat.TIPO_MENU, None, "ml", "pos_venda") == "ml_menu"
    assert cat.automacao_do_modelo(cat.TIPO_OPCAO, 6, "shopee", "chat") == "shopee_opcao_6"
    assert cat.automacao_do_modelo(cat.TIPO_AGUARDE, None, "tiktok", "chat") == "tiktok_aguarde"
    assert cat.automacao_do_modelo(cat.TIPO_DUVIDA_2, None, "tiktok", "chat") == "tiktok_duvida_24h"
    assert cat.automacao_do_modelo(cat.TIPO_OPCAO, 9, "shopee", "chat") is None
    assert cat.automacao_do_modelo(None, None, "shopee", "chat") is None


def test_cortes_do_modo_seco_so_das_regras_em_simular():
    ligada = T0 - timedelta(days=3)
    motor = T0 - timedelta(days=1)
    regras = [
        SimpleNamespace(automacao="shopee_menu", modo="simular", ligada_desde=ligada),
        SimpleNamespace(automacao="shopee_opcao_6", modo="enviar", ligada_desde=ligada),
        SimpleNamespace(automacao="shopee_aguarde", modo="desligado", ligada_desde=None),
        SimpleNamespace(automacao="shopee_opcao_2", modo="simular", ligada_desde=None),
    ]
    # O mais tarde entre a regra ligada e o motor rodando (antes, o DaVinci não tinha linha).
    assert cat.cortes_do_modo_seco(regras, motor) == {
        "shopee_menu": motor,
        "shopee_opcao_2": motor,
    }
    assert cat.cortes_do_modo_seco(regras) == {"shopee_menu": ligada}
    assert cat.cortes_do_modo_seco([]) == {}


def test_o_duoke_que_o_davinci_simula_sai_do_estado_depois_do_corte():
    corte = T0 - timedelta(hours=1)
    antes = _m(autor="loja", minutos=-120, texto=DUOKE[(cat.TIPO_MENU, None)])
    depois = _m(autor="loja", minutos=0, texto=DUOKE[(cat.TIPO_MENU, None)])
    opcao = _m(autor="loja", minutos=5, texto=DUOKE[(cat.TIPO_OPCAO, 6)])
    convite = _m(autor="loja", minutos=6, texto=DUOKE[(cat.TIPO_CONVITE, None)])
    nossa = _m(
        autor="loja",
        origem="davinci_auto",
        minutos=7,
        texto=cat.TEXTO_MENU,
        payload={"automacao": {"codigo": "shopee_menu"}},
    )
    comprador = _m(minutos=8)
    msgs = [antes, depois, opcao, convite, nossa, comprador]
    estado = cat.sem_o_duoke_substituido(
        msgs, plataforma="shopee", canal="chat", cortes={"shopee_menu": corte}
    )
    # Sai só o menu do Duoke DEPOIS do corte; a opção (regra não simulada aqui),
    # o convite (fora dos tipos do robô), a nossa e a do comprador ficam.
    assert estado == [antes, opcao, convite, nossa, comprador]
    cortes = {"shopee_menu": corte, "shopee_opcao_6": corte, "shopee_convite": corte}
    assert cat.sem_o_duoke_substituido(msgs, plataforma="shopee", canal="chat", cortes=cortes) == [
        antes,
        convite,
        nossa,
        comprador,
    ]
    assert cat.sem_o_duoke_substituido(msgs, plataforma="shopee", canal="chat", cortes={}) == msgs


def test_a_opcao_tardia_do_duoke_nao_segura_o_menu_do_davinci():
    """A contraprova da simulação: menu, dígito 6, a resposta do Duoke 12 h depois do
    menu; o comprador escreve 13 h depois. O DaVinci respondeu a opção 1 min depois do
    dígito — a sessão DELE acabou 12 h depois disso, e ele manda o menu. Com o Duoke no
    estado, a resposta tardia segurava a sessão e o modo seco nem criava a linha."""
    corte = T0 - timedelta(days=1)
    menu = _m(autor="loja", minutos=0, texto=DUOKE[(cat.TIPO_MENU, None)])
    digito = _m(minutos=1, texto="6", mid="d6")
    tardia = _m(autor="loja", minutos=12 * 60, texto=DUOKE[(cat.TIPO_OPCAO, 6)])
    volta = _m(minutos=13 * 60, mid="b13")
    msgs = [menu, digito, tardia, volta]
    # As linhas do DaVinci no registro: o menu (para a mensagem de antes) e a opção 6.
    registro = [
        cat.Linha(
            "shopee_menu", "simulado", "conversa:c1:msg:b0", menu.em, menu.em + timedelta(minutes=1)
        ),
        cat.Linha(
            "shopee_opcao_6",
            "simulado",
            "conversa:c1:msg:d6",
            digito.em,
            digito.em + timedelta(minutes=1),
        ),
    ]
    ativas = ["shopee_menu", "shopee_opcao_6"]
    cortes = {"shopee_menu": corte, "shopee_opcao_6": corte}
    # Com o Duoke no estado (o modo seco de antes): nenhum menu para a volta.
    dependente = _cands(_conv(msgs, ativas, registro=registro))
    assert "conversa:c1:msg:b13" not in [c.chave for c in dependente]
    # Sozinho: o menu do DaVinci sai para a volta do comprador.
    estado = cat.sem_o_duoke_substituido(msgs, plataforma="shopee", canal="chat", cortes=cortes)
    independente = _cands(_conv(estado, ativas, registro=registro))
    assert [c.chave for c in independente if c.automacao == "shopee_menu"] == [
        "conversa:c1:msg:b13"
    ]
    # E a decisão da linha vê o mesmo (o `duoke_depois` continua vendo tudo).
    aut = cat.CATALOGO["shopee_menu"]
    f = cat.fatos_da_conversa(
        aut,
        msgs=msgs,
        registro=registro,
        chave="conversa:c1:msg:b13",
        evento_em=volta.em,
        agora=volta.em + timedelta(minutes=1),
        regra=_regra("shopee_menu"),
        cortes=cortes,
    )
    assert f["ja_mandado"] is False
    sem_corte = cat.fatos_da_conversa(
        aut,
        msgs=msgs,
        registro=registro,
        chave="conversa:c1:msg:b13",
        evento_em=volta.em,
        agora=volta.em + timedelta(minutes=1),
        regra=_regra("shopee_menu"),
    )
    assert sem_corte["ja_mandado"] is True


def test_opcao_ja_respondida_pelo_duoke_simulado_vale_a_linha_do_davinci():
    aut = cat.CATALOGO["shopee_opcao_6"]
    corte = T0 - timedelta(days=1)
    menu = _m(autor="loja", minutos=0, texto=DUOKE[(cat.TIPO_MENU, None)])
    d1 = _m(minutos=2, texto="6")
    resposta = _m(autor="loja", minutos=3, texto=DUOKE[(cat.TIPO_OPCAO, 6)])
    d2 = _m(minutos=10, texto="6")
    tardia = _m(autor="loja", minutos=12 * 60, texto=DUOKE[(cat.TIPO_OPCAO, 6)])
    f = cat.fatos_da_conversa(
        aut,
        msgs=[menu, d1, resposta, d2, tardia],
        registro=[],
        chave="k",
        evento_em=d2.em,
        agora=T0 + timedelta(minutes=11),
        regra=_regra("shopee_opcao_6"),
        cortes={"shopee_menu": corte, "shopee_opcao_6": corte},
    )
    # A resposta do Duoke (simulada aqui) não conta; o menu também não: sem
    # linha do DaVinci, a 2ª vez do "6" é a 1ª resposta dele.
    assert f["opcao_ja_respondida"] is False
    # O "Duoke ainda ligado?" do modo enviar continua vendo as mensagens dele.
    assert f["duoke_depois"] == [tardia.em]


def test_opcao_ja_respondida_na_sessao():
    aut = cat.CATALOGO["shopee_opcao_6"]
    menu = _m(autor="loja", minutos=0, texto=DUOKE[(cat.TIPO_MENU, None)])
    d1 = _m(minutos=2, texto="6")
    resposta = _m(autor="loja", minutos=3, texto=DUOKE[(cat.TIPO_OPCAO, 6)])
    d2 = _m(minutos=10, texto="6")
    f = cat.fatos_da_conversa(
        aut,
        msgs=[menu, d1, resposta, d2],
        registro=[],
        chave="k",
        evento_em=d2.em,
        agora=T0 + timedelta(minutes=11),
        regra=_regra("shopee_opcao_6"),
    )
    assert f["opcao_ja_respondida"] is True
    assert cat.decidir(aut, f, _regra("shopee_opcao_6")) == "opcao_ja_respondida"


@pytest.mark.parametrize(
    "codigo,fatos,motivo",
    [
        ("shopee_menu", {"canal_sem_acesso": True}, "loja_sem_acesso"),
        ("shopee_menu", {"canal_auto": True}, "ia_no_automatico"),
        ("shopee_menu", {"conversa_bloqueada": True}, "conversa_bloqueada"),
        ("shopee_menu", {"ja_mandado": True}, "ja_mandado"),
        (
            "shopee_menu",
            {"etiqueta": "reclamacao", "pessoa_respondeu_24h": True},
            "disputa_com_pessoa",
        ),
        # Menu, opção e "aguarde" respondem ao comprador mesmo com reclamação.
        ("shopee_menu", {"reclamacao_aberta": True}, None),
        ("shopee_opcao_1", {"pessoa_respondeu": True}, "pessoa_respondeu"),
        ("shopee_aguarde", {"ja_mandado": True}, "ja_mandado"),
        ("shopee_convite", {"ja_recebeu": True}, "ja_recebeu"),
        ("shopee_convite", {"reclamacao_aberta": True}, "reclamacao_aberta"),
        ("shopee_convite", {"etiqueta": "devolucao"}, "devolucao"),
        ("tiktok_convite", {"nao_e_primeira": True}, "nao_e_primeira"),
        ("shopee_duvida_2h", {"ja_comprou": True}, "ja_comprou"),
        ("shopee_duvida_2h", {"tem_cartao_pedido": True}, "ja_comprou"),
        # Igual ao Duoke: vai mesmo com a equipe tendo respondido.
        ("shopee_duvida_2h", {"pessoa_respondeu": True}, None),
        ("shopee_duvida_26h", {"ja_comprou": True}, "ja_comprou"),
        ("tiktok_duvida_2h", {"tem_cartao_produto": False}, "sem_cartao_produto"),
        ("tiktok_duvida_2h", {"tem_cartao_produto": True}, None),
        ("shopee_pedido_recebido", {"pedido_cancelado": True}, "pedido_cancelado"),
        ("shopee_pedido_recebido", {"devolucao_aberta": True}, "devolucao"),
        ("shopee_pedido_recebido", {"janela_shopee_ok": False}, "fora_da_janela_shopee"),
        ("shopee_entregue", {"status_pedido": "COMPLETED"}, "ja_concluido"),
        ("shopee_entregue", {"status_pedido": "TO_RETURN"}, "status_mudou"),
        ("shopee_entregue", {"status_pedido": "TO_CONFIRM_RECEIVE"}, None),
        # Devolução ENCERRADA também: o Duoke não manda para quem devolveu.
        (
            "shopee_entregue",
            {"status_pedido": "TO_CONFIRM_RECEIVE", "devolucao_qualquer": True},
            "devolucao",
        ),
        (
            "shopee_pos_conclusao",
            {"status_pedido": "COMPLETED", "devolucao_qualquer": True},
            "devolucao",
        ),
        ("shopee_pos_conclusao", {"status_pedido": "COMPLETED", "avaliou": True}, "avaliou"),
        ("shopee_pos_conclusao", {"status_pedido": "COMPLETED", "tem_conversa": True}, None),
        ("ml_menu", {"via_agente": True}, "via_agente"),
        ("ml_menu", {"reclamacao_ml": True}, "reclamacao_aberta"),
        ("shopee_opcao_4", {}, "sem_texto"),
        # O pedido não pago com cupom ("carrinho").
        (
            "shopee_nao_pago",
            {"status_pedido": "UNPAID", "total_pedido": 1200, "valor_cupom": 20},
            None,
        ),
        (
            "shopee_nao_pago",
            {"pedido_pago": True, "total_pedido": 1200, "valor_cupom": 20},
            "pedido_pago",
        ),
        ("shopee_nao_pago", {"pedido_cancelado": True}, "pedido_cancelado"),
        ("shopee_nao_pago", {"status_pedido": "READY_TO_SHIP"}, "status_mudou"),
        ("shopee_nao_pago", {"status_pedido": "UNPAID"}, "sem_valor"),
        ("shopee_nao_pago", {"status_pedido": "UNPAID", "total_pedido": 950}, "abaixo_do_minimo"),
        (
            "shopee_nao_pago",
            {"total_pedido": 1200, "valor_cupom": 20, "ja_recebeu": True},
            "ja_recebeu",
        ),
        ("shopee_nao_pago", {"etiqueta": "reclamacao", "total_pedido": 1200}, "reclamacao_aberta"),
        # A resposta da avaliação: vai como o Duoke (sem as exclusões das campanhas).
        ("shopee_avaliacao_boa", {}, None),
        ("shopee_avaliacao_boa", {"reclamacao_aberta": True}, None),
        ("shopee_avaliacao_boa", {"estrelas_mudaram": True}, "estrelas_mudaram"),
        ("shopee_avaliacao_ruim", {"pessoa_respondeu": True}, "pessoa_respondeu"),
        # O "pedido recebido" do TikTok: as exclusões das campanhas.
        ("tiktok_pedido_recebido", {}, None),
        ("tiktok_pedido_recebido", {"etiqueta": "devolucao"}, "devolucao"),
    ],
)
def test_decidir_condicoes_e_exclusoes(codigo, fatos, motivo):
    aut = cat.CATALOGO[codigo]
    assert cat.decidir(aut, fatos, _regra(codigo)) == motivo


def test_condicao_da_regra_vale_sobre_o_padrao():
    aut = cat.CATALOGO["shopee_duvida_2h"]
    assert cat.decidir(aut, {"pessoa_respondeu": True}, _regra("shopee_duvida_2h")) is None
    regra = _regra("shopee_duvida_2h", nao_se_pessoa_respondeu=True)
    assert cat.decidir(aut, {"pessoa_respondeu": True}, regra) == "pessoa_respondeu"


# ── Horário, validade, comparação ─────────────────────────────────────────


def test_janela_9h_20h_em_sao_paulo():
    aut = cat.CATALOGO["shopee_entregue"]
    ini, fim = aut.janela
    madrugada = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)  # 03h SP
    assert cat.ajustar_janela(madrugada, ini, fim) == datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    tarde = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)  # 15h SP: fica
    assert cat.ajustar_janela(tarde, ini, fim) == tarde
    noite = datetime(2026, 10, 5, 23, 30, tzinfo=UTC)  # 20h30 SP: dia seguinte 9h
    assert cat.ajustar_janela(noite, ini, fim) == datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    assert cat.ajustar_janela(noite, None, None) == noite


def test_validade_e_janela_de_comparacao():
    menu = cat.CATALOGO["shopee_menu"]
    assert cat.validade_ate(menu, T0, None) == T0 + timedelta(minutes=30)
    entregue = cat.CATALOGO["shopee_entregue"]
    # Até o fim da janela do dia seguinte (20h SP = 23h UTC).
    assert cat.validade_ate(entregue, T0, (time(9), time(20))) == datetime(
        2026, 10, 6, 23, 0, tzinfo=UTC
    )
    pos = cat.CATALOGO["shopee_pos_conclusao"]
    assert cat.janela_comparacao(pos, T0, T0 + timedelta(hours=4)) == (
        T0 + timedelta(hours=3),
        T0 + timedelta(hours=8),
    )
    assert cat.janela_comparacao(menu, T0, T0 + timedelta(minutes=1)) == (
        T0 + timedelta(minutes=-4),
        T0 + timedelta(minutes=21),
    )


# ── Semente ───────────────────────────────────────────────────────────────


def test_semente_simular_onde_o_duoke_manda_e_desligado_no_resto():
    menu = cat.CATALOGO["shopee_menu"]
    assert cat.regra_semente(menu, "Barbosa")["modo"] == cat.MODO_SIMULAR
    assert cat.regra_semente(menu, "atv")["modo"] == cat.MODO_DESLIGADO
    assert cat.regra_semente(menu, "aguiar")["modo"] == cat.MODO_DESLIGADO
    assert cat.regra_semente(cat.CATALOGO["shopee_aguarde"], "atv")["modo"] == cat.MODO_SIMULAR
    assert (
        cat.regra_semente(cat.CATALOGO["shopee_opcao_4"], "barbosa")["modo"] == cat.MODO_DESLIGADO
    )
    assert cat.regra_semente(cat.CATALOGO["ml_menu"], "victor mei")["modo"] == cat.MODO_SIMULAR
    entregue = cat.regra_semente(cat.CATALOGO["shopee_entregue"], "kfa")
    assert (entregue["janela_inicio"], entregue["janela_fim"]) == (time(9), time(20))
    # Toda automação do catálogo tem loja no Duoke (menos a opção 4, travada).
    for aut in cat.CATALOGO.values():
        assert bool(aut.lojas_duoke) != aut.travada, aut.codigo


def test_divergencia_do_motivo_operacional():
    assert cat.divergencia_do_motivo("atrasado") == "motor_atrasado"
    assert cat.divergencia_do_motivo("teto_dia") == "teto_dia"
    assert cat.divergencia_do_motivo("ja_comprou") is None
    assert cat.divergencia_do_motivo(None) is None


# ── As que faltavam do Duoke (05/10/2026, à noite): só simulação ──────────


def test_cupom_pela_faixa_do_duoke():
    """A tabela tirada dos textos do Duoke (o valor do texto × o total do pedido)."""
    assert cat.valor_cupom("Barbosa", 999.99) is None, "celular abaixo de R$ 1.000 não ganha"
    assert cat.valor_cupom("barbosa", 1000) == 20
    assert cat.valor_cupom("barbosa", 1495.84) == 20
    assert cat.valor_cupom("barbosa", 1500.47) == 25
    assert cat.valor_cupom("barbosa", 1930) == 25
    assert cat.valor_cupom("barbosa", 2020.88) == 30
    assert cat.valor_cupom("vortan", 14712.88) == 30
    assert cat.valor_cupom(" INOVA ", 157.23) == 5
    assert cat.valor_cupom("inova", 149.99) is None, "mala abaixo de R$ 150 não ganha (0 de 4)"
    assert cat.valor_cupom("kfa", 499.53) == 5
    assert cat.valor_cupom("minas", 507) == 10
    assert cat.valor_cupom("poofy", 804.6) == 10
    assert cat.valor_cupom("barbosa", None) is None
    assert cat.tipo_da_loja("Inova") == "mala" and cat.tipo_da_loja("mega") == "celular"


def test_o_cupom_entra_no_texto_e_sem_valor_o_validador_barra():
    aut = cat.CATALOGO["shopee_nao_pago"]
    partes, motivos = cat.renderizar(
        cat.partes_padrao(aut, "barbosa"),
        comprador="maria.silva",
        plataforma="shopee",
        canal="chat",
        valores={"valor_cupom": "25"},
    )
    assert motivos == []
    assert partes[0] == {"tipo": "cartao_pedido"}
    assert "💸 R$25 OFF" in partes[1]["texto"] and "{" not in partes[1]["texto"]
    assert "maria.silva" not in partes[1]["texto"], "o do Duoke não leva o nome"
    _, sem_valor = cat.renderizar(
        cat.partes_padrao(aut, "barbosa"), comprador=None, plataforma="shopee", canal="chat"
    )
    assert any("lacuna" in m for m in sem_valor)
    assert cat.placeholders_de(aut).keys() == {"comprador", "valor_cupom"}
    assert cat.placeholders_de(cat.CATALOGO["shopee_menu"]).keys() == {"comprador"}


def test_resposta_da_avaliacao_publica_mais_chat_com_o_nome():
    boa = cat.CATALOGO["shopee_avaliacao_boa"]
    partes, motivos = cat.renderizar(
        cat.partes_padrao(boa), comprador="ana.paula", plataforma="shopee", canal="chat"
    )
    assert motivos == []
    assert [p["tipo"] for p in partes] == ["resposta_publica", "texto"]
    assert partes[0]["texto"] == "Obrigado pela confiança! 🙏 Volte sempre que precisar!"
    assert partes[1]["texto"] == "Obrigado pela confiança, ana.paula! 🙏 Volte sempre que precisar!"
    ruim = cat.CATALOGO["shopee_avaliacao_ruim"]
    partes, _ = cat.renderizar(
        cat.partes_padrao(ruim), comprador=None, plataforma="shopee", canal="chat"
    )
    assert partes[1]["texto"] == cat.TEXTO_AVALIACAO_RUIM_PUBLICA, "sem o nome: só o público"
    # O chat do Duoke (22 de 22): o usuário, um espaço e o texto público, sem vírgula.
    partes, _ = cat.renderizar(
        cat.partes_padrao(ruim), comprador="ana.paula", plataforma="shopee", canal="chat"
    )
    assert partes[1]["texto"] == "ana.paula " + cat.TEXTO_AVALIACAO_RUIM_PUBLICA
    assert partes[0]["texto"] == cat.TEXTO_AVALIACAO_RUIM_PUBLICA
    # A resposta do Duoke chega até ~18 h depois: a janela de comparação é de 24 h.
    for aut in (boa, ruim):
        assert cat.janela_comparacao(aut, T0, T0 + timedelta(hours=1)) == (
            T0,
            T0 + timedelta(hours=24),
        )
    # A pública passa pelo limite da resposta de avaliação (500), não o do chat.
    longa = [{"tipo": "resposta_publica", "texto": "x" * 600}, {"tipo": "texto", "texto": "Oi!"}]
    _, motivos = cat.renderizar(longa, comprador=None, plataforma="shopee", canal="chat")
    assert any("500" in m for m in motivos)
    assert (boa.estrelas, ruim.estrelas) == ((4, 5), (1, 3))
    assert [cat.estrelas_da_faixa(n) for n in (1, 2, 3, 4, 5, None)] == [
        "shopee_avaliacao_ruim",
        "shopee_avaliacao_ruim",
        "shopee_avaliacao_ruim",
        "shopee_avaliacao_boa",
        "shopee_avaliacao_boa",
        None,
    ]


@pytest.mark.parametrize("tipo", list(DUOKE_AVALIACAO))
def test_assinatura_da_resposta_da_avaliacao(tipo):
    publica, chat = DUOKE_AVALIACAO[tipo]
    assert cat.assinatura_avaliacao(publica) == tipo
    assert cat.assinatura_avaliacao(chat) == tipo
    # Fora das assinaturas de MENSAGEM: o comparador dela é pela avaliação.
    assert cat.assinatura(chat) is None


def test_assinatura_da_avaliacao_nao_pega_pessoa():
    for texto in (
        "Obrigado pela confiança!",  # pessoa, sem o resto do modelo
        "Sentimos muito, vamos resolver",
        "Olá! Obrigado pela avaliação, volte sempre",
        "",
        None,
    ):
        assert cat.assinatura_avaliacao(texto) is None, texto


def test_so_simulam_e_a_semente_das_novas():
    novas = (
        "shopee_nao_pago",
        "shopee_avaliacao_boa",
        "shopee_avaliacao_ruim",
        "tiktok_pedido_recebido",
    )
    for codigo in novas:
        aut = cat.CATALOGO[codigo]
        assert aut.so_simular in cat.SO_SIMULAR, codigo
    assert {a.codigo for a in cat.CATALOGO.values() if a.so_simular} == set(novas)
    sem = cat.regra_semente
    nao_pago = cat.CATALOGO["shopee_nao_pago"]
    assert sem(nao_pago, "Barbosa")["modo"] == cat.MODO_SIMULAR
    assert sem(nao_pago, "atv")["modo"] == cat.MODO_SIMULAR
    assert sem(nao_pago, "inova")["modo"] == cat.MODO_SIMULAR
    assert sem(nao_pago, "kia")["modo"] == cat.MODO_DESLIGADO, "a Kia não tem (0 de 22)"
    assert sem(nao_pago, "aguiar")["modo"] == cat.MODO_DESLIGADO
    # Um por comprador em 24 h (o Duoke: nada abaixo de 24 h, de novo a partir de 27 h).
    assert sem(nao_pago, "barbosa")["condicoes"] == {"um_por_comprador_h": 24}
    assert cat.UM_POR_COMPRADOR_H == 24
    boa = cat.CATALOGO["shopee_avaliacao_boa"]
    assert sem(boa, "kia")["modo"] == cat.MODO_SIMULAR
    assert sem(boa, "aguiar")["modo"] == cat.MODO_DESLIGADO, "a Aguiar responde à mão"
    tt = cat.CATALOGO["tiktok_pedido_recebido"]
    assert [sem(tt, n)["modo"] for n in ("atv", "barbosa", "mini", "injox", "jlas", "eron")] == [
        cat.MODO_SIMULAR,
        cat.MODO_SIMULAR,
        cat.MODO_SIMULAR,
        cat.MODO_DESLIGADO,
        cat.MODO_DESLIGADO,
        cat.MODO_DESLIGADO,
    ]
    # O "pedido recebido" do TikTok compara na conversa (o aviso não traz o nº);
    # o da Shopee, pelo pedido; a avaliação, pela avaliação.
    assert tt.na_conversa and not cat.CATALOGO["shopee_pedido_recebido"].na_conversa
    assert not boa.na_conversa and boa.alvo == cat.ALVO_AVALIACAO
    assert cat.automacao_do_modelo(cat.TIPO_PEDIDO_RECEBIDO, None, "tiktok", "chat") == (
        "tiktok_pedido_recebido"
    )
    assert cat.automacao_do_modelo(cat.TIPO_NAO_PAGO, None, "shopee", "chat") == "shopee_nao_pago"


def test_divergencia_do_carrinho_e_campanha():
    aut = cat.CATALOGO["shopee_nao_pago"]
    assert cat.divergencia_do_motivo("devolucao", aut) == "exclusao_disputa"
    assert "visto_de_hora_em_hora" in cat.DIVERGENCIAS
    assert aut.diferenca_combinada == "visto_de_hora_em_hora"
