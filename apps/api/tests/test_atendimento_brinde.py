"""O brinde do pedido não pago: o botão e a trava da NF (o dono, 05/11/2026).

`services/atendimento/brinde.py` e `routers/atendimento_brinde.py`. O Bling é FALSO
(nada sai daqui): registra cada GET e PUT e aplica o PUT no pedido dele. O que estes
testes seguram:

- a trava da NF: com oferta fica fora; sem oferta, igual; expira e volta sozinho;
  adicionado/dispensado soltam; incerto não solta; erro na consulta não derruba;
- adicionar: o feliz (o corpo do último GET, o item sem id, o desconto +0,01, as
  Observações, o espelho, o pino da Margem, a nota); o lote certo; kit; .us nunca;
  sem saldo vai para .cd; idempotente; NF emitida recusa; 4xx; timeout vira incerto
  e a próxima ação começa pelo GET, nunca repete o PUT às cegas; dispensar;
- expirar: uma vez só, com a nota;
- o acesso e o escopo das rotas; a migration sobe e desce.
"""

from __future__ import annotations

import copy
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, select, text, update

from app.config import get_settings
from app.models import (
    AtendimentoAutomacaoRegistro,
    AtendimentoBrinde,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    MargemAudit,
    NfFaturamento,
    StoreInfo,
    UserRole,
)
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services import nf_auto_enfileirar
from app.services.atendimento import brinde, painel, troca
from tests.lojas_da_pessoa import dar_lojas

NUMERO = "390101"
BLING_ID = 88001
PEDIDO = "SHPBRINDE1"
LOJA = "7301"
URL = "/api/atendimento/brindes"
MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/0455_atendimento_brindes.py"


def _http(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("PUT", "https://api.bling.com.br/Api/v3/pedidos/vendas/1")
    corpo = '{"error":{"fields":[{"msg":"A soma das parcelas difere do total"}]}}'
    return httpx.HTTPStatusError(
        str(status), request=req, response=httpx.Response(status, request=req, text=corpo)
    )


class BlingFalso:
    """O Bling: registra cada chamada e APLICA o PUT no pedido dele."""

    def __init__(self, itens=None, *, situacao=6, saldos=None, nf=0, desconto=None):
        self.order = {
            "id": BLING_ID,
            "numero": NUMERO,
            "numeroLoja": PEDIDO,
            "situacao": {"id": situacao, "valor": 0},
            "observacoes": "obs antiga",
            "contato": {"id": 1, "nome": "Cliente"},
            "loja": {"id": int(LOJA)},
            "notaFiscal": {"id": nf},
            "transporte": {"volumes": []},
            "total": 899.0,
            "totalProdutos": 899.0,
            "parcelas": [{"id": 5, "valor": 899.0}],
            "desconto": desconto if desconto is not None else {"valor": 10.0, "unidade": "REAL"},
            "itens": itens
            or [
                {
                    "id": 11,
                    "codigo": "dg002.sp",
                    "descricao": "Uranyx A17",
                    "quantidade": 1,
                    "valor": 909.0,
                    "produto": {"id": 501},
                }
            ],
        }
        self.saldos = {
            "a019.cd": 2598,
            "a019.ci": 99,
            "a019.sp": 100,
            "a075": 4937,
            **(saldos or {}),
        }
        self.chamadas: list[str] = []
        self.corpos: list[dict] = []
        self.falha_put: str | None = None
        self.falha_get = 0
        # O que o Bling muda no pedido além do corpo (total, parcelas).
        self.depois_do_put: dict = {}

    async def get_order(self, bling_id):
        self.chamadas.append("get")
        if self.falha_get > 0:
            self.falha_get -= 1
            raise httpx.ConnectTimeout("sem resposta")
        return copy.deepcopy(self.order)

    async def update_order(self, bling_id, body):
        self.chamadas.append("put")
        self.corpos.append(copy.deepcopy(body))
        if self.falha_put == "4xx":
            raise _http(400)
        if self.falha_put == "timeout":
            raise httpx.ReadTimeout("sem resposta")
        if self.falha_put == "timeout_aplica":
            self._aplica(body)
            raise httpx.ReadTimeout("sem resposta")
        self._aplica(body)
        return {"id": bling_id}

    def _aplica(self, body):
        self.order["observacoes"] = body.get("observacoes")
        self.order["desconto"] = body.get("desconto")
        self.order["itens"] = [{**i, "id": i.get("id") or 99} for i in body["itens"]]
        self.order.update(self.depois_do_put)

    async def find_active_product_by_sku(self, sku, *, estrito=False):
        self.chamadas.append(f"produto:{sku}")
        if sku not in self.saldos:
            return None
        return {
            "id": 7000 + len(sku),
            "sku": sku,
            "stock": self.saldos[sku],
            "name": f"Brinde {sku}",
            "raw": {"unidade": "UN"},
        }

    async def cost_by_skus(self, skus):
        return {}


async def _nop(*a, **kw):
    return None


@pytest.fixture(autouse=True)
async def _chave_do_upsert(db):
    """A UNIQUE (bling_id, item_index) da migration 0111: o `create_all` não a cria."""
    await db.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bling_orders_teste_brinde"
            " ON bling_orders (bling_id, item_index)"
        )
    )
    await db.commit()
    yield
    await db.execute(text("DROP INDEX IF EXISTS uq_bling_orders_teste_brinde"))
    await db.commit()


