"""Rodapé de picking da etiqueta com os itens do pedido no Bling (09/11/2026).

Camada PURA (`services/nf_etiqueta_picking.py`) com a etiqueta da Shopee
SINTÉTICA na geometria medida nas reais (`tests/declaracao_sintetica.py`):
leitura do rodapé cru do Upseller e do nosso, troca de SKU, kit, quantidade,
idempotência, as linhas DUIMP que o transform carimba no pé, falta de espaço
e a criação do rodapé nas etiquetas antigas (subidas sem ele).
"""

from __future__ import annotations

import fitz  # PyMuPDF
import pytest

from app.services.nf_etiqueta_declaracao import (
    ItemDeclaracao,
    formatar_item,
    ler_declaracao,
    reescrever_declaracao,
)
from app.services.nf_etiqueta_juntar import redimensionar_para_etiqueta
from app.services.nf_etiqueta_picking import (
    escrever_picking,
    ler_picking,
    parece_pedido_shopee,
)
from app.services.nf_etiqueta_transform import transformar_etiqueta
from tests.declaracao_sintetica import (
    declaracao_upseller,
    etiqueta_shopee,
    sem_declaracao,
)

_SOBE, _DESCE = 0.767, 0.23  # tinta acima/abaixo da baseline (× fs)


def _item(sku: str, q: int = 1) -> ItemDeclaracao:
    return ItemDeclaracao(sku=sku, descricao="Produto Teste", variacao="", quantidade=q)


def _linhas_do_pe(pdf: bytes, pagina: int = 0) -> list[tuple[float, float, str, float]]:
    """(topo da tinta, fundo da tinta, texto, baseline) de cada span da metade
    de baixo da página da etiqueta."""
    page = fitz.open(stream=pdf, filetype="pdf")[pagina]
    out = []
    for bl in page.get_text("dict")["blocks"]:
        for ln in bl.get("lines", []):
            for s in ln["spans"]:
                y, fs = s["origin"][1], s["size"]
                if s["text"].strip() and y > page.rect.height * 0.8:
                    out.append((y - _SOBE * fs, y + _DESCE * fs, s["text"], y))
    return sorted(out, key=lambda x: x[3])


def _sem_sobrepor_duimp(pdf: bytes) -> tuple[float, float]:
    """(fim do rodapé, topo da 1ª DUIMP) pela tinta — o rodapé termina acima."""
    linhas = _linhas_do_pe(pdf)
    duimp = [x for x in linhas if x[2].startswith("DUIMP:")]
    rodape = [x for x in linhas if not x[2].startswith("DUIMP:")]
    assert duimp and rodape
    fim, topo = max(x[1] for x in rodape), min(x[0] for x in duimp)
    assert fim < topo, (fim, topo)
    return fim, topo


def _texto(pdf: bytes, pagina: int) -> str:
    return fitz.open(stream=pdf, filetype="pdf")[pagina].get_text()


# --- leitura ---------------------------------------------------------------


def test_le_o_rodape_cru_do_upseller():
    r = ler_picking(etiqueta_shopee([("dg062.pi+a003.pi", 1)], ref="UP2NYY264253"))
    assert r is not None
    assert r.pagina == 0
    assert r.ref == "UP2NYY264253"
    assert r.deadline == "09/11/2026"
    assert r.itens == (("dg062.pi+a003.pi", 1),)
    assert (r.total_skus, r.total_itens, r.mais) == (1, 1, 0)
    assert r.topo == pytest.approx(384.6, abs=0.2)
    assert r.fundo == pytest.approx(403.2, abs=0.3)
    assert r.cabecalho == "SKU: 1; Total Items: 1; #UP2NYY264253; Deadline:09/11/2026"
    assert r.itens_declaracao() == [ItemDeclaracao("dg062.pi+a003.pi", "", "", 1)]


