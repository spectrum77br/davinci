"""PDF da Simulação de importação (Financeiro → Simulação).

Camada PURA (sem DB): recebe os campos da cotação e devolve os bytes de
um PDF A4 com cabeçalho + mercadoria + tabela de custos USD/BRL.

O cálculo ESPELHA o computed `calc` de simulacao.vue (versão nova da
spec `simulaçao`):
- AFRMM = 8% do Frete+Seguro (não mais 2% do CIF);
- Taxa BL = campo fixo em BRL (não mais 9% do CIF);
- as 7 taxas brasileiras (`*_brl`) entram no espaço USD como valor/câmbio.
Se o front mudar o cálculo, mudar aqui junto.
"""

from dataclasses import dataclass
from decimal import Decimal

import fitz  # PyMuPDF

PDF_MEDIA = "application/pdf"

_AFRMM_PCT = 0.08  # 8% do Frete+Seguro


def _f(v: object) -> float:
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


@dataclass
class LinhaCusto:
    label: str
    usd: float
    brl: float
    aliquota: float | None = None  # fração (0.16 = 16%)
    bold: bool = False
    raw: bool = False  # fator: número puro, sem moeda


def calcular(sim: object) -> dict:
    """Deriva todas as linhas do quadro de custos a partir da cotação.

    `sim` é qualquer objeto com os atributos da FinanceiroSimulacao
    (o model ORM serve direto; testes podem passar um stub).
    """
    g = lambda name: _f(getattr(sim, name, None))  # noqa: E731

    cambio = g("taxa_cambio") or 1.0
    frete = g("frete_seguro_usd")
    valor_unit = g("valor_unitario_usd")
    qtd = g("quantidade")
    total_invoice = valor_unit * qtd
    cif = total_invoice + frete
    ii = cif * g("aliquota_ii")
    # Só o IPI tem base (TOTAL CIF + II); os demais são sobre o CIF.
    ipi = (cif + ii) * g("aliquota_ipi")
    pis = cif * g("aliquota_pis")
    cofins = cif * g("aliquota_cofins")
    afrmm = frete * _AFRMM_PCT

    # Taxas brasileiras fixas (BRL) → contribuição no espaço USD.
    def brl_to_usd(name: str) -> float:
        return g(name) / cambio if cambio > 0 else 0.0

    siscomex = brl_to_usd("taxa_siscomex_brl")
    taxa_bl = brl_to_usd("taxa_bl_brl")
    armaz = brl_to_usd("armazenagem_brl")
    desp_sda = brl_to_usd("despachante_sda_brl")
    desp_hon = brl_to_usd("despachante_honorarios_brl")
    corret = brl_to_usd("corretagem_cambio_brl")
    inspec = brl_to_usd("inspecao_brl")
    outras = brl_to_usd("outras_taxas_brl")

    subtotal = (
        cif + ii + ipi + pis + cofins + afrmm + siscomex + taxa_bl
        + armaz + desp_sda + desp_hon + corret + inspec + outras
    )
    taxas_gerais = subtotal * g("aliquota_taxas_gerais")
    imp_fed = subtotal * g("aliquota_impostos_fed")
    icms = subtotal * g("aliquota_icms")
    frete_nac = g("frete_nacional_usd")
    intermed = cif * g("aliquota_intermediacao")
    total_processo = subtotal + taxas_gerais + imp_fed + icms + frete_nac + intermed
    valor_unidade = total_processo / qtd if qtd > 0 else 0.0
    fator = valor_unidade / valor_unit if valor_unit > 0 else 0.0

    return {
        "cambio": cambio,
        "total_invoice": total_invoice,
        "cif": cif,
        "afrmm": afrmm,
        "subtotal": subtotal,
        "total_processo": total_processo,
        "valor_unidade": valor_unidade,
        "fator": fator,
        "linhas": [
            LinhaCusto("Frete + Seguro", frete, frete * cambio),
            LinhaCusto("Valor Unitário Origem", valor_unit, valor_unit * cambio),
            LinhaCusto("Total Invoice (Valor × Qtd)", total_invoice, total_invoice * cambio),
            LinhaCusto("TOTAL CIF", cif, cif * cambio, bold=True),
            LinhaCusto("II", ii, ii * cambio, aliquota=g("aliquota_ii")),
            LinhaCusto("IPI", ipi, ipi * cambio, aliquota=g("aliquota_ipi")),
            LinhaCusto("PIS", pis, pis * cambio, aliquota=g("aliquota_pis")),
            LinhaCusto("COFINS", cofins, cofins * cambio, aliquota=g("aliquota_cofins")),
            LinhaCusto("AFRMM (8% Frete+Seguro)", afrmm, afrmm * cambio, aliquota=_AFRMM_PCT),
            LinhaCusto("Taxa SISCOMEX", siscomex, g("taxa_siscomex_brl")),
            LinhaCusto("Taxa BL", taxa_bl, g("taxa_bl_brl")),
            LinhaCusto("Armazenagem Terminal", armaz, g("armazenagem_brl")),
            LinhaCusto("Despachante S.D.A", desp_sda, g("despachante_sda_brl")),
            LinhaCusto("Despachante Honorários", desp_hon, g("despachante_honorarios_brl")),
            LinhaCusto("Corretagem Câmbio", corret, g("corretagem_cambio_brl")),
            LinhaCusto("Inspeção (pré-embarque)", inspec, g("inspecao_brl")),
            LinhaCusto("Outras Taxas", outras, g("outras_taxas_brl")),
            LinhaCusto("SUBTOTAL", subtotal, subtotal * cambio, bold=True),
            LinhaCusto("Taxas Gerais Importação", taxas_gerais, taxas_gerais * cambio, aliquota=g("aliquota_taxas_gerais")),
            LinhaCusto("Impostos Federais Saída NF", imp_fed, imp_fed * cambio, aliquota=g("aliquota_impostos_fed")),
            LinhaCusto("ICMS", icms, icms * cambio, aliquota=g("aliquota_icms")),
            LinhaCusto("Frete Nacional", frete_nac, frete_nac * cambio),
            LinhaCusto("Intermediação (% CIF)", intermed, intermed * cambio, aliquota=g("aliquota_intermediacao")),
            LinhaCusto("TOTAL DO PROCESSO", total_processo, total_processo * cambio, bold=True),
            LinhaCusto("VALOR POR UNIDADE / KIT", valor_unidade, valor_unidade * cambio, bold=True),
            LinhaCusto("FATOR DE MULTIPLICAÇÃO", fator, fator, bold=True, raw=True),
        ],
    }


