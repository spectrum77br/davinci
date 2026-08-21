"""Auto-hold da Margem (services/margem_auto_hold): pendente "Em aberto" é
segurado no Bling (Aguardando Cancelamento 83955 + recado nas Observações) e
PERMANECE visível na aba Pendentes até o humano decidir.

Cobre: quem é segurado (e quem NÃO é), idempotência entre ticks, falha isolada
por pedido, kill-switch, visibilidade na listagem (o pino
bling_status_margem='Pendente' abre exceção no filtro base) e o espelho da
situação no snapshot ao Aprovar um pedido segurado (sem ele a linha aprovada
sumia de todas as abas até o próximo rebuild).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingOrder
from app.routers import margens as margens_router
from app.services import margem_auto_hold

pytestmark = pytest.mark.asyncio

HOJE = date(2026, 8, 21)


class FakeBling:
    """Grava as chamadas que o hold faz no Bling (get/PUT observações/situação)."""

    def __init__(
        self,
        observacoes: str | None = None,
        *,
        fail_situacao_for: set[int] | None = None,
    ) -> None:
        self._observacoes = observacoes
        self.fail_situacao_for = fail_situacao_for or set()
        self.get_calls: list[int] = []
        self.put_bodies: list[tuple[int, dict]] = []
        self.situacao_calls: list[tuple[int, int]] = []

    async def get_order(self, bling_id: int) -> dict:
        self.get_calls.append(bling_id)
        return {
            "id": bling_id,
            "numero": "291670",
            "observacoes": self._observacoes,
            "contato": {"id": 1, "nome": "Cliente"},
            "itens": [{"id": 10, "codigo": "sku-1"}],
        }

    async def update_order(self, bling_id: int, body: dict) -> None:
        self.put_bodies.append((bling_id, body))

    async def update_order_situacao(self, bling_id: int, situacao_id: int) -> None:
        if bling_id in self.fail_situacao_for:
            raise RuntimeError("bling fora do ar")
        self.situacao_calls.append((bling_id, situacao_id))


async def _seed_pedido(
    db: AsyncSession,
    *,
    pedido: str,
    bling_id: int,
    situacao: str = "6",
    status: str | None = None,
    margem_baixa: bool = True,
    saldo_gap: bool = False,
    saldo_aguardando: bool = False,
    frete_estourado: bool = False,
) -> None:
    """Uma linha em bling_orders + uma linha-item no snapshot verificar_margem.

    margem_baixa → marketplace_margem 5 < margem_minima 10.
    saldo_gap → valorbase 100 vs líquido 80 (divergência real > R$0,01).
    saldo_aguardando → valorbase presente e líquido NULL (não reconciliado).
    frete_estourado → frete plataforma 30 > frete anúncio 10.
    """
    db.add(
        BlingOrder(
            bling_id=bling_id,
            numero=pedido,
            item_codigo=f"sku-{pedido}",
            item_index=0,
            situacao=situacao,
            status=status,
        )
    )
    await db.commit()
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, item_proportion,
                bling_status_margem,
                marketplace_margem, margem_minima,
                bling_valorbase_item,
                marketplace_liquido_base_margem_item,
                evento_frete_anuncio, marketplace_frete_real_cobrado_item
            )
            VALUES (
                :id, :pedido, :bling_id, :sku,
                :situacao, 'Em aberto', 'amazon', 1,
                :status,
                :margem, :minima,
                :valorbase,
                :liquido,
                :frete_anuncio, :frete_pago
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "pedido": pedido,
            "bling_id": bling_id,
            "sku": f"sku-{pedido}",
            "situacao": situacao,
            "status": status,
            "margem": 5 if margem_baixa else 50,
            "minima": 10,
            "valorbase": 100 if (saldo_gap or saldo_aguardando) else None,
            "liquido": 80 if saldo_gap else None,
            "frete_anuncio": 10 if frete_estourado else None,
            "frete_pago": 30 if frete_estourado else None,
        },
    )
    await db.commit()


async def _snapshot(db: AsyncSession, pedido: str) -> dict:
    return (
        (
            await db.execute(
                text(
                    "SELECT situacao, bling_status_margem "
                    "FROM verificar_margem WHERE pedido_bling = :p"
                ),
                {"p": pedido},
            )
        )
        .mappings()
        .one()
    )


async def _audits(db: AsyncSession, pedido: str) -> list[dict]:
    return [
        dict(r)
        for r in (
            await db.execute(
                text(
                    "SELECT acao, valor_antigo, valor_novo, origem, mudado_por "
                    "FROM margem_audit WHERE pedido_bling = :p ORDER BY created_at"
                ),
                {"p": pedido},
            )
        ).mappings()
    ]


async def test_segura_pendente_em_aberto(db: AsyncSession):
    await _seed_pedido(db, pedido="291670", bling_id=111)
    fake = FakeBling(observacoes="obs antiga do pedido")

    res = await margem_auto_hold.run(db, client=fake, hoje=HOJE)

    assert res == {"held": 1, "failed": 0}
    # Bling: recado datado NO TOPO preservando o texto antigo, depois situação.
    assert fake.get_calls == [111]
    assert len(fake.put_bodies) == 1
    obs = fake.put_bodies[0][1]["observacoes"]
    assert obs.startswith("21/08 - Margem DaVinci: pedido segurado")
    assert "margem abaixo do mínimo" in obs
    assert obs.endswith("\nobs antiga do pedido")
    assert fake.situacao_calls == [(111, 83955)]
    # Espelhos locais: bling_orders + snapshot com o pino 'Pendente'.
    order = (
        await db.execute(
            text("SELECT situacao, status FROM bling_orders WHERE bling_id = 111")
        )
    ).one()
    assert tuple(order) == ("83955", "Pendente")
    snap = await _snapshot(db, "291670")
    assert snap["situacao"] == "83955"
    assert snap["bling_status_margem"] == "Pendente"
    # Auditoria: ação do robô (mudado_por NULL, origem própria).
    audits = await _audits(db, "291670")
    assert audits == [
        {
            "acao": "situacao",
            "valor_antigo": "6",
            "valor_novo": "83955",
            "origem": "margens_auto",
            "mudado_por": None,
        }
    ]


async def test_segundo_tick_nao_repete(db: AsyncSession):
    await _seed_pedido(db, pedido="291670", bling_id=111)
    await margem_auto_hold.run(db, client=FakeBling(), hoje=HOJE)

    fake2 = FakeBling()
    res = await margem_auto_hold.run(db, client=fake2, hoje=HOJE)

    # Situação espelhada '83955' já não é '6' → não é mais candidato.
    assert res == {"held": 0, "failed": 0}
    assert fake2.get_calls == []
    assert fake2.situacao_calls == []


async def test_observacao_ja_escrita_hoje_nao_duplica(db: AsyncSession):
    """PUT de ontem OK mas situação falhou → retry do dia só refaz a situação."""
    await _seed_pedido(db, pedido="291670", bling_id=111)
    linha = f"{HOJE:%d/%m} - " + margem_auto_hold._mensagem("margem abaixo do mínimo")
    fake = FakeBling(observacoes=linha)

    res = await margem_auto_hold.run(db, client=fake, hoje=HOJE)

    assert res == {"held": 1, "failed": 0}
    assert fake.put_bodies == []  # compose idempotente: nada a reescrever
    assert fake.situacao_calls == [(111, 83955)]


async def test_nao_segura_pedido_que_ja_andou(db: AsyncSession):
    await _seed_pedido(db, pedido="291670", bling_id=111, situacao="15")
    fake = FakeBling()

    res = await margem_auto_hold.run(db, client=fake, hoje=HOJE)

    assert res == {"held": 0, "failed": 0}
    assert fake.situacao_calls == []


async def test_nao_segura_status_aprovado(db: AsyncSession):
    """Aprovado gravado (pós-clique) não volta a ser segurado, mesmo com
    margem baixa e situação de volta a Em aberto."""
    await _seed_pedido(db, pedido="291670", bling_id=111, status="Aprovado")
    fake = FakeBling()

    res = await margem_auto_hold.run(db, client=fake, hoje=HOJE)

    assert res == {"held": 0, "failed": 0}
    assert fake.situacao_calls == []


async def test_nao_segura_gatilho_frete(db: AsyncSession):
    """Frete estourado fica fora (a própria aba Pendentes o exclui)."""
    await _seed_pedido(
        db,
        pedido="291670",
        bling_id=111,
        margem_baixa=True,
        frete_estourado=True,
    )
    fake = FakeBling()

    res = await margem_auto_hold.run(db, client=fake, hoje=HOJE)

    assert res == {"held": 0, "failed": 0}
    assert fake.situacao_calls == []


async def test_segura_pendente_gravado_sem_gatilho(db: AsyncSession):
    """'Pendente' GRAVADO (hold manual da edição de saldo) é segurado com o
    motivo genérico."""
    await _seed_pedido(
        db, pedido="291670", bling_id=111, status="Pendente", margem_baixa=False
    )
    fake = FakeBling()

    res = await margem_auto_hold.run(db, client=fake, hoje=HOJE)

    assert res == {"held": 1, "failed": 0}
    assert "pendente de análise" in fake.put_bodies[0][1]["observacoes"]


async def test_motivo_distingue_ramos_do_saldo(db: AsyncSession):
    """Gap real → "saldo divergente"; líquido ainda NULL → "aguardando saldo
    da plataforma" (não acusa divergência que não existe)."""
    await _seed_pedido(
        db, pedido="401", bling_id=401, margem_baixa=False, saldo_gap=True
    )
    await _seed_pedido(
        db, pedido="402", bling_id=402, margem_baixa=False, saldo_aguardando=True
    )
    fake = FakeBling()

    res = await margem_auto_hold.run(db, client=fake, hoje=HOJE)

    assert res == {"held": 2, "failed": 0}
    obs = {bid: body["observacoes"] for bid, body in fake.put_bodies}
    assert "saldo divergente" in obs[401]
    assert "aguardando" not in obs[401]
    assert "aguardando saldo da plataforma" in obs[402]
    assert "divergente" not in obs[402]