def test_le_quantidade_entre_parenteses_e_deadline_vazio():
    """O Upseller escreve '( *8 )' pra quantidade > 1 e 'Deadline: -' sem data."""
    r = ler_picking(etiqueta_shopee([("b020.12", 8), ("a073", 1)], deadline=None))
    assert r is not None
    assert r.itens == (("b020.12", 8), ("a073", 1))
    assert r.deadline is None
    assert (r.total_skus, r.total_itens) == (2, 9)


def test_sem_rodape_devolve_none():
    assert ler_picking(etiqueta_shopee(None)) is None
    assert ler_picking(sem_declaracao()) is None  # etiqueta sem rodapé (não Shopee)
    assert ler_picking(declaracao_upseller([("b1", "Mala", "Preta", 1)], etiqueta=False)) is None
    assert ler_picking(b"nao sou pdf") is None
    assert ler_picking(b"") is None


def test_transform_continua_apagando_o_rodape():
    """`transformar_etiqueta` segue pura: só apaga (regra 4)."""
    assert ler_picking(transformar_etiqueta(etiqueta_shopee())) is None


# --- subida: transform + rodapé com o item do Bling --------------------------


def _subida(raw: bytes, itens, *, duimp: str | None = None) -> tuple[bytes, str, str]:
    rodape = ler_picking(raw)
    assert rodape is not None
    t = transformar_etiqueta(raw, duimp=duimp)
    return escrever_picking(t, itens or rodape.itens_declaracao(), rodape)


def test_troca_de_sku_sai_com_o_do_bling_e_mantem_ref_e_deadline():
    raw = etiqueta_shopee([("dg054.ci+a001.ci", 1)], ref="UP2NYY264266")
    novo, st, det = _subida(raw, [_item("dg054.sp+a001.sp")])
    assert st == "ok", det
    r = ler_picking(novo)
    assert r is not None
    assert r.itens == (("dg054.sp+a001.sp", 1),)
    assert r.cabecalho == "SKU: 1; Total Items: 1; #UP2NYY264266; Deadline:09/11/2026"
    assert r.topo == pytest.approx(384.6, abs=0.2)  # mesmo lugar do original
    txt = _texto(novo, 0)
    assert "dg054.ci" not in txt and "dg054.sp+a001.sp" in txt
    # o resto do transform continua valendo
    assert "35261100000000000000550050000001031000000000" not in txt
    assert "Shopee Loja Teste" not in txt


def test_kit_fica_numa_linha_so():
    novo, st, _ = _subida(etiqueta_shopee(), [_item("dg062.pi+a003.pi+a001.ci")])
    assert st == "ok"
    assert ler_picking(novo).itens == (("dg062.pi+a003.pi+a001.ci", 1),)
    assert "1. dg062.pi+a003.pi+a001.ci" in _texto(novo, 0)


def test_quantidade_maior_que_um():
    novo, st, _ = _subida(etiqueta_shopee(), [_item("b005.8", 3)])
    assert st == "ok"
    r = ler_picking(novo)
    assert r.itens == (("b005.8", 3),)
    assert r.cabecalho.startswith("SKU: 1; Total Items: 3;")
    assert r.linhas == (("1. b005.8", "( *3 )"),)
    assert "*3" in _texto(novo, 0)


def test_varios_itens_na_ordem_do_bling():
    itens = [_item("b017.12.18"), _item("b014.18", 2), _item("a001.ci")]
    novo, st, _ = _subida(etiqueta_shopee([("b017.12.18", 1), ("b014.18", 1)]), itens)
    assert st == "ok"
    r = ler_picking(novo)
    assert r.itens == (("b017.12.18", 1), ("b014.18", 2), ("a001.ci", 1))
    assert r.cabecalho.startswith("SKU: 3; Total Items: 4;")
    assert [e for e, _d in r.linhas] == ["1. b017.12.18", "2. b014.18", "3. a001.ci"]
    # todas cabem a 8 pt sem DUIMP (até 4 linhas)
    assert r.fs_itens == pytest.approx(8.0)


