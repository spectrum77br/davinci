"""Os arquivos do relatório da Conferência Shopee: Excel, CSV, Markdown, JSON e HTML.

Tudo sai do relatório congelado (calculo.montar_relatorio, versao 1) — nenhum
arquivo recalcula número; só formata. As variações ("▲ 12,3%", "novo", "=",
"—", "p.p.") vêm de `calculo.variacao`, a mesma regra da tela.

  • excel — abas "Resumo" (grupo × métrica × 4 semanas + variações e dois
    gráficos de linha), "Semana" (duas células amarelas escolhem a semana e a
    de comparação; a tabela por conta recalcula com SOMASES sobre "Dados"),
    uma aba por métrica (conta × 4 semanas, em VALORES) e "Dados" (uma linha
    por semana × grupo × conta, os números crus; chave inicio|fim|grupo|conta).
    Estilo do relatório da Denúncia (Arial, cabeçalho azul-escuro, total azul
    claro, bordas cinza).
  • csv — `;` e vírgula decimal (abre direto no Excel em português), UTF-8 com
    BOM, uma linha por grupo × conta × semana.
  • markdown, json_bytes, html — o HTML é uma página só (CSS embutido, tema
    claro) com os 6 blocos do documento original: título, cartões, uma tabela
    por grupo, últimas 4 semanas, notas.
"""

from __future__ import annotations

import csv as _csv
import io
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from html import escape
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.services.conferencia_shopee.calculo import (
    METRICAS,
    dinheiro,
    formatar,
    investimento,
    variacao,
    variacoes,
)
from app.services.conferencia_shopee.periodos import FUSO, dia_da_semana

ROTULO_STATUS = {
    "pendente": "na fila",
    "coletando": "coletando",
    "ok": "ok",
    "parcial": "parcial",
    "deslogada": "deslogada",
    "perfil_em_uso": "perfil em uso",
    "sem_automacao": "sem automação",
    "bloqueada": "bloqueada pela Shopee",
    "interrompida": "interrompida",
    "erro": "erro",
    "expirada": "não coletada a tempo",
}

# Nome da aba de cada métrica (o Excel não aceita "/" em nome de aba).
ABA_METRICA = {
    "vendas_afiliados": "Vendas afiliados",
    "vendas_ads": "Vendas Ads",
    "saldo_ads": "Saldo Ads",
    "impressoes": "Impressões",
    "invest_afiliados": "Invest. afiliados",
    "invest_ads": "Invest. Ads",
    "pct": "% sobre vendas",
    "vendas": "Vendas",
}

_NOTA_GRUPO = {
    "mala": "",
    "celular": "total da conta − eletro; saldo de Ads da conta inteira",
    "eletro": "só os produtos de eletro das contas de celular; Saldo Ads = —",
}


# ───────────────────────────────────────────────────────────── textos comuns


def _ddmm(d: str) -> str:
    return f"{d[8:10]}/{d[5:7]}" if d and len(d) >= 10 else (d or "")


def _ddmmaaaa(d: str) -> str:
    return f"{d[8:10]}/{d[5:7]}/{d[:4]}" if d and len(d) >= 10 else (d or "")


def titulo(rel: Mapping) -> str:
    """"Conferência Shopee — 28/09 a 04/10/2026"."""
    s = (rel.get("semanas") or [None])[0]
    if not s:
        return "Conferência Shopee"
    return f"Conferência Shopee — {_ddmm(s['inicio'])} a {_ddmmaaaa(s['fim'])}"


def comparado_com(rel: Mapping) -> str:
    """"comparado com 21/09 a 27/09"."""
    sem = rel.get("semanas") or []
    if len(sem) < 2:
        return ""
    return f"comparado com {_ddmm(sem[1]['inicio'])} a {_ddmm(sem[1]['fim'])}"


def tipo_texto(rel: Mapping) -> str:
    """"semana fechada" / "parcial seg–qua"."""
    if rel.get("tipo") != "parcial":
        return "semana fechada"
    s = (rel.get("semanas") or [None])[0]
    if not s:
        return "parcial"
    return f"parcial {dia_da_semana(s['inicio'])[:3]}–{dia_da_semana(s['fim'])[:3]}"


def _quando(iso: Any) -> str:
    """ISO → "06/10/2026 às 16:41" (Brasília)."""
    if not iso:
        return ""
    try:
        q = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    if q.tzinfo is not None:
        q = q.astimezone(FUSO)
    return q.strftime("%d/%m/%Y às %H:%M")


def _status(s: str | None) -> str:
    return ROTULO_STATUS.get(s or "", (s or "—").replace("_", " "))


def _rotulos(rel: Mapping) -> list[str]:
    return [s.get("rotulo") or f"{_ddmm(s['inicio'])}–{_ddmm(s['fim'])}" for s in rel["semanas"]]


def _grupos_e_geral(rel: Mapping) -> list[tuple[str, str, Mapping]]:
    """[(chave, rótulo, total)] de Mala, Celular, Eletro e Geral."""
    saida = [(g["chave"], g["rotulo"], g["total"]) for g in rel["grupos"]]
    saida.append(("geral", "Geral", rel["geral"]))
    return saida


