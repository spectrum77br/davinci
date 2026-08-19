"""Transforma a etiqueta pra a "visualização" da impressão (item 2 da Fase 3b).

Cobre a camada PURA `transformar_etiqueta(pdf_bytes, nome?) -> bytes` com uma
etiqueta SINTÉTICA (sem depender de nenhum PDF de amostra): as 3 regras do
faturador — remetente=destinatário, sem bloco NF, sem logo do marketplace — e
a preservação do QR code.
"""

from __future__ import annotations

import fitz  # PyMuPDF
import pytest

from app.services.nf_etiqueta_transform import (
    EtiquetaTransformError,
    transformar_etiqueta,
)

_CHAVE = "35260730734713000140550040000029931203736630"

# PDF válido com zero páginas (o fitz não SALVA um doc sem páginas, então o
# caso "pdf_vazio" precisa vir de bytes montados à mão).
_PDF_SEM_PAGINAS = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF"
)


def _etiqueta_sintetica(*, com_logo: bool = True) -> bytes:
    """Monta uma etiqueta tipo Correios: DESTINATÁRIO/REMETENTE, bloco DANFE com
    a chave, um logo pequeno no topo (marketplace) e um QR quadrado (preservar).
    """
    doc = fitz.open()
    page = doc.new_page(width=300, height=442)
    page.insert_text((20, 20), "DESTINATÁRIO", fontsize=7, fontname="helv")
    page.insert_text((20, 32), "Fulano De Tal", fontsize=8, fontname="helv")
    page.insert_text((20, 280), "REMETENTE", fontsize=7, fontname="helv")
    page.insert_text((20, 294), "Loja Origem XYZ", fontsize=8, fontname="helv")
    page.insert_text((20, 340), "DANFE SIMPLIFICADO - ETIQUETA", fontsize=6, fontname="helv")
    page.insert_text((20, 350), _CHAVE, fontsize=5, fontname="helv")
    page.insert_text(
        (5, 392), "SKU: 1; Total Items: 1; #UP123; Deadline:05/08/2026",
        fontsize=6, fontname="helv",
    )
    page.insert_text((5, 402), "1. dg053.sp+a001.sp", fontsize=6, fontname="helv")
    if com_logo:
        logo = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 79, 22), False)
        logo.set_rect(logo.irect, (200, 120, 255))
        page.insert_image(fitz.Rect(216, 14, 260, 27), pixmap=logo)
    qr = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 60), False)
    qr.set_rect(qr.irect, (0, 0, 0))
    page.insert_image(fitz.Rect(20, 60, 80, 120), pixmap=qr)
    return doc.tobytes()


def _etiqueta_jt() -> bytes:
    """Etiqueta tipo J&T (TikTok): o nome do destinatário começa um pouco à
    ESQUERDA do rótulo e a chave da NF vem solta, sem o rótulo DANFE/NF:.
    """
    doc = fitz.open()
    page = doc.new_page(width=300, height=442)
    page.insert_text((42.9, 20), "DESTINATÁRIO", fontsize=7, fontname="helv")
    page.insert_text((34, 32), "yasmin", fontsize=8, fontname="helv")
    page.insert_text((34, 280), "REMETENTE:", fontsize=7, fontname="helv")
    page.insert_text((34, 292), "m**1", fontsize=8, fontname="helv")
    page.insert_text((60, 376), _CHAVE, fontsize=5, fontname="helv")
    return doc.tobytes()


def _etiqueta_ml() -> bytes:
    """Etiqueta tipo Mercado Livre: o bloco da NF fica no MEIO da etiqueta, com o
    destinatário e o código de barras de rastreio LOGO ABAIXO dele.
    """
    doc = fitz.open()
    page = doc.new_page(width=300, height=442)
    page.insert_text((20, 20), "REMETENTE", fontsize=7, fontname="helv")
    page.insert_text((20, 32), "KFA Comercio Varejista", fontsize=8, fontname="helv")
    page.insert_text((20, 150), "DANFE SIMPLIFICADO", fontsize=6, fontname="helv")
    page.insert_text((20, 160), _CHAVE, fontsize=5, fontname="helv")
    page.insert_text((20, 200), "DESTINATÁRIO", fontsize=7, fontname="helv")
    page.insert_text((20, 212), "Daniel Do Carmo", fontsize=8, fontname="helv")
    page.insert_text((20, 230), "Rua Teste 100 - Sumaré/SP", fontsize=7, fontname="helv")
    barras = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 260, 40), False)
    barras.set_rect(barras.irect, (0, 0, 0))
    page.insert_image(fitz.Rect(20, 300, 280, 340), pixmap=barras)
    return doc.tobytes()


