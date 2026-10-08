"""Local de saída do Flex e trava de ORIGEM (Eduardo, 08/10/2026: "o motoboy
vai sair de São Bernardo" e "não vai ser pra sempre fixo em São Bernardo, eu
quero poder alterar").

A leitura de 08/10 mostrou as 17 contas do ML com a assinatura "in" saindo de
Piracicaba. O motor guarda a origem da assinatura (subscriptions/v1 →
origin.zip_code / origin.city.name) e só mexe na conta cuja saída do Flex é
na CIDADE do local de saída (`flex_local`, aba Flex — São Bernardo do Campo /
.sp de fábrica). O LOTE do local é o estoque que liga/desliga o Flex.

O que se confere aqui:
  • o local: padrão, troca (só admin), validação, cache e Histórico;
  • a leitura da origem no formato real do ML (pares CEP+cidade; uma saída
    sem cidade = não se sabe; nenhuma `origin` = formato mudou, fica a antiga);
  • conta saindo de Piracicaba: nem lê, nem desliga, nem liga, nem aprova —
    e o anúncio mostra o porquê em português claro;
  • trocar o endereço (ou o local): o "Sincronizar agora" vê na hora; antes
    de LIGAR um anúncio aprovado a assinatura é relida (sem o cache de 1 h);
  • a plataforma fora do ar por mais de 1 h: a origem velha não vale;
  • trocar o lote muda o estoque que o motor conta;
  • a emergência continua desligando tudo, de onde quer que saia;
  • a tela: /config, /acesso e o "ligados que deveriam desligar".
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FlexAnuncioEstado, FlexConta, FlexLocal, Product, UserRole
from app.services import flex_config, flex_envio, flex_local, flex_motor, flex_textos
from app.services.marketplaces import flex_api
from app.services.marketplaces.flex_api import AssinaturaFlex
from app.services.marketplaces.ml import ML_API_BASE
from tests.test_flex_api import cena
from tests.test_flex_clientes import ASSINATURA, _ml
from tests.test_flex_motor import ORIGEM_PIRACICABA, ORIGEM_SB, _estados, mundo

# Fixtures importadas: o pytest acha pelo nome do parâmetro.
_FIXTURES = (cena, mundo)

PIRACICABA = AssinaturaFlex(
    True,
    "in",
    "assinatura do Flex ativa",
    origem_cep=ORIGEM_PIRACICABA,
    origem_cidade="Piracicaba",
)
SAO_BERNARDO = AssinaturaFlex(
    True,
    "in",
    "assinatura do Flex ativa",
    origem_cep=ORIGEM_SB,
    origem_cidade="São Bernardo do Campo",
)
MOTIVO_PIRACICABA = (
    "saída do Flex da conta fora de São Bernardo do Campo: Piracicaba (CEP 13400-123)"
)


@pytest.fixture(autouse=True)
def _flex_visivel(monkeypatch):
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: True)


async def _conta(db: AsyncSession, iid) -> FlexConta | None:
    db.expire_all()
    return await db.get(FlexConta, iid)


async def _velha(db: AsyncSession) -> None:
    """A leitura da assinatura passa da validade de 1 h."""
    await db.execute(text("UPDATE flex_conta SET lido_em = now() - interval '2 hours'"))
    await db.commit()


# ---- o local de saída -------------------------------------------------------------


def test_cidade_sem_acento_nem_maiuscula():
    assert flex_local.mesma_cidade("São Bernardo do Campo", "sao  bernardo do CAMPO")
    for variante in (
        "São Bernardo do Campo - SP",
        "São Bernardo do Campo/SP",
        "São Bernardo do Campo (SP)",
        "sao-bernardo do campo",
    ):
        assert flex_local.mesma_cidade("São Bernardo do Campo", variante), variante
    assert flex_local.mesma_cidade("Santa Bárbara d’Oeste", "Santa Barbara d'Oeste")
    assert flex_local.mesma_cidade(" Piracicaba ", "PIRACICABA")
    assert not flex_local.mesma_cidade("São Bernardo do Campo", "Santo André")
    assert not flex_local.mesma_cidade("", "")
    sb = flex_local.LocalFlex("São Bernardo do Campo", "sp")
    assert flex_local.origem_ok("São Bernardo do Campo", sb) is True
    assert flex_local.origem_ok("Sao Bernardo do Campo", sb) is True
    assert flex_local.origem_ok("Piracicaba", sb) is False
    # Duas saídas: as duas têm de ser na cidade (uma fora já bloqueia).
    assert flex_local.origem_ok("São Bernardo do Campo, Piracicaba", sb) is False
    assert flex_local.origem_ok(None, sb) is False


@pytest.mark.asyncio
async def test_local_de_fabrica_troca_e_cache(db: AsyncSession, make_user):
    # Sem linha no banco: São Bernardo do Campo / .sp (o que era fixo).
    local = await flex_local.carregar(forcar=True)
    assert (local.cidade, local.lote) == ("São Bernardo do Campo", "sp")
    assert (flex_local.cidade(), flex_local.lote()) == ("São Bernardo do Campo", "sp")
    dono = await make_user(role=UserRole.ADMIN)
    novo = await flex_local.salvar(db, cidade="  Piracicaba\x00 ", lote=".PI", por=dono.id)
    # Antes do commit o cache não troca (o commit pode falhar).
    assert flex_local.lote() == "sp"
    await db.commit()
    flex_local.usar(novo)
    assert (novo.cidade, novo.lote, novo.atualizado_por) == ("Piracicaba", "pi", dono.id)
    assert (flex_local.cidade(), flex_local.lote()) == ("Piracicaba", "pi")
    flex_local.limpar_cache()
    assert flex_local.lote() == "sp"  # sem carregar: o de fábrica
    assert (await flex_local.carregar()).lote == "pi"
    linha = (await db.execute(select(FlexLocal))).scalars().all()
    assert [(x.id, x.cidade, x.lote) for x in linha] == [(1, "Piracicaba", "pi")]
    for cidade, lote, codigo in [
        ("", "sp", "cidade_vazia"),
        ("x" * 101, "sp", "cidade_longa"),
        ("Piracicaba", "cd", "lote_invalido"),
        ("Piracicaba, SP", "sp", "cidade_invalida"),
    ]:
        with pytest.raises(flex_local.LocalFlexInvalidoError) as e:
            await flex_local.salvar(db, cidade=cidade, lote=lote, por=None)
        assert e.value.codigo == codigo


# ---- leitura da origem (formato real do ML) ---------------------------------------


def _assinatura(status: str, cep: str | None, cidade: str | None = None) -> dict:
    a: dict = {"site_id": "MLB", "user_id": 1, "mode": "FLEX", "service_id": 1, "status": status}
    if cep is not None:
        a["origin"] = {
            "id": 1667162710,
            "zip_code": cep,
            "city": {"id": "BR-SP-20", "name": cidade} if cidade else {"id": "BR-SP-20"},
            "address_line": "Rua de Teste 1",
        }
    return a


SB_IN = _assinatura("in", "09750-000", "São Bernardo do Campo")
PIRA_IN = _assinatura("in", "13400-123", "Piracicaba")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corpo", "cep", "cidade", "ausente"),
    [
        # Uma assinatura "in" (o caso das 17 contas em 08/10/2026).
        ([PIRA_IN], "13400123", "Piracicaba", False),
        ([SB_IN], "09750000", "São Bernardo do Campo", False),
        # A origem da assinatura "out" não conta; duas "in" ficam as duas, em pares.
        (
            [_assinatura("out", "13400000", "Piracicaba"), SB_IN],
            "09750000",
            "São Bernardo do Campo",
            False,
        ),
        ([SB_IN, PIRA_IN], "09750000,13400123", "São Bernardo do Campo, Piracicaba", False),
        # Uma "in" sem cidade (ou sem origin) junto de outra de São Bernardo:
        # não se sabe de onde a conta sai — nada passa.
        ([SB_IN, _assinatura("in", "13400-123")], None, None, False),
        ([SB_IN, _assinatura("in", None)], None, None, False),
        # Nenhuma "in" com origin: o formato mudou (o motor fica com a antiga).
        ([_assinatura("in", None)], None, None, True),
        ({"results": [PIRA_IN]}, "13400123", "Piracicaba", False),
    ],
)
async def test_ml_assinatura_traz_a_origem(corpo, cep, cidade, ausente):
    with respx.mock(base_url=ML_API_BASE) as router:
        router.get(ASSINATURA).mock(return_value=httpx.Response(200, json=corpo))
        r = await _ml().ler_assinatura_flex()
    assert (r.ativo, r.status) == (True, "in")
    assert (r.origem_cep, r.origem_cidade, r.origem_ausente) == (cep, cidade, ausente)


def test_assinatura_sem_flex_nao_tem_origem():
    r = flex_api.classificar_assinatura_ml(httpx.Response(200, json=[_assinatura("out", "1", "x")]))
    assert (r.ativo, r.origem_cep, r.origem_cidade) == (False, None, None)


def test_cep_com_digito_estranho_nao_derruba():
    r = flex_api.classificar_assinatura_ml(
        httpx.Response(200, json=[_assinatura("in", "13400-12³", "Piracicaba")])
    )
    assert (r.origem_cep, r.origem_cidade) == ("1340012", "Piracicaba")


# ---- a regra (pura) ---------------------------------------------------------------


def test_bloqueio_da_conta_pela_cidade_do_local():
    sb = flex_motor.ContaFlex(
        "ml", True, "in", origem_cep=ORIGEM_SB, origem_cidade="São Bernardo do Campo"
    )
    pira = flex_motor.ContaFlex(
        "ml", True, "in", origem_cep=ORIGEM_PIRACICABA, origem_cidade="Piracicaba"
    )
    sem = flex_motor.ContaFlex("ml", True, "in")
    assert flex_motor.bloqueio_da_conta("ml", sb) is None
    assert flex_motor.bloqueio_da_conta("ml", pira) == MOTIVO_PIRACICABA
    assert flex_motor.bloqueio_da_conta("ml", sem) == flex_motor.MOTIVO_SEM_ORIGEM
    # Conta sem Flex: o motivo de antes (a origem nem entra).
    assert flex_motor.bloqueio_da_conta("ml", flex_motor.ContaFlex("ml", False, "out")) == (
        "conta sem Flex ativo no ML (status out)"
    )
    # Shopee: a origem não vem na leitura do canal — a trava é só do ML.
    assert (
        flex_motor.bloqueio_da_conta("shopee", flex_motor.ContaFlex("shopee", True, "in")) is None
    )
    # O local passa a ser Piracicaba: inverte.
    flex_local._guardar(flex_local.LocalFlex("Piracicaba", "pi"))
    assert flex_motor.bloqueio_da_conta("ml", pira) is None
    assert flex_motor.bloqueio_da_conta("ml", sb) == (
        "saída do Flex da conta fora de Piracicaba: São Bernardo do Campo (CEP 09750-000)"
    )


def test_textos_claros_da_origem():
    claro = flex_textos.motivo_claro(MOTIVO_PIRACICABA)
    assert claro.startswith(
        "A saída do Flex desta conta no Mercado Livre está em Piracicaba (CEP 13400-123), "
        "não em São Bernardo do Campo."
    )
    assert "Troque o endereço do Flex no painel do Mercado Livre" in claro
    sem_cep = flex_textos.motivo_claro("saída do Flex da conta fora de Piracicaba: Campinas")
    assert sem_cep.startswith("A saída do Flex desta conta no Mercado Livre está em Campinas, não")
    claro = flex_textos.motivo_claro(flex_motor.MOTIVO_SEM_ORIGEM)
    assert claro.startswith("Ainda não deu para ler de onde sai o Flex desta conta")
    # Os textos seguem a cidade do local.
    flex_local._guardar(flex_local.LocalFlex("Piracicaba", "pi"))
    assert "5 peças livres em Piracicaba (dg053.pi)" in flex_textos.motivo_claro(
        "saldo Flex 5 em dg053.pi (liga com 3)"
    )


# ---- o motor ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conta_saindo_de_piracicaba_o_motor_nao_mexe(db, mundo, monkeypatch):
    """Piloto, conta "in" saindo de Piracicaba: o MLB9 (Flex ligado, sem .sp)
    e o MLB77 (sem vínculo) seriam desligados; nada é tocado — nem lido."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    ml.assinatura = PIRACICABA
    resumo = await flex_motor.rodar_motor()
    assert ml.chamadas == []  # nem leitura por anúncio, nem escrita
    assert resumo["contas_fora_da_origem"] == 1
    assert resumo["local"] == "São Bernardo do Campo (.sp)"
    est = await _estados(db)
    assert {e.motivo for e in est.values()} == {MOTIVO_PIRACICABA}
    assert {e.desejado for e in est.values()} == {"inelegivel"}
    assert not any(e.aguardando_aprovacao for e in est.values())
    conta = await _conta(db, mundo["conta_id"])
    assert (conta.flex_ativo, conta.origem_cep, conta.origem_cidade) == (
        True,
        ORIGEM_PIRACICABA,
        "Piracicaba",
    )
    with pytest.raises(flex_motor.FlexRegraError) as e:
        await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=mundo["dono_id"])
    assert e.value.codigo == "conta_fora_da_origem"
    assert "Piracicaba" in e.value.detalhe

    # O Eduardo troca o endereço do Flex para São Bernardo no painel do ML. A
    # rodada do cron ainda usa a leitura de menos de 1 h…
    ml.assinatura = SAO_BERNARDO
    await flex_motor.rodar_motor()
    assert ml.escritas == []
    # …mas o "Sincronizar agora" relê a assinatura na hora.
    resumo = await flex_motor.rodar_motor(origem="manual", reler_contas=True)
    assert resumo["contas_fora_da_origem"] == 0
    assert sorted(ml.escritas) == [("desligar", "MLB77"), ("desligar", "MLB9")]
    conta = await _conta(db, mundo["conta_id"])
    assert (conta.origem_cep, conta.origem_cidade) == (ORIGEM_SB, "São Bernardo do Campo")
    assert (await _estados(db))["MLB1"].aguardando_aprovacao is True

    # E se voltar para Piracicaba, para de mexer de novo.
    ml.assinatura = PIRACICABA
    await _velha(db)
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ml.chamadas == []
    assert not any(e.aguardando_aprovacao for e in (await _estados(db)).values())