@pytest.fixture(autouse=True)
def _sem_esperas(monkeypatch):
    monkeypatch.setattr(brinde, "ESPERAS_CONFERENCIA_S", (0.0, 0.0))
    monkeypatch.setattr(painel, "invalidar_observacoes", _nop)
    # O retrato da Margem lê a view pesada: aqui só importa que é chamado depois.
    monkeypatch.setattr(brinde.verificar_margem, "refresh_silent", _nop)
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_usuarios", "")


def _ligar(monkeypatch, bling):
    async def _b(session):
        return bling

    monkeypatch.setattr(troca, "_cliente_bling", _b)


async def _cenario(
    db,
    make_user,
    *,
    resumo="1x Uranyx A17",
    oferta=True,
    itens=(("dg002.sp", 1),),
    situacao="6",
    pino=None,
    entrou_ha_h=1.0,
    conversa=True,
):
    dono = await make_user(role=UserRole.ADMIN, email=f"dono-{uuid4().hex[:6]}@davinci-test.com")
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name="kfa",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    db.add(StoreInfo(user_id=dono.id, platform="shopee", account_name="kfa", bling_store_id=LOJA))
    entrou = datetime.now(UTC) - timedelta(hours=entrou_ha_h)
    for i, (sku, qtd) in enumerate(itens):
        db.add(
            BlingOrder(
                bling_id=BLING_ID,
                item_index=i,
                numero=NUMERO,
                numeroloja=PEDIDO,
                situacao=situacao,
                loja=LOJA,
                item_codigo=sku,
                item_descricao=f"Produto {sku}",
                item_quantidade=qtd,
                itemvalor=909,
                status=pino,
                data=datetime.now(UTC),
                created_at=entrou,
            )
        )
    c = None
    if conversa:
        c = AtendimentoConversa(
            integration_id=integ.id,
            plataforma="shopee",
            canal="chat",
            externo_id=f"conv-{uuid4().hex[:8]}",
            pedido_marketplace=PEDIDO,
        )
        db.add(c)
        await db.flush()
    if resumo is not None:
        db.add(
            AtendimentoPedidoComprador(
                integration_id=integ.id,
                plataforma="shopee",
                comprador_id="c1",
                pedido=PEDIDO,
                itens_resumo=resumo,
            )
        )
    if oferta:
        db.add(
            AtendimentoAutomacaoRegistro(
                automacao="shopee_nao_pago_brinde",
                integration_id=integ.id,
                plataforma="shopee",
                alvo="pedido",
                chave=f"pedido:{PEDIDO}",
                pedido=PEDIDO,
                conversa_id=c.id if c else None,
                devido_em=datetime.now(UTC),
                estado="enviado",
            )
        )
    await db.commit()
    return dono, integ, c


async def _linha(db, integ):
    iid = integ.id
    db.expire_all()
    return (
        await db.execute(
            select(AtendimentoBrinde).where(
                AtendimentoBrinde.integration_id == iid, AtendimentoBrinde.pedido == PEDIDO
            )
        )
    ).scalar_one_or_none()


async def _notas(db, c):
    cid = getattr(c, "id", c)
    return (
        (
            await db.execute(
                select(AtendimentoMensagem.texto).where(
                    AtendimentoMensagem.conversa_id == cid, AtendimentoMensagem.tipo == "nota"
                )
            )
        )
        .scalars()
        .all()
    )


# ─────────────── as puras ───────────────


def test_lote_do_item_vendido_e_o_kit():
    assert brinde.lote_do_vendido(["dg002.sp"]) == "sp"
    assert brinde.lote_do_vendido(["dg053.ci+a001.pi"]) == "ci"
    assert brinde.lote_do_vendido(["a001.ra+dg053.pi"]) == "pi"
    assert brinde.lote_do_vendido(["a001.ra"]) == "ra"
    assert brinde.lote_do_vendido(["dg002.us"]) is None
    assert brinde.lote_do_vendido(["b009.8.12"]) is None
    assert brinde.lote_do_vendido(["a019.sp", "dg002.ci"]) == "ci"


