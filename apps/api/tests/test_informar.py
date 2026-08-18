"""Botões INFORMAR — montagem das mensagens (puro) + gate de admin do endpoint."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica, User, UserRole, UserStatus
from app.services import informar, logistica_rules


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def usuario(db: AsyncSession) -> User:
    email = f"usr-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.USER, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# ---- linhas_logistica ----


def test_linhas_logistica_formato_e_ordem_por_plataforma():
    ml = Logistica(
        plataforma="Mercado Livre",
        conta="loja a",
        pedido_marketplace="2000014548125021",
        pedido_bling="290662",
        meli_status={"order_status": "cancelled"},
        status_bling="Problemas",
    )
    shopee = Logistica(
        plataforma="Shopee",
        conta="vortan",
        pedido_marketplace="SPX123",
        pedido_bling="290001",
        meli_status={},
        status_bling="Aguardando Cancelamento",
    )
    linhas = informar.linhas_logistica([shopee, ml])
    assinatura_ml = logistica_rules.assinatura_para(
        "Mercado Livre", {"order_status": "cancelled"}
    )
    # Ordena por plataforma (ML antes de Shopee) e segue o formato pedido:
    # pedido marketplace - conta - status plataforma - status bling.
    assert linhas == [
        f"2000014548125021 - loja a - {assinatura_ml} - Problemas",
        "SPX123 - vortan - - - Aguardando Cancelamento",
    ]


def test_linhas_logistica_cai_no_pedido_bling_e_tracos():
    r = Logistica(
        plataforma="Amazon",
        conta=None,
        pedido_marketplace=None,
        pedido_bling="123",
        meli_status={},
        status_bling=None,
    )
    assert informar.linhas_logistica([r]) == ["123 - - - - - -"]


# ---- linhas_estoque ----


def test_linhas_estoque_espelha_aviso_do_sweep():
    entries = [
        ("290002", "shopee vortan equipe 2", "b035.28"),
        ("290001", "", ""),
    ]
    assert informar.linhas_estoque(entries) == [
        "Pedido 290001",
        "Pedido 290002 (shopee vortan equipe 2): b035.28",
    ]


# ---- montar_mensagens ----


def test_montar_mensagens_uma_parte_sem_rotulo():
    assert informar.montar_mensagens("Cabeçalho (2)", ["a", "b"]) == ["Cabeçalho (2)\na\nb"]


def test_montar_mensagens_sem_linhas_devolve_vazio():
    assert informar.montar_mensagens("X", []) == []


def test_montar_mensagens_fatia_por_bytes_e_preserva_linhas():
    linhas = ["x" * 100 for _ in range(10)]
    msgs = informar.montar_mensagens("Head", linhas, max_bytes=350)
    # 3 linhas por bloco (3×101 bytes ≤ 350; a 4ª estoura) → 3+3+3+1.
    assert len(msgs) == 4
    assert msgs[0].startswith("Head — parte 1/4\n")
    assert msgs[3].startswith("Head — parte 4/4\n")
    corpo: list[str] = []
    for m in msgs:
        corpo.extend(m.split("\n")[1:])
    assert corpo == linhas


# ---- gate de admin do endpoint ----


@pytest.mark.asyncio
async def test_informar_config_exige_admin(
    client: AsyncClient, auth_as, admin: User, usuario: User
):
    auth_as(usuario)
    r = await client.get("/api/informar/logistica")
    assert r.status_code == 403

    auth_as(admin)
    r = await client.get("/api/informar/logistica")
    assert r.status_code == 200
    body = r.json()
    assert body["contexto"] == "logistica"
    assert body["recipients"] == []

    # contexto desconhecido → 404 (não existe rota solta pra outros nomes)
    r = await client.get("/api/informar/outra-coisa")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_informar_put_salva_so_ids_do_diretorio(
    client: AsyncClient, auth_as, admin: User, monkeypatch: pytest.MonkeyPatch
):
    from app.config import get_settings

    monkeypatch.setattr(
        get_settings(), "threema_recipient_names", "AAAA1111:Ana,BBBB2222:Bia",
        raising=False,
    )
    monkeypatch.setattr(get_settings(), "threema_recipients", "", raising=False)
    auth_as(admin)
    r = await client.put(
        "/api/informar/controle_estoque",
        json={"recipients": ["aaaa1111", "ZZZZ9999"]},
    )
    assert r.status_code == 200
    # Normaliza pra maiúsculas e descarta quem não está no diretório.
    assert r.json()["recipients"] == ["AAAA1111"]
