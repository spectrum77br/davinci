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
from openpyxl.utils import get_column_letter

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
    "sem_automacao": "perfil Firefox, o robô não abre",
    "bloqueada": "bloqueada pela Shopee",
    "interrompida": "interrompida",
    "erro": "erro",
    "expirada": "não coletada a tempo",
}

# Nome da aba de cada métrica (o Excel não aceita "/" em nome de aba).



# ───────────────────────────────────────────────────────────── textos comuns


_NOTA_GRUPO = {
    "mala": "",
    "celular": "total da conta − eletro; saldo de Ads da conta inteira",
    "eletro": "só os produtos de eletro das contas de celular; Saldo Ads = —",
}


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
_NORMAL = Font(name=_FONTE, size=10)
_NEGRITO = Font(name=_FONTE, size=10, bold=True)
_CINZA = Font(name=_FONTE, size=10, color="808080")
_fino = Side(style="thin", color="BFBFBF")
_BORDA = Border(left=_fino, right=_fino, top=_fino, bottom=_fino)
_COR_XLSX = {"verde": "008000", "vermelho": "C00000", "cinza": "808080"}
_FORMATO = {
    "dinheiro": '"R$" #,##0.00',
    "inteiro": "#,##0",
    # % gravado como FRAÇÃO (0,0234) com o formato padrão de porcentagem. Com o
    # número 2,34 e um "%" literal o Excel mostrava 2,3%, mas o Numbers e a
    # pré-visualização do Mac mostravam 234,0% (print de 07/10/2026).
    "percentual": "0.0%",
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
    if tipo and isinstance(valor, int | float) and not isinstance(valor, bool):
        if tipo == "percentual":
            c.value = valor / 100
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
            ("pct", "% s/ vendas por grupo", "0.0%"),
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




def excel(rel: Mapping) -> BytesIO:
    """Só a aba Resumo (Mala, Celular, Eletro e Geral nas 4 semanas + os 2
    gráficos). Pedido de 07/10/2026: "é só do resumo que eu preciso" — as abas
    Semana, uma por métrica e Dados saíram; o detalhe por loja segue no sistema
    (JSON/CSV e o próprio relatório guardado)."""
    wb = Workbook()
    _aba_resumo(wb, rel)
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

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px 48px; background: #ffffff; color: #1f2937;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  font-size: 14px; line-height: 1.4; }
main { max-width: 1280px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; color: #1F3864; }
/* faixas azuis como no Excel: grupo (h2) e blocos (h3), coladas na tabela de baixo */
h2 { font-size: 15px; margin: 32px 0 0; padding: 8px 12px; background: #1F3864; color: #ffffff;
  border-radius: 8px 8px 0 0; text-transform: uppercase; letter-spacing: .04em; }
h3 { font-size: 13px; margin: 18px 0 0; padding: 6px 12px; background: #2F5597; color: #ffffff;
  border-radius: 6px 6px 0 0; }
.sub { color: #6b7280; margin: 0 0 12px; }
.nota-grupo { margin: 0; padding: 4px 12px; background: #DDEBF7; color: #1F3864; font-size: 12px;
  border: 1px solid #BFBFBF; border-bottom: 0; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 12px; margin: 20px 0 8px; }
.card { border: 1px solid #BFBFBF; border-radius: 10px; background: #ffffff; overflow: hidden; }
.card h3 { margin: 0; padding: 8px 14px; background: #1F3864; color: #ffffff; border-radius: 0;
  font-size: 14px; }
.card h3 .cinza { color: #c9d7ef; font-weight: 400; font-size: 12px; }
.card .lin { margin: 10px 14px; text-align: right; }
.card .rot { color: #6b7280; font-size: 12px; text-align: left; }
.card .val { font-size: 18px; font-weight: 600; }
.ant { display: block; font-size: 11px; color: #6b7280; white-space: nowrap; }
.rolar { overflow-x: auto; margin: 0 0 8px; }
table { border-collapse: collapse; width: 100%; margin: 0; border: 1px solid #BFBFBF; }
th { background: #1F3864; color: #ffffff; font-weight: 600; padding: 6px 8px; text-align: center;
  font-size: 12px; border: 1px solid #BFBFBF; }
td { border: 1px solid #BFBFBF; padding: 6px 8px; text-align: right; vertical-align: top;
  white-space: nowrap; }
tbody tr:nth-child(even) td { background: #F3F6FB; }
td.conta, td.txt { text-align: left; font-weight: 600; }
td.conta small { display: block; color: #6b7280; font-size: 11px; font-weight: 400; }
tr.total td { font-weight: 700; background: #D9E1F2; }
.verde { color: #1a7f37; } .vermelho { color: #c62828; } .cinza { color: #6b7280; }
.aviso { color: #b45309; font-size: 11px; display: block; white-space: normal; font-weight: 400; }
h2 .qtd { font-weight: 400; text-transform: none; letter-spacing: 0; opacity: .8; font-size: 13px; }
td.s1 { font-weight: 700; }
td.var { text-align: center; }
.avisos { margin: 16px 0 0; padding: 8px 12px; border: 1px solid #f59e0b55; background: #fffbeb;
  color: #b45309; border-radius: 6px; font-size: 13px; } .avisos p { margin: 2px 0; }
ul.notas { padding: 10px 12px 10px 30px; margin: 0; color: #374151; border: 1px solid #BFBFBF;
  border-top: 0; } ul.notas li { margin-bottom: 4px; }
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
        # "sem automação" já diz tudo no rótulo; o erro cru do navegador só confunde.
        if lin.get("erro") and lin.get("status") != "sem_automacao":
            detalhe += f": {lin['erro']}"
        partes.append(f'<span class="aviso">⚠️ {escape(detalhe)}</span>')
    for a in lin.get("avisos") or []:
        partes.append(f'<span class="aviso">⚠️ {escape(a)}</span>')
    return f'<td class="conta">{"".join(partes)}</td>'


def html(rel: Mapping) -> str:
    """Página única (CSS embutido, tema claro) com o mesmo Resumo da aba e do
    Excel: Mala, Celular, Eletro e Geral nas 4 semanas, as duas comparações e
    só os avisos que mudam a leitura (pedido de 07/10/2026: "só o resumo")."""
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
    cab = (
        "<tr><th>Métrica</th>" + "".join(f"<th>{escape(r)}</th>" for r in rotulos)
        + "<th>vs semana anterior</th><th>vs média 3 sem.</th></tr>"
    )
    for chave, rot, total in _grupos_e_geral(rel):
        sem_dados = total.get("sem_dados") if chave != "geral" else None
        extra = f" · {sem_dados} sem dados" if sem_dados else ""
        qtd = escape(_contas_txt(total["contas"]) + extra)
        p.append(f'<h2>{escape(rot)} <span class="qtd">({qtd})</span></h2>')
        p.append(f'<div class="rolar"><table><thead>{cab}</thead><tbody>')
        for m in METRICAS:
            v_ant, v_media = variacoes(total["semanas"], m["chave"])
            p.append(
                f'<tr><td class="txt">{escape(m["rotulo"])}</td>'
                + "".join(
                    f"<td{' class=\"s1\"' if i == 0 else ''}>"
                    f"{escape(formatar(_valor(total['semanas'], i, m['chave']), m['tipo']))}</td>"
                    for i in range(len(rotulos))
                )
                + f'<td class="var">{_var_html(v_ant)}</td>'
                + f'<td class="var">{_var_html(v_media)}</td></tr>'
            )
        p.append("</tbody></table></div>")

    avisos = _avisos_curtos(rel)
    if avisos:
        p.append('<div class="avisos">' + "".join(f"<p>⚠️ {escape(a)}</p>" for a in avisos)
                 + "</div>")
    p.append("</main></body></html>")
    return "\n".join(p)


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
