"""O RELATÓRIO do eval do agente: as notas do Claude contra as barras da spec 9.4 (20/10/2026).

Lê a amostra, os resultados de cada braço (`rodar.py`), a chave da exportação
(`exportar.py`: id escondido → item e braço) e as notas que a sessão do
Claude Code escreveu — uma linha por id:

    {"id", "enviaria_sem_editar": bool, "erro_critico": bool,
     "acao_pessoa_correta": bool, "comentario": str,
     opcionais: "rotulo" (S|A1|A2|P|B|C — vence o da amostra),
                "orientacao_correta": bool, "produto_sem_rastro": bool}

e imprime, por braço: erro crítico (barra 0), recall de ação de pessoa pela
decisão simulada (B, P e C; barra 100%), encaminhamento evitável (pessoa em
item A1 ou S; barra ≤ 5%), enviável sem editar (A1, S, estoque, prazo, NF,
especificação; ≥ 75%), orientação correta nos assuntos de orientação (≥ 90%),
estoque e variação resolvidos (≥ 85%), afirmação de produto sem rastro
(≤ 2%), custo por resposta (≤ US$ 0,005) e latência p95 (≤ 45 s); tudo
também POR ASSUNTO; a calibração da confiança; a comparação pareada com o
braço-base; o controle (quanto a nota dá "enviaria" para a própria equipe);
os plantados pegos (barra 20 de 20) e a concordância da releitura (≥ 90% no
"enviaria", 100% no "erro crítico").

Só lê arquivos e não importa o app (roda no container ou no Mac). Nada de
texto de cliente na saída: só ids, assuntos e contagens.

Uso:
    uv run python -m scripts.agente_eval.relatorio --amostra amostra.jsonl \\
        --resultados resultados_A1.jsonl resultados_A2.jsonl \\
        --chave nota/chave.json --notas notas.jsonl [notas_releitura.jsonl] [--json]
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

# As barras da spec 9.4.
BARRA_ERRO_CRITICO = 0
BARRA_RECALL = 1.0
BARRA_EVITAVEL = 0.05
BARRA_ENVIAVEL = 0.75
BARRA_ORIENTACAO = 0.90
BARRA_ESTOQUE = 0.85
BARRA_PRODUTO_SEM_RASTRO = 0.02
BARRA_CUSTO_USD = 0.005
BARRA_LATENCIA_MS = 45_000
BARRA_CONCORDANCIA_ENVIARIA = 0.90

BRACO_EQUIPE = "equipe"
PREFIXO_PLANTADO = "plantado:"
BRACO_BASE = "A2"
# O L0 (`agente.replay.rascunho_do_legado`; o teste confere): a parada do
# registro lido do banco e o pulado sem a sugestão do legado.
PARADA_LEGADO = "legado"
PULADO_SEM_LEGADO = "sem_rascunho_do_legado"
# Os rótulos da spec 14 e os estratos da amostra (`amostra.py`).
PRECISA_PESSOA = frozenset({"B", "P", "C"})
RESPONDIVEIS = frozenset({"A1", "S"})
ESTRATOS_ENVIAVEIS = frozenset(
    {"sem_demanda", "estoque_variacao", "prazo_rastreio", "nota_fiscal", "especificacao"}
)
ESTRATO_ORIENTACAO = "orientacao"
ESTRATO_ESTOQUE = "estoque_variacao"
# As checagens do agente que pegam afirmação de produto sem fonte
# (`agente.checagens.PRODUTO_*`; o teste confere).
CHECAGENS_DE_PRODUTO = frozenset({"produto_sem_fonte", "produto_contradiz"})
FAIXAS_CONFIANCA = ((0.0, 0.5), (0.5, 0.85), (0.85, 1.01))
# Item 8 da rodada "redondo" (23/10/2026): o que a troca para o Sonnet 5.5 e o COMO PENSAR
# do bloco 2 devem mover (sem nota: lido dos registros). A ação `dado_indisponivel` que o
# MODELO escolheu deve cair; o assunto não pedido (`checagens.ASSUNTO_NAO_PEDIDO`), também;
# a G1 (`checagens.OUTRO_PRODUTO_SEM_BUSCA`, `NAO_TEMOS_SEM_FONTE`, `NAO_TEMOS_MODELO`)
# mostra o outro produto dito sem a busca; e as ferramentas que o COMO PENSAR manda usar.
ACAO_DADO_INDISPONIVEL = "dado_indisponivel"
CHECAGEM_ASSUNTO_NAO_PEDIDO = "assunto_nao_pedido"
CHECAGENS_G1 = frozenset({"outro_produto_sem_busca", "nao_temos_sem_fonte", "nao_temos_modelo"})
FERRAMENTAS_DA_RODADA = (
    "ver_anuncio",
    "ver_pedido",
    "consultar_manual",
    "buscar_respostas_equipe",
    "buscar_anuncios_da_loja",
)


def _ler_jsonl(caminho: Path) -> list[dict]:
    with caminho.open(encoding="utf-8") as f:
        return [json.loads(linha) for linha in f if linha.strip()]


def p95(valores: Iterable[float]) -> float | None:
    """O p95 pelo posto mais próximo (sem interpolar). PURA."""
    ordem = sorted(valores)
    if not ordem:
        return None
    return ordem[max(0, math.ceil(0.95 * len(ordem)) - 1)]


def _percentil(ordem: Sequence[float], q: float) -> float:
    return ordem[max(0, math.ceil(q * len(ordem)) - 1)]


def _taxa(par: tuple[int, int]) -> float | None:
    sim, total = par
    return sim / total if total else None


def _par(sim: int, total: int) -> tuple[int, int]:
    return (sim, total)


def _rotulo(item: Mapping[str, Any], notas_do_item: Sequence[Mapping[str, Any]]) -> str | None:
    for nota in notas_do_item:
        if nota.get("rotulo"):
            return str(nota["rotulo"]).strip().upper()
    return item.get("rotulo")


def _assunto(reg: Mapping[str, Any]) -> str:
    decisao = reg.get("decisao") or {}
    proposta = reg.get("proposta") or {}
    return str(decisao.get("categoria") or proposta.get("assunto") or reg.get("atalho") or "?")


def metricas_da_rodada(validos: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Item 8 da rodada "redondo": as taxas do braço que o eval S1 compara (ver o topo). PURA.

    Só os itens que o agente respondeu (nem o L0, nem o pulado): o `dado_indisponivel` que o
    modelo escolheu, o assunto não pedido, a G1, a decisão simulada (pessoa, apto ao
    automático, enviaria), as ferramentas usadas (itens que chamaram cada uma), os turnos e
    a latência p50/p90.
    """
    regs = [r for r in validos.values() if not r.get("pulado") and r.get("parada") != PARADA_LEGADO]
    n = len(regs)

    def _conta(teste: Any) -> tuple[int, int]:
        return _par(sum(1 for r in regs if teste(r)), n)

    def _checagens(r: Mapping[str, Any]) -> set[str]:
        return set((r.get("rastro") or {}).get("checagens") or [])

    def _do_modelo(r: Mapping[str, Any]) -> set[str]:
        decisao = r.get("decisao") or {}
        proposta = r.get("proposta") or {}
        return {*(decisao.get("acoes_modelo") or []), proposta.get("acao_pessoa")}

    usadas = Counter(
        nome
        for r in regs
        for nome in {p.get("ferramenta") for p in (r.get("rastro") or {}).get("passos") or []}
        if nome
    )
    ms = sorted(int(r["ms"]) for r in regs if isinstance(r.get("ms"), int))
    turnos = [int(r["turnos"]) for r in regs if isinstance(r.get("turnos"), int)]
    return {
        "n": n,
        "dado_indisponivel_do_modelo": _conta(lambda r: ACAO_DADO_INDISPONIVEL in _do_modelo(r)),
        "assunto_nao_pedido": _conta(lambda r: CHECAGEM_ASSUNTO_NAO_PEDIDO in _checagens(r)),
        "g1": _conta(lambda r: bool(_checagens(r) & CHECAGENS_G1)),
        "pessoa": _conta(lambda r: bool((r.get("decisao") or {}).get("pessoa"))),
        "apto_auto": _conta(lambda r: bool((r.get("decisao") or {}).get("apto_auto"))),
        "enviaria": _conta(lambda r: bool((r.get("decisao") or {}).get("enviaria"))),
        "ferramentas": {nome: _par(usadas.get(nome, 0), n) for nome in FERRAMENTAS_DA_RODADA},
        "turnos_media": sum(turnos) / len(turnos) if turnos else None,
        "latencia_p50_ms": _percentil(ms, 0.5) if ms else None,
        "latencia_p90_ms": _percentil(ms, 0.9) if ms else None,
    }


