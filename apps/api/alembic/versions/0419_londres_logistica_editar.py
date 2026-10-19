"""users: libera a Logística (ver + editar) para o londres — só dado, sem tabela

Vinicius, 19/10/2026: o londres não via o botão "Suspender entrega" na Amazon ›
Envio próprio — o botão só aparece pra quem edita a Logística, e a 0372 ligou
só `view`. Primeiro pediu uma permissão só pro botão, depois mudou: "libera
edição total na logistica pra ele". É o mesmo que marcar `view` e `edit` na
linha Pós-venda › Logística em Usuários › londres › Permissões; vai por
migration porque ninguém daqui tem o banco de produção na mão (mesmo caminho
da 0372 e da 0395).

Liga só `view` e `edit` e preserva o resto da linha (`delete` segue como
estava). Acha o usuário pelo e-mail (lefili02@tuta.com, sem diferenciar
maiúscula) e só mexe se achar EXATAMENTE um que não seja admin; senão não muda
nada e só imprime os candidatos — o deploy grava a saída do alembic em
/var/log/davinci-deploy.log, é ali que se confere. Imprime também o "Acesso às
contas" (`sales_teams`): com vínculo marcado ele só vê e edita na Logística os
pedidos dessas contas.

O downgrade não tira a permissão (não dá pra saber se já estava ligada antes).

Revision ID: 0419_londres_logistica_editar
Revises: 0418_refunds_freight_for_pedido
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0419_londres_logistica_editar"
down_revision: str | None = "0418_refunds_freight_for_pedido"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
EMAIL = "lefili02@tuta.com"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    conn = op.get_bind()
    candidatos = conn.execute(
        sa.text(
            "SELECT id, name, email, role::text, permissions -> 'logistica', sales_teams "  # noqa: S608
            f'FROM "{SCHEMA}".users '  # nome fixo daqui
            "WHERE lower(email) = :email OR lower(coalesce(name, '')) LIKE :like"
        ),
        {"email": EMAIL, "like": "%londres%"},
    ).all()
    exatos = [c for c in candidatos if (c.email or "").strip().lower() == EMAIL]
    for c in candidatos:
        print(
            f"[0419] candidato: name={c.name!r} email={c.email} role={c.role} "
            f"logistica={c[4]} sales_teams={c.sales_teams}"
        )
    if len(exatos) != 1:
        print(f"[0419] NADA MUDOU: {len(exatos)} usuário(s) com e-mail '{EMAIL}' — esperava 1")
        return
    alvo = exatos[0]
    if alvo.role == "admin":
        print("[0419] NADA MUDOU: londres é admin, já edita tudo")
        return
    conn.execute(
        sa.text(
            f'UPDATE "{SCHEMA}".users SET permissions = jsonb_set('  # noqa: S608
            "coalesce(permissions, '{}'::jsonb), '{logistica}', "
            "coalesce(permissions -> 'logistica', '{}'::jsonb) "
            "|| '{\"view\": true, \"edit\": true}'::jsonb) "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": str(alvo.id)},
    )
    print(
        f"[0419] OK: {alvo.email} agora vê e edita a Logística "
        f"(sales_teams={alvo.sales_teams})"
    )


def downgrade() -> None:
    pass
