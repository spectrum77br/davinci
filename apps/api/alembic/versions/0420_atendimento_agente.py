"""atendimento: o AGENTE (Claude) — fatos da loja, pares da equipe, replay e sites da marca

Eduardo, 20/10/2026: "quando eu botar o Haiku já quero funcionando com o
padrão novo dele, ser mais independente". O agente de atendimento (Claude
Haiku 5.5 com ferramentas SÓ DE LEITURA) entra no lugar da IA de uma chamada
canal por canal; sem chave Claude, tudo segue no legado sem mudança.

O que muda (só aditivo):
  • `atendimento_canais.agente` (bool, padrão false): o botão "Agente
    (Claude)" da tela de canais, só admin;
  • `atendimento_canais.auto_orientacao` (jsonb, padrão `[]`): os assuntos de
    orientação que o rascunho do agente pode mandar sozinho no canal;
  • `atendimento_canais.auto_agente` (jsonb, padrão `[]`): os OUTROS assuntos
    (dos `auto_categorias` do canal) em que o rascunho do agente pode sair
    sozinho — o dono marca depois do eval, só onde o agente empatou ou ganhou
    do legado (decisão 8 da spec, revisão de 20/10/2026);
  • `atendimento_fatos`: fatos da loja com escopo (global, plataforma, loja,
    anúncio, produto, SKU, marca), ativo só com aprovação (CHECK), `tsv` com
    índice GIN calculado pela aplicação (`unaccent` não é IMMUTABLE; o
    `to_tsvector('portuguese')` daqui não depende de extensão — em produção
    só há citext, pgcrypto e unaccent). Fica COM o gatilho do Histórico;
  • `atendimento_pares`: pergunta × resposta da equipe, mascaradas e
    generalizadas, UNIQUE (conversa, resposta), GIN no `tsv` e btree em
    (plataforma, loja, anúncio);
  • `atendimento_agente_replay`: o replay noturno (o que o agente teria feito
    em cada gatilho), UNIQUE (dia, gatilho);
  • `atendimento_site_produtos`: o instantâneo das páginas de produto dos
    sites da marca, UNIQUE (marca, slug).
`atendimento_pares`, `atendimento_agente_replay` e `atendimento_site_produtos`
são escritas pela máquina: ficam FORA do Histórico (`historico/sql.EXCLUIDAS`).

SEMENTE dos fatos (idempotente pela `chave`; os textos ficam CONGELADOS aqui,
como a semente da 0366):
  • ATIVOS — o que já está aprovado no código e no manual de produção (o
    `faca` das regras `espec_*` é exatamente o de `ficha_respostas.
    REGRAS_APROVADAS`, conferido em 20/10): os quatro fatos do celular
    (desbloqueado, RAM física × virtual, Wi-Fi 5 GHz não é 5G, o S5 tem NFC),
    o código M da mala (design, não tamanho), a air fryer que não é bivolt, a
    pulseira padrão dos Apple Watch aprovados (um fato por produto de
    `politicas_atendimento.IDS_PRODUTOS_PULSEIRA_PADRAO`, só os que existem) e
    a origem São Paulo do anúncio do Apple Watch (`_APROVACAO_WATCH`, só se a
    loja existe);
  • CANDIDATOS (`ativo=false`, esperam o dono na aba do Manual): capa de
    outro modelo, NF junto, peças avulsas, atualização de sistema, onde vai o
    chip, o remetente com o nome do cliente, as três recusas padrão e a
    garantia de 90 dias da Charlots, do site (revisão de 20/10/2026: o dono
    aprovou o site como FONTE; o prazo de garantia é texto de política —
    decisão 4, candidato até ele aprovar).

`lock_timeout` de 3 s, como as outras do atendimento (as FKs novas pegam
ShareRowExclusiveLock em integrations, products, canais, conversas e
mensagens). `tests/test_atendimento_agente_esquema.py` roda esta num schema
descartável e compara com o model.

O downgrade apaga as quatro tabelas (fatos, pares e replay se perdem) e as
três colunas do canal.

Revision ID: 0420_atendimento_agente
Revises: 0419_londres_logistica_editar
"""

import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.config import get_settings

revision: str = "0420_atendimento_agente"
down_revision: str | None = "0419_londres_logistica_editar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = get_settings().database_schema
CANAIS = "atendimento_canais"
FATOS = "atendimento_fatos"
PARES = "atendimento_pares"
REPLAY = "atendimento_agente_replay"
SITES = "atendimento_site_produtos"

