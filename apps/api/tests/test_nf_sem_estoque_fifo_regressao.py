"""Regressão do FIFO do sem estoque — ataques das revisões (R1..R8, A1..A9) e
achados.

Regra do Eduardo (30/10): a peça fica com o pedido mais ANTIGO (ordem de
entrada no Bling); só os mais novos que estouram o saldo vão pra Aguardando
Cancelamento (83955). Nunca 83955 sem certeza; na dúvida, ADIA por pouco
tempo (nem 83955, nem fila, nem marca). Mas a fila automática nunca pode
parar em silêncio: adiado não ocupa as vagas do tick, a dúvida tem prazo e o
sem leitura tem teto (erro no painel + aviso).

R8 no cálculo puro fica em tests/unit/test_estoque_fifo.py; de ponta a ponta
(o mais antigo chega atrasado no espelho) é o A1 e o A3 daqui.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingKitComponent, BlingOrder, NfCommand, NfFaturamento, Product, User
from app.services import nf_auto_enfileirar, nf_emissao_gerar, threema
from app.services.nf_auto_enfileirar import run_auto_enfileirar_nf
from tests.test_nf_auto_enfileirar import (
    _async_return,
    _FakeBlingSituacao,
    _FakeRedis,
    _FakeThreema,
    _redis_falso,
    _Relogio,
    _RespostaSaldos,
    _seed_loja,
    _seed_pedido,
    _sem_espera_do_tipo_de_envio,
    admin,
    limpa_kits,
    relogio,
)

# Fixtures importadas: o pytest acha pelo nome do parâmetro (as autouse — Redis
# em memória, relógio parado e sem a espera do Flex — valem aqui como no
# módulo de origem).
_FIXTURES = (admin, limpa_kits, _redis_falso, relogio, _sem_espera_do_tipo_de_envio)


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
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, limpa_kits: None,
    relogio: _Relogio,
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

    # passada de 2 min depois: o adiado não toma vaga do tick
    relogio.avanca(2)
    assert (await run_auto_enfileirar_nf())["candidatos"] == 0
    # 6 min: a 502 respondeu (e o 902 já soltou a 501) → o 901 segue
    relogio.avanca(4)
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
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, relogio: _Relogio
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
    relogio.avanca(6)
    s2 = await run_auto_enfileirar_nf()
    assert s2["candidatos"] == 1
    assert ok.chamadas == [(700001, 83955)]
    _fila, fats = await _estado(db)
    assert fats == {"830001": "sem_estoque"}


# ---- R7 / higiene: sem bling_id = o MAIS NOVO (fim da fila) -----------------


@pytest.mark.asyncio
async def test_r7_sem_bling_id_e_o_mais_novo(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Pedido sem `bling_id` é o MAIS NOVO (fim da fila): número e `bling_id`
    são escalas diferentes — o número no lugar do id fazia dele o mais antigo
    de todos (300000 < 700001) e ele ficava com a peça de quem reservou antes.
    Sem id ele também não pode ser movido no Bling: o PATCH falha e o teto da
    83955 o tira do sweep com 'erro' + aviso."""
    from app.routers.nf import _sem_estoque_fifo

    await _seed_qtd(db, numero="300000", sku="b1", bling_id=None, pid=555, dias=4)
    await _seed_qtd(db, numero="830001", sku="b1", bling_id=700001, pid=555, dias=3)
    await _seed_qtd(db, numero="830002", sku="b1", bling_id=700002, pid=555, dias=2)
    await db.commit()
    _usa(monkeypatch, _fake(saldos={555: -1.0}))

    res = await _sem_estoque_fifo(db, ["300000", "830001", "830002"])
    assert res.lote == {"300000": ["b1"]}


# ---- Achado 5: aviso do kit sem composição ---------------------------------


