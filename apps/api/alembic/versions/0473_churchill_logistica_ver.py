"""users: libera a Logística (ver) para o churchill — só dado, sem tabela

Vinicius, 11/11/2026: "o painel logística o churchil não tá conseguindo ver,
tem que liberar para ele esse painel logística e para ele ver o flex". A aba
Flex já é dele desde o 2b47fe74 (`flex_config.pode_ver` pelo nome), mas ela
fica dentro da Logística, e sem `logistica.view` a página nem abre.

É o mesmo que marcar `view` na linha Pós-venda › Logística em Usuários ›
churchill › Permissões; vai por migration porque ninguém daqui tem o banco de
produção na mão (mesmo caminho da 0372 e da 0419). Liga só `view` e preserva o
resto da linha (`edit`/`delete` seguem como estavam — aprovar e sincronizar no
Flex pedem `edit`).

Acha o usuário pelo NOME "churchill" (sem diferenciar maiúscula nem espaço nas
pontas — a mesma régua do Estoque Negativo e do Flex) e só mexe se achar
EXATAMENTE um que não seja admin; senão não muda nada e só imprime os
candidatos — o deploy grava a saída do alembic em /var/log/davinci-deploy.log,
é ali que se confere. Imprime também o "Acesso às contas" (`sales_teams`): com
vínculo marcado ele só vê na Logística (e no Flex) as contas dessas equipes.

O downgrade não tira a permissão (não dá pra saber se já estava ligada antes).

Revision ID: 0473_churchill_logistica_ver
Revises: 0472_juridico_pedido_processo
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0473_churchill_logistica_ver"
down_revision: str | None = "0472_juridico_pedido_processo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
NOME = "churchill"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    conn = op.get_bind()
    candidatos = conn.execute(
        sa.text(
            "SELECT id, name, email, role::text, status::text, "  # noqa: S608
            "permissions -> 'logistica', sales_teams "
            f'FROM "{SCHEMA}".users '  # nome fixo daqui
            "WHERE lower(coalesce(name, '')) LIKE :like"
        ),
        {"like": "%church%"},
    ).all()
    exatos = [c for c in candidatos if (c.name or "").strip().lower() == NOME]
    for c in candidatos:
        print(
            f"[0473] candidato: name={c.name!r} email={c.email} role={c.role} "
            f"status={c.status} logistica={c[5]} sales_teams={c.sales_teams}"
        )
    if len(exatos) != 1:
        print(f"[0473] NADA MUDOU: {len(exatos)} usuário(s) com o nome '{NOME}' — esperava 1")
        return
    alvo = exatos[0]
    if alvo.role == "admin":
        print("[0473] NADA MUDOU: churchill é admin, já vê tudo")
        return
    conn.execute(
        sa.text(
            f'UPDATE "{SCHEMA}".users SET permissions = jsonb_set('  # noqa: S608
            "coalesce(permissions, '{}'::jsonb), '{logistica}', "
            "coalesce(permissions -> 'logistica', '{}'::jsonb) "
            "|| '{\"view\": true}'::jsonb) "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": str(alvo.id)},
    )
    print(
        f"[0473] OK: {alvo.name} ({alvo.email}) agora vê a Logística "
        f"(sales_teams={alvo.sales_teams})"
    )


def downgrade() -> None:
    pass
