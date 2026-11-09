"""DaVinci's real Bling client against the own ERP, in process, with no network."""
# ruff: noqa: S105, S106 - Tokens below are synthetic test fixtures.

import asyncio
import hashlib
import hmac
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_core import balance, order, run

from app.erp_local import finance
from app.erp_local.api import create_app
from app.erp_local.bling_compat import FIRST_OWN_ID, TOKEN_ENV
from app.erp_local.core import dumps

TOKEN = "synthetic-compat-token"


BASE = "http://127.0.0.1:8765/api/erp-local/bling/v3"


@pytest.fixture
def bling(monkeypatch):
    """The real client file, with only its settings replaced: the same switch used in production."""
    name = "app.services.marketplaces"
    keys = [name, name + ".base", name + ".bling", "app.config"]
    previous = {key: sys.modules.get(key) for key in keys}
    package = ModuleType(name)
    package.__path__ = [str(Path(__file__).parents[1] / "app/services/marketplaces")]
    config = ModuleType("app.config")
    settings = SimpleNamespace(bling_api_base=BASE, bling_api_token=TOKEN)
    config.get_settings = lambda: settings
    monkeypatch.setitem(sys.modules, name, package)
    monkeypatch.setitem(sys.modules, "app.config", config)
    for key in keys[1:3]:
        sys.modules.pop(key, None)
    module = importlib.import_module(name + ".bling")
    yield module
    for key in keys[1:3]:
        if previous[key] is None:
            sys.modules.pop(key, None)


@pytest.fixture
def client(erp, bling, tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    app = create_app(erp.database)
    original = httpx.AsyncClient

    def transport(**kwargs):
        return original(transport=httpx.ASGITransport(app=app), **kwargs)

    async def no_gate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bling.httpx, "AsyncClient", transport)
    monkeypatch.setattr(bling, "_acquire_bling_rate_slot", no_gate)
    bling._DEFAULT_DEPOSIT_CACHE.clear()
    # An expired Bling login must not matter once the switch points at the ERP.
    return bling.BlingClient({"access_token": "velho", "expires_at": 1})


def go(awaitable):
    return asyncio.run(awaitable)


def test_requires_the_integration_token(erp, monkeypatch):
    app = create_app(erp.database)
    http = TestClient(app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False)
    assert http.get("/api/erp-local/bling/v3/produtos").status_code == 401
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    headers = {"Authorization": "Bearer outro"}
    assert http.get("/api/erp-local/bling/v3/produtos", headers=headers).status_code == 401
    headers = {"Authorization": "Bearer " + TOKEN}
    assert http.get("/api/erp-local/bling/v3/produtos", headers=headers).status_code == 200
    denied = http.post("/api/erp-local/bling/v3/produtos", json={"codigo": "x", "nome": "y"})
    assert denied.status_code == 401
    assert http.post("/api/erp-local/commands", json={}).status_code == 403


def test_catalog_reads_through_the_real_client(erp, client, bling):
    run(
        erp,
        "product.update",
        {
            "sku": "demo.aparelho",
            "version": 0,
            "data": {
                "price": "99.5",
                "cost_price": "40",
                "unit": "UN",
                "brand": "Marca",
                "gtin": "4006381333931",
                "gross_weight_kg": "1.5",
                "width_cm": "30",
                "dimension_unit": "m",
                "stock_min": "2",
                "condition": "new",
            },
        },
    )
    pages = go(client.list_products_page(pagina=1, limite=2))
    assert len(pages) == 2 and all(p["id"] > FIRST_OWN_ID for p in pages)
    found = go(client.find_active_product_by_sku("demo.aparelho"))
    assert found["sku"] == "demo.aparelho" and found["name"]
    assert go(client.product_exists_by_sku("demo.aparelho")) is True
    assert go(client.product_exists_by_sku("nao.existe")) is False
    assert go(client.cost_by_skus({"demo.aparelho"})) == {"demo.aparelho": 40.0}
    detail = go(client.get_product(found["id"]))
    assert detail["codigo"] == "demo.aparelho" and detail["marca"] == "Marca"
    assert detail["gtin"] == "4006381333931" and detail["pesoBruto"] == 1.5
    assert detail["dimensoes"] == {
        "largura": 0.3,
        "altura": 0.0,
        "profundidade": 0.0,
        "unidadeMedida": 0,
    }
    assert detail["condicao"] == 1 and detail["estoque"]["minimo"] == 2.0
    parsed = bling.parse_bling_product(detail)
    assert parsed["sku"] == "demo.aparelho" and parsed["price"] == 99.5
    assert parsed["stock"] == 8 and parsed["situacao"] == "A" and parsed["formato"] == "S"
    assert go(client.test_connection()).ok is True
    assert go(client.update_price(found["id"], 120)) == {"id": found["id"]}
    assert (
        next(p for p in erp.snapshot()["products"] if p["sku"] == "demo.aparelho")["price"]
        == "120.0000"
    )


