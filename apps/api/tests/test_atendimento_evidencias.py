"""Afirmações comerciais precisam de prova da conversa, não da confiança da IA."""

import pytest

from app.services.atendimento.evidencias import conferir_afirmacoes, motivos_sem_evidencia


def _fatos(titulo="Apple Watch Series 11 - 42mm ou 46mm - 1 Ano de Garantia Apple", **ficha):
    return {
        "anuncio": None,
        "ficha_do_anuncio": [
            {
                "de_onde": "cartão de produto que o cliente mandou",
                "fonte": "loja (Shopee), agora",
                "titulo": titulo,
                "atributos": ["Marca: Apple"],
                **ficha,
            }
        ],
    }


def _codigos(resposta, fatos=None):
    return {p.codigo for p in conferir_afirmacoes(resposta, fatos or {})}


def test_resposta_real_35beedd5_nao_se_aprova_por_marca_ou_fala_do_comprador():
    resposta = (
        "Olá! O Apple Watch anunciado é original da Apple, novo, lacrado e na versão "
        "americana, conforme consta no título do anúncio."
    )
    fatos = _fatos()
    fatos["comprador"] = "Vocês falaram que é versão americana, novo, lacrado e original."
    fatos["exemplos"] = [{"resposta": "É novo, original, lacrado e americano."}]
    assert _codigos(resposta, fatos) == {
        "produto_original_sem_evidencia",
        "produto_novo_sem_evidencia",
        "produto_lacrado_sem_evidencia",
        "produto_versao_americana_sem_evidencia",
    }


def test_resposta_real_ea25622d_condicao_nao_esta_no_titulo():
    resposta = (
        "Olá! Sim, o Apple Watch Series 11 anunciado é original, novo e lacrado, "
        "conforme descrito no título do anúncio."
    )
    assert _codigos(resposta, _fatos()) == {
        "produto_original_sem_evidencia",
        "produto_novo_sem_evidencia",
        "produto_lacrado_sem_evidencia",
    }


@pytest.mark.parametrize(
    "titulo,resposta",
    [
        (
            "Apple Watch Original Novo Lacrado Versão Americana",
            "É original, novo e lacrado, americano.",
        ),
        (
            "Relógios ORIGINAIS NOVOS LACRADOS AMERICANOS",
            "Os relógios são originais, novos e lacrados.",
        ),
        ("Ｒｅｌóｇｉｏ ＯＲＩＧＩＮＡＬ ＮＯＶＯ ＬＡＣＲＡＤＯ", "É original, novo e lacrado."),
        ("Apple Watch NOVO\u00a0LACRADO ORIGINAL", "O produto é novo, original e lacrado."),
    ],
)
def test_titulo_da_propria_conversa_comprova_condicao(titulo, resposta):
    assert conferir_afirmacoes(resposta, _fatos(titulo)) == ()


@pytest.mark.parametrize(
    "fatos",
    [
        {"anuncio": "Apple Watch Original Novo Lacrado Versão Americana"},
        _fatos("Rádio Original Novo Lacrado", atributos=["Versão: Americana"]),
        _fatos(
            "Rádio Original Novo Lacrado",
            atributos=[],
            descricao="Este modelo é da versão americana.",
        ),
        _fatos("Rádio Original Novo Lacrado", atributos=["Região: EUA"]),
    ],
)
def test_versao_pode_vir_da_ficha_autorizada_e_condicao_do_titulo(fatos):
    assert motivos_sem_evidencia("É original, novo, lacrado e da versão americana.", fatos) == []


def test_descricao_e_atributos_nao_substituem_titulo_para_condicao():
    fatos = _fatos(
        descricao="Produto original, novo e lacrado.",
        atributos=["Condição: Novo", "Produto: Original", "Embalagem: Lacrado"],
    )
    assert len(conferir_afirmacoes("É original, novo e lacrado.", fatos)) == 3


@pytest.mark.parametrize(
    "ficha",
    [
        {"atributos": ["Versão: Americana"]},
        {"descricao": "Este modelo é da versão americana."},
        {"atributos": ["Região: EUA"]},
    ],
)
def test_apple_versao_so_com_titulo_conforme_manual(ficha):
    fatos = _fatos("Apple Watch Original Novo Lacrado", **ficha)
    assert _codigos("É original, novo, lacrado e versão americana.", fatos) == {
        "produto_versao_americana_sem_evidencia"
    }


def test_outro_anuncio_nao_comprova_versao_ou_condicao():
    fatos = _fatos(
        "Original Novo Lacrado Versão Americana",
        fonte="outro anúncio do mesmo produto (Mercado Livre)",
        descricao="Versão americana.",
    )
    assert len(conferir_afirmacoes("É original, novo, lacrado e versão americana.", fatos)) == 4


def test_segunda_ficha_nao_comprova_condicao_de_outro_produto():
    fatos = _fatos()
    fatos["ficha_do_anuncio"].append(
        _fatos("Original Novo Lacrado Versão Americana")["ficha_do_anuncio"][0]
    )
    assert len(conferir_afirmacoes("É original, novo, lacrado e versão americana.", fatos)) == 4


