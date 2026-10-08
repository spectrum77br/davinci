"""Trava de ORIGEM do Flex (Eduardo, 08/10/2026: "o motoboy vai sair de São
Bernardo" — procedimento-flex.md, seção 2).

A leitura de 08/10 mostrou as 17 contas do ML com a assinatura "in" saindo
de Piracicaba (CEP 134xx). O Flex ligado pela peça do .sp (São Bernardo)
venderia para quem mora perto de Piracicaba. O motor guarda a origem da
assinatura (subscriptions/v1 → origin.zip_code / city.name) e só mexe na
conta cuja saída está em `flex_origem_ceps` (padrão 09600-09899).

O que se confere aqui:
  • a faixa de CEP (e o erro de digitação vira o lado seguro);
  • a leitura da origem no formato real da resposta do ML;
  • conta saindo de Piracicaba: nem lê, nem desliga, nem liga, nem aprova —
    e o anúncio mostra o porquê em português claro;
  • trocou o endereço para São Bernardo: na próxima conferência o motor volta
    a mexer;
  • conta "in" sem a origem (lida antes da 0383) é perguntada já, não espera 1 h;
  • a emergência continua desligando tudo, de onde quer que saia;
  • a trava desligada (`flex_origem_ceps` vazio) mexe como antes;
  • a tela (GET /api/flex/config) mostra a origem e o motivo.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FlexConta
from app.services import flex_config, flex_motor, flex_textos
from app.services.marketplaces import flex_api
from app.services.marketplaces.flex_api import AssinaturaFlex
from app.services.marketplaces.ml import ML_API_BASE
from tests.test_flex_api import cena
from tests.test_flex_clientes import ASSINATURA, _ml
from tests.test_flex_motor import (
    ORIGEM_PIRACICABA,
    ORIGEM_SB,
    _estados,
    mundo,
)

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
MOTIVO_PIRACICABA = "saída do Flex da conta fora de São Bernardo: Piracicaba (CEP 13400-123)"


@pytest.fixture(autouse=True)
def _flex_visivel(monkeypatch):
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: True)


async def _conta(db: AsyncSession, iid) -> FlexConta | None:
    db.expire_all()
    return await db.get(FlexConta, iid)


# ---- faixa de CEP ----------------------------------------------------------------


def test_faixas_de_cep():
    sb = ((9600000, 9899999),)
    assert flex_config.faixas_origem("09600-09899") == sb
    assert flex_config.faixas_origem("09600000-09899999") == sb
    assert flex_config.faixas_origem(" 09750-000 ") == ((9750000, 9750000),)  # um CEP só
    assert flex_config.faixas_origem("09600") == ((9600000, 9600999),)  # prefixo
    assert flex_config.faixas_origem("09600-09899, 13400-13432") == (
        (9600000, 9899999),
        (13400000, 13432999),
    )
    # Vazio = trava desligada; lixo = nenhuma faixa válida = ninguém passa.
    assert flex_config.faixas_origem("") is None
    assert flex_config.faixas_origem("  ") is None
    assert flex_config.faixas_origem("são bernardo") == ()
    assert flex_config.faixas_origem("09899-09600") == ()  # fim antes do início


def test_origem_permitida():
    sb = flex_config.faixas_origem("09600-09899")
    assert flex_config.origem_permitida("09750000", sb) is True
    assert flex_config.origem_permitida("09750-000", sb) is True
    assert flex_config.origem_permitida("09600000", sb) is True
    assert flex_config.origem_permitida("09899999", sb) is True
    assert flex_config.origem_permitida("09900000", sb) is False  # Diadema
    assert flex_config.origem_permitida("13400123", sb) is False  # Piracicaba
    assert flex_config.origem_permitida(None, sb) is False
    assert flex_config.origem_permitida("0975", sb) is False  # ilegível
    assert flex_config.origem_permitida("13400123", None) is None  # trava desligada
    assert flex_config.origem_permitida("09750000", ()) is False  # .env com erro


def test_origem_permitida_todas(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "flex_origem_ceps", "09600-09899")
    assert flex_config.origem_permitida_todas("09750000") is True
    assert flex_config.origem_permitida_todas("09750000,13400123") is False
    assert flex_config.origem_permitida_todas(None) is False
    monkeypatch.setattr(get_settings(), "flex_origem_ceps", "")
    assert flex_config.origem_permitida_todas("13400123") is None


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corpo", "cep", "cidade"),
    [
        # Uma assinatura "in" (o caso das 17 contas em 08/10/2026).
        ([_assinatura("in", "13400-123", "Piracicaba")], "13400123", "Piracicaba"),
        (
            [_assinatura("in", "09750000", "São Bernardo do Campo")],
            "09750000",
            "São Bernardo do Campo",
        ),
        # A origem da assinatura "out" não conta; duas "in" ficam as duas.
        (
            [
                _assinatura("out", "13400000", "Piracicaba"),
                _assinatura("in", "09750-000", "São Bernardo do Campo"),
            ],
            "09750000",
            "São Bernardo do Campo",
        ),
        (
            [
                _assinatura("in", "09750-000", "São Bernardo do Campo"),
                _assinatura("in", "13400-123", "Piracicaba"),
            ],
            "09750000,13400123",
            "São Bernardo do Campo, Piracicaba",
        ),
        # Sem origem na resposta: None (a trava trata como "não se sabe").
        ([_assinatura("in", None)], None, None),
        ([_assinatura("in", "13400-123")], "13400123", None),
        ({"results": [_assinatura("in", "13400-123", "Piracicaba")]}, "13400123", "Piracicaba"),
    ],
)
async def test_ml_assinatura_traz_a_origem(corpo, cep, cidade):
    with respx.mock(base_url=ML_API_BASE) as router:
        router.get(ASSINATURA).mock(return_value=httpx.Response(200, json=corpo))
        r = await _ml().ler_assinatura_flex()
    assert (r.ativo, r.status) == (True, "in")
    assert (r.origem_cep, r.origem_cidade) == (cep, cidade)


def test_assinatura_sem_flex_nao_tem_origem():
    r = flex_api.classificar_assinatura_ml(
        httpx.Response(200, json=[_assinatura("out", "13400-123", "Piracicaba")])
    )
    assert (r.ativo, r.origem_cep, r.origem_cidade) == (False, None, None)


# ---- a regra (pura) ---------------------------------------------------------------


def test_bloqueio_da_conta_pela_origem(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "flex_origem_ceps", "09600-09899")
    sb = flex_motor.ContaFlex("ml", True, "in", origem_cep=ORIGEM_SB)
    pira = flex_motor.ContaFlex(
        "ml", True, "in", origem_cep=ORIGEM_PIRACICABA, origem_cidade="Piracicaba"
    )
    sem = flex_motor.ContaFlex("ml", True, "in")
    duas = flex_motor.ContaFlex(
        "ml",
        True,
        "in",
        origem_cep=f"{ORIGEM_SB},{ORIGEM_PIRACICABA}",
        origem_cidade="São Bernardo do Campo, Piracicaba",
    )
    assert flex_motor.bloqueio_da_conta("ml", sb) is None
    assert flex_motor.bloqueio_da_conta("ml", pira) == MOTIVO_PIRACICABA
    assert flex_motor.bloqueio_da_conta("ml", sem) == flex_motor.MOTIVO_SEM_ORIGEM
    # Uma das origens fora já bloqueia (o comprador perto de Piracicaba vê o Flex).
    assert "(CEP 13400-123)" in flex_motor.bloqueio_da_conta("ml", duas)
    # Conta sem Flex continua com o motivo de antes (a origem nem entra).
    assert flex_motor.bloqueio_da_conta("ml", flex_motor.ContaFlex("ml", False, "out")) == (
        "conta sem Flex ativo no ML (status out)"
    )
    # Shopee: a origem não vem na leitura do canal — a trava é só do ML.
    assert (
        flex_motor.bloqueio_da_conta("shopee", flex_motor.ContaFlex("shopee", True, "in")) is None
    )
    # Trava desligada: como antes.
    monkeypatch.setattr(get_settings(), "flex_origem_ceps", "")
    assert flex_motor.bloqueio_da_conta("ml", pira) is None
    assert flex_motor.bloqueio_da_conta("ml", sem) is None
    # .env com erro de digitação: ninguém passa (o lado seguro).
    monkeypatch.setattr(get_settings(), "flex_origem_ceps", "sao bernardo")
    assert flex_motor.bloqueio_da_conta("ml", sb) is not None


def test_textos_claros_da_origem():
    claro = flex_textos.motivo_claro(MOTIVO_PIRACICABA)
    assert claro.startswith(
        "A saída do Flex desta conta no Mercado Livre está em Piracicaba (CEP 13400-123), "
        "não em São Bernardo."
    )
    assert "Troque o endereço do Flex no painel do Mercado Livre" in claro
    claro = flex_textos.motivo_claro(flex_motor.MOTIVO_SEM_ORIGEM)
    assert claro.startswith("Ainda não deu para ler de onde sai o Flex desta conta")


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
    # Aprovar ligar numa conta assim é recusado (não fica pendurado até
    # alguém trocar o endereço).
    with pytest.raises(flex_motor.FlexRegraError) as e:
        await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=mundo["dono_id"])
    assert e.value.codigo == "conta_fora_da_origem"
    assert "Piracicaba" in e.value.detalhe

    # O Eduardo troca o endereço do Flex para São Bernardo no painel do ML:
    # na próxima conferência (validade de 1 h) o motor volta a mexer.
    ml.assinatura = SAO_BERNARDO
    await flex_motor.rodar_motor()
    assert ml.escritas == []  # ainda vale a leitura de menos de 1 h
    await db.execute(text("UPDATE flex_conta SET lido_em = now() - interval '2 hours'"))
    await db.commit()
    resumo = await flex_motor.rodar_motor()
    assert resumo["contas_fora_da_origem"] == 0
    assert sorted(ml.escritas) == [("desligar", "MLB77"), ("desligar", "MLB9")]
    conta = await _conta(db, mundo["conta_id"])
    assert (conta.origem_cep, conta.origem_cidade) == (ORIGEM_SB, "São Bernardo do Campo")
    est = await _estados(db)
    assert est["MLB1"].aguardando_aprovacao is True  # ligar volta a pedir aprovação

    # E se voltar para Piracicaba, para de mexer de novo.
    ml.assinatura = PIRACICABA
    await db.execute(text("UPDATE flex_conta SET lido_em = now() - interval '2 hours'"))
    await db.commit()
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ml.chamadas == []
    est = await _estados(db)
    assert not any(e.aguardando_aprovacao for e in est.values())


@pytest.mark.asyncio
async def test_conta_in_sem_origem_e_perguntada_na_hora(db, mundo, monkeypatch):
    """A linha gravada antes da 0383 (assinatura "in", origem NULL, lida há
    5 min): o motor pergunta de novo já — sem esperar a validade de 1 h."""
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    ml = mundo["ml"]
    agora = datetime.now(UTC)
    db.add(
        FlexConta(
            integration_id=mundo["conta_id"],
            plataforma="ml",
            flex_ativo=True,
            status="in",
            detalhe="assinatura do Flex ativa",
            lido_em=agora - timedelta(minutes=5),
        )
    )
    await db.commit()
    await flex_motor.rodar_motor()
    assert ml.assinaturas_lidas == 1
    conta = await _conta(db, mundo["conta_id"])
    assert conta.origem_cep == ORIGEM_SB
    assert ("desligar", "MLB9") in ml.escritas
    # A resposta veio sem origem: fica bloqueada e pergunta de novo na próxima.
    ml.assinatura = AssinaturaFlex(True, "in", "assinatura do Flex ativa")
    await db.execute(text("UPDATE flex_conta SET lido_em = now() - interval '2 hours'"))
    await db.commit()
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ml.chamadas == []
    est = await _estados(db)
    assert {e.motivo for e in est.values()} == {flex_motor.MOTIVO_SEM_ORIGEM}
    await flex_motor.rodar_motor()
    assert ml.assinaturas_lidas == 3  # sem origem: não usa o cache de 1 h


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
    # Os da conta bloqueada nunca foram lidos: a emergência desliga todos
    # (o que não se sabe se está ligado também — test_emergencia_desliga_o_que_nunca_foi_lido).
    assert sorted(ml.escritas) == [
        ("desligar", e) for e in ("MLB1", "MLB2", "MLB3", "MLB77", "MLB9")
    ]
    assert "MLB50" not in str(ml.chamadas)  # a outra conta (fora de flex_contas)
    assert resumo["restantes"] == 0


@pytest.mark.asyncio
async def test_trava_desligada_mexe_como_antes(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    monkeypatch.setattr(mundo["cfg"], "flex_origem_ceps", "")
    ml = mundo["ml"]
    ml.assinatura = PIRACICABA
    await flex_motor.rodar_motor()
    assert sorted(ml.escritas) == [("desligar", "MLB77"), ("desligar", "MLB9")]


@pytest.mark.asyncio
async def test_observar_com_conta_de_piracicaba_nao_le_nem_simula(db, mundo):
    """Em observar (como está em produção): a conta de Piracicaba fica com o
    motivo da origem e nenhuma leitura por anúncio é gasta."""
    ml = mundo["ml"]
    ml.assinatura = PIRACICABA
    resumo = await flex_motor.rodar_motor()
    assert ml.chamadas == [] and resumo["contas_fora_da_origem"] == 1
    est = await _estados(db)
    assert {e.motivo for e in est.values()} == {MOTIVO_PIRACICABA}


# ---- a tela -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_mostra_a_origem_e_o_motivo(
    client: AsyncClient, db: AsyncSession, cena, auth_as: Callable, monkeypatch
):
    monkeypatch.setattr(cena["cfg"], "flex_origem_ceps", "09600-09899")
    agora = datetime.now(UTC)
    db.add(
        FlexConta(
            integration_id=cena["conta_id"],
            plataforma="ml",
            flex_ativo=True,
            status="in",
            detalhe="assinatura do Flex ativa",
            lido_em=agora,
            origem_cep=ORIGEM_PIRACICABA,
            origem_cidade="Piracicaba",
        )
    )
    await db.commit()
    auth_as(cena["admin"])
    r = await client.get("/api/flex/config")
    assert r.status_code == 200, r.text
    c = {x["id"]: x for x in r.json()["contas"]}[str(cena["conta_id"])]
    assert (
        c["flex_ativo"],
        c["flex_origem_cep"],
        c["flex_origem_cidade"],
        c["flex_origem_ok"],
    ) == (True, ORIGEM_PIRACICABA, "Piracicaba", False)
    assert c["flex_motivo"] == MOTIVO_PIRACICABA

    await db.execute(
        text("UPDATE flex_conta SET origem_cep = :c, origem_cidade = 'São Bernardo do Campo'"),
        {"c": ORIGEM_SB},
    )
    await db.commit()
    r = await client.get("/api/flex/config")
    c = {x["id"]: x for x in r.json()["contas"]}[str(cena["conta_id"])]
    assert (c["flex_origem_ok"], c["flex_motivo"]) == (True, None)

    # Trava desligada: a tela não julga a origem.
    monkeypatch.setattr(cena["cfg"], "flex_origem_ceps", "")
    r = await client.get("/api/flex/config")
    c = {x["id"]: x for x in r.json()["contas"]}[str(cena["conta_id"])]
    assert (c["flex_origem_ok"], c["flex_motivo"]) == (None, None)
