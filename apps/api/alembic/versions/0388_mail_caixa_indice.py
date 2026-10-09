"""mail: o índice leve da aba E-mail › Caixas (pasta e loja de cada e-mail)

O dono, 09/10/2026 (print da aba E-mail › Caixas, que mostra só remetente,
assunto e data): "visualmente na aba de e-mail poderia trazer o nome da loja
aqui também; deixar separado e visualmente igual ao Tuta". A pasta e a loja
ficam DENTRO do conteúdo cifrado da Central; com o leitor dele trazendo as ~40
pastas da conta (milhares de e-mails), decifrar tudo a cada clique fica caro
(medido em `tests/test_mail_caixa_volume.py`). Esta migration só ACRESCENTA
uma tabela nossa, ligada por FK às dele (nenhuma coluna nova nas tabelas da
0386 nem nas da 0387):

  • `mail_caixa_indice` — 1:1 com o e-mail (PK = FK para mail_messages, ON
    DELETE CASCADE): a pasta (a de sistema pelo tipo do Tuta; a pessoal por
    uma chave OPACA — o nome não fica em claro), se é de segurança, e a loja
    PROVÁVEL (ficha/integração/marca e plataforma, sem FK: é cache). Nada de
    assunto, remetente, texto ou endereço. `mailbox_id` também SEM FK, de
    propósito: a FK pegaria FOR KEY SHARE na linha da caixa a cada gravação,
    e o agente do Mac segura essa linha (FOR UPDATE) a cada sinal — a GET da
    tela ficava esperando (revisão de 09/10). A caixa apagada leva as linhas
    junto pelo CASCADE do e-mail (mail_messages → mail_mailboxes).

Começa VAZIA: o job do worker (`mail_caixa_indice`, a cada minuto, em lotes)
e a própria rota preenchem. Fora do Histórico (`historico/sql.EXCLUIDAS`: a
máquina escreve). `lock_timeout` de 10 s, como a 0386/0387 (a FK pega
ShareRowExclusiveLock em mail_messages, que o agente do Mac trava a cada
sinal) — no upgrade E no downgrade. O downgrade apaga a tabela
(é só cache: nada se perde).

`tests/test_mail_caixa_migration.py` roda esta num schema descartável (em
cima da 0386 e da 0387) e compara com o model.

Revision ID: 0388_mail_caixa_indice
Revises: 0387_mail_atendimento
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0388_mail_caixa_indice"
down_revision: str | None = "0387_mail_atendimento"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
INDICE = "mail_caixa_indice"
SELOS = ("loja", "site", "sem_loja", "privado")


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.create_table(
        INDICE,
        sa.Column(
            "message_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.mail_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Sem FK em mailbox_id: ver o texto acima (o CASCADE vem pelo e-mail).
        sa.Column("mailbox_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("recebido_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pasta_chave", sa.String(32), nullable=False),
        sa.Column("pasta_tipo", sa.String(8), nullable=False),
        sa.Column("seguranca", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("selo", sa.String(12), nullable=False),
        sa.Column("store_info_id", pg.UUID(as_uuid=True)),
        sa.Column("integration_id", pg.UUID(as_uuid=True)),
        sa.Column("marca_id", pg.UUID(as_uuid=True)),
        sa.Column("plataforma", sa.String(16)),
        sa.Column("fonte_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("base", sa.String(16), nullable=False),
        sa.Column("regras_versao", sa.Integer(), nullable=False),
        sa.Column(
            "indexado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("message_id"),
        sa.CheckConstraint(f"selo IN ({_lista(SELOS)})", name="selo"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_mail_caixa_indice_caixa_recebido",
        INDICE,
        ["mailbox_id", "recebido_em", "message_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_mail_caixa_indice_caixa_pasta",
        INDICE,
        ["mailbox_id", "pasta_chave", "recebido_em", "message_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_table(INDICE, schema=SCHEMA)
