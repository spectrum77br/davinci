"""Pedido num estoque só — Vinicius, 23/09/2026.

O pedido 298787 caiu com o A17 Branco em .ci e o A17 Preto em .sp. A regra:
"quando sp ou ci tem tudo ganha quem tiver na prioridade, se nenhum item tem
prioridade cadastrada ganha quem tem mais estoque" e "quando nenhum estoque tem
tudo deixa como está".

O saldo virtual do Bling já desconta a reserva deste próprio pedido: item que
fica no estoque precisa de saldo >= 0, item que muda precisa de saldo >= qtd.
"""

import pytest

from app.services import prioridade_estoque as prio

BRANCO_CI, BRANCO_SP = "dg054.ci+a001.ci", "dg054.sp+a001.sp"
PRETO_CI, PRETO_SP = "dg052.ci+a001.ci", "dg052.sp+a001.sp"


class ClienteFalso:
    def __init__(self, saldos: dict[str, float]):
        self.saldos = {k.lower(): v for k, v in saldos.items()}
        self.consultas: list[str] = []

    async def find_active_product_by_sku(self, sku: str):
        self.consultas.append(sku)
        chave = sku.strip().lower()
        if chave not in self.saldos:
            return None
        return {"id": abs(hash(chave)) % 100000 + 1, "sku": chave, "name": chave,
                "stock": self.saldos[chave]}


async def plano(saldos, itens, mapa=None, redireciona=lambda cod: False):
    cliente = ClienteFalso(saldos)
    resultado = await prio._plano_estoque_unico(
        cliente, {}, qtd_por_codigo=dict(itens), mapa=mapa or {}, redireciona=redireciona,
    )
    return resultado, cliente


def trocas(p) -> list[tuple[str, str]]:
    return [(t["antigo"], t["alvo"]) for t in p["trocas"]]


TUDO_NOS_DOIS = {BRANCO_CI: 10, PRETO_CI: 10, BRANCO_SP: 50, PRETO_SP: 50,
                 "a001.ci": 100, "a001.sp": 100}
PEDIDO_298787 = [(BRANCO_CI, 1), (PRETO_SP, 1)]


async def test_298787_os_dois_tem_tudo_ganha_a_prioridade():
    p, _ = await plano(TUDO_NOS_DOIS, PEDIDO_298787, mapa={"dg054": "ci"})
    assert p["estoque"] == "ci" and p["motivo"] == "prioridade"
    assert trocas(p) == [(PRETO_SP, PRETO_CI)]


async def test_298787_sem_prioridade_ganha_quem_tem_mais_estoque():
    p, _ = await plano(TUDO_NOS_DOIS, PEDIDO_298787)
    assert p["estoque"] == "sp" and p["motivo"] == "mais_estoque"
    assert trocas(p) == [(BRANCO_CI, BRANCO_SP)]


async def test_estoque_da_prioridade_sem_tudo_perde_pro_que_tem_tudo():
    saldos = dict(TUDO_NOS_DOIS, **{PRETO_CI: 0})  # CI não tem o preto
    p, _ = await plano(saldos, PEDIDO_298787, mapa={"dg054": "ci"})
    assert p["estoque"] == "sp"
    assert trocas(p) == [(BRANCO_CI, BRANCO_SP)]


async def test_nenhum_estoque_tem_tudo_deixa_como_esta():
    saldos = dict(TUDO_NOS_DOIS, **{PRETO_CI: 0, BRANCO_SP: 0})
    p, _ = await plano(saldos, PEDIDO_298787)
    assert p["sem_estoque"] is True and p["misturado"] is True
    assert "trocas" not in p


async def test_dois_kits_mudando_com_o_mesmo_fone_somam_o_fone():
    """Os dois kits mostram 1 no SP porque o Bling mostra o kit pela peça mais
    escassa: é o MESMO fone. Mandar os dois pro SP pediria 2 fones."""
    itens = [(BRANCO_CI, 1), (PRETO_CI, 1), ("dg055.sp+a001.sp", 1)]
    saldos = {BRANCO_CI: 5, PRETO_CI: 5, "dg055.ci+a001.ci": 0,
              BRANCO_SP: 1, PRETO_SP: 1, "dg055.sp+a001.sp": 0, "a001.sp": 1}
    p, _ = await plano(saldos, itens)
    assert p["sem_estoque"] is True

    saldos["a001.sp"] = 2
    p, _ = await plano(saldos, itens)
    assert p["estoque"] == "sp"
    assert sorted(trocas(p)) == [(PRETO_CI, PRETO_SP), (BRANCO_CI, BRANCO_SP)]


