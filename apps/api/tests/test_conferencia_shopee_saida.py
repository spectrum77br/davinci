"""Conferência Shopee — os arquivos do relatório (services/conferencia_shopee/saida).

Excel: abre no openpyxl com as abas do documento; "Semana" tem as duas células
amarelas com a lista das semanas e fórmulas SUMIFS/COUNTIFS sobre "Dados"
(nomes em inglês, recalcula ao abrir); as abas de métrica são VALORES. CSV com
`;` e vírgula decimal. HTML e MD com os títulos dos grupos. Mesmo cenário
sintético dos testes do cálculo.
"""

from __future__ import annotations

import io
import json
import zipfile

from openpyxl import load_workbook

from app.services.conferencia_shopee import saida
from tests.test_conferencia_shopee_calculo import relatorio_exemplo

ABAS = [
    "Resumo",
    "Semana",
    "Vendas afiliados",
    "Vendas Ads",
    "Saldo Ads",
    "Impressões",
    "Invest. afiliados",
    "Invest. Ads",
    "% sobre vendas",
    "Vendas",
    "Dados",
]


def _excel():
    rel = relatorio_exemplo()
    buf = saida.excel(rel)
    return rel, buf.getvalue(), load_workbook(io.BytesIO(buf.getvalue()))


def test_excel_abre_com_as_abas_do_documento():
    _, bruto, wb = _excel()
    assert bruto[:2] == b"PK"
    assert wb.sheetnames == ABAS
    assert wb.calculation.fullCalcOnLoad is True
    # Dois gráficos de linha na aba Resumo.
    with zipfile.ZipFile(io.BytesIO(bruto)) as z:
        graficos = [n for n in z.namelist() if n.startswith("xl/charts/chart")]
        assert len(graficos) == 2
        xml = "".join(z.read(n).decode() for n in graficos)
    assert xml.count("<lineChart>") == 2
    assert "Vendas por grupo" in xml and "% s/ vendas por grupo" in xml


def test_excel_resumo_tem_grupo_metrica_4_semanas_e_variacoes():
    rel, _, wb = _excel()
    ws = wb["Resumo"]
    assert ws["A1"].value.startswith("Conferência Shopee — 28/09 a 04/10/2026")
    cab = [c.value for c in ws[4]][:7]
    assert cab == ["Mala (1 conta)", "28/09–04/10", "21/09–27/09", "14/09–20/09",
                   "07/09–13/09", "vs anterior", "vs média 3 sem."]
    vendas_af = [c.value for c in ws[5]][:7]
    assert vendas_af[0] == "Vendas afiliados"
    assert vendas_af[1] == rel["grupos"][0]["total"]["semanas"][0]["vendas_afiliados"]
    assert vendas_af[5] == "=" and vendas_af[6] == "="
    titulos = [ws.cell(r, 1).value for r in range(1, ws.max_row + 1)]
    for grupo in ("Celular (3 contas)", "Eletro (1 conta)", "Geral (4 contas)"):
        assert grupo in titulos


