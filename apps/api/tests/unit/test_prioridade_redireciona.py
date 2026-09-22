"""De qual lote a peça sai quando o anúncio promete o total da família.

Eduardo (22/09/2026): "se tiver em ci vai continuar saindo do estoque de lá, se
tiver prioridade, por exemplo para sp, aí passa a sair do sp". E quando o lote
da prioridade está vazio, a peça tem que sair de onde ela está — senão o anúncio
que mostra 36 derruba o saldo para negativo, que é o caso real do dg057.ci.
"""

import pytest

from app.services import estoque_familia, prioridade_estoque


class ClienteFalso:
    """Devolve o produto do Bling por SKU, com o saldo que o teste definir."""

    def __init__(self, saldos: dict[str, float]):
        self.saldos = saldos
        self.consultas: list[str] = []

    async def find_active_product_by_sku(self, sku: str):
        # O Bling acha o produto sem ligar para maiúscula/minúscula.
        self.consultas.append(sku)
        chave = sku.strip().lower()
        if chave not in self.saldos:
            return None
        return {"id": abs(hash(chave)) % 100000, "sku": chave, "name": chave,
                "stock": self.saldos[chave]}


@pytest.fixture
def ligar_familia(monkeypatch):
    def _f(ativo=True, prefixos="dg055,dg057"):
        class C:
            estoque_familia_ativo = ativo
            estoque_familia_prefixos = prefixos
            estoque_familia_minimo = 5

        monkeypatch.setattr(estoque_familia, "get_settings", lambda: C())

    return _f


async def escolher(cliente, codigo, tag, prio, qtd=1, redirecionar=True):
    return await prioridade_estoque._lote_com_saldo(
        cliente, {}, codigo=codigo, tag_atual=tag, prioridade=prio,
        qtd=qtd, redirecionar=redirecionar,
    )


async def test_prioridade_tem_peca_entao_sai_de_la(ligar_familia):
    ligar_familia()
    c = ClienteFalso({"dg057.ci": 10, "dg057.sp": 36})
    alvo, _ = await escolher(c, "dg057.sp", "sp", "ci")
    assert alvo == "dg057.ci"


async def test_prioridade_vazia_entao_sai_do_proprio_lote(ligar_familia):
    """O anúncio é do .sp, a prioridade é ci e o ci está vazio: a peça sai do sp
    mesmo, em vez de o pedido ficar travado."""
    ligar_familia()
    c = ClienteFalso({"dg057.ci": 0, "dg057.sp": 36})
    alvo, _ = await escolher(c, "dg057.sp", "sp", "ci")
    assert alvo == "dg057.sp"


async def test_o_caso_dg057_anuncio_do_ci_com_ci_vazio(ligar_familia):
    """O caso que motivou tudo: o anúncio do .ci mostra 36 porque a família tem
    36, mas o .ci está em -1. A venda tem que sair do .sp."""
    ligar_familia()
    c = ClienteFalso({"dg057.ci": -1, "dg057.sp": 36})
    alvo, _ = await escolher(c, "dg057.ci", "ci", "ci")
    assert alvo == "dg057.sp"


async def test_sem_redirecionamento_o_comportamento_e_o_de_hoje(ligar_familia):
    """Desligado, o robô não inventa lote nenhum: ou a prioridade cobre, ou nada."""
    ligar_familia()
    c = ClienteFalso({"dg057.ci": -1, "dg057.sp": 36})
    alvo, _ = await escolher(c, "dg057.ci", "ci", "ci", redirecionar=False)
    assert alvo is None


async def test_nenhum_lote_cobre_a_quantidade(ligar_familia):
    ligar_familia()
    c = ClienteFalso({"dg057.ci": 0, "dg057.sp": 1})
    alvo, _ = await escolher(c, "dg057.ci", "ci", "ci", qtd=5)
    assert alvo is None


async def test_kit_redireciona_o_kit_inteiro(ligar_familia):
    ligar_familia()
    c = ClienteFalso({"dg057.ci+a001.ci": 0, "dg057.sp+a001.sp": 36})
    alvo, _ = await escolher(c, "dg057.ci+a001.ci", "ci", "ci")
    assert alvo == "dg057.sp+a001.sp"


async def test_produto_que_nao_existe_no_bling_e_pulado(ligar_familia):
    ligar_familia()
    c = ClienteFalso({"dg057.sa": 50})  # ci e sp nem existem
    alvo, _ = await escolher(c, "dg057.ci", "ci", "ci")
    assert alvo == "dg057.sa"


async def test_linha_fora_da_soma_nao_redireciona(ligar_familia):
    """A soma está ligada só para dg055 e dg057; dg053 segue a regra antiga."""
    ligar_familia(prefixos="dg055,dg057")
    assert estoque_familia.familia_ligada("dg057.ci") is True
    assert estoque_familia.familia_ligada("dg053.ci") is False


async def test_soma_desligada_nao_redireciona_nada(ligar_familia):
    ligar_familia(ativo=False)
    assert estoque_familia.familia_ligada("dg057.ci") is False


async def test_sku_com_maiuscula_e_espaco_continua_casando(ligar_familia):
    """Regressão: `DG053.CI + Brinde` existe no catálogo. Comparar sem baixar a
    caixa fazia o robô achar que o produto não existia e parar de trocar."""
    ligar_familia(prefixos="")
    c = ClienteFalso({"dg053.sp+brinde": 9})
    alvo, prod = await escolher(c, "DG053.CI + Brinde", "ci", "sp")
    assert alvo == "DG053.sp+Brinde"
    assert prod["stock"] == 9