def _contas_txt(n: int) -> str:
    return f"{n} {'conta' if n == 1 else 'contas'}"


def _valor(semanas: Sequence[Mapping], i: int, chave: str) -> float | None:
    return (semanas[i] if i < len(semanas) else {}).get(chave)


def _nome_arquivo_base(rel: Mapping) -> str:
    s = (rel.get("semanas") or [None])[0]
    return f"conferencia-shopee-{s['inicio']}_{s['fim']}" if s else "conferencia-shopee"


def nome_arquivo(rel: Mapping, ext: str) -> str:
    """conferencia-shopee-<S1.inicio>_<S1.fim>.<ext>."""
    return f"{_nome_arquivo_base(rel)}.{ext}"


# ───────────────────────────────────────────────────────────── Excel

_FONTE = "Arial"
_TITULO = Font(name=_FONTE, bold=True, size=13)
_CAB_FILL = PatternFill("solid", fgColor="1F3864")
_CAB_FONT = Font(name=_FONTE, bold=True, color="FFFFFF", size=10)
_TOTAL_FILL = PatternFill("solid", fgColor="D9E1F2")
_ENTRADA_FILL = PatternFill("solid", fgColor="FFFF00")
_NORMAL = Font(name=_FONTE, size=10)
_NEGRITO = Font(name=_FONTE, size=10, bold=True)
_CINZA = Font(name=_FONTE, size=10, color="808080")
_fino = Side(style="thin", color="BFBFBF")
_BORDA = Border(left=_fino, right=_fino, top=_fino, bottom=_fino)
_COR_XLSX = {"verde": "008000", "vermelho": "C00000", "cinza": "808080"}
_FORMATO = {
    "dinheiro": '"R$" #,##0.00',
    "inteiro": "#,##0",
    "percentual": '0.0"%"',
}


def _cab(ws, linha: int, valores: Sequence[Any], col: int = 1) -> None:
    for j, v in enumerate(valores, col):
        c = ws.cell(linha, j, v)
        c.font, c.fill, c.border = _CAB_FONT, _CAB_FILL, _BORDA
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _celula(
    ws, linha: int, col: int, valor: Any, tipo: str | None = None, total=False, formula=False
):
    c = ws.cell(linha, col, valor)
    if not formula and isinstance(valor, str) and valor.startswith("="):
        # O openpyxl trata todo texto que começa com "=" como fórmula: a
        # variação "=" (sem mudança) viraria uma fórmula vazia e o Excel
        # pediria para "reparar" o arquivo.
        c.data_type = "s"
    c.font = _NEGRITO if total else _NORMAL
    c.border = _BORDA
    if total:
        c.fill = _TOTAL_FILL
    if tipo and isinstance(valor, int | float):
        c.number_format = _FORMATO[tipo]
    return c


def _celula_variacao(ws, linha: int, col: int, var: Mapping, total=False):
    c = _celula(ws, linha, col, var["texto"], total=total)
    c.font = Font(name=_FONTE, size=10, bold=total, color=_COR_XLSX.get(var["cor"], "808080"))
    c.alignment = Alignment(horizontal="center")
    return c


def _larguras(ws, larguras: Sequence[int]) -> None:
    for j, w in enumerate(larguras, 1):
        ws.column_dimensions[get_column_letter(j)].width = w


