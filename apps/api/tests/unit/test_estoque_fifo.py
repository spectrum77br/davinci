"""FIFO do sem estoque: quem vai pra Aguardando Cancelamento quando o saldo
virtual do Bling fica negativo.

Eduardo (30/10/2026): "estamos mudando para aguardando cancelamento, está
certo, só que ao invés de mudar os que estão negativos, ele muda os que já
estão RESERVADOS, e os negativos deixa". A peça é do pedido mais antigo (já
reservada pra ele); quem estourou o saldo foram os mais novos.
"""

import pytest

from app.services.estoque_fifo import PedidoPecas, negativos_fifo

_AUTO = object()


def _p(numero, pecas, ordem=_AUTO, **kw):
    """`ordem` = bling_id; por padrão anda junto com o número (como no Bling)."""
    if ordem is _AUTO:
        ordem = 20_000_000_000 + int(numero)
    return PedidoPecas(numero=numero, ordem=ordem, pecas=pecas, **kw)


def test_caso_da_queixa_antigo_reservado_passa_e_o_novo_vai():
    """Saldo −1 com dois pedidos da mesma peça: o mais antigo tem a peça
    reservada e segue; só o mais novo é o negativo."""
    neg = negativos_fifo(
        {"b026.18": -1},
        [_p("306580", {"b026.18": 1}), _p("306633", {"b026.18": 1})],
    )
    assert neg == {"306633": {"b026.18"}}


def test_rajada_de_30_10_tres_pecas_nove_pedidos():
    """O caso real do b026.18: 3 peças livres e 9 pedidos (saldo −6), mais o
    306575 já em Em digitação. Ficam com a peça os 3 que entraram primeiro no
    Bling; vão os 6 mais novos."""
    pedidos = [_p("306575", {"b026.18": 1}, garantido=True)]
    pedidos += [
        _p(n, {"b026.18": 1})
        for n in (
            "306580", "306581", "306589", "306616", "306620", "306622", "306624", "306633",
            "306636",
        )
    ]
    neg = negativos_fifo({"b026.18": -6}, pedidos)
    assert set(neg) == {"306616", "306620", "306622", "306624", "306633", "306636"}
    assert not {"306575", "306580", "306581", "306589"} & set(neg)


def test_deficit_cobre_varios_pedidos_novos():
    neg = negativos_fifo(
        {"x": -2},
        [_p("100001", {"x": 1}), _p("100002", {"x": 1}), _p("100003", {"x": 1})],
    )
    assert set(neg) == {"100002", "100003"}


def test_quantidade_2_no_mais_novo_cobre_o_deficit_sozinho():
    neg = negativos_fifo(
        {"x": -2},
        [_p("100001", {"x": 1}), _p("100002", {"x": 1}), _p("100003", {"x": 2})],
    )
    assert neg == {"100003": {"x"}}


def test_quantidade_2_que_nao_cobre_puxa_o_anterior():
    """Déficit 3 com dois pedidos de 2: o mais novo solta 2, falta 1 — o
    anterior também vai."""
    neg = negativos_fifo({"x": -3}, [_p("100001", {"x": 2}), _p("100002", {"x": 2})])
    assert set(neg) == {"100001", "100002"}


def test_quantidade_2_no_antigo_com_deficit_1_so_o_novo_vai():
    neg = negativos_fifo({"x": -1}, [_p("100001", {"x": 2}), _p("100002", {"x": 1})])
    assert neg == {"100002": {"x"}}


def test_multi_sku_o_pedido_que_vai_solta_todas_as_pecas():
    """O mais novo leva x e y, as duas negativas em 1: ele sozinho cobre os
    dois déficits — os antigos de x e de y seguem."""
    neg = negativos_fifo(
        {"x": -1, "y": -1},
        [_p("100001", {"x": 1}), _p("100002", {"y": 1}), _p("100003", {"x": 1, "y": 1})],
    )
    assert neg == {"100003": {"x", "y"}}


def test_multi_sku_antigo_com_duas_pecas_passa():
    neg = negativos_fifo(
        {"x": -1, "y": -1},
        [_p("100001", {"x": 1, "y": 1}), _p("100002", {"x": 1}), _p("100003", {"y": 1})],
    )
    assert neg == {"100002": {"x"}, "100003": {"y"}}


