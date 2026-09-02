"""Link "Aprovar pelo celular" — token assinado (puro) + página pública GET/POST.

O POST tem de disparar EXATATAMENTE o mesmo fluxo da aba Margem
(Atendido→Aprovado no Bling, bling_orders e snapshot), atribuído ao
usuário-sistema; pedido já aprovado é idempotente e não toca no Bling.
"""

from __future__ import annotations

import time
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, User
from app.routers import margens as margens_router
from app.services import aprovar_link

# ---- token (puro) ----


def test_token_roundtrip():
    t = aprovar_link.gerar_token("291670")
    assert aprovar_link.validar_token(t) == "291670"


def test_token_adulterado_ou_lixo_nao_valida():
    t = aprovar_link.gerar_token("291670")
    payload, sig = t.split(".", 1)
    sig_trocada = ("A" if sig[0] != "A" else "B") + sig[1:]
    assert aprovar_link.validar_token(f"{payload}.{sig_trocada}") is None
    # Payload trocado (outro pedido) com a assinatura antiga também cai.
    outro = aprovar_link.gerar_token("999999").split(".", 1)[0]
    assert aprovar_link.validar_token(f"{outro}.{sig}") is None
    assert aprovar_link.validar_token("sem-ponto") is None
    assert aprovar_link.validar_token("") is None


def test_token_vencido_nao_valida():
    velho = int(time.time()) - aprovar_link.VALIDADE_S - 10
    t = aprovar_link.gerar_token("291670", agora=velho)
    assert aprovar_link.validar_token(t) is None


def test_url_aprovar_usa_app_url():
    url = aprovar_link.url_aprovar("291670")
    assert url.startswith("http://localhost:3000/api/aprovar/")


# ---- página pública ----

pytestmark = pytest.mark.asyncio


async def _seed_pedido(
    db: AsyncSession, *, status: str | None = "Pendente", situacao: str = "83955"
) -> BlingOrder:
    """Pedido segurado pelo auto-hold: 83955 + pino Pendente, com linha no
    snapshot pra página mostrar a conta."""
    order = BlingOrder(
        bling_id=111,
        numero="291670",
        item_codigo="sku-a",
        item_index=0,
        situacao=situacao,
        status=status,
    )
    db.add(order)
    await db.execute(
        text(
            "INSERT INTO verificar_margem"
            " (bling_order_item_id, pedido_bling, plataforma_bling, loja_nome,"
            "  bling_status_margem)"
            " VALUES (:i, '291670', 'ml', 'Loja ML', :s)"
        ),
        {"i": str(uuid.uuid4()), "s": status},
    )
    await db.commit()
    await db.refresh(order)
    return order


class _FakeBling:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def update_order_situacao(self, bling_id: int, situacao_id: int) -> None:
        self.calls.append((bling_id, situacao_id))


def _mock_bling(monkeypatch: pytest.MonkeyPatch) -> _FakeBling:
    fake = _FakeBling()

    async def _fake_global(session):  # noqa: ANN001, ANN202
        return fake

    monkeypatch.setattr(margens_router, "_global_bling_client", _fake_global)
    return fake


async def test_get_mostra_confirmacao(client: AsyncClient, db: AsyncSession):
    await _seed_pedido(db)
    r = await client.get(f"/api/aprovar/{aprovar_link.gerar_token('291670')}")
    assert r.status_code == 200
    assert "Pedido 291670" in r.text
    assert "ml Loja ML" in r.text
    assert "Aprovar pedido" in r.text  # botão do form


async def test_get_token_invalido_404(client: AsyncClient):
    r = await client.get("/api/aprovar/qualquer-coisa")
    assert r.status_code == 404
    assert "Link inválido" in r.text


async def test_get_pedido_inexistente_404(client: AsyncClient):
    r = await client.get(f"/api/aprovar/{aprovar_link.gerar_token('404404')}")
    assert r.status_code == 404
    assert "Não achei o pedido 404404" in r.text


async def test_post_aprova_como_na_aba_margem(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    order = await _seed_pedido(db)
    fake = _mock_bling(monkeypatch)

    r = await client.post(f"/api/aprovar/{aprovar_link.gerar_token('291670')}")

    assert r.status_code == 200
    assert "aprovado ✓" in r.text
    # Mesmos passos da aba: Atendido → Aprovado.
    assert fake.calls == [
        (111, margens_router.SITUACAO_ATENDIDO),
        (111, margens_router.SITUACAO_APROVADO),
    ]
    await db.refresh(order)
    sistema = (
        await db.execute(select(User).where(User.open_id == "system:aprovacao-threema"))
    ).scalar_one()
    assert order.status == "Aprovado"
    assert order.situacao == str(margens_router.SITUACAO_APROVADO)
    assert order.verificado is True
    assert order.aprovado_por == sistema.id
    snap = (
        await db.execute(
            text(
                "SELECT bling_status_margem, situacao FROM verificar_margem"
                " WHERE pedido_bling = '291670'"
            )
        )
    ).one()
    assert tuple(snap) == ("Aprovado", str(margens_router.SITUACAO_APROVADO))


async def test_post_ja_aprovado_e_idempotente(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    await _seed_pedido(db, status="Aprovado", situacao="6")
    fake = _mock_bling(monkeypatch)

    r = await client.post(f"/api/aprovar/{aprovar_link.gerar_token('291670')}")

    assert r.status_code == 200
    assert "já está aprovado" in r.text
    assert fake.calls == []
