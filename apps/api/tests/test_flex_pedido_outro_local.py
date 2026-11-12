"""Pedido Flex de conta com a saída do Flex em OUTRA cidade (Vinicius,
12/11/2026: as 4 contas do ML com Flex saem de Piracicaba) não é de São
Bernardo: não tira peça do saldo Flex do .sp e não espera o acerto do
estoque. O robô de prioridade (não vai para o .sp): test_prioridade_flex.py.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    BlingOrder,
    FlexConta,
    FlexPedido,
    Integration,
    IntegrationPlatform,
    Product,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services import flex_local, flex_motor


@pytest_asyncio.fixture
async def mundo(db: AsyncSession, make_user, monkeypatch):
    """sbc (ML, saída em São Bernardo), pira (ML, saída em Piracicaba), shopee
    (loja liberada), semcidade (ML sem a cidade lida) — liberadas — e fora (ML
    com saída em São Bernardo, fora de `flex_contas`)."""
    admin = await make_user(role=UserRole.ADMIN)
    agora = datetime.now(UTC)
    ids: dict[str, object] = {}
    for chave, plataforma, cidade in (
        ("sbc", IntegrationPlatform.ML, "São Bernardo do Campo - SP"),
        ("pira", IntegrationPlatform.ML, "Piracicaba"),
        ("shopee", IntegrationPlatform.SHOPEE, None),
        ("semcidade", IntegrationPlatform.ML, None),
        ("fora", IntegrationPlatform.ML, "São Bernardo do Campo"),
    ):
        integ = Integration(
            user_id=admin.id,
            platform=plataforma,
            name=chave,
            credentials=encrypt_json({"access_token": "t"}),
        )
        db.add(integ)
        await db.flush()
        ids[chave] = integ.id
        db.add(
            FlexConta(
                integration_id=integ.id,
                plataforma=plataforma.value,
                flex_ativo=True,
                status="in",
                lido_em=agora,
                origem_cidade=cidade,
                origem_lida_em=agora if cidade else None,
            )
        )
    monkeypatch.setattr(
        get_settings(),
        "flex_contas",
        ",".join(str(ids[c]) for c in ("sbc", "pira", "shopee", "semcidade")),
    )
    db.add(
        Product(
            user_id=admin.id,
            sku="dg053.sp",
            name="Mala",
            stock=5,
            situacao="A",
            bling_product_id=123,
        )
    )
    await db.commit()
    return {"admin": admin, **ids}


def _pedido(db: AsyncSession, bling_id: int, conta, *, situacao: str = "6") -> None:
    db.add(
        BlingOrder(
            numero=str(bling_id),
            bling_id=bling_id,
            loja="1",
            item_index=0,
            item_codigo="dg053.ci",
            item_quantidade=1,
            situacao=situacao,
            data=datetime.now(UTC),
        )
    )
    db.add(FlexPedido(bling_id=bling_id, plataforma="ml", integration_id=conta, no_sp=False))


@pytest.mark.asyncio
async def test_contas_do_local_e_fora_do_local(db: AsyncSession, mundo):
    """Do local (o robô leva ao .sp): só o que se SABE que sai de lá. Fora do
    local (não desconta): só o que se SABE que sai de outra cidade. A conta
    sem cidade lida não está em nenhum dos dois."""
    assert await flex_local.contas_do_local(db) == {mundo["sbc"], mundo["shopee"]}
    assert await flex_local.contas_fora_do_local(db) == {mundo["pira"]}
    # Trocar o local para Piracicaba inverte o ML.
    pira = flex_local.LocalFlex("Piracicaba", "pi")
    assert await flex_local.contas_do_local(db, pira) == {mundo["pira"], mundo["shopee"]}
    assert await flex_local.contas_fora_do_local(db, pira) == {mundo["sbc"], mundo["fora"]}


@pytest.mark.asyncio
async def test_saldo_nao_desconta_o_pedido_de_piracicaba(db: AsyncSession, mundo):
    """dg053.sp com 5: o pedido Flex de São Bernardo, o de conta sem cidade
    lida e o sem conta conhecida descontam (lado seguro); o de Piracicaba não."""
    _pedido(db, 811001, mundo["sbc"])
    _pedido(db, 811002, mundo["pira"])
    _pedido(db, 811003, None)
    _pedido(db, 811004, mundo["semcidade"])
    await db.commit()
    s = (await flex_motor.calcular_saldos(db, ["dg053.sp"]))["dg053.sp"]
    assert (s.estoque, s.pendentes, s.saldo) == (5, 3, 2)


def test_acerto_pendente_de_outra_cidade():
    args = ("9", False, None, ["dg053.ci"], "sp")
    assert flex_motor.acerto_pendente(*args) is True
    assert flex_motor.acerto_pendente(*args, do_local=False) is False


@pytest.mark.asyncio
async def test_aba_pedidos_flex_sem_acerto_para_piracicaba(
    client: AsyncClient, db: AsyncSession, mundo, auth_as: Callable
):
    """Os dois saíram (atendido) sem passar pelo .sp: só o de São Bernardo
    espera o acerto; o de Piracicaba não — e não aceita "acertado"."""
    _pedido(db, 812001, mundo["sbc"], situacao="9")
    _pedido(db, 812002, mundo["pira"], situacao="9")
    await db.commit()
    auth_as(mundo["admin"])
    r = await client.get("/api/flex/pedidos")
    assert r.status_code == 200, r.text
    pendente = {p["bling_id"]: p["acerto_pendente"] for p in r.json()}
    assert pendente == {812001: True, 812002: False}
    assert (await client.post("/api/flex/pedidos/812002/acertado")).status_code == 409
    assert (await client.post("/api/flex/pedidos/812001/acertado")).status_code == 200
