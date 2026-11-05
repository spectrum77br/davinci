# ruff: noqa: F811 — fixtures importadas dos testes de ponta a ponta
"""O recurso que este produto não tem (o dono, 04 e 05/11/2026).

"esse produto não tem, mas o produto Y tem, que temos" e "dá pra ser mais incisivo?": o CÓDIGO
procura na loja e acrescenta a frase fixa, sem depender do modelo e sem chamar o modelo de novo.
Os títulos dos testes são os de produção (lidos em 05/11/2026, só leitura).
"""

from __future__ import annotations

import ast
import collections
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Product, ProductLink
from app.models.enums import LinkSyncStatus
from app.services.atendimento import agente, constantes, validador
from app.services.atendimento import ficha as ficha_svc
from app.services.atendimento import ia as ia_svc
from app.services.atendimento.agente import catalogo, checagens, contexto, ferramentas
from app.services.atendimento.agente.saida import Proposta
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    NAO_DO_VERIFICADOR,
    _decisao,
    _do_agente,
    _do_verificador,
    _ligar,
    _usa,
    agente_ligado,
    api,
    envios,
    ia_ligada,
)
from tests.test_atendimento_agente_e2e import _final as _final_e2e
from tests.test_atendimento_agente_ferramentas import _base as _base_db
from tests.test_atendimento_agente_ferramentas import _ctx
from tests.test_atendimento_agente_link_anuncio import SHOP_ID, URL, _loja_com_shop_id
from tests.test_atendimento_agente_redondo import _canal_real
from tests.test_atendimento_agente_redondo2 import ITEM_A17, ITEM_A18
from tests.test_atendimento_ficha_loja import RedisMem
from tests.test_atendimento_ia import _conversa as _conversa_ia
from tests.test_atendimento_ia import _importar_manual_base, _msg

NFC = next(r for r in checagens.RECURSOS_DA_LOJA if r.chave == "nfc")
G5 = next(r for r in checagens.RECURSOS_DA_LOJA if r.chave == "5g")
ZIPER = next(r for r in checagens.RECURSOS_DA_LOJA if r.chave == "ziper_expansivo")
TITULO_A17 = "Uranyx A17 Pro Max 12.128 ou 12.64 GB Memoria"
ITEM_S7 = "44400000007"
ITEM_F112 = "44400000112"
ITEM_WATCH = "44400000200"
ITEM_PAUSADO = "44400000300"
ITEM_VARIOS = "44400000400"
ITEM_USADO = "44400000500"
ML_A17 = "MLB1000000001"
ML_S5 = "MLB1000000002"


def _p(
    texto: str = "O A17 Pro Max não tem NFC.",
    *,
    acao: str = "responder",
    assunto: str = "espec_celular",
    evidencias: list[str] | None = None,
    acao_pessoa: str | None = None,
) -> Proposta:
    return Proposta(
        acao=acao,
        texto=texto,
        assunto=assunto,
        confianca=0.9,
        acao_pessoa=acao_pessoa,
        motivo_pessoa="",
        evidencias=evidencias if evidencias is not None else [f"ficha:{ITEM_A17}"],
    )


def _alvo(
    p: Proposta | None = None,
    *,
    falas: tuple[str, ...] = ("esse tem nfc?",),
    titulos: tuple[str, ...] = (TITULO_A17,),
    pedido: bool = False,
    canal: str = "chat",
    postado: str | None = None,
) -> Any:
    return checagens.recurso_a_oferecer(
        p or _p(),
        textos_cliente=list(falas),
        titulos=list(titulos),
        ids_da_conversa={ITEM_A17},
        tem_pedido=pedido,
        canal=canal,
        postado=postado,
    )


# ═══════════════ quem aciona ═══════════════


@pytest.mark.parametrize(
    "texto",
    [
        "O A17 Pro Max não tem NFC.",
        "Olá! Este aparelho não possui NFC, mas tem bateria de 6000 mAh.",
        "Este modelo não conta com NFC.",
        "Rede móvel 4G e Wi-Fi 5 GHz, sem NFC.",
        "O anúncio não informa NFC.",
        "O anúncio não informa se tem NFC.",
        "NFC não consta no anúncio.",
    ],
)
def test_a_negativa_do_modelo_aciona(texto):
    assert _alvo(_p(texto)) == (NFC, "celular")


@pytest.mark.parametrize(
    "texto",
    [
        "A ficha do Uranyx S7 não traz NFC entre as características.",
        # Os das respostas reais de 30 dias.
        "Olá! O Uranyx S7 não tem NFC, então não serve para pagamento por aproximação.",
    ],
)
def test_a_negativa_do_modelo_da_conversa_aciona(texto):
    assert _alvo(_p(texto), titulos=("Uranyx S7 12.256 GB",)) == (NFC, "celular")


@pytest.mark.parametrize(
    "texto",
    [
        "Sim, o A17 Pro Max tem NFC.",
        "Tem NFC sim.",
        # A negativa é de outra coisa.
        "O A17 não tem 5G, mas tem NFC.",
        "O A17 Pro Max não tem NFC, e o A18 Pro Max tem NFC.",
        "O A17 Pro Max não tem NFC e o A18 Pro Max tem NFC.",
        "O A17 Pro Max não tem NFC. O A18 Pro Max tem NFC.",
        "Qual modelo você quer saber se tem NFC?",
        "Este não tem NFC. Qual modelo você procura?",
        # O modelo já ofereceu.
        "Este não tem NFC, mas temos o Uranyx A18 Pro Max, que tem.",
        "Este não tem NFC. Temos outro modelo que tem.",
        "Este não tem NFC. Segue o link do anúncio: {link_anuncio:a}",
        "Este não tem NFC; há outra opção na nossa loja.",
        # Não fala do recurso.
        "A bateria é de 6000 mAh.",
    ],
)
def test_o_texto_que_nao_aciona(texto):
    assert _alvo(_p(texto)) is None


def test_nao_temos_no_texto_nao_conta_como_oferta():
    # "Não temos a informação" não oferece nada: a negativa do NFC ainda aciona.
    p = _p("Não temos outra informação do modelo, mas ele não tem NFC.")
    assert _alvo(p) == (NFC, "celular")


def test_o_modelo_ja_citou_o_anuncio_de_fora_nao_duplica():
    p = _p(evidencias=[f"ficha:{ITEM_A17}", f"anuncio:{ITEM_A18}"])
    assert _alvo(p) is None


@pytest.mark.parametrize(
    "falas",
    [
        ("qual a bateria?",),
        ("tem garantia de fábrica?",),
    ],
)
def test_cliente_nao_perguntou_o_recurso_nao_aciona(falas):
    assert _alvo(falas=falas) is None


def test_quem_aciona_e_a_fala_do_cliente_nao_o_titulo():
    # O título diz NFC, o cliente pergunta outra coisa e o modelo fala do NFC por conta dele.
    titulos = ("Uranyx A17 Pro Max 12.64 GB Memoria NFC",)
    assert _alvo(falas=("é original?",), titulos=titulos) is None


@pytest.mark.parametrize(
    "fala",
    [
        "o nfc não funciona",
        "comprei e o nfc parou",
        "o nfc veio com defeito",
        "quero devolver, não tem nfc",
    ],
)
def test_reclamacao_nao_aciona(fala):
    assert _alvo(falas=(fala,)) is None


@pytest.mark.parametrize("assunto", ["defeito", "garantia", "cancelamento", "pede_atendente"])
def test_outro_assunto_nao_aciona(assunto):
    assert _alvo(_p(assunto=assunto)) is None


def test_acao_que_nao_e_responder_nao_mexe():
    assert _alvo(_p(acao="perguntar")) is None
    assert _alvo(_p(acao="pessoa", acao_pessoa=constantes.ACAO_EXCECAO)) is None


def test_com_pedido_nao_aciona_nesta_versao():
    assert _alvo(pedido=True) is None
    assert _alvo(pedido=True, postado=None) is None
    # A pergunta pública é antes da compra, seja o que for que o contexto ache.
    assert _alvo(pedido=True, canal=constantes.CANAL_PERGUNTA) == (NFC, "celular")


def test_a_constante_abre_o_pedido_nao_enviado(monkeypatch):
    monkeypatch.setattr(checagens, "RECURSO_COM_PEDIDO", checagens.RECURSO_COM_PEDIDO_NAO_ENVIADO)
    assert _alvo(pedido=True) == (NFC, "celular")
    assert _alvo(pedido=True, postado="2026-11-04") is None


def test_o_wifi_5g_nao_e_o_5g():
    assert _alvo(_p("Não tem 5G."), falas=("pega wifi 5g?",)) is None
    assert _alvo(_p("Não tem 5G."), falas=("pega 5g?",)) == (G5, "celular")