@pytest.mark.asyncio
async def test_antes_de_ligar_o_aprovado_a_assinatura_e_relida(db, mundo, monkeypatch):
    """A conta saía de São Bernardo (lida agora há pouco) e o endereço do Flex
    foi trocado para Piracicaba: aprovar o MLB1 relê a assinatura antes de
    ligar — não liga (achado da revisão de 08/10/2026)."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    lidas = ml.assinaturas_lidas
    ml.assinatura = PIRACICABA
    ml.chamadas.clear()
    res = await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=mundo["dono_id"])
    assert ml.assinaturas_lidas == lidas + 1
    assert ("ligar", "MLB1") not in ml.chamadas
    assert res["aplicado"] is False
    est = (await _estados(db))["MLB1"]
    assert (est.desejado, est.motivo) == ("inelegivel", MOTIVO_PIRACICABA)
    assert est.aprovado_em is None  # a aprovação não fica pendurada


@pytest.mark.asyncio
async def test_aprovacao_pendente_faz_a_rodada_reler_a_conta(db, mundo, monkeypatch):
    """Uma aprovação que ainda não ligou (o Bling não confirmou): a rodada do
    cron relê a assinatura da conta dela antes de ligar."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    await db.execute(
        text("UPDATE flex_anuncio_estado SET aprovado_em = now() WHERE external_id = 'MLB1'")
    )
    await db.commit()
    ml.assinatura = PIRACICABA
    lidas = ml.assinaturas_lidas
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ml.assinaturas_lidas == lidas + 1
    assert ml.chamadas == []


