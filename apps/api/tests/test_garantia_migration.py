"""As migrations 0380 (Painel de Garantia Uranyx), 0434 (garantia automática),
0439 (pedido de garantia), 0459 (prazo por produto e o caso do SAC), 0460
(prazo 0 = sem garantia) e 0462 (o contato do comprador) criam EXATAMENTE o
que o model declara.

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Aqui a 0380 roda de verdade num schema descartável e o catálogo do
Postgres dos dois lados é comparado (colunas, defaults, CHECK, FK com ON
DELETE, PK, UNIQUE, índices e os gatilhos "só inserção"). E o downgrade
desfaz tudo, inclusive a função do gatilho.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import inspect
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base

_VERSOES = Path(__file__).resolve().parent.parent / "alembic" / "versions"
TABELAS = [
    "garantia_atendimento_anexos",
    "garantia_atendimentos",
    "garantia_conversas",
    "garantia_log",
    "garantia_pendencias",
    "garantia_sac",
    "garantias",
    "pricing_garantia_prazo",
]
_FILTRO = (
    "('garantias', 'garantia_atendimentos', 'garantia_atendimento_anexos', 'garantia_log',"
    " 'garantia_conversas', 'garantia_pendencias', 'garantia_sac', 'pricing_garantia_prazo')"
)


def _carregar(nome: str = "0380_garantias"):
    spec = importlib.util.spec_from_file_location(f"migration_{nome}", _VERSOES / f"{nome}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db: AsyncSession, schema: str) -> dict[str, list]:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "").replace(f'"{schema}".', "") if s else s

    colunas = (
        await db.execute(
            text(
                "SELECT table_name, column_name, udt_name, is_nullable, column_default,"
                " character_maximum_length"
                " FROM information_schema.columns"
                f" WHERE table_schema = :schema AND table_name IN {_FILTRO}"
                " ORDER BY table_name, column_name"
            ),
            {"schema": schema},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                f"""
                SELECT cl.relname, co.conname, co.contype::text, pg_get_constraintdef(co.oid)
                  FROM pg_constraint co
                  JOIN pg_class cl ON cl.oid = co.conrelid
                  JOIN pg_namespace n ON n.oid = cl.relnamespace
                 WHERE n.nspname = :schema AND cl.relname IN {_FILTRO}
                 ORDER BY cl.relname, co.conname
                """
            ),
            {"schema": schema},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                f"""
                SELECT tablename, indexname, indexdef
                  FROM pg_indexes
                 WHERE schemaname = :schema AND tablename IN {_FILTRO}
                 ORDER BY tablename, indexname
                """
            ),
            {"schema": schema},
        )
    ).all()
    gatilhos = (
        await db.execute(
            text(
                f"""
                SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid)
                  FROM pg_trigger t
                  JOIN pg_class c ON c.oid = t.tgrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = :schema AND c.relname IN {_FILTRO} AND NOT t.tgisinternal
                 ORDER BY c.relname, t.tgname
                """
            ),
            {"schema": schema},
        )
    ).all()
    funcoes = (
        await db.execute(
            text(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                " WHERE n.nspname = :schema AND p.proname = 'garantia_so_insercao'"
            ),
            {"schema": schema},
        )
    ).all()
    return {
        "tabelas": sorted({r[0] for r in colunas}),
        "colunas": [tuple(r[:4]) + (limpo(r[4]),) + tuple(r[5:]) for r in colunas],
        "constraints": [(r[0], r[1], r[2], limpo(r[3])) for r in constraints],
        "indices": [(r[0], r[1], limpo(r[2])) for r in indices],
        "gatilhos": [(r[0], r[1], limpo(r[2])) for r in gatilhos],
        "funcoes": [r[0] for r in funcoes],
    }


@pytest.mark.asyncio
async def test_migration_bate_com_o_model_e_o_downgrade_desfaz(db: AsyncSession):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0380"
    mod = _carregar()
    assert mod.revision == "0380_garantias"
    assert mod.down_revision == "0379_pricing_carrefour_netshoes"
    mod_auto = _carregar("0434_garantia_automatica")
    assert mod_auto.down_revision == "0433_atendimento_zap_saude"
    mod_pedido = _carregar("0439_garantia_pedido")
    assert mod_pedido.down_revision == "0438_mail_composer"
    mod_sac = _carregar("0459_garantia_sac")
    assert (mod_sac.revision, mod_sac.down_revision) == (
        "0459_garantia_sac",
        "0458_chamados_fechar_dia",
    )
    # As tabelas quentes (garantias, pricing_products, garantia_log): a 0459
    # desiste em vez de enfileirar a API atrás dela (como 0449/0451).
    for passo in (mod_sac.upgrade, mod_sac.downgrade):
        assert "SET LOCAL lock_timeout" in inspect.getsource(passo)
    # O prazo 0 (06/11/2026, "se for avulso não tem garantia").
    mod_zero = _carregar("0460_garantia_sem_garantia")
    assert (mod_zero.revision, mod_zero.down_revision) == (
        "0460_garantia_sem_garantia",
        "0459_garantia_sac",
    )
    for passo in (mod_zero.upgrade, mod_zero.downgrade):
        assert "SET LOCAL lock_timeout" in inspect.getsource(passo)
    # O contato do comprador na aba Dados (09/11/2026). A 0461 é da
    # Certificações (financeiro_suprimentos), fora daqui.
    mod_contato = _carregar("0462_garantia_contato")
    assert (mod_contato.revision, mod_contato.down_revision) == (
        "0462_garantia_contato",
        "0461_certificacao_garantia",
    )

    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que as duas referenciam: users(id), a conversa do Atendimento e o
    # índice comprador × pedido.
    await db.execute(text(f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY)'))
    await db.execute(text(f'CREATE TABLE "{rascunho}".atendimento_conversas (id uuid PRIMARY KEY)'))
    await db.execute(
        text(f'CREATE TABLE "{rascunho}".atendimento_pedidos_comprador (pedido varchar(64))')
    )
    # A 0459 põe o prazo na linha da Tabela de Preços (e a vigência aponta para ela).
    await db.execute(text(f'CREATE TABLE "{rascunho}".pricing_products (id uuid PRIMARY KEY)'))
    await db.commit()

    def _rodar(conn, passo: str, modulo=None) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(modulo or mod, passo)()

    mod.SCHEMA = rascunho
    mod_auto.SCHEMA = rascunho
    mod_pedido.SCHEMA = rascunho
    mod_sac.SCHEMA = rascunho
    mod_zero.SCHEMA = rascunho
    mod_contato.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await conn.run_sync(_rodar, "upgrade", mod_auto)
        await conn.run_sync(_rodar, "upgrade", mod_pedido)
        await conn.run_sync(_rodar, "upgrade", mod_sac)
        await conn.run_sync(_rodar, "upgrade", mod_zero)
        await conn.run_sync(_rodar, "upgrade", mod_contato)
        await db.commit()

        da_migration = await _catalogo(db, rascunho)
        do_model = await _catalogo(db, schema_model)
        assert da_migration["tabelas"] == TABELAS
        assert len(da_migration["colunas"]) > 50
        assert da_migration["colunas"] == do_model["colunas"]
        assert da_migration["constraints"] == do_model["constraints"]
        assert da_migration["indices"] == do_model["indices"]
        assert da_migration["gatilhos"] == do_model["gatilhos"]
        assert da_migration["funcoes"] == ["garantia_so_insercao"]
        # A coluna e o CHECK do prazo na linha da Tabela de Preços (0459).
        prazo_do_produto = text(
            "SELECT c.udt_name, c.is_nullable, pg_get_constraintdef(co.oid)"
            " FROM information_schema.columns c"
            " JOIN pg_namespace n ON n.nspname = c.table_schema"
            " JOIN pg_class cl ON cl.relnamespace = n.oid AND cl.relname = c.table_name"
            " JOIN pg_constraint co ON co.conrelid = cl.oid"
            "  AND co.conname = 'ck_pricing_products_garantia_meses'"
            " WHERE c.table_schema = :schema AND c.table_name = 'pricing_products'"
            "  AND c.column_name = 'garantia_meses'"
        )
        do_rascunho = (await db.execute(prazo_do_produto, {"schema": rascunho})).all()
        assert do_rascunho == (await db.execute(prazo_do_produto, {"schema": schema_model})).all()
        assert len(do_rascunho) == 1 and "garantia_meses >= 0" in do_rascunho[0][2]
        # 0 (sem garantia) entra; 121 e -1 não.
        produto = uuid.uuid4()
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".pricing_products (id, garantia_meses)'
                f" VALUES ('{produto}', 0)"
            )
        )
        await db.commit()
        for ruim in (121, -1):
            with pytest.raises(Exception, match="ck_pricing_products_garantia_meses"):
                await db.execute(
                    text(f'UPDATE "{rascunho}".pricing_products SET garantia_meses = {ruim}')
                )
            await db.rollback()
        nomes = {c[1] for c in da_migration["constraints"]}
        assert {
            "pk_garantias",
            "uq_garantias_cpf_nf",
            "ck_garantias_cpf_digitos",
            "ck_garantias_prazos_completos",
            "fk_garantias_criado_por_users",
            "fk_garantia_atendimentos_garantia_id_garantias",
            "ck_garantia_atendimentos_cobertura_valida",
            "ck_garantia_log_acao_valida",
            "ck_garantias_solicitacao_origem_valida",
            "fk_garantias_nao_solicitada_por_users",
            "ck_garantias_fim_garantia_ok",
            "uq_garantia_sac_conversa_id",
            "ck_garantia_sac_confirmado_com_garantia",
            "ck_garantia_sac_encerrado_confirmado",
            "ck_garantia_sac_encerrado_com_motivo",
            "fk_garantia_sac_garantia_id_garantias",
        } <= nomes
        # O nome da FK do anexo passa de 63 caracteres (o SQLAlchemy encurta):
        # confere pelo que ela faz.
        fks = [d for t, _, tipo, d in da_migration["constraints"] if tipo == "f"]
        assert "REFERENCES garantia_atendimentos(id) ON DELETE RESTRICT" in " | ".join(fks)
        assert "REFERENCES users(id) ON DELETE RESTRICT" in " | ".join(fks)
        gatilhos = {(t, g) for t, g, _ in da_migration["gatilhos"]}
        for tabela in ("garantia_atendimentos", "garantia_atendimento_anexos", "garantia_log"):
            assert (tabela, f"{tabela}_so_insercao") in gatilhos
            assert (tabela, f"{tabela}_sem_truncate") in gatilhos
        assert not any(t == "garantias" for t, _ in gatilhos)  # o cadastro se corrige

        # O gatilho do rascunho recusa UPDATE/DELETE de verdade.
        uid = uuid.uuid4()
        await db.execute(text(f"INSERT INTO \"{rascunho}\".users (id) VALUES ('{uid}')"))
        gid = (
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".garantias'
                    " (pedido_bling, nf_numero, cliente_nome, cpf, criado_por)"
                    f" VALUES ('1', '10', 'Fulano', '52998224725', '{uid}') RETURNING id"
                )
            )
        ).scalar_one()
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".garantia_log (garantia_id, acao, user_nome)'
                f" VALUES ({gid}, 'cadastrou', 'x')"
            )
        )
        await db.commit()
        with pytest.raises(Exception, match="garantia_so_insercao"):
            await db.execute(text(f"UPDATE \"{rascunho}\".garantia_log SET detalhe = 'y'"))
        await db.rollback()
        # CPF fora do formato e prazo pela metade: o banco recusa.
        with pytest.raises(Exception, match="ck_garantias_cpf_digitos"):
            await db.execute(text(f"UPDATE \"{rascunho}\".garantias SET cpf = '123'"))
        await db.rollback()
        with pytest.raises(Exception, match="ck_garantias_prazos_completos"):
            await db.execute(
                text(f"UPDATE \"{rascunho}\".garantias SET data_inicio = '2026-10-07'")
            )
        await db.rollback()
        await db.execute(text("SELECT set_config('davinci.garantia_expurgo', 'sim', true)"))
        await db.execute(text(f'DELETE FROM "{rascunho}".garantia_log'))
        await db.execute(text(f'DELETE FROM "{rascunho}".garantias'))
        await db.commit()

        # A garantia do robô: sem pessoa, origem 'robo'; a manual sem pessoa, não.
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".garantias'
                " (pedido_bling, nf_numero, cliente_nome, cpf, origem)"
                " VALUES ('2', '11', 'Fulano', '52998224725', 'robo')"
            )
        )
        await db.commit()
        with pytest.raises(Exception, match="ck_garantias_manual_tem_autor"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".garantias'
                    " (pedido_bling, nf_numero, cliente_nome, cpf)"
                    " VALUES ('3', '12', 'Fulano', '52998224725')"
                )
            )
        await db.rollback()
        await db.execute(text(f'DELETE FROM "{rascunho}".garantias'))
        await db.commit()

        # O pedido de garantia: a origem fora da lista, o banco recusa.
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".garantias'
                " (pedido_bling, nf_numero, cliente_nome, cpf, origem)"
                " VALUES ('4', '13', 'Fulano', '52998224725', 'robo')"
            )
        )
        await db.commit()
        with pytest.raises(Exception, match="ck_garantias_solicitacao_origem_valida"):
            await db.execute(
                text(f"UPDATE \"{rascunho}\".garantias SET solicitacao_origem = 'palpite'")
            )
        await db.rollback()
        await db.execute(text(f'DELETE FROM "{rascunho}".garantias'))
        await db.commit()

        # O fim único sem prazo, e o caso confirmado sem garantia: o banco recusa.
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".garantias'
                " (pedido_bling, nf_numero, cliente_nome, cpf, origem)"
                " VALUES ('5', '14', 'Fulano', '52998224725', 'robo')"
            )
        )
        await db.commit()
        with pytest.raises(Exception, match="ck_garantias_fim_garantia_ok"):
            await db.execute(
                text(f"UPDATE \"{rascunho}\".garantias SET fim_garantia = '2027-01-01'")
            )
        await db.rollback()
        conversa = uuid.uuid4()
        await db.execute(
            text(f"INSERT INTO \"{rascunho}\".atendimento_conversas (id) VALUES ('{conversa}')")
        )
        await db.commit()
        with pytest.raises(Exception, match="ck_garantia_sac_confirmado_com_garantia"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".garantia_sac'
                    " (conversa_id, protocolo, recebido_em, confirmado_em)"
                    f" VALUES ('{conversa}', 'US-26-0001', now(), now())"
                )
            )
        await db.rollback()
        await db.execute(text(f'DELETE FROM "{rascunho}".garantias'))
        await db.commit()

        # A volta da 0462 tira só as 3 colunas do contato.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", mod_contato)
        await db.commit()
        colunas_contato = text(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = :s"
            " AND table_name = 'garantias' AND column_name LIKE 'contato%'"
        )
        assert (await db.execute(colunas_contato, {"s": rascunho})).scalar_one() == 0

        # A volta da 0460: o 0 vira NULL (o CHECK antigo, 1..120, não aceita o 0).
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", mod_zero)
        await db.commit()
        assert (
            await db.execute(text(f'SELECT garantia_meses FROM "{rascunho}".pricing_products'))
        ).all() == [(None,)]
        with pytest.raises(Exception, match="ck_pricing_products_garantia_meses"):
            await db.execute(text(f'UPDATE "{rascunho}".pricing_products SET garantia_meses = 0'))
        await db.rollback()
        await db.execute(text(f'UPDATE "{rascunho}".pricing_products SET garantia_meses = 12'))
        await db.commit()

        # O downgrade tira tudo o que a 0459, a 0439, a 0434 e a 0380 puseram.
        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade", mod_sac)
        await conn.run_sync(_rodar, "downgrade", mod_pedido)
        await conn.run_sync(_rodar, "downgrade", mod_auto)
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        depois = await _catalogo(db, rascunho)
        assert depois == {
            "tabelas": [],
            "colunas": [],
            "constraints": [],
            "indices": [],
            "gatilhos": [],
            "funcoes": [],
        }
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
