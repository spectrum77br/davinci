"""Conciliação — Temu: entram as lojas Barbosa e JLAS

Vinicius, 18/11/2026: "vamos fazer outras lojas" → logou as três no AdsPower do Mac mini
→ "Sim, pode fazer" (saque e reserva como linhas do extrato). A Eron entrou na 0495.

- "barbosa (temu)": pedidos da loja 205925390 do Bling, loja 635517739923741 na Temu.
- "jlas (temu)": pedidos da loja 206288651 do Bling, loja 635517739909677 na Temu.
- As duas por robô (`modo_extrato = 'upload'`), desde 24/10/2026 como as outras, cada uma
  numa ÂNCORA arquivada (ver a 0495: a Conciliação exige `integration_id` e a Temu não
  tem integração de verdade).
- O nº da loja na Temu (`mall_id`, lido na tela de cada uma em 18/11) fica gravado:
  leitura de outra loja é recusada.

A Atv (loja 206288703 do Bling) fica DE FORA: em 18/11 a tela dela não tinha saldo,
extrato nem pedido a receber — não há o que conciliar, nem linha pra ler o `mall_id`.

Voltar atrás sem downgrade: `UPDATE conciliacao_lojas SET ativa = false WHERE conta IN
('barbosa (temu)', 'jlas (temu)')`. DOWNGRADE: apaga as duas lojas e as duas âncoras (e,
em cascata, as linhas delas); a Eron e as tabelas da 0495 ficam.

Revision ID: 0501_conciliacao_temu_lojas
Revises: 0500_organizar_ocultos
"""

# ruff: noqa: S608 — SQL montado só com nomes fixos desta migration (schema), sem entrada de fora
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0501_conciliacao_temu_lojas"
down_revision: str | None = "0500_organizar_ocultos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
# (nome da âncora, conta na Conciliação, loja do Bling, nº da loja na Temu)
LOJAS = (
    ("barbosa (temu) · só conciliação", "barbosa (temu)", "205925390", "635517739923741"),
    ("jlas (temu) · só conciliação", "jlas (temu)", "206288651", "635517739909677"),
)
INICIO = "2026-10-24"


def upgrade() -> None:
    bind = op.get_bind()
    # o mesmo dono da âncora da Eron; sem ela (banco de teste), quem tem mais integrações
    dono = bind.execute(
        sa.text(
            f"""
            SELECT user_id FROM {SCHEMA}.integrations
             WHERE platform <> 'bling'
             GROUP BY user_id
             ORDER BY bool_or(platform = 'temu' AND status = 'ancora') DESC,
                      count(*) DESC, min(created_at)
             LIMIT 1
            """
        )
    ).scalar()
    if dono is None:
        # banco sem nenhuma integração (teste, instalação nova): não há loja pra ligar
        print("0501_conciliacao_temu_lojas: nenhuma integração no banco — lojas NÃO ligadas")
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
        print(
            f"0501_conciliacao_temu_lojas: âncora {ancora} · loja ligada = {ligada or 'JÁ EXISTIA'}"
        )


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
