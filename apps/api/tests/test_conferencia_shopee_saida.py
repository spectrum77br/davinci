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

ABAS = ["Resumo"]  # pedido de 07/10/2026: "é só do resumo que eu preciso"


def _excel():
    rel = relatorio_exemplo()
    buf = saida.excel(rel)
    return rel, buf.getvalue(), load_workbook(io.BytesIO(buf.getvalue()))


def test_excel_abre_com_as_abas_do_documento():
    _, bruto, wb = _excel()
    assert bruto[:2] == b"PK"
    assert wb.sheetnames == ABAS
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
    # % s/ vendas gravado como fração com formato de % (aparece 7,0%, não 700,0%).
    lin_pct = next(r for r in range(5, 14) if ws.cell(r, 1).value == "% s/ vendas")
    c = ws.cell(lin_pct, 2)
    assert c.number_format == "0.0%"
    assert c.value == rel["grupos"][0]["total"]["semanas"][0]["pct"] / 100


def test_excel_so_resumo_sem_formulas():
    _, _, wb = _excel()
    ws = wb["Resumo"]
    for row in ws.iter_rows():
        for c in row:
            assert not (isinstance(c.value, str) and c.value.startswith("=") and len(c.value) > 1)


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


def test_html_so_o_resumo():
    rel = relatorio_exemplo()
    pagina = saida.html(rel)
    assert pagina.startswith("<!doctype html>")
    assert "<style>" in pagina and "<script" not in pagina and "http" not in pagina
    assert "<title>Conferência Shopee — 28/09 a 04/10/2026</title>" in pagina
    assert "comparado com 21/09 a 27/09" in pagina
    # Os 4 blocos do Resumo, com a contagem de contas e as duas comparações.
    for trecho in ('<h2>Mala <span class="qtd">(1 conta)</span></h2>',
                   '<h2>Celular <span class="qtd">(3 contas · 1 sem dados)</span></h2>',
                   '<h2>Eletro <span class="qtd">(1 conta)</span></h2>',
                   '<h2>Geral <span class="qtd">(4 contas)</span></h2>',
                   "vs semana anterior", "vs média 3 sem.", "% s/ vendas"):
        assert trecho in pagina, trecho
    # Nada de detalhe por loja nem notas longas.
    for fora in ('class="card"', "Total Mala", "Por conta", "<h2>Notas</h2>", "Últimas 4 semanas"):
        assert fora not in pagina, fora
    # Só os avisos que mudam a leitura.
    assert "Sem dados: Luno (perfil Firefox, o robô não abre)" in pagina
    assert "Afiliados incompletos: Barbosa (até 03/10)" in pagina
    # Visual de planilha: faixa azul nos títulos e grade cinza nas células.
    assert "background: #1F3864" in pagina and "border: 1px solid #BFBFBF" in pagina
    # A variação vem colorida pelo significado.
    assert '<span class="verde">' in pagina or '<span class="vermelho">' in pagina

def test_html_e_md_escapam_o_nome():
    rel = relatorio_exemplo()
    rel["grupos"][0]["linhas"][0]["conta"] = "<b>Inova|X</b>"
    rel["contas_sem_dados"][0]["conta"] = "<b>Luno</b>"
    assert "&lt;b&gt;Luno&lt;/b&gt;" in saida.html(rel)
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
