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

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Logistica,
    LogisticaStatus,
    SituacaoBling,
    User,
    UserRole,
    UserStatus,
)
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


def test_mensagem_so_de_regra_aplicavel_ao_estado():
    # Regra de OUTRO estado não empresta mensagem (bug 19/08: cruzamento de
    # combinações). Só a exata do estado atual (ou curinga) vale.
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    outra = LogisticaStatus(
        status_plataforma=chave, status_atual="Em andamento", mensagem_bling="de outro estado"
    )
    row = Logistica(plataforma="Mercado Livre", meli_status=meli, status_bling="Problemas")
    assert logistica_bling.mensagem_bling_para([outra], row) is None
    exata = LogisticaStatus(
        status_plataforma=chave, status_atual="Problemas", mensagem_bling="desta"
    )
    assert logistica_bling.mensagem_bling_para([outra, exata], row) == "desta"


# ---- endpoints preview + aplicar ----


class _FakeBling:
    def __init__(self, order: dict):
        self._order = order
        self.put_body: dict | None = None
        self.situacao_set: int | None = None

    async def get_order(self, bling_id: int) -> dict:
        return self._order

    async def update_order(self, bling_id: int, body: dict) -> dict:
        self.put_body = body
        return body

    async def update_order_situacao(self, bling_id: int, situacao_id: int) -> None:
        self.situacao_set = situacao_id


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


# ---- alterar_status_bling_para (puro) ----


def test_status_casa_regra():
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    rule = LogisticaStatus(status_plataforma=chave, alterar_status_bling="Entregue")
    row = Logistica(plataforma="Mercado Livre", meli_status=meli)
    assert logistica_bling.alterar_status_bling_para([rule], row) == "Entregue"


def test_status_none_sem_regra_ou_vazio():
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    row = Logistica(plataforma="Mercado Livre", meli_status=meli)
    assert logistica_bling.alterar_status_bling_para([], row) is None
    vazia = LogisticaStatus(status_plataforma=chave, alterar_status_bling="  ")
    assert logistica_bling.alterar_status_bling_para([vazia], row) is None


# ---- endpoints alterar-status-bling ----


@pytest.mark.asyncio
async def test_status_preview_e_aplicar(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)

    # Catálogo de situações do Bling (nome -> id).
    db.add_all(
        [SituacaoBling(id=6, nome="Em aberto"), SituacaoBling(id=83953, nome="Entregue")]
    )
    # Regra da aba Status: transição "Em aberto" -> "Entregue".
    rs = await client.post(
        "/api/logistica/status",
        json={
            "status_plataforma": chave,
            "status_atual": "Em aberto",
            "alterar_status_bling": "Entregue",
        },
    )
    assert rs.status_code == 201, rs.text
    db.add(
        BlingOrder(bling_id=555, numero="99001", item_codigo="sku1", item_index=0, situacao="6")
    )
    await db.commit()

    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Mercado Livre", "pedido_bling": "99001", "meli_status": meli},
    )
    assert rc.status_code == 201, rc.text
    lid = rc.json()["id"]

    # Pedido no Bling está na situação 6 (Em aberto); alvo é 83953 (Entregue).
    fake = _FakeBling({"id": 555, "numero": 99001, "situacao": {"id": 6, "valor": 0}})

    async def _fake_client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_client)

    # Preview: NÃO muda.
    rp = await client.post(f"/api/logistica/{lid}/alterar-status-bling/preview")
    assert rp.status_code == 200, rp.text
    body = rp.json()
    assert body["bling_order_id"] == 555
    assert body["situacao_de"] == "Em aberto"
    assert body["situacao_de_id"] == 6
    assert body["situacao_alvo"] == "Entregue"
    assert body["situacao_alvo_id"] == 83953
    assert body["situacao_atual_id"] == 6
    assert body["situacao_atual_nome"] == "Em aberto"
    assert body["ja_no_alvo"] is False
    # Pedido está no "de" da regra (Em aberto) → a mudança se aplica.
    assert body["aplicavel"] is True
    assert fake.situacao_set is None  # preview não mexeu

    # Aplicar: PATCH da situação + sincroniza status_bling local.
    ra = await client.post(f"/api/logistica/{lid}/alterar-status-bling")
    assert ra.status_code == 200, ra.text
    assert ra.json()["situacao_alvo_id"] == 83953
    assert fake.situacao_set == 83953
    # A linha da Logística passou a refletir a situação alvo.
    rl = await client.get(f"/api/logistica?plataforma=ml")
    linha = next(x for x in rl.json() if x["id"] == lid)
    assert linha["status_bling"] == "Entregue"


