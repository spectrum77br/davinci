"""Vídeo da expedição (Vinicius 17/09/2026): solicitado na tela Devoluções
(abas Acompanhamento/Fraude), respondido pela equipe do SKU no Controle de
Estoque (aba Pedidos travada até responder).

Cobre:
- POST /api/devolutions/acompanhamento/{pedido}/video/solicitar → pendente;
- GET /api/estoque/videos-pendentes só pra quem tem a tag do SKU (cerca da aba
  Pedidos); admin vê tudo; etiqueta_disponivel quando a etiqueta está guardada;
- POST /api/estoque/videos-pendentes/{pedido}: link (normalizado com https://)
  ou "não tenho o vídeo" + motivo; fora da cerca = 403; não pendente = 404;
- pedir de novo depois do link (apagou, com motivo) → pendente + refazer_motivo;
- DELETE .../video → volta a "não solicitado";
- GET /api/devolutions/acompanhamento espelha o estado em todas as linhas.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingOrder, User, UserRole, UserStatus
from app.models.nf import NfEtiquetaArquivo

pytestmark = pytest.mark.asyncio

_SP = ZoneInfo("America/Sao_Paulo")
_ITEM_A = UUID("bbbbbbbb-0000-0000-0000-000000000001")
_ITEM_B = UUID("bbbbbbbb-0000-0000-0000-000000000002")
_PEDIDO = "556001"

_PERM_DEV = {"devolucoes": {"view": True, "edit": True, "delete": False}}
_PERM_CE = {"controle_estoque": {"view": True, "edit": True, "delete": False}}


async def _user(
    db: AsyncSession,
    *,
    name: str,
    role: UserRole = UserRole.USER,
    permissions: dict | None = None,
    stock_tags: list[str] | None = None,
) -> User:
    email = f"{name}-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        name=name,
        role=role,
        status=UserStatus.ACTIVE,
        permissions=permissions or {},
        stock_tags=stock_tags,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed(db: AsyncSession, schema: str) -> None:
    """Pedido 556001 em Aguardando Devolução com 2 itens: um `.ra` e um `.pi`
    (a cerca da aba Pedidos casa qualquer item do pedido) + a view fake que o
    GET /acompanhamento consome."""
    entrada = datetime.now(_SP).date() - timedelta(days=3)
    db.add_all([
        BlingOrder(
            id=_ITEM_A, bling_id=5560011, numero=_PEDIDO, numeroloja="MKT-556001",
            situacao="83957", aguardando_devolucao_data=entrada, loja="123",
            item_codigo="dg019.ra", item_descricao="Fossibot F105 - Preto",
            item_quantidade=1, item_index=0, nome_destinatario="Maria da Silva",
        ),
        BlingOrder(
            id=_ITEM_B, bling_id=5560012, numero=_PEDIDO, numeroloja="MKT-556001",
            situacao="83957", aguardando_devolucao_data=entrada, loja="123",
            item_codigo="a003.pi", item_descricao="Fone UFB10 - Branco",
            item_quantidade=2, item_index=1, nome_destinatario="Maria da Silva",
        ),
    ])
    await db.commit()
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_devolucoes AS
            SELECT * FROM (VALUES
                (
                    '2026-09-10T12:00:00+00:00'::timestamptz,
                    '{_PEDIDO}'::text, 'MKT-556001'::text, '83957'::text,
                    'Shopee'::text, 'Shopee Jlas'::text, 123::bigint,
                    'dg019.ra'::text, 'Fossibot F105 - Preto'::text, 1::integer,
                    '{_ITEM_A}'::uuid, 'Maria da Silva'::text,
                    'Curitiba'::text, 'PR'::text
                ),
                (
                    '2026-09-10T12:00:00+00:00'::timestamptz,
                    '{_PEDIDO}'::text, 'MKT-556001'::text, '83957'::text,
                    'Shopee'::text, 'Shopee Jlas'::text, 123::bigint,
                    'a003.pi'::text, 'Fone UFB10 - Branco'::text, 2::integer,
                    '{_ITEM_B}'::uuid, 'Maria da Silva'::text,
                    'Curitiba'::text, 'PR'::text
                )
            ) AS t(
                data, pedido_bling, pedido_marketplace, situacao,
                plataforma_bling, loja_nome, bling_loja_id,
                sku, produto, quantidade,
                bling_order_item_id, nome_destinatario,
                cidade_destino, uf_destino
            )
            """  # noqa: S608
        )
    )
    await db.commit()