_CODIGO = "regra aprovada no código"
_REF_CELULAR = 'ficha_respostas.REGRAS_APROVADAS["espec_celular"]'
_REF_SPEC = "spec do agente 5.2 (20/10/2026): candidato"
# Os fatos semeados — congelados em 20/10/2026. `escopo` global salvo dito.
SEMENTE: tuple[dict, ...] = (
    # Aprovados no código (`ficha_respostas.REGRAS_APROVADAS`, ativos no manual).
    {
        "chave": "celular_desbloqueado",
        "assunto": "espec_celular",
        "texto": "O celular é desbloqueado: não vem preso a nenhuma operadora.",
        "ativo": True,
        "referencia": _REF_CELULAR,
    },
    {
        "chave": "celular_ram_virtual",
        "assunto": "espec_celular",
        "texto": (
            "Quando o anúncio soma a RAM física e a virtual, são duas memórias "
            "diferentes: a RAM virtual usa parte do armazenamento interno."
        ),
        "ativo": True,
        "referencia": _REF_CELULAR,
    },
    {
        "chave": "celular_wifi_5ghz",
        "assunto": "espec_celular",
        "texto": "Wi-Fi de 5 GHz é a faixa do Wi-Fi; não é a rede móvel 5G.",
        "ativo": True,
        "referencia": _REF_CELULAR,
    },
    {
        "chave": "uranyx_s5_nfc",
        "escopo": "marca",
        "marca": "uranyx",
        "assunto": "espec_celular",
        "texto": "O Uranyx S5 tem NFC.",
        "ativo": True,
        "referencia": _REF_CELULAR,
    },
    {
        "chave": "mala_codigo_m",
        "assunto": "espec_mala",
        "texto": (
            "Os códigos de modelo com a letra M e um número são nomes de design "
            "(formato e cor), não de tamanho."
        ),
        "ativo": True,
        "referencia": 'ficha_respostas.REGRAS_APROVADAS["espec_mala"]',
    },
    {
        "chave": "airfryer_nao_bivolt",
        "escopo": "marca",
        "marca": "uranyx",
        "assunto": "espec_eletro",
        "texto": (
            "A air fryer Uranyx não é bivolt: há versões de 110 V e de 220 V, "
            "escolhidas na variação antes de finalizar a compra."
        ),
        "ativo": True,
        "referencia": 'ficha_respostas.REGRAS_APROVADAS["espec_eletro"]',
    },
    # A origem aprovada do anúncio do Apple Watch (Shopee).
    {
        "chave": "origem_envio_shopee_22299473628",
        "escopo": "anuncio",
        "plataforma": "shopee",
        "integration_id": "2ac2633b-c698-4718-99cb-6e5443a80689",
        "item_id": "22299473628",
        "assunto": None,
        "texto": "O envio deste produto é feito de São Paulo.",
        "ativo": True,
        "referencia": "politicas_atendimento._APROVACAO_WATCH",
    },
    # Candidatos: esperam o dono. O prazo de garantia do site da Charlots é
    # texto de política (decisão 4): o dono aprovou o site como FONTE, não o
    # texto — candidato até ele aprovar.
    {
        "chave": "charlots_garantia_90_dias",
        "escopo": "marca",
        "marca": "charlots",
        "assunto": "garantia",
        "texto": "As malas Charlots têm garantia de 90 dias contra defeitos de fabricação.",
        "fonte": "site",
        "referencia": "charlots.com.br/produto/<slug> (toda página de produto)",
    },
    {
        "chave": "capa_outro_modelo",
        "assunto": "acessorios_avulsos",
        "texto": (
            "Capinha e película são feitas para as medidas de cada modelo; as de "
            "outro modelo não servem."
        ),
    },
    {
        "chave": "nf_vai_junto",
        "assunto": "nota_fiscal",
        "texto": "Todos os produtos são enviados com nota fiscal, que vai junto com o pedido.",
    },
    {
        "chave": "pecas_avulsas",
        "assunto": "acessorios_avulsos",
        "texto": (
            "Peças e acessórios avulsos só estão à venda quando há um anúncio "
            "próprio deles na loja."
        ),
    },
    {
        "chave": "atualizacao_sistema",
        "assunto": "uso_configuracao",
        "texto": (
            "Quando há atualização de sistema para o modelo, ela aparece no "
            "próprio celular, nas configurações de atualização do sistema."
        ),
    },
    {
        "chave": "onde_vai_o_chip",
        "assunto": "uso_configuracao",
        "texto": (
            "O chip vai na gaveta de chip do celular, que abre com um pino no furinho ao lado dela."
        ),
    },
    {
        "chave": "remetente_etiqueta",
        "assunto": "rastreio",
        "texto": (
            "Por padrão de envio da loja, a etiqueta traz o seu nome também como "
            "remetente; o pedido é seu e segue normalmente pelo rastreio."
        ),
        "referencia": "padrão da etiqueta da loja (nf_etiqueta_transform.py)",
    },
    {
        "chave": "recusa_desconto",
        "assunto": "desconto",
        "texto": (
            "O valor do anúncio é o praticado pela loja; os cupons ativos aparecem "
            "na própria página do anúncio."
        ),
        "fonte": "recusa_padrao",
    },
    {
        "chave": "recusa_brinde_amostra",
        "assunto": "desconto",
        "texto": "A loja não envia brinde nem amostra.",
        "fonte": "recusa_padrao",
    },
    {
        "chave": "recusa_troca_pagando_diferenca",
        "assunto": "troca_devolucao",
        "texto": (
            "Não fazemos troca pagando a diferença; a troca segue pela devolução "
            "do próprio pedido no app."
        ),
        "fonte": "recusa_padrao",
    },
)
# A pulseira padrão: um fato por produto aprovado pela equipe
# (`politicas_atendimento.IDS_PRODUTOS_PULSEIRA_PADRAO`, 20/10/2026).
PRODUTOS_PULSEIRA = (
    "377c85df-57d7-43f8-b832-4e7884bf9c3e",
    "5158a6f8-8d5e-40a2-bc4a-f1240e6a5ddd",
    "cbdf0902-c63e-4abb-a8dd-8b3ae98fec58",
    "df0823b8-0674-4f50-896c-179364acb823",
    "ef352399-7446-4e6b-a82b-3a2ff106a05f",
)
TEXTO_PULSEIRA = (
    "A pulseira deste Apple Watch tem tamanho padrão; isso não escolhe P/M ou M/G "
    "nem confirma o ajuste no braço."
)