@pytest.mark.asyncio
async def test_conta_in_sem_origem_e_perguntada_na_hora(db, mundo, monkeypatch):
    """A linha gravada antes da 0383 (assinatura "in", origem NULL, lida há
    5 min): o motor pergunta de novo já — sem esperar a validade de 1 h."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    db.add(
        FlexConta(
            integration_id=mundo["conta_id"],
            plataforma="ml",
            flex_ativo=True,
            status="in",
            detalhe="assinatura do Flex ativa",
            lido_em=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    await db.commit()
    await flex_motor.rodar_motor()
    assert ml.assinaturas_lidas == 1
    conta = await _conta(db, mundo["conta_id"])
    assert conta.origem_cidade == "São Bernardo do Campo"
    assert ("desligar", "MLB9") in ml.escritas


@pytest.mark.asyncio
async def test_saida_sem_cidade_bloqueia_e_sem_origin_fica_a_antiga(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    await flex_motor.rodar_motor()  # grava São Bernardo
    # Uma saída sem cidade (a resposta veio incompleta): não se sabe — para.
    ml.assinatura = AssinaturaFlex(True, "in", "assinatura do Flex ativa")
    await _velha(db)
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ml.chamadas == []
    assert {e.motivo for e in (await _estados(db)).values()} == {flex_motor.MOTIVO_SEM_ORIGEM}
    # Nenhuma `origin` na resposta (o formato mudou): fica a que se sabia.
    await db.execute(
        text("UPDATE flex_conta SET origem_cep = :c, origem_cidade = 'São Bernardo do Campo'"),
        {"c": ORIGEM_SB},
    )
    await _velha(db)
    ml.assinatura = AssinaturaFlex(True, "in", "assinatura do Flex ativa", origem_ausente=True)
    await flex_motor.rodar_motor()
    conta = await _conta(db, mundo["conta_id"])
    assert conta.origem_cidade == "São Bernardo do Campo"
    assert ml.leituras  # a conta volta a ser lida


@pytest.mark.asyncio
async def test_plataforma_fora_do_ar_a_origem_vale_6h(db, mundo, monkeypatch):
    """Um 5xx na releitura de hora em hora não para a conta; a origem lida há
    mais de 6 h sem confirmação para (revisão de 08/10/2026)."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    await _velha(db)  # 2 h
    ml.assinatura = AssinaturaFlex(None, None, "503 Service Unavailable")
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ml.leituras  # 2 h: continua
    await db.execute(text("UPDATE flex_conta SET origem_lida_em = now() - interval '7 hours'"))
    await _velha(db)
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ml.chamadas == []  # 7 h sem confirmar: para
    conta = await _conta(db, mundo["conta_id"])
    assert (conta.flex_ativo, conta.origem_cidade) == (True, "São Bernardo do Campo")
    assert {e.motivo for e in (await _estados(db)).values()} == {flex_motor.MOTIVO_SEM_ORIGEM}
    # Voltou a responder: volta a mexer.
    ml.assinatura = SAO_BERNARDO
    await flex_motor.rodar_motor()
    assert ml.leituras