def test_sem_itens_do_bling_o_rodape_volta_como_o_upseller_imprimiu():
    raw = etiqueta_shopee([("b020.12", 8)], ref="UP2NYY244973")
    antes = ler_picking(raw)
    novo, st, _ = _subida(raw, [])  # pedido fake / consulta que falhou
    assert st == "ok"
    depois = ler_picking(novo)
    assert depois.cabecalho == antes.cabecalho
    assert depois.linhas == antes.linhas == (("1. b020.12", "( *8 )"),)


def test_idempotente_mesmos_bytes():
    novo, st, _ = _subida(etiqueta_shopee(), [_item("dg054.sp+a001.sp")])
    assert st == "ok"
    de_novo, st2, _ = escrever_picking(novo, [_item("dg054.sp+a001.sp")], ler_picking(novo))
    assert st2 == "igual"
    assert de_novo is novo
    # sem passar o rodapé lido, também
    assert escrever_picking(novo, [_item("dg054.sp+a001.sp")], None)[1] == "igual"


def test_rodape_cru_com_os_mesmos_itens_e_igual():
    raw = etiqueta_shopee([("dg062.pi+a003.pi", 1)])
    novo, st, _ = escrever_picking(raw, [_item("dg062.pi+a003.pi")], ler_picking(raw))
    assert st == "igual" and novo is raw


def test_mesma_entrada_mesmos_bytes():
    raw = etiqueta_shopee()
    t, rodape = transformar_etiqueta(raw), ler_picking(raw)
    a = escrever_picking(t, [_item("dg054.sp+a001.sp")], rodape)[0]
    b = escrever_picking(t, [_item("dg054.sp+a001.sp")], rodape)[0]
    assert a == b  # sem /ID novo a cada gravação


def test_rele_e_reescreve_o_nosso_rodape():
    """Impressão: o blob já tem o NOSSO rodapé e o Bling trocou depois."""
    blob = _subida(etiqueta_shopee(ref="UP2NYY000777"), [_item("dg054.sp+a001.sp")])[0]
    lido = ler_picking(blob)
    assert lido is not None and lido.ref == "UP2NYY000777"
    novo, st, det = escrever_picking(blob, [_item("dg054.pr+a001.pr", 2)], lido)
    assert st == "ok" and "trocado" in det
    r = ler_picking(novo)
    assert r.itens == (("dg054.pr+a001.pr", 2),)
    assert r.ref == "UP2NYY000777" and r.deadline == "09/11/2026"
    assert "dg054.sp" not in _texto(novo, 0)
    assert _texto(novo, 0).count("Total Items") == 1  # o velho saiu


def test_so_o_rodape_e_apagado():
    raw = etiqueta_shopee([("b017.12.18", 1), ("b014.18", 1)])
    t = transformar_etiqueta(raw)
    blob = escrever_picking(t, [_item("x1"), _item("x2")], ler_picking(raw))[0]
    novo, st, _ = escrever_picking(blob, [_item("y1")], ler_picking(blob))
    assert st == "ok"
    antes, depois = fitz.open(stream=blob, filetype="pdf"), fitz.open(stream=novo, filetype="pdf")
    for pg in (0, 1):
        a = [w[4] for w in antes[pg].get_text("words") if w[1] < 380]
        d = [w[4] for w in depois[pg].get_text("words") if w[1] < 380]
        assert a == d
        assert len(antes[pg].get_image_info()) == len(depois[pg].get_image_info())
    assert "CEP: 00000000" in _texto(novo, 0) and "x1" not in _texto(novo, 0)


