"""Declaração de conteúdo com os itens do pedido no Bling (06/11/2026).

Camada PURA (`services/nf_etiqueta_declaracao.py`): as regras de texto do
Eduardo (DESCRIÇÃO/VARIAÇÃO tiradas do `item_descricao`) e a reescrita da
tabela "IDENTIFICAÇÃO DOS BENS" nos dois layouts reais, com declarações
SINTÉTICAS montadas na geometria medida (`tests/declaracao_sintetica.py`).
"""

from __future__ import annotations

import fitz  # PyMuPDF
import pytest

from app.services.nf_etiqueta_declaracao import (
    ItemDeclaracao,
    assinatura_itens,
    formatar_item,
    ler_declaracao,
    reescrever_declaracao,
    separar_descricao,
    total_declaracao,
)
from app.services.nf_etiqueta_juntar import redimensionar_para_etiqueta
from app.services.nf_etiqueta_transform import transformar_etiqueta
from tests.declaracao_sintetica import (
    abrir_tabela,
    declaracao_nossa,
    declaracao_upseller,
    sem_declaracao,
    texto_da_tabela,
)

# Declaração do anúncio (o que o Upseller imprime): SKU velho, título do anúncio.
_ANUNCIO = [("b036.24", ["Mala De Viagem Despacho", "Antiga Roxo"], ["M3 Roxo", "Escuro"], 1)]
_BRANCA = formatar_item(
    "b027.24", "Mala Chanfrada M3 tamanho 24 - Branca (DT - PPDT802 - DT01) AVULSO", 1
)


def _palavras(pdf: bytes) -> list[str]:
    _doc, page = abrir_tabela(pdf)
    return [w[4] for w in page.get_text("words")]


def _keywords(pdf: bytes) -> str:
    return fitz.open(stream=pdf, filetype="pdf").metadata.get("keywords") or ""


# --- regras de texto --------------------------------------------------------


@pytest.mark.parametrize(
    ("descricao", "esperado"),
    [
        (
            "Mala Brilho Listrada P1 tamanho 24 - Preto (DT - PPDT802 - DT01) AVULSO",
            ("Mala Brilho Listrada P1 tamanho 24", "Preto"),
        ),
        (
            "Uranyx A17 Pro Max 12.128 - Laranja + Fone",
            ("Uranyx A17 Pro Max 12.128", "Laranja + Fone"),
        ),
        ("Uranyx C65 PRO-8+256 Azul AVULSO", ("Uranyx C65 PRO-8+256 Azul", "")),
        (
            "Mala Chanfrada M3 tamanho 26 - Roxo Escuro AVULSO SALVADO",
            ("Mala Chanfrada M3 tamanho 26", "Roxo Escuro"),
        ),
    ],
)
def test_exemplos_do_dono(descricao, esperado):
    assert separar_descricao(descricao) == esperado


@pytest.mark.parametrize(
    ("descricao", "esperado"),
    [
        # corta no ÚLTIMO " - "; o primeiro fica na descrição
        (
            "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata + Fone UFB10 + Relogio",
            ("Uranyx A18 Pro Max - S5 Edition 16.128", "Prata + Fone UFB10 + Relogio"),
        ),
        # parêntese no meio deixa " - - ": vira um separador só
        (
            "Mala Seta P5 tamanho 24 - (OM - PPB088) - Azul Claro",
            ("Mala Seta P5 tamanho 24", "Azul Claro"),
        ),
        # aninhado, minúsculas, USADO como palavra inteira
        ("Celular X1 (lote (B) 7) usado - Verde", ("Celular X1", "Verde")),
        ("Livros Usados Infantis", ("Livros Usados Infantis", "")),
        # traço solto na ponta depois de tirar as palavras internas
        ("Encosto Cabeça - SALVADO", ("Encosto Cabeça", "")),
        ("Encosto Cabeça", ("Encosto Cabeça", "")),
        # en dash e símbolo fora do Latin-1
        ("Cadeado TSA – Preto ✓", ("Cadeado TSA", "Preto")),
        ("  Mala   Lisa  -  Bege  ", ("Mala Lisa", "Bege")),
        # nada sobra pela regra: mostra como veio (melhor que linha em branco)
        ("(DT - PPDT802) AVULSO", ("(DT - PPDT802) AVULSO", "")),
        (None, ("", "")),
    ],
)
def test_regras_de_texto(descricao, esperado):
    assert separar_descricao(descricao) == esperado