def _metricas_do_braco(
    braco: str,
    itens: Mapping[str, Mapping[str, Any]],
    resultados: Mapping[str, Mapping[str, Any]],
    notas: Mapping[str, Mapping[str, Any]],
    rotulos: Mapping[str, str | None],
) -> dict[str, Any]:
    """As métricas de UM braço. `notas` = item → a nota deste braço.

    O L0 sem a sugestão do legado (`PULADO_SEM_LEGADO`) não conta em métrica
    nenhuma (revisão de 20/10/2026: contava como "pessoa" e dava recall de
    graça ao legado): sai em `sem_legado`, e `versoes_do_legado` diz contra
    qual versão do prompt do legado o L0 compara.
    """
    sem_legado = sum(1 for r in resultados.values() if r.get("pulado") == PULADO_SEM_LEGADO)
    validos = {
        i: r
        for i, r in resultados.items()
        if not r.get("erro") and r.get("pulado") != PULADO_SEM_LEGADO
    }
    recall = [0, 0]
    evitavel = [0, 0]
    enviavel = [0, 0]
    orientacao = [0, 0]
    estoque = [0, 0]
    acao_correta = [0, 0]
    sem_rastro = [0, 0]
    sem_rastro_codigo = 0
    erro_critico = 0
    por_assunto: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "notas": 0, "enviaria": 0, "pessoa": 0, "erro_critico": 0}
    )
    calibracao = {f"{a:.2f}-{min(b, 1.0):.2f}": [0, 0] for a, b in FAIXAS_CONFIANCA}
    for iid, reg in validos.items():
        item = itens.get(iid, {})
        rot = rotulos.get(iid)
        estrato = item.get("estrato") or reg.get("estrato")
        decisao = reg.get("decisao") or {}
        pessoa = bool(decisao.get("pessoa"))
        nota = notas.get(iid)
        a = por_assunto[_assunto(reg)]
        a["n"] += 1
        a["pessoa"] += int(pessoa)
        if rot in PRECISA_PESSOA:
            recall[1] += 1
            recall[0] += int(pessoa)
        elif rot in RESPONDIVEIS:
            evitavel[1] += 1
            evitavel[0] += int(pessoa and not decisao.get("sem_resposta"))
        if set((reg.get("rastro") or {}).get("checagens") or []) & CHECAGENS_DE_PRODUTO:
            sem_rastro_codigo += 1
        if nota is None:
            continue
        envia = bool(nota.get("enviaria_sem_editar"))
        critico = bool(nota.get("erro_critico"))
        a["notas"] += 1
        a["enviaria"] += int(envia)
        a["erro_critico"] += int(critico)
        erro_critico += int(critico)
        if rot in PRECISA_PESSOA:
            acao_correta[1] += 1
            acao_correta[0] += int(bool(nota.get("acao_pessoa_correta")))
            continue
        if rot in RESPONDIVEIS or estrato in ESTRATOS_ENVIAVEIS:
            enviavel[1] += 1
            enviavel[0] += int(envia)
        if estrato == ESTRATO_ORIENTACAO:
            orientacao[1] += 1
            certa = nota.get("orientacao_correta")
            orientacao[0] += int(bool(certa) if certa is not None else envia and not critico)
        if estrato == ESTRATO_ESTOQUE:
            estoque[1] += 1
            estoque[0] += int(envia and not critico)
        if "produto_sem_rastro" in nota:
            sem_rastro[1] += 1
            sem_rastro[0] += int(bool(nota["produto_sem_rastro"]))
        confianca = (reg.get("proposta") or {}).get("confianca")
        if isinstance(confianca, int | float):
            for (de, ate), faixa in zip(FAIXAS_CONFIANCA, calibracao.values(), strict=True):
                if de <= confianca < ate:
                    faixa[0] += int(envia)
                    faixa[1] += 1
    custos = sorted(
        float(r.get("custo_usd") or 0.0) for r in validos.values() if not r.get("pulado")
    )
    latencias = [int(r["ms"]) for r in validos.values() if isinstance(r.get("ms"), int)]
    versoes = Counter(
        str((r.get("decisao") or {}).get("prompt_versao"))
        for r in validos.values()
        if r.get("parada") == PARADA_LEGADO
    )
    return {
        "braco": braco,
        "n": len(validos),
        "sem_legado": sem_legado,
        "versoes_do_legado": dict(versoes.most_common()),
        "erros_de_execucao": len(resultados) - len(validos) - sem_legado,
        "notas": sum(1 for i in validos if i in notas),
        "erro_critico": erro_critico,
        "recall_acao_pessoa": _par(*recall),
        "acao_pessoa_correta": _par(*acao_correta),
        "evitavel": _par(*evitavel),
        "enviavel": _par(*enviavel),
        "orientacao": _par(*orientacao),
        "estoque": _par(*estoque),
        "produto_sem_rastro": _par(*sem_rastro),
        "produto_sem_rastro_pego_pelo_codigo": sem_rastro_codigo,
        "custo": {
            "media": sum(custos) / len(custos) if custos else None,
            "p50": _percentil(custos, 0.5) if custos else None,
            "p90": _percentil(custos, 0.9) if custos else None,
            "max": custos[-1] if custos else None,
            "total": round(sum(custos), 6),
        },
        "latencia_p95_ms": p95(latencias),
        "paradas": dict(
            Counter(
                r.get("parada") or r.get("pulado") or "?" for r in validos.values()
            ).most_common()
        ),
        "por_assunto": {k: dict(v) for k, v in sorted(por_assunto.items())},
        "calibracao": {k: _par(*v) for k, v in calibracao.items()},
        # Item 8 da rodada "redondo" (23/10/2026).
        "redondo": metricas_da_rodada(validos),
    }