@pytest.mark.asyncio
async def test_troca_do_local_no_meio_da_rodada_nao_mistura_lote(db, mundo, monkeypatch):
    """Alguém troca o local para .ci enquanto a rodada lê o ML: a rodada
    termina com o local do começo (.sp) — não desliga anúncio com peça."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    monkeypatch.setattr(flex_local, "_VALIDADE_S", 0.0)
    ml = mundo["ml"]
    ml.estado.update({"MLB1": True, "MLB2": True})
    ler = ml.ler_flex
    trocou = []

    async def ler_trocando(item):
        if not trocou:
            trocou.append(item)
            async with flex_motor.session_scope() as s:
                await flex_local.salvar(s, cidade="São Bernardo do Campo", lote="ci", por=None)
            flex_local.limpar_cache()
            await flex_local.carregar()  # outro job do worker relê o cache global
        return await ler(item)

    monkeypatch.setattr(ml, "ler_flex", ler_trocando)
    resumo = await flex_motor.rodar_motor()
    assert trocou and resumo["local"] == "São Bernardo do Campo (.sp)"
    est = await _estados(db)
    assert est["MLB1"].motivo == "saldo Flex 5 em dg053.sp (liga com 3)"
    assert ("desligar", "MLB1") not in ml.escritas
    assert ("desligar", "MLB2") not in ml.escritas
    # A rodada seguinte já usa o .ci (o dg053.ci tem 100).
    monkeypatch.setattr(ml, "ler_flex", ler)
    resumo = await flex_motor.rodar_motor()
    assert resumo["local"] == "São Bernardo do Campo (.ci)"


@pytest.mark.asyncio
async def test_aprovacao_no_meio_da_rodada_espera_a_releitura(db, mundo, monkeypatch):
    """A aprovação cai depois da conferência das contas, no meio da rodada do
    cron: a rodada NÃO liga (a conta não foi relida agora); a aprovação
    continua valendo para a próxima."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    ler = ml.ler_flex
    marcou = []

    async def ler_aprovando(item):
        if not marcou:
            marcou.append(item)
            async with flex_motor.session_scope() as s:
                await s.execute(
                    text(
                        "UPDATE flex_anuncio_estado SET aprovado_em = now() "
                        "WHERE external_id = 'MLB1'"
                    )
                )
        return await ler(item)

    monkeypatch.setattr(ml, "ler_flex", ler_aprovando)
    lidas = ml.assinaturas_lidas
    ml.chamadas.clear()
    resumo = await flex_motor.rodar_motor()
    # A assinatura estava no cache (< 1 h) e não havia aprovação no começo:
    # a conta NÃO foi relida nesta rodada — a aprovação que chegou no meio
    # dela espera.
    assert ml.assinaturas_lidas == lidas
    assert ("ligar", "MLB1") not in ml.escritas
    assert resumo["ligar_esperando_releitura"] == 1
    monkeypatch.setattr(ml, "ler_flex", ler)
    ml.chamadas.clear()
    await flex_motor.rodar_motor()  # a próxima relê a conta (tem aprovação) e liga
    assert ml.assinaturas_lidas == lidas + 1
    assert ("ligar", "MLB1") in ml.escritas


