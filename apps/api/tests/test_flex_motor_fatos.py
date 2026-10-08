"""Motor do Flex com os FATOS lidos nas contas em 02/10/2026 (revisão do cético).

  • aprovação só para anúncio LIDO desligado; a aprovação some quando o
    anúncio é lido já ligado — o motor nunca religa com aprovação velha;
  • a CONTA precisa poder ter Flex (ML: assinatura "in"; Shopee: Entrega
    Direta ligada na loja) — senão, nenhuma leitura/escrita por anúncio;
  • fila de leituras justa (rodízio, espera crescente; uma conta com 403 não
    congela as outras);
  • anúncio não ativo não ocupa vaga, não pede aprovação e, ligado, desliga;
  • DESLIGAR recusado tenta de novo em 1 h;
  • LIGAR aprovado tem vaga reservada no teto (e, com a rodada ocupada, vai
    para a fila do worker);
  • descoberta dos anúncios da conta que o DaVinci não conhece;
  • emergência como job, com andamento.

Nenhuma chamada externa: os clientes falsos de tests/test_flex_motor.
"""

# Os cenários `mundo`/`shopee` vêm de tests/test_flex_motor: o pytest pega a
# fixture importada pelo nome do parâmetro (o F811 é esse uso).
# ruff: noqa: F811
from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from arq import Retry
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker, worker_pool
from app.models import (
    FlexAnuncioEstado,
    FlexConta,
    FlexEmergencia,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Product,
    ProductLink,
    User,
)
from app.security.cipher import encrypt_json
from app.services import flex_config, flex_motor
from app.services.advisory_lock import SYNC_NAMESPACE
from app.services.marketplaces import flex_api
from app.services.marketplaces.flex_api import AssinaturaFlex, ResultadoFlex
from tests.test_flex_motor import (  # noqa: F401 — fixtures
    FakeML,
    _estados,
    _trilha,
    mundo,
    shopee,
)


@pytest.fixture(autouse=True)
def _flex_visivel(monkeypatch):
    # Estes testes são do Flex em si; quem vê (`flex_usuarios`) tem teste
    # próprio em test_flex_visibilidade.py.
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: True)


async def _conta(db: AsyncSession, iid) -> FlexConta | None:
    db.expire_all()
    return await db.get(FlexConta, iid)


# ---- 1. aprovação só de quem foi LIDO desligado ---------------------------------------


