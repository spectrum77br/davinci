"""A especificação real do produto na legenda (services/marketing/ficha.py).

Eduardo, 24/09/2026: "a descrição está muito genérica". E estava, por um
motivo estrutural: 9 modelos de legenda, todos de MARCA, nenhum de produto —
então dois ou três textos giravam entre todos os vídeos, e o único dado de
produto que entrava era o nome.

A informação existia: 738 anúncios com descrição escrita pelo próprio time.
Este módulo extrai o que casa limpo e monta UMA FRASE.

A decisão que estes testes defendem: FRASE PRONTA, não número solto. O sandbox
do template bloqueia `{% if %}` de propósito (sem isso `{{ 'x' * 10**9 }}`
trava o servidor), então não existe condicional — e um `{{ bateria }}` vazio
publicaria "Bateria de  que aguenta o dia" na conta da marca. Carregando a
frase inteira, produto sem ficha simplesmente não rende frase.
"""

import pytest
from sqlalchemy import select

from app.services.marketing.ficha import atributos, destaque

REAL = (
    "Uranyx Fossibot F109S Memoria Ram 24 GB Tela 6.7 Polegadas IPS "
    "Processador Dimensity 6300 Câmera traseira 50MP Câmera frontal 8MP "
    "Bateria 10600 mAh IP68 NFC 5G Desbloqueio Impressão digital"
)


def test_extrai_a_especificacao_de_uma_descricao_real():
    d = destaque(REAL)
    assert d == "Bateria de 10.600 mAh, tela de 6.7” e câmera de 50 MP."


def test_a_unidade_mantem_a_caixa():
    """`.capitalize()` do Python abaixa todo o resto e estragava a unidade:
    "10.600 mAh" virava "mah", "50 MP" virava "mp". Ninguém revisa isso e
    todo mundo vê."""
    d = destaque(REAL)
    assert "mAh" in d and "MP" in d
    assert "mah" not in d and " mp" not in d


def test_produto_sem_ficha_devolve_VAZIO_e_nao_frase_quebrada():
    """Vazio é resposta legítima: o template fecha sem a frase. Dois dos 12
    produtos em produção não têm ficha legível, e a legenda deles sai inteira
    do mesmo jeito."""
    assert destaque("produto sem especificação nenhuma") == ""
    assert destaque(None) == ""
    assert destaque("") == ""


def test_numero_absurdo_e_descartado():
    """Celular com 500 mAh não existe — é erro de leitura do texto livre.
    Publicar número errado é pior que não publicar número."""
    assert destaque("Bateria 500 mAh") == ""
    assert "10.600" in destaque("Bateria 10600 mAh")


def test_no_maximo_tres_itens():
    """Mais que isso vira lista de supermercado e o leitor desiste. A legenda
    é pra vender, não pra catalogar."""
    d = destaque(REAL)
    assert d.count(",") + d.count(" e ") <= 3


def test_virgula_decimal_vira_ponto():
    assert "6.74" in destaque("Tela 6,74 Polegadas")


def test_selos_sem_numero_tambem_contam():
    achados = atributos("aparelho com IP68 e NFC")
    assert "resistente a água e poeira" in achados
    assert "NFC" in achados


# ---------- o catálogo de malas ----------

MALA = (
    "MALAS DE VIAGEM SORRISO Parte Externa: Estrutura com dupla chapa ABS "
    "Cadeado com segredo numérico Rodas 360 em silicone gel Alça retrátil "
    "Puxador Telescópio com trava Zíper com tratamento termico"
)


def test_le_caracteristica_de_mala_tambem():
    """O mesmo extrator serve os dois catálogos. Os termos saíram de uma
    contagem nas 1.591 descrições reais, não de palpite: ABS aparece 5.957
    vezes, rodas 360° e telescópico 5.010, cadeado com segredo 5.001."""
    d = destaque(MALA)
    assert "ABS" in d and "cadeado com segredo" in d


