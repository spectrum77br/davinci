"""A 0437 zera a Conciliação e recomeça todas as lojas em 24/10 (Vinicius, 28/10).

Roda a migration de verdade no schema de teste: guarda a cópia das cinco tabelas,
apaga linhas, pedidos, dias e o controle das leituras, põe o início em 24/10 e limpa
"última leitura"/"último recálculo"/erro; os fretes do Melhor Envio ficam. O downgrade
devolve tudo a partir da cópia.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models import (
    Base,
    ConciliacaoDia,
    ConciliacaoFretePago,
    ConciliacaoLoja,
    ConciliacaoMovimento,
    ConciliacaoPedido,
    ConciliacaoSync,
    Integration,
)
from app.models.enums import IntegrationPlatform
from app.security.cipher import encrypt_json

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0437_conciliacao_recomeca_24_10.py"
)
AGORA = datetime(2026, 10, 27, 12, tzinfo=UTC)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0437", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _contar(db, tabela: str) -> int:
    schema = Base.metadata.schema
    return (await db.execute(text(f'SELECT count(*) FROM "{schema}"."{tabela}"'))).scalar_one()


async def test_0437_zera_e_recomeca_em_24_10_e_o_downgrade_devolve(db, make_user):
    mod = _carregar()
    assert mod.revision == "0437_conciliacao_recomeca_24_10"
    assert mod.down_revision == "0436_atendimento_canal_auto_continuacao"
    dono = await make_user()
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.ML,
        name="kia",
        status="active",
        credentials=encrypt_json({"access_token": "tok"}),
    )
    db.add(integ)
    await db.flush()
    iid = integ.id
    db.add_all(
        [
            ConciliacaoLoja(
                integration_id=iid,
                platform=IntegrationPlatform.ML,
                conta="kia (ml)",
                inicio=date(2026, 10, 1),
                ultimo_extrato_em=AGORA,
                ultimo_fatura_em=AGORA,
                ultimo_recalculo_em=AGORA,
                ultimo_erro="saldo não bate",
                extrato_config_em=AGORA,
            ),
            ConciliacaoMovimento(
                integration_id=iid,
                platform=IntegrationPlatform.ML,
                origem="mp_extrato",
                id_externo="x1",
                data=AGORA,
                tipo="fatura_cobrada",
                debito=Decimal("362.32"),
                classificacao="fatura_ml",
                vinculado_por="cairo sa",
            ),
            ConciliacaoPedido(
                integration_id=iid,
                platform=IntegrationPlatform.ML,
                pedido_bling="302141",
                etapa="finalizado",
                observacao="motivo dele",
            ),
            ConciliacaoDia(integration_id=iid, dia=date(2026, 10, 16), fechado_por="cairo sa"),
            ConciliacaoSync(integration_id=iid, origem="mp_extrato", chave="2026-10-01_2026-10-27"),
            ConciliacaoFretePago(integration_id=iid, envio_id="ME1", frete_pago=Decimal("31.89")),
        ]
    )
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    schema = Base.metadata.schema
    mod.SCHEMA = schema
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()

        for tabela in mod.APAGADAS:
            assert await _contar(db, tabela) == 0, tabela
            assert await _contar(db, tabela + mod.SUFIXO) == 1, tabela
        assert await _contar(db, "conciliacao_lojas_ate_20261028") == 1
        assert await _contar(db, "conciliacao_fretes_pagos") == 1  # fato do Melhor Envio fica
        loja = (
            await db.execute(
                text(
                    f"SELECT inicio, ultimo_extrato_em, ultimo_fatura_em, ultimo_recalculo_em,"
                    f' ultimo_erro, extrato_config_em FROM "{schema}".conciliacao_lojas'
                )
            )
        ).one()
        assert loja.inicio == date(2026, 10, 24)
        assert (loja.ultimo_extrato_em, loja.ultimo_fatura_em, loja.ultimo_recalculo_em) == (
            None,
            None,
            None,
        )
        assert loja.ultimo_erro is None
        assert loja.extrato_config_em == AGORA  # a config do relatório do MP continua valendo

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        for tabela in mod.APAGADAS:
            assert await _contar(db, tabela) == 1, tabela
        mov = (
            await db.execute(
                text(f'SELECT classificacao, vinculado_por FROM "{schema}".conciliacao_movimentos')
            )
        ).one()
        assert (mov.classificacao, mov.vinculado_por) == ("fatura_ml", "cairo sa")
        loja = (
            await db.execute(text(f'SELECT inicio, ultimo_erro FROM "{schema}".conciliacao_lojas'))
        ).one()
        assert (loja.inicio, loja.ultimo_erro) == (date(2026, 10, 1), "saldo não bate")
        copias = (
            await db.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema = :s AND table_name LIKE '%\\_ate\\_20261028'"
                ),
                {"s": schema},
            )
        ).scalar_one()
        assert copias == 0
    finally:
        await db.rollback()
        for tabela in mod.COPIADAS:
            await db.execute(text(f'DROP TABLE IF EXISTS "{schema}"."{tabela}{mod.SUFIXO}"'))
        await db.commit()
