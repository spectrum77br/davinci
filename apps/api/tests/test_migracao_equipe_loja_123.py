"""A 0445 acerta a "Equipe" de Lojas: 1 Londres, 2 Israel, 3 Marrocos (Eduardo, 03/11).

Roda a migration de verdade no schema de teste (lojas e usuários FABRICADOS, com
os ids da lista para os usuários; nunca a produção). O schema de teste nasce do
model, que já aceita 3: o teste põe primeiro o CHECK de antes (1, 2), como está
na produção.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Base, StoreInfo, User, UserRole, UserStatus

_MIGRATION = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0445_equipe_da_loja_123.py"
)
LONDRES, ISRAEL, MARROCOS, MAKISA, JA_CERTA = (str(uuid4()) for _ in range(5))


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0445", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SCHEMA = Base.metadata.schema
    return mod


async def _rodar(db, mod, passo: str, *args) -> None:
    def _executar(conn) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)(*args)

    conn = await db.connection()
    await conn.run_sync(_executar)
    await db.commit()


async def _semear(db, mod, make_user, **valores_usuario) -> None:
    """Lojas e usuários como na produção em 03/11 (CHECK 1, 2)."""
    await _rodar(db, mod, "_check", "1, 2")
    dono = await make_user()
    arquivada = datetime(2026, 7, 7, tzinfo=UTC)
    atv_ml, presencial = mod.SEM_NUMERO_EM_0311
    db.add_all(
        [
            StoreInfo(
                id=UUID(LONDRES),
                user_id=dono.id,
                platform="shopee",
                account_name="barbosa",
                sales_team=101,
                commercial_team=1,
            ),
            StoreInfo(
                id=UUID(ISRAEL),
                user_id=dono.id,
                platform="shopee",
                account_name="atv",
                sales_team=102,
                commercial_team=1,
            ),
            StoreInfo(
                id=UUID(MARROCOS),
                user_id=dono.id,
                platform="shopee",
                account_name="poofy",
                sales_team=103,
                commercial_team=1,
                archived_at=arquivada,
            ),
            StoreInfo(
                id=UUID(atv_ml),
                user_id=dono.id,
                platform="mercadolivre",
                account_name="atv",
                sales_team=102,
                archived_at=arquivada,
            ),
            StoreInfo(
                id=UUID(presencial),
                user_id=dono.id,
                platform="presencial",
                account_name="venda presencial",
                sales_team=103,
            ),
            StoreInfo(id=UUID(MAKISA), user_id=dono.id, platform="amazon", account_name="makisa"),
            StoreInfo(
                id=UUID(JA_CERTA),
                user_id=dono.id,
                platform="ml",
                account_name="kia",
                sales_team=101,
                commercial_team=1,
            ),
        ]
    )
    for uid, nome, antes, _novo in mod.USUARIOS:
        email = f"{nome.replace(' ', '')}@davinci-test.com"
        db.add(
            User(
                id=UUID(uid),
                open_id=f"email:{email}",
                email=email,
                name=nome,
                role=UserRole.USER,
                status=UserStatus.ACTIVE,
                commercial_team=valores_usuario.get(nome, antes),
            )
        )
    db.add(
        User(
            open_id="email:londres@davinci-test.com",
            email="londres@davinci-test.com",
            name="londres",
            role=UserRole.USER,
            status=UserStatus.ACTIVE,
            commercial_team=1,
            sales_teams=[101],
        )
    )
    await db.commit()


async def _lojas(db) -> dict[str, tuple]:
    s = Base.metadata.schema
    linhas = await db.execute(
        text(f'SELECT id::text, commercial_team, sales_team FROM "{s}".store_info')  # noqa: E501
    )
    return {i: (c, v) for i, c, v in linhas.all()}


async def _pessoas(db) -> dict[str, int | None]:
    s = Base.metadata.schema
    linhas = await db.execute(
        text(f'SELECT name, commercial_team FROM "{s}".users WHERE name IS NOT NULL')  # noqa: E501
    )
    return dict(linhas.all())


async def _gravar_3(db, tabela: str, condicao: str) -> None:
    s = Base.metadata.schema
    await db.execute(text(f'UPDATE "{s}".{tabela} SET commercial_team = 3 WHERE {condicao}'))
    await db.commit()


async def test_0445_acerta_lojas_e_usuarios_e_o_downgrade_devolve(db, make_user, capsys):
    mod = _carregar()
    assert mod.revision == "0445_equipe_da_loja_123"
    assert mod.down_revision == "0444_tipo_conteudo_historia_produto"
    await _semear(db, mod, make_user)
    atv_ml, presencial = mod.SEM_NUMERO_EM_0311
    antes, pessoas_antes = await _lojas(db), await _pessoas(db)
    with pytest.raises(IntegrityError):  # o CHECK de 03/11 não aceita 3
        await _gravar_3(db, "store_info", f"id = '{MAKISA}'")
    await db.rollback()

    await _rodar(db, mod, "upgrade")
    lojas = await _lojas(db)
    assert lojas[LONDRES] == (1, 101)
    assert lojas[ISRAEL] == (2, 102)
    assert lojas[MARROCOS] == (3, 103)  # arquivada também
    assert lojas[atv_ml] == (2, 102) and lojas[presencial] == (3, 103)  # estavam sem número
    assert lojas[MAKISA] == (None, None)  # sem equipe de vendas: não muda
    assert lojas[JA_CERTA] == (1, 101)
    assert "4 loja(s) com a equipe 1/2/3 acertada; 4 usuário(s)" in capsys.readouterr().out
    pessoas = await _pessoas(db)
    assert pessoas == {"israel": 2, "marrocos": 3, "cairo": None, "cairo sa": None, "londres": 1}

    # Rodar de novo não muda nada (e não para).
    await _rodar(db, mod, "upgrade")
    assert await _lojas(db) == lojas and await _pessoas(db) == pessoas

    await _rodar(db, mod, "downgrade")
    assert await _lojas(db) == antes
    assert await _pessoas(db) == pessoas_antes
    with pytest.raises(IntegrityError):  # o CHECK voltou a (1, 2)
        await _gravar_3(db, "users", "name = 'londres'")
    await db.rollback()


async def test_0445_usuario_num_valor_inesperado_para_tudo(db, make_user):
    mod = _carregar()
    await _semear(db, mod, make_user, cairo=1)  # nem 2 (03/11) nem NULL (novo)
    antes, pessoas_antes = await _lojas(db), await _pessoas(db)
    with pytest.raises(RuntimeError, match="cairo está com equipe 1"):
        await _rodar(db, mod, "upgrade")
    await db.rollback()
    assert await _lojas(db) == antes and await _pessoas(db) == pessoas_antes


async def test_0445_downgrade_com_equipe_3_mexida_depois_para(db, make_user):
    mod = _carregar()
    await _semear(db, mod, make_user)
    await _rodar(db, mod, "upgrade")
    # Alguém pôs a makisa (sem equipe de vendas) na Equipe 3 à mão.
    await _gravar_3(db, "store_info", f"id = '{MAKISA}'")
    with pytest.raises(RuntimeError, match="equipe 3 mexidos depois"):
        await _rodar(db, mod, "downgrade")
    await db.rollback()
    assert (await _lojas(db))[MAKISA] == (3, None)