@pytest.mark.asyncio
async def test_status_ja_no_alvo(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    db.add(SituacaoBling(id=83953, nome="Entregue"))
    rs = await client.post(
        "/api/logistica/status",
        json={"status_plataforma": chave, "alterar_status_bling": "Entregue"},
    )
    assert rs.status_code == 201, rs.text
    db.add(
        BlingOrder(bling_id=556, numero="99002", item_codigo="sku1", item_index=0, situacao="83953")
    )
    await db.commit()
    # Painel com status_bling STALE ("Em andamento") — o Bling vivo diz Entregue.
    rc = await client.post(
        "/api/logistica",
        json={
            "plataforma": "Mercado Livre",
            "pedido_bling": "99002",
            "meli_status": meli,
            "status_bling": "Em andamento",
        },
    )
    lid = rc.json()["id"]
    # Pedido já está em 83953 (Entregue).
    fake = _FakeBling({"id": 556, "numero": 99002, "situacao": {"id": 83953, "valor": 0}})

    async def _fake_client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_client)
    rp = await client.post(f"/api/logistica/{lid}/alterar-status-bling/preview")
    assert rp.status_code == 200, rp.text
    assert rp.json()["ja_no_alvo"] is True
    assert rp.json()["situacao_atual_nome"] == "Entregue"
    # O preview sincronizou o status_bling STALE do painel com a situação viva.
    rl = await client.get("/api/logistica?plataforma=ml")
    linha = next(x for x in rl.json() if x["id"] == lid)
    assert linha["status_bling"] == "Entregue"


@pytest.mark.asyncio
async def test_status_atual_divergente(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    """Regra é 'Em andamento -> Entregue', mas o pedido está em 'Em aberto' (fora
    do 'de'): preview marca aplicavel=False e aplicar levanta divergente — nunca
    regride nem pula etapa."""
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    db.add_all(
        [
            SituacaoBling(id=6, nome="Em aberto"),
            SituacaoBling(id=15, nome="Em andamento"),
            SituacaoBling(id=83953, nome="Entregue"),
        ]
    )
    rs = await client.post(
        "/api/logistica/status",
        json={
            "status_plataforma": chave,
            "status_atual": "Em andamento",
            "alterar_status_bling": "Entregue",
        },
    )
    assert rs.status_code == 201, rs.text
    db.add(
        BlingOrder(bling_id=558, numero="99004", item_codigo="sku1", item_index=0, situacao="6")
    )
    await db.commit()
    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Mercado Livre", "pedido_bling": "99004", "meli_status": meli},
    )
    lid = rc.json()["id"]
    # Pedido está em 6 (Em aberto), mas a regra exige "de" = 15 (Em andamento).
    fake = _FakeBling({"id": 558, "numero": 99004, "situacao": {"id": 6, "valor": 0}})

    async def _fake_client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_client)

    # Preview: transição da regra visível, mas NÃO aplicável (pedido fora do "de").
    rp = await client.post(f"/api/logistica/{lid}/alterar-status-bling/preview")
    assert rp.status_code == 200, rp.text
    body = rp.json()
    assert body["situacao_de"] == "Em andamento"
    assert body["situacao_de_id"] == 15
    assert body["situacao_alvo"] == "Entregue"
    assert body["situacao_atual_id"] == 6
    assert body["ja_no_alvo"] is False
    assert body["aplicavel"] is False

    # Aplicar: recusa e não escreve no Bling.
    ra = await client.post(f"/api/logistica/{lid}/alterar-status-bling")
    assert ra.status_code == 422
    assert ra.json()["detail"]["code"] == "logistica_status_atual_divergente"
    assert fake.situacao_set is None


