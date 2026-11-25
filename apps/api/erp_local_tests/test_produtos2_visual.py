# ruff: noqa: E501 — as regras de CSS conferidas aqui são trechos exatos do bling-layout.css.
"""Produtos 2 (o ERP dentro do DaVinci, /erp?embed=1#products): sem a faixa do modo e sem o
"Todas as lojas", e com as letras da Tabela de Preços — só nessa tela embutida (M-28, 24/11/2026).

Feito pelo robô de mudanças (fora das pastas dele) e revisado: o painel da direita (#action-rail)
sobe os mesmos 29 px da faixa (79 → 50 px) e continua começando logo abaixo da linha do título e do
Atualizar, como antes (conferido na tela: título 22–48 px, Atualizar até 49 px, painel em 50 px;
antes, título 50–78, Atualizar até 78, painel em 79)."""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "erp_local" / "static"
TELA = "body.embedded[data-screen=products]"
# O título "Produtos" da lista; o editor de produto (nome do produto no título) fica como era.
TITULO = f"{TELA}:not([data-product-editor=true]) header h1"


def _css():
    return (STATIC / "bling-layout.css").read_text(encoding="utf-8")


def test_faixa_e_todas_as_lojas_somem_so_no_produtos_embutido():
    css = _css()
    assert f"{TELA} #environment,{TELA} #header-context{{display:none!important}}" in css
    # Fora do DaVinci (o ERP inteiro) e nas outras telas a faixa continua: nada de esconder geral.
    assert "body.embedded #environment" not in css
    assert "body.embedded #header-context" not in css


def test_titulo_abas_e_atualizar_com_as_letras_da_tabela_de_precos():
    css = _css()
    assert f"{TITULO}{{font-family:ui-sans-serif,system-ui,sans-serif" in css
    assert "font-size:22px;font-weight:600;letter-spacing:-.55px;color:rgb(15,23,41)" in css
    assert f"{TELA} #products-view-tabs{{gap:8px}}" in css
    assert (
        "font-size:14px;font-weight:400;line-height:20px;padding:6px 12px;border-radius:8px;"
        "border:1px solid rgb(229,231,235);background:#fff;color:rgb(15,23,41)"
    ) in css
    assert f"{TELA} #products-view-tabs button.active{{background:rgb(36,99,235)" in css
    assert f"{TELA} header #refresh{{" in css
    assert "font-size:12px;font-weight:500;line-height:16px;height:28px" in css
    assert f"{TELA} #action-rail{{top:50px}}" in css
    assert "body.embedded #action-rail{top:79px}" in css, "Pedidos 2 continua como estava"


def test_a_tela_ja_vem_marcada_na_abertura_embutida():
    # Sem isso a faixa apareceria por um instante antes do primeiro desenho.
    js = (STATIC / "navigation.js").read_text(encoding="utf-8")
    assert "document.body.classList.add('embedded');document.body.dataset.screen=page;" in js


def test_o_titulo_do_editor_de_produto_continua_como_era():
    # Revisão (24/11): `body.embedded[data-screen=products] header h1` (0,2,3) ganhava de
    # `body[data-product-editor=true] header h1` (0,1,3, 20px) e o nome do produto no editor
    # também ia para 22px/600. O pedido fala só do título "Produtos".
    css = _css()
    assert f"{TELA} header h1{{" not in css
    editor = "body[data-product-editor=true] header h1"
    assert f"{editor}{{font-size:20px;line-height:1.4;white-space:normal}}" in css


# M-40…M-47 (25/11/2026): a lista de Produtos no padrão da grade da Tabela de Preços, sem o painel
# da direita, e o estoque do produto ao lado (tela dividida 55/45). O comportamento está em ui.cjs;
# aqui, as medidas do pedido no CSS.
GRADE = ".p2-grid"


def test_m40_titulo_sem_reticencias_sem_painel_e_largura_toda_so_na_lista():
    css = _css()
    lista = "body[data-screen=products]:not([data-product-editor=true])"
    assert f"{lista} header h1:before{{content:none}}" in css
    assert f"{lista} #action-rail{{display:none!important}}" in css
    for regra in (
        f"{lista} .workspace header{{padding-right:14px}}",
        f"{lista} #content{{padding-right:14px}}",
        f"{lista} #notice{{margin-right:14px}}",
        f"{lista} footer{{padding-right:14px}}",
    ):
        assert regra in css, regra
    # O Atualizar do canto sai só da aba Produtos (o novo fica na barra), já no primeiro desenho;
    # a aba Anúncios e o editor ficam com ele.
    assert (
        f"{lista}:not([data-products-tab=listings]) header #refresh{{display:none!important}}"
        in css
    )
    # As colunas e as alturas antigas (imagem, 68px, preço azul em negrito) não sobram.
    for antiga in (
        ".product-list td{height:68px}",
        ".product-price{",
        ".product-image{",
        ".price-adjust{",
    ):
        assert antiga not in css, antiga