def test_declaracao_nao_e_tocada():
    raw = etiqueta_shopee()
    decl = [formatar_item("dg054.sp+a001.sp", "Uranyx A17 Pro Max 12.128 - Branco + Fone", 1)]
    t, st_decl, _ = reescrever_declaracao(transformar_etiqueta(raw), decl)
    assert st_decl == "ok"
    novo, st, _ = escrever_picking(t, decl, ler_picking(raw))
    assert st == "ok"
    assert ler_declaracao(novo) == ler_declaracao(t) == decl
    assert _texto(novo, 1) == _texto(t, 1)
    # a marca da declaração continua (impressão segue no caminho rápido)
    assert reescrever_declaracao(novo, decl)[1] == "igual"


# --- DUIMP -------------------------------------------------------------------


@pytest.mark.parametrize("n_duimp", [1, 2, 3])
@pytest.mark.parametrize("n_itens", [1, 2, 3, 5])
def test_nunca_sobrepoe_as_linhas_duimp(n_duimp, n_itens):
    duimp = " | ".join(f"26BR00000{k}" for k in range(n_duimp))
    itens = [_item(f"dg0{k}.sp+a001.sp") for k in range(n_itens)]
    novo, st, det = _subida(etiqueta_shopee(), itens, duimp=duimp)
    assert st == "ok", det
    _sem_sobrepor_duimp(novo)
    txt = _texto(novo, 0)
    for k in range(n_duimp):
        assert f"DUIMP: 26BR00000{k}" in txt  # nenhuma DUIMP apagada
    r = ler_picking(novo)
    assert len(r.itens) + r.mais == n_itens
    assert r.total_skus == n_itens
    # e a reimpressão com os mesmos itens não mexe
    assert escrever_picking(novo, itens, r)[1] == "igual"


def test_tres_duimps_e_dois_itens_cabem_encolhendo():
    novo, st, _ = _subida(
        etiqueta_shopee(), [_item("dg054.sp+a001.sp"), _item("a003.pi")],
        duimp="26BR0001 | 26BR0002 | 26BR0003",
    )
    assert st == "ok"
    r = ler_picking(novo)
    assert r.itens == (("dg054.sp+a001.sp", 1), ("a003.pi", 1)) and r.mais == 0
    assert r.fs_itens < 8.0


def test_reescrever_com_duimp_nao_apaga_a_duimp():
    blob = _subida(etiqueta_shopee(), [_item("a1"), _item("a2")], duimp="26BR0001 | 26BR0002")[0]
    novo, st, _ = escrever_picking(blob, [_item("b1")], ler_picking(blob))
    assert st == "ok"
    txt = _texto(novo, 0)
    assert "DUIMP: 26BR0001" in txt and "DUIMP: 26BR0002" in txt
    assert "a1" not in txt.split() and "1. b1" in txt
    _sem_sobrepor_duimp(novo)


# --- falta de espaço -----------------------------------------------------------


def test_muitos_itens_encolhem_e_o_resto_vira_mais_n():
    itens = [_item(f"b0{k:02d}.18") for k in range(12)]
    novo, st, det = _subida(etiqueta_shopee(), itens)
    assert st == "ok", det
    r = ler_picking(novo)
    assert r.fs_itens == pytest.approx(6.0)
    assert r.mais > 0 and len(r.itens) + r.mais == 12
    assert r.linhas[-1] == (f"+{r.mais} itens", "")
    assert r.cabecalho.startswith("SKU: 12; Total Items: 12;")
    assert [s for s, _q in r.itens] == [f"b0{k:02d}.18" for k in range(len(r.itens))]
    assert r.fundo <= 442 - 2.0
    assert escrever_picking(novo, itens, r)[1] == "igual"


def test_cinco_itens_encolhem_sem_resumir():
    itens = [_item(f"c{k}") for k in range(5)]
    novo, st, _ = _subida(etiqueta_shopee(), itens)
    assert st == "ok"
    r = ler_picking(novo)
    assert r.mais == 0 and len(r.itens) == 5
    assert 6.0 <= r.fs_itens < 8.0


