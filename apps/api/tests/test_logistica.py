"""Logística — CRUD dos casos + aba Status + sugestão de Status Bling.

Registro manual no formato da planilha. Gated pelo recurso `logistica`
(view p/ listar/opções/sugestão/status, edit p/ criar/editar/remover). A
sugestão devolve os Status Bling candidatos que a curadoria da planilha já viu
pra a assinatura de status do Meli informada — nunca decide sozinho.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole, UserStatus
from app.services import logistica_rules


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def viewer(db: AsyncSession) -> User:
    email = f"vw-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={"logistica": {"view": True}},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def outsider(db: AsyncSession) -> User:
    email = f"out-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.USER, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_crud_lifecycle(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)

    meli = {"order_status": "cancelled", "ship_status": "delivered", "cancel_group": "mediations"}
    r = await client.post(
        "/api/logistica",
        json={
            "data": "2026-07-10",
            "pedido_bling": "283041",
            "pedido_marketplace": "2000012345",
            "plataforma": "Mercado Livre",
            "conta": "inova",
            "meli_status": meli,
            "rastreio": "BR123456789BR",
            "localizacao": "em trânsito",
            "status_bling": "Aguardando Devolução",
            "chamado": "CH-9981",
            "observacao": "cliente pediu troca",
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["pedido_bling"] == "283041"
    assert r.json()["meli_status"] == meli
    assert r.json()["rastreio"] == "BR123456789BR"
    assert r.json()["chamado"] == "CH-9981"
    assert r.json()["status_bling"] == "Aguardando Devolução"

    # Lista.
    r = await client.get("/api/logistica")
    assert any(c["id"] == cid for c in r.json())

    # Edita (troca classificação + limpa um campo do meli + rastreio novo).
    r = await client.patch(
        f"/api/logistica/{cid}",
        json={
            "status_bling": "Resolvido",
            "meli_status": {"order_status": "cancelled"},
            "rastreio": "NEW999",
        },
    )
    assert r.status_code == 200
    assert r.json()["status_bling"] == "Resolvido"
    assert r.json()["meli_status"] == {"order_status": "cancelled"}
    assert r.json()["rastreio"] == "NEW999"
    assert r.json()["chamado"] == "CH-9981"  # inalterado

    # Remove.
    r = await client.delete(f"/api/logistica/{cid}")
    assert r.status_code == 204
    r = await client.get("/api/logistica")
    assert not any(c["id"] == cid for c in r.json())


@pytest.mark.asyncio
async def test_list_filtra_por_plataforma(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(
        "/api/logistica", json={"pedido_bling": "ML1", "plataforma": "Mercado Livre"}
    )
    assert r.status_code == 201
    ml_id = r.json()["id"]
    r = await client.post("/api/logistica", json={"pedido_bling": "SP1", "plataforma": "Shopee"})
    assert r.status_code == 201
    sp_id = r.json()["id"]

    # ?plataforma=ml → só Mercado Livre (chave canônica → rótulo).
    r = await client.get("/api/logistica?plataforma=ml")
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert ml_id in ids
    assert sp_id not in ids

    # sem filtro → traz as duas.
    ids = {c["id"] for c in (await client.get("/api/logistica")).json()}
    assert {ml_id, sp_id} <= ids


@pytest.mark.asyncio
async def test_status_crud(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(
        "/api/logistica/status",
        json={
            "plataforma": "Mercado Livre",
            "status_plataforma": "Devolução em trânsito",
            "status_atual": "Em andamento",
            "alterar_status_bling": "Aguardando Devolução",
            "monitoramento": True,
            "abrir_chamado": True,
            "abrir_reembolso": True,
            "mensagem_chamado": "acompanhar devolução",
            "mensagem_bling": "cliente devolveu",
            "mensagem_threema": "avisar equipe",
        },
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["status_plataforma"] == "Devolução em trânsito"
    assert r.json()["plataforma"] == "Mercado Livre"
    assert r.json()["status_atual"] == "Em andamento"
    assert r.json()["monitoramento"] is True
    assert r.json()["abrir_chamado"] is True
    assert r.json()["abrir_reembolso"] is True
    assert r.json()["mensagem_bling"] == "cliente devolveu"
    assert r.json()["mensagem_threema"] == "avisar equipe"
    assert "anexar_envio" not in r.json()

    r = await client.get("/api/logistica/status")
    assert any(s["id"] == sid for s in r.json())

    r = await client.patch(
        f"/api/logistica/status/{sid}",
        json={
            "abrir_chamado": False,
            "abrir_reembolso": False,
            "alterar_status_bling": "",
            "status_atual": "",
            "mensagem_bling": "",
            "monitoramento": False,
        },
    )
    assert r.status_code == 200
    assert r.json()["abrir_chamado"] is False
    assert r.json()["abrir_reembolso"] is False
    assert r.json()["monitoramento"] is False
    assert r.json()["alterar_status_bling"] is None
    assert r.json()["status_atual"] is None
    assert r.json()["mensagem_bling"] is None

    r = await client.delete(f"/api/logistica/status/{sid}")
    assert r.status_code == 204
    r = await client.get("/api/logistica/status")
    assert not any(s["id"] == sid for s in r.json())


@pytest.mark.asyncio
async def test_status_cria_vazio(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    # Campos opcionais: dá pra criar uma linha vazia pra preencher à mão depois.
    auth_as(admin)
    r = await client.post("/api/logistica/status", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status_plataforma"] is None
    assert body["plataforma"] is None
    assert body["monitoramento"] is False
    assert body["abrir_chamado"] is False
    assert body["abrir_reembolso"] is False


@pytest.mark.asyncio
async def test_enviar_chamado_404(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(f"/api/logistica/{uuid.uuid4()}/enviar-chamado")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "logistica_not_found"


@pytest.mark.asyncio
async def test_enviar_chamado_sem_mensagem(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    # Sem meli_status → sem assinatura → nenhuma regra da aba Status casa →
    # 422 logistica_sem_mensagem_chamado (nem tenta chamar o ML).
    auth_as(admin)
    r = await client.post(
        "/api/logistica",
        json={"plataforma": "Mercado Livre", "conta": "loja", "pedido_marketplace": "ML1"},
    )
    cid = r.json()["id"]
    r = await client.post(f"/api/logistica/{cid}/enviar-chamado")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "logistica_sem_mensagem_chamado"


@pytest.mark.asyncio
async def test_viewer_can_list_not_edit(
    client: AsyncClient, admin: User, viewer: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post("/api/logistica", json={"pedido_bling": "999"})
    assert r.status_code == 201

    auth_as(viewer)
    # view-only: lista, opções, sugestão e status OK
    assert (await client.get("/api/logistica")).status_code == 200
    assert (await client.get("/api/logistica/opcoes")).status_code == 200
    assert (await client.get("/api/logistica/status")).status_code == 200
    r = await client.post("/api/logistica/sugestao", json={"meli_status": {"order_status": "paid"}})
    assert r.status_code == 200
    # mas NÃO cria (nem caso nem status)
    assert (await client.post("/api/logistica", json={"pedido_bling": "hack"})).status_code == 403
    assert (
        await client.post("/api/logistica/status", json={"status_plataforma": "x"})
    ).status_code == 403


@pytest.mark.asyncio
async def test_outsider_forbidden(
    client: AsyncClient, outsider: User, auth_as: Callable[[User | None], None]
):
    auth_as(outsider)
    assert (await client.get("/api/logistica")).status_code == 403


@pytest.mark.asyncio
async def test_patch_not_found(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.patch(f"/api/logistica/{uuid.uuid4()}", json={"status_bling": "z"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "logistica_not_found"

    r = await client.patch(f"/api/logistica/status/{uuid.uuid4()}", json={"abrir_chamado": True})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "logistica_status_not_found"


@pytest.mark.asyncio
async def test_atualizar_meli_404(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(f"/api/logistica/{uuid.uuid4()}/atualizar-meli")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "logistica_not_found"


@pytest.mark.asyncio
async def test_atualizar_meli_nao_ml(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    # Linha de outra plataforma: enriquecer não se aplica → 422 logistica_nao_ml.
    auth_as(admin)
    r = await client.post(
        "/api/logistica",
        json={"plataforma": "Shopee", "conta": "loja", "pedido_marketplace": "SP123"},
    )
    cid = r.json()["id"]
    r = await client.post(f"/api/logistica/{cid}/atualizar-meli")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "logistica_nao_ml"


@pytest.mark.asyncio
async def test_atualizar_meli_sem_pedido(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    # ML sem pedido de marketplace → 422 logistica_sem_pedido.
    auth_as(admin)
    r = await client.post(
        "/api/logistica", json={"plataforma": "Mercado Livre", "conta": "inova"}
    )
    cid = r.json()["id"]
    r = await client.post(f"/api/logistica/{cid}/atualizar-meli")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "logistica_sem_pedido"


@pytest.mark.asyncio
async def test_atualizar_meli_sem_integracao(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    # ML com pedido mas conta sem integração ML cadastrada → 422 logistica_sem_integracao.
    auth_as(admin)
    r = await client.post(
        "/api/logistica",
        json={
            "plataforma": "Mercado Livre",
            "conta": "conta-inexistente-xyz",
            "pedido_marketplace": "2000012345",
        },
    )
    cid = r.json()["id"]
    r = await client.post(f"/api/logistica/{cid}/atualizar-meli")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "logistica_sem_integracao"


@pytest.mark.asyncio
async def test_sugestao_candidatos_ordenados(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(
        "/api/logistica/sugestao",
        json={"meli_status": {"order_status": "cancelled", "ship_status": "delivered", "cancel_group": "mediations"}},
    )
    assert r.status_code == 200
    cand = r.json()["candidatos"]
    assert len(cand) > 1
    # ordenado por frequência decrescente
    counts = [c["matches"] for c in cand]
    assert counts == sorted(counts, reverse=True)
    labels = {c["status_bling"] for c in cand}
    assert "Aguardando Devolução" in labels


@pytest.mark.asyncio
async def test_opcoes_expostas(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.get("/api/logistica/opcoes")
    assert r.status_code == 200
    body = r.json()
    assert body["field_order"] == logistica_rules.FIELD_ORDER
    assert "cancelled" in body["field_options"]["order_status"]
    assert "status_bling_options" in body


@pytest.mark.asyncio
async def test_opcoes_status_bling_options(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
    db: AsyncSession,
):
    # Os nomes das situações do Bling alimentam o dropdown "Alterar Status Bling".
    from app.models import SituacaoBling

    db.add(SituacaoBling(id=999001, nome="Aguardando Devolução"))
    db.add(SituacaoBling(id=999002, nome="Entregue"))
    await db.commit()

    auth_as(admin)
    r = await client.get("/api/logistica/opcoes")
    assert r.status_code == 200
    opts = r.json()["status_bling_options"]
    assert "Aguardando Devolução" in opts
    assert "Entregue" in opts


def test_sugerir_selecao_vazia_retorna_lista_vazia():
    assert logistica_rules.sugerir({}) == []
    assert logistica_rules.sugerir({"order_status": ""}) == []


# PNG 1x1 válido (assinatura + IHDR + IDAT + IEND).
_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da6360000002000154a24f60000000004945"
    "4e44ae426082"
)


async def _criar_status(client: AsyncClient) -> str:
    r = await client.post(
        "/api/logistica/status", json={"status_plataforma": "com anexo"}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_anexo_upload_serve_delete(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    sid = await _criar_status(client)

    # upload
    r = await client.post(
        f"/api/logistica/status/{sid}/anexos",
        files={"file": ("foto.png", _PNG_1X1, "image/png")},
    )
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert r.json()["filename"] == "foto.png"
    assert r.json()["content_type"] == "image/png"
    assert r.json()["size_bytes"] == len(_PNG_1X1)

    # aparece na listagem do status
    r = await client.get("/api/logistica/status")
    row = next(s for s in r.json() if s["id"] == sid)
    assert any(a["id"] == aid for a in row["anexos"])

    # serve os bytes com o content-type certo
    r = await client.get(f"/api/logistica/anexos/{aid}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == _PNG_1X1

    # delete
    r = await client.delete(f"/api/logistica/anexos/{aid}")
    assert r.status_code == 204
    r = await client.get(f"/api/logistica/anexos/{aid}")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "logistica_anexo_not_found"


@pytest.mark.asyncio
async def test_anexo_tipo_invalido(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    sid = await _criar_status(client)
    r = await client.post(
        f"/api/logistica/status/{sid}/anexos",
        files={"file": ("doc.txt", b"nao sou imagem", "text/plain")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "logistica_anexo_tipo_invalido"


@pytest.mark.asyncio
async def test_anexo_status_not_found(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(
        f"/api/logistica/status/{uuid.uuid4()}/anexos",
        files={"file": ("foto.png", _PNG_1X1, "image/png")},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "logistica_status_not_found"


@pytest.mark.asyncio
async def test_anexo_viewer_serve_mas_nao_edita(
    client: AsyncClient,
    admin: User,
    viewer: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)
    sid = await _criar_status(client)
    r = await client.post(
        f"/api/logistica/status/{sid}/anexos",
        files={"file": ("foto.png", _PNG_1X1, "image/png")},
    )
    aid = r.json()["id"]

    auth_as(viewer)
    # view pode ver a imagem
    r = await client.get(f"/api/logistica/anexos/{aid}")
    assert r.status_code == 200
    # mas não pode subir nem apagar
    r = await client.post(
        f"/api/logistica/status/{sid}/anexos",
        files={"file": ("foto.png", _PNG_1X1, "image/png")},
    )
    assert r.status_code == 403
    r = await client.delete(f"/api/logistica/anexos/{aid}")
    assert r.status_code == 403