def test_o_ziper_so_na_mala():
    titulo = ("Mala De Bordo 20 Polegadas 10kg Polipropileno",)
    p = _p("Esta mala não tem zíper expansivo.", assunto="espec_mala")
    assert _alvo(p, falas=("tem ziper expansivel?",), titulos=titulo) == (ZIPER, "mala")
    assert _alvo(p, falas=("é expansivo?",), titulos=titulo) == (ZIPER, "mala")
    # "expansível" de celular é a memória.
    celular = _p("A memória não é expansível.")
    assert _alvo(celular, falas=("a memória é expansível?",)) is None


def test_sem_saber_o_tipo_da_conversa_nao_aciona():
    assert _alvo(titulos=()) is None
    assert _alvo(titulos=("Produto XPTO",)) is None
    # Dois tipos na conversa: não se sabe de qual fala.
    assert _alvo(titulos=(TITULO_A17, "Smartwatch Serie 9 Max")) is None


@pytest.mark.parametrize(
    ("titulo", "tipo"),
    [
        (TITULO_A17, "celular"),
        ("Uranyx S5 128 Gb 16 Gb Ram Nfc + Tag E Fone Prata", "celular"),
        ("Smartwatch Série 9 Max Bluetooth Esportes Ecg Chamadas Nfc Caixa Rosa", "relogio"),
        ("Apple Watch Series 11 GPS 42mm", "relogio"),
        ("Fone De Ouvido UFB10 Bluetooth", "fone"),
        ("Kit 3 Malas 24/20/14 Despacho Bordo + Frasqueira", "mala"),
        ("Fossibot F109 Celular Robusto 5g 24 Gb+256 Gb", "celular"),
        ("Tablet Uranyx 128 GB", "tablet"),
        ("Produto XPTO", None),
    ],
)
def test_o_tipo_do_titulo(titulo, tipo):
    assert checagens.tipo_do_titulo(titulo) == tipo


# ═══════════════ a revisão de 05/11/2026: o que disparava e não podia ═══════════════


@pytest.mark.parametrize(
    "texto",
    [
        # O produto TEM o recurso.
        "Sim, faz pagamento sem contato por NFC.",
        "Não é só NFC, tem também carregamento rápido.",
        "O NFC não vem ativado de fábrica, é só ligar nas configurações.",
        "Sim! NFC - não precisa levar cartão.",
        "O anúncio tem versões com e sem NFC.",
        "Não tem problema com NFC.",
        "Não há cobrança para usar o NFC.",
        "Vem com ou sem NFC, conforme o pedido.",
        # A alternativa já está no texto, sem "temos" nem "outro".
        "O A17 Pro Max não tem NFC; a versão Premium deste anúncio tem.",
        "O A17 Pro Max não tem NFC, mas o A18 Pro Max tem.",
        "o modelo hotwav a17 pro max não tem nfc somente o modelo a18 pro max uranyx!",
        "modelo c57s não tem nfc mais pode verificar na descrição do anuncio esta escrito qual"
        " aparelho tem!",
        "Este anúncio só tem NFC na versão Premium, a sua versão não tem NFC.",
        # "esse recurso" não diz qual: dois do vocabulário.
        "O A17 Pro Max não tem NFC nem 5G.",
        "O A17 Pro Max não tem NFC. Também não tem 5G.",
        "O A17 não tem NFC, mas tem 5G.",
    ],
)
def test_o_texto_que_a_revisao_tirou(texto):
    assert _alvo(_p(texto)) is None


@pytest.mark.parametrize("fala", ["esse tem nfc e 5g?", "tem 5g? e nfc?"])
def test_o_cliente_que_pergunta_os_dois_nao_aciona(fala):
    assert _alvo(falas=(fala,)) is None


def test_a_evidencia_de_outra_ficha_ja_e_oferta():
    assert _alvo(_p(evidencias=[f"ficha:{ITEM_A17}", f"ficha:{ITEM_A18}"])) is None


@pytest.mark.parametrize(
    ("fala", "texto"),
    [
        ("o wifi desse é 5g?", "O Wi-Fi não é 5G."),
        ("a banda do wifi é 5g?", "Não tem 5G."),
        ("pega a internet 5g da minha casa?", "Não tem 5G."),
        ("pega 5g?", "O Wi-Fi não é 5G."),
    ],
)
def test_a_rede_de_casa_nao_e_o_5g(fala, texto):
    assert _alvo(_p(texto), falas=(fala,)) is None


@pytest.mark.parametrize(
    "falas",
    [
        ("Comprei esse ontem, chegou hoje", "Ele tem NFC?"),
        ("Quero justamente sem NFC, esse é sem NFC?",),
        ("Nao quero outro modelo, so quero saber se esse tem nfc",),
        ("esse é sem nfc?",),
    ],
)
def test_o_cliente_que_nao_pede_alternativa(falas):
    assert _alvo(falas=falas) is None


def _alvo_com_a_conversa(**kw: Any) -> Any:
    return checagens.recurso_a_oferecer(
        _p(),
        textos_cliente=["esse tem nfc?"],
        titulos=[TITULO_A17],
        ids_da_conversa={ITEM_A17},
        tem_pedido=False,
        canal="chat",
        **kw,
    )


def test_a_oferta_nao_volta_a_cada_pergunta():
    assert _alvo_com_a_conversa() == (NFC, "celular")
    oferta = "Este não tem 5G. Temos o Uranyx F112 Pro, que tem esse recurso: https://x"
    assert _alvo_com_a_conversa(textos_da_loja=[oferta]) is None
    assert _alvo_com_a_conversa(textos_da_loja=["Este não tem NFC."]) is None
    assert _alvo_com_a_conversa(textos_da_loja=["Bom dia, em que posso ajudar?"]) is not None


def test_a_compra_dita_antes_nao_aciona():
    assert _alvo_com_a_conversa(textos_cliente_antes=["comprei semana passada"]) is None
    assert _alvo_com_a_conversa(textos_cliente_antes=["boa tarde"]) is not None


# ═══════════════ quem pode ser oferecido ═══════════════


def _achado(
    item: str = ITEM_A18,
    titulo: str = "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium",
    *,
    disponivel: bool | None = True,
    da_conversa: bool = False,
    nome: str | None = None,
    variacoes: tuple[str, ...] = (),
) -> dict:
    return {
        "item_id": item,
        "nome_curto": nome if nome is not None else catalogo.nome_curto(titulo),
        "variacoes_casadas": list(variacoes),
        "disponivel": disponivel,
        "da_conversa": da_conversa,
        "titulo_plano": catalogo.plano(" ".join([titulo, *variacoes])),
    }


def _achados(*anuncios: dict, recurso: Any = NFC, tipo: str = "celular", texto: str = "") -> list:
    return checagens.achados_do_recurso(
        recurso,
        [{"anuncios": list(anuncios)}],
        tipo=tipo,
        ids_da_conversa={ITEM_A17},
        texto=texto,
    )


def test_o_achado_a_venda_com_o_recurso_escrito_entra():
    assert [a["item_id"] for a in _achados(_achado())] == [ITEM_A18]


@pytest.mark.parametrize(
    "achado",
    [
        # Apple Watch com NFC para quem pergunta de celular.
        _achado(ITEM_WATCH, "Apple Watch Series 11 NFC GPS 42mm"),
        _achado(ITEM_WATCH, "Smartwatch Série 9 Max Bluetooth Nfc"),
        # O anúncio da conversa.
        _achado(ITEM_A17, "Uranyx A17 Pro Max 12.128 GB NFC"),
        _achado(ITEM_S7, "Uranyx S7 12.256 GB NFC", da_conversa=True),
        # Pausado (null) e esgotado.
        _achado(ITEM_PAUSADO, "Uranyx S5 128 Gb NFC", disponivel=None),
        _achado(ITEM_PAUSADO, "Uranyx S5 128 Gb NFC", disponivel=False),
        # O recurso não está escrito: "literal".
        _achado(ITEM_S7, "Uranyx S7 12.256 GB Tela Traseira"),
        _achado(ITEM_S7, "Uranyx S7 12.256 GB Pagamento Aproximacao"),
        # A condição que a frase não diz.
        _achado(ITEM_USADO, "Doogee S41 Max 16.256 GB NFC Resistente (USADO)"),
        _achado(ITEM_USADO, "Uranyx F109S 24.256 NFC Recondicionado"),
        # O nome de série não é nome de produto.
        _achado(ITEM_VARIOS, "Uranyx Séries C com 64,128,256 e 512 GB Memoria NFC"),
        # O nome não se escreve.
        _achado(ITEM_S7, "Capa...uranyx A18 Pro Max Nfc 16.128 Gb"),
        _achado(ITEM_S7, "Case Uranyx S5 128 Nfc Gb Memoria 16 Gb Ram"),
    ],
)
def test_o_achado_que_nao_entra(achado):
    assert _achados(achado) == []


