"""16/10 (Vinicius): "a Margem lê uma vez, decidimos, e não relê o pedido". Linha
decidida (Aprovado por qualquer um; Reprovado por pessoa) não é tocada pelo rebuild nem
pelos refreshes do robô — fica com os números da decisão; só situação/pino acompanham o
Bling. Reprovado pelo robô (hold) continua vivo."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, SituacaoBling
from app.services import verificar_margem as vm


async def _seed(
    db: AsyncSession,
    *,
    pedido: str,
    bling_id: int,
    status: str | None,
    aprovado_por: uuid.UUID | None = None,
    margem: float = 0.5,
    situacao: str = "6",
) -> None:
    await db.merge(SituacaoBling(id=6, nome="Em aberto"))
    await db.merge(SituacaoBling(id=12, nome="Cancelado"))
    await db.merge(SituacaoBling(id=83955, nome="Aguardando Cancelamento"))
    item_id = uuid.uuid4()
    db.add(
        BlingOrder(
            id=item_id,
            bling_id=bling_id,
            numero=pedido,
            item_codigo=f"sku-{pedido}",
            item_index=0,
            situacao=situacao,
            status=status,
            aprovado_por=aprovado_por,
        )
    )
    await db.commit()
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku, situacao, situacao_nome,
                plataforma_bling, item_proportion, bling_status_margem, aprovado_por,
                marketplace_margem, margem_minima
            ) VALUES (
                :id, :pedido, :bling_id, :sku, :situacao, 'Em aberto',
                'ml', 1, :status, :aprovado_por, :margem, 0.10
            )
            """
        ),
        {
            "id": str(item_id),
            "pedido": pedido,
            "bling_id": bling_id,
            "sku": f"sku-{pedido}",
            "situacao": situacao,
            "status": status,
            "aprovado_por": str(aprovado_por) if aprovado_por else None,
            "margem": margem,
        },
    )
    await db.commit()


async def _linha(db: AsyncSession, pedido: str) -> dict | None:
    r = (
        (
            await db.execute(
                text(
                    "SELECT situacao, situacao_nome, bling_status_margem, aprovado_por, "
                    "marketplace_margem FROM verificar_margem WHERE pedido_bling = :p"
                ),
                {"p": pedido},
            )
        )
        .mappings()
        .first()
    )
    return dict(r) if r else None


async def test_pedido_decidido_nao_e_relido_e_so_espelha_o_bling(db, make_user):
    pessoa = await make_user()
    await _seed(db, pedido="400001", bling_id=91, status="Aprovado")  # robô aprovou
    await _seed(db, pedido="400002", bling_id=92, status="Reprovado", aprovado_por=pessoa.id)
    await _seed(db, pedido="400003", bling_id=93, status="Reprovado")  # hold do robô
    await _seed(db, pedido="400004", bling_id=94, status="Pendente")
    assert await vm._tem_decidida(db, pedido_bling="400001")
    assert await vm._tem_decidida(db, bling_id=92)
    assert not await vm._tem_decidida(db, pedido_bling="400003")
    assert not await vm._tem_decidida(db, pedido_bling="400004")
    # refresh do robô num pedido decidido: nada é relido (0 linhas), números ficam
    assert await vm.refresh_for_pedido(db, "400001") == 0
    assert await vm.refresh_for_bling_id(db, 92) == 0
    assert float((await _linha(db, "400001"))["marketplace_margem"]) == 0.5
    # o Bling mudou depois (cancelou): situação e pino acompanham, número não
    await db.execute(text("UPDATE bling_orders SET situacao = '12' WHERE numero = '400001'"))
    await db.commit()
    assert await vm.refresh_for_pedido(db, "400001") == 0
    linha = await _linha(db, "400001")
    assert linha["situacao"] == "12" and linha["situacao_nome"] == "Cancelado"
    assert linha["bling_status_margem"] == "Aprovado" and float(linha["marketplace_margem"]) == 0.5


async def test_rebuild_preserva_decididas_e_refaz_as_vivas(db, make_user):
    """A view é trocada por uma tabela-espelho (a de verdade precisa de meia dúzia de
    tabelas): a linha viva vem com margem nova; a decidida não é tocada."""
    pessoa = await make_user()
    await _seed(db, pedido="400011", bling_id=111, status="Aprovado", aprovado_por=pessoa.id)
    await _seed(db, pedido="400012", bling_id=112, status="Pendente", margem=0.05)
    # Ninguém decidiu (status NULL) — o caso de quase todo pedido novo. 19/10: o
    # NULL fazia o rebuild pular a linha e ela congelava na 1ª leitura.
    await _seed(db, pedido="400013", bling_id=113, status=None, margem=0.05)
    stub = vm.qualified_table("_vm_stub_view_src")
    await db.execute(text(f"CREATE TABLE {stub} (LIKE {vm.SNAPSHOT_TABLE} INCLUDING ALL)"))  # noqa: S608
    await db.execute(text(f"INSERT INTO {stub} SELECT * FROM {vm.SNAPSHOT_TABLE}"))  # noqa: S608
    # a "view" recalculou as duas: margem 0.9 em ambas
    await db.execute(text(f"UPDATE {stub} SET marketplace_margem = 0.9"))  # noqa: S608
    await db.execute(text(f"CREATE VIEW {vm.VIEW_TABLE} AS SELECT * FROM {stub}"))  # noqa: S608
    await db.commit()
    try:
        await vm.rebuild_all(db)
        viva = await _linha(db, "400012")
        decidida = await _linha(db, "400011")
        assert float(viva["marketplace_margem"]) == 0.9  # refeita a partir da view
        assert float((await _linha(db, "400013"))["marketplace_margem"]) == 0.9  # sem status
        assert float(decidida["marketplace_margem"]) == 0.5  # ficou como na decisão
        assert decidida["bling_status_margem"] == "Aprovado"
        # pessoa voltou pra Pendente: a linha volta a ser viva no rebuild seguinte
        await db.execute(
            text(
                "UPDATE bling_orders SET status = 'Pendente', aprovado_por = NULL "
                "WHERE numero = '400011'"
            )
        )
        await db.commit()
        await vm.rebuild_all(db)  # 1º: espelha o pino (deixa de ser decidida)
        assert (await _linha(db, "400011"))["bling_status_margem"] == "Pendente"
        await vm.rebuild_all(db)  # 2º: já viva, é refeita
        assert float((await _linha(db, "400011"))["marketplace_margem"]) == 0.9
    finally:
        await db.execute(text(f"DROP VIEW IF EXISTS {vm.VIEW_TABLE}"))
        await db.execute(text(f"DROP TABLE IF EXISTS {stub}"))
        await db.commit()