def test_origin_ids_are_kept_so_davinci_rows_stay_valid(erp, client):
    with erp.connection() as db:
        db.execute(
            "INSERT INTO catalog_origin VALUES (?,?)",
            (
                "demo.fone",
                dumps(
                    {
                        "bling_product_id": "16710802485",
                        "catalog_state": "ready",
                        "sellable": True,
                        "stock_state": "known",
                        "source_status": "A",
                    }
                ),
            ),
        )
    assert go(client.get_product(16710802485))["codigo"] == "demo.fone"
    found = go(client.find_active_product_by_sku("demo.fone"))
    assert found["id"] == 16710802485


def test_product_structure_category_and_supplier_writes_use_erp_rules(erp, client, bling):
    category = go(client.find_or_create_category("Malas"))
    assert go(client.get_category_id_by_name("Malas")) == category
    assert go(client.find_or_create_category("malas")) == category
    part = go(client.find_active_product_by_sku("demo.aparelho"))["id"]
    created = go(
        client.create_product(
            name="Kit novo",
            sku="kit.novo",
            price=50,
            category_id=category,
            formato="E",
            estrutura={
                "tipoEstoque": "V",
                "lancamentoEstoque": "P",
                "componentes": [{"produto": {"id": part}, "quantidade": 2}],
            },
        )
    )
    products = {p["sku"]: p for p in erp.snapshot()["products"]}
    assert products["kit.novo"]["kind"] == "kit"
    assert products["kit.novo"]["category"]["path"][0]["name"] == "Malas"
    assert go(client.get_product(created["id"]))["estrutura"]["componentes"][0]["quantidade"] == 2
    go(
        client.update_product_estrutura(
            product_id=created["id"],
            estrutura={
                "tipoEstoque": "V",
                "lancamentoEstoque": "P",
                "componentes": [{"produto": {"id": part}, "quantidade": 3}],
            },
        )
    )
    products = {p["sku"]: p for p in erp.snapshot()["products"]}
    assert products["kit.novo"]["components"] == [{"sku": "demo.aparelho", "quantity": "3.000"}]
    contact = run(
        erp,
        "contact.save",
        {"data": {"name": "Fornecedor ACME", "roles": ["supplier"]}},
    )["id"]
    supplier = go(client.find_contato_id_by_name("fornecedor acme"))
    assert supplier and supplier > FIRST_OWN_ID
    assert go(client.get_contato(supplier))["nome"] == "Fornecedor ACME"
    go(client.link_supplier_to_product(product_id=part, supplier_id=supplier, cost_price=12.5))
    go(client.link_supplier_to_product(product_id=part, supplier_id=supplier, cost_price=13))
    rows = next(p for p in erp.snapshot()["products"] if p["sku"] == "demo.aparelho")["suppliers"][
        "rows"
    ]
    assert len(rows) == 1 and rows[0]["contact_id"] == contact
    assert rows[0]["cost_price"] == "13.0000"
    entry = go(
        client.create_conta_pagar(
            contato_id=supplier, valor=100.5, vencimento="2027-01-10", historico="Parcela DI"
        )
    )
    assert entry["id"] > FIRST_OWN_ID
    with erp.connection() as db:
        accounts = finance.snapshot(db)["installments"]
    assert any(i["kind"] == "payable" and i["amount"] == "100.50" for i in accounts)