async def test_flag_desligada(db: AsyncSession, monkeypatch):
    await _seed_pedido(db, pedido="291670", bling_id=111)
    monkeypatch.setattr(get_settings(), "margem_auto_hold", False, raising=False)
    fake = FakeBling()

    res = await margem_auto_hold.run(db, client=fake, hoje=HOJE)

    assert res == {"held": 0, "failed": 0, "skipped": "disabled"}
    assert fake.get_calls == []


async def test_falha_num_pedido_nao_derruba_os_demais(db: AsyncSession):
    await _seed_pedido(db, pedido="100", bling_id=111)
    await _seed_pedido(db, pedido="200", bling_id=222)
    fake = FakeBling(fail_situacao_for={111})

    res = await margem_auto_hold.run(db, client=fake, hoje=HOJE)

    assert res == {"held": 1, "failed": 1}
    assert fake.situacao_calls == [(222, 83955)]
    # O que falhou ficou como estava (rollback) e continua candidato.
    snap_fail = await _snapshot(db, "100")
    assert snap_fail["situacao"] == "6"
    assert snap_fail["bling_status_margem"] is None
    assert await _audits(db, "100") == []
    # O que deu certo foi espelhado + auditado.
    snap_ok = await _snapshot(db, "200")
    assert snap_ok["situacao"] == "83955"
    assert len(await _audits(db, "200")) == 1


