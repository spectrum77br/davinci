"""A regra de família: qual SKU soma com qual.

Eduardo (22/09/2026): "dg053.ci+a001.ci + dg053.sp+a001.sp aí pode somar".
O teste que importa é o contrário: garantir que kit NÃO some com simples nem
com outro kit — foi esse engano que somaria 3.252 celulares onde existem 1.183.
"""

from app.services.estoque_familia import chave_familia, irmaos, lote_de


def test_o_mesmo_kit_em_lotes_diferentes_e_a_mesma_familia():
    assert chave_familia("dg053.ci+a001.ci") == "dg053+a001"
    assert chave_familia("dg053.sp+a001.sp") == "dg053+a001"
    assert chave_familia("dg053.ci+a001.ci") == chave_familia("dg053.sp+a001.sp")


def test_kit_com_acessorio_diferente_e_outra_familia():
    """dg053.ci+a001.ci e dg053.ci+a003.ci são o MESMO celular, mas anúncios
    diferentes — somar um no outro contaria a peça duas vezes."""
    assert chave_familia("dg053.ci+a003.ci") == "dg053+a003"
    assert chave_familia("dg053.ci+a001.ci") != chave_familia("dg053.ci+a003.ci")


def test_kit_nao_soma_com_o_produto_simples():
    """O caso que quase virou 3.252 celulares: o Bling dá ao kit o saldo do
    menor componente, então o simples aparece repetido dentro de cada kit."""
    assert chave_familia("dg053.ci") == "dg053"
    assert chave_familia("dg053.ci") != chave_familia("dg053.ci+a001.ci")


def test_kit_de_tres_pecas():
    assert chave_familia("dg053.sp+a003.sp+a004.sp") == "dg053+a003+a004"
    assert chave_familia("dg053.ci+a003.ci+a004.ci") == "dg053+a003+a004"


def test_lote_fora_da_venda_nao_forma_familia():
    """`cd` é o Centro de Distribuição (32 mil peças ainda não distribuídas) e
    `us` é usado — Eduardo mandou ficarem de fora."""
    assert chave_familia("dg053.cd") is None
    assert chave_familia("dg053.us") is None


def test_sufixo_desconhecido_nao_forma_familia():
    """`pp` e `lj` existem no catálogo mas não são lote reconhecido."""
    assert chave_familia("dg053.pp") is None
    assert chave_familia("dg053.lj") is None


def test_numero_no_fim_nao_e_lote():
    """b009.8.12.20.24 são tamanhos e uaf001m1.110 é voltagem — se virassem
    lote, produtos diferentes cairiam na mesma família."""
    assert chave_familia("b009.8.12.20.24") is None
    assert chave_familia("uaf001m1.110") is None
    assert chave_familia("b017.18.24") is None


def test_kit_com_lotes_misturados_nao_forma_familia():
    """Conservador: não dá para saber de que lote sai a peça."""
    assert chave_familia("dg053.ci+a001.sp") is None


def test_sku_vazio_ou_fake():
    assert chave_familia(None) is None
    assert chave_familia("") is None
    assert chave_familia("fake.ci") is None


def test_lote_de_reconhece_so_os_sufixos_conhecidos():
    assert lote_de("dg053.ci") == "ci"
    assert lote_de("dg053.SP") == "sp"
    assert lote_de("dg053") is None
    assert lote_de("uaf001m1.110") is None


def test_irmaos_lista_um_por_lote_de_venda():
    assert irmaos("dg053.ci+a001.ci") == [
        "dg053.ci+a001.ci",
        "dg053.pi+a001.pi",
        "dg053.ra+a001.ra",
        "dg053.sa+a001.sa",
        "dg053.sp+a001.sp",
    ]
    assert irmaos("dg053.sp") == [
        "dg053.ci",
        "dg053.pi",
        "dg053.ra",
        "dg053.sa",
        "dg053.sp",
    ]
    assert irmaos("dg053.cd") == []