def _etiqueta_ml_correios() -> bytes:
    """Etiqueta ML postada nos Correios: o "NF:" aparece SOLTO no cabeçalho
    postal (Contrato/Sedex/PESO), colado no código de rastreio e no código de
    barras dele — e sem nenhuma chave de acesso na página.
    """
    doc = fitz.open()
    page = doc.new_page(width=300, height=442)
    page.insert_text((20, 20), "NF: 1464559", fontsize=7, fontname="helv")
    page.insert_text((20, 30), "SHP: 47733455146", fontsize=7, fontname="helv")
    page.insert_text((20, 40), "Contrato: 9912278851", fontsize=7, fontname="helv")
    page.insert_text((20, 50), "AD779019760BR", fontsize=7, fontname="helv")
    barras = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 260, 40), False)
    barras.set_rect(barras.irect, (0, 0, 0))
    page.insert_image(fitz.Rect(20, 60, 280, 100), pixmap=barras)
    page.insert_text((20, 150), "DESTINATARIO", fontsize=7, fontname="helv")
    page.insert_text((20, 162), "Daniel do Carmo", fontsize=8, fontname="helv")
    return doc.tobytes()


def _etiqueta_ml_flex() -> bytes:
    """Etiqueta tipo Mercado Livre Flex: a 1ª página NÃO tem rótulo REMETENTE nem
    DESTINATÁRIO — o remetente vem SOLTO no topo (nome da loja, endereço,
    cidade/UF/CEP e Pack ID) logo abaixo da tarja de cabeçalho, com o logo do
    marketplace colado à esquerda. O nome do destinatário só existe na Declaração
    de Conteúdo (2ª página), onde REMETENTE e DESTINATÁRIO são colunas LADO A LADO.
    """
    doc = fitz.open()
    p0 = doc.new_page(width=300, height=442)
    p0.insert_text((10, 10), "UP2NYY220582  18/08/2026  13:02:07", fontsize=7, fontname="helv")
    p0.insert_text((64, 25), "Keila Lojas #1423186352", fontsize=7, fontname="helv")
    p0.insert_text((64, 36), "Estrada Particular Sadae Takagi 2235", fontsize=7, fontname="helv")
    p0.insert_text((64, 47), "Sao Bernardo do Campo BR-SP 09852070", fontsize=7, fontname="helv")
    p0.insert_text((64, 59), "Pack ID: 2000014590774099", fontsize=7, fontname="helv")
    p0.insert_text((51, 82), "XSP4", fontsize=7, fontname="helv")
    p0.insert_text((92, 199), "SSP36", fontsize=7, fontname="helv")
    p0.insert_text((33, 285), "Retira na JULLY BANHO E TOSA", fontsize=7, fontname="helv")
    logo = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 25, 18), False)
    logo.set_rect(logo.irect, (200, 120, 255))
    p0.insert_image(fitz.Rect(33, 17, 58, 35), pixmap=logo)
    barras = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 170, 52), False)
    barras.set_rect(barras.irect, (0, 0, 0))
    p0.insert_image(fitz.Rect(50, 100, 220, 152), pixmap=barras)

    p1 = doc.new_page(width=297, height=421)
    p1.insert_text((68, 25), "DECLARAÇÃO DE CONTEÚDO", fontsize=8, fontname="helv")
    p1.insert_text((55, 48), "REMETENTE", fontsize=7, fontname="helv")
    p1.insert_text((194, 48), "DESTINATÁRIO", fontsize=7, fontname="helv")
    for x_rot, x_val, valores in (
        (9, 34, ("Aguiar", "30734713000140", "09852070")),
        (152, 177, ("Júnior graça", "11728252890", "13423580")),
    ):
        for i, (rotulo, valor) in enumerate(
            zip(("NOME:", "CPF/CNPJ:", "CEP:"), valores)
        ):
            y = 59 + i * 7
            p1.insert_text((x_rot, y), rotulo, fontsize=6, fontname="helv")
            p1.insert_text((x_val, y), valor, fontsize=6, fontname="helv")
    return doc.tobytes()


def _texto(pdf_bytes: bytes, pagina: int = 0) -> str:
    return fitz.open(stream=pdf_bytes, filetype="pdf")[pagina].get_text()


def _imagens_renderizadas(pdf_bytes: bytes, pagina: int = 0) -> list[tuple[int, int]]:
    page = fitz.open(stream=pdf_bytes, filetype="pdf")[pagina]
    return [
        (round(r.width), round(r.height))
        for img in page.get_images(full=True)
        for r in page.get_image_rects(img[0])
    ]


def test_remetente_vira_destinatario():
    out = transformar_etiqueta(_etiqueta_sintetica())
    txt = _texto(out)
    # o nome antigo do remetente sumiu e virou o nome do destinatário
    assert "Loja Origem" not in txt
    assert txt.count("Fulano") == 2


def test_remove_bloco_nf():
    out = transformar_etiqueta(_etiqueta_sintetica())
    txt = _texto(out)
    assert "DANFE" not in txt
    assert _CHAVE not in txt