@pytest.mark.asyncio
async def test_aprovacao_velha_nao_religa_sozinha(db, mundo, monkeypatch):
    """Cenário do cético: MLB2 já está com Flex no ML, mas a 1ª rodada não
    chega a lê-lo (teto). Antes ele aparecia "esperando aprovação"; a pessoa
    aprovava, a leitura via LIGADO e a aprovação ficava gravada — quando o
    vendedor desligava no painel, a rodada seguinte RELIGAVA sozinha."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    monkeypatch.setattr(flex_motor, "_TETO_LEITURAS_ML", 1)
    ml = mundo["ml"]
    ml.estado.update({"MLB1": True, "MLB2": True})
    await flex_motor.rodar_motor()
    est = await _estados(db)
    assert ml.leituras == ["MLB1"]  # o teto de 1: só o primeiro da fila
    # Não lido: a regra quer ligado, mas NÃO pede aprovação (não se sabe).
    assert (est["MLB2"].desejado, est["MLB2"].observado) == ("ligado", None)
    assert est["MLB2"].aguardando_aprovacao is False
    assert ("pedir_aprovacao", "pendente") not in await _trilha(db, "MLB2")

    # A pessoa aprova mesmo assim (a API deixa: a regra quer ligado). O motor
    # lê: já está ligado — a aprovação foi "usada" e some, sem ligar nada.
    res = await flex_motor.aprovar(mundo["conta_id"], "MLB2", por=mundo["dono_id"])
    assert ("ligar", "MLB2") not in ml.escritas
    assert res["aplicado"] is False
    est = (await _estados(db))["MLB2"]
    assert (est.observado, est.aprovado_em, est.aprovado_por) == ("ligado", None, None)

    # O vendedor desliga no painel do ML: o motor NÃO religa — pede de novo.
    monkeypatch.setattr(flex_motor, "_TETO_LEITURAS_ML", 400)
    ml.estado["MLB2"] = False
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ("ligar", "MLB2") not in ml.escritas
    est = (await _estados(db))["MLB2"]
    assert (est.observado, est.aguardando_aprovacao) == ("desligado", True)


@pytest.mark.asyncio
async def test_aprovacao_some_quando_alguem_liga_no_painel(db, mundo, monkeypatch):
    """Observar: aprovado MLB1 (lido desligado); alguém liga no painel. A
    leitura vê LIGADO e a aprovação sai — não fica pendurada para um dia
    ligar sozinha depois que alguém desligar."""
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    assert (await _estados(db))["MLB1"].aprovado_em is not None
    ml.estado["MLB1"] = True
    await flex_motor.rodar_motor()
    est = (await _estados(db))["MLB1"]
    assert (est.observado, est.aprovado_em) == ("ligado", None)


# ---- 2. a conta pode ter Flex? ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["out", "pending", "http_404", "http_403"])
async def test_conta_sem_flex_nao_le_nem_escreve(db, mundo, monkeypatch, status):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    ml.assinatura = AssinaturaFlex(False, status, f"assinatura do Flex: {status}")
    resumo = await flex_motor.rodar_motor()
    # Nenhuma leitura nem escrita por anúncio, nem descoberta.
    assert ml.chamadas == [] and ml.descobertas == []
    assert resumo["contas_sem_flex"] == 1
    est = await _estados(db)
    assert set(est) == {"MLB1", "MLB2", "MLB3", "MLB9", "MLB77"}
    motivo = f"conta sem Flex ativo no ML (status {status})"
    for e in est.values():
        assert (e.desejado, e.motivo, e.aguardando_aprovacao) == ("inelegivel", motivo, False)
    conta = await _conta(db, mundo["conta_id"])
    assert (conta.flex_ativo, conta.status) == (False, status)

    # Aprovar: a conta não pode (409 na tela), sem escrever nada.
    with pytest.raises(flex_motor.FlexRegraError) as e:
        await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    assert e.value.codigo == "conta_sem_flex"

    # Emergência: a conta fica de fora — antes eram DELETE em todos os
    # anúncios nunca lidos dela.
    resumo = await flex_motor.emergencia(por=None)
    assert ml.escritas == []
    assert (resumo["contas_sem_flex"], resumo["alvos"]) == (1, 0)


@pytest.mark.asyncio
async def test_assinatura_em_cache_e_erro_mantem_a_anterior(db, mundo, monkeypatch):
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    await flex_motor.rodar_motor()
    assert ml.assinaturas_lidas == 1  # vale por 1 h
    # Passou 1 h e a conferência falhou (rede/5xx): vale a anterior ("in").
    await db.execute(text("UPDATE flex_conta SET lido_em = now() - interval '2 hours'"))
    await db.commit()
    ml.assinatura = AssinaturaFlex(None, None, "503 Service Unavailable")
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ml.assinaturas_lidas == 2
    conta = await _conta(db, mundo["conta_id"])
    assert (conta.flex_ativo, conta.status, conta.erro) == (True, "in", "503 Service Unavailable")
    # Trava de origem (08/10/2026): a leitura da assinatura tem mais de 1 h e
    # não se confirmou — não se sabe se o motoboy ainda sai da cidade do
    # local. A conta fica parada (sem leitura nem escrita) até responder; o
    # banco guarda a origem que se sabia.
    assert ml.leituras == []
    assert conta.origem_cidade == "São Bernardo do Campo"
    est = await _estados(db)
    assert {e.motivo for e in est.values()} == {flex_motor.MOTIVO_SEM_ORIGEM}
    # Volta a responder: a conta saiu do Flex ("out").
    ml.assinatura = AssinaturaFlex(False, "out", "assinatura do Flex: out")
    await flex_motor.rodar_motor()
    conta = await _conta(db, mundo["conta_id"])
    assert (conta.flex_ativo, conta.status, conta.erro) == (False, "out", None)


@pytest.mark.asyncio
async def test_conta_nunca_conferida_nao_escreve(db, mundo, monkeypatch):
    """Sem resposta nenhuma da assinatura (nunca conferida): negação por
    padrão — sem leitura, sem escrita, sem pedir aprovação."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    ml.assinatura = AssinaturaFlex(None, None, "timeout")
    await flex_motor.rodar_motor()
    assert ml.chamadas == []
    est = await _estados(db)
    assert {e.motivo for e in est.values()} == {
        "não deu para conferir a assinatura do Flex da conta no ML"
    }
    assert not any(e.aguardando_aprovacao for e in est.values())
    # Na emergência a dúvida não segura: a conta não conferida entra.
    resumo = await flex_motor.emergencia(por=None)
    assert resumo["contas_sem_flex"] == 0 and resumo["alvos"] == 5