F105 = "Fossibot F105 Celular Robusto 4g 12 Gb+64 Gb, Bateria 10300mah, Nfc, Android 14, Ip68 Preto"
C68 = (
    "Oukitel C68 Plus-ev Celular 4gb+128gb, Tela 7,2 Hd+ 120hz, Bateria 6000mah, Nfc, Android 15.0"
)
S7_NFC = (
    "Fossibot S7 Celular 4g 8 Gb+256 Gb, Tela 6,9 Hd+, Bateria 5000mah, Nfc, Android 16 Dourado"
)


@pytest.mark.parametrize("titulo", [F105, C68, S7_NFC, "Uranyx A17 Pro Max 12.128 GB NFC"])
def test_o_titulo_que_a_equipe_desmente_nao_entra(titulo):
    # Vale a equipe (o dono, 05/11/2026): o título diz NFC, a equipe nega.
    achado = _achado(ITEM_S7, titulo, nome=catalogo.nome_curto(titulo))
    assert _achados(achado) == []
    dado = _dado(titulo, especificacoes=_espec())
    assert checagens.ficha_confirma_o_recurso(NFC, dado, achado) is None


def test_o_anuncio_que_mistura_os_da_lista_com_um_de_fora_nao_entra():
    titulo = "Uranyx F109 NFC 24.256"
    casada = _achado(ITEM_S7, titulo, variacoes=("NFC - F105,64 GB",))
    assert _achados(casada) == []
    assert _achados(_achado(ITEM_S7, titulo, variacoes=("NFC - F109,64 GB",)))


@pytest.mark.parametrize(
    "linhas",
    [
        # O prefixo do anúncio em todas as variações, de modelos diferentes.
        (("NFC - F105,64 GB", True), ("NFC - F112", True), ("NFC - F109 5G,256 GB", True)),
        # Quase todas: "na versão que tem" não diria qual.
        (("NFC - F112", True), ("NFC - F109", True), ("F117", True)),
    ],
)
def test_o_prefixo_em_varios_modelos_nao_prova_a_versao(linhas):
    titulo = "Uranyx F109 5G 24.256 ou F109S 24.256 - Resistente a Água e Queda"
    dado = _dado(titulo, variacoes=_variacoes(*linhas))
    assert checagens.ficha_confirma_o_recurso(NFC, dado, _achado()) is None


def test_a_ficha_continua_lida_com_a_regua_larga():
    # O que não dispara no texto do modelo tira a oferta quando está na ficha.
    dado = _dado(atributos=["NFC - não possui"])
    assert checagens.ficha_confirma_o_recurso(NFC, dado, _achado()) is None


def test_o_mesmo_modelo_e_pelo_titulo_inteiro():
    titulo = "Mala De Viagem 20 Polegadas + Mochila Executiva 10Kg Bordo ME1 Expansível"
    achado = _achado("1", titulo, nome="Mala De Viagem")
    conversa = ["Mala Executiva De Bordo 20 Polegadas 10Kg ME1 - Preto"]
    assert checagens.mesmo_modelo_da_conversa(achado, conversa)
    assert not checagens.mesmo_modelo_da_conversa(achado, ["Mala De Bordo 20 Polegadas ABS"])


def test_o_nome_para_a_frase():
    assert checagens.nome_para_a_frase("uranyx F109") == "Uranyx F109"
    assert checagens.nome_para_a_frase("Uranyx S5 128") == "Uranyx S5"
    assert checagens.nome_para_a_frase("Uranyx S5 16") == "Uranyx S5"


@pytest.mark.parametrize(
    ("achado", "busca", "esperado"),
    [
        # Sem código, o nome não acha nada.
        (_achado("1", "Mala De Viagem Expansivel", nome="Mala De Viagem"), [], False),
        # Sem a busca, não se sabe.
        (_achado(nome="Uranyx F109"), [], False),
        # O F109 de 4G à venda na mesma loja: "pelo nome" leva a ele também.
        (
            _achado("1", "Uranyx F109 5g 256", nome="Uranyx F109"),
            [_achado("1", "Uranyx F109 5g 256"), _achado("2", "Uranyx F109 4g 128")],
            False,
        ),
        # O outro está fora de estoque: não aparece como opção.
        (
            _achado("1", "Uranyx F109 5g 256", nome="Uranyx F109"),
            [_achado("1", "Uranyx F109 5g 256"), _achado("2", "F109 4g", disponivel=False)],
            True,
        ),
    ],
)
def test_sem_link_o_nome_acha_so_quem_tem(achado, busca, esperado):
    busca_toda = [{"anuncios": busca}] if busca else []
    assert checagens.nome_acha_so_quem_tem(G5, achado, busca_toda) is esperado


def test_o_anuncio_de_que_o_texto_fala_nao_entra():
    # "O A18 não tem NFC" não pode virar "temos o A18, que tem".
    assert _achados(_achado(), texto="O A18 Pro Max não tem NFC.") == []
    fora = checagens.achados_do_recurso(
        NFC,
        [{"anuncios": [_achado()]}],
        tipo="celular",
        ids_da_conversa={ITEM_A17},
        texto="Este não tem NFC.",
        evidencias=[f"ficha:{ITEM_A18}"],
    )
    assert fora == []


def test_o_mesmo_nome_e_o_mesmo_anuncio_nao_repetem():
    a = _achado()
    b = _achado("99", "Uranyx A18 Pro Max NFC 16.128 Gb Android 16")
    assert [x["item_id"] for x in _achados(a, a, b)] == [ITEM_A18]


def test_o_5g_do_wifi_nao_e_literal():
    assert _achados(_achado(ITEM_S7, "Uranyx S7 12.256 GB Wi-Fi 5G"), recurso=G5) == []
    assert len(_achados(_achado(ITEM_F112, "Uranyx F112 Pro 5g 256 Gb"), recurso=G5)) == 1


def test_a_mala_expansivel_e_literal_e_o_nome_tem_artigo():
    mala = _achado(
        "1", "Mala De Bordo 20 Polegadas Expansível + Mochila Executiva 10Kg P7 - Branco"
    )
    assert len(_achados(mala, recurso=ZIPER, tipo="mala")) == 1
    assert checagens._artigo_do_nome("Mala De Bordo") == "a"
    assert checagens._artigo_do_nome("Kit 3 Malas") == "o"
    assert checagens._artigo_do_nome("Uranyx A18 Pro Max") == "o"
    # A mala para quem pergunta de celular.
    assert _achados(mala, recurso=ZIPER, tipo="celular") == []


def _dado(titulo: str = "Uranyx A18 Pro Max 16.128 Gb NFC", **kw: Any) -> dict:
    return {"item_id": ITEM_A18, "titulo": titulo, "atributos": [], "descricao": "", **kw}


def _variacoes(*linhas: tuple[str, bool], completas: bool = True, fonte: str = "loja") -> dict:
    return {
        "variacoes": [{"nome": n, "disponivel": d, "fonte": fonte} for n, d in linhas],
        "completas": completas,
        "omitidas": 0,
    }


def _espec(valor: str = "sim", **kw: Any) -> dict:
    return {"campos": [{"campo": "nfc", "valor": valor, "fonte": "anuncio", **kw}], "conflitos": []}


@pytest.mark.parametrize(
    ("dado", "esperado"),
    [
        # Confirma: o anúncio inteiro.
        (_dado(), False),
        (_dado(especificacoes=_espec()), False),
        (_dado(variacoes=_variacoes(("Branco NFC", True), ("Laranja NFC", False))), False),
        # Só uma versão tem: a frase fala da versão.
        (
            _dado(
                "Uranyx S5 12.64 / 12.128 / 16.128 GB Memoria NFC",
                variacoes=_variacoes(
                    ("Laranja,16 GB / 128 GB Premium NFC", True), ("Laranja,12 GB / 64 GB", True)
                ),
            ),
            True,
        ),
        # A versão que tem está esgotada.
        (
            _dado(variacoes=_variacoes(("16 GB Premium NFC", False), ("12 GB / 64 GB", True))),
            None,
        ),
        # A ficha contradiz.
        (_dado(descricao="Versão 4GB+128GB: sem NFC."), None),
        (_dado(descricao="Este modelo não tem NFC."), None),
        (_dado(atributos=["NFC: No"]), None),
        (_dado(especificacoes=_espec("não")), None),
        (_dado(especificacoes=_espec(conflito_no_anuncio=True)), None),
        (
            _dado(
                especificacoes={
                    "campos": [],
                    "conflitos": [{"campo": "nfc", "valor": "sim", "outro_valor": "não"}],
                }
            ),
            None,
        ),
        # Vários modelos e nenhuma variação diz qual tem.
        (_dado(especificacoes=_espec(varia_por_modelo=True)), None),
        (_dado("Uranyx A17 Pro Max 12.128 ou 12.64 GB Memoria / Uranyx S5 16.128 NFC"), None),
        (_dado("Smartphones Uranyx Séries com 64,128 Memoria Varios Modelos com NFC"), None),
        (
            _dado(variacoes=_variacoes(("A17 - Laranja", True), ("C17 - Prata", True))),
            None,
        ),
        # A ficha de outro anúncio, a falta dela e nada à venda.
        (_dado(fonte=f"{ficha_svc.FONTE_OUTRO_ANUNCIO} (Shopee)"), None),
        ({"item_id": ITEM_A18, "titulo": "Uranyx A18 NFC", "sem_ficha": True}, None),
        (None, None),
        (_dado(variacoes=_variacoes(("Branco NFC", False), ("Laranja NFC", False))), None),
        # O nome do cadastro não conta (o nome interno): vale o título.
        (
            _dado(variacoes=_variacoes(("Interno NFC", True), ("Outro", True), fonte="cadastro")),
            False,
        ),
    ],
)
def test_o_que_a_ficha_diz(dado, esperado):
    assert checagens.ficha_confirma_o_recurso(NFC, dado, _achado()) is esperado


