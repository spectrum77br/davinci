"""CONGELA a régua de um canal num arquivo, para o `regatilho --config-canal-json` (03/11/2026).

O eval do Mercado Livre: nenhum canal do ML está no automático (todos em "humano"), então o
regatilho das conversas do ML usa as listas de um canal que está — as da Kia Shopee
(`auto_categorias`, `auto_orientacao`, `auto_agente`: todos os assuntos do agente), com o
`modo` "auto". A plataforma e o canal continuam os da conversa do ML (o regatilho os tira do
registro: o limite de 350 caracteres do pós-venda, a pergunta pública). Lido UMA vez, antes
dos dois braços, o arquivo entra no checksum das amostras: os dois braços decidem com a
MESMA régua, mesmo que alguém mexa no canal da Kia no meio do teste.

    uv run python -m scripts.agente_eval.config_canal \\
        --canal 1ca982c3-96e0-4000-95f2-e1fe368f954d --saida /out/config_ml.json

SÓ LEITURA (a sessão do replay). Saída: {"modo", "auto_categorias", "auto_orientacao",
"auto_agente", "de_canal", "modo_do_canal", "lido_em"} — o regatilho só lê os quatro primeiros.
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.atendimento import constantes
from scripts.agente_eval import regatilho

SEM_CANAL = 2


def congelar(
    canal: Mapping[str, Any], *, de_canal: str, modo: str | None, agora: datetime
) -> dict[str, Any]:
    """A régua do canal no formato do `--config-canal-json` (o `modo` trocado, se dado). PURA."""
    return {
        "modo": modo or canal.get("modo"),
        "auto_categorias": list(canal.get("auto_categorias") or []),
        "auto_orientacao": list(canal.get("auto_orientacao") or []),
        "auto_agente": list(canal.get("auto_agente") or []),
        "de_canal": de_canal,
        "modo_do_canal": canal.get("modo"),
        "lido_em": agora.isoformat(),
    }


async def principal(argv: Sequence[str] | None = None, *, agora: datetime | None = None) -> int:
    p = argparse.ArgumentParser(description="Congela a régua de um canal (só leitura).")
    p.add_argument("--canal", default=regatilho.KIA, help="o canal de onde vêm as listas")
    p.add_argument("--modo", default=constantes.MODO_AUTO, help="o modo do arquivo (auto)")
    p.add_argument("--saida", type=Path, required=True)
    a = p.parse_args(list(argv) if argv is not None else None)
    canal = await regatilho._canal_do_banco(a.canal)
    if canal is None:
        print(f"canal {a.canal} não encontrado: nada gravado", file=sys.stderr)
        return SEM_CANAL
    config = congelar(canal, de_canal=a.canal, modo=a.modo, agora=agora or datetime.now(UTC))
    a.saida.parent.mkdir(parents=True, exist_ok=True)
    a.saida.write_text(json.dumps(config, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
    print(
        json.dumps(
            {
                "saida": str(a.saida),
                "de_canal": a.canal,
                "modo": config["modo"],
                "modo_do_canal": config["modo_do_canal"],
                **{
                    k: len(config[k]) for k in ("auto_categorias", "auto_orientacao", "auto_agente")
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(principal(argv))


if __name__ == "__main__":
    sys.exit(main())