@pytest.mark.asyncio
async def test_status_maquina_de_estados(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    """Duas regras pra MESMA chave (máquina de estados): a transição escolhida
    depende da situação atual do pedido no Bling. Pedido em 'Em andamento' segue
    a regra 'Em andamento -> Entregue'; já em 'Entregue' não faz nada."""
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    db.add_all(
        [
            SituacaoBling(id=83965, nome="Enviado Etiqueta"),
            SituacaoBling(id=15, nome="Em andamento"),
            SituacaoBling(id=83953, nome="Entregue"),
        ]
    )
    # Regra A: Enviado Etiqueta -> Em andamento. Regra B: Em andamento -> Entregue.
    for de, alvo in [("Enviado Etiqueta", "Em andamento"), ("Em andamento", "Entregue")]:
        rs = await client.post(
            "/api/logistica/status",
            json={"status_plataforma": chave, "status_atual": de, "alterar_status_bling": alvo},
        )
        assert rs.status_code == 201, rs.text
    db.add(
        BlingOrder(bling_id=560, numero="99005", item_codigo="sku1", item_index=0, situacao="15")
    )
    await db.commit()
    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Mercado Livre", "pedido_bling": "99005", "meli_status": meli},
    )
    lid = rc.json()["id"]

    # Pedido em 15 (Em andamento): escolhe a regra B (Em andamento -> Entregue).
    fake = _FakeBling({"id": 560, "numero": 99005, "situacao": {"id": 15, "valor": 0}})

    async def _fake_client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_client)
    rp = await client.post(f"/api/logistica/{lid}/alterar-status-bling/preview")
    assert rp.status_code == 200, rp.text
    body = rp.json()
    assert body["situacao_de"] == "Em andamento"
    assert body["situacao_alvo"] == "Entregue"
    assert body["aplicavel"] is True
    assert body["ja_no_alvo"] is False

    ra = await client.post(f"/api/logistica/{lid}/alterar-status-bling")
    assert ra.status_code == 200, ra.text
    assert fake.situacao_set == 83953

    # Agora o pedido está em 83953 (Entregue): nenhuma regra parte daí, mas é o
    # alvo da regra B -> ja_no_alvo, nada a fazer.
    fake.situacao_set = None
    fake._order["situacao"]["id"] = 83953
    rp2 = await client.post(f"/api/logistica/{lid}/alterar-status-bling/preview")
    assert rp2.status_code == 200, rp2.text
    assert rp2.json()["ja_no_alvo"] is True
    assert rp2.json()["aplicavel"] is False


@pytest.mark.asyncio
async def test_status_preview_sem_regra_422(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "shipped"}
    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Mercado Livre", "pedido_bling": "77002", "meli_status": meli},
    )
    lid = rc.json()["id"]
    r = await client.post(f"/api/logistica/{lid}/alterar-status-bling/preview")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "logistica_sem_status_bling"


@pytest.mark.asyncio
async def test_status_desconhecido_422(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    # Regra pede um status que NÃO existe no catálogo situacao_bling.
    rs = await client.post(
        "/api/logistica/status",
        json={"status_plataforma": chave, "alterar_status_bling": "Status Inexistente"},
    )
    assert rs.status_code == 201, rs.text
    db.add(
        BlingOrder(bling_id=557, numero="99003", item_codigo="sku1", item_index=0, situacao="6")
    )
    await db.commit()
    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Mercado Livre", "pedido_bling": "99003", "meli_status": meli},
    )
    lid = rc.json()["id"]
    r = await client.post(f"/api/logistica/{lid}/alterar-status-bling/preview")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "logistica_status_bling_desconhecido"