def test_stock_writes_keep_the_ledger_and_refuse_services(erp, client):
    run(erp, "product.create", {"sku": "serv", "name": "Serviço", "kind": "service"})
    part = go(client.find_active_product_by_sku("demo.aparelho"))["id"]
    service = go(client.find_active_product_by_sku("serv"))["id"]
    depot = go(client.get_default_deposit_id())
    assert depot > FIRST_OWN_ID
    before = balance(erp)
    go(client.update_stock_by_id(part, 3, operation="E", observacao="Entrada DaVinci", custo=9.5))
    after = balance(erp)
    assert after != before
    with pytest.raises(httpx.HTTPStatusError):
        go(client.update_stock_by_id(service, 1, operation="E"))
    assert balance(erp) == after
    stock = go(client.get_product(part))["estoque"]["saldoVirtualTotal"]
    assert stock == 11.0


def test_order_reads_and_situations_match_the_client_expectations(erp, client):
    created = order(erp, external="LOJA-777", sku="demo.aparelho")
    before = balance(erp)
    rows = go(client.list_pedidos_vendas(numeros_lojas=["LOJA-777"]))
    assert len(rows) == 1 and rows[0]["situacao"]["id"] == 6
    assert rows[0]["numeroLoja"] == "LOJA-777"
    assert list(go(client.pedidos_por_numero_loja(["LOJA-777", "OUTRO"]))) == ["LOJA-777"]
    assert go(client.list_pedidos_vendas(id_situacao=12)) == []
    assert go(client.list_pedidos_vendas(data_inicial="2999-01-01")) == []
    identifier = rows[0]["id"]
    detail = go(client.get_order(identifier))
    assert detail["itens"][0]["codigo"] == "demo.aparelho" and detail["id"] == identifier
    go(client.update_order(identifier, {**detail, "observacoes": "Nota do DaVinci"}))
    assert go(client.get_order(identifier))["observacoes"] == "Nota do DaVinci"
    changed = {**detail, "itens": [{**detail["itens"][0], "quantidade": 9}]}
    with pytest.raises(httpx.HTTPStatusError):
        go(client.update_order(identifier, changed))
    # A label moves nothing; only cancellation releases the reservation.
    assert go(client.update_order_situacao(identifier, 83955)) is True
    assert go(client.update_order_situacao(identifier, 83955)) is False
    assert balance(erp) == before
    assert go(client.get_order(identifier))["situacao"]["id"] == 83955
    assert go(client.update_order_situacao(identifier, 12)) is True
    assert balance(erp) != before
    assert next(o for o in erp.snapshot()["orders"] if o["id"] == created["id"])["status"] == (
        "cancelled"
    )
    with pytest.raises(httpx.HTTPStatusError):
        go(client.update_order_situacao(identifier, 424242))


def test_situation_catalog_and_store_list_come_back_in_client_shape(erp, client):
    modules = go(client.list_situacoes_modulos())
    assert modules[0]["nome"] == "Vendas"
    names = {s["id"]: s["nome"] for s in go(client.list_situacoes_modulo(modules[0]["id"]))}
    assert names[6] == "Em aberto" and names[83955] == "Aguardando Cancelamento"
    assert names[21] == "Em digitação"
    assert isinstance(go(client.list_lojas()), list)


