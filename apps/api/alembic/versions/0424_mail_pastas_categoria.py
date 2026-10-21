"""e-mail: a CATEGORIA da pasta criada (Venda · Chamado · Informativo · Site) manda no vínculo

Decisões do dono (Eduardo, 21/10/2026):
  1. A Entrada (INBOX), os Enviados e a caixa da Goslin ligam EXATAMENTE como
     hoje ("a entrada pode ligar ainda, não?"). A categoria vale só para as
     pastas CRIADAS pela equipe (pessoais).
  2. Pasta `venda` → a conversa de E-MAIL daquela venda (criada se não existe,
     também quando o nº ainda não chegou ao Bling, com alerta).
  3. Pasta `chamado` → Pós-venda › Chamados (a ligação existe, mas NENHUMA pasta
     nasce `chamado`: nada chega lá até alguém escolher).
  4. Pasta `informativo` → uma linha por e-mail no item da pasta (aba E-mail);
     nenhuma pasta nasce informativa.
  5. Semente: as 6 pastas "vendas *" (shopee, ml, amazon, temu, ali, magalu)
     = `venda`. O resto fica SEM categoria (NULL = não liga: o e-mail fica
     `ignorado`/`pasta_sem_categoria` e é relido quando alguém escolhe; a
     resposta num fio ou protocolo que a equipe já tem liga como sempre) —
     menos as do item 9.
  6. O passado não muda (nenhuma meta antiga é refeita).
  9. `site` ("Site (como hoje)", 4ª mensagem do dono: "as dos sites Uranyx e
     Charlots então pode manter como era; o resto já vem esse novo padrão"):
     a pasta liga EXATAMENTE como antes desta migration (a loja pelo endereço,
     o tipo pelo conteúdo, o chamado RF6 do site pelo protocolo, a conversa na
     aba E-mail). A semente dá `site` SÓ às pastas de site cuja marca é a da
     Uranyx ou a da Charlots (ver "A semente do site" abaixo).

Número: 0424 (o origin/main já tem a 0421 das devoluções, a 0422 dos fatos da
garantia Uranyx e a 0423 do fato Apple da Kia).

O que muda no banco:
  • `mail_folders.categoria` varchar(12) NULL + CHECK ('informativo', 'chamado',
    'venda', 'site'), `categoria_por` (FK users, SET NULL) e `categoria_em`;
  • o estado novo `informativo` em `mail_message_meta` (o CHECK é refeito);
  • `mail_message_meta.chamado_id` (FK chamados, SET NULL) + índice: o e-mail de
    pasta `chamado` ligado ao chamado da Pós-venda. É o ÚNICO vínculo: as
    tabelas dos chamados não mudam (regra do dono, 21/10: a Pós-venda só
    acrescenta — nunca cria chamado nem escreve no histórico dele).

A semente reconhece a pasta "vendas *" pelo que a regra de palavras JÁ gravou
(com a escolha de pessoa por cima): pasta pessoal (tipo do Tuta 0 ou 9), não
ignorada, que lê o corpo, finalidade `vendas` e plataforma de marketplace (o
site nunca é venda). Em produção (21/10/2026, só contagem): exatamente as 6.

A semente do site resolve a marca da pasta como a ponte resolve
(`regras.classificar` + `rotear._rota_do_site`): pasta pessoal, não ignorada,
que lê o corpo, plataforma `site`; a marca é a da primeira palavra do nome que
é palavra de MARCA na regra (`atendimento_regras_pasta_email`, as ativas; a
tabela vazia = a semente da 0387) e só vale se a marca EXISTE ativa em
`marcas` (a pasta "*marca" de marca fora do cadastro não é site). É Uranyx ou
Charlots quando o slug, o nome ou o domínio da marca dizem `uranyx`,
`charlots` ou `charlotspark`. Em produção (21/10/2026): "*uranyx sac" (marca
uranyx; sac@/atacado@/duvidas@uranyx.com.br são as caixas do site) e "*poofy"
(a marca `poofy` do cadastro tem o NOME "charlots" e o domínio charlots.com.br:
é o site da Charlots). "*makisa" fica sem categoria (não há marca `makisa` no
cadastro: a pasta liga às lojas Makisa pelo endereço, nunca ao site).
`categoria_por` fica NULL (foi a migration, não uma pessoa) e `categoria_em`
= agora.

DOWNGRADE: a linha informativa vira `ignorado` (motivo `pasta_informativa`)
antes de o estado sair do CHECK; as metas `ignorado` que só ficaram assim pela
regra nova (`pasta_sem_categoria`, `chamado_sem_pedido`, `chamado_sem_chamado`,
sem conversa nem mensagem) são APAGADAS — a ponte de antes as relê e liga como ligava. As
colunas saem.

`lock_timeout` de 10 s, como as outras do e-mail: se a ponte segurar uma linha
por mais que isso, a migration falha inteira (nada gravado) e é só rodar de
novo.

Revision ID: 0424_mail_pastas_categoria
Revises: 0423_atendimento_fato_apple_kia
"""