def _aba_resumo(wb: Workbook, rel: Mapping) -> None:
    ws = wb.active
    ws.title = "Resumo"
    rotulos = _rotulos(rel)
    ws.cell(1, 1, f"{titulo(rel)} ({tipo_texto(rel)})").font = _TITULO
    ws.cell(2, 1, f"{comparado_com(rel)} · gerado em {_quando(rel.get('gerado_em'))}").font = (
        _NORMAL
    )
    linha = 4
    for chave, rot, total in _grupos_e_geral(rel):
        _cab(
            ws,
            linha,
            [f"{rot} ({_contas_txt(total['contas'])})", *rotulos, "vs anterior", "vs média 3 sem."],
        )
        linha += 1
        for m in METRICAS:
            _celula(ws, linha, 1, m["rotulo"])
            for i in range(len(rotulos)):
                _celula(ws, linha, 2 + i, _valor(total["semanas"], i, m["chave"]), m["tipo"])
            v_ant, v_media = variacoes(total["semanas"], m["chave"])
            _celula_variacao(ws, linha, 2 + len(rotulos), v_ant)
            _celula_variacao(ws, linha, 3 + len(rotulos), v_media)
            linha += 1
        if chave != "geral" and total.get("sem_dados"):
            ws.cell(linha, 1, f"{total['sem_dados']} sem dados").font = _CINZA
            linha += 1
        linha += 1

    # Dados dos gráficos: semanas da mais antiga para a mais recente.
    grupos = _grupos_e_geral(rel)
    ws.cell(linha, 1, "Dados dos gráficos (da semana mais antiga para a mais recente)").font = (
        _NEGRITO
    )
    linha += 1
    blocos = {}
    for chave, titulo_bloco in (("vendas", "Vendas"), ("pct", "% s/ vendas")):
        _cab(ws, linha, [titulo_bloco, *[rot for _, rot, _ in grupos]])
        inicio = linha
        for i in reversed(range(len(rotulos))):
            linha += 1
            _celula(ws, linha, 1, rotulos[i])
            tipo = "percentual" if chave == "pct" else "dinheiro"
            for j, (_, _, total) in enumerate(grupos, 2):
                _celula(ws, linha, j, _valor(total["semanas"], i, chave), tipo)
        blocos[chave] = (inicio, linha)
        linha += 2

    for n, (chave, nome_grafico, formato) in enumerate(
        (
            ("vendas", "Vendas por grupo", '"R$" #,##0'),
            ("pct", "% s/ vendas por grupo", '0.0"%"'),
        )
    ):
        inicio, fim = blocos[chave]
        grafico = LineChart()
        grafico.title = nome_grafico
        grafico.height, grafico.width = 7.5, 16
        grafico.y_axis.number_format = formato
        grafico.y_axis.majorGridlines = None
        grafico.x_axis.delete = False
        grafico.y_axis.delete = False
        dados = Reference(ws, min_col=2, max_col=1 + len(grupos), min_row=inicio, max_row=fim)
        grafico.add_data(dados, titles_from_data=True)
        grafico.set_categories(Reference(ws, min_col=1, min_row=inicio + 1, max_row=fim))
        ws.add_chart(grafico, f"J{4 + n * 16}")

    _larguras(ws, [28, *[15] * len(rotulos), 14, 16])
    ws.freeze_panes = ws.cell(4, 2)


def _linhas_dados(rel: Mapping) -> list[dict]:
    """Uma linha por semana × grupo × conta (a base da aba "Dados" e do CSV)."""
    saida = []
    for i, sem in enumerate(rel["semanas"]):
        for g in rel["grupos"]:
            for lin in g["linhas"]:
                ident = lin.get("conta_id") or lin["conta"]
                saida.append(
                    {
                        "chave": f"{sem['inicio']}|{sem['fim']}|{g['chave']}|{ident}",
                        "semana": sem.get("rotulo") or "",
                        "inicio": sem["inicio"],
                        "fim": sem["fim"],
                        "grupo": g["rotulo"],
                        "conta": lin["conta"],
                        "ident": ident,
                        "usuario": lin.get("usuario") or "",
                        "status": lin.get("status") or "",
                        "erro": lin.get("erro") or "",
                        "valores": lin["semanas"][i] if i < len(lin["semanas"]) else {},
                    }
                )
    return saida


_COLS_DADOS = ["Chave", "Semana", "Início", "Fim", "Grupo", "Conta", "Conta (id)", "Usuário",
               "Status"]
_COL_METRICA_DADOS = {
    m["chave"]: get_column_letter(len(_COLS_DADOS) + 1 + k) for k, m in enumerate(METRICAS)
}


def _aba_dados(wb: Workbook, linhas: list[dict]) -> int:
    ws = wb.create_sheet("Dados")
    _cab(ws, 1, [*_COLS_DADOS, *[m["rotulo"] for m in METRICAS]])
    for n, d in enumerate(linhas, 2):
        for j, v in enumerate(
            [d["chave"], d["semana"], d["inicio"], d["fim"], d["grupo"], d["conta"], d["ident"],
             d["usuario"], _status(d["status"])],
            1,
        ):
            _celula(ws, n, j, v)
        for k, m in enumerate(METRICAS, len(_COLS_DADOS) + 1):
            _celula(ws, n, k, d["valores"].get(m["chave"]), m["tipo"])
    _larguras(ws, [58, 13, 11, 11, 9, 16, 38, 18, 14, *[15] * len(METRICAS)])
    ws.freeze_panes = ws.cell(2, 1)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(_COLS_DADOS) + len(METRICAS))}" + str(
        max(len(linhas), 1) + 1
    )
    return max(len(linhas), 1) + 1


def _texto_xlsx(v: str) -> str:
    return '"' + v.replace('"', '""') + '"'


# Variação no Excel: a célula guarda o número (fração para %, pontos para
# p.p.) e o formato desenha a seta e a cor — como o texto de calculo.variacao.
_VERDE, _VERMELHO = "[Color10]", "[Red]"


def _formato_variacao(tipo: str, bom: str) -> str:
    sobe = _VERDE if bom == "sobe" else _VERMELHO if bom == "desce" else ""
    desce = _VERMELHO if bom == "sobe" else _VERDE if bom == "desce" else ""
    if tipo == "percentual":
        return f'{sobe}"▲ "0.0" p.p.";{desce}"▼ "0.0" p.p.";"="'
    return f'{sobe}"▲ "0.0%;{desce}"▼ "0.0%;"="'