def test_remove_logo_preserva_qr():
    imgs = _imagens_renderizadas(transformar_etiqueta(_etiqueta_sintetica()))
    # o logo pequeno (79x22) sai; o QR quadrado (60x60) fica
    assert (60, 60) in imgs
    assert (79, 22) not in imgs


def test_remove_rodape_picking():
    out = transformar_etiqueta(_etiqueta_sintetica())
    txt = _texto(out)
    assert "Total Items" not in txt
    assert "Deadline" not in txt
    assert "dg053.sp+a001.sp" not in txt
    # o resto da etiqueta continua lá
    assert "Fulano" in txt


def test_destinatario_nome_sobrepoe_o_lido():
    out = transformar_etiqueta(_etiqueta_sintetica(), destinatario_nome="Beltrano Oficial")
    txt = _texto(out)
    assert "Beltrano Oficial" in txt
    assert "Loja Origem" not in txt


def test_etiqueta_jt_nome_desalinhado_e_chave_sem_rotulo():
    txt = _texto(transformar_etiqueta(_etiqueta_jt()))
    # 1) o nome do destinatário é lido mesmo começando à esquerda do rótulo
    assert "m**1" not in txt
    assert txt.count("yasmin") == 2
    # 2) a chave da NF sai mesmo sem o rótulo DANFE/NF: como âncora
    assert _CHAVE not in txt


def test_etiqueta_ml_nao_corta_o_destinatario():
    # O bloco da NF no meio da etiqueta NÃO pode arrastar a faixa branca até o
    # fim da página (era o que apagava destinatário/endereço/barras no ML).
    out = transformar_etiqueta(_etiqueta_ml())
    txt = _texto(out)
    assert _CHAVE not in txt
    assert "DANFE" not in txt
    assert "KFA" not in txt
    assert txt.count("Daniel Do Carmo") == 2
    assert "Rua Teste 100" in txt
    assert (260, 40) in _imagens_renderizadas(out)


def test_etiqueta_ml_correios_preserva_cabecalho_postal():
    # O "NF:" solto no cabeçalho dos Correios NÃO pode virar âncora de bloco: a
    # faixa branca comia o rastreio, o código de barras dele e o destinatário.
    out = transformar_etiqueta(_etiqueta_ml_correios())
    txt = _texto(out)
    assert "NF:" not in txt
    assert "1464559" not in txt
    assert "AD779019760BR" in txt
    assert "Contrato: 9912278851" in txt
    assert "SHP: 47733455146" in txt
    assert "Daniel do Carmo" in txt
    assert (260, 40) in _imagens_renderizadas(out)


def test_etiqueta_ml_flex_troca_nome_e_preserva_o_resto_do_topo():
    # Sem rótulo REMETENTE na 1ª página: da 1ª linha some SÓ o nome da loja
    # (entra o destinatário, lido da Declaração na 2ª página) — endereço,
    # cidade/UF/CEP, Pack ID e o logo do ML FICAM (triagem do ML usa).
    out = transformar_etiqueta(_etiqueta_ml_flex())
    txt = _texto(out)
    for vazamento in ("Keila", "1423186352"):
        assert vazamento not in txt
    assert "Júnior graça" in txt
    # endereço/cidade do hub e o Pack ID continuam na etiqueta
    assert "Sadae Takagi 2235" in txt
    assert "Sao Bernardo do Campo BR-SP 09852070" in txt
    assert "Pack ID: 2000014590774099" in txt
    # roteirização e destinatário da etiqueta continuam lá
    assert "XSP4" in txt
    assert "SSP36" in txt
    assert "JULLY" in txt
    imgs = _imagens_renderizadas(out)
    assert (25, 18) in imgs       # logo do ML colado no bloco FICA
    assert (170, 52) in imgs      # código de barras do rastreio fica


def test_declaracao_apaga_cpf_e_cep_do_remetente():
    txt = _texto(transformar_etiqueta(_etiqueta_ml_flex()), pagina=1)
    # lado REMETENTE: nome trocado, documento e CEP apagados
    assert "Aguiar" not in txt
    assert "30734713000140" not in txt
    assert txt.count("Júnior graça") == 2
    # lado DESTINATÁRIO intacto
    assert "11728252890" in txt
    assert "13423580" in txt
    assert txt.count("09852070") == 0


def test_declaracao_nao_apaga_dados_quando_blocos_sao_empilhados():
    # Na etiqueta tipo Correios REMETENTE e DESTINATÁRIO ficam um embaixo do
    # outro — ali o corte horizontal não vale e nada pode ser apagado por ele.
    txt = _texto(transformar_etiqueta(_etiqueta_sintetica()))
    assert "Fulano" in txt


def test_pdf_invalido_levanta():
    with pytest.raises(EtiquetaTransformError):
        transformar_etiqueta(b"nao sou um pdf")


def test_pdf_vazio_levanta():
    with pytest.raises(EtiquetaTransformError):
        transformar_etiqueta(_PDF_SEM_PAGINAS)
