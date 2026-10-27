"""atendimento: canal Zap (WhatsApp Cloud API oficial da Meta) — RF10, Onda 1

Eduardo, 16/10/2026 (desenho aprovado em docs/zap/desenho.md, §4.2): cada
número de WhatsApp das lojas vira um canal EXTERNO do Atendimento
(`plataforma='whatsapp'`, `canal='zap'`, `externo_ref='zap:<phone_number_id>'`,
sem integração). A conversa, as mensagens, a fila e a etiqueta são as de
sempre (`atendimento_canais`/`conversas`/`mensagens` não mudam: o CHECK
`tem_origem` e o `uq_atendimento_conversas_robo` já aceitam o formato).

O que muda (só aditivo, seis tabelas novas, todas `atendimento_zap_*`):
  • `atendimento_zap_apps`     — o app da Meta: app secret e verify token
    CIFRADOS (`cipher.encrypt_json`), `app_id` UNIQUE (vai na URL do webhook);
  • `atendimento_zap_contas`   — a WABA e o token do usuário do sistema
    (cifrado), com o CHECK do `status`;
  • `atendimento_zap_numeros`  — o número: `envio_ligado` nasce false (o botão
    "pausar"), a etapa da migração do Duoke (CHECK), a lista de teste, o teto
    do dia, o PIN cifrado e a saúde (CHECK);
  • `atendimento_zap_contatos` — o cliente num número (o `id` é o `externo_id`
    da conversa), com os dois UNIQUE PARCIAIS (numero, bsuid) e
    (numero, wa_id_norm) e o CHECK do `opt_out_tipo`;
  • `atendimento_zap_eventos`  — a caixa de entrada do webhook, um item por
    linha: `chave` UNIQUE (idempotência da reentrega), CHECKs do `tipo` e do
    `resultado`, e o índice PARCIAL da fila
    `(proxima_em NULLS FIRST, id) WHERE processado_em IS NULL`;
  • `atendimento_zap_midias`   — o registro da mídia (o baixador é da Onda 3),
    UNIQUE (numero_ref, media_id), CHECKs da direção e do status.

Tudo nasce desligado: nenhuma linha é criada aqui, e as chaves
`ATENDIMENTO_ZAP_*` do `config.py` nascem False. Sem app cadastrado, o webhook
responde 404 e nada é processado.

Histórico (no mesmo commit): `atendimento_zap_(contatos|eventos|midias)` entram
em `historico/sql.EXCLUIDAS` (telefone, BSUID, texto e nome de arquivo do
cliente não vão para o gatilho); `apps`, `contas` e `numeros` ficam com ele (os
`*_enc` saem como `{_arquivo: true}`).

`SET LOCAL lock_timeout` de 3 s, como as outras do atendimento: as FKs novas
pegam ShareRowExclusiveLock em users, marcas, atendimento_canais,
atendimento_conversas e atendimento_mensagens com a api e o sync rodando.
`tests/test_atendimento_migration.py` roda esta num schema descartável e
compara o catálogo com o model (e o downgrade com o de antes).

O downgrade apaga as seis tabelas (o que o Zap leu se perde; os canais
`zap:*`, as conversas e as mensagens ficam, como as de uma loja desconectada).

Nasceu como 0415 no desenho (17/10), virou 0417 em 19/10 e 0430 em 26/10: o
origin/main chegou primeiro com as 0415–0429 — esta é a 0430, em cima da 0429.

Revision ID: 0432_atendimento_zap
Revises: 0431_mail_bridge_sync
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0432_atendimento_zap"
down_revision: str | None = "0431_mail_bridge_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
APPS = "atendimento_zap_apps"
CONTAS = "atendimento_zap_contas"
NUMEROS = "atendimento_zap_numeros"
CONTATOS = "atendimento_zap_contatos"
EVENTOS = "atendimento_zap_eventos"
MIDIAS = "atendimento_zap_midias"

# Os valores das colunas de estado — cópia de `app/models/atendimento_zap.py`
# (migration não importa model; o teste da migration confere que batem).
CONTA_STATUS = ("novo", "ok", "token_invalido", "sem_permissao", "vencendo", "erro")
NUMERO_ETAPAS = (
    "teste",
    "preparando",
    "aguardando_duoke",
    "migrando",
    "ativo",
    "pausado",
    "desligado",
)
NUMERO_SAUDE = ("novo", "ok", "restrito", "erro", "sem_eventos")
OPT_OUT_TIPOS = ("todos", "marketing")
EVENTO_TIPOS = ("mensagem", "status", "erro", "preferencia", "conta", "ignorado")
EVENTO_RESULTADOS = (
    "gravado",
    "repetido",
    "numero_desconhecido",
    "waba_desconhecida",
    "numero_de_outra_waba",
    "sem_alvo",
    "objeto_inesperado",
    "json_invalido",
    "erro",
)
MIDIA_DIRECOES = ("entrada", "saida")
MIDIA_STATUS = (
    "pendente",
    "baixando",
    "baixada",
    "grande_demais",
    "falhou",
    "expirada",
    "apagada",
)


def _em(coluna: str, valores: tuple[str, ...], *, nulo: bool = False) -> str:
    lista = ", ".join(f"'{v}'" for v in valores)
    regra = f"{coluna} IN ({lista})"
    return f"{coluna} IS NULL OR {regra}" if nulo else regra


def _check(tabela: str, nome: str, regra: str) -> sa.CheckConstraint:
    # Nome final (`op.f`): sem passar de novo pela convenção ck_<tabela>_<nome>.
    return sa.CheckConstraint(regra, name=op.f(f"ck_{tabela}_{nome}"))


def _uuid(nome: str, nullable: bool = True) -> sa.Column:
    return sa.Column(nome, pg.UUID(as_uuid=True), nullable=nullable)


def _jsonb(nome: str, vazio: str) -> sa.Column:
    return sa.Column(nome, pg.JSONB(), server_default=sa.text(f"'{vazio}'::jsonb"), nullable=False)


def _quando(nome: str, nullable: bool = True, agora: bool = False) -> sa.Column:
    return sa.Column(
        nome,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if agora else None,
        nullable=nullable,
    )


def _bool(nome: str, padrao: bool) -> sa.Column:
    return sa.Column(
        nome, sa.Boolean(), server_default=sa.text("true" if padrao else "false"), nullable=False
    )


def _carimbos() -> list[sa.Column]:
    return [
        _quando("created_at", nullable=False, agora=True),
        _quando("updated_at", nullable=False, agora=True),
    ]


def _fk(
    tabela: str, coluna: str, alvo: str, ondelete: str, nome: str | None = None
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [coluna],
        [f"{SCHEMA}.{alvo}.id"],
        name=nome or f"fk_{tabela}_{coluna}_{alvo}",
        ondelete=ondelete,
    )


def upgrade() -> None:
    # As FKs novas pegam ShareRowExclusiveLock nas tabelas referidas com a api,
    # o sync e os crons rodando: sem teto, uma transação longa faria a
    # migration esperar e enfileirar todo mundo atrás dela.
    op.execute("SET LOCAL lock_timeout = '3s'")

    # ── O app da Meta ────────────────────────────────────────────────────
    op.create_table(
        APPS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("app_id", sa.String(32), nullable=False),
        sa.Column("nome", sa.String(120), nullable=True),
        sa.Column("segredo_enc", sa.LargeBinary(), nullable=False),
        sa.Column("verify_token_enc", sa.LargeBinary(), nullable=False),
        _bool("teste", False),
        _bool("ativo", True),
        _quando("ultimo_evento_em"),
        _uuid("criado_por"),
        *_carimbos(),
        _fk(APPS, "criado_por", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{APPS}"),
        sa.UniqueConstraint("app_id", name=f"uq_{APPS}_app_id"),
        schema=SCHEMA,
    )

    # ── A WABA ───────────────────────────────────────────────────────────
    op.create_table(
        CONTAS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        _uuid("app_ref", nullable=False),
        sa.Column("waba_id", sa.String(32), nullable=False),
        sa.Column("business_id", sa.String(32), nullable=True),
        sa.Column("nome", sa.String(120), nullable=True),
        sa.Column("token_enc", sa.LargeBinary(), nullable=False),
        _quando("token_expira_em"),
        _bool("token_nunca_vence", False),
        _quando("token_conferido_em"),
        _quando("token_trocado_em"),
        _quando("inscrito_em"),
        sa.Column("status", sa.String(16), server_default=sa.text("'novo'"), nullable=False),
        _quando("ultimo_ok_em"),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column("ultimo_erro_code", sa.Integer(), nullable=True),
        _quando("ultimo_erro_em"),
        *_carimbos(),
        _check(CONTAS, "status", _em("status", CONTA_STATUS)),
        _fk(CONTAS, "app_ref", APPS, "RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{CONTAS}"),
        sa.UniqueConstraint("waba_id", name=f"uq_{CONTAS}_waba_id"),
        schema=SCHEMA,
    )

    # ── O número (o botão "pausar") ──────────────────────────────────────
    op.create_table(
        NUMEROS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        _uuid("conta_ref", nullable=False),
        sa.Column("phone_number_id", sa.String(32), nullable=False),
        sa.Column("display_phone_number", sa.String(32), nullable=False),
        sa.Column("verified_name", sa.String(120), nullable=True),
        sa.Column("nome_status", sa.String(24), nullable=True),
        sa.Column("nome_na_barra", sa.String(120), nullable=True),
        _uuid("marca_id"),
        _uuid("canal_id"),
        _bool("teste", False),
        sa.Column("etapa", sa.String(16), server_default=sa.text("'preparando'"), nullable=False),
        _bool("envio_ligado", False),
        sa.Column(
            "destinos_teste",
            pg.ARRAY(sa.String(32)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("teto_dia", sa.Integer(), nullable=True),
        sa.Column("pin_enc", sa.LargeBinary(), nullable=True),
        sa.Column(
            "registro_tentativas",
            pg.ARRAY(sa.DateTime(timezone=True)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        _jsonb("saude", "{}"),
        sa.Column("saude_status", sa.String(16), server_default=sa.text("'novo'"), nullable=False),
        _quando("ultimo_recebido_em"),
        _quando("ultimo_enviado_em"),
        _quando("ultimo_ok_em"),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column("ultimo_erro_code", sa.Integer(), nullable=True),
        _quando("ultimo_erro_em"),
        _quando("duoke_liberado_em"),
        _quando("migrado_em"),
        sa.Column("observacao", sa.Text(), nullable=True),
        *_carimbos(),
        _check(NUMEROS, "etapa", _em("etapa", NUMERO_ETAPAS)),
        _check(NUMEROS, "saude_status", _em("saude_status", NUMERO_SAUDE)),
        _fk(NUMEROS, "conta_ref", CONTAS, "RESTRICT"),
        _fk(NUMEROS, "marca_id", "marcas", "SET NULL"),
        _fk(NUMEROS, "canal_id", "atendimento_canais", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{NUMEROS}"),
        sa.UniqueConstraint("phone_number_id", name=f"uq_{NUMEROS}_phone_number_id"),
        sa.UniqueConstraint("canal_id", name=f"uq_{NUMEROS}_canal_id"),
        schema=SCHEMA,
    )

    # ── O cliente num número ─────────────────────────────────────────────
    op.create_table(
        CONTATOS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        _uuid("numero_ref", nullable=False),
        sa.Column("bsuid", sa.String(64), nullable=True),
        sa.Column("wa_id", sa.String(20), nullable=True),
        sa.Column("wa_id_norm", sa.String(20), nullable=True),
        sa.Column("nome_perfil", sa.String(120), nullable=True),
        _quando("ultima_recebida_em"),
        _quando("janela_gratis_ate"),
        _quando("opt_in_em"),
        sa.Column("opt_in_origem", sa.String(32), nullable=True),
        _quando("opt_out_em"),
        sa.Column("opt_out_tipo", sa.String(16), nullable=True),
        # O último gesto de preferência aplicado (a Meta não garante a ordem).
        _quando("preferencia_em"),
        _bool("bloqueado", False),
        _quando("apagado_em"),
        _uuid("conversa_id"),
        *_carimbos(),
        _check(CONTATOS, "opt_out_tipo", _em("opt_out_tipo", OPT_OUT_TIPOS, nulo=True)),
        # Nome à mão: pela convenção ficaria a 1 caractere do teto do Postgres.
        _fk(CONTATOS, "numero_ref", NUMEROS, "RESTRICT", nome="fk_atendimento_zap_contatos_numero"),
        _fk(CONTATOS, "conversa_id", "atendimento_conversas", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{CONTATOS}"),
        sa.UniqueConstraint("conversa_id", name=f"uq_{CONTATOS}_conversa_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_atendimento_zap_contatos_bsuid",
        CONTATOS,
        ["numero_ref", "bsuid"],
        unique=True,
        postgresql_where=sa.text("bsuid IS NOT NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_atendimento_zap_contatos_wa_id_norm",
        CONTATOS,
        ["numero_ref", "wa_id_norm"],
        unique=True,
        postgresql_where=sa.text("wa_id_norm IS NOT NULL"),
        schema=SCHEMA,
    )

    # ── A caixa de entrada do webhook ────────────────────────────────────
    op.create_table(
        EVENTOS,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("chave", sa.String(191), nullable=False),
        _uuid("app_ref"),
        sa.Column("waba_id", sa.String(32), nullable=True),
        sa.Column("phone_number_id", sa.String(32), nullable=True),
        sa.Column("campo", sa.String(48), nullable=True),
        sa.Column("tipo", sa.String(16), nullable=False),
        _jsonb("payload", "{}"),
        _quando("recebido_em", nullable=False, agora=True),
        _quando("processado_em"),
        _quando("proxima_em"),
        sa.Column("tentativas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("resultado", sa.String(32), nullable=True),
        sa.Column("erro", sa.String(200), nullable=True),
        _check(EVENTOS, "tipo", _em("tipo", EVENTO_TIPOS)),
        _check(EVENTOS, "resultado", _em("resultado", EVENTO_RESULTADOS, nulo=True)),
        _fk(EVENTOS, "app_ref", APPS, "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{EVENTOS}"),
        sa.UniqueConstraint("chave", name=f"uq_{EVENTOS}_chave"),
        schema=SCHEMA,
    )
    # A fila: só o que falta processar; o evento novo (`proxima_em` NULL)
    # primeiro. Quem pega usa `processado_em IS NULL AND (proxima_em IS NULL
    # OR proxima_em <= now())`.
    op.create_index(
        "ix_atendimento_zap_eventos_fila",
        EVENTOS,
        [sa.text("proxima_em NULLS FIRST"), "id"],
        postgresql_where=sa.text("processado_em IS NULL"),
        schema=SCHEMA,
    )

    # ── A mídia (o baixador é da Onda 3) ─────────────────────────────────
    op.create_table(
        MIDIAS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        _uuid("numero_ref", nullable=False),
        _uuid("mensagem_id"),
        sa.Column("direcao", sa.String(16), nullable=False),
        sa.Column("media_id", sa.String(64), nullable=True),
        sa.Column("mime", sa.String(100), nullable=True),
        sa.Column("mime_detectado", sa.String(100), nullable=True),
        sa.Column("sha256_meta", sa.String(100), nullable=True),
        sa.Column("tamanho", sa.BigInteger(), nullable=True),
        sa.Column("nome_original", sa.String(255), nullable=True),
        sa.Column("caminho", sa.String(255), nullable=True),
        sa.Column("sha256_local", sa.CHAR(64), nullable=True),
        sa.Column("status", sa.String(16), server_default=sa.text("'pendente'"), nullable=False),
        sa.Column("tentativas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _quando("proxima_em"),
        _quando("expira_meta_em"),
        _quando("apagar_em"),
        sa.Column("erro", sa.String(200), nullable=True),
        _quando("baixada_em"),
        *_carimbos(),
        _check(MIDIAS, "direcao", _em("direcao", MIDIA_DIRECOES)),
        _check(MIDIAS, "status", _em("status", MIDIA_STATUS)),
        _fk(MIDIAS, "numero_ref", NUMEROS, "RESTRICT"),
        _fk(MIDIAS, "mensagem_id", "atendimento_mensagens", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{MIDIAS}"),
        sa.UniqueConstraint("numero_ref", "media_id", name=f"uq_{MIDIAS}_numero_ref_media_id"),
        schema=SCHEMA,
    )
    op.create_index(f"ix_{MIDIAS}_mensagem_id", MIDIAS, ["mensagem_id"], schema=SCHEMA)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    # Filhas antes das mães; DROP TABLE leva junto os índices e as FKs.
    for tabela in (MIDIAS, EVENTOS, CONTATOS, NUMEROS, CONTAS, APPS):
        op.drop_table(tabela, schema=SCHEMA)