def test_formatar_item_sku_como_esta_e_quantidade():
    it = formatar_item(" dg053.sp+a001.sp ", "Uranyx A17 Pro Max 12.128 - Laranja + Fone", 2)
    assert it == ItemDeclaracao(
        "dg053.sp+a001.sp", "Uranyx A17 Pro Max 12.128", "Laranja + Fone", 2
    )
    assert formatar_item("a076", "Cadeado", None).quantidade == 1
    assert formatar_item("a076", "Cadeado", 0).quantidade == 1
    assert formatar_item("a076", "Cadeado", 3.0).quantidade == 3
    assert formatar_item("a076", "Cadeado", "x").quantidade == 1


def test_assinatura_nao_depende_da_ordem_e_muda_com_o_item():
    a = formatar_item("b026.18", "Mala Chanfrada M3 tamanho 18 - Prata", 1)
    b = formatar_item("b027.24", "Mala Chanfrada M3 tamanho 24 - Branca", 1)
    assert assinatura_itens([a, b]) == assinatura_itens([b, a])
    assert len(assinatura_itens([a])) == 16
    assert assinatura_itens([a]) != assinatura_itens([b])
    assert assinatura_itens([a]) != assinatura_itens(
        [formatar_item("b026.18", "Mala Chanfrada M3 tamanho 18 - Prata", 2)]
    )
    assert assinatura_itens([a]) != assinatura_itens(
        [formatar_item("b026.18", "Mala Chanfrada M3 tamanho 18 - Azul", 1)]
    )


# --- Upseller ---------------------------------------------------------------


def test_troca_sku_descricao_variacao_e_total():
    raw = declaracao_upseller(_ANUNCIO)
    out, st, det = reescrever_declaracao(raw, [_BRANCA])
    assert st == "ok", det
    txt = texto_da_tabela(out)
    for velho in ("b036.24", "Despacho", "Roxo", "PPDT802", "AVULSO"):
        assert velho not in txt
    assert ler_declaracao(out) == [
        ItemDeclaracao("b027.24", "Mala Chanfrada M3 tamanho 24", "Branca", 1)
    ]
    assert total_declaracao(out) == 1
    # cabeçalho, remetente/destinatário e texto legal intactos
    for fica in (
        "IDENTIFICAÇÃO DOS BENS",
        "DESCRIÇÃO",
        "VARIAÇÃO",
        "Fulano De Tal",
        "Texto legal sintetico",
        "Assinatura do Declarante/Remetente",
        "Total",
        "01-01-2026",
    ):
        assert fica in txt
    # a etiqueta (página 0) não muda
    d0, d1 = fitz.open(stream=raw, filetype="pdf"), fitz.open(stream=out, filetype="pdf")
    assert d0[0].get_text() == d1[0].get_text()
    assert d0[0].get_pixmap(dpi=50).samples == d1[0].get_pixmap(dpi=50).samples
    assert f"davinci-decl:{assinatura_itens([_BRANCA])}" in _keywords(out)


