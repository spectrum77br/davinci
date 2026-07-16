"""Executor da Mensagem Bling — anexa a `mensagem_bling` da regra da aba Status
nas Observações do pedido de venda no Bling.

Cobre o sanitizador puro (`build_observacoes_put_body`), a composição da linha
datada (`compose_observacoes`), o casador da mensagem (`mensagem_bling_para`) e
os endpoints preview (dry-run, sem escrita) + aplicar (PUT mockado).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Logistica, LogisticaStatus, User, UserRole, UserStatus
from app.services import logistica_bling, logistica_rules


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# ---- compose_observacoes (puro) ----


def test_compose_prepend_linha_datada():
    out = logistica_bling.compose_observacoes(
        "linha antiga", "cliente recebeu", hoje=date(2026, 7, 16)
    )
    assert out == "16/07 - cliente recebeu\nlinha antiga"


def test_compose_sem_observacao_previa():
    out = logistica_bling.compose_observacoes(None, "oi", hoje=date(2026, 7, 16))
    assert out == "16/07 - oi"


def test_compose_idempotente_mesma_linha_no_topo():
    ja = "16/07 - oi\nlinha antiga"
    out = logistica_bling.compose_observacoes(ja, "oi", hoje=date(2026, 7, 16))
    assert out == ja  # não duplica


# ---- build_observacoes_put_body (puro) ----


def _order() -> dict:
    return {
        "id": 123,
        "numero": 282380,
        "total": 2372,
        "totalProdutos": 2372,
        "taxas": {"taxaComissao": 308.36},
        "tributacao": {"totalICMS": 0},
        "observacoes": "antiga",
        "contato": {"id": 999, "nome": "Fulano", "numeroDocumento": "1"},
        "loja": {"id": 205, "unidadeNegocio": {"id": 2}},
        "situacao": {"id": 83962, "valor": 0},
        "categoria": {"id": 0},
        "vendedor": {"id": 0},
        "notaFiscal": {"id": 0},
        "itens": [
            {
                "id": 1,
                "codigo": "sku1",
                "quantidade": 1,
                "valor": 2372,
                "produto": {"id": 16},
                "comissao": {"base": 0, "aliquota": 0, "valor": 0},
                "naturezaOperacao": {"id": 0},
            }
        ],
        "parcelas": [{"id": 9, "valor": 2372, "formaPagamento": {"id": 3}}],
        "transporte": {"frete": 0, "contato": {"id": 0, "nome": ""}, "volumes": [{"id": 5}]},
    }


def test_body_troca_observacoes_e_remove_calculados():
    body = logistica_bling.build_observacoes_put_body(_order(), "NOVA")
    assert body["observacoes"] == "NOVA"
    for k in ("total", "totalProdutos", "taxas", "tributacao"):
        assert k not in body


def test_body_remove_objetos_id_zero_e_reduz_referencias():
    body = logistica_bling.build_observacoes_put_body(_order(), "NOVA")
    # id=0 removidos
    for k in ("categoria", "vendedor", "notaFiscal"):
        assert k not in body
    # contato/loja/situacao viram referência por id
    assert body["contato"] == {"id": 999}
    assert body["loja"] == {"id": 205}
    assert body["situacao"] == {"id": 83962}
    # item: produto vira referência; comissao zerada + naturezaOperacao id=0 saem
    it = body["itens"][0]
    assert it["produto"] == {"id": 16}
    assert "comissao" not in it
    assert "naturezaOperacao" not in it
    # transporte.contato id=0 sai; volumes preservados
    assert "contato" not in body["transporte"]
    assert body["transporte"]["volumes"] == [{"id": 5}]


def test_body_preserva_itens_e_parcelas():
    body = logistica_bling.build_observacoes_put_body(_order(), "NOVA")
    assert body["itens"][0]["codigo"] == "sku1"
    assert body["itens"][0]["quantidade"] == 1
    assert body["parcelas"] == [{"id": 9, "valor": 2372, "formaPagamento": {"id": 3}}]


def test_body_nao_muta_original():
    o = _order()
    logistica_bling.build_observacoes_put_body(o, "NOVA")
    assert o["observacoes"] == "antiga"
    assert "total" in o


# ---- mensagem_bling_para (puro) ----


def test_mensagem_casa_regra():
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    rule = LogisticaStatus(status_plataforma=chave, mensagem_bling="colar no Bling")
    row = Logistica(plataforma="Mercado Livre", meli_status=meli)
    assert logistica_bling.mensagem_bling_para([rule], row) == "colar no Bling"


def test_mensagem_none_sem_regra_ou_vazia():
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    row = Logistica(plataforma="Mercado Livre", meli_status=meli)
    assert logistica_bling.mensagem_bling_para([], row) is None
    vazia = LogisticaStatus(status_plataforma=chave, mensagem_bling="  ")
    assert logistica_bling.mensagem_bling_para([vazia], row) is None


# ---- endpoints preview + aplicar ----


class _FakeBling:
    def __init__(self, order: dict):
        self._order = order
        self.put_body: dict | None = None

    async def get_order(self, bling_id: int) -> dict:
        return self._order

    async def update_order(self, bling_id: int, body: dict) -> dict:
        self.put_body = body
        return body


@pytest.mark.asyncio
async def test_preview_e_aplicar(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)

    # Regra da aba Status com Mensagem Bling.
    rs = await client.post(
        "/api/logistica/status",
        json={"status_plataforma": chave, "mensagem_bling": "produto voltou ao trânsito"},
    )
    assert rs.status_code == 201, rs.text

    # Pedido no Bling (numero casa com pedido_bling da linha da Logística).
    db.add(
        BlingOrder(bling_id=555, numero="99001", item_codigo="sku1", item_index=0, situacao="6")
    )
    await db.commit()

    # Linha da Logística ML.
    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Mercado Livre", "pedido_bling": "99001", "meli_status": meli},
    )
    assert rc.status_code == 201, rc.text
    lid = rc.json()["id"]

    fake = _FakeBling({"id": 555, "numero": 99001, "observacoes": "linha antiga", "total": 10})

    async def _fake_client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_client)

    # Preview: NÃO escreve.
    rp = await client.post(f"/api/logistica/{lid}/mensagem-bling/preview")
    assert rp.status_code == 200, rp.text
    body = rp.json()
    assert body["bling_order_id"] == 555
    assert body["mensagem"] == "produto voltou ao trânsito"
    assert body["observacoes_novo"].endswith("linha antiga")
    assert "produto voltou ao trânsito" in body["observacoes_novo"]
    assert body["put_body"]["observacoes"] == body["observacoes_novo"]
    assert "total" not in body["put_body"]  # sanitizado
    assert fake.put_body is None  # preview não chamou update_order

    # Aplicar: escreve via PUT mockado.
    ra = await client.post(f"/api/logistica/{lid}/mensagem-bling")
    assert ra.status_code == 200, ra.text
    assert fake.put_body is not None
    assert fake.put_body["observacoes"] == ra.json()["observacoes_novo"]
    assert "produto voltou ao trânsito" in fake.put_body["observacoes"]


@pytest.mark.asyncio
async def test_preview_sem_regra_422(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "shipped"}
    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Mercado Livre", "pedido_bling": "77001", "meli_status": meli},
    )
    assert rc.status_code == 201, rc.text
    lid = rc.json()["id"]
    r = await client.post(f"/api/logistica/{lid}/mensagem-bling/preview")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "logistica_sem_mensagem_bling"


@pytest.mark.asyncio
async def test_aplicar_404(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(f"/api/logistica/{uuid.uuid4()}/mensagem-bling")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "logistica_not_found"
