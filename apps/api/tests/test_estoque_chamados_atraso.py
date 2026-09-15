"""Chamados de ATRASO NA POSTAGEM em lote (Controle de Estoque › Pedidos) —
Eduardo 15/09: um chamado por loja; fila até 60 min depois do corte, queda de
energia depois disso no mesmo dia; ML pelo robô, Shopee na mão; loja com os
dois motivos recebe um chamado só."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    BlingEnvioEvento,
    BlingOrder,
    Chamado,
    ChamadoMensagem,
    ChamadoPedido,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.services import chamados_atraso as svc

pytestmark = pytest.mark.asyncio

PERM_EDIT = {
    "controle_estoque": {"view": True, "edit": True, "delete": False},
    "chamados": {"view": True, "edit": True, "delete": False},
}
PERM_VIEW = {
    "controle_estoque": {"view": True, "edit": True, "delete": False},
    "chamados": {"view": True, "edit": False, "delete": False},
}


async def _user(db, perms: dict, *, role: UserRole = UserRole.ADMIN) -> User:
    u = User(
        open_id=f"email:at-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"at-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=role,
        status=UserStatus.ACTIVE,
        permissions=perms,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _corte(hora: int, minuto: int = 0) -> datetime:
    """Corte de HOJE às hora:minuto BRT (em UTC) — a regra do "mesmo dia" olha o dia BRT."""
    hoje = datetime.now(svc.BRT).date()
    return datetime(hoje.year, hoje.month, hoje.day, hora, minuto, tzinfo=svc.BRT).astimezone(UTC)


async def _lojas(db, user: User) -> None:
    db.add_all(
        [
            StoreInfo(user_id=user.id, platform="ml", account_name="aguiar", bling_store_id="55"),
            StoreInfo(
                user_id=user.id, platform="shopee", account_name="vortan", bling_store_id="77"
            ),
        ]
    )
    await db.commit()


async def _pedido(
    db,
    numero: str,
    *,
    loja: str = "55",
    corte: datetime | None,
    postagem: datetime | None,
    sku: str = "i200.sa",
) -> None:
    """Postado = situação 15 + evento no ledger. Não postado = 21 (etiqueta
    gerada) — o trigger do ledger só carimba a 15, como em produção."""
    bid = int(numero)
    db.add(
        BlingOrder(
            bling_id=bid,
            numero=numero,
            numeroloja=f"MK{numero}",
            item_codigo=sku,
            item_index=0,
            situacao="15" if postagem is not None else "21",
            loja=loja,
            data=datetime.now(UTC) - timedelta(days=1),
            em_andamento_data=postagem.date() if postagem else None,
            marketplace_ship_deadline=corte,
        )
    )
    if postagem is not None:
        db.add(
            BlingEnvioEvento(
                bling_id=bid,
                item_index=0,
                item_codigo=sku,
                numero=numero,
                occurred_at=postagem,
                shipping_day=postagem.astimezone(svc.BRT).date(),
            )
        )
    await db.commit()


async def _cenario(db, user: User) -> None:
    """ML aguiar: 940001 fila (12 min), 940002 energia (3h30). Shopee vortan:
    940003 fila (5 min). Fora: 940004 no prazo, 940005 dia seguinte, 940006 não
    postado, 940007 sem corte."""
    await _lojas(db, user)
    c13 = _corte(13)
    await _pedido(db, "940001", corte=c13, postagem=c13 + timedelta(minutes=12))
    await _pedido(db, "940002", corte=c13, postagem=c13 + timedelta(hours=3, minutes=30))
    await _pedido(db, "940003", loja="77", corte=_corte(12), postagem=_corte(12, 5))
    await _pedido(db, "940004", corte=c13, postagem=c13 - timedelta(minutes=10))
    await _pedido(db, "940005", corte=c13, postagem=c13 + timedelta(hours=12))
    await _pedido(db, "940006", corte=c13, postagem=None)
    await _pedido(db, "940007", corte=None, postagem=c13 + timedelta(minutes=5))


TODOS = ["940001", "940002", "940003", "940004", "940005", "940006", "940007", "999999"]


async def test_preview_classifica_agrupa_por_loja_e_explica_o_que_ficou_fora(client, db, auth_as):
    user = await _user(db, PERM_EDIT)
    auth_as(user)
    await _cenario(db, user)

    r = await client.post("/api/estoque/pedidos/chamados-atraso/preview", json={"pedidos": TODOS})
    assert r.status_code == 200, r.text
    body = r.json()
    grupos = {g["loja"]: g for g in body["grupos"]}
    assert set(grupos) == {"ML aguiar", "SHOPEE vortan"}

    ml = grupos["ML aguiar"]
    assert ml["canal"] == "robo" and ml["plataforma"] == "ml" and ml["conta"] == "aguiar"
    assert [(p["pedido_bling"], p["motivo"], p["atraso_min"]) for p in ml["pedidos"]] == [
        ("940001", "fila", 12),
        ("940002", "energia", 210),
    ]
    # loja com os dois motivos → um chamado só, texto misto com cada bloco
    assert "Fila na postagem:" in ml["texto"] and "Queda de energia:" in ml["texto"]
    assert "• MK940001 — despachar até 13:00, postagem confirmada às 13:12" in ml["texto"]
    assert "• MK940002 — despachar até 13:00, postagem confirmada às 16:30" in ml["texto"]
    assert "sobre 2 pedidos desta conta" in ml["texto"]

    sh = grupos["SHOPEE vortan"]
    assert sh["canal"] == "manual" and sh["plataforma"] == "shopee"
    assert [p["motivo"] for p in sh["pedidos"]] == ["fila"]
    assert "fila na agência" in sh["texto"] and "queda de energia" not in sh["texto"].lower()

    fora = {e["pedido_bling"]: e["motivo"] for e in body["excluidos"]}
    assert fora == {
        "940004": "no_prazo",
        "940005": "dia_seguinte",
        "940006": "nao_postado",
        "940007": "sem_corte",
        "999999": "pedido_nao_encontrado",
    }

    # escolha manual da tela vence o cálculo: 940001 vira energia → texto só de energia
    r = await client.post(
        "/api/estoque/pedidos/chamados-atraso/preview",
        json={"pedidos": ["940001", "940002"], "motivos": {"940001": "energia"}},
    )
    assert r.status_code == 200, r.text
    g = r.json()["grupos"][0]
    assert [p["motivo"] for p in g["pedidos"]] == ["energia", "energia"]
    assert "Fila na postagem:" not in g["texto"] and "queda de energia elétrica" in g["texto"]


async def test_abrir_cria_um_chamado_por_loja_liga_os_pedidos_e_nao_repete(client, db, auth_as):
    user = await _user(db, PERM_EDIT)
    auth_as(user)
    await _cenario(db, user)
    prev = (
        await client.post("/api/estoque/pedidos/chamados-atraso/preview", json={"pedidos": TODOS})
    ).json()
    grupos = []
    for g in prev["grupos"]:
        texto = g["texto"]
        if g["loja"] == "ML aguiar":
            texto = "TEXTO EDITADO NA TELA\n" + texto  # a tela deixa ajustar antes de enviar
        grupos.append(
            {
                "chave": g["chave"],
                "texto": texto,
                "pedidos": [
                    {"pedido_bling": p["pedido_bling"], "motivo": p["motivo"]} for p in g["pedidos"]
                ],
            }
        )

    r = await client.post("/api/estoque/pedidos/chamados-atraso", json={"grupos": grupos})
    assert r.status_code == 200, r.text
    abertos = [a for a in r.json()["abertos"] if a.get("chamado_id")]
    assert {(a["loja"], a["canal"], a["pedidos"]) for a in abertos} == {
        ("ML aguiar", "robo", 2),
        ("SHOPEE vortan", "manual", 1),
    }

    chamados = {
        c.conta: c
        for c in (await db.execute(select(Chamado).where(Chamado.origem == "logistica"))).scalars()
    }
    ml = chamados["aguiar"]
    assert ml.canal == "robo" and ml.plataforma == "ml" and ml.pedido_bling == "940001"
    assert ml.pedido_marketplace == "MK940001" and ml.resolvido is False
    assert "2 pedido(s)" in (ml.observacao or "") and "MK940002" in (ml.observacao or "")
    # abertura PENDENTE no canal robô, sem protocolo = tarefa `abrir` do robô do formulário
    abertura_ml = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ml.id, ChamadoMensagem.tipo == "abertura"
            )
        )
    ).scalar_one()
    assert abertura_ml.status == "pendente" and abertura_ml.canal == "robo"
    assert abertura_ml.direcao == "enviada" and abertura_ml.texto.startswith(
        "TEXTO EDITADO NA TELA"
    )
    assert ml.chamado is None

    sh = chamados["vortan"]
    assert sh.canal == "manual" and sh.plataforma == "shopee"
    abertura_sh = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == sh.id, ChamadoMensagem.tipo == "abertura"
            )
        )
    ).scalar_one()
    assert abertura_sh.status == "registrada" and abertura_sh.erro == "plataforma_sem_api"
    assert "• MK940003 — despachar até 12:00, postagem confirmada às 12:05" in abertura_sh.texto

    ligacoes = {
        (cp.pedido_bling, cp.motivo): cp
        for cp in (await db.execute(select(ChamadoPedido))).scalars()
    }
    assert set(ligacoes) == {("940001", "fila"), ("940002", "energia"), ("940003", "fila")}
    assert ligacoes[("940002", "energia")].chamado_id == ml.id
    assert ligacoes[("940003", "fila")].chamado_id == sh.id

    # a aba Pedidos enxerga o chamado em cada linha
    por_pedido = await svc.chamados_por_pedido(db, ["940001", "940002", "940003", "940004"])
    assert set(por_pedido) == {"940001", "940002", "940003"}
    assert por_pedido["940001"]["status"] == "pendente" and por_pedido["940001"]["canal"] == "robo"
    assert (
        por_pedido["940003"]["status"] == "registrada" and por_pedido["940003"]["motivo"] == "fila"
    )

    # de novo: os três já têm chamado aberto → ficam de fora, nada é aberto
    prev2 = (
        await client.post("/api/estoque/pedidos/chamados-atraso/preview", json={"pedidos": TODOS})
    ).json()
    assert prev2["grupos"] == []
    assert {e["pedido_bling"] for e in prev2["excluidos"] if e["motivo"] == "ja_tem_chamado"} == {
        "940001",
        "940002",
        "940003",
    }
    r = await client.post("/api/estoque/pedidos/chamados-atraso", json={"grupos": grupos})
    assert r.status_code == 200, r.text
    assert not [a for a in r.json()["abertos"] if a.get("chamado_id")]
    assert len((await db.execute(select(Chamado))).scalars().all()) == 2


async def test_precisa_de_permissao_de_chamados(client, db, auth_as):
    # admin passa por tudo: o teste precisa de um usuário comum só com view
    user = await _user(db, PERM_VIEW, role=UserRole.USER)
    auth_as(user)
    r = await client.post("/api/estoque/pedidos/chamados-atraso/preview", json={"pedidos": ["1"]})
    assert r.status_code == 403
    r = await client.post(
        "/api/estoque/pedidos/chamados-atraso",
        json={"grupos": [{"chave": "55", "pedidos": [{"pedido_bling": "1"}]}]},
    )
    assert r.status_code == 403


async def test_render_texto_por_motivo():
    c = _corte(13)
    fila = svc.PedidoAtraso(
        "1", "MK1", c.isoformat(), (c + timedelta(minutes=9)).isoformat(), 9, "fila"
    )
    energia = svc.PedidoAtraso(
        "2", "MK2", c.isoformat(), (c + timedelta(hours=4)).isoformat(), 240, "energia"
    )
    assert svc.motivo_por_atraso(60) == "fila" and svc.motivo_por_atraso(61) == "energia"
    t = svc.render_texto([fila])
    assert t.startswith("Olá, equipe. Entramos em contato sobre 1 pedido(s)")
    assert (
        "fila na agência" in t and "• MK1 — despachar até 13:00, postagem confirmada às 13:09" in t
    )
    t = svc.render_texto([energia])
    assert "queda de energia elétrica" in t and "postagem confirmada às 17:00" in t
    t = svc.render_texto([fila, energia])
    assert "Fila na postagem:\n• MK1" in t and "Queda de energia:\n• MK2" in t
