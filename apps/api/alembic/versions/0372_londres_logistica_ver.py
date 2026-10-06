"""users: libera a Logística (só ver) para o londres — só dado, sem tabela

Vinicius, 06/10/2026: "nesse painel eu preciso que o londres tenha acesso para
visualizar logistica" … "faz pelo servidor". É o mesmo que marcar `view` na
linha Pós-venda › Logística em Usuários › londres › Permissões; vai por
migration porque ninguém daqui tem o banco de produção na mão.

Liga só `view` e preserva o resto da linha (se ele já tivesse `edit`, segue).
Acha o usuário pelo nome (`londres`, sem diferenciar maiúscula) e só mexe se
achar EXATAMENTE um que não seja admin; senão não muda nada e só imprime os
candidatos — o deploy mostra a saída do alembic, é ali que se confere. Imprime
também o "Acesso às contas" (`sales_teams`): com vínculo marcado ele só vê na
Logística os pedidos dessas contas.

O downgrade não tira a permissão (não dá pra saber se já estava ligada antes).

Revision ID: 0372_londres_logistica_ver
Revises: 0371_atendimento_automacoes_simulacao
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0372_londres_logistica_ver"
down_revision: str | None = "0371_atendimento_automacoes_simulacao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
NOME = "londres"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    conn = op.get_bind()
    candidatos = conn.execute(
        sa.text(
            "SELECT id, name, email, role::text, permissions -> 'logistica', sales_teams "  # noqa: S608
            f'FROM "{SCHEMA}".users '  # nome fixo daqui
            "WHERE lower(coalesce(name, '')) LIKE :like OR lower(email) LIKE :like"
        ),
        {"like": f"%{NOME}%"},
    ).all()
    exatos = [c for c in candidatos if (c.name or "").strip().lower() == NOME]
    for c in candidatos:
        print(
            f"[0372] candidato: name={c.name!r} email={c.email} role={c.role} "
            f"logistica={c[4]} sales_teams={c.sales_teams}"
        )
    if len(exatos) != 1:
        print(f"[0372] NADA MUDOU: {len(exatos)} usuário(s) com nome '{NOME}' — esperava 1")
        return
    alvo = exatos[0]
    if alvo.role == "admin":
        print("[0372] NADA MUDOU: londres é admin, já vê tudo")
        return
    conn.execute(
        sa.text(
            f'UPDATE "{SCHEMA}".users SET permissions = jsonb_set('  # noqa: S608
            "coalesce(permissions, '{}'::jsonb), '{logistica}', "
            "coalesce(permissions -> 'logistica', '{}'::jsonb) || '{\"view\": true}'::jsonb) "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": str(alvo.id)},
    )
    print(f"[0372] OK: {alvo.email} agora vê a Logística (sales_teams={alvo.sales_teams})")


def downgrade() -> None:
    pass
