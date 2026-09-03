"""Aba Margem mostra só pedidos EM TRIAGEM (Eduardo, 03/09).

"em margem é para aparecer somente os com situação em aberto e enviado
etiqueta" — Entregue/Resolvido/Problemas etc. saem da listagem por padrão
(`situacao=triagem`); `situacao=all` mostra tudo; o lookup ("Buscar pedido")
continua achando qualquer situação; o Informar segue a mesma régua da aba.

"Enviado etiqueta" = 21 (Em digitação, canônico desde 03/09/2026) e 83965
(Enviado Etiqueta, legado — pedidos históricos): os dois ficam na triagem.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers.informar import _pedidos_margem

pytestmark = pytest.mark.asyncio


def _perms() -> dict:
    return {"margem": {"view": True, "edit": True, "delete": False}}


async def _seed(
    db: AsyncSession,
    *,
    pedido: str,
    situacao: str,
    situacao_nome: str,
    status_gravado: str | None = None,
    data: datetime | None = None,
) -> None:
    """Linha-item shopee com margem baixa (6% < mínima 10%) → Pendente por
    gatilho, em qualquer situação; `status_gravado` simula o pino do robô;
    `data` = data do pedido (default agora — "recente")."""
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, loja_nome,
                item_proportion, marketplace_margem, margem_minima,
                bling_valorbase_item, bling_custo_produtos,
                marketplace_liquido_base_margem_item, bling_status_margem, data
            ) VALUES (
                :id, :pedido, :bling_id, :sku,
                :situacao, :situacao_nome, 'shopee', 'Loja Teste',
                1, 0.06, 0.10,
                200, 100,
                106, :status_gravado, :data
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "pedido": pedido,
            "bling_id": int(pedido),
            "sku": f"sku-{pedido}",
            "situacao": situacao,
            "situacao_nome": situacao_nome,
            "status_gravado": status_gravado,
            "data": data or datetime.now(UTC),
        },
    )
    await db.commit()


async def _pedidos_listados(client, **params) -> set[str]:
    res = await client.get(
        "/api/margens/marketplace", params={"status": "Pendente", **params}
    )
    assert res.status_code == 200, res.text
    return {i["pedido_bling"] for i in res.json()["items"]}


async def test_listagem_mostra_so_em_aberto_e_enviado_etiqueta(
    client, db, make_user, auth_as
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, pedido="910001", situacao="6", situacao_nome="Em aberto")
    await _seed(db, pedido="910002", situacao="83965", situacao_nome="Enviado Etiqueta")
    await _seed(db, pedido="910006", situacao="21", situacao_nome="Em digitação")
    await _seed(db, pedido="910003", situacao="83953", situacao_nome="Entregue")
    await _seed(db, pedido="910004", situacao="545902", situacao_nome="Resolvido")
    # Segurado pelo robô: 83955 com 'Pendente' gravado → tem que continuar
    # visível (é onde o Eduardo aprova/reprova).
    await _seed(
        db, pedido="910005", situacao="83955", situacao_nome="Aguardando Cancelamento",
        status_gravado="Pendente",
    )

    padrao = await _pedidos_listados(client)
    assert padrao >= {"910001", "910002", "910006", "910005"}
    assert not padrao & {"910003", "910004"}

    explicito = await _pedidos_listados(client, situacao="triagem")
    assert explicito == padrao

    tudo = await _pedidos_listados(client, situacao="all")
    assert tudo >= {"910001", "910002", "910006", "910003", "910004", "910005"}

    res = await client.get(
        "/api/margens/marketplace", params={"status": "Pendente", "situacao": "xyz"}
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "invalid_situacao"


async def test_triagem_esconde_pedidos_antigos(client, db, make_user, auth_as):
    """Eduardo 03/09 à tarde: "aparece somente os em aberto e os em digitação
    que virou recente, os antigos não precisam" — rascunhos "Em digitação"
    de maio (e Em aberto velhos) saem da triagem; com `situacao=all` seguem
    visíveis. Segurado pelo robô não tem recorte de idade."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    velho = datetime.now(UTC) - timedelta(days=45)
    await _seed(db, pedido="910030", situacao="21", situacao_nome="Em digitação", data=velho)
    await _seed(db, pedido="910031", situacao="6", situacao_nome="Em aberto", data=velho)
    await _seed(db, pedido="910032", situacao="21", situacao_nome="Em digitação")  # recente
    await _seed(
        db, pedido="910033", situacao="83955", situacao_nome="Aguardando Cancelamento",
        status_gravado="Pendente", data=velho,
    )

    padrao = await _pedidos_listados(client)
    assert "910032" in padrao and "910033" in padrao
    assert not padrao & {"910030", "910031"}

    tudo = await _pedidos_listados(client, situacao="all")
    assert tudo >= {"910030", "910031", "910032", "910033"}

    # O Informar segue a aba: os antigos ficam fora do relatório também.
    pedidos = {p.pedido for p in await _pedidos_margem(db)}
    assert "910032" in pedidos and "910030" not in pedidos


async def test_buscar_pedido_acha_qualquer_situacao(client, db, make_user, auth_as):
    """O lookup é pra achar UM pedido específico — não filtra por situação."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, pedido="910010", situacao="83953", situacao_nome="Entregue")

    res = await client.get("/api/margens/marketplace/lookup", params={"pedido": "910010"})
    assert res.status_code == 200, res.text
    assert {i["pedido_bling"] for i in res.json()["items"]} == {"910010"}


async def test_informar_segue_a_mesma_regua_da_aba(client, db, make_user, auth_as):
    """O relatório do Informar lista o que a aba Pendentes mostra — Entregue
    fica fora dele também."""
    await _seed(db, pedido="910020", situacao="6", situacao_nome="Em aberto")
    await _seed(db, pedido="910021", situacao="83953", situacao_nome="Entregue")
    # Segurado pelo robô (83955 + 'Pendente' gravado) entra no relatório.
    await _seed(
        db, pedido="910022", situacao="83955", situacao_nome="Aguardando Cancelamento",
        status_gravado="Pendente",
    )

    pedidos = {p.pedido for p in await _pedidos_margem(db)}
    assert "910020" in pedidos
    assert "910022" in pedidos
    assert "910021" not in pedidos