def _aba_semana(wb: Workbook, rel: Mapping, n_dados: int) -> None:
    ws = wb.create_sheet("Semana", 1)
    rotulos = _rotulos(rel)
    ws.cell(1, 1, "Semana").font = _NEGRITO
    ws.cell(2, 1, "Comparar com").font = _NEGRITO
    for linha, valor in ((1, rotulos[0] if rotulos else ""),
                         (2, rotulos[1] if len(rotulos) > 1 else "")):
        c = ws.cell(linha, 2, valor)
        c.fill, c.font, c.border = _ENTRADA_FILL, _NEGRITO, _BORDA
        c.alignment = Alignment(horizontal="center")
    ws.cell(1, 3, "← escolha as semanas na lista (células amarelas)").font = _CINZA
    lista = DataValidation(
        type="list", formula1=_texto_xlsx(",".join(rotulos)), allow_blank=False
    )
    lista.error = "Escolha uma das semanas da lista."
    lista.errorTitle = "Semana"
    ws.add_data_validation(lista)
    lista.add("B1")
    lista.add("B2")

    # Cabeçalho: Grupo | Conta | para cada métrica: Semana, Comparação, Var.
    _cab(ws, 4, ["Grupo", "Conta"])
    _cab(ws, 5, ["", ""])
    ws.merge_cells("A4:A5")
    ws.merge_cells("B4:B5")
    col = 3
    colunas: dict[str, tuple[str, str, str]] = {}
    for m in METRICAS:
        _cab(ws, 4, [m["rotulo"], "", ""], col)
        ws.merge_cells(start_row=4, start_column=col, end_row=4, end_column=col + 2)
        _cab(ws, 5, ["Semana", "Comparação", "Var."], col)
        colunas[m["chave"]] = tuple(get_column_letter(col + k) for k in range(3))
        col += 3

    def faixa(letra: str) -> str:
        return f"Dados!${letra}$2:${letra}${n_dados}"

    semana_col, grupo_col, ident_col = faixa("B"), faixa("E"), faixa("G")

    def criterios(celula_semana: str, grupo: str | None, ident: str | None) -> str:
        partes = [semana_col, celula_semana]
        if grupo is not None:
            partes += [grupo_col, _texto_xlsx(grupo)]
        if ident is not None:
            partes += [ident_col, _texto_xlsx(ident)]
        return ",".join(partes)

    def escrever(linha: int, rot_grupo: str, nome: str, grupo: str | None, ident: str | None,
                 total: bool) -> None:
        _celula(ws, linha, 1, rot_grupo, total=total)
        _celula(ws, linha, 2, nome, total=total)
        for m in METRICAS:
            c_sem, c_cmp, c_var = colunas[m["chave"]]
            for letra, entrada in ((c_sem, "$B$1"), (c_cmp, "$B$2")):
                crit = criterios(entrada, grupo, ident)
                if m["chave"] == "pct":
                    ia = f"{colunas['invest_afiliados'][0 if letra == c_sem else 1]}{linha}"
                    iads = f"{colunas['invest_ads'][0 if letra == c_sem else 1]}{linha}"
                    vend = f"{colunas['vendas'][0 if letra == c_sem else 1]}{linha}"
                    # ROUND(…;2): o relatório guarda o % com 2 casas e a
                    # variação compara ESSE número — sem arredondar, 6,40 ×
                    # 6,40 daria "▲ 0,0 p.p." colorido aqui e "=" na tela.
                    expr = (
                        f'=IF(OR(ISTEXT({vend}),AND(ISTEXT({ia}),ISTEXT({iads}))),"—",'
                        f'IF({vend}=0,"—",ROUND((N({ia})+N({iads}))/{vend}*100,2)))'
                    )
                else:
                    metrica = faixa(_COL_METRICA_DADOS[m["chave"]])
                    expr = (
                        f'=IF(COUNTIFS({crit},{metrica},"<>")=0,"—",SUMIFS({metrica},{crit}))'
                    )
                c = _celula(
                    ws, linha, column_index_from_string(letra), expr, total=total,
                    formula=True,
                )
                c.number_format = _FORMATO[m["tipo"]]
            x, y = f"{c_sem}{linha}", f"{c_cmp}{linha}"
            if m["tipo"] == "percentual":
                expr = f'=IF(OR(ISTEXT({x}),ISTEXT({y})),"—",IF({x}={y},"=",{x}-{y}))'
            else:
                novo = '"—"' if m["chave"] == "saldo_ads" else '"novo"'
                expr = (
                    f'=IF(OR(ISTEXT({x}),ISTEXT({y})),"—",IF({y}=0,IF({x}=0,"=",'
                    f'IF({x}>0,{novo},"—")),IF({x}={y},"=",({x}-{y})/ABS({y}))))'
                )
            c = _celula(
                ws, linha, column_index_from_string(c_var), expr, total=total, formula=True
            )
            c.number_format = _formato_variacao(m["tipo"], m["bom"])
            c.alignment = Alignment(horizontal="center")

    linha = 6
    for g in rel["grupos"]:
        for lin in g["linhas"]:
            ident = lin.get("conta_id") or lin["conta"]
            escrever(linha, g["rotulo"], lin["conta"], g["rotulo"], ident, total=False)
            linha += 1
        escrever(linha, g["rotulo"], f"Total {g['rotulo']} ({_contas_txt(len(g['linhas']))})",
                 g["rotulo"], None, total=True)
        linha += 1
    escrever(linha, "Geral", f"Geral ({_contas_txt(rel['geral']['contas'])})", None, None,
             total=True)
    _larguras(ws, [12, 30, *[14, 14, 11] * len(METRICAS)])
    ws.freeze_panes = ws.cell(6, 3)