def test_kit_desmontado_vira_n_linhas_na_ordem_do_bling():
    """306622: o anúncio é 1 kit; no Bling são 2 itens. Sai uma linha por
    item do Bling, na ordem do Bling, Total = 2."""
    raw = declaracao_upseller(
        [("b026.18.24", ["Kit 2 Malas de Viagem", "18 e 24 Polegadas"], "M3 Prata", 1)]
    )
    itens = [
        formatar_item("b027.24", "Mala Chanfrada M3 tamanho 24 - Branca", 1),
        formatar_item("b026.18", "Mala Chanfrada M3 tamanho 18 - Prata", 1),
    ]
    out, st, _ = reescrever_declaracao(raw, itens)
    assert st == "ok"
    assert [i.sku for i in ler_declaracao(out)] == ["b027.24", "b026.18"]
    assert total_declaracao(out) == 2
    assert "b026.18.24" not in texto_da_tabela(out)


def test_quantidade_em_digitos_e_total_somado():
    itens = [
        formatar_item("a076", "Cadeado TSA - Preto", 2),
        formatar_item("z0217", "Encosto Cabeça", 3),
    ]
    out, st, _ = reescrever_declaracao(declaracao_upseller(_ANUNCIO), itens)
    assert st == "ok"
    assert [i.quantidade for i in ler_declaracao(out)] == [2, 3]
    assert total_declaracao(out) == 5
    txt = texto_da_tabela(out)
    assert "(2)" not in txt and "(3)" not in txt  # dígitos simples, sem parênteses


def test_kit_com_sku_longo_quebra_e_rele_inteiro():
    it = formatar_item(
        "dg091.pi+a003.pi+a004.pi",
        "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata + Fone UFB10 + Relogio",
        1,
    )
    out, st, _ = reescrever_declaracao(declaracao_upseller(_ANUNCIO), [it])
    assert st == "ok"
    assert ler_declaracao(out) == [it]
    _doc, page = abrir_tabela(out)
    ws = page.get_text("words")
    y_total = min(w[1] for w in ws if w[4] == "Total")
    skus = [w for w in ws if 21.7 < w[0] < 82.0 and 104 < w[1] < y_total]
    # quebrou depois de '+', sem estourar a coluna SKU
    assert [w[4] for w in skus] == ["dg091.pi+", "a003.pi+", "a004.pi"]
    assert all(w[2] <= 82.0 for w in skus)


def test_idempotente_marca_e_caminho_rapido():
    raw = declaracao_upseller(_ANUNCIO)
    out, st, _ = reescrever_declaracao(raw, [_BRANCA])
    assert st == "ok"
    # mesmos itens (em outra ordem também) → 'igual', os MESMOS bytes
    out2, st2, det2 = reescrever_declaracao(out, [_BRANCA])
    assert (st2, out2) == ("igual", out)
    assert det2 == assinatura_itens([_BRANCA])
    # itens mudaram depois (troca no Bling após a subida) → reescreve em cima
    # da própria saída, sem acumular linha velha
    prata = formatar_item("b026.18", "Mala Chanfrada M3 tamanho 18 - Prata", 1)
    out3, st3, _ = reescrever_declaracao(out, [prata])
    assert st3 == "ok"
    assert ler_declaracao(out3) == [prata]
    assert "b027.24" not in texto_da_tabela(out3)
    assert _keywords(out3).count("davinci-decl:") == 1
    # e volta: reescrever de novo dá a mesma leitura que da 1ª vez
    out4, st4, _ = reescrever_declaracao(out3, [_BRANCA])
    assert st4 == "ok" and ler_declaracao(out4) == ler_declaracao(out)


def test_marca_igual_nao_abre_a_tabela():
    """O caminho rápido confia na marca: nem olha a tabela."""
    doc = fitz.open(stream=declaracao_upseller(_ANUNCIO), filetype="pdf")
    doc.set_metadata({"keywords": f"outra-coisa davinci-decl:{assinatura_itens([_BRANCA])}"})
    raw = doc.tobytes()
    out, st, _ = reescrever_declaracao(raw, [_BRANCA])
    assert st == "igual" and out is raw