@pytest.mark.asyncio
async def test_shopee_com_entrega_direta_desligada_na_loja(db, mundo, shopee, monkeypatch):
    """Fato: o 90022 existe nas 14 lojas, mas está desligado NA LOJA. O
    anúncio não pede aprovação (o botão não ligaria nada) e nem é lido."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "ativo")
    shopee.loja = AssinaturaFlex(False, "out", "Entrega Direta desligada na loja")
    await flex_motor.rodar_motor()
    assert shopee.chamadas == []
    est = (await _estados(db))["777"]
    assert est.desejado == "inelegivel"
    assert est.motivo == "Entrega Direta desligada na loja — ligue no Seller Center primeiro"
    assert est.aguardando_aprovacao is False


@pytest.mark.asyncio
async def test_shopee_so_leitura_nao_pede_aprovacao_nem_toma_vaga(db, mundo, shopee, monkeypatch):
    """Loja com a Entrega Direta ligada, mas `flex_shopee_escrita` desligado:
    o anúncio Shopee com saldo NÃO pede aprovação (aprovar não ligaria nada)
    e não toma a vaga da família dos anúncios do ML."""
    ci = (await db.execute(select(Product.id).where(Product.sku == "dg053.ci"))).scalar_one()
    loja = (await db.execute(select(Integration.id).where(Integration.name == "loja"))).scalar_one()
    db.add(
        ProductLink(
            user_id=mundo["dono_id"],
            product_id=ci,
            integration_id=loja,
            platform=IntegrationPlatform.SHOPEE,
            external_id="888",
            variation_id="1",
            stock=4,
            created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    )  # o vínculo mais antigo
    await db.commit()
    shopee.canais["888"] = [
        {"logistic_id": 90001, "enabled": True, "is_free": False},
        {"logistic_id": 90022, "enabled": False, "is_free": False},
    ]
    monkeypatch.setattr(mundo["cfg"], "flex_contas", f"{mundo['conta_id']},{loja}")

    async def _montar(integ):
        return shopee if integ.platform == IntegrationPlatform.SHOPEE else mundo["ml"]

    monkeypatch.setattr(flex_motor, "montar_cliente", _montar)
    await flex_motor.rodar_motor()
    est = await _estados(db)
    assert est["888"].desejado == "desligado"
    assert est["888"].motivo.startswith("Shopee só leitura (flex_shopee_escrita) — saldo Flex 5")
    assert est["888"].aguardando_aprovacao is False
    # As 2 vagas da família continuam com o ML.
    assert (est["MLB1"].desejado, est["MLB2"].desejado) == ("ligado", "ligado")
    with pytest.raises(flex_motor.FlexRegraError) as e:
        await flex_motor.aprovar(loja, "888", por=None)
    assert e.value.codigo == "shopee_so_leitura"


@pytest.mark.asyncio
async def test_shopee_status_do_anuncio_vem_na_leitura(db, mundo, shopee, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "ativo")
    monkeypatch.setattr(mundo["cfg"], "flex_shopee_escrita", True)
    shopee.status["777"] = "UNLIST"
    await flex_motor.rodar_motor()
    est = (await _estados(db))["777"]
    assert est.status_anuncio == "paused" and est.status_em is not None


# ---- 3. fila de leituras justa ---------------------------------------------------------


def _anuncio(conta, ext, **kw) -> flex_motor.Anuncio:
    return flex_motor.Anuncio(integration_id=conta, external_id=ext, plataforma="ml", **kw)


def test_escolher_leituras_rodizio_espera_e_aprovados_primeiro():
    agora = datetime(2026, 10, 2, 12, tzinfo=UTC)
    a = uuid.UUID(int=1)
    b = uuid.UUID(int=2)
    anuncios = [_anuncio(a, f"A{i}") for i in range(4)] + [_anuncio(b, f"B{i}") for i in range(2)]
    decisoes = {x.chave: flex_motor.Decisao("desligado", "x") for x in anuncios}
    lido = agora - timedelta(hours=1)

    def est(**kw):
        base = {
            "plataforma": "ml",
            "desejado": "desligado",
            "observado": "desligado",
            "observado_em": lido,
            "recusa": None,
            "leitura_em": lido,
        }
        return flex_motor._Estado(**{**base, **kw})

    estados = {
        # A0 falhou há pouco: espera a próxima leitura (não volta ao topo).
        (a, "A0"): est(
            observado=None,
            observado_em=None,
            leitura_em=agora,
            proxima_leitura=agora + timedelta(minutes=15),
        ),
        # A1 lido há muito tempo; A2 nunca tentado; A3 aprovado por uma pessoa.
        (a, "A1"): est(leitura_em=agora - timedelta(days=1)),
        (a, "A3"): est(aprovado=True),
        (b, "B0"): est(),
        (b, "B1"): est(leitura_em=agora - timedelta(hours=2)),
    }
    ordem = [
        x.external_id
        for x in flex_motor._escolher_leituras(anuncios, decisoes, estados, None, agora)
    ]
    # A conta "a" vem primeiro (o aprovado); depois rodízio a, b, a, b, a.
    assert ordem == ["A3", "B1", "A2", "B0", "A1"]
    # Conta bloqueada (sem Flex): fora da fila.
    bloqueados = [
        x if x.integration_id == a else flex_motor.replace(x, bloqueio="conta sem Flex")
        for x in anuncios
    ]
    ordem = [
        x.external_id
        for x in flex_motor._escolher_leituras(bloqueados, decisoes, estados, None, agora)
    ]
    assert ordem == ["A3", "A2", "A1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_http", [403, 401])
async def test_conta_com_403_nao_congela_as_outras(db, mundo, monkeypatch, status_http):
    """Achado: a leitura que falha ficava no topo da fila e tomava as vagas
    de todas as rodadas — a conta boa nunca era relida e nada desligava. Com
    teto de 3 leituras por rodada, uma conta cujas leituras dão 403/401 não
    impede o MLB9 da conta boa de ser lido e desligado."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    monkeypatch.setattr(flex_motor, "_TETO_LEITURAS_ML", 3)
    dono = mundo["dono_id"]
    quebrada = Integration(
        user_id=dono,
        platform=IntegrationPlatform.ML,
        name="quebrada",
        credentials=encrypt_json({"access_token": "x"}),
    )
    db.add(quebrada)
    await db.flush()
    quebrada_id = quebrada.id
    for i in range(6):
        # b009: sem lote — inelegível, não disputa a vaga da família.
        db.add(
            ProductLink(
                user_id=dono,
                product_id=mundo["b009_id"],
                integration_id=quebrada_id,
                platform=IntegrationPlatform.ML,
                external_id=f"MLBQ{i}",
                stock=3,
            )
        )
    await db.commit()
    monkeypatch.setattr(mundo["cfg"], "flex_contas", f"{mundo['conta_id']},{quebrada_id}")

    class Quebrada(FakeML):
        async def ler_flex(self, item):
            self.chamadas.append(("ler", item))
            return ResultadoFlex(
                flex_api.SEM_PERMISSAO, status_http=status_http, detalhe="forbidden"
            )

    q = Quebrada()
    boa = mundo["ml"]

    async def _montar(integ):
        return q if integ.id == quebrada_id else boa

    monkeypatch.setattr(flex_motor, "montar_cliente", _montar)
    rodadas = 0
    while ("desligar", "MLB9") not in boa.escritas and rodadas < 6:
        q.chamadas.clear()
        await flex_motor.rodar_motor()
        rodadas += 1
        # A quebrada nunca leva o teto inteiro; com 401 é 1 leitura e a vaga
        # que sobra vai para a conta boa.
        assert len(q.leituras) <= (1 if status_http == 401 else 2)
    assert ("desligar", "MLB9") in boa.escritas
    assert rodadas <= 5
    # A leitura que falhou espera (crescente) — não volta ao topo da fila.
    db.expire_all()
    falhas = (
        (
            await db.execute(
                select(FlexAnuncioEstado).where(
                    FlexAnuncioEstado.integration_id == quebrada_id,
                    FlexAnuncioEstado.leitura_falhas > 0,
                )
            )
        )
        .scalars()
        .all()
    )
    assert falhas
    for e in falhas:
        assert e.proxima_leitura > datetime.now(UTC) + timedelta(minutes=10)
        assert e.ultimo_erro.startswith(f"leitura: {status_http}")


