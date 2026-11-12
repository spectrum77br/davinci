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
    escrever_picking_livre,
    ler_picking,
    parece_pedido_shopee,
    plataforma_pelo_numero,
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


# --- revisão 09/11: casos de borda ------------------------------------------


def _tinta_por_linha(pdf: bytes) -> list[tuple[float, float, str]]:
    """(topo, fundo, texto) da tinta de cada linha do rodapé (sem DUIMP)."""
    return [(a, b, t) for a, b, t, _y in _linhas_do_pe(pdf) if not t.startswith("DUIMP")]


def _sem_sobrepor_linhas(pdf: bytes) -> None:
    linhas = sorted(_tinta_por_linha(pdf))
    for (a0, a1, ta), (b0, _b1, tb) in zip(linhas, linhas[1:], strict=False):
        if abs(a0 - b0) < 0.5:  # mesma linha (SKU e quantidade)
            continue
        assert a1 < b0, (ta, a1, tb, b0)


def _com_ref(raw: bytes, ref: str):
    from dataclasses import replace

    return replace(ler_picking(raw), ref=ref)


def test_ref_comprida_encolhe_o_cabecalho_sem_encostar_no_item():
    """Cabeçalho largo (ref ~6 caracteres maior que as reais) encolhe; o 1º
    item desce o que a tinta das duas fontes pede — não encosta."""
    raw = etiqueta_shopee([("dg062.pi+a003.pi", 1)])
    rodape = _com_ref(raw, "UP2NYY264253-" + "X" * 30)
    novo, st, det = escrever_picking(transformar_etiqueta(raw), [_item("dg062.pi+a003.pi")], rodape)
    assert st == "ok", det
    _sem_sobrepor_linhas(novo)
    page = fitz.open(stream=novo, filetype="pdf")[0]
    assert max(w[2] for w in page.get_text("words") if w[1] > 380) <= page.rect.width - 4.9
    r = ler_picking(novo)
    assert r.itens == (("dg062.pi+a003.pi", 1),) and r.fs_cab >= 6.0
    assert escrever_picking(novo, [_item("dg062.pi+a003.pi")], r)[1] == "igual"


def test_ref_enorme_perde_o_deadline_e_encurta_o_ref_dentro_da_pagina():
    raw = etiqueta_shopee([("dg062.pi+a003.pi", 1)])
    novo, st, det = escrever_picking(
        transformar_etiqueta(raw), [_item("a1")], _com_ref(raw, "X" * 60)
    )
    assert st == "ok", det
    r = ler_picking(novo)
    assert "Deadline" not in r.cabecalho
    assert r.ref.startswith("XXXX") and r.ref.endswith("...")
    assert r.fs_cab == pytest.approx(6.0)
    page = fitz.open(stream=novo, filetype="pdf")[0]
    assert max(w[2] for w in page.get_text("words") if w[1] > 380) <= page.rect.width - 4.9
    _sem_sobrepor_linhas(novo)
    # relido (ref encurtada) e reescrito com os mesmos itens: não mexe
    assert escrever_picking(novo, [_item("a1")], r)[1] == "igual"


def _escalada(pdf: bytes, largura: float, altura: float) -> bytes:
    """A etiqueta inteira desenhada numa página de outro tamanho (outra
    impressora/layout): o texto continua extraível."""
    src = fitz.open(stream=pdf, filetype="pdf")
    out = fitz.open()
    for k in range(src.page_count):
        out.new_page(width=largura, height=altura).show_pdf_page(
            fitz.Rect(0, 0, largura, altura), src, k
        )
    return out.tobytes()


def test_pagina_estreita_cabecalho_menor_que_os_itens_nao_sobrepoe():
    raw = _escalada(etiqueta_shopee([("dg054.ci+a001.ci", 1)]), 200, 295)
    rodape = ler_picking(raw)
    assert rodape is not None
    novo, st, det = escrever_picking(
        transformar_etiqueta(raw), [_item("dg054.sp+a001.sp"), _item("a003.pi", 2)], rodape
    )
    assert st == "ok", det
    r = ler_picking(novo)
    assert r.itens == (("dg054.sp+a001.sp", 1), ("a003.pi", 2))
    assert r.fs_cab < r.fs_itens  # o cabeçalho encolheu mais que os itens
    _sem_sobrepor_linhas(novo)