def _margem_permissions() -> dict:
    return {"margem": {"view": True, "edit": True, "delete": False}}


async def test_listagem_mostra_segurado_e_esconde_os_outros_83955(
    client, db: AsyncSession, make_user, auth_as
):
    """Exceção de visibilidade: 83955 + pino 'Pendente' (auto-hold) aparece;
    83955 + NULL (controle de estoque) e 83955 + 'Reprovado' (clique) seguem
    ocultos como sempre."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    await _seed_pedido(db, pedido="301", bling_id=301, situacao="83955", status="Pendente")
    await _seed_pedido(db, pedido="302", bling_id=302, situacao="83955", status=None)
    await _seed_pedido(
        db, pedido="303", bling_id=303, situacao="83955", status="Reprovado"
    )

    response = await client.get("/api/margens/marketplace")

    assert response.status_code == 200
    pedidos = {item["pedido_bling"] for item in response.json()["items"]}
    assert "301" in pedidos
    assert "302" not in pedidos
    assert "303" not in pedidos

    # E o segurado aparece na aba Pendentes (filtro status=Pendente).
    response = await client.get("/api/margens/marketplace?status=Pendente")
    assert response.status_code == 200
    pedidos = {item["pedido_bling"] for item in response.json()["items"]}
    assert "301" in pedidos


async def test_aprovar_pedido_segurado_espelha_situacao_no_snapshot(
    client, db: AsyncSession, make_user, auth_as, monkeypatch
):
    """Aprovar um auto-segurado devolve o pedido ao fluxo (9 → 6 no Bling) e o
    snapshot ganha a situação nova NA HORA — sem o espelho, a linha ficava com
    83955 + 'Aprovado' e o filtro base a escondia de todas as abas até o
    próximo rebuild."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    await _seed_pedido(
        db, pedido="291670", bling_id=111, situacao="83955", status="Pendente"
    )

    class FakeDecisionClient:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int]] = []

        async def update_order_situacao(self, bling_id: int, situacao_id: int) -> None:
            self.calls.append((bling_id, situacao_id))

    fake = FakeDecisionClient()

    async def fake_global_bling_client(session):
        return fake

    monkeypatch.setattr(margens_router, "_global_bling_client", fake_global_bling_client)

    response = await client.patch(
        "/api/margens/marketplace/status/291670",
        json={"status": "Aprovado", "sku": "sku-291670"},
    )

    assert response.status_code == 200
    assert fake.calls == [
        (111, margens_router.SITUACAO_ATENDIDO),
        (111, margens_router.SITUACAO_APROVADO),
    ]
    snap = await _snapshot(db, "291670")
    assert snap["situacao"] == "6"
    assert snap["bling_status_margem"] == "Aprovado"


