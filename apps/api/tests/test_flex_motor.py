"""Motor do Flex por anúncio no banco (projeto Flex, etapa 3 — services/flex_motor).

Cada modo: `desligado` não faz nada; `observar` lê e grava estado + trilha mas
NÃO faz nenhuma chamada de escrita (assert explícito, inclusive com o cliente
de verdade e respx); `piloto`/`ativo` desligam sozinhos, ligam só o que uma
pessoa aprovou, até o teto por rodada, uma chamada de cada vez por anúncio.
Recusa (403), conflito (409), gancho do pedido Flex, emergência e Shopee só
leitura. Nenhuma chamada externa: clientes falsos ou respx.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from arq import Retry
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.config import get_settings
from app.models import (
    BlingOrder,
    FlexAnuncioEstado,
    FlexLog,
    FlexPedido,
    Integration,
    IntegrationPlatform,
    Listing,
    Product,
    ProductLink,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services import flex_motor, marketplace_shipment_check
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.marketplaces import flex_api
from app.services.marketplaces.flex_api import ResultadoFlex
from app.services.marketplaces.ml import ML_API_BASE

T0 = datetime(2026, 9, 1, tzinfo=UTC)


class FakeML:
    """Mercado Livre de mentira: guarda o Flex de cada anúncio e cada chamada."""

    def __init__(self, estado: dict[str, bool] | None = None) -> None:
        self.estado = dict(estado or {})
        self.chamadas: list[tuple[str, str]] = []
        self.ligar_resposta: dict[str, ResultadoFlex] = {}
        self.desligar_resposta: dict[str, ResultadoFlex] = {}

    @property
    def escritas(self) -> list[tuple[str, str]]:
        return [c for c in self.chamadas if c[0] != "ler"]

    @property
    def leituras(self) -> list[str]:
        return [i for a, i in self.chamadas if a == "ler"]

    async def ler_flex(self, item):
        self.chamadas.append(("ler", item))
        if item not in self.estado:
            return ResultadoFlex(flex_api.INDISPONIVEL, status_http=404)
        return ResultadoFlex(flex_api.OK, has_flex=self.estado[item], status_http=200)

    async def ligar_flex(self, item):
        self.chamadas.append(("ligar", item))
        if item in self.ligar_resposta:
            return self.ligar_resposta[item]
        self.estado[item] = True
        return ResultadoFlex(flex_api.OK, has_flex=True, status_http=204)

    async def desligar_flex(self, item):
        self.chamadas.append(("desligar", item))
        if item in self.desligar_resposta:
            return self.desligar_resposta[item]
        self.estado[item] = False
        return ResultadoFlex(flex_api.OK, has_flex=False, status_http=204)


class FakeShopee:
    """Shopee de mentira: `logistic_info` por anúncio; a escrita aplica a lista
    recebida (e guarda o payload para conferir que veio COMPLETO)."""

    def __init__(self, canais: dict[str, list[dict]]) -> None:
        self.canais = {k: [dict(c) for c in v] for k, v in canais.items()}
        self.chamadas: list[tuple[str, Any]] = []

    @property
    def escritas(self):
        return [c for c in self.chamadas if c[0] == "atualizar"]

    async def ler_canais_flex(self, ids, canais_flex):
        self.chamadas.append(("ler", list(ids)))
        out = {}
        for i in ids:
            lista = self.canais.get(str(i))
            if lista is None:
                continue
            out[str(i)] = ResultadoFlex(
                flex_api.OK,
                has_flex=flex_api.flex_nos_canais(lista, canais_flex),
                canais=tuple(dict(c) for c in lista),
            )
        return out

    async def atualizar_canal_flex(self, item_id, logistic_info, *, ligar, canais_flex):
        payload = flex_api.payload_canais(logistic_info, canais_flex, ligar)
        self.chamadas.append(("atualizar", (str(item_id), payload)))
        self.canais[str(item_id)] = [dict(c) for c in payload]
        return ResultadoFlex(flex_api.OK, has_flex=ligar, status_http=200)


# ---- cenário -------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mundo(db: AsyncSession, monkeypatch):
    """Conta ML permitida com 4 anúncios + 1 anúncio só importado, e uma conta
    ML NÃO permitida. dg053.sp com 5 peças livres; n_liga 3, n_desliga 1,
    2 anúncios por família."""
    cfg = get_settings()
    for chave, valor in {
        "flex_modo": "observar",
        "flex_n_liga": 3,
        "flex_n_desliga": 1,
        "flex_kits": False,
        "flex_max_anuncios_por_familia": 2,
        "flex_teto_escritas_por_rodada": 50,
        "flex_shopee_escrita": False,
        "flex_shopee_canais": "90022",
        "flex_intervalo_min": 15,
    }.items():
        monkeypatch.setattr(cfg, chave, valor)

    dono = User(
        open_id=f"email:flex-{uuid.uuid4().hex[:6]}@x",
        email=f"flex-{uuid.uuid4().hex[:6]}@x",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(dono)
    await db.flush()
    creds = encrypt_json(
        {"access_token": "tok", "refresh_token": "r", "client_id": "c", "client_secret": "s",
         "expires_at": 9_999_999_999}
    )
    conta = Integration(user_id=dono.id, platform=IntegrationPlatform.ML, name="vita",
                        credentials=creds)
    outra = Integration(user_id=dono.id, platform=IntegrationPlatform.ML, name="fora",
                        credentials=creds)
    db.add_all([conta, outra])
    await db.flush()
    prods = {}
    for sku, stock, sit in [
        ("dg053.ci", 100, "A"),
        ("dg053.sp", 5, "A"),
        ("b009", 7, "A"),
    ]:
        p = Product(user_id=dono.id, sku=sku, name=sku, stock=stock, situacao=sit,
                    bling_product_id=abs(hash(sku)) % 10**8)
        db.add(p)
        prods[sku] = p
    await db.flush()

    def _link(integ, ext, sku, dias):
        return ProductLink(
            user_id=dono.id,
            product_id=prods[sku].id,
            integration_id=integ.id,
            platform=IntegrationPlatform.ML,
            external_id=ext,
            stock=10,
            listing_title=f"anúncio {ext}",
            created_at=T0 + timedelta(days=dias),
        )

    db.add_all(
        [
            _link(conta, "MLB1", "dg053.ci", 0),
            _link(conta, "MLB2", "dg053.ci", 1),
            _link(conta, "MLB3", "dg053.ci", 2),
            _link(conta, "MLB9", "b009", 3),
            _link(outra, "MLB50", "dg053.ci", 0),
        ]
    )
    # Anúncio da conta que nunca foi vinculado (negação por padrão).
    db.add(
        Listing(user_id=dono.id, integration_id=conta.id, platform=IntegrationPlatform.ML,
                external_id="MLB77", title="importado sem vínculo")
    )
    await db.commit()
    monkeypatch.setattr(cfg, "flex_contas", str(conta.id))

    fake = FakeML({"MLB1": False, "MLB2": False, "MLB3": False, "MLB9": True, "MLB77": True,
                   "MLB50": True})
    montados: list[uuid.UUID] = []

    async def _montar(integ):
        montados.append(integ.id)
        return fake

    monkeypatch.setattr(flex_motor, "montar_cliente", _montar)
    # Bling de mentira para a conferência antes de LIGAR: por padrão concorda
    # com o banco (o `products.stock` do .sp); `bling["dg053.sp"] = n` muda o
    # que ele diz; `bling["fora"] = True` = o Bling não respondeu.
    bling: dict[str, Any] = {}
    consultas_bling: list[list[str]] = []

    async def _saldos_bling(skus):
        consultas_bling.append(sorted(skus))
        if bling.get("fora"):
            return None
        out = {}
        async with flex_motor.session_scope() as s:
            for sku in skus:
                if sku in bling:
                    out[sku] = bling[sku]
                    continue
                v = (
                    await s.execute(
                        text("SELECT min(stock) FROM products WHERE lower(sku) = :s"), {"s": sku}
                    )
                ).scalar()
                if v is None:
                    return None
                out[sku] = int(v)
        return out

    monkeypatch.setattr(flex_motor, "saldos_bling", _saldos_bling)
    # Ids guardados à parte: o `expire_all` dos testes faria o ORM recarregar
    # o objeto fora do contexto assíncrono.
    return {"conta_id": conta.id, "outra_id": outra.id, "dono_id": dono.id, "ml": fake,
            "montados": montados, "sp_bling_id": prods["dg053.sp"].bling_product_id,
            "b009_id": prods["b009"].id, "cfg": cfg, "bling": bling,
            "consultas_bling": consultas_bling, "sp_id": prods["dg053.sp"].id}


async def _estados(db: AsyncSession) -> dict[str, FlexAnuncioEstado]:
    db.expire_all()
    rows = (await db.execute(select(FlexAnuncioEstado))).scalars().all()
    return {e.external_id: e for e in rows}


async def _trilha(db: AsyncSession, ext: str | None = None) -> list[tuple[str, str]]:
    db.expire_all()
    q = select(FlexLog.acao, FlexLog.resultado).order_by(FlexLog.id)
    if ext:
        q = q.where(FlexLog.external_id == ext)
    return [(a, r) for a, r in (await db.execute(q)).all()]


async def _pedido_flex(db, bling_id: int, codigo: str, qtd: int, *, situacao="6", no_sp=False):
    db.add(BlingOrder(numero=str(bling_id), bling_id=bling_id, loja="1", item_index=0,
                      item_codigo=codigo, item_quantidade=qtd, situacao=situacao,
                      data=datetime.now(UTC)))
    db.add(FlexPedido(bling_id=bling_id, plataforma="ml", envio_tipo="self_service", no_sp=no_sp))
    await db.commit()


# ---- modos -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_desligado_nao_faz_nada(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "desligado")
    resumo = await flex_motor.rodar_motor()
    assert resumo["rodou"] is False
    assert await _estados(db) == {}
    assert mundo["ml"].chamadas == []
    assert mundo["montados"] == []


@pytest.mark.asyncio
async def test_sem_contas_permitidas_nao_avalia_nada(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_contas", "")
    resumo = await flex_motor.rodar_motor()
    assert resumo["rodou"] is False
    assert "nenhuma conta" in resumo["motivo"]
    assert await _estados(db) == {}
    assert mundo["ml"].chamadas == []


@pytest.mark.asyncio
async def test_observar_le_grava_e_nao_escreve(db, mundo):
    resumo = await flex_motor.rodar_motor()
    assert resumo["rodou"] is True
    ml = mundo["ml"]
    # NENHUMA chamada de escrita em observar.
    assert ml.escritas == []
    assert sorted(ml.leituras) == ["MLB1", "MLB2", "MLB3", "MLB77", "MLB9"]

    est = await _estados(db)
    assert set(est) == {"MLB1", "MLB2", "MLB3", "MLB9", "MLB77"}  # MLB50 é de conta fora
    assert (est["MLB1"].desejado, est["MLB1"].observado) == ("ligado", "desligado")
    assert est["MLB1"].aguardando_aprovacao is True
    assert est["MLB1"].saldo_sp == 5 and est["MLB1"].familias == "dg053"
    assert est["MLB2"].desejado == "ligado"
    assert est["MLB3"].desejado == "desligado"
    assert "limite de 2" in est["MLB3"].motivo
    assert est["MLB3"].aguardando_aprovacao is False
    assert (est["MLB9"].desejado, est["MLB9"].observado) == ("inelegivel", "ligado")
    assert (est["MLB77"].desejado, est["MLB77"].observado) == ("inelegivel", "ligado")
    assert resumo["simulados"] == 2  # desligar MLB9 e MLB77

    assert await _trilha(db, "MLB9") == [("decidir", "ok"), ("ler", "ok"), ("desligar", "simulado")]
    assert await _trilha(db, "MLB1") == [
        ("decidir", "ok"),
        ("ler", "ok"),
        ("pedir_aprovacao", "pendente"),
    ]

    # Segunda rodada sem mudança: nada novo na trilha, ainda sem escrita.
    antes = len(await _trilha(db))
    await flex_motor.rodar_motor()
    assert len(await _trilha(db)) == antes
    assert ml.escritas == []


@pytest.mark.asyncio
async def test_observar_com_cliente_de_verdade_nao_chama_post_nem_delete(db, mundo, monkeypatch):
    """O mesmo modo observar com o MercadoLivreClient real (respx): as rotas de
    escrita existem no mock e NÃO são chamadas."""
    from app.services.atendimento.clientes import cliente_da_integracao

    monkeypatch.setattr(flex_motor, "montar_cliente", cliente_da_integracao)
    with respx.mock(base_url=ML_API_BASE, assert_all_called=False) as router:
        for item, flex in {"MLB1": False, "MLB2": False, "MLB3": False, "MLB9": True,
                           "MLB77": True}.items():
            router.get(f"/flex/sites/MLB/items/{item}/v2").mock(
                return_value=httpx.Response(200, json={"has_flex": flex})
            )
        post = router.post(url__regex=r"/flex/.*").mock(return_value=httpx.Response(204))
        delete = router.delete(url__regex=r"/flex/.*").mock(return_value=httpx.Response(204))
        await flex_motor.rodar_motor()
        await flex_motor.rodar_motor()
        assert post.call_count == 0
        assert delete.call_count == 0
        assert all(c.request.method == "GET" for c in router.calls)
    est = await _estados(db)
    assert est["MLB9"].observado == "ligado"
    assert est["MLB1"].aguardando_aprovacao is True


@pytest.mark.asyncio
async def test_piloto_desliga_sozinho_e_liga_so_aprovado(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    resumo = await flex_motor.rodar_motor()
    # Desligar é automático (o inelegível com Flex ligado e o importado sem
    # vínculo); ligar espera uma pessoa.
    assert sorted(ml.escritas) == [("desligar", "MLB77"), ("desligar", "MLB9")]
    assert resumo["desligar_ok"] == 2
    est = await _estados(db)
    assert est["MLB9"].observado == "desligado" and est["MLB9"].aplicado_em is not None
    assert est["MLB1"].aguardando_aprovacao is True
    assert ("desligar", "ok") in await _trilha(db, "MLB9")
    log = (
        await db.execute(select(FlexLog).where(FlexLog.acao == "desligar"))
    ).scalars().first()
    assert (log.estado_antes, log.estado_depois, log.modo) == ("ligado", "desligado", "piloto")

    # A pessoa aprova o MLB1: o motor roda só para ele e liga.
    ml.chamadas.clear()
    res = await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=mundo["dono_id"])
    assert res["aplicado"] is True
    assert ml.chamadas == [("ler", "MLB1"), ("ligar", "MLB1")]
    est = await _estados(db)
    assert est["MLB1"].observado == "ligado"
    assert est["MLB1"].aguardando_aprovacao is False
    # A aprovação foi usada: se o Flex cair, LIGAR de novo pede outra.
    assert est["MLB1"].aprovado_em is None
    assert est["MLB2"].aguardando_aprovacao is True  # o outro continua esperando
    trilha = await _trilha(db, "MLB1")
    assert trilha[-2:] == [("aprovar", "ok"), ("ligar", "ok")]
    quem = (
        await db.execute(select(FlexLog.por).where(FlexLog.acao == "aprovar"))
    ).scalar_one()
    assert quem == mundo["dono_id"]

    # Alguém desliga no painel do ML: o motor NÃO religa sozinho.
    ml.estado["MLB1"] = False
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ("ligar", "MLB1") not in ml.chamadas
    est = await _estados(db)
    assert (est["MLB1"].observado, est["MLB1"].aguardando_aprovacao) == ("desligado", True)


@pytest.mark.asyncio
async def test_aprovar_respeita_a_regra(db, mundo, monkeypatch):
    with pytest.raises(flex_motor.FlexRegraError) as e:
        await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    assert e.value.codigo == "nao_avaliado"
    await flex_motor.rodar_motor()
    with pytest.raises(flex_motor.FlexRegraError) as e:
        await flex_motor.aprovar(mundo["conta_id"], "MLB9", por=None)
    assert e.value.codigo == "nao_elegivel"
    with pytest.raises(flex_motor.FlexRegraError) as e:
        await flex_motor.aprovar(mundo["outra_id"], "MLB50", por=None)
    assert e.value.codigo == "conta_nao_permitida"
    # Em observar a aprovação fica registrada, sem escrever.
    res = await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=mundo["dono_id"])
    assert (res["aprovado"], res["aplicado"]) == (True, False)
    assert mundo["ml"].escritas == []
    est = await _estados(db)
    assert est["MLB1"].aprovado_por == mundo["dono_id"]
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "desligado")
    with pytest.raises(flex_motor.FlexRegraError) as e:
        await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    assert e.value.codigo == "flex_desligado"


@pytest.mark.asyncio
async def test_saldo_flex_histerese_e_pedidos_flex(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "ativo")
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    assert ml.estado["MLB1"] is True

    # Venda Flex de 3 ainda reservando no .ci: saldo 5 - 3 = 2 → entre 1 e 3,
    # continua ligado (histerese), e o MLB2 deixa de querer ligar.
    await _pedido_flex(db, 900001, "dg053.ci", 3)
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    est = await _estados(db)
    assert (est["MLB1"].desejado, est["MLB1"].saldo_sp) == ("ligado", 2)
    assert "histerese" in est["MLB1"].motivo
    assert est["MLB2"].desejado == "desligado"
    assert est["MLB2"].aguardando_aprovacao is False
    assert ml.escritas == []

    # Pedido cancelado (a peça não saiu) ou já no .sp não desconta; outro
    # Flex aberto de 2 zera o saldo → desliga sozinho.
    await _pedido_flex(db, 900002, "dg053.ci", 9, situacao="12")
    await _pedido_flex(db, 900003, "dg053.sp", 9, no_sp=True)
    await _pedido_flex(db, 900004, "dg053.ra", 2)
    await flex_motor.rodar_motor()
    est = await _estados(db)
    assert (est["MLB1"].desejado, est["MLB1"].saldo_sp) == ("desligado", 0)
    assert ("desligar", "MLB1") in ml.escritas
    assert ml.estado["MLB1"] is False


async def _saldo(db, sku="dg053.sp") -> flex_motor.SaldoSp:
    db.expire_all()
    return (await flex_motor.calcular_saldos(db, [sku]))[sku]


@pytest.mark.asyncio
async def test_pedido_flex_que_saiu_sem_sp_desconta_ate_o_acerto(db, mundo):
    """Achado da revisão: o pedido Flex que SAI (em andamento/atendido) sem ter
    ido ao .sp é baixado pelo Bling no .ci, mas a peça sai de São Bernardo —
    o .sp do Bling fica com 3 peças a mais. Antes, ao passar para a situação
    15 o desconto sumia (o saldo voltava de 2 para 5 para sempre)."""
    await _pedido_flex(db, 905001, "dg053.ci", 3)
    assert (await _saldo(db)).saldo == 2
    await db.execute(text("UPDATE bling_orders SET situacao = '15' WHERE bling_id = 905001"))
    await db.commit()
    assert (await _saldo(db)).saldo == 2  # saiu, mas o .sp não foi acertado
    await db.execute(text("UPDATE bling_orders SET situacao = '9' WHERE bling_id = 905001"))
    # Velho também: depois de sair, o desconto não tem janela de dias.
    await db.execute(
        text("UPDATE flex_pedido SET detectado_em = now() - interval '60 days'"
             " WHERE bling_id = 905001")
    )
    await db.commit()
    assert (await _saldo(db)).saldo == 2
    # A pessoa acertou o estoque no Bling: para de descontar.
    await db.execute(text("UPDATE flex_pedido SET acertado_em = now() WHERE bling_id = 905001"))
    await db.commit()
    assert (await _saldo(db)).saldo == 5
    # Cancelado nunca desconta (a peça não saiu).
    await _pedido_flex(db, 905002, "dg053.ci", 4, situacao="12")
    assert (await _saldo(db)).saldo == 5


@pytest.mark.asyncio
async def test_pedido_levado_ao_sp_desconta_ate_o_produto_ser_atualizado(db, mundo):
    """Achado da revisão: o robô leva o pedido Flex ao .sp e grava `no_sp`; o
    `products.stock` do .sp só cai quando o webhook do Bling chega (e o da
    reserva às vezes não vem). Antes, o desconto sumia na hora e o saldo
    voltava de 2 para 5 com só 2 peças de verdade."""
    await _pedido_flex(db, 906001, "dg053.ci", 3)
    assert (await _saldo(db)).saldo == 2
    # O robô trocou para o .sp agora (o produto foi atualizado ANTES disso).
    await db.execute(text("UPDATE products SET updated_at = now() - interval '1 hour'"
                          " WHERE lower(sku) = 'dg053.sp'"))
    await db.execute(text("UPDATE bling_orders SET item_codigo = 'dg053.sp'"
                          " WHERE bling_id = 906001"))
    await db.execute(text("UPDATE flex_pedido SET no_sp = true, sp_em = now()"
                          " WHERE bling_id = 906001"))
    await db.commit()
    s = await _saldo(db)
    assert (s.estoque, s.pendentes, s.movidos, s.saldo) == (5, 0, 3, 2)
    # O webhook do Bling chegou: o produto foi atualizado depois da troca e o
    # estoque já traz a reserva — não desconta duas vezes.
    await db.execute(text("UPDATE products SET stock = 2, updated_at = now() + interval '1 second'"
                          " WHERE lower(sku) = 'dg053.sp'"))
    await db.commit()
    s = await _saldo(db)
    assert (s.estoque, s.movidos, s.saldo) == (2, 0, 2)


@pytest.mark.asyncio
async def test_ligar_confere_o_saldo_no_bling(db, mundo, monkeypatch):
    """Achado da revisão: antes de LIGAR, o saldo do .sp é conferido no Bling.
    O banco diz 5 (webhook que não veio); o Bling diz 1 → não liga, fica a
    falha com espera e a aprovação continua. Bling fora do ar: também não."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    mundo["bling"]["dg053.sp"] = 1
    res = await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    assert res["aplicado"] is False
    assert ("ligar", "MLB1") not in ml.chamadas
    assert ["dg053.sp"] in mundo["consultas_bling"]
    est = (await _estados(db))["MLB1"]
    assert est.observado == "desligado"
    assert est.aprovado_em is not None  # a aprovação continua
    assert est.proxima_tentativa is not None
    assert "o Bling mostra só 1 peça(s) livre(s) em dg053.sp" in est.ultimo_erro
    assert ("ligar", "erro") in await _trilha(db, "MLB1")

    # Bling fora do ar: desconhecido não liga.
    mundo["bling"].clear()
    mundo["bling"]["fora"] = True
    await db.execute(text("UPDATE flex_anuncio_estado SET proxima_tentativa = NULL"))
    await db.commit()
    await flex_motor.rodar_motor()
    assert ("ligar", "MLB1") not in ml.chamadas
    assert "não deu para conferir" in (await _estados(db))["MLB1"].ultimo_erro

    # O Bling confirma: liga.
    mundo["bling"].clear()
    await db.execute(text("UPDATE flex_anuncio_estado SET proxima_tentativa = NULL"))
    await db.commit()
    await flex_motor.rodar_motor()
    assert ("ligar", "MLB1") in ml.chamadas
    assert (await _estados(db))["MLB1"].observado == "ligado"