def test_excel_semana_tem_entradas_amarelas_e_formulas_sobre_dados():
    rel, _, wb = _excel()
    ws = wb["Semana"]
    assert ws["B1"].value == "28/09–04/10"
    assert ws["B2"].value == "21/09–27/09"
    for celula in ("B1", "B2"):
        assert ws[celula].fill.fgColor.rgb.endswith("FFFF00")
    validacoes = ws.data_validations.dataValidation
    assert len(validacoes) == 1
    v = validacoes[0]
    assert v.type == "list"
    assert {str(r) for r in v.sqref.ranges} == {"B1", "B2"}
    assert v.formula1 == '"28/09–04/10,21/09–27/09,14/09–20/09,07/09–13/09"'

    # Linha 6 = primeira conta (Inova, Mala): semana, comparação e variação.
    assert ws["A6"].value == "Mala" and ws["B6"].value == "Inova"
    f_sem, f_cmp, f_var = ws["C6"].value, ws["D6"].value, ws["E6"].value
    assert f_sem.startswith("=IF(COUNTIFS(Dados!$B$2:$B$")
    assert "SUMIFS(Dados!$J$2:$J$" in f_sem and ",$B$1," in f_sem and ",$B$2," not in f_sem
    assert '"Mala"' in f_sem and '"id-inova"' in f_sem
    assert ",$B$2," in f_cmp and ",$B$1," not in f_cmp
    assert f_var.startswith("=IF(OR(ISTEXT(C6),ISTEXT(D6))")
    assert '"novo"' in f_var and "/ABS(D6)" in f_var
    assert "▲" in ws["E6"].number_format and "[Color10]" in ws["E6"].number_format
    # O % da linha sai das células da própria linha (investimentos ÷ vendas).
    cab = [c.value for c in ws[4]]
    col_pct = cab.index("% s/ vendas") + 1
    f_pct = ws.cell(6, col_pct).value
    assert "N(" in f_pct and "*100" in f_pct
    # Com 2 casas, como o relatório guarda: 6,40 × 6,40 dá "=", não um
    # "▲ 0,0 p.p." colorido por causa da 3ª casa.
    assert "ROUND((N(" in f_pct and "*100,2)" in f_pct
    # % é "desce": subir é vermelho.
    assert ws.cell(6, col_pct + 2).number_format.startswith('[Red]"▲ "0.0" p.p."')
    # Saldo nunca é "novo".
    col_saldo = cab.index("Saldo Ads") + 1
    assert '"novo"' not in ws.cell(6, col_saldo + 2).value
    # Totais por grupo (só semana + grupo) e o Geral (só a semana).
    nomes = [ws.cell(r, 2).value for r in range(6, ws.max_row + 1)]
    assert "Total Mala (1 conta)" in nomes and "Total Celular (3 contas)" in nomes
    assert nomes[-1] == "Geral (4 contas)"
    f_geral = ws.cell(ws.max_row, 3).value
    assert "Dados!$E$" not in f_geral and "Dados!$G$" not in f_geral
    # Só nomes de função em inglês (o Excel em português traduz sozinho).
    texto = " ".join(str(c.value) for row in ws.iter_rows(min_row=6) for c in row if c.value)
    for pt in ("SOMASES", "CONT.SES", "SE(", "ÉTEXTO"):
        assert pt not in texto


def test_excel_dados_uma_linha_por_semana_grupo_conta_com_chave():
    rel, _, wb = _excel()
    ws = wb["Dados"]
    cab = [c.value for c in ws[1]]
    assert cab[:9] == ["Chave", "Semana", "Início", "Fim", "Grupo", "Conta", "Conta (id)",
                       "Usuário", "Status"]
    assert cab[9:] == [m["rotulo"] for m in rel["metricas"]]
    n_linhas = sum(len(g["linhas"]) for g in rel["grupos"])
    assert ws.max_row == 1 + 4 * n_linhas
    chaves = [ws.cell(r, 1).value for r in range(2, ws.max_row + 1)]
    assert "2026-09-28|2026-10-04|eletro|id-barbosa" in chaves
    assert "2026-09-28|2026-10-04|celular|id-barbosa" in chaves
    assert len(set(chaves)) == len(chaves)
    # Vazio fica vazio (a fórmula diferencia "sem dados" de zero).
    luno = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 6).value == "Luno")
    assert all(ws.cell(luno, c).value is None for c in range(10, 18))


def test_excel_abas_de_metrica_sao_valores():
    rel, _, wb = _excel()
    ws = wb["Vendas"]
    assert [c.value for c in ws[3]][:2] == ["Grupo", "Conta"]
    linhas = {(ws.cell(r, 1).value, ws.cell(r, 2).value): r for r in range(4, ws.max_row + 1)}
    r = linhas[("Celular", "Barbosa")]
    assert ws.cell(r, 3).value == 1400.0
    assert ws.cell(linhas[("Eletro", "Barbosa")], 3).value == 600.0
    assert ws.cell(r, 3).number_format == '"R$" #,##0.00'
    assert ws.cell(linhas[("Geral", "Geral (4 contas)")], 3).value == (
        rel["geral"]["semanas"][0]["vendas"]
    )
    assert wb["% sobre vendas"].cell(3, 1).value == "Grupo"


def test_excel_so_a_aba_semana_tem_formula():
    """O openpyxl trata texto que começa com "=" como fórmula: a variação "="
    (sem mudança) não pode virar uma fórmula vazia (o Excel pede reparo)."""
    _, bruto, wb = _excel()
    iguais = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if ws.title == "Semana" and c.row >= 6 and c.column >= 3:
                    assert c.data_type == "f", c.coordinate
                    continue
                assert c.data_type != "f", f"{ws.title}!{c.coordinate}"
                iguais += c.value == "="
    assert iguais > 10
    with zipfile.ZipFile(io.BytesIO(bruto)) as z:
        for nome in z.namelist():
            if nome.startswith("xl/worksheets/sheet") and nome != "xl/worksheets/sheet2.xml":
                assert b"<f>" not in z.read(nome) and b"<f/>" not in z.read(nome), nome