def test_davinci_two_step_kit_creation_builds_a_virtual_kit(erp, client):
    part = go(client.find_active_product_by_sku("demo.aparelho"))["id"]
    created = go(client.create_product(sku="kit.duas.etapas", name="Kit", formato="E"))
    go(
        client.update_product_estrutura(
            product_id=created["id"],
            estrutura={
                "tipoEstoque": "V",
                "lancamentoEstoque": "P",
                "componentes": [{"produto": {"id": part}, "quantidade": 2}],
            },
        )
    )
    product = next(p for p in erp.snapshot()["products"] if p["sku"] == "kit.duas.etapas")
    assert product["kind"] == "kit"
    assert product["components"] == [{"sku": "demo.aparelho", "quantity": "2.000"}]
    assert go(client.get_product(created["id"]))["formato"] == "E"
    with pytest.raises(httpx.HTTPStatusError):
        go(
            client.update_product_estrutura(
                product_id=part,
                estrutura={
                    "tipoEstoque": "V",
                    "lancamentoEstoque": "P",
                    "componentes": [{"produto": {"id": created["id"]}, "quantidade": 1}],
                },
            )
        )


def test_davinci_real_order_mapper_reads_what_the_compat_api_returns(erp, client):
    contact = run(
        erp,
        "contact.save",
        {
            "data": {
                "name": "Comprador Teste",
                "roles": ["customer"],
                "person_type": "F",
                "document": "52998224725",
                "postal_code": "30110000",
                "state": "MG",
                "city": "Belo Horizonte",
                "district": "Centro",
                "street": "Rua Exemplo",
                "number": "20",
            }
        },
    )["id"]
    run(
        erp,
        "order.import",
        {
            "channel": "Teste",
            "store": "A",
            "external_id": "LOJA-1",
            "depot": "geral",
            "contact_id": contact,
            "date": "2026-11-06",
            "items": [{"sku": "demo.aparelho", "quantity": "2", "unit_price": "50"}],
            "purchase_order": "PC-9",
            "recipient": {"name": "Quem Recebe", "street": "Rua Entrega", "state": "RJ"},
            "channel_fees": {"base": "90", "shipping_cost": "12.5", "commission": "15"},
        },
    )
    rows = go(client.list_pedidos_vendas(numeros_lojas=["LOJA-1"]))
    raw = go(client.get_order(rows[0]["id"]))
    # In its own process: the real mapper imports production settings, kept out of these tests.
    script = (
        "import json,sys\n"
        "from app.services.bling_orders import _row_from_item\n"
        "raw=json.load(sys.stdin)\n"
        "row=_row_from_item(raw,raw['itens'][0],item_index=0,store_id=None)\n"
        "print(json.dumps(row,default=str))\n"
    )
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        input=json.dumps(raw),
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
    )
    mapped = json.loads(done.stdout)
    assert mapped["bling_id"] == rows[0]["id"] and mapped["numeroloja"] == "LOJA-1"
    assert mapped["numero_documento"] == "PC-9"
    assert mapped["item_codigo"] == "demo.aparelho" and mapped["item_quantidade"] == 2
    assert float(mapped["itemvalor"]) == 50.0
    assert float(mapped["custofrete"]) == 12.5 and float(mapped["taxacomissao"]) == 15.0
    assert float(mapped["valorbase"]) == 90.0
    assert mapped["situacao"] == "6" and mapped["uf_destino"] == "RJ"
    assert mapped["endereco_destino"] == "Rua Entrega"


def test_stock_balance_and_invoice_endpoints_follow_the_client_shape(erp, client):
    part = go(client.find_active_product_by_sku("demo.aparelho"))["id"]
    response = go(client._request("GET", "/estoques/saldos", params=[("idsProdutos[]", str(part))]))
    row = response.json()["data"][0]
    assert row["produto"]["id"] == part and row["saldoVirtualTotal"] == 8.0
    assert row["saldoFisicoTotal"] == 8.0 and row["depositos"][0]["saldoFisico"] == 8.0
    assert go(client._request("GET", "/nfe", params={"pagina": 1, "limite": 1})).json() == {
        "data": []
    }
    assert go(client._request("GET", "/nfe/1")).status_code == 404


