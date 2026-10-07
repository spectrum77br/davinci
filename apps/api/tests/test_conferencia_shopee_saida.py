"""Conferência Shopee — os arquivos do relatório (services/conferencia_shopee/saida).

Resumo no desenho da planilha do dono (07/10/2026): métrica (categoria +
afiliados/Ads) × semanas da mais VELHA para a mais nova × Mala/Celular/Eletro/
Geral + o bloco Variação (S1 × S2). Excel com só a aba Resumo (cabeçalho
azul-escuro, rótulos e Geral em bege, grade cinza, % como fração com 0.00%,
gráficos ABAIXO da tabela); HTML com a mesma tabela; MD compacto; CSV com
`;`, vírgula decimal e as métricas novas. Relatório antigo (sem cliques,
pedidos e conversão) sai com "—", nunca 0 nem erro. Mesmo cenário sintético
dos testes do cálculo.
"""

from __future__ import annotations

import copy
import io
import json
import re
import zipfile

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell

from app.services.conferencia_shopee import calculo, saida
from tests.test_conferencia_shopee_calculo import (
    GERADO,
    coleta,
    dados,
    execucao,
    relatorio_exemplo,
    semana,
)

ABAS = ["Resumo"]  # pedido de 07/10/2026: "é só do resumo que eu preciso"
NOVAS = calculo.CHAVES[8:]  # cliques, pedidos e conversão (07/10/2026)
SEMANAS_ANTIGA_PRIMEIRO = ["07/09 a 13/09", "14/09 a 20/09", "21/09 a 27/09", "28/09 a 04/10"]
VARIACAO = "Variação (28/09–04/10 × 21/09–27/09)"
GRUPOS = ["Mala", "Celular", "Eletro", "Geral"]
NAVY, BEGE = "1F3864", "DDD9C4"


def _excel(rel=None):
    rel = rel or relatorio_exemplo()
    buf = saida.excel(rel)
    return rel, buf.getvalue(), load_workbook(io.BytesIO(buf.getvalue()))


def _relatorio_antigo() -> dict:
    """Um relatório congelado antes de 07/10/2026: sem as 6 métricas novas."""
    rel = copy.deepcopy(relatorio_exemplo())
    rel["metricas"] = rel["metricas"][:8]

    def limpa(semanas):
        for s in semanas:
            for k in NOVAS:
                s.pop(k, None)

    for g in rel["grupos"]:
        limpa(g["total"]["semanas"])
        for lin in g["linhas"]:
            limpa(lin["semanas"])
    limpa(rel["geral"]["semanas"])
    return rel


def _total(rel, grupo: str, i: int, chave: str):
    if grupo == "geral":
        return rel["geral"]["semanas"][i][chave]
    return next(g for g in rel["grupos"] if g["chave"] == grupo)["total"]["semanas"][i][chave]


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
    assert "Vendas por grupo" in xml and "% investimento / vendas por grupo" in xml


