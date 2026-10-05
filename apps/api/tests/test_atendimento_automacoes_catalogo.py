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
- o horário (09h–20h) e a validade; a semente (simular nas lojas do Duoke).
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
        )
        assert motivos == [], (aut.codigo, comprador, motivos)
        textos = [p["texto"] for p in partes if p["tipo"] == "texto"]
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
    """A rede para quando a leitura não adota a nossa linha (volta como `externo`)."""
    for aut in _automaticas():
        for loja in ("barbosa", "inova"):
            for nome in ("maria.silva", None):
                partes, _ = cat.renderizar(
                    cat.partes_padrao(aut, loja),
                    comprador=nome,
                    plataforma=aut.plataforma,
                    canal=aut.canal,
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
