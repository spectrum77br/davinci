"""Recupera a foto/PDF das mensagens da Amazon que entraram SÓ PELO NOME (23/11/2026).

Até 23/11/2026 o leitor da caixa da Amazon guardava só o nome do anexo
("image.jpg" na tela). A foto continua no e-mail, na caixa (o leitor nunca
move nem apaga nada): este script relê cada e-mail pelo UID guardado na
mensagem — SÓ LEITURA na caixa, EXAMINE + BODY.PEEK[], como o leitor —,
confere que é o MESMO e-mail (Message-ID) com os MESMOS anexos, grava a cópia
no disco e completa o anexo (`anexo_amazon.recuperar_antigos`). SÓ COM O OK
DO EDUARDO (regrava mensagens já gravadas em produção).

SECO POR PADRÃO: sem `--aplicar` lê a caixa e só conta o que faria (nada no
disco, nada no banco). Nenhuma mensagem sai para o comprador.

Uso (de dentro de apps/api, no servidor):
    python -m scripts.atendimento_amazon_anexos              # seco: só conta
    python -m scripts.atendimento_amazon_anexos --aplicar

A saída é o resumo em JSON: só contagens (nada de comprador).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import app.db as _db
from app.services.atendimento import anexo_amazon


async def _rodar(*, aplicar: bool, limite: int) -> dict:
    async with _db.SessionLocal() as session:
        return await anexo_amazon.recuperar_antigos(session, aplicar=aplicar, limite=limite)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--aplicar", action="store_true", help="grava (sem ele, só conta)")
    p.add_argument("--limite", type=int, default=200, help="no máximo N mensagens")
    args = p.parse_args(argv)
    resumo = asyncio.run(_rodar(aplicar=args.aplicar, limite=args.limite))
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
