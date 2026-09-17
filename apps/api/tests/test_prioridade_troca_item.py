"""Como o robô de prioridade troca o item do pedido no PUT do Bling.

Eduardo (17/09/2026): "precisamos deixar isso perfeito". Editar o item deixa a
composição VELHA colada nele (o Bling baixa o kit errado e sobra reserva
órfã); substituir o item faz o Bling refazer a composição.
"""

from app.services.prioridade_estoque import aplicar_trocas_nos_itens

TROCA = {
    "antigo": "dg053.ci+a001.ci",
    "alvo": "dg053.sp+a001.sp",
    "alvo_id": 77,
    "alvo_nome": "Kit SP",
    "qtd": 1,
}


def _itens() -> list[dict]:
    return [
        {"id": 9, "codigo": "dg053.ci+a001.ci", "quantidade": 1, "valor": 100.0},
        {"id": 10, "codigo": "outro.ci", "quantidade": 2, "valor": 50.0},
    ]


def test_editar_mantem_o_id_do_item():
    """Jeito antigo: mesmo item, então o Bling segue com a composição velha."""
    novos, aplicadas = aplicar_trocas_nos_itens(_itens(), [TROCA], substituir=False)
    assert novos[0]["id"] == 9
    assert novos[0]["codigo"] == "dg053.sp+a001.sp"
    assert novos[0]["produto"] == {"id": 77}
    assert novos[0]["descricao"] == "Kit SP"
    assert aplicadas == [TROCA]


def test_substituir_tira_o_id_para_o_bling_refazer_a_composicao():
    novos, aplicadas = aplicar_trocas_nos_itens(_itens(), [TROCA], substituir=True)
    assert "id" not in novos[0]
    assert novos[0]["codigo"] == "dg053.sp+a001.sp"
    # o resto do item é preservado: quantidade e valor vão inteiros no PUT
    assert (novos[0]["quantidade"], novos[0]["valor"]) == (1, 100.0)
    assert aplicadas == [TROCA]


def test_item_que_nao_casa_passa_intacto_nos_dois_modos():
    for substituir in (False, True):
        novos, _ = aplicar_trocas_nos_itens(_itens(), [TROCA], substituir=substituir)
        assert novos[1] == {"id": 10, "codigo": "outro.ci", "quantidade": 2, "valor": 50.0}
        assert len(novos) == 2


def test_nao_mexe_no_dicionario_que_veio_do_bling():
    """O body do PUT é montado à parte — resposta do GET fica intacta."""
    originais = _itens()
    aplicar_trocas_nos_itens(originais, [TROCA], substituir=True)
    assert originais[0] == {
        "id": 9,
        "codigo": "dg053.ci+a001.ci",
        "quantidade": 1,
        "valor": 100.0,
    }


def test_sem_troca_correspondente_nao_aplica_nada():
    novos, aplicadas = aplicar_trocas_nos_itens(_itens(), [], substituir=True)
    assert aplicadas == []
    assert novos == _itens()


def test_duas_linhas_do_mesmo_kit_contam_uma_troca_so():
    """Pedido com o mesmo kit em dois itens: as duas linhas trocam, mas a
    troca entra UMA vez em `aplicadas` (senão o plano de compensação
    somaria a mesma peça duas vezes)."""
    itens = _itens() + [{"id": 11, "codigo": "dg053.ci+a001.ci", "quantidade": 1}]
    novos, aplicadas = aplicar_trocas_nos_itens(itens, [TROCA], substituir=True)
    assert [i["codigo"] for i in novos].count("dg053.sp+a001.sp") == 2
    assert aplicadas == [TROCA]