def test_pagina_girada_nao_e_erro_e_nao_mexe():
    """Nunca vista nas reais: não escreve e não vira `erro` (que loga warning
    a cada impressão)."""
    doc = fitz.open(stream=etiqueta_shopee(), filetype="pdf")
    doc[0].set_rotation(90)
    raw = doc.tobytes()
    rodape = ler_picking(raw)
    assert rodape is not None
    t = transformar_etiqueta(raw)
    novo, st, det = escrever_picking(t, [_item("dg054.sp+a001.sp")], rodape)
    assert (st, novo) == ("nao_coube", t) and "girada" in det
    doc = fitz.open(stream=_blob_antigo(), filetype="pdf")
    doc[0].set_rotation(90)
    antigo = doc.tobytes()
    novo, st, det = escrever_picking(antigo, [_item("a1")], None, criar_se_faltar=True)
    assert (st, novo) == ("sem_rodape", antigo) and "girada" in det


def _com_texto(pdf: bytes, x: float, y: float, texto: str, fs: float = 8.0) -> bytes:
    doc = fitz.open(stream=pdf, filetype="pdf")
    doc[0].insert_text((x, y), texto, fontsize=fs, fontname="helv")
    return doc.tobytes()


@pytest.mark.parametrize("y", [391.5, 401.4])  # na linha do cabeçalho / do item
def test_texto_colado_na_linha_do_rodape_nao_e_do_rodape(y):
    """Carimbo no meio da linha do rodapé: não entra no SKU lido (nem na
    reserva da subida) e a reescrita desiste em vez de apagá-lo."""
    raw = _com_texto(etiqueta_shopee([("dg062.pi+a003.pi", 1)]), 150, y, "LADO")
    r = ler_picking(raw)
    assert r.itens == (("dg062.pi+a003.pi", 1),)
    assert r.linhas == (("1. dg062.pi+a003.pi", "*1"),)
    assert "LADO" not in r.cabecalho
    novo, st, det = escrever_picking(raw, [_item("dg054.sp")], r)
    assert (st, novo) == ("nao_coube", raw) and "faixa_ocupada" in det
    assert "LADO" in _texto(novo, 0)


def test_rodape_com_deadline_vazio_volta_com_deadline_vazio():
    """O Upseller imprime 'Deadline: -' sem data: a reserva (sem itens do
    Bling) devolve o cabeçalho igual, e com os itens do Bling também fica."""
    raw = etiqueta_shopee([("b020.12", 8)], ref="UP2NYY000001", deadline=None)
    cru = ler_picking(raw)
    assert cru.cabecalho.endswith("; Deadline: -") and cru.sem_prazo
    reserva, st, _ = _subida(raw, [])
    assert st == "ok"
    assert ler_picking(reserva).cabecalho == cru.cabecalho
    assert ler_picking(reserva).linhas == cru.linhas
    com_bling, st, _ = _subida(raw, [_item("b020.12.18", 8)])
    assert st == "ok"
    r = ler_picking(com_bling)
    assert r.cabecalho == "SKU: 1; Total Items: 8; #UP2NYY000001; Deadline: -"
    assert escrever_picking(com_bling, [_item("b020.12.18", 8)], r)[1] == "igual"


# --- ordem: a mesma da declaração da etiqueta --------------------------------


def _subida_completa(raw: bytes, itens) -> bytes:
    """Subida como a do router: transform → declaração → rodapé."""
    rodape = ler_picking(raw)
    t, st_d, _ = reescrever_declaracao(transformar_etiqueta(raw), itens)
    assert st_d == "ok"
    novo, st, det = escrever_picking(t, itens, rodape)
    assert st == "ok", det
    return novo