def _aba_metrica(wb: Workbook, rel: Mapping, m: Mapping) -> None:
    ws = wb.create_sheet(ABA_METRICA[m["chave"]])
    rotulos = _rotulos(rel)
    ws.cell(1, 1, f"{m['rotulo']} — últimas 4 semanas").font = _TITULO
    _cab(ws, 3, ["Grupo", "Conta", *rotulos, "vs anterior", "vs média 3 sem."])
    linha = 4

    def escrever(rot_grupo: str, nome: str, semanas: Sequence[Mapping], total: bool) -> None:
        _celula(ws, linha, 1, rot_grupo, total=total)
        _celula(ws, linha, 2, nome, total=total)
        for i in range(len(rotulos)):
            _celula(ws, linha, 3 + i, _valor(semanas, i, m["chave"]), m["tipo"], total=total)
        v_ant, v_media = variacoes(semanas, m["chave"])
        _celula_variacao(ws, linha, 3 + len(rotulos), v_ant, total=total)
        _celula_variacao(ws, linha, 4 + len(rotulos), v_media, total=total)

    for g in rel["grupos"]:
        for lin in g["linhas"]:
            escrever(g["rotulo"], lin["conta"], lin["semanas"], total=False)
            linha += 1
        escrever(
            g["rotulo"],
            f"Total {g['rotulo']} ({_contas_txt(g['total']['contas'])})",
            g["total"]["semanas"],
            total=True,
        )
        linha += 1
    escrever("Geral", f"Geral ({_contas_txt(rel['geral']['contas'])})", rel["geral"]["semanas"],
             total=True)
    _larguras(ws, [12, 30, *[15] * len(rotulos), 14, 16])
    ws.freeze_panes = ws.cell(4, 3)


def excel(rel: Mapping) -> BytesIO:
    """A planilha das 4 semanas (Resumo, Semana, uma aba por métrica, Dados)."""
    wb = Workbook()
    _aba_resumo(wb, rel)
    linhas = _linhas_dados(rel)
    for m in METRICAS:
        _aba_metrica(wb, rel, m)
    n_dados = _aba_dados(wb, linhas)
    _aba_semana(wb, rel, n_dados)
    # As fórmulas da aba "Semana" recalculam ao abrir (o openpyxl não calcula).
    wb.calculation.fullCalcOnLoad = True
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ───────────────────────────────────────────────────────────── CSV


def _num_csv(v: Any, tipo: str) -> str:
    if v is None:
        return ""
    if tipo == "inteiro":
        return str(int(round(v)))
    return f"{v:.2f}".replace(".", ",")


def csv(rel: Mapping) -> bytes:
    """`;`, vírgula decimal, UTF-8 com BOM: uma linha por grupo × conta × semana."""
    buf = io.StringIO()
    w = _csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(
        ["Grupo", "Conta", "Usuário", "Status", "Erro", "Semana", "Início", "Fim",
         *[m["rotulo"] for m in METRICAS]]
    )
    for g in rel["grupos"]:
        for lin in g["linhas"]:
            for i, sem in enumerate(rel["semanas"]):
                valores = lin["semanas"][i] if i < len(lin["semanas"]) else {}
                w.writerow(
                    [
                        g["rotulo"],
                        lin["conta"],
                        lin.get("usuario") or "",
                        _status(lin.get("status")),
                        lin.get("erro") or "",
                        sem.get("rotulo") or "",
                        _ddmmaaaa(sem["inicio"]),
                        _ddmmaaaa(sem["fim"]),
                        *[_num_csv(valores.get(m["chave"]), m["tipo"]) for m in METRICAS],
                    ]
                )
    return buf.getvalue().encode("utf-8-sig")


# ───────────────────────────────────────────────────────────── JSON


def json_bytes(rel: Mapping) -> bytes:
    """O relatório como está guardado (dados para regenerar os outros)."""
    return json.dumps(rel, ensure_ascii=False, indent=2).encode("utf-8")


# ───────────────────────────────────────────────────────────── Markdown


def _md(texto: Any) -> str:
    return str(texto if texto is not None else "").replace("|", "\\|").replace("\n", " ")


def _celula_md(semanas: Sequence[Mapping], m: Mapping) -> str:
    atual = _valor(semanas, 0, m["chave"])
    anterior = _valor(semanas, 1, m["chave"])
    if atual is None and anterior is None:
        return "—"
    return f"{formatar(atual, m['tipo'])} ({_ant_texto(atual, anterior, m)})"


