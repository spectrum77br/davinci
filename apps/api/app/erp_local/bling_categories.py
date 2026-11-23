"""Bling's financial categories (Contas a pagar), by id, as Bling's own screen lists them.

Bling refuses the category list to DaVinci's app (`/categorias/receitas-despesas` answers 403), but
its Contas a pagar screen carries all of them; this table was read there, read only, on
23/11/2026 (64 categories; copy in davinci-erp `docs/bling-categorias-2026-11-23.json`). A
category created in Bling after that day still shows by its id ("Bling <id>").
"""

# id -> (name, parent id or None)
CATEGORIES = {
    "14693500507": ("ACORDO", "14640946901"),
    "14728734811": ("ADS/Marketing/Publicidade", None),
    "14640948587": ("Advocacia/Cartório", None),
    "14708970636": ("ÁGUA", "14693939024"),
    "14693501010": ("Aluguel", None),
    "14694083279": ("ANATEL", "14693633600"),
    "14700167185": ("ANUNCIO DIFERENTE", "14693438289"),
    "14695936823": ("AVARIAS", "14693438289"),
    "14694083276": ("BANCO", "14693633600"),
    "14694087090": ("Bonificação", None),
    "14694085716": ("BRAS", "14640946838"),
    "14708971022": ("CAIXAS PERSONALIZADAS", "14693922704"),
    "14705917468": ("CAPINHA", "14693438289"),
    "14695514628": ("CARREGADOR", "14693438289"),
    "14708970383": ("COMBUSTÍVEL", "14693920279"),
    "14640946838": ("Compras", None),
    "14694645561": ("Conserto", None),
    "14642080270": ("Consórcio", None),
    "14640948584": ("Contabilidade", None),
    "14693939024": ("Contas Consumo", None),
    "14694642998": ("DESCONTOS PAGAR-RECEBER", None),
    "14695348115": ("Desembalados", None),
    "14703221238": ("DEVOLVEU PRODUTO", "14693438289"),
    "14693646422": ("Doação", None),
    "14708971023": ("EMBALAGENS/CAIXAS/BOLHA/FITA ENVIO", "14693922704"),
    "14708970637": ("ENERGIA", "14693939024"),
    "14693500255": ("ENVIOS", "14640946901"),
    "14724559072": ("ERRO .CI", "14693438289"),
    "14694077116": ("ERRO .PI", "14693438289"),
    "14694077179": ("ERRO .RA", "14693438289"),
    "14708970319": ("ERRO .SA", "14693438289"),
    "14694077247": ("ERRO .SC", "14693438289"),
    "14694077181": ("ERRO .SP", "14693438289"),
    "14701402005": ("ERRO BARRACAO", "14693438289"),
    "14708972038": ("Financiamento", None),
    "14640946901": ("Folha", None),
    "14708970382": ("FRETE", "14693920279"),
    "14640948585": ("Frete", None),
    "14694077252": ("GARANTIA", "14693438289"),
    "14709689538": ("IMOBILIZADO (ATIVO)", "14693922704"),
    "14640948588": ("Importação", None),
    "14693438796": ("IMPORTADO", "14640946838"),
    "14667336881": ("Imposto", None),
    "14640946899": ("Insumos", None),
    "14708970704": ("INTERNET", "14693939024"),
    "14640946900": ("Investimento", None),
    "14693500635": ("Juros", None),
    "14712872550": ("LACRE/ADESIVOS/CHAVE", "14693922704"),
    "14640943346": ("Loteamento", None),
    "14693922704": ("Material", None),
    "14694077112": ("NFC/RAM", "14693438289"),
    "14641066770": ("Outros Reembolsos", None),
    "14693500318": ("PGTO", "14640946901"),
    "14693501078": ("Programa/Certificado/INPI", None),
    "14693634477": ("Recargas Chip", None),
    "14693438289": ("Reembolso Cliente", None),
    "14694091083": ("Retirada", None),
    "14694083280": ("SEGURO", "14693633600"),
    "14693633600": ("Taxa", None),
    "14708970767": ("TELEFONE", "14693939024"),
    "14708970508": ("TRANSPORTADORA", "14693920279"),
    "14693920279": ("Transporte", None),
    "14704377754": ("USADOS", "14640946838"),
    "14693438921": ("Y", "14640946838"),
}


def path(category_id):
    """The category as Bling's screen shows it under its parent ("Folha › PGTO"), or None."""
    found = CATEGORIES.get(str(category_id or ""))
    if not found:
        return None
    name, parent = found
    return f"{CATEGORIES[parent][0]} › {name}" if parent in CATEGORIES else name
