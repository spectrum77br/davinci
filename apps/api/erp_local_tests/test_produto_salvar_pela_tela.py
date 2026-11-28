"""M-70 (26/11/2026): o cadastro do produto no Produtos 2 ficou limpo — sem foto, Formato,
Tipo, Preço venda, Unidade, Condição, a grade dos 16 campos e as abas Imagens, Fornecedores,
Tributação, Tabela de Preços e Vínculos. Tirar da tela não pode apagar nada: ao salvar, o que não
aparece volta exatamente como veio.

M-109/M-110 (27/11/2026): saíram também a aba Estoque (Mínimo, Máximo, Crossdocking, Localização) e as
dobras Código DUN, Listas de preço, Descrição complementar, Link Externo, Vídeo, Observações e Tags.
Essas chaves nem vão no comando (ausente = o servidor mantém), e o custo só vai quando mexido no
Custo (R$), que mostra duas casas e nunca corta as outras.

A prova vai do começo ao fim, pelo caminho de verdade: um produto com TUDO preenchido (cadastro
inteiro, NCM e fiscal, fornecedor, imagem, categoria, preço e custo) é lido pela API do ERP,
aberto e salvo pela tela de verdade (tela_produto.cjs: os arquivos do ERP num navegador de
mentira), e o comando que a tela manda volta pela mesma API. Depois, o produto lido de novo é
comparado campo a campo com o de antes.
"""

import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import client
from test_contacts import save as contact
from test_core import run

from app.erp_local import product_images

HERE = Path(__file__).resolve().parent
TELA = HERE / "tela_produto.cjs"
JSDOM = HERE.parents[1] / "web" / "node_modules" / "jsdom"
NODE = shutil.which("node")
SKU = "demo.aparelho"

pytestmark = pytest.mark.skipif(
    not NODE or not JSDOM.exists(), reason="precisa do node e do jsdom de apps/web"
)


def _preencher(erp, monkeypatch):
    """Tudo o que o cadastro guarda, preenchido de uma vez (os campos fora da tela também)."""
    supplier = contact(erp, roles=["supplier"])
    category = run(erp, "category.save", {"data": {"name": "Malas"}})["id"]
    # A conferência da imagem (Pillow) não é o assunto aqui: a imagem só precisa existir.
    monkeypatch.setattr(
        product_images,
        "inspect_image",
        lambda raw, filename: {
            "format": "PNG",
            "width": 8,
            "height": 6,
            "filename": filename,
            "size": len(raw),
        },
    )
    image = product_images.upload(erp, b"imagem de teste", "frente.png")["image"]
    run(
        erp,
        "product.update",
        {
            "sku": SKU,
            "version": 0,
            "data": {
                "name": "Aparelho completo",
                "price": "199.90",
                "cost_price": "80",
                "unit": "CX",
                "gtin": "7908855913168",
                "gtin_tax": "4006381333931",
                "status": "active",
                "brand": "Uranyx",
                "location": "A1-03",
                "weight_kg": "0.5",
                "height_cm": "5",
                "width_cm": "9.5",
                "length_cm": "17",
                "condition": "new",
                "production": "third_party",
                "expiry_date": "2027-01-31",
                "free_shipping": True,
                "net_weight_kg": "0.4",
                "gross_weight_kg": "0.45",
                "volumes": 2,
                "items_per_box": "10",
                "dimension_unit": "mm",
                "department": "adultoU",
                "dun": [{"code": "17891234567892", "quantity": "12"}],
                "short_description_html": "<p>Curta</p>",
                "complementary_html": "<p>Complementar</p>",
                "external_link": "https://exemplo.com/p",
                "video_url": "https://exemplo.com/v",
                "notes": "Nota",
                "tags": ["Azul", "Mala"],
                "stock_min": "1",
                "stock_max": "9",
                "crossdocking": 3,
                "voltage": "bivolt",
            },
            "tax": {
                "version": 0,
                "data": {
                    "origin": "1",
                    "ncm": "8517.12.31",
                    "cest": "21.064.00",
                    "item_type": "00",
                    "approximate_tax_percent": "12.5",
                    "tipi_exception": "001",
                    "additional_info": "Informação fiscal",
                },
                "reason": "Cadastro fiscal",
            },
            "suppliers": {
                "version": 0,
                "rows": [
                    {
                        "contact_id": supplier["id"],
                        "description": "Aparelho no fornecedor",
                        "code": "F-1",
                        "purchase_price": "70",
                        "cost_price": "75",
                        "warranty_months": 12,
                        "is_default": True,
                    }
                ],
                "reason": "Fornecedor",
            },
            "images": {
                "version": 0,
                "rows": [{"id": image["id"], "alt": "Frente"}],
                "reason": "Foto",
            },
            "category": {"version": 0, "category_id": category},
        },
    )


