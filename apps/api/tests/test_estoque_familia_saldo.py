"""Quanto o anúncio publica quando a soma por família está ligada.

Eduardo (22/09/2026): "pode juntar". O anúncio de um lote não pode zerar
enquanto houver peça do mesmo produto em outro lote. O que NÃO muda: de onde a
peça sai na venda, que continua sendo o lote do anúncio ou a prioridade.

O teste que mais importa é `test_anuncio_zerado_volta_a_vender`: é o caso do
dg057.ci, zerado hoje com 36 peças disponíveis no .sp.
"""

import pytest

from app.models import Product
from app.services import estoque_familia


class _Config:
    def __init__(self, *, ativo=True, prefixos="", minimo=0):
        self.estoque_familia_ativo = ativo
        self.estoque_familia_prefixos = prefixos
        self.estoque_familia_minimo = minimo


@pytest.fixture
def configurar(monkeypatch):
    def _f(**kwargs):
        monkeypatch.setattr(estoque_familia, "get_settings", lambda: _Config(**kwargs))

    return _f


async def _produto(db, user, sku: str, stock: int, situacao: str = "A") -> Product:
    p = Product(user_id=user.id, sku=sku, name=sku, stock=stock, situacao=situacao)
    db.add(p)
    await db.flush()
    return p


async def test_desligado_publica_o_saldo_do_proprio_produto(db, make_user, configurar):
    u = await make_user()
    await _produto(db, u, "dg053.sp", 900)
    p = await _produto(db, u, "dg053.ci", 200)
    configurar(ativo=False)
    assert await estoque_familia.saldo_publicavel(db, p) == 200


async def test_ligado_publica_o_total_da_familia(db, make_user, configurar):
    u = await make_user()
    await _produto(db, u, "dg053.sp", 900)
    await _produto(db, u, "dg053.ra", 90)
    p = await _produto(db, u, "dg053.ci", 200)
    configurar()
    assert await estoque_familia.saldo_publicavel(db, p) == 1190


async def test_anuncio_zerado_volta_a_vender(db, make_user, configurar):
    """O caso do dg057.ci: zerado, com 36 peças no .sp."""
    u = await make_user()
    await _produto(db, u, "dg057.sp", 36)
    p = await _produto(db, u, "dg057.ci", 0)
    configurar()
    assert await estoque_familia.saldo_publicavel(db, p) == 36


async def test_soma_ignora_o_dono(db, make_user, configurar):
    """dg053.ci está no heisenberg e dg053.sp no bill gates — mesmo celular,
    mesmo galpão. Eduardo decidiu que o dono não separa a conta."""
    dono1 = await make_user()
    dono2 = await make_user()
    await _produto(db, dono2, "dg053.sp", 973)
    p = await _produto(db, dono1, "dg053.ci", 217)
    configurar()
    assert await estoque_familia.saldo_publicavel(db, p) == 1190


async def test_produto_excluido_nao_entra_na_soma(db, make_user, configurar):
    u = await make_user()
    await _produto(db, u, "dg053.sp", 900, situacao="E")
    p = await _produto(db, u, "dg053.ci", 200)
    configurar()
    assert await estoque_familia.saldo_publicavel(db, p) == 200


async def test_piso_de_seguranca_impede_a_soma_de_saldo_baixo(db, make_user, configurar):
    """1 peça em 14 anúncios ofereceria a mesma peça 14 vezes."""
    u = await make_user()
    await _produto(db, u, "dg080.sp", 1)
    p = await _produto(db, u, "dg080.ci", 0)
    configurar(minimo=5)
    assert await estoque_familia.saldo_publicavel(db, p) == 0
    configurar(minimo=0)
    assert await estoque_familia.saldo_publicavel(db, p) == 1


async def test_fora_dos_prefixos_nao_soma(db, make_user, configurar):
    """Começamos só pelo A17; o resto continua como está hoje."""
    u = await make_user()
    await _produto(db, u, "i214.sp", 500)
    p = await _produto(db, u, "i214.sa", 0)
    configurar(prefixos="dg052,dg053,dg054,dg055,dg056,dg057")
    assert await estoque_familia.saldo_publicavel(db, p) == 0
    configurar(prefixos="")
    assert await estoque_familia.saldo_publicavel(db, p) == 500


async def test_lote_fora_da_venda_nao_soma(db, make_user, configurar):
    """cd é o Centro de Distribuição e us é usado: ficam de fora."""
    u = await make_user()
    await _produto(db, u, "dg053.sp", 900)
    p = await _produto(db, u, "dg053.cd", 32000)
    configurar()
    assert await estoque_familia.saldo_publicavel(db, p) == 32000


async def test_kit_soma_com_kit_e_nao_com_o_simples(db, make_user, configurar):
    """O Bling dá ao kit o saldo do menor componente, então o simples aparece
    repetido dentro de cada kit — somar os dois contaria a peça duas vezes."""
    u = await make_user()
    await _produto(db, u, "dg053.sp", 900)
    await _produto(db, u, "dg053.sp+a001.sp", 900)
    p = await _produto(db, u, "dg053.ci+a001.ci", 200)
    configurar()
    assert await estoque_familia.saldo_publicavel(db, p) == 1100


async def test_cache_nao_mistura_familias(db, make_user, configurar):
    u = await make_user()
    await _produto(db, u, "dg053.sp", 900)
    await _produto(db, u, "dg054.sp", 7)
    a = await _produto(db, u, "dg053.ci", 200)
    b = await _produto(db, u, "dg054.ci", 3)
    configurar()
    cache: dict[str, int] = {}
    assert await estoque_familia.saldo_publicavel(db, a, cache=cache) == 1100
    assert await estoque_familia.saldo_publicavel(db, b, cache=cache) == 10
    assert cache == {"dg053": 1100, "dg054": 10}
