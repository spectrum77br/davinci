"""Regressão do FIFO do sem estoque — ataques da revisão (R1..R7) e achados.

Regra do Eduardo (30/10): a peça fica com o pedido mais ANTIGO (ordem de
entrada no Bling); só os mais novos que estouram o saldo vão pra Aguardando
Cancelamento (83955). Nunca 83955 sem certeza; na dúvida, ADIA (nem 83955,
nem fila, nem marca — a próxima passada confere de novo).

R8 (reserva desconhecida de pedido mais ANTIGO) é cálculo puro e fica em
tests/unit/test_estoque_fifo.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingKitComponent, BlingOrder, NfCommand, NfFaturamento, Product, User
from app.services import nf_emissao_gerar, threema
from app.services.nf_auto_enfileirar import run_auto_enfileirar_nf
from tests.test_nf_auto_enfileirar import (
    _async_return,
    _FakeBlingSituacao,
    _FakeThreema,
    _redis_falso,
    _seed_loja,
    _seed_pedido,
    _sem_espera_do_tipo_de_envio,
    admin,
    limpa_kits,
)

# Fixtures importadas: o pytest acha pelo nome do parâmetro (as autouse — Redis
# em memória e sem a espera do Flex — valem aqui como no módulo de origem).
_FIXTURES = (admin, limpa_kits, _redis_falso, _sem_espera_do_tipo_de_envio)


async def _seed_qtd(
    db: AsyncSession, *, numero: str, sku: str, bling_id: int | None, pid: int | None,
    qtd: int = 1, dias: int = 1, situacao: str = "6", loja: str = "930001",
) -> None:
    await _seed_pedido(
        db, numero=numero, loja=loja, sku=sku, bling_id=bling_id,
        item_produto_id=pid, data=datetime.now(UTC) - timedelta(days=dias),
        situacao=situacao,
    )
    await db.flush()
    o = (await db.execute(select(BlingOrder).where(BlingOrder.numero == numero))).scalar_one()
    o.item_quantidade = qtd


async def _estado(db: AsyncSession) -> tuple[list[str], dict[str, str | None]]:
    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    fats = {
        f.pedido_bling: f.status_faturamento
        for f in (await db.execute(select(NfFaturamento))).scalars().all()
    }
    return sorted(n for c in cmds for n in c.numeros), fats


def _fake(**kw) -> _FakeBlingSituacao:
    orders = {i: {"id": i, "observacoes": ""} for i in range(700000, 700020)}
    return _FakeBlingSituacao(orders=orders, **kw)


def _usa(monkeypatch: pytest.MonkeyPatch, fake: object) -> None:
    monkeypatch.setattr(nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake))


def _threema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        get_settings(), "nf_sem_estoque_threema_recipients", "7KMPCBS5", raising=False
    )
    _FakeThreema.enviados = []
    monkeypatch.setattr(threema, "ThreemaClient", _FakeThreema)


# ---- R1 / R2: a queixa, com o físico na resposta ---------------------------


@pytest.mark.asyncio
async def test_r1_queixa_com_fisico_so_o_novo_vai(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_qtd(db, numero="830001", sku="b1", bling_id=700001, pid=555, dias=2)
    await _seed_qtd(db, numero="830002", sku="b1", bling_id=700002, pid=555, dias=1)
    await db.commit()
    fake = _fake(saldos={555: -1.0}, fisicos={555: 1.0})
    _usa(monkeypatch, fake)

    await run_auto_enfileirar_nf()
    fila, fats = await _estado(db)
    assert fake.chamadas == [(700002, 83955)]
    assert fila == ["830001"]
    assert fats == {"830001": "processando", "830002": "sem_estoque"}


@pytest.mark.parametrize(("q_antigo", "q_novo"), [(2, 1), (1, 2)])
@pytest.mark.asyncio
async def test_r2_quantidade_2_o_novo_vai(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, q_antigo: int, q_novo: int
):
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_qtd(db, numero="830001", sku="b1", bling_id=700001, pid=555, qtd=q_antigo, dias=2)
    await _seed_qtd(db, numero="830002", sku="b1", bling_id=700002, pid=555, qtd=q_novo, dias=1)
    await db.commit()
    fake = _fake(saldos={555: -1.0}, fisicos={555: 2.0})
    _usa(monkeypatch, fake)

    await run_auto_enfileirar_nf()
    fila, _fats = await _estado(db)
    assert fake.chamadas == [(700002, 83955)]
    assert fila == ["830001"]


# ---- R3 / achado 1: kit "+" com composição conta SEMPRE pelos componentes --


@pytest.mark.parametrize("com_fisico", [True, False])
@pytest.mark.asyncio
async def test_r3_kit_mais_com_saldo_proprio_conta_pelo_componente(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, limpa_kits: None,
    com_fisico: bool,
):
    """dg075.pi (601) simples, mais antigo; kit "+" dg075.pi+a004.pi (901 =
    601 + 602), mais novo. Físico 1 da dg075.pi e os dois reservando no
    componente → virtual −1. O kit tem saldo PRÓPRIO positivo (prod: 69 com a
    dg075.pi em −1) — antes ele ficava inteiro, fora da disputa, e o simples
    mais antigo ia pra 83955. Agora vai só o kit (o mais novo), com ou sem o
    físico na resposta."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_qtd(db, numero="830001", sku="dg075.pi", bling_id=700001, pid=601, dias=2)
    await _seed_qtd(db, numero="830002", sku="dg075.pi+a004.pi", bling_id=700002, pid=901)
    for comp in (601, 602):
        db.add(
            BlingKitComponent(
                kit_bling_product_id=901, component_bling_product_id=comp, quantidade=1
            )
        )
    await db.commit()
    fake = _fake(
        saldos={601: -1.0, 602: 200.0, 901: 69.0},
        fisicos={601: 1.0, 602: 200.0, 901: 71.0} if com_fisico else {},
    )
    _usa(monkeypatch, fake)

    await run_auto_enfileirar_nf()
    fila, fats = await _estado(db)
    assert fake.chamadas == [(700002, 83955)]
    assert fila == ["830001"]
    assert fats == {"830001": "processando", "830002": "sem_estoque"}
    # o saldo do próprio kit com composição não decide nada — nem é lido
    assert fake.pedidos_saldos == [["601", "602"]]


