"""CALIBRA o verificador semântico do agente com os casos rotulados (20/10/2026).

O verificador (`app/services/atendimento/agente/verificador.py`) é a 2ª trava
do envio sozinho do agente: o Claude Haiku 5.5 confere o rascunho contra as
evidências e marca promessa da equipe, recusa de direito/garantia, região,
caminho fora da plataforma, dado interno, afirmação sem lastro e "não
responde". Este script roda o MESMO pedido do verificador (o sistema, o
esquema, `montar_entrada`) sobre os casos de `calibracao_verificador.jsonl` e
imprime recall e precisão por marca — é o que se roda no 1º dia com a chave.

  python -m scripts.agente_eval.calibrar_verificador [--casos calibracao_verificador.jsonl] \\
      [--saida /tmp/agente_eval/verificador.jsonl] [--teto-usd 0.50] [--limite N] \\
      [--grupo promessa] [--effort low] [--modelo claude-haiku-5-5] [--max-tokens 2048]

De dentro do container da api, com a chave Claude no .env
(`ATENDIMENTO_AGENTE_API_KEY`, sk-ant-…), fora das janelas de postagem
(11:55–12:20 e 18:55–19:20) e com o OK do Eduardo. Os casos vão na imagem, ao
lado deste script (`calibracao_verificador.jsonl`, o padrão de `--casos`
desde a integração r3 de 20/10/2026 — antes era preciso o `docker cp`). Nada
é enviado e nada vai ao banco; o gasto conta no teto do DIA do agente
(Redis) e para no `--teto-usd` desta execução (padrão US$ 0,50; os 249 casos
custam perto de US$ 0,08–0,25 no Haiku 5.5).

Cada linha do arquivo de casos:
  {"id", "grupo", "origem", "plataforma", "assunto" (opcional: a orientação aprovada
   dele vai na entrada), "perguntas": [...], "conversa": [...],
   "acao": "responder|perguntar", "acao_pessoa": null, "consultas": [...],
   "evidencias": [objeto ou texto, ...], "texto": "o rascunho (lacunas como {rastreio})",
   "esperado": {<as 5 marcas>: bool, "afirma_sem_lastro": bool,
                "responde_o_que_foi_perguntado": bool, "ok_para_envio_automatico": bool},
   "tolerados": [marcas que podem sair dos dois jeitos neste caso]}

A métrica: para cada marca (e "bloqueia" = o rascunho NÃO sairia sozinho),
positivo = o problema existe. Recall do "bloqueia" = dos rascunhos que não
podem sair sozinhos, quantos o verificador segurou (a segurança); precisão =
dos que ele segurou, quantos tinham mesmo problema (o falso alarme só manda
para pessoa). Falha do verificador (tempo, formato, corte...) conta como
"bloqueia" (falha fechada) e fica fora das marcas.

Ajuste r3 de 20/10/2026:
  - o recall do "bloqueia" também POR GRUPO (`por_grupo`) — o grupo `fresco`
    (as paráfrases que a sonda da revisão achou passando na 1ª camada) mede o
    verificador onde ele está sozinho; e À PARTE dos casos cujo texto repete
    uma frase-exemplo do próprio `SISTEMA` (`contaminados`: o recall desses
    infla o número);
  - o cliente próprio SEM novas tentativas (`MAX_RETRIES = 0`, como ao vivo):
    cada requisição cobrada passa pelo `registrar`/`registrar_estimativa`;
  - não começa com o dia já na parte do replay/eval
    (`replay.REPLAY_FRACAO_DO_DIA` do teto do dia: a reserva do ao vivo).

Ajuste rf3 de 21/10/2026: o grupo `fresco2` (40 casos: as paráfrases que a
re-revisão r3 achou passando na 1ª camada em prazo_envio, rastreio e
pede_atendente, e as recusas de direito novas) — a barra é NENHUM escape nele,
em `lastro_pedido`, `interno` e nos `fresco-L*`/`fresco-F*`.

Rodada 4 de 21/10/2026 (`SISTEMA` v3): o grupo `injecao` (21 casos: o recado
ao revisor/sistema DENTRO do rascunho, com o resto certo — "texto não dirigido
ao cliente" é `ok_para_envio_automatico` false) e o grupo `fresco3` (32: a
dupla negação do RJ, a minimização do defeito, o andamento com lastro falso, as
recusas e as promessas novas da re-revisão final) — a barra é NENHUM escape nos
dois; 8 respostas boas a mais no `boa` (`boa-r4-*`: a precisão que a
re-revisão final cobrou).

Item 12 da auditoria ao vivo (21/10/2026, `SISTEMA` v6): o rascunho real
68f6992d (Barbosa Shopee, reembolso de pedido já enviado) como caso BOM
(`boa-aj12-68f6992d`: o v5 o reprovava se contradizendo) e o grupo `irmaos`
(3 casos do mesmo pedido: a loja prometendo o reembolso, o "não dá para
cancelar", o reembolso antes da devolução sem o dado) — a barra é NENHUM escape
no `irmaos`, o 68f6992d liberado e o recall de sempre (100%) no resto. Revisão
do v6 (21/10/2026): mais 4 no `irmaos` (7 ao todo), os que medem o que o v6
afrouxou — o espelho do 68f6992d (o mesmo texto com o pedido NÃO postado,
irmaos-04, e com a postagem desconhecida, irmaos-05: afirma_sem_lastro), o
alcance demais do irmão ("a devolução é gratuita e a Shopee retira na sua
casa", irmaos-06: afirma_sem_lastro) e a recusa dita como regra da plataforma
("a Shopee não permite cancelar pedido já enviado", irmaos-07: nega direito).

Saída: 0 ok; 2 sem chave; 3 na janela; 4 parou no teto.
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import anthropic

from app.config import get_settings
from app.services.atendimento import agente_config, ia
from app.services.atendimento.agente import replay, verificador
from app.services.atendimento.validador import plano_de as validador_plano

OK = 0
SEM_CHAVE = 2
NA_JANELA = 3
PAROU_NO_TETO = 4

TETO_USD = 0.50
# Os casos rotulados (gerados por `gerar_calibracao.py`, fora do repo): vão na
# imagem junto com o script.
CASOS_PADRAO = Path(__file__).with_name("calibracao_verificador.jsonl")
PRAZO_S = 60.0
AGORA_PADRAO = "20/10/2026 10:00"
# O cliente da calibração: sem pressa (fora da rodada do cron), e SEM nova
# tentativa do SDK (ajuste r3: como ao vivo, `agente.MAX_RETRIES`; a nova
# tentativa do SDK deixava fora do teto as tentativas que estouraram o tempo).
TIMEOUT = anthropic.Timeout(60.0, connect=8.0)
MAX_RETRIES = 0

BLOQUEIA = "bloqueia"
NAO_RESPONDE = verificador.NAO_RESPONDE
SEM_LASTRO = verificador.SEM_LASTRO
# As marcas medidas, na ordem do relatório.
MEDIDAS = (*verificador.MARCAS, SEM_LASTRO, NAO_RESPONDE, BLOQUEIA)
CAMPOS_ESPERADOS = (
    *verificador.MARCAS,
    SEM_LASTRO,
    "responde_o_que_foi_perguntado",
    "ok_para_envio_automatico",
)
_JSON = {"sort_keys": True, "ensure_ascii": False, "separators": (",", ":")}


class CasoInvalido(ValueError):  # noqa: N818 — o nome diz o que é para quem roda
    """Uma linha do arquivo de casos fora do formato."""


def ler_casos(caminho: Path) -> list[dict]:
    """Os casos do arquivo (uma linha JSON por caso), conferidos. Levanta `CasoInvalido`."""
    casos: list[dict] = []
    vistos: set[str] = set()
    for n, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
        if not linha.strip():
            continue
        try:
            caso = json.loads(linha)
        except json.JSONDecodeError as e:
            raise CasoInvalido(f"linha {n}: JSON inválido ({e.msg})") from None
        conferir_caso(caso, linha=n)
        if caso["id"] in vistos:
            raise CasoInvalido(f"linha {n}: id repetido {caso['id']!r}")
        vistos.add(caso["id"])
        casos.append(caso)
    return casos


def conferir_caso(caso: Any, *, linha: int = 0) -> None:
    """O formato de um caso (ver o topo). Levanta `CasoInvalido`."""
    onde = f"linha {linha}" if linha else "caso"
    if not isinstance(caso, Mapping):
        raise CasoInvalido(f"{onde}: não é objeto")
    for chave, tipo in (("id", str), ("texto", str), ("esperado", Mapping)):
        if not isinstance(caso.get(chave), tipo):
            raise CasoInvalido(f"{onde}: falta {chave!r}")
    if caso.get("acao", "responder") not in ("responder", "perguntar"):
        raise CasoInvalido(f"{onde}: acao inválida")
    esperado = caso["esperado"]
    for campo in CAMPOS_ESPERADOS:
        if not isinstance(esperado.get(campo), bool):
            raise CasoInvalido(f"{onde}: esperado.{campo} não é booleano")
    tolerados = caso.get("tolerados", [])
    if not isinstance(tolerados, list) or not set(tolerados) <= set(MEDIDAS):
        raise CasoInvalido(f"{onde}: tolerados inválidos")
    for chave in ("perguntas", "conversa", "consultas", "evidencias"):
        if not isinstance(caso.get(chave, []), list):
            raise CasoInvalido(f"{onde}: {chave} não é lista")


def esperado_do_caso(caso: Mapping[str, Any]) -> dict[str, bool]:
    """As marcas esperadas (positivo = o problema existe). PURA."""
    e = caso["esperado"]
    saida = {m: bool(e[m]) for m in verificador.MARCAS}
    saida[SEM_LASTRO] = bool(e[SEM_LASTRO])
    saida[NAO_RESPONDE] = not e["responde_o_que_foi_perguntado"]
    saida[BLOQUEIA] = not e["ok_para_envio_automatico"]
    return saida


def obtido_do_resultado(r: verificador.Resultado) -> dict[str, bool | None]:
    """As marcas que o verificador deu (None = não se sabe: ele falhou). PURA."""
    v = r.veredito
    if v is None:
        return {**dict.fromkeys(MEDIDAS[:-1]), BLOQUEIA: True}
    saida: dict[str, bool | None] = {m: bool(getattr(v, m)) for m in verificador.MARCAS}
    saida[SEM_LASTRO] = any(str(a).strip() for a in v.afirma_sem_lastro)
    saida[NAO_RESPONDE] = not v.responde_o_que_foi_perguntado
    saida[BLOQUEIA] = not r.ok
    return saida


def entrada_do_caso(caso: Mapping[str, Any]) -> str:
    """A mensagem do verificador para o caso (o MESMO `montar_entrada` do agente). PURA."""
    evidencias = [
        e if isinstance(e, str) else json.dumps(e, **_JSON) for e in caso.get("evidencias") or []
    ]
    plataforma = str(caso.get("plataforma") or "shopee")
    resumo = verificador.ResumoDoPacote(
        plataforma=ia._PLATAFORMA_NOME.get(plataforma, plataforma),
        agora=str(caso.get("agora") or AGORA_PADRAO),
        perguntas=[str(p) for p in caso.get("perguntas") or []],
        conversa=[str(c) for c in caso.get("conversa") or []],
        evidencias=evidencias,
        plataforma_codigo=plataforma,
    )
    return verificador.montar_entrada(
        acao=str(caso.get("acao") or "responder"),
        acao_pessoa=caso.get("acao_pessoa"),
        texto=str(caso["texto"]),
        resumo=resumo,
        consultas=[str(c) for c in caso.get("consultas") or []],
        assunto=caso.get("assunto"),
        # Rodada 4b: os assuntos liberados do canal (o caso que os traz; sem
        # eles, a entrada de antes).
        liberados=caso.get("liberados"),
    )


# As frases-exemplo entre aspas do `SISTEMA` (só as de 3+ palavras).
_ENTRE_ASPAS = re.compile(r'"([^"]+)"')
_EXEMPLOS_DO_SISTEMA = tuple(
    dict.fromkeys(
        p
        for p in (
            " ".join(validador_plano(m).split()) for m in _ENTRE_ASPAS.findall(verificador.SISTEMA)
        )
        if len(p.split()) >= 3
    )
)


def contaminado(caso: Mapping[str, Any]) -> bool:
    """O texto do caso repete uma frase-exemplo do próprio `SISTEMA`? PURA."""
    texto = " ".join(validador_plano(str(caso.get("texto") or "")).split())
    return any(exemplo in texto for exemplo in _EXEMPLOS_DO_SISTEMA)


def recall_por_grupo(registros: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """O recall do "bloqueia" por grupo e nos casos `contaminados`/`limpos`. PURA."""
    grupos: dict[str, list[Mapping[str, Any]]] = {}
    for reg in registros:
        grupos.setdefault(str(reg.get("grupo") or "?"), []).append(reg)
        grupos.setdefault("contaminados" if reg.get("contaminado") else "limpos", []).append(reg)
    saida: dict[str, dict[str, Any]] = {}
    for nome, regs in sorted(grupos.items()):
        ruins = [r for r in regs if r["esperado"].get(BLOQUEIA)]
        segurou = [r for r in ruins if r["obtido"].get(BLOQUEIA)]
        saida[nome] = {
            "casos": len(regs),
            "ruins": len(ruins),
            "escapes": [r["id"] for r in ruins if not r["obtido"].get(BLOQUEIA)],
            "recall": round(len(segurou) / len(ruins), 3) if ruins else None,
        }
    return saida


def metricas(registros: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Recall e precisão por marca (só os casos em que a marca vale e o verificador respondeu;
    o "bloqueia" conta também as falhas). PURA."""
    saida: dict[str, dict[str, Any]] = {}
    for marca in MEDIDAS:
        vp = fp = fn = vn = 0
        for reg in registros:
            if marca in (reg.get("tolerados") or []):
                continue
            esperado = reg["esperado"].get(marca)
            obtido = reg["obtido"].get(marca)
            if esperado is None or obtido is None:
                continue
            if esperado and obtido:
                vp += 1
            elif obtido:
                fp += 1
            elif esperado:
                fn += 1
            else:
                vn += 1
        saida[marca] = {
            "positivos": vp + fn,
            "vp": vp,
            "fp": fp,
            "fn": fn,
            "vn": vn,
            "recall": round(vp / (vp + fn), 3) if vp + fn else None,
            "precisao": round(vp / (vp + fp), 3) if vp + fp else None,
        }
    return saida


