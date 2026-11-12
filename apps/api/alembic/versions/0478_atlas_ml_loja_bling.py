"""atlas (Mercado Livre): a loja do Bling nº 206365164 — só dado

Vinicius, 12/11/2026: a conta atlas do ML (CNPJ 68.044.112, AdsPower 207) foi
ligada no DaVinci (integração 2833c45b) e no Bling ("Loja Mercado Livre - 47",
nº 206365164 — "É a atlas"). Sem esse número no DaVinci a venda dela não acha a
conta: a Logística e o robô de prioridade casam o pedido pela loja do Bling em
Lojas (`store_info.bling_store_id`), e a detecção do pedido Flex e a leitura do
envio pela loja da empresa (`stores.bling_store_id`). Os dois campos só admin
mexe na tela (e Empresas pede senha) — "você não consegue colocar?": vai pelo
servidor, como a 0372 e a 0395.

Só preenche campo VAZIO, e só se achar EXATAMENTE a linha certa:
- Lojas: account_name "atlas", plataforma ml, não arquivada;
- Empresas: a loja ML ligada à integração 2833c45b.
E nada muda se outra loja já usa o 206365164. O deploy grava a saída do
alembic em /var/log/davinci-deploy.log — é ali que se confere ("[0478]").

O downgrade não apaga (não dá pra saber se alguém preencheu na tela depois).

Revision ID: 0478_atlas_ml_loja_bling
Revises: 0477_flex_ajuste_contas
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0478_atlas_ml_loja_bling"
down_revision: str | None = "0477_flex_ajuste_contas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
LOJA_BLING = 206365164
INTEGRACAO = "2833c45b-9ba5-485e-a9c8-9de7a74593f3"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    conn = op.get_bind()

    # Outra loja (não a atlas do ML — a atlas da Shopee tem o mesmo nome) já
    # usa o número?
    outras = conn.execute(
        sa.text(
            f'SELECT \'lojas\' AS onde, account_name || \'/\' || platform AS nome '  # noqa: S608
            f'FROM "{SCHEMA}".store_info '
            "WHERE trim(bling_store_id) = :txt "
            "AND NOT (lower(trim(account_name)) = 'atlas' AND platform = 'ml') "
            f'UNION ALL SELECT \'empresas\', id::text FROM "{SCHEMA}".stores '
            "WHERE bling_store_id = :num AND (integration_id IS NULL "
            "OR integration_id <> CAST(:integ AS uuid))"
        ),
        {"txt": str(LOJA_BLING), "num": LOJA_BLING, "integ": INTEGRACAO},
    ).all()
    if outras:
        onde = [tuple(r) for r in outras]
        print(f"[0478] NADA MUDOU: a loja Bling {LOJA_BLING} já está em {onde}")
        return

    # Lojas (store_info): a linha atlas · ML.
    lojas = conn.execute(
        sa.text(
            f'SELECT id, bling_store_id FROM "{SCHEMA}".store_info '  # noqa: S608
            "WHERE lower(trim(account_name)) = 'atlas' AND platform = 'ml' "
            "AND archived_at IS NULL"
        )
    ).all()
    if len(lojas) != 1:
        print(f"[0478] Lojas: NADA MUDOU — {len(lojas)} linha(s) atlas/ml ativas, esperava 1")
    elif (lojas[0].bling_store_id or "").strip():
        print(f"[0478] Lojas: já tinha Bling ID {lojas[0].bling_store_id!r} — não mexi")
    else:
        conn.execute(
            sa.text(
                f'UPDATE "{SCHEMA}".store_info SET bling_store_id = :txt '  # noqa: S608
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"txt": str(LOJA_BLING), "id": str(lojas[0].id)},
        )
        print(f"[0478] Lojas: atlas/ml agora tem Bling ID {LOJA_BLING}")

    # Empresas (stores): a loja ML ligada à integração atlas.
    loja = conn.execute(
        sa.text(
            f'SELECT id, bling_store_id FROM "{SCHEMA}".stores '  # noqa: S608
            "WHERE integration_id = CAST(:integ AS uuid) AND marketplace::text = 'ml'"
        ),
        {"integ": INTEGRACAO},
    ).all()
    if len(loja) != 1:
        print(
            f"[0478] Empresas: NADA MUDOU — {len(loja)} loja(s) ML na integração atlas, "
            "esperava 1"
        )
    elif loja[0].bling_store_id is not None:
        print(f"[0478] Empresas: já tinha loja Bling {loja[0].bling_store_id} — não mexi")
    else:
        conn.execute(
            sa.text(
                f'UPDATE "{SCHEMA}".stores SET bling_store_id = :num '  # noqa: S608
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"num": LOJA_BLING, "id": str(loja[0].id)},
        )
        print(f"[0478] Empresas: loja ML da atlas agora tem loja Bling {LOJA_BLING}")


def downgrade() -> None:
    pass