def test_m40_grade_com_as_medidas_da_tabela_de_precos():
    css = _css()
    fonte = 'font-family:ui-sans-serif,system-ui,sans-serif,"Apple Color Emoji","Segoe UI Emoji","Segoe UI Symbol","Noto Color Emoji"'
    assert ".p2-frame{border:1px solid rgb(229,231,235);border-radius:4px;background:#fff;" in css
    assert (
        f"{GRADE}{{width:100%;min-width:0;table-layout:fixed;border-collapse:separate;border-spacing:0;{fonte};font-size:12px;font-weight:400;"
        in css
    )
    assert f"{GRADE}>thead>tr,{GRADE}>tbody>tr.p2-row-head{{height:24px}}" in css
    assert f"{GRADE}>tbody>tr{{height:34px}}" in css
    assert (
        f"{GRADE}>thead>tr>th,{GRADE}>tbody>tr>th{{padding:0 8px;border:0;background:#fff;"
        "font-size:11px;font-weight:600;"
    ) in css
    assert (
        f"{GRADE}>tbody>tr>td{{padding:4px 8px;border:0;border-top:1px solid rgb(229,231,235);"
        "background:#fff;font-size:12px;font-weight:400;line-height:16px;color:rgb(15,23,41);"
    ) in css
    assert (
        f"{GRADE}>thead>tr>.p2-num,{GRADE}>tbody>tr>.p2-num{{text-align:center;border-left:1px solid rgb(229,231,235)}}"
        in css
    )
    assert f"{GRADE} .p2-col-code{{width:130px}}" in css
    assert f"{GRADE} .p2-col-price,{GRADE} .p2-col-stock{{width:110px}}" in css
    # Preço em letra normal; o número do Estoque azul, peso 500, sem fundo; contorno azul no aberto.
    assert (
        f"{GRADE} .p2-stock,{GRADE} .p2-link{{padding:0;border:0;border-radius:0;background:none;font:inherit;font-weight:500;color:rgb(37,99,235);"
        in css
    )
    assert (
        f"{GRADE}>tbody>tr>td.p2-stock-cell.is-open{{outline:2px solid rgb(37,99,235);outline-offset:-2px}}"
        in css
    )


def test_m42_tela_dividida_e_estoque_com_as_mesmas_linhas():
    css = _css()
    assert (
        ".p2-split{display:grid;grid-template-columns:minmax(0,55fr) minmax(0,45fr);column-gap:16px;align-items:start}"
        in css
    )
    # À direita: 24px com código - nome (linha do cabeçalho da lista), 34px para quadros, cabeçalho
    # Data | Entrada | Saída | Observação e lançamentos; quadros com rótulo 11px/600 e número 12px.
    assert ".p2-moves>thead>tr{height:34px}" in css
    assert ".p2-fact span{margin-right:6px;font-size:11px;font-weight:600;line-height:14px}" in css
    assert ".p2-fact strong{font-size:12px;font-weight:400;color:rgb(15,23,41)}" in css
    assert ".p2-grid .p2-side-button.is-primary{background:rgb(37,99,235)" in css


def test_m41_botoes_da_barra_no_tamanho_dos_filtros():
    css = _css()
    assert (
        ".catalog-bar .catalog-bar-primary{background:rgb(37,99,235);border-color:rgb(37,99,235);color:#fff}"
        in css
    )
    # Excel, Atualizar e Configuração usam a mesma classe dos filtros (28px, 12px, peso 500).
    assert (
        ".catalog-bar .catalog-bar-button{display:inline-flex;align-items:center;justify-content:center;gap:4px;height:28px;"
        in css
    )


def test_arquivos_novos_carregam_antes_do_app():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    ordem = [
        html.index(f'/assets/{nome}"')
        for nome in ("product-list.js", "xlsx.js", "product-stock.js", "app.js")
    ]
    assert ordem == sorted(ordem)