def test_logistics_object_and_order_events_are_exposed(erp, client):
    created = order(erp, external="LOJA-2", sku="demo.aparelho")
    with erp.connection() as db:
        queued = db.execute(
            "SELECT event,body FROM compat_outbox WHERE event='pedido.alteracao' ORDER BY id DESC"
        ).fetchone()
    assert queued["event"] == "pedido.alteracao"
    run(erp, "logistics.prepare", {"id": created["id"], "service": "Correios"})
    rows = go(client.list_pedidos_vendas(numeros_lojas=["LOJA-2"]))
    raw = go(client.get_order(rows[0]["id"]))
    assert json.loads(queued["body"])["dados"]["id"] == rows[0]["id"]
    volume = raw["transporte"]["volumes"][0]["id"]
    assert go(client.get_logistica_objeto(volume))["servico"]["nome"] == "Correios"


def test_outbox_delivers_signed_events_retries_and_never_repeats(erp, monkeypatch):
    from app.erp_local.bling_compat import deliver_outbox

    order(erp, external="LOJA-3", sku="demo.aparelho")
    seen = []

    def refuse(_body, _headers):
        return False

    first = deliver_outbox(erp, refuse, "segredo", now="2026-11-06T10:00:00+00:00")
    assert first == {"delivered": 0, "pending": first["pending"]} and first["pending"] >= 1
    waiting = deliver_outbox(erp, refuse, "segredo", now="2026-11-06T10:00:10+00:00")
    assert waiting == {"delivered": 0, "pending": 0}

    def accept(body, headers):
        expected = hmac.new(b"segredo", body, hashlib.sha256).hexdigest()
        assert headers["X-Bling-Signature"] == "sha256=" + expected
        seen.append(headers["X-Bling-Delivery"])
        return True

    done = deliver_outbox(erp, accept, "segredo", now="2026-11-06T10:05:00+00:00")
    assert done["delivered"] == len(seen) >= 1
    assert deliver_outbox(erp, accept, "segredo", now="2026-11-06T11:00:00+00:00")["delivered"] == 0


def test_stock_moves_announce_parts_and_kits_with_the_available_balance(erp):
    run(
        erp,
        "stock.post",
        {"sku": "demo.aparelho", "depot": "geral", "type": "E", "quantity": "2", "reason": "x"},
    )
    with erp.connection() as db:
        bodies = [
            json.loads(r[0])["dados"]
            for r in db.execute(
                "SELECT body FROM compat_outbox WHERE event='estoque.alteracao' ORDER BY id"
            )
        ]
    seen = {b["codigo"]: b["estoque"]["saldoVirtualTotal"] for b in bodies}
    assert seen["demo.aparelho"] == 10.0
    kit = next(p for p in erp.snapshot()["products"] if p["sku"] == "demo.kit")
    capacity = next(b for b in kit["balances"] if b["depot"] == "geral")["available"]
    assert seen["demo.kit"] == float(capacity)


def test_real_catalog_shape_is_covered_by_the_compat_detail(erp, client):
    """Every field read from a real Bling product must exist in what we answer."""
    read = Path(__file__).resolve().parents[3] / "data/erp-local/bling-catalog-read.jsonl"
    if not read.exists():
        pytest.skip("Leitura do cadastro não está nesta máquina")
    rows = (json.loads(line) for line in read.read_text("utf-8").splitlines())
    sample = next(r["data"] for r in rows if r["kind"] == "product")
    part = go(client.find_active_product_by_sku("demo.aparelho"))["id"]
    mine = go(client.get_product(part))
    ignored = {"image_paths", "video_url", "descricaoEmbalagemDiscreta", "artigoPerigoso"}
    assert set(sample) - set(mine) - ignored == set()
    assert set(sample["tributacao"]) - set(mine["tributacao"]) <= {
        "nFCI",
        "codigoItem",
        "classeEnquadramentoIpi",
        "valorIpiFixo",
        "codigoSeloIpi",
        "codigoANP",
        "descricaoANP",
        "percentualGLP",
        "percentualGasNacional",
        "percentualGasImportado",
        "valorPartida",
        "tipoArmamento",
        "descricaoCompletaArmamento",
        "grupoProduto",
    }