@pytest.mark.asyncio
async def test_status_aplicar_404(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(f"/api/logistica/{uuid.uuid4()}/alterar-status-bling")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "logistica_not_found"


class _MultiFakeBling:
    """Fake do Bling que resolve get_order/update por bling_id (pro lote)."""

    def __init__(self, orders: dict[int, dict]):
        self._orders = orders
        self.situacao_sets: list[tuple[int, int]] = []

    async def get_order(self, bling_id: int) -> dict:
        return self._orders[bling_id]

    async def update_order_situacao(self, bling_id: int, situacao_id: int) -> None:
        self.situacao_sets.append((bling_id, situacao_id))
        self._orders[bling_id]["situacao"]["id"] = situacao_id


@pytest.mark.asyncio
async def test_aplicar_status_em_lote(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    """O lote aplica a mudança de situação das linhas ML que casam uma regra:
    conta aplicados (mudou), pulados (já no alvo / fora do fluxo) e ignora as sem
    regra. Sincroniza o status_bling local de quem casa."""
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    db.add_all(
        [
            SituacaoBling(id=15, nome="Em andamento"),
            SituacaoBling(id=83953, nome="Entregue"),
        ]
    )
    # Regra curinga (sem status_atual): de qualquer estado -> Entregue.
    rs = await client.post(
        "/api/logistica/status",
        json={"status_plataforma": chave, "alterar_status_bling": "Entregue"},
    )
    assert rs.status_code == 201, rs.text
    db.add_all(
        [
            BlingOrder(bling_id=561, numero="99010", item_codigo="s1", item_index=0, situacao="15"),
            BlingOrder(bling_id=562, numero="99011", item_codigo="s2", item_index=0, situacao="83953"),
        ]
    )
    await db.commit()
    # row1: casa, pedido em Em andamento -> aplica. row2: casa, já Entregue ->
    # pulado. row3: sem regra (outra assinatura) -> ignorado.
    ids = []
    for num, m in [
        ("99010", meli),
        ("99011", meli),
        ("99012", {"order_status": "paid", "ship_status": "shipped"}),
    ]:
        rc = await client.post(
            "/api/logistica",
            json={"plataforma": "Mercado Livre", "pedido_bling": num, "meli_status": m},
        )
        ids.append(rc.json()["id"])

    fake = _MultiFakeBling(
        {
            561: {"id": 561, "numero": 99010, "situacao": {"id": 15, "valor": 0}},
            562: {"id": 562, "numero": 99011, "situacao": {"id": 83953, "valor": 0}},
        }
    )

    async def _fake_client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_client)

    out = await logistica_bling.aplicar_status_em_lote(db)
    assert out == {"aplicados": 1, "pulados": 1, "falhas": 0}
    assert fake.situacao_sets == [(561, 83953)]  # só a row1 mudou

    # A row1 teve o status_bling local sincronizado pro alvo aplicado.
    row1 = (
        await db.execute(select(Logistica).where(Logistica.pedido_bling == "99010"))
    ).scalar_one()
    assert row1.status_bling == "Entregue"


@pytest.mark.asyncio
async def test_aplicar_status_em_lote_inclui_shopee(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    """O lote também processa linhas Shopee: a chave usa a assinatura própria da
    Shopee (assinatura_para) e a transição no Bling se aplica igual ao ML."""
    auth_as(admin)
    meli = {"order_status": "COMPLETED"}
    chave = logistica_rules.assinatura_para("Shopee", meli)  # "Concluído"
    assert chave == "Concluído"
    db.add_all(
        [
            SituacaoBling(id=15, nome="Em andamento"),
            SituacaoBling(id=83953, nome="Entregue"),
        ]
    )
    rs = await client.post(
        "/api/logistica/status",
        json={
            "status_plataforma": chave,
            "plataforma": "Shopee",
            "alterar_status_bling": "Entregue",
        },
    )
    assert rs.status_code == 201, rs.text
    db.add(
        BlingOrder(bling_id=571, numero="98010", item_codigo="s1", item_index=0, situacao="15")
    )
    await db.commit()
    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Shopee", "pedido_bling": "98010", "meli_status": meli},
    )
    assert rc.status_code == 201, rc.text

    fake = _MultiFakeBling(
        {571: {"id": 571, "numero": 98010, "situacao": {"id": 15, "valor": 0}}}
    )

    async def _fake_client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_client)

    out = await logistica_bling.aplicar_status_em_lote(db)
    assert out == {"aplicados": 1, "pulados": 0, "falhas": 0}
    assert fake.situacao_sets == [(571, 83953)]
    row = (
        await db.execute(select(Logistica).where(Logistica.pedido_bling == "98010"))
    ).scalar_one()
    assert row.status_bling == "Entregue"