@pytest.mark.asyncio
async def test_trocar_o_local_muda_a_cidade_e_o_lote(db, mundo, monkeypatch):
    """O local vira Piracicaba / .pi: a conta que sai de Piracicaba é a que o
    motor mexe e o estoque contado é o dg053.pi."""
    ml = mundo["ml"]
    ml.assinatura = PIRACICABA
    db.add(
        Product(
            user_id=mundo["dono_id"],
            sku="dg053.pi",
            name="dg053.pi",
            stock=7,
            situacao="A",
            bling_product_id=777001,
        )
    )
    await db.commit()
    await flex_local.salvar(db, cidade="Piracicaba", lote="pi", por=mundo["dono_id"])
    await db.commit()
    flex_local.limpar_cache()  # o worker relê do banco no começo da rodada
    resumo = await flex_motor.rodar_motor()
    assert resumo["local"] == "Piracicaba (.pi)"
    assert resumo["contas_fora_da_origem"] == 0
    est = await _estados(db)
    assert est["MLB1"].motivo == "saldo Flex 7 em dg053.pi (liga com 3)"
    assert est["MLB1"].desejado == "ligado"
    assert ml.leituras  # a conta de Piracicaba agora é lida
    # O pedido Flex já no lote do local conta como "no lote do Flex".
    assert flex_envio.item_no_sp("dg053.pi") is True
    assert flex_envio.item_no_sp("dg053.sp") is False


