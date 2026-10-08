"""As migrations 0377 (Conferência Shopee) e 0382 (Mercado Livre e Amazon) criam
EXATAMENTE o que o model declara.

E os downgrades desfazem.

O conftest monta o schema de teste pelo `create_all` do model, nunca pela
migration. Aqui a 0377 e, em cima dela, a 0382 rodam de verdade num schema
descartável e o catálogo do Postgres dos dois lados é comparado: as quatro
tabelas `conferencia_shopee_*` (colunas, defaults, CHECK, FK com ON DELETE, PK,
UNIQUE, índices — inclusive os únicos parciais por plataforma). E os seeds:
as 20 lojas da Shopee (17 ativas, 4 de Mala) e a linha do Informar (0377); as
contas do ML e da Amazon ligadas às integrações e às lojas do Bling (0382,
com integrações e lojas inventadas: nome com espaço, override, loja do
cadastro, loja pelo apelido, integração repetida, arquivada e de outra
plataforma). Todos idempotentes. O downgrade da 0382 volta o catálogo ao da
0377 (e apaga as rodadas do ML/Amazon e as contas que ela semeou); o da 0377
tira tudo.

E o Histórico: execução, coleta e saldo são da máquina (fora do gatilho); a
lista de lojas, editada por pessoas, continua coberta.
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

from app.historico import sql as hsql
from app.models import Base

_VERSOES = Path(__file__).resolve().parent.parent / "alembic" / "versions"
_MIGRATION = _VERSOES / "0377_conferencia_shopee.py"
_MIGRATION_PLATAFORMAS = _VERSOES / "0382_conferencia_plataformas.py"
TABELAS = [
    "conferencia_shopee_coleta",
    "conferencia_shopee_conta",
    "conferencia_shopee_execucao",
    "conferencia_shopee_saldo",
]


def _carregar(caminho: Path = _MIGRATION):
    spec = importlib.util.spec_from_file_location(f"migration_{caminho.stem}", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db: AsyncSession, schema: str) -> dict[str, list]:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "") if s else s

    colunas = (
        await db.execute(
            text(
                "SELECT table_name, column_name, udt_name, is_nullable, column_default,"
                " is_identity, numeric_precision, numeric_scale"
                " FROM information_schema.columns"
                " WHERE table_schema = :schema AND table_name LIKE 'conferencia\\_shopee\\_%'"
                " ORDER BY table_name, column_name"
            ),
            {"schema": schema},
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
                 WHERE n.nspname = :schema AND cl.relname LIKE 'conferencia\\_shopee\\_%'
                 ORDER BY cl.relname, co.conname
                """
            ),
            {"schema": schema},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                """
                SELECT tablename, indexname, indexdef
                  FROM pg_indexes
                 WHERE schemaname = :schema AND tablename LIKE 'conferencia\\_shopee\\_%'
                 ORDER BY tablename, indexname
                """
            ),
            {"schema": schema},
        )
    ).all()
    return {
        "tabelas": sorted({r[0] for r in colunas}),
        "colunas": [tuple(r[:4]) + (limpo(r[4]),) + tuple(r[5:]) for r in colunas],
        "constraints": [(r[0], r[1], r[2], limpo(r[3])) for r in constraints],
        "indices": [(r[0], r[1], limpo(r[2])) for r in indices],
    }


async def _preparar_rascunho(db: AsyncSession, rascunho: str) -> None:
    """O schema descartável com só o que as duas migrations tocam: o cadastro
    do Informar (0377) e integrações/lojas/empresas (a FK e a semente da 0382),
    com as mesmas colunas que elas leem."""
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".threema_informar_config ('
            " id uuid PRIMARY KEY, contexto text NOT NULL UNIQUE,"
            " recipients text NOT NULL DEFAULT '',"
            " created_at timestamptz NOT NULL DEFAULT now(),"
            " updated_at timestamptz NOT NULL DEFAULT now())"
        )
    )
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".integrations ('
            " id uuid PRIMARY KEY, platform text NOT NULL, name text NOT NULL,"
            " archived_at timestamptz, store_id uuid, bling_loja_id bigint)"
        )
    )
    await db.execute(
        text(f'CREATE TABLE "{rascunho}".companies (id uuid PRIMARY KEY, apelido text NOT NULL)')
    )
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".stores ('
            " id uuid PRIMARY KEY, company_id uuid NOT NULL, marketplace text NOT NULL,"
            " apelido_override text, integration_id uuid, bling_store_id bigint)"
        )
    )