@pytest.mark.asyncio
async def test_kit_mais_no_lote_e_entre_quem_segura_conta_pelo_componente(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, limpa_kits: None
):
    """O mesmo kit "+" no lote E entre quem segura a peça: o mais antigo (Em
    digitação, outra loja) tem a única dg075.pi; o simples e o kit do lote
    estouram os dois. Antes, com o kit do lote no saldo próprio (69), o kit
    "+" ficava inteiro também entre quem segura — só o simples ia e o kit
    novo passava sem peça."""
    from app.routers.nf import _sem_estoque_fifo

    await _seed_qtd(
        db, numero="830000", sku="dg075.pi+a004.pi", bling_id=700000, pid=901,
        dias=3, situacao="21", loja="930009",
    )
    await _seed_qtd(db, numero="830001", sku="dg075.pi", bling_id=700001, pid=601, dias=2)
    await _seed_qtd(db, numero="830002", sku="dg075.pi+a004.pi", bling_id=700002, pid=901)
    for comp in (601, 602):
        db.add(
            BlingKitComponent(
                kit_bling_product_id=901, component_bling_product_id=comp, quantidade=1
            )
        )
    await db.commit()
    _usa(
        monkeypatch,
        _fake(saldos={601: -2.0, 602: 200.0, 901: 69.0}, fisicos={601: 1.0, 602: 200.0}),
    )

    res = await _sem_estoque_fifo(db, ["830001", "830002"])
    assert res.lote == {"830001": ["dg075.pi"], "830002": ["dg075.pi+a004.pi"]}
    assert res.adiados == {}


# ---- R4 / achado 2: componente sem leitura → adia, sem contagem dupla ------