@pytest.mark.asyncio
async def test_kit_sem_composicao_adia_decide_o_virtual_e_avisa_uma_vez_por_dia(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, relogio: _Relogio
):
    """Kit sem composição no cache segurando peça com saldo negativo: o pedido
    que depende do físico fica na DÚVIDA (adiado, não vai direto pra 83955);
    vencido o prazo decide o virtual (o lado seguro contra vender sem peça).
    Sem aviso a regra repetiria em silêncio até a sync semanal dos kits: sai 1
    aviso por kit/dia no grupo do sem estoque."""
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
    # físico 4 diz que cabe, virtual −1 diz que não: dúvida — adiado
    assert s1["sem_estoque"] == 0 and s1["adiados_duvida"] == 1
    assert fake.chamadas == []
    avisos_kit = [t for t, _r in _FakeThreema.enviados if "kit sem composição" in t]
    assert len(avisos_kit) == 1
    assert "Kit kitnovo (id Bling 950)" in avisos_kit[0]
    assert "adiados até 10 min" in avisos_kit[0]

    relogio.avanca(6)
    assert (await run_auto_enfileirar_nf())["adiados_duvida"] == 1  # dentro do prazo
    relogio.avanca(6)
    s3 = await run_auto_enfileirar_nf()
    # vencido o prazo com o kit suspeito: decide o virtual — D = 1, vai
    assert s3["sem_estoque"] == 1
    assert fake.chamadas == [(700001, 83955)]

    # outra passada no mesmo dia, com outro pedido da peça: nada de aviso novo
    await _seed_qtd(db, numero="830002", sku="b026.18", bling_id=700002, pid=501)
    await db.commit()
    relogio.avanca(2)
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


# ============================================================================
# Ataques da 3ª revisão (A1..A9) — Bling DINÂMICO e espelho tick a tick.
# ============================================================================

_RESERVAM = {"6", "21"}


