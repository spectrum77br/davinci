"""Vídeo da embalagem por pedido (Controle de Estoque, 18/09/2026).

Vinicius: "todo pedido que a quantidade for mais de 1 pede vídeo (ex.: 2 Apple
Watch no mesmo pedido)". Botão antes de Obs salva o link (sempre Google
Drive); a aba Envios mostra por dia Feito / Parcial / Não feito contando só os
pedidos que pedem vídeo.

  * PUT /pedidos/{n}/video: só link do Drive, normaliza https, troca; DELETE.
  * GET /pedidos: `pede_video` pela SOMA das unidades do pedido inteiro
    (2 do mesmo SKU ou 2 produtos diferentes) + `video` salvo.
  * GET /envios: `videos` por dia (necessários/feitos/pendentes/status).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingEnvioEvento, BlingOrder, User, UserRole, UserStatus

pytestmark = pytest.mark.asyncio

PERM_EDIT = {"controle_estoque": {"view": True, "edit": True, "delete": False}}
_DIA = date(2026, 6, 2)
_DRIVE = "https://drive.google.com/file/d/1AbC/view?usp=sharing"


@pytest_asyncio.fixture
async def admin_edit(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:pv-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"pv-{uuid.uuid4().hex[:6]}@davinci-test.com",
        name="Empacotador",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM_EDIT,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _pedido(
    db: AsyncSession, bling_id: int, *itens: tuple[str, int | None], situacao: str = "83953"
) -> str:
    """Pedido verde no dia _DIA com os itens (sku, quantidade). 83953 (Entregue)
    não dispara o trigger do ledger — o evento é semeado à parte com o dia
    controlado (`_evento`)."""
    for i, (sku, qtd) in enumerate(itens):
        db.add(
            BlingOrder(
                bling_id=bling_id, numero=str(bling_id), item_codigo=sku, item_index=i,
                item_quantidade=qtd, situacao=situacao, em_andamento_data=_DIA,
            )
        )
    await db.commit()
    return str(bling_id)


async def _evento(db: AsyncSession, bling_id: int, *skus: str, dia: date = _DIA) -> None:
    for i, sku in enumerate(skus):
        db.add(
            BlingEnvioEvento(
                bling_id=bling_id, item_index=i, item_codigo=sku, numero=str(bling_id),
                occurred_at=datetime(dia.year, dia.month, dia.day, 12, 0), shipping_day=dia,
            )
        )
    await db.commit()


async def _pedidos(client: AsyncClient) -> dict[str, list[dict]]:
    r = await client.get(f"/api/estoque/pedidos?data_inicio={_DIA}&data_fim={_DIA}")
    assert r.status_code == 200, r.text
    out: dict[str, list[dict]] = {}
    for p in r.json()["data"]:
        out.setdefault(p["pedido_bling"], []).append(p)
    return out


async def _videos_do_dia(client: AsyncClient, dia: date = _DIA) -> dict:
    r = await client.get(f"/api/estoque/envios?data_inicio={dia}&data_fim={dia}")
    assert r.status_code == 200, r.text
    return next(i for i in r.json()["data"] if i["data"] == dia.isoformat())["videos"]


async def test_salvar_video_so_aceita_drive_e_troca(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None], admin_edit: User,
):
    auth_as(admin_edit)
    n = await _pedido(db, 960001, ("watch.sp", 2))
    url = f"/api/estoque/pedidos/{n}/video"

    # "ok", link do WhatsApp, vazio: não é prova, não conta
    for ruim in ("ok", "https://wa.me/5511999", "", "drive google"):
        r = await client.put(url, json={"link": ruim})
        assert r.status_code == 422, (ruim, r.text)
        assert r.json()["detail"]["code"] == "video_link_nao_drive"
    # pedido que não existe
    assert (await client.put("/api/estoque/pedidos/000000/video", json={"link": _DRIVE})).status_code == 404

    # sem https → completa; quem salvou e quando voltam pra tela
    r = await client.put(url, json={"link": "drive.google.com/file/d/1AbC/view"})
    assert r.status_code == 200, r.text
    assert r.json()["link"] == "https://drive.google.com/file/d/1AbC/view"
    assert r.json()["salvo_por"] == "Empacotador" and r.json()["salvo_em"]
    # trocar o link = mesmo pedido, sem duplicar
    r = await client.put(url, json={"link": _DRIVE})
    assert r.status_code == 200 and r.json()["link"] == _DRIVE
    assert (await _pedidos(client))[n][0]["video"]["link"] == _DRIVE

    # remover (colado no pedido errado)
    assert (await client.delete(url)).status_code == 204
    assert (await client.delete(url)).status_code == 404
    assert (await _pedidos(client))[n][0]["video"] is None


async def test_pedidos_pede_video_pela_soma_do_pedido(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None], admin_edit: User,
):
    auth_as(admin_edit)
    duas_unidades = await _pedido(db, 960011, ("watch.sp", 2))
    dois_produtos = await _pedido(db, 960012, ("watch.sp", 1), ("capa.sp", 1))
    um = await _pedido(db, 960013, ("watch.sp", 1))
    sem_qtd = await _pedido(db, 960014, ("watch.sp", None))

    rows = await _pedidos(client)
    assert rows[duas_unidades][0]["pede_video"] is True
    # 2 produtos diferentes na mesma caixa: as DUAS linhas pedem (grão = pedido)
    assert [p["pede_video"] for p in rows[dois_produtos]] == [True, True]
    assert rows[um][0]["pede_video"] is False
    assert rows[sem_qtd][0]["pede_video"] is False
    assert all(p["video"] is None for ps in rows.values() for p in ps)

    # o link salvo aparece em todas as linhas do pedido
    assert (
        await client.put(f"/api/estoque/pedidos/{dois_produtos}/video", json={"link": _DRIVE})
    ).status_code == 200
    rows = await _pedidos(client)
    assert [p["video"]["link"] for p in rows[dois_produtos]] == [_DRIVE, _DRIVE]
    assert rows[duas_unidades][0]["video"] is None


async def test_envios_videos_do_dia(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None], admin_edit: User,
):
    auth_as(admin_edit)
    a = await _pedido(db, 960021, ("watch.sp", 2))
    b = await _pedido(db, 960022, ("watch.sp", 1), ("capa.sp", 1))
    c = await _pedido(db, 960023, ("watch.sp", 1))
    await _evento(db, 960021, "watch.sp")
    await _evento(db, 960022, "watch.sp", "capa.sp")
    await _evento(db, 960023, "watch.sp")
    # outro dia só com pedido de 1 unidade
    outro = date(2026, 6, 3)
    await _pedido(db, 960024, ("watch.sp", 1))
    await _evento(db, 960024, "watch.sp", dia=outro)

    v = await _videos_do_dia(client)
    assert v == {"necessarios": 2, "feitos": 0, "pendentes": [a, b], "status": "nao_feito"}
    assert (await _videos_do_dia(client, outro))["status"] == "nenhum"

    assert (await client.put(f"/api/estoque/pedidos/{a}/video", json={"link": _DRIVE})).status_code == 200
    v = await _videos_do_dia(client)
    assert (v["feitos"], v["pendentes"], v["status"]) == (1, [b], "parcial")

    assert (await client.put(f"/api/estoque/pedidos/{b}/video", json={"link": _DRIVE})).status_code == 200
    v = await _videos_do_dia(client)
    assert (v["feitos"], v["pendentes"], v["status"]) == (2, [], "feito")
    # pedido de 1 unidade nunca entra na conta
    assert v["necessarios"] == 2 and c not in v["pendentes"]
