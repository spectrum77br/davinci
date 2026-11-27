# ruff: noqa: E501 - the address cases read better one per line
"""Orders and buyers from UpSeller's export (Temu, Shein, AliExpress, Amazon), so they stop
depending on Bling.

All data here is made up; the shapes follow real exports of 27/11/2026 (Jesse's robot sent the
address formats of 608 Amazon orders, anonymized)."""

import base64
import io
import json
import os

import pytest
from openpyxl import Workbook
from test_consulta import LOCAL, app_client, mirrored

from app.erp_local import recipient_capture, service_loop, store_sales, upseller_import
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
    "Comissão Total",
    "Pós-venda/Cancelado/Devolvido",
    "SKU (Armazém)",
    "Quantidade de Produtos",
    "Nome do Produto",
    "Valor rateado por produto",
    "Nome do Anúncio",
    "SKU",
    "Preço de Produto",
    "NCM*",
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
    "Método de Envio",
    "Nº de Rastreio",
    "Chave da Nota Fiscal",
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


def sale(number=PO, *, platform="Temu", store="Temu Barbosa", state="Para Retirada", quantity=1,
         sku="a076"):  # fmt: skip
    return {
        "Nº de Pedido da Plataforma": number,
        "Nº de Pedido": "#UP2NYY000001",
        "Plataformas": platform,
        "Nome da Loja no UpSeller": store,
        "Estado do Pedido": state,
        "Hora do Pedido": "26/11/2026 23:36",
        "Hora do Pagamento": "26/11/2026 23:36",
        "Moeda": "BRL",
        "Valor do Pedido": "42,79",
        "Valor Total de Produtos": 42.79,
        "Comissão Total": "4,10",
        "SKU (Armazém)": sku,
        "Quantidade de Produtos": quantity,
        "Nome do Produto": "Produto de teste",
        "Nome do Anúncio": "Anúncio de teste",
        "SKU": sku,
        "Preço de Produto": "42,79",
        "NCM*": "85167920",
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
        "Método de Envio": "J&T express Drop off",
        "Nº de Rastreio": "888000000000001",
        "Chave da Nota Fiscal": "35261100000000000000550010000000011000000010",
    }


def amazon(number, line1, line2, store="Amazon KFA", **extra):
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
        **extra,
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


def plain(value):
    return json.loads(json.dumps(value))


