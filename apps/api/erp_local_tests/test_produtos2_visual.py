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


def test_o_titulo_do_editor_de_produto_tem_a_regra_dele():
    # Revisão (24/11): `body.embedded[data-screen=products] header h1` (0,2,3) ganhava de
    # `body[data-product-editor=true] header h1` (0,1,3) e mudava o nome do produto no editor junto.
    # O título da lista continua só na regra dela; o do editor, desde a M-112 (27/11/2026), tem as
    # letras da Tabela de Preços na regra do próprio editor (22px, 600, -0,55px, rgb(15,23,41)).
    css = _css()
    assert f"{TELA} header h1{{" not in css
    editor = "body[data-product-editor=true] header h1"
    assert (
        f'{editor}{{font-family:ui-sans-serif,system-ui,sans-serif,"Apple Color Emoji","Segoe UI Emoji","Segoe UI Symbol","Noto Color Emoji";'
        "font-size:22px;font-weight:600;letter-spacing:-.55px;color:rgb(15,23,41);line-height:1.4;white-space:normal}"
    ) in css


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
    # M-113 (27/11/2026): o Preço virou Custo (mesma largura) e o número do Estoque ficou na letra das
    # outras células (rgb(15,23,41), peso 400), sem fundo; o link das peças do kit continua azul.
    assert ".p2-col-price" not in css
    assert (
        f"{GRADE} .p2-stock{{padding:0;border:0;border-radius:0;background:none;font:inherit;font-weight:400;color:rgb(15,23,41);"
        in css
    )
    assert (
        f"{GRADE} .p2-link{{padding:0;border:0;border-radius:0;background:none;font:inherit;font-weight:500;color:rgb(37,99,235);"
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
    # M-113: os botões do painel de estoque em 12px, peso 500.
    assert "font:inherit;font-size:12px;font-weight:500;line-height:16px" in _regra(css, ".p2-grid .p2-side-button")


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


def test_m70_editor_sem_reticencias_e_alto_em_linhas_de_caixas_iguais():
    # M-70 (26/11/2026): o "… ›" sai só do título do cadastro do produto; as outras telas ficam.
    # M-112 (27/11/2026): as linhas passaram de 7 para 9 colunas iguais em tela larga.
    css = _css()
    assert "body[data-product-editor=true] header h1:before{content:none}" in css
    assert "header h1:before{content:'… ›'" in css
    assert (
        ".product-head-row{display:grid;grid-template-columns:repeat(9,minmax(0,1fr));gap:16px;"
        in css
    )
    # A linha só com caixas reservadas (dos pedidos seguintes) não ocupa espaço.
    assert ".product-head-row:not(:has(label,a,button)){display:none}" in css
    for velha in (".product-head-image{", ".product-head-grid{", ".product-status-price{"):
        assert velha not in css, velha


def test_m72_fotos_e_embalagem_com_o_selo_da_tabela_de_precos():
    # Mesmas cores da classeEtiquetaMidia de pages/pricing/[tab].vue.
    css = _css()
    assert ".p2-midia.is-vazio{border-style:dashed;border-color:rgba(107,114,128,.4)" in css
    assert ".p2-midia.is-ok{border-color:rgb(186,230,253);background:rgb(240,249,255)" in css
    assert ".p2-midia.is-ambar{border-color:rgb(252,211,77);background:rgb(255,251,235)" in css
    # M-112: rótulo em cima e o botão (28px, raio 8px, 12px/500) embaixo.
    assert (
        ".p2-midia-cell{grid-template-rows:auto 28px;align-items:center;justify-items:start}" in css
    )
    assert (
        "min-width:5.5rem;height:28px;padding:0 10px;border:1px solid;border-radius:8px;"
        "font-size:12px;font-weight:500;" in css
    )


# M-107…M-112 (27/11/2026): o cadastro do produto. O comportamento está em ui.cjs; aqui, as medidas
# dos pedidos no CSS.
EDITOR = "body[data-product-editor=true]"
FONTE = 'font-family:ui-sans-serif,system-ui,sans-serif,"Apple Color Emoji","Segoe UI Emoji","Segoe UI Symbol","Noto Color Emoji"'


def test_m112_grade_de_nove_colunas_com_os_grupos_e_quebra_em_quatro_e_dois():
    css = _css()
    for regra in (
        ".product-head-row>[data-campo=nome]{grid-column:span 5}",
        ".product-head-row>.product-group[data-grupo=certificado]{grid-column:span 5}",
        ".product-head-row>.product-group[data-grupo=dimensoes]{grid-column:span 4}",
        ".product-head-row>.product-group[data-grupo=venda]{grid-column:1/-1}",
        ".product-group-cells{display:grid;gap:16px;grid-template-columns:repeat(var(--p2-colunas,9),minmax(0,1fr))}",
        ".product-group[data-grupo=certificado]>.product-group-cells{--p2-colunas:5}",
        ".product-group[data-grupo=dimensoes]>.product-group-cells{--p2-colunas:4}",
        ".product-group[data-grupo=venda] [data-campo=duimp_padrao]{grid-column:span 2}",
        # Título do grupo: maiúsculas, 11px, 700, 0,55px, cinza, fio de 0,75px embaixo.
        ".product-group-title{margin:0 0 8px;padding:0 0 4px;border-bottom:.75px solid rgb(229,231,235);"
        "font-size:11px;line-height:16px;font-weight:700;letter-spacing:.55px;text-transform:uppercase;"
        "color:rgb(107,114,128)}",
    ):
        assert regra in css, regra
    # Abaixo de 1100px (a área do Produtos 2 no iframe): 4 por linha, o Nome e cada grupo na linha
    # inteira, o DUIMP com 1 coluna; abaixo de 620px, 2 por linha.
    estreita = css[css.index("@media(max-width:1099.98px){\n .product-head-row") :]
    for regra in (
        ".product-head-row,.product-group-cells{grid-template-columns:repeat(4,minmax(0,1fr))}",
        ".product-head-row>[data-campo=nome],.product-head-row>.product-group[data-grupo]{grid-column:1/-1}",
        ".product-group[data-grupo=venda] [data-campo=duimp_padrao]{grid-column:auto}",
    ):
        assert regra in estreita[: estreita.index("}\n}")], regra
    assert (
        "@media(max-width:619.98px){.product-head-row,.product-group-cells{grid-template-columns:repeat(2,minmax(0,1fr))}}"
        in css
    )
    # As quebras antigas (900px e 560px) não mexem mais no alto do cadastro.
    assert "@media(max-width:900px){.product-head-row" not in css
    assert "@media(max-width:560px){.product-head-row" not in css


def test_m112_letras_e_caixas_da_tabela_de_precos_so_no_cadastro():
    css = _css()
    assert f"{EDITOR} #content,{EDITOR} header,{EDITOR} #notice,{EDITOR} dialog{{{FONTE}}}" in css
    for regra in (
        # Rótulo 11px/600 e valor 12px/400, rgb(15,23,41); caixas de 28px, borda 0,75px, raio 4px.
        "#product-edit-form .product-head-cell>label,#product-edit-form .product-descricao-curta>.field-title"
        "{font-size:11px;line-height:16px;font-weight:600;color:rgb(15,23,41)}",
        "#product-edit-form .product-head-cell input:not([type=checkbox]):not([type=hidden]){height:28px;"
        "min-height:28px;padding:0 8px;border:.75px solid rgb(229,231,235);border-radius:4px;font-size:12px;"
        "line-height:16px;font-weight:400;color:rgb(15,23,41)",
        ".p2-menu-caixa{display:flex;align-items:center;justify-content:space-between;gap:6px;width:100%;"
        "height:28px;min-height:28px;padding:0 8px;border:.75px solid rgb(229,231,235);border-radius:4px;"
        "background:#fff;color:rgb(15,23,41);font-size:12px;font-weight:400;",
        ".p2-regra-botao{display:block;width:100%;height:28px;min-height:28px;padding:0 8px;"
        "border:.75px solid rgb(229,231,235);border-radius:4px;background:#fff;color:rgb(15,23,41);"
        "font-size:12px;font-weight:400;",
        # Abas 14px/400 (ativa rgb(15,23,41), as outras rgb(107,114,128)).
        "#product-edit-form [role=tab]{background:transparent;border:0;border-radius:0;padding:9px 10px;"
        "color:rgb(107,114,128);box-shadow:none;border-bottom:3px solid transparent;font-size:14px;"
        "font-weight:400;line-height:20px}",
        "#product-edit-form [role=tab][aria-selected=true]{border-bottom-color:var(--blue,#2453d4);"
        "color:rgb(15,23,41);font-weight:400}",
        # Fechar (e o Salvar de fora do modo Consulta) igual ao Atualizar: 12px/500, 28px, raio 8px.
        "#product-edit-form>.form-footer button{height:28px;min-height:28px;padding:0 10px;border-radius:8px;"
        "font-size:12px;line-height:16px;font-weight:500}",
        "#product-edit-form>.form-footer button.secondary{border:1px solid rgb(229,231,235);background:#fff;"
        "color:rgb(15,23,41)}",
        # M-107: sem cor, "—" em cinza.
        ".p2-menu-caixa.is-vazio>span{color:rgb(107,114,128)}",
    ):
        assert regra in css, regra
    # A fonte do cadastro é só do cadastro: a lista continua com as regras dela.
    assert "body[data-screen=products] #content{font-family" not in css


def test_m110_sem_o_painel_da_direita_e_largura_toda_so_com_o_cadastro_aberto():
    css = _css()
    for regra in (
        f"{EDITOR} #action-rail{{display:none!important}}",
        f"{EDITOR} .workspace header{{padding-right:14px}}",
        f"{EDITOR} #content{{padding-right:14px}}",
        f"{EDITOR} #notice{{margin-right:14px}}",
        f"{EDITOR} footer{{padding-right:14px}}",
    ):
        assert regra in css, regra
    # As outras telas continuam reservando os 238px do painel.
    assert "#content{padding:0 238px 24px 10px;" in css
    for velha in (".product-history-action{", ".product-tag{", ".dun-remove{"):
        assert velha not in css, velha


def test_m111_tabela_da_estrutura_nas_letras_da_tabela_de_precos():
    css = _css()
    for regra in (
        ".p2-estrutura-tipo-linha>[data-campo=tipo_estoque]{grid-column:span 2}",
        ".p2-estrutura-moldura{border:.75px solid rgb(229,231,235);border-radius:4px;",
        ".p2-estrutura>thead>tr>th{padding:0 8px;border:0;background:#fff;font-size:11px;line-height:16px;font-weight:600;",
        ".p2-estrutura>tbody>tr,.p2-estrutura>tfoot>tr{height:34px}",
        ".p2-estrutura>tbody>tr>td,.p2-estrutura>tfoot>tr>td{padding:4px 8px;border:0;border-top:1px solid rgb(229,231,235);"
        "background:#fff;font-size:12px;line-height:16px;font-weight:400;color:rgb(15,23,41);",
        ".p2-estrutura>thead>tr>.num,.p2-estrutura>tbody>tr>.num,.p2-estrutura>tfoot>tr>.num{text-align:right;",
        ".p2-estrutura>thead>tr>.p2-estrutura-n,.p2-estrutura>tbody>tr>.p2-estrutura-n{width:36px;text-align:center;color:rgb(107,114,128)}",
        ".p2-estrutura>tfoot>tr>td{font-weight:600;background:rgb(249,250,251)}",
        "#product-edit-form .p2-estrutura input.p2-estrutura-qtde{width:80px;height:24px;min-height:24px;padding:0 6px;"
        "border:.75px solid rgb(229,231,235);border-radius:4px;",
        ".p2-estrutura-del{display:inline-flex;align-items:center;justify-content:center;padding:4px;border:0;"
        "border-radius:4px;background:none;color:rgb(220,38,38);",
        ".p2-estrutura-add{margin-top:8px;height:28px;min-height:28px;padding:0 10px;border:1px solid rgb(229,231,235);"
        "border-radius:8px;background:#fff;color:rgb(15,23,41);font-size:12px;line-height:16px;font-weight:500;",
    ):
        assert regra in css, regra


def _regra(css, seletor):
    """O corpo da regra cujo seletor é exatamente `seletor` (a primeira)."""
    inicio = css.index(seletor + "{") + len(seletor) + 1
    return css[inicio : css.index("}", inicio)]


# M-113…M-116 (27/11/2026): a lista com Custo, Cor e o Layout ▾ (colunas e filtros). O comportamento
# está em ui.cjs; aqui, as letras e medidas do pedido no CSS.
LISTA_JS = STATIC / "product-list.js"


def test_m113_rodape_de_paginas_com_as_letras_da_tabela_de_precos_so_na_lista():
    css = _css()
    rodape = _regra(css, ".product-list .reference-pager.p2-pager")
    assert rodape.startswith("font-family:ui-sans-serif,system-ui,sans-serif,")
    assert "font-size:12px;font-weight:400;" in rodape
    assert "color:rgb(107,114,128)" in rodape
    botao = _regra(css, ".product-list .reference-pager.p2-pager .action")
    for parte in (
        "height:28px;",
        "padding:0 10px;",
        "border:.75px solid rgb(229,231,235);",
        "border-radius:8px;",
        "background:#fff;",
        "color:rgb(15,23,41);",
        "font-size:12px;font-weight:500;",
    ):
        assert parte in botao, parte
    # As outras telas que usam .reference-pager ficam como eram.
    assert ".reference-pager{font-size:10px;color:#8594ac;margin-top:18px}" in css
    assert ".reference-pager button{font-size:10px}" in css
    js = (STATIC / "catalog.js").read_text(encoding="utf-8")
    assert js.count('class="reference-pager p2-pager"') == 1


def test_m115_larguras_das_colunas_iguais_no_css_e_na_conta_da_largura_minima():
    """Cada coluna do Layout tem a largura do pedido no CSS (.p2-col-<chave>) e a MESMA no
    p2ListColumns, que soma as à vista na largura mínima da tabela (Descrição ≥ 280px)."""
    import re

    css = _css()
    js = LISTA_JS.read_text(encoding="utf-8")
    colunas = re.findall(r"\['([a-z_]+)','([^']+)','([^']+)',(\d+)\]", js)
    assert [c[0] for c in colunas] == [
        "cor", "code", "model", "number", "category", "sale_category", "tag",
        "cost_center", "min_margin", "special", "cost", "photo", "packaging", "stock",
    ]
    pedido = {
        "cor": 90, "code": 130, "model": 90, "number": 130, "category": 120,
        "sale_category": 140, "tag": 90, "cost_center": 110, "min_margin": 110,
        "special": 170, "cost": 110, "photo": 70, "packaging": 90, "stock": 110,
    }
    no_css = {}
    sem_comentarios = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for seletores, largura in re.findall(r"([^{}]+)\{width:(\d+)px\}", sem_comentarios):
        for s in seletores.split(","):
            m = re.fullmatch(r"\s*\.p2-grid \.p2-col-([a-z_]+)\s*", s)
            if m:
                no_css[m.group(1)] = int(largura)
    for chave, _menu, _cabecalho, largura in colunas:
        assert int(largura) == pedido[chave] == no_css[chave], chave
    assert "P2_DESC_MIN=280" in js
    # A tabela rola para o lado dentro da moldura; a página não (a tela dividida tem minmax(0,…)).
    assert ".p2-frame.p2-list-frame{overflow-x:auto}" in css
    # Margem herdada em cinza.
    assert ".p2-grid>tbody>tr>td.p2-herdada{color:rgb(107,114,128)}" in css


def test_m116_layout_com_filtro_ligado_e_letras_do_menu():
    css = _css()
    assert ".catalog-bar .catalog-bar-button.is-filtered{border-color:rgb(96,165,250)}" in css
    opcoes = _regra(css, ".catalog-bar .p2-layout-menu label")
    assert "font-size:12px;line-height:16px;font-weight:400;" in opcoes
    link = _regra(css, ".catalog-bar .p2-layout-link")
    assert "color:rgb(36,99,235);" in link
    assert "font-size:11px;font-weight:500;" in link
    assert _regra(css, ".catalog-bar .p2-layout-link.is-on") == "font-weight:700"
    quadro = _regra(css, ".p2-layout-filter")
    assert "border:.75px solid rgb(229,231,235);" in quadro
    assert "border-radius:4px;" in quadro
    assert "background:rgb(249,250,251)" in quadro
    # Lista comprida rola dentro do filtro.
    assert "max-height:168px;overflow-y:auto" in _regra(css, ".p2-layout-values")

