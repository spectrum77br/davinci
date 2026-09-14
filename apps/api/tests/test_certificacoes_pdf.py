"""Integridade do relatório, inclusive paginação e textos maiores que uma página."""

import re
import unicodedata
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import fitz

from app.services.certificacoes_pdf import montar_pdf


def _texto(page: fitz.Page) -> str:
    return " ".join(unicodedata.normalize("NFKC", page.get_text()).split())


def test_certificacoes_pdf_formats_values_and_escapes_html():
    rows = [
        SimpleNamespace(
            produto="<b>Produto & modelo</b>",
            modelo="UAF001",
            nome_comercial="M1, M2, M3",
            certificado="inmetro",
            numero="0069/25",
            valor=Decimal("123456789012.34"),
            inicio=date(2026, 8, 3),
            fim="2027-08-03",
        ),
        SimpleNamespace(produto="Valor zero", valor=Decimal("0.00")),
        SimpleNamespace(produto="Sem valor"),
    ]
    with fitz.open(stream=montar_pdf(rows), filetype="pdf") as doc:
        texto = _texto(doc[0])
        assert len(doc) == 1
        assert "<b>Produto & modelo</b>" in texto
        assert "R$ 123.456.789.012,34" in texto
        assert "R$ 0,00" in texto
        assert "03/08/2026" in texto and "03/08/2027" in texto
        assert "None" not in texto
        assert "3 certificações" in texto
        assert doc[0].rect.width > doc[0].rect.height


def test_certificacoes_pdf_repeats_headers_without_duplicating_data():
    rows = [SimpleNamespace(produto=f"PRODUTO{i:04d}", nome_comercial="M1, M2") for i in range(90)]
    with fitz.open(stream=montar_pdf(rows), filetype="pdf") as doc:
        assert len(doc) > 1
        textos = [_texto(page) for page in doc]
        for i, texto in enumerate(textos):
            for coluna in (
                "Produto",
                "Modelo",
                "Nome comercial",
                "Certificado",
                "Número",
                "Valor",
                "Início",
                "Fim",
            ):
                assert coluna in texto
            assert f"Página {i + 1} de {len(doc)}" in texto
        # Cabeçalhos repetidos não podem carregar texto oculto da primeira página.
        assert re.findall(r"PRODUTO\d{4}", " ".join(textos)) == [
            f"PRODUTO{i:04d}" for i in range(90)
        ]


def test_certificacoes_pdf_preserves_commercial_name_larger_than_one_page():
    palavras = [f"PALAVRA{i:04d}" for i in range(1000)]
    with fitz.open(
        stream=montar_pdf([SimpleNamespace(produto="Longo", nome_comercial=" ".join(palavras))]),
        filetype="pdf",
    ) as doc:
        assert len(doc) > 1
        assert re.findall(r"PALAVRA\d{4}", " ".join(_texto(page) for page in doc)) == palavras


def test_certificacoes_pdf_keeps_unbroken_names_inside_page():
    with fitz.open(
        stream=montar_pdf([SimpleNamespace(nome_comercial="W" * 800)]), filetype="pdf"
    ) as doc:
        all_words = [word for page in doc for word in page.get_text("words")]
        assert sum(word[4].count("W") for word in all_words) == 800
        assert all(20 <= word[0] < word[2] <= 822 for word in all_words)


def test_certificacoes_pdf_empty_state():
    with fitz.open(stream=montar_pdf([]), filetype="pdf") as doc:
        assert len(doc) == 1
        assert "Nenhuma certificação cadastrada." in _texto(doc[0])
        assert "0 certificações" in _texto(doc[0])
