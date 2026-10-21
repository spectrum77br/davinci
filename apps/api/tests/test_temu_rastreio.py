"""Rastreio Temu — código na etiqueta + sonda no 17track (21/10/2026).

A Temu marca "Enviado" na compra da etiqueta e o Bling vem com Tracking N/A;
o código da transportadora só está na etiqueta. Anjun vai ao 17track só com o
código; J&T exige o CPF/CNPJ do destinatário, que sai do pedido e nunca volta
na resposta. Sem Redis: arquivo separado do test_logistica_track.
"""

from __future__ import annotations

import uuid
from typing import Any

import fitz
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, NfEtiquetaArquivo, User, UserRole, UserStatus
from app.services import logistica_track, temu_rastreio

CPF = "12345678909"


def _pdf(texto: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((40, 60), texto, fontsize=9)
    out = doc.tobytes()
    doc.close()
    return out


# ---- código na etiqueta (sem banco) ----


def test_acha_codigo_anjun():
    cod = temu_rastreio.codigo_da_etiqueta(
        _pdf("AnjunExpress\nPK-1771075865150232333\nAJ261021123794101\nPO-029-04715530293113412")
    )
    assert cod == temu_rastreio.CodigoEtiqueta("AJ261021123794101", 191915, "Anjun")


def test_acha_codigo_jt():
    cod = temu_rastreio.codigo_da_etiqueta(_pdf("J&T Express\n888002704060957"))
    assert cod == temu_rastreio.CodigoEtiqueta("888002704060957", 100797, "J&T")


def test_nao_casa_pedaco_de_numero_maior():
    # 888… dentro da chave de acesso de 44 dígitos e AJ grudado em letra.
    texto = "Chave 3526101234567800019955001000088800270406095712\nXAJ261021123794101"
    assert temu_rastreio.codigo_da_etiqueta(_pdf(texto)) is None


def test_pdf_quebrado_vale_sem_codigo():
    assert temu_rastreio.codigo_da_etiqueta(b"%PDF-1.7 lixo") is None


# ---- sonda (rota) ----


@pytest_asyncio.fixture
async def operador(db: AsyncSession) -> User:
    email = f"op-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={"controle_estoque": {"view": True, "edit": True}},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _pedido(db: AsyncSession, numero: str, etiqueta: bytes, documento: str | None) -> None:
    db.add(
        BlingOrder(
            bling_id=int(numero) * 10,
            numero=numero,
            numeroloja=f"PO-029-{numero}",
            situacao="21",
            item_index=0,
            documento_destinatario=documento,
        )
    )
    db.add(
        NfEtiquetaArquivo(
            pedido_bling=numero, filename="e.pdf", content_type="application/pdf", blob=etiqueta
        )
    )
    await db.commit()


@pytest.fixture
def chamadas(monkeypatch) -> list[tuple[str, Any, str]]:
    feitas: list[tuple[str, Any, str]] = []

    async def _chamar(_c, endpoint, payload, *, base=logistica_track._BASE):
        feitas.append((endpoint, payload, base))
        if endpoint == "register":
            return {
                "code": 0,
                "data": {
                    "accepted": [{"number": "AJ261021123794101"}],
                    "rejected": [{"number": "888002704060957", "error": {"code": -18019901}}],
                },
            }
        if endpoint == "gettrackinfo":
            return {
                "code": 0,
                "data": {
                    "accepted": [
                        {
                            "number": "AJ261021123794101",
                            "track_info": {
                                "latest_status": {
                                    "status": "InTransit",
                                    "sub_status": "InTransit_PickedUp",
                                },
                                "providers": [
                                    {
                                        "events": [
                                            {
                                                "time_iso": "2026-10-21T12:33:52-03:00",
                                                "description": "Objeto coletado",
                                                "location": "Piracicaba / SP",
                                            }
                                        ]
                                    }
                                ],
                            },
                        }
                    ]
                },
            }
        return {"code": 0, "data": {"quota_remain": 95}}

    monkeypatch.setattr(logistica_track, "_chamar", _chamar)
    return feitas


async def test_sonda_registra_anjun_sem_cpf_e_jt_com_cpf(
    db: AsyncSession, client: AsyncClient, auth_as, operador, chamadas
):
    await _pedido(db, "304753", _pdf("AnjunExpress AJ261021123794101"), CPF)
    await _pedido(db, "303225", _pdf("J&T Express 888002704060957"), "123.456.789-09")
    await _pedido(db, "304816", _pdf("sem codigo nenhum"), None)
    auth_as(operador)

    r = await client.post(
        "/api/logistica/17track/sonda-temu",
        json={"pedidos": ["304753", "303225", "304816", "999999"]},
    )

    assert r.status_code == 200
    assert CPF not in r.text  # o CPF vai ao 17track e nunca volta na resposta
    reg = next(p for e, p, _ in chamadas if e == "register")
    assert reg == [
        {"number": "AJ261021123794101", "carrier": 191915},
        {"number": "888002704060957", "carrier": 100797, "cpf_or_cnpj": CPF},
    ]
    assert {b for e, _, b in chamadas if e in ("register", "gettrackinfo")} == {
        temu_rastreio.BASE_V24
    }
    info = next(p for e, p, _ in chamadas if e == "gettrackinfo")
    assert all("cpf_or_cnpj" not in li for li in info)

    corpo = r.json()
    assert corpo["quota_restante"] == 95
    por = {p["pedido"]: p for p in corpo["pedidos"]}
    assert por["304753"]["registro"] == "aceito"
    assert por["304753"]["cpf_enviado"] is False
    assert por["304753"]["status"] == "InTransit"
    assert por["304753"]["eventos"][0]["descricao"] == "Objeto coletado"
    assert por["303225"]["registro"] == "ja_registrado"
    assert por["303225"]["cpf_enviado"] is True
    assert por["304816"]["erro"] == "codigo_nao_achado_na_etiqueta"
    assert por["999999"]["erro"] == "sem_etiqueta"


async def test_sonda_jt_sem_cpf_nao_vai_ao_17track(
    db: AsyncSession, client: AsyncClient, auth_as, operador, chamadas
):
    await _pedido(db, "303226", _pdf("J&T 888002704060958"), None)
    auth_as(operador)

    r = await client.post("/api/logistica/17track/sonda-temu", json={"pedidos": ["303226"]})

    assert r.status_code == 200
    assert r.json()["pedidos"] == [
        {
            "pedido": "303226",
            "codigo": "888002704060958",
            "transportadora": "J&T",
            "carrier": 100797,
            "erro": "jt_sem_cpf_no_pedido",
        }
    ]
    assert chamadas == []


async def test_sonda_exige_edicao_do_controle_de_estoque(
    db: AsyncSession, client: AsyncClient, auth_as, chamadas
):
    email = f"vw-{uuid.uuid4().hex[:6]}@davinci-test.com"
    so_ve = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={"controle_estoque": {"view": True}},
    )
    db.add(so_ve)
    await db.commit()
    auth_as(so_ve)

    r = await client.post("/api/logistica/17track/sonda-temu", json={"pedidos": ["304753"]})

    assert r.status_code == 403
    assert chamadas == []