@pytest.mark.asyncio
async def test_variacao_sem_vinculo_vivo_deixa_o_anuncio_inelegivel(db, mundo):
    """Achado da revisão (cenário dos revisores): o MLB1 vende a preta
    (dg053.ci, vínculo vivo, dg053.sp com 5) e a azul (x777.ci, sem .sp). A
    azul teve o vínculo marcado morto — ou nunca foi vinculada e só aparece
    nas variações do anúncio importado. Antes, a regra só via a preta e
    pedia aprovação para ligar ("saldo Flex 5 em dg053.sp")."""
    db.add(Product(user_id=mundo["dono_id"], sku="x777.ci", name="azul", stock=50,
                   situacao="A"))
    await db.flush()
    azul = (await db.execute(select(Product.id).where(Product.sku == "x777.ci"))).scalar_one()
    await db.execute(
        text("UPDATE product_links SET variation_id = '111' WHERE external_id = 'MLB1'")
    )
    db.add(ProductLink(user_id=mundo["dono_id"], product_id=azul,
                       integration_id=mundo["conta_id"], platform=IntegrationPlatform.ML,
                       external_id="MLB1", variation_id="222", stock=50,
                       morto_desde=datetime.now(UTC), morto_motivo="anúncio encerrado"))
    await db.commit()
    await flex_motor.rodar_motor()
    est = (await _estados(db))["MLB1"]
    assert est.desejado == "inelegivel"
    assert est.motivo == "variação x777.ci com vínculo morto no DaVinci"
    assert est.aguardando_aprovacao is False

    # Vínculo morto DUPLICADO (outro vínculo vivo cobre a mesma variação) não pesa.
    await db.execute(text("UPDATE product_links SET morto_motivo = 'duplicado: anúncio inteiro'"
                          " WHERE variation_id = '222'"))
    await db.commit()
    await flex_motor.rodar_motor()
    est = (await _estados(db))["MLB1"]
    # Volta à regra do saldo (aqui perde a vaga da família para MLB2/MLB3,
    # que ficaram com o Flex enquanto ele estava fora).
    assert est.desejado != "inelegivel"
    assert "vínculo" not in est.motivo

    # A azul nunca vinculada, mas à venda no anúncio importado (raw_data do ML).
    db.add(Listing(
        user_id=mundo["dono_id"], integration_id=mundo["conta_id"],
        platform=IntegrationPlatform.ML, external_id="MLB1", title="mala",
        raw_data={"id": "MLB1", "variations": [
            {"id": 111, "available_quantity": 10,
             "attributes": [{"id": "SELLER_SKU", "value_name": "dg053.ci"}]},
            {"id": 333, "available_quantity": 50, "seller_custom_field": "x888.ci"},
        ]},
    ))
    await db.commit()
    await flex_motor.rodar_motor()
    est = (await _estados(db))["MLB1"]
    assert est.desejado == "inelegivel"
    assert est.motivo == "variação x888.ci sem vínculo com produto do DaVinci"