@pytest.mark.parametrize(
    ("line1", "line2", "number", "district", "expected"),
    [
        # Temu: the district in its column, the number masked, the number in "Endereço 1".
        ("Rua das Flores, 120", "Casa 2", "*******", "Centro",
         ("Rua das Flores", "120", "Casa 2", "Centro", True)),
        # Amazon (number and district columns empty), the formats of 27/11/2026:
        ("Rua das Flores 120", "Casa Jardim América", "", "",
         ("Rua das Flores", "120", "Casa", "Jardim América", True)),
        ("Rua das Flores 120", "casa vila nova", "", "",
         ("Rua das Flores", "120", "casa", "vila nova", True)),
        ("Rua das Flores 120", "12 Centro", "", "", ("Rua das Flores", "120", "12", "Centro", True)),
        ("Rua 25 de Março 120", "apto 12 - Centro", "", "",
         ("Rua 25 de Março", "120", "apto 12", "Centro", True)),
        ("Avenida Brasil 55", "ap 12 centro", "", "", ("Avenida Brasil", "55", "ap 12", "centro", True)),
        ("Rua das Flores 120", "bl 3 ap 4 Jardim das Rosas", "", "",
         ("Rua das Flores", "120", "bl 3 ap 4", "Jardim das Rosas", True)),
        ("Rua Coronel Silva 120 loja 2", "Centro", "", "",
         ("Rua Coronel Silva", "120", "loja 2", "Centro", True)),
        ("Rua das Flores 120 Vila Nova", "", "", "", ("Rua das Flores", "120", "", "Vila Nova", True)),
        ("Avenida Brasil, 55", "Jardim América", "", "",
         ("Avenida Brasil", "55", "", "Jardim América", True)),
        ("Rua Sete - Vila Nova", "123", "", "", ("Rua Sete", "123", "", "Vila Nova", True)),
        ("Travessa Sao Jorge s/n", "Centro", "", "", ("Travessa Sao Jorge", "S/N", "", "Centro", True)),
        ("Rua 9 de Julho 450", "Bairro: Centro", "", "", ("Rua 9 de Julho", "450", "", "Centro", True)),
        ("Alameda dos Anjos - Setor - Norte 9", "residencial Lago", "", "",
         ("Alameda dos Anjos - Setor - Norte", "9", "", "residencial Lago", True)),
        # Two spaces split Amazon's complement from the district.
        ("Rua das Flores 120", "apto 12  Jardim América", "", "",
         ("Rua das Flores", "120", "apto 12", "Jardim América", True)),
        ("Rua das Flores 120", "sobrado fundos  Vila Nova", "", "",
         ("Rua das Flores", "120", "sobrado fundos", "Vila Nova", True)),
        # No district to tell apart: kept, marked to be checked.
        ("Rua das Flores, 120", "apt 12", "", "", ("Rua das Flores", "120", "apt 12", "", False)),
        ("Rua das Flores 120", "qd 9 lt 9", "", "", ("Rua das Flores", "120", "qd 9 lt 9", "", False)),
        ("Rua das Flores numero 120", "apartamento 4", "", "",
         ("Rua das Flores", "120", "apartamento 4", "", False)),
        ("Rua Alfa quadra 9 lote 9", "", "", "", ("Rua Alfa", "S/N", "quadra 9 lote 9", "", False)),
        ("Rua das Flores", "120", "", "", ("Rua das Flores", "120", "", "", False)),
    ],
)  # fmt: skip
def test_the_address_is_split_as_the_invoice_needs_it(line1, line2, number, district, expected):
    row = {"Endereço 1": line1, "Endereço 2": line2, "Número": number, "Bairro": district}
    got = upseller_import.split_address("amazon", row)
    assert (got["street"], got["number"], got["complement"], got["district"], got["certain"]) == (
        expected
    )


def test_a_temu_order_the_erp_lacks_comes_in_once_with_everything_and_its_cancellation_follows(
    erp,
):
    service_loop.adopt_bling_copy_stores(erp)
    first = upseller_import.import_sheet(erp, sheet([sale()]), "temu", "Temu, 3 dias")
    assert (first["novos"], first["compradores"], first["sem_loja"], first["dados_gravados"]) == (
        1,
        1,
        [],
        1,
    )
    ((source, _, order),) = observations(erp)
    assert source == upseller_import.SOURCE and order["status"] == "UPSELLER_COPY"
    assert [(i["seller_sku"], i["quantity"]) for i in order["items"]] == [("a076", 1)]
    assert order["items"][0]["listing"]["ncm"] == "85167920"
    assert order["items"][0]["unit_price"] == {"amount": "42.79", "currency": "BRL"}
    assert order["shipping"]["tracking"] == "888000000000001"
    assert order["values"]["commission"] == "4.10"
    stored = buyer(erp, "temu", "205925390", PO)
    assert stored["ready"] and stored["buyer"]["document"] == "12345678909"
    assert {k: stored["recipient"][k] for k in ("street", "number", "complement", "district")} == {
        "street": "Rua das Flores",
        "number": "120",
        "complement": "Casa 2",
        "district": "Centro",
    }
    # Every column, as UpSeller sent it, kept sealed.
    with erp.connection() as db:
        raw = upseller_import.raw_order(db, "temu", "205925390", PO)
        sealed = db.execute("SELECT sealed FROM upseller_orders").fetchone()[0]
    assert raw["columns"] == HEAD
    assert raw["rows"][0][HEAD.index("Nome do Destinatário")] == "Maria Teste"
    assert "Maria" not in sealed and "12345678909" not in sealed
    # The next export brings the same order: nothing new is kept.
    again = upseller_import.import_sheet(erp, sheet([sale()]), "temu")
    assert (again["novos"], again["iguais"], again["dados_gravados"]) == (0, 1, 0)
    assert len(observations(erp)) == 1
    # Cancelled at Temu: the newest reading says so, and wins.
    cancelled = upseller_import.import_sheet(erp, sheet([sale(state="Cancelado")]), "temu")
    assert (cancelled["atualizados"], cancelled["cancelados"], cancelled["dados_gravados"]) == (
        1,
        1,
        1,
    )
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
    unmapped = sale(quantity=1, sku="A076")
    unmapped["SKU (Armazém)"] = "e3"  # UpSeller's warehouse SKU, unmapped (27/11/2026)
    result = upseller_import.import_sheet(erp, sheet([unmapped]), "temu")
    assert (result["novos"], result["conferidos"], result["divergencias_total"]) == (0, 1, 1)
    # Product codes are compared without case or lot; the lists say what differs.
    assert plain(result["divergencias"]) == [
        {"pedido": PO, "campos": ["itens"], "upseller": [["a076", 1]], "bling": [["a076", 2]]}
    ]
    assert [s for s, _, _ in observations(erp)] == ["bling_copy_v1_read"]
    same = upseller_import.import_sheet(erp, sheet([sale(quantity=2, sku="A076")]), "temu")
    assert same["divergencias"] == [] and same["conferidos"] == 1