def test_a_variacao_casada_na_busca_e_por_versao():
    achado = _achado(variacoes=("P1 Pro - Preto - NFC,256",))
    assert checagens.ficha_confirma_o_recurso(NFC, _dado("Uranyx P1 Pro 256 GB"), achado) is True


# ═══════════════ a frase e o canal ═══════════════


def _oferta(
    item: str = ITEM_A18,
    nome: str = "Uranyx A18 Pro Max",
    lacuna: str | None = None,
    por_versao: bool = False,
) -> checagens.OfertaDoRecurso:
    return checagens.OfertaDoRecurso(
        item_id=item,
        nome=nome,
        lacuna=lacuna,
        por_versao=por_versao,
        ids=(f"anuncio:{item}", f"ficha:{item}"),
    )


def _com(p: Proposta, *ofertas: Any, links=None, plat="shopee", canal="chat") -> Any:
    return checagens.proposta_com_o_recurso(
        p, NFC, list(ofertas), links=links or {}, plataforma=plat, canal=canal
    )


def test_um_anuncio_sem_link():
    novo, usadas = _com(_p(), _oferta(), plat="ml", canal=constantes.CANAL_PERGUNTA)
    assert novo.texto == (
        "O A17 Pro Max não tem NFC. Temos o Uranyx A18 Pro Max, que tem esse recurso; "
        "aparece na página da loja no app, pelo nome."
    )
    assert novo.evidencias == [f"ficha:{ITEM_A17}", f"anuncio:{ITEM_A18}", f"ficha:{ITEM_A18}"]
    assert [o.item_id for o in usadas] == [ITEM_A18]


def test_um_anuncio_com_link_na_shopee():
    oferta = _oferta(lacuna="{link_anuncio:a}")
    novo, _ = _com(_p(), oferta, links={"a": URL})
    assert novo.texto == (
        "O A17 Pro Max não tem NFC. Temos o Uranyx A18 Pro Max, que tem esse recurso: "
        "{link_anuncio:a}"
    )


def test_dois_anuncios_com_link_um_por_frase():
    a, b = _oferta(lacuna="{link_anuncio:a}"), _oferta("2", "Uranyx F112 Pro", "{link_anuncio:b}")
    novo, usadas = _com(_p(), a, b, links={"a": URL, "b": URL + "2"})
    assert novo.texto == (
        "O A17 Pro Max não tem NFC. Temos o Uranyx A18 Pro Max, que tem esse recurso: "
        "{link_anuncio:a}. Temos também o Uranyx F112 Pro, que tem esse recurso: {link_anuncio:b}"
    )
    assert len(usadas) == 2


def test_dois_anuncios_sem_link():
    novo, _ = _com(_p(), _oferta(), _oferta("2", "Uranyx F112 Pro"), plat="ml", canal="pergunta")
    assert novo.texto == (
        "O A17 Pro Max não tem NFC. Temos o Uranyx A18 Pro Max e o Uranyx F112 Pro, que têm esse "
        "recurso; aparecem na página da loja no app, pelo nome."
    )


def test_o_que_tem_link_vence_o_que_nao_tem():
    novo, usadas = _com(
        _p(), _oferta("2", "Uranyx F112 Pro"), _oferta(lacuna="{link_anuncio:a}"), links={"a": URL}
    )
    assert [o.item_id for o in usadas] == [ITEM_A18] and "F112" not in novo.texto


def test_a_versao_e_dita():
    novo, _ = _com(_p(), _oferta(por_versao=True), plat="ml", canal="pergunta")
    assert "Temos o Uranyx A18 Pro Max na versão que tem esse recurso;" in novo.texto
    # Dois sem link, com versão: "o A na versão e o B, que têm" não diz qual. Uma oferta só.
    b = _oferta("2", "Uranyx F112 Pro")
    _novo, usadas = _com(_p(), _oferta(por_versao=True), b, plat="ml", canal="pergunta")
    assert [o.item_id for o in usadas] == [ITEM_A18]


def test_a_frase_entra_logo_depois_da_negativa_e_o_fecho_fica_por_ultimo():
    p = _p("Olá! Este aparelho não tem NFC. Qualquer dúvida, estamos à disposição!")
    novo, _ = _com(p, _oferta(lacuna="{link_anuncio:a}"), links={"a": URL})
    assert novo.texto == (
        "Olá! Este aparelho não tem NFC. Temos o Uranyx A18 Pro Max, que tem esse recurso: "
        "{link_anuncio:a}. Qualquer dúvida, estamos à disposição!"
    )
    sem_ponto, _ = _com(_p("Este aparelho não tem NFC"), _oferta(), plat="ml", canal="pergunta")
    assert sem_ponto.texto.startswith("Este aparelho não tem NFC. Temos o")


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        # O resto é do produto da conversa: fica ANTES da oferta, e a frase nomeia o recurso.
        (
            "Olá! O A17 Pro Max não tem NFC. Ele tem Wifi, rede móvel 4G e Dual Sim.",
            "Olá! O A17 Pro Max não tem NFC. Ele tem Wifi, rede móvel 4G e Dual Sim. Quanto ao "
            "NFC, temos o Uranyx A18 Pro Max, que tem esse recurso: {link_anuncio:a}",
        ),
        (
            "Este aparelho não possui NFC, mas tem bateria de 6000 mAh.",
            "Este aparelho não possui NFC, mas tem bateria de 6000 mAh. Quanto ao NFC, temos o "
            "Uranyx A18 Pro Max, que tem esse recurso: {link_anuncio:a}",
        ),
        # O conteúdo e o fecho: a oferta entre os dois.
        (
            "Este aparelho não tem NFC. Ele tem 2 chips. Qualquer dúvida, estamos à disposição!",
            "Este aparelho não tem NFC. Ele tem 2 chips. Quanto ao NFC, temos o Uranyx A18 Pro "
            "Max, que tem esse recurso: {link_anuncio:a}. Qualquer dúvida, estamos à disposição!",
        ),
    ],
)
def test_nada_do_produto_da_conversa_fica_depois_da_oferta(texto, esperado):
    novo, _ = _com(_p(texto), _oferta(lacuna="{link_anuncio:a}"), links={"a": URL})
    assert novo.texto == esperado


def test_a_negativa_com_cortesia_na_mesma_frase_nao_e_fecho():
    p = _p("Olá! Obrigado pelo contato, este aparelho não tem NFC.")
    novo, _ = _com(p, _oferta(lacuna="{link_anuncio:a}"), links={"a": URL})
    assert novo.texto == (
        "Olá! Obrigado pelo contato, este aparelho não tem NFC. Temos o Uranyx A18 Pro Max, que "
        "tem esse recurso: {link_anuncio:a}"
    )


def test_o_limite_do_ml_tira_o_extra_e_depois_nao_mexe():
    canal = constantes.CANAL_POS_VENDA
    limite = constantes.limite_caracteres("ml", canal)
    assert limite == 350
    uma_oferta = checagens.frase_do_recurso([_oferta()])
    duas = checagens.frase_do_recurso([_oferta(), _oferta("2", "Uranyx F112 Pro")])
    # Cabe uma, não cabem as duas.
    corpo = "x" * (limite - len(uma_oferta) - len(" O A17 não tem NFC. ") - 2)
    p = _p(f"{corpo} O A17 não tem NFC.")
    assert len(p.texto) + 1 + len(uma_oferta) <= limite < len(p.texto) + 1 + len(duas)
    novo, usadas = _com(p, _oferta(), _oferta("2", "Uranyx F112 Pro"), plat="ml", canal=canal)
    assert [o.item_id for o in usadas] == [ITEM_A18] and len(novo.texto) <= limite
    # Não cabe nenhuma: fica como está (quem chama).
    assert _com(_p("x" * 340 + " Sem NFC."), _oferta(), plat="ml", canal=canal) is None