def test_us_nunca_e_o_brinde():
    assert brinde.familia_do_sku("a019.us") is None
    assert brinde.familia_do_sku("a075.us") is None
    assert brinde.familia_do_sku("a019.sp") == "airtag"
    assert brinde.familia_do_sku("a075") == "chaveiro"
    assert brinde.familia_do_sku("a0191.sp") is None
    assert brinde.skus_candidatos("airtag", "us") == ["a019.cd"]
    assert brinde.skus_candidatos("airtag", "sp") == ["a019.sp", "a019.cd"]
    assert brinde.skus_candidatos("airtag", "cd") == ["a019.cd"]
    assert brinde.skus_candidatos("chaveiro", "sp") == ["a075"]


def test_compensa_o_desconto_e_recusa_percentual():
    body = {"desconto": {"valor": 10.0, "unidade": "REAL"}}
    brinde.compensar_desconto(body)
    assert body["desconto"] == {"valor": 10.01, "unidade": "REAL"}
    sem = {}
    brinde.compensar_desconto(sem)
    assert sem["desconto"] == {"valor": 0.01, "unidade": "REAL"}
    zero = {"desconto": {"valor": 0, "unidade": "PERCENTUAL"}}
    brinde.compensar_desconto(zero)
    assert zero["desconto"]["unidade"] == "REAL"
    with pytest.raises(brinde.BrindeRecusado) as e:
        brinde.compensar_desconto({"desconto": {"valor": 5, "unidade": "PERCENTUAL"}})
    assert e.value.code == "desconto_percentual"


# ─────────────── a trava da NF ───────────────


async def _segurados(db):
    return await brinde.segurados_pela_oferta(db, [NUMERO])


async def test_trava_com_oferta_fica_fora_e_sem_oferta_igual(db, make_user):
    await _cenario(db, make_user, oferta=False)
    assert await _segurados(db) == set()
    await db.execute(delete(BlingOrder).where(BlingOrder.numero == NUMERO))
    await db.commit()


async def test_trava_segura_e_expira_sozinha(db, make_user):
    await _cenario(db, make_user)
    assert await _segurados(db) == {NUMERO}
    await db.execute(
        update(BlingOrder)
        .where(BlingOrder.numero == NUMERO)
        .values(created_at=datetime.now(UTC) - timedelta(hours=12, minutes=1))
    )
    await db.commit()
    assert await _segurados(db) == set()


@pytest.mark.parametrize(
    ("estado", "segura"),
    [("adicionado", False), ("dispensado", False), ("expirado", False), ("incerto", True)],
)
async def test_trava_quem_solta(db, make_user, estado, segura):
    _, integ, _ = await _cenario(db, make_user)
    db.add(AtendimentoBrinde(integration_id=integ.id, pedido=PEDIDO, estado=estado))
    await db.commit()
    assert (await _segurados(db)) == ({NUMERO} if segura else set())


async def test_candidatos_do_sweep_pulam_o_segurado_e_o_aviso_ainda_conta(
    db, make_user, monkeypatch
):
    from tests.test_nf_auto_enfileirar import _seed_loja

    dono = await make_user(role=UserRole.ADMIN, email=f"d-{uuid4().hex[:6]}@davinci-test.com")
    await _seed_loja(db, dono, plataforma="shopee", bling_store_id="7302")
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name="x",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    db.add(
        BlingOrder(
            bling_id=BLING_ID,
            item_index=0,
            numero=NUMERO,
            numeroloja=PEDIDO,
            situacao="6",
            loja="7302",
            item_codigo="dg002.sp",
            item_quantidade=1,
            itemvalor=909,
            data=datetime.now(UTC),
        )
    )
    await db.commit()
    assert await nf_auto_enfileirar._candidatos(db) == [NUMERO]  # sem oferta: igual
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao="tiktok_nao_pago_brinde",
            integration_id=integ.id,
            plataforma="tiktok",
            alvo="pedido",
            chave=f"pedido:{PEDIDO}",
            pedido=PEDIDO,
            devido_em=datetime.now(UTC),
            estado="enviado",
        )
    )
    await db.commit()
    assert await nf_auto_enfileirar._candidatos(db) == []
    assert await nf_auto_enfileirar._candidatos(db, limite=None, pular_adiados=False) == [NUMERO]

    # Erro na consulta da trava: o sweep segue, a transação não aborta.
    async def _quebra(session, numeros):
        await session.execute(text("SELECT * FROM tabela_que_nao_existe"))
        return set()

    monkeypatch.setattr(brinde, "segurados_pela_oferta", _quebra)
    assert await nf_auto_enfileirar._candidatos(db) == [NUMERO]


# ─────────────── adicionar ───────────────