def _com_duimp_em(pdf: bytes, baseline: float) -> bytes:
    """DUIMP carimbada mais alto que o normal (só pra apertar a faixa)."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    doc[0].insert_text((6, baseline), "DUIMP: 26BR9999", fontsize=9, fontname="hebo")
    return doc.tobytes()


def test_nao_coube_nem_o_cabecalho():
    raw = etiqueta_shopee()
    t = _com_duimp_em(transformar_etiqueta(raw), 400.0)
    novo, st, det = escrever_picking(t, [_item("dg054.sp")], ler_picking(raw))
    assert st == "nao_coube", det
    assert novo is t


def test_nao_coube_cabe_uma_linha_mas_sao_tres_itens():
    raw = etiqueta_shopee()
    t = _com_duimp_em(transformar_etiqueta(raw), 411.4)
    tres = [_item("a1"), _item("a2"), _item("a3")]
    novo, st, _ = escrever_picking(t, tres, ler_picking(raw))
    assert st == "nao_coube" and novo is t
    um, st1, _ = escrever_picking(t, [_item("a1")], ler_picking(raw))
    assert st1 == "ok"
    _sem_sobrepor_duimp(um)


# --- impressão: etiquetas antigas sem rodapé ----------------------------------


def _blob_antigo(duimp: str | None = None) -> bytes:
    """Etiqueta da Shopee subida entre 04/08 e 09/11: o transform apagou o
    rodapé e ninguém escreveu de volta."""
    return transformar_etiqueta(etiqueta_shopee([("dg054.ci+a001.ci", 1)]), duimp=duimp)


def test_sem_rodape_sem_criar_nao_mexe():
    blob = _blob_antigo()
    novo, st, _ = escrever_picking(blob, [_item("dg054.sp+a001.sp")], None)
    assert st == "sem_rodape" and novo is blob


@pytest.mark.parametrize("duimp", [None, "26BR0001", "26BR0001 | 26BR0002 | 26BR0003"])
def test_criar_se_faltar_na_faixa_livre(duimp):
    blob = _blob_antigo(duimp)
    novo, st, det = escrever_picking(
        blob, [_item("dg054.sp+a001.sp")], None, criar_se_faltar=True
    )
    assert st == "ok" and "criado" in det
    r = ler_picking(novo)
    assert r.itens == (("dg054.sp+a001.sp", 1),)
    assert r.ref is None and r.deadline is None
    assert r.cabecalho == "SKU: 1; Total Items: 1"
    assert r.topo == pytest.approx(384.6, abs=0.2)
    if duimp:
        _sem_sobrepor_duimp(novo)
    # a reimpressão com o rodapé criado não cria outro
    assert escrever_picking(novo, [_item("dg054.sp+a001.sp")], r, criar_se_faltar=True)[1] == (
        "igual"
    )


def test_criar_se_faltar_com_a_faixa_ocupada_nao_mexe():
    doc = fitz.open(stream=_blob_antigo(), filetype="pdf")
    doc[0].insert_text((40, 420), "Observacao do transportador", fontsize=7, fontname="helv")
    ocupada = doc.tobytes()
    novo, st, det = escrever_picking(ocupada, [_item("a1")], None, criar_se_faltar=True)
    assert st == "sem_rodape" and "faixa_ocupada" in det
    assert novo is ocupada


def test_criar_se_faltar_com_imagem_na_faixa_nao_mexe():
    doc = fitz.open(stream=_blob_antigo(), filetype="pdf")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), False)
    pix.set_rect(pix.irect, (0, 0, 0))
    doc[0].insert_image(fitz.Rect(250, 400, 280, 430), pixmap=pix)
    ocupada = doc.tobytes()
    assert escrever_picking(ocupada, [_item("a1")], None, criar_se_faltar=True)[1] == "sem_rodape"


def test_etiqueta_que_nao_e_da_shopee_fica_intocada():
    """TikTok/ML/Amazon: sem rodapé no cru e a impressão não pede pra criar."""
    blob = transformar_etiqueta(sem_declaracao())
    novo, st, _ = escrever_picking(blob, [_item("a1")], ler_picking(blob))
    assert st == "sem_rodape" and novo is blob


# --- itens vazios / PDF ruim ------------------------------------------------


@pytest.mark.parametrize("itens", [None, [], [ItemDeclaracao("", "x", "", 1)]])
def test_sem_itens_mantem_o_rodape(itens):
    raw = etiqueta_shopee()
    novo, st, _ = escrever_picking(raw, itens, ler_picking(raw), criar_se_faltar=True)
    assert st == "sem_itens" and novo is raw
    assert ler_picking(novo).itens == (("dg054.ci+a001.ci", 1),)


def test_pdf_invalido_e_erro_com_os_bytes_originais():
    ruim = b"nao sou pdf"
    novo, st, det = escrever_picking(ruim, [_item("a1")], None, criar_se_faltar=True)
    assert st == "erro" and novo is ruim and det
    vazio, st2, _ = escrever_picking(b"", [_item("a1")], None)
    assert st2 == "erro" and vazio == b""


def test_texto_fora_do_latin1_nao_quebra():
    """Latin-1 sai como está; o que a Helvetica Base14 não escreve sai fora
    (mesma regra da declaração) e SKU que fica vazio não vira linha."""
    novo, st, _ = _subida(etiqueta_shopee(), [_item("açaí.x"), _item("ka→b✓"), _item("中文")])
    assert st == "ok"
    r = ler_picking(novo)
    assert r.itens == (("açaí.x", 1), ("kab", 1))
    assert r.total_skus == 2


def test_sku_comprido_termina_em_reticencias_sem_invadir_a_quantidade():
    longo = "dg054.sp+" + "+".join(f"a00{k}.sp" for k in range(1, 9)) + "+b012.12.18.24"
    novo, st, _ = _subida(etiqueta_shopee(), [_item(longo, 2)])
    assert st == "ok"
    r = ler_picking(novo)
    esq, dir_ = r.linhas[0]
    assert esq.endswith("...") and dir_ == "( *2 )"
    page = fitz.open(stream=novo, filetype="pdf")[0]
    sku_fim = max(w[2] for w in page.get_text("words") if w[4].endswith("..."))
    qtd_ini = min(w[0] for w in page.get_text("words") if w[4] == "(")
    assert sku_fim < qtd_ini
    assert escrever_picking(novo, [_item(longo, 2)], r)[1] == "igual"


# --- impressão de ponta a ponta (redimensionada) -----------------------------


def test_rodape_legivel_depois_do_redimensionamento_da_impressao():
    raw = etiqueta_shopee([("dg054.ci+a001.ci", 1)])
    blob = _subida(raw, [_item("dg054.sp+a001.sp")], duimp="26BR0001")[0]
    impresso = redimensionar_para_etiqueta(blob)
    r = ler_picking(impresso)
    assert r is not None and r.itens == (("dg054.sp+a001.sp", 1),)
    assert r.ref == "UP2NYY000001"


# --- reserva: nº de pedido da Shopee ----------------------------------------


@pytest.mark.parametrize(
    ("numeroloja", "esperado"),
    [
        ("261107NM1344HA", True),
        ("261108Q70YDNUK", True),
        (" 261107nqe8bwkh ", True),
        ("26110712345678", False),  # só dígitos (não é Shopee)
        ("2000012345678901", False),  # ML
        ("579876543210987654", False),  # TikTok
        ("702-1234567-1234567", False),  # Amazon
        ("PO-211-01234567890", False),  # Temu
        ("", False),
        (None, False),
    ],
)
def test_parece_pedido_shopee(numeroloja, esperado):
    assert parece_pedido_shopee(numeroloja) is esperado
