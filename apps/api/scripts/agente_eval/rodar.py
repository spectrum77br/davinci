"""RODA o eval do agente: a amostra num braço, com teto em dólar (spec 9, 20/10/2026).

LEIA-ME (curto) — o eval inteiro, de dentro do container da api no servidor
(`ssh davinci-prod "docker exec davinci-api-1 uv run python -m ..."`), FORA das
janelas de postagem (11:55–12:20 e 18:55–19:20, São Paulo), com a chave Claude
no .env (`ATENDIMENTO_AGENTE_API_KEY`, sk-ant-…) e com o OK do Eduardo (spec 12,
decisão 12). Nada é enviado nem vira rascunho; a sessão é só leitura.

  1. amostra   python -m scripts.agente_eval.amostra --saida /tmp/agente_eval/amostra.jsonl
                 [--loja kia|todas|<uuid>] [--dias 30] [--n 60] [--conjunto teste]
  2. aquecer   python -m scripts.agente_eval.preaquecer --amostra /tmp/agente_eval/amostra.jsonl
                 (a ÚNICA etapa que vai à loja, dentro das cotas; --sem-loja só confere o cache)
  3. rodar     python -m scripts.agente_eval.rodar --amostra /tmp/agente_eval/amostra.jsonl \\
                 --saida /tmp/agente_eval --braco A2 [--teto-usd 2.0]
               um braço por vez: A1 (Haiku low), A2 (Haiku medium), A3 (Haiku high), S1
               (Sonnet medium — o padrão ao vivo desde 23/10/2026); ablação:
               A2-sem-equipe, A2-sem-fatos, S1-sem-equipe, S1-sem-fatos. Grava
               resultados_<braço>.jsonl (mascarado) e RETOMA do que já está nele.
               --braco L0: a sugestão que o LEGADO escreveu para cada gatilho, lida
               do banco (sem modelo, sem custo, sem a chave Claude).
  4. exportar  python -m scripts.agente_eval.exportar --amostra /tmp/agente_eval/amostra.jsonl \\
                 --resultados /tmp/agente_eval/resultados_*.jsonl --saida /tmp/agente_eval/nota
               → para_nota.jsonl (às cegas, mascarado) e chave.json (NÃO abrir na sessão da nota)
  5. nota      copiar para o Mac mini e dar a nota numa sessão do Claude Code (decisão 4):
               ssh davinci-prod "docker exec davinci-api-1 cat \\
                 /tmp/agente_eval/nota/para_nota.jsonl" > para_nota.jsonl
               a sessão escreve notas.jsonl, uma linha por id:
               {"id", "enviaria_sem_editar", "erro_critico", "acao_pessoa_correta", "comentario"}
               (+ opcionais "rotulo" S|A1|A2|P|B|C, "orientacao_correta", "produto_sem_rastro")
  6. relatório python -m scripts.agente_eval.relatorio --amostra … --resultados … \\
                 --chave /tmp/agente_eval/nota/chave.json --notas notas.jsonl
               (só lê arquivos: roda no container ou no Mac)
  7. apagar    rm -rf /tmp/agente_eval (spec 9.3: a exportação sai depois do relatório)

Este script: recusa sem a chave e dentro da janela; roda 4 conversas por vez
(`--concorrencia`); para de começar item novo quando o gasto DESTA execução
chega ao `--teto-usd` (padrão US$ 2) ou quando o relógio entra numa janela
(o teto do DIA do agente, `atendimento_agente_teto_usd_dia`, também vale:
parada "teto"); imprime o custo até ali a cada item. `--gravar` também grava
em `atendimento_agente_replay` (só para UM braço: a linha é por gatilho e dia).
Saída: 0 ok; 2 sem chave; 3 na janela; 4 parou no teto; 5 parou na janela; 6 parou
no provedor (sem crédito ou no limite da API).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO
from uuid import UUID

from app.config import get_settings
from app.models import AtendimentoConversa
from app.services.atendimento import agente, agente_config
from app.services.atendimento.agente import custo, ferramentas, replay

OK = 0
SEM_CHAVE = 2
NA_JANELA = 3
PAROU_NO_TETO = 4
PAROU_NA_JANELA = 5
PAROU_NO_PROVEDOR = 6

TETO_USD = 2.0
SAIDA_PADRAO = Path("/tmp/agente_eval")  # noqa: S108 — dentro do container

# Os braços da spec 9.1 que este script roda. O L0 (o legado como ele
# escreveu, lido do banco: `replay.rascunho_do_legado`) não chama modelo; o L1
# (o legado com o Haiku) não existe aqui.
BRACO_LEGADO = "L0"
MODELO_SONNET = "claude-sonnet-5-5"
# Item 5 da rodada "redondo" (23/10/2026): o padrão do agente virou o Sonnet 5.5 — os
# braços A continuam o HAIKU (a comparação de antes), com o modelo explícito.
MODELO_HAIKU = "claude-haiku-5-5"
# O braço Sonnet custa ~10× o Haiku: o teto da CONVERSA dele é outro (a 1ª
# requisição com o cache frio já passava dos US$ 0,05 do Haiku; revisão de
# 20/10/2026). O replay/eval soma na chave PRÓPRIA da conversa
# (`custo.CHAVE_CONVERSA_REPLAY`): os braços não comem o teto da conversa ao
# vivo; o DIA é um só.
TETO_CONVERSA_SONNET_USD = 0.5
BRACOS: dict[str, dict[str, Any]] = {
    "A1": {"modelo": MODELO_HAIKU, "effort": "low"},
    "A2": {"modelo": MODELO_HAIKU, "effort": "medium"},
    "A3": {"modelo": MODELO_HAIKU, "effort": "high"},
    "S1": {
        "modelo": MODELO_SONNET,
        "effort": "medium",
        "teto_conversa_usd": TETO_CONVERSA_SONNET_USD,
    },
}
# Ablações (spec 9.1): sem os exemplos da equipe; sem os fatos novos.
SUFIXOS: dict[str, dict[str, Any]] = {
    "-sem-equipe": {"ferramentas_fora": frozenset({ferramentas.BUSCAR_RESPOSTAS_EQUIPE})},
    "-sem-fatos": {"sem_fatos": True},
}


def extra_do_braco(braco: str) -> dict[str, Any]:
    """O `ctx_extra` do braço ("A2", "A2-sem-equipe", "S1"...). Desconhecido → ValueError."""
    base, extra = braco, {}
    for sufixo, valores in SUFIXOS.items():
        if braco.endswith(sufixo):
            base, extra = braco[: -len(sufixo)], valores
            break
    if base not in BRACOS:
        raise ValueError(f"braço desconhecido: {braco!r} (use {', '.join(BRACOS)})")
    return {**BRACOS[base], **extra}


def arquivo_do_braco(saida: Path, braco: str) -> Path:
    return saida / f"resultados_{braco}.jsonl"


def ler_resultados(caminho: Path) -> list[dict]:
    if not caminho.exists():
        return []
    with caminho.open(encoding="utf-8") as f:
        return [json.loads(linha) for linha in f if linha.strip()]


def _ler_amostra(caminho: Path) -> list[dict]:
    with caminho.open(encoding="utf-8") as f:
        return [json.loads(linha) for linha in f if linha.strip()]


async def _um(
    item: dict, braco: str, extra: dict[str, Any], *, gravar: bool, modo: str | None
) -> dict:
    """Um item: o replay do gatilho (sessão só leitura) → o registro do JSONL."""
    registro: dict[str, Any] = {
        "id": item["id"],
        "braco": braco,
        "estrato": item.get("estrato"),
        "rotulo": item.get("rotulo"),
    }
    async with replay.sessao_replay() as s:
        conversa = await s.get(AtendimentoConversa, UUID(item["conversa_id"]))
        if conversa is None:
            return {**registro, "erro": "conversa_sumiu"}
        if braco == BRACO_LEGADO:
            r = await replay.rascunho_do_legado(
                s,
                conversa,
                datetime.fromisoformat(item["gatilho_em"]),
                gatilho_id=UUID(item["gatilho_id"]),
            )
            return {**r.para_jsonl(), **registro}
        r = await replay.rodar_gatilho(
            s,
            conversa,
            datetime.fromisoformat(item["gatilho_em"]),
            gravar=gravar,
            gatilho_id=UUID(item["gatilho_id"]),
            extra=extra,
            modo=modo,
        )
    return {**r.para_jsonl(), **registro}


async def principal(
    argv: Sequence[str] | None = None,
    *,
    agora: datetime | None = None,
    eco: TextIO | None = None,
) -> int:
    eco = eco or sys.stdout
    p = argparse.ArgumentParser(description="Roda o eval do agente num braço (sem enviar nada).")
    p.add_argument("--amostra", type=Path, required=True)
    p.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    p.add_argument("--braco", default="A2")
    p.add_argument("--teto-usd", type=float, default=TETO_USD)
    p.add_argument("--concorrencia", type=int, default=replay.CONCORRENCIA)
    p.add_argument("--conjunto", choices=("dev", "teste", "todos"), default="todos")
    p.add_argument("--limite", type=int, default=None, help="no máximo N itens nesta execução")
    p.add_argument(
        "--modo",
        choices=("auto", "copiloto"),
        default=None,
        help="simula o canal neste modo (padrão: o modo de hoje do canal)",
    )
    p.add_argument("--gravar", action="store_true", help="também em atendimento_agente_replay")
    args = p.parse_args(list(argv) if argv is not None else None)
    legado = args.braco == BRACO_LEGADO
    extra: dict[str, Any] = {}
    if not legado:
        try:
            extra = extra_do_braco(args.braco)
        except ValueError as e:
            p.error(str(e))

    if not legado and agente_config.chave_do_agente(get_settings()) is None:
        print("sem a chave Claude (ATENDIMENTO_AGENTE_API_KEY com sk-ant-): nada rodou", file=eco)
        return SEM_CHAVE
    if janela := replay.janela_de_postagem(agora):
        print(f"dentro da janela de postagem ({janela}): nada rodou", file=eco)
        return NA_JANELA

    itens = _ler_amostra(args.amostra)
    if args.conjunto != "todos":
        itens = [i for i in itens if i.get("conjunto") == args.conjunto]
    destino = arquivo_do_braco(args.saida, args.braco)
    feitos = {r["id"] for r in ler_resultados(destino)}
    fila = [i for i in itens if i["id"] not in feitos]
    if args.limite is not None:
        fila = fila[: max(0, args.limite)]
    destino.parent.mkdir(parents=True, exist_ok=True)

    estado: dict[str, Any] = {
        "gasto": 0.0,
        "em_voo": 0,
        "feitos": 0,
        "codigo": OK,
        "paradas": Counter(),
    }
    # O teto da CONVERSA deste braço: a reserva de quem está no ar (ajuste r3:
    # com a concorrência, o `--teto-usd` só era conferido ANTES de cada item e
    # a execução passava dele — no S1, até ~US$ 1,5).
    teto_da_conversa = float(
        extra.get("teto_conversa_usd") or get_settings().atendimento_agente_teto_usd_conversa or 0
    )
    trava = asyncio.Semaphore(max(1, args.concorrencia))
    escrita = asyncio.Lock()
    total = len(fila)
    print(
        f"braço {args.braco}: {total} item(ns) a rodar ({len(feitos)} já no arquivo), "
        f"teto US$ {args.teto_usd:.2f}",
        file=eco,
    )

    async def _rodar(item: dict) -> None:
        async with trava:
            if estado["codigo"] != OK:
                return
            reserva = 0.0 if legado else (estado["em_voo"] + 1) * teto_da_conversa
            if estado["gasto"] >= args.teto_usd or (
                reserva and estado["gasto"] + reserva > args.teto_usd
            ):
                estado["codigo"] = PAROU_NO_TETO
                return
            if replay.janela_de_postagem():
                estado["codigo"] = PAROU_NA_JANELA
                return
            if not legado and await replay.parte_do_dia_gasta():
                # A reserva do agente ao vivo (`replay.REPLAY_FRACAO_DO_DIA`).
                estado["codigo"] = PAROU_NO_TETO
                return
            estado["em_voo"] += 1
            try:
                registro = await _um(item, args.braco, extra, gravar=args.gravar, modo=args.modo)
            except Exception as e:  # noqa: BLE001 — o item fica com o erro; o eval segue
                # O gasto do item com erro não volta: conta o teto da conversa
                # (pelo alto; ajuste r3).
                registro = {
                    "id": item["id"],
                    "braco": args.braco,
                    "erro": type(e).__name__,
                    "custo_usd": 0.0 if legado else teto_da_conversa,
                }
            finally:
                estado["em_voo"] -= 1
            if (
                registro.get("parada") == agente.PARADA_TETO
                and registro.get("teto_qual") != custo.TETO_CONVERSA
            ):
                # O teto do DIA do agente (Redis): nada mais roda hoje — e o
                # item NÃO vai ao arquivo (a próxima execução o roda de novo).
                # A resposta que estourou já foi paga: soma (ajuste r3).
                estado["gasto"] += float(registro.get("custo_usd") or 0.0)
                estado["codigo"] = PAROU_NO_TETO
                return
            async with escrita:
                with destino.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(registro, ensure_ascii=False, sort_keys=True) + "\n")
                estado["gasto"] += float(registro.get("custo_usd") or 0.0)
                estado["feitos"] += 1
                parada = registro.get("parada") or registro.get("pulado") or registro.get("erro")
                estado["paradas"][parada] += 1
                print(
                    f"[{estado['feitos']}/{total}] {item['id']} {parada} "
                    f"US$ {float(registro.get('custo_usd') or 0.0):.4f} · "
                    f"até agora US$ {estado['gasto']:.4f} / {args.teto_usd:.2f}",
                    file=eco,
                )
                # O teto da CONVERSA só pula o item (ele fica no arquivo).
                erro = registro.get("erro_provedor") or {}
                if erro.get("sem_credito") or erro.get("limite"):
                    # Sem crédito ou no limite da API: os próximos também falhariam.
                    print(f"o provedor recusou ({erro.get('motivo')}): parando", file=eco)
                    estado["codigo"] = PAROU_NO_PROVEDOR

    await asyncio.gather(*(_rodar(i) for i in fila))
    print(
        json.dumps(
            {
                "braco": args.braco,
                "rodados": estado["feitos"],
                "faltam": total - estado["feitos"],
                "custo_usd": round(estado["gasto"], 6),
                "paradas": dict(estado["paradas"]),
                "arquivo": str(destino),
                "saida": {
                    OK: "ok",
                    PAROU_NO_TETO: "parou no teto",
                    PAROU_NA_JANELA: "parou na janela de postagem",
                    PAROU_NO_PROVEDOR: "parou no provedor (sem crédito ou limite)",
                }[estado["codigo"]],
            },
            ensure_ascii=False,
        ),
        file=eco,
    )
    return estado["codigo"]


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(principal(argv))


if __name__ == "__main__":
    sys.exit(main())