async def test_adicionar_feliz(db, make_user, monkeypatch):
    dono, integ, c = await _cenario(db, make_user)
    c_id = c.id
    bling = BlingFalso()
    _ligar(monkeypatch, bling)
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["ok"] is True and r["estado"] == "adicionado" and r["sku"] == "a019.sp"
    assert bling.chamadas.count("put") == 1
    # O GET é a última chamada antes do PUT: o corpo sai dele.
    assert bling.chamadas[bling.chamadas.index("put") - 1] == "get"
    corpo = bling.corpos[0]
    novo = corpo["itens"][-1]
    assert "id" not in novo
    assert novo["codigo"] == "a019.sp" and novo["quantidade"] == 1 and novo["valor"] == 0.01
    assert novo["produto"] == {"id": 7000 + len("a019.sp")} and novo["unidade"] == "UN"
    # O item vendido fica como estava, com o id; o total sai do corpo; parcelas iguais.
    assert corpo["itens"][0]["id"] == 11 and corpo["itens"][0]["valor"] == 909.0
    assert "total" not in corpo and "totalProdutos" not in corpo
    assert corpo["parcelas"] == [{"id": 5, "valor": 899.0}]
    assert corpo["desconto"] == {"valor": 10.01, "unidade": "REAL"}
    assert "BRINDE a019.sp (oferta do não pago, por " in corpo["observacoes"]
    assert corpo["observacoes"].endswith("obs antiga")
    assert corpo["situacao"] == {"id": 6}

    dono_id = dono.id
    db.expire_all()
    linhas = (
        (
            await db.execute(
                select(BlingOrder)
                .where(BlingOrder.numero == NUMERO)
                .order_by(BlingOrder.item_index)
            )
        )
        .scalars()
        .all()
    )
    assert [x.item_codigo for x in linhas] == ["dg002.sp", "a019.sp"]
    # Só a linha do brinde: o celular segue para o julgamento do robô da Margem.
    assert [x.status for x in linhas] == [None, "Aprovado"]
    assert [x.aprovado_por for x in linhas] == [None, dono_id]
    trilha = (
        (await db.execute(select(MargemAudit).where(MargemAudit.pedido_bling == NUMERO)))
        .scalars()
        .all()
    )
    assert any(t.valor_novo == "Aprovado" and t.origem == "atendimento_brinde" for t in trilha)
    assert any("a019.sp" in n for n in await _notas(db, c_id))
    linha = (
        await db.execute(select(AtendimentoBrinde).where(AtendimentoBrinde.pedido == PEDIDO))
    ).scalar_one()
    assert linha.por == dono_id and linha.bling_id == BLING_ID and linha.tipo == "airtag"