import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0424_mail_pastas_categoria"
down_revision: str | None = "0423_atendimento_fato_apple_kia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
PASTAS = "mail_folders"
META = "mail_message_meta"
REGRAS = "atendimento_regras_pasta_email"

# Congelados aqui (a migration não muda quando o código mudar).
CATEGORIAS = ("informativo", "chamado", "venda", "site")
ESTADOS_ANTES = (
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
ESTADOS_DEPOIS = (*ESTADOS_ANTES, "informativo")
# As plataformas de marketplace da regra de palavras (sem o site).
MARKETPLACES = ("ml", "shopee", "amazon", "tiktok", "temu", "magalu", "aliexpress", "shein")
# MailSetKind do Tuta: 0 = pasta pessoal, 9 = importados.
TIPOS_CRIADAS = ("0", "9")
# As palavras de MARCA da semente da 0387 (`regras.SEMENTE`): valem só com a
# tabela de regras VAZIA (como `regras.carregar`).
MARCAS_DA_SEMENTE = (
    ("uranyx", "uranyx"),
    ("charlots", "charlots-park"),
    ("7buyers", "7buyers"),
    ("poofy", "poofy"),
    ("makisa", "makisa"),
    ("locagil", "locagil"),
)
# Decisão 9 do dono: os sites que ficam "como hoje" — a marca cujo slug, nome
# ou domínio diz uma destas palavras.
SITES_COMO_HOJE = ("uranyx", "charlots", "charlotspark")
_PALAVRA = re.compile(r"[a-z0-9]+")


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _palavras(nome: str | None) -> list[str]:
    """O nome em palavras, sem acento e minúsculo (= `regras.palavras`)."""
    base = unicodedata.normalize("NFKD", nome or "")
    return _PALAVRA.findall("".join(c for c in base if not unicodedata.combining(c)).lower())


def _dominios(*brutos: str | None) -> list[str]:
    """Os domínios da marca (= `rotear._dominios_da_marca`)."""
    saida: list[str] = []
    for bruto in brutos:
        for parte in (bruto or "").replace(";", ",").split(","):
            d = parte.strip().lower().removeprefix("https://").removeprefix("http://")
            d = d.removeprefix("www.").split("/")[0].strip(".")
            if d and "." in d and d not in saida:
                saida.append(d)
    return saida


def _e_site_como_hoje(slug: str, nome: str | None, dominios: list[str]) -> bool:
    """A marca é a da Uranyx ou a da Charlots (pelo slug, pelo nome ou pelo domínio)?"""
    ditas = set(_palavras(slug)) | set(_palavras(nome)) | {d.split(".")[0] for d in dominios}
    return bool(ditas & set(SITES_COMO_HOJE))


def _semear_sites(bind) -> list[str]:
    """Decisão 9: `site` nas pastas de site da Uranyx e da Charlots. Devolve os nomes."""
    pastas = bind.execute(
        sa.text(
            f'SELECT id, nome FROM "{SCHEMA}".{PASTAS}'  # noqa: S608 — constantes
            " WHERE categoria IS NULL"
            f" AND coalesce(tipo_tuta, '0') IN ({_lista(TIPOS_CRIADAS)})"
            " AND ignorar IS false"
            " AND ler = 'corpo'"
            " AND coalesce(plataforma_manual, plataforma) = 'site'"
        )
    ).all()
    if not pastas:
        return []
    regras = bind.execute(
        sa.text(f'SELECT tipo, palavra, valor, ativa FROM "{SCHEMA}".{REGRAS}')  # noqa: S608
    ).all()
    if regras:
        da_marca = {
            "".join(_palavras(palavra)): (valor or "").strip().lower()
            for tipo, palavra, valor, ativa in regras
            if tipo == "marca" and ativa and len(_palavras(palavra)) == 1
        }
    else:
        da_marca = dict(MARCAS_DA_SEMENTE)
    marcas = {
        (slug or "").lower(): (nome, _dominios(dominio_br, dominio))
        for slug, nome, dominio, dominio_br in bind.execute(
            sa.text(
                f'SELECT slug, nome, dominio, dominio_br FROM "{SCHEMA}".marcas'  # noqa: S608
                " WHERE ativo IS true"
            )
        ).all()
    }
    marcadas: list[str] = []
    for pasta_id, nome in pastas:
        slug = next((da_marca[p] for p in _palavras(nome) if p in da_marca), None)
        if slug is None or slug not in marcas:
            continue  # sem marca, ou marca fora do cadastro: a ponte não a acha como site
        if not _e_site_como_hoje(slug, *marcas[slug]):
            continue
        bind.execute(
            sa.text(
                f'UPDATE "{SCHEMA}".{PASTAS}'  # noqa: S608
                " SET categoria = 'site', categoria_em = now(), revisada = true"
                " WHERE id = :id AND categoria IS NULL"
            ),
            {"id": pasta_id},
        )
        marcadas.append(nome)
    return marcadas


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    # ── mail_folders: a categoria e quem/quando escolheu ──
    op.add_column(PASTAS, sa.Column("categoria", sa.String(12)), schema=SCHEMA)
    op.add_column(PASTAS, sa.Column("categoria_por", pg.UUID(as_uuid=True)), schema=SCHEMA)
    op.add_column(PASTAS, sa.Column("categoria_em", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_mail_folders_categoria"),
        PASTAS,
        f"categoria IS NULL OR categoria IN ({_lista(CATEGORIAS)})",
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_mail_folders_categoria_por_users"),
        PASTAS,
        "users",
        ["categoria_por"],
        ["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    # ── mail_message_meta: o estado `informativo` e o chamado da Pós-venda ──
    op.drop_constraint(op.f("ck_mail_message_meta_estado"), META, type_="check", schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_mail_message_meta_estado"),
        META,
        f"estado IN ({_lista(ESTADOS_DEPOIS)})",
        schema=SCHEMA,
    )
    op.add_column(META, sa.Column("chamado_id", pg.UUID(as_uuid=True)), schema=SCHEMA)
    op.create_foreign_key(
        op.f("fk_mail_message_meta_chamado_id_chamados"),
        META,
        "chamados",
        ["chamado_id"],
        ["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_index(op.f("ix_mail_message_meta_chamado_id"), META, ["chamado_id"], schema=SCHEMA)
    # ── a semente (decisão 5): só as pastas "vendas *" de marketplace = venda ──
    op.execute(
        sa.text(
            f'UPDATE "{SCHEMA}".{PASTAS}'  # noqa: S608 — constantes da própria migration
            " SET categoria = 'venda', categoria_em = now(), revisada = true"
            " WHERE categoria IS NULL"
            f" AND coalesce(tipo_tuta, '0') IN ({_lista(TIPOS_CRIADAS)})"
            " AND ignorar IS false"
            " AND ler = 'corpo'"
            " AND coalesce(finalidade_manual, finalidade) = 'vendas'"
            f" AND coalesce(plataforma_manual, plataforma) IN ({_lista(MARKETPLACES)})"
        )
    )
    # ── decisão 9: as pastas de site da Uranyx e da Charlots = site ("como hoje") ──
    _semear_sites(op.get_bind())


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    # A linha informativa não cabe no CHECK de antes: vira `ignorado`.
    op.execute(
        sa.text(
            f'UPDATE "{SCHEMA}".{META}'  # noqa: S608
            " SET estado = 'ignorado', motivo = 'pasta_informativa'"
            " WHERE estado = 'informativo'"
        )
    )
    # O que só ficou `ignorado` pela regra nova (nunca chegou a conversa nem a
    # chamado): sem a meta, a ponte de antes relê e liga como ligava.
    op.execute(
        sa.text(
            f'DELETE FROM "{SCHEMA}".{META}'  # noqa: S608
            " WHERE estado = 'ignorado'"
            " AND motivo IN ('pasta_sem_categoria', 'chamado_sem_pedido', 'chamado_sem_chamado')"
            " AND conversa_id IS NULL AND mensagem_id IS NULL AND chamado_id IS NULL"
        )
    )
    op.drop_index(op.f("ix_mail_message_meta_chamado_id"), META, schema=SCHEMA)
    op.drop_constraint(
        op.f("fk_mail_message_meta_chamado_id_chamados"), META, type_="foreignkey", schema=SCHEMA
    )
    op.drop_column(META, "chamado_id", schema=SCHEMA)
    op.drop_constraint(op.f("ck_mail_message_meta_estado"), META, type_="check", schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_mail_message_meta_estado"),
        META,
        f"estado IN ({_lista(ESTADOS_ANTES)})",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("fk_mail_folders_categoria_por_users"), PASTAS, type_="foreignkey", schema=SCHEMA
    )
    op.drop_constraint(op.f("ck_mail_folders_categoria"), PASTAS, type_="check", schema=SCHEMA)
    op.drop_column(PASTAS, "categoria_em", schema=SCHEMA)
    op.drop_column(PASTAS, "categoria_por", schema=SCHEMA)
    op.drop_column(PASTAS, "categoria", schema=SCHEMA)
