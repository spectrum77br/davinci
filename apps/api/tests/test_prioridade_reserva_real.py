"""Robô de prioridade contra um Bling que RESERVA estoque de verdade.

Revisão de 28/09/2026 do conserto do vai-e-volta: com saldo fixo nos testes, três
defeitos passavam batido — só aparecem quando o saldo virtual é "físico menos o
que os pedidos em aberto seguram" e a troca move a reserva, como no Bling:
  * a trava anti-volta segurava a SAÍDA de um lote negativo (o check de estoque
    do enfileirar mandaria o pedido para Aguardando Cancelamento);
  * de um lote a -1 saíam TODOS os pedidos, quando bastava um;
  * dois kits de pedidos diferentes pegavam o mesmo fone (a001) na mesma rodada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingOrder, MargemAudit
from app.services import estoque_familia, nf_emissao_gerar
from app.services import prioridade_estoque as prio


class BlingComReserva:
    """Virtual = físico − o que os pedidos em aberto seguram; kit = a peça mais
    escassa. O PUT troca o item e, com ele, a reserva (modo substituir)."""

    def __init__(self, fisico: dict[str, int], pedidos: dict[str, list[tuple[str, int]]]):
        self.fisico = {k.lower(): v for k, v in fisico.items()}
        self.pedidos = {n: [(c.lower(), q) for c, q in itens] for n, itens in pedidos.items()}
        self.puts: list[tuple[str, list[str]]] = []

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

    def _existe(self, sku: str) -> bool:
        return all(p in self.fisico for p in self._pecas(sku))

    async def find_active_product_by_sku(self, sku, estrito=False):
        if not self._existe(sku):
            return None
        return {"id": abs(hash(sku.lower())) % 10**6, "sku": sku.lower(), "name": sku,
                "stock": self.virtual(sku)}

    async def get_order(self, bling_id):
        numero = str(bling_id)
        return {"id": bling_id, "numero": numero, "situacao": {"id": 6}, "contato": {"id": 1},
                "loja": {"id": 2}, "observacoes": "",
                "itens": [{"id": 50 + i, "codigo": c, "quantidade": q, "valor": 10.0,
                           "produto": {"id": 1}} for i, (c, q) in enumerate(self.pedidos[numero])]}

    async def update_order(self, bling_id, body):
        numero = str(bling_id)
        novos = [(i["codigo"].lower(), int(i["quantidade"])) for i in body["itens"]]
        self.pedidos[numero] = novos
        self.puts.append((numero, [c for c, _ in novos]))
        return body


@pytest.fixture
def cenario(db: AsyncSession, monkeypatch):
    """Configuração de produção: prioridade ci, soma por família ligada e
    redirecionando, substituir item ligado (sem compensação por fora)."""
    cfg = get_settings()
    monkeypatch.setattr(cfg, "prioridade_substitui_item", True)
    monkeypatch.setattr(cfg, "prioridade_pedido_estoque_unico", True)
    monkeypatch.setattr(cfg, "estoque_familia_redireciona", True)

    class _Familia:
        estoque_familia_ativo = True
        estoque_familia_prefixos = "dg052,dg053,dg054,dg057"
        estoque_familia_minimo = 5

    monkeypatch.setattr(estoque_familia, "get_settings", lambda: _Familia())

    async def _mapa(session):
        return {"dg052": "ci", "dg053": "ci", "dg054": "ci", "dg057": "ci"}

    monkeypatch.setattr(prio, "_mapa_prioridades", _mapa)

    async def _montar(fisico, pedidos):
        bling = BlingComReserva(fisico, pedidos)
        for numero, itens in pedidos.items():
            for i, (cod, q) in enumerate(itens):
                db.add(BlingOrder(numero=numero, bling_id=int(numero), loja="5001", item_index=i,
                                  item_codigo=cod, item_quantidade=q, situacao="6",
                                  data=datetime.now(UTC)))
        await db.commit()

        async def _client(session):
            return bling

        monkeypatch.setattr(nf_emissao_gerar, "_bling_client_opt", _client)
        return bling

    return _montar


@pytest.mark.asyncio
async def test_de_um_lote_negativo_sai_so_o_excedente(db: AsyncSession, cenario):
    """dg052.ci com 2 peças e 3 pedidos (virtual -1), sp com 10: sai UM pedido
    (antes: os três, e dois voltavam uma hora depois)."""
    bling = await cenario(
        {"dg052.ci": 2, "dg052.sp": 10},
        {"610001": [("dg052.ci", 1)], "610002": [("dg052.ci", 1)], "610003": [("dg052.ci", 1)]},
    )
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["610001", "610002", "610003"])
    await db.commit()
    assert resumo["trocados"] == 1 and len(bling.puts) == 1
    assert bling.virtual("dg052.ci") == 0

    # e na rodada seguinte ninguém volta nem sai
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["610001", "610002", "610003"])
    await db.commit()
    assert resumo["trocados"] == 0 and len(bling.puts) == 1


@pytest.mark.asyncio
async def test_trava_nao_segura_saida_de_lote_negativo(db: AsyncSession, cenario):
    """O robô trouxe o pedido para o ci há 10 min; depois o ci ficou a -1 (venda
    nova por outro anúncio). Sair do ci é necessário — a trava não segura."""
    bling = await cenario({"dg057.ci": 0, "dg057.sp": 36}, {"620001": [("dg057.ci", 1)]})
    db.add(MargemAudit(
        pedido_bling="620001", bling_id="620001", sku="dg057.sp",
        valor_antigo="dg057.sp", valor_novo="dg057.ci", origem="prioridade_estoque",
        acao="sku", created_at=datetime.now(UTC) - timedelta(minutes=10),
    ))
    await db.commit()
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["620001"])
    await db.commit()
    assert resumo["trocados"] == 1
    assert bling.puts == [("620001", ["dg057.sp"])]


@pytest.mark.asyncio
async def test_trava_segura_so_a_volta_opcional(db: AsyncSession, cenario):
    """O inverso: o pedido está no sp (que atende) e o robô o tirou do ci há 10
    min. O ci tem peça de novo, mas voltar é só preferência: espera a janela."""
    bling = await cenario({"dg057.ci": 3, "dg057.sp": 36}, {"630001": [("dg057.sp", 1)]})
    db.add(MargemAudit(
        pedido_bling="630001", bling_id="630001", sku="dg057.ci",
        valor_antigo="dg057.ci", valor_novo="dg057.sp", origem="prioridade_estoque",
        acao="sku", created_at=datetime.now(UTC) - timedelta(minutes=10),
    ))
    await db.commit()
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["630001"])
    assert resumo["trocados"] == 0 and resumo["anti_vai_e_volta"] == 1 and bling.puts == []


@pytest.mark.asyncio
async def test_fone_compartilhado_nao_e_prometido_duas_vezes(db: AsyncSession, cenario):
    """Dois pedidos de kits DIFERENTES no sp (dg053+a001 e dg054+a001), e no ci
    só 1 fone a001 sobrando: só UM vem para o ci (antes: os dois, e o a001.ci ia
    a -1 — que expulsava todo kit ci do A17 na rodada seguinte)."""
    bling = await cenario(
        # 640000 já está no ci (e é lido primeiro: guarda o saldo do kit dg054.ci
        # na rodada); o a001.ci tem 2, um dele — sobra 1.
        {"dg053.ci": 5, "dg054.ci": 5, "a001.ci": 2,
         "dg053.sp": 5, "dg054.sp": 5, "a001.sp": 10},
        {"640000": [("dg054.ci+a001.ci", 1)],
         "640001": [("dg053.sp+a001.sp", 1)], "640002": [("dg054.sp+a001.sp", 1)]},
    )
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["640000", "640001", "640002"])
    await db.commit()
    assert resumo["trocados"] == 1 and len(bling.puts) == 1
    assert bling.puts[0][0] == "640001"
    assert bling.virtual("a001.ci") == 0


@pytest.mark.asyncio
async def test_saldo_zero_do_proprio_pedido_nao_troca(db: AsyncSession, cenario):
    """O 298543: 1 dg057.ci, o pedido a reservou (virtual 0). Fica no ci — antes
    ia para o sp e voltava a cada 2 min (486 trocas em 24 h)."""
    bling = await cenario({"dg057.ci": 1, "a001.ci": 5, "dg057.sp": 36, "a001.sp": 50},
                          {"650001": [("dg057.ci+a001.ci", 1)]})
    for _ in range(3):
        await prio.aplicar_prioridade_estoque(db, numeros=["650001"])
        await db.commit()
    assert bling.puts == []


@pytest.mark.asyncio
async def test_pedido_misturado_com_item_em_lote_negativo_nao_e_segurado(
    db: AsyncSession, cenario
):
    """Pedido [dg054.ci, dg052.sp]; o robô tinha tirado o dg052 do ci há 10
    min, e agora o dg052.sp ficou a -1 e o ci tem peça. Pedido misturado não
    tem "estoque atual" único: a necessidade é item por item — e este item
    PRECISA sair, então a trava não segura (senão: Aguardando Cancelamento)."""
    bling = await cenario(
        {"dg054.ci": 5, "dg054.sp": 5, "dg052.ci": 3, "dg052.sp": 0},
        {"660001": [("dg054.ci", 1), ("dg052.sp", 1)]},
    )
    db.add(MargemAudit(
        pedido_bling="660001", bling_id="660001", sku="dg052.ci",
        valor_antigo="dg052.ci", valor_novo="dg052.sp", origem="prioridade_estoque",
        acao="sku", created_at=datetime.now(UTC) - timedelta(minutes=10),
    ))
    await db.commit()
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["660001"])
    await db.commit()
    assert resumo["trocados"] == 1
    assert bling.puts == [("660001", ["dg054.ci", "dg052.ci"])]


@pytest.mark.asyncio
async def test_troca_obrigatoria_que_toma_429_e_adiada(db: AsyncSession, cenario):
    """O pedido precisa sair do dg057.ci (-1), mas o GET do pedido toma 429:
    fica em 'adiados' — o gancho do enfileirar não o manda para o check de
    estoque (que o poria em Aguardando Cancelamento com 36 no sp)."""
    from app.services.marketplaces.bling import BlingCloudflareError

    bling = await cenario({"dg057.ci": 0, "dg057.sp": 36}, {"670001": [("dg057.ci", 1)]})

    async def _429(bling_id):
        raise BlingCloudflareError("429 Too Many Requests")

    bling.get_order = _429
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["670001"])
    assert resumo["adiados"] == ["670001"] and resumo["falhas"] == 1
    assert bling.puts == []


@pytest.mark.asyncio
async def test_recusa_definitiva_do_bling_nao_adia(db: AsyncSession, cenario):
    """Erro de validação do Bling (definitivo) não prende o pedido fora do check."""
    bling = await cenario({"dg057.ci": 0, "dg057.sp": 36}, {"680001": [("dg057.ci", 1)]})

    async def _recusa(bling_id, body):
        raise ValueError("erro 67: venda inválida")

    bling.update_order = _recusa
    resumo = await prio.aplicar_prioridade_estoque(db, numeros=["680001"])
    assert "adiados" not in resumo and resumo["falhas"] == 1
