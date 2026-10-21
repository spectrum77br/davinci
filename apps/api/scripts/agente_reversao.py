"""A REVERSÃO do automático do agente pela nota da auditoria (spec 8; 20/10/2026).

A rotina do Mac mini, depois de dar a nota ao dia (`agente_auditoria_export`
→ a sessão do Claude → notas.jsonl), manda os dois para cá: o assunto com 1
erro crítico, ou com a nota "enviaria sem editar" abaixo de 90% em 7 dias
(10+ notas), sai do `auto_orientacao`/`auto_agente` do canal e o Telegram
avisa. Só tira — volta pelo clique do dono.

Entrada: JSONL (arquivos ou `-` = a entrada padrão), as linhas da auditoria
(`"tipo": "envio_automatico"`) e as das notas (`id`, `enviaria_sem_editar`,
`erro_critico`) misturadas ou em arquivos separados.

Uso (do Mac, com o OK do Eduardo — GRAVA no canal):
    cat auditoria_*.jsonl notas_*.jsonl | ssh davinci-prod \\
        "docker exec -i davinci-api-1 uv run python -m scripts.agente_reversao - --dry-run"
    (sem --dry-run: tira os assuntos e avisa no Telegram)

  --dry-run   só diz o que tiraria (nada gravado, sem aviso).
  --dias N    a janela da nota (padrão 7).

A saída é o resumo em JSON (canal, assunto, motivo e contas; nada de texto).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import app.db as _db
from app.services.atendimento.agente import custo, reversao


def _linhas(caminhos: list[str]) -> list[dict[str, Any]]:
    saida: list[dict[str, Any]] = []
    for caminho in caminhos:
        texto = sys.stdin.read() if caminho == "-" else Path(caminho).read_text(encoding="utf-8")
        for linha in texto.splitlines():
            if not linha.strip():
                continue
            try:
                valor = json.loads(linha)
            except ValueError:
                continue
            if isinstance(valor, dict):
                saida.append(valor)
    return saida


async def rodar(linhas: list[dict[str, Any]], *, gravar: bool, dias: int) -> dict[str, Any]:
    registros = [x for x in linhas if x.get("tipo") == reversao.TIPO_ENVIO]
    notas = [x for x in linhas if "tipo" not in x and x.get("id")]
    decididas = reversao.decidir(registros, notas, agora=datetime.now(UTC), dias=dias)
    async with _db.session_scope() as session:
        feitas = await reversao.reverter(session, decididas, gravar=gravar)
        if not gravar:
            await session.rollback()
    if gravar and (texto := reversao.texto_do_alerta(feitas)):
        await custo.avisar_telegram(texto)
    return {
        "gravar": gravar,
        "registros": len(registros),
        "notas": len(notas),
        "assuntos": [f"{r.assunto} ({r.motivo})" for r in decididas],
        "canais": feitas,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("entrada", nargs="+", help="JSONL da auditoria e das notas (- = stdin)")
    parser.add_argument("--dry-run", action="store_true", help="só diz o que tiraria")
    parser.add_argument("--dias", type=int, default=reversao.JANELA_DIAS)
    args = parser.parse_args(argv)
    resumo = asyncio.run(
        rodar(_linhas(args.entrada), gravar=not args.dry_run, dias=max(1, args.dias))
    )
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