def test_excel_resumo_no_desenho_da_planilha():
    rel, _, wb = _excel()
    ws = wb["Resumo"]
    assert ws["A1"].value == "Conferência Shopee — 28/09 a 04/10/2026 (semana fechada)"
    assert ws["A2"].value.startswith("comparado com 21/09 a 27/09 · gerado em")
    # Cabeçalho em 2 linhas: semanas da mais velha para a mais nova (4 colunas
    # mescladas cada) e, no fim, a Variação S1 × S2.
    assert [ws.cell(4, 3 + 4 * b).value for b in range(5)] == [
        *SEMANAS_ANTIGA_PRIMEIRO, VARIACAO,
    ]
    assert ws["A5"].value == "Métrica"
    assert [ws.cell(5, c).value for c in range(3, 23)] == GRUPOS * 5
    mesclas = {str(r) for r in ws.merged_cells.ranges}
    assert {"A4:B4", "A5:B5", "C4:F4", "G4:J4", "K4:N4", "O4:R4", "S4:V4"} <= mesclas
    # Duas colunas de rótulo: a categoria mesclada nas suas 2 linhas.
    assert [(ws.cell(r, 1).value, ws.cell(r, 2).value) for r in range(6, 16)] == [
        ("Vendas", "afiliados"), (None, "Ads"),
        ("Impressões", "afiliados"), (None, "Ads"),
        ("Conversão", "afiliados"), (None, "Ads"),
        ("Investimento", "afiliados"), (None, "Ads"),
        ("Resumo", "% investimento / vendas"), (None, "Vendas no período"),
    ]
    assert {"A6:A7", "A8:A9", "A10:A11", "A12:A13", "A14:A15"} <= mesclas
    assert ws.freeze_panes == "C6"
    # Números: S1 de Mala em O, S4 em C; Geral na 4ª coluna de cada semana.
    assert ws["O6"].value == _total(rel, "mala", 0, "vendas_afiliados")
    assert ws["C6"].value == _total(rel, "mala", 3, "vendas_afiliados")
    assert ws["R15"].value == _total(rel, "geral", 0, "vendas")
    assert ws["P9"].value == _total(rel, "celular", 0, "impressoes")
    assert ws["O6"].number_format == '"R$" #,##0.00'
    assert ws["P9"].number_format == "#,##0"
    # Impressões de afiliados não existem na Shopee: "—" em tudo, até na variação.
    assert all(ws.cell(8, c).value == "—" for c in range(3, 23))
    # % como FRAÇÃO com 2 casas (aparece 7,00%, não 700%): conversão e % s/ vendas.
    for r, chave in ((10, "conversao_afiliados"), (11, "conversao_ads"), (14, "pct")):
        c = ws.cell(r, 15)
        assert c.number_format == "0.00%", chave
        assert c.value == _total(rel, "mala", 0, chave) / 100, chave
    # Variação (S1 × S2, cenário com as 4 semanas iguais): "=" em cinza.
    assert ws["S6"].value == "=" and ws["S6"].font.color.rgb.endswith("808080")
    # Saldo Ads não entra no Resumo.
    textos = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]
    assert not any("Saldo" in t for t in textos)


def _estilo_cru(bruto: bytes) -> dict[str, str]:
    """célula → id de estilo no XML (o openpyxl, ao LER, apaga o estilo das
    células de dentro de uma mescla; o arquivo guarda)."""
    with zipfile.ZipFile(io.BytesIO(bruto)) as z:
        xml = z.read("xl/worksheets/sheet1.xml").decode()
    return dict(re.findall(r'<c r="([A-Z]+\d+)" s="(\d+)"', xml))


def test_excel_estilo_da_planilha():
    _, bruto, wb = _excel()
    ws = wb["Resumo"]
    # Cabeçalho azul-escuro com letra branca em negrito.
    for ref in ("A4", "C4", "A5", "C5", "F5", "S4", "V5"):
        c = ws[ref]
        assert c.fill.fgColor.rgb.endswith(NAVY), ref
        assert c.font.bold and c.font.color.rgb.endswith("FFFFFF"), ref
    # As células de dentro das mesclas guardam o mesmo estilo da primeira.
    cru = _estilo_cru(bruto)
    for dentro, primeira in (("B4", "A4"), ("D4", "C4"), ("V4", "S4"), ("B5", "A5"),
                             ("A7", "A6"), ("A15", "A14")):
        assert cru[dentro] == cru[primeira], dentro
    # Rótulos e TODA coluna Geral (semanas e variação) em bege; o resto sem.
    for ref in ("A6", "B6", "B15", "F6", "J10", "N14", "R15", "V6", "V15"):
        assert ws[ref].fill.fgColor.rgb.endswith(BEGE), ref
    for ref in ("C6", "E10", "O6", "S6", "U15"):
        assert not ws[ref].fill.fgColor.rgb.endswith(BEGE), ref
    # Grade cinza fina em toda célula da tabela (as de dentro das mesclas já
    # foram conferidas acima, pelo estilo cru).
    for r in range(4, 16):
        for c in range(1, 23):
            if isinstance(ws.cell(r, c), MergedCell):
                continue
            borda = ws.cell(r, c).border
            for lado in (borda.left, borda.right, borda.top, borda.bottom):
                assert lado.style == "thin" and lado.color.rgb.endswith("BFBFBF"), (r, c)
    # Nenhum formato com "%" literal (o Mac multiplica): só o 0.00% / 0.0% de verdade.
    for row in ws.iter_rows():
        for c in row:
            if "%" in (c.number_format or ""):
                assert c.number_format in ("0.00%", "0.0%"), c.coordinate