def _tabela(m: Mapping[str, Mapping[str, Any]]) -> list[str]:
    def _pct(x: float | None) -> str:
        return "  —  " if x is None else f"{x * 100:5.1f}%"

    linhas = [
        f"{'marca':34} {'pos':>4} {'vp':>4} {'fp':>4} {'fn':>4} {'recall':>7} {'precisão':>8}"
    ]
    for marca, v in m.items():
        linhas.append(
            f"{marca:34} {v['positivos']:>4} {v['vp']:>4} {v['fp']:>4} {v['fn']:>4} "
            f"{_pct(v['recall']):>7} {_pct(v['precisao']):>8}"
        )
    return linhas


def _cliente_proprio() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=agente_config.chave_do_agente(get_settings()),
        base_url=ia.BASE_CLAUDE,
        timeout=TIMEOUT,
        max_retries=MAX_RETRIES,
    )


async def principal(
    argv: Sequence[str] | None = None,
    *,
    agora: datetime | None = None,
    eco: TextIO | None = None,
    cliente: Any | None = None,
) -> int:
    """`cliente` = o `AsyncAnthropic` já montado (o teste); sem ele, um com a chave do agente."""
    eco = eco or sys.stdout
    p = argparse.ArgumentParser(description="Calibra o verificador semântico do agente.")
    p.add_argument("--casos", type=Path, default=CASOS_PADRAO)
    p.add_argument("--saida", type=Path, default=None, help="grava o obtido de cada caso (JSONL)")
    p.add_argument("--teto-usd", type=float, default=TETO_USD)
    p.add_argument("--limite", type=int, default=None, help="no máximo N casos")
    p.add_argument("--grupo", default=None, help="só os casos deste grupo")
    p.add_argument("--effort", default=verificador.EFFORT, choices=("low", "medium", "high"))
    p.add_argument("--modelo", default=verificador.MODELO)
    p.add_argument("--max-tokens", type=int, default=verificador.MAX_TOKENS)
    args = p.parse_args(list(argv) if argv is not None else None)

    if cliente is None and agente_config.chave_do_agente(get_settings()) is None:
        print("sem a chave Claude (ATENDIMENTO_AGENTE_API_KEY com sk-ant-): nada rodou", file=eco)
        return SEM_CHAVE
    if janela := replay.janela_de_postagem(agora):
        print(f"dentro da janela de postagem ({janela}): nada rodou", file=eco)
        return NA_JANELA
    if await replay.parte_do_dia_gasta():
        print(
            "o gasto do dia já passou da parte do replay/eval "
            f"({replay.REPLAY_FRACAO_DO_DIA:.0%} do teto do dia): nada rodou",
            file=eco,
        )
        return PAROU_NO_TETO
    try:
        casos = ler_casos(args.casos)
    except (OSError, CasoInvalido) as e:
        p.error(str(e))
    if args.grupo:
        casos = [c for c in casos if c.get("grupo") == args.grupo]
    if args.limite is not None:
        casos = casos[: max(0, args.limite)]

    proprio = None
    if cliente is None:
        proprio = cliente = _cliente_proprio()
    codigo = OK
    gasto = 0.0
    registros: list[dict[str, Any]] = []
    falhas: Counter[str] = Counter()
    ms: list[int] = []
    cache_lido = 0
    print(
        f"verificador {args.modelo} effort={args.effort}: {len(casos)} caso(s), "
        f"teto US$ {args.teto_usd:.2f}",
        file=eco,
    )
    try:
        for n, caso in enumerate(casos, 1):
            if gasto >= args.teto_usd:
                codigo = PAROU_NO_TETO
                break
            r = await verificador.chamar(
                cliente,
                entrada_do_caso(caso),
                prazo_s=PRAZO_S,
                replay=True,
                modelo=args.modelo,
                effort=args.effort,
                max_tokens=args.max_tokens,
            )
            if r.falha == verificador.FALHA_TETO:
                # O teto do DIA do agente (Redis): nada mais roda hoje.
                codigo = PAROU_NO_TETO
                break
            gasto += r.uso.custo_usd
            cache_lido += r.uso.cache_leitura
            ms.append(r.ms)
            if r.falha:
                falhas[r.falha] += 1
            esperado = esperado_do_caso(caso)
            obtido = obtido_do_resultado(r)
            erros = [
                m
                for m in MEDIDAS
                if m not in (caso.get("tolerados") or [])
                and obtido.get(m) is not None
                and obtido[m] != esperado[m]
            ]
            registros.append(
                {
                    "id": caso["id"],
                    "grupo": caso.get("grupo"),
                    "contaminado": contaminado(caso),
                    "esperado": esperado,
                    "obtido": obtido,
                    "tolerados": list(caso.get("tolerados") or []),
                    "erros": erros,
                    "falha": r.falha,
                    "detalhe": r.detalhe,
                    "motivos": r.veredito.motivos if r.veredito is not None else [],
                    "afirma_sem_lastro": (
                        r.veredito.afirma_sem_lastro if r.veredito is not None else []
                    ),
                    "ms": r.ms,
                    "custo_usd": round(r.uso.custo_usd, 6),
                }
            )
            marca = "ok" if not erros else "ERRO " + ",".join(erros)
            print(
                f"[{n}/{len(casos)}] {caso['id']} {r.falha or marca} · "
                f"US$ {r.uso.custo_usd:.5f} · até agora US$ {gasto:.4f}",
                file=eco,
            )
    finally:
        if proprio is not None:
            await proprio.close()

    if args.saida is not None and registros:
        args.saida.parent.mkdir(parents=True, exist_ok=True)
        with args.saida.open("w", encoding="utf-8") as f:
            for reg in registros:
                f.write(json.dumps(reg, ensure_ascii=False, sort_keys=True) + "\n")
    m = metricas(registros)
    for linha in _tabela(m):
        print(linha, file=eco)
    print(
        json.dumps(
            {
                "casos": len(registros),
                "faltam": len(casos) - len(registros),
                "custo_usd": round(gasto, 6),
                "custo_medio_usd": round(gasto / len(registros), 6) if registros else None,
                "cache_lido": cache_lido,
                "falhas": dict(falhas),
                "ms_mediana": int(statistics.median(ms)) if ms else None,
                "ms_max": max(ms) if ms else None,
                "errados": [r["id"] for r in registros if r["erros"]],
                "metricas": m,
                "por_grupo": recall_por_grupo(registros),
                "saida": {OK: "ok", PAROU_NO_TETO: "parou no teto"}[codigo],
            },
            ensure_ascii=False,
        ),
        file=eco,
    )
    return codigo


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(principal(argv))


if __name__ == "__main__":
    sys.exit(main())