async def _semear_integracoes(db: AsyncSession, rascunho: str) -> dict[tuple[str, str], uuid.UUID]:
    """Integrações e lojas inventadas para a semente da 0382. Devolve
    (plataforma, nome) → id."""
    ids: dict[tuple[str, str], uuid.UUID] = {}

    async def integracao(plataforma: str, nome: str, *, arquivada: bool = False,
                         store_id=None, loja: int | None = None) -> uuid.UUID:
        iid = uuid.uuid4()
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".integrations'
                " (id, platform, name, archived_at, store_id, bling_loja_id)"
                " VALUES (:id, :p, :n, CASE WHEN :arq THEN now() END, :s, :l)"
            ),
            {"id": iid, "p": plataforma, "n": nome, "arq": arquivada, "s": store_id, "l": loja},
        )
        ids.setdefault((plataforma, nome), iid)
        return iid

    async def loja(marketplace: str, apelido: str, bling: int, *, integracao_id=None) -> uuid.UUID:
        empresa, sid = uuid.uuid4(), uuid.uuid4()
        await db.execute(
            text(f'INSERT INTO "{rascunho}".companies (id, apelido) VALUES (:id, :a)'),
            {"id": empresa, "a": apelido},
        )
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".stores'
                " (id, company_id, marketplace, integration_id, bling_store_id)"
                " VALUES (:id, :c, :m, :i, :b)"
            ),
            {"id": sid, "c": empresa, "m": marketplace, "i": integracao_id, "b": bling},
        )
        return sid

    # ML: loja do Bling por override (bling_loja_id da integração).
    for i, nome in enumerate(
        ["Poofy", "jlas 2", "kfa2", "inova", "barbosa", "aguiar", "aguiar2", "kia",
         "victor mei", "dream2", "Zorvex", "vita", "eron", "jlas", "counhago"]
    ):
        await integracao("ml", nome, loja=300 + i)
    # ML: a MESMA loja do Bling da "aguiar" (305) — fica só na primeira da lista.
    await integracao("ml", "mini", loja=305)
    await integracao("ml", "marquezini", loja=111)
    # ML: loja pelo cadastro da integração (integrations.store_id).
    await integracao("ml", "forpaper", store_id=await loja("ml", "Forpaper Ltda", 222))
    # ML: loja que aponta para a integração (stores.integration_id).
    kfa = await integracao("ml", "kfa")
    await loja("ml", "KFA Ltda", 333, integracao_id=kfa)
    # ML: sem vínculo de loja nenhum — acha pelo apelido do cadastro de Lojas.
    await integracao("ml", "velasco")
    await loja("ml", "Velasco", 444)
    # ML: integração REPETIDA (duas "mega" vivas) → não liga nenhuma.
    await integracao("ml", "mega", loja=555)
    await integracao("ml", "Mega", loja=556)
    # ML: sem loja do Bling nenhuma.
    await integracao("ml", "injox")
    # ML: arquivada não conta (Nexus nem está na lista; Lucas MEI fica sem).
    await integracao("ml", "lucas mei", arquivada=True, loja=777)
    # Amazon e uma Shopee com o mesmo nome (a plataforma separa).
    for nome in ("kfa", "kia", "nexus", "poofy"):
        await integracao("amazon", nome)
    await integracao("shopee", "kfa", loja=999)
    await db.commit()
    return ids