def test_sem_marca_reescreve_mesmo_com_o_item_certo_e_preserva_keywords():
    """Etiqueta corrigida à mão (306622/307238) ou subida antes desta regra:
    sem marca, reescreve — a descrição/variação velhas também saem."""
    doc = fitz.open(
        stream=declaracao_upseller([("b027.24", "Mala De Viagem Despacho", "M3 Roxo", 1)]),
        filetype="pdf",
    )
    doc.set_metadata({"keywords": "upseller davinci-decl:0000000000000000"})
    out, st, _ = reescrever_declaracao(doc.tobytes(), [_BRANCA])
    assert st == "ok"
    assert ler_declaracao(out) == [
        ItemDeclaracao("b027.24", "Mala Chanfrada M3 tamanho 24", "Branca", 1)
    ]
    kw = _keywords(out)
    assert "upseller" in kw and "0000000000000000" not in kw
    assert kw.count("davinci-decl:") == 1


def test_tiktok_sem_rabo_desce_so_o_total():
    raw = declaracao_upseller(_ANUNCIO, com_rabo=False)
    itens = [
        formatar_item(f"b0{k}7.24", f"Mala Chanfrada M3 tamanho 24 - Cor {k}", 1) for k in range(4)
    ]
    out, st, det = reescrever_declaracao(raw, itens)
    assert st == "ok", det
    assert [i.sku for i in ler_declaracao(out)] == [i.sku for i in itens]
    assert total_declaracao(out) == 4


def test_itens_que_nao_cabem_descem_o_texto_legal():
    raw = declaracao_upseller(_ANUNCIO)
    itens = [
        formatar_item(
            f"b0{k}6.18.24", "Mala Chanfrada M3 tamanho 18 e 24 Polegadas - Prata Escuro", 1
        )
        for k in range(5)
    ]
    out, st, det = reescrever_declaracao(raw, itens)
    assert st == "ok", det
    assert "fs=9 " in det and "delta=0.0" not in det
    assert [i.sku for i in ler_declaracao(out)] == [i.sku for i in itens]
    _doc, page = abrir_tabela(out)
    ws = page.get_text("words")
    y_total = max(w[1] for w in ws if w[4] == "Total")
    legal = [w for w in ws if w[4] in ("Texto", "Assinatura")]
    assert legal and all(w[1] > y_total for w in legal)  # o rabo desceu junto
    assert max(w[3] for w in ws) <= page.rect.height
    ws0 = fitz.open(stream=raw, filetype="pdf")[1].get_text("words")
    y_total0 = max(w[1] for w in ws0 if w[4] == "Total")
    antes = [w[4] for w in ws0 if w[1] > y_total0 + 5]
    depois = [w[4] for w in ws if w[1] > y_total + 5]
    assert antes == depois  # mesmo texto, mesma ordem


def test_encolhe_a_fonte_quando_nem_descendo_cabe():
    raw = declaracao_upseller(_ANUNCIO)
    # 10 linhas de 2 andares não cabem a 9 pt nem descendo o rabo (~173 pt
    # livres); a 8 pt cabem.
    itens = [
        formatar_item(
            f"b{k:02d}6.18.24", "Mala Chanfrada M3 tamanho 18 e 24 Polegadas - Prata Escuro", 1
        )
        for k in range(10)
    ]
    out, st, det = reescrever_declaracao(raw, itens)
    assert st == "ok", det
    assert "fs=9 " not in det and "fs=8" in det
    assert ler_declaracao(out) == itens and total_declaracao(out) == 10
    _doc, page = abrir_tabela(out)
    assert max(w[3] for w in page.get_text("words")) <= page.rect.height - 4


def test_nao_coube_deixa_como_veio():
    raw = declaracao_upseller(_ANUNCIO)
    itens = [
        formatar_item(
            f"x{k}", "Mala Chanfrada M3 tamanho 18 e 24 Polegadas Cadeado TSA - Prata Escuro", 1
        )
        for k in range(30)
    ]
    out, st, _ = reescrever_declaracao(raw, itens)
    assert (st, out) == ("nao_coube", raw)


