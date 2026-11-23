"""Contatos externos do Threema na tabela do Cadastro (tarefa 16, C4)

Os contatos avulsos (quem recebe aviso sem login no DaVinci, ex.: "Roma")
moravam na linha `contexto='diretorio'` de `threema_informar_config`, no
formato `ID:Nome` do `.env`. Esta migration COPIA cada um para
`threema_contato` (origem `migracao`, ativo). A linha antiga FICA: o código
passa a reescrevê-la como espelho a cada gravação (services/threema), e é o
que vale se o código for revertido.

Mesma leitura do `parse_recipient_directory`: separa por vírgula (`;` vale
vírgula), só entra pedaço com `:`, ID em maiúsculas, nome vazio vira o ID, o
1º de cada ID vence. ID fora do formato do Threema não é copiado (o seletor
mostrava, mas nenhum envio chegava a ele). Nome cortado em 80.

Só dados: nenhuma tabela muda. Rodar de novo não duplica (o ID é único).

DOWNGRADE: apaga os contatos com origem `migracao` (a linha antiga continua
com eles). Os cadastrados na tela depois disto ficam em threema_contato (e no
espelho).

Revision ID: 0507_threema_contatos_copia
Revises: 0506_threema_cadastro
"""

# ruff: noqa: S608
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0507_threema_contatos_copia"
down_revision: str | None = "0506_threema_cadastro"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"
CONTEXTO = "diretorio"
ORIGEM = "migracao"
TAM_NOME = 80
# FIXO (mesmo de services/threema._THREEMA_ID; o teste confere).
THREEMA_ID_REGEX = r"^(?:[A-Z0-9]{8}|\*[A-Z0-9]{7})$"


def contatos_da_linha(recipients: str | None) -> list[tuple[str, str]]:
    """[(ID, nome)] da linha antiga, como o `parse_recipient_directory` lia
    (sem a parte dos IDs avulsos, que a linha não tinha)."""
    import re

    formato = re.compile(THREEMA_ID_REGEX)
    vistos: dict[str, str] = {}
    for parte in (recipients or "").replace(";", ",").split(","):
        parte = parte.strip()
        if not parte or ":" not in parte:
            continue
        rid, _, nome = parte.partition(":")
        rid = rid.strip().upper()
        nome = nome.strip()
        if rid and rid not in vistos:
            vistos[rid] = nome or rid
    return [(rid, nome[:TAM_NOME]) for rid, nome in vistos.items() if formato.match(rid)]


def upgrade() -> None:
    conn = op.get_bind()
    linha = conn.execute(
        sa.text(f"SELECT recipients FROM {SCHEMA}.threema_informar_config WHERE contexto = :c"),
        {"c": CONTEXTO},
    ).scalar_one_or_none()
    for rid, nome in contatos_da_linha(linha):
        conn.execute(
            sa.text(
                f"INSERT INTO {SCHEMA}.threema_contato (nome, threema_id, ativo, origem) "
                "VALUES (:nome, :rid, true, :origem) ON CONFLICT (threema_id) DO NOTHING"
            ),
            {"nome": nome, "rid": rid, "origem": ORIGEM},
        )


def downgrade() -> None:
    op.execute(
        sa.text(f"DELETE FROM {SCHEMA}.threema_contato WHERE origem = :o").bindparams(o=ORIGEM)
    )
