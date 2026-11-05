"""Controle de Estoque › "Transferir estoque" (Eduardo, 05/11/2026): troca a
loja interna "Centro de Distribuição" do Bling. Só admin; saída na origem
ANTES da entrada no destino; nunca lança duas vezes; confere o extrato quando
o Bling responde erro depois de receber."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Devolution,
    EstoqueTransferencia,
    EstoqueTransferenciaLinha,
    Product,
    UserRole,
)
from app.models.stock_movement import StockMovement
from app.services import devolution_stock_return as dsr
from app.services import estoque_transferencia as svc
from app.services.marketplaces.bling import BlingCloudflareError

pytestmark = pytest.mark.asyncio

URL = "/api/estoque/transferencias"


def _erro_post(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.bling.com.br/Api/v3/estoques")
    return httpx.HTTPStatusError(
        str(status), request=req, response=httpx.Response(status, request=req)
    )


class FakeBling:
    """Lança no "Bling" de mentira. `falhas` = fila de exceções por chamada
    (None = deu certo); `saldos` = id → (físico, virtual)."""

    def __init__(self, saldos: dict[int, tuple[int, int]]):
        self.saldos = saldos
        self.lancamentos: list[tuple[int, int, str, str | None, float | None]] = []
        self.falhas: list[Exception | None] = []
        # Saldos das próximas leituras, uma por GET (vazio = `saldos`).
        self.saldos_seguintes: list[dict[int, tuple[int, int]]] = []
        self.kw: list[dict] = []

    async def _request(self, method, path, *, params=None, json=None):
        assert (method, path) == ("GET", "/estoques/saldos")
        ids = [int(v) for k, v in params if k == "idsProdutos[]"]
        saldos = self.saldos_seguintes.pop(0) if self.saldos_seguintes else self.saldos
        data = [
            {
                "produto": {"id": i},
                "saldoFisicoTotal": saldos[i][0],
                "saldoVirtualTotal": saldos[i][1],
            }
            for i in ids
            if i in saldos
        ]
        return httpx.Response(200, json={"data": data}, request=httpx.Request(method, "http://x"))

    async def update_stock_by_id(self, pid, qty, *, operation, observacao=None, custo=None, **_kw):
        self.kw.append(dict(_kw))
        falha = self.falhas.pop(0) if self.falhas else None
        if falha is not None:
            raise falha
        self.lancamentos.append((int(pid), int(qty), operation, observacao, custo))
        return {}


@pytest.fixture
async def produtos(db: AsyncSession, make_user):
    dono = await make_user()
    db.add_all(
        [
            Product(
                user_id=dono.id,
                sku="a004.cd",
                name="Fone A004",
                situacao="A",
                formato="S",
                bling_product_id=870101,
                bling_cost_price=Decimal("65"),
                stock=1000,
            ),
            Product(
                user_id=dono.id,
                sku="a004.pi",
                name="Fone A004",
                situacao="A",
                formato="S",
                bling_product_id=870102,
                bling_cost_price=Decimal("65"),
            ),
            Product(
                user_id=dono.id,
                sku="uaf001m1.220",
                name="Air fryer 220",
                situacao="A",
                formato="S",
                bling_product_id=870201,
                bling_cost_price=Decimal("240"),
            ),
            Product(
                user_id=dono.id,
                sku="uaf001m1.110",
                name="Air fryer 110",
                situacao="A",
                formato="S",
                bling_product_id=870202,
                bling_cost_price=Decimal("250"),
            ),
            Product(
                user_id=dono.id,
                sku="dg053.sp+a001.sp",
                name="Kit",
                situacao="A",
                formato="E",
                bling_product_id=870301,
            ),
        ]
    )
    await db.commit()


@pytest.fixture(autouse=True)
async def _sem_movimentos_de_outros(db: AsyncSession):
    """stock_movements não é limpo entre testes: estes usam ids 870xxx só deles."""
    from sqlalchemy import delete

    await db.execute(
        delete(StockMovement).where(StockMovement.bling_product_id.between(870000, 870999))
    )
    await db.commit()
    yield
    await db.execute(
        delete(StockMovement).where(StockMovement.bling_product_id.between(870000, 870999))
    )
    await db.commit()


@pytest.fixture
def bling(monkeypatch):
    fake = FakeBling({870101: (1000, 1000), 870102: (0, 0), 870201: (5, 5), 870202: (0, 0)})

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


def _corpo(*linhas, obs="distribuição do CD"):
    return {
        "observacao": obs,
        "linhas": [{"origem": o, "destino": d, "quantidade": q} for o, d, q in linhas],
    }


async def _linhas(db: AsyncSession) -> list[EstoqueTransferenciaLinha]:
    return list(
        (
            await db.execute(
                select(EstoqueTransferenciaLinha)
                .order_by(EstoqueTransferenciaLinha.created_at, EstoqueTransferenciaLinha.ordem)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def test_so_admin(client: AsyncClient, make_user, auth_as: Callable, produtos, bling):
    auth_as(
        await make_user(
            role=UserRole.USER,
            permissions={"controle_estoque": {"view": True, "edit": True}},
        )
    )
    assert (await client.get(f"{URL}/produtos?q=a004")).status_code == 403
    assert (await client.get(URL)).status_code == 403
    assert (
        await client.post(f"{URL}/conferir", json=_corpo(("a004.cd", "a004.pi", 1)))
    ).status_code == 403
    r = await client.post(
        URL, json=_corpo(("a004.cd", "a004.pi", 1)), headers={"Idempotency-Key": "chave-12345"}
    )
    assert r.status_code == 403
    assert bling.lancamentos == []


async def test_saida_antes_da_entrada_com_custo_do_destino(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    r = await client.post(
        URL, json=_corpo(("a004.cd", "a004.pi", 200)), headers={"Idempotency-Key": "chave-00000001"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estado"] == "ok"
    assert body["linhas"][0]["estado"] == "ok"
    assert [(p, q, op) for p, q, op, _o, _c in bling.lancamentos] == [
        (870101, 200, "S"),
        (870102, 200, "E"),
    ]
    # Saída sem custo; entrada com o custo do cadastro do destino.
    assert bling.lancamentos[0][4] is None
    assert bling.lancamentos[1][4] == 65.0
    assert (
        f"Transferência DaVinci #{body['numero']}: a004.cd -> a004.pi (200)"
        in bling.lancamentos[0][3]
    )
    assert "heisenberg" in bling.lancamentos[0][3]
    t = (await db.execute(select(EstoqueTransferencia))).scalar_one()
    assert t.criado_por_nome == "heisenberg"
    assert t.observacao == "distribuição do CD"


async def test_troca_de_produto_avisada_e_custo_do_destino(
    client: AsyncClient, produtos, bling, admin
):
    r = await client.post(f"{URL}/conferir", json=_corpo(("uaf001m1.220", "uaf001m1.110", 2)))
    assert r.status_code == 200
    linha = r.json()["linhas"][0]
    assert linha["erros"] == []
    assert linha["troca_produto"] is True
    assert linha["custo"] == 250.0
    assert bling.lancamentos == []  # conferir não lança nada


async def test_mesma_chave_nao_lanca_duas_vezes(client: AsyncClient, produtos, bling, admin):
    h = {"Idempotency-Key": "chave-repetida-1"}
    r1 = await client.post(URL, json=_corpo(("a004.cd", "a004.pi", 5)), headers=h)
    r2 = await client.post(URL, json=_corpo(("a004.cd", "a004.pi", 5)), headers=h)
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["numero"] == r2.json()["numero"]
    assert len(bling.lancamentos) == 2
    assert (await client.post(URL, json=_corpo(("a004.cd", "a004.pi", 5)))).status_code == 422


async def test_saldo_disponivel_barra_e_soma_linhas_da_mesma_origem(
    client: AsyncClient, produtos, bling, admin
):
    bling.saldos[870101] = (200, 150)  # 50 reservados para pedido em aberto
    r = await client.post(
        URL,
        json=_corpo(("a004.cd", "a004.pi", 100), ("a004.cd", "a004.pi", 60)),
        headers={"Idempotency-Key": "chave-saldo-01"},
    )
    assert r.status_code == 422
    linhas = r.json()["detail"]["linhas"]
    assert "a004.cd tem 150 disponível no Bling (físico 200" in linhas[0]["erros"][0]
    assert "pede 160" in linhas[0]["erros"][0]
    assert bling.lancamentos == []


async def test_kit_inexistente_e_mesmo_sku(client: AsyncClient, produtos, bling, admin):
    r = await client.post(
        f"{URL}/conferir",
        json=_corpo(
            ("dg053.sp+a001.sp", "a004.pi", 1),
            ("a004.cd", "dg010.ci", 1),
            ("a004.cd", "A004.CD ", 1),
            ("a004.cd", "a004.pi", 0),
        ),
    )
    erros = [x["erros"] for x in r.json()["linhas"]]
    assert "kit" in erros[0][0]
    assert "dg010.ci (destino) não existe no Bling" in erros[1][0]
    assert erros[2] == ["Origem e destino são o mesmo SKU."]
    assert "Quantidade" in erros[3][0]


async def test_saida_recusada_nao_lanca_entrada(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    bling.falhas = [BlingCloudflareError("status=429")]
    r = await client.post(
        URL, json=_corpo(("a004.cd", "a004.pi", 10)), headers={"Idempotency-Key": "chave-recusa-01"}
    )
    assert r.json()["linhas"][0]["estado"] == "falhou"
    assert "nada mudou" in r.json()["linhas"][0]["texto"]
    assert bling.lancamentos == []
    (linha,) = await _linhas(db)
    assert (linha.saida_status, linha.entrada_status) == ("falhou", "aguardando")
    # O worker não mexe em saída recusada (nada saiu): fica para a pessoa.
    await svc.processar_pendentes(bling, agora=datetime.now(UTC) + timedelta(hours=1))
    assert bling.lancamentos == []


async def test_entrada_recusada_o_worker_tenta_de_novo(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    bling.falhas = [None, BlingCloudflareError("status=429")]
    r = await client.post(
        URL,
        json=_corpo(("a004.cd", "a004.pi", 10)),
        headers={"Idempotency-Key": "chave-entrada-01"},
    )
    assert r.json()["linhas"][0]["estado"] == "andamento"
    assert [op for _p, _q, op, _o, _c in bling.lancamentos] == ["S"]
    (linha,) = await _linhas(db)
    assert (linha.saida_status, linha.entrada_status, linha.entrada_auto_tentativas) == (
        "ok",
        "falhou",
        1,
    )
    # Antes do intervalo: não tenta.
    await svc.processar_pendentes(bling, agora=datetime.now(UTC))
    assert len(bling.lancamentos) == 1
    out = await svc.processar_pendentes(bling, agora=datetime.now(UTC) + timedelta(minutes=5))
    assert out["entradas"] == 1
    assert [op for _p, _q, op, _o, _c in bling.lancamentos] == ["S", "E"]
    (linha,) = await _linhas(db)
    assert linha.entrada_status == "ok"


async def test_saida_incerta_vai_para_a_pessoa_mesmo_com_movimento_igual(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    """O espelho do extrato não traz a observação: uma venda do mesmo SKU e
    quantidade não pode "confirmar" a saída (a entrada sairia sem a saída —
    peça sobrando). Saída em dúvida vai direto para a pessoa."""
    bling.falhas = [_erro_post(504)]
    r = await client.post(
        URL, json=_corpo(("a004.cd", "a004.pi", 1)), headers={"Idempotency-Key": "chave-incerta-1"}
    )
    linha_out = r.json()["linhas"][0]
    assert linha_out["estado"] == "atencao"
    assert "Transferência DaVinci #" in linha_out["texto"]
    assert linha_out["precisa_conferir"] is True and linha_out["pode_ja_lancou"] is True
    assert bling.lancamentos == []
    (linha,) = await _linhas(db)
    assert linha.saida_status == "sem_confirmacao"
    # Uma venda (S 1 do mesmo SKU) cai no espelho logo depois: nada muda.
    db.add(
        StockMovement(
            id=uuid4(),
            bling_product_id=870101,
            sku="a004.cd",
            date=linha.saida_tentativa_at + timedelta(seconds=20),
            tipo="S",
            quantidade=1,
        )
    )
    await db.commit()
    await svc.processar_pendentes(bling, agora=datetime.now(UTC) + timedelta(minutes=20))
    assert bling.lancamentos == []
    (linha,) = await _linhas(db)
    assert (linha.saida_status, linha.entrada_status) == ("sem_confirmacao", "aguardando")
    # A pessoa viu "Transferência DaVinci #N" no extrato do Bling: segue a entrada.
    r = await client.post(
        f"{URL}/linhas/{linha.id}/resolver",
        json={
            "acao": "ja_lancou",
            "conferi_extrato": True,
            "tentativa": linha_out["tentativa"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["linhas"][0]["estado"] == "ok"
    assert [(p, op) for p, _q, op, _o, _c in bling.lancamentos] == [(870102, "E")]


async def test_sem_confirmacao_pede_conferir_o_extrato_e_a_tentativa_atual(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    bling.falhas = [_erro_post(503)]  # 503 do POST não é repetido: é dúvida
    r = await client.post(
        URL, json=_corpo(("a004.cd", "a004.pi", 3)), headers={"Idempotency-Key": "chave-semconf-1"}
    )
    assert bling.kw[-1]["repetir_503"] is False
    (linha,) = await _linhas(db)
    assert linha.saida_status == "sem_confirmacao"
    lista = (await client.get(URL)).json()
    vista = lista[0]["linhas"][0]
    assert vista["estado"] == "atencao"
    assert vista["precisa_conferir"] is True
    r = await client.post(f"{URL}/linhas/{linha.id}/resolver", json={"acao": "tentar_de_novo"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "conferir_extrato"
    # Aba antiga (outra tentativa): recusado.
    r = await client.post(
        f"{URL}/linhas/{linha.id}/resolver",
        json={
            "acao": "tentar_de_novo",
            "conferi_extrato": True,
            "tentativa": "2026-01-01T00:00:00+00:00",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "linha_mudou"
    assert bling.lancamentos == []
    r = await client.post(
        f"{URL}/linhas/{linha.id}/resolver",
        json={"acao": "tentar_de_novo", "conferi_extrato": True, "tentativa": vista["tentativa"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["linhas"][0]["estado"] == "ok"
    assert [op for _p, _q, op, _o, _c in bling.lancamentos] == ["S", "E"]


async def test_ja_lancou_marca_ok_sem_lancar(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    bling.falhas = [None, _erro_post(504)]
    await client.post(
        URL, json=_corpo(("a004.cd", "a004.pi", 4)), headers={"Idempotency-Key": "chave-jalancou1"}
    )
    (linha,) = await _linhas(db)
    assert (linha.saida_status, linha.entrada_status) == ("ok", "conferindo")
    await svc.processar_pendentes(bling, agora=datetime.now(UTC) + timedelta(minutes=11))
    (linha,) = await _linhas(db)
    assert (linha.saida_status, linha.entrada_status) == ("ok", "sem_confirmacao")
    r = await client.post(
        f"{URL}/linhas/{linha.id}/resolver", json={"acao": "ja_lancou", "conferi_extrato": True}
    )
    assert r.status_code == 200
    assert r.json()["linhas"][0]["estado"] == "ok"
    assert len(bling.lancamentos) == 1  # só a saída de antes
    (linha,) = await _linhas(db)
    assert "por heisenberg" in linha.resolvido_manual


async def test_entrada_incerta_so_confirma_com_movimento_depois_da_tentativa(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    bling.falhas = [None, _erro_post(504)]
    await client.post(
        URL, json=_corpo(("a004.cd", "a004.pi", 9)), headers={"Idempotency-Key": "chave-entr-conf"}
    )
    (linha,) = await _linhas(db)
    t0 = linha.entrada_tentativa_at
    # Movimento de ANTES da tentativa nunca é dela.
    db.add(
        StockMovement(
            id=uuid4(),
            bling_product_id=870102,
            sku="a004.pi",
            date=t0 - timedelta(seconds=40),
            tipo="E",
            quantidade=9,
        )
    )
    await db.commit()
    await svc.processar_pendentes(bling, agora=t0 + timedelta(minutes=1))
    (linha,) = await _linhas(db)
    assert linha.entrada_status == "conferindo"
    mov = StockMovement(
        id=uuid4(),
        bling_product_id=870102,
        sku="a004.pi",
        date=t0 + timedelta(seconds=15),
        tipo="E",
        quantidade=9,
    )
    db.add(mov)
    await db.commit()
    out = await svc.processar_pendentes(bling, agora=t0 + timedelta(minutes=2))
    assert out["confirmadas"] == 1
    (linha,) = await _linhas(db)
    assert (linha.entrada_status, linha.entrada_mov_ref) == ("ok", mov.id)
    assert len(bling.lancamentos) == 1  # nada relançado


async def test_saldo_conferido_de_novo_na_hora_de_lancar(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    """Entre o "Conferir" e a saída o saldo caiu (venda, outra transferência):
    a saída é recusada e nada sai."""
    bling.saldos_seguintes = [{870101: (1000, 1000)}, {870101: (5, 5)}]
    r = await client.post(
        URL, json=_corpo(("a004.cd", "a004.pi", 10)), headers={"Idempotency-Key": "chave-saldo-02"}
    )
    linha_out = r.json()["linhas"][0]
    assert linha_out["estado"] == "falhou"
    assert "Na hora de lançar, a004.cd tinha 5 disponível" in linha_out["texto"]
    assert bling.lancamentos == []
    # "Tentar de novo" também confere o saldo: agora tem.
    (linha,) = await _linhas(db)
    r = await client.post(f"{URL}/linhas/{linha.id}/resolver", json={"acao": "tentar_de_novo"})
    assert r.status_code == 200
    assert r.json()["linhas"][0]["estado"] == "ok"
    assert [op for _p, _q, op, _o, _c in bling.lancamentos] == ["S", "E"]


async def test_ja_lancou_nao_vale_para_saida_recusada(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    bling.falhas = [BlingCloudflareError("status=429")]
    r = await client.post(
        URL, json=_corpo(("a004.cd", "a004.pi", 2)), headers={"Idempotency-Key": "chave-jl-recus"}
    )
    assert r.json()["linhas"][0]["pode_ja_lancou"] is False
    (linha,) = await _linhas(db)
    r = await client.post(
        f"{URL}/linhas/{linha.id}/resolver", json={"acao": "ja_lancou", "conferi_extrato": True}
    )
    assert r.status_code == 409
    assert bling.lancamentos == []


async def test_entrada_recusada_de_vez_ja_lancei_no_bling(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    bling.falhas = [None] + [BlingCloudflareError("status=429")] * 10
    await client.post(
        URL, json=_corpo(("a004.cd", "a004.pi", 6)), headers={"Idempotency-Key": "chave-esgotada"}
    )
    agora = datetime.now(UTC)
    for k in range(1, 8):
        await svc.processar_pendentes(bling, agora=agora + timedelta(hours=k))
    (linha,) = await _linhas(db)
    assert (linha.entrada_status, linha.entrada_auto_tentativas) == ("falhou", svc.ENTRADA_AUTO_MAX)
    vista = (await client.get(URL)).json()[0]["linhas"][0]
    assert vista["estado"] == "atencao" and vista["pode_ja_lancou"] is True
    bling.falhas = []
    r = await client.post(
        f"{URL}/linhas/{linha.id}/resolver",
        json={"acao": "ja_lancou", "conferi_extrato": True, "tentativa": vista["tentativa"]},
    )
    assert r.status_code == 200
    assert r.json()["linhas"][0]["estado"] == "ok"
    assert [op for _p, _q, op, _o, _c in bling.lancamentos] == ["S"]  # só a saída


async def test_saida_que_nunca_saiu_vira_recusada_e_aparece_na_lista(
    client: AsyncClient, db: AsyncSession, produtos, bling, admin
):
    """O processo caiu entre gravar a transferência e lançar: depois de 10 min
    o worker a marca como recusada (nada saiu), com "Tentar de novo". Mais
    antiga que o limite da lista, continua aparecendo por estar parada."""
    t, _ = await svc.criar(
        db,
        bling,
        idem_key="chave-orfa-001",
        user_id=admin.id,
        user_nome="heisenberg",
        observacao=None,
        linhas=[{"origem": "a004.cd", "destino": "a004.pi", "quantidade": 3}],
    )
    out = await svc.processar_pendentes(bling, agora=datetime.now(UTC) + timedelta(minutes=11))
    assert out["paradas"] == 1
    for k in range(3):  # 3 transferências mais novas, já resolvidas
        await client.post(
            URL,
            json=_corpo(("a004.cd", "a004.pi", 1)),
            headers={"Idempotency-Key": f"chave-nova-{k:04d}"},
        )
    lista = (await client.get(f"{URL}?limite=1")).json()
    numeros = [x["numero"] for x in lista]
    assert t.numero in numeros and len(numeros) == 2
    orfa = next(x for x in lista if x["numero"] == t.numero)["linhas"][0]
    assert orfa["estado"] == "falhou" and orfa["pode_resolver"] is True


async def test_resposta_atrasada_nao_volta_o_estado(db: AsyncSession, produtos, bling, admin):
    t, _ = await svc.criar(
        db,
        bling,
        idem_key="chave-atrasada",
        user_id=admin.id,
        user_nome="heisenberg",
        observacao=None,
        linhas=[{"origem": "a004.cd", "destino": "a004.pi", "quantidade": 3}],
    )
    (linha,) = await _linhas(db)
    marca = await svc._marcar_enviando(linha.id, "saida", (svc.PENDENTE,))
    # O worker já tirou a perna de "enviando" (foi para a pessoa conferir).
    await svc._gravar_se(linha.id, "saida", de=(svc.ENVIANDO,), saida_status=svc.SEM_CONFIRMACAO)
    gravou = await svc._gravar_se(
        linha.id,
        "saida",
        de=(svc.ENVIANDO,),
        marca=marca,
        saida_status=svc.OK,
        entrada_status=svc.PENDENTE,
    )
    assert gravou is False
    (linha,) = await _linhas(db)
    assert (linha.saida_status, linha.entrada_status) == ("sem_confirmacao", "aguardando")


async def test_bling_nao_repete_post_503_quando_pedido(monkeypatch):
    from app.services.marketplaces import bling as bling_mod

    chamadas = []

    class _Http:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kw):
            chamadas.append(method)
            return httpx.Response(503, request=httpx.Request(method, url))

    async def _slot(*_a, **_kw):
        return None

    monkeypatch.setattr(bling_mod.httpx, "AsyncClient", _Http)
    monkeypatch.setattr(bling_mod, "_acquire_bling_rate_slot", _slot)
    c = bling_mod.BlingClient({"access_token": "x", "expires_at": 4102444800})
    r = await c._request("POST", "/estoques", json={}, repetir_503=False)
    assert r.status_code == 503 and chamadas == ["POST"]


async def test_entrada_da_transferencia_nao_confirma_devolucao(db: AsyncSession, produtos):
    """Entrada de transferência e entrada de devolução do mesmo produto e
    quantidade, juntas: um só movimento no extrato não confirma a devolução
    (pode ser da transferência)."""
    agora = datetime.now(UTC)
    t = EstoqueTransferencia(idem_key="x" * 10)
    db.add(t)
    await db.flush()
    db.add(
        EstoqueTransferenciaLinha(
            transferencia_id=t.id,
            origem_sku="a004.cd",
            origem_bling_id=870101,
            destino_sku="a004.pi",
            destino_bling_id=870102,
            quantidade=1,
            saida_status="ok",
            entrada_status="ok",
            entrada_tentativa_at=agora,
        )
    )
    dev = Devolution(
        pedido_bling="999001",
        conta="Shopee",
        sku="a004.pi",
        quantidade=1,
        estoque_status="conferindo",
        estoque_conferir_bling_id=870102,
        estoque_tentativa_at=agora,
    )
    db.add(dev)
    db.add(
        StockMovement(
            id=uuid4(),
            bling_product_id=870102,
            sku="a004.pi",
            date=agora,
            tipo="E",
            quantidade=1,
        )
    )
    await db.commit()
    await dsr.conferir_entradas_pendentes(db, agora=agora + timedelta(minutes=1))
    await db.refresh(dev)
    assert dev.estoque_status == "conferindo"
