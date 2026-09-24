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

    # A guarda NÃO recusou: a chamada chegou ao fim e o tamanho saiu do nome,
    # mesmo sem anúncio ligado. Se a guarda tivesse barrado (como na primeira
    # versão, que exigia prefixo de marca), aqui viria vazio.
    assert await _destaque_do_produto(db, c) == "26 polegadas."


def test_mala_avulsa_se_diferencia_pelo_TAMANHO():
    """As 104 malas de 12 polegadas têm a MESMA descrição de anúncio (é o
    texto padrão do catálogo). O que separa uma da outra é o tamanho, que o
    nome guarda como "tamanho 12" / "tamanho 28"."""
    doze = destaque(MALA, "Mala Listrada M1 tamanho 12 - Preto")
    vinte = destaque(MALA, "Mala Chanfrada M3 tamanho 28 - Marrom")
    assert doze.startswith("12 polegadas")
    assert vinte.startswith("28 polegadas")
    assert doze != vinte


def test_codigo_interno_do_fornecedor_nao_vaza_pra_legenda():
    """O catálogo de malas guarda a referência do fornecedor no próprio nome:
    "Mala Listrada M1 tamanho 12 - Preto (DT - DTLG056 - DT02)". Sem limpeza,
    o "(DT - DTLG056 - DT02)" ia pro Instagram junto — visto renderizado antes
    de subir."""
    from app.services.marketing.legenda import nome_de_vitrine

    completo, _ = nome_de_vitrine(
        "Mala Listrada M1 tamanho 12 - Preto (DT - DTLG056 - DT02)", "charlots"
    )
    assert "DTLG056" not in completo and "(" not in completo
    assert completo == "Mala Listrada M1 tamanho 12 Preto"
    # E o nome sem parênteses continua intacto.
    assert nome_de_vitrine("Uranyx F109S 24.256 - Preto", "uranyx")[0] == "F109S 256 GB Preto"


# ── Um anúncio, várias respostas (24/09/2026, criativo do F110L).
#
# Medido em produção: dos 785 produtos com anúncio de celular, 113 saíam com
# a RAM da OUTRA versão, e o F110L 8.128 ia ao ar com "20 GB de RAM" e "NFC"
# — os do F110 Pro 5G, que vem antes no mesmo anúncio.

F110_DOIS_APARELHOS = """Fossibot F110 Pro 5G
Memoria Interna 128 GB
Memoria Ram 20 GB sendo (8 GB Fisica + 12 GB Virtual)
Tela 6.75 HD+
Camera Traseira 50 MP
Bateria 10.000 mAh
NFC
--------------------------
Fossibot F110 L

Memoria Interna 128 GB
Memoria Ram 8 GB sendo (4 GB Fisica + 4 GB Virtual)
Camera Traseira 50 MP
Bateria 10.000 mAh
Android 15
"""


def test_anuncio_com_dois_aparelhos_usa_o_bloco_do_nome():
    d = destaque(F110_DOIS_APARELHOS, "Uranyx F110L 8.128 - Preto")
    assert d == "Bateria de 10.000 mAh, câmera de 50 MP e 8 GB de RAM."
    assert "NFC" not in d and "20 GB" not in d


def test_modelo_seguido_de_qualificador_e_outro_aparelho():
    """"F110" não é o bloco do "F110 L" nem do "F110 Pro": sem bloco certo,
    vale só o que os dois dizem igual."""
    assert destaque(F110_DOIS_APARELHOS, "Fossibot F110 8.128") == (
        "Bateria de 10.000 mAh e câmera de 50 MP."
    )
    assert "20 GB de RAM" in destaque(F110_DOIS_APARELHOS, "Fossibot F110 Pro 5G 20.256")


def test_sem_nome_so_o_que_todos_os_blocos_concordam():
    assert destaque(F110_DOIS_APARELHOS) == "Bateria de 10.000 mAh e câmera de 50 MP."


def test_titulo_pode_vir_depois_do_aviso_da_caixa():
    """Muito anúncio abre com "Caixa Slim…" e só na linha seguinte diz o
    aparelho. E "Poco M7" não pode casar o bloco do "Poco M7 Pro 5G"."""
    texto = (
        "Caixa Slim, o aparelho acompanha apenas o cabo!\nPoco M7 + Fone U9\n"
        "Bateria 7000 mAh\nTela 6.9 polegadas\n"
        "------------------\n"
        "Caixa Slim, o aparelho acompanha apenas o cabo!\nPoco M7 Pro 5G + Fone U9\n"
        "Bateria 5110 mAh\nTela 6.67 polegadas\n"
    )
    assert destaque(texto, "Poco M7 12.128 - Preto") == "Bateria de 7.000 mAh e tela de 6.9”."
    assert destaque(texto, "Poco M7 Pro 5G 24.512 - Preto") == (
        "Bateria de 5.110 mAh, tela de 6.67” e 5G."
    )


def test_versoes_de_memoria_a_ram_vem_do_nome():
    texto = (
        "Oukitel C68 Plus + Fone\nMemoria Interna 128 ou 256 GB\n"
        "Memoria Ram 24 GB sendo (8 GB Fisica + 16 GB Virtual)\n"
        "Memoria Ram 16 GB sendo (4 GB Fisica + 12 GB Virtual)\nBateria 6000 mAh"
    )
    assert "16 GB de RAM" in destaque(texto, "Uranyx C68 Plus 16.256 - Preto")
    assert "24 GB de RAM" in destaque(texto, "Uranyx C68 Plus 24.256 - Preto")
    # Nome sem a RAM: duas respostas e nada que desempate — fica de fora.
    assert "RAM" not in destaque(texto, "x055.ci")


def test_formato_com_dois_pontos_tambem_e_lido():
    """O bloco do Oukitel G1 escreve "Bateria: 10600 Mah". Sem ler isso, a
    frase saía com a câmera de 13 MP do WP28E (bloco seguinte); a do G1 é 48."""
    texto = (
        "Oukitel G1 + Relógio USW10\n\nMemoria Ram: 24 GB sendo (6 GB Fisica + 18 GB Virtual)\n"
        "Camera Traseira: 48 MP SONY\nBateria: 10600 Mah\n"
        "———————————————\nUranyx Oukitel WP28E\n\nMemoria Ram 16 GB\n"
        "Tela 6.52 Polegadas\nCâmera traseira 13MP\nBateria 10600 mAh\n"
    )
    assert destaque(texto, "Uranyx G1 24.256 - Preto") == (
        "Bateria de 10.600 mAh, câmera de 48 MP e 24 GB de RAM."
    )


def test_bateria_do_relogio_do_kit_nao_esconde_a_do_celular():
    """Antes valia a PRIMEIRA bateria: com o relógio (450 mAh) na frente, a do
    celular sumia junto com a dele."""
    assert destaque("Relógio: Bateria 450 mAh\nCelular: Bateria 6000 mAh") == (
        "Bateria de 6.000 mAh."
    )