def _ant_texto(atual: float | None, anterior: float | None, m: Mapping) -> str:
    """"ant. R$ 900,00 ▲ 11,1%"; sem a semana anterior, só "ant. —"."""
    if anterior is None:
        return "ant. —"
    var = variacao(atual, anterior, m["tipo"], m["bom"], m["chave"])
    return f"ant. {formatar(anterior, m['tipo'])} {var['texto']}"


def _cartao(total: Mapping) -> list[tuple[str, str, str, dict]]:
    """[(rótulo, valor, anterior, variação)] de Vendas, Investimento e %."""
    s = total.get("semanas") or []
    s0 = s[0] if s else {}
    s1 = s[1] if len(s) > 1 else {}
    return [
        ("Vendas", dinheiro(s0.get("vendas")), dinheiro(s1.get("vendas")),
         variacao(s0.get("vendas"), s1.get("vendas"), "dinheiro", "sobe")),
        ("Investimento", dinheiro(investimento(s0)), dinheiro(investimento(s1)),
         variacao(investimento(s0), investimento(s1), "dinheiro", "neutro")),
        ("% s/ vendas", formatar(s0.get("pct"), "percentual"),
         formatar(s1.get("pct"), "percentual"),
         variacao(s0.get("pct"), s1.get("pct"), "percentual", "desce")),
    ]


def _conta_md(lin: Mapping) -> str:
    nome = _md(lin["conta"])
    if lin.get("usuario"):
        nome += f" ({_md(lin['usuario'])})"
    if lin.get("status") not in ("ok", None):
        nome += f" ⚠️ {_md(_status(lin.get('status')))}"
    return nome


def markdown(rel: Mapping) -> str:
    rotulos = _rotulos(rel)
    out: list[str] = [f"# {titulo(rel)}", ""]
    out.append(
        f"{comparado_com(rel)} · {tipo_texto(rel)} · gerado em {_quando(rel.get('gerado_em'))}"
    )
    out += ["", "## Resumo", ""]
    out.append("| Grupo | Vendas | Investimento | % s/ vendas |")
    out.append("|---|---:|---:|---:|")
    for _, rot, total in _grupos_e_geral(rel):
        cels = [
            f"{v} (ant. {a} {var['texto']})" for _, v, a, var in _cartao(total)
        ]
        out.append(f"| {rot} ({_contas_txt(total['contas'])}) | " + " | ".join(cels) + " |")

    for g in rel["grupos"]:
        out += ["", f"## {g['rotulo']}"]
        if _NOTA_GRUPO.get(g["chave"]):
            out.append(f"_{_NOTA_GRUPO[g['chave']]}_")
        out.append("")
        if not g["linhas"]:
            out.append("Nenhuma conta neste grupo nestas 4 semanas.")
            continue
        out.append("| Conta | " + " | ".join(m["rotulo"] for m in METRICAS) + " |")
        out.append("|---|" + "---:|" * len(METRICAS))
        for lin in g["linhas"]:
            out.append(
                f"| {_conta_md(lin)} | "
                + " | ".join(_celula_md(lin["semanas"], m) for m in METRICAS)
                + " |"
            )
        out.append(
            f"| **Total {g['rotulo']} ({_contas_txt(g['total']['contas'])})** | "
            + " | ".join(f"**{_celula_md(g['total']['semanas'], m)}**" for m in METRICAS)
            + " |"
        )

    out += ["", "## Últimas 4 semanas"]
    cab = "| Métrica | " + " | ".join(rotulos) + " | vs anterior | vs média 3 sem. |"
    for _, rot, total in _grupos_e_geral(rel):
        out += ["", f"### {rot}", "", cab, "|---|" + "---:|" * (len(rotulos) + 2)]
        for m in METRICAS:
            v_ant, v_media = variacoes(total["semanas"], m["chave"])
            valores = [formatar(_valor(total["semanas"], i, m["chave"]), m["tipo"])
                       for i in range(len(rotulos))]
            out.append(
                f"| {m['rotulo']} | " + " | ".join(valores)
                + f" | {v_ant['texto']} | {v_media['texto']} |"
            )
    out += ["", "### Por conta", ""]
    out.append(
        "| Grupo | Conta | " + " | ".join(f"Vendas {r}" for r in rotulos)
        + " | vs anterior | vs média 3 sem. | " + " | ".join(f"% {r}" for r in rotulos) + " |"
    )
    out.append("|---|---|" + "---:|" * (2 * len(rotulos) + 2))
    for g in rel["grupos"]:
        for lin in g["linhas"]:
            v_ant, v_media = variacoes(lin["semanas"], "vendas")
            vendas = [dinheiro(_valor(lin["semanas"], i, "vendas")) for i in range(len(rotulos))]
            pcts = [formatar(_valor(lin["semanas"], i, "pct"), "percentual")
                    for i in range(len(rotulos))]
            out.append(
                f"| {g['rotulo']} | {_md(lin['conta'])} | " + " | ".join(vendas)
                + f" | {v_ant['texto']} | {v_media['texto']} | " + " | ".join(pcts) + " |"
            )

    out += ["", "## Notas", ""]
    out += [f"- {_md(n)}" for n in rel.get("notas") or []]
    out += [f"- {t}" for t in _avisos_finais(rel)]
    return "\n".join(out) + "\n"


