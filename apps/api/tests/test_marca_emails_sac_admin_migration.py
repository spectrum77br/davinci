"""A 0435 (28/10/2026): `marca_emails` fica só com sac@ e admin@.

O SQL da migration roda de verdade no schema de teste (o `davinci.` trocado
pelo do teste): o duvidas@ vira admin@ do mesmo domínio, duvidas@ e atacado@
saem, o admin@ que já existia fica, e o downgrade refaz os dois antigos.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.models import Marca, MarcaEmail
from tests.conftest import TEST_SCHEMA

pytestmark = pytest.mark.asyncio

_ARQUIVO = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0435_marca_emails_sac_admin.py"
)


class _Op:
    def __init__(self) -> None:
        self.sqls: list[str] = []

    def execute(self, sql: str) -> None:
        self.sqls.append(sql)


def _sqls(funcao: str) -> list[str]:
    spec = importlib.util.spec_from_file_location("m0435", _ARQUIVO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    op = _Op()
    mod.op = op
    getattr(mod, funcao)()
    return [s.replace("davinci.", f'"{TEST_SCHEMA}".') for s in op.sqls]


async def _rodar(db, funcao: str) -> None:
    for sql in _sqls(funcao):
        await db.execute(text(sql))
    await db.commit()


async def _linhas(db) -> dict[tuple[str, str], str]:
    out = {}
    for e in (await db.scalars(select(MarcaEmail).execution_options(populate_existing=True))).all():
        marca = await db.get(Marca, e.marca_id)
        out[(marca.slug, e.tipo)] = e.email
    return out


async def test_duvidas_vira_admin_e_os_antigos_saem(db):
    uranyx = Marca(nome="uranyx", slug="uranyx")
    ja_tem = Marca(nome="charlots", slug="poofy")
    so_atacado = Marca(nome="locagil", slug="locagil")
    db.add_all([uranyx, ja_tem, so_atacado])
    await db.flush()
    db.add_all(
        [
            MarcaEmail(marca_id=uranyx.id, tipo="sac", email="sac@uranyx.com.br"),
            MarcaEmail(marca_id=uranyx.id, tipo="duvidas", email="duvidas@uranyx.com.br"),
            MarcaEmail(marca_id=uranyx.id, tipo="atacado", email="atacado@uranyx.com.br"),
            MarcaEmail(marca_id=ja_tem.id, tipo="admin", email="admin@charlots.com.br"),
            MarcaEmail(marca_id=ja_tem.id, tipo="duvidas", email="duvidas@outro.com.br"),
            MarcaEmail(marca_id=so_atacado.id, tipo="atacado", email="atacado@locagil.com.br"),
        ]
    )
    await db.commit()

    await _rodar(db, "upgrade")
    assert await _linhas(db) == {
        ("uranyx", "sac"): "sac@uranyx.com.br",
        ("uranyx", "admin"): "admin@uranyx.com.br",
        # O admin@ que já existia não é trocado.
        ("poofy", "admin"): "admin@charlots.com.br",
    }
    # Rodar de novo não muda nada.
    await _rodar(db, "upgrade")
    assert len(await _linhas(db)) == 3

    await _rodar(db, "downgrade")
    assert await _linhas(db) == {
        ("uranyx", "sac"): "sac@uranyx.com.br",
        ("uranyx", "duvidas"): "duvidas@uranyx.com.br",
        ("uranyx", "atacado"): "atacado@uranyx.com.br",
        ("poofy", "duvidas"): "duvidas@charlots.com.br",
        ("poofy", "atacado"): "atacado@charlots.com.br",
    }