def calcular(
    itens: Mapping[str, Mapping[str, Any]],
    resultados: Mapping[str, Mapping[str, Mapping[str, Any]]],
    chave: Mapping[str, Mapping[str, Any]],
    notas: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """As métricas de todos os braços, do controle, dos plantados e da releitura. PURA.

    `itens` = id → item da amostra; `resultados` = braço → id → registro;
    `chave` = id escondido → {item, braço}; `notas` = as linhas das notas.
    """
    por_braco: dict[str, dict[str, dict]] = defaultdict(dict)
    todas_do_item: dict[str, list[dict]] = defaultdict(list)
    repetidas: dict[tuple[str, str], list[dict]] = defaultdict(list)
    controle = [0, 0]
    plantados = [0, 0]
    plantados_por_tipo: Counter[str] = Counter()
    for nota in notas:
        alvo = chave.get(str(nota.get("id")))
        if alvo is None:
            continue
        item, braco = str(alvo["item"]), str(alvo["braco"])
        todas_do_item[item].append(dict(nota))
        repetidas[(item, braco)].append(dict(nota))
        if braco == BRACO_EQUIPE:
            controle[1] += 1
            controle[0] += int(bool(nota.get("enviaria_sem_editar")))
        elif braco.startswith(PREFIXO_PLANTADO):
            plantados[1] += 1
            pego = bool(nota.get("erro_critico"))
            plantados[0] += int(pego)
            if not pego:
                plantados_por_tipo[braco.removeprefix(PREFIXO_PLANTADO)] += 1
        else:
            por_braco[braco].setdefault(item, dict(nota))
    rotulos = {iid: _rotulo(item, todas_do_item.get(iid, [])) for iid, item in itens.items()}
    bracos = {
        b: _metricas_do_braco(b, itens, regs, por_braco.get(b, {}), rotulos)
        for b, regs in sorted(resultados.items())
    }
    concordancia = {"pares": 0, "enviaria": 0, "erro_critico": 0}
    for (_item, braco), lista in repetidas.items():
        if len(lista) < 2 or braco.startswith(PREFIXO_PLANTADO):
            continue
        a, b = lista[0], lista[1]
        concordancia["pares"] += 1
        concordancia["enviaria"] += int(
            bool(a.get("enviaria_sem_editar")) == bool(b.get("enviaria_sem_editar"))
        )
        concordancia["erro_critico"] += int(
            bool(a.get("erro_critico")) == bool(b.get("erro_critico"))
        )
    return {
        "bracos": bracos,
        "comparacao": comparar(por_braco),
        "comparacao_por_assunto": comparar_por_assunto(por_braco, resultados),
        "controle": {BRACO_EQUIPE: _par(*controle)},
        "plantados": _par(*plantados),
        "plantados_escapados": dict(plantados_por_tipo),
        "concordancia": concordancia,
        "sem_rotulo": sum(1 for r in rotulos.values() if not r),
    }


def comparar(por_braco: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, dict]:
    """Pareada contra o braço-base (A2, ou o 1º): nos itens com nota nos dois, quem
    "enviaria" onde o outro não."""
    if not por_braco:
        return {}
    base = BRACO_BASE if BRACO_BASE in por_braco else sorted(por_braco)[0]
    saida: dict[str, dict] = {}
    for braco, notas in sorted(por_braco.items()):
        if braco == base:
            continue
        comuns = set(notas) & set(por_braco[base])
        ganha = sum(
            1
            for i in comuns
            if notas[i].get("enviaria_sem_editar")
            and not por_braco[base][i].get("enviaria_sem_editar")
        )
        perde = sum(
            1
            for i in comuns
            if por_braco[base][i].get("enviaria_sem_editar")
            and not notas[i].get("enviaria_sem_editar")
        )
        saida[braco] = {"contra": base, "itens": len(comuns), "ganha": ganha, "perde": perde}
    return saida


def comparar_por_assunto(
    por_braco: Mapping[str, Mapping[str, Mapping[str, Any]]],
    resultados: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, dict[str, dict]]:
    """A comparação pareada de `comparar`, por ASSUNTO (o da resposta do
    braço-base): a decisão 8 libera o agente assunto a assunto, onde ele
    empata ou ganha do legado. PURA. braço → assunto → {itens, ganha, perde}."""
    if not por_braco:
        return {}
    base = BRACO_BASE if BRACO_BASE in por_braco else sorted(por_braco)[0]
    regs_base = resultados.get(base, {})
    saida: dict[str, dict[str, dict]] = {}
    for braco, notas in sorted(por_braco.items()):
        if braco == base:
            continue
        por_assunto: dict[str, dict[str, int]] = defaultdict(
            lambda: {"itens": 0, "ganha": 0, "perde": 0}
        )
        for i in sorted(set(notas) & set(por_braco[base])):
            a = por_assunto[_assunto(regs_base.get(i, {}))]
            este = bool(notas[i].get("enviaria_sem_editar"))
            do_base = bool(por_braco[base][i].get("enviaria_sem_editar"))
            a["itens"] += 1
            a["ganha"] += int(este and not do_base)
            a["perde"] += int(do_base and not este)
        saida[braco] = {k: dict(v) for k, v in sorted(por_assunto.items())}
    return saida


def _pct(par: tuple[int, int]) -> str:
    taxa = _taxa(par)
    return f"{par[0]}/{par[1]} ({taxa:.0%})" if taxa is not None else "—"


def _ok(passou: bool | None) -> str:
    return "—" if passou is None else ("OK" if passou else "FALHA")


def barras(m: Mapping[str, Any]) -> list[tuple[str, str, str, bool | None]]:
    """(métrica, valor, barra, passou) do braço — a tabela da spec 9.4. PURA."""

    def taxa(chave: str) -> float | None:
        return _taxa(m[chave])

    recall, evitavel, enviavel = taxa("recall_acao_pessoa"), taxa("evitavel"), taxa("enviavel")
    orientacao, estoque, rastro = taxa("orientacao"), taxa("estoque"), taxa("produto_sem_rastro")
    custo = m["custo"]["media"]
    lat = m["latencia_p95_ms"]
    return [
        ("Erro crítico", str(m["erro_critico"]), "0", m["erro_critico"] <= BARRA_ERRO_CRITICO),
        (
            "Recall de ação de pessoa",
            _pct(m["recall_acao_pessoa"]),
            "100%",
            None if recall is None else recall >= BARRA_RECALL,
        ),
        (
            "Encaminhamento evitável",
            _pct(m["evitavel"]),
            "≤ 5%",
            None if evitavel is None else evitavel <= BARRA_EVITAVEL,
        ),
        (
            "Enviável sem editar",
            _pct(m["enviavel"]),
            "≥ 75%",
            None if enviavel is None else enviavel >= BARRA_ENVIAVEL,
        ),
        (
            "Orientação correta",
            _pct(m["orientacao"]),
            "≥ 90%",
            None if orientacao is None else orientacao >= BARRA_ORIENTACAO,
        ),
        (
            "Estoque e variação resolvidos",
            _pct(m["estoque"]),
            "≥ 85%",
            None if estoque is None else estoque >= BARRA_ESTOQUE,
        ),
        (
            "Afirmação de produto sem rastro",
            _pct(m["produto_sem_rastro"]),
            "≤ 2%",
            None if rastro is None else rastro <= BARRA_PRODUTO_SEM_RASTRO,
        ),
        (
            "Custo por resposta",
            "—" if custo is None else f"US$ {custo:.4f}",
            "≤ US$ 0,005",
            None if custo is None else custo <= BARRA_CUSTO_USD,
        ),
        (
            "Latência p95",
            "—" if lat is None else f"{lat / 1000:.1f} s",
            "≤ 45 s",
            None if lat is None else lat <= BARRA_LATENCIA_MS,
        ),
    ]


def imprimir(m: Mapping[str, Any], *, eco: TextIO | None = None) -> None:
    eco = eco or sys.stdout
    for braco, b in m["bracos"].items():
        print(f"\n== Braço {braco}: {b['n']} itens, {b['notas']} com nota ==", file=eco)
        for nome, valor, barra, passou in barras(b):
            print(f"  {nome:<34} {valor:>18}   barra {barra:<12} {_ok(passou)}", file=eco)
        c = b["custo"]
        if c["media"] is not None:
            print(
                f"  custo: média US$ {c['media']:.4f} · p50 {c['p50']:.4f} · p90 {c['p90']:.4f}"
                f" · máx {c['max']:.4f} · total US$ {c['total']:.4f}",
                file=eco,
            )
        print(f"  ação de pessoa certa (nota): {_pct(b['acao_pessoa_correta'])}", file=eco)
        print(
            f"  produto sem fonte pego pelo código: {b['produto_sem_rastro_pego_pelo_codigo']}",
            file=eco,
        )
        print(f"  paradas: {b['paradas']}", file=eco)
        rd = b.get("redondo") or {}
        if rd.get("n"):
            # Item 8 da rodada "redondo" (23/10/2026): o que o Sonnet e o COMO PENSAR movem.
            ms50, ms90 = rd["latencia_p50_ms"], rd["latencia_p90_ms"]
            dado = _pct(rd["dado_indisponivel_do_modelo"])
            print(
                f"  rodada redondo: dado_indisponivel do modelo {dado}"
                f" · assunto não pedido {_pct(rd['assunto_nao_pedido'])} · G1 {_pct(rd['g1'])}"
                f" · pessoa {_pct(rd['pessoa'])} · apto ao automático {_pct(rd['apto_auto'])}"
                f" · enviaria {_pct(rd['enviaria'])}",
                file=eco,
            )
            print(
                "  ferramentas (itens que usaram): "
                + ", ".join(f"{k} {_pct(v)}" for k, v in rd["ferramentas"].items())
                + (f" · turnos {rd['turnos_media']:.1f}" if rd["turnos_media"] is not None else "")
                + (f" · latência p50 {ms50} ms · p90 {ms90} ms" if ms50 is not None else ""),
                file=eco,
            )
        if b["sem_legado"] or b["versoes_do_legado"]:
            # O L0: quantos itens têm a sugestão do legado e de qual versão.
            print(
                f"  legado: {b['n']} comparáveis, {b['sem_legado']} sem a sugestão do legado; "
                f"versões do prompt: {b['versoes_do_legado']}",
                file=eco,
            )
        print("  por assunto (n · com nota · enviaria · pessoa · erro crítico):", file=eco)
        for assunto, a in b["por_assunto"].items():
            print(
                f"    {assunto:<28} {a['n']:>4} · {a['notas']:>4} · {a['enviaria']:>4}"
                f" · {a['pessoa']:>4} · {a['erro_critico']:>3}",
                file=eco,
            )
        print(f"  calibração da confiança (enviaria/notas): {b['calibracao']}", file=eco)
    for braco, comp in m["comparacao"].items():
        print(
            f"\nPareado {braco} × {comp['contra']}: {comp['itens']} itens, "
            f"ganha {comp['ganha']}, perde {comp['perde']}",
            file=eco,
        )
        for assunto, a in (m.get("comparacao_por_assunto") or {}).get(braco, {}).items():
            print(
                f"    {assunto:<28} {a['itens']:>4} itens · ganha {a['ganha']} · "
                f"perde {a['perde']}",
                file=eco,
            )
    print(
        f"\nControle (a equipe como braço): enviaria {_pct(m['controle'][BRACO_EQUIPE])}", file=eco
    )
    pl = m["plantados"]
    print(
        f"Plantados pegos: {_pct(pl)} — barra todos {_ok(pl[0] == pl[1] if pl[1] else None)}",
        file=eco,
    )
    if m["plantados_escapados"]:
        print(f"  escaparam: {m['plantados_escapados']}", file=eco)
    c = m["concordancia"]
    if c["pares"]:
        enviaria = c["enviaria"] / c["pares"]
        critico = c["erro_critico"] / c["pares"]
        print(
            f"Releitura: {c['pares']} pares · enviaria {enviaria:.0%} "
            f"({_ok(enviaria >= BARRA_CONCORDANCIA_ENVIARIA)}) · erro crítico {critico:.0%} "
            f"({_ok(critico >= 1.0)})",
            file=eco,
        )
    if m["sem_rotulo"]:
        print(f"Itens sem rótulo (fora do recall e do evitável): {m['sem_rotulo']}", file=eco)


def ler_tudo(
    amostra: Path, arquivos_resultados: Sequence[Path], chave: Path, arquivos_notas: Sequence[Path]
) -> tuple[dict, dict, dict, list[dict]]:
    itens = {str(i["id"]): i for i in _ler_jsonl(amostra)}
    resultados: dict[str, dict[str, dict]] = defaultdict(dict)
    for caminho in arquivos_resultados:
        for reg in _ler_jsonl(caminho):
            braco = str(reg.get("braco") or caminho.stem.removeprefix("resultados_"))
            resultados[braco][str(reg["id"])] = reg
    notas = [n for caminho in arquivos_notas for n in _ler_jsonl(caminho)]
    return itens, dict(resultados), json.loads(chave.read_text(encoding="utf-8")), notas


def principal(argv: list[str] | None = None, *, eco: TextIO | None = None) -> int:
    p = argparse.ArgumentParser(description="O relatório do eval do agente (spec 9.4).")
    p.add_argument("--amostra", type=Path, required=True)
    p.add_argument("--resultados", type=Path, nargs="+", required=True)
    p.add_argument("--chave", type=Path, required=True)
    p.add_argument("--notas", type=Path, nargs="+", required=True)
    p.add_argument("--json", action="store_true", help="as métricas em JSON")
    args = p.parse_args(argv)
    m = calcular(*ler_tudo(args.amostra, args.resultados, args.chave, args.notas))
    if args.json:
        print(json.dumps(m, ensure_ascii=False, indent=2, default=list), file=eco or sys.stdout)
    else:
        imprimir(m, eco=eco)
    return 0


def main(argv: list[str] | None = None) -> int:
    return principal(argv)


if __name__ == "__main__":
    sys.exit(main())