def _ordem(pdf: bytes) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    decl = [(i.sku, i.quantidade) for i in ler_declaracao(pdf)]
    return decl, list(ler_picking(pdf).itens)


_A = formatar_item("dg062.pi+a003.pi", "Uranyx A17 Pro Max 12.128 - Branco + Fone", 1)
_B = formatar_item("b005.8", "Mala Chanfrada M3 tamanho 18 - Prata", 2)


def test_bling_inverteu_a_ordem_rodape_continua_na_ordem_da_declaracao():
    """A declaração não reescreve quando só a ordem muda (igual) — o rodapé
    também não: '1.'/'2.' do rodapé batem com o Nº da tabela."""
    blob = _subida_completa(etiqueta_shopee([("dg062.pi+a003.pi", 1)]), [_A, _B])
    assert _ordem(blob) == ([("dg062.pi+a003.pi", 1), ("b005.8", 2)],) * 2
    base, st_d, _ = reescrever_declaracao(blob, [_B, _A])
    assert st_d == "igual"
    novo, st, _ = escrever_picking(base, [_B, _A], ler_picking(base))
    assert st == "igual" and novo is base
    decl, rod = _ordem(novo)
    assert decl == rod


def test_declaracao_reescrita_na_ordem_nova_leva_o_rodape_junto():
    """Mudou a descrição (a declaração reescreve, na ordem do Bling) e a ordem:
    o rodapé sai na mesma ordem da tabela."""
    blob = _subida_completa(etiqueta_shopee([("dg062.pi+a003.pi", 1)]), [_A, _B])
    a2 = formatar_item("dg062.pi+a003.pi", "Uranyx A17 Pro Max 12.128 - Preto + Fone", 1)
    base, st_d, _ = reescrever_declaracao(blob, [_B, a2])
    assert st_d == "ok"
    novo, st, _ = escrever_picking(base, [_B, a2], ler_picking(base))
    assert st == "ok"
    decl, rod = _ordem(novo)
    assert decl == rod == [("b005.8", 2), ("dg062.pi+a003.pi", 1)]


def test_sem_declaracao_legivel_a_ordem_e_a_do_rodape_impresso():
    blob = _subida_completa(etiqueta_shopee([("dg062.pi+a003.pi", 1)]), [_A, _B])
    doc = fitz.open(stream=blob, filetype="pdf")
    doc.delete_page(1)  # sem a página da declaração
    so_etiqueta = doc.tobytes()
    novo, st, _ = escrever_picking(so_etiqueta, [_B, _A], ler_picking(so_etiqueta))
    assert st == "igual" and novo is so_etiqueta
    # conteúdo diferente: ordem do Bling
    novo, st, _ = escrever_picking(so_etiqueta, [_B, _item("a009")], ler_picking(so_etiqueta))
    assert st == "ok"
    assert ler_picking(novo).itens == (("b005.8", 2), ("a009", 1))


# --- criar só onde o transform apagou ----------------------------------------


def test_criar_se_faltar_so_onde_o_transform_apagou_um_rodape():
    """Layout da Shopee que nunca teve rodapé (DANFE no pé, PAC): o transform
    não apagou nada ali, a impressão não inventa rodapé."""
    nunca_teve = transformar_etiqueta(etiqueta_shopee(None))
    novo, st, det = escrever_picking(nunca_teve, [_item("a1")], None, criar_se_faltar=True)
    assert (st, novo) == ("sem_rodape", nunca_teve) and "sem_rastro" in det
    # a etiqueta que tinha rodapé (apagado pelo transform) ganha
    apagado = _blob_antigo()
    assert escrever_picking(apagado, [_item("a1")], None, criar_se_faltar=True)[1] == "ok"


# --- rodapé LIVRE: Amazon, TikTok, Carrefour, SEDEX (12/11/2026) -----------