def _tela(state, full, edits=()):
    out = subprocess.run(  # noqa: S603 — o node do sistema e um arquivo do próprio repositório
        [NODE, str(TELA)],
        input=json.dumps({"state": state, "full": full, "edits": list(edits)}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _salvar_pela_tela(c, edits=()):
    before = c.get(f"/api/erp-local/products/{SKU}/full").json()
    screen = _tela(c.get("/api/erp-local/state").json(), before, edits)
    assert [cmd["kind"] for cmd in screen["commands"]] == ["product.update"], screen["notice"]
    payload = screen["commands"][0]["payload"]
    r = c.post(
        "/api/erp-local/commands",
        json={"operation_key": str(uuid4()), "kind": "product.update", "payload": payload},
        headers={"X-ERP-Local": "1"},
    )
    assert r.status_code == 200, r.text
    return before, payload, c.get(f"/api/erp-local/products/{SKU}/full").json()


# O que saiu da tela (M-109/M-110) e não pode ir no comando.
FORA_DA_TELA = (
    "stock_min",
    "stock_max",
    "crossdocking",
    "location",
    "dun",
    "complementary_html",
    "description",
    "external_link",
    "video_url",
    "notes",
    "tags",
)


def _igual_menos(before, after, mudou, fora=None):
    """Campo a campo: só a versão do cadastro sobe e só o que foi mudado na tela muda (`fora`: o
    que muda fora de `details`, como o custo)."""
    fora = fora or {}
    assert set(after) == set(before)
    for key in before:
        if key == "details":
            continue
        assert after[key] == fora.get(key, before[key]), key
    assert after["details"]["version"] == before["details"]["version"] + 1
    for key in before["details"]:
        if key == "version":
            continue
        expected = mudou.get(key, before["details"][key])
        assert after["details"][key] == expected, key


def test_salvar_sem_mexer_devolve_o_produto_inteiro_como_veio(tmp_path, monkeypatch):
    with client(tmp_path) as c:
        erp = c.app.state.erp
        run(erp, "demo.seed", {})
        _preencher(erp, monkeypatch)
        before, payload, after = _salvar_pela_tela(c)
        # Fiscal, fornecedores, imagens, tipo e o preço de venda nem são enviados; nem o custo
        # (ninguém mexeu) e o que saiu da tela (M-109/M-110).
        assert set(payload) == {"sku", "version", "data"}
        assert "price" not in payload["data"] and "cost_price" not in payload["data"]
        for key in FORA_DA_TELA:
            assert key not in payload["data"], key
        # O que tinha antes continua lá (os dados das abas que saíram também).
        assert before["tax"]["data"]["ncm"] == "85171231" and before["tax"]["version"] == 1
        assert before["suppliers"]["rows"][0]["code"] == "F-1"
        assert before["images"]["rows"][0]["alt"] == "Frente"
        assert before["category"]["id"] and before["price"] == "199.9000"
        assert before["details"]["voltage"] == "bivolt" and before["details"]["unit"] == "CX"
        assert before["details"]["notes"] == "Nota" and before["details"]["tags"] == ["Azul", "Mala"]
        assert before["details"]["stock_min"] and before["details"]["location"] == "A1-03"
        assert before["cost_price"] == "80.0000"
        _igual_menos(before, after, {})


def test_salvar_com_mudanca_na_tela_muda_so_o_que_foi_mexido(tmp_path, monkeypatch):
    with client(tmp_path) as c:
        erp = c.app.state.erp
        run(erp, "demo.seed", {})
        _preencher(erp, monkeypatch)
        # M-72: o Peso (o Peso Bruto de sempre) mexido também vai. M-109: o Custo (R$) mexido vai
        # com o número digitado (a vírgula vira ponto).
        before, payload, after = _salvar_pela_tela(
            c,
            [
                ["[name=status]", False],
                ["[name=gross_weight_kg]", "0.5"],
                ["[name=cost_price]", "81,50"],
            ],
        )
        assert payload["data"]["gross_weight_kg"] == "0.5"
        assert payload["data"]["cost_price"] == "81.50"
        for key in FORA_DA_TELA:
            assert key not in payload["data"], key
        _igual_menos(
            before,
            after,
            {"status": "inactive", "gross_weight_kg": "0.500"},
            {"cost_price": "81.5000"},
        )
