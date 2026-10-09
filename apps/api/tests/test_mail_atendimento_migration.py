"""A 0387 (camada do atendimento por cima da Central de e-mail) cria EXATAMENTE
o que o model declara, em cima da 0386 dele — e o downgrade desfaz só o nosso.

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Aqui a 0386 (dele) e a 0387 (nossa) rodam de verdade num schema
descartável e o catálogo do Postgres dos dois lados é comparado: colunas (com
o padrão), CHECK, FK com ON DELETE, PK e índices das tabelas laterais.

E: uma ponta só (a 0387 vem DEPOIS da 0386, que já está em produção); as 4
tabelas dele saem da 0387 intocadas; a configuração, a ligação fila ↔
conversa e a regra de palavras são de pessoa (ficam no Histórico) e o que a
ponte decide (metadados e pastas) é da máquina (fica fora); a semente da
regra de palavras entra com ids fixos.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.historico import sql as hsql
from app.models import Base

_ALEMBIC = Path(__file__).resolve().parent.parent / "alembic"
_DELE = _ALEMBIC / "versions" / "0386_mail_central.py"
_NOSSA = _ALEMBIC / "versions" / "0387_mail_atendimento.py"
TABELAS = [
    "atendimento_regras_pasta_email",
    "mail_agente_v2",
    "mail_folders",
    "mail_mailbox_settings",
    "mail_message_meta",
    "mail_message_tuta",
    "mail_outbox_meta",
    "mail_reconciliation",
]
DE_PESSOA = ["atendimento_regras_pasta_email", "mail_mailbox_settings", "mail_outbox_meta"]
DELE = ["mail_attachments", "mail_mailboxes", "mail_messages", "mail_outbox"]
# As tabelas de fora que as nossas apontam (no schema descartável, só o id).
REFERIDAS = [
    "users",
    "atendimento_conversas",
    "atendimento_mensagens",
    "store_info",
    "integrations",
    "marcas",
]


def _carregar(caminho: Path):
    spec = importlib.util.spec_from_file_location(f"migration_{caminho.stem}", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_uma_ponta_so_e_depois_da_central():
    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC))
    scripts = ScriptDirectory.from_config(config)
    # Uma ponta só (hoje a própria 0387): duas pontas = `alembic upgrade head`
    # para no deploy.
    assert len(scripts.get_heads()) == 1
    nossa = scripts.get_revision("0387_mail_atendimento")
    assert nossa.down_revision == "0386_mail_central"
    assert nossa.revision in {item.revision for item in scripts.walk_revisions()}


def test_pessoa_fica_no_historico_e_a_maquina_fora():
    # De pessoa: quem ligou a ponte, quem passou o envio para "real", quem
    # resolveu um envio incerto, a palavra nova da regra. Da máquina (a ponte a
    # cada minuto): os metadados de cada e-mail e as pastas.
    assert hsql.a_cobrir(TABELAS) == DE_PESSOA


async def _catalogo(db: AsyncSession, schema: str, tabelas: list[str]) -> dict[str, list]:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "") if s else s

    colunas = (
        await db.execute(
            text(
                "SELECT table_name, column_name, udt_name, is_nullable, column_default,"
                " character_maximum_length FROM information_schema.columns"
                " WHERE table_schema = :schema AND table_name = ANY(:tabelas)"
                " ORDER BY table_name, column_name"
            ),
            {"schema": schema, "tabelas": tabelas},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                """
                SELECT cl.relname, co.conname, co.contype::text, pg_get_constraintdef(co.oid)
                  FROM pg_constraint co
                  JOIN pg_class cl ON cl.oid = co.conrelid
                  JOIN pg_namespace n ON n.oid = cl.relnamespace
                 WHERE n.nspname = :schema AND cl.relname = ANY(:tabelas)
                 ORDER BY cl.relname, co.conname
                """
            ),
            {"schema": schema, "tabelas": tabelas},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT tablename, indexname, indexdef FROM pg_indexes"
                " WHERE schemaname = :schema AND tablename = ANY(:tabelas)"
                " ORDER BY tablename, indexname"
            ),
            {"schema": schema, "tabelas": tabelas},
        )
    ).all()
    return {
        "tabelas": sorted({r[0] for r in colunas}),
        "colunas": [tuple(r[:4]) + (limpo(r[4]), r[5]) for r in colunas],
        "constraints": [(r[0], r[1], r[2], limpo(r[3])) for r in constraints],
        "indices": [(r[0], r[1], limpo(r[2])) for r in indices],
    }


@pytest.mark.asyncio
async def test_migration_bate_com_o_model_e_o_downgrade_desfaz(db: AsyncSession):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0387"
    dele, nossa = _carregar(_DELE), _carregar(_NOSSA)
    assert (nossa.revision, nossa.down_revision) == ("0387_mail_atendimento", "0386_mail_central")

    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    for tabela in REFERIDAS:
        await db.execute(text(f'CREATE TABLE "{rascunho}".{tabela} (id uuid PRIMARY KEY)'))
    await db.commit()

    def _rodar(conn, passos: list[tuple[object, str]]) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            for mod, passo in passos:
                getattr(mod, passo)()

    dele.SCHEMA = rascunho
    nossa.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, [(dele, "upgrade")])
        await db.commit()
        dele_antes = await _catalogo(db, rascunho, DELE)
        conn = await db.connection()
        await conn.run_sync(_rodar, [(nossa, "upgrade")])
        await db.commit()

        da_migration = await _catalogo(db, rascunho, TABELAS)
        do_model = await _catalogo(db, schema_model, TABELAS)
        assert da_migration["tabelas"] == TABELAS
        # Comparação que não compara nada passaria calada: o catálogo tem corpo.
        assert len(da_migration["colunas"]) >= 115
        assert len(da_migration["indices"]) >= 19
        nomes = {c[1] for c in da_migration["constraints"]}
        assert {
            "pk_mail_mailbox_settings",
            "fk_mail_mailbox_settings_mailbox_id_mail_mailboxes",
            "fk_mail_mailbox_settings_updated_by_users",
            "ck_mail_mailbox_settings_visibilidade",
            "ck_mail_mailbox_settings_envio_modo",
            "ck_mail_mailbox_settings_agente_tipo",
            "ck_mail_mailbox_settings_tetos",
            "ck_mail_mailbox_settings_destinatarios_lista",
            "ck_mail_mailbox_settings_agente_info_objeto",
            "pk_mail_folders",
            "uq_mail_folders_mailbox_id_chave",
            "ck_mail_folders_ler",
            "pk_mail_message_meta",
            "ck_mail_message_meta_estado",
            "fk_mail_message_meta_message_id_mail_messages",
            "fk_mail_message_meta_conversa_id_atendimento_conversas",
            "fk_mail_message_meta_mensagem_id_atendimento_mensagens",
            "fk_mail_message_meta_duplicado_de_mail_messages",
            "pk_mail_outbox_meta",
            "fk_mail_outbox_meta_outbox_id_mail_outbox",
            "uq_mail_outbox_meta_atendimento_mensagem_id",
            "ck_mail_outbox_meta_origem",
            "ck_mail_outbox_meta_resolucao",
            "uq_atendimento_regras_pasta_email_tipo_palavra",
            "ck_atendimento_regras_pasta_email_tipo",
            # O que o conector v2 escreve (máquina).
            "pk_mail_agente_v2",
            "fk_mail_agente_v2_mailbox_id_mail_mailboxes",
            "ck_mail_agente_v2_contadores_objeto",
            "ck_mail_agente_v2_aliases_lista",
            "pk_mail_message_tuta",
            "fk_mail_message_tuta_message_id_mail_messages",
            "pk_mail_reconciliation",
            "uq_mail_reconciliation_mailbox_id_folder_id_dia",
            "fk_mail_reconciliation_folder_id_mail_folders",
        } <= nomes
        assert da_migration["colunas"] == do_model["colunas"]
        assert da_migration["constraints"] == do_model["constraints"]
        assert da_migration["indices"] == do_model["indices"]

        fks = {nome: d for _, nome, tipo, d in da_migration["constraints"] if tipo == "f"}
        assert "ON DELETE CASCADE" in fks["fk_mail_mailbox_settings_mailbox_id_mail_mailboxes"]
        assert "ON DELETE SET NULL" in fks["fk_mail_mailbox_settings_updated_by_users"]
        # O e-mail apagado leva a meta; a conversa apagada só solta a ligação.
        assert "ON DELETE CASCADE" in fks["fk_mail_message_meta_message_id_mail_messages"]
        assert "ON DELETE SET NULL" in fks["fk_mail_message_meta_conversa_id_atendimento_conversas"]
        assert "ON DELETE CASCADE" in fks["fk_mail_outbox_meta_outbox_id_mail_outbox"]
        assert "ON DELETE CASCADE" in fks["fk_mail_folders_mailbox_id_mail_mailboxes"]
        # O que o v2 escreve some com a caixa, o e-mail e a pasta.
        assert "ON DELETE CASCADE" in fks["fk_mail_agente_v2_mailbox_id_mail_mailboxes"]
        assert "ON DELETE CASCADE" in fks["fk_mail_message_tuta_message_id_mail_messages"]
        assert "ON DELETE CASCADE" in fks["fk_mail_reconciliation_folder_id_mail_folders"]
        cols = {(c[0], c[1]): c for c in da_migration["colunas"]}
        padroes = {c[1]: c[4] for c in da_migration["colunas"] if c[0] == "mail_mailbox_settings"}
        assert padroes["visibilidade"].startswith("'privada'")
        assert padroes["ponte_ligada"] == "false"
        assert padroes["ponte_so_aliases_de_loja"] == "true"
        assert padroes["remetente_estrito"] == "false"
        assert padroes["envio_modo"].startswith("'teste'")
        assert (padroes["teto_hora"], padroes["teto_dia"], padroes["teto_conta_hora"]) == (
            "30",
            "300",
            "100",
        )
        assert cols[("mail_mailbox_settings", "ponte_desde")][3:5] == ("YES", None)

        # As 4 tabelas dele saem da 0387 exatamente como entraram.
        assert await _catalogo(db, rascunho, DELE) == dele_antes
        # A semente da regra de palavras, com ids fixos (a mesma do serviço).
        from app.services.mail_atendimento import regras as regras_svc

        semente = (
            await db.execute(
                text(
                    "SELECT tipo, palavra, valor FROM"
                    f' "{rascunho}".atendimento_regras_pasta_email ORDER BY id'
                )
            )
        ).all()
        assert [tuple(r) for r in semente] == list(regras_svc.SEMENTE)

        # A linha nasce só com a caixa e com os padrões (sem dado nenhum).
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".users (id) VALUES '
                "('00000000-0000-0000-0000-000000000001')"
            )
        )
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".mail_mailboxes (id, owner_user_id, label, config_enc,'
                " agent_token_hash, send_enabled, agent_can_send, state) VALUES"
                " ('00000000-0000-0000-0000-0000000000aa', '00000000-0000-0000-0000-000000000001',"
                " 'x', '\\x00', repeat('0', 64), false, false, 'offline')"
            )
        )
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".mail_mailbox_settings (mailbox_id)'
                " VALUES ('00000000-0000-0000-0000-0000000000aa')"
            )
        )
        await db.commit()
        linha = (
            await db.execute(
                text(
                    "SELECT visibilidade, envio_modo, destinatarios_teste::text, agente_info::text"
                    f' FROM "{rascunho}".mail_mailbox_settings'
                )
            )
        ).one()
        assert tuple(linha) == ("privada", "teste", "[]", "{}")
        for sql, ck in (
            ("visibilidade = 'publica'", "ck_mail_mailbox_settings_visibilidade"),
            ("envio_modo = 'producao'", "ck_mail_mailbox_settings_envio_modo"),
            ("teto_hora = -1", "ck_mail_mailbox_settings_tetos"),
            ("destinatarios_teste = '{}'::jsonb", "ck_mail_mailbox_settings_destinatarios_lista"),
        ):
            with pytest.raises(Exception, match=ck):
                await db.execute(text(f'UPDATE "{rascunho}".mail_mailbox_settings SET {sql}'))
            await db.rollback()
        # Apagar a caixa leva a configuração junto.
        await db.execute(text(f'DELETE FROM "{rascunho}".mail_mailboxes'))
        await db.commit()
        restam = await db.scalar(text(f'SELECT count(*) FROM "{rascunho}".mail_mailbox_settings'))
        assert restam == 0

        # O downgrade da 0387 tira só o que é nosso.
        conn = await db.connection()
        await conn.run_sync(_rodar, [(nossa, "downgrade")])
        await db.commit()
        depois = await _catalogo(db, rascunho, TABELAS)
        assert depois == {"tabelas": [], "colunas": [], "constraints": [], "indices": []}
        assert await _catalogo(db, rascunho, DELE) == dele_antes
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