def _etiqueta_livre(
    w: float = 300.0, h: float = 442.0, *, fim: float = 380.0, up: bool = True
) -> bytes:
    """Etiqueta sem rodapé (Amazon/TikTok/Carrefour/SEDEX): linha 'UP…' do
    Upseller no topo (quando `up`), moldura e um bloco preto (código de
    barras) que termina em `fim`."""
    doc = fitz.open()
    p = doc.new_page(width=w, height=h)
    if up:
        p.insert_text((20, 12), "UP2NYY266996 12/11/2026 08:29:54", fontsize=8)
    p.draw_rect(fitz.Rect(0.06 * w, 0.04 * h, 0.96 * w, fim), color=(0, 0, 0), width=1)
    p.draw_rect(fitz.Rect(0.1 * w, fim - 0.1 * h, 0.5 * w, fim - 2), color=None, fill=(0, 0, 0))
    return doc.tobytes()


def _pe(pdf: bytes) -> tuple[list[tuple[float, float, str, float]], float]:
    """(spans do rodapé livre (topo/fundo da tinta, texto, fs), altura)."""
    page = fitz.open(stream=pdf, filetype="pdf")[0]
    out = []
    for bl in page.get_text("dict")["blocks"]:
        for ln in bl.get("lines", []):
            for sp in ln["spans"]:
                y, fs, t = sp["origin"][1], sp["size"], sp["text"].strip()
                if t and y > page.rect.height * 0.5 and not t.startswith("DUIMP"):
                    out.append((y - _SOBE * fs, y + _DESCE * fs, t, fs))
    return sorted(out), page.rect.height


def test_livre_amazon_1_sku_abaixo_do_conteudo_com_ref_sem_deadline():
    pdf = _etiqueta_livre(fim=380.0)
    novo, st, det = escrever_picking_livre(pdf, [_item("b048.18")], plataforma="amazon")
    assert st == "ok", det
    assert "encolhida=0" in det
    pe, h = _pe(novo)
    textos = [t for _a, _b, t, _f in pe]
    assert textos[0] == "SKU: 1; Total Items: 1; #UP2NYY266996"
    assert "1. b048.18" in textos and "*1" in textos
    assert min(a for a, *_ in pe) > 380.0  # não cobre o conteúdo
    assert max(b for _a, b, *_ in pe) < h
    assert "Deadline" not in _texto(novo, 0)


def test_livre_amazon_3_skus_encolhe_8_por_cento_e_cabe():
    pdf = _etiqueta_livre(fim=422.0)  # como a real: imagem até 422 de 442
    itens = [_item("b048.18"), _item("b011.18"), _item("b005.18", 2)]
    novo, st, det = escrever_picking_livre(pdf, itens, plataforma="amazon")
    assert st == "ok", det
    assert "encolhida=1" in det and "mais=0" in det
    pe, h = _pe(novo)
    textos = [t for _a, _b, t, _f in pe]
    assert textos[0] == "SKU: 3; Total Items: 4; #UP2NYY266996"
    for t in ("1. b048.18", "2. b011.18", "3. b005.18", "( *2 )"):
        assert t in textos
    assert min(a for a, *_ in pe) > 422.0 * 0.92
    assert max(b for _a, b, *_ in pe) < h
    # o conteúdo encolheu preso no topo (a linha UP continua lá em cima)
    assert "UP2NYY266996 12/11/2026" in _texto(novo, 0)


def test_livre_tiktok_nao_encolhe_e_desvia_da_duimp():
    pdf = transformar_etiqueta(_etiqueta_livre(fim=390.0), duimp="26BR0000000001")
    novo, st, det = escrever_picking_livre(pdf, [_item("dg078.pi+a003.pi")], plataforma="tiktok")
    assert st == "ok", det
    assert "encolhida=0" in det
    pe, _h = _pe(novo)
    assert pe[0][2] == "SKU: 1; Total Items: 1; #UP2NYY266996"
    assert min(a for a, *_ in pe) > 390.0
    _fim, _topo = _sem_sobrepor_duimp(novo)


