"""A migration 0392 (conta de Ads ↔ conta da Conferência) cria EXATAMENTE o que o
model declara, semeia o vínculo pelas regras combinadas e o downgrade desfaz.

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Aqui a 0392 roda de verdade num schema descartável (só as colunas
que ela toca/lê das duas tabelas), COM linhas antes do upgrade — a semente
roda dentro dele:
  • ML pela `integration_id`; Shopee pelo nome normalizado; JLAS → Atlas e
    KIA → Fiore só sem conta com o próprio nome; arquivada e outra plataforma
    ficam de fora; par que não é um-para-um fica sem; o `department` não muda;
  • a semente de novo não muda nada (idempotente);
  • a coluna, a FK (ON DELETE SET NULL) e o único parcial batem com o model;
  • o downgrade tira os três.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base
from app.services.marketing import horarios_contas

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0392_marketing_conta_conferencia.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0392", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db: AsyncSession, schema: str) -> dict[str, list]:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "").replace(f'"{schema}".', "") if s else s

    colunas = (
        await db.execute(
            text(
                "SELECT column_name, udt_name, is_nullable, column_default"
                "  FROM information_schema.columns"
                " WHERE table_schema = :schema AND table_name = 'marketing_accounts'"
                "   AND column_name = 'conferencia_conta_id'"
            ),
            {"schema": schema},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                """
                SELECT co.conname, co.contype::text, pg_get_constraintdef(co.oid)
                  FROM pg_constraint co
                  JOIN pg_class cl ON cl.oid = co.conrelid
                  JOIN pg_namespace n ON n.oid = cl.relnamespace
                 WHERE n.nspname = :schema AND cl.relname = 'marketing_accounts'
                   AND co.conname LIKE '%conferencia%'
                 ORDER BY co.conname
                """
            ),
            {"schema": schema},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes"
                " WHERE schemaname = :schema AND tablename = 'marketing_accounts'"
                "   AND indexname LIKE '%conferencia%' ORDER BY indexname"
            ),
            {"schema": schema},
        )
    ).all()
    return {
        "colunas": [tuple(r) for r in colunas],
        "constraints": [(r[0], r[1], limpo(r[2])) for r in constraints],
        "indices": [(r[0], limpo(r[1])) for r in indices],
    }


async def test_migration_semeia_bate_com_o_model_e_o_downgrade_desfaz(db: AsyncSession):
    mod = _carregar()
    assert mod.revision == "0392_marketing_conta_conferencia"
    assert mod.down_revision == "0391_email_fora_do_chat"
    assert mod.APELIDOS == horarios_contas.APELIDOS, "a semente e a tela com os mesmos apelidos"
    fonte = _MIGRATION.read_text(encoding="utf-8").upper()
    assert "DELETE FROM" not in fonte and "TRUNCATE" not in fonte
    assert "SET DEPARTMENT" not in fonte, "o department não é tocado"

    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0392"
    r = f'"{rascunho}"'
    await db.execute(text(f"DROP SCHEMA IF EXISTS {r} CASCADE"))
    await db.execute(text(f"CREATE SCHEMA {r}"))
    # Só o que a 0392 toca/lê.
    await db.execute(
        text(
            f"CREATE TABLE {r}.marketing_accounts (id uuid PRIMARY KEY, name varchar(128) NOT NULL,"
            " platform varchar(32) NOT NULL, department varchar(32) NOT NULL,"
            " integration_id uuid, arquivada_em timestamptz)"
        )
    )
    await db.execute(
        text(
            f"CREATE TABLE {r}.conferencia_shopee_conta (id uuid PRIMARY KEY,"
            " plataforma text NOT NULL, nome text NOT NULL, grupo text NOT NULL,"
            " integration_id uuid)"
        )
    )

    ids: dict[str, uuid.UUID] = {}

    async def conta(chave, plataforma, nome, grupo="celular", integracao=None):
        ids[chave] = uuid.uuid4()
        await db.execute(
            text(
                f"INSERT INTO {r}.conferencia_shopee_conta"
                " (id, plataforma, nome, grupo, integration_id) VALUES (:i, :p, :n, :g, :int)"
            ),
            {"i": ids[chave], "p": plataforma, "n": nome, "g": grupo, "int": integracao},
        )

    async def ads(chave, platform, name, department="geral", integracao=None, arquivada=False):
        ids[chave] = uuid.uuid4()
        await db.execute(
            text(
                f"INSERT INTO {r}.marketing_accounts"
                " (id, name, platform, department, integration_id, arquivada_em)"
                " VALUES (:i, :n, :p, :d, :int, CASE WHEN :arq THEN now() END)"
            ),
            {"i": ids[chave], "n": name, "p": platform, "d": department, "int": integracao,
             "arq": arquivada},
        )

    integ_kfa, integ_nexus, integ_dupla = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # Conferência
    await conta("c_atlas", "shopee", "Atlas")
    await conta("c_fiore", "shopee", "Fiore")
    await conta("c_lucas", "shopee", "Lucas MEI")
    await conta("c_mega", "shopee", "Mega")
    await conta("c_vita", "shopee", "Vita")
    await conta("c_velha", "shopee", "Velha")
    await conta("c_kfa_ml", "ml", "KFA", "mala", integ_kfa)
    await conta("c_dupla_ml", "ml", "Dupla", "celular", integ_dupla)
    await conta("c_mega_ml", "ml", "Mega")  # sem integração: o ML não casa pelo nome
    # Ads
    await ads("a_jlas", "shopee", "JLAS")
    await ads("a_kia", "shopee", "KIA")
    await ads("a_lucas", "shopee", " \u200bLucas  MEI")
    await ads("a_mega", "shopee", "Mega")
    await ads("a_vita1", "shopee", "Vita")
    await ads("a_vita2", "shopee", "VITA ")  # o mesmo nome: nenhuma das duas fica com a Vita
    await ads("a_velha", "shopee", "Velha", arquivada=True)
    await ads("a_luno", "shopee", "Luno")
    await ads("a_kfa_ml", "ml", "kfa", "celular", integ_kfa)
    await ads("a_nexus_ml", "ml", "nexus", "mala", integ_nexus)
    await ads("a_dupla1", "ml", "dupla", "celular", integ_dupla)
    await ads("a_dupla2", "ml", "dupla 2", "celular", integ_dupla)
    await ads("a_mega_ml", "ml", "mega", "geral")
    await ads("a_amazon", "amazon", "Mega")
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()

        async def vinculos() -> dict[str, str | None]:
            linhas = (
                await db.execute(
                    text(f"SELECT id, conferencia_conta_id FROM {r}.marketing_accounts")
                )
            ).all()
            nome = {v: k for k, v in ids.items()}
            return {nome[i]: (nome[c] if c else None) for i, c in linhas}

        esperado = {
            "a_jlas": "c_atlas",
            "a_kia": "c_fiore",
            "a_lucas": "c_lucas",
            "a_mega": "c_mega",
            "a_vita1": None,
            "a_vita2": None,
            "a_velha": None,  # arquivada
            "a_luno": None,  # sem conta
            "a_kfa_ml": "c_kfa_ml",
            "a_nexus_ml": None,
            "a_dupla1": None,  # duas contas de Ads na mesma integração
            "a_dupla2": None,
            "a_mega_ml": None,
            "a_amazon": None,
        }
        assert await vinculos() == esperado
        dept = dict(
            (
                await db.execute(text(f"SELECT name, department FROM {r}.marketing_accounts"
                                      " WHERE platform = 'ml'"))
            ).all()
        )
        assert dept["kfa"] == "celular", "o department continua como estava"

        # Idempotente: de novo não muda nada.
        await db.execute(text(mod.sql_semente(rascunho)))
        await db.commit()
        assert await vinculos() == esperado

        # O que a migration criou = o que o model declara.
        da_migration = await _catalogo(db, rascunho)
        do_model = await _catalogo(db, schema_model)
        assert da_migration["colunas"] == [("conferencia_conta_id", "uuid", "YES", None)]
        assert da_migration == do_model
        assert [n for n, *_ in da_migration["constraints"]] == [
            "fk_marketing_accounts_conferencia_conta"
        ]
        assert "ON DELETE SET NULL" in da_migration["constraints"][0][2]
        ((nome_idx, def_idx),) = da_migration["indices"]
        assert nome_idx == "uq_marketing_accounts_conferencia_conta_id"
        assert "UNIQUE" in def_idx and "WHERE (conferencia_conta_id IS NOT NULL)" in def_idx

        # Uma conta da Conferência, uma conta de Ads.
        with pytest.raises(Exception, match="uq_marketing_accounts_conferencia_conta_id"):
            await db.execute(
                text(f"UPDATE {r}.marketing_accounts SET conferencia_conta_id = :c WHERE id = :i"),
                {"c": ids["c_atlas"], "i": ids["a_luno"]},
            )
        await db.rollback()

        # Conta apagada da Conferência: a de Ads fica sem vínculo (SET NULL).
        await db.execute(
            text(f"DELETE FROM {r}.conferencia_shopee_conta WHERE id = :c"), {"c": ids["c_mega"]}
        )
        await db.commit()
        assert (await vinculos())["a_mega"] is None

        # O downgrade tira a coluna, a FK e o índice.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert await _catalogo(db, rascunho) == {"colunas": [], "constraints": [], "indices": []}
    finally:
        await db.rollback()
        await db.execute(text(f"DROP SCHEMA IF EXISTS {r} CASCADE"))
        await db.commit()


async def test_a_tela_acha_o_mesmo_vinculo_que_a_semente():
    """horarios_contas.resolver_vinculos (conta criada depois da 0392) segue as
    MESMAS regras da semente: os mesmos casos do teste acima, sem banco."""
    from app.models import ConferenciaShopeeConta, MarketingAccount

    integ_kfa, integ_nexus, integ_dupla = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    def conta(plataforma, nome, integracao=None):
        return ConferenciaShopeeConta(
            id=uuid.uuid4(), plataforma=plataforma, nome=nome, grupo="celular",
            integration_id=integracao,
        )

    def ads(platform, name, integracao=None):
        return MarketingAccount(
            id=uuid.uuid4(), name=name, platform=platform, department="geral",
            integration_id=integracao, conferencia_conta_id=None,
        )

    contas = {
        "c_atlas": conta("shopee", "Atlas"),
        "c_fiore": conta("shopee", "Fiore"),
        "c_lucas": conta("shopee", "Lucas MEI"),
        "c_mega": conta("shopee", "Mega"),
        "c_vita": conta("shopee", "Vita"),
        "c_kfa_ml": conta("ml", "KFA", integ_kfa),
        "c_dupla_ml": conta("ml", "Dupla", integ_dupla),
        "c_mega_ml": conta("ml", "Mega"),
    }
    shopee = {
        "a_jlas": ads("shopee", "JLAS"),
        "a_kia": ads("shopee", "KIA"),
        "a_lucas": ads("shopee", " \u200bLucas  MEI"),
        "a_mega": ads("shopee", "Mega"),
        "a_vita1": ads("shopee", "Vita"),
        "a_vita2": ads("shopee", "VITA "),
        "a_luno": ads("shopee", "Luno"),
    }
    ml = {
        "a_kfa_ml": ads("ml", "kfa", integ_kfa),
        "a_nexus_ml": ads("ml", "nexus", integ_nexus),
        "a_dupla1": ads("ml", "dupla", integ_dupla),
        "a_dupla2": ads("ml", "dupla 2", integ_dupla),
        "a_mega_ml": ads("ml", "mega"),
    }
    nome_conta = {c.id: k for k, c in contas.items()}
    achado = {}
    for plataforma, grupo in (("shopee", shopee), ("ml", ml)):
        v = horarios_contas.resolver_vinculos(
            list(grupo.values()), list(contas.values()), plataforma
        )
        achado |= {k: nome_conta[v[a.id].id] if a.id in v else None for k, a in grupo.items()}
    assert achado == {
        "a_jlas": "c_atlas", "a_kia": "c_fiore", "a_lucas": "c_lucas", "a_mega": "c_mega",
        "a_vita1": None, "a_vita2": None, "a_luno": None,
        "a_kfa_ml": "c_kfa_ml", "a_nexus_ml": None, "a_dupla1": None, "a_dupla2": None,
        "a_mega_ml": None,
    }