@pytest.mark.asyncio
async def test_emergencia_desliga_tambem_na_conta_de_piracicaba(db, mundo, monkeypatch):
    """O botão de emergência é o freio de mão: desliga o Flex de tudo das
    contas liberadas, de onde quer que o motoboy saia."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    ml.assinatura = PIRACICABA
    await flex_motor.rodar_motor()
    assert ml.escritas == []
    resumo = await flex_motor.emergencia(por=mundo["dono_id"])
    # Os da conta bloqueada nunca foram lidos: a emergência desliga todos.
    assert sorted(ml.escritas) == [
        ("desligar", e) for e in ("MLB1", "MLB2", "MLB3", "MLB77", "MLB9")
    ]
    assert "MLB50" not in str(ml.chamadas)  # a outra conta (fora de flex_contas)
    assert resumo["restantes"] == 0


# ---- a tela -----------------------------------------------------------------------


async def _conta_out(client: AsyncClient, conta_id) -> dict:
    r = await client.get("/api/flex/config")
    assert r.status_code == 200, r.text
    return {x["id"]: x for x in r.json()["contas"]}[str(conta_id)]


@pytest.mark.asyncio
async def test_config_mostra_o_local_a_origem_e_o_motivo(
    client: AsyncClient, db: AsyncSession, cena, auth_as: Callable, make_user
):
    db.add(
        FlexConta(
            integration_id=cena["conta_id"],
            plataforma="ml",
            flex_ativo=True,
            status="in",
            detalhe="assinatura do Flex ativa",
            lido_em=datetime.now(UTC),
            origem_cep=ORIGEM_PIRACICABA,
            origem_cidade="Piracicaba",
        )
    )
    await db.commit()
    auth_as(cena["admin"])
    r = await client.get("/api/flex/config")
    corpo = r.json()
    assert corpo["local"]["cidade"] == "São Bernardo do Campo" and corpo["local"]["lote"] == "sp"
    assert corpo["pode_alterar_local"] is True
    assert corpo["cidades_vistas"] == ["Piracicaba"]
    assert corpo["lotes"] == ["ci", "pi", "ra", "sa", "sp"]
    c = await _conta_out(client, cena["conta_id"])
    assert (c["flex_origem_cidade"], c["flex_origem_ok"], c["flex_motivo"]) == (
        "Piracicaba",
        False,
        MOTIVO_PIRACICABA,
    )

    # O admin troca o local para Piracicaba / .pi: a conta passa.
    r = await client.put("/api/flex/local", json={"cidade": "Piracicaba", "lote": "pi"})
    assert r.status_code == 200, r.text
    assert (r.json()["cidade"], r.json()["lote"]) == ("Piracicaba", "pi")
    assert r.json()["atualizado_por"]
    c = await _conta_out(client, cena["conta_id"])
    assert (c["flex_origem_ok"], c["flex_motivo"]) == (True, None)
    assert (await client.get("/api/flex/acesso")).json()["cidade"] == "Piracicaba"

    # Erros de validação e quem pode.
    r = await client.put("/api/flex/local", json={"cidade": "Piracicaba", "lote": "zz"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "lote_invalido"
    r = await client.put("/api/flex/local", json={"cidade": "   ", "lote": "sp"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "cidade_vazia"
    r = await client.put("/api/flex/local", json={"cidade": "Piracicaba, SP", "lote": "sp"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "cidade_invalida"
    # Outro admin trocou enquanto o formulário estava aberto: 409, nada muda.
    r = await client.put(
        "/api/flex/local",
        json={
            "cidade": "Campinas",
            "lote": "sp",
            "cidade_antes": "São Bernardo do Campo",
            "lote_antes": "sp",
        },
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "local_mudou"
    gerente = await make_user(role=UserRole.USER)
    auth_as(gerente)
    r = await client.put("/api/flex/local", json={"cidade": "Campinas", "lote": "sp"})
    assert r.status_code in (403,), r.text
    linha = (await db.execute(select(FlexLocal))).scalar_one()
    await db.refresh(linha)
    assert (linha.cidade, linha.lote) == ("Piracicaba", "pi")


@pytest.mark.asyncio
async def test_deveriam_desligar_so_conta_onde_o_robo_mexe(
    client: AsyncClient, db: AsyncSession, cena, auth_as: Callable
):
    """MLB2 (ligado, a regra quer inelegível) só conta como "deveria desligar"
    quando a conta sai da cidade do local — senão o robô não toca nela."""
    db.add(
        FlexConta(
            integration_id=cena["conta_id"],
            plataforma="ml",
            flex_ativo=True,
            status="in",
            lido_em=datetime.now(UTC),
            origem_cep=ORIGEM_PIRACICABA,
            origem_cidade="Piracicaba",
        )
    )
    await db.commit()
    auth_as(cena["admin"])
    r = await client.get("/api/flex/anuncios?desligar=true")
    assert r.status_code == 200, r.text
    assert (r.json()["itens"], r.json()["resumo"]["desligar"]) == ([], 0)
    await db.execute(
        text("UPDATE flex_conta SET origem_cep = :c, origem_cidade = 'São Bernardo do Campo'"),
        {"c": ORIGEM_SB},
    )
    await db.commit()
    r = await client.get("/api/flex/anuncios?desligar=true")
    assert [i["external_id"] for i in r.json()["itens"]] == ["MLB2"]
    assert r.json()["resumo"]["desligar"] == 1
    assert r.json()["resumo"]["sem_controle"] == 0


@pytest.mark.asyncio
async def test_sem_controle_e_ligados_fora_ao_trocar_o_local(
    client: AsyncClient, db: AsyncSession, cena, auth_as: Callable
):
    """MLB2 tem Flex ligado numa conta que sai de São Bernardo. Trocar o local
    para Piracicaba: a resposta avisa 1 anúncio que fica fora do controle, e o
    resumo passa a mostrar "fora do controle"."""
    db.add(
        FlexConta(
            integration_id=cena["conta_id"],
            plataforma="ml",
            flex_ativo=True,
            status="in",
            lido_em=datetime.now(UTC),
            origem_lida_em=datetime.now(UTC),
            origem_cep=ORIGEM_SB,
            origem_cidade="São Bernardo do Campo",
        )
    )
    await db.commit()
    auth_as(cena["admin"])
    r = await client.get("/api/flex/anuncios")
    assert r.json()["resumo"]["sem_controle"] == 0
    r = await client.put("/api/flex/local", json={"cidade": "Piracicaba", "lote": "pi"})
    assert r.status_code == 200, r.text
    assert r.json()["ligados_fora"] == 1
    r = await client.get("/api/flex/anuncios")
    assert r.json()["resumo"]["sem_controle"] == 1
    assert r.json()["resumo"]["desligar"] == 0


@pytest.mark.asyncio
async def test_estado_do_anuncio_nao_muda_com_a_leitura_da_tela(db, mundo):
    """A tela não chama a plataforma (só o motor): ler /config com a conta
    bloqueada não mexe em nada."""
    ml = mundo["ml"]
    ml.assinatura = PIRACICABA
    await flex_motor.rodar_motor()
    antes = (await db.execute(select(FlexAnuncioEstado.motivo))).scalars().all()
    assert set(antes) == {MOTIVO_PIRACICABA}


# ---- o lote de cada pedido Flex ---------------------------------------------------


@pytest.mark.asyncio
async def test_pedido_que_saiu_fica_com_o_lote_dele(db, mundo):
    """Um pedido Flex saiu de São Bernardo (lote .sp) sem passar pelo .sp e
    espera o acerto. O local muda para Piracicaba / .pi: o acerto continua
    pendente (pelo .sp do pedido) e o pedido NÃO tira peça do .pi."""
    from tests.test_flex_motor import _pedido_flex, _saldo

    db.add(
        Product(
            user_id=mundo["dono_id"],
            sku="dg053.pi",
            name="dg053.pi",
            stock=6,
            situacao="A",
            bling_product_id=777002,
        )
    )
    await db.commit()
    await _pedido_flex(db, 906001, "dg053.pi", 2, situacao="15")  # o Bling baixou o .pi
    linha = await db.get(flex_motor.FlexPedido, 906001)
    assert linha.lote == "sp"  # padrão (o local de quando foi detectado)
    # No .sp: o pedido desconta 2 (saiu de lá sem passar pelo .sp).
    assert (await _saldo(db)).saldo == 3
    assert flex_motor.acerto_pendente("15", False, None, ["dg053.pi"], "sp") is True
    # O local vira Piracicaba / .pi.
    with flex_local.fixar(flex_local.LocalFlex("Piracicaba", "pi")):
        # O pedido é do .sp: não desconta do .pi…
        assert (await _saldo(db, "dg053.pi")).saldo == 6
        # …e o acerto dele continua pendente (era do .sp).
        assert flex_motor.acerto_pendente("15", False, None, ["dg053.pi"], "sp") is True
        # Um pedido do .pi com o item no .pi não tem acerto.
        assert flex_motor.acerto_pendente("15", False, None, ["dg053.pi"], "pi") is False


@pytest.mark.asyncio
async def test_deteccao_grava_o_lote_e_nao_troca_o_do_pedido_antigo(db, mundo):
    from app.models import BlingOrder

    db.add(
        BlingOrder(
            numero="907001",
            bling_id=907001,
            loja="1",
            item_index=0,
            item_codigo="dg053.sp",
            item_quantidade=1,
            situacao="15",
            data=datetime.now(UTC),
        )
    )
    await db.commit()
    lido = flex_envio.EnvioLido(
        bling_id=907001,
        plataforma="ml",
        integration_id=None,
        numero="907001",
        numeroloja="1",
        envio_tipo="self_service",
        envio_flex=True,
    )
    await flex_envio.registrar_pedidos_flex(db, [lido])
    await db.commit()
    linha = await db.get(flex_motor.FlexPedido, 907001)
    assert (linha.lote, linha.no_sp) == ("sp", True)
    # O pedido já SAIU (15) e o local muda para .pi: ele fica com o .sp dele.
    await flex_local.salvar(db, cidade="Piracicaba", lote="pi", por=None)
    await db.commit()
    flex_local.limpar_cache()
    await flex_envio.registrar_pedidos_flex(db, [lido])
    await db.commit()
    db.expire_all()
    linha = await db.get(flex_motor.FlexPedido, 907001)
    assert (linha.lote, linha.no_sp) == ("sp", True)


@pytest.mark.asyncio
async def test_pedido_aberto_acompanha_o_local_e_o_que_saiu_nao(db, mundo):
    """Com o robô do pedido desligado (padrão), a detecção mantém o lote do
    pedido que já saiu e passa o pedido ainda em aberto para o lote do local
    de agora (achado da 3ª conferência, 08/10/2026)."""
    from app.models import BlingOrder

    for bid, sit in ((908001, "6"), (908002, "15")):
        db.add(
            BlingOrder(
                numero=str(bid),
                bling_id=bid,
                loja="1",
                item_index=0,
                item_codigo="dg053.ci",
                item_quantidade=1,
                situacao=sit,
                data=datetime.now(UTC),
            )
        )
    await db.commit()
    lidos = [
        flex_envio.EnvioLido(
            bling_id=b,
            plataforma="ml",
            integration_id=None,
            numero=str(b),
            numeroloja=str(b),
            envio_tipo="self_service",
            envio_flex=True,
        )
        for b in (908001, 908002)
    ]
    await flex_envio.registrar_pedidos_flex(db, lidos)
    await db.commit()
    await flex_local.salvar(db, cidade="Piracicaba", lote="pi", por=None)
    await db.commit()
    flex_local.limpar_cache()
    await flex_envio.registrar_pedidos_flex(db, lidos)
    await db.commit()
    db.expire_all()
    aberto = await db.get(flex_motor.FlexPedido, 908001)
    saiu = await db.get(flex_motor.FlexPedido, 908002)
    assert (aberto.lote, saiu.lote) == ("pi", "sp")


@pytest.mark.asyncio
async def test_in_sem_origin_nao_libera_o_ligar(db, mundo, monkeypatch):
    """A releitura antes de ligar responde "in" sem `origin`: a origem não foi
    confirmada — a aprovação espera."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    ml.assinatura = AssinaturaFlex(True, "in", "assinatura do Flex ativa", origem_ausente=True)
    ml.chamadas.clear()
    res = await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=mundo["dono_id"])
    assert res["aplicado"] is False
    assert ("ligar", "MLB1") not in ml.escritas
    assert (await _estados(db))["MLB1"].aprovado_em is not None  # a aprovação fica


def test_cidade_com_espaco_especial_nao_gruda():
    import asyncio

    class _S:
        def add(self, *_a):
            pass

        async def get(self, *_a, **_k):
            return None

        async def flush(self):
            pass

    novo = asyncio.run(
        flex_local.salvar(_S(), cidade="São Bernardo do\tCampo", lote="sp", por=None)
    )
    assert novo.cidade == "São Bernardo do Campo"


@pytest.mark.asyncio
async def test_config_conta_com_origem_vencida_aparece_parada(
    client: AsyncClient, db: AsyncSession, cena, auth_as: Callable
):
    db.add(
        FlexConta(
            integration_id=cena["conta_id"],
            plataforma="ml",
            flex_ativo=True,
            status="in",
            lido_em=datetime.now(UTC),
            origem_lida_em=datetime.now(UTC) - timedelta(hours=7),
            origem_cep=ORIGEM_SB,
            origem_cidade="São Bernardo do Campo",
        )
    )
    await db.commit()
    auth_as(cena["admin"])
    c = await _conta_out(client, cena["conta_id"])
    assert (c["flex_origem_ok"], c["flex_motivo"]) == (False, flex_motor.MOTIVO_SEM_ORIGEM)
