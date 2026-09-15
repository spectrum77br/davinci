"""Robô de prioridade — compensação de estoque nos KITS (Eduardo, 14/09).

O Bling aceita a troca do kit no pedido, mas na baixa da NF continua
descontando a composição antiga (comprovado no extrato: dg053.ci caindo com
os pedidos já em .sp). Produto simples ele baixa certo. Então, ao trocar um
kit, o robô lança ENTRADA nos componentes antigos e SAÍDA nos novos (POST
/estoques), com registro em transação própria antes de cada lançamento.
Cobre:
  * kit: E nos antigos e S nos novos, em ordem, qtd × multiplicidade, linhas
    `ok` com lancado_at; pedaço sem tag não gera movimento; simples: nada;
    dois kits do mesmo pedido com componente em comum somam;
  * maiúsculas/espaços; componente sem cadastro → só a partir dele a fila
    para (os anteriores são lançados);
  * Bling responde erro (HTTP ou Cloudflare/429) → `falhou` (retentável);
    rede cai → `incerto`;
  * trava: segunda passada no mesmo pedido não lança de novo (índice único);
  * sweep: retenta `falhou` em ordem e para se a linha anterior não está ok;
    pula pedido morto; resolve produto que faltava; estorna só cancelado/
    excluído que não saiu (carimbo antes do POST; recusa limpa, timeout
    mantém e marca incerto); órfão vira incerto; aviso Threema só se alguém
    recebeu, uma vez por linha.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Logistica,
    MargemAudit,
    PrioridadeEstoqueMovimento,
    StockMovement,
)
from app.services import nf_emissao_gerar
from app.services import prioridade_estoque as prio
from app.services import prioridade_estoque_movimentos as mov
from app.services.marketplaces.bling import BlingCloudflareError

IDS = {
    "dg053.ci": 101, "a001.ci": 102, "dg053.sp": 201, "a001.sp": 202,
    "dg053.ci+a001.ci": 900, "dg053.sp+a001.sp": 901, "dg019.ra": 301, "dg019.sp": 302,
    "dg053.ci+brinde": 910, "dg053.sp+brinde": 911, "brinde": 500,
    "dg053.ci+dg053.ci": 920, "dg053.sp+dg053.sp": 921,
    "dg053.ci+x999.ci": 930, "dg053.sp+x999.sp": 931, "x999.ci": 401,  # x999.sp não existe
    "b002.ci": 103, "b002.sp": 203, "dg053.ci+b002.ci": 940, "dg053.sp+b002.sp": 941,
}


def _http_error(status: int = 400) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://bling/estoques")
    resp = httpx.Response(status, request=req, text="erro bling")
    return httpx.HTTPStatusError("erro", request=req, response=resp)


class FakeBling:
    def __init__(self, *, falha: dict | None = None, existe: dict | None = None):
        self.movs: list[tuple[str, int, int, str]] = []  # (operacao, produto_id, qtd, obs)
        self.calls: list[str] = []
        self.falha = falha or {}  # sku -> exceção a levantar no POST (uma vez)
        self.ids = dict(IDS, **(existe or {}))
        self._itens: list[tuple[str, int]] = []

    async def find_active_product_by_sku(self, sku):
        self.calls.append(f"find:{sku}")
        pid = self.ids.get(sku.lower())
        return {"id": pid, "sku": sku, "name": sku, "stock": 50} if pid else None

    async def get_order(self, bling_id):
        self.calls.append("get_order")
        return {"id": bling_id, "numero": self._numero, "situacao": {"id": 6},
                "contato": {"id": 1}, "loja": {"id": 2}, "observacoes": "",
                "itens": [{"codigo": c, "quantidade": q, "produto": {"id": 1}}
                          for c, q in self._itens]}

    async def update_order(self, bling_id, body):
        self.calls.append("put")
        return body

    async def update_stock_by_id(self, pid, qty, *, operation, observacao=None, **kw):
        sku = next((s for s, i in self.ids.items() if i == pid), "?")
        exc = self.falha.get(sku)
        if exc is not None:
            self.falha.pop(sku)
            raise exc
        self.movs.append((operation, pid, qty, observacao or ""))
        return {"id": 1}


async def _pedido(
    db: AsyncSession, numero: str, itens: list[tuple[str, int]], situacao: str = "6"
) -> None:
    for i, (codigo, qtd) in enumerate(itens):
        db.add(BlingOrder(numero=numero, bling_id=int(numero), loja="5001", item_index=i,
                          item_codigo=codigo, item_quantidade=qtd, situacao=situacao,
                          data=datetime.now(UTC)))
    await db.commit()


def _arma(monkeypatch, client: FakeBling, numero: str, itens: list[tuple[str, int]]) -> None:
    client._numero, client._itens = numero, itens

    async def _mapa(session):
        return {"dg053": "sp", "dg019": "sp", "x999": "sp", "b002": "sp"}

    async def _client(session):
        return client

    monkeypatch.setattr(prio, "_mapa_prioridades", _mapa)
    monkeypatch.setattr(nf_emissao_gerar, "_bling_client_opt", _client)


async def _linhas(db: AsyncSession, numero: str) -> list[PrioridadeEstoqueMovimento]:
    db.expire_all()
    return list((await db.execute(
        select(PrioridadeEstoqueMovimento)
        .where(PrioridadeEstoqueMovimento.pedido_bling == numero)
        .order_by(PrioridadeEstoqueMovimento.ordem)
    )).scalars().all())


def _ops(client: FakeBling) -> list[tuple[str, int, int]]:
    return [(m[0], m[1], m[2]) for m in client.movs]


@pytest.mark.asyncio
async def test_kit_entrada_nos_antigos_e_saida_nos_novos(db: AsyncSession, monkeypatch):
    await _pedido(db, "297155", [("dg053.ci+a001.ci", 2)])
    client = FakeBling()
    _arma(monkeypatch, client, "297155", [("dg053.ci+a001.ci", 2)])

    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["297155"])
    await db.commit()

    assert resumo["trocados"] == 1
    assert resumo["estoque_movimentos"] == 4 and resumo["estoque_falhas"] == 0
    assert _ops(client) == [("E", 102, 2), ("E", 101, 2), ("S", 202, 2), ("S", 201, 2)]
    assert all("pedido 297155" in m[3] and "dg053.ci+a001.ci -> dg053.sp+a001.sp" in m[3]
               for m in client.movs)
    regs = await _linhas(db, "297155")
    assert [(r.sku, r.operacao, r.quantidade, r.bling_product_id, r.status) for r in regs] == [
        ("a001.ci", "E", 2, 102, "ok"), ("dg053.ci", "E", 2, 101, "ok"),
        ("a001.sp", "S", 2, 202, "ok"), ("dg053.sp", "S", 2, 201, "ok"),
    ]
    assert all(r.lancado_at is not None and r.revertido_at is None and r.tentativas == 1
               for r in regs)


@pytest.mark.asyncio
async def test_dois_kits_do_pedido_somam_o_componente_em_comum(db: AsyncSession, monkeypatch):
    itens = [("dg053.ci+a001.ci", 1), ("dg053.ci+b002.ci", 2)]
    await _pedido(db, "297160", itens)
    client = FakeBling()
    _arma(monkeypatch, client, "297160", itens)
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["297160"])
    assert resumo["trocados"] == 2 and resumo["estoque_movimentos"] == 6
    # dg053.ci aparece nos dois kits: 1 + 2 = 3, num lançamento só.
    assert _ops(client) == [
        ("E", 102, 1), ("E", 103, 2), ("E", 101, 3), ("S", 202, 1), ("S", 203, 2), ("S", 201, 3),
    ]


@pytest.mark.asyncio
async def test_pedaco_sem_tag_maiusculas_e_multiplicidade(db: AsyncSession, monkeypatch):
    await _pedido(db, "297200", [("DG053.CI + Brinde", 1)])
    client = FakeBling()
    _arma(monkeypatch, client, "297200", [("DG053.CI + Brinde", 1)])
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["297200"])
    assert resumo["trocados"] == 1 and resumo["estoque_movimentos"] == 2
    assert _ops(client) == [("E", 101, 1), ("S", 201, 1)]

    await _pedido(db, "297201", [("dg053.ci+dg053.ci", 3)])
    client2 = FakeBling()
    _arma(monkeypatch, client2, "297201", [("dg053.ci+dg053.ci", 3)])
    await prio.aplicar_prioridade_estoque(db, numeros=["297201"])
    assert _ops(client2) == [("E", 101, 6), ("S", 201, 6)]


@pytest.mark.asyncio
async def test_produto_simples_nao_mexe_no_estoque(db: AsyncSession, monkeypatch):
    await _pedido(db, "297154", [("dg019.ra", 1)])
    client = FakeBling()
    _arma(monkeypatch, client, "297154", [("dg019.ra", 1)])
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["297154"])
    assert resumo["trocados"] == 1 and resumo["estoque_movimentos"] == 0
    assert client.movs == [] and await _linhas(db, "297154") == []


@pytest.mark.asyncio
async def test_componente_sem_cadastro_para_a_fila_a_partir_dele(db: AsyncSession, monkeypatch):
    # Plano: E dg053.ci, E x999.ci, S dg053.sp, S x999.sp (x999.sp não existe).
    await _pedido(db, "297300", [("dg053.ci+x999.ci", 1)])
    client = FakeBling()
    _arma(monkeypatch, client, "297300", [("dg053.ci+x999.ci", 1)])
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["297300"])
    assert resumo["estoque_movimentos"] == 3 and resumo["estoque_falhas"] == 1
    assert _ops(client) == [("E", 101, 1), ("E", 401, 1), ("S", 201, 1)]
    regs = await _linhas(db, "297300")
    assert [(r.sku, r.status) for r in regs] == [
        ("dg053.ci", "ok"), ("x999.ci", "ok"), ("dg053.sp", "ok"), ("x999.sp", "falhou"),
    ]
    assert regs[3].bling_product_id is None and "não encontrado" in regs[3].erro


@pytest.mark.asyncio
async def test_bling_recusa_vira_falhou_e_rede_vira_incerto(db: AsyncSession, monkeypatch):
    await _pedido(db, "297400", [("dg053.ci+a001.ci", 1)])
    client = FakeBling(falha={"dg053.ci": _http_error(400)})
    _arma(monkeypatch, client, "297400", [("dg053.ci+a001.ci", 1)])
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["297400"])
    # E a001.ci ok; E dg053.ci recusado → S nem tentados.
    assert resumo["estoque_movimentos"] == 1 and resumo["estoque_falhas"] == 3
    assert _ops(client) == [("E", 102, 1)]
    regs = await _linhas(db, "297400")
    assert [(r.sku, r.status) for r in regs] == [
        ("a001.ci", "ok"), ("dg053.ci", "falhou"), ("a001.sp", "falhou"), ("dg053.sp", "falhou"),
    ]
    assert "HTTP 400" in regs[1].erro and regs[1].tentativas == 1
    assert regs[2].erro == "aguardando lançamento anterior"

    # 429/Cloudflare (Bling respondeu) também é retentável.
    await _pedido(db, "297401", [("dg053.ci+a001.ci", 1)])
    client2 = FakeBling(falha={"a001.ci": BlingCloudflareError("status=429")})
    _arma(monkeypatch, client2, "297401", [("dg053.ci+a001.ci", 1)])
    await prio.aplicar_prioridade_estoque(db, numeros=["297401"])
    regs = await _linhas(db, "297401")
    assert regs[0].status == "falhou" and "BlingCloudflareError" in regs[0].erro

    # Rede/timeout: não se sabe se entrou → incerto (nunca retenta sozinho).
    await _pedido(db, "297402", [("dg053.ci+a001.ci", 1)])
    client3 = FakeBling(falha={"a001.ci": httpx.ConnectTimeout("timeout")})
    _arma(monkeypatch, client3, "297402", [("dg053.ci+a001.ci", 1)])
    await prio.aplicar_prioridade_estoque(db, numeros=["297402"])
    regs = await _linhas(db, "297402")
    assert [(r.sku, r.status) for r in regs] == [
        ("a001.ci", "incerto"), ("dg053.ci", "falhou"),
        ("a001.sp", "falhou"), ("dg053.sp", "falhou"),
    ]


@pytest.mark.asyncio
async def test_trava_nao_lanca_duas_vezes(db: AsyncSession, monkeypatch):
    client = FakeBling()
    cache: dict = {}
    trocas = [("dg053.ci+a001.ci", "dg053.sp+a001.sp", 1)]
    r1 = await mov.compensar_estoque_kits(client, numero="297500", bling_id=297500,
                                          trocas=trocas, cache=cache)
    r2 = await mov.compensar_estoque_kits(client, numero="297500", bling_id=297500,
                                          trocas=trocas, cache=cache)
    assert r1 == {"ok": 4, "falhas": 0} and r2 == {"ok": 0, "falhas": 0}
    assert len(client.movs) == 4 and len(await _linhas(db, "297500")) == 4
    assert client.calls.count("find:dg053.ci") == 1  # cache do tick


@pytest.mark.asyncio
async def test_sweep_retenta_em_ordem_e_para_se_anterior_nao_esta_ok(db, monkeypatch):
    await _pedido(db, "297600", [("dg053.ci+a001.ci", 1)])
    client = FakeBling(falha={"dg053.ci": _http_error(400)})
    _arma(monkeypatch, client, "297600", [("dg053.ci+a001.ci", 1)])
    await prio.aplicar_prioridade_estoque(db, numeros=["297600"])
    assert _ops(client) == [("E", 102, 1)]

    resumo = await mov.retentar_falhas(client, db)
    assert resumo == {"retentados": 3, "falhas": 0}
    assert _ops(client) == [("E", 102, 1), ("E", 101, 1), ("S", 202, 1), ("S", 201, 1)]
    assert all(r.status == "ok" for r in await _linhas(db, "297600"))
    assert await mov.retentar_falhas(client, db) == {"retentados": 0, "falhas": 0}

    # Linha anterior `incerto`: as seguintes NÃO são lançadas (S sem E, nunca).
    await _pedido(db, "297601", [("dg053.ci+a001.ci", 1)])
    client2 = FakeBling(falha={"a001.ci": httpx.ConnectTimeout("t")})
    _arma(monkeypatch, client2, "297601", [("dg053.ci+a001.ci", 1)])
    await prio.aplicar_prioridade_estoque(db, numeros=["297601"])
    assert await mov.retentar_falhas(client2, db) == {"retentados": 0, "falhas": 0}
    assert client2.movs == []

    # Pedido morto: retry não mexe.
    await _pedido(db, "297602", [("dg053.ci+a001.ci", 1)], situacao="12")
    db.add(PrioridadeEstoqueMovimento(pedido_bling="297602", sku="dg053.ci", bling_product_id=101,
                                      operacao="E", quantidade=1, ordem=0, status="falhou"))
    await db.commit()
    client3 = FakeBling()
    assert await mov.retentar_falhas(client3, db) == {"retentados": 0, "falhas": 0}
    assert client3.movs == []


@pytest.mark.asyncio
async def test_sweep_resolve_produto_que_faltava(db: AsyncSession, monkeypatch):
    await _pedido(db, "297650", [("dg053.ci+x999.ci", 1)])
    client = FakeBling()
    _arma(monkeypatch, client, "297650", [("dg053.ci+x999.ci", 1)])
    await prio.aplicar_prioridade_estoque(db, numeros=["297650"])
    # x999.sp continua sem cadastro → só conta tentativa.
    assert await mov.retentar_falhas(client, db) == {"retentados": 0, "falhas": 1}
    regs = await _linhas(db, "297650")
    assert regs[3].status == "falhou" and regs[3].tentativas == 1
    # Produto cadastrado → lança e fica ok.
    client.ids["x999.sp"] = 402
    assert await mov.retentar_falhas(client, db) == {"retentados": 1, "falhas": 0}
    regs = await _linhas(db, "297650")
    assert regs[3].status == "ok" and regs[3].bling_product_id == 402


async def _linha_ok(db, numero, sku, pid, operacao, ordem=0) -> PrioridadeEstoqueMovimento:
    m = PrioridadeEstoqueMovimento(pedido_bling=numero, bling_id=int(numero), sku=sku,
                                   bling_product_id=pid, operacao=operacao, quantidade=1,
                                   ordem=ordem, status="ok", lancado_at=datetime.now(UTC))
    db.add(m)
    await db.commit()
    return m


@pytest.mark.asyncio
async def test_estorno_so_de_pedido_morto_que_nao_saiu(db: AsyncSession, monkeypatch):
    # 297700 cancelado antes de sair → estorna; 297701 em aberto → não;
    # 297702 excluído → estorna; 297703 cancelado mas passou por 15 → não;
    # 297704 cancelado mas com rastreio andando na Logística → não.
    for numero, sit in (("297700", "12"), ("297701", "6"), ("297702", "excluido"),
                        ("297703", "12"), ("297704", "12")):
        await _pedido(db, numero, [("dg053.sp+a001.sp", 1)], situacao=sit)
    db.add(MargemAudit(pedido_bling="297703", acao="situacao", valor_antigo="21",
                       valor_novo="15", origem="job_envio", mudado_por=None))
    db.add(Logistica(pedido_bling="297704", rastreio="AA1BR", rastreio_17track="AA1BR"))
    await db.commit()
    for numero in ("297700", "297701", "297702", "297703", "297704"):
        await _linha_ok(db, numero, "dg053.ci", 101, "E", 0)
        await _linha_ok(db, numero, "dg053.sp", 201, "S", 1)
    client = FakeBling(falha={"dg053.sp": _http_error(400)})  # 1ª S recusada (limpa carimbo)

    resumo = await mov.estornar_cancelados(client, db)
    assert resumo == {"estornados": 3, "falhas": 1}
    db.expire_all()
    regs = (await db.execute(select(PrioridadeEstoqueMovimento))).scalars().all()
    revertidos = {(r.pedido_bling, r.sku) for r in regs if r.revertido_at is not None}
    assert revertidos == {("297700", "dg053.ci"), ("297702", "dg053.ci"), ("297702", "dg053.sp")}
    assert sorted((m[0], m[1]) for m in client.movs) == [("E", 201), ("S", 101), ("S", 101)]
    # Passada seguinte: só o que faltou (297700 dg053.sp).
    client.movs.clear()
    assert await mov.estornar_cancelados(client, db) == {"estornados": 1, "falhas": 0}
    assert [(m[0], m[1]) for m in client.movs] == [("E", 201)]

    # Timeout no estorno: mantém o carimbo (não estorna 2x) e marca incerto.
    await _pedido(db, "297705", [("dg053.sp+a001.sp", 1)], situacao="12")
    await _linha_ok(db, "297705", "dg053.ci", 101, "E", 0)
    client2 = FakeBling(falha={"dg053.ci": httpx.ReadTimeout("t")})
    assert await mov.estornar_cancelados(client2, db) == {"estornados": 0, "falhas": 1}
    r = (await _linhas(db, "297705"))[0]
    assert r.revertido_at is not None and r.status == "incerto" and "sem confirmação" in r.erro
    assert await mov.estornar_cancelados(client2, db) == {"estornados": 0, "falhas": 0}


@pytest.mark.asyncio
async def test_pendente_orfao_vira_incerto(db: AsyncSession):
    novo = PrioridadeEstoqueMovimento(pedido_bling="297800", sku="dg053.ci", bling_product_id=101,
                                      operacao="E", quantidade=1, status="pendente")
    velho = PrioridadeEstoqueMovimento(pedido_bling="297801", sku="dg053.ci", bling_product_id=101,
                                       operacao="E", quantidade=1, status="pendente")
    db.add_all([novo, velho])
    await db.flush()
    id_novo, id_velho = novo.id, velho.id
    await db.commit()
    await db.execute(update(PrioridadeEstoqueMovimento).where(
        PrioridadeEstoqueMovimento.id == id_velho
    ).values(created_at=datetime.now(UTC) - timedelta(hours=2)))
    await db.commit()
    assert await mov.marcar_pendentes_orfaos(db) == 1
    db.expire_all()
    assert (await db.get(PrioridadeEstoqueMovimento, id_velho)).status == "incerto"
    assert (await db.get(PrioridadeEstoqueMovimento, id_novo)).status == "pendente"


@pytest.mark.asyncio
async def test_aviso_threema_so_quando_alguem_recebe(db: AsyncSession, monkeypatch):
    m1 = PrioridadeEstoqueMovimento(pedido_bling="297900", sku="dg053.ci", bling_product_id=101,
                                    operacao="E", quantidade=1, status="incerto", erro="timeout")
    m2 = PrioridadeEstoqueMovimento(pedido_bling="297900", sku="a001.ci", bling_product_id=102,
                                    operacao="E", quantidade=1, status="falhou",
                                    tentativas=mov.MAX_TENTATIVAS, erro="HTTP 500")
    m3 = PrioridadeEstoqueMovimento(pedido_bling="297900", sku="dg053.sp", bling_product_id=201,
                                    operacao="S", quantidade=1, status="falhou", tentativas=1)
    db.add_all([m1, m2, m3])
    await db.commit()
    enviados: list[str] = []
    resultado = {"sent": [], "failed": ["ABCDEFGH"]}

    class _Th:
        async def send_to_all(self, texto, destinos):
            enviados.append(texto)
            return resultado

    monkeypatch.setattr(mov.threema, "ThreemaClient", _Th)
    monkeypatch.setattr(mov.threema, "parse_recipients", lambda s: ["ABCDEFGH"])
    # Ninguém recebeu: não carimba, tenta de novo depois.
    assert await mov.avisar_falhas(db) == 0
    resultado.update(sent=["ABCDEFGH"], failed=[])
    assert await mov.avisar_falhas(db) == 2
    assert "297900" in enviados[-1] and "dg053.ci" in enviados[-1] and "a001.ci" in enviados[-1]
    assert "dg053.sp" not in enviados[-1]  # ainda tem tentativas
    assert await mov.avisar_falhas(db) == 0  # já avisado


@pytest.mark.asyncio
async def test_indice_unico_parcial_existe_no_schema_de_teste(db: AsyncSession):
    # O ON CONFLICT do robô depende deste índice (create_all lê o __table_args__).
    n = (await db.execute(text(
        "select count(*) from pg_indexes where indexname = 'uq_prioridade_estoque_mov_vivo'"
    ))).scalar_one()
    assert n == 1


@pytest.mark.asyncio
async def test_5xx_vira_incerto_e_concilia_pelo_extrato(db: AsyncSession, monkeypatch):
    # Caso real 15/09: HTTP 504 do gateway e o Bling processou a saída. Retentar
    # baixaria 2x — então 5xx é `incerto` e o sweep confere no extrato.
    await _pedido(db, "297341", [("dg053.ci+a001.ci", 1)])
    client = FakeBling(falha={"dg053.sp": _http_error(504)})
    _arma(monkeypatch, client, "297341", [("dg053.ci+a001.ci", 1)])
    await prio.aplicar_prioridade_estoque(db, numeros=["297341"])
    regs = await _linhas(db, "297341")
    assert [(r.sku, r.status) for r in regs][3] == ("dg053.sp", "incerto")
    assert "HTTP 504" in regs[3].erro
    # Retry NÃO mexe em incerto.
    assert await mov.retentar_falhas(client, db) == {"retentados": 0, "falhas": 0}

    # Sem movimento no extrato → continua incerto.
    assert await mov.resolver_incertos_pelo_extrato(db) == 0
    # O extrato mostra a saída de dg053.sp (produto 201) na janela → conciliado.
    db.add(StockMovement(bling_product_id=201, sku="dg053.sp", date=datetime.now(UTC),
                         tipo="S", quantidade=1))
    await db.commit()
    assert await mov.resolver_incertos_pelo_extrato(db) == 1
    r = (await _linhas(db, "297341"))[3]
    assert r.status == "ok" and r.lancado_at is not None and "conciliado" in r.erro

    # Movimento que já pertence a uma linha ok da mesma janela NÃO resolve outro incerto.
    await _pedido(db, "297342", [("dg053.ci+a001.ci", 1)])
    client2 = FakeBling(falha={"dg053.sp": _http_error(502)})
    _arma(monkeypatch, client2, "297342", [("dg053.ci+a001.ci", 1)])
    await prio.aplicar_prioridade_estoque(db, numeros=["297342"])
    assert (await _linhas(db, "297342"))[3].status == "incerto"
    assert await mov.resolver_incertos_pelo_extrato(db) == 0


@pytest.mark.asyncio
async def test_orfao_concilia_pela_hora_da_tentativa_nao_pela_do_aviso(db: AsyncSession):
    """Caso real 15/09: um deploy reiniciou o worker entre gravar a linha e
    lançar no Bling. A linha só vira `incerto` 60 min depois, no sweep — e a
    janela de conferência era centrada nesse momento, toda DEPOIS do lançamento
    real. O órfão nunca conciliava e avisava para sempre."""
    agora = datetime.now(UTC)
    tentativa = agora - timedelta(minutes=90)

    linha = PrioridadeEstoqueMovimento(
        pedido_bling="297415", sku="a001.ci", bling_product_id=301,
        operacao="E", quantidade=1, status="pendente",
    )
    db.add(linha)
    await db.flush()
    linha_id = linha.id
    await db.commit()
    # A linha foi gravada na hora da tentativa, 90 min atrás.
    await db.execute(update(PrioridadeEstoqueMovimento).where(
        PrioridadeEstoqueMovimento.id == linha_id
    ).values(created_at=tentativa))
    await db.commit()

    # O Bling registrou o movimento no mesmo minuto da tentativa.
    db.add(StockMovement(bling_product_id=301, sku="a001.ci", date=tentativa,
                         tipo="E", quantidade=1))
    await db.commit()

    # O sweep marca o órfão agora — `updated_at` fica 90 min depois do movimento.
    assert await mov.marcar_pendentes_orfaos(db) == 1
    db.expire_all()

    assert await mov.resolver_incertos_pelo_extrato(db) == 1
    db.expire_all()
    r = await db.get(PrioridadeEstoqueMovimento, linha_id)
    assert r.status == "ok"
    assert "conciliado" in (r.erro or "")


@pytest.mark.asyncio
async def test_concilia_carimbando_o_movimento_sem_dono(db: AsyncSession):
    """O carimbo é o que as próximas conciliações contam como "movimento já
    atribuído". Usar o último da janela gravava a hora de OUTRO pedido: no
    pedido 297415 a linha ficou com 16:22, hora do 297440, quando o movimento
    dela era 15:27."""
    agora = datetime.now(UTC)
    cedo = agora - timedelta(minutes=50)
    tarde = agora - timedelta(minutes=5)

    # Uma linha JÁ confirmada, dona do movimento mais antigo.
    dona = PrioridadeEstoqueMovimento(
        pedido_bling="297440", sku="a001.ci", bling_product_id=401,
        operacao="E", quantidade=1, status="ok", lancado_at=cedo,
    )
    # A nossa, interrompida.
    nossa = PrioridadeEstoqueMovimento(
        pedido_bling="297415", sku="a001.ci", bling_product_id=401,
        operacao="E", quantidade=1, status="incerto", erro="processo interrompido",
    )
    db.add_all([dona, nossa])
    await db.flush()
    nossa_id = nossa.id
    await db.commit()
    # A tentativa da nossa linha foi lá atrás; só agora ela virou `incerto`.
    await db.execute(update(PrioridadeEstoqueMovimento).where(
        PrioridadeEstoqueMovimento.id == nossa_id
    ).values(created_at=agora - timedelta(minutes=60)))
    await db.commit()

    db.add_all([
        StockMovement(bling_product_id=401, sku="a001.ci", date=cedo, tipo="E", quantidade=1),
        StockMovement(bling_product_id=401, sku="a001.ci", date=tarde, tipo="E", quantidade=1),
    ])
    await db.commit()

    assert await mov.resolver_incertos_pelo_extrato(db) == 1
    db.expire_all()
    r = await db.get(PrioridadeEstoqueMovimento, nossa_id)
    assert r.status == "ok"
    # Fica com o movimento SEM dono (o mais recente), não com o do 297440.
    assert abs((r.lancado_at - tarde).total_seconds()) < 2
