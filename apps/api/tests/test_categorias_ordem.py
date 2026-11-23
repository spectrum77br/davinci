"""M-23 (23/11/2026): as categorias em ordem alfabética — Celular, Eletro, Mala —
em tudo o que o servidor escreve para alguém ler (services/categorias_ordem.py).

Só ordem de exibição: o relatório guardado da Conferência continua na ordem em
que foi calculado; quem escreve o arquivo (Excel, HTML, CSV, MD, Threema)
ordena. Sem banco, sem rede.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from app.services import categorias_ordem as co
from app.services.conferencia_shopee import saida


def test_a_ordem_e_alfabetica():
    assert co.ORDEM_CATEGORIAS == ("celular", "eletro", "mala")
    assert list(co.ORDEM_CATEGORIAS) == sorted(co.ORDEM_CATEGORIAS)
    assert [co.posicao_categoria(c) for c in ("celular", "eletro", "mala")] == [0, 1, 2]
    # Maiúscula e espaço não atrapalham; o que não é categoria vem depois.
    assert co.posicao_categoria(" Mala ") == 2
    for fora in ("geral", "catalogo", "sem", "", None):
        assert co.posicao_categoria(fora) == 3


def test_ordenar_e_estavel_e_deixa_o_resto_depois():
    entrada = ["geral", "mala", "catalogo", "celular", "eletro", "sem"]
    copia = list(entrada)
    assert co.ordenar_categorias(entrada) == [
        "celular",
        "eletro",
        "mala",
        "geral",
        "catalogo",
        "sem",
    ]
    assert entrada == copia, "não mexe na original"
    grupos = [{"chave": "mala"}, {"chave": "celular"}, {"chave": "eletro"}]
    assert [g["chave"] for g in co.ordenar_categorias(grupos, lambda g: g["chave"])] == [
        "celular",
        "eletro",
        "mala",
    ]


def test_planilha_da_conferencia_celular_eletro_mala_geral():
    assert saida.GRUPOS_PLANILHA == (
        ("celular", "Celular"),
        ("eletro", "Eletro"),
        ("mala", "Mala"),
        ("geral", "Geral"),
    )


def test_grupos_em_ordem_nao_mexe_no_relatorio_guardado():
    rel = {"grupos": [{"chave": "mala"}, {"chave": "celular"}, {"chave": "eletro"}, "lixo"]}
    antes = copy.deepcopy(rel)
    assert [g["chave"] for g in saida.grupos_em_ordem(rel)] == ["celular", "eletro", "mala"]
    assert rel == antes
    assert saida.grupos_em_ordem({}) == []


def test_a_tela_usa_a_mesma_lista():
    """apps/web/lib/categorias.ts tem a mesma ordem (o teste da tela,
    apps/web/tests/categorias-ordem.cjs, confere do lado de lá)."""
    web = Path(__file__).resolve().parents[2] / "web" / "lib" / "categorias.ts"
    if not web.exists():  # a imagem da API não leva o apps/web
        pytest.skip("apps/web não está aqui")
    m = re.search(r"ORDEM_CATEGORIAS = \[([^\]]*)\]", web.read_text(encoding="utf-8"))
    assert m, "ORDEM_CATEGORIAS na lib da tela"
    assert tuple(re.findall(r"'(\w+)'", m.group(1))) == co.ORDEM_CATEGORIAS
