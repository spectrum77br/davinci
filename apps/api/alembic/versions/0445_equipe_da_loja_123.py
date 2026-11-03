"""equipe da loja 1/2/3 = Londres/Israel/Marrocos (commercial_team ← sales_team)

Eduardo, 03/11/2026: "em lojas as equipes está tudo com 1, sendo que a equipe
1 é o londres, corrija para mim, equipe 2 é israel, equipe 3 é marrocos". E,
perguntado, escolheu que trocar a equipe na tela de Lojas troca também quem vê
a loja: a "Equipe" de Lojas (`commercial_team` 1/2/3) e a equipe de vendas
(`sales_team` 101/102/103) passam a ser uma coisa só — a API grava as duas
juntas (`team_scope.alinhar_equipes_da_loja`).

* O CHECK de `commercial_team` (store_info e users) passa a aceitar 3.
* store_info: `commercial_team = sales_team - 100` em toda loja das três
  equipes (101→1, 102→2, 103→3), arquivadas inclusive. Em 03/11 eram 83: 81
  com 1 e 2 sem número (as da lista `SEM_NUMERO_EM_0311`). Loja sem equipe de
  vendas (internas, makisa, agil) não muda.
* users, pelo id (conferindo o nome e o valor lido em 03/11): israel 1→2,
  marrocos NULL→3, cairo e cairo sa 2→NULL (para não aparecerem como Israel;
  decisão do Eduardo). No usuário a equipe continua só organização: nenhum
  acesso muda.

Se um usuário da lista não existe, tem outro nome ou está num valor que não é
nem o de 03/11 nem o novo, a migration para inteira e nada muda.

DOWNGRADE: as lojas voltam ao retrato de 03/11 (1; NULL nas duas da lista) só
onde o número ainda é o que a 0445 gravaria (sales_team − 100); os usuários
voltam só se ainda têm o valor novo. Depois o CHECK volta a (1, 2): se sobrou
algum 3 (loja ou usuário mexido à mão), para com a mensagem e nada muda.

Revision ID: 0445_equipe_da_loja_123
Revises: 0444_tipo_conteudo_historia_produto
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0445_equipe_da_loja_123"
down_revision: str | None = "0444_tipo_conteudo_historia_produto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

# Lojas com equipe de vendas e SEM número em 03/11 (o downgrade as devolve a NULL).
SEM_NUMERO_EM_0311: tuple[str, ...] = (
    "5fd84133-5a17-4785-ba40-04e486b4638a",  # mercadolivre atv (102, arquivada)
    "de3a4944-d80f-4df9-ae58-088fc3e57d17",  # presencial venda presencial (103)
)

# (id, nome, commercial_team em 03/11, novo).
USUARIOS: tuple[tuple[str, str, int | None, int | None], ...] = (
    ("09660c5d-6c3b-40d8-af15-c8e815148994", "israel", 1, 2),
    ("8731f741-396d-4b86-9969-b4f2638d2cc6", "marrocos", None, 3),
    ("9f89ffdb-9bf0-4062-8845-742f3ad603e1", "cairo", 2, None),
    ("dc20330d-e9f6-47b7-acd8-9546e5cc5b04", "cairo sa", 2, None),
)


def _check(valores: str) -> None:
    for tabela in ("store_info", "users"):
        nome = f"ck_{tabela}_commercial_team_valid"
        op.execute(
            f"ALTER TABLE {SCHEMA}.{tabela} DROP CONSTRAINT IF EXISTS {nome}, "  # noqa: S608
            f"ADD CONSTRAINT {nome} CHECK (commercial_team IN ({valores}))"
        )


def _conferir_usuarios(conn, *, ida: bool) -> list[tuple[str, int | None]]:
    """Confere cada usuário e devolve (id, valor a gravar) só dos que mudam."""
    gravar: list[tuple[str, int | None]] = []
    for uid, nome, antes, novo in USUARIOS:
        linha = conn.execute(
            sa.text(f"SELECT name, commercial_team FROM {SCHEMA}.users WHERE id = :id"),  # noqa: S608
            {"id": uid},
        ).first()
        if linha is None or linha.name != nome:
            raise RuntimeError(
                f"0445: usuário {nome} ({uid}) não encontrado ou com outro nome "
                f"({linha.name if linha else 'sem linha'}) — nada mudou"
            )
        de, para = (antes, novo) if ida else (novo, antes)
        if linha.commercial_team == para:
            continue
        if linha.commercial_team != de:
            if not ida:
                print(f"0445 downgrade: {nome} mexido depois ({linha.commercial_team}), fica")
                continue
            raise RuntimeError(
                f"0445: {nome} está com equipe {linha.commercial_team}, "
                f"esperava {de} (03/11) ou {para} — nada mudou"
            )
        gravar.append((uid, para))
    return gravar


def upgrade() -> None:
    conn = op.get_bind()
    usuarios = _conferir_usuarios(conn, ida=True)
    _check("1, 2, 3")
    lojas = conn.execute(
        sa.text(
            f"UPDATE {SCHEMA}.store_info SET commercial_team = sales_team - 100 "  # noqa: S608
            "WHERE sales_team IN (101, 102, 103) "
            "AND commercial_team IS DISTINCT FROM sales_team - 100"
        )
    ).rowcount
    for uid, valor in usuarios:
        conn.execute(
            sa.text(f"UPDATE {SCHEMA}.users SET commercial_team = :v WHERE id = :id"),  # noqa: S608
            {"v": valor, "id": uid},
        )
    print(f"0445: {lojas} loja(s) com a equipe 1/2/3 acertada; {len(usuarios)} usuário(s)")


def downgrade() -> None:
    conn = op.get_bind()
    usuarios = _conferir_usuarios(conn, ida=False)
    for uid, valor in usuarios:
        conn.execute(
            sa.text(f"UPDATE {SCHEMA}.users SET commercial_team = :v WHERE id = :id"),  # noqa: S608
            {"v": valor, "id": uid},
        )
    conn.execute(
        sa.text(
            f"UPDATE {SCHEMA}.store_info "  # noqa: S608
            "SET commercial_team = CASE WHEN id = ANY(CAST(:sem AS uuid[])) THEN NULL ELSE 1 END "
            "WHERE sales_team IN (101, 102, 103) AND commercial_team = sales_team - 100"
        ),
        {"sem": list(SEM_NUMERO_EM_0311)},
    )
    sobra = conn.execute(
        sa.text(
            f"SELECT (SELECT count(*) FROM {SCHEMA}.store_info WHERE commercial_team = 3) "  # noqa: S608
            f"+ (SELECT count(*) FROM {SCHEMA}.users WHERE commercial_team = 3)"
        )
    ).scalar_one()
    if sobra:
        raise RuntimeError(
            f"0445 downgrade: {sobra} loja(s)/usuário(s) com equipe 3 mexidos depois — "
            "acerte à mão antes de voltar"
        )
    _check("1, 2")