def test_csv_ponto_e_virgula_e_virgula_decimal():
    rel = relatorio_exemplo()
    bruto = saida.csv(rel)
    assert bruto.startswith(b"\xef\xbb\xbf")
    linhas = bruto.decode("utf-8-sig").splitlines()
    cab = linhas[0].split(";")
    assert cab[:8] == ["Grupo", "Conta", "Usuário", "Status", "Erro", "Semana", "Início", "Fim"]
    assert cab[8:] == [m["rotulo"] for m in rel["metricas"]]
    n_linhas = sum(len(g["linhas"]) for g in rel["grupos"])
    assert len(linhas) == 1 + 4 * n_linhas
    inova = linhas[1].split(";")
    assert inova[:8] == ["Mala", "Inova", "loja_teste", "ok", "", "28/09–04/10", "28/09/2026",
                         "04/10/2026"]
    assert inova[8] == "1000,00"  # vendas afiliados
    assert inova[11] == "5000"  # impressões (inteiro)
    assert inova[14] == "7,00"  # %
    luno = next(x.split(";") for x in linhas if ";Luno;" in x)
    assert luno[3] == "perfil Firefox, o robô não abre" and luno[4] == "perfil Firefox"
    assert luno[8:] == [""] * 8


def test_html_pagina_unica_com_os_grupos():
    rel = relatorio_exemplo()
    pagina = saida.html(rel)
    assert pagina.startswith("<!doctype html>")
    assert "<style>" in pagina and "<script" not in pagina and "http" not in pagina
    assert "<title>Conferência Shopee — 28/09 a 04/10/2026</title>" in pagina
    assert "comparado com 21/09 a 27/09" in pagina
    for trecho in ("<h2>Mala</h2>", "<h2>Celular</h2>", "<h2>Eletro</h2>",
                   "<h2>Últimas 4 semanas</h2>", "<h2>Notas</h2>", "Total Mala (1 conta)",
                   "Total Celular (3 contas)", "vs média 3 sem.", 'class="ant">ant. '):
        assert trecho in pagina
    # Cartões dos 4 grupos.
    assert pagina.count('<div class="card">') == 4
    # Visual de planilha: faixa azul nos títulos e grade cinza nas células.
    assert "background: #1F3864" in pagina and "border: 1px solid #BFBFBF" in pagina
    # A variação vem colorida pelo significado.
    assert '<span class="verde">' in pagina or '<span class="vermelho">' in pagina
    # Sem automação: só o rótulo simples, sem o erro cru do navegador.
    assert "perfil Firefox, o robô não abre" in pagina and "o robô não abre: " not in pagina


def test_html_e_md_escapam_o_nome():
    rel = relatorio_exemplo()
    rel["grupos"][0]["linhas"][0]["conta"] = "<b>Inova|X</b>"
    assert "&lt;b&gt;Inova|X&lt;/b&gt;" in saida.html(rel)
    assert "<b>Inova\\|X</b>" in saida.markdown(rel)


def test_markdown():
    rel = relatorio_exemplo()
    md = saida.markdown(rel)
    assert md.startswith("# Conferência Shopee — 28/09 a 04/10/2026\n")
    for trecho in ("## Mala", "## Celular", "## Eletro", "## Últimas 4 semanas", "## Notas",
                   "**Total Mala (1 conta)**", "Sem dados: Luno (perfil Firefox, o robô não abre)",
                   "Afiliados incompletos: Barbosa (até 03/10)",
                   "Gasto de Ads sem produto (ficou em Celular): Barbosa R$ 10,00"):
        assert trecho in md


def test_json_e_nome_do_arquivo():
    rel = relatorio_exemplo()
    assert json.loads(saida.json_bytes(rel)) == rel
    assert saida.nome_arquivo(rel, "xlsx") == "conferencia-shopee-2026-09-28_2026-10-04.xlsx"
    assert saida.titulo(rel) == "Conferência Shopee — 28/09 a 04/10/2026"
    assert saida.tipo_texto({**rel, "tipo": "parcial"}) == "parcial seg–dom"
