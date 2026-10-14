"""users: libera Tabela Preços — Contas (ver + editar) para o cairo sa — só dado

Cairo, 14/10/2026: a aba Contas da Tabela de Preços (margem/frete por tipo de
cada loja) dava "forbidden" para ele — "veja se tem permissão, caso não
concede via servidor". É o mesmo que marcar `view` e `edit` na linha
Tabela Preços — Contas em Usuários › cairo sa › Permissões; vai por migration
porque ninguém daqui tem o banco de produção na mão (mesmo caminho da 0372).

Liga só `view` e `edit` e preserva o resto da linha (se já tivesse `delete`,
segue). Acha o usuário pelo e-mail (sa.geral@tutamail.com, sem diferenciar
maiúscula) e só mexe se achar EXATAMENTE um que não seja admin; senão não muda
nada e só imprime os candidatos — o deploy grava a saída do alembic em
/var/log/davinci-deploy.log, é ali que se confere. Imprime também o "Acesso às
contas" (`sales_teams`): com equipe marcada ele só vê na Tabela de Preços as
contas da equipe, a não ser que tenha `tabela_precos_todas_contas`.

O downgrade não tira a permissão (não dá pra saber se já estava ligada antes).

Revision ID: 0395_cairo_tabela_precos_contas
Revises: 0394_logistica_mensagem_bling_feita
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0395_cairo_tabela_precos_contas"
down_revision: str | None = "0394_logistica_mensagem_bling_feita"
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
            "permissions -> 'tabela_precos_contas', "
            "permissions -> 'tabela_precos_todas_contas', sales_teams "
            f'FROM "{SCHEMA}".users '  # nome fixo daqui
            "WHERE lower(email) = :email OR lower(coalesce(name, '')) LIKE :like"
        ),
        {"email": EMAIL, "like": "%cairo%"},
    ).all()
    exatos = [c for c in candidatos if (c.email or "").strip().lower() == EMAIL]
    for c in candidatos:
        print(
            f"[0395] candidato: name={c.name!r} email={c.email} role={c.role} "
            f"contas={c[4]} todas_contas={c[5]} sales_teams={c.sales_teams}"
        )
    if len(exatos) != 1:
        print(f"[0395] NADA MUDOU: {len(exatos)} usuário(s) com e-mail '{EMAIL}' — esperava 1")
        return
    alvo = exatos[0]
    if alvo.role == "admin":
        print("[0395] NADA MUDOU: cairo é admin, já vê tudo")
        return
    conn.execute(
        sa.text(
            f'UPDATE "{SCHEMA}".users SET permissions = jsonb_set('  # noqa: S608
            "coalesce(permissions, '{}'::jsonb), '{tabela_precos_contas}', "
            "coalesce(permissions -> 'tabela_precos_contas', '{}'::jsonb) "
            "|| '{\"view\": true, \"edit\": true}'::jsonb) "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": str(alvo.id)},
    )
    print(
        f"[0395] OK: {alvo.email} agora vê e edita Tabela Preços — Contas "
        f"(sales_teams={alvo.sales_teams})"
    )


def downgrade() -> None:
    pass
