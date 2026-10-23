# ruff: noqa: F811, E501 — fixtures importadas do laço e do e2e; as tabelas de casos reais
"""Rodada "redondo" (23/10/2026), revisão do fechamento: os achados da revisão adversarial.

Cada teste reproduz o achado no caminho AUTO inteiro (ou na função que o decide) e falha
no código de antes desta revisão (as partes 1 e 2 sem os consertos):

A1. G1 calada no "tem sim / está disponível / é vendido aqui": o C17 esgotado (variação do
    anúncio do A18 da Barbosa) dito disponível ia ao verificador; o mesmo anúncio buscado
    por dois termos guardava só o disponível do último; o termo do modelo liberava número.
A2. A busca vazia do termo inteiro virava "não vende" (G1 e a entrada do v10), mas é
    artefato ("fone de ouvido bluetooth sem fio" com o Fone UFB10 na loja); o anúncio cujos
    títulos divergem logo no começo não casava nada (o C3 e o G1 da Vita).
M1. O convite da saudação aceitava o complemento ("responde por aqui rapidinho", "e resolve
    tudo") e valia para quem pediu atendente.
M2. O status de OUTRO pedido (o `ver_pedido`) e a moldura que afirma o estado sozinha ("já
    consta e aparece registrado") davam lastro ao pedido da conversa.
M3. O prazo do fato 25 (garantia Apple de 1 ano) liberava "1 ano com a loja" e pulava a
    conferência da promessa; o fato 8 (malas Charlots) liberava os 90 dias de um celular.
M4. O TODAS_ESGOTADAS com a lista das variações incompleta; "os outros também estão
    esgotados" e "o anúncio inteiro está sem estoque" escapavam da 1ª camada.
M5. O pedido de devolução sem a palavra ("não gostei", "mandar de volta", "retornar o
    produto") era lido como "assunto não pedido".
M6. O fato 11 (condicional: "só estão à venda quando há anúncio próprio") provava o "não
    vendemos peças avulsas".
M7. "Hoje era o dia da entrega e não recebi" com a resposta só do status saía sozinha (o
    fato 29 manda encaminhar para abrir o chamado).
L1. O nome cortado no meio da palavra («Uranyx A18 Pro Max 1»), a variação com o pedaço do
    título velho (o "64" do "64 ou 128 Gb" dava o A17 preto de 64 GB como disponível) e o
    "da_conversa" do anúncio que o `ver_anuncio` acabou de ler.
L2. "Vocês entregam COM nota fiscal aqui?" deixava de ser pergunta de entrega.

Nenhuma chamada a LLM, à loja ou à produção. Os títulos dos anúncios são os do catálogo
de produção (sem dado de cliente).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.atendimento import constantes, validador
from app.services.atendimento.agente import acao_pessoa, catalogo, checagens, ferramentas
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import verificador as v
from scripts.agente_eval import calibrar_verificador as calibrar
from tests.test_atendimento_agente_ferramentas import _base as _base_db
from tests.test_atendimento_agente_ferramentas import _chamar, _ctx
from tests.test_atendimento_agente_redondo import (
    FATO_23,
    FATO_24,
    GARANTIA_URANYX,
    RASCUNHO_3EE1,
    _auto,
    _bloco_1,
    _cli,
    _menu,
    _proposta,
)
from tests.test_atendimento_agente_redondo2 import (
    FICHA_A17,
    ITEM_A17,
    ITEM_A18,
    _b,
    _loja_com_catalogo,
)

VAI = "verificador"
G1 = {checagens.OUTRO_PRODUTO_SEM_BUSCA, checagens.NAO_TEMOS_SEM_FONTE, checagens.NAO_TEMOS_MODELO}

# O anúncio do A18 da Barbosa (58266122162): os vínculos A18 (com estoque) e os C17 (zerados),
# com os títulos que cada vínculo guardou (títulos de produção, 23/10/2026).
ITEM_A18_BARBOSA = "58266122162"
VINCULOS_A18 = {
    ITEM_A18_BARBOSA: [
        catalogo.Vinculo(ITEM_A18_BARBOSA, "1", "Uranyx A18 Pro Max 16.128 Gb NFC Memoria S5 Premium Edition - A18 - Branco NFC", 773, True),
        catalogo.Vinculo(ITEM_A18_BARBOSA, "2", "Uranyx A18 Pro Max 16.128 Gb NFC Memoria S5 Premium Edition - A18 - Laranja NFC", 773, True),
        catalogo.Vinculo(ITEM_A18_BARBOSA, "3", "Uranyx A18 Pro Max 128 Gb Memoria 16 Gb Ram S5 Edition NFC - C17 - Laranja", 0, True),
        catalogo.Vinculo(ITEM_A18_BARBOSA, "4", "Uranyx A18 Pro Max 128 Gb Memoria 16 Gb Ram S5 Edition NFC - C17 - Azul", 0, True),
        catalogo.Vinculo(ITEM_A18_BARBOSA, "5", "Uranyx A18 Pro Max 128 Gb Memoria 16 Gb Ram S5 Edition NFC - C17 - Prata", 0, True),
    ]
}  # fmt: skip
# O A17 da Barbosa (22499563693): os vínculos com o título VELHO ("64 ou 128 Gb Memoria…")
# e os do título novo.
ITEM_A17_BARBOSA = "22499563693"
VINCULOS_A17 = {
    ITEM_A17_BARBOSA: [
        catalogo.Vinculo(ITEM_A17_BARBOSA, "1", "Uranyx A17 Pro Max 64 ou 128 Gb Memoria 12 Gb Ram - A17 - Preto,12 GB / 128 GB", 25, True),
        catalogo.Vinculo(ITEM_A17_BARBOSA, "2", "Uranyx A17 Pro Max 64 ou 128 Gb Memoria 12 Gb Ram - A17 - Preto,12 GB / 64 GB", 0, True),
        catalogo.Vinculo(ITEM_A17_BARBOSA, "3", "Uranyx A17 Pro Max 64 ou 128 Gb Memoria 12 Gb Ram - A17 - Laranja,12 GB / 64 GB", 15, True),
        catalogo.Vinculo(ITEM_A17_BARBOSA, "4", "Uranyx A17 Pro Max 64 ou 128 Gb Memoria 12 Gb Ram - A17 - Prata,12 GB / 64 GB", 0, True),
        catalogo.Vinculo(ITEM_A17_BARBOSA, "5", "Uranyx A17 Pro Max 16.128 - 12.128 GB - 12.64 Memoria NFC - Laranja,16 GB / 128 GB Premium NFC", 1308, True),
        catalogo.Vinculo(ITEM_A17_BARBOSA, "6", "Uranyx A17 Pro Max 16.128 - 12.128 GB - 12.64 Memoria NFC - Prata,16 GB / 128 GB Premium NFC", 1345, True),
    ]
}  # fmt: skip
# O Fone da Barbosa e da Vita: "fone sem fio" não casa o termo inteiro.
ITEM_FONE = "58203678720"
VINCULOS_FONE = {
    ITEM_FONE: [
        catalogo.Vinculo(
            ITEM_FONE,
            "1",
            "Uranyx Fone De Ouvido UFB10 Bluetooth Display Colorido - Preto",
            1102,
            True,
        )
    ]
}
# O anúncio de vários modelos da Vita (58250391785): os títulos divergem logo no começo.
ITEM_SERIES = "58250391785"
VINCULOS_SERIES = {
    ITEM_SERIES: [
        catalogo.Vinculo(ITEM_SERIES, "1", "Smartphones Celulares Uranyx Séries com 64,128 ou 256 GB Memoria Varios Modelos - C3 - Azul,128 GB", 0, True),
        catalogo.Vinculo(ITEM_SERIES, "2", "Uranyx Oukitel C3 16.128 GB - C3 - Dourado + Fone,16 GB / 128 GB", 0, True),
        catalogo.Vinculo(ITEM_SERIES, "3", "Smartphones Celulares Uranyx Séries com 64,128 ou 256 GB Memoria Varios Modelos - C3 - Preto,128 GB", 135, True),
        catalogo.Vinculo(ITEM_SERIES, "4", "Smartphones Celulares Uranyx Séries com 64,128 ou 256 GB Memoria Varios Modelos - P1 - Preto,256 GB", 11, True),
    ]
}  # fmt: skip


def _busca_real(termo: str, vinculos: dict) -> dict:
    """O que a `buscar_anuncios_da_loja` registra (`ctx.registrar_busca`) com estes vínculos."""
    lido = catalogo.ler_termo(termo)
    achados = catalogo.buscar_nos_vinculos(lido, vinculos)
    return {
        "termo": lido.texto,
        "acessorios": sorted(lido.acessorios),
        "modelo": lido.modelo,
        "anuncios": [{**a.para_o_modelo(), "titulo_plano": a.titulo_plano} for a in achados],
        "sem_no_titulo": [] if achados else catalogo.sem_no_titulo(lido.acessorios, vinculos),
    }


def _g1(r) -> set[str]:
    return set(r.codigos) & G1


# ═══════════════ A1. o "tem sim / está disponível" do produto de fora ═══════════════


@pytest.mark.parametrize(
    ("texto", "evidencias"),
    [
        ("Tem sim! O C17 também é vendido aqui, é só procurar por ele no app.", []),
        ("Tem sim, o Uranyx C17 está disponível para compra aqui no app, é só buscar pelo nome.", []),
        ("Tem sim, o Uranyx C17 está disponível para compra aqui no app, é só buscar pelo nome.", [f"anuncio:{ITEM_A18_BARBOSA}"]),
        ("O C17 pode comprar aqui mesmo, é só buscar pelo nome no app.", [f"anuncio:{ITEM_A18_BARBOSA}"]),
        ("Tem o C17 sim, é só procurar por ele.", [f"anuncio:{ITEM_A18_BARBOSA}"]),
        ("Tem o C17, sim.", [f"anuncio:{ITEM_A18_BARBOSA}"]),
        ("O C17 tem aqui na loja também.", [f"anuncio:{ITEM_A18_BARBOSA}"]),
    ],
)  # fmt: skip
async def test_a1_o_c17_esgotado_dito_disponivel_segura(texto, evidencias):
    busca = _busca_real("C17", VINCULOS_A18)
    assert [a["disponivel"] for a in busca["anuncios"]] == [False]
    r = await _auto(
        _proposta(texto, "estoque_variacao", evidencias, 0.9),
        [_menu(), *_cli("vocês tem o C17?")],
        buscas=[busca],
    )
    assert checagens.OUTRO_PRODUTO_SEM_BUSCA in r.codigos, r.codigos
    assert r.decisao != VAI


async def test_a1_o_esgotado_dito_esgotado_e_o_disponivel_citado_passam():
    busca_c17 = _busca_real("C17", VINCULOS_A18)
    r = await _auto(
        _proposta(
            "O C17 está esgotado no momento, sem previsão de reposição.",
            "estoque_variacao",
            [f"anuncio:{ITEM_A18_BARBOSA}"],
            0.9,
        ),
        [_menu(), *_cli("vocês tem o C17?")],
        buscas=[busca_c17],
    )
    assert not _g1(r), r.codigos
    # O mesmo anúncio buscado por "A18" (disponível) e por "C17" (esgotado): cada termo com o
    # SEU disponível — o "C17 disponível" segura; o "A18 em outro anúncio", não.
    buscas = [_busca_real("A18", VINCULOS_A18), busca_c17]
    assert [b["anuncios"][0]["disponivel"] for b in buscas] == [True, False]
    texto_a18 = "Temos sim! O Uranyx A18 Pro Max está à venda em outro anúncio da nossa loja."
    texto_c17 = "Temos sim! O Uranyx C17 está à venda e disponível em outro anúncio da nossa loja."
    for texto, segura in ((texto_a18, False), (texto_c17, True)):
        for ordem in (buscas, buscas[::-1]):
            r = await _auto(
                _proposta(texto, "estoque_variacao", [f"anuncio:{ITEM_A18_BARBOSA}"], 0.9),
                [_menu(), *_cli("vocês tem o A18 e o C17?")],
                fichas=[FICHA_A17],
                buscas=ordem,
            )
            assert (checagens.OUTRO_PRODUTO_SEM_BUSCA in r.codigos) is segura, (texto, r.codigos)
    # A entrada do verificador: cada termo com o disponível DELE.
    ctx = agente_ctx.ContextoAgente.da_conversa(
        SimpleNamespace(id=1, plataforma="shopee", canal="chat"), None, modo=constantes.MODO_AUTO
    )
    for b in buscas:
        ctx.registrar_busca(b)
    linhas = v.linhas_da_busca(v.busca_do_contexto(ctx), ())
    assert linhas[1].startswith('- termo "A18": ') and linhas[1].endswith("disponivel=true")
    assert linhas[2].startswith('- termo "C17": ') and "disponivel=false" in linhas[2]


@pytest.mark.parametrize(
    "texto",
    [
        # A característica do produto da conversa, com o número solto e a marca ("tem").
        "Olá! Obrigado pelo aviso! Pelo anúncio, ele tem 16 GB de RAM, sendo 8 GB físicos e 8 GB virtuais.",
        "Olá! Os aparelhos Uranyx têm 1 ano de garantia a partir da entrega: os 90 primeiros dias com a loja e os 9 meses seguintes direto com a Uranyx.",
        "Olá! Ele tem 5G e NFC.",
        "Seu pedido tem nota fiscal, que vai junto com o pedido.",
        "O C17 não tem NFC.",
    ],
)
async def test_a1_a_afirmacao_de_ter_sem_produto_de_fora_nao_e_g1(texto):
    ficha = {"item_id": "1", "titulo": "Celular A18 Pro Max 16GB 512GB Tela 6.9 HD+ Dual Chip Android 14", "de_onde": "anúncio da conversa"}  # fmt: skip
    r = await _auto(
        _proposta(texto, "espec_celular", [], 0.9), [_menu(), *_cli("é isso?")], fichas=[ficha]
    )
    assert not _g1(r), (texto, r.codigos)


def test_a1_a_conjuncao_e_o_numero_nao_viram_modelo():
    # "e 8" não é o modelo "E8" (a conjunção); o "A 18" do cliente continua o A18.
    assert "e8" not in catalogo.palavras("8 GB físicos e 8 GB virtuais")
    assert "a18" in catalogo.palavras("capa pro Uranyx A 18 Pró Max")
    assert checagens._modelos_e_marcas("ele tem 5G e 108MP")[0] == set()


async def test_a1_o_termo_do_modelo_nao_libera_numero(db: AsyncSession, make_user):
    conversa, _integ = await _loja_com_catalogo(db, make_user)
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    dados = await _chamar(
        ctx, base, conversa, "buscar_anuncios_da_loja", termo="Doogee Fire 7 Ultra"
    )
    assert dados["anuncios"] == []
    # O "7" do termo é do próprio modelo: nunca vira número "do sistema".
    assert not any("7" in t for t in ctx.numeros_vistos), ctx.numeros_vistos
    # O número do anúncio achado continua liberado ("Cesto 4 Litros").
    await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="cesto de vidro 4 litros")
    assert any("Cesto 4 Litros" in t for t in ctx.numeros_vistos)


# ═══════════════ A2. a busca vazia só prova com o acessório fora de todo título ═══════════════


def test_a2_sem_no_titulo():
    assert catalogo.sem_no_titulo(["fone"], VINCULOS_FONE) == []
    assert catalogo.sem_no_titulo(["fone", "capa"], VINCULOS_FONE) == ["capa"]
    # O "Case …" que é celular conta (a prova de ausência não tem a guarda do celular).
    case = {
        "9": [
            catalogo.Vinculo("9", "1", "Case Uranyx A17 Pro Max 12.128 GB Celular - Preto", 5, True)
        ]
    }
    assert catalogo.sem_no_titulo(["capa"], case) == []
    # O genérico (peça, acessório) nunca se prova pela busca.
    assert catalogo.sem_no_titulo(["peca", "acessorio"], VINCULOS_FONE) == []
    # Com o teto de vínculos lido, nada se prova.
    muitos = {
        str(i): [catalogo.Vinculo(str(i), "1", "Uranyx X - Preto", 1, True)]
        for i in range(catalogo.MAX_VINCULOS)
    }
    assert catalogo.sem_no_titulo(["capa"], muitos) == []
    # O termo inteiro que não casa ("fone de ouvido bluetooth sem fio") não prova nada.
    busca = _busca_real("fone de ouvido bluetooth sem fio", VINCULOS_FONE)
    assert busca["anuncios"] == [] and busca["sem_no_titulo"] == []


@pytest.mark.parametrize(
    ("sem_no_titulo", "segura"),
    [([], True), (["fone"], False)],
)
async def test_a2_nao_vendemos_com_a_busca_vazia(sem_no_titulo, segura):
    busca = {**_b("fone de ouvido bluetooth sem fio"), "sem_no_titulo": sem_no_titulo}
    r = await _auto(
        _proposta(
            "No momento não vendemos fone de ouvido avulso nesta loja.",
            "acessorios_avulsos",
            [],
            0.9,
        ),
        [_menu(), *_cli("Vocês vendem fone de ouvido bluetooth sem fio?")],
        fichas=[FICHA_A17],
        buscas=[busca],
    )
    assert (checagens.NAO_TEMOS_SEM_FONTE in r.codigos) is segura, r.codigos
    linhas = v.linhas_da_busca(v.busca_do_contexto(r.ctx), ())
    if segura:
        assert linhas[1].endswith(
            "nenhum anúncio casou com o termo (isso não prova que a loja não vende)"
        )
        assert r.decisao != VAI
    else:
        # Calibração v10 (revisão L1–L3): a linha diz o que prova.
        assert 'nenhum anúncio desta loja tem "fone" no título — a loja NÃO vende' in linhas[1]


def test_a2_o_anuncio_com_os_titulos_divergentes_no_comeco_casa():
    (series,) = catalogo.buscar_nos_vinculos(catalogo.ler_termo("C3"), VINCULOS_SERIES)
    assert series.item_id == ITEM_SERIES and series.disponivel is True
    assert set(series.variacoes_casadas) == {
        "C3 - Azul,128 GB",
        "C3 - Dourado + Fone,16 GB / 128 GB",
        "C3 - Preto,128 GB",
    }
    # O "fone" da variação ("Dourado + Fone") não faz do anúncio de celulares um fone.
    assert catalogo.buscar_nos_vinculos(catalogo.ler_termo("fone"), VINCULOS_SERIES) == []


async def test_a2_a_ferramenta_diz_que_a_busca_vazia_nao_prova(db: AsyncSession, make_user):
    conversa, _integ = await _loja_com_catalogo(db, make_user)
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    vazio = await _chamar(
        ctx, base, conversa, "buscar_anuncios_da_loja", termo="cesto de vidro 8 litros"
    )
    assert vazio["anuncios"] == [] and "NÃO prova" in vazio["nenhum"]
    assert ctx.buscas_catalogo[-1]["sem_no_titulo"] == []
    capa = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="capa A18")
    assert "nenhum anúncio desta loja tem 'capa' no título" in capa["nenhum"]


# ═══════════════ M1. o convite da saudação só como fecho, só na saudação ═══════════════


@pytest.mark.parametrize(
    "sufixo",
    [
        "rapidinho",
        "logo",
        "já já",
        "assim que possível",
        "o quanto antes",
        "com prioridade",
        "e resolve tudo",
    ],
)
async def test_m1_o_convite_com_complemento_continua_promessa(sufixo):
    texto = f"Olá! Mande a sua dúvida que a nossa equipe responde por aqui {sufixo}."
    r = await _auto(_proposta(texto, "saudacao_ou_vaga", [], 0.9), [_menu(), *_cli("Oi")])
    assert checagens.PROMESSA_DE_EQUIPE in r.codigos, r.codigos
    assert r.decisao != VAI


@pytest.mark.parametrize("fala", ["6", "quero falar com um atendente humano"])
async def test_m1_a_quem_pediu_atendente_o_convite_e_promessa(fala):
    texto = "Claro! Conte qual é a sua dúvida que a nossa equipe responde por aqui mesmo."
    r = await _auto(_proposta(texto, "pede_atendente", [], 0.9), [_menu(), *_cli(fala)])
    assert checagens.PROMESSA_DE_EQUIPE in r.codigos, r.codigos


async def test_m1_o_convite_exato_da_saudacao_continua_saindo():
    texto = "Olá! Como podemos ajudar? Se for sobre o seu pedido, conte qual é a sua dúvida que a nossa equipe responde por aqui."
    r = await _auto(
        _proposta(texto, "saudacao_ou_vaga", [], 0.9), [_menu(), *_cli("Oi", "Boaaa w")]
    )
    assert checagens.PROMESSA_DE_EQUIPE not in r.codigos, r.codigos


# ═══════════════ M2. o status é do pedido da conversa ═══════════════

STATUS_SEPARACAO = (
    "O pedido está em separação no centro logístico, novas atualizações em até 1 dia."
)
OUTRO_PEDIDO = {
    "pedido": {
        "numero": "261015BBBB0001",
        "situacao": "Em andamento",
        "postado": "sim",
        "enviado": True,
        "entregue": False,
        "ultimo_status": STATUS_SEPARACAO,
        "chamados": [],
        "devolucoes": [],
    }
}
CTX_COLETA = {
    "pedido": {"numero": "9", "numeroloja": "261022AAAA0009", "situacao": "Em andamento"},
    "logistica": {"status": "Aguardando coleta"},
    "chamados": [],
    "devolucoes": [],
}


@pytest.mark.parametrize(
    "texto",
    [
        "O seu pedido {numero_pedido} está em separação no centro logístico.",
        "Seu pedido já está no centro logístico, em separação.",
    ],
)
async def test_m2_o_status_de_outro_pedido_nao_e_lastro(texto):
    r = await _auto(
        _proposta(texto, "rastreio", ["pedido"], 0.9),
        [_menu(), *_cli("meu pedido está sem atualização")],
        ctx_pedido=CTX_COLETA,
        bloco=_bloco_1("Aguardando coleta pela transportadora"),
        postado=acao_pessoa.POSTADO_SIM,
        valores={"numero_pedido": "261022AAAA0009"},
        textos_extra=[json.dumps(OUTRO_PEDIDO, ensure_ascii=False)],
    )
    assert checagens.PEDIDO_SEM_LASTRO in r.codigos, r.codigos
    assert r.decisao != VAI


def test_m2_o_outro_pedido_citado_pelo_numero_e_o_unico_lido_tem_o_status_dele():
    ctx = agente_ctx.ContextoAgente.da_conversa(
        SimpleNamespace(id=1, plataforma="shopee", canal="chat"), None, modo=constantes.MODO_AUTO
    )
    ctx.permitir(textos=[json.dumps(OUTRO_PEDIDO, ensure_ascii=False)])
    bloco_conversa = _bloco_1("Aguardando coleta pela transportadora")
    citado = "O pedido 261015BBBB0001 está em separação no centro logístico."
    assert (
        checagens.conferir_andamento_sem_lastro(citado, bloco_conversa, ctx, assunto="rastreio")
        == []
    )
    # Sem o pedido da conversa, o único pedido lido é o do assunto.
    so_ele = "Ele está em separação no centro logístico."
    assert checagens.conferir_andamento_sem_lastro(so_ele, None, ctx, assunto="rastreio") == []
    pend = checagens.conferir_andamento_sem_lastro(so_ele, bloco_conversa, ctx, assunto="rastreio")
    assert [p.codigo for p in pend] == [checagens.PEDIDO_SEM_LASTRO]


@pytest.mark.parametrize(
    ("texto", "tem_lastro"),
    [
        ("O envio do seu pedido já consta e aparece registrado.", False),
        ("Seu pedido já aparece registrado.", False),
        # A moldura sem verbo de estado continua sem afirmar nada.
        ("O último status do seu pedido é que ele está aguardando envio.", True),
    ],
)
def test_m2_a_moldura_que_afirma_o_estado(texto, tem_lastro):
    bloco = {**_bloco_1("Aguardando envio"), "postado": acao_pessoa.POSTADO_NAO, "enviado": False}
    pend = checagens.conferir_andamento_sem_lastro(texto, bloco, None, assunto="rastreio")
    assert (pend == []) is tem_lastro, (texto, pend)


# ═══════════════ M3. o prazo de fato é mais estrito que o da regra ═══════════════

FATO_25 = "Os produtos Apple da Kia são novos e lacrados, com garantia Apple de 1 ano a partir da ativação."
FATO_8 = "As malas Charlots têm garantia de 90 dias contra defeitos de fabricação."
REGRA_LEGAL = [
    {"pedido": None, "status": None, "regra": dict(ferramentas.REGRA_LEGAL), "periodo": None}
]


MOTIVO_GARANTIA = validador._MOTIVO_GARANTIA


def _validar(texto: str, prazos) -> list[str]:
    return validador.validar(
        texto,
        plataforma="shopee",
        canal="chat",
        origem=constantes.ORIGEM_IA,
        prazos_de_garantia=prazos,
    )


@pytest.mark.parametrize(
    ("texto", "fatos", "barra"),
    [
        ("Sim! O Apple Watch tem garantia de 1 ano com a loja, contada a partir da compra.", [FATO_25], True),
        ("Sim, a garantia é de 1 ano, direto com a nossa loja: é só chamar por aqui.", [FATO_25], True),
        ("Sim! O celular tem garantia de 1 ano.", [FATO_25], True),
        ("Sim! O aparelho tem garantia de 90 dias contra defeitos de fabricação.", [FATO_8], True),
        ("Sim! O Apple Watch é novo e lacrado, com garantia Apple de 1 ano a partir da ativação.", [FATO_25], False),
        ("A mala Charlots tem garantia de 90 dias contra defeitos de fabricação.", [FATO_8], False),
    ],
)  # fmt: skip
def test_m3_o_prazo_geral_do_fato(texto, fatos, barra):
    erros = _validar(texto, validador.prazos_dos_fatos(fatos))
    assert (MOTIVO_GARANTIA in erros) is barra, (texto, erros)


def test_m3_o_prazo_do_fato_nao_pula_a_promessa():
    texto = "Sim! O Apple Watch tem garantia de 1 ano com a loja, contada a partir da compra."
    prazos = validador.prazos_dos_fatos([FATO_25])
    assert validador.conferir_garantia(texto, REGRA_LEGAL, prazos_de_garantia=prazos)
    # A regra da linha (o ver_garantia) continua livrando a frase dela, e o mesmo número pela
    # regra e pelo fato sai uma vez só (o corte repetido comia o texto ao lado).
    regra = validador.prazos_da_regra(GARANTIA_URANYX)
    assert (
        validador.conferir_garantia(
            RASCUNHO_3EE1,
            GARANTIA_URANYX,
            prazos_de_garantia=regra | validador.prazos_dos_fatos([FATO_23, FATO_24]),
        )
        == []
    )


async def test_m3_no_caminho_auto_o_fato_25_com_a_loja_nao_sai():
    ficha = {
        "item_id": "23999000001",
        "titulo": "Apple Watch Series 11 GPS 46mm Caixa Alumínio",
        "de_onde": "anúncio da conversa",
    }
    r = await _auto(
        _proposta(
            "Sim! O Apple Watch tem garantia de 1 ano com a loja, contada a partir da compra.",
            "garantia",
            ["fato:25", f"ficha:{ficha['item_id']}"],
            0.9,
        ),
        [_menu(), *_cli("Tem garantia?")],
        fichas=[ficha],
        fatos_lidos={"fato:25": FATO_25},
        garantias=REGRA_LEGAL,
    )
    assert r.decisao != VAI, r.motivos
    assert any(MOTIVO_GARANTIA in m for m in r.motivos), r.motivos


# ═══════════════ M4. "os outros também esgotados" só com a lista inteira ═══════════════


def _cesto(completas: bool, omitidas: int = 0, disponiveis=(False, False)) -> dict:
    nomes = ("Cesto 2,5 Litros", "Cesto 4 Litros")
    return {
        "item_id": "22995184839",
        "titulo": "Uranyx Cesto De Vidro Para Air Fryers",
        "de_onde": "anúncio da conversa",
        "variacoes": [{"nome": n, "disponivel": d, "confiavel": True, "fonte": "loja"} for n, d in zip(nomes, disponiveis, strict=True)],
        "variacoes_completas": completas,
        **({"variacoes_omitidas": omitidas} if omitidas else {}),
    }  # fmt: skip


def test_m4_todas_esgotadas_so_com_a_lista_inteira():
    assert v.todas_esgotadas([json.dumps([_cesto(True)])])
    assert not v.todas_esgotadas([json.dumps([_cesto(False)])])
    assert not v.todas_esgotadas([json.dumps([_cesto(True, omitidas=1)])])
    # A ficha do ver_anuncio ("variacoes": {"variacoes", "completas", "omitidas"}).
    lida = {
        "item_id": "1",
        "variacoes": {"variacoes": _cesto(True)["variacoes"], "completas": True, "omitidas": 0},
    }
    assert v.todas_esgotadas([json.dumps(lida)])
    assert not v.todas_esgotadas(
        [json.dumps({**lida, "variacoes": {**lida["variacoes"], "completas": False}})]
    )


@pytest.mark.parametrize(
    "frase",
    [
        "Os outros também estão esgotados no momento.",
        "O anúncio inteiro está sem estoque agora.",
        "Tudo está esgotado no momento.",
    ],
)
@pytest.mark.parametrize(
    ("ficha", "esperado"),
    [
        (_cesto(False), checagens.VARIACAO_SEM_DADO),
        (_cesto(True, disponiveis=(False, True)), checagens.VARIACAO_CONTRADIZ),
        (_cesto(True), None),
    ],
)
async def test_m4_os_outros_esgotados_na_1a_camada(frase, ficha, esperado):
    texto = "Olá! No momento o Cesto 2,5 Litros está esgotado no anúncio. " + frase
    r = await _auto(
        _proposta(texto, "estoque_variacao", ["variacoes:22995184839", "ficha:22995184839"], 0.9),
        [_menu(), *_cli("Quero comprar o cesto de 2,5, tem previsão?")],
        fichas=[ficha],
        anuncio_titulo=ficha["titulo"],
    )
    codigos = {checagens.VARIACAO_SEM_DADO, checagens.VARIACAO_CONTRADIZ} & set(r.codigos)
    assert codigos == ({esperado} if esperado else set()), (frase, r.codigos)


# ═══════════════ M5. o pedido de devolução sem a palavra ═══════════════

TEXTO_DEVOLUCAO = "Você pode pedir a devolução em Minhas Compras > o pedido > Devolução/Reembolso; a Shopee analisa e devolve pela mesma forma de pagamento."
CTX_ENTREGUE = {
    "pedido": {"numero": "9", "numeroloja": "261022AAAA0009", "situacao": "Em andamento"},
    "logistica": {"status": "Entregue"},
    "chamados": [],
    "devolucoes": [],
}


@pytest.mark.parametrize(
    "fala",
    [
        "Recebi e não gostei, como faço?",
        "não serviu, como mando de volta?",
        "quero mandar de volta",
        "Não ficou bom em mim, o que faço?",
        "posso mandar de volta?",
        "Como faço pra retornar o produto?",
        "queria o A18 em vez do A17 que comprei",
    ],
)
def test_m5_o_pedido_de_devolucao_sem_a_palavra_nao_e_assunto_nao_pedido(fala):
    assert (
        checagens.conferir_assunto_nao_pedido(
            TEXTO_DEVOLUCAO, acao="responder", textos_cliente=[fala]
        )
        == []
    )


async def test_m5_no_caminho_auto_a_devolucao_de_rotina_vai_ao_verificador():
    r = await _auto(
        _proposta(TEXTO_DEVOLUCAO, "troca_devolucao", ["pedido"], 0.9),
        [_menu(), *_cli("Recebi e não gostei, como faço?")],
        ctx_pedido=CTX_ENTREGUE,
        bloco={**_bloco_1("Pedido entregue"), "entregue": True},
        postado=acao_pessoa.POSTADO_SIM,
        valores={"numero_pedido": "261022AAAA0009"},
    )
    assert checagens.ASSUNTO_NAO_PEDIDO not in r.codigos, r.codigos
    assert r.decisao == VAI, r.motivos


@pytest.mark.parametrize(
    "fala", ["Quando o meu pedido chegará?", "gostei muito, obrigada", "o que vem na caixa?"]
)
def test_m5_quem_nao_pediu_continua_segurando(fala):
    pend = checagens.conferir_assunto_nao_pedido(
        TEXTO_DEVOLUCAO, acao="responder", textos_cliente=[fala]
    )
    assert [p.codigo for p in pend] == [checagens.ASSUNTO_NAO_PEDIDO], fala


# ═══════════════ M6. só o fato que NEGA a venda prova o "não temos" ═══════════════

FATO_11 = "Peças e acessórios avulsos só estão à venda quando há um anúncio próprio deles na nossa loja; quando não há, não indicamos outras lojas nem a busca da plataforma."
FATO_26 = "No momento não trabalhamos com capinha nem película avulsas nesta loja."
FICHA_AIR = {
    "item_id": "58212957869",
    "titulo": "AirFryer Fritadeira Digital 4 Litros Tigela de Vidro Tela Touch 110-220v",
    "de_onde": "anúncio da conversa",
}


@pytest.mark.parametrize(
    "texto",
    [
        "No momento não vendemos peças avulsas nesta loja.",
        "Não trabalhamos com peças avulsas, só com o aparelho completo.",
        "Infelizmente não vendemos acessórios avulsos.",
    ],
)
async def test_m6_o_fato_11_nao_prova_o_nao_temos(texto):
    r = await _auto(
        _proposta(texto, "acessorios_avulsos", ["fato:11"], 0.9),
        [
            _menu(),
            *_cli(
                "eu queria saber o valor somente do recipiente de vidro, vocês vendem a peça separada?"
            ),
        ],
        fichas=[FICHA_AIR],
        fatos_lidos={"fato:11": FATO_11},
        anuncio_titulo=FICHA_AIR["titulo"],
    )
    assert checagens.NAO_TEMOS_SEM_FONTE in r.codigos, r.codigos
    assert r.decisao != VAI


def test_m6_os_fatos_que_negam():
    assert checagens._fatos_que_negam([FATO_26]) == {"capa", "pelicula"}
    assert checagens._fatos_que_negam([FATO_11]) == set()


# ═══════════════ M7. o atraso dito pelo cliente sem o chamado ═══════════════

STATUS_CD = "O pedido chegou ao centro de distribuição e segue para a entrega."
TEXTO_STATUS = "Entendemos! O último status do seu pedido é que ele chegou ao centro de distribuição e segue para a entrega. Você acompanha cada movimentação em Minhas Compras > o pedido > Informações do envio."
CTX_TRANSITO = {
    "pedido": {"numero": "9", "numeroloja": "261011AAAA0003", "situacao": "Em andamento"},
    "logistica": {"status": "Em trânsito"},
    "chamados": [],
    "devolucoes": [],
}


async def _atraso(fala: str, *, assunto: str = "entrega_nao_recebida", bloco=None, acoes=None):
    return await _auto(
        _proposta(TEXTO_STATUS, assunto, ["pedido"], 0.9),
        [_menu(), *_cli(fala)],
        ctx_pedido=CTX_TRANSITO,
        bloco=bloco or _bloco_1(STATUS_CD),
        postado=acao_pessoa.POSTADO_SIM,
        valores={"numero_pedido": "261011AAAA0003"},
        acoes_modelo=acoes,
    )


@pytest.mark.parametrize(
    ("fala", "assunto"),
    [
        ("Hoje era o dia da entrega do relogio e ainda não recebi", "entrega_nao_recebida"),
        ("a previsão era hoje e não chegou", "rastreio"),
        ("era pra chegar hoje e nada", "rastreio"),
    ],
)
async def test_m7_d3_o_hoje_era_o_dia_sozinho_nao_segura_na_shopee(fala, assunto):
    """D3 (decisão do dono, 23/10/2026 ~12:50): na Shopee, o "hoje era o dia" SOZINHO é a
    resposta-padrão da equipe ("pode seguir o prazo informado na compra pela plataforma"):
    nem a trava suave nem a régua do chamado seguram (antes da D3, a trava M7 segurava)."""
    r = await _atraso(fala, assunto=assunto)
    assert checagens.ATRASO_SEM_CHAMADO not in r.codigos, r.codigos
    assert constantes.ACAO_ABRIR_CHAMADO not in r.ctx.acoes_pessoa


@pytest.mark.parametrize(
    "fala",
    [
        "Hoje era o dia da entrega e ainda não chegou, me ajuda",
        "era pra chegar hoje, vocês podem ver com a Shopee?",
        "a previsão era hoje e não chegou, quero o reembolso",
    ],
)
async def test_m7_d3_com_a_condicao_do_chamado_segura(fala):
    """D3: com a condição do chamado (o pedido de ajuda/ação, o dinheiro pelo atraso) a
    régua do CÓDIGO abre o chamado (antes era só a trava suave)."""
    r = await _atraso(fala, assunto="rastreio")
    assert constantes.ACAO_ABRIR_CHAMADO in r.ctx.acoes_pessoa, r.ctx.acoes_pessoa
    assert r.decisao != VAI


def test_m7_d3_a_trava_suave_continua_por_baixo_na_shopee():
    """D3: sem a ação do chamado (a régua do código não viu o postado, por exemplo), a trava
    suave segura o pedido de ação e a falha da entrega; o "hoje era o dia" sozinho, não."""
    bloco = _bloco_1(STATUS_CD)

    def trava(fala: str, status: str = STATUS_CD) -> list[str]:
        return [
            p.codigo
            for p in checagens.conferir_atraso_sem_chamado(
                TEXTO_STATUS,
                acao="responder",
                textos_cliente=[fala],
                pedido={**bloco, "ultimo_status": status},
                plataforma="shopee",
            )
        ]

    assert trava("Hoje era o dia da entrega e ainda não chegou, me ajuda") == [
        checagens.ATRASO_SEM_CHAMADO
    ]
    assert trava("Hoje era o dia da entrega e ainda não recebi") == []
    falha = "Houve um imprevisto durante a entrega e ela não pôde ser concluída hoje."
    assert trava("Hoje era o dia da entrega e ainda não recebi", falha) == [
        checagens.ATRASO_SEM_CHAMADO
    ]
    # Fora da Shopee, como antes.
    assert checagens.conferir_atraso_sem_chamado(
        TEXTO_STATUS,
        acao="responder",
        textos_cliente=["Hoje era o dia da entrega e ainda não recebi"],
        pedido=bloco,
        plataforma="ml",
    )


async def test_m7_o_que_nao_e_o_atraso_nao_segura():
    # A pergunta de quando chega, a ação de abrir o chamado escolhida, o pedido entregue.
    for fala, kw in (
        ("Quando chega meu pedido?", {}),
        (
            "Hoje era o dia da entrega e ainda não recebi",
            {"acoes": [constantes.ACAO_ABRIR_CHAMADO]},
        ),
        (
            "Hoje era o dia da entrega e ainda não recebi",
            {"bloco": {**_bloco_1(STATUS_CD), "entregue": True}},
        ),
        (
            "Hoje era o dia da entrega e ainda não recebi",
            {"bloco": {**_bloco_1(None), "postado": acao_pessoa.POSTADO_NAO, "enviado": False}},
        ),
    ):
        r = await _atraso(fala, **kw)
        assert checagens.ATRASO_SEM_CHAMADO not in r.codigos, (fala, kw, r.codigos)


# ═══════════════ L1. os nomes e as variações do catálogo ═══════════════


def test_l1_o_titulo_ate_a_palavra_e_a_variacao_sem_o_titulo_velho():
    titulo, variacoes = catalogo.titulo_e_variacoes(
        [x.titulo for x in VINCULOS_A18[ITEM_A18_BARBOSA]]
    )
    assert titulo == "Uranyx A18 Pro Max" and catalogo.nome_curto(titulo) == "Uranyx A18 Pro Max"
    assert variacoes == [
        "A18 - Branco NFC",
        "A18 - Laranja NFC",
        "C17 - Laranja",
        "C17 - Azul",
        "C17 - Prata",
    ]
    # O "64" do título velho não é da variação de 128 GB: o A17 preto de 64 GB está zerado.
    (a17,) = catalogo.buscar_nos_vinculos(catalogo.ler_termo("A17 preto 64 GB"), VINCULOS_A17)
    assert a17.disponivel is False and a17.variacoes_casadas == ("A17 - Preto,12 GB / 64 GB",)
    (a17,) = catalogo.buscar_nos_vinculos(catalogo.ler_termo("A17 laranja 64 GB"), VINCULOS_A17)
    assert a17.disponivel is True
    # A palavra que nenhuma variação tem escolhe pelo título do próprio vínculo ("S5").
    (a18,) = catalogo.buscar_nos_vinculos(catalogo.ler_termo("A18 S5"), VINCULOS_A18)
    assert a18.disponivel is True


async def test_l1_o_anuncio_lido_pelo_ver_anuncio_nao_e_da_conversa(db: AsyncSession, make_user):
    conversa, _integ = await _loja_com_catalogo(db, make_user)
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    # O ver_anuncio do A18 (outro anúncio da loja) põe o A18 em `ctx.anuncios`.
    ctx.aceitar_anuncio(ITEM_A18)
    dados = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="A18")
    assert [(a["item_id"], a["da_conversa"]) for a in dados["anuncios"]] == [(ITEM_A18, False)]
    linhas = v.linhas_da_busca(v.busca_do_contexto(ctx), ())
    assert "é o anúncio desta conversa" not in " ".join(linhas)
    # O anúncio da conversa continua marcado.
    proprio = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="A17 Pro Max")
    assert [(a["item_id"], a["da_conversa"]) for a in proprio["anuncios"]][0] == (ITEM_A17, True)


# ═══════════════ L2. "entregam COM nota fiscal aqui?" é pergunta de entrega ═══════════════


@pytest.mark.parametrize(
    ("fala", "de_entrega"),
    [
        ("Vocês enviam nota fiscal?", False),
        ("vcs mandam a nota fiscal junto?", False),
        ("Vocês emitem NF?", False),
        ("Vocês entregam com nota fiscal aqui?", True),
        ("vcs enviam com nota fiscal pra minha casa?", True),
    ],
)
def test_l2_a_nota_como_objeto_e_a_entrega_com_nota(fala, de_entrega):
    assert checagens._pergunta_de_entrega(fala) is de_entrega


# ═══════════════ a calibração das regras revistas ═══════════════

NOVOS_FECHAMENTO = {
    "viz-catalogo-fone-busca-vazia": False,
    "boa-catalogo-pelicula-sem-no-titulo": True,
    "viz-catalogo-c17-esgotado-disponivel": False,
    "reposicao-redondo-lista-incompleta": False,
}


def test_calibracao_as_linhas_do_fechamento():
    casos = {c["id"]: c for c in calibrar.ler_casos(calibrar.CASOS_PADRAO)}
    for id_, bom in NOVOS_FECHAMENTO.items():
        caso = casos[id_]
        calibrar.conferir_caso(caso)
        assert caso["esperado"]["ok_para_envio_automatico"] is bom, id_
        assert not calibrar.contaminado(caso), id_
    entrada = calibrar.entrada_do_caso
    assert "nenhum anúncio casou com o termo (isso não prova" in entrada(
        casos["viz-catalogo-fone-busca-vazia"]
    )
    assert 'nenhum anúncio desta loja tem "pelicula" no título' in entrada(
        casos["boa-catalogo-pelicula-sem-no-titulo"]
    )
    assert 'nenhum anúncio desta loja tem "capa" no título' in entrada(
        casos["boa-catalogo-capa-fato"]
    )
    assert "disponivel=false" in entrada(casos["viz-catalogo-c17-esgotado-disponivel"])
    assert v.TODAS_ESGOTADAS not in entrada(casos["reposicao-redondo-lista-incompleta"])
    assert v.TODAS_ESGOTADAS in entrada(casos["boa-redondo-073983a2"])


async def test_a1_sem_produto_na_conversa_o_anuncio_lido_e_o_assunto():
    """Sem produto na conversa, o anúncio que o `ver_anuncio` leu é o assunto: "o A18 tem
    NFC" (característica) não é a afirmação de que a loja o tem — e o "temos o A18 à venda"
    continua pedindo a busca."""
    lida = {
        "item_id": ITEM_A18_BARBOSA,
        "titulo": "Uranyx A18 Pro Max 16.128 Gb NFC Memoria S5 Premium Edition",
        "variacoes": [
            {"nome": "A18 - Branco NFC", "disponivel": True, "confiavel": True, "fonte": "loja"}
        ],
        "variacoes_completas": True,
        "da_conversa": False,
    }
    r = await _auto(
        _proposta(
            "Sim, o Uranyx A18 Pro Max tem NFC.",
            "espec_celular",
            [f"ficha:{ITEM_A18_BARBOSA}"],
            0.9,
        ),
        [_menu(), *_cli("o A18 tem NFC?")],
        fichas_lidas=[lida],
    )
    assert not _g1(r), r.codigos
    r = await _auto(
        _proposta(
            "Temos sim, o Uranyx A18 Pro Max está à venda na nossa loja.",
            "estoque_variacao",
            [f"ficha:{ITEM_A18_BARBOSA}"],
            0.9,
        ),
        [_menu(), *_cli("vocês têm o A18?")],
        fichas_lidas=[lida],
    )
    assert checagens.OUTRO_PRODUTO_SEM_BUSCA in r.codigos, r.codigos


@pytest.mark.parametrize(
    "texto",
    [
        "Sim! Na caixa tem carregador, cabo e o fone.",
        "Sim, o aparelho tem capinha e película de brinde na caixa.",
        "O F109S tem processador Helio G81 e acompanha o fone UFB10.",
    ],
)
async def test_a1_o_conteudo_da_caixa_nao_e_g1(texto):
    """O "tem" do acessório que vem na caixa e o do processador não são a afirmação de que
    a loja vende o produto."""
    r = await _auto(
        _proposta(texto, "conteudo_da_caixa", [], 0.9),
        [_menu(), *_cli("vem com carregador?")],
        fichas=[FICHA_A17],
    )
    assert not _g1(r), r.codigos
