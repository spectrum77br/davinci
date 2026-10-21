"""Rastreio Temu — código na etiqueta + sonda e rodada no 17track (21/10/2026).

A Temu marca "Enviado" na compra da etiqueta e o Bling vem com Tracking N/A;
o código da transportadora só está na etiqueta. Anjun vai ao 17track só com o
código; J&T exige o CPF/CNPJ do destinatário, que sai do pedido e nunca volta
na resposta. A rodada leva a 15 só o que a transportadora já coletou, com as
travas da Magalu. Sem Redis: arquivo separado do test_logistica_track.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import fitz
import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as app_db
from app.models import (
    BlingEnvioEvento,
    BlingOrder,
    MargemAudit,
    NfEtiquetaArquivo,
    User,
    UserRole,
    UserStatus,
)
from app.services import logistica_track, temu_rastreio
from app.services import marketplace_shipment_check as sweep

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


# ---- rodada automática ----

BID = 27104098429
PO = "PO-029-04644091987833262"
COLETA = datetime(2026, 10, 20, 15, 33, 52, tzinfo=UTC)  # 12:33 em Brasília


class Bling:
    """Bling falso no molde do test_magalu_shipment_sync."""

    def __init__(self, *, antes=21, depois=15):
        self.antes, self.depois = antes, depois
        self.leituras = 0
        self.escritas: list[tuple[int, int]] = []

    async def _request(self, method, path):
        assert method == "GET" and path == f"/pedidos/vendas/{BID}"
        estado = self.antes if self.leituras == 0 else self.depois
        self.leituras += 1
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": BID,
                    "numero": 304816,
                    "numeroLoja": PO,
                    "situacao": {"id": estado},
                }
            },
        )

    async def update_order_situacao(self, bid, estado):
        self.escritas.append((bid, estado))
        return True


async def _pedido_temu(
    db: AsyncSession, etiqueta: bytes, *, numeroloja: str = PO, documento: str | None = None
) -> BlingOrder:
    row = BlingOrder(
        bling_id=BID,
        numero="304816",
        numeroloja=numeroloja,
        loja="206288651",
        item_index=0,
        item_codigo="uaf001m1.110",
        item_quantidade=1,
        situacao="21",
        em_andamento_data=date(2026, 10, 21),
        documento_destinatario=documento,
    )
    db.add(row)
    db.add(
        NfEtiquetaArquivo(
            pedido_bling="304816", filename="e.pdf", content_type="application/pdf", blob=etiqueta
        )
    )
    await db.commit()
    return row


def _track(status: str, *eventos: tuple[datetime, str]) -> dict:
    return {
        "latest_status": {"status": status},
        "providers": [
            {
                "events": [
                    {"time_iso": q.isoformat(), "description": d, "location": "Piracicaba / SP"}
                    for q, d in eventos
                ]
            }
        ],
    }


@pytest.fixture
def ambiente(db, monkeypatch):
    """Liga a rodada no `db` do teste, num Bling falso e num 17track falso."""
    bling = Bling()
    feitas: list[tuple[str, Any]] = []
    respostas: dict[str, dict] = {}

    @asynccontextmanager
    async def scope():
        yield db
        await db.commit()

    async def _chamar(_c, endpoint, payload, *, base=logistica_track._BASE):
        feitas.append((endpoint, payload))
        return respostas.get(endpoint) or {"code": 0, "data": {"accepted": [], "rejected": []}}

    monkeypatch.setattr(app_db, "session_scope", scope)
    monkeypatch.setattr(sweep, "_get_bling_integration", AsyncMock(return_value=object()))
    monkeypatch.setattr(sweep, "_build_bling_client", AsyncMock(return_value=bling))
    monkeypatch.setattr(logistica_track, "_chamar", _chamar)
    return bling, feitas, respostas


async def test_rodada_coletado_fica_verde_com_a_hora_da_coleta(db: AsyncSession, ambiente):
    bling, feitas, respostas = ambiente
    row = await _pedido_temu(db, _pdf("AnjunExpress AJ261021124112101"))
    respostas["gettrackinfo"] = {
        "code": 0,
        "data": {
            "accepted": [
                {
                    "number": "AJ261021124112101",
                    "track_info": _track(
                        "InTransit",
                        (COLETA, "Objeto coletado"),
                        (COLETA.replace(hour=18, minute=19), "A caminho do centro de trânsito"),
                    ),
                }
            ]
        },
    }

    resumo = await temu_rastreio.rodada()

    assert resumo["verdes"] == 1 and resumo["erros"] == 0
    assert bling.escritas == [(BID, 15)] and bling.leituras == 2
    await db.refresh(row)
    assert row.situacao == "15" and row.em_andamento_data == date(2026, 10, 20)
    ev = (await db.scalars(select(BlingEnvioEvento).where(BlingEnvioEvento.bling_id == BID))).one()
    assert ev.occurred_at == COLETA  # a coluna Envio mostra a hora da coleta
    origens = set(
        (
            await db.scalars(select(MargemAudit.origem).where(MargemAudit.pedido_bling == "304816"))
        ).all()
    )
    assert origens == {"job_envio_temu", "temu_17track_coleta"}
    assert [e for e, _ in feitas if e == "register"] == []  # já conhecido: nada a registrar


async def test_rodada_registra_jt_desconhecida_com_cpf_e_nao_muda(db: AsyncSession, ambiente):
    bling, feitas, respostas = ambiente
    row = await _pedido_temu(db, _pdf("J&T 888002704060957"), documento=CPF)
    respostas["gettrackinfo"] = {
        "code": 0,
        "data": {
            "rejected": [
                {
                    "number": "888002704060957",
                    "error": {"code": logistica_track.ERRO_NAO_REGISTRADO},
                }
            ]
        },
    }
    respostas["register"] = {"code": 0, "data": {"accepted": [{"number": "888002704060957"}]}}

    resumo = await temu_rastreio.rodada()

    assert resumo["registrados"] == 1 and resumo["verdes"] == 0
    leitura = next(p for e, p in feitas if e == "gettrackinfo")
    assert leitura == [{"number": "888002704060957", "carrier": 100797}]  # leitura sem CPF
    registro = next(p for e, p in feitas if e == "register")
    assert registro == [{"number": "888002704060957", "carrier": 100797, "cpf_or_cnpj": CPF}]
    assert bling.leituras == 0 and bling.escritas == []
    await db.refresh(row)
    assert row.situacao == "21"


@pytest.mark.parametrize("status", ["InfoReceived", "NotFound", "Expired"])
async def test_rodada_sem_prova_de_coleta_nao_mexe(db: AsyncSession, ambiente, status):
    bling, _feitas, respostas = ambiente
    row = await _pedido_temu(db, _pdf("AJ261021124112101"))
    respostas["gettrackinfo"] = {
        "code": 0,
        "data": {
            "accepted": [
                {
                    "number": "AJ261021124112101",
                    "track_info": _track(status, (COLETA, "Etiqueta emitida")),
                }
            ]
        },
    }

    resumo = await temu_rastreio.rodada()

    assert resumo["ainda_nao_saiu"] == 1 and resumo["verdes"] == 0
    assert bling.leituras == 0
    await db.refresh(row)
    assert row.situacao == "21"


async def test_rodada_ignora_pedido_que_nao_e_temu(db: AsyncSession, ambiente):
    _bling, feitas, _respostas = ambiente
    await _pedido_temu(db, _pdf("AJ261021124112101"), numeroloja="LU-1234567891234567")

    resumo = await temu_rastreio.rodada()

    assert resumo["candidatos"] == 0 and feitas == []


async def test_aplicar_nao_mexe_se_o_bling_ja_mudou(db: AsyncSession):
    row = await _pedido_temu(db, _pdf("AJ261021124112101"))
    bling = Bling(antes=12)  # cancelaram no Bling depois do sync

    motivo = await temu_rastreio._aplicar(db, bling, row, COLETA)

    assert motivo == "mudou_no_bling" and bling.escritas == []
    await db.refresh(row)
    assert row.situacao == "21"


async def test_aplicar_nao_grava_sem_ler_15_no_bling(db: AsyncSession):
    row = await _pedido_temu(db, _pdf("AJ261021124112101"))
    bling = Bling(depois=21)  # PATCH "deu certo" mas o Bling continua em 21

    motivo = await temu_rastreio._aplicar(db, bling, row, COLETA)

    assert motivo == "bling_nao_confirmou"
    await db.refresh(row)
    assert row.situacao == "21"
