"""Produtos 2 (o ERP dentro do DaVinci, /erp?embed=1#products): sem a faixa do modo e sem o
"Todas as lojas", e com as letras da Tabela de Preços — só nessa tela embutida (M-28, 24/11/2026).

Feito pelo robô de mudanças (fora das pastas dele) e revisado: o painel da direita (#action-rail)
sobe os mesmos 29 px da faixa (79 → 50 px) e continua começando logo abaixo da linha do título e do
Atualizar, como antes (conferido na tela: título 22–48 px, Atualizar até 49 px, painel em 50 px;
antes, título 50–78, Atualizar até 78, painel em 79)."""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "erp_local" / "static"
TELA = "body.embedded[data-screen=products]"


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
    assert f"{TELA} header h1{{font-family:ui-sans-serif,system-ui,sans-serif" in css
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