class _FalhaFakeBling(_MultiFakeBling):
    """Como o _MultiFakeBling, mas o PATCH de situação de alguns pedidos
    devolve erro (o 400 real do Bling quando a transição é proibida)."""

    def __init__(self, orders: dict[int, dict], falham: set[int]):
        super().__init__(orders)
        self._falham = falham

    async def update_order_situacao(self, bling_id: int, situacao_id: int) -> None:
        if bling_id in self._falham:
            raise RuntimeError("400 Bad Request: transicao de situacao invalida")
        await super().update_order_situacao(bling_id, situacao_id)


@pytest.mark.asyncio
async def test_aplicar_status_em_lote_sobrevive_a_falha_de_uma_linha(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    """Regressão do incidente de 01-02/set: um 400 do Bling numa linha fazia o
    rollback expirar as instâncias ORM e o lote INTEIRO morria com
    MissingGreenlet — o cron de 5min nunca mais aplicava nada. Agora a falha é
    contada, logada e as demais linhas seguem sendo aplicadas."""
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    db.add_all(
        [
            SituacaoBling(id=15, nome="Em andamento"),
            SituacaoBling(id=83953, nome="Entregue"),
        ]
    )
    rs = await client.post(
        "/api/logistica/status",
        json={"status_plataforma": chave, "alterar_status_bling": "Entregue"},
    )
    assert rs.status_code == 201, rs.text
    db.add_all(
        [
            BlingOrder(bling_id=581, numero="99020", item_codigo="s1", item_index=0, situacao="15"),
            BlingOrder(bling_id=582, numero="99021", item_codigo="s2", item_index=0, situacao="15"),
        ]
    )
    await db.commit()
    for num in ("99020", "99021"):
        rc = await client.post(
            "/api/logistica",
            json={"plataforma": "Mercado Livre", "pedido_bling": num, "meli_status": meli},
        )
        assert rc.status_code == 201, rc.text

    # As duas linhas casam a regra e precisam mudar 15 -> 83953, mas o Bling
    # recusa a transição do pedido 581.
    fake = _FalhaFakeBling(
        {
            581: {"id": 581, "numero": 99020, "situacao": {"id": 15, "valor": 0}},
            582: {"id": 582, "numero": 99021, "situacao": {"id": 15, "valor": 0}},
        },
        falham={581},
    )

    async def _fake_client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_client)

    out = await logistica_bling.aplicar_status_em_lote(db)

    # Independente da ordem de processamento: a 581 falha, a 582 é aplicada.
    assert out == {"aplicados": 1, "pulados": 0, "falhas": 1}
    assert fake.situacao_sets == [(582, 83953)]
    row_ok = (
        await db.execute(select(Logistica).where(Logistica.pedido_bling == "99021"))
    ).scalar_one()
    assert row_ok.status_bling == "Entregue"


class _PuloProibidoFakeBling(_MultiFakeBling):
    """Bling real: de "Aguardando Devolução" (83957) NÃO dá pra ir direto a
    "Entregue" (83953) — só passando por "Atendido" (9). Este fake devolve o
    400 verdadeiro (httpx.HTTPStatusError) nesse pulo e aceita o resto."""

    async def update_order_situacao(self, bling_id: int, situacao_id: int) -> None:
        atual = int(self._orders[bling_id]["situacao"]["id"])
        if atual == 83957 and situacao_id == 83953:
            req = httpx.Request("PATCH", f"https://bling/pedidos/vendas/{bling_id}/situacoes/{situacao_id}")
            raise httpx.HTTPStatusError(
                "400 Bad Request", request=req, response=httpx.Response(400, request=req)
            )
        await super().update_order_situacao(bling_id, situacao_id)