def test_multi_sku_so_a_peca_negativa_conta():
    """Peça com saldo positivo (ou fora do mapa) não decide nada: o pedido
    antigo com z sobrando continua passando."""
    neg = negativos_fifo(
        {"x": -1, "z": 10},
        [_p("100001", {"x": 1, "z": 1, "w": 1}), _p("100002", {"x": 1})],
    )
    assert neg == {"100002": {"x"}}


def test_sem_bling_id_o_numero_como_inteiro_decide():
    """Sem a ordem de entrada no Bling, o número maior é o mais novo —
    comparado como número (100000 > 99999)."""
    neg = negativos_fifo(
        {"x": -1}, [_p("100000", {"x": 1}, ordem=None), _p("99999", {"x": 1}, ordem=None)]
    )
    assert neg == {"100000": {"x"}}


def test_ordem_de_entrada_no_bling_decide():
    """Quem entrou por último no Bling (bling_id maior) reservou por último —
    é ele o negativo, mesmo com número menor (a data da venda não entra)."""
    neg = negativos_fifo(
        {"x": -1},
        [_p("100001", {"x": 1}, ordem=700009), _p("100009", {"x": 1}, ordem=700001)],
    )
    assert neg == {"100001": {"x"}}


def test_saldo_zero_ou_positivo_nao_marca_ninguem():
    pedidos = [_p("100001", {"x": 1}), _p("100002", {"x": 1})]
    assert negativos_fifo({"x": 0}, pedidos) == {}
    assert negativos_fifo({"x": 3}, pedidos) == {}
    assert negativos_fifo({}, pedidos) == {}


def test_fora_das_situacoes_que_reservam_nao_conta_como_reserva():
    """Pedido que não segura peça no Bling (ex.: já em Aguardando
    Cancelamento) não absorve o déficit de quem segura: com saldo −1 e um só
    pedido reservando, é ele que vai. E o que não reserva, com saldo
    negativo, também não passa (não tem peça pra defender)."""
    neg = negativos_fifo(
        {"x": -1},
        [_p("100001", {"x": 1}), _p("100009", {"x": 1}, reserva=False)],
    )
    assert neg == {"100001": {"x"}, "100009": {"x"}}


def test_fora_das_situacoes_que_reservam_com_saldo_zero_passa():
    """Regra de antes para quem não reserva: só negativo bloqueia."""
    assert negativos_fifo({"x": 0}, [_p("100009", {"x": 1}, reserva=False)]) == {}


def test_garantido_nunca_e_o_negativo():
    """Em digitação (ou já na fila da NF) tem a peça garantida, mesmo sendo o
    mais novo: o déficit cai no pedido Em aberto."""
    neg = negativos_fifo(
        {"x": -1},
        [_p("100001", {"x": 1}), _p("100002", {"x": 1}, garantido=True)],
    )
    assert neg == {"100001": {"x"}}


def test_deficit_maior_que_os_pedidos_marca_todos_os_que_podem():
    """Reserva de fora do espelho (ou Em digitação) explica parte do déficit:
    todo pedido que pode ir, vai — sem inventar mais nada."""
    neg = negativos_fifo(
        {"x": -5},
        [_p("100001", {"x": 1}), _p("100002", {"x": 1}), _p("100003", {"x": 1}, garantido=True)],
    )
    assert set(neg) == {"100001", "100002"}


def test_sem_bling_id_o_numero_entra_no_lugar_da_ordem():
    """Revisão R7: sem o `bling_id`, a ordem é o NÚMERO como inteiro — não o
    fim da fila. Antes o pedido sem id virava o "mais novo" por infinito,
    absorvia o déficit (e não dá pra mover pedido sem id no Bling) e o novo
    de verdade passava sem peça."""
    neg = negativos_fifo(
        {"x": -1},
        [
            _p("300000", {"x": 1}, ordem=None),
            _p("830001", {"x": 1}, ordem=700001),
            _p("830002", {"x": 1}, ordem=700002),
        ],
    )
    assert neg == {"830002": {"x"}}


