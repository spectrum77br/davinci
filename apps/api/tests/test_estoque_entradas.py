"""Produtos 2 › estoque do produto › "Incluir" (M-43, Harry 24/11/2026): entrada avulsa no Bling.

Só admin; só produto simples, ativo e com id no Bling (kit: "Kit não tem estoque próprio: inclua
nas peças"); a mesma Idempotency-Key nunca lança duas vezes; custo do produto e observação vão no
lançamento; erro depois de enviar NÃO é repetido: o worker confere no extrato. O Bling é sempre
falso aqui."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingKitComponent, Devolution, EstoqueEntrada, Product, UserRole
from app.models.alert import Alert
from app.models.stock_movement import StockMovement
from app.services import devolution_stock_return as dsr
from app.services import estoque_entrada as svc
from app.services.marketplaces.bling import BlingCloudflareError

pytestmark = pytest.mark.asyncio

URL = "/api/estoque/entradas"
KIT = "Kit não tem estoque próprio: inclua nas peças"


def _erro_post(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.bling.com.br/Api/v3/estoques")
    return httpx.HTTPStatusError(
        str(status), request=req, response=httpx.Response(status, request=req)
    )


class FakeBling:
    """Lança no "Bling" de mentira. `falhas` = fila de exceções por POST (None = deu certo)."""

    def __init__(self):
        self.lancamentos: list[dict] = []
        self.tentativas = 0
        self.falhas: list[Exception | None] = []
        self.custos: dict[str, float] = {}

    async def update_stock_by_id(self, pid, qty, *, operation, observacao=None, custo=None, **kw):
        self.tentativas += 1
        falha = self.falhas.pop(0) if self.falhas else None
        if falha is not None:
            raise falha
        self.lancamentos.append(
            {
                "pid": int(pid),
                "qtd": int(qty),
                "op": operation,
                "obs": observacao,
                "custo": custo,
                **kw,
            }
        )
        return {"id": 9_990_000 + len(self.lancamentos)}

    async def cost_by_skus(self, skus):
        return {s: self.custos[s] for s in skus if s in self.custos}

    async def _request(self, *a, **kw):  # pragma: no cover — nada mais pode ser chamado
        raise AssertionError(f"chamada inesperada ao Bling: {a}")


@pytest.fixture(autouse=True)
async def _limpa_ids_deste_arquivo(db: AsyncSession):
    """stock_movements e bling_kit_components não são limpos entre testes: ids 880xxx são daqui."""

    async def limpar():
        await db.execute(
            delete(StockMovement).where(StockMovement.bling_product_id.between(880000, 880999))
        )
        await db.execute(
            delete(BlingKitComponent).where(
                BlingKitComponent.kit_bling_product_id.between(880000, 880999)
            )
        )
        await db.commit()

    await limpar()
    yield
    await limpar()


@pytest.fixture
async def produtos(db: AsyncSession, make_user):
    dono = await make_user()

    def p(sku, bid, **kw):
        base = {"situacao": "A", "formato": "S", "name": f"Produto {sku}"}
        base.update(kw)
        return Product(user_id=dono.id, sku=sku, bling_product_id=bid, **base)

    db.add_all(
        [
            p("dg019.ra", 880101, bling_cost_price=Decimal("812.5"), stock=98),
            p("dg020.ra", 880102, situacao="I"),
            p("dg021.ra", None),
            p("z2819", 880103),
            p("dg019.ra+a001.ra", 880301, formato="E"),
            p("b011", 880302, formato="E"),
            p("kitestrutura", 880303),
        ]
    )
    db.add(BlingKitComponent(kit_bling_product_id=880303, component_bling_product_id=880101))
    await db.commit()


@pytest.fixture
def bling(monkeypatch):
    fake = FakeBling()

    async def _client(_session):
        return fake

    monkeypatch.setattr(dsr, "_get_bling_client", _client)
    return fake


@pytest.fixture
async def admin(db: AsyncSession, make_user, auth_as: Callable):
    u = await make_user(role=UserRole.ADMIN)
    u.name = "heisenberg"
    await db.commit()
    auth_as(u)
    return u


async def _entradas(db: AsyncSession) -> list[EstoqueEntrada]:
    return list(
        (
            await db.execute(
                select(EstoqueEntrada)
                .order_by(EstoqueEntrada.created_at)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


def _post(client: AsyncClient, sku: str, qtd: int, chave: str, obs: str | None = None):
    return client.post(
        URL,
        json={"sku": sku, "quantidade": qtd, "observacao": obs},
        headers={"Idempotency-Key": chave},
    )


async def test_entrada_ok_com_custo_e_observacao(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    r = await _post(client, "DG019.RA", 10, "chave-ent-0001", "entrada de mercadoria")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estado"] == "ok"
    assert body["texto"] == "Entrada lançada. O saldo atualiza em alguns minutos."
    assert (body["sku"], body["quantidade"]) == ("dg019.ra", 10)
    (lanc,) = bling.lancamentos
    assert (lanc["pid"], lanc["qtd"], lanc["op"], lanc["custo"]) == (880101, 10, "E", 812.5)
    assert lanc["repetir_503"] is False  # 503 do POST é dúvida, não repete
    (e,) = await _entradas(db)
    assert lanc["obs"] == f"entrada de mercadoria · Entrada DaVinci #{e.numero} · heisenberg"
    assert (e.status, e.criado_por, e.criado_por_nome) == ("ok", admin.id, "heisenberg")
    assert (e.observacao, e.custo, e.bling_lancamento_id) == (
        "entrada de mercadoria",
        Decimal("812.5"),
        9_990_001,
    )
    assert e.tentativa_at is not None and e.lancada_at is not None

    lista = (await client.get(f"{URL}?sku=DG019.RA&limite=50")).json()
    assert len(lista) == 1
    assert {
        k: lista[0][k] for k in ("sku", "quantidade", "observacao", "estado", "criado_por")
    } == {
        "sku": "dg019.ra",
        "quantidade": 10,
        "observacao": "entrada de mercadoria",
        "estado": "ok",
        "criado_por": "heisenberg",
    }
    assert lista[0]["id"] == body["id"] and lista[0]["criado_em"]
    assert (await client.get(f"{URL}?sku=z2819")).json() == []


async def test_mesma_chave_nunca_lanca_duas_vezes(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    r1 = await _post(client, "dg019.ra", 5, "chave-repetida-01")
    r2 = await _post(client, "dg019.ra", 5, "chave-repetida-01")
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert len(bling.lancamentos) == 1 and bling.tentativas == 1
    # A mesma chave com outro pedido: recusada, nada lançado.
    r3 = await _post(client, "dg019.ra", 6, "chave-repetida-01")
    assert r3.status_code == 409
    assert r3.json()["detail"]["code"] == "idempotency_conflito"
    # Sem chave: recusado.
    r4 = await client.post(URL, json={"sku": "dg019.ra", "quantidade": 1})
    assert r4.status_code == 422
    assert r4.json()["detail"]["code"] == "idempotency_key"
    assert len(bling.lancamentos) == 1
    assert len(await _entradas(db)) == 1


async def test_kit_recusado_com_a_mensagem_do_pedido(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    for i, sku in enumerate(("dg019.ra+a001.ra", "b011", "kitestrutura")):
        r = await _post(client, sku, 1, f"chave-kit-{i:04d}")
        assert r.status_code == 422, (sku, r.text)
        assert r.json()["detail"] == {"code": "kit", "message": KIT}
    assert bling.tentativas == 0
    assert await _entradas(db) == []


async def test_inativo_sem_bling_nao_encontrado_e_quantidade(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    casos = [
        ("dg020.ra", 1, "inativo"),
        ("dg021.ra", 1, "sem_bling"),
        ("nada999", 1, "nao_encontrado"),
        ("dg019.ra", 0, "quantidade"),
        ("dg019.ra", 100_001, "quantidade"),
    ]
    for i, (sku, qtd, code) in enumerate(casos):
        r = await _post(client, sku, qtd, f"chave-inv-{i:04d}")
        assert r.status_code == 422, (sku, r.text)
        assert r.json()["detail"]["code"] == code
        assert r.json()["detail"]["message"]
    assert bling.tentativas == 0
    assert await _entradas(db) == []


async def test_nao_admin_403(client: AsyncClient, make_user, auth_as: Callable, produtos, bling):
    auth_as(
        await make_user(
            role=UserRole.USER,
            permissions={"controle_estoque": {"view": True, "edit": True}},
        )
    )
    r = await _post(client, "dg019.ra", 1, "chave-nadmin-01")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "admin_only"
    assert (await client.get(URL)).status_code == 403
    assert bling.tentativas == 0


async def test_custo_do_bling_quando_o_davinci_nao_tem(client: AsyncClient, produtos, bling, admin):
    bling.custos = {"z2819": 37.9}
    r = await _post(client, "z2819", 3, "chave-custo-001")
    assert r.json()["estado"] == "ok"
    assert bling.lancamentos[0]["custo"] == 37.9
    assert bling.lancamentos[0]["obs"].startswith("Entrada DaVinci #")


async def test_recusa_certa_falhou_e_nao_repete(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    bling.falhas = [BlingCloudflareError("status=429")]
    r = await _post(client, "dg019.ra", 4, "chave-recusa-01")
    body = r.json()
    assert body["estado"] == "falhou"
    assert "nada entrou" in body["texto"]
    (e,) = await _entradas(db)
    assert e.status == "falhou"
    await svc.processar_pendentes(bling, agora=datetime.now(UTC) + timedelta(hours=1))
    assert bling.tentativas == 1  # o worker não relança recusada (nada entrou)


async def test_erro_depois_de_enviar_confere_no_extrato_e_nao_repete(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    """504 do POST: o Bling pode ter gravado. Nunca lança de novo às cegas: o
    worker procura o movimento no extrato a partir da tentativa."""
    bling.falhas = [_erro_post(504)]
    r = await _post(client, "dg019.ra", 7, "chave-incerta-01")
    body = r.json()
    assert body["estado"] == "enviando"  # "conferindo" na tela: não acabou, não incluir de novo
    assert "conferindo no extrato" in body["texto"]
    (e,) = await _entradas(db)
    assert e.status == "conferindo"
    t0 = e.tentativa_at
    # Movimento de ANTES da tentativa nunca é dela; outro produto/quantidade também não.
    db.add_all(
        [
            StockMovement(
                id=uuid4(),
                bling_product_id=880101,
                sku="dg019.ra",
                date=t0 - timedelta(seconds=40),
                tipo="E",
                quantidade=7,
            ),
            StockMovement(
                id=uuid4(),
                bling_product_id=880101,
                sku="dg019.ra",
                date=t0 + timedelta(seconds=10),
                tipo="E",
                quantidade=8,
            ),
        ]
    )
    await db.commit()
    out = await svc.processar_pendentes(bling, agora=t0 + timedelta(minutes=1))
    assert out["conferidas"] == 1 and out["confirmadas"] == 0
    (e,) = await _entradas(db)
    assert e.status == "conferindo"
    mov = StockMovement(
        id=uuid4(),
        bling_product_id=880101,
        sku="dg019.ra",
        date=t0 + timedelta(seconds=15),
        tipo="E",
        quantidade=7,
    )
    db.add(mov)
    await db.commit()
    out = await svc.processar_pendentes(bling, agora=t0 + timedelta(minutes=2))
    assert out["confirmadas"] == 1
    (e,) = await _entradas(db)
    assert (e.status, e.mov_ref) == ("ok", mov.id)
    assert bling.tentativas == 1 and bling.lancamentos == []  # nada relançado
    r = await _post(client, "dg019.ra", 7, "chave-incerta-01")
    assert r.json()["estado"] == "ok"
    assert bling.tentativas == 1


async def test_sem_achar_no_extrato_vira_sem_confirmacao_e_avisa_no_sino(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    bling.falhas = [httpx.ReadTimeout("timeout")]
    await _post(client, "dg019.ra", 2, "chave-semconf-01")
    (e,) = await _entradas(db)
    assert e.status == "conferindo"
    await svc.processar_pendentes(bling, agora=e.tentativa_at + timedelta(minutes=11))
    (e,) = await _entradas(db)
    assert e.status == "sem_confirmacao"
    assert f"Entrada DaVinci #{e.numero}" in e.erro
    lista = (await client.get(URL)).json()
    assert lista[0]["estado"] == "sem_confirmacao"
    avisos = (
        (await db.execute(select(Alert).where(Alert.dedupe_key.like(f"estoque_entrada:{e.id}:%"))))
        .scalars()
        .all()
    )
    assert len(avisos) == 1 and avisos[0].user_id == admin.id
    assert bling.tentativas == 1


async def test_enviando_parado_vai_para_o_extrato(db: AsyncSession, produtos, bling, admin):
    """O processo caiu no meio do POST ("enviando" parado): o worker confere no
    extrato, nunca lança de novo."""
    e, _ = await svc.criar(
        db,
        bling,
        idem_key="chave-orfa-env1",
        user_id=admin.id,
        user_nome="heisenberg",
        sku="dg019.ra",
        quantidade=3,
        observacao=None,
    )
    marca = await svc._marcar_enviando(e.id, (svc.PENDENTE,))
    out = await svc.processar_pendentes(bling, agora=marca + timedelta(minutes=6))
    assert out["conferidas"] == 1
    (e,) = await _entradas(db)
    assert e.status == "conferindo"
    assert bling.tentativas == 0


async def test_pendente_esquecida_o_worker_lanca_ou_encerra(
    db: AsyncSession, produtos, bling, admin
):
    """Gravada e não lançada (o processo caiu antes do POST — nada foi enviado):
    o worker lança depois de 1 min; passada de 30 min, ou com uma igual mais
    nova (a pessoa incluiu de novo), encerra como falhou e avisa."""
    agora = datetime.now(UTC)
    e1, _ = await svc.criar(
        db,
        bling,
        idem_key="chave-pend-0001",
        user_id=admin.id,
        user_nome="heisenberg",
        sku="dg019.ra",
        quantidade=3,
        observacao=None,
    )
    # Antes de 1 min: ninguém mexe (o clique ainda está lançando).
    await svc.processar_pendentes(bling, agora=agora)
    assert bling.tentativas == 0
    out = await svc.processar_pendentes(bling, agora=agora + timedelta(minutes=2))
    assert out["lancadas"] == 1
    assert [x["qtd"] for x in bling.lancamentos] == [3]
    e1 = (await _entradas(db))[0]
    assert e1.status == "ok"

    velha, _ = await svc.criar(
        db,
        bling,
        idem_key="chave-pend-0002",
        user_id=admin.id,
        user_nome="heisenberg",
        sku="z2819",
        quantidade=1,
        observacao=None,
    )
    repetida, _ = await svc.criar(
        db,
        bling,
        idem_key="chave-pend-0003",
        user_id=admin.id,
        user_nome="heisenberg",
        sku="dg019.ra",
        quantidade=9,
        observacao=None,
    )
    nova, _ = await svc.criar(
        db,
        bling,
        idem_key="chave-pend-0004",
        user_id=admin.id,
        user_nome="heisenberg",
        sku="dg019.ra",
        quantidade=9,
        observacao=None,
    )
    await db.execute(
        EstoqueEntrada.__table__.update()
        .where(EstoqueEntrada.id == velha.id)
        .values(created_at=agora - timedelta(minutes=40))
    )
    await db.execute(
        EstoqueEntrada.__table__.update()
        .where(EstoqueEntrada.id.in_((repetida.id, nova.id)))
        .values(created_at=EstoqueEntrada.created_at - timedelta(minutes=3))
    )
    await db.commit()
    out = await svc.processar_pendentes(bling, agora=datetime.now(UTC))
    assert out["paradas"] == 2 and out["lancadas"] == 1
    estados = {x.idem_key: (x.status, x.erro) for x in await _entradas(db)}
    assert estados["chave-pend-0002"][0] == "falhou"
    assert "nada entrou" in estados["chave-pend-0002"][1]
    assert estados["chave-pend-0003"][0] == "falhou"
    assert f"#{nova.numero}" in estados["chave-pend-0003"][1]
    assert estados["chave-pend-0004"][0] == "ok"
    assert [x["qtd"] for x in bling.lancamentos] == [3, 9]
    avisos = (
        (await db.execute(select(Alert).where(Alert.dedupe_key.like("estoque_entrada:%"))))
        .scalars()
        .all()
    )
    assert len(avisos) == 2


async def test_entrada_avulsa_nao_confirma_devolucao(db: AsyncSession, produtos):
    """Entrada do "Incluir" e entrada de devolução do mesmo produto e
    quantidade, juntas: um só movimento no extrato não confirma a devolução
    (pode ser da entrada avulsa)."""
    agora = datetime.now(UTC)
    db.add(
        EstoqueEntrada(
            idem_key="chave-cruzada-1",
            sku="dg019.ra",
            bling_product_id=880101,
            quantidade=1,
            status="ok",
            tentativa_at=agora,
        )
    )
    dev = Devolution(
        pedido_bling="999002",
        conta="Shopee",
        sku="dg019.ra",
        quantidade=1,
        estoque_status="conferindo",
        estoque_conferir_bling_id=880101,
        estoque_tentativa_at=agora,
    )
    db.add(dev)
    db.add(
        StockMovement(
            id=uuid4(),
            bling_product_id=880101,
            sku="dg019.ra",
            date=agora,
            tipo="E",
            quantidade=1,
        )
    )
    await db.commit()
    await dsr.conferir_entradas_pendentes(db, agora=agora + timedelta(minutes=1))
    await db.refresh(dev)
    assert dev.estoque_status == "conferindo"