@pytest.mark.parametrize(
    "resposta",
    [
        "Olá! Como podemos ajudar você?",
        "Conte sua dúvida sobre a originalidade do produto.",
        "Você quer saber se é original?",
        "Sua pergunta foi: ‘É novo e lacrado?’. Pode explicar sua dúvida?",
        "Não posso confirmar se é original, novo ou lacrado.",
        "Você quer saber se é original, novo ou lacrado?",
        "Não sabemos se é a versão americana.",
        "Não temos informações sobre ser novo ou lacrado.",
        "Não garantimos que seja novo.",
        "A equipe precisa confirmar se o produto é novo.",
        "Se for novo, conte sua dúvida.",
        "Se tiver uma nova dúvida, envie uma nova mensagem.",
        "Podemos ajudar de novo em seu novo pedido.",
        "Consulte a mensagem original da conversa.",
        "Não tenho a confirmação de que seja lacrado.",
        "Você quer saber se o produto é original",
        "Conte qual é sua dúvida sobre ser novo ou lacrado.",
        "Não está confirmado que seja novo.",
        "Não há indicação de que seja original.",
        "A condição de novo não está confirmada.",
    ],
)
def test_pergunta_hipotese_desconhecimento_e_novo_contato_nao_sao_afirmacoes(resposta):
    assert motivos_sem_evidencia(resposta, {}) == []


@pytest.mark.parametrize(
    "resposta",
    [
        "O relógio não é original.",
        "O produto não é novo.",
        "Não é lacrado.",
        "Não é da versão americana.",
        "É original? Sim, é.",
        "Não posso confirmar a versão, mas é novo e lacrado.",
        "Não sei se é original e o aparelho é novo.",
    ],
)
def test_negacao_e_afirmacao_depois_de_ressalva_exigem_prova(resposta):
    assert conferir_afirmacoes(resposta, {})


@pytest.mark.parametrize("campo", ["original", "novo", "lacrado"])
def test_negacao_na_fonte_nao_libera_afirmacao_positiva(campo):
    fatos = {"anuncio": f"Apple Watch não é {campo}"}
    assert conferir_afirmacoes(f"É {campo}.", fatos)
    assert conferir_afirmacoes(f"Não é {campo}.", fatos) == ()


def test_titulo_negativo_em_forma_de_atributo():
    fatos = {"anuncio": "Produto - Original: não"}
    assert conferir_afirmacoes("Não é original.", fatos) == ()
    assert conferir_afirmacoes("É original.", fatos)


@pytest.mark.parametrize(
    "titulo,negativa,positiva",
    [
        ("Não é da versão americana", "Não é da versão americana.", "É versão americana."),
        ("Não é vendido como original", "Não é original.", "É original."),
        ("Não é novo, lacrado ou original", "Não é lacrado.", "É lacrado."),
    ],
)
def test_negacao_na_fonte_nao_vira_prova_positiva(titulo, negativa, positiva):
    assert conferir_afirmacoes(negativa, {"anuncio": titulo}) == ()
    assert conferir_afirmacoes(positiva, {"anuncio": titulo})


def test_tomada_ou_garantia_americana_nao_comprovam_versao_do_produto():
    fatos = _fatos(descricao="A tomada americana acompanha o relógio. Garantia americana.")
    assert conferir_afirmacoes("É versão americana.", fatos)


def test_evidencia_divergente_vai_para_pessoa():
    fatos = _fatos("Apple Watch não é novo")
    fatos["anuncio"] = "Apple Watch novo"
    assert conferir_afirmacoes("É novo.", fatos)
    assert conferir_afirmacoes("Não é novo.", fatos)


def test_negacao_sobre_outra_coisa_nao_inverte_condicao():
    assert conferir_afirmacoes("Não tem riscos e é novo.", {"anuncio": "Produto Novo"}) == ()
    assert conferir_afirmacoes("Não é apenas novo; é lacrado.", {"anuncio": "Novo Lacrado"}) == ()


def test_motivos_sao_estaveis_e_nao_copiam_texto_do_comprador():
    motivos = motivos_sem_evidencia("É novo.", {"comprador": "DADO PRIVADO: novo"})
    assert motivos == ["condição de novo sem confirmação no título do anúncio"]


@pytest.mark.parametrize(
    "resposta",
    [
        "É original? Com certeza!",
        "É original? Pode confiar.",
        "É novo? Sem dúvida!",
        "É lacrado? Claro que é.",
        "É versão americana? Pode ficar tranquilo!",
        "É original? 100%!",
        "É original? Não.",
    ],
)
def test_confirmacao_retorica_tambem_exige_prova(resposta):
    assert conferir_afirmacoes(resposta, {})


def test_resposta_curta_negativa_nao_vira_afirmacao_positiva():
    assert conferir_afirmacoes("É original? Não.", {"anuncio": "Não é original"}) == ()
    assert conferir_afirmacoes("É original? Não.", {"anuncio": "Produto original"})
    assert conferir_afirmacoes("É original? Não sabemos.", {}) == ()
    assert conferir_afirmacoes("É original? Com certeza vamos verificar.", {}) == ()