def test_sem_bling_id_e_numero_nao_inteiro_e_o_mais_novo():
    """Sem id e sem número inteiro (ex. avulso "306539.1") não dá pra provar
    que reservou antes: é o mais novo."""
    neg = negativos_fifo(
        {"x": -1}, [_p("100009", {"x": 1}), _p("306539.1", {"x": 1}, ordem=None)]
    )
    assert neg == {"306539.1": {"x"}}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Limite conhecido do físico (revisão R8): reserva que o espelho não vê é"
        " tratada como de pedido MAIS NOVO (ingest atrasado de 30/10). Se ela for"
        " de um MAIS ANTIGO (fora da janela de 30 dias, situação velha no"
        " espelho), o novo do lote passa. Decisão pendente com o Eduardo."
    ),
)
def test_ingest_atrasado_do_mais_antigo_o_novo_do_lote_nao_passa():
    """Revisão R8: o Bling reserva X (mais antigo, fora do espelho) e B (novo,
    no lote); físico 1 → virtual −1. O B não tem peça — mas o físico diz que
    os conhecidos (só o B) cabem e ninguém vai. Strict: quando a regra mudar,
    este teste passa e o marcador tem de sair."""
    neg = negativos_fifo({"p": -1.0}, [PedidoPecas("B", 200, {"p": 1.0})], {"p": 1.0})
    assert neg == {"B": {"p"}}


def test_kit_mais_e_o_simples_disputam_o_componente():
    """Revisão R3 (cálculo puro): o kit "+" dg075.pi+a004.pi aberto nos
    componentes disputa a dg075.pi com o simples mais antigo — físico 1,
    virtual −1: vai só o kit (mais novo), com ou sem o físico."""
    pedidos = [
        _p("830001", {"dg075.pi": 1}),
        _p("830002", {"dg075.pi": 1, "a004.pi": 1}),
    ]
    saldos = {"dg075.pi": -1, "a004.pi": 200}
    assert negativos_fifo(saldos, pedidos, {"dg075.pi": 1, "a004.pi": 200}) == {
        "830002": {"dg075.pi"}
    }
    assert negativos_fifo(saldos, pedidos) == {"830002": {"dg075.pi"}}


def test_ingest_atrasado_o_fisico_poe_o_deficit_em_quem_o_espelho_nao_viu():
    """30/10 08:18: o Bling já reservava os 9 pedidos de 29/10 (saldo −6), mas
    o espelho só tinha os 2 mais antigos + o 306575 (Em digitação). Físico 4:
    os 3 que o espelho conhece cabem — o déficit é dos que ainda não
    entraram (os mais novos). Sem o físico, a conta fica conservadora."""
    pedidos = [
        _p("306575", {"b026.18": 1}, garantido=True),
        _p("306580", {"b026.18": 1}),
        _p("306581", {"b026.18": 1}),
    ]
    assert negativos_fifo({"b026.18": -6}, pedidos, {"b026.18": 4}) == {}
    assert set(negativos_fifo({"b026.18": -6}, pedidos)) == {"306580", "306581"}


def test_ingest_atrasado_quando_os_novos_chegam_eles_e_que_vao():
    """08:22: os 4 primeiros já estão garantidos (na fila da NF) e chegaram o
    306616 e o 306620 — físico 4, conhecidos pedem 6: vão os 2."""
    pedidos = [
        _p(n, {"b026.18": 1}, garantido=True) for n in ("306575", "306580", "306581", "306589")
    ]
    pedidos += [_p("306616", {"b026.18": 1}), _p("306620", {"b026.18": 1})]
    neg = negativos_fifo({"b026.18": -3}, pedidos, {"b026.18": 4})
    assert set(neg) == {"306616", "306620"}


def test_fisico_nunca_passa_do_deficit_do_bling():
    """Espelho com reserva a mais (situação atrasada no espelho): o físico
    diria déficit 3, o Bling diz 1 — vale o menor, só o mais novo vai."""
    pedidos = [_p("100001", {"x": 1}), _p("100002", {"x": 1}), _p("100003", {"x": 1})]
    assert negativos_fifo({"x": -1}, pedidos, {"x": 0}) == {"100003": {"x"}}


def test_fisico_so_vale_para_a_peca_que_tem():
    """Peça sem físico (ex.: kit inteiro) fica na conta do virtual."""
    pedidos = [_p("100001", {"x": 1, "k": 1}), _p("100002", {"k": 1})]
    neg = negativos_fifo({"x": -5, "k": -1}, pedidos, {"x": 10})
    assert neg == {"100002": {"k"}}
