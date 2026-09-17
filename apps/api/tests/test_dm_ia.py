"""O cérebro da DM: o que ele NÃO deixa sair, e o que não sai daqui.

Estes testes não medem o modelo — medem a trava. O modelo pode escrever o que
quiser; o que decide se a mensagem sai é o `validar`, e ele roda em código.

A lista de proibidos é a mesma da ferramenta do Claude, pelo mesmo motivo:
preço, prazo e frete ditos em canal de atendimento vinculam o fornecedor
(CDC art. 30 e 35), e "foi o robô" não é defesa (art. 34).
"""

from __future__ import annotations

import pytest

from app.services.dm_ia import mascarar, validar

# ─────────────── o que o robô não pode dizer ───────────────


@pytest.mark.parametrize(
    "texto",
    [
        "Essa mala sai por R$ 299,00",
        "Fica 299,90 à vista",
        "Estamos com 15% de desconto",
        "Chega em 5 dias úteis",
        "Chega até sexta",
        "O frete é grátis pro Brasil todo",
        "O frete fica por nossa conta",
        "A garantia é de 90 dias",
        "O prazo de envio é de 2 semanas",
    ],
)
def test_recusa_o_que_vincula(texto: str):
    motivo = validar(texto)
    assert motivo is not None, f"deixou passar: {texto!r}"


@pytest.mark.parametrize(
    "texto",
    [
        "Oi! Nossas malas têm casco rígido e fechadura TSA.",
        "O 20\" é a nossa mala de bordo. Pra confirmar a medida da sua "
        "companhia aérea, chama no WhatsApp.",
        "As cores mudam de um modelo pro outro. Me diz qual você viu que o "
        "time confirma.",
        "Quem confirma valor é o time — chama no WhatsApp que eles te passam.",
    ],
)
def test_deixa_passar_resposta_boa(texto: str):
    assert validar(texto) is None, f"barrou sem motivo: {texto!r}"


def test_recusa_vazio():
    assert validar("") is not None
    assert validar("   ") is not None


def test_recusa_longo_demais():
    # 600 caracteres acentuados = 1200 bytes, acima do teto da plataforma.
    motivo = validar("ã" * 600)
    assert motivo is not None
    assert "bytes" in motivo


# ─────────────── o que não sai do servidor ───────────────


def test_mascara_dado_pessoal():
    """O modelo precisa da intenção, não do CPF.

    Tudo isso chega em DM de e-commerce o tempo todo, e nada disso precisa
    atravessar a fronteira pra alguém escrever "chama no WhatsApp".
    """
    bruto = (
        "meu cpf é 123.456.789-00, telefone (11) 98765-4321, "
        "email joao@exemplo.com.br, cep 01310-100, "
        "cartão 4111 1111 1111 1111"
    )
    limpo = mascarar(bruto)

    assert "123.456.789-00" not in limpo
    assert "98765-4321" not in limpo
    assert "joao@exemplo.com.br" not in limpo
    assert "01310-100" not in limpo
    assert "4111" not in limpo

    assert "[CPF]" in limpo
    assert "[TELEFONE]" in limpo
    assert "[EMAIL]" in limpo


def test_mascara_nao_estraga_texto_normal():
    bruto = "essa mala de 20 polegadas cabe na cabine?"
    assert mascarar(bruto) == bruto


def test_cpf_sem_pontuacao_tambem_e_mascarado():
    assert "[CPF]" in mascarar("cpf 12345678900")


# ─────────────── acabamento ───────────────


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("11930000710", "(11) 93000-0710"),
        ("1930000710", "(19) 3000-0710"),
        ("", ""),
        (None, ""),
        ("11 93000-0710", "(11) 93000-0710"),
    ],
)
def test_formata_telefone(bruto, esperado):
    """Telefone cru no meio da frase parece erro, e a pessoa desconfia."""
    from app.services.dm_ia import formatar_fone

    assert formatar_fone(bruto) == esperado
