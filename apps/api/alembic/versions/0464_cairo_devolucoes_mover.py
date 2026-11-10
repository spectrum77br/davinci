"""users: libera "Devoluções — mover entre Acompanhamento e Fraude" pro cairo sa — só dado

Cairo, 10/11/2026: a equipe ficava passando o mesmo pedido do painel Fraude
pro Acompanhamento e vice-versa. O botão de mover passou a ser só de admin ou de
quem tem o recurso novo `devolucoes_mover` (edit); o Cairo não é admin e
precisa continuar movendo ("precisa deixar para mim mover também"). É o mesmo
que marcar `edit` na linha nova em Usuários › cairo sa › Permissões; vai por
migration porque ninguém daqui tem o banco de produção na mão (mesmo caminho
da 0395).

Liga `view` e `edit` (o `edit` já traz o `view` na cascata do schema) e
preserva o resto da linha. Acha o usuário pelo e-mail (sa.geral@tutamail.com,
sem diferenciar maiúscula) e só mexe se achar EXATAMENTE um que não seja
admin; senão não muda nada e só imprime os candidatos — o deploy grava a
saída do alembic em /var/log/davinci-deploy.log, é ali que se confere.

O downgrade não tira a permissão (o recurso é novo, mas tirar à força poderia
desfazer uma marcação feita depois pela tela).

Revision ID: 0464_cairo_devolucoes_mover
Revises: 0463_conciliacao_imposto_pedido
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0464_cairo_devolucoes_mover"
down_revision: str | None = "0463_conciliacao_imposto_pedido"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
EMAIL = "sa.geral@tutamail.com"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    conn = op.get_bind()
    candidatos = conn.execute(
        sa.text(
            "SELECT id, name, email, role::text, "  # noqa: S608
            "permissions -> 'devolucoes', permissions -> 'devolucoes_mover' "
            f'FROM "{SCHEMA}".users '  # nome fixo daqui
            "WHERE lower(email) = :email OR lower(coalesce(name, '')) LIKE :like"
        ),
        {"email": EMAIL, "like": "%cairo%"},
    ).all()
    exatos = [c for c in candidatos if (c.email or "").strip().lower() == EMAIL]
    for c in candidatos:
        print(
            f"[0464] candidato: name={c.name!r} email={c.email} role={c.role} "
            f"devolucoes={c[4]} devolucoes_mover={c[5]}"
        )
    if len(exatos) != 1:
        print(f"[0464] NADA MUDOU: {len(exatos)} usuário(s) com e-mail '{EMAIL}' — esperava 1")
        return
    alvo = exatos[0]
    if alvo.role == "admin":
        print("[0464] NADA MUDOU: cairo é admin, já move")
        return
    conn.execute(
        sa.text(
            f'UPDATE "{SCHEMA}".users SET permissions = jsonb_set('  # noqa: S608
            "coalesce(permissions, '{}'::jsonb), '{devolucoes_mover}', "
            "coalesce(permissions -> 'devolucoes_mover', '{}'::jsonb) "
            "|| '{\"view\": true, \"edit\": true}'::jsonb) "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": str(alvo.id)},
    )
    print(f"[0464] OK: {alvo.email} agora move pedidos entre Acompanhamento e Fraude")


def downgrade() -> None:
    pass