def _num_br(v: float, casas: int = 2) -> str:
    s = f"{v:,.{casas}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _fmt_usd(v: float) -> str:
    return f"US$ {_num_br(v)}"


def _fmt_brl(v: float) -> str:
    return f"R$ {_num_br(v)}"


def _fmt_pct(v: float | None) -> str:
    return "" if v is None else f"{v * 100:.2f}%".replace(".", ",")


def _data_br(v: object) -> str:
    if v is None:
        return ""
    try:
        return v.strftime("%d/%m/%Y")  # type: ignore[union-attr]
    except AttributeError:
        return str(v)


def montar_pdf(sim: object, *, lote_nome: str | None = None) -> bytes:
    """Gera o PDF A4 da simulação. `lote_nome` = nome do lote vinculado."""
    d = calcular(sim)
    g = lambda name: getattr(sim, name, None)  # noqa: E731

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    x0, x1 = 40, 555
    y = 46.0

    def texto(x: float, yy: float, s: str, size: float = 9, bold: bool = False) -> None:
        page.insert_text(
            (x, yy), s, fontsize=size,
            fontname="helv" if not bold else "hebo",
        )

    texto(x0, y, "Simulação de Importação", 16, bold=True)
    y += 24

    # ── Cabeçalho ──────────────────────────────────────────────────────
    cab = [
        ("Lote", lote_nome or ""),
        ("Cliente", str(g("cliente") or "")),
        ("Data", _data_br(g("data"))),
    ]
    for label, val in cab:
        texto(x0, y, f"{label}:", 9, bold=True)
        texto(x0 + 70, y, val, 9)
        y += 13
    y += 6

    # ── Mercadoria ─────────────────────────────────────────────────────
    texto(x0, y, "Mercadoria", 11, bold=True)
    y += 15
    merc = [
        ("Descrição", str(g("descricao") or "")),
        ("Quantidade", str(int(_f(g("quantidade")))) if g("quantidade") else ""),
        ("NCM", str(g("ncm") or "")),
        ("Descrição NCM", str(g("descricao_ncm") or "")[:110]),
        ("Taxa de Câmbio", _num_br(d["cambio"], 4)),
    ]
    for label, val in merc:
        texto(x0, y, f"{label}:", 9, bold=True)
        texto(x0 + 100, y, val, 9)
        y += 13
    y += 8

    # ── Tabela de custos ──────────────────────────────────────────────
    texto(x0, y, "Custos", 11, bold=True)
    y += 15
    col_aliq, col_usd, col_brl = 300, 380, 470
    texto(x0, y, "Item", 8, bold=True)
    texto(col_aliq, y, "Alíquota", 8, bold=True)
    texto(col_usd, y, "USD", 8, bold=True)
    texto(col_brl, y, "BRL", 8, bold=True)
    y += 4
    page.draw_line((x0, y), (x1, y), width=0.6)
    y += 11

    for ln in d["linhas"]:
        texto(x0, y, ln.label, 8.4, bold=ln.bold)
        texto(col_aliq, y, _fmt_pct(ln.aliquota), 8.4)
        if ln.raw:
            texto(col_usd, y, _num_br(ln.usd, 4), 8.4, bold=ln.bold)
        else:
            texto(col_usd, y, _fmt_usd(ln.usd), 8.4, bold=ln.bold)
            texto(col_brl, y, _fmt_brl(ln.brl), 8.4, bold=ln.bold)
        if ln.bold:
            page.draw_line((x0, y + 3), (x1, y + 3), width=0.4)
        y += 13.2

    pdf = doc.tobytes()
    doc.close()
    return pdf
