"""Conciliação — Temu (piloto na loja Eron), lida da TELA por robô

Vinicius, 17/11/2026: "agora vamos para a Temu" → "Sim, piloto na Eron" → "Desde 24/10".
A Temu (Brasil) não tem API de extrato/repasse; o robô do Mac mini abre Finanças ›
Pagamentos no Seller Center e envia o que a página carrega (extrato por pedido, previsto
por pedido, saldo). Desenho em `docs/conciliacao-temu-desenho.md`.

O que esta migration faz:

- `conciliacao_lojas.bling_loja`: a loja do Bling escrita na própria loja da Conciliação,
  pra conta que não tem integração de verdade.
- `conciliacao_previstos`: o que a plataforma disse que vai pagar por pedido. Fica FORA da
  Margem de propósito (lá, valor real ≠ Bling vira "saldo divergente" e o robô da Margem
  segura o pedido).
- A loja "eron (temu)", por robô (`modo_extrato = 'upload'`), desde 24/10/2026, pedidos da
  loja 206288707 do Bling.
- A ÂNCORA: as tabelas da Conciliação exigem `integration_id` e a Temu não tem integração
  (a 0347 recusou criar uma por loja de robô, porque todo worker que percorre
  `integrations` passaria a mirá-la). A âncora nasce ARQUIVADA (`archived_at`), com
  `status = 'ancora'`, credenciais vazias, sem loja/Store, e com um nome que não casa com
  nenhuma loja da tela Lojas — arquivada ela não aparece em Integrações, Produtos nem
  Preços (o botão Excluir de Integrações apagaria a Conciliação da loja em cascata) e os
  crons que respeitam `archived_at` passam direto. Dono = quem tem mais integrações.

Voltar atrás sem downgrade: `UPDATE conciliacao_lojas SET ativa = false WHERE conta =
'eron (temu)'` tira a loja do painel e nada das outras muda. DOWNGRADE: apaga a loja, a
âncora (e, em cascata, as linhas dela), a tabela e a coluna.

Revision ID: 0495_conciliacao_temu
Revises: 0494_reembolso_cliente_atendimento
"""

# ruff: noqa: S608 — SQL montado só com nomes fixos desta migration (schema), sem entrada de fora
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0495_conciliacao_temu"
down_revision: str | None = "0494_reembolso_cliente_atendimento"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
# (nome da âncora, conta na Conciliação, loja do Bling, nº da loja na Temu) — só a Eron no
# piloto. O nº da loja na Temu (`mall_id`, vem em cada linha do extrato; conferido na tela
# em 17/11) fica gravado: leitura de outra loja é recusada.
LOJAS = (("eron (temu) · só conciliação", "eron (temu)", "206288707", "635517739909443"),)
INICIO = "2026-10-24"


def upgrade() -> None:
    op.add_column(
        "conciliacao_lojas", sa.Column("bling_loja", sa.String(40), nullable=True), schema=SCHEMA
    )
    op.create_table(
        "conciliacao_previstos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pedido_marketplace", sa.String(120), nullable=False),
        sa.Column("valor", sa.Numeric(14, 2), nullable=False),
        sa.Column("primeiro_valor", sa.Numeric(14, 2), nullable=False),
        sa.Column("liquidacao_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_pedido", sa.String(60), nullable=True),
        sa.Column("primeiro_lido_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lido_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "raw",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "integration_id", "pedido_marketplace", name="uq_conciliacao_previsto_pedido"
        ),
        schema=SCHEMA,
    )

    bind = op.get_bind()
    dono = bind.execute(
        sa.text(
            f"""
            SELECT user_id FROM {SCHEMA}.integrations
             WHERE platform <> 'bling'
             GROUP BY user_id
             ORDER BY count(*) DESC, min(created_at)
             LIMIT 1
            """
        )
    ).scalar()
    if dono is None:
        # banco sem nenhuma integração (teste, instalação nova): não há loja pra ligar
        print("0495_conciliacao_temu: nenhuma integração no banco — loja NÃO ligada")
        return
    for nome, conta, bling_loja, mall_id in LOJAS:
        ancora = bind.execute(
            sa.text(
                f"""
                INSERT INTO {SCHEMA}.integrations
                       (id, user_id, platform, name, credentials, status, archived_at)
                SELECT gen_random_uuid(), CAST(:dono AS uuid), 'temu', CAST(:nome AS text),
                       ''::bytea, 'ancora', now()
                 WHERE NOT EXISTS (
                       SELECT 1 FROM {SCHEMA}.integrations
                        WHERE platform = 'temu' AND name = CAST(:nome AS text)
                 )
                RETURNING id
                """
            ),
            {"dono": dono, "nome": nome},
        ).scalar()
        if ancora is None:
            ancora = bind.execute(
                sa.text(
                    f"SELECT id FROM {SCHEMA}.integrations "
                    "WHERE platform = 'temu' AND name = CAST(:nome AS text)"
                ),
                {"nome": nome},
            ).scalar()
        ligada = bind.execute(
            sa.text(
                f"""
                INSERT INTO {SCHEMA}.conciliacao_lojas
                       (id, integration_id, platform, conta, ativa, inicio, modo_extrato,
                        bling_loja)
                SELECT gen_random_uuid(), CAST(:ancora AS uuid), 'temu',
                       CAST(:conta AS varchar), true, DATE '{INICIO}', 'upload',
                       CAST(:bling_loja AS varchar)
                 WHERE NOT EXISTS (
                       SELECT 1 FROM {SCHEMA}.conciliacao_lojas
                        WHERE integration_id = CAST(:ancora AS uuid)
                           OR lower(conta) = lower(CAST(:conta AS varchar))
                 )
                RETURNING conta
                """
            ),
            {"ancora": ancora, "conta": conta, "bling_loja": bling_loja},
        ).scalar()
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {SCHEMA}.conciliacao_sync
                       (id, integration_id, origem, chave, status, detalhe)
                SELECT gen_random_uuid(), CAST(:ancora AS uuid), 'temu_extrato', 'identidade',
                       'lido', jsonb_build_object('mall_id', CAST(:mall_id AS text))
                 WHERE NOT EXISTS (
                       SELECT 1 FROM {SCHEMA}.conciliacao_sync
                        WHERE integration_id = CAST(:ancora AS uuid)
                          AND origem = 'temu_extrato' AND chave = 'identidade'
                 )
                """
            ),
            {"ancora": ancora, "mall_id": mall_id},
        )
        print(f"0495_conciliacao_temu: âncora {ancora} · loja ligada = {ligada or 'JÁ EXISTIA'}")


def downgrade() -> None:
    bind = op.get_bind()
    for nome, conta, _bling_loja, _mall_id in LOJAS:
        bind.execute(
            sa.text(
                f"DELETE FROM {SCHEMA}.conciliacao_lojas "
                "WHERE platform = 'temu' AND conta = CAST(:conta AS varchar)"
            ),
            {"conta": conta},
        )
        # a âncora leva junto, em cascata, movimentos, pedidos, dias e previstos da loja
        bind.execute(
            sa.text(
                f"DELETE FROM {SCHEMA}.integrations "
                "WHERE platform = 'temu' AND name = CAST(:nome AS text) AND status = 'ancora'"
            ),
            {"nome": nome},
        )
    op.drop_table("conciliacao_previstos", schema=SCHEMA)
    op.drop_column("conciliacao_lojas", "bling_loja", schema=SCHEMA)