def _avisos_finais(rel: Mapping) -> list[str]:
    """Contas sem dados, afiliados incompletos, divergências e Ads sem item."""
    saida = []
    if rel.get("contas_sem_dados"):
        saida.append(
            "Sem dados: "
            + ", ".join(
                f"{c['conta']} ({_status(c.get('status'))}"
                + (f": {c['erro']}" if c.get("erro") else "")
                + ")"
                for c in rel["contas_sem_dados"]
            )
        )
    if rel.get("afiliados_incompletos"):
        saida.append(
            "Afiliados incompletos: "
            + ", ".join(f"{c['conta']} (até {_ddmm(c['ate'])})"
                        for c in rel["afiliados_incompletos"])
        )
    if rel.get("divergencias"):
        saida.append(
            "Eletro: o DaVinci e a categoria da Shopee discordam em "
            + ", ".join(
                f"{d['conta']} · {d.get('nome') or d['item_id']} (DaVinci: {d['davinci']}, "
                f"Shopee: {d.get('categoria_shopee')})"
                for d in rel["divergencias"]
            )
        )
    if rel.get("nao_atribuido_ads"):
        saida.append(
            "Gasto de Ads sem produto (ficou em Celular): "
            + ", ".join(f"{d['conta']} {dinheiro(d['gasto'])}" for d in rel["nao_atribuido_ads"])
        )
    return saida


