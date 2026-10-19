"""Prévia/importação de leituras conferidas na fonte (nunca busca ou envia mensagens).

    python -m scripts.atendimento_notificacoes_migrar snapshot.json
    python -m scripts.atendimento_notificacoes_migrar snapshot.json --relatorio previa.json
    python -m scripts.atendimento_notificacoes_migrar snapshot.json --aplicar --hash-previsto HASH

A execução padrão usa transação READ ONLY e imprime apenas identificadores
de escopo/contagens. O hash da prévia inclui os marcos atuais de leitura;
leituras concorrentes exigem nova prévia. O arquivo de evidência não é
substituído por totais de loja ou nomes de comprador.
Se o sync ou uma conversa estiverem em uso, `escopo_ocupado` recusa a
aplicação; aguarde a operação terminar e refaça a prévia.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text

from app.db import session_scope
from app.services.atendimento import migrar_notificacoes as migracao

SAIDA_OK = 0
SAIDA_RECUSADO = 2


async def executar(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migra um snapshot completo de leitura para o comunicador."
    )
    parser.add_argument("arquivo", type=Path)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument(
        "--aplicar", action="store_true", help="grava somente após conferir o hash da prévia"
    )
    modo.add_argument("--seco", action="store_true", help="somente leitura (padrão)")
    parser.add_argument(
        "--hash-previsto", help="hash emitido pela prévia deste snapshot e estado de leitura"
    )
    parser.add_argument(
        "--relatorio", type=Path, help="salva o relatório JSON com permissão privada"
    )
    args = parser.parse_args(argv)
    try:
        snapshot = migracao.ler_snapshot(args.arquivo)
        if args.relatorio is not None:
            protegido = {args.arquivo.resolve(), snapshot.evidencia_arquivo.resolve()}
            if args.relatorio.resolve() in protegido or (
                args.relatorio.exists() and any(args.relatorio.samefile(p) for p in protegido)
            ):
                raise migracao.MigracaoRecusada(
                    "relatorio", "O relatório não pode substituir o manifesto ou a evidência."
                )
        async with session_scope() as session:
            if not args.aplicar:
                await session.execute(text("SET TRANSACTION READ ONLY"))
            relatorio = await migracao.migrar(
                session,
                snapshot,
                aplicar=args.aplicar,
                hash_previsto=args.hash_previsto,
            )
            corpo = json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n"
            # Escrever o relatório dentro da transação evita aplicar sem
            # conseguir entregar o resultado de uma gravação solicitada.
            if args.relatorio is not None:
                fd = os.open(args.relatorio, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
                    os.fchmod(arquivo.fileno(), 0o600)
                    arquivo.write(corpo)
        sys.stdout.write(corpo)
        return SAIDA_OK
    except migracao.MigracaoRecusada as exc:
        print(
            json.dumps(
                {"aplicado": False, "code": exc.code, "detail": exc.detail}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return SAIDA_RECUSADO
    except OSError:
        print(
            '{"aplicado": false, "code": "relatorio", "detail": "Falha ao gravar relatório."}',
            file=sys.stderr,
        )
        return SAIDA_RECUSADO


def main(argv: list[str] | None = None) -> None:
    sys.exit(asyncio.run(executar(argv)))


if __name__ == "__main__":
    main()