def test_o_limite_conta_a_url_e_nao_a_lacuna():
    limite = constantes.limite_caracteres("shopee", "chat")
    url = "https://shopee.com.br/" + "y" * 100
    frase = checagens.frase_do_recurso([_oferta(lacuna="{link_anuncio:a}")])
    corpo = "x" * (limite - len(frase) - 20)
    p = _p(f"{corpo} Não tem NFC.")
    assert len(p.texto) + 1 + len(frase) <= limite
    assert _com(p, _oferta(lacuna="{link_anuncio:a}"), links={"a": url}) is None


def test_no_ml_so_iso_8859_1():
    assert _com(_p(), _oferta(nome="Uranyx 日本 A18"), plat="ml", canal="pergunta") is None
    # A acentuação do português cabe.
    novo, _ = _com(_p(), _oferta(nome="Uranyx Série A18"), plat="ml", canal="pergunta")
    assert validador._fora_do_latin1(novo.texto) == []


# ═══════════════ a frase passa nas réguas ═══════════════


def _ctx_g1(*anuncios: dict) -> Any:
    return SimpleNamespace(
        buscas_catalogo=[
            {
                "termo": "NFC",
                "achados": [a["item_id"] for a in anuncios],
                "anuncios": list(anuncios),
            }
        ],
        anuncios_buscados={},
        fichas_lidas=[],
        links_anuncio={},
    )


@pytest.mark.parametrize(
    ("ofertas", "citados"),
    [
        ([_oferta()], [ITEM_A18]),
        ([_oferta(lacuna="{link_anuncio:a}")], [ITEM_A18]),
        ([_oferta(por_versao=True)], [ITEM_A18]),
        ([_oferta(), _oferta("2", "Uranyx F112 Pro")], [ITEM_A18, "2"]),
    ],
)
def test_a_g1_aceita_a_frase_fixa(ofertas, citados):
    ctx = _ctx_g1(_achado(), _achado("2", "Uranyx F112 Pro 5g 256 Gb NFC"))
    texto = "O A17 Pro Max não tem NFC. " + checagens.frase_do_recurso(ofertas)
    assert (
        checagens.conferir_outro_produto_da_loja(
            texto,
            acao="responder",
            evidencias=[f"anuncio:{i}" for i in citados],
            ctx=ctx,
            fichas=[],
            fontes_extra=[TITULO_A17],
        )
        == []
    )


def test_a_g1_segura_a_frase_sem_a_evidencia():
    ctx = _ctx_g1(_achado())
    texto = "O A17 Pro Max não tem NFC. " + checagens.frase_do_recurso([_oferta()])
    (p,) = checagens.conferir_outro_produto_da_loja(
        texto, acao="responder", evidencias=[], ctx=ctx, fichas=[], fontes_extra=[TITULO_A17]
    )
    assert checagens.pendencia_dura(p)


def test_por_que_a_frase_nao_repete_o_recurso():
    # "que tem NFC" é lido como dito do produto da CONVERSA, cuja especificação diz não.
    ctx = SimpleNamespace(registro=SimpleNamespace(textos=[]))
    fichas = [
        {
            "item_id": ITEM_A17,
            "titulo": TITULO_A17,
            "especificacoes": {"campos": [{"campo": "nfc", "valor": "não"}], "conflitos": []},
        }
    ]
    repetindo = checagens.conferir_fonte_produto(
        "O A17 não tem NFC. Temos o Uranyx A18 Pro Max, que tem NFC.", fichas, ctx
    )
    assert [p.codigo for p in repetindo] == [checagens.PRODUTO_CONTRADIZ]
    fixa = "O A17 não tem NFC. " + checagens.frase_do_recurso([_oferta()])
    assert checagens.conferir_fonte_produto(fixa, fichas, ctx) == []


def test_o_link_confere_com_a_frase_fixa():
    ctx = SimpleNamespace(
        links_anuncio={
            ITEM_A18: {"codigo": "a", "url": URL, "titulo_plano": "uranyx a18 pro max nfc"},
            "2": {"codigo": "b", "url": URL + "2", "titulo_plano": "uranyx f112 pro nfc"},
        }
    )
    texto = "O A17 Pro Max não tem NFC. " + checagens.frase_do_recurso(
        [_oferta(lacuna="{link_anuncio:a}"), _oferta("2", "Uranyx F112 Pro", "{link_anuncio:b}")]
    )
    assert (
        checagens.conferir_link_do_anuncio(
            texto, ctx=ctx, evidencias=[f"anuncio:{ITEM_A18}", "anuncio:2"]
        )
        == []
    )


# ═══════════════ o registro ═══════════════


def test_o_registro_so_quando_ha():
    conversa = SimpleNamespace(
        id=None,
        integration_id=None,
        canal_id=None,
        plataforma="shopee",
        canal="chat",
        pedido_marketplace=None,
        anuncio_id=None,
    )
    ctx = contexto.ContextoAgente.da_conversa(conversa, None, modo="auto", nomes=())
    assert "recurso_oferecido" not in ctx.para_fatos()
    ctx.recurso_oferecido = {"recurso": "nfc", "anuncios": [ITEM_A18]}
    ctx.recurso_sem_oferta = {"recurso": "5g", "porque": "sem_anuncio"}
    fatos = ctx.para_fatos()
    assert fatos["recurso_oferecido"] == {"recurso": "nfc", "anuncios": [ITEM_A18]}
    assert fatos["recurso_sem_oferta"]["porque"] == "sem_anuncio"


# ═══════════════ de ponta a ponta (o `ia._gerar`, com o banco e a API falsa) ═══════════════


async def _link(
    db: AsyncSession,
    integ: Integration,
    item: str,
    variacao: str,
    titulo: str,
    estoque: int = 50,
    status: str | None = None,
    plataforma: IntegrationPlatform = IntegrationPlatform.SHOPEE,
) -> None:
    produto = Product(user_id=integ.user_id, sku=f"sku-{item}-{variacao}", name=titulo, stock=1)
    db.add(produto)
    await db.flush()
    db.add(
        ProductLink(
            user_id=integ.user_id,
            product_id=produto.id,
            integration_id=integ.id,
            platform=plataforma,
            external_id=item,
            variation_id=variacao,
            external_sku=f"sku-{item}-{variacao}",
            listing_title=titulo,
            stock=estoque,
            price=999,
            last_sync_status=LinkSyncStatus.OK,
            anuncio_status=status,
        )
    )
    await db.commit()


def _ficha(r: RedisMem, integ: Integration, item: str, titulo: str, descricao: str) -> None:
    """A ficha fresca do anúncio: a mesma chave e o mesmo formato do enriquecimento."""
    r.dados[ficha_svc.chave_ficha("shopee", integ.id, item)] = json.dumps(
        {
            "titulo": titulo,
            "descricao": descricao,
            "raw": {"item_id": item},
            "buscada_em": datetime.now(UTC).isoformat(),
        }
    )


async def _nfc(
    db: AsyncSession,
    make_user,
    monkeypatch,
    pergunta: str = "Esse tem NFC?",
    shop: int | None = SHOP_ID,
) -> Any:
    r = RedisMem()
    monkeypatch.setattr(ficha_svc, "redis", r)
    await _importar_manual_base(db)
    conversa, integ = await _loja_com_shop_id(db, make_user, shop_id=shop)
    await _canal_real(db, conversa)
    await _msg(db, conversa, pergunta, ha=timedelta(minutes=6))
    _ficha(r, integ, ITEM_A17, f"{TITULO_A17} - Laranja", "Celular com 2 chips. Sem NFC, 4G.")
    _ficha(
        r,
        integ,
        ITEM_A18,
        "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium",
        "Celular com NFC para pagamento por aproximação.",
    )
    return conversa, integ, r


def _modelo(
    api: Any, texto: str = "O A17 Pro Max não tem NFC.", *, item: str = ITEM_A17, **kw: Any
) -> None:
    api.roteiro = [
        _usa("ver_anuncio", item_id=item),
        _final_e2e(
            texto=texto,
            assunto=kw.pop("assunto", "espec_celular"),
            confianca=0.9,
            evidencias=kw.pop("evidencias", [f"ficha:{item}"]),
            **kw,
        ),
    ]