async def _drop_view(db: AsyncSession, schema: str) -> None:
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.commit()


async def _acompanhamento(client) -> list[dict]:
    r = await client.get("/api/devolutions/acompanhamento")
    assert r.status_code == 200, r.text
    return [i for i in r.json()["items"] if i["pedido_bling"] == _PEDIDO]


async def test_fluxo_solicitar_responder_refazer_sem_video_cancelar(
    client, db: AsyncSession, auth_as
) -> None:
    schema = get_settings().database_schema
    await _seed(db, schema)
    vini = await _user(db, name="vinicius", permissions=_PERM_DEV)
    ra = await _user(db, name="azeroth", permissions=_PERM_CE, stock_tags=["ra"])
    sp = await _user(db, name="coreia", permissions=_PERM_CE, stock_tags=["sp"])
    try:
        # Antes de pedir: nada pendente, coluna em "solicitar".
        auth_as(vini)
        assert all(i["video_status"] == "nao_solicitado" for i in await _acompanhamento(client))

        # 1) Devoluções pede o vídeo.
        r = await client.post(f"/api/devolutions/acompanhamento/{_PEDIDO}/video/solicitar", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["video_status"] == "pendente"
        assert body["video_solicitado_por"] == "vinicius"
        assert body["video_refazer_motivo"] is None
        itens = await _acompanhamento(client)
        assert len(itens) == 2 and all(i["video_status"] == "pendente" for i in itens)

        # 2) Equipe .ra vê a pendência (item dg019.ra); equipe .sp não.
        auth_as(ra)
        r = await client.get("/api/estoque/videos-pendentes")
        assert r.status_code == 200, r.text
        pend = r.json()
        assert pend["total"] == 1
        p = pend["data"][0]
        assert p["pedido_bling"] == _PEDIDO
        assert p["pedido_marketplace"] == "MKT-556001"
        assert p["cliente"] == "Maria da Silva"
        assert [(i["sku"], i["quantidade"]) for i in p["itens"]] == [("dg019.ra", 1), ("a003.pi", 2)]
        assert p["solicitado_por"] == "vinicius"
        assert p["etiqueta_disponivel"] is False
        assert p["refazer_motivo"] is None

        auth_as(sp)
        r = await client.get("/api/estoque/videos-pendentes")
        assert r.status_code == 200 and r.json()["total"] == 0
        # ...e não consegue responder por fora.
        r = await client.post(
            f"/api/estoque/videos-pendentes/{_PEDIDO}", json={"link": "drive.google.com/abc"}
        )
        assert r.status_code == 403, r.text

        # 3) Equipe .ra cola o link (sem https → normaliza).
        auth_as(ra)
        r = await client.post(
            f"/api/estoque/videos-pendentes/{_PEDIDO}", json={"link": "drive.google.com/abc"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["video_status"] == "enviado"
        r = await client.get("/api/estoque/videos-pendentes")
        assert r.json()["total"] == 0

        auth_as(vini)
        itens = await _acompanhamento(client)
        assert all(i["video_status"] == "enviado" for i in itens)
        assert itens[0]["video_link"] == "https://drive.google.com/abc"
        assert itens[0]["video_enviado_por"] == "azeroth"
        assert itens[0]["video_enviado_em"] is not None

        # 4) Não gostou: apaga o link com motivo → volta pra equipe refazer.
        r = await client.post(
            f"/api/devolutions/acompanhamento/{_PEDIDO}/video/solicitar",
            json={"motivo": "não mostra o lacre"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["video_status"] == "pendente"
        assert r.json()["video_link"] is None
        assert r.json()["video_refazer_motivo"] == "não mostra o lacre"

        auth_as(ra)
        r = await client.get("/api/estoque/videos-pendentes")
        assert r.json()["total"] == 1
        assert r.json()["data"][0]["refazer_motivo"] == "não mostra o lacre"

        # 5) Equipe responde "não tenho o vídeo" + motivo.
        r = await client.post(
            f"/api/estoque/videos-pendentes/{_PEDIDO}",
            json={"sem_video_motivo": "pedido não foi filmado"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["video_status"] == "sem_video"
        assert (await client.get("/api/estoque/videos-pendentes")).json()["total"] == 0

        auth_as(vini)
        itens = await _acompanhamento(client)
        assert all(i["video_status"] == "sem_video" for i in itens)
        assert itens[0]["video_sem_motivo"] == "pedido não foi filmado"
        assert itens[0]["video_link"] is None
        assert itens[0]["video_refazer_motivo"] is None

        # 6) Cancelar → volta a "não solicitado".
        r = await client.delete(f"/api/devolutions/acompanhamento/{_PEDIDO}/video")
        assert r.status_code == 200, r.text
        assert r.json()["video_status"] == "nao_solicitado"
        assert all(i["video_status"] == "nao_solicitado" for i in await _acompanhamento(client))
    finally:
        await _drop_view(db, schema)


async def test_resposta_invalida_e_nao_pendente(client, db: AsyncSession, auth_as) -> None:
    schema = get_settings().database_schema
    await _seed(db, schema)
    vini = await _user(db, name="vinicius", permissions=_PERM_DEV)
    ra = await _user(db, name="azeroth", permissions=_PERM_CE, stock_tags=["ra"])
    try:
        # Sem solicitação: responder dá 404 (nada pendente).
        auth_as(ra)
        r = await client.post(
            f"/api/estoque/videos-pendentes/{_PEDIDO}", json={"link": "https://x.com/v"}
        )
        assert r.status_code == 404, r.text

        auth_as(vini)
        assert (
            await client.post(f"/api/devolutions/acompanhamento/{_PEDIDO}/video/solicitar")
        ).status_code == 200

        auth_as(ra)
        # Nem link nem motivo / os dois juntos / link inválido / motivo curto.
        for payload in (
            {},
            {"link": "https://x.com/v", "sem_video_motivo": "não filmado"},
            {"link": "sem ponto"},
            {"sem_video_motivo": "no"},
        ):
            r = await client.post(f"/api/estoque/videos-pendentes/{_PEDIDO}", json=payload)
            assert r.status_code == 422, (payload, r.text)

        # Pedido inexistente no espelho do Bling: solicitar dá 404.
        auth_as(vini)
        r = await client.post("/api/devolutions/acompanhamento/999999/video/solicitar")
        assert r.status_code == 404
    finally:
        await _drop_view(db, schema)


async def test_admin_ve_tudo_e_etiqueta_guardada(client, db: AsyncSession, auth_as) -> None:
    schema = get_settings().database_schema
    await _seed(db, schema)
    db.add(NfEtiquetaArquivo(
        pedido_bling=_PEDIDO,
        filename=f"etiqueta_{_PEDIDO}.pdf",
        content_type="application/pdf",
        size_bytes=4,
        blob=b"%PDF",
    ))
    await db.commit()
    vini = await _user(db, name="vinicius", permissions=_PERM_DEV)
    admin = await _user(db, name="heisenberg", role=UserRole.ADMIN)
    try:
        auth_as(vini)
        assert (
            await client.post(f"/api/devolutions/acompanhamento/{_PEDIDO}/video/solicitar")
        ).status_code == 200
        auth_as(admin)
        r = await client.get("/api/estoque/videos-pendentes")
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 1
        assert r.json()["data"][0]["etiqueta_disponivel"] is True
        # Admin filtrando uma tag que não é do pedido: some.
        r = await client.get("/api/estoque/videos-pendentes?tag=sp")
        assert r.json()["total"] == 0
    finally:
        await _drop_view(db, schema)