async def test_aprovar_local_only_num_segurado_ainda_solta_no_bling(
    client, db: AsyncSession, make_user, auth_as, monkeypatch
):
    """A UI manda local_only=true quando a pendência é de saldo/frete — mas num
    pedido AUTO-SEGURADO o Aprovar tem de soltar no Bling mesmo assim, senão a
    venda fica presa em Aguardando Cancelamento com status Aprovado (invisível
    em todas as abas)."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    await _seed_pedido(
        db, pedido="291670", bling_id=111, situacao="83955", status="Pendente"
    )

    class FakeDecisionClient:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int]] = []

        async def update_order_situacao(self, bling_id: int, situacao_id: int) -> None:
            self.calls.append((bling_id, situacao_id))

    fake = FakeDecisionClient()

    async def fake_global_bling_client(session):
        return fake

    monkeypatch.setattr(margens_router, "_global_bling_client", fake_global_bling_client)

    response = await client.patch(
        "/api/margens/marketplace/status/291670",
        json={"status": "Aprovado", "sku": "sku-291670", "local_only": True},
    )

    assert response.status_code == 200
    assert fake.calls == [
        (111, margens_router.SITUACAO_ATENDIDO),
        (111, margens_router.SITUACAO_APROVADO),
    ]
    order = (
        await db.execute(
            text("SELECT situacao, status FROM bling_orders WHERE bling_id = 111")
        )
    ).one()
    assert tuple(order) == ("6", "Aprovado")
    snap = await _snapshot(db, "291670")
    assert snap["situacao"] == "6"
    assert snap["bling_status_margem"] == "Aprovado"