def test_tabela_que_continua_em_outra_pagina_nao_mexe():
    raw = declaracao_upseller(_ANUNCIO, com_total=False)
    out, st, _ = reescrever_declaracao(raw, [_BRANCA])
    assert (st, out) == ("multipagina", raw)


def test_duas_declaracoes_no_mesmo_pdf_nao_mexe():
    raw = declaracao_upseller(_ANUNCIO, paginas=2)
    out, st, _ = reescrever_declaracao(raw, [_BRANCA])
    assert (st, out) == ("multipagina", raw)


def test_sem_declaracao_sem_itens_e_pdf_invalido_devolvem_a_entrada():
    raw = sem_declaracao()
    assert reescrever_declaracao(raw, [_BRANCA])[:2] == (raw, "sem_declaracao")
    dec = declaracao_upseller(_ANUNCIO)
    assert reescrever_declaracao(dec, [])[:2] == (dec, "sem_itens")
    assert reescrever_declaracao(dec, None)[:2] == (dec, "sem_itens")
    lixo = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog>>endobj\n"
    out, st, det = reescrever_declaracao(lixo, [_BRANCA])
    assert out is lixo and st == "erro" and det
    assert reescrever_declaracao(b"nao sou pdf", [_BRANCA])[:2] == (b"nao sou pdf", "erro")
    assert reescrever_declaracao(b"", [_BRANCA])[:2] == (b"", "erro")


def test_caracteres_fora_do_latin1():
    it = ItemDeclaracao("a076", "Cadeado TSA – “Preto” 😀", "Preto…", 1)
    out, st, _ = reescrever_declaracao(declaracao_upseller(_ANUNCIO), [it])
    assert st == "ok"
    txt = texto_da_tabela(out)
    assert "·" not in txt
    assert 'Cadeado TSA - "Preto"' in txt and "Preto..." in txt


def test_sobrevive_ao_transform_e_ao_redimensionamento_da_impressao():
    """Fluxo real: transform na subida → reescrita → redimensionar na
    impressão. O SKU novo continua no texto do PDF que vai pra impressora."""
    raw = transformar_etiqueta(declaracao_upseller(_ANUNCIO), "Fulano De Tal")
    out, st, _ = reescrever_declaracao(raw, [_BRANCA])
    assert st == "ok"
    impresso = redimensionar_para_etiqueta(out)
    txt = "".join(p.get_text() for p in fitz.open(stream=impresso, filetype="pdf"))
    assert "b027.24" in txt and "b036.24" not in txt


# --- a nossa (Temu/AliExpress) ----------------------------------------------


def test_layout_nosso_temu():
    raw = declaracao_nossa(
        [("b008.12.18", "Kit Malas Listrada M1 tamanho 12.18 Polegadas - Champanhe", 1)]
    )
    assert ler_declaracao(raw) == [
        ItemDeclaracao(
            "b008.12.18", "Kit Malas Listrada M1 tamanho 12.18 Polegadas - Champanhe", "", 1
        )
    ]
    itens = [
        formatar_item("b008.12.20", "Kit Malas Listrada M1 tamanho 12.20 Polegadas - Champanhe", 1),
        formatar_item("a076", "Cadeado TSA - Preto", 2),
    ]
    out, st, det = reescrever_declaracao(raw, itens)
    assert st == "ok" and "nossa" in det
    assert ler_declaracao(out) == itens
    assert total_declaracao(out) == 3
    _doc, page = abrir_tabela(out)
    fontes = {
        s["text"]: s["font"]
        for b in page.get_text("dict")["blocks"]
        for ln in b.get("lines", [])
        for s in ln["spans"]
    }
    assert fontes["b008.12.20"] == "Helvetica-Bold"  # SKU em negrito, como o executor
    assert fontes["Cadeado TSA"] == "Helvetica"
    assert reescrever_declaracao(out, itens)[1] == "igual"
