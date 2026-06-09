"""Regressão da migration 0132: prod tinha a row id=1 (mala, era
singleton) E a sequence em last_value=1 / is_called=false. Sem setval
antes do INSERT do 'celular', o nextval devolvia 1 e colidia com a PK
da mala — `ON CONFLICT (categoria) DO NOTHING` só pega o conflito do
índice de categoria, conflito de PK aborta a transação.

O conftest cria a tabela via `Base.metadata.create_all` (não roda
alembic), então essa regressão não aparece nos testes existentes.
Este teste reproduz o estado de prod no nível de SQL e valida que o
fix funciona (próximo INSERT pega id=2, sem colisão).
"""
# ruff: noqa: S608
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SCHEMA = "davinci_test"


@pytest.mark.asyncio
async def test_celular_insert_apos_setval_evita_colisao_de_pk(db: AsyncSession):
    # Estado de prod: row id=1 (mala) + sequence em last_value=1 com
    # is_called=false (i.e., próximo nextval = 1).
    await db.execute(text(
        f'INSERT INTO "{SCHEMA}".import_config (id, categoria, tempo_reposicao, tempo_estoque) '
        f"VALUES (1, 'mala', 120, 60)"
    ))
    await db.execute(text(
        f"SELECT setval('\"{SCHEMA}\".import_config_id_seq', 1, false)"
    ))
    await db.commit()

    # Fix da migration: setval pra MAX(id) com is_called=true → próximo
    # nextval = 2.
    await db.execute(text(
        f"SELECT setval('\"{SCHEMA}\".import_config_id_seq', "
        f'(SELECT COALESCE(MAX(id), 1) FROM "{SCHEMA}".import_config), true)'
    ))

    # INSERT do celular SEM especificar id (igual a migration).
    await db.execute(text(
        f'INSERT INTO "{SCHEMA}".import_config '
        f"(categoria, tempo_reposicao, tempo_estoque) "
        f"VALUES ('celular', 150, 60) "
        f"ON CONFLICT (categoria) DO NOTHING"
    ))
    await db.commit()

    result = await db.execute(text(
        f'SELECT id, categoria FROM "{SCHEMA}".import_config ORDER BY id'
    ))
    rows = result.all()
    # Sem o setval, este INSERT abortaria com PK collision em id=1.
    assert [(r.id, r.categoria) for r in rows] == [(1, "mala"), (2, "celular")]


@pytest.mark.asyncio
async def test_setval_em_tabela_vazia_nao_quebra(db: AsyncSession):
    """A migration tem `COALESCE(MAX(id), 1)`. Mesa zerada deve continuar
    funcionando — próximo nextval cai em 2 (não ideal mas seguro)."""
    # Garante tabela vazia.
    await db.execute(text(f'DELETE FROM "{SCHEMA}".import_config'))
    await db.commit()

    await db.execute(text(
        f"SELECT setval('\"{SCHEMA}\".import_config_id_seq', "
        f'(SELECT COALESCE(MAX(id), 1) FROM "{SCHEMA}".import_config), true)'
    ))
    await db.execute(text(
        f'INSERT INTO "{SCHEMA}".import_config '
        f"(categoria, tempo_reposicao, tempo_estoque) "
        f"VALUES ('celular', 150, 60)"
    ))
    await db.commit()

    result = await db.execute(text(
        f'SELECT id, categoria FROM "{SCHEMA}".import_config'
    ))
    rows = result.all()
    assert len(rows) == 1
    assert rows[0].categoria == "celular"
    # id pode ser 2 (COALESCE(MAX, 1) → 1 + is_called=true → nextval=2).
    # Detalhe operacional aceito; o que importa é que NÃO colide.
    assert rows[0].id >= 2
