"""Orders and buyers from UpSeller's export (Temu, Amazon), so they stop depending on Bling.

All data here is made up; the shapes follow a real export of 27/11/2026."""

import base64
import io
import json
import os

import pytest
from openpyxl import Workbook
from test_consulta import LOCAL, app_client, mirrored

from app.erp_local import recipient_capture, service_loop, upseller_import
from app.erp_local.core import ERPError, dumps

HEAD = [
    "Nº de Pedido da Plataforma",
    "Nº de Pedido",
    "Plataformas",
    "Nome da Loja no UpSeller",
    "Estado do Pedido",
    "Hora do Pedido",
    "Hora do Pagamento",
    "Moeda",
    "Valor do Pedido",
    "Valor Total de Produtos",
    "Pós-venda/Cancelado/Devolvido",
    "SKU (Armazém)",
    "Quantidade de Produtos",
    "Nome do Produto",
    "Valor rateado por produto",
    "ID da Taxa",
    "Nome do Destinatário",
    "Telefone do Destinatário",
    "Endereço do Destinatário",
    "Endereço 1",
    "Endereço 2",
    "Número",
    "Bairro",
    "Cidade",
    "Estado",
    "CEP",
]
PO = "PO-029-12345678901234567"


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("ERP_PII_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())


def sheet(rows, head=HEAD):
    book = Workbook()
    page = book.active
    page.append(head)
    for row in rows:
        page.append([row.get(column) for column in head])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def temu(number=PO, *, state="Para Retirada", quantity=1, sku="a076", store="Temu Barbosa"):
    return {
        "Nº de Pedido da Plataforma": number,
        "Nº de Pedido": "#UP2NYY000001",
        "Plataformas": "Temu",
        "Nome da Loja no UpSeller": store,
        "Estado do Pedido": state,
        "Hora do Pedido": "26/11/2026 23:36",
        "Hora do Pagamento": "26/11/2026 23:36",
        "Moeda": "BRL",
        "Valor do Pedido": "42,79",
        "Valor Total de Produtos": 42.79,
        "SKU (Armazém)": sku,
        "Quantidade de Produtos": quantity,
        "Nome do Produto": "Produto de teste",
        "ID da Taxa": "12345678909",
        "Nome do Destinatário": "Maria Teste",
        "Telefone do Destinatário": "+55 11999990000",
        "Endereço do Destinatário": "Rua das Flores, 120",
        "Endereço 1": "Rua das Flores, 120",
        "Endereço 2": "Casa 2",
        "Número": "*******",
        "Bairro": "Centro",
        "Cidade": "Uibaí",
        "Estado": "BA",
        "CEP": "44950-000",
    }


def amazon(number, line1, line2, store="Amazon KFA"):
    return {
        "Nº de Pedido da Plataforma": number,
        "Plataformas": "Amazon",
        "Nome da Loja no UpSeller": store,
        "Estado do Pedido": "Para Emitir",
        "Hora do Pedido": "27/11/2026 07:53",
        "ID da Taxa": "98765432100",
        "Nome do Destinatário": "João Teste",
        "Endereço 1": line1,
        "Endereço 2": line2,
        "Número": "",
        "Bairro": "",
        "Cidade": "Joinville",
        "Estado": "SC",
        "CEP": "89200000",
    }


def observations(erp, number=PO):
    with erp.connection() as db:
        return [
            (r["source"], r["updated_at"], json.loads(r["data"]))
            for r in db.execute(
                "SELECT r.source,o.updated_at,o.data FROM native_order_observations o JOIN "
                "native_order_reads r ON r.digest=o.read_digest WHERE o.external_id=? "
                "ORDER BY o.updated_at",
                (number,),
            )
        ]


def buyer(erp, channel, store, number):
    with erp.connection() as db:
        return recipient_capture.for_order(db, channel, store, number)


@pytest.mark.parametrize(
    ("platform", "line1", "line2", "number", "district", "expected"),
    [
        # Temu: the district in its column, the number masked, the number in "Endereço 1".
        ("temu", "Rua das Flores, 120", "Casa 2", "*******", "Centro",
         ("Rua das Flores", "120", "Casa 2", "Centro", True)),
        # Amazon: number and district columns empty, both inside the address lines.
        ("amazon", "Avenida Brasil, 55", "Jardim América", "", "",
         ("Avenida Brasil", "55", "", "Jardim América", True)),
        ("amazon", "Rua Sete - Vila Nova", "123", "", "",
         ("Rua Sete", "123", "", "Vila Nova", True)),
        ("amazon", "Rua Treze de Maio 450", "apto 12", "", "",
         ("Rua Treze de Maio", "450", "apto 12", "", False)),
        ("amazon", "Estrada Velha", "Centro", "", "", ("Estrada Velha", "S/N", "", "Centro", True)),
    ],
)  # fmt: skip
def test_the_address_is_split_as_the_invoice_needs_it(
    platform, line1, line2, number, district, expected
):
    row = {"Endereço 1": line1, "Endereço 2": line2, "Número": number, "Bairro": district}
    got = upseller_import.split_address(platform, row)
    assert (got["street"], got["number"], got["complement"], got["district"], got["complete"]) == (
        expected
    )


def test_a_temu_order_the_erp_lacks_comes_in_once_and_its_cancellation_follows(erp):
    service_loop.adopt_bling_copy_stores(erp)
    first = upseller_import.import_sheet(erp, sheet([temu()]), "temu", "Temu, 3 dias")
    assert (first["novos"], first["compradores"], first["sem_loja"]) == (1, 1, [])
    ((source, _, order),) = observations(erp)
    assert source == upseller_import.SOURCE and order["status"] == "UPSELLER_COPY"
    assert [(i["seller_sku"], i["quantity"]) for i in order["items"]] == [("a076", 1)]
    assert order["total"] == {"amount": "42.79", "currency": "BRL"}
    stored = buyer(erp, "temu", "205925390", PO)
    assert stored["ready"] and stored["buyer"]["document"] == "12345678909"
    assert {k: stored["recipient"][k] for k in ("street", "number", "complement", "district")} == {
        "street": "Rua das Flores",
        "number": "120",
        "complement": "Casa 2",
        "district": "Centro",
    }
    # The next export brings the same order: nothing new is kept.
    again = upseller_import.import_sheet(erp, sheet([temu()]), "temu")
    assert (again["novos"], again["iguais"]) == (0, 1) and len(observations(erp)) == 1
    # Cancelled at Temu: the newest reading says so, and wins.
    cancelled = upseller_import.import_sheet(erp, sheet([temu(state="Cancelado")]), "temu")
    assert (cancelled["atualizados"], cancelled["cancelados"]) == (1, 1)
    readings = observations(erp)
    assert [o["status"] for _, _, o in readings] == ["UPSELLER_COPY", "CANCELLED"]
    assert readings[1][1] > readings[0][1]


def test_an_order_the_copy_of_bling_brought_is_compared_not_kept_twice(erp):
    service_loop.adopt_bling_copy_stores(erp)
    copy = {"id": PO, "status": "BLING_COPY", "items": [{"seller_sku": "a076", "quantity": 2}]}
    with erp.connection() as db:
        db.execute(
            "INSERT INTO native_order_reads VALUES "
            "('d1','bling_copy_v1_read','2026-11-27T10:00:00+00:00','{}')"
        )
        db.execute(
            "INSERT INTO native_order_observations VALUES ('o1','d1','temu','205925390',?,"
            "'2026-11-27T09:00:00+00:00','2026-11-27T10:00:00+00:00',?)",
            (PO, dumps(copy)),
        )
        db.commit()
    result = upseller_import.import_sheet(erp, sheet([temu(quantity=1)]), "temu")
    assert (result["novos"], result["conferidos"]) == (0, 1)
    assert result["divergencias"] == [{"pedido": PO, "campos": ["itens"]}]
    assert [s for s, _, _ in observations(erp)] == ["bling_copy_v1_read"]


def test_the_amazon_buyer_fills_the_gap_and_one_from_bling_is_compared_never_replaced(erp):
    store = "0b18f356-234c-42f2-8641-ffe254a6b69c"
    known = "701-0000002-0000002"
    # Bling's copy already gave this buyer, with house 124.
    recipient_capture.capture(
        erp,
        "amazon",
        store,
        known,
        {
            "name": "João Teste",
            "document": "98765432100",
            "street": "Rua Sete",
            "number": "124",
            "complement": "",
            "district": "Vila Nova",
            "city": "Joinville",
            "state": "SC",
            "postal_code": "89200000",
        },
    )
    rows = [
        amazon("701-0000001-0000001", "Avenida Brasil, 55", "Jardim América"),
        amazon(known, "Rua Sete - Vila Nova", "123"),
        amazon("701-0000003-0000003", "Rua Treze de Maio 450", "apto 12"),
        amazon("701-0000004-0000004", "Rua Um, 1", "Centro", store="Amazon Desconhecida"),
    ]
    head = [c for c in HEAD if c not in upseller_import.REQUIRED_ORDERS]
    result = upseller_import.import_sheet(erp, sheet(rows, head), "amazon")
    assert result["pedidos"] == 4 and result["sem_loja"] == ["Amazon Desconhecida"]
    assert (result["compradores"], result["compradores_conferidos"]) == (1, 1)
    assert result["compradores_divergentes"] == [{"pedido": known, "campos": ["recipient.number"]}]
    assert result["sem_endereco_completo"] == ["701-0000003-0000003"]
    assert buyer(erp, "amazon", store, known)["recipient"]["number"] == "124"
    filled = buyer(erp, "amazon", store, "701-0000001-0000001")
    assert filled["ready"] and filled["recipient"]["district"] == "Jardim América"
    assert buyer(erp, "amazon", store, "701-0000003-0000003") is None
    # Amazon orders come from Amazon itself: none is created from the spreadsheet.
    assert observations(erp, "701-0000001-0000001") == []
    text = json.dumps(result, ensure_ascii=False)
    assert "João" not in text and "98765432100" not in text and "Brasil" not in text


def test_a_sheet_missing_a_column_or_from_an_unknown_store_brings_nothing(erp):
    service_loop.adopt_bling_copy_stores(erp)
    with pytest.raises(ERPError) as missing:
        upseller_import.import_sheet(
            erp, sheet([temu()], [c for c in HEAD if c != "Bairro"]), "temu"
        )
    assert missing.value.code == "upseller_columns" and "Bairro" in missing.value.message
    unknown = upseller_import.import_sheet(erp, sheet([temu(store="Temu Desconhecida")]), "temu")
    assert unknown["sem_loja"] == ["Temu Desconhecida"] and unknown["novos"] == 0
    assert observations(erp) == []
    with pytest.raises(ERPError):
        upseller_import.import_sheet(erp, b"not a spreadsheet", "temu")


def test_the_route_takes_the_file_in_read_only_mode_and_answers_nothing_personal(tmp_path):
    with app_client(tmp_path) as c:
        erp = c.app.state.erp
        mirrored(erp)
        service_loop.adopt_bling_copy_stores(erp)
        assert c.get("/api/erp-local/state").json()["consulta"]["active"] is True
        reply = c.post(
            "/api/erp-local/upseller-import",
            files={"arquivo": ("Export_Order.xlsx", sheet([temu()]))},
            data={"plataforma": "temu", "filtro": "Temu, 3 dias, total 1"},
            headers=LOCAL,
        )
        assert reply.status_code == 200, reply.text
        assert reply.json()["novos"] == 1 and "Maria" not in reply.text
        last = c.get("/api/erp-local/state").json()["upseller_imports"]["temu"]
        assert last["novos"] == 1 and last["filtro"] == "Temu, 3 dias, total 1"
        sales = c.get("/api/erp-local/store-sales", params={"q": PO}).json()["items"]
        assert [(s["number"], s["status"]) for s in sales] == [(PO, "Cópia do UpSeller")]
