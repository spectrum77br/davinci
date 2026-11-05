"""atendimento: todas as lojas de hoje para o cairo sa — só dado

Cairo, 05/11/2026: a Caixa do Atendimento abria vazia para ele ("Você ainda não
tem lojas no Atendimento") — "eu preciso ver atendimento de todas as lojas".
Desde a 0440 quem não é admin vê só as lojas gravadas em
`atendimento_lojas_pessoa`, e ele nunca teve linha. É o mesmo que clicar "marcar
todas" em Usuários › cairo sa › Lojas no atendimento; vai por migration porque
ninguém daqui tem o banco de produção na mão (mesmo caminho da 0395).

Grava as mesmas chaves que aquela tela lista (`escopo.lojas_para_vincular`):
as fichas ativas que são loja, as integrações ativas (menos o Bling) e os
canais sem integração (o robô, a Amazon só de e-mail, o número do WhatsApp).
A que ele já tiver fica. O papel dele não muda: segue `user`, e o site, as
redes e o Direct continuam só de admin.

É um RETRATO: loja criada depois não entra sozinha, marca-se na tela.

Acha o usuário pelo e-mail (sa.geral@tutamail.com, sem diferenciar maiúscula; há
outro "cairo", então nunca pelo nome) e só mexe se achar EXATAMENTE um que não
seja admin; senão não muda nada e só imprime o que achou — o deploy grava a
saída do alembic em /var/log/davinci-deploy.log, é ali que se confere.

DOWNGRADE: tira só as linhas dele com `criado_por` vazio (as desta migration; a
tela de Usuários sempre grava o autor).

Revision ID: 0456_cairo_atendimento_todas_lojas
Revises: 0455_atendimento_brindes
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "0456_cairo_atendimento_todas_lojas"
down_revision: str | None = "0455_atendimento_brindes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"
EMAIL = "sa.geral@tutamail.com"
# FIXO (não importa a constante viva do serviço): as fichas que não são loja.
FICHAS_QUE_NAO_SAO_LOJA = frozenset({"interno", "presencial"})


def _usuarios(conn: Any) -> list[Any]:
    return conn.execute(
        sa.text(
            "SELECT id, name, email, role::text AS papel "  # noqa: S608
            f'FROM "{SCHEMA}".users '  # nome fixo daqui
            "WHERE lower(btrim(email)) = :email"
        ),
        {"email": EMAIL},
    ).all()


def _chaves(conn: Any) -> dict[str, list[str]]:
    fichas = conn.execute(
        sa.text(
            f'SELECT id, platform FROM "{SCHEMA}".store_info '  # noqa: S608
            "WHERE archived_at IS NULL"
        )
    ).all()
    integracoes = conn.execute(
        sa.text(
            f'SELECT id FROM "{SCHEMA}".integrations '  # noqa: S608
            "WHERE archived_at IS NULL AND platform::text <> 'bling'"
        )
    ).all()
    canais = conn.execute(
        sa.text(
            f'SELECT id FROM "{SCHEMA}".atendimento_canais '  # noqa: S608
            "WHERE integration_id IS NULL AND (robo_perfil_id IS NOT NULL "
            "OR externo_ref LIKE 'amazon:%' OR externo_ref LIKE 'zap:%')"
        )
    ).all()
    return {
        "f": [
            f"f:{str(f.id).lower()}"
            for f in fichas
            if " ".join((f.platform or "").split()).lower() not in FICHAS_QUE_NAO_SAO_LOJA
        ],
        "i": [f"i:{str(i.id).lower()}" for i in integracoes],
        "c": [f"c:{str(c.id).lower()}" for c in canais],
    }


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    conn = op.get_bind()
    achados = _usuarios(conn)
    for u in achados:
        print(f"[0456] candidato: name={u.name!r} email={u.email} papel={u.papel}")
    if len(achados) != 1:
        print(f"[0456] NADA MUDOU: {len(achados)} usuário(s) com e-mail '{EMAIL}' — esperava 1")
        return
    alvo = achados[0]
    if alvo.papel == "admin":
        print("[0456] NADA MUDOU: é admin, já vê todas as lojas")
        return
    chaves = _chaves(conn)
    gravadas = 0
    for lista in chaves.values():
        for chave in lista:
            gravadas += conn.execute(
                sa.text(
                    f'INSERT INTO "{SCHEMA}".atendimento_lojas_pessoa (user_id, chave) '  # noqa: S608
                    "VALUES (CAST(:id AS uuid), :chave) ON CONFLICT DO NOTHING"
                ),
                {"id": str(alvo.id), "chave": chave},
            ).rowcount
    print(
        f"[0456] OK: {alvo.email} com {len(chaves['f'])} fichas, {len(chaves['i'])} integrações "
        f"e {len(chaves['c'])} canais ({gravadas} linhas novas)"
    )


def downgrade() -> None:
    conn = op.get_bind()
    achados = _usuarios(conn)
    if len(achados) != 1:
        print(f"[0456] NADA MUDOU: {len(achados)} usuário(s) com e-mail '{EMAIL}' — esperava 1")
        return
    tiradas = conn.execute(
        sa.text(
            f'DELETE FROM "{SCHEMA}".atendimento_lojas_pessoa '  # noqa: S608
            "WHERE user_id = CAST(:id AS uuid) AND criado_por IS NULL"
        ),
        {"id": str(achados[0].id)},
    ).rowcount
    print(f"[0456] {tiradas} linhas tiradas de {achados[0].email}")