def test_mala_nao_vira_celular_e_vice_versa():
    """Misturar as regras dos dois catálogos não confunde: o que não casa fica
    de fora, que é a regra da casa aqui."""
    assert "mAh" not in destaque(MALA)
    assert "rodinhas" not in destaque(REAL)


def test_o_que_diferencia_a_mala_vem_do_NOME():
    """As 1.591 descrições de mala são o MESMO texto padrão do catálogo, então
    a frase sairia idêntica em todos os vídeos. O que separa um kit do outro
    está no nome — e vem primeiro, porque diferenciar vale mais que descrever.
    """
    seis = destaque(MALA, "Kit 6 Malas Sorriso M6 - Cinza")
    oito = destaque(MALA, "Kit 8 Malas - Prata")
    assert seis.startswith("Kit com 6 peças")
    assert oito.startswith("Kit com 8 peças")
    assert seis != oito, "dois kits diferentes não podem render a mesma frase"


def test_nome_sem_diferenciador_nao_atrapalha():
    assert destaque(MALA, "Mala Bordo P").startswith("Casco rígido")
    assert destaque(REAL, "Uranyx F109S 24.256 - Preto").startswith("Bateria")


# ---------- a guarda do vínculo errado ----------


@pytest.mark.asyncio
async def test_ficha_de_produto_de_OUTRA_marca_nao_entra_na_legenda(db, make_user):
    """Criativo e produto são vinculados à mão, e o vínculo erra.

    Em produção os criativos da charlots (malas) apontam para celulares da
    Uranyx. Sem esta guarda, a legenda de mala sairia com "Bateria de 11.000
    mAh, tela de 6.52” e câmera de 13 MP" — foi visto renderizado antes de
    subir, e é o tipo de coisa que ninguém revisa numa publicação de robô.

    Descasou, sai SEM a frase: legenda genérica é problema pequeno, ficha de
    outro produto no ar é problema grande.
    """
    from app.models import Marca, MarketingCreative, Product
    from app.services.marketing.legenda import _destaque_do_produto

    u = await make_user()
    m = Marca(nome="charlots", slug="charlots")
    db.add(m)
    await db.flush()
    # Produto de OUTRA marca, com ficha de celular.
    p = Product(name="Uranyx WP53 24.128 - Preto", sku="dg023.ra", user_id=u.id)
    db.add(p)
    await db.flush()
    c = MarketingCreative(modelo="video", marca="charlots", marca_id=m.id, product_id=p.id)
    db.add(c)
    await db.commit()

    assert await _destaque_do_produto(db, c) == "", "ficha de celular não entra em legenda de mala"


@pytest.mark.asyncio
async def test_produto_SEM_prefixo_de_marca_continua_valendo(db, make_user):
    """A primeira versão desta guarda exigia que o nome do produto COMEÇASSE
    com a marca do criativo — e quebrava a Charlots inteira: só os produtos da
    Uranyx carregam o prefixo ("Uranyx F109S"); os de mala não ("Mala Listrada
    tamanho 26"). Consertar um problema e criar outro maior.

    A regra certa é mais estreita: recusa só quando o nome começa com OUTRA
    marca cadastrada. Nome sem prefixo nenhum — que é o caso normal — passa.
    """
    from app.models import Marca, MarketingCreative, Product
    from app.services.marketing.legenda import _destaque_do_produto

    u = await make_user()
    db.add_all([Marca(nome="charlots", slug="charlots"), Marca(nome="uranyx", slug="uranyx")])
    await db.flush()
    m = (await db.execute(select(Marca).where(Marca.nome == "charlots"))).scalar_one()
    p = Product(name="Mala Listrada tamanho 26 - Mostarda", sku="b024.26", user_id=u.id)
    db.add(p)
    await db.flush()
    c = MarketingCreative(modelo="video", marca="charlots", marca_id=m.id, product_id=p.id)
    db.add(c)
    await db.commit()

    # Sem anúncio ligado não há frase, mas o importante é NÃO ter sido
    # recusado pela guarda — a chamada chega ao fim em vez de sair no começo.
    assert await _destaque_do_produto(db, c) == ""
