"""Endpoint POST /api/nf-cadastro/faturamento/conferir-frete.

Expõe o confere-frete do Melhor Envio (item 3, impressão tipo "próprio"): recebe
CEPs + a caixa + o frete projetado, cota no Melhor Envio e devolve
libera/motivo + as cotações. A camada pura já é testada em test_melhor_envio.py;
aqui cobre só o wire (auth, token faltando, erro de API, echo das cotações).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole, UserStatus
from app.services.melhor_envio import (
    Cotacao,
    MelhorEnvioApiError,
    MelhorEnvioClient,
    MelhorEnvioConfigError,
)

_ROTA = "/api/nf-cadastro/faturamento/conferir-frete"

_COTACOES = [
    Cotacao(1, "PAC", "Correios", Decimal("24.50"), 6, None),
    Cotacao(2, "SEDEX", "Correios", Decimal("39.90"), 2, None),
    Cotacao(17, ".Package", "Jadlog", None, None, "indisponível"),
]

_BODY = {
    "from_cep": "13400853",
    "to_cep": "01310100",
    "produtos": [
        {"id": "1", "width": 20, "height": 10, "length": 20, "weight": 1}
    ],
    "frete_projetado": "30.00",
}


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_conferir_frete_libera_dentro_do_projetado(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin)

    async def _fake(self, **kwargs):
        return _COTACOES

    monkeypatch.setattr(MelhorEnvioClient, "calcular_frete", _fake)

    r = await client.post(_ROTA, json=_BODY)
    assert r.status_code == 200
    data = r.json()
    assert data["libera"] is True
    assert data["motivo"] == "dentro_do_projetado"
    assert data["menor_frete"] == "24.50"
    assert data["servico_escolhido"] == "PAC"
    assert data["diferenca"] == "5.50"
    # ecoa TODAS as cotações (inclusive a com erro)
    assert len(data["cotacoes"]) == 3
    assert data["cotacoes"][2]["erro"] is not None
    assert data["cotacoes"][2]["preco"] is None


@pytest.mark.asyncio
async def test_conferir_frete_bloqueia_acima(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin)

    async def _fake(self, **kwargs):
        return _COTACOES

    monkeypatch.setattr(MelhorEnvioClient, "calcular_frete", _fake)

    body = {**_BODY, "frete_projetado": "20.00"}
    r = await client.post(_ROTA, json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["libera"] is False
    assert data["motivo"] == "acima_do_projetado"
    assert data["diferenca"] == "-4.50"


@pytest.mark.asyncio
async def test_conferir_frete_sem_token(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin)

    async def _fake(self, **kwargs):
        raise MelhorEnvioConfigError("sem token")

    monkeypatch.setattr(MelhorEnvioClient, "calcular_frete", _fake)

    r = await client.post(_ROTA, json=_BODY)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "melhor_envio_token_missing"


@pytest.mark.asyncio
async def test_conferir_frete_api_erro(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin)

    async def _fake(self, **kwargs):
        raise MelhorEnvioApiError(500, "boom")

    monkeypatch.setattr(MelhorEnvioClient, "calcular_frete", _fake)

    r = await client.post(_ROTA, json=_BODY)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "melhor_envio_erro"


@pytest.mark.asyncio
async def test_conferir_frete_exige_auth(client: AsyncClient):
    r = await client.post(_ROTA, json=_BODY)
    assert r.status_code in (401, 403)
