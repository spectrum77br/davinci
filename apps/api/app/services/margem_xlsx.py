"""Planilha da aba Margem — as linhas do filtro atual, como a tela mostra.

Consumido por GET /api/margens/marketplace/export.xlsx. As colunas calculadas
(Efetivo, Lucro, Margem Final) repetem as regras do MargemPainel.vue
(saldoEfetivoVisual, lucroFinal, margemFinal) — mudou lá, muda aqui.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from app.services.rentabilidade_xlsx import _BRL, _PCT, _header

_SP = ZoneInfo("America/Sao_Paulo")
_DATA = "DD/MM/YYYY"
# Plataformas em que o Saldo Efetivo é o repasse da plataforma (nunca o Bling).
_ANCORADAS = ("ml", "shopee", "tiktok")


def _f(v: object) -> float | None:
    return None if v is None else float(v)  # type: ignore[arg-type]


def _ancorado(r: Mapping) -> bool:
    return (r["plataforma"] or "") in _ANCORADAS


def _margem_final(r: Mapping) -> float | None:
    if _ancorado(r) and r["saldo_plataforma"] is None and r["saldo_manual"] is None:
        return None
    m = _f(r["margem_pos_reembolso"])
    return _f(r["margem_bling"]) if (m is None or m == 0) else m


def _lucro(r: Mapping) -> float | None:
    m = _margem_final(r)
    if m is None or r["custo_produto"] is None:
        return None
    return m * float(r["custo_produto"])


def _saldo_efetivo_visual(r: Mapping) -> float | None:
    # 0 enquanto não aprovado e Bling × Plataforma divergem (o alerta da tela).
    bling, plat = _f(r["saldo_bling"]), _f(r["saldo_plataforma"])
    divergente = (
        bling is not None and plat is not None and not _ancorado(r) and abs(bling - plat) > 0.01
    )
    if r["status"] != "Aprovado" and divergente:
        return 0.0
    final = _f(r["saldo_final"])
    return final if final is not None else _f(r["saldo_efetivo"])


def _conta(r: Mapping) -> str:
    nome = r["pricing_account_name"]
    if not nome:
        return r["conta"] or ""
    tipo = r["pricing_account_listing_type"] or ""
    if re.search(r"premium", tipo, re.I):
        return f"{nome} premium"
    if re.search(r"cl[aá]ss?ico", tipo, re.I):
        return f"{nome} clássico"
    return f"{nome} {tipo}" if tipo else nome


def _data(v: object) -> date | None:
    # Bling manda a data como meia-noite UTC: a parte da data vale como está.
    if isinstance(v, datetime):
        return (v.astimezone(UTC) if v.tzinfo else v).date()
    return v if isinstance(v, date) else None


def _hora(v: object) -> str:
    if not isinstance(v, datetime):
        return ""
    return (v.astimezone(_SP) if v.tzinfo else v).strftime("%H:%M")


def _motivo(r: Mapping) -> str:
    motivos = []
    if r["attention_margem"]:
        motivos.append("margem baixa")
    if r["attention_saldo"]:
        motivos.append("saldo")
    return ", ".join(motivos)


def build_margem_xlsx(
    rows: Sequence[Mapping],
    *,
    com_lucro: bool,
    com_frete_resultado: bool,
) -> BytesIO:
    """Monta o .xlsx (uma aba, uma linha por item) e devolve o buffer."""
    # (título, valor, formato)
    colunas: list[tuple[str, Callable[[Mapping], object], str | None]] = [
        ("Data", lambda r: _data(r["data"]), _DATA),
        ("Hora", lambda r: _hora(r["pedido_entrou_em"]), None),
        ("Pedido Bling", lambda r: r["pedido_bling"] or "", None),
        ("Pedido Marketplace", lambda r: r["pedido_marketplace"] or "", None),
        ("Plataforma", lambda r: (r["plataforma"] or "").upper(), None),
        ("Conta", _conta, None),
        ("Segmento", lambda r: r["pricing_leaf_segment_name"] or "", None),
        ("SKU", lambda r: r["sku"] or "", None),
        ("Produto", lambda r: r["produto"] or "", None),
        ("Quantidade", lambda r: _f(r["quantidade"]), "0"),
        ("Custo", lambda r: _f(r["custo_produto"]), _BRL),
        ("Frete Plataforma", lambda r: _f(r["frete_plataforma"]), _BRL),
        ("Frete Anúncio", lambda r: _f(r["frete_anuncio"]), _BRL),
        ("Frete Projetado", lambda r: _f(r["frete_projetado"]), _BRL),
    ]
    if com_frete_resultado:
        colunas.append(("Frete Resultado", lambda r: _f(r["resultado_frete"]), _BRL))
    colunas += [
        ("Saldo Plataforma", lambda r: _f(r["saldo_plataforma"]), _BRL),
        ("Saldo Bling", lambda r: _f(r["saldo_bling"]), _BRL),
        ("Saldo Efetivo", _saldo_efetivo_visual, _BRL),
        ("Reembolsos", lambda r: _f(r["ajustes"]), _BRL),
    ]
    if com_lucro:
        colunas.append(("Lucro", _lucro, _BRL))
    colunas += [
        ("Margem Final", _margem_final, _PCT),
        ("Margem Mínima", lambda r: _f(r["margem_minima"]), _PCT),
        ("Situação", lambda r: r["situacao"] or "", None),
        ("Status", lambda r: r["status"] or "", None),
        ("Motivo", _motivo, None),
        ("Observação", lambda r: r["observacao"] or "", None),
    ]

    wb = Workbook()
    ws = wb.active
    ws.title = "Margem"
    titulos = [c[0] for c in colunas]
    ws.append(titulos)
    _header(ws, 1, titulos)
    for r in rows:
        ws.append([valor(r) for _, valor, _ in colunas])

    ultima = len(rows) + 1
    for j, (titulo, _, fmt) in enumerate(colunas, start=1):
        letra = get_column_letter(j)
        if fmt:
            for i in range(2, ultima + 1):
                ws[f"{letra}{i}"].number_format = fmt
        largura = 40 if titulo == "Produto" else 30 if titulo == "Observação" else 14
        ws.column_dimensions[letra].width = largura
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(colunas))}{ultima}"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