@pytest.mark.asyncio
async def test_leitura_que_falha_espera_crescente_e_volta_quando_le(db, mundo, monkeypatch):
    ml = mundo["ml"]
    del ml.estado["MLB3"]  # 404 no GET do Flex
    await flex_motor.rodar_motor()
    est = (await _estados(db))["MLB3"]
    assert est.leitura_falhas == 1 and est.leitura_em is not None
    primeira = est.proxima_leitura - est.leitura_em
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert "MLB3" not in ml.leituras  # esperando
    await db.execute(
        text(
            "UPDATE flex_anuncio_estado SET proxima_leitura = now()"
            " - interval '1 minute' WHERE external_id = 'MLB3'"
        )
    )
    await db.commit()
    await flex_motor.rodar_motor()
    est = (await _estados(db))["MLB3"]
    assert est.leitura_falhas == 2
    assert est.proxima_leitura - est.leitura_em > primeira  # crescente
    # Voltou a responder: zera.
    ml.estado["MLB3"] = False
    await db.execute(text("UPDATE flex_anuncio_estado SET proxima_leitura = NULL"))
    await db.commit()
    await flex_motor.rodar_motor()
    est = (await _estados(db))["MLB3"]
    assert (est.leitura_falhas, est.proxima_leitura, est.observado) == (0, None, "desligado")