# ───────────────────────────────────────────────────────────── HTML

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px 48px; background: #ffffff; color: #1f2937;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  font-size: 14px; line-height: 1.4; }
main { max-width: 1280px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 32px 0 4px; }
h3 { font-size: 15px; margin: 20px 0 6px; }
.sub, .nota-grupo { color: #6b7280; margin: 0 0 12px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 12px; margin: 20px 0 8px; }
.card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px 14px; background: #f9fafb; }
.card h3 { margin: 0 0 8px; }
.card .lin { margin-bottom: 8px; }
.card .rot { color: #6b7280; font-size: 12px; }
.card .val { font-size: 18px; font-weight: 600; }
.ant { display: block; font-size: 11px; color: #6b7280; white-space: nowrap; }
.rolar { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 4px 0 8px; }
th { background: #1F3864; color: #ffffff; font-weight: 600; padding: 6px 8px; text-align: center;
  font-size: 12px; border: 1px solid #BFBFBF; }
td { border: 1px solid #e5e7eb; padding: 6px 8px; text-align: right; vertical-align: top;
  white-space: nowrap; }
td.conta, td.txt { text-align: left; }
td.conta small { display: block; color: #6b7280; font-size: 11px; }
tr.total td { font-weight: 700; background: #D9E1F2; }
.verde { color: #1a7f37; } .vermelho { color: #c62828; } .cinza { color: #6b7280; }
.aviso { color: #b45309; font-size: 11px; display: block; white-space: normal; }
ul.notas { padding-left: 18px; color: #374151; } ul.notas li { margin-bottom: 4px; }
"""


def _var_html(var: Mapping) -> str:
    return f'<span class="{escape(var.get("cor") or "cinza")}">{escape(var["texto"])}</span>'


def _celula_html(semanas: Sequence[Mapping], m: Mapping) -> str:
    atual = _valor(semanas, 0, m["chave"])
    anterior = _valor(semanas, 1, m["chave"])
    if atual is None and anterior is None:
        return "<td>—</td>"
    if anterior is None:
        ant = "ant. —"
    else:
        var = variacao(atual, anterior, m["tipo"], m["bom"], m["chave"])
        ant = f"ant. {escape(formatar(anterior, m['tipo']))} {_var_html(var)}"
    return f'<td>{escape(formatar(atual, m["tipo"]))}<span class="ant">{ant}</span></td>'


def _conta_html(lin: Mapping) -> str:
    partes = [escape(lin["conta"])]
    if lin.get("usuario"):
        partes.append(f"<small>{escape(lin['usuario'])}</small>")
    if lin.get("status") not in ("ok", None):
        detalhe = _status(lin.get("status"))
        if lin.get("erro"):
            detalhe += f": {lin['erro']}"
        partes.append(f'<span class="aviso">⚠️ {escape(detalhe)}</span>')
    for a in lin.get("avisos") or []:
        partes.append(f'<span class="aviso">⚠️ {escape(a)}</span>')
    return f'<td class="conta">{"".join(partes)}</td>'


def html(rel: Mapping) -> str:
    """Página única (CSS embutido, tema claro) com o mesmo desenho da aba."""
    rotulos = _rotulos(rel)
    p: list[str] = [
        "<!doctype html>",
        '<html lang="pt-BR"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(titulo(rel))}</title>",
        f"<style>{_CSS}</style></head><body><main>",
        f"<h1>{escape(titulo(rel))}</h1>",
        f'<p class="sub">{escape(comparado_com(rel))} · {escape(tipo_texto(rel))} · '
        f"gerado em {escape(_quando(rel.get('gerado_em')))}</p>",
    ]

    # 2. Cartões
    p.append('<section class="cards">')
    for _, rot, total in _grupos_e_geral(rel):
        p.append(f'<div class="card"><h3>{escape(rot)} '
                 f'<span class="cinza">({escape(_contas_txt(total["contas"]))})</span></h3>')
        for rotulo_lin, valor, anterior, var in _cartao(total):
            p.append(
                f'<div class="lin"><div class="rot">{escape(rotulo_lin)}</div>'
                f'<div class="val">{escape(valor)}</div>'
                f'<span class="ant">ant. {escape(anterior)} {_var_html(var)}</span></div>'
            )
        p.append("</div>")
    p.append("</section>")

    # 3. Uma tabela por grupo
    cab = "<tr><th>Conta</th>" + "".join(f"<th>{escape(m['rotulo'])}</th>" for m in METRICAS)
    cab += "</tr>"
    for g in rel["grupos"]:
        p.append(f"<h2>{escape(g['rotulo'])}</h2>")
        if _NOTA_GRUPO.get(g["chave"]):
            p.append(f'<p class="nota-grupo">{escape(_NOTA_GRUPO[g["chave"]])}</p>')
        if not g["linhas"]:
            p.append('<p class="sub">Nenhuma conta neste grupo nestas 4 semanas.</p>')
            continue
        p.append(f'<div class="rolar"><table><thead>{cab}</thead><tbody>')
        for lin in g["linhas"]:
            p.append(
                "<tr>" + _conta_html(lin)
                + "".join(_celula_html(lin["semanas"], m) for m in METRICAS) + "</tr>"
            )
        total = g["total"]
        p.append(
            f'<tr class="total"><td class="conta">Total {escape(g["rotulo"])} '
            f"({escape(_contas_txt(total['contas']))})</td>"
            + "".join(_celula_html(total["semanas"], m) for m in METRICAS) + "</tr>"
        )
        p.append("</tbody></table></div>")

    # 4. Últimas 4 semanas
    p.append("<h2>Últimas 4 semanas</h2>")
    cab4 = (
        "<tr><th>Métrica</th>" + "".join(f"<th>{escape(r)}</th>" for r in rotulos)
        + "<th>vs anterior</th><th>vs média 3 sem.</th></tr>"
    )
    for _, rot, total in _grupos_e_geral(rel):
        p.append(f"<h3>{escape(rot)}</h3>")
        p.append(f'<div class="rolar"><table><thead>{cab4}</thead><tbody>')
        for m in METRICAS:
            v_ant, v_media = variacoes(total["semanas"], m["chave"])
            p.append(
                f'<tr><td class="txt">{escape(m["rotulo"])}</td>'
                + "".join(
                    f"<td>{escape(formatar(_valor(total['semanas'], i, m['chave']), m['tipo']))}"
                    "</td>"
                    for i in range(len(rotulos))
                )
                + f"<td>{_var_html(v_ant)}</td><td>{_var_html(v_media)}</td></tr>"
            )
        p.append("</tbody></table></div>")

    p.append("<h3>Por conta</h3>")
    cab_conta = (
        "<tr><th>Conta</th>" + "".join(f"<th>Vendas {escape(r)}</th>" for r in rotulos)
        + "<th>vs anterior</th><th>vs média 3 sem.</th>"
        + "".join(f"<th>% {escape(r)}</th>" for r in rotulos) + "</tr>"
    )
    for g in rel["grupos"]:
        if not g["linhas"]:
            continue
        p.append(f"<h3>{escape(g['rotulo'])}</h3>")
        p.append(f'<div class="rolar"><table><thead>{cab_conta}</thead><tbody>')
        for lin in g["linhas"]:
            v_ant, v_media = variacoes(lin["semanas"], "vendas")
            p.append(
                f'<tr><td class="conta">{escape(lin["conta"])}</td>'
                + "".join(
                    f"<td>{escape(dinheiro(_valor(lin['semanas'], i, 'vendas')))}</td>"
                    for i in range(len(rotulos))
                )
                + f"<td>{_var_html(v_ant)}</td><td>{_var_html(v_media)}</td>"
                + "".join(
                    f"<td>{escape(formatar(_valor(lin['semanas'], i, 'pct'), 'percentual'))}</td>"
                    for i in range(len(rotulos))
                )
                + "</tr>"
            )
        p.append("</tbody></table></div>")

    # 5. Notas
    p.append('<h2>Notas</h2><ul class="notas">')
    p += [f"<li>{escape(n)}</li>" for n in rel.get("notas") or []]
    p += [f"<li>⚠️ {escape(t)}</li>" for t in _avisos_finais(rel)]
    p.append("</ul></main></body></html>")
    return "\n".join(p)
