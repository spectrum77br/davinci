"""A 0508 cria Cadastros › Produtos igual ao model e com os dados do pedido M-29 (24/11/2026).

O conftest monta o schema de teste pelo `create_all` do model, nunca pela migration. Aqui a
migration roda de verdade num schema rascunho e o catálogo — colunas, constraints e índices,
inclusive os ÚNICOS por lower(nome) — tem de sair igual ao do model. Confere os 2 tipos e os 14
nomes iniciais, a recusa de nome repetido sem olhar maiúsculas, o RESTRICT do tipo com nomes,
e o downgrade (as duas tabelas saem) e o upgrade de novo.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Base

_MIGRATION = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0508_produto_cadastros.py"
)
TABELAS = ("produto_cadastro_tipos", "produto_cadastros")


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0508", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db, schema: str) -> dict:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "") if s else s

    colunas = (
        await db.execute(
            text(
                "SELECT table_name, column_name, udt_name, character_maximum_length, "
                "is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = ANY(:t) "
                "ORDER BY table_name, column_name"
            ),
            {"s": schema, "t": list(TABELAS)},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                "SELECT cl.relname, co.conname, co.contype::text, pg_get_constraintdef(co.oid) "
                "FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :s AND cl.relname = ANY(:t) ORDER BY cl.relname, co.conname"
            ),
            {"s": schema, "t": list(TABELAS)},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT tablename, indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = :s AND tablename = ANY(:t) ORDER BY tablename, indexname"
            ),
            {"s": schema, "t": list(TABELAS)},
        )
    ).all()
    return {
        "colunas": [tuple(r[:5]) + (limpo(r[5]),) for r in colunas],
        "constraints": [(r[0], r[1], r[2], limpo(r[3])) for r in constraints],
        "indices": [(r[0], r[1], limpo(r[2])) for r in indices],
    }


@pytest.mark.asyncio
async def test_0508_cria_igual_ao_model_com_os_dados_do_pedido_e_desce(db):
    mod = _carregar()
    assert mod.revision == "0508_produto_cadastros"
    # Depois da 0507 do M-22, que vem depois da 0506 da Fraude do main (24/11): uma ponta só, a
    # nossa. Com duas pontas o `alembic upgrade head` do deploy recusa.
    assert mod.down_revision == "0507_marketing_gasto_dia"
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    raiz = Path(__file__).resolve().parent.parent
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "alembic"))
    roteiro = ScriptDirectory.from_config(cfg)
    # Uma ponta só (a 0509 da aprovação automática veio depois, 24/11/2026).
    assert len(roteiro.get_heads()) == 1, roteiro.get_heads()
    caminho = [r.revision for r in roteiro.walk_revisions("base", "0508_produto_cadastros")]
    assert caminho[:4] == [
        "0508_produto_cadastros",
        "0507_marketing_gasto_dia",
        "0506_devolucao_fraude_reabre_negociacao",
        "0505_alertas_por_equipe",
    ]
    from app.models.produto_cadastro import CHAVE_CATEGORIA, CHAVE_TAG_ENVIO

    assert [c for c, _t, _n in mod.INICIAIS] == [CHAVE_CATEGORIA, CHAVE_TAG_ENVIO]

    modelo = Base.metadata.schema
    rascunho = f"{modelo}_m0508"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # A 0508 também abre a aba a quem já vê Cadastros (teste abaixo): a users tem de existir.
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".users (name text, email text, role text, permissions jsonb)'
        )
    )
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        da_migration = await _catalogo(db, rascunho)
        do_model = await _catalogo(db, modelo)
        assert da_migration["colunas"], "a migration não criou as tabelas"
        assert da_migration == do_model

        linhas = (
            await db.execute(
                text(
                    f'SELECT t.nome, t.chave, c.nome FROM "{rascunho}".produto_cadastros c '
                    f'JOIN "{rascunho}".produto_cadastro_tipos t ON t.id = c.tipo_id '
                    "ORDER BY t.nome, c.nome"
                )
            )
        ).all()
        assert [(t, c) for t, _k, c in linhas] == [
            ("Categoria", "Acessórios"),
            ("Categoria", "Celular"),
            ("Categoria", "Celular Kit"),
            ("Categoria", "Eletro"),
            ("Categoria", "Eletro Kit"),
            ("Categoria", "Insumos"),
            ("Categoria", "Mala"),
            ("Categoria", "Mala Kit"),
            ("Tag de envio", ".cd"),
            ("Tag de envio", ".ci"),
            ("Tag de envio", ".pi"),
            ("Tag de envio", ".ra"),
            ("Tag de envio", ".sa"),
            ("Tag de envio", ".sp"),
        ]
        assert {(t, k) for t, k, _c in linhas} == {
            ("Categoria", "categoria"),
            ("Tag de envio", "tag_envio"),
        }

        # nome repetido no tipo sem olhar maiúsculas; tipo repetido; tipo com nomes não sai
        with pytest.raises(IntegrityError, match="uq_produto_cadastros_tipo_nome"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".produto_cadastros (tipo_id, nome) '
                    f"SELECT id, 'CELULAR' FROM \"{rascunho}\".produto_cadastro_tipos "
                    "WHERE chave = 'categoria'"
                )
            )
        await db.rollback()
        with pytest.raises(IntegrityError, match="uq_produto_cadastro_tipos_nome"):
            await db.execute(
                text(
                    f"INSERT INTO \"{rascunho}\".produto_cadastro_tipos (nome) VALUES ('categoria')"
                )
            )
        await db.rollback()
        with pytest.raises(IntegrityError, match="fk_produto_cadastros_tipo_id"):
            await db.execute(
                text(f"DELETE FROM \"{rascunho}\".produto_cadastro_tipos WHERE chave = 'tag_envio'")
            )
        await db.rollback()

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert (await _catalogo(db, rascunho))["colunas"] == []

        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        assert await _catalogo(db, rascunho) == do_model
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


@pytest.mark.asyncio
async def test_0508_abre_a_aba_para_quem_ja_ve_alguma_aba_de_cadastros(db):
    """M-29 item 11: "mesma permissão das outras abas do grupo Cadastros". A aba tem a chave
    própria `cadastro_produtos`; para ninguém que já trabalha em Cadastros ficar sem ela no dia do
    deploy, a 0508 dá `view` a quem já tem `view` em alguma aba de Cadastros com chave própria.
    Criar/editar/excluir continuam só de admin (no servidor). Admin não precisa (vê tudo). Quem
    não vê nenhuma aba de Cadastros, ou só a Legendas (chave dos Criativos), não ganha nada.
    O downgrade tira a chave de todos."""
    mod = _carregar()
    rascunho = f"{Base.metadata.schema}_m0508u"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a 0508 toca na users, como na produção (role é enum lá; aqui texto).
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".users (name text, email text PRIMARY KEY, '
            "role text NOT NULL, permissions jsonb)"
        )
    )
    ver = '{"view": true, "edit": false, "delete": false}'
    pessoas = {
        "loja@x": ("user", f'{{"lojas_info": {ver}}}'),
        "integra@x": ("user", f'{{"integracoes": {ver}, "tabela_precos": {ver}}}'),
        "cadastro@x": ("user", f'{{"cadastro": {ver}, "empresa": {ver}}}'),
        "admin@x": ("admin", f'{{"lojas_info": {ver}}}'),
        "legendas@x": ("user", f'{{"marketing_criativos": {ver}}}'),
        "sem_view@x": ("user", '{"lojas_info": {"view": false, "edit": true}}'),
        "nada@x": ("user", None),
    }
    for email, (role, perms) in pessoas.items():
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".users (name, email, role, permissions) '
                "VALUES (:e, :e, :r, CAST(:p AS jsonb))"
            ),
            {"e": email, "r": role, "p": perms},
        )
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    async def _aba() -> dict:
        linhas = await db.execute(
            text(f"SELECT email, permissions -> 'cadastro_produtos' FROM \"{rascunho}\".users")
        )
        return dict(linhas.all())

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        so_ver = {"view": True, "edit": False, "delete": False}
        assert await _aba() == {
            "loja@x": so_ver,
            "integra@x": so_ver,
            "cadastro@x": so_ver,
            "admin@x": None,
            "legendas@x": None,
            "sem_view@x": None,
            "nada@x": None,
        }
        # O resto das permissões fica como estava.
        integra = (
            await db.execute(
                text(f"SELECT permissions FROM \"{rascunho}\".users WHERE email = 'integra@x'")
            )
        ).scalar_one()
        assert integra["tabela_precos"] == {"view": True, "edit": False, "delete": False}

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert set((await _aba()).values()) == {None}
        assert (
            await db.execute(
                text(f"SELECT permissions FROM \"{rascunho}\".users WHERE email = 'nada@x'")
            )
        ).scalar_one() is None
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
