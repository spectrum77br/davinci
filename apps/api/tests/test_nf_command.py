"""Outbox da importação avulsa (Fase 3a-4) + superfície M2M do executor.

`POST /api/nf-cadastro/faturamento/enfileirar` gera a planilha POR FATURADOR e
cria um NfCommand (CSV congelado) por faturador, marcando cada pedido como
'processando' em nf_faturamento. O executor local faz poll de
`/agent/lease` (X-Agent-Token), pega o login do faturador + a planilha em
base64, e reporta em `/agent/commands/{id}/result` (done → 'ok', failed →
'erro').
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    BlingOrder,
    NfCommand,
    NfFaturador,
    NfFaturamento,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt

_TOKEN = "nf-agent-test-token"


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed_pedido(
    db: AsyncSession, *, numero: str, loja: str, sku: str, nome: str, unit: float
) -> None:
    db.add(
        BlingOrder(
            numero=numero,
            data=datetime(2026, 6, 23, tzinfo=UTC),
            loja=loja,
            situacao="15",
            item_index=0,
            item_codigo=sku,
            item_descricao=nome,
            item_quantidade=1,
            itemvalor=unit,
            nome_destinatario="Cleso Menezes",
            cep_destino="30570050",
            endereco_destino="Rua Emídio Beruto",
            numero_destino="30",
            bairro_destino="Cinquentenário",
            cidade_destino="Belo Horizonte",
            uf_destino="MG",
        )
    )


async def _seed_dois_faturadores(db: AsyncSession, admin: User) -> None:
    """Duas lojas com faturadores distintos + um pedido cada → 2 comandos."""
    f1 = NfFaturador(
        nome="bling avulso", modo="bling", nf_cheia=True,
        sku_fonte="principal", nome_fonte="produto", ncm="4202.12.10",
        ads_power="perfil-A", usuario="user-a", senha_enc=encrypt("segredoA"),
    )
    f2 = NfFaturador(
        nome="bling exclusivo", modo="bling", nf_cheia=False,
        percentual="0.1", sku_fonte="a001", nome_fonte="embalagem",
        ads_power="perfil-B", usuario="user-b", senha_enc=encrypt("segredoB"),
    )
    db.add_all([f1, f2])
    await db.flush()
    db.add(StoreInfo(user_id=admin.id, platform="amazon", account_name="l1",
                     bling_store_id="930001", nf_faturador_id=f1.id))
    db.add(StoreInfo(user_id=admin.id, platform="shopee", account_name="l2",
                     bling_store_id="930002", nf_faturador_id=f2.id))
    await db.flush()
    await _seed_pedido(db, numero="830001", loja="930001", sku="dg053.ci", nome="Capa", unit=500)
    await _seed_pedido(db, numero="830002", loja="930002", sku="x1", nome="Produto X", unit=1000)
    await db.commit()


@pytest.mark.asyncio
async def test_enfileirar_cria_comando_por_faturador(
    db: AsyncSession, client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    await _seed_dois_faturadores(db, admin)

    r = await client.post(
        "/api/nf-cadastro/faturamento/enfileirar",
        json={"numeros": ["830001", "830002"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["comandos"] == 2
    assert body["pedidos_ok"] == 2
    assert body["pulados"] == []

    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert len(cmds) == 2
    assert {c.status for c in cmds} == {"pending"}
    assert all(c.planilha and c.nome_arquivo.endswith(".csv") for c in cmds)
    # cada comando cobre exatamente o pedido da sua loja/faturador
    cobertos = sorted(n for c in cmds for n in c.numeros)
    assert cobertos == ["830001", "830002"]

    # cada pedido virou 'processando' em nf_faturamento
    fats = {f.pedido_bling: f for f in (await db.execute(select(NfFaturamento))).scalars().all()}
    assert fats["830001"].status_faturamento == "processando"
    assert fats["830002"].status_faturamento == "processando"


@pytest.mark.asyncio
async def test_enfileirar_sem_faturador_422(
    db: AsyncSession, client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    # loja SEM faturador → nenhum bloco gerado
    db.add(StoreInfo(user_id=admin.id, platform="shopee", account_name="l2",
                     bling_store_id="940002"))
    await db.flush()
    await _seed_pedido(db, numero="840002", loja="940002", sku="b", nome="B", unit=100)
    await db.commit()

    r = await client.post(
        "/api/nf-cadastro/faturamento/enfileirar",
        json={"numeros": ["840002"]},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "nf_nenhum_pedido_gerado"


@pytest.mark.asyncio
async def test_agent_lease_entrega_login_e_planilha(
    db: AsyncSession, client: AsyncClient, admin: User,
    auth_as: Callable[[User | None], None], monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    auth_as(admin)
    await _seed_dois_faturadores(db, admin)
    await client.post("/api/nf-cadastro/faturamento/enfileirar",
                      json={"numeros": ["830001", "830002"]})

    r = await client.post(
        "/api/nf-cadastro/agent/lease",
        json={"limit": 5},
        headers={"X-Agent-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    cmds = r.json()["commands"]
    assert len(cmds) == 2
    por_user = {c["usuario"]: c for c in cmds}
    a = por_user["user-a"]
    assert a["ads_power"] == "perfil-A"
    assert a["senha"] == "segredoA"  # descriptografada no lease
    assert a["numeros"] == ["830001"]
    # planilha em base64 decodifica pro CSV congelado (BOM + cabeçalho)
    csv_bytes = base64.b64decode(a["planilha_b64"])
    assert csv_bytes.startswith(b"\xef\xbb\xbf")

    # os comandos viraram 'claimed' com attempts=1
    rows = (await db.execute(select(NfCommand))).scalars().all()
    assert {c.status for c in rows} == {"claimed"}
    assert all(c.attempts == 1 and c.claimed_at is not None for c in rows)


@pytest.mark.asyncio
async def test_agent_lease_sem_token_401(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    # sem header
    r = await client.post("/api/nf-cadastro/agent/lease", json={"limit": 5})
    assert r.status_code == 401
    # token errado
    r = await client.post("/api/nf-cadastro/agent/lease", json={"limit": 5},
                          headers={"X-Agent-Token": "errado"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_agent_lease_token_vazio_fecha(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    # token não configurado no servidor → endpoint fechado mesmo com header
    monkeypatch.setattr(get_settings(), "nf_agent_token", "")
    r = await client.post("/api/nf-cadastro/agent/lease", json={"limit": 5},
                          headers={"X-Agent-Token": "qualquer"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_agent_result_done_marca_ok(
    db: AsyncSession, client: AsyncClient, admin: User,
    auth_as: Callable[[User | None], None], monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    auth_as(admin)
    await _seed_dois_faturadores(db, admin)
    await client.post("/api/nf-cadastro/faturamento/enfileirar",
                      json={"numeros": ["830001", "830002"]})
    lease = await client.post("/api/nf-cadastro/agent/lease", json={"limit": 5},
                              headers={"X-Agent-Token": _TOKEN})
    cmd = lease.json()["commands"][0]

    r = await client.post(
        f"/api/nf-cadastro/agent/commands/{cmd['id']}/result",
        json={"status": "done"},
        headers={"X-Agent-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"

    row = await db.get(NfCommand, uuid.UUID(cmd["id"]))
    await db.refresh(row)
    assert row.status == "done"
    assert row.completed_at is not None

    numero = cmd["numeros"][0]
    fat = (await db.execute(
        select(NfFaturamento).where(NfFaturamento.pedido_bling == numero)
    )).scalar_one()
    await db.refresh(fat)
    assert fat.status_faturamento == "ok"
    assert fat.erro_faturamento is None


@pytest.mark.asyncio
async def test_agent_result_failed_marca_erro(
    db: AsyncSession, client: AsyncClient, admin: User,
    auth_as: Callable[[User | None], None], monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    auth_as(admin)
    await _seed_dois_faturadores(db, admin)
    await client.post("/api/nf-cadastro/faturamento/enfileirar",
                      json={"numeros": ["830001", "830002"]})
    lease = await client.post("/api/nf-cadastro/agent/lease", json={"limit": 5},
                              headers={"X-Agent-Token": _TOKEN})
    cmd = lease.json()["commands"][0]

    r = await client.post(
        f"/api/nf-cadastro/agent/commands/{cmd['id']}/result",
        json={"status": "failed", "result": "AdsPower não abriu"},
        headers={"X-Agent-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"

    numero = cmd["numeros"][0]
    fat = (await db.execute(
        select(NfFaturamento).where(NfFaturamento.pedido_bling == numero)
    )).scalar_one()
    await db.refresh(fat)
    assert fat.status_faturamento == "erro"
    assert fat.erro_faturamento == "AdsPower não abriu"


@pytest.mark.asyncio
async def test_agent_result_comando_inexistente_404(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    r = await client.post(
        f"/api/nf-cadastro/agent/commands/{uuid.uuid4()}/result",
        json={"status": "done"},
        headers={"X-Agent-Token": _TOKEN},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "nf_command_not_found"


@pytest.mark.asyncio
async def test_agent_planilha_serve_csv(
    db: AsyncSession, client: AsyncClient, admin: User,
    auth_as: Callable[[User | None], None], monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    auth_as(admin)
    await _seed_dois_faturadores(db, admin)
    await client.post("/api/nf-cadastro/faturamento/enfileirar",
                      json={"numeros": ["830001", "830002"]})
    cmd = (await db.execute(select(NfCommand))).scalars().first()

    r = await client.get(
        f"/api/nf-cadastro/agent/commands/{cmd.id}/planilha",
        headers={"X-Agent-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")
