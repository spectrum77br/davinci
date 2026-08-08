"""Testes do fatiador/casador PURO do lote de etiquetas (nf_etiqueta_lote)."""

from __future__ import annotations

import fitz
import pytest

from app.services import nf_etiqueta_lote


def _lote_pdf(paginas: list[list[str]]) -> bytes:
    """PDF sintético: cada item é uma página com as linhas dadas."""
    doc = fitz.open()
    for linhas in paginas:
        page = doc.new_page(width=300, height=442)
        y = 20
        for linha in linhas:
            page.insert_text((20, y), linha, fontsize=8, fontname="helv")
            y += 14
    return doc.tobytes()


def test_fatiar_lote_dois_pedidos():
    """Etiqueta+declaração por pedido → 2 fatias; numeroloja só na Shopee."""
    pdf = _lote_pdf(
        [
            ["DESTINATÁRIO", "Fulano De Tal", "Pedido: 260808R7G290SQ"],
            ["DECLARAÇÃO DE CONTEÚDO", "SKU", "a001"],
            ["DESTINATÁRIO", "Maria Aparecida Silva"],  # TikTok: sem nº
            ["DECLARAÇÃO DE CONTEÚDO", "SKU", "b011.20"],
        ]
    )
    fatias = nf_etiqueta_lote.fatiar_lote(pdf)
    assert len(fatias) == 2
    assert fatias[0].numeroloja == "260808R7G290SQ"
    assert (fatias[0].pagina_ini, fatias[0].pagina_fim) == (0, 1)
    assert fitz.open(stream=fatias[0].pdf, filetype="pdf").page_count == 2
    assert fatias[1].numeroloja is None
    assert (fatias[1].pagina_ini, fatias[1].pagina_fim) == (2, 3)
    assert "Maria Aparecida Silva" in fatias[1].texto
    assert "b011.20" in fatias[1].texto


def test_fatiar_declaracao_multipagina_continua_fatia():
    pdf = _lote_pdf(
        [
            ["Pedido: 260808AAAAAA"],
            ["DECLARAÇÃO DE CONTEÚDO", "página 1"],
            ["DECLARAÇÃO DE CONTEÚDO", "página 2 (continuação)"],
            ["Pedido: 260808BBBBBB"],
        ]
    )
    fatias = nf_etiqueta_lote.fatiar_lote(pdf)
    assert len(fatias) == 2
    assert (fatias[0].pagina_ini, fatias[0].pagina_fim) == (0, 2)
    assert fatias[1].numeroloja == "260808BBBBBB"


def test_fatiar_pdf_invalido():
    with pytest.raises(nf_etiqueta_lote.EtiquetaLoteError):
        nf_etiqueta_lote.fatiar_lote(b"nao sou um pdf")


def test_fatiar_danfe_numeroloja_solto():
    """Layout DANFE SIMPLIFICADO (Shopee): o "Pedido:" da etiqueta traz o
    código de TRIAGEM (SOCSP7) e o numeroloja real aparece SOLTO no corpo —
    os dois viram candidatos; o router valida contra o banco."""
    pdf = _lote_pdf(
        [
            [
                "SP7",
                "Pedido:",
                "SOCSP7",
                "DESTINATÁRIO",
                "260808STCA6RQ5",
                "BR267780939818Z",
                "35260859882322000154550020000002961253235925",
                "DANFE SIMPLIFICADO - ETIQUETA",
            ],
            ["DECLARAÇÃO DE CONTEÚDO", "uaf001m1.110"],
        ]
    )
    fatias = nf_etiqueta_lote.fatiar_lote(pdf)
    assert len(fatias) == 1
    assert fatias[0].numeroloja == "SOCSP7"  # 1º match — diagnóstico
    assert fatias[0].numerolojas == ["SOCSP7", "260808STCA6RQ5"]


