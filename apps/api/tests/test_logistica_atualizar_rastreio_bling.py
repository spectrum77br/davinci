"""Botão ⟳ da Localização numa linha Amazon "Envio próprio" ainda SEM rastreio
dos Correios: a rota `POST /api/logistica/{id}/atualizar-rastreio` lê o pedido
no Bling na hora (é de lá que vem o `…BR`) e, se o código já existe, segue
pro 17track como sempre. Bling e 17track falsos.

Caso real (21/09/2026): 298196 e 298281 etiquetados juntos às 08:49; um ganhou
rastreio às 08:57 e o outro só às 09:22, porque o motor relê cada linha no
Bling de hora em hora."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Logistica, User, UserRole, UserStatus
from app.services import logistica_bling, logistica_track_sync
from app.services.marketplaces.bling import BlingCloudflareError

NUM = "AD912266053BR"
BLING_ID = 26856107922


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


class FakeBling:
    """Bling mínimo: só o que `logistica_amazon_bling.enrich_row` consulta.
    `codigo` = o codigoRastreamento do volume (vazio = etiqueta sem código)."""

    def __init__(self, codigo: str | None = NUM, *, erro: Exception | None = None):
        self.codigo = codigo
        self.erro = erro
        self.chamadas: list[int] = []

    async def get_order(self, bid):
        self.chamadas.append(bid)
        if self.erro is not None:
            raise self.erro
        return {
            "transporte": {
                "volumes": [{"id": 9, "servico": "SEDEX", "codigoRastreamento": self.codigo or ""}]
            },
            "contato": {"id": 3, "nome": "Fulano"},
        }

    async def get_logistica_objeto(self, oid):
        return {"dataSaida": "2026-09-21", "prazoEntregaPrevisto": 3}

    async def get_contato(self, cid):
        return {"email": "abc123@marketplace.amazon.com.br", "nome": "Fulano"}


@pytest.fixture
def fake_17track(monkeypatch):
    """`atualizar_linha` falso: registra a linha que recebeu e devolve
    'consultando' (o 17track foi acionado; a leitura chega pelo push)."""
    st = {"linhas": []}

    async def _atualizar(session, row, **kw):
        st["linhas"].append(row.rastreio)
        return {"resultado": "consultando"}

    monkeypatch.setattr(logistica_track_sync, "atualizar_linha", _atualizar)
    return st


def _usar_bling(monkeypatch, fake: FakeBling) -> None:
    async def _client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _client)


async def _linha(db: AsyncSession, *, espelho: bool = True, **kw) -> Logistica:
    base = {
        "data": date.today(),
        "pedido_bling": "298196",
        "pedido_marketplace": "702-0000001-0000001",
        "plataforma": "Amazon",
        "conta": "kfa",
        "meli_status": {"order_status": "Shipped", "fulfillment_channel": "MFN"},
        "amazon_canal": "proprio",
        "localizacao": "São José da Lapa/MG",
        "status_bling": "Em andamento",
    }
    base.update(kw)
    row = Logistica(**base)
    db.add(row)
    if espelho:
        db.add(BlingOrder(numero=base["pedido_bling"], bling_id=BLING_ID))
    await db.commit()
    await db.refresh(row)
    return row


@pytest.mark.asyncio
async def test_proprio_sem_rastreio_e_bling_com_codigo_segue_pro_17track(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
    fake_17track,
):
    auth_as(admin)
    row = await _linha(db)
    fake = FakeBling(NUM)
    _usar_bling(monkeypatch, fake)

    r = await client.post(f"/api/logistica/{row.id}/atualizar-rastreio")
    assert r.status_code == 200, r.text
    body = r.json()
    # O Bling deu o código → a rota segue pro 17track como se a linha sempre o tivesse.
    assert body["resultado"] == "consultando"
    assert body["linha"]["rastreio"] == NUM
    assert body["linha"]["servico_envio"] == "SEDEX"
    assert body["linha"]["bling_enriquecido_em"] is not None
    assert fake.chamadas == [BLING_ID]
    assert fake_17track["linhas"] == [NUM]
    await db.refresh(row)
    assert row.rastreio == NUM  # persistido, não só na resposta


@pytest.mark.asyncio
async def test_bling_sem_codigo_devolve_sem_rastreio_no_bling_e_grava_o_servico(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
    fake_17track,
):
    auth_as(admin)
    row = await _linha(db)
    _usar_bling(monkeypatch, FakeBling(codigo=None))

    r = await client.post(f"/api/logistica/{row.id}/atualizar-rastreio")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resultado"] == "sem_rastreio_no_bling"
    assert body["detalhe"] is None
    assert body["linha"]["rastreio"] is None
    # O que o Bling já tinha (serviço, contato) fica gravado mesmo sem o código.
    assert body["linha"]["servico_envio"] == "SEDEX"
    assert body["linha"]["cliente_email"] == "abc123@marketplace.amazon.com.br"
    assert body["linha"]["bling_enriquecido_em"] is not None
    assert fake_17track["linhas"] == []  # 17track não é acionado sem …BR
    await db.refresh(row)
    assert row.servico_envio == "SEDEX" and row.bling_enriquecido_em is not None


@pytest.mark.asyncio
async def test_sem_espelho_do_bling_devolve_sem_rastreio_no_bling(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
    fake_17track,
):
    auth_as(admin)
    row = await _linha(db, espelho=False)
    fake = FakeBling(NUM)
    _usar_bling(monkeypatch, fake)

    r = await client.post(f"/api/logistica/{row.id}/atualizar-rastreio")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resultado"] == "sem_rastreio_no_bling"
    assert body["detalhe"] == "pedido sem espelho do Bling"
    assert body["linha"]["rastreio"] is None
    assert fake.chamadas == []  # sem id interno não há o que perguntar ao Bling
    assert fake_17track["linhas"] == []


@pytest.mark.asyncio
async def test_linha_ml_sem_rastreio_continua_422(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
    fake_17track,
):
    auth_as(admin)
    row = await _linha(
        db, plataforma="Mercado Livre", amazon_canal=None, meli_status={}, localizacao=None
    )
    fake = FakeBling(NUM)
    _usar_bling(monkeypatch, fake)

    r = await client.post(f"/api/logistica/{row.id}/atualizar-rastreio")
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "logistica_sem_rastreio_correios"
    assert fake.chamadas == [] and fake_17track["linhas"] == []


@pytest.mark.asyncio
async def test_bling_fora_do_ar_devolve_502(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
    fake_17track,
):
    auth_as(admin)
    row = await _linha(db)
    _usar_bling(monkeypatch, FakeBling(NUM, erro=BlingCloudflareError("challenge")))

    r = await client.post(f"/api/logistica/{row.id}/atualizar-rastreio")
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["code"] == "logistica_bling_erro"
    assert fake_17track["linhas"] == []
    await db.refresh(row)
    assert row.rastreio is None and row.bling_enriquecido_em is None


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://api.bling.com.br/Api/v3/pedidos/vendas/1")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(status, request=req))


@pytest.mark.asyncio
async def test_pedido_apagado_no_bling_nao_e_fora_do_ar(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
    fake_17track,
):
    """404 do Bling = o pedido não existe mais lá (o espelho ainda tem o id): o
    Bling respondeu, então é `sem_rastreio_no_bling` com detalhe, não 502 —
    senão o operador ficaria tentando de novo pra sempre."""
    auth_as(admin)
    row = await _linha(db)
    _usar_bling(monkeypatch, FakeBling(NUM, erro=_http_error(404)))

    r = await client.post(f"/api/logistica/{row.id}/atualizar-rastreio")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resultado"] == "sem_rastreio_no_bling"
    assert body["detalhe"] == "pedido não existe mais no Bling"
    assert fake_17track["linhas"] == []


@pytest.mark.asyncio
async def test_429_do_bling_e_502(
    client: AsyncClient,
    admin: User,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    monkeypatch,
    fake_17track,
):
    auth_as(admin)
    row = await _linha(db)
    _usar_bling(monkeypatch, FakeBling(NUM, erro=_http_error(429)))

    r = await client.post(f"/api/logistica/{row.id}/atualizar-rastreio")
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["code"] == "logistica_bling_erro"
    assert fake_17track["linhas"] == []
