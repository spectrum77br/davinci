"""Item 4, fase 4c — a TROCA DE LOTE AUTOMÁTICA (decisão (a) do Eduardo, 07/10/2026).

`services/atendimento/troca_lote_auto.py` e o registro no `worker.py`. O
Bling e a Shopee são FALSOS (os de `test_atendimento_troca`: nada sai daqui).
O que estes testes seguram:

- desligado (qualquer uma das duas chaves) não faz nada — nem o banco é lido,
  nem um GET no Bling;
- só o nível 0 (o mesmo produto em outro lote, com estoque) troca sozinho; o
  nível 1 e o 2 nunca — inclusive o mesmo produto num lote que o robô de lote
  não liberaria;
- a lista piloto — e o robô não roda com ela VAZIA (decisão (e): "*" = todos);
- só a loja Shopee entra na rodada (a troca v1 só confere a Shopee);
- a troca do robô parada no meio é retomada pela rodada (só para frente,
  nunca PUT), até o teto — depois fica para a pessoa;
- a recusa ou o erro de um pedido não param a rodada; o limite por rodada e o
  orçamento de tempo;
- idempotente: uma troca por pedido (a da pessoa, a aberta, a concluída e a
  abortada param o robô; a indisponibilidade tenta de novo depois da espera,
  até o teto) e a recusa das travas do banco descansa na memória;
- a trava da rodada no Redis e o cron no worker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import structlog
from sqlalchemy import update

from app import worker
from app.config import get_settings
from app.models import BlingOrder, NfFaturamento, StoreInfo
from app.redis_client import redis
from app.services.atendimento import etiqueta_fatos, troca, troca_lote_auto, troca_sugestoes
from tests.test_atendimento_troca import (  # noqa: F401 — fixtures (_chaves, _observacoes)
    A17_BRANCO,
    AGORA,
    ERRO_MARCA,
    LOJA,
    NUMERO,
    BlingFalso,
    ShopeeFalsa,
    _cenario,
    _chaves,
    _linhas_bo,
    _loja,
    _observacoes,
    _produtos,
    _trocas,
)

CI, SP = "dg053.ci", "dg053.sp"


@pytest.fixture(autouse=True)
def _memoria():
    troca_lote_auto.limpar_memoria()
    yield
    troca_lote_auto.limpar_memoria()


@pytest.fixture
def robo(monkeypatch):
    """As duas chaves ligadas (a da troca já vem ligada do `_chaves`, autouse) e a
    lista piloto escrita ("*" = todos: o robô não roda com ela vazia)."""
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_troca_lote_auto", True)
    monkeypatch.setattr(s, "atendimento_troca_pedidos", "*")
    return s


def _agora() -> datetime:
    return datetime.now(UTC)


# ─────────────── fábrica: vários pedidos ───────────────


class BlingVarios:
    """Um `BlingFalso` por pedido (pelo `bling_id`); os produtos são de todos."""

    def __init__(self) -> None:
        self.pedidos: dict[int, BlingFalso] = {}
        self.produtos: dict[str, dict] = {}

    def pedido(self, numero: str, bling_id: int, sku: str = CI) -> BlingFalso:
        b = BlingFalso(((sku, 1, 511),))
        b.order.update(id=bling_id, numero=numero, numeroLoja=f"SHP{numero}")
        b.produtos = self.produtos
        self.pedidos[bling_id] = b
        return b

    def produto(self, sku: str, pid: int, estoque: float) -> None:
        self.produtos[sku.lower()] = {"id": pid, "sku": sku, "stock": estoque, "name": A17_BRANCO}

    def chamadas(self) -> list[tuple]:
        return [c for b in self.pedidos.values() for c in b.chamadas]

    async def get_order(self, bling_id):
        return await self.pedidos[int(bling_id)].get_order(bling_id)

    async def update_order(self, bling_id, body):
        return await self.pedidos[int(bling_id)].update_order(bling_id, body)

    async def update_order_situacao(self, bling_id, situacao_id):
        return await self.pedidos[int(bling_id)].update_order_situacao(bling_id, situacao_id)

    async def find_active_product_by_sku(self, sku, *, estrito=False):
        p = self.produtos.get(sku.strip().lower())
        return dict(p) if p else None

    async def get_product(self, pid):
        return {"estoque": {"saldoVirtualTotal": 5}}

    async def list_pedidos_vendas(self, **kw):
        return []


async def _base(db, make_user, monkeypatch):
    """A loja Shopee, o A17 Branco nos dois lotes (o .ci zerado) e a soma por família ligada."""
    dono = await make_user()
    await _loja(db, dono)
    s = get_settings()
    monkeypatch.setattr(s, "estoque_familia_ativo", True)
    monkeypatch.setattr(s, "estoque_familia_redireciona", True)
    await _produtos(
        db, dono, [(CI, A17_BRANCO, 0, 495, 511, "S"), (SP, A17_BRANCO, 7, 495, 512, "S")]
    )
    return dono


async def _pedido_em_83955(db, numero: str, bling_id: int, *, prazo_horas: float = 20) -> None:
    """O pedido em 83955 com a marca viva de falta de estoque do dg053.ci."""
    db.add(
        BlingOrder(
            bling_id=bling_id,
            item_index=0,
            numero=numero,
            numeroloja=f"SHP{numero}",
            situacao="83955",
            loja=LOJA,
            item_codigo=CI,
            item_produto_id=511,
            item_descricao=f"Produto {CI}",
            item_quantidade=1,
            itemvalor=899,
            data=AGORA - timedelta(days=1),
            marketplace_ship_deadline=AGORA + timedelta(hours=prazo_horas),
        )
    )
    quando = AGORA - timedelta(hours=3)
    db.add(
        NfFaturamento(
            pedido_bling=numero,
            status_faturamento="sem_estoque",
            erro_faturamento=ERRO_MARCA.format(CI),
            created_at=quando,
            updated_at=quando,
        )
    )
    await db.commit()


def _ligar_varios(monkeypatch, bling, shopee=None):
    shopee = shopee if shopee is not None else ShopeeFalsa()

    async def _b(session):
        return bling

    async def _s(session, p):
        return shopee

    monkeypatch.setattr(troca, "_cliente_bling", _b)
    monkeypatch.setattr(troca, "_cliente_shopee", _s)
    return shopee


def _espiao(monkeypatch, comportamento=None):
    """Troca o `executar` por um espião: registra a chamada e faz o que mandarem."""
    chamadas: list[dict] = []
    comportamento = comportamento or {}

    async def _executar(session, user, **kw):
        chamadas.append({"user": user, **kw})
        acao = comportamento.get(kw["numero_bling"])
        if isinstance(acao, Exception):
            raise acao
        return SimpleNamespace(
            id=uuid4(),
            estado=acao or "concluida",
            sku_antigo=kw["sku_antigo"],
            sku_novo=kw["sku_novo"],
            codigo_erro=None if acao in (None, "concluida") else "bling_indisponivel",
        )

    monkeypatch.setattr(troca, "executar", _executar)
    return chamadas


# ─────────────── as chaves ───────────────


@pytest.mark.parametrize("ativa,lote_auto", [(False, False), (True, False), (False, True)])
async def test_desligado_nao_faz_nada(db, make_user, monkeypatch, ativa, lote_auto):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_troca_ativa", ativa)
    monkeypatch.setattr(s, "atendimento_troca_lote_auto", lote_auto)
    monkeypatch.setattr(worker._settings, "atendimento_troca_ativa", ativa)
    monkeypatch.setattr(worker._settings, "atendimento_troca_lote_auto", lote_auto)

    def _explode(*a, **kw):
        raise AssertionError("desligado não pode ler o banco")

    # Nem uma sessão é aberta, nem a lista de pedidos lida.
    monkeypatch.setattr(troca_lote_auto, "_db", SimpleNamespace(SessionLocal=_explode))
    monkeypatch.setattr(etiqueta_fatos, "pedidos_em_83955", _explode)
    monkeypatch.setattr(troca, "executar", _explode)

    assert await troca_lote_auto.rodada() is None
    assert await troca_lote_auto.atendimento_troca_lote({}) is None
    assert await worker.atendimento_troca_lote({}) is None
    assert c.bling.chamadas == [] and c.shopee.chamadas == []
    assert await _trocas(db) == []


# ─────────────── o nível 0, sozinho ───────────────


async def test_troca_o_lote_sozinho(db, make_user, monkeypatch, robo):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    with structlog.testing.capture_logs() as logs:
        resumo = await troca_lote_auto.rodada()
    assert resumo is not None
    assert (resumo["candidatos"], resumo["tentadas"], resumo["concluidas"]) == (1, 1, 1)
    assert (resumo["recusadas"], resumo["falhas"], resumo["paradas"]) == (0, 0, 0)

    [t] = await _trocas(db)
    assert (t.estado, t.automatica, t.nivel, t.criado_por) == ("concluida", True, 0, None)
    assert (t.sku_antigo, t.sku_novo, t.aceite_fonte) == (CI, SP, None)
    assert len(c.bling.puts()) == 1 and c.bling.patches() == [9, 6]
    # Sem pessoa, a Margem NÃO é aprovada: o pino fica como estava.
    [bo] = await _linhas_bo(db)
    assert (bo.item_codigo, bo.situacao, bo.status, bo.aprovado_por) == (SP, "6", None, None)

    trocou = [x for x in logs if x["event"] == "atendimento_troca_lote_trocou"]
    assert trocou and trocou[0]["pedido"] == NUMERO and trocou[0]["para"] == SP
    # O log não leva nada do comprador.
    assert "Comprador Fictício" not in repr(logs)


async def test_idempotente_o_pedido_trocado_nao_e_trocado_de_novo(db, make_user, monkeypatch, robo):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    assert (await troca_lote_auto.rodada())["concluidas"] == 1
    c.bling.chamadas.clear()

    # A rodada seguinte: o pedido já está em Em aberto — nada a fazer.
    resumo = await troca_lote_auto.rodada()
    assert (resumo["candidatos"], resumo["tentadas"]) == (0, 0)

    # O pedido volta a 83955 por falta de estoque (marca nova, depois da
    # troca): uma troca por pedido — o robô não troca de novo.
    await db.execute(update(BlingOrder).where(BlingOrder.numero == NUMERO).values(situacao="83955"))
    await db.execute(
        update(NfFaturamento)
        .where(NfFaturamento.pedido_bling == NUMERO)
        .values(
            status_faturamento="sem_estoque",
            erro_faturamento=ERRO_MARCA.format(SP),
            updated_at=_agora() + timedelta(minutes=1),
        )
    )
    await db.commit()
    resumo = await troca_lote_auto.rodada()
    assert resumo["tentadas"] == 0
    assert resumo["puladas"] == {"ja_trocado": 1}
    assert c.bling.chamadas == [] and len(await _trocas(db)) == 1


# ─────────────── nunca os níveis 1 e 2 ───────────────


async def test_nivel_1_nunca(db, make_user, monkeypatch, robo):
    c = await _cenario(db, make_user, monkeypatch)  # dg053.sp → dg054.sp (outra cor)
    resumo = await troca_lote_auto.rodada()
    assert (resumo["candidatos"], resumo["tentadas"]) == (0, 0)
    assert resumo["puladas"] == {"sem_lote_com_estoque": 1}
    assert c.bling.chamadas == [] and await _trocas(db) == []


async def test_mesmo_produto_em_lote_que_o_robo_de_lote_nao_libera_nunca(
    db, make_user, monkeypatch, robo
):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    # Sem a soma por família (nem a prioridade de lote), o mesmo produto em
    # outro lote é nível 1: pede a caixinha do aceite — o robô não troca.
    monkeypatch.setattr(robo, "estoque_familia_ativo", False)
    resumo = await troca_lote_auto.rodada()
    assert (resumo["candidatos"], resumo["tentadas"]) == (0, 0)
    assert c.bling.chamadas == [] and await _trocas(db) == []


async def test_so_o_nivel_0_com_estoque_entra(db, make_user, monkeypatch, robo):
    """O filtro é o nível 0 com estoque: o 1 e o 2 (com estoque) e o 0 sem estoque ficam fora."""
    await _cenario(db, make_user, monkeypatch, nivel0=True)

    def _sug(sku, nivel, motivo_fora=None):
        return troca_sugestoes.Sugestao(
            sku=sku,
            nome=A17_BRANCO,
            nivel=nivel,
            estoque=0 if motivo_fora else 9,
            estoque_em=None,
            produto_id=None,
            dif_custo_pct=0.0,
            motivo_fora=motivo_fora,
        )

    sugestoes = [
        _sug("dg054.sp", troca_sugestoes.NIVEL_MODELO),
        _sug("dg060.sp", troca_sugestoes.NIVEL_ESPECIFICACAO),
        _sug(SP, troca_sugestoes.NIVEL_LOTE, troca_sugestoes.FORA_SEM_ESTOQUE),
    ]
    monkeypatch.setattr(troca_sugestoes, "candidatos", lambda *a, **kw: list(sugestoes))
    chamadas = _espiao(monkeypatch)
    resumo = await troca_lote_auto.rodada()
    assert resumo["puladas"] == {"sem_lote_com_estoque": 1} and chamadas == []

    sugestoes.append(_sug("dg053.ra", troca_sugestoes.NIVEL_LOTE))
    await troca_lote_auto.rodada()
    assert [(x["sku_antigo"], x["sku_novo"]) for x in chamadas] == [(CI, "dg053.ra")]
    assert chamadas[0]["user"] is None and chamadas[0]["automatica"] is True


# ─────────────── o piloto ───────────────


async def test_piloto(db, make_user, monkeypatch, robo):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    monkeypatch.setattr(robo, "atendimento_troca_pedidos", "299999, 299998")
    resumo = await troca_lote_auto.rodada()
    assert (resumo["candidatos"], resumo["puladas"]) == (0, {"fora_do_piloto": 1})
    assert c.bling.chamadas == [] and await _trocas(db) == []

    monkeypatch.setattr(robo, "atendimento_troca_pedidos", f"299999,{NUMERO}")
    resumo = await troca_lote_auto.rodada()
    assert resumo["concluidas"] == 1


# ─────────────── um pedido não para a rodada ───────────────


async def test_falha_de_um_pedido_nao_para_a_rodada(db, make_user, monkeypatch, robo):
    await _base(db, make_user, monkeypatch)
    await _pedido_em_83955(db, "300203", 70203, prazo_horas=30)
    await _pedido_em_83955(db, "300201", 70201, prazo_horas=5)
    await _pedido_em_83955(db, "300202", 70202, prazo_horas=10)
    chamadas = _espiao(
        monkeypatch,
        {
            "300201": RuntimeError("o DaVinci quebrou"),
            "300202": troca.TrocaRecusada("prazo_vencido"),
        },
    )
    agora = _agora()
    with structlog.testing.capture_logs() as logs:
        resumo = await troca_lote_auto.rodada(agora=agora)
    # O prazo mais curto primeiro; o erro e a recusa não param a rodada.
    assert [x["numero_bling"] for x in chamadas] == ["300201", "300202", "300203"]
    assert all(x["user"] is None and x["automatica"] is True for x in chamadas)
    assert len({x["idem_key"] for x in chamadas}) == 3
    assert (resumo["tentadas"], resumo["falhas"], resumo["recusadas"]) == (3, 1, 1)
    assert resumo["concluidas"] == 1 and resumo["recusas"] == {"prazo_vencido": 1}
    eventos = {x["event"] for x in logs}
    assert {
        "atendimento_troca_lote_pedido_falhou",
        "atendimento_troca_lote_recusada",
        "atendimento_troca_lote_trocou",
    } <= eventos
    # Só o tipo do erro, nunca a mensagem.
    assert "o DaVinci quebrou" not in repr(logs)

    # A recusa antes da linha e o erro descansam (o `executar` relê o
    # catálogo inteiro): na rodada seguinte, só o terceiro.
    chamadas.clear()
    resumo = await troca_lote_auto.rodada(agora=agora + timedelta(minutes=10))
    assert [x["numero_bling"] for x in chamadas] == ["300203"]
    assert resumo["puladas"] == {"recusa_recente": 2}
    # Passada a espera, voltam.
    chamadas.clear()
    await troca_lote_auto.rodada(agora=agora + troca_lote_auto.ESPERA_RECUSA + timedelta(seconds=1))
    assert [x["numero_bling"] for x in chamadas] == ["300201", "300202", "300203"]


async def test_bling_recusa_um_e_o_outro_troca(db, make_user, monkeypatch, robo):
    """Com o `executar` de verdade: o PUT recusado (4xx) num pedido e o outro troca."""
    await _base(db, make_user, monkeypatch)
    await _pedido_em_83955(db, "300301", 70301, prazo_horas=5)
    await _pedido_em_83955(db, "300302", 70302, prazo_horas=10)
    bling = BlingVarios()
    bling.produto(SP, 512, 9)
    bling.pedido("300301", 70301).falha_put = "4xx"
    bling.pedido("300302", 70302)
    _ligar_varios(monkeypatch, bling)

    resumo = await troca_lote_auto.rodada()
    assert (resumo["tentadas"], resumo["recusadas"], resumo["concluidas"]) == (2, 1, 1)
    assert resumo["recusas"] == {"bling_recusou": 1}
    estados = {t.pedido_bling: (t.estado, t.codigo_erro) for t in await _trocas(db)}
    assert estados == {"300301": ("abortada", "bling_recusou"), "300302": ("concluida", None)}
    assert bling.pedidos[70302].patches() == [9, 6]
    assert bling.pedidos[70301].patches() == []

    # A rodada seguinte: o recusado pelo Bling não volta (uma troca por
    # pedido); o trocado já saiu de 83955. Nenhuma chamada nova ao Bling.
    antes = len(bling.chamadas())
    resumo = await troca_lote_auto.rodada()
    assert (resumo["tentadas"], resumo["puladas"]) == (0, {"abortada": 1})
    assert len(bling.chamadas()) == antes


# ─────────────── o limite e o orçamento ───────────────


async def test_limite_por_rodada(db, make_user, monkeypatch, robo):
    await _base(db, make_user, monkeypatch)
    for i, horas in enumerate((30, 5, 10)):
        await _pedido_em_83955(db, f"30040{i}", 70400 + i, prazo_horas=horas)
    chamadas = _espiao(monkeypatch)
    resumo = await troca_lote_auto.rodada(limite=2)
    assert [x["numero_bling"] for x in chamadas] == ["300401", "300402"]
    assert (resumo["candidatos"], resumo["tentadas"], resumo["cortadas"]) == (3, 2, 1)
    assert troca_lote_auto.MAX_POR_RODADA == 10


async def test_orcamento_de_tempo_nao_comeca_troca_nova(db, make_user, monkeypatch, robo):
    await _base(db, make_user, monkeypatch)
    await _pedido_em_83955(db, "300501", 70501)
    await _pedido_em_83955(db, "300502", 70502)
    chamadas = _espiao(monkeypatch)
    resumo = await troca_lote_auto.rodada(orcamento_s=0)
    assert chamadas == [] and (resumo["tentadas"], resumo["cortadas"]) == (0, 2)
    # O orçamento cabe com folga no `timeout` do cron (o arq não mata no meio).
    assert troca_lote_auto.ORCAMENTO_S < troca_lote_auto.RODADA_TTL_S


async def test_desligar_no_meio_para_a_rodada(db, make_user, monkeypatch, robo):
    await _base(db, make_user, monkeypatch)
    await _pedido_em_83955(db, "300601", 70601, prazo_horas=5)
    await _pedido_em_83955(db, "300602", 70602, prazo_horas=10)
    chamadas: list[str] = []

    async def _executar(session, user, **kw):
        chamadas.append(kw["numero_bling"])
        monkeypatch.setattr(robo, "atendimento_troca_lote_auto", False)
        raise troca.TrocaRecusada("troca_lote_auto_desligada")

    monkeypatch.setattr(troca, "executar", _executar)
    resumo = await troca_lote_auto.rodada()
    assert chamadas == ["300601"] and resumo["cortadas"] == 1
    # A recusa da chave não vira memória: religada, o pedido entra de novo.
    assert troca_lote_auto._recusa_lembrada("300601", _agora()) is None


# ─────────────── uma troca por pedido ───────────────


def _linha(estado, *, automatica=True, codigo=None, minutos=60):
    return {"estado": estado, "automatica": automatica, "codigo_erro": codigo, "minutos": minutos}


@pytest.mark.parametrize(
    "linhas,esperado",
    [
        ([], None),
        ([_linha("abortada", automatica=False, codigo="previa_mudou")], "troca_de_pessoa"),
        ([_linha("concluida", automatica=False)], "troca_de_pessoa"),
        ([_linha("incerta", codigo="bling_indisponivel")], "troca_aberta"),
        ([_linha("em_atendido")], "troca_aberta"),
        ([_linha("concluida")], "ja_trocado"),
        ([_linha("abortada", codigo="saldo_insuficiente")], "abortada"),
        ([_linha("abortada", codigo="bling_recusou")], "abortada"),
        ([_linha("abortada", codigo="bling_indisponivel", minutos=10)], "esperando"),
        ([_linha("abortada", codigo="plataforma_indisponivel", minutos=45)], None),
        ([_linha("abortada", codigo="peca_em_disputa", minutos=45)], None),
        (
            [_linha("abortada", codigo="bling_indisponivel", minutos=m) for m in (40, 90, 200)],
            "tentativas_esgotadas",
        ),
    ],
)
def test_bloqueio(linhas, esperado):
    agora = _agora()
    trocas = [
        SimpleNamespace(
            estado=x["estado"],
            automatica=x["automatica"],
            codigo_erro=x["codigo_erro"],
            created_at=agora - timedelta(minutes=x["minutos"]),
        )
        for x in linhas
    ]
    assert troca_lote_auto._bloqueio(trocas, agora) == esperado


async def test_indisponivel_tenta_de_novo_depois_da_espera(db, make_user, monkeypatch, robo):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    c.shopee.falha = True  # a Shopee não responde: nada é escrito no Bling
    agora = _agora()
    resumo = await troca_lote_auto.rodada(agora=agora)
    assert resumo["recusas"] == {"plataforma_indisponivel": 1}
    assert c.bling.escritas() == []
    [t] = await _trocas(db)
    assert (t.estado, t.codigo_erro, t.automatica) == ("abortada", "plataforma_indisponivel", True)

    # Logo depois: espera.
    resumo = await troca_lote_auto.rodada(agora=agora + timedelta(minutes=10))
    assert (resumo["tentadas"], resumo["puladas"]) == (0, {"esperando": 1})
    # Passada a espera, com a Shopee de volta: troca.
    c.shopee.falha = False
    resumo = await troca_lote_auto.rodada(
        agora=agora + troca_lote_auto.ESPERA_TRANSITORIA + timedelta(minutes=1)
    )
    assert resumo["concluidas"] == 1
    assert sorted(t.estado for t in await _trocas(db)) == ["abortada", "concluida"]


async def test_troca_da_pessoa_segura_o_robo(db, make_user, monkeypatch, robo):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    # A pessoa tentou e o Bling recusou: o robô não passa por cima.
    c.bling.falha_put = "4xx"
    previa = await troca.previa(db, c.adm, numero_bling=NUMERO, sku_antigo=CI, sku_novo=SP)
    with pytest.raises(troca.TrocaRecusada):
        await troca.executar(
            db,
            c.adm,
            numero_bling=NUMERO,
            sku_antigo=CI,
            sku_novo=SP,
            idem_key=uuid4(),
            previa_hash=previa["previa_hash"],
        )
    c.bling.falha_put = None
    c.bling.chamadas.clear()
    resumo = await troca_lote_auto.rodada()
    assert (resumo["tentadas"], resumo["puladas"]) == (0, {"troca_de_pessoa": 1})
    assert c.bling.chamadas == []


# ─────────────── o cron ───────────────


async def test_rodada_ocupada_sai_na_hora(db, make_user, monkeypatch, robo):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    chave = troca_lote_auto._CHAVE_RODADA.format(get_settings().database_schema)
    await redis.delete(chave)
    try:
        await redis.set(chave, "outra-rodada", ex=60)
        assert await troca_lote_auto.atendimento_troca_lote({}) is None
        assert c.bling.chamadas == [] and await _trocas(db) == []
        await redis.delete(chave)
        resumo = await troca_lote_auto.atendimento_troca_lote({})
        assert resumo is not None and resumo["concluidas"] == 1
        # A trava é solta no fim.
        assert await redis.get(chave) is None
    finally:
        await redis.delete(chave)


async def test_worker_respeita_as_chaves(monkeypatch):
    s = worker._settings
    rodada = AsyncMock(return_value={"tentadas": 0})
    monkeypatch.setattr(troca_lote_auto, "atendimento_troca_lote", rodada)
    monkeypatch.setattr(s, "atendimento_troca_ativa", True)
    monkeypatch.setattr(s, "atendimento_troca_lote_auto", False)
    assert await worker.atendimento_troca_lote({}) is None
    rodada.assert_not_awaited()
    monkeypatch.setattr(s, "atendimento_troca_lote_auto", True)
    assert await worker.atendimento_troca_lote({}) == {"tentadas": 0}
    rodada.assert_awaited_once()


def test_cron_no_worker():
    crons = {c.name: c for c in worker.WorkerSettings.cron_jobs}
    tl = crons["cron:atendimento_troca_lote"]
    assert tl.coroutine is worker.atendimento_troca_lote
    assert tl.minute == worker._ATENDIMENTO_TROCA_LOTE_MINUTOS == {3, 13, 23, 33, 43, 53}
    assert not tl.run_at_startup
    # Fora dos crons de token (:00/:30; o do Bling no :15) e do Robô da Margem (:15/:45).
    assert not {0, 15, 30, 45} & tl.minute
    # A trava da rodada dura o `timeout`: o job morto não segura a próxima.
    assert tl.timeout_s == troca_lote_auto.RODADA_TTL_S == 540 < 10 * 60
    funcoes = {getattr(f, "coroutine", f): f for f in worker.WorkerSettings.functions}
    assert worker.atendimento_troca_lote in funcoes
    assert funcoes[worker.atendimento_troca_lote].max_tries == 1


# ─────────────── as correções da revisão (08/10) ───────────────


async def test_lista_piloto_vazia_nao_solta_o_robo(db, make_user, monkeypatch, robo):
    """Decisão (e): as duas chaves ligadas com a lista esquecida não trocam todos os
    pedidos de uma vez — o cron só avisa no log."""
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    monkeypatch.setattr(robo, "atendimento_troca_pedidos", "")
    assert troca_lote_auto.ligado() is False
    with structlog.testing.capture_logs() as logs:
        assert await troca_lote_auto.atendimento_troca_lote({}) is None
    assert "atendimento_troca_lote_sem_lista_piloto" in {x["event"] for x in logs}
    assert await troca_lote_auto.rodada() is None
    assert c.bling.chamadas == [] and await _trocas(db) == []
    # "*" = todos, dito com todas as letras.
    monkeypatch.setattr(robo, "atendimento_troca_pedidos", "*")
    assert (await troca_lote_auto.rodada())["concluidas"] == 1


async def test_so_a_loja_shopee_entra_na_rodada(db, make_user, monkeypatch, robo):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    await db.execute(
        update(StoreInfo).where(StoreInfo.bling_store_id == LOJA).values(platform="ml")
    )
    await db.commit()
    chamadas = _espiao(monkeypatch)
    resumo = await troca_lote_auto.rodada()
    assert (resumo["candidatos"], resumo["puladas"]) == (0, {"fora_da_shopee": 1})
    assert chamadas == [] and c.bling.chamadas == []


async def test_robo_retoma_a_troca_dele_parada_no_meio(db, make_user, monkeypatch, robo):
    """O PATCH 6 levou 429: o pedido ficou em Atendido (9), o limbo que nenhum robô
    pega. A rodada seguinte RETOMA (GET primeiro, nunca PUT) e o pedido volta a 6."""
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    c.bling.falha_patch = {6}
    resumo = await troca_lote_auto.rodada()
    assert (resumo["paradas"], resumo["retomadas"]["tentadas"]) == (1, 0)
    [t] = await _trocas(db)
    assert (t.estado, t.automatica) == ("em_atendido", True)
    c.bling.falha_patch = set()
    with structlog.testing.capture_logs() as logs:
        resumo = await troca_lote_auto.rodada()
    assert resumo["retomadas"]["tentadas"] == 1 and resumo["retomadas"]["concluidas"] == 1
    # O pedido não é trocado de novo: a troca do robô segue a mesma.
    assert resumo["tentadas"] == 0
    [t] = await _trocas(db)
    assert t.estado == "concluida"
    assert len(c.bling.puts()) == 1 and c.bling.patches() == [9, 6, 6]
    assert "atendimento_troca_lote_retomou" in {x["event"] for x in logs}


async def test_retomada_do_robo_tem_teto(db, make_user, monkeypatch, robo):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    c.bling.falha_patch = {6}
    await troca_lote_auto.rodada()
    for _ in range(troca_lote_auto.MAX_RETOMADAS):
        resumo = await troca_lote_auto.rodada()
        assert (resumo["retomadas"]["tentadas"], resumo["retomadas"]["paradas"]) == (1, 1)
    c.bling.chamadas.clear()
    with structlog.testing.capture_logs() as logs:
        resumo = await troca_lote_auto.rodada()
    # Esgotada: fica para a pessoa (a lista "Trocas paradas no meio"), sem Bling.
    assert (resumo["retomadas"]["tentadas"], resumo["retomadas"]["esgotadas"]) == (0, 1)
    assert c.bling.chamadas == []
    assert "atendimento_troca_lote_parada_para_a_pessoa" in {x["event"] for x in logs}
    [t] = await _trocas(db)
    assert (t.estado, t.codigo_erro) == ("em_atendido", "bling_indisponivel")