def test_numerolojas_nao_casa_dentro_de_codigos():
    """O formato Shopee não pode casar DENTRO do código de barras (44 díg.),
    do rastreio TikTok (15 díg.) nem do rastreio BR...Z."""
    texto = (
        "Pedido:\n999881646186225\n"
        "35260854386601000103550040000008281255060742\n"
        "BR267780939818Z"
    )
    # só o match de "Pedido:" (rastreio TikTok — inofensivo, o banco filtra)
    assert nf_etiqueta_lote._numerolojas_do_texto(texto) == ["999881646186225"]


def test_casa_por_texto_unico():
    texto = "DESTINATÁRIO\nMaria Aparecida Silva\nDECLARAÇÃO DE CONTEÚDO\nb011.20"
    candidatos = [
        ("860002", "Maria Aparecida Silva", {"b011.20"}),
        ("860003", "Outro Cliente", {"a001"}),
    ]
    assert nf_etiqueta_lote.casa_por_texto(texto, candidatos) == "860002"


def test_casa_por_texto_ambiguo_nao_casa():
    """Dois pedidos com o mesmo nome+SKU → melhor não casar do que errar."""
    texto = "Maria Aparecida Silva\nb011.20"
    candidatos = [
        ("860002", "Maria Aparecida Silva", {"b011.20"}),
        ("860004", "Maria Aparecida Silva", {"b011.20"}),
    ]
    assert nf_etiqueta_lote.casa_por_texto(texto, candidatos) is None


def test_casa_por_texto_exige_nome_e_sku():
    texto = "Maria Aparecida Silva\nb011.20"
    # nome bate mas o SKU não → não casa
    assert (
        nf_etiqueta_lote.casa_por_texto(
            texto, [("860002", "Maria Aparecida Silva", {"x999"})]
        )
        is None
    )
    # candidato sem nome ou sem SKU não participa
    assert (
        nf_etiqueta_lote.casa_por_texto(texto, [("860002", None, {"b011.20"})])
        is None
    )
    assert (
        nf_etiqueta_lote.casa_por_texto(
            texto, [("860002", "Maria Aparecida Silva", set())]
        )
        is None
    )


def test_casa_por_texto_nome_abreviado_tiktok():
    """A etiqueta TikTok imprime o nome ABREVIADO; o Bling tem o completo —
    casa por prefixo do nome (+ SKU, como sempre)."""
    candidatos = [
        ("289312", "ANA CLARA ANACLETO PINTO", {"dg054.sp"}),
        ("289318", "SANDRO BASTOS PEREIRA", {"dg053.sp"}),
    ]
    texto = (
        "DESTINATÁRIO\nana clara\ncasa 1, Sorocaba, SP\n"
        "DECLARAÇÃO DE CONTEÚDO\ndg054.sp"
    )
    assert nf_etiqueta_lote.casa_por_texto(texto, candidatos) == "289312"
    texto2 = "DESTINATÁRIO\nsandro\nDECLARAÇÃO DE CONTEÚDO\ndg053.sp"
    assert nf_etiqueta_lote.casa_por_texto(texto2, candidatos) == "289318"


def test_casa_por_texto_nome_abreviado_ambiguo_nao_casa():
    """Duas 'Ana' com o MESMO SKU na janela → ambíguo → não casa (manual)."""
    candidatos = [
        ("111", "ANA CLARA ANACLETO", {"dg054.sp"}),
        ("222", "ANA BEATRIZ SOUZA", {"dg054.sp"}),
    ]
    assert nf_etiqueta_lote.casa_por_texto("ana clara\ndg054.sp", candidatos) is None


def test_casa_por_texto_nome_nao_casa_dentro_de_palavra():
    """Prefixo casa como palavra inteira: 'ANA' não casa dentro de 'SANTANA'."""
    assert (
        nf_etiqueta_lote.casa_por_texto(
            "SANTANA SILVA\ndg054.sp", [("111", "ANA CLARA", {"dg054.sp"})]
        )
        is None
    )


def test_casa_por_texto_sku_token_isolado():
    """SKU não pode casar como substring de outro código (b011.2 vs b011.20)."""
    texto = "Maria Aparecida Silva\nb011.20"
    assert (
        nf_etiqueta_lote.casa_por_texto(
            texto, [("860002", "Maria Aparecida Silva", {"b011.2"})]
        )
        is None
    )