@pytest.mark.asyncio
async def test_r4_componente_sem_leitura_adia_quem_depende_dele(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, limpa_kits: None
):
    """b026.18.24 (901 = 501 + 502), mais antigo; b026.12.18 (902 = 503 +
    501), mais novo. A 502 não voltou do Bling. O 902 (mais novo) estoura a
    501 e vai — certo, sem depender da 502. O 901 não pode ser decidido sem a
    502: ADIADO (nem 83955, nem fila, nem marca). Sem contagem dupla: o 901
    conta na 501 pelo componente, não como "kit inteiro" + componente."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_qtd(db, numero="830001", sku="b026.18.24", bling_id=700001, pid=901, dias=2)
    await _seed_qtd(db, numero="830002", sku="b026.12.18", bling_id=700002, pid=902, dias=1)
    for kit, comp in ((901, 501), (901, 502), (902, 503), (902, 501)):
        db.add(
            BlingKitComponent(
                kit_bling_product_id=kit, component_bling_product_id=comp, quantidade=1
            )
        )
    await db.commit()
    fake = _fake(
        saldos={901: -1.0, 902: -1.0, 501: -1.0, 503: 200.0},  # 502 não veio
        fisicos={501: 1.0, 503: 200.0},
    )
    _usa(monkeypatch, fake)

    summary = await run_auto_enfileirar_nf()
    fila, fats = await _estado(db)
    assert fake.chamadas == [(700002, 83955)]
    assert fila == []
    assert fats == {"830002": "sem_estoque"}
    assert summary["adiados_estoque"] == 1
    # só os componentes vão ao Bling (o kit com composição não é lido)
    assert fake.pedidos_saldos == [["501", "502", "503"]]

    # próxima passada: a 502 respondeu (e o 902 já soltou a 501) → o 901 segue
    fake.saldos.update({501: 0.0, 502: 30.0})
    summary2 = await run_auto_enfileirar_nf()
    fila, fats = await _estado(db)
    assert summary2["candidatos"] == 1
    assert fila == ["830001"]
    assert fats == {"830001": "processando", "830002": "sem_estoque"}


@pytest.mark.asyncio
async def test_produto_que_o_bling_nao_devolveu_adia_so_quem_depende_dele(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Achado 3: a resposta que veio vale. O b1 voltou (negativo, o novo vai);
    o c1 não voltou — só o pedido do c1 é adiado, nunca passa sem conferir."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_qtd(db, numero="830001", sku="b1", bling_id=700001, pid=555, dias=3)
    await _seed_qtd(db, numero="830002", sku="b1", bling_id=700002, pid=555, dias=2)
    await _seed_qtd(db, numero="830003", sku="c1", bling_id=700003, pid=556, dias=1)
    await db.commit()
    fake = _fake(saldos={555: -1.0})
    _usa(monkeypatch, fake)

    summary = await run_auto_enfileirar_nf()
    fila, fats = await _estado(db)
    assert fake.chamadas == [(700002, 83955)]
    assert fila == ["830001"]
    assert "830003" not in fats
    assert summary["adiados_estoque"] == 1


@pytest.mark.asyncio
async def test_saldos_bling_aproveita_os_lotes_que_responderam():
    """Achado 3: um lote de 50 falhando não joga fora os outros."""
    from app.routers.nf import _saldos_bling

    class _Falha2(_FakeBlingSituacao):
        async def _request(self, method: str, path: str, params=None):
            ids = [int(v) for _k, v in params or []]
            if 51 in ids:
                raise RuntimeError("429")
            return await super()._request(method, path, params)

    fake = _Falha2(saldos={i: float(i) for i in range(1, 121)})
    out = await _saldos_bling(fake, set(range(1, 121)))
    assert sorted(out) == list(range(1, 51)) + list(range(101, 121))


# ---- R5 / achado 4: teto de recusas da 83955 ------------------------------


@pytest.mark.asyncio
async def test_r5_bling_recusa_83955_cinco_vezes_sai_do_sweep_com_aviso(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """PATCH 83955 recusado: as 4 primeiras passadas deixam o pedido sem marca
    (tenta de novo); na 5ª recusa do dia ele ganha 'erro' no painel (sai do
    sweep — não ocupa vaga do tick pra sempre) e sai UM aviso no Threema."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_qtd(db, numero="830001", sku="b1", bling_id=700001, pid=555)
    await db.commit()

    class _Recusa(_FakeBlingSituacao):
        patches = 0

        async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> None:
            type(self).patches += 1
            raise RuntimeError("400")

        async def update_order(self, bling_order_id: int, body: dict) -> dict:
            r = await super().update_order(bling_order_id, body)
            self.orders[bling_order_id]["observacoes"] = body.get("observacoes")
            return r

    fake = _Recusa(saldos={555: -1.0}, orders={700001: {"id": 700001, "observacoes": ""}})
    _usa(monkeypatch, fake)
    _threema(monkeypatch)

    for passada in range(1, 5):
        s = await run_auto_enfileirar_nf()
        assert s["sem_estoque_pendente"] == 1, passada
        assert "sem_estoque_recusado" not in s
        fila, fats = await _estado(db)
        assert fila == [] and fats == {}
    assert _FakeThreema.enviados == []

    s5 = await run_auto_enfileirar_nf()
    assert s5["sem_estoque_recusado"] == 1
    fila, fats = await _estado(db)
    assert fila == [] and fats == {"830001": "erro"}
    erro = (
        await db.execute(
            select(NfFaturamento.erro_faturamento).where(NfFaturamento.pedido_bling == "830001")
        )
    ).scalar_one()
    assert "recusou Aguardando Cancelamento 5 vezes" in erro
    assert "b1" in erro
    [(texto, recipients)] = _FakeThreema.enviados
    assert recipients == ["7KMPCBS5"]
    assert "Pedido 830001" in texto and "mude a situação à mão" in texto

    s6 = await run_auto_enfileirar_nf()
    assert s6["candidatos"] == 0
    assert _Recusa.patches == 5
    assert len(fake.puts) == 1  # o recado nas Observações não se repete
    assert len(_FakeThreema.enviados) == 1