def test_the_amazon_buyer_is_always_kept_and_one_from_bling_is_compared_never_replaced(erp):
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
    unpaid = amazon("701-0000005-0000005", "", "", **{
        "Estado do Pedido": "Outras", "ID da Taxa": "", "Nome do Destinatário": "", "CEP": "",
    })  # fmt: skip
    rows = [
        amazon("701-0000001-0000001", "Avenida Brasil 55", "ap 12 Jardim América"),
        amazon(known, "Rua Sete - Vila Nova", "123"),
        amazon("701-0000003-0000003", "Rua Treze de Maio 450", "apto 12"),
        amazon("701-0000004-0000004", "Rua Um, 1", "Centro", store="Amazon Desconhecida"),
        unpaid,
    ]
    head = [c for c in HEAD if c not in upseller_import.REQUIRED_ORDERS]
    result = upseller_import.import_sheet(erp, sheet(rows, head), "amazon")
    assert result["pedidos"] == 5 and result["sem_loja"] == ["Amazon Desconhecida"]
    assert (result["compradores"], result["compradores_conferidos"]) == (2, 1)
    assert result["compradores_divergentes"] == [{"pedido": known, "campos": ["recipient.number"]}]
    assert result["sem_endereco_completo"] == ["701-0000003-0000003"]
    assert result["sem_dados_no_upseller"] == ["701-0000005-0000005"]
    assert result["sem_endereco_completo_total"] == 1 and result["sem_loja_total"] == 1
    assert buyer(erp, "amazon", store, known)["recipient"]["number"] == "124"
    filled = buyer(erp, "amazon", store, "701-0000001-0000001")
    assert filled["ready"] and filled["recipient"]["district"] == "Jardim América"
    assert filled["recipient"]["complement"] == "ap 12"
    # The one without a district is kept, but not ready for an invoice.
    review = buyer(erp, "amazon", store, "701-0000003-0000003")
    assert review and not review["ready"] and "endereço a conferir" in review["missing"]
    assert buyer(erp, "amazon", store, "701-0000005-0000005") is None
    # Amazon orders come from Amazon itself: none is created from the spreadsheet.
    assert observations(erp, "701-0000001-0000001") == []
    text = json.dumps(result, ensure_ascii=False)
    assert "João" not in text and "98765432100" not in text and "Brasil" not in text