def _busca(texto: str) -> str:
    """O mesmo de `services/atendimento/busca_texto.texto_de_busca` (congelado)."""
    base = unicodedata.normalize("NFKD", texto or "")
    return " ".join("".join(c for c in base if not unicodedata.combining(c)).lower().split())


def _uuid(nome: str, nullable: bool = True) -> sa.Column:
    return sa.Column(nome, pg.UUID(as_uuid=True), nullable=nullable)


def _quando(nome: str, nullable: bool = True, agora: bool = False) -> sa.Column:
    return sa.Column(
        nome,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if agora else None,
        nullable=nullable,
    )


def _jsonb(nome: str, vazio: str) -> sa.Column:
    return sa.Column(nome, pg.JSONB(), server_default=sa.text(f"'{vazio}'::jsonb"), nullable=False)


def _semear() -> None:
    conn = op.get_bind()
    inserir = sa.text(
        f'INSERT INTO "{SCHEMA}".{FATOS} (chave, escopo, plataforma, integration_id, '  # noqa: S608
        "item_id, product_id, marca, assunto, texto, fonte, referencia, ativo, aprovado_por, "
        "aprovado_em, tsv) "
        "SELECT :chave, :escopo, :plataforma, CAST(:integration_id AS uuid), :item_id, "
        "CAST(:product_id AS uuid), :marca, :assunto, :texto, :fonte, :referencia, "
        "CAST(:ativo AS boolean), :aprovado_por, "
        "CASE WHEN CAST(:ativo AS boolean) THEN now() END, "
        "setweight(to_tsvector('portuguese'::regconfig, :busca), 'A') "
        # Fato de loja ou de produto que não existe neste banco não nasce.
        "WHERE (CAST(:integration_id AS uuid) IS NULL OR EXISTS (SELECT 1 FROM "
        f'"{SCHEMA}".integrations WHERE id = CAST(:integration_id AS uuid))) '
        "AND (CAST(:product_id AS uuid) IS NULL OR EXISTS (SELECT 1 FROM "
        f'"{SCHEMA}".products WHERE id = CAST(:product_id AS uuid))) '
        "ON CONFLICT (chave) DO NOTHING"
    )
    linhas = [*SEMENTE]
    for produto in PRODUTOS_PULSEIRA:
        linhas.append(
            {
                "chave": f"pulseira_padrao:{produto}",
                "escopo": "produto",
                "product_id": produto,
                "assunto": "espec_outros",
                "texto": TEXTO_PULSEIRA,
                "ativo": True,
                "referencia": "politicas_atendimento.IDS_PRODUTOS_PULSEIRA_PADRAO",
            }
        )
    for f in linhas:
        ativo = bool(f.get("ativo", False))
        conn.execute(
            inserir,
            {
                "chave": f["chave"],
                "escopo": f.get("escopo", "global"),
                "plataforma": f.get("plataforma"),
                "integration_id": f.get("integration_id"),
                "item_id": f.get("item_id"),
                "product_id": f.get("product_id"),
                "marca": f.get("marca"),
                "assunto": f.get("assunto"),
                "texto": f["texto"],
                "fonte": f.get("fonte", "manual"),
                "referencia": f.get("referencia", _REF_SPEC),
                "ativo": ativo,
                "aprovado_por": f.get("aprovado_por", _CODIGO) if ativo else None,
                "busca": _busca(f["texto"]),
            },
        )


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.add_column(
        CANAIS,
        sa.Column("agente", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(CANAIS, _jsonb("auto_orientacao", "[]"), schema=SCHEMA)
    op.add_column(CANAIS, _jsonb("auto_agente", "[]"), schema=SCHEMA)

    op.create_table(
        FATOS,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chave", sa.String(64), nullable=True),
        sa.Column("escopo", sa.String(16), server_default=sa.text("'global'"), nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=True),
        _uuid("integration_id"),
        sa.Column("item_id", sa.String(64), nullable=True),
        _uuid("product_id"),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("marca", sa.String(16), nullable=True),
        sa.Column("assunto", sa.String(32), nullable=True),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("fonte", sa.String(16), nullable=False),
        sa.Column("referencia", sa.Text(), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("aprovado_por", sa.Text(), nullable=True),
        _quando("aprovado_em"),
        _quando("valido_ate"),
        sa.Column("tsv", pg.TSVECTOR(), nullable=False),
        _quando("created_at", nullable=False, agora=True),
        _quando("updated_at", nullable=False, agora=True),
        # Nome final (`op.f`): sem passar de novo pela convenção ck_<tabela>_<nome>.
        sa.CheckConstraint(
            "escopo IN ('global', 'plataforma', 'loja', 'anuncio', 'produto', 'sku', 'marca')",
            name=op.f("ck_atendimento_fatos_escopo"),
        ),
        sa.CheckConstraint(
            "fonte IN ('manual', 'frase_rapida', 'correcao', 'dono', 'recusa_padrao', 'site')",
            name=op.f("ck_atendimento_fatos_fonte"),
        ),
        sa.CheckConstraint(
            "(escopo <> 'plataforma' OR plataforma IS NOT NULL)"
            " AND (escopo <> 'loja' OR integration_id IS NOT NULL)"
            " AND (escopo <> 'anuncio' OR (plataforma IS NOT NULL AND item_id IS NOT NULL))"
            " AND (escopo <> 'produto' OR product_id IS NOT NULL)"
            " AND (escopo <> 'sku' OR sku IS NOT NULL)"
            " AND (escopo <> 'marca' OR marca IS NOT NULL)",
            name=op.f("ck_atendimento_fatos_escopo_preenchido"),
        ),
        sa.CheckConstraint(
            "NOT ativo OR aprovado_por IS NOT NULL",
            name=op.f("ck_atendimento_fatos_ativo_aprovado"),
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            name="fk_atendimento_fatos_integration_id_integrations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            [f"{SCHEMA}.products.id"],
            name="fk_atendimento_fatos_product_id_products",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_fatos"),
        sa.UniqueConstraint("chave", name="uq_atendimento_fatos_chave"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_fatos_tsv", FATOS, ["tsv"], postgresql_using="gin", schema=SCHEMA
    )

    op.create_table(
        PARES,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        _uuid("conversa_id", nullable=False),
        _uuid("resposta_msg_id", nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=False),
        _uuid("integration_id"),
        _uuid("canal_id"),
        sa.Column("item_id", sa.String(64), nullable=True),
        sa.Column("assunto", sa.String(32), nullable=True),
        sa.Column("pergunta_gen", sa.Text(), nullable=False),
        sa.Column("resposta_gen", sa.Text(), nullable=False),
        sa.Column("fonte", sa.String(24), nullable=False),
        sa.Column("peso", sa.Float(), server_default=sa.text("1"), nullable=False),
        sa.Column("autor_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _quando("perguntado_em"),
        _quando("respondido_em", nullable=False),
        sa.Column("tsv", pg.TSVECTOR(), nullable=False),
        _quando("created_at", nullable=False, agora=True),
        _quando("updated_at", nullable=False, agora=True),
        sa.ForeignKeyConstraint(
            ["conversa_id"],
            [f"{SCHEMA}.atendimento_conversas.id"],
            name="fk_atendimento_pares_conversa_id_atendimento_conversas",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resposta_msg_id"],
            [f"{SCHEMA}.atendimento_mensagens.id"],
            name="fk_atendimento_pares_resposta_msg_id_atendimento_mensagens",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            name="fk_atendimento_pares_integration_id_integrations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["canal_id"],
            [f"{SCHEMA}.atendimento_canais.id"],
            name="fk_atendimento_pares_canal_id_atendimento_canais",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_pares"),
        sa.UniqueConstraint(
            "conversa_id",
            "resposta_msg_id",
            name="uq_atendimento_pares_conversa_id_resposta_msg_id",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_atendimento_pares_tsv", PARES, ["tsv"], postgresql_using="gin", schema=SCHEMA
    )
    op.create_index(
        "ix_atendimento_pares_plataforma_integration_id_item_id",
        PARES,
        ["plataforma", "integration_id", "item_id"],
        schema=SCHEMA,
    )
    op.create_index("ix_atendimento_pares_respondido_em", PARES, ["respondido_em"], schema=SCHEMA)

    op.create_table(
        REPLAY,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("dia", sa.Date(), nullable=False),
        _uuid("conversa_id", nullable=False),
        _uuid("gatilho_id", nullable=False),
        _quando("gatilho_em", nullable=False),
        sa.Column("plataforma", sa.String(16), nullable=False),
        _uuid("integration_id"),
        _jsonb("proposta", "{}"),
        _jsonb("decisao", "{}"),
        _jsonb("rastro", "{}"),
        sa.Column("resposta_real", sa.Text(), nullable=True),
        sa.Column("modelo", sa.String(128), nullable=True),
        sa.Column("effort", sa.String(16), nullable=True),
        sa.Column("parada", sa.String(32), nullable=True),
        sa.Column("turnos", sa.SmallInteger(), nullable=True),
        sa.Column("ms", sa.Integer(), nullable=True),
        _jsonb("uso", "{}"),
        sa.Column("custo_usd", sa.Numeric(12, 6), nullable=True),
        _quando("criado_em", nullable=False, agora=True),
        sa.ForeignKeyConstraint(
            ["conversa_id"],
            [f"{SCHEMA}.atendimento_conversas.id"],
            name="fk_atendimento_agente_replay_conversa_id_atendimento_conversas",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_agente_replay"),
        sa.UniqueConstraint(
            "dia", "gatilho_id", name="uq_atendimento_agente_replay_dia_gatilho_id"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        SITES,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("marca", sa.String(16), nullable=False),
        sa.Column("slug", sa.String(191), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("modelo_chave", sa.String(64), nullable=True),
        _jsonb("specs", "[]"),
        sa.Column("resumo", sa.Text(), nullable=True),
        sa.Column("garantia_texto", sa.Text(), nullable=True),
        _quando("lido_em", nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        _quando("created_at", nullable=False, agora=True),
        _quando("updated_at", nullable=False, agora=True),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_site_produtos"),
        sa.UniqueConstraint("marca", "slug", name="uq_atendimento_site_produtos_marca_slug"),
        schema=SCHEMA,
    )

    _semear()


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    # DROP TABLE leva junto os índices, as FKs e a sequência do id.
    for tabela in (SITES, REPLAY, PARES, FATOS):
        op.drop_table(tabela, schema=SCHEMA)
    op.drop_column(CANAIS, "auto_agente", schema=SCHEMA)
    op.drop_column(CANAIS, "auto_orientacao", schema=SCHEMA)
    op.drop_column(CANAIS, "agente", schema=SCHEMA)