def test_excel_avisos_curtos_e_graficos_abaixo_da_tabela():
    _, _, wb = _excel()
    ws = wb["Resumo"]
    avisos = [ws.cell(r, 1).value for r in range(16, 22) if ws.cell(r, 1).value]
    assert avisos == [
        "⚠️ Sem dados: Luno (perfil Firefox, o robô não abre)",
        "⚠️ Afiliados incompletos: Barbosa (até 03/10)",
    ]
    # Os gráficos começam depois da tabela e dos avisos (sem cobrir nada) e os
    # dados deles ficam embaixo dos gráficos.
    linha_avisos = max(r for r in range(16, 22) if ws.cell(r, 1).value)
    ancoras = sorted((g.anchor._from.row + 1, g.anchor._from.col) for g in ws._charts)
    assert [linha for linha, _ in ancoras] == [linha_avisos + 2] * 2
    assert [col for _, col in ancoras] == [0, 6]  # A e G, lado a lado
    titulo = next(r for r in range(1, ws.max_row + 1)
                  if ws.cell(r, 1).value == "Dados dos gráficos (da semana mais antiga para a "
                  "mais recente)")
    assert titulo >= linha_avisos + 2 + 15
    assert [ws.cell(titulo + 1, c).value for c in range(1, 6)] == ["Vendas", *GRUPOS]
    assert [ws.cell(titulo + 2 + i, 1).value for i in range(4)] == [
        "07/09–13/09", "14/09–20/09", "21/09–27/09", "28/09–04/10",
    ]


def test_excel_so_resumo_sem_formulas():
    _, _, wb = _excel()
    ws = wb["Resumo"]
    for row in ws.iter_rows():
        for c in row:
            assert not (isinstance(c.value, str) and c.value.startswith("=") and len(c.value) > 1)
            assert c.data_type != "f", c.coordinate


def test_eletro_sem_conta_mostra_traco():
    """Sem conta de celular com eletro: a coluna Eletro sai "—" (nunca 0)."""
    rel = calculo.montar_relatorio(
        execucao(), [coleta("Inova", "mala", dados([semana(i) for i in range(4)]))],
        gerado_em=GERADO,
    )
    _, _, wb = _excel(rel)
    ws = wb["Resumo"]
    eletro = [ws.cell(r, 3 + 4 * b + 2).value for b in range(4) for r in range(6, 16)]
    assert set(eletro) == {"—"}
    assert ws["U6"].value == "—"  # variação do Eletro
    pagina = saida.html(rel)
    assert pagina.count('<td class="vazio">—</td>') >= 4 * 10


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
    # As métricas de 07/10/2026 no fim, na ordem do relatório.
    assert cab[16:] == ["Cliques afiliados", "Pedidos afiliados", "Conversão afiliados",
                        "Cliques Ads", "Pedidos Ads", "Conversão Ads"]
    assert inova[16:] == ["400", "10", "2,50", "10", "3", "30,00"]
    luno = next(x.split(";") for x in linhas if ";Luno;" in x)
    assert luno[3] == "perfil Firefox, o robô não abre" and luno[4] == "perfil Firefox"
    assert luno[8:] == [""] * 14