@pytest.mark.asyncio
async def test_migration_bate_com_o_model_semeia_e_o_downgrade_desfaz(db: AsyncSession):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0377"
    mod = _carregar()
    assert mod.revision == "0377_conferencia_shopee"
    assert mod.down_revision == "0376_imobilizado"
    mod_p = _carregar(_MIGRATION_PLATAFORMAS)
    assert mod_p.revision == "0382_conferencia_plataformas"
    assert mod_p.down_revision == "0381_marketing_ads_estado"

    await _preparar_rascunho(db, rascunho)
    await db.commit()

    def _rodar(conn, modulo, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(modulo, passo)()

    async def rodar(modulo, passo: str) -> None:
        conn = await db.connection()
        await conn.run_sync(_rodar, modulo, passo)
        await db.commit()

    mod.SCHEMA = rascunho
    mod_p.SCHEMA = rascunho
    try:
        await rodar(mod, "upgrade")
        so_0377 = await _catalogo(db, rascunho)
        assert so_0377["tabelas"] == TABELAS
        assert "uq_conferencia_shopee_conta_adspower_user_id" in {
            c[1] for c in so_0377["constraints"]
        }

        # Os seeds da 0377: 20 lojas, 17 ativas, 4 de Mala; o Informar nasce vazio.
        async def contas(plataforma: str = "shopee") -> list[tuple]:
            return (
                await db.execute(
                    text(
                        "SELECT adspower_user_id, nome, grupo, ativo, conta_key"
                        f' FROM "{rascunho}".conferencia_shopee_conta'
                        " WHERE plataforma = :p ORDER BY nome"
                    ),
                    {"p": plataforma},
                )
            ).all()

        async def contas_0377() -> list[tuple]:
            return (
                await db.execute(
                    text(
                        "SELECT adspower_user_id, nome, grupo, ativo, conta_key"
                        f' FROM "{rascunho}".conferencia_shopee_conta ORDER BY nome'
                    )
                )
            ).all()

        semeadas = await contas_0377()
        assert len(semeadas) == 20
        assert sum(1 for c in semeadas if c[3]) == 17
        mala = sorted(c[1] for c in semeadas if c[2] == "mala")
        assert mala == ["Inova", "KFA", "Minas", "Poofy"]
        assert {c[1] for c in semeadas if not c[3]} == {"VR", "Eron", "Lucas MEI"}
        por_nome = {c[1]: c for c in semeadas}
        assert por_nome["Victor"][4] == "victor mei"
        assert por_nome["Barbosa"][0] == "k1dkeaxv"
        informar = (
            await db.execute(
                text(
                    f'SELECT recipients FROM "{rascunho}".threema_informar_config'
                    " WHERE contexto = 'conferencia_shopee'"
                )
            )
        ).all()
        assert informar == [("",)]

        # Idempotente: rodar o seed de novo não duplica nada.
        await rodar(mod, "_semear")
        assert len(await contas_0377()) == 20
        n = (
            await db.execute(
                text(f'SELECT count(*) FROM "{rascunho}".threema_informar_config')
            )
        ).scalar_one()
        assert n == 1

        # O CHECK segura valor fora da lista (TEXT, não enum).
        with pytest.raises(Exception, match="ck_conferencia_shopee_conta_grupo"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".conferencia_shopee_conta'
                    " (adspower_user_id, nome, grupo) VALUES ('kx', 'X', 'eletro')"
                )
            )
        await db.rollback()

        # ── 0382 em cima: o catálogo das duas = o do model ───────────────────
        integracoes = await _semear_integracoes(db, rascunho)
        await rodar(mod_p, "upgrade")
        da_migration = await _catalogo(db, rascunho)
        do_model = await _catalogo(db, schema_model)
        assert da_migration["tabelas"] == TABELAS
        # Comparação que não compara nada passaria calada: o catálogo tem corpo.
        assert len(da_migration["colunas"]) > 38
        nomes = {c[1] for c in da_migration["constraints"]}
        assert {
            "pk_conferencia_shopee_conta",
            "ck_conferencia_shopee_conta_grupo",
            "ck_conferencia_shopee_conta_plataforma",
            "fk_conferencia_shopee_conta_integration",
            "pk_conferencia_shopee_execucao",
            "ck_conferencia_shopee_execucao_tipo",
            "ck_conferencia_shopee_execucao_origem",
            "ck_conferencia_shopee_execucao_status",
            "ck_conferencia_shopee_execucao_plataforma",
            "pk_conferencia_shopee_coleta",
            "ck_conferencia_shopee_coleta_status",
            "fk_conferencia_shopee_coleta_execucao",
            "fk_conferencia_shopee_coleta_conta",
            "pk_conferencia_shopee_saldo",
        } <= nomes
        # O UNIQUE de antes virou os dois parciais por plataforma.
        assert "uq_conferencia_shopee_conta_adspower_user_id" not in nomes
        assert da_migration["colunas"] == do_model["colunas"]
        assert da_migration["constraints"] == do_model["constraints"]
        assert da_migration["indices"] == do_model["indices"]

        defs = {nome: d for _, nome, d in da_migration["indices"]}
        assert "(criado_em DESC)" in defs["ix_conferencia_shopee_execucao_criado_em"]
        assert "(status, disponivel_apos)" in defs[
            "ix_conferencia_shopee_coleta_status_disponivel_apos"
        ]
        assert "(adspower_user_id, lido_em)" in defs[
            "ix_conferencia_shopee_saldo_adspower_user_id_lido_em"
        ]
        assert "UNIQUE" in defs["uq_conferencia_shopee_conta_plataforma_adspower_user_id"]
        assert "WHERE (adspower_user_id IS NOT NULL)" in defs[
            "uq_conferencia_shopee_conta_plataforma_adspower_user_id"
        ]
        assert "WHERE (integration_id IS NOT NULL)" in defs[
            "uq_conferencia_shopee_conta_plataforma_integration_id"
        ]
        assert "(plataforma, bling_loja_id) WHERE (bling_loja_id IS NOT NULL)" in defs[
            "uq_conferencia_shopee_conta_plataforma_bling_loja_id"
        ]
        assert "WHERE (status = 'coletando'::text)" in defs[
            "uq_conferencia_shopee_execucao_plataforma_coletando"
        ]
        fks = {nome: d for _, nome, tipo, d in da_migration["constraints"] if tipo == "f"}
        assert "ON DELETE CASCADE" in fks["fk_conferencia_shopee_coleta_execucao"]
        assert "ON DELETE SET NULL" in fks["fk_conferencia_shopee_coleta_conta"]
        assert "ON DELETE SET NULL" in fks["fk_conferencia_shopee_conta_integration"]
        cols = {(c[0], c[1]): c for c in da_migration["colunas"]}
        assert cols[("conferencia_shopee_saldo", "id")][5] == "YES"  # identity
        assert cols[("conferencia_shopee_saldo", "valor")][6:8] == (14, 2)
        assert cols[("conferencia_shopee_execucao", "status")][3:5] == (
            "NO",
            "'coletando'::text",
        )
        assert cols[("conferencia_shopee_coleta", "status")][4] == "'pendente'::text"
        for tabela in ("conferencia_shopee_conta", "conferencia_shopee_execucao"):
            assert cols[(tabela, "plataforma")][3:5] == ("NO", "'shopee'::text")
        for tabela in ("conferencia_shopee_conta", "conferencia_shopee_coleta"):
            assert cols[(tabela, "adspower_user_id")][3] == "YES"

        # As 20 da Shopee continuam Shopee, intactas.
        assert await contas("shopee") == semeadas

        # ── a semente do ML e da Amazon ──────────────────────────────────────
        async def semente(plataforma: str) -> dict[str, dict]:
            linhas = (
                await db.execute(
                    text(
                        "SELECT nome, grupo, ativo, conta_key, integration_id, bling_loja_id,"
                        " observacao, adspower_user_id"
                        f' FROM "{rascunho}".conferencia_shopee_conta WHERE plataforma = :p'
                    ),
                    {"p": plataforma},
                )
            ).mappings().all()
            return {r["nome"]: dict(r) for r in linhas}

        ml = await semente("ml")
        assert len(ml) == 26
        assert all(c["adspower_user_id"] is None for c in ml.values())
        assert sorted(n for n, c in ml.items() if c["grupo"] == "mala") == [
            "Forpaper", "KFA", "Marquezini", "Poofy",
        ]
        # Ativas = as da lista com integração E loja; Mega (integração repetida),
        # Injox (sem loja do Bling) e Mini (loja do Bling repetida) entram
        # desativadas, com a nota.
        assert sorted(n for n, c in ml.items() if c["ativo"]) == sorted([
            "Marquezini", "Forpaper", "KFA", "Poofy", "Jlas 2", "KFA 2", "Inova", "Barbosa",
            "Aguiar", "Aguiar 2", "Kia", "Velasco", "Victor MEI", "Dream 2", "Zorvex", "Vita",
        ])
        # A loja do Bling que a busca achou para duas contas fica só na primeira
        # da lista (Aguiar); a Mini entra sem loja, desativada, com a nota.
        assert (ml["Aguiar"]["bling_loja_id"], ml["Mini"]["bling_loja_id"]) == ("305", None)
        assert not ml["Mini"]["ativo"]
        assert ml["Mini"]["integration_id"] == integracoes[("ml", "mini")]
        assert ml["Mini"]["observacao"] == mod_p._LOJA_REPETIDA.format(loja="305")
        assert (ml["Jlas 2"]["integration_id"], ml["Jlas 2"]["conta_key"]) == (
            integracoes[("ml", "jlas 2")], "jlas2",
        )
        assert ml["Victor MEI"]["integration_id"] == integracoes[("ml", "victor mei")]
        assert ml["Poofy"]["integration_id"] == integracoes[("ml", "Poofy")]
        assert (ml["Marquezini"]["bling_loja_id"], ml["Forpaper"]["bling_loja_id"]) == (
            "111", "222",
        )
        assert (ml["KFA"]["bling_loja_id"], ml["Velasco"]["bling_loja_id"]) == ("333", "444")
        assert ml["KFA"]["integration_id"] == integracoes[("ml", "kfa")]
        assert ml["Mega"]["integration_id"] is None and not ml["Mega"]["ativo"]
        assert "Sem integração do Mercado Livre" in ml["Mega"]["observacao"]
        assert ml["Injox"]["bling_loja_id"] is None and not ml["Injox"]["ativo"]
        assert ml["Injox"]["integration_id"] == integracoes[("ml", "injox")]
        assert "Sem loja do Bling" in ml["Injox"]["observacao"]
        assert ml["Marquezini"]["observacao"] is None
        # As desativadas pela lista do dono ficam desativadas mesmo ligadas.
        assert not ml["Jlas"]["ativo"] and ml["Jlas"]["integration_id"] is not None
        assert ml["Jlas"]["observacao"] == mod_p._DESATIVADA
        # Integração arquivada não conta.
        assert ml["Lucas MEI"]["integration_id"] is None
        assert ml["Atlas"]["observacao"].startswith(mod_p._NOVA_ML)
        assert "Sem integração" in ml["Atlas"]["observacao"]

        amazon = await semente("amazon")
        assert {n: (c["grupo"], c["ativo"], c["bling_loja_id"]) for n, c in amazon.items()} == {
            "KFA": ("mala", True, "204438129"),
            "Poofy": ("mala", True, "206099015"),
            "Kia": ("celular", True, "204713113"),
            "Nexus": ("celular", False, "206064394"),
        }
        # A KFA da Amazon é a integração da Amazon (nem a do ML, nem a da Shopee).
        assert amazon["KFA"]["integration_id"] == integracoes[("amazon", "kfa")]

        # Idempotente: rodar a semente de novo não duplica nada.
        await rodar(mod_p, "_semear")
        assert len(await contas("ml")) == 26 and len(await contas("amazon")) == 4

        # O CHECK da plataforma; e a mesma integração não entra duas vezes.
        with pytest.raises(Exception, match="ck_conferencia_shopee_conta_plataforma"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".conferencia_shopee_conta'
                    " (plataforma, nome, grupo) VALUES ('tiktok', 'X', 'mala')"
                )
            )
        await db.rollback()
        with pytest.raises(Exception, match="uq_conferencia_shopee_conta_plataforma_integration"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".conferencia_shopee_conta'
                    " (plataforma, nome, grupo, integration_id) VALUES ('ml', 'X', 'mala', :i)"
                ),
                {"i": integracoes[("ml", "kfa")]},
            )
        await db.rollback()
        # Nem a mesma loja do Bling (contaria as vendas duas vezes).
        with pytest.raises(Exception, match="uq_conferencia_shopee_conta_plataforma_bling_loja"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".conferencia_shopee_conta'
                    " (plataforma, nome, grupo, bling_loja_id) VALUES ('ml', 'X', 'mala', '305')"
                )
            )
        await db.rollback()

        # Uma rodada do ML (com coleta sem perfil) para o downgrade apagar.
        ex_ml = uuid.uuid4()
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".conferencia_shopee_execucao'
                " (id, plataforma, tipo, origem, semanas, afiliados_ate,"
                "  esperar_afiliados_ate, corte, prazo)"
                " VALUES (:id, 'ml', 'semanal', 'manual', '[]', '2026-10-04', now(), now(),"
                "  now())"
            ),
            {"id": ex_ml},
        )
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".conferencia_shopee_coleta'
                " (execucao_id, nome, grupo) VALUES (:id, 'Kia', 'celular')"
            ),
            {"id": ex_ml},
        )
        # Duas rodadas coletando na MESMA plataforma: o único parcial segura.
        with pytest.raises(Exception, match="uq_conferencia_shopee_execucao_plataforma_coletando"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".conferencia_shopee_execucao'
                    " (plataforma, tipo, origem, semanas, afiliados_ate,"
                    "  esperar_afiliados_ate, corte, prazo)"
                    " VALUES ('ml', 'semanal', 'manual', '[]', '2026-10-04', now(), now(), now())"
                )
            )
        await db.rollback()
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".conferencia_shopee_execucao'
                " (id, plataforma, tipo, origem, semanas, afiliados_ate,"
                "  esperar_afiliados_ate, corte, prazo)"
                " VALUES (:id, 'ml', 'semanal', 'manual', '[]', '2026-10-04', now(), now(),"
                "  now())"
            ),
            {"id": ex_ml},
        )
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".conferencia_shopee_coleta'
                " (execucao_id, nome, grupo) VALUES (:id, 'Kia', 'celular')"
            ),
            {"id": ex_ml},
        )
        await db.commit()

        # O downgrade da 0382 volta EXATAMENTE ao catálogo da 0377 e só tira o
        # que é do ML/Amazon.
        await rodar(mod_p, "downgrade")
        assert await _catalogo(db, rascunho) == so_0377
        assert await contas_0377() == semeadas
        n = (
            await db.execute(
                text(f'SELECT count(*) FROM "{rascunho}".conferencia_shopee_execucao')
            )
        ).scalar_one()
        assert n == 0

        # O downgrade da 0377 tira tudo o que ela pôs.
        await rodar(mod, "downgrade")
        depois = await _catalogo(db, rascunho)
        assert depois == {"tabelas": [], "colunas": [], "constraints": [], "indices": []}
        n = (
            await db.execute(
                text(f'SELECT count(*) FROM "{rascunho}".threema_informar_config')
            )
        ).scalar_one()
        assert n == 0
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


def test_historico_fica_fora_das_tabelas_da_maquina():
    assert hsql.a_cobrir(TABELAS) == ["conferencia_shopee_conta"]
