"""Pedido Flex vai para o .sp (projeto Flex, etapa 2 — 02/10/2026).

O pedido Flex sai fisicamente de São Bernardo. O robô de prioridade leva cada
item para o lote .sp da mesma base ANTES de qualquer outra regra — sem olhar a
prioridade cadastrada, a trava anti-volta nem o pedido num estoque só. Sem
peça no .sp ele NÃO troca para outro lote: grava o aviso no `flex_pedido`, a
trilha no `flex_log` e um alerta no sino dos admins. Pedido que não é Flex
continua exatamente como antes.

O Bling simulado reserva de verdade (virtual = físico − pedidos em aberto;
kit = a peça mais escassa), como em test_prioridade_reserva_real.py. Nenhuma
chamada externa.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Alert,
    BlingOrder,
    FlexLog,
    FlexPedido,
    Logistica,
    MargemAudit,
    NfFaturamento,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.services import nf_emissao_gerar
from app.services import prioridade_estoque as prio


class BlingComReserva:
    """Virtual = físico − o que os pedidos em aberto seguram; kit = a peça mais
    escassa. O PUT troca o item e, com ele, a reserva (modo substituir)."""

    def __init__(self, fisico: dict[str, int], pedidos: dict[str, list[tuple[str, int]]]):
        self.fisico = {k.lower(): v for k, v in fisico.items()}
        self.pedidos = {n: [(c.lower(), q) for c, q in itens] for n, itens in pedidos.items()}
        self.puts: list[tuple[str, list[str]]] = []
        self.observacoes: dict[str, str] = {}

    @staticmethod
    def _pecas(sku: str) -> list[str]:
        return [p.strip() for p in sku.lower().split("+") if p.strip()]

    def virtual(self, sku: str) -> int:
        pecas = self._pecas(sku)
        if len(pecas) > 1:
            return min(self.virtual(p) for p in pecas)
        peca = pecas[0]
        reservado = sum(
            q for itens in self.pedidos.values() for cod, q in itens if peca in self._pecas(cod)
        )
        return self.fisico.get(peca, 0) - reservado

    async def find_active_product_by_sku(self, sku, estrito=False):
        if not all(p in self.fisico for p in self._pecas(sku)):
            return None
        return {
            "id": abs(hash(sku.lower())) % 10**6,
            "sku": sku.lower(),
            "name": sku,
            "stock": self.virtual(sku),
        }

    async def get_order(self, bling_id):
        numero = str(bling_id)
        return {
            "id": bling_id,
            "numero": numero,
            "situacao": {"id": 6},
            "contato": {"id": 1},
            "loja": {"id": 2},
            "observacoes": self.observacoes.get(numero, ""),
            "itens": [
                {"id": 50 + i, "codigo": c, "quantidade": q, "valor": 10.0, "produto": {"id": 1}}
                for i, (c, q) in enumerate(self.pedidos[numero])
            ],
        }

    async def update_order(self, bling_id, body):
        numero = str(bling_id)
        novos = [(i["codigo"].lower(), int(i["quantidade"])) for i in body["itens"]]
        self.pedidos[numero] = novos
        self.observacoes[numero] = body.get("observacoes") or ""
        self.puts.append((numero, [c for c, _ in novos]))
        return body


@pytest.fixture
def cenario(db: AsyncSession, monkeypatch):
    """Robô com a configuração de produção (substitui item, pedido num estoque
    só ligado) e prioridade CI cadastrada para as bases do teste — o lado
    OPOSTO do Flex, para provar que a regra Flex passa por cima dela."""
    cfg = get_settings()
    monkeypatch.setattr(cfg, "prioridade_substitui_item", True)
    monkeypatch.setattr(cfg, "prioridade_pedido_estoque_unico", True)
    monkeypatch.setattr(cfg, "estoque_familia_redireciona", False)
    monkeypatch.setattr(cfg, "flex_pedido_no_sp", True)
    mapa = {"dg052": "ci", "dg053": "ci", "dg054": "ci", "a001": "ci"}

    async def _mapa(session):
        return dict(mapa)

    monkeypatch.setattr(prio, "_mapa_prioridades", _mapa)

    async def _montar(fisico, pedidos, *, flex=(), flex_logistica=()):
        bling = BlingComReserva(fisico, pedidos)
        db.add(
            User(
                open_id=f"email:{uuid.uuid4().hex[:8]}@x",
                email=f"{uuid.uuid4().hex[:8]}@x",
                name="admin",
                role=UserRole.ADMIN,
                status=UserStatus.ACTIVE,
            )
        )
        for numero, itens in pedidos.items():
            for i, (cod, q) in enumerate(itens):
                db.add(
                    BlingOrder(
                        numero=numero,
                        bling_id=int(numero),
                        loja="5001",
                        item_index=i,
                        item_codigo=cod,
                        item_quantidade=q,
                        situacao="6",
                        data=datetime.now(UTC),
                    )
                )
        for numero in flex:
            db.add(
                FlexPedido(
                    bling_id=int(numero),
                    plataforma="ml",
                    numeroloja=f"2000{numero}",
                    envio_tipo="self_service",
                    no_sp=False,
                )
            )
        for numero in flex_logistica:
            db.add(
                Logistica(
                    pedido_bling=numero,
                    plataforma="Mercado Livre",
                    pedido_marketplace=f"2000{numero}",
                    envio_tipo="self_service",
                    envio_flex=True,
                )
            )
        await db.commit()

        async def _client(session):
            return bling

        monkeypatch.setattr(nf_emissao_gerar, "_bling_client_opt", _client)
        return bling

    return _montar


async def _flex(db: AsyncSession, numero: str) -> FlexPedido | None:
    db.expire_all()
    return (
        await db.execute(select(FlexPedido).where(FlexPedido.bling_id == int(numero)))
    ).scalar_one_or_none()


async def _trilha(db: AsyncSession, numero: str) -> list[tuple[str, str]]:
    return [
        (r.acao, r.resultado)
        for r in (
            await db.execute(
                select(FlexLog).where(FlexLog.bling_id == int(numero)).order_by(FlexLog.id)
            )
        ).scalars()
    ]


async def _alertas(db: AsyncSession) -> list[Alert]:
    return list((await db.execute(select(Alert))).scalars())


@pytest.mark.asyncio
async def test_flex_com_sp_suficiente_vai_para_o_sp(db: AsyncSession, cenario):
    """Prioridade CI cadastrada e o CI com peça: um pedido normal ficaria no CI.
    O pedido Flex vai para o .sp mesmo assim — kit inteiro, num PUT só."""
    bling = await cenario(
        {"dg053.ci": 10, "a001.ci": 10, "dg053.sp": 2, "a001.sp": 2},
        {"700001": [("dg053.ci+a001.ci", 1)]},
        flex=["700001"],
    )
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["700001"])
    await db.commit()

    assert bling.puts == [("700001", ["dg053.sp+a001.sp"])]
    assert resumo["trocados"] == 1 and resumo["flex_trocados"] == 1
    assert "pedido Flex" in bling.observacoes["700001"]
    assert "dg053.ci+a001.ci -> dg053.sp+a001.sp" in bling.observacoes["700001"]

    fp = await _flex(db, "700001")
    assert fp.no_sp is True and fp.alerta is None
    item = (
        await db.execute(select(BlingOrder.item_codigo).where(BlingOrder.numero == "700001"))
    ).scalar_one()
    assert item == "dg053.sp+a001.sp"  # espelho já no .sp para o check de estoque
    audit = (await db.execute(select(MargemAudit))).scalars().all()
    assert [(a.valor_antigo, a.valor_novo) for a in audit] == [
        ("dg053.ci+a001.ci", "dg053.sp+a001.sp")
    ]
    assert await _trilha(db, "700001") == [("pedido_sp", "ok")]
    assert await _alertas(db) == []

    # Rodada seguinte: já no .sp — nada muda, nem volta para o CI da prioridade.
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["700001"])
    await db.commit()
    assert len(bling.puts) == 1 and resumo["trocados"] == 0
    assert await _trilha(db, "700001") == [("pedido_sp", "ok")]


@pytest.mark.asyncio
async def test_flex_sem_sp_nao_troca_e_avisa(db: AsyncSession, cenario):
    """.sp com 1 peça e o pedido Flex pede 2: não vai para o .sp nem para o RA
    (que tem peça e seria a saída do redirecionamento) — fica como está, com o
    aviso. Depois que chega peça no .sp, a próxima rodada troca e apaga o aviso."""
    bling = await cenario(
        {"dg053.ci": 10, "dg053.ra": 10, "dg053.sp": 1},
        {"710001": [("dg053.ci", 2)]},
        flex=["710001"],
    )
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["710001"])
    await db.commit()

    assert bling.puts == [] and resumo["trocados"] == 0
    assert resumo["flex_sem_sp"] == 1
    assert "710001" not in resumo.get("adiados", [])  # segue a esteira; a pessoa decide
    fp = await _flex(db, "710001")
    assert fp.no_sp is False
    assert "NÃO foi trocado" in fp.alerta
    assert "dg053.sp tem 1 livre e o pedido precisa de 2" in fp.alerta
    assert await _trilha(db, "710001") == [("pedido_sem_sp", "pendente")]
    alertas = await _alertas(db)
    assert len(alertas) == 1
    assert alertas[0].title == "Pedido Flex 710001 sem peça no .sp"
    assert alertas[0].dedupe_key == "flex_sem_sp:710001"
    assert "O robô NÃO trocou o lote" in alertas[0].message

    # A próxima rodada não repete o aviso nem a trilha.
    await prio.aplicar_prioridade_estoque(db, numeros=["710001"])
    await db.commit()
    assert bling.puts == []
    assert await _trilha(db, "710001") == [("pedido_sem_sp", "pendente")]
    assert len(await _alertas(db)) == 1

    # Chegou peça no .sp: troca, `no_sp` liga e o aviso some.
    bling.fisico["dg053.sp"] = 5
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["710001"])
    await db.commit()
    assert bling.puts == [("710001", ["dg053.sp"])]
    fp = await _flex(db, "710001")
    assert fp.no_sp is True and fp.alerta is None
    assert await _trilha(db, "710001") == [("pedido_sem_sp", "pendente"), ("pedido_sp", "ok")]


@pytest.mark.asyncio
async def test_flex_tudo_ou_nada_com_peca_compartilhada(db: AsyncSession, cenario):
    """Dois kits no mesmo pedido Flex usam o fone a001: o .sp tem 1 fone, cada
    kit sozinho caberia, os dois juntos não. Nenhum dos dois é trocado."""
    bling = await cenario(
        {
            "dg053.ci": 5, "dg054.ci": 5, "a001.ci": 5,
            "dg053.sp": 5, "dg054.sp": 5, "a001.sp": 1,
        },
        {"720001": [("dg053.ci+a001.ci", 1), ("dg054.ci+a001.ci", 1)]},
        flex=["720001"],
    )
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["720001"])
    await db.commit()
    assert bling.puts == [] and resumo["flex_sem_sp"] == 1
    fp = await _flex(db, "720001")
    assert "não cobre todos juntos" in fp.alerta


@pytest.mark.asyncio
async def test_flex_lote_usado_nao_vira_novo(db: AsyncSession, cenario):
    """Item .us (usado) num pedido Flex: o .sp seria o produto NOVO — não troca,
    avisa para trocar à mão."""
    bling = await cenario(
        {"dg053.us": 3, "dg053.sp": 3},
        {"730001": [("dg053.us", 1)]},
        flex=["730001"],
    )
    await prio.aplicar_prioridade_estoque(db, numeros=["730001"])
    await db.commit()
    assert bling.puts == []
    fp = await _flex(db, "730001")
    assert "lote .us não tem .sp equivalente" in fp.alerta


@pytest.mark.asyncio
async def test_flex_ignora_a_trava_anti_volta(db: AsyncSession, cenario):
    """O robô tirou o pedido do .sp há 10 min (prioridade CI). Para um pedido
    normal, voltar seria opcional e a trava seguraria; o pedido Flex volta."""
    bling = await cenario(
        {"dg053.ci": 10, "dg053.sp": 3},
        {"740001": [("dg053.ci", 1)]},
        flex=["740001"],
    )
    db.add(
        MargemAudit(
            pedido_bling="740001",
            bling_id="740001",
            sku="dg053.sp",
            valor_antigo="dg053.sp",
            valor_novo="dg053.ci",
            origem="prioridade_estoque",
            acao="sku",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
    )
    await db.commit()
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["740001"])
    await db.commit()
    assert bling.puts == [("740001", ["dg053.sp"])]
    assert resumo.get("anti_vai_e_volta", 0) == 0


@pytest.mark.asyncio
async def test_pedido_normal_inalterado_ao_lado_do_flex(db: AsyncSession, cenario):
    """Na mesma rodada: o pedido normal no .sp vai para o CI da prioridade (como
    sempre); o pedido Flex no CI vai para o .sp. Nenhum aviso."""
    bling = await cenario(
        {"dg053.ci": 10, "dg053.sp": 5},
        {"750001": [("dg053.sp", 1)], "750002": [("dg053.ci", 1)]},
        flex=["750002"],
    )
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["750001", "750002"])
    await db.commit()
    assert sorted(bling.puts) == [("750001", ["dg053.ci"]), ("750002", ["dg053.sp"])]
    assert "prioridade de estoque" in bling.observacoes["750001"]
    assert "Flex" not in bling.observacoes["750001"]
    assert resumo["trocados"] == 2 and resumo["flex_trocados"] == 1
    assert await _flex(db, "750001") is None  # o normal não ganha linha Flex
    assert await _trilha(db, "750001") == []
    assert await _alertas(db) == []


@pytest.mark.asyncio
async def test_chave_desligada_volta_ao_robo_de_antes(db: AsyncSession, cenario, monkeypatch):
    """FLEX_PEDIDO_NO_SP=false: o pedido Flex segue a prioridade como qualquer
    outro (fica no CI) e nada é gravado no Flex."""
    bling = await cenario(
        {"dg053.ci": 10, "dg053.sp": 5},
        {"760001": [("dg053.ci", 1)]},
        flex=["760001"],
    )
    monkeypatch.setattr(get_settings(), "flex_pedido_no_sp", False)
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["760001"])
    await db.commit()
    assert bling.puts == [] and "flex_pedidos" not in resumo
    assert await _trilha(db, "760001") == []


@pytest.mark.asyncio
async def test_flex_marcado_so_pela_logistica_e_sem_prioridade(
    db: AsyncSession, cenario, monkeypatch
):
    """Pedido que só a Logística marcou Flex (o shipment check ainda não
    registrou), sem prioridade nenhuma e sem o pedido num estoque só: vai para o
    .sp e ganha a linha em `flex_pedido`; o pedido normal nem é consultado."""
    bling = await cenario(
        {"dg053.ci": 10, "dg053.sp": 5},
        {"770001": [("dg053.ci", 1)], "770002": [("dg053.ci", 1)]},
        flex_logistica=["770001"],
    )
    monkeypatch.setattr(get_settings(), "prioridade_pedido_estoque_unico", False)

    async def _vazio(session):
        return {}

    monkeypatch.setattr(prio, "_mapa_prioridades", _vazio)
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["770001", "770002"])
    await db.commit()
    assert bling.puts == [("770001", ["dg053.sp"])]
    assert resumo["flex_pedidos"] == 1 and resumo["avaliados"] == 0
    fp = await _flex(db, "770001")
    assert fp is not None and fp.no_sp is True and fp.plataforma == "ml"
    assert fp.numeroloja == "2000770001"


# ---- revisão de 02/10/2026 ---------------------------------------------------


async def _admin_id(db: AsyncSession):
    return (await db.execute(select(User.id).limit(1))).scalar_one()


@pytest.mark.asyncio
async def test_flex_vem_antes_do_pedido_normal_mais_antigo(db: AsyncSession, cenario, monkeypatch):
    """Achado da revisão: o laço seguia o número do pedido. Com dg053.sp = 1
    e prioridade .sp para dg053, o normal 780001 (que pode sair do CI) pegava
    a última peça de São Bernardo e o Flex 780002 ficava sem ela."""
    async def _mapa_sp(session):
        return {"dg053": "sp"}

    bling = await cenario(
        {"dg053.ci": 10, "dg053.sp": 1},
        {"780001": [("dg053.ci", 1)], "780002": [("dg053.ci", 1)]},
        flex=["780002"],
    )
    monkeypatch.setattr(prio, "_mapa_prioridades", _mapa_sp)
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["780001", "780002"])
    await db.commit()
    assert bling.puts == [("780002", ["dg053.sp"])]
    assert resumo["flex_trocados"] == 1
    fp = await _flex(db, "780002")
    assert fp.no_sp is True and fp.alerta is None


@pytest.mark.asyncio
async def test_sp_segurado_para_o_flex_sem_peca(db: AsyncSession, cenario, monkeypatch):
    """O Flex 790002 precisa de 2 e o .sp só tem 1: ele não troca (a pessoa
    decide). A peça de São Bernardo fica segurada para ele — o pedido normal
    com prioridade .sp não a leva (fica no CI, que tem peça)."""
    async def _mapa_sp(session):
        return {"dg053": "sp"}

    bling = await cenario(
        {"dg053.ci": 10, "dg053.sp": 1},
        {"790001": [("dg053.ci", 1)], "790002": [("dg053.ci", 2)]},
        flex=["790002"],
    )
    monkeypatch.setattr(prio, "_mapa_prioridades", _mapa_sp)
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["790001", "790002"])
    await db.commit()
    assert bling.puts == []
    assert resumo["flex_sem_sp"] == 1
    # O gancho do enfileirar só com o pedido normal: o Flex sem peça (com o
    # aviso gravado) continua segurando o .sp.
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["790001"])
    await db.commit()
    assert bling.puts == []
    # Chegou peça bastante no .sp: o Flex vai primeiro e o normal leva o resto.
    bling.fisico["dg053.sp"] = 3
    await prio.aplicar_prioridade_estoque(db, numeros=["790001", "790002"])
    await db.commit()
    assert bling.puts == [("790002", ["dg053.sp"]), ("790001", ["dg053.sp"])]


@pytest.mark.asyncio
async def test_troca_para_o_sp_grava_quando(db: AsyncSession, cenario):
    """`flex_pedido.sp_em`: o motor do Flex segue descontando o pedido até o
    produto .sp ser atualizado depois disso (o webhook pode não vir)."""
    antes = datetime.now(UTC)
    await cenario({"dg053.ci": 10, "dg053.sp": 5}, {"791001": [("dg053.ci", 1)]}, flex=["791001"])
    await prio.aplicar_prioridade_estoque(db, numeros=["791001"])
    await db.commit()
    fp = await _flex(db, "791001")
    assert fp.no_sp is True
    assert fp.sp_em is not None and fp.sp_em >= antes


@pytest.mark.asyncio
async def test_pedido_ml_novo_espera_o_tipo_de_envio(db: AsyncSession, cenario):
    """Achado da revisão: a NF automática (minutos pares) pegava o pedido ML/
    Shopee antes de o shipment check dizer se é Flex — o robô o tratava como
    normal (podia tirá-lo do .sp) e a planilha saía com o lote errado. Agora
    o pedido recém-chegado sem prazo nem tipo de envio fica para depois."""
    bling = await cenario(
        {"dg053.ci": 10, "dg053.sp": 5},
        {"792001": [("dg053.sp", 1)], "792002": [("dg053.sp", 1)], "792003": [("dg053.sp", 1)]},
    )
    db.add(StoreInfo(user_id=await _admin_id(db), platform="ml", bling_store_id="5001"))
    await db.commit()
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["792001", "792002", "792003"])
    await db.commit()
    assert bling.puts == []
    assert sorted(resumo["adiados"]) == ["792001", "792002", "792003"]
    assert sorted(resumo["adiados_envio"]) == ["792001", "792002", "792003"]

    # 792001: o shipment check leu (gravou o prazo) e não é Flex → segue a
    # prioridade CI de sempre. 792002: a Logística já sabe o tipo. 792003:
    # passou da espera sem ninguém ler (conta sem acesso) → segue.
    await db.execute(text("UPDATE bling_orders SET marketplace_ship_deadline = now()"
                          " WHERE numero = '792001'"))
    db.add(Logistica(pedido_bling="792002", plataforma="Mercado Livre", envio_flex=False,
                     envio_tipo="cross_docking"))
    await db.execute(text("UPDATE bling_orders SET created_at = now() - interval '11 minutes'"
                          " WHERE numero = '792003'"))
    await db.commit()
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["792001", "792002", "792003"])
    await db.commit()
    assert "adiados_envio" not in resumo
    assert sorted(n for n, _ in bling.puts) == ["792001", "792002", "792003"]


@pytest.mark.asyncio
async def test_pedido_novo_de_outra_plataforma_nao_espera(db: AsyncSession, cenario, monkeypatch):
    """Sem Flex na plataforma (TikTok, Amazon…) ou com a espera zerada: nada muda."""
    bling = await cenario(
        {"dg053.ci": 10, "dg053.sp": 5},
        {"793001": [("dg053.sp", 1)], "793002": [("dg053.sp", 1)]},
    )
    db.add(StoreInfo(user_id=await _admin_id(db), platform="tiktok", bling_store_id="5001"))
    await db.commit()
    await prio.aplicar_prioridade_estoque(db, numeros=["793001"])
    await db.commit()
    assert bling.puts == [("793001", ["dg053.ci"])]
    # Loja ML, mas a espera zerada na configuração: segue na hora.
    await db.execute(text("UPDATE store_info SET platform = 'ml'"))
    await db.commit()
    monkeypatch.setattr(get_settings(), "flex_espera_envio_min", 0)
    await prio.aplicar_prioridade_estoque(db, numeros=["793002"])
    await db.commit()
    assert bling.puts[-1] == ("793002", ["dg053.ci"])


@pytest.mark.asyncio
async def test_flex_reconhecido_depois_da_fila_da_nf_nao_troca(db: AsyncSession, cenario):
    """O tipo de envio chegou além da espera e o pedido já foi para a fila da
    NF (planilha com o lote antigo): trocar agora deixaria a NF com um SKU e o
    pedido com outro. O robô não troca e avisa uma pessoa."""
    bling = await cenario({"dg053.ci": 10, "dg053.sp": 5}, {"794001": [("dg053.ci", 1)]},
                          flex=["794001"])
    db.add(NfFaturamento(pedido_bling="794001", status_faturamento="processando"))
    await db.commit()
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["794001"])
    await db.commit()
    assert bling.puts == []
    assert resumo["flex_na_fila_da_nf"] == 1
    fp = await _flex(db, "794001")
    assert fp.no_sp is False
    assert "fila da NF" in fp.alerta and "refaça a NF" in fp.alerta
    alertas = await _alertas(db)
    assert len(alertas) == 1 and "DEPOIS de entrar na fila da NF" in alertas[0].message