@pytest.mark.asyncio
async def test_aplicar_status_passa_por_atendido_quando_bling_recusa_o_pulo(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    """Caso real 291981 (03/09): devolução Shopee cancelada → regra "Concluído
    | Aguardando Devolução → Entregue"; o Bling recusava o pulo direto (400) a
    cada 10 min. Agora o lote passa por "Atendido" e chega em "Entregue"."""
    auth_as(admin)
    meli = {
        "order_status": "COMPLETED",
        "return_status": "CANCELLED",
        "logistics_status": "LOGISTICS_DELIVERY_DONE",
    }
    chave = logistica_rules.assinatura_para("Shopee", meli)
    db.add_all(
        [
            SituacaoBling(id=9, nome="Atendido"),
            SituacaoBling(id=83957, nome="Aguardando Devolução"),
            SituacaoBling(id=83953, nome="Entregue"),
        ]
    )
    rs = await client.post(
        "/api/logistica/status",
        json={
            "plataforma": "Shopee",
            "status_plataforma": chave,
            "status_atual": "Aguardando Devolução",
            "alterar_status_bling": "Entregue",
        },
    )
    assert rs.status_code == 201, rs.text
    db.add(
        BlingOrder(bling_id=591, numero="99030", item_codigo="s1", item_index=0, situacao="83957")
    )
    await db.commit()
    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Shopee", "pedido_bling": "99030", "meli_status": meli},
    )
    assert rc.status_code == 201, rc.text

    fake = _PuloProibidoFakeBling(
        {591: {"id": 591, "numero": 99030, "situacao": {"id": 83957, "valor": 0}}}
    )

    async def _fake_client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_client)

    out = await logistica_bling.aplicar_status_em_lote(db)

    assert out == {"aplicados": 1, "pulados": 0, "falhas": 0}
    # Escala: Atendido primeiro, depois Entregue.
    assert fake.situacao_sets == [(591, 9), (591, 83953)]
    row = (
        await db.execute(select(Logistica).where(Logistica.pedido_bling == "99030"))
    ).scalar_one()
    assert row.status_bling == "Entregue"


@pytest.mark.asyncio
async def test_recarregar_enfileira_job(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None], monkeypatch
):
    """O endpoint só ENFILEIRA o job em background (não roda inline)."""
    auth_as(admin)
    calls: list[str] = []

    class _FakePool:
        async def enqueue_job(self, name: str, *a, **k) -> None:
            calls.append(name)

    async def _fake_pool():
        return _FakePool()

    import app.routers.logistica as lr

    monkeypatch.setattr(lr, "get_arq_pool", _fake_pool)
    r = await client.post("/api/logistica/recarregar")
    assert r.status_code == 200, r.text
    assert r.json()["enqueued"] is True
    assert calls == ["logistica_recarregar"]


@pytest.mark.asyncio
async def test_sync_status_bling_row_espelha_bling_vivo(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    """O helper lê a situação VIVA do Bling e corrige o status_bling STALE do
    painel — independe de haver regra com Alterar Status Bling."""
    auth_as(admin)
    db.add(SituacaoBling(id=15, nome="Em andamento"))
    db.add(
        BlingOrder(bling_id=700, numero="88800", item_codigo="s1", item_index=0, situacao="83960")
    )
    await db.commit()
    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Mercado Livre", "pedido_bling": "88800", "status_bling": "Em aberto"},
    )
    assert rc.status_code == 201, rc.text

    fake = _MultiFakeBling({700: {"id": 700, "numero": 88800, "situacao": {"id": 15}}})

    async def _fake_client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_client)

    row = (
        await db.execute(select(Logistica).where(Logistica.pedido_bling == "88800"))
    ).scalar_one()
    nome = await logistica_bling.sync_status_bling_row(db, row)
    await db.commit()
    assert nome == "Em andamento"
    assert row.status_bling == "Em andamento"


@pytest.mark.asyncio
async def test_sync_status_bling_row_best_effort_sem_pedido(
    db: AsyncSession, monkeypatch
):
    """Sem pedido_bling (ou sem integração) engole o BlingObsError e devolve
    None, sem tocar no status_bling."""
    row = Logistica(plataforma="Mercado Livre", status_bling="Em aberto")
    nome = await logistica_bling.sync_status_bling_row(db, row)
    assert nome is None
    assert row.status_bling == "Em aberto"
