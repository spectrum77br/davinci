"""A 0388 (o índice leve da aba E-mail › Caixas) cria EXATAMENTE o que o model
declara, em cima da 0386 (dele) e da 0387 (nossa) — e o downgrade desfaz só ela.

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Aqui as três rodam de verdade num schema descartável e o catálogo
do Postgres dos dois lados é comparado (colunas com o padrão, CHECK, FK com
ON DELETE, PK e índices). E: uma ponta só (a 0388 vem DEPOIS da 0387); as
tabelas dele e as da 0387 saem da 0388 intocadas; o índice é da máquina (fica
fora do Histórico); apagar o e-mail (ou a caixa) leva a linha junto.
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
from tests.test_mail_atendimento_migration import (
    DELE,
    REFERIDAS,
    _catalogo,
)
from tests.test_mail_atendimento_migration import TABELAS as DA_0387

_ALEMBIC = Path(__file__).resolve().parent.parent / "alembic"
_VERSOES = _ALEMBIC / "versions"
TABELAS = ["mail_caixa_indice"]


def _carregar(caminho: Path):
    spec = importlib.util.spec_from_file_location(f"migration_{caminho.stem}", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_uma_ponta_so_e_depois_da_0387():
    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    nossa = scripts.get_revision("0388_mail_caixa_indice")
    assert nossa.down_revision == "0387_mail_atendimento"


def test_o_indice_e_da_maquina_e_fica_fora_do_historico():
    assert hsql.a_cobrir(TABELAS) == []


@pytest.mark.asyncio
async def test_migration_bate_com_o_model_e_o_downgrade_desfaz(db: AsyncSession):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0388"
    dele = _carregar(_VERSOES / "0386_mail_central.py")
    camada = _carregar(_VERSOES / "0387_mail_atendimento.py")
    nossa = _carregar(_VERSOES / "0388_mail_caixa_indice.py")
    assert (nossa.revision, nossa.down_revision) == (
        "0388_mail_caixa_indice",
        "0387_mail_atendimento",
    )

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

    for mod in (dele, camada, nossa):
        mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, [(dele, "upgrade"), (camada, "upgrade")])
        await db.commit()
        antes = await _catalogo(db, rascunho, DELE + DA_0387)
        conn = await db.connection()
        await conn.run_sync(_rodar, [(nossa, "upgrade")])
        await db.commit()

        da_migration = await _catalogo(db, rascunho, TABELAS)
        do_model = await _catalogo(db, schema_model, TABELAS)
        assert da_migration["tabelas"] == TABELAS
        assert len(da_migration["colunas"]) == 15
        assert {i[1] for i in da_migration["indices"]} == {
            "pk_mail_caixa_indice",
            "ix_mail_caixa_indice_caixa_recebido",
            "ix_mail_caixa_indice_caixa_pasta",
        }
        # (O Postgres 18 também lista os NOT NULL como constraint: só os de verdade.)
        nomes = {c[1] for c in da_migration["constraints"] if c[2] in ("p", "c", "f", "u")}
        assert nomes == {
            "pk_mail_caixa_indice",
            "ck_mail_caixa_indice_selo",
            "fk_mail_caixa_indice_message_id_mail_messages",
        }
        assert da_migration["colunas"] == do_model["colunas"]
        assert da_migration["constraints"] == do_model["constraints"]
        assert da_migration["indices"] == do_model["indices"]
        fks = {nome: d for _, nome, tipo, d in da_migration["constraints"] if tipo == "f"}
        assert "ON DELETE CASCADE" in fks["fk_mail_caixa_indice_message_id_mail_messages"]
        # Sem FK na caixa, de propósito: a gravação do índice não pega FOR KEY
        # SHARE na linha que o agente do Mac trava com FOR UPDATE a cada sinal.
        assert set(fks) == {"fk_mail_caixa_indice_message_id_mail_messages"}
        # Nenhuma coluna de texto livre: só chaves, ids, carimbos e o selo.
        tipos = {c[1]: c[2] for c in da_migration["colunas"]}
        assert "text" not in tipos.values()
        # As tabelas dele e as da 0387 saem da 0388 exatamente como entraram.
        assert await _catalogo(db, rascunho, DELE + DA_0387) == antes

        # O selo fora da lista não entra; apagar a caixa (e o e-mail) leva a linha junto.
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
                f'INSERT INTO "{rascunho}".mail_messages (id, mailbox_id, source_id, direction,'
                " received_at, content_enc, attachment_count) VALUES"
                " ('00000000-0000-0000-0000-0000000000bb', '00000000-0000-0000-0000-0000000000aa',"
                " 's1', 'inbound', now(), '\\x00', 0)"
            )
        )
        await db.commit()
        valores = (
            "('00000000-0000-0000-0000-0000000000bb', '00000000-0000-0000-0000-0000000000aa',"
            " now(), 's1', '1', {selo}, now(), 'abc', 1)"
        )
        colunas = (
            "(message_id, mailbox_id, recebido_em, pasta_chave, pasta_tipo, selo, fonte_em,"
            " base, regras_versao)"
        )
        with pytest.raises(Exception, match="ck_mail_caixa_indice_selo"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".mail_caixa_indice {colunas} VALUES '
                    + valores.format(selo="'seguranca'")
                )
            )
        await db.rollback()
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".mail_caixa_indice {colunas} VALUES '
                + valores.format(selo="'loja'")
            )
        )
        await db.commit()
        linha = (
            await db.execute(
                text(
                    f'SELECT seguranca, indexado_em IS NOT NULL FROM "{rascunho}".mail_caixa_indice'
                )
            )
        ).one()
        assert tuple(linha) == (False, True)
        # Apagar a CAIXA leva o índice junto (pelo CASCADE do e-mail).
        await db.execute(text(f'DELETE FROM "{rascunho}".mail_mailboxes'))
        await db.commit()
        assert await db.scalar(text(f'SELECT count(*) FROM "{rascunho}".mail_caixa_indice')) == 0

        # O downgrade da 0388 tira só ela.
        conn = await db.connection()
        await conn.run_sync(_rodar, [(nossa, "downgrade")])
        await db.commit()
        depois = await _catalogo(db, rascunho, TABELAS)
        assert depois == {"tabelas": [], "colunas": [], "constraints": [], "indices": []}
        assert await _catalogo(db, rascunho, DELE + DA_0387) == antes
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