def test_livre_sedex_escala_a_fonte_com_a_largura_e_sem_ref():
    pdf = _etiqueta_livre(577.5, 813.0, fim=700.0, up=False)
    novo, st, det = escrever_picking_livre(pdf, [_item("b011.18", 2)], plataforma="amazon")
    assert st == "ok", det
    pe, h = _pe(novo)
    cab = pe[0]
    assert cab[2] == "SKU: 1; Total Items: 2"  # sem a linha UP: sem '#'
    assert cab[3] == pytest.approx(9.0 * 577.5 / 300.0, abs=0.05)
    assert min(a for a, *_ in pe) > 700.0 and max(b for _a, b, *_ in pe) < h
    # na impressora (reduzida pro tamanho da etiqueta) volta a ~9 pt
    impressa = redimensionar_para_etiqueta(novo)
    assert _pe(impressa)[0][0][3] == pytest.approx(9.0, abs=0.6)


def test_livre_carrefour_sem_ref():
    pdf = _etiqueta_livre(298.0, 419.0, fim=360.0, up=False)
    novo, st, det = escrever_picking_livre(pdf, [_item("b017.12.20")], plataforma="carrefour")
    assert st == "ok", det
    pe, _h = _pe(novo)
    assert pe[0][2] == "SKU: 1; Total Items: 1"
    assert sorted(t for _a, _b, t, _f in pe[1:]) == ["*1", "1. b017.12.20"]


def test_livre_sem_faixa_ou_shopee_ou_lixo_devolve_igual():
    itens = [_item("b048.18")]
    # conteúdo até a borda, sem Amazon pra encolher
    cheia = _etiqueta_livre(fim=440.0)
    assert escrever_picking_livre(cheia, itens, plataforma="tiktok")[:2] == (cheia, "sem_rodape")
    # Shopee com rodapé: não é com esta função
    shopee = etiqueta_shopee([("dg062.pi+a003.pi", 1)])
    assert escrever_picking_livre(shopee, itens, plataforma="shopee")[:2] == (shopee, "sem_rodape")
    # nunca levanta
    novo, st, _d = escrever_picking_livre(b"lixo", itens, plataforma="amazon")
    assert (novo, st) == (b"lixo", "erro")
    assert escrever_picking_livre(cheia, [], plataforma="amazon")[:2] == (cheia, "sem_itens")


@pytest.mark.parametrize(
    ("numeroloja", "esperado"),
    [
        ("261107NM1344HA", "shopee"),
        ("702-1234567-1234567", "amazon"),
        ("5778955150001-A", "carrefour"),
        ("579876543210987654", "tiktok"),
        ("5798765432109876543", "tiktok"),
        ("2000012345678901", None),  # ML
        ("PO-211-01234567890", None),  # Temu
        ("", None),
        (None, None),
    ],
)
def test_plataforma_pelo_numero(numeroloja, esperado):
    assert plataforma_pelo_numero(numeroloja) == esperado


def test_livre_amazon_encolhida_com_duimp_mantem_a_fonte_e_nao_cobre():
    """A DUIMP do transform usa 'hebo' na mesma página: depois de encolher,
    o cabeçalho continua em Helvetica-Bold (não cai pra Times) e o rodapé
    não encosta na DUIMP."""
    pdf = transformar_etiqueta(_etiqueta_livre(fim=422.0), duimp="26BR0000000001")
    itens = [_item("b048.18"), _item("b011.18"), _item("b005.18")]
    novo, st, det = escrever_picking_livre(pdf, itens, plataforma="amazon")
    assert st == "ok" and "encolhida=1" in det, det
    page = fitz.open(stream=novo, filetype="pdf")[0]
    spans = [
        s for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])
        for s in ln["spans"] if s["text"].strip()
    ]
    cab = next(s for s in spans if s["text"].startswith("SKU: 3"))
    assert cab["font"] == "Helvetica-Bold"
    duimp = next(s for s in spans if s["text"].startswith("DUIMP"))
    rodape = [s for s in spans if s["origin"][1] >= cab["origin"][1] - 0.1]
    fim_duimp = duimp["origin"][1] + _DESCE * duimp["size"]
    topo_rodape = min(s["origin"][1] - _SOBE * s["size"] for s in rodape)
    assert duimp not in rodape and fim_duimp < topo_rodape
