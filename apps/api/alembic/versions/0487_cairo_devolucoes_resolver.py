"""users: libera "Devoluções — resolver e reabrir" pro cairo sa — só dado

Vinicius, 16/11/2026: "somente adm e o Cairo consegue clicar em resolver uma
devolução". O Resolver (e o reabrir) do painel novo passou a ser só de admin
ou de quem tem o recurso novo `devolucoes_resolver` (edit); o Cairo não é
admin. É o mesmo que marcar `edit` na linha nova em Usuários › cairo sa ›
Permissões; vai por migration porque ninguém daqui tem o banco de produção na
mão (mesmo caminho da 0464).

Liga `view` e `edit` e preserva o resto da linha. Acha o usuário pelo e-mail
(sa.geral@tutamail.com, sem diferenciar maiúscula) e só mexe se achar
EXATAMENTE um que não seja admin; senão não muda nada e só imprime os
candidatos — o deploy grava a saída do alembic em /var/log/davinci-deploy.log.

O downgrade não tira a permissão (tirar à força poderia desfazer uma marcação
feita depois pela tela).

Revision ID: 0487_cairo_devolucoes_resolver
Revises: 0486_devolucao_reabre_o_que_nao_terminou
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0487_cairo_devolucoes_resolver"
down_revision: str | None = "0486_devolucao_reabre_o_que_nao_terminou"
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
            "permissions -> 'devolucoes', permissions -> 'devolucoes_resolver' "
            f'FROM "{SCHEMA}".users '  # nome fixo daqui
            "WHERE lower(email) = :email OR lower(coalesce(name, '')) LIKE :like"
        ),
        {"email": EMAIL, "like": "%cairo%"},
    ).all()
    exatos = [c for c in candidatos if (c.email or "").strip().lower() == EMAIL]
    for c in candidatos:
        print(
            f"[0487] candidato: name={c.name!r} email={c.email} role={c.role} "
            f"devolucoes={c[4]} devolucoes_resolver={c[5]}"
        )
    if len(exatos) != 1:
        print(f"[0487] NADA MUDOU: {len(exatos)} usuário(s) com e-mail '{EMAIL}' — esperava 1")
        return
    alvo = exatos[0]
    if alvo.role == "admin":
        print("[0487] NADA MUDOU: cairo é admin, já resolve")
        return
    conn.execute(
        sa.text(
            f'UPDATE "{SCHEMA}".users SET permissions = jsonb_set('  # noqa: S608
            "coalesce(permissions, '{}'::jsonb), '{devolucoes_resolver}', "
            "coalesce(permissions -> 'devolucoes_resolver', '{}'::jsonb) "
            '|| \'{"view": true, "edit": true}\'::jsonb) '
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": str(alvo.id)},
    )
    print(f"[0487] OK: {alvo.email} agora resolve e reabre devoluções")


def downgrade() -> None:
    pass
