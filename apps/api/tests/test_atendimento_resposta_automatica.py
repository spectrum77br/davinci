"""A resposta automática do Duoke não tira a conversa da fila (01/10/2026).

O robô do Duoke responde sozinho pela mesma API da pessoa ("selecione sua
dúvida", "sua mensagem foi recebida"): chega como mensagem DA LOJA. A Caixa
tratava isso como resposta, e a Shopee ATV mostrava 2 conversas esperando
quando eram 7.

05/10/2026: a mesma régua, somada às mensagens que o DaVinci manda sozinho
fora do /atendimento (`e_mensagem_automatica`), tira a mensagem automática
da métrica de tempo de resposta e dos exemplos da IA — no SQL, igual. E,
na correção do mesmo dia: a campanha do Duoke que começa pelo usuário do
comprador ("fulano já segue nossa loja aqui", que deixava a mediana do
TikTok em 2,4 min) e, pelo payload, a figurinha 0007 da campanha e os
cartões que a própria Shopee põe na conversa.

E a fila "Falta responder" passa a usar a mesma régua (`gravar.recalcular` e
`recalcular_conversa`): a campanha "já segue nossa loja" do TikTok, a
figurinha 0007, os cartões `server`/`crm` da Shopee e a senha da devolução
fechavam a vez do comprador (7 conversas do TikTok em 7 dias apareciam
respondidas sem estar). Menos no turno em que o comprador SÓ mandou o
cartão do produto/pedido (`e_cartao_do_comprador`): ali vale a régua de
antes — sem isso, ~375 conversas por semana da Shopee ficariam na fila para
sempre. E a mensagem automática não aposenta a sugestão da IA nem vira a
"resposta real" da comparação IA × equipe.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import Text, literal, select
from sqlalchemy.dialects.postgresql import JSONB

from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    Integration,
    IntegrationPlatform,
)
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento import gravar
from app.services.atendimento.constantes import (
    CAMPANHAS_COM_USUARIO,
    PRIMEIRAS_LETRAS_AUTOMATICA,
    RESPOSTAS_AUTOMATICAS,
    e_automatica_pelo_payload,
    e_cartao_do_comprador,
    e_mensagem_automatica,
    e_resposta_automatica,
)
from app.services.devolucao_mensagem_comprador import texto_para
from app.services.logistica_cliente_mensagens import TEMPLATES_PADRAO

T0 = datetime(2026, 10, 1, 7, 30, tzinfo=UTC)


def _msg(
    autor: str, texto: str, minutos: int, payload: dict | None = None
) -> AtendimentoMensagem:
    return AtendimentoMensagem(
        autor=autor,
        origem="cliente" if autor == "cliente" else "externo",
        tipo="texto",
        texto=texto,
        enviada_em=T0 + timedelta(minutes=minutos),
        status="recebida",
        payload=payload or {},
    )


def test_reconhece_os_modelos_do_duoke():
    assert e_resposta_automatica(
        "Olá, por favor selecione sua dúvida e logo um atendente irá te responder"
    )
    assert e_resposta_automatica("OLA,  a sua mensagem foi RECEBIDA. Há mais mensagens…")
    assert e_resposta_automatica("Descreva sua dúvida que assim que um atendente estiver livre")
    # Campanhas automáticas que no TikTok chegam como mensagem da loja.
    assert e_resposta_automatica("Oi! Recebemos seu pedido e já estamos preparando pra envio.")
    assert e_resposta_automatica("Ficou alguma dúvida sobre o produto? Estou aqui pra te ajudar")
    assert e_resposta_automatica("Oi! Seu produto ainda está no carrinho 🛒 Finalize agora")
    assert e_resposta_automatica("Oi! Tudo bem? 😊 Confirmamos a entrega do seu pedido! Por se")
    # Resposta de pessoa (mesmo as prontas do Duoke) continua contando.
    assert not e_resposta_automatica("A entrega é feita pela Shopee, não temos acesso")
    assert not e_resposta_automatica("bom dia tudo bem ?")
    assert not e_resposta_automatica("Olá! Seu pedido já foi enviado")
    assert not e_resposta_automatica(None)
    assert not e_resposta_automatica("")


def test_automatica_nao_tira_da_fila_e_a_de_pessoa_tira():
    conversa = AtendimentoConversa(plataforma="shopee", canal="chat", externo_id="c1")
    gravar.recalcular(
        conversa,
        [
            _msg("cliente", "a garantia como fica?", 0),
            _msg("loja", "Olá, a sua mensagem foi recebida. Há mais mensagens", 10),
        ],
    )
    assert conversa.aguardando_resposta is True
    # A lista continua mostrando a última mensagem (a automática).
    assert conversa.ultima_autor == "loja"

    gravar.recalcular(conversa, [_msg("loja", "Pode ficar tranquila, a garantia é de 90 dias", 20)])
    assert conversa.aguardando_resposta is False


# ── A régua única: Duoke + o que o DaVinci manda sozinho (05/10/2026) ─────
# A métrica de tempo de resposta e os exemplos da IA olham no SQL
# (`gravar.mensagem_automatica_sql`); quem tem a mensagem na mão, no Python
# (`e_mensagem_automatica`). Os dois lados têm de dizer o mesmo.


def _devolucao(sku: str, pedido: str = "250930ABCD") -> SimpleNamespace:
    return SimpleNamespace(pedido_marketplace=pedido, pedido_bling="12345", sku=sku)


def _senhas() -> list[str]:
    """O texto de `devolucao_mensagem_comprador.texto_para`: mala, Apple e aparelho."""
    return [
        texto_para(_devolucao(sku), loja=loja)
        for sku in ("B-MALA-20", "IPHONE11.64", "SAMSUNG-A10")
        for loja in ("", "Mini Shop", "Vita Ltda. Comércio")
    ]


def _emails_amazon() -> list[str]:
    """Os quatro modelos padrão da logística da Amazon, preenchidos."""
    valores = defaultdict(
        lambda: "x",
        cliente="Maria José da Silva Pereira",
        pedido_amazon="701-1234567-1234567",
        postagem="01/10/2026",
        servico="SEDEX",
        rastreio="AA123456789BR",
        previsao_correios="05/10/2026",
    )
    return [t["corpo"].format_map(valores) for t in TEMPLATES_PADRAO.values()]


AUTOMATICAS = [
    *RESPOSTAS_AUTOMATICAS,
    "Olá, por favor selecione sua dúvida e logo um atendente irá te responder",
    "OLA,  a sua mensagem foi RECEBIDA. Há mais mensagens…",
    "  Oi! Recebemos seu pedido e já estamos preparando pra envio.",
    "Ficou alguma dúvida sobre o produto?\nEstou aqui pra te ajudar",
    "Oi! Tudo bem? 😊 Confirmamos a entrega do seu pedido! Por se",
    "Oi! 👋 Notamos que você deixou alguns itens no carrinho",
    "OLÁ, POR FAVOR SELECIONE SUA DÚVIDA",
    "\n\tOlá, a sua mensagem foi recebida",
    "Descreva   sua dúvida que assim que um atendente",
    *_senhas(),
    *_emails_amazon(),
    # A campanha que começa pelo usuário do comprador (qualquer caractere).
    "fulana.123 já segue nossa loja aqui no TikTok? Seguindo você ganha cupom",
    "_juju.vendas JÁ SEGUE NOSSA LOJA AQUI na Shopee?",
    "9maria  já\u00a0segue nossa  loja aqui no tiktok?",
    " .ponto já segue nossa loja aqui",
    "Ícaro já segue nossa loja aqui",
    "ze ja segue nossa loja aqui",
]
DE_PESSOA = [
    "A entrega é feita pela Shopee, não temos acesso",
    "bom dia tudo bem ?",
    "Olá! Seu pedido já foi enviado",
    "Olá! Aqui é a loja Mini. Pode mandar foto do produto?",
    "Recebemos de volta o aparelho e já fizemos o reembolso.",
    "Olá, Maria! A transportadora registrou a entrega ontem, pode conferir com a portaria?",
    "Oi, a sua mensagem foi lida e já vou verificar",
    "ola por favor selecione sua duvida",  # sem a vírgula: não é o robô
    # Perto da campanha, mas não ela: mais de uma palavra antes, a frase no
    # meio, ou só o começo da frase.
    "Oi! Você já segue nossa loja aqui?",
    "Obrigada fulana, já segue nossa loja aqui",
    "fulana já segue nossa loja",
    "Que bom que você já segue nossa loja aqui na Shopee!",
    "fulana já segue a nossa loja aqui",
    "Obrigado! 😊",
    "",
    None,
]


def test_mensagem_automatica_reconhece_duoke_senha_e_amazon_e_nao_a_pessoa():
    for texto in AUTOMATICAS:
        assert e_mensagem_automatica(texto), texto
    for texto in DE_PESSOA:
        assert not e_mensagem_automatica(texto), texto
    # A régua da fila (só o Duoke, sobre o texto normalizado) está inteira
    # dentro da nova, que casa o texto cru (acento, caixa e espaço à parte)...
    for texto in AUTOMATICAS + DE_PESSOA:
        if e_resposta_automatica(texto):
            assert e_mensagem_automatica(texto), texto
    # ...e a senha e o e-mail da logística não mexem na fila.
    assert not any(e_resposta_automatica(t) for t in _senhas() + _emails_amazon())


async def test_mensagem_automatica_sql_diz_o_mesmo_que_o_python(db):
    textos = [*AUTOMATICAS, *DE_PESSOA]
    consulta = select(
        *(
            gravar.mensagem_automatica_sql(literal(t, type_=Text)).label(f"t{i}")
            for i, t in enumerate(textos)
        )
    )
    linha = (await db.execute(consulta)).one()
    for i, texto in enumerate(textos):
        assert linha[i] is e_mensagem_automatica(texto), texto


def test_campanha_com_usuario_fura_o_prefiltro_da_primeira_letra():
    """O usuário começa por número/"_"/"." — fora das letras do pré-filtro. É
    o caso que o SQL precisa do segundo pré-filtro (o trecho) para acertar."""
    assert CAMPANHAS_COM_USUARIO
    for texto in ("9maria já segue nossa loja aqui", "_juju já segue nossa loja aqui"):
        assert texto.lstrip()[0] not in PRIMEIRAS_LETRAS_AUTOMATICA
        assert e_mensagem_automatica(texto), texto
    # A régua antiga (só o Duoke) não a pegava — a fila usa a nova (abaixo).
    assert not e_resposta_automatica("fulana já segue nossa loja aqui no TikTok?")


def _figurinha(sticker_id, fonte="openapi") -> dict:
    return {
        "source": fonte,
        "message_type": "sticker",
        "content": {"sticker_id": sticker_id, "sticker_package_id": "br_shoppito"},
    }


# O item cru da Shopee (`payload`). O texto é só o rótulo: o que separa a
# figurinha da campanha da figurinha que a pessoa mandou é o payload.
PAYLOADS_AUTOMATICOS = [
    _figurinha("0007"),
    {"source": "server", "message_type": "voucher", "content": {"voucher_id": 1}},
    {"source": "server", "message_type": "logistics_card", "content": {}},
    {"source": "server", "message_type": "track_rr_status_card"},
    {"source": "crm", "message_type": "crm_order_rate", "content": {"title": "x"}},
    {"source": "crm", "message_type": "text", "content": {"text": "x"}},
]
PAYLOADS_DE_PESSOA = [
    None,
    {},
    [],
    "texto",
    _figurinha("0028"),
    _figurinha("0012"),
    _figurinha(7),
    _figurinha("0007", fonte="android"),  # a figurinha 0007 do COMPRADOR
    _figurinha("0007", fonte="mini_webchat"),
    {"source": "openapi", "message_type": "text", "content": {"text": "x", "sticker_id": "0007"}},
    {"source": "openapi", "message_type": "sticker", "content": "0007"},
    {"source": "openapi", "message_type": "order", "content": {"order_sn": "x"}},
    {"source": "openapi", "message_type": "voucher", "content": {"voucher_id": 1}},
    {"source": ["server"], "message_type": "voucher"},
    {"source": "SERVER", "message_type": "voucher"},
    {"message_type": "sticker", "content": {"sticker_id": "0007"}},
]


def test_payload_da_shopee_separa_a_campanha_e_o_cartao():
    for payload in PAYLOADS_AUTOMATICOS:
        assert e_automatica_pelo_payload(payload), payload
        assert e_mensagem_automatica("[Figurinha]", payload), payload
    for payload in PAYLOADS_DE_PESSOA:
        assert not e_automatica_pelo_payload(payload), payload
        assert not e_mensagem_automatica("[Figurinha]", payload), payload
    # Sem payload, a régua é só a do texto (quem não tem a coluna à mão).
    assert not e_mensagem_automatica("[Figurinha]")
    # Texto automático continua automático com qualquer payload.
    assert e_mensagem_automatica("Olá, a sua mensagem foi recebida", _figurinha("0028"))


async def test_payload_sql_diz_o_mesmo_que_o_python(db):
    casos = [
        (texto, payload)
        for payload in (*PAYLOADS_AUTOMATICOS, *PAYLOADS_DE_PESSOA)
        for texto in ("[Figurinha]", "Olá, a sua mensagem foi recebida", None)
    ]
    consulta = select(
        *(
            gravar.mensagem_automatica_sql(
                literal(texto, type_=Text), literal(payload, type_=JSONB)
            ).label(f"t{i}")
            for i, (texto, payload) in enumerate(casos)
        )
    )
    linha = (await db.execute(consulta)).one()
    for i, (texto, payload) in enumerate(casos):
        assert linha[i] is e_mensagem_automatica(texto, payload), (texto, payload)


# ── A fila "Falta responder" com a régua nova (05/10/2026) ────────────────
# `gravar.recalcular` (a mensagem nova, sem banco) e `recalcular_conversa`
# (refaz do banco, no SQL) usam `e_mensagem_automatica(texto, payload)` — a
# mesma régua da métrica. Antes, só o começo do texto do Duoke.


def test_fila_campanha_figurinha_cartao_e_senha_nao_fecham_a_vez():
    """Cada uma, depois da pergunta do comprador, deixa a conversa esperando
    uma pessoa — e a última mensagem da lista continua sendo ela."""
    casos = [
        ("fulana.123 já segue nossa loja aqui no TikTok? Siga e ganhe cupom", {}),
        ("[Figurinha]", _figurinha("0007")),
        ("[Cupom]", {"source": "server", "message_type": "voucher", "content": {}}),
        ("[Mensagem]", {"source": "crm", "message_type": "crm_order_rate"}),
        (_senhas()[0], {}),
        ("Olá, a sua mensagem foi recebida. Há mais mensagens", {}),
    ]
    for texto, payload in casos:
        conversa = AtendimentoConversa(plataforma="tiktok", canal="chat", externo_id="c1")
        gravar.recalcular(
            conversa,
            [_msg("cliente", "o pedido não chegou", 0), _msg("loja", texto, 1, payload)],
        )
        assert conversa.aguardando_resposta is True, (texto, payload)
        assert conversa.situacao == "aberta", (texto, payload)
        assert conversa.ultima_da_loja_em is None, (texto, payload)
        assert conversa.ultima_autor == "loja"  # a lista mostra a automática

        # A pessoa responde: aí sim fecha a vez.
        gravar.recalcular(conversa, [_msg("loja", "Vou verificar com a transportadora", 30)])
        assert conversa.aguardando_resposta is False, (texto, payload)
        assert conversa.situacao == "respondida"


def test_fila_figurinha_de_pessoa_e_resposta_comum_continuam_fechando():
    for texto, payload in (
        ("[Figurinha]", _figurinha("0028")),
        ("[Figurinha]", _figurinha("0007", fonte="android")),
        ("Oi! Você já segue nossa loja aqui?", {}),
        ("Olá! Seu pedido já foi enviado", {}),
    ):
        conversa = AtendimentoConversa(plataforma="shopee", canal="chat", externo_id="c1")
        gravar.recalcular(
            conversa,
            [_msg("cliente", "e aí?", 0), _msg("loja", texto, 1, payload)],
        )
        assert conversa.aguardando_resposta is False, (texto, payload)


def test_fila_recalcular_aceita_mensagem_sem_payload():
    """Quem passa um objeto sem `payload` (ou com None) cai só na régua do texto."""
    conversa = AtendimentoConversa(plataforma="shopee", canal="chat", externo_id="c1")
    sem = _msg("loja", "[Figurinha]", 1)
    sem.payload = None
    gravar.recalcular(conversa, [_msg("cliente", "oi", 0), sem])
    assert conversa.aguardando_resposta is False


async def _conversa_no_banco(db, make_user, plataforma: str = "shopee") -> AtendimentoConversa:
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform(plataforma),
        name="loja fila",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.commit()
    canal = AtendimentoCanal(integration_id=integ.id, plataforma=plataforma, canal="chat")
    db.add(canal)
    await db.commit()
    conversa, criada = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=plataforma,
        canal_nome="chat",
        externo_id="fila-1",
    )
    assert criada
    await db.commit()
    return conversa


async def test_fila_pela_gravacao_e_recalcular_conversa_no_sql(db, make_user):
    """O caminho do sync (`gravar_mensagem` → `recalcular`) e o refazer do
    banco (`recalcular_conversa`, no SQL) dizem o mesmo — inclusive com uma
    fileira de automáticas maior que as 20 que o filtro antigo olhava."""
    conversa = await _conversa_no_banco(db, make_user)
    await gravar.gravar_mensagem(
        db, conversa, externo_id="l-0", autor="loja", texto="Bom dia! Como posso ajudar?",
        enviada_em=T0,
    )
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto="cadê meu pedido?",
        enviada_em=T0 + timedelta(minutes=5),
    )
    for i in range(25):
        payload = (
            _figurinha("0007")
            if i % 2
            else {"source": "crm", "message_type": "crm_order_rate", "content": {}}
        )
        await gravar.gravar_mensagem(
            db, conversa, externo_id=f"a-{i}", autor="loja", texto="[Figurinha]",
            enviada_em=T0 + timedelta(minutes=6 + i), payload=payload,
        )
    await gravar.gravar_mensagem(
        db, conversa, externo_id="a-senha", autor="loja", texto=_senhas()[0],
        enviada_em=T0 + timedelta(minutes=40),
    )
    await db.commit()
    assert conversa.aguardando_resposta is True
    assert gravar._utc(conversa.ultima_da_loja_em) == T0  # a resposta de pessoa de antes

    await gravar.recalcular_conversa(db, conversa)
    assert conversa.aguardando_resposta is True
    assert conversa.situacao == "aberta"
    assert gravar._utc(conversa.ultima_da_loja_em) == T0
    assert conversa.ultima_autor == "loja"

    # A pessoa responde depois: as duas formas fecham a vez.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="l-1", autor="loja", texto="Já foi postado, segue o rastreio",
        enviada_em=T0 + timedelta(minutes=50),
    )
    assert conversa.aguardando_resposta is False
    await gravar.recalcular_conversa(db, conversa)
    assert conversa.aguardando_resposta is False
    assert gravar._utc(conversa.ultima_da_loja_em) == T0 + timedelta(minutes=50)


# ── O turno em que o comprador SÓ mandou cartão (05/10/2026) ──────────────
# A régua de antes: a automática que ela contava (tudo menos o robô e as
# campanhas do Duoke) fecha a vez. Quem decide é `recalcular_conversa`, no
# SQL — a gravação o chama para essa mensagem.


def _cartao_shopee(tipo: str = "item") -> dict:
    return {"source": "android", "message_type": tipo, "content": {"item_id": 123}}


CARTOES = [
    _cartao_shopee("item"),
    _cartao_shopee("variation_card"),
    _cartao_shopee("order"),
    {"type": "PRODUCT_CARD", "content": "{}"},
    {"type": "ORDER_CARD", "content": "{}"},
]
NAO_CARTOES = [
    None,
    {},
    [],
    "item",
    _cartao_shopee("text"),
    _cartao_shopee("sticker"),
    _cartao_shopee("image"),
    _cartao_shopee("faq_liveagent"),
    _cartao_shopee("return_refund_card"),
    _cartao_shopee("ITEM"),
    {"message_type": ["item"]},
    {"type": "product_card"},
    {"type": "TEXT"},
    {"type": "PRODUCT_CARD_X"},
    {"message_type": None, "type": None},
]


def test_cartao_do_comprador_shopee_e_tiktok():
    for payload in CARTOES:
        assert e_cartao_do_comprador(payload), payload
    for payload in NAO_CARTOES:
        assert not e_cartao_do_comprador(payload), payload


async def test_cartao_e_regua_antiga_no_sql_dizem_o_mesmo_que_o_python(db):
    payloads = [*CARTOES, *NAO_CARTOES]
    linha = (
        await db.execute(
            select(
                *(
                    gravar.cartao_do_comprador_sql(literal(p, type_=JSONB)).label(f"p{i}")
                    for i, p in enumerate(payloads)
                )
            )
        )
    ).one()
    for i, payload in enumerate(payloads):
        assert linha[i] is e_cartao_do_comprador(payload), payload

    textos = [*AUTOMATICAS, *DE_PESSOA]
    linha = (
        await db.execute(
            select(
                *(
                    gravar.resposta_automatica_sql(literal(t, type_=Text)).label(f"t{i}")
                    for i, t in enumerate(textos)
                )
            )
        )
    ).one()
    for i, texto in enumerate(textos):
        assert linha[i] is e_resposta_automatica(texto), texto


async def _gravar(db, conversa, externo_id, autor, texto, quando, **kw):
    m, _ = await gravar.gravar_mensagem(
        db, conversa, externo_id=externo_id, autor=autor, texto=texto, enviada_em=quando, **kw
    )
    await db.commit()
    return m


async def test_turno_so_de_cartao_fecha_como_antes_e_com_texto_nao(db, make_user):
    """Shopee: o comprador abriu o chat pelo produto (cartão de item, de
    variação e de pedido) e não escreveu nada. O robô do Duoke não fecha a
    vez (nunca fechou); a figurinha 0007, 26 h depois, fecha — como antes.
    Quando ele escreve, a figurinha seguinte não fecha mais. A gravação e o
    refazer do banco dizem o mesmo; a sugestão da IA fica pendente."""
    conversa = await _conversa_no_banco(db, make_user)
    await _gravar(db, conversa, "l-0", "loja", "Bom dia! Como posso ajudar?", T0)
    for i, tipo in enumerate(("item", "variation_card", "order")):
        ultimo_cartao = await _gravar(
            db, conversa, f"c-{i}", "cliente", "[Variação]" if i == 1 else None,
            T0 + timedelta(minutes=5 + i), tipo="produto", payload=_cartao_shopee(tipo),
        )
    assert conversa.aguardando_resposta is True  # o cartão abre a vez, como antes
    sugestao = AtendimentoRascunho(
        conversa_id=conversa.id, mensagem_gatilho_id=ultimo_cartao.id,
        texto="Olá! Posso ajudar com esse produto?", status="pendente",
    )
    db.add(sugestao)
    await db.commit()

    await _gravar(
        db, conversa, "r-1", "loja", "Olá, a sua mensagem foi recebida. Há mais mensagens",
        T0 + timedelta(minutes=9),
    )
    assert conversa.aguardando_resposta is True
    await gravar.recalcular_conversa(db, conversa)
    assert conversa.aguardando_resposta is True

    figurinha = T0 + timedelta(hours=26)
    await _gravar(
        db, conversa, "a-1", "loja", "[Figurinha]", figurinha, payload=_figurinha("0007")
    )
    assert (conversa.aguardando_resposta, conversa.situacao) == (False, "respondida")
    assert gravar._utc(conversa.ultima_da_loja_em) == figurinha
    assert conversa.ultima_autor == "loja"
    await gravar.recalcular_conversa(db, conversa)
    assert (conversa.aguardando_resposta, conversa.situacao) == (False, "respondida")
    assert gravar._utc(conversa.ultima_da_loja_em) == figurinha
    # Fechou a vez, mas não respondeu ninguém: a sugestão continua valendo.
    await db.refresh(sugestao)
    assert sugestao.status == "pendente"

    # O comprador escreve (e manda o cartão de novo): volta para a fila, e a
    # figurinha seguinte não fecha mais.
    depois = figurinha + timedelta(hours=1)
    await _gravar(db, conversa, "c-9", "cliente", "tem na cor azul?", depois)
    await _gravar(
        db, conversa, "c-10", "cliente", None, depois + timedelta(minutes=1),
        tipo="produto", payload=_cartao_shopee(),
    )
    await _gravar(
        db, conversa, "a-2", "loja", "[Figurinha]", depois + timedelta(hours=26),
        payload=_figurinha("0007"),
    )
    await _gravar(
        db, conversa, "a-3", "loja", "[Cupom]", depois + timedelta(hours=27),
        payload={"source": "server", "message_type": "voucher", "content": {}},
    )
    assert (conversa.aguardando_resposta, conversa.situacao) == (True, "aberta")
    assert gravar._utc(conversa.ultima_da_loja_em) == figurinha
    await gravar.recalcular_conversa(db, conversa)
    assert (conversa.aguardando_resposta, conversa.situacao) == (True, "aberta")
    assert gravar._utc(conversa.ultima_da_loja_em) == figurinha

    # A pessoa responde: fecha, e o cartão seguinte volta à régua de antes.
    resposta = depois + timedelta(hours=28)
    await _gravar(db, conversa, "l-1", "loja", "Temos sim, na azul e na preta!", resposta)
    assert conversa.aguardando_resposta is False
    await _gravar(
        db, conversa, "c-11", "cliente", None, resposta + timedelta(minutes=5),
        tipo="produto", payload=_cartao_shopee(),
    )
    assert conversa.aguardando_resposta is True
    await _gravar(
        db, conversa, "a-4", "loja", "[Mensagem]", resposta + timedelta(minutes=6),
        payload={"source": "crm", "message_type": "crm_order_rate", "content": {}},
    )
    assert conversa.aguardando_resposta is False
    await gravar.recalcular_conversa(db, conversa)
    assert conversa.aguardando_resposta is False
    assert gravar._utc(conversa.ultima_da_loja_em) == resposta + timedelta(minutes=6)


async def test_turno_so_de_cartao_no_tiktok_e_texto_antes_do_cartao(db, make_user):
    """TikTok: o cartão do produto e a campanha "já segue" 40 s depois —
    fecha, como antes. Texto e DEPOIS o cartão: o turno não é só de cartão,
    a campanha não fecha (nas duas formas)."""
    conversa = await _conversa_no_banco(db, make_user, plataforma="tiktok")
    campanha = "fulana.123 já segue nossa loja aqui no TikTok? Siga e ganhe cupom"
    await _gravar(
        db, conversa, "c-1", "cliente", None, T0, tipo="produto",
        payload={"type": "PRODUCT_CARD", "content": "{}"},
    )
    await _gravar(db, conversa, "a-1", "loja", campanha, T0 + timedelta(seconds=40))
    assert conversa.aguardando_resposta is False
    await gravar.recalcular_conversa(db, conversa)
    assert conversa.aguardando_resposta is False

    await _gravar(db, conversa, "c-2", "cliente", "chega até sexta?", T0 + timedelta(hours=2))
    await _gravar(
        db, conversa, "c-3", "cliente", None, T0 + timedelta(hours=2, minutes=1),
        tipo="pedido", payload={"type": "ORDER_CARD", "content": "{}"},
    )
    await _gravar(db, conversa, "a-2", "loja", campanha, T0 + timedelta(hours=2, minutes=2))
    assert conversa.aguardando_resposta is True
    await gravar.recalcular_conversa(db, conversa)
    assert conversa.aguardando_resposta is True
    assert gravar._utc(conversa.ultima_da_loja_em) == T0 + timedelta(seconds=40)


async def test_figurinha_de_comprador_nao_e_cartao(db, make_user):
    """A figurinha (ou o FAQ que ele tocou) é o comprador falando: a 0007
    da campanha não fecha a vez dele."""
    conversa = await _conversa_no_banco(db, make_user)
    await _gravar(
        db, conversa, "c-1", "cliente", "[Figurinha]", T0, tipo="outro",
        payload=_figurinha("0007", fonte="android"),
    )
    await _gravar(
        db, conversa, "a-1", "loja", "[Figurinha]", T0 + timedelta(hours=26),
        payload=_figurinha("0007"),
    )
    assert conversa.aguardando_resposta is True
    await gravar.recalcular_conversa(db, conversa)
    assert conversa.aguardando_resposta is True


# ── A sugestão da IA e a comparação IA × equipe (05/10/2026) ──────────────


async def test_mensagem_automatica_nao_aposenta_a_sugestao_nem_vira_resposta_real(db, make_user):
    """A figurinha 0007 (26 h depois), o cartão `crm`, o robô e a campanha do
    Duoke: a conversa continua em "Falta responder", a sugestão continua
    pendente, a nota do modo observação continua `observou` e a comparação
    IA × equipe não mostra nenhuma delas como a resposta real. A resposta de
    pessoa, por fora, continua aposentando e promovendo."""
    conversa = await _conversa_no_banco(db, make_user)
    pergunta = await _gravar(db, conversa, "c-1", "cliente", "cadê meu pedido?", T0)
    sugestao = AtendimentoRascunho(
        conversa_id=conversa.id, mensagem_gatilho_id=pergunta.id,
        texto="Seu pedido já saiu, segue o rastreio.", status="pendente",
    )
    db.add(sugestao)
    await db.commit()
    nota = AtendimentoAvaliacao(rascunho_id=sugestao.id, acao="observou", nota="ok")
    db.add(nota)
    await db.commit()

    automaticas = [
        ("[Figurinha]", _figurinha("0007")),
        ("[Mensagem]", {"source": "crm", "message_type": "crm_order_rate", "content": {}}),
        ("Olá, a sua mensagem foi recebida. Há mais mensagens", {}),
        ("fulana já segue nossa loja aqui na Shopee?", {}),
    ]
    for i, (texto, payload) in enumerate(automaticas):
        await _gravar(
            db, conversa, f"a-{i}", "loja", texto, T0 + timedelta(hours=26, minutes=i),
            payload=payload,
        )
    await db.refresh(sugestao)
    await db.refresh(nota)
    assert conversa.aguardando_resposta is True
    assert (sugestao.status, nota.acao, nota.texto_final) == ("pendente", "observou", None)
    (s,) = await rota._sugestoes(db, conversa.id)
    assert (s.status, s.resposta_real) == ("pendente", None)

    # Nem chamada direto a promoção aceita a automática.
    figurinha = (
        await db.execute(
            select(AtendimentoMensagem).where(AtendimentoMensagem.externo_id == "a-0")
        )
    ).scalar_one()
    await gravar._promover_observacao(db, sugestao, figurinha)
    assert (nota.acao, nota.texto_final) == ("observou", None)

    real = await _gravar(
        db, conversa, "l-1", "loja", "Já saiu! Segue o rastreio BR123.", T0 + timedelta(hours=27)
    )
    await db.refresh(sugestao)
    await db.refresh(nota)
    assert conversa.aguardando_resposta is False
    assert (sugestao.status, nota.acao, nota.texto_final) == (
        "substituido", "escreveu_do_zero", "Já saiu! Segue o rastreio BR123.",
    )
    (s,) = await rota._sugestoes(db, conversa.id)
    assert s.resposta_real is not None and s.resposta_real.mensagem_id == str(real.id)
