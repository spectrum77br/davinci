"""Os arquivos do relatório da Conferência Shopee: Excel, CSV, Markdown, JSON e HTML.

Tudo sai do relatório congelado (calculo.montar_relatorio, versao 1) — nenhum
arquivo recalcula número; só formata. As variações ("▲ 12,3%", "novo", "=",
"—", "p.p.") vêm de `calculo.variacao`, a mesma regra da tela.

O Resumo (Excel, HTML e a tela) segue o desenho da planilha antiga do dono —
pedido de 07/10/2026, "mais ou menos desse jeito":

                 |  07/09 a 13/09  | … |  28/09 a 04/10  | Variação (28/09–04/10 × 21/09–27/09)
   Métrica       | Mala|Cel|Ele|Geral | … | Mala|Cel|Ele|Geral | Mala|Cel|Ele|Geral
   Vendas     afiliados | …
              Ads       | …
   Impressões afiliados | sempre "—" (a Shopee não tem impressões de afiliados)
   …
   Resumo     % investimento / vendas · Vendas no período

Semanas da mais VELHA para a mais nova (S4 → S1) e, no fim, a variação S1 × S2
de cada grupo. Quem monta as linhas e colunas é `planilha()`, o mesmo desenho
do `planilhaResumo()` de apps/web/lib/conferencia.ts — mudou lá, muda aqui.

  • excel — só a aba "Resumo": a planilha acima (cabeçalho azul-escuro com
    letra branca, rótulos e colunas Geral em bege, grade cinza fina; % gravado
    como FRAÇÃO com formato 0.00%), os avisos curtos e, ABAIXO da tabela, os
    dois gráficos de linha e os dados deles.
  • csv — `;` e vírgula decimal (abre direto no Excel em português), UTF-8 com
    BOM, uma linha por grupo × conta × semana, todas as métricas (o detalhe
    por loja, inclusive o Saldo Ads, mora aqui e no JSON).
  • markdown — o mesmo Resumo, compacto: uma tabela por grupo (linhas da
    planilha × 4 semanas + Variação), vendas por conta, avisos e notas.
  • json_bytes — o relatório como está guardado.
  • html — página única (CSS embutido, tema claro) com a mesma planilha.
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
from openpyxl.utils import get_column_letter

from app.services.conferencia_shopee.calculo import (
    CASAS_PLANILHA,
    METRICA,
    METRICAS,
    dinheiro,
    formatar_planilha,
    variacao,
)
from app.services.conferencia_shopee.periodos import FUSO, dia_da_semana

ROTULO_STATUS = {
    "pendente": "na fila",
    "coletando": "coletando",
    "ok": "ok",
    "parcial": "parcial",
    "deslogada": "deslogada",
    "perfil_em_uso": "perfil em uso",
    "sem_automacao": "perfil Firefox, o robô não abre",
    "bloqueada": "bloqueada pela Shopee",
    "interrompida": "interrompida",
    "erro": "erro",
    "expirada": "não coletada a tempo",
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


def _subtitulo(rel: Mapping, *, com_tipo: bool = True) -> str:
    """"comparado com 21/09 a 27/09 · semana fechada · gerado em …" (no
    Excel o tipo já vai no título)."""
    partes = [comparado_com(rel), tipo_texto(rel) if com_tipo else ""]
    if rel.get("gerado_em"):
        partes.append(f"gerado em {_quando(rel.get('gerado_em'))}")
    return " · ".join(p for p in partes if p)


def _status(s: str | None) -> str:
    return ROTULO_STATUS.get(s or "", (s or "—").replace("_", " "))


def _rotulo_semana(s: Mapping) -> str:
    """"28/09–04/10" (o rótulo guardado no relatório)."""
    return s.get("rotulo") or f"{_ddmm(s['inicio'])}–{_ddmm(s['fim'])}"


def _contas_txt(n: int) -> str:
    return f"{n} {'conta' if n == 1 else 'contas'}"


def _valor(semanas: Sequence[Mapping] | None, i: int, chave: str) -> float | None:
    """Valor de uma métrica numa semana; semana ou chave que falta (relatório
    antigo, sem cliques/pedidos/conversão) → vazio, nunca 0."""
    s = list(semanas or [])
    v = (s[i] if i < len(s) and isinstance(s[i], Mapping) else {}).get(chave)
    return v if isinstance(v, int | float) and not isinstance(v, bool) else None


def _nome_arquivo_base(rel: Mapping) -> str:
    s = (rel.get("semanas") or [None])[0]
    return f"conferencia-shopee-{s['inicio']}_{s['fim']}" if s else "conferencia-shopee"


def nome_arquivo(rel: Mapping, ext: str) -> str:
    """conferencia-shopee-<S1.inicio>_<S1.fim>.<ext>."""
    return f"{_nome_arquivo_base(rel)}.{ext}"


# ───────────────────────────────────────────────────────────── a planilha

# As 4 colunas de cada semana (e da Variação), nesta ordem.
GRUPOS_PLANILHA: tuple[tuple[str, str], ...] = (
    ("mala", "Mala"),
    ("celular", "Celular"),
    ("eletro", "Eletro"),
    ("geral", "Geral"),
)
# (categoria — mesclada nas linhas seguidas dela —, sub-rótulo, métrica). Métrica
# None = não existe na Shopee (impressões de afiliados): sempre "—". O Saldo Ads
# não entra (continua no relatório, no CSV e no JSON).
LINHAS_PLANILHA: tuple[tuple[str, str, str | None], ...] = (
    ("Vendas", "afiliados", "vendas_afiliados"),
    ("Vendas", "Ads", "vendas_ads"),
    ("Impressões", "afiliados", None),
    ("Impressões", "Ads", "impressoes"),
    ("Conversão", "afiliados", "conversao_afiliados"),
    ("Conversão", "Ads", "conversao_ads"),
    ("Investimento", "afiliados", "invest_afiliados"),
    ("Investimento", "Ads", "invest_ads"),
    ("Resumo", "% investimento / vendas", "pct"),
    ("Resumo", "Vendas no período", "vendas"),
)
_SEM_VARIACAO = {"texto": "—", "direcao": None, "cor": "cinza"}


def semana_planilha(s: Mapping) -> str:
    """Cabeçalho da semana: "07/09 a 13/09"."""
    return f"{_ddmm(s['inicio'])} a {_ddmm(s['fim'])}"


def rotulo_variacao(rel: Mapping) -> str:
    """"Variação (28/09–04/10 × 21/09–27/09)"; sem a S2, só "Variação"."""
    sem = rel.get("semanas") or []
    if len(sem) < 2:
        return "Variação"
    return f"Variação ({_rotulo_semana(sem[0])} × {_rotulo_semana(sem[1])})"


def _totais_planilha(rel: Mapping) -> list[Mapping | None]:
    """O total de Mala, Celular e Eletro e o Geral, na ordem das colunas."""
    grupos = {g.get("chave"): g.get("total") for g in rel.get("grupos") or []
              if isinstance(g, Mapping)}
    return [rel.get("geral") if chave == "geral" else grupos.get(chave)
            for chave, _ in GRUPOS_PLANILHA]


def planilha(rel: Mapping) -> dict[str, Any]:
    """O Resumo no desenho da planilha, com os NÚMEROS (quem escreve formata):

    {"semanas": [{"indice" (0 = S1), "rotulo": "07/09 a 13/09"}] da mais velha
       para a mais nova,
     "grupos": [{"chave", "rotulo"}] (Mala, Celular, Eletro, Geral),
     "variacao": "Variação (28/09–04/10 × 21/09–27/09)",
     "linhas": [{"categoria", "sub", "chave" (métrica | None), "tipo", "span"
       (linhas que a categoria cobre; 0 = coberta pela de cima),
       "valores": [[número | None] por grupo] por semana,
       "variacoes": [variação S1 × S2] por grupo}]}

    Grupo, semana ou métrica que o relatório não tem (relatório de antes de
    07/10/2026 não tem cliques/pedidos/conversão) → None ("—"), nunca 0."""
    periodos = [s for s in (rel.get("semanas") or []) if isinstance(s, Mapping)][:4]
    semanas = [{"indice": i, "rotulo": semana_planilha(s)} for i, s in enumerate(periodos)]
    semanas.reverse()
    totais = [(t or {}).get("semanas") if isinstance(t, Mapping) else None
              for t in _totais_planilha(rel)]
    linhas = []
    for k, (categoria, sub, chave) in enumerate(LINHAS_PLANILHA):
        span = 0
        if k == 0 or LINHAS_PLANILHA[k - 1][0] != categoria:
            span = 1
            while k + span < len(LINHAS_PLANILHA) and LINHAS_PLANILHA[k + span][0] == categoria:
                span += 1
        m = METRICA.get(chave) if chave else None
        if m is None:
            valores = [[None] * len(GRUPOS_PLANILHA) for _ in semanas]
            variacoes = [dict(_SEM_VARIACAO) for _ in GRUPOS_PLANILHA]
        else:
            valores = [[_valor(t, s["indice"], m["chave"]) for t in totais] for s in semanas]
            # p.p. com 2 casas, como o % da planilha ("▼ 0,01 p.p."): com 1 casa
            # uma conversão de 0,44% → 0,43% sairia "▼ 0,0 p.p." em vermelho.
            variacoes = [
                variacao(_valor(t, 0, m["chave"]), _valor(t, 1, m["chave"]), m["tipo"],
                         m["bom"], m["chave"], casas=CASAS_PLANILHA)
                for t in totais
            ]
        linhas.append(
            {
                "categoria": categoria,
                "sub": sub,
                "chave": chave,
                "tipo": m["tipo"] if m else None,
                "span": span,
                "valores": valores,
                "variacoes": variacoes,
            }
        )
    return {
        "semanas": semanas,
        "grupos": [{"chave": c, "rotulo": r} for c, r in GRUPOS_PLANILHA],
        "variacao": rotulo_variacao(rel),
        "linhas": linhas,
    }


def _texto_planilha(v: float | None, tipo: str | None) -> str:
    return "—" if v is None or tipo is None else formatar_planilha(v, tipo)


def _avisos_curtos(rel: Mapping) -> list[str]:
    """Só o que muda a leitura do resumo: contas sem dados e afiliados incompletos."""
    saida = []
    if rel.get("contas_sem_dados"):
        saida.append(
            "Sem dados: "
            + ", ".join(
                f"{c['conta']} ({_status(c.get('status'))})" for c in rel["contas_sem_dados"]
            )
        )
    if rel.get("afiliados_incompletos"):
        saida.append(
            "Afiliados incompletos: "
            + ", ".join(
                f"{c['conta']} (até {_ddmm(c['ate'])})" for c in rel["afiliados_incompletos"]
            )
        )
    return saida


# ───────────────────────────────────────────────────────────── Excel

_FONTE = "Arial"
_TITULO = Font(name=_FONTE, bold=True, size=13)
_CAB_FILL = PatternFill("solid", fgColor="1F3864")
_CAB_FONT = Font(name=_FONTE, bold=True, color="FFFFFF", size=10)
# Bege da planilha do dono: as duas colunas de rótulo e toda coluna "Geral".
_BEGE = PatternFill("solid", fgColor="DDD9C4")
_NORMAL = Font(name=_FONTE, size=10)
_NEGRITO = Font(name=_FONTE, size=10, bold=True)
_AVISO = Font(name=_FONTE, size=10, color="B45309")
_fino = Side(style="thin", color="BFBFBF")
_BORDA = Border(left=_fino, right=_fino, top=_fino, bottom=_fino)
_COR_XLSX = {"verde": "008000", "vermelho": "C00000", "cinza": "808080"}
_FORMATO = {
    "dinheiro": '"R$" #,##0.00',
    "inteiro": "#,##0",
    # % gravado como FRAÇÃO (0,075) com o formato padrão de porcentagem, 2
    # casas. Com o número 7,5 e um "%" literal o Excel mostrava 7,5%, mas o
    # Numbers e a pré-visualização do Mac mostravam 750% (print de 07/10/2026).
    "percentual": "0.00%",
}
_CENTRO = Alignment(horizontal="center", vertical="center")
_CENTRO_QUEBRA = Alignment(horizontal="center", vertical="center", wrap_text=True)
# Onde a tabela começa (linha do 1º cabeçalho) e as colunas de rótulo.
_LIN_CAB = 4
_COLS_ROTULO = 2
# Altura do gráfico em linhas da planilha (7,5 cm ≈ 15 linhas de 15 pt) + folga.
_LINHAS_GRAFICO = 17


def _cab(ws, linha: int, col: int, valor: Any = None) -> None:
    c = ws.cell(linha, col, valor)
    c.font, c.fill, c.border, c.alignment = _CAB_FONT, _CAB_FILL, _BORDA, _CENTRO_QUEBRA


def _mesclar_cab(ws, linha: int, col_ini: int, col_fim: int, valor: Any) -> None:
    """Cabeçalho mesclado. Mescla ANTES e pinta todas as células do trecho
    depois (o merge_cells troca as de dentro por células novas, sem estilo:
    a borda e o azul sumiriam em alguns leitores)."""
    if col_fim > col_ini:
        ws.merge_cells(start_row=linha, start_column=col_ini, end_row=linha, end_column=col_fim)
    ws.cell(linha, col_ini, valor)
    for col in range(col_ini, col_fim + 1):
        c = ws.cell(linha, col)
        c.font, c.fill, c.border, c.alignment = _CAB_FONT, _CAB_FILL, _BORDA, _CENTRO_QUEBRA


def _texto_xlsx(ws, linha: int, col: int, valor: Any):
    c = ws.cell(linha, col, valor)
    if isinstance(valor, str) and valor.startswith("="):
        # O openpyxl trata todo texto que começa com "=" como fórmula: a
        # variação "=" (sem mudança) viraria uma fórmula vazia e o Excel
        # pediria para "reparar" o arquivo.
        c.data_type = "s"
    return c


def _celula_valor(ws, linha: int, col: int, valor: float | None, tipo: str | None, bege: bool):
    """Número com o formato da métrica (% como fração); vazio → "—"."""
    if valor is None or tipo is None:
        c = _texto_xlsx(ws, linha, col, "—")
        c.alignment = _CENTRO
    else:
        c = ws.cell(linha, col, valor / 100 if tipo == "percentual" else valor)
        c.number_format = _FORMATO[tipo]
    c.font, c.border = _NORMAL, _BORDA
    if bege:
        c.fill = _BEGE
    return c


def _celula_variacao(ws, linha: int, col: int, var: Mapping, bege: bool):
    c = _texto_xlsx(ws, linha, col, var["texto"])
    c.font = Font(name=_FONTE, size=10, color=_COR_XLSX.get(var.get("cor") or "", "808080"))
    c.alignment, c.border = _CENTRO, _BORDA
    if bege:
        c.fill = _BEGE
    return c


def _tabela_planilha(ws, p: Mapping) -> int:
    """Escreve a planilha a partir de `_LIN_CAB`; devolve a última linha."""
    n = len(p["grupos"])
    cab1, cab2 = _LIN_CAB, _LIN_CAB + 1
    _mesclar_cab(ws, cab1, 1, _COLS_ROTULO, None)
    _mesclar_cab(ws, cab2, 1, _COLS_ROTULO, "Métrica")
    blocos = [s["rotulo"] for s in p["semanas"]] + [p["variacao"]]
    for b, rotulo_bloco in enumerate(blocos):
        col = _COLS_ROTULO + 1 + b * n
        _mesclar_cab(ws, cab1, col, col + n - 1, rotulo_bloco)
        for j, g in enumerate(p["grupos"]):
            _cab(ws, cab2, col + j, g["rotulo"])

    primeira = cab2 + 1
    for k, lin in enumerate(p["linhas"]):
        r = primeira + k
        # Categoria mesclada na vertical (mescla antes de pintar, como no
        # cabeçalho); a célula de baixo, coberta, também leva o bege e a borda.
        if lin["span"] > 1:
            ws.merge_cells(start_row=r, start_column=1, end_row=r + lin["span"] - 1, end_column=1)
        if lin["span"]:
            ws.cell(r, 1, lin["categoria"])
        cat = ws.cell(r, 1)
        cat.font, cat.fill, cat.border = _NEGRITO, _BEGE, _BORDA
        cat.alignment = Alignment(horizontal="left", vertical="center")
        sub = ws.cell(r, 2, lin["sub"])
        sub.font, sub.fill, sub.border = _NORMAL, _BEGE, _BORDA
        for b, valores in enumerate(lin["valores"]):
            for j, v in enumerate(valores):
                _celula_valor(ws, r, _COLS_ROTULO + 1 + b * n + j, v, lin["tipo"],
                              bege=j == n - 1)
        col_var = _COLS_ROTULO + 1 + len(lin["valores"]) * n
        for j, var in enumerate(lin["variacoes"]):
            _celula_variacao(ws, r, col_var + j, var, bege=j == n - 1)
    return primeira + len(p["linhas"]) - 1


def _dados_graficos(ws, rel: Mapping, linha: int) -> dict[str, tuple[int, int]]:
    """Os números dos 2 gráficos (semana mais antiga → mais recente);
    devolve {métrica: (linha do cabeçalho, última linha)}."""
    totais = _totais_planilha(rel)
    semanas = [s for s in (rel.get("semanas") or []) if isinstance(s, Mapping)][:4]
    ws.cell(linha, 1, "Dados dos gráficos (da semana mais antiga para a mais recente)").font = (
        _NEGRITO
    )
    linha += 1
    blocos = {}
    for chave, titulo_bloco in (("vendas", "Vendas"), ("pct", "% investimento / vendas")):
        _cab(ws, linha, 1, titulo_bloco)
        for j, (_, rot) in enumerate(GRUPOS_PLANILHA, 2):
            _cab(ws, linha, j, rot)
        inicio = linha
        for i in reversed(range(len(semanas))):
            linha += 1
            c = ws.cell(linha, 1, _rotulo_semana(semanas[i]))
            c.font, c.border = _NORMAL, _BORDA
            tipo = METRICA[chave]["tipo"]
            for j, total in enumerate(totais, 2):
                v = _valor((total or {}).get("semanas"), i, chave)
                # Vazio fica vazio (sem "—"): texto no meio quebraria a linha.
                c = ws.cell(linha, j, None if v is None else
                            (v / 100 if tipo == "percentual" else v))
                c.font, c.border = _NORMAL, _BORDA
                c.number_format = _FORMATO[tipo]
        blocos[chave] = (inicio, linha)
        linha += 2
    return blocos


def _aba_resumo(wb: Workbook, rel: Mapping) -> None:
    ws = wb.active
    ws.title = "Resumo"
    p = planilha(rel)
    ws.cell(1, 1, f"{titulo(rel)} ({tipo_texto(rel)})").font = _TITULO
    ws.cell(2, 1, _subtitulo(rel, com_tipo=False)).font = _NORMAL
    ultima = _tabela_planilha(ws, p)

    # Só os avisos curtos, logo abaixo da tabela.
    linha = ultima + 2
    for aviso in _avisos_curtos(rel):
        ws.cell(linha, 1, f"⚠️ {aviso}").font = _AVISO
        linha += 1

    # Os 2 gráficos ABAIXO da tabela (lado a lado, sem cobrir nada) e, embaixo
    # deles, os números que eles usam.
    linha_graficos = linha + 1
    blocos = _dados_graficos(ws, rel, linha_graficos + _LINHAS_GRAFICO)
    for chave, nome_grafico, formato, ancora in (
        ("vendas", "Vendas por grupo", '"R$" #,##0', "A"),
        ("pct", "% investimento / vendas por grupo", "0.0%", "G"),
    ):
        inicio, fim = blocos[chave]
        if fim <= inicio:
            continue  # relatório sem semana: nada para desenhar
        grafico = LineChart()
        grafico.title = nome_grafico
        grafico.height, grafico.width = 7.5, 16
        grafico.y_axis.number_format = formato
        grafico.y_axis.majorGridlines = None
        grafico.x_axis.delete = False
        grafico.y_axis.delete = False
        dados = Reference(ws, min_col=2, max_col=1 + len(GRUPOS_PLANILHA), min_row=inicio,
                          max_row=fim)
        grafico.add_data(dados, titles_from_data=True)
        grafico.set_categories(Reference(ws, min_col=1, min_row=inicio + 1, max_row=fim))
        ws.add_chart(grafico, f"{ancora}{linha_graficos}")

    n_dados = (len(p["semanas"]) + 1) * len(GRUPOS_PLANILHA)
    for j, largura in enumerate([14, 24, *[15] * n_dados], 1):
        ws.column_dimensions[get_column_letter(j)].width = largura
    # As 2 colunas de rótulo e os 2 cabeçalhos ficam parados ao rolar.
    ws.freeze_panes = ws.cell(_LIN_CAB + 2, _COLS_ROTULO + 1)
    # Impressão: paisagem, a largura toda numa página.
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def excel(rel: Mapping) -> BytesIO:
    """Só a aba Resumo, no desenho da planilha do dono (07/10/2026), com os 2
    gráficos embaixo. O detalhe por loja segue no sistema (JSON/CSV e o
    próprio relatório guardado)."""
    wb = Workbook()
    _aba_resumo(wb, rel)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ───────────────────────────────────────────────────────────── CSV


def _num_csv(v: Any, tipo: str) -> str:
    if v is None or isinstance(v, bool) or not isinstance(v, int | float):
        return ""
    if tipo == "inteiro":
        return str(int(round(v)))
    return f"{v:.2f}".replace(".", ",")


def csv(rel: Mapping) -> bytes:
    """`;`, vírgula decimal, UTF-8 com BOM: uma linha por grupo × conta ×
    semana, todas as métricas (relatório antigo sai com as colunas novas
    vazias)."""
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


def _qtd_grupo(chave: str, total: Mapping | None) -> str:
    """"(3 contas · 1 sem dados)"; no Geral, só as contas."""
    total = total or {}
    texto = _contas_txt(int(total.get("contas") or 0))
    if chave != "geral" and total.get("sem_dados"):
        texto += f" · {total['sem_dados']} sem dados"
    return f"({texto})"


def _conta_md(lin: Mapping) -> str:
    nome = _md(lin["conta"])
    if lin.get("status") not in ("ok", None):
        nome += f" ⚠️ {_md(_status(lin.get('status')))}"
    return nome


def markdown(rel: Mapping) -> str:
    """O Resumo da planilha, compacto: uma tabela por grupo (as 10 linhas da
    planilha × semanas da mais velha para a mais nova + Variação), as vendas
    por conta, os avisos e as notas."""
    p = planilha(rel)
    rotulos = [s["rotulo"] for s in p["semanas"]]
    totais = _totais_planilha(rel)
    out: list[str] = [f"# {titulo(rel)}", "", _subtitulo(rel), "", "## Resumo"]
    cab = "| Métrica | | " + " | ".join(rotulos) + f" | {p['variacao']} |"
    sep = "|---|---|" + "---:|" * (len(rotulos) + 1)
    for j, g in enumerate(p["grupos"]):
        out += ["", f"### {g['rotulo']} {_qtd_grupo(g['chave'], totais[j])}", "", cab, sep]
        for lin in p["linhas"]:
            valores = [_texto_planilha(v[j], lin["tipo"]) for v in lin["valores"]]
            out.append(
                f"| {lin['categoria'] if lin['span'] else ''} | {lin['sub']} | "
                + " | ".join(valores) + f" | {lin['variacoes'][j]['texto']} |"
            )

    out += ["", "## Vendas por conta", ""]
    out.append("| Grupo | Conta | " + " | ".join(rotulos) + " | Variação |")
    out.append("|---|---|" + "---:|" * (len(rotulos) + 1))
    m = METRICA["vendas"]
    for g in rel["grupos"]:
        for lin in g["linhas"]:
            s = lin.get("semanas") or []
            vendas = [dinheiro(_valor(s, x["indice"], "vendas")) for x in p["semanas"]]
            var = variacao(_valor(s, 0, "vendas"), _valor(s, 1, "vendas"), m["tipo"], m["bom"])
            out.append(
                f"| {_md(g['rotulo'])} | {_conta_md(lin)} | " + " | ".join(vendas)
                + f" | {var['texto']} |"
            )

    avisos = _avisos_finais(rel)
    if avisos:
        out += ["", "## Avisos", ""]
        out += [f"- {_md(t)}" for t in avisos]
    out += ["", "## Notas", ""]
    out += [f"- {_md(n)}" for n in rel.get("notas") or []]
    return "\n".join(out) + "\n"


def _avisos_finais(rel: Mapping) -> list[str]:
    """Contas sem dados, afiliados incompletos, divergências e Ads sem item."""
    saida = []
    if rel.get("contas_sem_dados"):
        saida.append(
            "Sem dados: "
            + ", ".join(
                f"{c['conta']} ({_status(c.get('status'))}"
                + (f": {c['erro']}" if c.get("erro") and c.get("status") != "sem_automacao" else "")
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

# Larguras fixas das 2 colunas de rótulo: a 2ª gruda em `left` = largura da
# 1ª. No celular encolhem para sobrar lugar para os números.
_CSS = """
:root { color-scheme: light; --larg-cat: 116px; --larg-sub: 184px; }
@media (max-width: 640px) { :root { --larg-cat: 106px; --larg-sub: 124px; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px 48px; background: #ffffff; color: #1f2937;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  font-size: 14px; line-height: 1.4; }
h1 { font-size: 22px; margin: 0 0 4px; color: #1F3864; }
.sub { color: #6b7280; margin: 0 0 16px; }
/* A tabela é larga: rola de lado e as 2 colunas de rótulo ficam paradas. */
.rolar { overflow-x: auto; margin: 0 0 8px; max-width: max-content;
  border-left: 1px solid #BFBFBF; border-top: 1px solid #BFBFBF; }
table.planilha { border-collapse: separate; border-spacing: 0; font-size: 13px; }
.planilha th, .planilha td { border-right: 1px solid #BFBFBF; border-bottom: 1px solid #BFBFBF;
  padding: 5px 8px; white-space: nowrap; }
.planilha th { background: #1F3864; color: #ffffff; font-weight: 700; text-align: center; }
.planilha td { text-align: right; background: #ffffff; }
.planilha td.vazio, .planilha td.var { text-align: center; }
.planilha td.rotulo, .planilha td.geral { background: #DDD9C4; }
.planilha .cat { text-align: left; font-weight: 700; vertical-align: middle;
  white-space: normal; }
.planilha .subr { text-align: left; white-space: normal; }
.planilha .c1 { position: sticky; left: 0; z-index: 2; width: var(--larg-cat);
  min-width: var(--larg-cat); max-width: var(--larg-cat); }
.planilha .c2 { position: sticky; left: var(--larg-cat); z-index: 2; width: var(--larg-sub);
  min-width: var(--larg-sub); max-width: var(--larg-sub); }
.planilha th.c12 { position: sticky; left: 0; z-index: 3;
  min-width: calc(var(--larg-cat) + var(--larg-sub)); }
/* A data da semana fica à vista enquanto o bloco dela estiver na tela: no
   celular a célula mesclada (4 colunas) passa da largura que sobra ao lado das
   2 colunas paradas e a data centrada caía fora da tela. */
.planilha th .semana { display: inline-block; position: sticky;
  left: calc(var(--larg-cat) + var(--larg-sub) + 8px); right: 8px; }
/* Celular: data à esquerda, grudada só pela esquerda, e o "Variação (…)"
   quebrando em linhas — mais largo que o que sobra da tela, ele escorregava
   para baixo das colunas paradas no fim da rolagem e só o fim aparecia. */
@media (max-width: 640px) { .planilha th.bloco { text-align: left; }
  .planilha th .semana { right: auto; max-width: 90px; white-space: normal; } }
.verde { color: #1a7f37; } .vermelho { color: #c62828; } .cinza { color: #6b7280; }
.avisos { margin: 16px 0 0; padding: 8px 12px; border: 1px solid #f59e0b55; background: #fffbeb;
  color: #b45309; border-radius: 6px; font-size: 13px; } .avisos p { margin: 2px 0; }
"""


def _var_html(var: Mapping) -> str:
    return f'<span class="{escape(var.get("cor") or "cinza")}">{escape(var["texto"])}</span>'


def _tabela_html(p: Mapping) -> str:
    n = len(p["grupos"])
    blocos = [s["rotulo"] for s in p["semanas"]] + [p["variacao"]]
    cab1 = '<tr><th class="rotulo c12" colspan="2"></th>' + "".join(
        f'<th class="bloco" colspan="{n}"><span class="semana">{escape(b)}</span></th>'
        for b in blocos
    ) + "</tr>"
    grupos = "".join(
        f"<th{' class=\"geral\"' if j == n - 1 else ''}>{escape(g['rotulo'])}</th>"
        for j, g in enumerate(p["grupos"])
    )
    cab2 = '<tr><th class="rotulo c12" colspan="2">Métrica</th>' + grupos * len(blocos) + "</tr>"
    corpo = []
    for lin in p["linhas"]:
        partes = ["<tr>"]
        if lin["span"]:
            rs = f' rowspan="{lin["span"]}"' if lin["span"] > 1 else ""
            partes.append(f'<td class="rotulo cat c1"{rs}>{escape(lin["categoria"])}</td>')
        partes.append(f'<td class="rotulo subr c2">{escape(lin["sub"])}</td>')
        for valores in lin["valores"]:
            for j, v in enumerate(valores):
                classes = [c for c in ("geral" if j == n - 1 else "",
                                       "vazio" if v is None or lin["tipo"] is None else "") if c]
                cl = f' class="{" ".join(classes)}"' if classes else ""
                partes.append(f"<td{cl}>{escape(_texto_planilha(v, lin['tipo']))}</td>")
        for j, var in enumerate(lin["variacoes"]):
            cl = "var geral" if j == n - 1 else "var"
            partes.append(f'<td class="{cl}">{_var_html(var)}</td>')
        partes.append("</tr>")
        corpo.append("".join(partes))
    return (
        '<div class="rolar"><table class="planilha"><thead>' + cab1 + cab2 + "</thead><tbody>"
        + "\n".join(corpo) + "</tbody></table></div>"
    )


def html(rel: Mapping) -> str:
    """Página única (CSS embutido, tema claro) com o Resumo no desenho da
    planilha — o mesmo da aba e do Excel — e só os avisos que mudam a leitura."""
    p: list[str] = [
        "<!doctype html>",
        '<html lang="pt-BR"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(titulo(rel))}</title>",
        f"<style>{_CSS}</style></head><body><main>",
        f"<h1>{escape(titulo(rel))}</h1>",
        f'<p class="sub">{escape(_subtitulo(rel))}</p>',
        _tabela_html(planilha(rel)),
    ]
    avisos = _avisos_curtos(rel)
    if avisos:
        p.append('<div class="avisos">' + "".join(f"<p>⚠️ {escape(a)}</p>" for a in avisos)
                 + "</div>")
    p.append("</main></body></html>")
    return "\n".join(p)