def test_html_no_desenho_da_planilha():
    rel = relatorio_exemplo()
    pagina = saida.html(rel)
    assert pagina.startswith("<!doctype html>")
    assert "<style>" in pagina and "<script" not in pagina and "http" not in pagina
    assert "<title>Conferência Shopee — 28/09 a 04/10/2026</title>" in pagina
    assert "comparado com 21/09 a 27/09" in pagina
    # Uma tabela só: 2 linhas de cabeçalho, semanas da mais velha para a mais
    # nova e a Variação no fim.
    assert pagina.count('<table class="planilha">') == 1
    # A data vai num span "parado" (sticky): no celular a célula mesclada passa
    # da tela e a data centrada sumia.
    posicoes = [pagina.index(f'<th class="bloco" colspan="4"><span class="semana">{r}</span></th>')
                for r in [*SEMANAS_ANTIGA_PRIMEIRO, VARIACAO]]
    assert posicoes == sorted(posicoes)
    assert re.search(r"\.planilha th \.semana \{[^}]*position: sticky;[^}]*"
                     r"left: calc\(var\(--larg-cat\) \+ var\(--larg-sub\)[^}]*right: ", pagina)
    assert '<th class="rotulo c12" colspan="2">Métrica</th>' in pagina
    assert pagina.count('<th class="geral">Geral</th>') == 5
    for trecho in ('<td class="rotulo cat c1" rowspan="2">Vendas</td>',
                   '<td class="rotulo cat c1" rowspan="2">Impressões</td>',
                   '<td class="rotulo cat c1" rowspan="2">Conversão</td>',
                   '<td class="rotulo cat c1" rowspan="2">Investimento</td>',
                   '<td class="rotulo cat c1" rowspan="2">Resumo</td>',
                   '<td class="rotulo subr c2">% investimento / vendas</td>',
                   '<td class="rotulo subr c2">Vendas no período</td>',
                   "<td>7,00%</td>",  # % com 2 casas
                   "<td>2,50%</td>", "<td>30,00%</td>",  # conversões da Mala
                   '<td class="geral">R$ 6.000,00</td>'):
        assert trecho in pagina, trecho
    # Impressões de afiliados: só "—".
    linha = pagina[pagina.index(">Impressões</td>"):]
    linha = linha[: linha.index("</tr>")]
    assert linha.count(">—<") == 4 * 5 and "<td>" not in linha.replace('<td class="', "")
    # Visual de planilha: azul no cabeçalho, bege nos rótulos e no Geral, grade
    # cinza, rola de lado com as 2 primeiras colunas paradas.
    for css in ("background: #1F3864", "background: #DDD9C4", "1px solid #BFBFBF",
                "overflow-x: auto", "position: sticky"):
        assert css in pagina, css
    # Nada de detalhe por loja, saldo nem notas longas; só os avisos curtos.
    for fora in ("Saldo", "Barbosa (loja_teste)", "<h2>", "Notas", "Gasto de Ads sem produto"):
        assert fora not in pagina, fora
    assert "Sem dados: Luno (perfil Firefox, o robô não abre)" in pagina
    assert "Afiliados incompletos: Barbosa (até 03/10)" in pagina


def test_variacao_colorida_pelo_que_e_bom():
    rel = relatorio_exemplo()
    s0 = rel["geral"]["semanas"][0]
    s0.update(vendas_afiliados=s0["vendas_afiliados"] * 1.5, pct=s0["pct"] - 1,
              conversao_ads=s0["conversao_ads"] - 2, invest_ads=s0["invest_ads"] * 2)
    pagina = saida.html(rel)
    assert '<td class="var geral"><span class="verde">▲ 50,0%</span></td>' in pagina
    assert '<td class="var geral"><span class="verde">▼ 1,00 p.p.</span></td>' in pagina  # %
    assert '<td class="var geral"><span class="vermelho">▼ 2,00 p.p.</span></td>' in pagina
    assert '<td class="var geral"><span class="cinza">▲ 100,0%</span></td>' in pagina  # invest.
    _, _, wb = _excel(rel)
    ws = wb["Resumo"]
    assert (ws["V6"].value, ws["V6"].font.color.rgb[-6:]) == ("▲ 50,0%", "008000")
    assert (ws["V11"].value, ws["V11"].font.color.rgb[-6:]) == ("▼ 2,00 p.p.", "C00000")
    assert (ws["V14"].value, ws["V14"].font.color.rgb[-6:]) == ("▼ 1,00 p.p.", "008000")


def test_variacao_em_pp_com_2_casas_como_o_percentual_da_planilha():
    """O % da planilha tem 2 casas e a variação em p.p. também: conversão de
    0,44% → 0,43% é "▼ 0,01 p.p." (com 1 casa sairia "▼ 0,0 p.p." em
    vermelho, dizendo zero). Igual na tela (planilhaResumo), no Excel, no HTML
    e no MD. O Threema (% com 1 casa) não muda."""
    rel = relatorio_exemplo()
    g0, g1 = rel["geral"]["semanas"][0], rel["geral"]["semanas"][1]
    g0["conversao_afiliados"], g1["conversao_afiliados"] = 0.43, 0.44
    g0["conversao_ads"], g1["conversao_ads"] = 0.25, 0.21
    p = saida.planilha(rel)
    var = {lin["chave"]: lin["variacoes"][3] for lin in p["linhas"]}
    assert (var["conversao_afiliados"]["texto"], var["conversao_afiliados"]["cor"]) == (
        "▼ 0,01 p.p.", "vermelho")
    assert (var["conversao_ads"]["texto"], var["conversao_ads"]["cor"]) == (
        "▲ 0,04 p.p.", "verde")
    # Dinheiro e inteiro continuam com 1 casa no % relativo.
    assert calculo.variacao(112.25, 100, "dinheiro", "sobe", None, casas=2)["texto"] == "▲ 12,3%"
    pagina = saida.html(rel)
    assert '<td class="var geral"><span class="vermelho">▼ 0,01 p.p.</span></td>' in pagina
    assert '<td class="var geral"><span class="verde">▲ 0,04 p.p.</span></td>' in pagina
    assert "▼ 0,01 p.p." in saida.markdown(rel)
    _, _, wb = _excel(rel)
    ws = wb["Resumo"]
    assert ws["V10"].value == "▼ 0,01 p.p." and ws["V11"].value == "▲ 0,04 p.p."
    assert "0,0 p.p." not in pagina