# ---- 4. status do anúncio --------------------------------------------------------------


def test_pausado_ligado_nao_ocupa_vaga_da_familia():
    conta = uuid.UUID(int=7)
    v = (flex_motor.Variacao(sku="dg053.ci", estoque_publicado=10),)
    saldos = {"dg053.sp": flex_motor.SaldoSp("dg053.sp", 10)}
    cfg = flex_motor.ConfigFlex(n_liga=3, n_desliga=1, max_anuncios_por_familia=2)
    antigo = datetime(2025, 1, 1, tzinfo=UTC)
    anuncios = [
        _anuncio(conta, "MLB1", variacoes=v, observado="ligado", status="paused", desde=antigo),
        _anuncio(
            conta,
            "MLB2",
            variacoes=v,
            observado="ligado",
            status="active",
            desde=antigo + timedelta(days=1),
        ),
        _anuncio(conta, "MLB3", variacoes=v, observado="ligado", desde=antigo + timedelta(days=2)),
    ]
    d = flex_motor.decidir_lote(anuncios, saldos, cfg)
    assert d[(conta, "MLB1")].desejado == "desligado"
    assert "pausado" in d[(conta, "MLB1")].motivo
    # Os dois que vendem ficam com as vagas (antes o MLB3 perdia para o pausado).
    assert (d[(conta, "MLB2")].desejado, d[(conta, "MLB3")].desejado) == ("ligado", "ligado")