async def test_pedido_inteiro_num_estoque_nao_e_dividido_por_prioridades_diferentes():
    """Hoje o item a item mandaria o preto pro SP e deixaria o branco no CI."""
    itens = [(BRANCO_CI, 1), (PRETO_CI, 1)]
    p, _ = await plano(TUDO_NOS_DOIS, itens, mapa={"dg054": "ci", "dg052": "sp"})
    assert p["estoque"] == "ci" and trocas(p) == []


async def test_todos_com_prioridade_sp_e_sp_tem_tudo_o_pedido_vai_inteiro():
    itens = [(BRANCO_CI, 1), (PRETO_CI, 1)]
    p, _ = await plano(TUDO_NOS_DOIS, itens, mapa={"dg054": "sp", "dg052": "sp"})
    assert p["estoque"] == "sp" and p["motivo"] == "prioridade"
    assert sorted(trocas(p)) == [(PRETO_CI, PRETO_SP), (BRANCO_CI, BRANCO_SP)]


async def test_prioridade_que_so_cobre_um_item_nao_divide_o_pedido():
    itens = [(BRANCO_CI, 1), (PRETO_CI, 1)]
    saldos = dict(TUDO_NOS_DOIS, **{PRETO_SP: 0})
    p, _ = await plano(saldos, itens, mapa={"dg054": "sp", "dg052": "sp"})
    assert p["estoque"] == "ci" and p["motivo"] == "ja_estava" and trocas(p) == []


async def test_mesmo_produto_em_dois_estoques_conta_a_reserva_do_pedido():
    """dg054.sp em 0 = a peça já está reservada pra ESTE pedido: dá pra ficar,
    mas não dá pra trazer mais uma."""
    itens = [("dg054.ci", 1), ("dg054.sp", 1)]
    p, _ = await plano({"dg054.ci": 5, "dg054.sp": 0}, itens)
    assert p["estoque"] == "ci"
    assert trocas(p) == [("dg054.sp", "dg054.ci")]


async def test_estoque_negativo_nao_segura_o_pedido_quando_a_familia_redireciona():
    itens = [("dg057.ci", 1), ("dg055.ci", 1)]
    saldos = {"dg057.ci": -1, "dg055.ci": 5, "dg057.sp": 36, "dg055.sp": 10}
    p, _ = await plano(saldos, itens, redireciona=lambda cod: True)
    assert p["estoque"] == "sp"
    assert sorted(trocas(p)) == [("dg055.ci", "dg055.sp"), ("dg057.ci", "dg057.sp")]


@pytest.mark.parametrize(
    "itens",
    [
        [(BRANCO_CI, 1)],                      # um item só
        [(BRANCO_CI, 1), ("dg052.us", 1)],     # usado não entra
        [(BRANCO_CI, 1), ("a006", 1)],         # acessório sem estoque no SKU
    ],
)
async def test_pedido_de_um_item_de_estoque_de_venda_fica_na_regra_de_sempre(itens):
    p, cliente = await plano(TUDO_NOS_DOIS, itens)
    assert p is None and cliente.consultas == []


async def test_tudo_no_mesmo_estoque_sem_prioridade_nao_consulta_o_bling():
    p, cliente = await plano(TUDO_NOS_DOIS, [(BRANCO_CI, 1), (PRETO_CI, 1)])
    assert p is None and cliente.consultas == []


async def test_prioridade_no_cd_fica_de_fora():
    p, _ = await plano(TUDO_NOS_DOIS, PEDIDO_298787, mapa={"dg054": "cd"})
    assert p is None


async def test_fone_que_nao_existe_no_estoque_barra_os_dois_kits():
    """Dois kits indo pro SP com o fone: se o fone do SP nem existe no Bling,
    não dá pra garantir — o pedido fica onde está."""
    saldos = {k: v for k, v in TUDO_NOS_DOIS.items() if k != "a001.sp"}
    p, _ = await plano(saldos, [(BRANCO_CI, 1), (PRETO_CI, 1)], mapa={"dg054": "sp", "dg052": "sp"})
    assert p["estoque"] == "ci" and trocas(p) == []
