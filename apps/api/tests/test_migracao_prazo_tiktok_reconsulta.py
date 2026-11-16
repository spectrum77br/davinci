"""A 0490 libera as devoluções TikTok pra rotina do prazo consultar de novo (16/11/2026).

Até o 8a452781 o ramo TikTok de `devolucao_prazo` dava TypeError e a rodada carimbava
`prazo_contestacao_at` sem prazo; com motivo preenchido, a rotina nunca mais lia a
linha. Roda a migration de verdade no schema de teste (dados FABRICADOS, nunca a
produção).
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import Base, BlingOrder, Devolution, StoreInfo

pytestmark = pytest.mark.asyncio

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0490_devolucao_prazo_tiktok_reconsulta.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0490", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SCHEMA = Base.metadata.schema
    return mod


async def _rodar(db, mod) -> None:
    def _executar(conn) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()

    conn = await db.connection()
    await conn.run_sync(_executar)
    await db.commit()


def _pedido(numero: str, numeroloja: str, loja: str) -> BlingOrder:
    return BlingOrder(
        id=uuid4(),
        numero=numero,
        numeroloja=numeroloja,
        bling_id=1,
        situacao="83957",
        loja=loja,
        data=datetime(2026, 11, 1, tzinfo=UTC),
        item_index=0,
        item_codigo="b001.26",
    )


async def test_0490_so_tiktok_sem_prazo_da_janela(db, make_user, capsys):
    mod = _carregar()
    dono = await make_user()
    agora = datetime.now(UTC)
    consultada = agora - timedelta(hours=3)
    db.add_all(
        [
            StoreInfo(
                user_id=dono.id, platform="TikTok Shop", account_name="injox", bling_store_id="77"
            ),
            StoreInfo(
                user_id=dono.id, platform="shopee", account_name="barbosa", bling_store_id="88"
            ),
            _pedido("290845", "585585025945338891", "77"),
            _pedido("291835", "2509X", "88"),
        ]
    )

    def dev(pedido: str, conta: str, **kw) -> Devolution:
        base = {
            "conta": conta,
            "pedido_bling": pedido,
            "sku": "b001.26",
            "motivo_devolucao": "Danificado (Outros)",
            "prazo_contestacao_at": consultada,
        }
        base.update(kw)
        return Devolution(**base)

    linhas = {
        # TikTok pelo espelho do pedido (a conta da linha é o nome da loja no Bling)
        "tt_pelo_pedido": dev("290845", "Loja 205660518"),
        # TikTok só pelo nome da conta (pedido fora do espelho)
        "tt_pela_conta": dev("999001", "injox"),
        "tt_com_prazo": dev("290845", "injox", prazo_contestacao=agora + timedelta(days=2)),
        "tt_velha": dev("290845", "injox", created_at=agora - timedelta(days=60)),
        "tt_nunca_consultada": dev("290845", "injox", prazo_contestacao_at=None),
        "shopee": dev("291835", "barbosa"),
        "sem_pedido_nem_conta": dev("", ""),
    }
    db.add_all(linhas.values())
    await db.commit()

    await _rodar(db, mod)

    assert "liberadas pra rotina do prazo consultar de novo: 2" in capsys.readouterr().out
    for d in linhas.values():
        await db.refresh(d)
    assert linhas["tt_pelo_pedido"].prazo_contestacao_at is None
    assert linhas["tt_pela_conta"].prazo_contestacao_at is None
    for nome in ("tt_com_prazo", "tt_velha", "shopee", "sem_pedido_nem_conta"):
        assert linhas[nome].prazo_contestacao_at is not None, nome
    assert linhas["tt_com_prazo"].prazo_contestacao is not None  # prazo nunca é apagado
    assert linhas["tt_nunca_consultada"].prazo_contestacao_at is None

    # de novo: nada sobrou pra liberar
    await _rodar(db, mod)
    assert "consultar de novo: 0" in capsys.readouterr().out
