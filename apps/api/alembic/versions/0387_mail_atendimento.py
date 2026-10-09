"""mail: a camada do atendimento POR CIMA da Central de e-mail (tabelas laterais)

Eduardo, 08/10/2026: a Central de e-mail do outro dev (0386_mail_central, já
em produção, com a caixa "Goslin — Tuta" lendo e respondendo) é a BASE — "vamos
apenas adicionar se formos agregar em algo". Esta migration só ACRESCENTA
tabelas nossas, ligadas por FK às dele; nenhuma coluna nova nas 4 tabelas da
0386 (o `test_mail_migration.py` dele compara aquelas tabelas com os modelos).

  • `mail_mailbox_settings` — 1:1 com a caixa (PK = FK para mail_mailboxes,
    ON DELETE CASCADE): visibilidade privada|empresa, a ponte para o
    /atendimento (desligada, com corte e "só aliases de loja"), o remetente
    estrito, o modo de envio (teste|real) com a lista de teste, os tetos
    (30/h, 300/dia, 100/h da conta), a pausa e o que o agente conta de si.
    Sem linha = os padrões: a Goslin não muda nada.
  • `mail_folders` — as pastas de cada caixa COM PONTE (a ponte grava ao ver
    o e-mail): plataforma/finalidade pela regra ou por pessoa e `ler`
    (corpo | so_contar | nao). Única por (caixa, chave).
  • `atendimento_regras_pasta_email` — a regra de palavras (global), com a
    semente de 08/10 (ids fixos).
  • `mail_message_meta` — 1:1 com o e-mail (PK = FK para mail_messages,
    CASCADE): o que a ponte decidiu (estado, loja, pedido, protocolo, golpe,
    conversa/mensagem do /atendimento) — SÓ metadado, nada do texto.
  • `mail_outbox_meta` — 1:1 com o job da fila dele (CASCADE): a mensagem
    `enviando` do /atendimento, quem resolveu um envio incerto e o recibo do
    Mac que chegou DEPOIS dessa resolução (`recibo_tardio`).

E o que o CONECTOR v2 (o nosso, do Mac; contrato em `routers/mail_agent_v2.py`)
escreve, também em tabelas laterais e fora do Histórico (é máquina):

  • `mail_agente_v2` — 1:1 com a caixa: instância, versões, contadores e os
    aliases ATIVOS da conta no Tuta (a cada volta, ~1 min);
  • `mail_message_tuta` — 1:1 com o e-mail que chegou pelo v2: a chave da
    pasta do Tuta onde ele está e se foi apagado (movido/apagado);
  • `mail_reconciliation` — a contagem do dia por pasta (no Tuta × gravado).

Nenhum dado de e-mail é migrado nem criado (nem a linha da Goslin); só a
semente da regra de palavras. Histórico: a configuração, a ligação
fila ↔ conversa e a regra ficam DENTRO (são de pessoa); as pastas e os
metadados ficam FORA (`historico/sql.EXCLUIDAS`: a máquina escreve a cada
volta da ponte). O gatilho já instalado nas 4 tabelas dele não é tocado aqui
(combinar com o outro dev).

`lock_timeout` de 10 s, como a 0386 (as FKs pegam ShareRowExclusiveLock em
users, mail_*, integrations, store_info, marcas e atendimento_*; o agente do
Mac trava a linha da caixa a cada sinal e o sync grava conversas o tempo
todo) — no upgrade E no downgrade. O downgrade apaga as tabelas (a
configuração e o que a ponte decidiu se perdem; a Central volta ao
comportamento de sempre — as conversas e mensagens que a ponte gravou no
/atendimento ficam).

`tests/test_mail_atendimento_migration.py` roda esta num schema descartável
(em cima da 0386) e compara com o model; e confere a ponta única.

Revision ID: 0387_mail_atendimento
Revises: 0386_mail_central
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0387_mail_atendimento"
down_revision: str | None = "0386_mail_central"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
CONFIG = "mail_mailbox_settings"
PASTAS = "mail_folders"
REGRAS = "atendimento_regras_pasta_email"
META = "mail_message_meta"
ENVIOS = "mail_outbox_meta"
AGENTE = "mail_agente_v2"
LOCAL = "mail_message_tuta"
CONTAGEM = "mail_reconciliation"

ESTADOS_META = (
    "novo",
    "gravado",
    "sem_vinculo",
    "sem_loja",
    "ignorado",
    "interno",
    "resumo",
    "duplicado",
    "seguranca",
    "privado",
    "erro",
)

# A semente da regra de palavras (CONGELADA nesta data: o que mudar depois é
# pela tela). (tipo, palavra sem acento e minúscula, valor). A mesma de
# `services/mail_atendimento/regras.SEMENTE`.
SEMENTE: tuple[tuple[str, str, str], ...] = (
    ("plataforma", "ml", "ml"),
    ("plataforma", "mercadolivre", "ml"),
    ("plataforma", "shopee", "shopee"),
    ("plataforma", "amazon", "amazon"),
    ("plataforma", "tiktok", "tiktok"),
    ("plataforma", "temu", "temu"),
    ("plataforma", "magalu", "magalu"),
    ("plataforma", "ali", "aliexpress"),
    ("plataforma", "aliexpress", "aliexpress"),
    ("plataforma", "shein", "shein"),
    ("finalidade", "vendas", "vendas"),
    ("finalidade", "venda", "vendas"),
    ("finalidade", "mensagens", "mensagens"),
    ("finalidade", "mensagem", "mensagens"),
    ("finalidade", "problema", "problema"),
    ("finalidade", "problemas", "problema"),
    ("finalidade", "reclamacao", "reclamacao"),
    ("finalidade", "reclamacoes", "reclamacao"),
    ("marca", "uranyx", "uranyx"),
    ("marca", "charlots", "charlots-park"),
    ("marca", "7buyers", "7buyers"),
    ("marca", "poofy", "poofy"),
    ("marca", "makisa", "makisa"),
    ("marca", "locagil", "locagil"),
)


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _uuid_fixo(i: int) -> UUID:
    """Um id estável para cada linha da semente (downgrade/upgrade dá o mesmo)."""
    return UUID(f"00000000-0000-4387-8000-{i:012d}")


def _fk(coluna: str, alvo: str, ondelete: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(
        coluna,
        pg.UUID(as_uuid=True),
        sa.ForeignKey(f"{SCHEMA}.{alvo}", ondelete=ondelete),
        nullable=nullable,
    )


def _jsonb(nome: str, padrao: str = "'[]'::jsonb") -> sa.Column:
    return sa.Column(nome, pg.JSONB(), nullable=False, server_default=sa.text(padrao))


def _bool(nome: str, padrao: str = "false") -> sa.Column:
    return sa.Column(nome, sa.Boolean(), nullable=False, server_default=sa.text(padrao))


def _indice(tabela: str, colunas: list[str]) -> None:
    op.create_index(f"ix_{tabela}_{'_'.join(colunas)}", tabela, colunas, schema=SCHEMA)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.create_table(
        CONFIG,
        sa.Column(
            "mailbox_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.mail_mailboxes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "visibilidade", sa.String(12), nullable=False, server_default=sa.text("'privada'")
        ),
        sa.Column("ponte_ligada", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ponte_desde", sa.DateTime(timezone=True)),
        sa.Column(
            "ponte_so_aliases_de_loja",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "remetente_estrito", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("envio_modo", sa.String(8), nullable=False, server_default=sa.text("'teste'")),
        sa.Column(
            "destinatarios_teste",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("teto_hora", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column("teto_dia", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column("teto_conta_hora", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("envio_pausado_ate", sa.DateTime(timezone=True)),
        sa.Column("envio_pausa_motivo", sa.String(200)),
        sa.Column("agente_tipo", sa.String(4)),
        sa.Column("agente_info", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "updated_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
        ),
        *_timestamps(),
        sa.CheckConstraint("visibilidade IN ('privada', 'empresa')", name="visibilidade"),
        sa.CheckConstraint("envio_modo IN ('teste', 'real')", name="envio_modo"),
        sa.CheckConstraint(
            "agente_tipo IS NULL OR agente_tipo IN ('v1', 'v2')", name="agente_tipo"
        ),
        sa.CheckConstraint(
            "teto_hora >= 0 AND teto_dia >= 0 AND teto_conta_hora >= 0", name="tetos"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(destinatarios_teste) = 'array'", name="destinatarios_lista"
        ),
        sa.CheckConstraint("jsonb_typeof(agente_info) = 'object'", name="agente_info_objeto"),
        schema=SCHEMA,
    )

    op.create_table(
        PASTAS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        _fk("mailbox_id", "mail_mailboxes.id", "CASCADE", nullable=False),
        sa.Column("chave", sa.String(191), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("caminho", sa.Text()),
        sa.Column("tipo_tuta", sa.String(8)),
        sa.Column("pai", sa.String(191)),
        sa.Column("plataforma", sa.String(16)),
        sa.Column("finalidade", sa.String(16)),
        sa.Column("plataforma_manual", sa.String(16)),
        sa.Column("finalidade_manual", sa.String(16)),
        _bool("ignorar"),
        sa.Column("ler", sa.String(10), nullable=False, server_default=sa.text("'so_contar'")),
        _bool("revisada"),
        sa.Column("vista_em", sa.DateTime(timezone=True)),
        sa.Column("sumiu_em", sa.DateTime(timezone=True)),
        sa.Column("ultima_contagem", sa.Integer()),
        *_timestamps(),
        sa.UniqueConstraint("mailbox_id", "chave"),
        sa.CheckConstraint("ler IN ('corpo', 'so_contar', 'nao')", name="ler"),
        schema=SCHEMA,
    )

    regras = op.create_table(
        REGRAS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tipo", sa.String(16), nullable=False),
        sa.Column("palavra", sa.String(64), nullable=False),
        sa.Column("valor", sa.String(64), nullable=False),
        _bool("ativa", "true"),
        *_timestamps(),
        sa.UniqueConstraint("tipo", "palavra"),
        sa.CheckConstraint("tipo IN ('plataforma', 'finalidade', 'marca')", name="tipo"),
        schema=SCHEMA,
    )
    op.bulk_insert(
        regras,
        [
            {"id": _uuid_fixo(i), "tipo": t, "palavra": p, "valor": v, "ativa": True}
            for i, (t, p, v) in enumerate(SEMENTE)
        ],
    )

    op.create_table(
        META,
        _fk("message_id", "mail_messages.id", "CASCADE", nullable=False),
        _fk("mailbox_id", "mail_mailboxes.id", "CASCADE", nullable=False),
        _fk("folder_id", "mail_folders.id", "SET NULL"),
        sa.Column("estado", sa.String(16), nullable=False, server_default=sa.text("'novo'")),
        sa.Column("motivo", sa.String(48)),
        _jsonb("sugestoes"),
        _jsonb("alertas"),
        sa.Column("remetente_tipo", sa.String(16)),
        sa.Column("alias_recebido", sa.String(254)),
        sa.Column("plataforma", sa.String(16)),
        sa.Column("finalidade", sa.String(16)),
        _fk("store_info_id", "store_info.id", "SET NULL"),
        _fk("integration_id", "integrations.id", "SET NULL"),
        _fk("marca_id", "marcas.id", "SET NULL"),
        sa.Column("tipo_caixa", sa.String(16)),
        sa.Column("protocolo", sa.String(32)),
        _jsonb("pedidos_citados"),
        sa.Column("pedido_marketplace", sa.String(64)),
        sa.Column("vinculado_por", sa.String(24)),
        _bool("suspeito"),
        _jsonb("suspeito_motivos"),
        sa.Column("auth_status", sa.String(8)),
        sa.Column("phishing_status", sa.String(8)),
        _bool("codigo_mascarado"),
        sa.Column("tuta_id", sa.String(191)),
        sa.Column("fio_tuta", sa.String(191)),
        sa.Column("mid_hash", sa.String(64)),
        sa.Column("resposta_a_hash", sa.String(64)),
        _fk("conversa_id", "atendimento_conversas.id", "SET NULL"),
        _fk("mensagem_id", "atendimento_mensagens.id", "SET NULL"),
        _fk("duplicado_de", "mail_messages.id", "SET NULL"),
        sa.Column("regras_versao", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("processado_em", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("message_id"),
        sa.CheckConstraint(f"estado IN ({_lista(ESTADOS_META)})", name="estado"),
        schema=SCHEMA,
    )
    _indice(META, ["mailbox_id", "estado"])
    _indice(META, ["estado", "integration_id"])
    for coluna in ("protocolo", "fio_tuta", "mid_hash", "resposta_a_hash", "conversa_id"):
        _indice(META, [coluna])

    op.create_table(
        ENVIOS,
        _fk("outbox_id", "mail_outbox.id", "CASCADE", nullable=False),
        _fk("atendimento_mensagem_id", "atendimento_mensagens.id", "SET NULL"),
        _fk("conversa_id", "atendimento_conversas.id", "SET NULL"),
        sa.Column("origem", sa.String(16), nullable=False, server_default=sa.text("'conversa'")),
        _bool("caixa_empresa"),
        sa.Column("enviado_mid_hash", sa.String(64)),
        _fk("resolvido_por", "users.id", "SET NULL"),
        sa.Column("resolvido_em", sa.DateTime(timezone=True)),
        sa.Column("resolucao", sa.String(10)),
        sa.Column("status_visto", sa.String(16)),
        sa.Column("sincronizado_em", sa.DateTime(timezone=True)),
        sa.Column("recibo_tardio", sa.String(16)),
        sa.Column("recibo_tardio_em", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("outbox_id"),
        sa.UniqueConstraint("atendimento_mensagem_id"),
        sa.CheckConstraint("origem IN ('conversa', 'caixa')", name="origem"),
        sa.CheckConstraint(
            "resolucao IS NULL OR resolucao IN ('saiu', 'nao_saiu')", name="resolucao"
        ),
        schema=SCHEMA,
    )
    for coluna in ("conversa_id", "enviado_mid_hash"):
        _indice(ENVIOS, [coluna])

    op.create_table(
        AGENTE,
        _fk("mailbox_id", "mail_mailboxes.id", "CASCADE", nullable=False),
        sa.Column("instancia", sa.String(64), nullable=False),
        sa.Column("versao_agente", sa.String(64)),
        sa.Column("versao_tuta", sa.String(32)),
        _jsonb("contadores", "'{}'::jsonb"),
        _jsonb("aliases_conta"),
        sa.Column("visto_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dois_agentes_em", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("mailbox_id"),
        sa.CheckConstraint("jsonb_typeof(contadores) = 'object'", name="contadores_objeto"),
        sa.CheckConstraint("jsonb_typeof(aliases_conta) = 'array'", name="aliases_lista"),
        schema=SCHEMA,
    )

    op.create_table(
        LOCAL,
        _fk("message_id", "mail_messages.id", "CASCADE", nullable=False),
        _fk("mailbox_id", "mail_mailboxes.id", "CASCADE", nullable=False),
        sa.Column("folder_key", sa.String(191), nullable=False),
        sa.Column("movido_em", sa.DateTime(timezone=True)),
        sa.Column("apagado_em", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("message_id"),
        schema=SCHEMA,
    )
    _indice(LOCAL, ["mailbox_id", "folder_key"])

    op.create_table(
        CONTAGEM,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        _fk("mailbox_id", "mail_mailboxes.id", "CASCADE", nullable=False),
        _fk("folder_id", "mail_folders.id", "CASCADE", nullable=False),
        sa.Column("dia", sa.Date(), nullable=False),
        sa.Column("no_tuta", sa.Integer(), nullable=False),
        sa.Column("gravados", sa.Integer()),
        sa.Column("faltando", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("a_mais", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _bool("ok"),
        sa.Column("motivo", sa.String(48)),
        sa.Column("conferido_em", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("mailbox_id", "folder_id", "dia"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Os DROPs pegam trava nas tabelas das FKs (users, store_info,
    # atendimento_*, mail_*): com o sync gravando, melhor falhar em 10 s do
    # que segurar a fila de quem escreve nelas.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_table(CONTAGEM, schema=SCHEMA)
    op.drop_table(LOCAL, schema=SCHEMA)
    op.drop_table(AGENTE, schema=SCHEMA)
    op.drop_table(ENVIOS, schema=SCHEMA)
    op.drop_table(META, schema=SCHEMA)
    op.drop_table(REGRAS, schema=SCHEMA)
    op.drop_table(PASTAS, schema=SCHEMA)
    op.drop_table(CONFIG, schema=SCHEMA)