# ---- R6 / achado 3: Bling fora → adia, e a próxima passada confere ----------


@pytest.mark.asyncio
async def test_r6_bling_fora_adia_e_a_proxima_passada_confere(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Antes: /estoques/saldos falhando = lote inteiro na fila sem conferir e
    'processando' (tentativa única: nunca mais conferido). Agora o pedido é
    ADIADO sem marca; com o Bling de volta, ele é conferido e vai pra 83955."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_qtd(db, numero="830001", sku="b1", bling_id=700001, pid=555)
    await db.commit()

    class _Falha(_FakeBlingSituacao):
        async def _request(self, method: str, path: str, params=None):
            raise RuntimeError("429")

    _usa(monkeypatch, _Falha())
    s1 = await run_auto_enfileirar_nf()
    fila, fats = await _estado(db)
    assert s1["adiados_estoque"] == 1
    assert s1["enfileirados"] == 0
    assert fila == [] and fats == {}

    ok = _fake(saldos={555: -1.0})
    _usa(monkeypatch, ok)
    s2 = await run_auto_enfileirar_nf()
    assert s2["candidatos"] == 1
    assert ok.chamadas == [(700001, 83955)]
    _fila, fats = await _estado(db)
    assert fats == {"830001": "sem_estoque"}


# ---- R7 / achado 6: sem bling_id = número como inteiro ----------------------


@pytest.mark.asyncio
async def test_r7_sem_bling_id_entra_pelo_numero_nao_pelo_fim(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Pedido sem `bling_id` entra na fila FIFO pelo número como inteiro. Antes
    ele era o "mais novo" por infinito, absorvia o déficit (e pedido sem id
    não dá pra mover no Bling) — o novo de verdade passava sem peça."""
    from app.routers.nf import _sem_estoque_fifo

    await _seed_qtd(db, numero="300000", sku="b1", bling_id=None, pid=555, dias=4)
    await _seed_qtd(db, numero="830001", sku="b1", bling_id=700001, pid=555, dias=3)
    await _seed_qtd(db, numero="830002", sku="b1", bling_id=700002, pid=555, dias=2)
    await db.commit()
    _usa(monkeypatch, _fake(saldos={555: -1.0}))

    res = await _sem_estoque_fifo(db, ["300000", "830001", "830002"])
    assert res.lote == {"830002": ["b1"]}


# ---- Achado 5: aviso do kit sem composição ---------------------------------


@pytest.mark.asyncio
async def test_kit_sem_composicao_desliga_o_fisico_e_avisa_uma_vez_por_dia(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Kit sem composição no cache segurando peça com saldo negativo desliga o
    físico (conta conservadora). Sem aviso a regra repete em silêncio até a
    sync semanal dos kits: sai 1 aviso por kit/dia no grupo do sem estoque."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_qtd(db, numero="830001", sku="b026.18", bling_id=700001, pid=501, dias=2)
    await _seed_qtd(
        db, numero="830000", sku="kitnovo", bling_id=700000, pid=950, dias=3,
        situacao="21", loja="930009",
    )
    db.add(
        Product(
            user_id=admin.id, sku="kitnovo", name="Kit novo", stock=0,
            formato="E", bling_product_id=950,
        )
    )
    await db.commit()
    fake = _fake(saldos={501: -1.0, 950: -1.0}, fisicos={501: 4.0})
    _usa(monkeypatch, fake)
    _threema(monkeypatch)

    s1 = await run_auto_enfileirar_nf()
    # sem o físico: D = 1 → o único do lote vai (o lado seguro)
    assert s1["sem_estoque"] == 1
    avisos_kit = [t for t, _r in _FakeThreema.enviados if "kit sem composição" in t]
    assert len(avisos_kit) == 1
    assert "Kit kitnovo (id Bling 950)" in avisos_kit[0]

    # outra passada no mesmo dia, com outro pedido da peça: nada de aviso novo
    await _seed_qtd(db, numero="830002", sku="b026.18", bling_id=700002, pid=501)
    await db.commit()
    await run_auto_enfileirar_nf()
    assert len([t for t, _r in _FakeThreema.enviados if "kit sem composição" in t]) == 1


# ---- Achado 7: 'excluido' pelo bling_id; linha viva decide ------------------


@pytest.mark.asyncio
async def test_pedido_reimportado_continua_segurando_a_peca(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Pedido excluído e reimportado: a versão velha (outro bling_id) tem as
    linhas 'excluido'; a viva, mais nova que o do lote, segura a peça e é o
    negativo (fora do lote). Antes o NOT EXISTS pelo NÚMERO derrubava a viva e
    o pedido do lote, mais antigo, levava o déficit."""
    from app.routers.nf import _sem_estoque_fifo

    await _seed_qtd(db, numero="830001", sku="b1", bling_id=700001, pid=555, dias=3)
    await _seed_qtd(
        db, numero="830009", sku="b1", bling_id=700008, pid=555, dias=2,
        situacao="excluido", loja="930009",
    )
    db.add(
        BlingOrder(
            bling_id=700009, numero="830009", loja="930009", situacao="6",
            item_index=0, item_codigo="b1", item_produto_id=555, item_quantidade=1,
            data=datetime.now(UTC) - timedelta(days=1),
        )
    )
    await db.commit()
    _usa(monkeypatch, _fake(saldos={555: -1.0}))

    res = await _sem_estoque_fifo(db, ["830001"])
    assert res.lote == {}
    assert res.fora_do_lote == {"830009": ["b1"]}


@pytest.mark.asyncio
async def test_linha_excluida_do_proprio_pedido_nao_decide_nem_soma(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Número do lote com a versão velha 'excluido' (outro bling_id, item 1) e
    a viva Em aberto: quem decide é a linha VIVA mais recente — o pedido
    reserva e é o mais antigo; a linha excluída não soma peça. Virtual −2,
    físico 1: os conhecidos pedem 2 → déficit 1 → só o 830002 (o mais novo).
    Antes: ou a situação 'excluido' (sem reserva → 83955 direto) ou a peça
    contada em dobro (déficit 2 → os dois iam)."""
    from app.routers.nf import _sem_estoque_fifo

    await _seed_qtd(db, numero="830001", sku="b1", bling_id=700001, pid=555, dias=3)
    db.add(
        BlingOrder(
            bling_id=700000, numero="830001", loja="930001", situacao="excluido",
            item_index=1, item_codigo="b1", item_produto_id=555, item_quantidade=1,
            data=datetime.now(UTC) - timedelta(days=4),
        )
    )
    await _seed_qtd(db, numero="830002", sku="b1", bling_id=700002, pid=555, dias=2)
    await db.commit()
    _usa(monkeypatch, _fake(saldos={555: -2.0}, fisicos={555: 1.0}))

    res = await _sem_estoque_fifo(db, ["830001", "830002"])
    assert res.lote == {"830002": ["b1"]}