async def test_chaveiro_sem_lote_nunca_us(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(
        db, make_user, resumo="1x Mala de bordo ABS", itens=(("b009.20.sp", 1),)
    )
    bling = BlingFalso(saldos={"a075.us": 2})
    _ligar(monkeypatch, bling)
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["sku"] == "a075"
    assert "produto:a075.us" not in bling.chamadas


async def test_kit_usa_o_lote_do_celular(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user, itens=(("dg053.ci+a001.pi", 1),))
    bling = BlingFalso(
        itens=[
            {
                "id": 11,
                "codigo": "dg053.ci+a001.pi",
                "quantidade": 1,
                "valor": 909.0,
                "produto": {"id": 501},
            }
        ]
    )
    _ligar(monkeypatch, bling)
    assert (await brinde.adicionar(db, integ.id, PEDIDO, dono))["sku"] == "a019.ci"


async def test_sem_saldo_no_lote_vai_para_o_cd(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso(saldos={"a019.sp": 0})
    _ligar(monkeypatch, bling)
    assert (await brinde.adicionar(db, integ.id, PEDIDO, dono))["sku"] == "a019.cd"


async def test_sem_saldo_nenhum_recusa(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso(saldos={"a019.sp": 0, "a019.cd": 0})
    _ligar(monkeypatch, bling)
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "sem_saldo" and "put" not in bling.chamadas


async def test_idempotente_nao_faz_segundo_put(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso()
    _ligar(monkeypatch, bling)
    await brinde.adicionar(db, integ.id, PEDIDO, dono)
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["ja_estava"] is True and bling.chamadas.count("put") == 1


async def test_brinde_posto_a_mao_marca_sem_put(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso()
    bling.order["itens"].append(
        {"id": 12, "codigo": "a019.ra", "quantidade": 1, "valor": 0.01, "produto": {"id": 9}}
    )
    _ligar(monkeypatch, bling)
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["estado"] == "adicionado" and r["sku"] == "a019.ra" and "put" not in bling.chamadas


async def test_item_comprado_a_preco_cheio_nao_e_o_brinde(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso()
    bling.order["itens"].append(
        {"id": 12, "codigo": "a019.ra", "quantidade": 1, "valor": 59.0, "produto": {"id": 9}}
    )
    _ligar(monkeypatch, bling)
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["sku"] == "a019.sp" and r["ja_estava"] is False
    assert bling.chamadas.count("put") == 1


async def test_travas_recusam_sem_put(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    _ligar(monkeypatch, BlingFalso(nf=55))
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "nf_emitida"

    bling = BlingFalso(situacao=21)
    _ligar(monkeypatch, bling)
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "situacao_mudou" and "put" not in bling.chamadas

    bling = BlingFalso()
    bling.order["transporte"] = {"volumes": [{"codigoRastreamento": "BR1"}]}
    _ligar(monkeypatch, bling)
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "rastreio" and "put" not in bling.chamadas

    bling = BlingFalso(desconto={"valor": 5, "unidade": "PERCENTUAL"})
    _ligar(monkeypatch, bling)
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "desconto_percentual" and "put" not in bling.chamadas


async def test_nf_no_banco_recusa_antes_do_bling(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    db.add(NfFaturamento(pedido_bling=NUMERO, status_faturamento="processando"))
    await db.commit()
    bling = BlingFalso()
    _ligar(monkeypatch, bling)
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "nf_emitida" and bling.chamadas == []


async def test_margem_em_analise_recusa(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user, pino="Pendente")
    bling = BlingFalso()
    _ligar(monkeypatch, bling)
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "margem_em_analise" and "put" not in bling.chamadas


async def test_sem_oferta_e_sem_pedido(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user, oferta=False)
    _ligar(monkeypatch, BlingFalso())
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "sem_oferta" and e.value.status == 404


async def test_4xx_recusa_e_nao_grava(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso()
    bling.falha_put = "4xx"
    _ligar(monkeypatch, bling)
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "bling_recusou" and "A soma das parcelas" in e.value.detail
    assert await _linha(db, integ) is None


async def test_timeout_vira_incerto_e_nunca_repete_o_put_as_cegas(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso()
    bling.falha_put = "timeout"
    _ligar(monkeypatch, bling)
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["ok"] is False and r["estado"] == "incerto"
    assert bling.chamadas.count("put") == 1
    await db.commit()
    assert await _segurados(db) == {NUMERO}  # incerto não solta a NF

    # O clique logo depois: GET primeiro; sem o brinde e dentro da folga, recusa.
    bling.chamadas.clear()
    bling.falha_put = None
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "espere_incerto"
    assert bling.chamadas == ["get"]
    await db.commit()

    # Passada a folga, com o GET mostrando que o brinde NÃO entrou: pode tentar.
    await db.execute(
        update(AtendimentoBrinde)
        .where(AtendimentoBrinde.integration_id == integ.id)
        .values(em=datetime.now(UTC) - timedelta(minutes=3))
    )
    await db.commit()
    bling.chamadas.clear()
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["estado"] == "adicionado" and bling.chamadas[0] == "get"
    assert bling.chamadas.count("put") == 1


async def test_timeout_que_aplicou_os_gets_confirmam(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso()
    bling.falha_put = "timeout_aplica"
    _ligar(monkeypatch, bling)
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["ok"] is True and r["estado"] == "adicionado"
    assert bling.chamadas.count("put") == 1


async def test_clique_depois_do_incerto_que_aplicou_nao_faz_put(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso()
    bling.falha_put = "timeout_aplica"
    _ligar(monkeypatch, bling)
    # Os GETs de conferência falham: fica incerto, com o brinde já lá.
    monkeypatch.setattr(brinde, "ESPERAS_CONFERENCIA_S", ())
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["estado"] == "incerto"
    bling.chamadas.clear()
    bling.falha_put = None
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["estado"] == "adicionado" and "put" not in bling.chamadas


async def test_espelho_que_falha_fica_incerto(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso()
    _ligar(monkeypatch, bling)

    async def _quebra(*a, **kw):
        raise RuntimeError("espelho")

    monkeypatch.setattr(brinde.bling_orders_svc, "upsert_order", _quebra)
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["ok"] is False and r["estado"] == "incerto"
    assert bling.chamadas.count("put") == 1


async def test_total_que_muda_fica_incerto_e_segura_a_nf(db, make_user, monkeypatch):
    """O `compensar_desconto` não foi testado no Bling real: esta é a defesa do 1º clique."""
    dono, integ, c = await _cenario(db, make_user)
    c_id, integ_id = c.id, integ.id
    bling = BlingFalso()
    bling.depois_do_put = {"total": 899.01}
    _ligar(monkeypatch, bling)
    r = await brinde.adicionar(db, integ_id, PEDIDO, dono)
    assert r["ok"] is False and r["estado"] == "incerto"
    assert "899.00" in r["mensagem"] and "899.01" in r["mensagem"]
    assert await _segurados(db) == {NUMERO}  # a NF segue segura
    erro = select(AtendimentoBrinde.erro).where(AtendimentoBrinde.pedido == PEDIDO)
    assert "899.01" in await db.scalar(erro)  # o painel mostra o `erro` do incerto ao recarregar
    # O brinde ESTÁ no pedido: o espelho e o pino da linha dele não podem esperar (o robô).
    linhas = (
        await db.execute(
            select(BlingOrder.item_codigo, BlingOrder.status)
            .where(BlingOrder.numero == NUMERO)
            .order_by(BlingOrder.item_index)
        )
    ).all()
    assert [tuple(x) for x in linhas] == [("dg002.sp", None), ("a019.sp", "Aprovado")]
    assert await _notas(db, c_id) == []

    # O clique seguinte confere e NÃO solta enquanto não bater; sem segundo PUT.
    bling.chamadas.clear()
    r = await brinde.adicionar(db, integ_id, PEDIDO, dono)
    assert r["ok"] is False and r["estado"] == "incerto" and "put" not in bling.chamadas
    assert await _segurados(db) == {NUMERO}

    # Alguém corrigiu no Bling: agora o total bate com as parcelas, e solta.
    bling.order["total"] = 899.0
    r = await brinde.adicionar(db, integ_id, PEDIDO, dono)
    assert r["ok"] is True and r["estado"] == "adicionado" and "put" not in bling.chamadas
    assert await _segurados(db) == set()
    assert await db.scalar(erro) is None


async def test_parcelas_que_mudam_ficam_incertas(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso()
    bling.depois_do_put = {"parcelas": [{"id": 5, "valor": 899.01}]}
    _ligar(monkeypatch, bling)
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["ok"] is False and r["estado"] == "incerto" and "parcelas" in r["mensagem"]
    assert await _segurados(db) == {NUMERO}


async def test_put_sem_resposta_que_aplicou_com_o_total_errado_fica_incerto(
    db, make_user, monkeypatch
):
    dono, integ, _ = await _cenario(db, make_user)
    bling = BlingFalso()
    bling.falha_put = "timeout_aplica"
    bling.depois_do_put = {"total": 899.01}
    _ligar(monkeypatch, bling)
    r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert r["ok"] is False and r["estado"] == "incerto" and "899.01" in r["mensagem"]
    assert await _segurados(db) == {NUMERO}


def test_divergencia_de_valores_pura():
    base = {"total": 899.0, "parcelas": [{"valor": 500.0}, {"valor": 399.0}]}
    assert brinde.divergencia_de_valores(base, base) is None
    assert brinde.divergencia_de_valores(base) is None
    # Só o total muda, ou só as parcelas; sem o "antes", vale a soma.
    assert "total" in brinde.divergencia_de_valores({**base, "total": 899.01}, base)
    assert "parcelas" in brinde.divergencia_de_valores(
        {**base, "parcelas": [{"valor": 899.0}]}, base
    )
    assert "parcelas" in brinde.divergencia_de_valores({**base, "total": 899.01})
    # O que já não batia ANTES do PUT não é culpa do brinde; sem parcelas, nada a comparar.
    velho = {"total": 899.0, "parcelas": [{"valor": 850.0}]}
    assert brinde.divergencia_de_valores(velho, velho) is None
    assert brinde.divergencia_de_valores({"total": 899.0, "parcelas": []}) is None


@pytest.mark.parametrize(
    ("estado", "conta"),
    [
        ("enviado", True),
        ("enviando", True),  # o worker pode morrer aqui: a mensagem pode ter saído
        ("revisar", True),  # o envio ambíguo: o comprador pode ter lido e pago
        ("simulado", False),
        ("falhou", False),
        ("pulado", False),
    ],
)
async def test_quem_tem_a_promessa_pelo_estado_do_motor(db, make_user, monkeypatch, estado, conta):
    dono, integ, _ = await _cenario(db, make_user)
    await db.execute(
        update(AtendimentoAutomacaoRegistro)
        .where(AtendimentoAutomacaoRegistro.pedido == PEDIDO)
        .values(estado=estado)
    )
    await db.commit()
    assert await _segurados(db) == ({NUMERO} if conta else set())
    bloco = await brinde.bloco_do_painel(db, integration_id=integ.id, pedido=PEDIDO, user=dono)
    assert (bloco is not None) is conta
    bling = BlingFalso()
    _ligar(monkeypatch, bling)
    if conta:
        r = await brinde.adicionar(db, integ.id, PEDIDO, dono)
        assert r["ok"] is True
    else:
        with pytest.raises(brinde.BrindeRecusado) as e:
            await brinde.adicionar(db, integ.id, PEDIDO, dono)
        assert e.value.code == "sem_oferta"


async def test_o_robo_ainda_julga_o_celular_com_o_brinde_aprovado(db, make_user, monkeypatch):
    """O pino só na linha do brinde: o WHERE do robô é por LINHA, antes do BOOL_OR."""
    from app.services import margem_auto_hold

    async def _retrato(pedido, *linhas):
        for sku, pino, margem in linhas:
            await db.execute(
                text(
                    "INSERT INTO verificar_margem (bling_order_item_id, pedido_bling, bling_id,"
                    " sku, situacao, situacao_nome, plataforma_bling, item_proportion,"
                    " bling_status_margem, marketplace_margem, margem_minima, marketplace_lucro,"
                    " marketplace_liquido_base_margem_item, bling_valorbase_item)"
                    " VALUES (:id, :p, 9, :sku,"
                    " '6', 'Em aberto', 'shopee', 1, :pino, :m, 0.10, 1, 100, 100)"
                ),
                {"id": str(uuid4()), "p": pedido, "sku": sku, "pino": pino, "m": margem},
            )
        await db.commit()

    async def _candidatos():
        return {
            x.pedido_bling: x.margem_baixa
            for x in await db.execute(text(margem_auto_hold._candidatos_sql()))
        }

    # O celular abaixo da mínima com o brinde aprovado: o robô reprova o pedido.
    await _retrato("P1", ("dg002.sp", None, 0.05), ("a019.sp", "Aprovado", -0.999))
    # O celular com margem boa: o brinde de -99,9% não reprova ninguém.
    await _retrato("P2", ("dg002.sp", None, 0.50), ("a019.sp", "Aprovado", -0.999))
    # Sem o pino (a prova do porquê dele): o brinde sozinho reprovaria.
    await _retrato("P3", ("dg002.sp", None, 0.50), ("a019.sp", None, -0.999))
    candidatos = await _candidatos()
    assert candidatos.get("P1") is True
    assert "P2" not in candidatos
    assert candidatos.get("P3") is True


async def test_sku_de_fora_recusa(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    _ligar(monkeypatch, BlingFalso())
    for ruim in ("a019.us", "a075", "dg002.sp"):
        with pytest.raises(brinde.BrindeRecusado) as e:
            await brinde.adicionar(db, integ.id, PEDIDO, dono, sku=ruim)
        assert e.value.code == "sku_invalido"


async def test_sem_indice_usa_os_nomes_do_espelho_tiktok(db, make_user, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user, resumo=None, itens=(("dg002.sp", 1),))
    await db.execute(
        update(BlingOrder)
        .where(BlingOrder.numero == NUMERO)
        .values(item_descricao="Celular Uranyx")
    )
    await db.commit()
    bling = BlingFalso()
    _ligar(monkeypatch, bling)
    assert (await brinde.adicionar(db, integ.id, PEDIDO, dono))["sku"] == "a019.sp"


# ─────────────── dispensar e expirar ───────────────


async def test_dispensar_solta_a_nf(db, make_user, monkeypatch):
    dono, integ, c = await _cenario(db, make_user)
    bling = BlingFalso()
    _ligar(monkeypatch, bling)
    r = await brinde.dispensar(db, integ.id, PEDIDO, dono)
    assert r["estado"] == "dispensado" and bling.chamadas == []
    assert await _segurados(db) == set()
    assert any("não adicionado" in n for n in await _notas(db, c))
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.adicionar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "dispensado"


async def test_incerto_nao_dispensa(db, make_user):
    dono, integ, _ = await _cenario(db, make_user)
    db.add(AtendimentoBrinde(integration_id=integ.id, pedido=PEDIDO, estado="incerto"))
    await db.commit()
    with pytest.raises(brinde.BrindeRecusado) as e:
        await brinde.dispensar(db, integ.id, PEDIDO, dono)
    assert e.value.code == "incerto_nao_dispensa"


async def test_expira_uma_vez_so_com_a_nota(db, make_user):
    _, integ, c = await _cenario(db, make_user, entrou_ha_h=13)
    c = c.id
    assert await brinde.expirar_vencidos(db) == 1
    await db.commit()
    assert (await _linha(db, integ)).estado == "expirado"
    assert await _notas(db, c) == [brinde.NOTA_EXPIROU]
    assert await brinde.expirar_vencidos(db) == 0
    await db.commit()
    assert len(await _notas(db, c)) == 1


async def test_expira_nao_toca_quem_esta_no_prazo_nem_sem_oferta(db, make_user):
    _, integ, _ = await _cenario(db, make_user, entrou_ha_h=11)
    assert await brinde.expirar_vencidos(db) == 0
    assert await _linha(db, integ) is None


async def test_expira_cancelado_sem_nota(db, make_user):
    _, integ, c = await _cenario(db, make_user, entrou_ha_h=13, situacao="12")
    c = c.id
    assert await brinde.expirar_vencidos(db) == 1
    await db.commit()
    assert (await _linha(db, integ)).estado == "expirado"
    assert await _notas(db, c) == []


# ─────────────── as rotas ───────────────


def test_rotas_tem_a_trava_da_caixa_e_quem_responde():
    from fastapi.routing import APIRoute

    from app.main import app

    minhas = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(URL + "/")]
    assert {r.path for r in minhas} == {
        f"{URL}/pendentes",
        f"{URL}/{{integration_id}}/{{pedido}}/adicionar",
        f"{URL}/{{integration_id}}/{{pedido}}/dispensar",
    }
    for r in minhas:
        assert rota._so_admin in [d.call for d in r.dependant.dependencies]
    for sufixo in ("adicionar", "dispensar"):
        assert ("POST", f"{URL}/{{integration_id}}/{{pedido}}/{sufixo}") in (
            rota.ROTAS_DE_QUEM_RESPONDE
        )


async def test_rota_escopo_e_lista(db, client, make_user, auth_as, monkeypatch):
    dono, integ, c = await _cenario(db, make_user)
    bling = BlingFalso()
    _ligar(monkeypatch, bling)
    url = f"{URL}/{integ.id}/{PEDIDO}"

    # A equipe de outra loja: a lista vazia e 404 nas escritas, sem falar com o Bling.
    fora = await make_user(email=f"fora-{uuid4().hex[:6]}@davinci-test.com")
    outra = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name="outra",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(outra)
    await db.flush()
    await dar_lojas(db, fora, outra)
    auth_as(fora)
    r = await client.get(f"{URL}/pendentes")
    assert r.status_code == 200 and r.json()["total"] == 0
    r = await client.post(f"{url}/adicionar")
    assert r.status_code == 404 and bling.chamadas == []
    assert (await client.post(f"{url}/dispensar")).status_code == 404

    # A equipe da loja: vê, adiciona.
    dentro = await make_user(email=f"dentro-{uuid4().hex[:6]}@davinci-test.com")
    await dar_lojas(db, dentro, integ)
    auth_as(dentro)
    r = await client.get(f"{URL}/pendentes")
    item = r.json()["pendentes"][0]
    assert r.json()["total"] == 1
    assert item["pedido"] == PEDIDO and item["tipo"] == "airtag" and item["rotulo"] == "AirTag"
    assert item["sku_sugerido"] == "a019.sp" and item["pode_adicionar"] is True
    assert item["conversa_id"] == str(c.id) and item["numero_bling"] == NUMERO
    assert item["loja"] == "kfa" and item["horas_restantes"] <= 11.1

    r = await client.post(f"{url}/adicionar")
    assert r.status_code == 200 and r.json()["estado"] == "adicionado"
    assert bling.chamadas.count("put") == 1
    r = await client.get(f"{URL}/pendentes")
    assert r.json()["total"] == 0


async def test_rota_recusa_vira_409(db, client, make_user, auth_as, monkeypatch):
    dono, integ, _ = await _cenario(db, make_user)
    _ligar(monkeypatch, BlingFalso(situacao=21))
    auth_as(dono)
    r = await client.post(f"{URL}/{integ.id}/{PEDIDO}/adicionar")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "situacao_mudou"


async def test_alerta_do_expirado_na_lista(db, client, make_user, auth_as):
    dono, _, _ = await _cenario(db, make_user, entrou_ha_h=13)
    await brinde.expirar_vencidos(db)
    await db.commit()
    auth_as(dono)
    corpo = (await client.get(f"{URL}/pendentes")).json()
    assert corpo["total"] == 0
    assert corpo["alertas"][0]["mensagem"] == brinde.NOTA_EXPIROU


async def test_bloco_do_painel(db, make_user):
    dono, integ, _ = await _cenario(db, make_user)
    b = await brinde.bloco_do_painel(db, integration_id=integ.id, pedido=PEDIDO, user=dono)
    assert b["tipo"] == "airtag" and b["pode_adicionar"] is True and b["estado"] is None
    assert b["sku_sugerido"] == "a019.sp"
    db.add(AtendimentoBrinde(integration_id=integ.id, pedido=PEDIDO, estado="adicionado"))
    await db.commit()
    b = await brinde.bloco_do_painel(db, integration_id=integ.id, pedido=PEDIDO, user=dono)
    assert b["estado"] == "adicionado" and b["pode_adicionar"] is False
    outro = await brinde.bloco_do_painel(db, integration_id=integ.id, pedido="OUTRO", user=dono)
    assert outro is None


async def test_painel_nao_mostra_sem_oferta(db, make_user):
    dono, integ, _ = await _cenario(db, make_user, oferta=False)
    bloco = await brinde.bloco_do_painel(db, integration_id=integ.id, pedido=PEDIDO, user=dono)
    assert bloco is None


# ─────────────── a migration ───────────────


async def test_migration_sobe_e_desce(db):
    spec = importlib.util.spec_from_file_location("migration_0455", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0455_atendimento_brindes"
    assert mod.down_revision == "0454_atendimento_modelo_desativado"
    schema = get_settings().database_schema
    mod.SCHEMA = schema

    async def existe() -> bool:
        return bool(
            await db.scalar(
                text(
                    "SELECT 1 FROM information_schema.tables"
                    " WHERE table_schema = :s AND table_name = 'atendimento_brindes'"
                ),
                {"s": schema},
            )
        )

    def _rodar(conn, funcao):
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            funcao()

    conn = await db.connection()
    assert await existe()
    await conn.run_sync(_rodar, mod.downgrade)
    assert not await existe()
    await conn.run_sync(_rodar, mod.upgrade)
    assert await existe()
    await db.commit()
