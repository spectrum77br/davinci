"""Manual base da IA do atendimento: importar e exportar (parte 2, P7).

O manual base é um JSON escrito pela equipe (e revisado pelo Eduardo) com a
lista oficial de ASSUNTOS, as REGRAS "QUANDO → FAÇA" e as RESPOSTAS PRONTAS:

    {"categorias": [{"id", "nome", "descricao", "exemplos": [..], "so_humano": bool,
                     "lacunas": [..]}],
     "regras": [{"tipo", "categoria", "plataforma", "canal", "prioridade", "quando",
                 "faca"}],
     "respostas_prontas": [{"titulo", "texto", "plataforma", "canal", "categoria"}]}

`tipo` da regra: `seguranca` (vale sempre, vem primeiro no prompt),
`categoria` (só no assunto; sem categoria = geral) ou `estilo` (tom e
assinatura, por último). Plataforma/canal vazios = todas.

    importar — confere TUDO antes de gravar (formato, assunto que existe,
               plataforma/canal, e CONFLITO: duas regras de assunto para o
               mesmo assunto, plataforma e canal — no arquivo ou com as que
               já estão ativas no banco). Qualquer erro ou conflito: nada é
               gravado e o comando sai com código 1. Reimportar o mesmo
               arquivo não duplica nada; nada é apagado nem desativado.
    --seco   — só mostra o que faria (não grava, não trava).
    exportar — o manual ATIVO nesse mesmo formato (no arquivo dado ou na
               tela). Sem assunto no banco, sai a lista das constantes: é o
               ponto de partida para escrever o manual base.

Uso (de dentro de apps/api; em produção, dentro do container da api):
    uv run python -m scripts.atendimento_manual exportar manual.json
    uv run python -m scripts.atendimento_manual importar manual.json --seco
    uv run python -m scripts.atendimento_manual importar manual.json

O relatório mostra os textos do MANUAL (é da equipe), nunca de comprador.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.db import session_scope
from app.services.atendimento import manual

# Códigos de saída: 0 = ok; 1 = recusado (erro ou conflito no manual);
# 2 = arquivo ilegível.
SAIDA_OK = 0
SAIDA_RECUSADO = 1
SAIDA_ARQUIVO = 2


async def _importar(arquivo: Path, *, seco: bool) -> int:
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"Não consegui ler {arquivo}: {e}")
        return SAIDA_ARQUIVO
    async with session_scope() as session:
        relatorio = await manual.importar_manual(session, dados, seco=seco)
        # Recusado: nada foi gravado, mas desfaz explicitamente — o commit
        # do `session_scope` não tem o que levar.
        if not relatorio.ok:
            await session.rollback()
    for linha in relatorio.linhas():
        print(linha)
    if not relatorio.ok:
        print("RECUSADO: nada foi gravado. Corrija o arquivo (ou desative na tela a regra "
              "que bate) e rode de novo.")
        return SAIDA_RECUSADO
    print("(seco) nada foi gravado." if seco else "Gravado.")
    return SAIDA_OK


async def _exportar(arquivo: Path | None) -> int:
    async with session_scope() as session:
        dados = await manual.exportar_manual(session)
    corpo = json.dumps(dados, ensure_ascii=False, indent=2) + "\n"
    if arquivo is None:
        sys.stdout.write(corpo)
    else:
        arquivo.write_text(corpo, encoding="utf-8")
        print(
            f"Exportado para {arquivo}: {len(dados['categorias'])} assuntos, "
            f"{len(dados['regras'])} regras, {len(dados['respostas_prontas'])} respostas prontas."
        )
    return SAIDA_OK


async def executar(argv: list[str] | None = None) -> int:
    """O comando inteiro, sem sair do processo (o teste chama direto)."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.atendimento_manual",
        description="Importa/exporta o manual base da IA do atendimento.",
    )
    sub = parser.add_subparsers(dest="comando", required=True)
    imp = sub.add_parser("importar", help="importa um manual base (JSON)")
    imp.add_argument("arquivo", type=Path)
    imp.add_argument("--seco", action="store_true", help="só mostra o que faria")
    exp = sub.add_parser("exportar", help="exporta o manual ativo (JSON)")
    exp.add_argument("arquivo", type=Path, nargs="?")
    args = parser.parse_args(argv)
    if args.comando == "importar":
        return await _importar(args.arquivo, seco=args.seco)
    return await _exportar(args.arquivo)


def main(argv: list[str] | None = None) -> None:
    sys.exit(asyncio.run(executar(argv)))


if __name__ == "__main__":
    main()