@pytest.mark.asyncio
async def test_anuncio_pausado_desliga_e_nao_pede_aprovacao(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    ml.estado.update({"MLB1": True, "MLB2": False})
    db.add_all(
        [
            # MLB1 pausado (importação de agora) e com Flex: desliga.
            Listing(
                user_id=mundo["dono_id"],
                integration_id=mundo["conta_id"],
                platform=IntegrationPlatform.ML,
                external_id="MLB1",
                title="mala",
                status=ListingStatus.PAUSED,
                imported_at=datetime.now(UTC),
            ),
            # MLB2 ativo na importação de ONTEM, mas a descoberta de hoje o viu
            # pausado: vale o mais novo — não pede aprovação.
            Listing(
                user_id=mundo["dono_id"],
                integration_id=mundo["conta_id"],
                platform=IntegrationPlatform.ML,
                external_id="MLB2",
                title="mala 2",
                status=ListingStatus.ACTIVE,
                imported_at=datetime.now(UTC) - timedelta(days=1),
            ),
        ]
    )
    await db.commit()
    ml.conta_itens = {"MLB2": "paused", "MLB3": "active"}
    await flex_motor.rodar_motor()
    assert ("desligar", "MLB1") in ml.escritas
    est = await _estados(db)
    assert (est["MLB1"].desejado, est["MLB1"].status_anuncio) == ("desligado", "paused")
    assert est["MLB1"].motivo.startswith("anúncio pausado na plataforma")
    assert (est["MLB2"].desejado, est["MLB2"].status_anuncio) == ("desligado", "paused")
    assert est["MLB2"].aguardando_aprovacao is False
    # O MLB3 (ativo) fica com a vaga e pede aprovação.
    assert (est["MLB3"].desejado, est["MLB3"].aguardando_aprovacao) == ("ligado", True)


# ---- 5. desligar recusado ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_desligar_recusado_tenta_de_novo_em_1h(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    ml.desligar_resposta["MLB9"] = ResultadoFlex(
        flex_api.INELEGIVEL, status_http=403, detalhe="item down"
    )
    await flex_motor.rodar_motor()
    est = (await _estados(db))["MLB9"]
    agora = datetime.now(UTC)
    # Antes: 24 h. O anúncio reativado não pode passar um dia vendendo Flex.
    assert agora + timedelta(minutes=50) < est.proxima_tentativa < agora + timedelta(minutes=70)
    assert est.recusa is None  # recusa só trava o LIGAR
    # Passou a hora: lê de novo (ainda ligado) e desliga.
    del ml.desligar_resposta["MLB9"]
    await db.execute(
        text(
            "UPDATE flex_anuncio_estado SET proxima_tentativa = now()"
            " - interval '1 minute' WHERE external_id = 'MLB9'"
        )
    )
    await db.commit()
    await flex_motor.rodar_motor()
    assert ml.estado["MLB9"] is False


# ---- 6. LIGAR aprovado não espera a fila de desligar -----------------------------------


@pytest.mark.asyncio
async def test_ligar_aprovado_tem_vaga_reservada_no_teto(db, mundo, monkeypatch):
    ml = mundo["ml"]
    await flex_motor.rodar_motor()  # observar: MLB1 lido desligado, pede aprovação
    await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=None)
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    monkeypatch.setattr(mundo["cfg"], "flex_teto_escritas_por_rodada", 2)
    ml.chamadas.clear()
    resumo = await flex_motor.rodar_motor()
    # Dois desligar na fila (MLB9, MLB77) e o teto é 2: um fica para a
    # próxima, o LIGAR aprovado sai já.
    assert ("ligar", "MLB1") in ml.escritas
    assert len([c for c in ml.escritas if c[0] == "desligar"]) == 1
    assert resumo["adiados_teto"] == 1


@pytest.mark.asyncio
async def test_aprovar_com_rodada_ocupada_vai_para_a_fila(
    db, mundo, monkeypatch, client: AsyncClient, auth_as
):
    pedidos: list[tuple] = []

    class Pool:
        async def enqueue_job(self, nome, *args, **kw):
            pedidos.append((nome, args, kw))
            return object()

    async def _pool():
        return Pool()

    monkeypatch.setattr(worker_pool, "get_arq_pool", _pool)
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    auth_as(await db.get(User, mundo["dono_id"]))
    ml.chamadas.clear()
    async with flex_motor.session_scope() as rodada:
        await rodada.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": flex_motor._MOTOR_LOCK_KEY},
        )
        r = await client.post(f"/api/flex/anuncios/{mundo['conta_id']}/MLB1/aprovar")
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert (corpo["aprovado"], corpo["aplicado"], corpo["na_fila"]) == (True, False, True)
        nome, args, kw = pedidos[-1]
        assert nome == "flex_aprovado_run"
        assert args == (str(mundo["conta_id"]), "MLB1", str(mundo["dono_id"]))
        assert kw["_job_id"] == f"flex_aprovado:{mundo['conta_id']}:MLB1"
        # O job com a rodada ainda ocupada: tenta de novo em 1 min.
        with pytest.raises(Retry):
            await worker.flex_aprovado_run({}, *args)
    # A rodada soltou: o job liga.
    res = await worker.flex_aprovado_run({}, *args)
    assert res["aplicado"] is True
    assert ("ligar", "MLB1") in ml.escritas


