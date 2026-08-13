"""Parse do endereço de destino em bling_orders._row_from_item.

Na API v3 do Bling o endereço de entrega costuma vir SÓ em
`transporte.etiqueta` (`enderecoEntrega` chega null). Sem o fallback,
`uf_destino`/`cep_destino` ficavam vazios e a restrição Apple→RJ nunca
disparava.
"""

from app.services.bling_orders import _row_from_item


def _order(transporte: dict, contato: dict | None = None) -> dict:
    return {
        "id": 1,
        "numero": "290000",
        "data": "2026-08-13",
        "transporte": transporte,
        "contato": contato or {},
    }


_ITEM = {"codigo": "iph15", "quantidade": 1, "valor": 100.0}


def test_endereco_vem_da_etiqueta_quando_endereco_entrega_null():
    row = _row_from_item(
        _order({
            "enderecoEntrega": None,
            "etiqueta": {
                "uf": "RJ",
                "cep": "20000-000",
                "municipio": "Rio de Janeiro",
                "endereco": "Rua A",
                "numero": "10",
                "bairro": "Centro",
            },
        }),
        _ITEM,
        item_index=0,
        store_id=None,
    )
    assert row["uf_destino"] == "RJ"
    assert row["cep_destino"] == "20000-000"
    assert row["cidade_destino"] == "Rio de Janeiro"


def test_endereco_entrega_tem_prioridade_sobre_etiqueta():
    row = _row_from_item(
        _order({
            "enderecoEntrega": {"uf": "SP", "cep": "13400-853"},
            "etiqueta": {"uf": "RJ", "cep": "20000-000"},
        }),
        _ITEM,
        item_index=0,
        store_id=None,
    )
    assert row["uf_destino"] == "SP"
    assert row["cep_destino"] == "13400-853"


def test_sem_endereco_nenhum_fica_none():
    row = _row_from_item(
        _order({"enderecoEntrega": None, "etiqueta": None}),
        _ITEM,
        item_index=0,
        store_id=None,
    )
    assert row["uf_destino"] is None
    assert row["cep_destino"] is None