def test_shein_and_aliexpress_come_in_from_upseller_in_their_own_stores(erp):
    shein = sale("ABC123456789012", platform="Shein", store="KFA", sku="b104")
    ali = sale("1234567890123456", platform="AliExpress", store="Shopee VIta", sku="c200")
    got = upseller_import.import_sheet(erp, sheet([shein]), "shein")
    assert (got["novos"], got["compradores"], got["sem_loja"]) == (1, 1, [])
    got = upseller_import.import_sheet(erp, sheet([ali]), "aliexpress")
    assert (got["novos"], got["compradores"], got["sem_loja"]) == (1, 1, [])
    with erp.connection() as db:
        policies = {
            (r[0], r[1]): r[2]
            for r in db.execute("SELECT channel,store,status FROM store_policies")
        }
    assert policies[("shein", "204808793")] == "identity_review"
    assert policies[("aliexpress", "205295441")] == "identity_review"
    ((source, _, order),) = observations(erp, "ABC123456789012")
    assert source == upseller_import.SOURCE and order["items"][0]["seller_sku"] == "b104"
    assert buyer(erp, "aliexpress", "205295441", "1234567890123456")["ready"]


def test_a_sheet_missing_a_column_or_from_an_unknown_store_brings_nothing(erp):
    service_loop.adopt_bling_copy_stores(erp)
    with pytest.raises(ERPError) as missing:
        upseller_import.import_sheet(
            erp, sheet([sale()], [c for c in HEAD if c != "Bairro"]), "temu"
        )
    assert missing.value.code == "upseller_columns" and "Bairro" in missing.value.message
    unknown = upseller_import.import_sheet(erp, sheet([sale(store="Temu Desconhecida")]), "temu")
    assert unknown["sem_loja"] == ["Temu Desconhecida"] and unknown["novos"] == 0
    assert observations(erp) == []
    with pytest.raises(ERPError):
        upseller_import.import_sheet(erp, b"not a spreadsheet", "temu")
    with pytest.raises(ERPError) as platform:
        upseller_import.import_sheet(erp, sheet([sale()]), "magalu")
    assert platform.value.code == "upseller_platform"


def test_a_sale_read_straight_and_by_the_copy_of_bling_shows_once(erp):
    """ML Fioree, 27/11/2026: read straight since 25/11 and still by the copy: 26 sales twice."""
    order = {"id": "2000016291374029", "status": "paid", "created_at": "2026-11-26T10:00:00+00:00"}
    with erp.connection() as db:
        db.execute(
            "INSERT INTO native_order_reads VALUES ('r1','x','2026-11-27T10:00:00+00:00','{}')"
        )
        for i, store in enumerate(("206368117", "f38bb655-b927-42ab-b109-b80e0e9d6616")):
            db.execute(
                "INSERT INTO native_order_observations VALUES (?,'r1','ml',?,?,"
                "'2026-11-27T09:00:00+00:00','2026-11-27T10:00:00+00:00',?)",
                (f"o{i}", store, order["id"], dumps(order)),
            )
        db.commit()
        sales = store_sales.listing(db, order["id"])["items"]
    assert [s["store_id"] for s in sales] == ["f38bb655-b927-42ab-b109-b80e0e9d6616"]


def test_the_route_takes_the_file_in_read_only_mode_and_answers_nothing_personal(tmp_path):
    with app_client(tmp_path) as c:
        erp = c.app.state.erp
        mirrored(erp)
        service_loop.adopt_bling_copy_stores(erp)
        assert c.get("/api/erp-local/state").json()["consulta"]["active"] is True
        reply = c.post(
            "/api/erp-local/upseller-import",
            files={"arquivo": ("Export_Order.xlsx", sheet([sale()]))},
            data={"plataforma": "temu", "filtro": "Temu, 3 dias, total 1"},
            headers=LOCAL,
        )
        assert reply.status_code == 200, reply.text
        assert reply.json()["novos"] == 1 and "Maria" not in reply.text
        last = c.get("/api/erp-local/state").json()["upseller_imports"]["temu"]
        assert last["novos"] == 1 and last["filtro"] == "Temu, 3 dias, total 1"
        sales = c.get("/api/erp-local/store-sales", params={"q": PO}).json()["items"]
        assert [(s["number"], s["status"]) for s in sales] == [(PO, "Cópia do UpSeller")]