# ---- 7. descoberta dos anúncios da conta ----------------------------------------------


@pytest.mark.asyncio
async def test_descoberta_poe_no_estado_o_anuncio_que_o_davinci_nao_conhece(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    ml.conta_itens = {"MLB1": "active", "MLB500": "active", "MLB501": "paused"}
    ml.estado.update({"MLB500": True, "MLB501": True})
    resumo = await flex_motor.rodar_motor()
    assert ml.descobertas == ["active", "paused"]
    assert resumo["fora_do_davinci_novos"] == 2
    est = await _estados(db)
    for ext, status in (("MLB500", "active"), ("MLB501", "paused")):
        assert est[ext].desejado == "desligado"
        assert est[ext].motivo == flex_motor.MOTIVO_FORA_DO_DAVINCI
        assert est[ext].status_anuncio == status
        assert est[ext].aguardando_aprovacao is False
    # Nasceram com Flex: o motor lê e desliga.
    assert ("desligar", "MLB500") in ml.escritas and ("desligar", "MLB501") in ml.escritas
    conta = await _conta(db, mundo["conta_id"])
    assert (conta.descoberta_ok, conta.descoberta_total, conta.descoberta_novos) == (True, 3, 2)
    assert ("decidir", "ok") in await _trilha(db, "MLB500")

    # Uma vez por dia: a rodada seguinte não lista a conta de novo.
    await flex_motor.rodar_motor()
    assert ml.descobertas == ["active", "paused"]
    await db.execute(text("UPDATE flex_conta SET descoberta_em = now() - interval '25 hours'"))
    await db.commit()
    await flex_motor.rodar_motor()
    assert ml.descobertas == ["active", "paused"] * 2


@pytest.mark.asyncio
async def test_emergencia_desliga_o_anuncio_descoberto(db, mundo, monkeypatch):
    """O "Desligar tudo" antes da primeira rodada: a emergência descobre a
    conta e desliga também o anúncio que o DaVinci não conhece."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "ativo")
    ml = mundo["ml"]
    ml.conta_itens = {"MLB600": "active"}
    ml.estado["MLB600"] = True
    resumo = await flex_motor.emergencia(por=None)
    assert ("desligar", "MLB600") in ml.escritas
    assert resumo["alvos"] == 6 and resumo["restantes"] == 0
    assert ml.estado["MLB600"] is False


# ---- 8. emergência como job ------------------------------------------------------------


@pytest.mark.asyncio
async def test_emergencia_job_tira_aprovacoes_na_hora_e_grava_o_andamento(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    monkeypatch.setattr(flex_motor, "_EMERGENCIA_PROGRESSO_S", 0.0)
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    await db.execute(
        text(
            "UPDATE flex_anuncio_estado SET aprovado_em = now()"
            " WHERE external_id IN ('MLB2', 'MLB3')"
        )
    )
    await db.commit()
    ml.chamadas.clear()
    prep = await flex_motor.preparar_emergencia(por=mundo["dono_id"])
    # O pedido HTTP: só banco. As aprovações já saíram, nada foi escrito.
    assert ml.chamadas == []
    assert prep["aprovacoes"] == 2
    est = await _estados(db)
    assert est["MLB2"].aprovado_em is None and est["MLB3"].aprovado_em is None
    db.expire_all()
    linha = await db.get(FlexEmergencia, prep["id"])
    assert (linha.status, linha.por, linha.modo) == ("na_fila", mundo["dono_id"], "piloto")
    assert sorted(x[1] for x in linha.aprovados) == ["MLB2", "MLB3"]

    resumo = await worker.flex_emergencia_run({}, prep["id"])
    db.expire_all()
    linha = await db.get(FlexEmergencia, prep["id"])
    assert linha.status == "concluida"
    assert linha.iniciado_em is not None and linha.terminado_em is not None
    assert linha.resumo["processados"] == resumo["processados"] == resumo["alvos"]
    # Os aprovados (lidos desligados) também são alvo; os ligados desligam.
    assert resumo["restantes"] == 0
    assert {e.observado for e in (await _estados(db)).values()} == {"desligado"}
    # Rodar o mesmo job de novo não faz nada (já concluída).
    ml.chamadas.clear()
    await worker.flex_emergencia_run({}, prep["id"])
    assert ml.chamadas == []


# ---- revisão de 05/10/2026 (antes de publicar em observar) ------------------------


def test_leitura_velha_volta_para_a_fila_mesmo_batendo_com_a_regra():
    """Em observar nada desliga: os anúncios lidos LIGADOS que a regra quer
    desligados ficam "precisa" para sempre e tomam as 400 vagas. O anúncio já
    certo (Z) nunca era relido — uma mudança no painel do ML não aparecia."""
    agora = datetime(2026, 10, 5, 12, tzinfo=UTC)
    a = uuid.UUID(int=1)
    muitos = [_anuncio(a, f"L{i}") for i in range(1000)]
    z = _anuncio(a, "Z")
    anuncios = [*muitos, z]
    decisoes = {x.chave: flex_motor.Decisao("desligado", "x") for x in anuncios}
    lido = agora - timedelta(hours=1)

    def est(obs, quando):
        return flex_motor._Estado(
            plataforma="ml",
            desejado="desligado",
            observado=obs,
            observado_em=quando,
            recusa=None,
            leitura_em=quando,
        )

    estados = {x.chave: est("ligado", lido) for x in muitos}
    estados[z.chave] = est("desligado", agora - timedelta(hours=7))
    ordem = [
        x.external_id
        for x in flex_motor._escolher_leituras(anuncios, decisoes, estados, None, agora)
    ]
    assert ordem.index("Z") < flex_motor._TETO_LEITURAS_ML
    # Lido há pouco e batendo com a regra: continua no fim da fila.
    estados[z.chave] = est("desligado", lido)
    ordem = [
        x.external_id
        for x in flex_motor._escolher_leituras(anuncios, decisoes, estados, None, agora)
    ]
    assert ordem.index("Z") == 1000


class _Integ:
    def __init__(self, iid):
        self.id = iid


class _ML503(FakeML):
    async def ler_flex(self, item):
        self.chamadas.append(("ler", item))
        return ResultadoFlex(flex_api.REPETIR, status_http=503, detalhe="503")


@pytest.mark.asyncio
async def test_ler_para_a_conta_depois_de_5_erros_seguidos(db):
    a = uuid.UUID(int=11)
    b = uuid.UUID(int=12)
    ruim, boa = _ML503(), FakeML({f"B{i}": False for i in range(3)})
    escolhidos = [_anuncio(a, f"A{i}") for i in range(20)]
    escolhidos += [_anuncio(b, f"B{i}") for i in range(3)]
    resumo: dict = {}
    out = await flex_motor._ler(escolhidos, {a: _Integ(a), b: _Integ(b)}, {a: ruim, b: boa}, resumo)
    assert len(ruim.leituras) == flex_motor._REPETIR_SEGUIDOS
    assert boa.leituras == ["B0", "B1", "B2"]
    assert resumo["contas_interrompidas"] == 1
    assert {k[1] for k in out} == {"A0", "A1", "A2", "A3", "A4", "B0", "B1", "B2"}


@pytest.mark.asyncio
async def test_ler_respeita_o_prazo_e_deixa_o_resto_para_a_proxima(db):
    a = uuid.UUID(int=21)
    cli = FakeML({f"A{i}": False for i in range(5)})
    escolhidos = [_anuncio(a, f"A{i}") for i in range(5)]
    resumo: dict = {}
    out = await flex_motor._ler(
        escolhidos, {a: _Integ(a)}, {a: cli}, resumo, prazo=time.monotonic() - 1
    )
    assert out == {} and cli.leituras == []
    assert resumo["leituras_adiadas"] == 5