@pytest.mark.asyncio
async def test_variacoes_da_plataforma_ml_e_shopee():
    raw_ml = {"variations": [
        {"id": 1, "available_quantity": 4,
         "attributes": [{"id": "SELLER_SKU", "value_name": "dg053.ci"}],
         "seller_custom_field": "velho"},
        {"id": 2, "available_quantity": 0, "sku": "dg054.ci"},
        {"id": 3},
        {"sem": "id"},
    ]}
    assert flex_motor.variacoes_da_plataforma("ml", raw_ml) == [
        ("1", "dg053.ci", 4), ("2", "dg054.ci", 0), ("3", None, None)
    ]
    assert flex_motor.variacoes_da_plataforma("ml", {"variations": []}) == []
    raw_sh = {"item": {"item_id": 9}, "model": {
        "model_id": 77, "model_sku": "dg053.ci",
        "stock_info_v2": {"summary_info": {"total_available_stock": 6}}}}
    assert flex_motor.variacoes_da_plataforma("shopee", raw_sh) == [("77", "dg053.ci", 6)]
    assert flex_motor.variacoes_da_plataforma("shopee", {"item_id": 9}) == []
    assert flex_motor.variacoes_da_plataforma("ml", None) == []


@pytest.mark.asyncio
async def test_sp_inativo_ou_gemeo_de_sku(db, mundo, monkeypatch):
    # Gêmeo do mesmo produto do Bling (mesmo bling_product_id) conta uma vez.
    db.add(Product(user_id=mundo["dono_id"], sku="DG053.SP", name="gêmeo", stock=5,
                   situacao="A", bling_product_id=mundo["sp_bling_id"]))
    await db.commit()
    await flex_motor.rodar_motor()
    assert (await _estados(db))["MLB1"].saldo_sp == 5
    # .sp inativo: estoque desconhecido → nunca liga.
    await db.execute(text("UPDATE products SET situacao = 'I' WHERE lower(sku) = 'dg053.sp'"))
    await db.commit()
    await flex_motor.rodar_motor()
    est = await _estados(db)
    assert est["MLB1"].desejado == "inelegivel"
    assert "nunca liga" in est["MLB1"].motivo
    assert est["MLB1"].aguardando_aprovacao is False