def test_relatorio_antigo_sem_as_metricas_novas_sai_com_traco():
    """Relatório congelado antes de 07/10/2026 (sem cliques/pedidos/conversão):
    todos os arquivos saem, com "—" na Conversão, nunca 0 nem erro."""
    rel = _relatorio_antigo()
    _, _, wb = _excel(rel)
    ws = wb["Resumo"]
    for r in (10, 11):  # Conversão afiliados / Ads
        assert {ws.cell(r, c).value for c in range(3, 23)} == {"—"}, r
    assert ws["O6"].value == _total(rel, "mala", 0, "vendas_afiliados")
    pagina = saida.html(rel)
    conv = pagina[pagina.index(">Conversão</td>"):]
    conv = conv[: conv.index(">Investimento</td>")]
    assert "%" not in conv
    md = saida.markdown(rel)
    assert "| Conversão | afiliados | — | — | — | — | — |" in md
    linhas = saida.csv(rel).decode("utf-8-sig").splitlines()
    assert linhas[1].split(";")[16:] == [""] * 6
    assert json.loads(saida.json_bytes(rel)) == rel


def test_html_e_md_escapam_o_nome():
    rel = relatorio_exemplo()
    rel["grupos"][0]["linhas"][0]["conta"] = "<b>Inova|X</b>"
    rel["contas_sem_dados"][0]["conta"] = "<b>Luno</b>"
    assert "&lt;b&gt;Luno&lt;/b&gt;" in saida.html(rel)
    assert "<b>Inova\\|X</b>" in saida.markdown(rel)


def test_markdown_compacto():
    rel = relatorio_exemplo()
    md = saida.markdown(rel)
    assert md.startswith("# Conferência Shopee — 28/09 a 04/10/2026\n")
    cab = ("| Métrica | | " + " | ".join(SEMANAS_ANTIGA_PRIMEIRO) + f" | {VARIACAO} |")
    assert md.count(cab) == 4  # Mala, Celular, Eletro, Geral
    for trecho in ("## Resumo", "### Mala (1 conta)", "### Celular (3 contas · 1 sem dados)",
                   "### Eletro (1 conta)", "### Geral (4 contas)",
                   "| Vendas | afiliados | " + "R$ 1.000,00 | " * 4 + "= |",
                   "| Impressões | afiliados | — | — | — | — | — |",
                   "| Conversão | afiliados | 2,50% | 2,50% | 2,50% | 2,50% | = |",
                   "|  | Vendas no período |",
                   "## Vendas por conta", "| Celular | Luno ⚠️ perfil Firefox, o robô não abre |",
                   "## Avisos", "Sem dados: Luno (perfil Firefox, o robô não abre)",
                   "Afiliados incompletos: Barbosa (até 03/10)",
                   "Gasto de Ads sem produto (ficou em Celular): Barbosa R$ 10,00", "## Notas"):
        assert trecho in md, trecho
    assert "Saldo Ads |" not in md


def test_json_e_nome_do_arquivo():
    rel = relatorio_exemplo()
    assert json.loads(saida.json_bytes(rel)) == rel
    assert saida.nome_arquivo(rel, "xlsx") == "conferencia-shopee-2026-09-28_2026-10-04.xlsx"
    assert saida.titulo(rel) == "Conferência Shopee — 28/09 a 04/10/2026"
    assert saida.tipo_texto({**rel, "tipo": "parcial"}) == "parcial seg–dom"