class _BlingVivo(_FakeBlingSituacao):
    """Bling com estado: pedidos (bling_id → itens, situação), físico por
    produto simples e composição dos kits. Virtual = físico − reserva dos
    pedidos em 6/21 (kit abre nos componentes); a 83955 solta a reserva.
    `omite`: ids que o /estoques/saldos não devolve."""

    def __init__(self, fisico, kits, pedidos):
        super().__init__(orders={b: {"id": b, "observacoes": ""} for b in pedidos})
        self.fisico = dict(fisico)
        self.kits = kits
        self.pedidos = {b: {"itens": its, "sit": "6"} for b, its in pedidos.items()}
        self.omite: set[int] = set()

    def _reserva(self, pid):
        tot = 0.0
        for p in self.pedidos.values():
            if p["sit"] not in _RESERVAM:
                continue
            for item, q in p["itens"]:
                for c, qc in self.kits.get(item, {item: 1}).items():
                    if c == pid:
                        tot += q * qc
        return tot

    def _linha(self, pid):
        if pid in self.omite:
            return None
        if pid in self.kits:
            comps = self.kits[pid]
            fis = min(self.fisico[c] // q for c, q in comps.items())
            vir = min((self.fisico[c] - self._reserva(c)) // q for c, q in comps.items())
            return {"produto": {"id": pid}, "saldoFisicoTotal": fis, "saldoVirtualTotal": vir}
        if pid not in self.fisico:
            return None
        f = self.fisico[pid]
        return {
            "produto": {"id": pid},
            "saldoFisicoTotal": f,
            "saldoVirtualTotal": f - self._reserva(pid),
        }

    async def _request(self, method, path, params=None):
        assert (method, path) == ("GET", "/estoques/saldos")
        ids = [int(v) for k, v in params or [] if k == "idsProdutos[]"]
        self.pedidos_saldos.append([str(i) for i in ids])
        return _RespostaSaldos([lin for i in ids if (lin := self._linha(i))])

    async def update_order_situacao(self, bling_order_id, situacao_id):
        await super().update_order_situacao(bling_order_id, situacao_id)
        self.pedidos[bling_order_id]["sit"] = str(situacao_id)


def _espelho(db, numero, bling_id, itens, *, loja="930001", situacao="6", dias=1.0):
    for idx, (pid, sku, q) in enumerate(itens):
        db.add(
            BlingOrder(
                bling_id=bling_id, numero=numero, loja=loja, situacao=situacao,
                item_index=idx, item_codigo=sku, item_produto_id=pid, item_quantidade=q,
                itemvalor=100, data=datetime.now(UTC) - timedelta(days=dias),
                nome_destinatario="Cleso", cep_destino="30570050",
                endereco_destino="Rua X", numero_destino="30", bairro_destino="Centro",
                cidade_destino="Belo Horizonte", uf_destino="MG",
                item_descricao=f"Produto {sku}",
            )
        )


async def _passadas(relogio: _Relogio, n: int) -> list[dict]:
    """`n` ticks do sweep, 2 min um do outro (como o cron)."""
    out = []
    for _ in range(n):
        relogio.avanca(2)
        out.append(await run_auto_enfileirar_nf())
    return out


@pytest.mark.asyncio
async def test_a1_o_mais_antigo_chega_atrasado_no_espelho_e_fica_com_a_peca(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, relogio: _Relogio
):
    """R8 de ponta a ponta: o Bling reserva X (700001, antigo) e B (700002,
    novo) na única peça. No 1º tick só o B está no espelho: o físico diz que
    cabe, o virtual não — DÚVIDA, o B é adiado. O X chega no tick seguinte e
    vai pra fila; o B volta e vai pra 83955. Antes o B passava no 1º tick e o
    X, o dono da peça, ia pra 83955."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    fake = _BlingVivo({555: 1}, {}, {700001: [(555, 1)], 700002: [(555, 1)]})
    _usa(monkeypatch, fake)
    _espelho(db, "830002", 700002, [(555, "b1", 1)], dias=1)
    await db.commit()
    s1 = await run_auto_enfileirar_nf()
    assert s1["adiados_duvida"] == 1
    assert fake.chamadas == []
    assert await _estado(db) == ([], {})

    _espelho(db, "830001", 700001, [(555, "b1", 1)], dias=2)
    await db.commit()
    await _passadas(relogio, 4)
    fila, fats = await _estado(db)
    assert fake.chamadas == [(700002, 83955)]
    assert fila == ["830001"]
    assert fats == {"830001": "processando", "830002": "sem_estoque"}


# A2/A3 — a queixa de 30/10 (malas b026), dados reais de prod.
B12, B18, B24 = 501, 502, 503
K1218, K1824 = 901, 902
_PED = {
    # numero: (bling_id, [(pid, sku, q)])
    "306575": (27258626035, [(B18, "b026.18", 1)]),
    "306580": (27258985905, [(K1218, "b026.12.18", 1)]),
    "306581": (27259023872, [(K1218, "b026.12.18", 1)]),
    "306589": (27259309391, [(K1824, "b026.18.24", 1)]),
    "306616": (27260398098, [(K1824, "b026.18.24", 1)]),
    "306620": (27260550581, [(K1218, "b026.12.18", 1)]),
    "306622": (27260657797, [(K1824, "b026.18.24", 1)]),
    "306624": (27260708835, [(K1824, "b026.18.24", 1)]),
    "306633": (27261165628, [(B18, "b026.18", 1)]),
    "306636": (27261233079, [(K1218, "b026.12.18", 1)]),
    "306644": (27262767411, [(B18, "b026.18", 1)]),
}
_KITS = {K1218: {B12: 1, B18: 1}, K1824: {B18: 1, B24: 1}}
_DONOS = ["306580", "306581", "306589"]  # + 306575 (Em digitação): as 4 peças
_NOVOS = ["306616", "306620", "306622", "306624", "306633", "306636", "306644"]


async def _cenario_b026(db, admin, monkeypatch, relogio, ticks, extra=10):
    """O Bling já reserva os 11 (306575 em 21) com físico 4 da b026.18; o
    espelho recebe os pedidos tick a tick (2 min) e depois roda `extra` ticks
    sem novidade (dúvidas vencem, adiados voltam)."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    for kit, comps in _KITS.items():
        for c, q in comps.items():
            db.add(
                BlingKitComponent(
                    kit_bling_product_id=kit, component_bling_product_id=c, quantidade=q
                )
            )
    fake = _BlingVivo(
        {B12: 196, B18: 4, B24: 100}, _KITS,
        {bid: [(pid, q) for pid, _s, q in its] for bid, its in _PED.values()},
    )
    fake.pedidos[_PED["306575"][0]]["sit"] = "21"
    _usa(monkeypatch, fake)
    _espelho(db, "306575", *_PED["306575"], situacao="21", loja="930009", dias=1.2)
    await db.commit()
    resumos = []
    for numeros in ticks:
        for n in numeros:
            bid, its = _PED[n]
            _espelho(db, n, bid, its, dias=1.0 - (bid - 27258000000) / 1e10)
        await db.commit()
        relogio.avanca(2)
        resumos.append(await run_auto_enfileirar_nf())
    resumos += await _passadas(relogio, extra)
    fila, _fats = await _estado(db)
    foram = sorted(n for n, b in _PED.items() if (b[0], 83955) in fake.chamadas)
    return fila, foram, resumos


@pytest.mark.asyncio
async def test_a2_queixa_b026_tick_a_tick_os_antigos_ficam_com_a_peca(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, limpa_kits: None,
    relogio: _Relogio,
):
    """O ingest real de 30/10 (08:18 → 08:50): ficam com a b026.18 o 306575 e
    os 3 que entraram primeiro no Bling; vão pra 83955 os 7 mais novos."""
    ticks = [
        ["306580", "306581"], ["306589"], ["306616"],
        ["306620", "306622", "306624", "306633", "306636"], ["306644"],
    ]
    fila, foram, _r = await _cenario_b026(db, admin, monkeypatch, relogio, ticks)
    assert foram == _NOVOS
    assert fila == _DONOS


@pytest.mark.asyncio
async def test_a3_queixa_b026_com_o_306580_atrasado_no_espelho(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, limpa_kits: None,
    relogio: _Relogio,
):
    """Mesmo dia, mas o 306580 (2º mais antigo) só entra no espelho junto com
    os novos das 08:24. Antes: o físico deixava passar o 306616 às 08:22 e o
    306580, dono da peça, ia pra 83955. Agora a dúvida segura o 306616 até o
    306580 chegar — os ANTIGOS ficam com a peça."""
    ticks = [
        ["306581"], ["306589"], ["306616"],
        ["306580", "306620", "306622", "306624", "306633", "306636"], ["306644"],
    ]
    fila, foram, _r = await _cenario_b026(db, admin, monkeypatch, relogio, ticks)
    assert "306580" not in foram
    assert foram == _NOVOS
    assert fila == _DONOS


@pytest.mark.asyncio
async def test_a2_tick_unico_os_nove_juntos(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, limpa_kits: None,
    relogio: _Relogio,
):
    ticks = [["306580", "306581", "306589", *_NOVOS]]
    fila, foram, resumos = await _cenario_b026(db, admin, monkeypatch, relogio, ticks)
    assert foram == _NOVOS
    assert fila == _DONOS
    assert "adiados_duvida" not in resumos[0]  # tudo no espelho: sem dúvida


@pytest.mark.asyncio
async def test_a8_kit_sem_composicao_sem_leitura_adia_em_vez_de_mandar_os_antigos(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, limpa_kits: None,
    relogio: _Relogio,
):
    """4 antigos do lote (conhecidos, físico 4) + 6 novos que o espelho ainda
    não viu, e um kit SEM composição segurando peça cuja leitura não volta.
    Antes o kit desligava o físico de tudo e os 4 antigos iam pra 83955. Agora
    ficam na DÚVIDA; os 6 novos chegam, vão pra 83955 (as duas contas
    concordam) e os 4 antigos seguem pra fila."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    pedidos = {700000 + i: [(555, 1)] for i in range(1, 11)}
    pedidos[690000] = [(950, 1)]  # kit sem composição segurando (outra loja)
    fake = _BlingVivo({555: 4}, {}, pedidos)  # o 950 não volta do /estoques/saldos
    _usa(monkeypatch, fake)
    for i in range(1, 5):
        _espelho(db, f"83000{i}", 700000 + i, [(555, "b1", 1)], dias=3 - i * 0.1)
    _espelho(db, "829999", 690000, [(950, "kitx", 1)], loja="930009", dias=5)
    db.add(Product(user_id=admin.id, sku="kitx", name="Kit X", stock=0, formato="E",
                   bling_product_id=950))
    await db.commit()
    s1 = await run_auto_enfileirar_nf()
    assert fake.chamadas == []
    assert s1["adiados_duvida"] == 4
    assert await _estado(db) == ([], {})

    for i in range(5, 11):
        _espelho(db, f"8300{i:02d}", 700000 + i, [(555, "b1", 1)], dias=1 - i * 0.01)
    await db.commit()
    await _passadas(relogio, 6)
    fila, fats = await _estado(db)
    assert sorted(b for b, _s in fake.chamadas) == list(range(700005, 700011))
    assert fila == ["830001", "830002", "830003", "830004"]


@pytest.mark.asyncio
async def test_a9_adiados_nao_tomam_as_vagas_do_tick_e_tem_teto(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, relogio: _Relogio
):
    """30 pedidos com produto que o Bling nunca devolve (número menor = vêm
    primeiro nos candidatos) + 1 pedido bom. Antes: os 30 adiados tomavam as
    30 vagas de todo tick e o bom nunca entrava. Agora o adiado é pulado por
    alguns minutos (o bom entra no tick seguinte) e, com 30 min sem leitura,
    sai do sweep com 'erro' + UM aviso — nunca 83955."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    _threema(monkeypatch)
    pedidos = {700100 + i: [(556, 1)] for i in range(30)}
    pedidos[700200] = [(555, 1)]
    fake = _BlingVivo({555: 50}, {}, pedidos)  # 556 nunca volta (produto excluído)
    _usa(monkeypatch, fake)
    for i in range(30):
        _espelho(db, f"8300{i:02d}", 700100 + i, [(556, "velho", 1)], dias=3)
    _espelho(db, "830100", 700200, [(555, "bom", 1)], dias=1)
    await db.commit()

    s1 = await run_auto_enfileirar_nf()
    assert s1["candidatos"] == 30 and s1["adiados_estoque"] == 30
    [s2] = await _passadas(relogio, 1)
    assert s2["candidatos"] == 1 and s2["enfileirados"] == 1
    fila, _fats = await _estado(db)
    assert fila == ["830100"]

    resumos = await _passadas(relogio, 15)  # até 32 min
    fila, fats = await _estado(db)
    assert fila == ["830100"]
    assert fake.chamadas == []
    assert {n: st for n, st in fats.items() if n != "830100"} == {
        f"8300{i:02d}": "erro" for i in range(30)
    }
    assert sum(r.get("sem_leitura_erro", 0) for r in resumos) == 30
    erro = (
        await db.execute(
            select(NfFaturamento.erro_faturamento).where(NfFaturamento.pedido_bling == "830000")
        )
    ).scalar_one()
    assert "não devolveu o saldo de velho por mais de 30 min" in erro
    [(texto, recipients)] = [
        (t, r) for t, r in _FakeThreema.enviados if "NÃO conferido" in t
    ]
    assert recipients == ["7KMPCBS5"]
    assert "Pedido 830000" in texto and "Pedido 830029" in texto
    # depois do teto, sai do sweep: nada de aviso novo
    await _passadas(relogio, 3)
    assert len([t for t, _r in _FakeThreema.enviados if "NÃO conferido" in t]) == 1


@pytest.mark.asyncio
async def test_sem_leitura_que_volta_zera_o_relogio_do_teto(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, relogio: _Relogio
):
    """O teto é de 30 min SEGUIDOS sem leitura: o Bling voltou a responder
    (e o pedido continuou adiado por outro motivo) — o relógio recomeça."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    fake = _BlingVivo({555: 1}, {}, {700001: [(555, 1)], 700000: [(555, 1)]})
    fake.omite = {555}
    _usa(monkeypatch, fake)
    _espelho(db, "830001", 700001, [(555, "b1", 1)], dias=1)
    await db.commit()
    await run_auto_enfileirar_nf()
    await _passadas(relogio, 12)  # 24 min sem leitura
    # volta a leitura: o 700000 (antigo, fora do espelho) deixa o 830001 na dúvida
    fake.omite = set()
    await _passadas(relogio, 3)
    # some de novo: o relógio do sem leitura recomeçou — 20 min ainda não é teto
    fake.omite = {555}
    await _passadas(relogio, 10)
    _fila, fats = await _estado(db)
    assert fats == {}
    assert fake.chamadas == []


class _RedisFora:
    """Redis que falha em tudo."""

    def __getattr__(self, nome):
        async def _falha(*_a, **_k):
            raise ConnectionError("redis fora")

        return _falha


@pytest.mark.asyncio
async def test_redis_fora_o_sweep_segue_e_a_duvida_fica_adiada(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, relogio: _Relogio
):
    """Sem Redis não há relógio: a dúvida e o sem leitura seguem ADIADOS (nunca
    83955 no escuro), sem teto e sem pulo — e o sweep não quebra: o pedido bom
    entra na fila."""
    monkeypatch.setattr(nf_auto_enfileirar, "redis", _RedisFora())
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    fake = _BlingVivo(
        {555: 1, 557: 10},
        {},
        {700000: [(555, 1)], 700002: [(555, 1)], 700003: [(556, 1)], 700004: [(557, 1)]},
    )
    _usa(monkeypatch, fake)
    _espelho(db, "830002", 700002, [(555, "b1", 1)])  # dúvida (o 700000 não está no espelho)
    _espelho(db, "830003", 700003, [(556, "c1", 1)])  # sem leitura
    _espelho(db, "830004", 700004, [(557, "d1", 1)])  # bom
    await db.commit()
    s1 = await run_auto_enfileirar_nf()
    assert s1["adiados_duvida"] == 1 and s1["adiados_estoque"] == 1
    assert s1["enfileirados"] == 1
    resumos = await _passadas(relogio, 20)  # 40 min: sem relógio, sem prazo nem teto
    assert all(r["adiados_duvida"] == 1 and r["adiados_estoque"] == 1 for r in resumos)
    fila, fats = await _estado(db)
    assert fila == ["830004"]
    assert fats == {"830004": "processando"}
    assert fake.chamadas == []


@pytest.mark.asyncio
async def test_conferencia_pura_nao_mexe_no_relogio(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, _redis_falso: _FakeRedis
):
    """`prazo=False` (o _conferir_pedidos.py da rodada ML) só confere: a dúvida
    volta crua e nada é gravado no Redis — senão quem só olha adiantaria o
    prazo do sweep."""
    from app.routers.nf import _pedidos_sem_estoque, _sem_estoque_fifo

    fake = _BlingVivo({555: 1}, {}, {700000: [(555, 1)], 700002: [(555, 1)]})
    _usa(monkeypatch, fake)
    _espelho(db, "830002", 700002, [(555, "b1", 1)])
    await db.commit()
    res = await _sem_estoque_fifo(db, ["830002"], prazo=False)
    assert res.lote == {} and res.duvidas == {"830002": ["b1"]}
    assert res.vencem_83955 == frozenset()
    assert await _pedidos_sem_estoque(db, ["830002"]) == {}
    assert _redis_falso.chaves == {}


@pytest.mark.asyncio
async def test_botao_enfileirar_pula_a_duvida_com_motivo(
    db: AsyncSession, client, admin: User, auth_as, monkeypatch: pytest.MonkeyPatch,
    relogio: _Relogio,
):
    """O botão Enfileirar também respeita a dúvida: o pedido sai como pulado
    com o motivo (sem marca, sem 83955); vencido o prazo, decide a regra do
    físico e ele entra."""
    auth_as(admin)
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    fake = _BlingVivo({555: 1, 557: 10}, {}, {700000: [(555, 1)], 700002: [(555, 1)],
                                              700004: [(557, 1)]})
    _usa(monkeypatch, fake)
    _espelho(db, "830002", 700002, [(555, "b1", 1)])
    _espelho(db, "830004", 700004, [(557, "d1", 1)])
    await db.commit()
    r = await client.post(
        "/api/nf-cadastro/faturamento/enfileirar", json={"numeros": ["830002", "830004"]}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pedidos_ok"] == 1
    [pulado] = body["pulados"]
    assert pulado["numero"] == "830002"
    assert "estoque em dúvida (b1)" in pulado["motivo"]
    assert fake.chamadas == []

    relogio.avanca(11)
    r = await client.post("/api/nf-cadastro/faturamento/enfileirar", json={"numeros": ["830002"]})
    assert r.status_code == 200, r.text
    assert r.json()["pedidos_ok"] == 1
    assert fake.chamadas == []