@pytest.mark.asyncio
async def test_teto_de_escritas_por_rodada(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    monkeypatch.setattr(mundo["cfg"], "flex_teto_escritas_por_rodada", 1)
    resumo = await flex_motor.rodar_motor()
    assert len(mundo["ml"].escritas) == 1
    assert resumo["adiados_teto"] == 1
    await flex_motor.rodar_motor()
    assert len(mundo["ml"].escritas) == 2


@pytest.mark.asyncio
async def test_recusa_403_nao_repete_ate_aprovar_de_novo(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    ml.ligar_resposta["MLB1"] = ResultadoFlex(flex_api.INELEGIVEL, status_http=403,
                                              detalhe="item down")
    await flex_motor.rodar_motor()
    await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    est = await _estados(db)
    assert est["MLB1"].desejado == "inelegivel"
    assert est["MLB1"].recusa == "403 item down"
    assert "recusou" in est["MLB1"].motivo
    assert est["MLB1"].aguardando_aprovacao is False
    assert ("ligar", "erro") in await _trilha(db, "MLB1")

    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    await flex_motor.rodar_motor()
    assert ("ligar", "MLB1") not in ml.chamadas
    assert (await _estados(db))["MLB1"].desejado == "inelegivel"

    # Uma pessoa manda tentar de novo (o anúncio foi corrigido): liga.
    del ml.ligar_resposta["MLB1"]
    res = await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    assert res["aplicado"] is True
    est = await _estados(db)
    assert (est["MLB1"].observado, est["MLB1"].recusa) == ("ligado", None)


@pytest.mark.asyncio
async def test_409_espera_antes_de_tentar_de_novo(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    ml.desligar_resposta["MLB9"] = ResultadoFlex(flex_api.REPETIR, status_http=409,
                                                 detalhe="conflict")
    resumo = await flex_motor.rodar_motor()
    assert resumo["desligar_falhou"] == 1
    est = await _estados(db)
    assert est["MLB9"].tentativas == 1
    assert est["MLB9"].proxima_tentativa > datetime.now(UTC) + timedelta(minutes=10)
    assert est["MLB9"].ultimo_erro.startswith("desligar: 409")
    assert est["MLB9"].observado_em is None  # vale só depois de ler de novo

    ml.chamadas.clear()
    resumo = await flex_motor.rodar_motor()
    assert ("desligar", "MLB9") not in ml.chamadas
    assert resumo["esperando_nova_tentativa"] == 1

    # Passou a espera: lê e tenta de novo.
    await db.execute(
        text("UPDATE flex_anuncio_estado SET proxima_tentativa = now() - interval '1 minute'")
    )
    await db.commit()
    del ml.desligar_resposta["MLB9"]
    await flex_motor.rodar_motor()
    est = await _estados(db)
    assert (est["MLB9"].observado, est["MLB9"].tentativas) == ("desligado", 0)


@pytest.mark.asyncio
async def test_gancho_do_pedido_flex_so_desliga_sem_ler(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    assert ml.estado["MLB1"] is True
    # Estado lido há tempo (não fresco) — o passe barato desliga mesmo assim.
    await db.execute(
        text("UPDATE flex_anuncio_estado SET observado_em = now() - interval '3 hours'")
    )
    await db.commit()
    await _pedido_flex(db, 910001, "dg053.ci", 5)
    ml.chamadas.clear()
    resumo = await flex_motor.rodar_motor(ler=False, so_desligar=True, origem="evento")
    assert ml.chamadas == [("desligar", "MLB1")]
    assert resumo["desligar_ok"] == 1


@pytest.mark.asyncio
async def test_trava_por_anuncio_e_da_rodada(db, mundo, monkeypatch):
    conta = mundo["conta_id"]
    ml = mundo["ml"]
    await flex_motor.rodar_motor()  # observar: MLB9 lido ligado, quer desligar
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml.chamadas.clear()
    # Outro processo no MLB9 (a emergência, por exemplo): nem a leitura nem a
    # escrita chamam o ML para ele nesta rodada.
    async with flex_motor.session_scope() as outro:
        await outro.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": flex_motor._chave_trava(conta, "MLB9")},
        )
        resumo = await flex_motor.rodar_motor()
    assert resumo["leituras_ocupadas"] == 1
    assert resumo["ocupado"] == 1
    assert "MLB9" not in [i for _, i in ml.chamadas]
    assert ("desligar", "MLB77") in ml.chamadas  # os outros seguem
    # Solta a trava: a rodada seguinte desliga.
    await flex_motor.rodar_motor()
    assert ("desligar", "MLB9") in ml.chamadas
    # Rodada em andamento: a segunda sai sem fazer nada.
    async with flex_motor.session_scope() as outro:
        await outro.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": flex_motor._MOTOR_LOCK_KEY},
        )
        resumo = await flex_motor.rodar_motor()
    assert resumo.get("ocupado") is True and resumo["rodou"] is False


# ---- emergência ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emergencia_em_observar_so_simula(db, mundo):
    """Simulação de verdade: a tela promete "só uma SIMULAÇÃO (mostra o que
    faria)" — a aprovação dada na fase observar NÃO pode sumir (antes sumia:
    o UPDATE rodava antes do `if not escreve`)."""
    await flex_motor.rodar_motor()
    await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    resumo = await flex_motor.emergencia(por=mundo["dono_id"])
    assert resumo["escreve"] is False
    assert resumo["simulados"] == 2  # MLB9 e MLB77 (os ligados conhecidos)
    assert resumo["aprovacoes"] == 1  # diz quantas sairiam
    assert mundo["ml"].escritas == []
    est = await _estados(db)
    assert est["MLB1"].aprovado_em is not None  # nada mudou
    assert ("emergencia", "simulado") in await _trilha(db)
    # A rodada seguinte não pede a aprovação de novo: ela continua valendo.
    await flex_motor.rodar_motor()
    est = await _estados(db)
    assert est["MLB1"].aprovado_em is not None
    assert est["MLB1"].aguardando_aprovacao is False


@pytest.mark.asyncio
async def test_emergencia_em_piloto_desliga_tudo_das_contas_permitidas(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    await flex_motor.aprovar(mundo["conta_id"], "MLB2", por=None)
    ml.chamadas.clear()
    resumo = await flex_motor.emergencia(por=mundo["dono_id"])
    assert sorted(ml.escritas) == [("desligar", "MLB1"), ("desligar", "MLB2")]
    assert resumo["desligados"] == 2 and resumo["restantes"] == 0
    assert "MLB50" not in str(ml.chamadas)  # conta fora de flex_contas
    est = await _estados(db)
    assert {e.observado for e in est.values()} == {"desligado"}
    assert est["MLB1"].motivo.startswith("emergência")
    por = (
        await db.execute(select(FlexLog.por).where(FlexLog.acao == "emergencia"))
    ).scalars().all()
    assert set(por) == {mundo["dono_id"]}
    # Nada volta a ligar sem nova aprovação.
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert not [c for c in ml.escritas if c[0] == "ligar"]
    assert (await _estados(db))["MLB1"].aguardando_aprovacao is True


@pytest.mark.asyncio
async def test_emergencia_anuncio_ocupado_nao_conta_como_desligado(db, mundo, monkeypatch):
    """Achado da revisão: com a trava do MLB9 com outro processo além da
    espera, a resposta dizia desligados=1, ocupados=1, restantes=0 — e a tela
    mostrava "Flex desligado" com o MLB9 ainda ligado. Ocupado é RESTANTE."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    monkeypatch.setattr(flex_motor, "_ESPERA_TRAVA_EMERGENCIA", 0.3)
    await flex_motor.rodar_motor()  # desliga MLB9 e MLB77 (inelegíveis)
    await db.execute(text("UPDATE flex_anuncio_estado SET observado = 'ligado'"
                          " WHERE external_id IN ('MLB9', 'MLB77')"))
    await db.commit()
    mundo["ml"].estado.update({"MLB9": True, "MLB77": True})
    mundo["ml"].chamadas.clear()
    async with flex_motor.session_scope() as outro:
        await outro.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": flex_motor._chave_trava(mundo["conta_id"], "MLB9")},
        )
        resumo = await flex_motor.emergencia(por=None)
    assert (resumo["desligados"], resumo["ocupados"], resumo["restantes"]) == (1, 1, 1)
    assert mundo["ml"].estado["MLB9"] is True
    # Clicar de novo continua.
    resumo = await flex_motor.emergencia(por=None)
    assert resumo["restantes"] == 0
    assert mundo["ml"].estado["MLB9"] is False


@pytest.mark.asyncio
async def test_emergencia_pega_o_que_a_rodada_ligou_durante_ela(db, mundo, monkeypatch):
    """Achado da revisão: a rodada está no meio de LIGAR o MLB1 (aprovado,
    lido desligado — não era alvo) quando a emergência começa. A emergência
    agora (1) tira a aprovação antes de escrever, (2) inclui o anúncio que
    tinha aprovação, (3) espera a trava dele e relê: o que foi ligado no meio
    é desligado em seguida."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    await flex_motor.rodar_motor()
    await db.execute(text("UPDATE flex_anuncio_estado SET aprovado_em = now()"
                          " WHERE external_id = 'MLB1'"))
    await db.commit()
    ml = mundo["ml"]
    ml.chamadas.clear()
    async with flex_motor.session_scope() as rodada:
        await rodada.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": flex_motor._chave_trava(mundo["conta_id"], "MLB1")},
        )
        tarefa = asyncio.create_task(flex_motor.emergencia(por=None))
        await asyncio.sleep(0.5)
        # A rodada termina de ligar o MLB1 (leu a aprovação antes da emergência).
        ml.estado["MLB1"] = True
        await rodada.execute(text("UPDATE flex_anuncio_estado SET observado = 'ligado'"
                                  " WHERE external_id = 'MLB1'"))
    resumo = await tarefa
    assert ("desligar", "MLB1") in ml.chamadas
    assert ml.estado["MLB1"] is False
    assert resumo["ocupados"] == 0 and resumo["restantes"] == 0
    est = (await _estados(db))["MLB1"]
    assert (est.observado, est.aprovado_em) == ("desligado", None)
    # E um LIGAR que chegue depois da emergência não liga: a aprovação saiu.
    tipo = await flex_motor._escrever(
        ml, integration_id=mundo["conta_id"], external_id="MLB1", plataforma="ml",
        acao="ligar", modo="piloto", por=None, skus_sp=("dg053.sp",),
    )
    assert tipo == "mudou"


@pytest.mark.asyncio
async def test_emergencia_desliga_o_que_nunca_foi_lido(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "ativo")
    resumo = await flex_motor.emergencia(por=None)
    # Sem estado nenhum (o motor nunca rodou): todos os anúncios da conta.
    assert resumo["alvos"] == 5
    assert sorted(i for _, i in mundo["ml"].escritas) == ["MLB1", "MLB2", "MLB3", "MLB77", "MLB9"]


# ---- Shopee ------------------------------------------------------------------------------


CANAIS = [
    {"logistic_id": 90001, "enabled": True, "is_free": False},
    {"logistic_id": 90022, "enabled": True, "is_free": False},
]


@pytest_asyncio.fixture
async def shopee(db, mundo, monkeypatch):
    conta = Integration(user_id=mundo["dono_id"], platform=IntegrationPlatform.SHOPEE,
                        name="loja", credentials=encrypt_json({"access_token": "t"}))
    db.add(conta)
    await db.flush()
    db.add(ProductLink(user_id=mundo["dono_id"], product_id=mundo["b009_id"],
                       integration_id=conta.id, platform=IntegrationPlatform.SHOPEE,
                       external_id="777", variation_id="1", stock=3))
    await db.commit()
    monkeypatch.setattr(mundo["cfg"], "flex_contas", str(conta.id))
    fake = FakeShopee({"777": CANAIS})

    async def _montar(integ):
        return fake

    monkeypatch.setattr(flex_motor, "montar_cliente", _montar)
    return fake


@pytest.mark.asyncio
async def test_shopee_so_le_sem_flex_shopee_escrita(db, mundo, shopee, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "ativo")
    resumo = await flex_motor.rodar_motor()
    assert shopee.escritas == []
    assert resumo["shopee_so_leitura"] == 1
    est = await _estados(db)
    assert (est["777"].plataforma, est["777"].desejado, est["777"].observado) == (
        "shopee",
        "inelegivel",
        "ligado",
    )
    assert ("desligar", "ignorado") in await _trilha(db, "777")


@pytest.mark.asyncio
async def test_shopee_escreve_com_a_lista_completa_e_confere(db, mundo, shopee, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "ativo")
    monkeypatch.setattr(mundo["cfg"], "flex_shopee_escrita", True)
    await flex_motor.rodar_motor()
    assert len(shopee.escritas) == 1
    item, payload = shopee.escritas[0][1]
    assert item == "777"
    assert payload == [
        {"logistic_id": 90001, "enabled": True, "is_free": False},
        {"logistic_id": 90022, "enabled": False, "is_free": False},
    ]
    # Leu antes de escrever (lista fresca) e depois (conferência).
    assert [c[0] for c in shopee.chamadas] == ["ler", "ler", "atualizar", "ler"]
    assert (await _estados(db))["777"].observado == "desligado"


# ---- worker e gancho ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_desligado_nem_chama_o_motor(mundo, monkeypatch):
    chamado: list[dict] = []

    async def _rodar(**kw):
        chamado.append(kw)
        return {"rodou": True}

    monkeypatch.setattr(flex_motor, "rodar_motor", _rodar)
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "desligado")
    await worker.flex_motor_tick({})
    assert chamado == []
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "observar")
    await worker.flex_motor_tick({})
    assert chamado == [{"origem": "cron"}]


@pytest.mark.asyncio
async def test_reavaliar_tenta_de_novo_se_a_rodada_estiver_ocupada(monkeypatch):
    async def _ocupado(**kw):
        assert kw == {"ler": False, "so_desligar": True, "origem": "evento"}
        return {"ocupado": True}

    monkeypatch.setattr(flex_motor, "rodar_motor", _ocupado)
    with pytest.raises(Retry):
        await worker.flex_reavaliar_run({})


@pytest.mark.asyncio
async def test_shipment_check_pede_a_reavaliacao(mundo, monkeypatch):
    from app import worker_pool

    pedidos: list[tuple] = []

    class Pool:
        async def enqueue_job(self, nome, *args, **kw):
            pedidos.append((nome, kw.get("_job_id", "")))
            return object()

    async def _pool():
        return Pool()

    monkeypatch.setattr(worker_pool, "get_arq_pool", _pool)
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "desligado")
    await marketplace_shipment_check._reavaliar_flex()
    assert pedidos == []
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "observar")
    await marketplace_shipment_check._reavaliar_flex()
    assert len(pedidos) == 1
    assert pedidos[0][0] == "flex_reavaliar_run"
    assert pedidos[0][1].startswith("flex_reavaliar:")