async def test_e2e_o_codigo_oferece_com_o_link_e_o_verificador_ve_a_lacuna(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    _modelo(api)

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == (
        f"O A17 Pro Max não tem NFC. Temos o Uranyx A18 Pro Max, que tem esse recurso: {URL}"
    ), (r.motivo, r.validador_erros)
    agente_ = _do_agente(r)
    assert agente_["recurso_oferecido"] == {"recurso": "nfc", "anuncios": [ITEM_A18]}
    assert agente_["links_anuncio"] == [URL]
    assert f"anuncio:{ITEM_A18}" in agente_["evidencias"]
    assert f"ficha:{ITEM_A18}" in agente_["evidencias"]
    # Nenhuma chamada a mais ao modelo: a do `ver_anuncio`, a final e o verificador.
    assert len(api.pedidos) == 2 and len(api.verificador) == 1
    entrada = api.verificador[0]["messages"][0]["content"]
    assert "shopee.com.br/product" not in entrada
    assert "que tem esse recurso: {link_anuncio:b}" in entrada
    # A ficha do outro anúncio e a busca estão nas EVIDÊNCIAS do verificador.
    assert "Celular com NFC para pagamento" in entrada
    assert f"anuncio:{ITEM_A18} «Uranyx A18 Pro Max»; disponivel=true" in entrada
    # O rastro mostra o que o código leu.
    assert {"buscar_anuncios_da_loja", "ver_anuncio"} <= {
        passo["ferramenta"] for passo in agente_["rastro"]
    }
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


async def test_e2e_dois_candidatos_com_link(db, make_user, agente_ligado, api, envios, monkeypatch):
    conversa, integ, r_mem = await _nfc(db, make_user, monkeypatch)
    await _link(db, integ, ITEM_F112, "1", "Uranyx F112 Pro NFC 256 Gb Memoria 24gb")
    _ficha(r_mem, integ, ITEM_F112, "Uranyx F112 Pro NFC 256 Gb Memoria 24gb", "Celular com NFC.")
    _modelo(api)

    r = await ia_svc.gerar_rascunho(db, conversa)

    # O nome mais curto primeiro (`catalogo.ordenar`), um link por frase.
    assert _do_agente(r)["recurso_oferecido"]["anuncios"] == [ITEM_F112, ITEM_A18], r.motivo
    assert "Temos também o Uranyx A18 Pro Max, que tem esse recurso:" in r.texto
    assert "{link_anuncio" not in r.texto
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


async def test_e2e_sem_id_da_loja_a_frase_e_a_do_nome(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch, shop=None)
    _modelo(api)

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == (
        "O A17 Pro Max não tem NFC. Temos o Uranyx A18 Pro Max, que tem esse recurso; aparece na "
        "página da loja no app, pelo nome."
    ), r.motivo
    assert "links_anuncio" not in _do_agente(r)
    assert [e["texto"] for e in envios] == [r.texto], r.motivo


async def test_e2e_sem_candidato_nao_mexe_e_nunca_diz_que_a_loja_nao_tem(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch, pergunta="Esse pega 5G?")
    _modelo(api, texto="O A17 Pro Max não tem 5G.")

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == "O A17 Pro Max não tem 5G.", r.motivo
    agente_ = _do_agente(r)
    assert "recurso_oferecido" not in agente_
    assert agente_["recurso_sem_oferta"] == {"recurso": "5g", "porque": "sem_anuncio"}
    assert f"ficha:{ITEM_A18}" not in agente_["evidencias"]
    assert [e["texto"] for e in envios] == [r.texto], r.motivo


async def test_e2e_candidatos_fora_e_o_pausado_e_o_de_outro_tipo(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, integ, r_mem = await _nfc(db, make_user, monkeypatch)
    _ficha(r_mem, integ, ITEM_WATCH, "Apple Watch Series 11 NFC GPS 42mm", "Relógio com NFC.")
    await _link(db, integ, ITEM_WATCH, "1", "Apple Watch Series 11 NFC GPS 42mm")
    await _link(db, integ, ITEM_PAUSADO, "1", "Uranyx S5 128 Gb NFC Memoria", status="paused")
    await _link(db, integ, ITEM_USADO, "1", "Doogee S41 Max 16.256 GB NFC Resistente (USADO)")
    _modelo(api)

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert _do_agente(r)["recurso_oferecido"]["anuncios"] == [ITEM_A18], r.motivo
    assert "Watch" not in r.texto and "S5" not in r.texto and "S41" not in r.texto


async def test_e2e_com_pedido_nao_aciona(db, make_user, agente_ligado, api, envios, monkeypatch):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    conversa.pedido_marketplace = "251104ABC"
    await db.commit()
    _modelo(api)

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert "Temos" not in (r.texto or "") and "recurso_oferecido" not in _do_agente(r)
    assert "recurso_sem_oferta" not in _do_agente(r)


async def test_e2e_o_modelo_que_ja_ofereceu_nao_duplica(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    api.roteiro = [
        _usa("ver_anuncio", item_id=ITEM_A17),
        _usa("buscar_anuncios_da_loja", termo="NFC"),
        _usa("ver_anuncio", item_id=ITEM_A18),
        _final_e2e(
            texto=("Este não tem NFC, mas temos o Uranyx A18 Pro Max, que tem: {link_anuncio:a}"),
            assunto="espec_celular",
            confianca=0.9,
            evidencias=[f"ficha:{ITEM_A17}", f"anuncio:{ITEM_A18}", f"ficha:{ITEM_A18}"],
        ),
    ]

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto.count("Temos") + r.texto.count("temos") == 1, r.texto
    assert "recurso_oferecido" not in _do_agente(r)


async def test_e2e_o_modelo_ja_buscou_e_nao_ofereceu_o_codigo_oferece(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    # A busca repetida não roda de novo (`consulta_repetida`): o código lê o que está no `ctx`.
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    api.roteiro = [
        _usa("ver_anuncio", item_id=ITEM_A17),
        _usa("buscar_anuncios_da_loja", termo="NFC"),
        _final_e2e(
            texto="O A17 Pro Max não tem NFC.",
            assunto="espec_celular",
            confianca=0.9,
            evidencias=[f"ficha:{ITEM_A17}"],
        ),
    ]

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert _do_agente(r)["recurso_oferecido"]["anuncios"] == [ITEM_A18], r.motivo
    assert URL in r.texto


async def test_e2e_a_ficha_que_contradiz_fica_de_fora(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, integ, r_mem = await _nfc(db, make_user, monkeypatch)
    _ficha(
        r_mem,
        integ,
        ITEM_A18,
        "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium",
        "Versão 16GB: com NFC. Versão 12GB: sem NFC.",
    )
    _modelo(api)

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == "O A17 Pro Max não tem NFC.", r.motivo
    assert _do_agente(r)["recurso_sem_oferta"] == {"recurso": "nfc", "porque": "sem_anuncio"}
    assert [e["texto"] for e in envios] == [r.texto], r.motivo


async def test_e2e_a_frase_antes_do_fecho_com_o_link(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    _modelo(api, texto="Este aparelho não tem NFC. Qualquer dúvida, estamos à disposição!")

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == (
        f"Este aparelho não tem NFC. Temos o Uranyx A18 Pro Max, que tem esse recurso: {URL}. "
        "Qualquer dúvida, estamos à disposição!"
    ), r.motivo
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


async def test_e2e_no_copiloto_a_oferta_fica_no_rascunho_e_nao_sai(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    await _ligar(db, conversa, modo=constantes.MODO_COPILOTO)
    _modelo(api)

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert envios == [] and _decisao(r) != "enviado"
    assert "que tem esse recurso" in (r.texto or ""), r.motivo
    assert _do_agente(r)["recurso_oferecido"]["anuncios"] == [ITEM_A18]


async def _pergunta_no_ml(db: AsyncSession, make_user, monkeypatch) -> Any:
    r_mem = RedisMem()
    monkeypatch.setattr(ficha_svc, "redis", r_mem)
    await _importar_manual_base(db)
    conversa = await _conversa_ia(
        db,
        make_user,
        plataforma="ml",
        canal="pergunta",
        platform=IntegrationPlatform.ML,
        pedido=None,
    )
    conversa.anuncio_id = ML_A17
    conversa.anuncio_titulo = "Uranyx A17 Pro Max 128 Gb Memoria 12 Gb Ram"
    integ = await db.get(Integration, conversa.integration_id)
    ml = {"status": "active", "plataforma": IntegrationPlatform.ML}
    await _link(db, integ, ML_A17, "1", "Uranyx A17 Pro Max 128 Gb Memoria 12 Gb Ram", **ml)
    await _link(db, integ, ML_S5, "1", "Uranyx S5 128 Gb Memoria 16 Gb Ram Nfc Laranja", **ml)
    # O pausado (34% dos vínculos vivos do ML) fica de fora.
    await _link(
        db,
        integ,
        "MLB1000000003",
        "1",
        "Uranyx S7 12.256 Gb Memoria Nfc",
        status="paused",
        plataforma=IntegrationPlatform.ML,
    )
    for item, titulo, descricao in (
        (ML_A17, "Uranyx A17 Pro Max 128 Gb Memoria 12 Gb Ram", "Celular. Não tem NFC."),
        (ML_S5, "Uranyx S5 128 Gb Memoria 16 Gb Ram Nfc Laranja", "Celular com NFC."),
    ):
        r_mem.dados[ficha_svc.chave_ficha("ml", integ.id, item)] = json.dumps(
            {
                "titulo": titulo,
                "descricao": descricao,
                "raw": {"id": item},
                "buscada_em": datetime.now(UTC).isoformat(),
            }
        )
    await _canal_real(db, conversa)
    await _msg(db, conversa, "Esse tem NFC?", ha=timedelta(minutes=6))
    await db.commit()
    return conversa


async def test_e2e_na_pergunta_do_ml_sem_link_e_pelo_nome(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa = await _pergunta_no_ml(db, make_user, monkeypatch)
    api.roteiro = [
        _usa("ver_anuncio", item_id=ML_A17),
        _final_e2e(
            texto="Este aparelho não tem NFC.",
            assunto="espec_celular",
            confianca=0.9,
            evidencias=[f"ficha:{ML_A17}"],
        ),
    ]

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == (
        "Este aparelho não tem NFC. Temos o Uranyx S5, que tem esse recurso; aparece na página "
        "da loja no app, pelo nome."
    ), (r.motivo, r.validador_erros)
    agente_ = _do_agente(r)
    assert agente_["recurso_oferecido"] == {"recurso": "nfc", "anuncios": [ML_S5]}
    assert "links_anuncio" not in agente_
    assert validador._fora_do_latin1(r.texto) == []
    assert agente_["sem_auto"] == [], agente_["sem_auto"]


async def test_e2e_so_uma_versao_tem_e_a_frase_fala_da_versao(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, integ, r_mem = await _nfc(db, make_user, monkeypatch)
    titulo = "Uranyx P1 Pro Max 12.64 / 16.128 GB Memoria NFC"
    await _link(db, integ, ITEM_VARIOS, "1", f"{titulo} - Laranja,16 GB / 128 GB Premium NFC")
    await _link(db, integ, ITEM_VARIOS, "2", f"{titulo} - Laranja,12 GB / 64 GB")
    _ficha(r_mem, integ, ITEM_VARIOS, titulo, "Celular. A versão Premium tem NFC.")
    _modelo(api)

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert "Temos também o Uranyx P1 Pro Max na versão que tem esse recurso:" in r.texto, r.motivo
    assert ITEM_VARIOS in _do_agente(r)["recurso_oferecido"]["anuncios"]
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


async def test_e2e_o_5g_com_o_link(db, make_user, agente_ligado, api, envios, monkeypatch):
    conversa, integ, r_mem = await _nfc(db, make_user, monkeypatch, pergunta="Esse pega 5G?")
    titulo = "Uranyx F112 Pro 5g 256 Gb Memoria 24gb Resistente"
    await _link(db, integ, ITEM_F112, "1", titulo)
    _ficha(r_mem, integ, ITEM_F112, titulo, "Celular com rede 5G.")
    _modelo(api, texto="O A17 Pro Max não tem 5G.")

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert "O A17 Pro Max não tem 5G. Temos o Uranyx F112 Pro, que tem esse recurso: " in r.texto, (
        r.motivo,
        r.validador_erros,
    )
    assert _do_agente(r)["recurso_oferecido"] == {"recurso": "5g", "anuncios": [ITEM_F112]}
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


async def test_e2e_a_mala_expansivel_com_o_artigo(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, integ, r_mem = await _nfc(
        db, make_user, monkeypatch, pergunta="Essa mala tem zíper expansivo?"
    )
    conversa.anuncio_id = "55500000001"
    conversa.anuncio_titulo = "Mala De Bordo 20 Polegadas 10kg ABS - Preto"
    await _link(db, integ, "55500000001", "1", conversa.anuncio_titulo)
    _ficha(r_mem, integ, "55500000001", conversa.anuncio_titulo, "Mala de bordo rígida.")
    titulo = "Mala De Viagem Bordo 20 Polegadas Expansível P7 - Preto"
    await _link(db, integ, "55500000002", "1", titulo)
    _ficha(r_mem, integ, "55500000002", titulo, "Mala com zíper expansível.")
    await db.commit()
    _modelo(
        api, texto="Esta mala não tem zíper expansivo.", item="55500000001", assunto="espec_mala"
    )

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto.startswith(
        "Esta mala não tem zíper expansivo. Temos a Mala De Viagem Bordo P7, que tem esse recurso: "
    ), (r.motivo, r.validador_erros, r.texto)
    assert _do_agente(r)["recurso_oferecido"]["recurso"] == "ziper_expansivo"
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


async def test_e2e_erro_no_catalogo_mantem_o_texto(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    _modelo(api)

    async def _quebra(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("o banco caiu")

    monkeypatch.setattr(catalogo, "vinculos_da_loja", _quebra)
    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == "O A17 Pro Max não tem NFC.", r.motivo
    assert _do_agente(r)["recurso_sem_oferta"] == {"recurso": "nfc", "porque": "erro"}
    assert [e["texto"] for e in envios] == [r.texto], r.motivo


async def test_e2e_o_prazo_da_leitura_nao_abre_outra_ficha(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    _modelo(api)
    monkeypatch.setattr(agente, "PRAZO_DO_RECURSO_S", 0.0)

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == "O A17 Pro Max não tem NFC.", r.motivo
    assert _do_agente(r)["recurso_sem_oferta"]["porque"] == "sem_anuncio"
    assert [e["texto"] for e in envios] == [r.texto], r.motivo


async def test_e2e_o_mesmo_modelo_inteiro_nao_e_oferecido(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    # "Este não tem" e "temos o mesmo, que tem" se desmentem: o C68 de 128 não tem, o de 512 tem.
    conversa, integ, r_mem = await _nfc(db, make_user, monkeypatch)
    conversa.anuncio_titulo = "Uranyx C68 Plus 16.128 Gb Memoria - Laranja"
    conversa.anuncio_id = ITEM_S7
    await _link(db, integ, ITEM_S7, "1", "Uranyx C68 Plus 16.128 Gb Memoria - Laranja")
    _ficha(r_mem, integ, ITEM_S7, conversa.anuncio_titulo, "Celular com 2 chips. Sem NFC.")
    await db.execute(ProductLink.__table__.delete().where(ProductLink.external_id == ITEM_A18))
    await _link(db, integ, ITEM_VARIOS, "1", "Uranyx C68 Plus 24.512 Gb Memoria NFC - Laranja")
    _ficha(r_mem, integ, ITEM_VARIOS, "Uranyx C68 Plus 24.512 Gb Memoria NFC", "Celular com NFC.")
    await db.commit()
    _modelo(api, texto="Este aparelho não tem NFC.", item=ITEM_S7)

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == "Este aparelho não tem NFC.", r.motivo
    assert _do_agente(r)["recurso_sem_oferta"]["porque"] == "sem_anuncio"


async def test_e2e_a_1a_camada_que_segura_a_oferta_volta_ao_texto_do_modelo(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    # Uma trava que só o texto com a oferta aciona: a resposta certa do modelo não pode cair.
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    _modelo(api)
    real = checagens.conferir_pix_ou_banco

    def _trava(texto: str) -> list:
        if "Temos o" in texto:
            return [checagens._pessoa(checagens.PIX_OU_BANCO, checagens.MOTIVO_PIX_OU_BANCO)]
        return real(texto)

    monkeypatch.setattr(checagens, "conferir_pix_ou_banco", _trava)
    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == "O A17 Pro Max não tem NFC.", r.motivo
    agente_ = _do_agente(r)
    assert agente_["recurso_sem_oferta"] == {"recurso": "nfc", "porque": "a_1a_camada_segurou"}
    # O `ctx` voltou: nem a ficha do outro anúncio nem o link ficam.
    assert f"ficha:{ITEM_A18}" not in agente_["evidencias"]
    assert "links_anuncio" not in agente_
    assert [e["texto"] for e in envios] == [r.texto], r.motivo


async def test_e2e_o_texto_do_modelo_segurado_nao_ganha_oferta(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    # O que o código segura no texto do modelo continua segurado, e nem procura na loja.
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    _modelo(api, texto="O A17 Pro Max não tem NFC. Faça um Pix que a gente resolve.")

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert envios == [] and _decisao(r) != "enviado"
    agente_ = _do_agente(r)
    assert "recurso_oferecido" not in agente_ and "recurso_sem_oferta" not in agente_
    assert "buscar_anuncios_da_loja" not in {p["ferramenta"] for p in agente_["rastro"]}


async def test_e2e_o_nao_do_verificador_a_oferta_volta_ao_texto_do_modelo(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    _modelo(api)
    # O "não" ao texto com a oferta; o "ok" ao texto do modelo.
    api.vereditos = [_do_verificador(**NAO_DO_VERIFICADOR), _do_verificador()]

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == "O A17 Pro Max não tem NFC.", r.motivo
    assert len(api.verificador) == 2
    agente_ = _do_agente(r)
    assert "recurso_oferecido" not in agente_
    assert agente_["recurso_sem_oferta"]["porque"] == "verificador"
    assert agente_["recurso_sem_oferta"]["anuncios"] == [ITEM_A18]
    assert f"ficha:{ITEM_A18}" not in agente_["evidencias"]
    assert "links_anuncio" not in agente_
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


async def test_e2e_o_nao_do_verificador_ao_modelo_continua_valendo(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    _modelo(api)
    api.vereditos = [_do_verificador(**NAO_DO_VERIFICADOR), _do_verificador(**NAO_DO_VERIFICADOR)]

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert envios == [] and _decisao(r) != "enviado"
    assert "Temos o" not in (r.texto or "")
    assert len(api.verificador) == 2


async def test_e2e_o_resto_do_texto_fica_antes_da_oferta(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    _modelo(api, texto="Olá! Este aparelho não tem NFC. Ele tem 2 chips.")

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == (
        "Olá! Este aparelho não tem NFC. Ele tem 2 chips. Quanto ao NFC, temos o Uranyx A18 Pro "
        f"Max, que tem esse recurso: {URL}"
    ), (r.motivo, r.validador_erros)
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


async def test_e2e_o_titulo_desmentido_nao_e_oferecido_na_pergunta_do_ml(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa = await _pergunta_no_ml(db, make_user, monkeypatch)
    integ = await db.get(Integration, conversa.integration_id)
    ml = {"status": "active", "plataforma": IntegrationPlatform.ML}
    await _link(db, integ, "MLB1000000007", "1", F105, **ml)
    await db.execute(ProductLink.__table__.delete().where(ProductLink.external_id == ML_S5))
    await db.commit()
    api.roteiro = [
        _usa("ver_anuncio", item_id=ML_A17),
        _final_e2e(
            texto="Este aparelho não tem NFC.",
            assunto="espec_celular",
            confianca=0.9,
            evidencias=[f"ficha:{ML_A17}"],
        ),
    ]

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == "Este aparelho não tem NFC.", r.motivo
    assert _do_agente(r)["recurso_sem_oferta"] == {"recurso": "nfc", "porque": "sem_anuncio"}


async def test_e2e_a_mala_do_mesmo_codigo_nao_e_oferecida(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, integ, r_mem = await _nfc(
        db, make_user, monkeypatch, pergunta="Essa mala tem zíper expansivo?"
    )
    conversa.anuncio_id = "55500000001"
    conversa.anuncio_titulo = "Mala Executiva De Bordo 20 Polegadas 10Kg ME1 - Preto"
    await _link(db, integ, "55500000001", "1", conversa.anuncio_titulo)
    _ficha(r_mem, integ, "55500000001", conversa.anuncio_titulo, "Mala de bordo rígida.")
    titulo = "Mala De Viagem 20 Polegadas + Mochila Executiva 10Kg Bordo ME1 Expansível"
    await _link(db, integ, "55500000002", "1", titulo)
    _ficha(r_mem, integ, "55500000002", titulo, "Mala de bordo.")
    await db.commit()
    _modelo(
        api, texto="Esta mala não tem zíper expansivo.", item="55500000001", assunto="espec_mala"
    )

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert r.texto == "Esta mala não tem zíper expansivo.", r.motivo
    assert _do_agente(r)["recurso_sem_oferta"]["porque"] == "sem_anuncio"


async def test_e2e_a_frase_sem_lastro_cortada_nao_ganha_oferta_depois(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    # A 1ª passada não oferece (a 2ª oração não é negativa). O corte deixa só a negativa, que
    # acionaria: o "não" a essa oferta levaria o resto junto, para pessoa.
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    frase = "A descrição do anúncio fala de NFC só no título."
    _modelo(api, texto=f"O A17 Pro Max não tem NFC. {frase}")
    api.vereditos = [
        _do_verificador(ok_para_envio_automatico=False, afirma_sem_lastro=[frase]),
        _do_verificador(),
    ]

    r = await ia_svc.gerar_rascunho(db, conversa)

    assert [e["texto"] for e in envios] == ["O A17 Pro Max não tem NFC."], r.motivo
    assert len(api.verificador) == 2
    assert "esse recurso" not in api.verificador[1]["messages"][0]["content"]
    assert "recurso_oferecido" not in _do_agente(r)


async def test_e2e_a_oferta_nao_muda_a_marca_nem_os_avisos_da_resposta(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    _modelo(api)
    com = await ia_svc.gerar_rascunho(db, conversa)
    agente_com = _do_agente(com)

    assert "que tem esse recurso" in com.texto, com.motivo
    assert agente_com.get("produto_pelo_anuncio") is True, agente_com
    assert not agente_com.get("avisos"), agente_com.get("avisos")
    entrada = api.verificador[0]["messages"][0]["content"]
    assert "pessoa confere" not in entrada
    assert "há mais de um anúncio na conversa" not in entrada
    assert "sem a marca do anúncio da conversa" not in entrada


async def test_o_caminho_sem_banco_e_o_replay_nao_leem_o_catalogo(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    # O re-gatilho offline é "determinístico, sem chave", e o replay grava o texto do modelo.
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    _modelo(api)
    buscas: list[str] = []
    busca_real = ferramentas.buscar_pelo_codigo

    async def _conta(ctx: Any, termo: str, **kw: Any) -> Any:
        buscas.append(termo)
        return await busca_real(ctx, termo, **kw)

    monkeypatch.setattr(ferramentas, "buscar_pelo_codigo", _conta)
    real = agente.conferir_proposta
    vistos: list[tuple[int, Any]] = []

    async def _espia(session, conversa_, gatilho, base, ctx, p, pac, **kw):
        if not vistos:
            await real(None, conversa_, gatilho, base, ctx, p, pac, forcar_conferencia=True)
            vistos.append((len(buscas), ctx.recurso_oferecido))
            ctx.modo_replay = True
            await real(session, conversa_, gatilho, base, ctx, p, pac, **kw)
            ctx.modo_replay = False
            vistos.append((len(buscas), ctx.recurso_oferecido))
        return await real(session, conversa_, gatilho, base, ctx, p, pac, **kw)

    monkeypatch.setattr(agente, "conferir_proposta", _espia)
    r = await ia_svc.gerar_rascunho(db, conversa)

    assert vistos == [(0, None), (0, None)]
    # Fora desses dois, a oferta continua.
    assert buscas and "que tem esse recurso" in r.texto, r.motivo


async def test_o_caminho_das_ferramentas_registra_e_nao_repete(
    db: AsyncSession, make_user, monkeypatch
):
    conversa, _integ, _r = await _nfc(db, make_user, monkeypatch)
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    (busca,) = await ferramentas.buscar_pelo_codigo(ctx, "NFC", base=base, conversa=conversa)
    assert [a["item_id"] for a in busca["anuncios"]] == [ITEM_A18]
    assert contexto.id_anuncio(ITEM_A18) in ctx.registro.ids
    # A repetida não roda, e o achado continua vindo do `ctx`.
    passos = len(ctx.rastro)
    (de_novo,) = await ferramentas.buscar_pelo_codigo(ctx, "NFC", base=base, conversa=conversa)
    assert de_novo["anuncios"] == busca["anuncios"] and len(ctx.rastro) == passos
    dado = await ferramentas.ver_pelo_codigo(ctx, ITEM_A18, base=base, conversa=conversa)
    assert dado["item_id"] == ITEM_A18 and contexto.id_ficha(ITEM_A18) in ctx.registro.ids
    assert any(f["item_id"] == ITEM_A18 for f in ctx.fichas_lidas)


def test_nenhum_nome_novo_sobrescreve_outro_do_modulo():
    # O `_NEGA_ANTES` de uma 1ª versão daqui sobrescreveu o regex da `conferir_regiao`.
    with open(checagens.__file__, encoding="utf-8") as arquivo:
        arvore = ast.parse(arquivo.read())
    nomes: collections.Counter[str] = collections.Counter()
    for no in arvore.body:
        if isinstance(no, ast.Assign):
            nomes.update(t.id for t in no.targets if isinstance(t, ast.Name))
        elif isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name):
            nomes[no.target.id] += 1
        elif isinstance(no, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            nomes[no.name] += 1
    novos = {n for n in nomes if "recurso" in n.lower() or n == "tipo_do_titulo"}
    assert novos and all(nomes[n] == 1 for n in novos)
    assert not isinstance(checagens._NEGA_ANTES, str)


def test_nao_mudou_o_que_o_modelo_ve():
    # Nada aqui toca o bloco 1 nem o prefixo: a frase não está no prompt nem nas ferramentas.
    assert "que tem esse recurso" not in ferramentas.DESCRICOES[ferramentas.BUSCAR_ANUNCIOS_DA_LOJA]
