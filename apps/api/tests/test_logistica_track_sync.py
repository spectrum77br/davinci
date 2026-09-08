"""Registro automático no 17track + pull da localização dos Correios (Logística).

Eduardo 04/09: "rastreio e localização de correios não está atualizando... isso é
em logística, sempre que mudar precisa atualizar em tempo real também". A causa
era não haver NINGUÉM registrando o rastreio de ENVIO no 17track — sem registro
o 17track não busca nos Correios e nunca empurra evento, então a coluna
Localização ficava eternamente com o proxy do marketplace.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica
from app.redis_client import redis
from app.services import logistica_track, logistica_track_sync


@pytest_asyncio.fixture(autouse=True)
async def _limpa_redis():
    """Quarentena, trava e aviso de saldo vivem no Redis (TTL de horas) — sem
    limpar, um teste contamina o seguinte e a suíte fica dependente da ordem."""

    async def _purga():
        keys = [k async for k in redis.scan_iter("17track:*")]
        if keys:
            await redis.delete(*keys)

    await _purga()
    yield
    await _purga()


@pytest.fixture
def fake_17track(monkeypatch):
    """Substitui as duas chamadas de rede do 17track e grava o que foi pedido."""
    chamadas: dict[str, list] = {"register": [], "fetch": []}
    estado = {"ok": None, "sem_quota": False, "eventos": []}

    async def _register(numbers):
        chamadas["register"].append(list(numbers))
        ok = estado["ok"]
        return {
            "ok": list(numbers) if ok is None else list(ok),
            "sem_quota": estado["sem_quota"],
        }

    async def _fetch(numbers):
        chamadas["fetch"].append(list(numbers))
        return list(estado["eventos"])

    monkeypatch.setattr(logistica_track, "register", _register)
    monkeypatch.setattr(logistica_track, "fetch", _fetch)
    return chamadas, estado


@pytest.mark.asyncio
async def test_registra_rastreio_novo_e_puxa_localizacao(db: AsyncSession, fake_17track):
    chamadas, estado = fake_17track
    row = Logistica(
        pedido_bling="291809",
        plataforma="Mercado Livre",
        data=date.today(),
        rastreio="AD828496989BR",
        localizacao="Aguardando NF → Rio de Janeiro/RJ",
        status_bling="Problemas",
    )
    db.add(row)
    await db.commit()
    estado["eventos"] = [("AD828496989BR", "Rio de Janeiro/RJ — Objeto em trânsito")]

    out = await logistica_track_sync.run(db, pedidos=["291809"])

    assert chamadas["register"] == [["AD828496989BR"]]
    assert out["registrados"] == 1
    assert out["atualizados"] == 1
    await db.refresh(row)
    assert row.rastreio_17track == "AD828496989BR"
    assert row.rastreio_17track_at is not None
    assert row.localizacao == "Rio de Janeiro/RJ — Objeto em trânsito"
    assert row.localizacao_at is not None


@pytest.mark.asyncio
async def test_nao_registra_de_novo_o_mesmo_numero(db: AsyncSession, fake_17track):
    chamadas, _ = fake_17track
    row = Logistica(
        pedido_bling="291810",
        data=date.today(),
        rastreio="AA111111111BR",
        rastreio_17track="AA111111111BR",
    )
    db.add(row)
    await db.commit()

    await logistica_track_sync.run(db, pedidos=["291810"])
    assert chamadas["register"] == []  # já registrado: não gasta quota de novo
    assert chamadas["fetch"] == [["AA111111111BR"]]


@pytest.mark.asyncio
async def test_rastreio_trocado_registra_o_novo(db: AsyncSession, fake_17track):
    chamadas, _ = fake_17track
    row = Logistica(
        pedido_bling="291811",
        data=date.today(),
        rastreio="BB222222222BR",
        rastreio_17track="AA111111111BR",  # o marketplace trocou o código
    )
    db.add(row)
    await db.commit()

    await logistica_track_sync.run(db, pedidos=["291811"])
    assert chamadas["register"] == [["BB222222222BR"]]
    await db.refresh(row)
    assert row.rastreio_17track == "BB222222222BR"


@pytest.mark.asyncio
async def test_sem_quota_nao_marca_como_registrado(db: AsyncSession, fake_17track):
    _, estado = fake_17track
    estado["ok"] = []
    estado["sem_quota"] = True
    row = Logistica(pedido_bling="291812", data=date.today(), rastreio="CC333333333BR")
    db.add(row)
    await db.commit()

    out = await logistica_track_sync.run(db, pedidos=["291812"])
    assert out["sem_quota"] is True
    assert out["registrados"] == 0
    await db.refresh(row)
    # Sem marca: a próxima rodada tenta de novo assim que a quota voltar.
    assert row.rastreio_17track is None


@pytest.mark.asyncio
async def test_varredura_so_ml_em_andamento_com_correios(db: AsyncSession, fake_17track):
    """Eduardo 07/09: cada cadastro custa crédito, e 127 dos 191 primeiros foram
    com pedido já entregue. A varredura passa a pegar só Mercado Livre em
    "Em andamento" — o resto não gasta saldo."""
    chamadas, _ = fake_17track
    ml, and_ = "Mercado Livre", "Em andamento"
    db.add_all(
        [
            # Não é Correios (não termina em BR).
            Logistica(pedido_bling="a1", plataforma=ml, status_bling=and_,
                      data=date.today(), rastreio="42314700000000"),
            # ML, mas já entregue: não gasta crédito.
            Logistica(pedido_bling="a2", plataforma=ml, status_bling="Entregue",
                      data=date.today(), rastreio="DD444444444BR"),
            # Shopee em andamento: outra plataforma, fora.
            Logistica(pedido_bling="a3", plataforma="Shopee", status_bling=and_,
                      data=date.today(), rastreio="EE555555555BR"),
            # ML em andamento, mas fora da janela.
            Logistica(pedido_bling="a4", plataforma=ml, status_bling=and_,
                      data=date.today() - timedelta(days=200), rastreio="GG777777777BR"),
            # Este vale.
            Logistica(pedido_bling="a5", plataforma=ml, status_bling=and_,
                      data=date.today(), rastreio="FF666666666BR"),
        ]
    )
    await db.commit()

    await logistica_track_sync.run(db)
    assert chamadas["register"] == [["FF666666666BR"]]


@pytest.mark.asyncio
async def test_erro_do_17track_nao_derruba_o_job(db: AsyncSession, monkeypatch):
    async def _boom(numbers):
        raise RuntimeError("17track fora do ar")

    monkeypatch.setattr(logistica_track, "register", _boom)
    monkeypatch.setattr(logistica_track, "fetch", _boom)
    db.add(Logistica(pedido_bling="291813", data=date.today(), rastreio="GG777777777BR"))
    await db.commit()

    out = await logistica_track_sync.run(db, pedidos=["291813"])
    assert out["registrados"] == 0
    assert out["atualizados"] == 0


def test_register_separa_ok_ja_registrado_e_sem_quota():
    # O /register devolve os problemas dentro de `rejected`: "já registrado" é
    # sucesso pra nós; "quota" tem que aparecer como sem_quota pra ninguém
    # marcar o número e o operador ser avisado.
    data = {
        "accepted": [{"number": "AA111111111BR"}],
        "rejected": [
            {"number": "BB222222222BR", "error": {"code": logistica_track.ERRO_JA_REGISTRADO}},
            {"number": "CC333333333BR", "error": {"code": logistica_track.ERRO_SEM_QUOTA}},
        ],
    }
    assert logistica_track._erro_code(data["rejected"][1]) == logistica_track.ERRO_SEM_QUOTA
    assert logistica_track._erro_code({"number": "x"}) is None


# ---- guarda da localização: proxy do marketplace × físico dos Correios ----


@pytest.mark.asyncio
async def test_proxy_do_ml_atualiza_enquanto_nao_ha_evento_dos_correios(
    db: AsyncSession, monkeypatch
):
    """O proxy do ML tem que continuar atualizando até o 17track dar o físico.

    A guarda antiga (`... and row.localizacao`) bloqueava o próprio ML: a
    primeira frase que ele escrevia na coluna passava a valer como "físico" e
    congelava. Foi o que travou o 291809 em "Aguardando NF → Rio de Janeiro/RJ ·
    previsão 25/08" por 10 dias. Agora a prova de físico é `localizacao_at`.
    """
    from app.services import logistica_meli

    async def _enr(client, order_id):
        return {
            "meli_status": {"order_status": "paid", "ship_status": "shipped"},
            "rastreio": "AD828496989BR",
            "localizacao": "Em trânsito → Rio de Janeiro/RJ",
            "datas": {},
        }

    monkeypatch.setattr(logistica_meli, "build_enrichment", _enr)
    monkeypatch.setattr(
        logistica_meli, "_ml_integration_for_conta", lambda *a, **k: _async_none()
    )

    row = Logistica(
        pedido_bling="291809",
        plataforma="Mercado Livre",
        conta="kia",
        pedido_marketplace="2000014649358745",
        data=date.today(),
        rastreio="AD828496989BR",
        localizacao="Aguardando NF → Rio de Janeiro/RJ · previsão 25/08",
    )
    db.add(row)
    await db.commit()

    await logistica_meli.enrich_row(db, row, client_cache={"kia": object()})
    assert row.localizacao == "Em trânsito → Rio de Janeiro/RJ"

    # Depois que o 17track carimbou o físico, o proxy do ML não sobrescreve mais.
    row.localizacao = "Rio de Janeiro/RJ — Objeto saiu para entrega"
    row.localizacao_at = datetime.now(UTC)
    await logistica_meli.enrich_row(db, row, client_cache={"kia": object()})
    assert row.localizacao == "Rio de Janeiro/RJ — Objeto saiu para entrega"
    await db.commit()


async def _async_none():
    return None


@pytest.mark.asyncio
async def test_entregue_fica_fora_da_varredura_mas_busca_pontual_pega(
    db: AsyncSession, fake_17track
):
    """Pedido entregue não gasta saldo na varredura (nem o de ontem), mas se o
    operador for atrás daquele pedido específico, o sistema responde."""
    chamadas, _ = fake_17track
    db.add(
        Logistica(
            pedido_bling="b1",
            plataforma="Mercado Livre",
            data=date.today() - timedelta(days=1),
            rastreio="HH888888888BR",
            status_bling="Entregue",
            meli_status={"ship_status": "delivered"},
        )
    )
    await db.commit()

    await logistica_track_sync.run(db)
    assert chamadas["register"] == []

    await logistica_track_sync.run(db, pedidos=["b1"])
    assert chamadas["register"] == [["HH888888888BR"]]


@pytest.mark.asyncio
async def test_numero_recusado_entra_em_quarentena(db: AsyncSession, fake_17track):
    """Recusa que não é falta de saldo (formato/transportadora) não pode voltar
    à fila a cada 15 min — senão `pendentes` nunca converge."""
    chamadas, estado = fake_17track
    estado["ok"] = []  # o 17track recusou, e não foi por saldo
    db.add(Logistica(pedido_bling="c1", plataforma="Mercado Livre", status_bling="Em andamento",
                     data=date.today(), rastreio="JJ000000000BR"))
    await db.commit()

    await logistica_track_sync.run(db)
    assert chamadas["register"] == [["JJ000000000BR"]]

    chamadas["register"].clear()
    await logistica_track_sync.run(db)
    assert chamadas["register"] == []  # em quarentena, não reenvia


@pytest.mark.asyncio
async def test_sem_quota_marca_aviso_para_a_tela(db: AsyncSession, fake_17track):
    _, estado = fake_17track
    estado["ok"] = []
    estado["sem_quota"] = True
    db.add(Logistica(pedido_bling="d1", plataforma="Mercado Livre", status_bling="Em andamento",
                     data=date.today(), rastreio="KK111111111BR"))
    await db.commit()

    await logistica_track_sync.run(db)
    assert await logistica_track_sync.sem_quota_desde()

    # Rodada seguinte com saldo apaga o aviso.
    estado["sem_quota"] = False
    estado["ok"] = None
    await logistica_track_sync.run(db, pedidos=["d1"])
    assert await logistica_track_sync.sem_quota_desde() is None


@pytest.mark.asyncio
async def test_divergencia_usa_a_regra_da_plataforma_da_linha(db: AsyncSession, fake_17track):
    """Linha Shopee não pode receber a divergência do MERCADO LIVRE.

    A regra do ML lê `ship_status`, chave que só o ML tem — rodá-la numa linha
    Shopee gera alarme falso ("Mercado Livre: COMPLETED") num pedido normal e
    apaga a divergência correta que o ingest da Shopee calculou.
    """
    _, estado = fake_17track
    row = Logistica(
        pedido_bling="e1",
        plataforma="Shopee",
        data=date.today(),
        rastreio="AP344823352BR",
        rastreio_17track="AP344823352BR",
        meli_status={"order_status": "COMPLETED", "logistics_status": "LOGISTICS_DELIVERY_DONE"},
        status_bling="Entregue",
    )
    db.add(row)
    await db.commit()
    estado["eventos"] = [("AP344823352BR", "Sao Paulo/SP — Objeto entregue ao destinatário")]

    await logistica_track_sync.run(db, pedidos=["e1"])
    await db.refresh(row)
    assert row.localizacao == "Sao Paulo/SP — Objeto entregue ao destinatário"
    assert row.divergencia is None  # pedido Shopee entregue e concluído: sem divergência


def test_divergencia_por_plataforma_despacha_certo():
    from app.services import logistica_rules

    shopee = {"order_status": "COMPLETED", "logistics_status": "LOGISTICS_DELIVERY_DONE"}
    loc = "Sao Paulo/SP — Objeto entregue ao destinatário"
    assert logistica_rules.detectar_divergencia_por_plataforma("Shopee", shopee, loc) is None
    # A regra do ML na MESMA entrada inventaria um alarme — é o bug que isto evita.
    assert logistica_rules.detectar_divergencia(shopee, loc) is not None
    # Plataforma que o sistema não conhece não inventa divergência.
    assert logistica_rules.detectar_divergencia_por_plataforma("Magalu", shopee, loc) is None


# ---- reconsulta forçada (07:00 / 16:30) ---------------------------------------


@pytest.fixture
def fake_forcar(monkeypatch):
    """Substitui as chamadas de rede da reconsulta forçada e registra o que foi
    pedido. `info` = gettrackinfo por número; `estado` = gettracklist por
    número; `retomar_ok`/`reregistrar_ok` = subconjuntos que "funcionam"."""
    chamadas: dict[str, list] = {
        "parar_e_retomar": [],
        "retomar_parados": [],
        "reregistrar": [],
        "fetch": [],
    }
    estado: dict = {
        "info": {},
        "desconhecidos": [],
        "estado": {},
        "retomar_ok": None,
        "ja_retomados": [],
        "parados": [],
        "reregistrar_ok": None,
        "apagados_sem_registro": [],
        "sem_quota": False,
        "quota": 5000,
        "eventos": [],
        "erro": None,
    }

    async def _detalhado(numbers):
        if estado["erro"]:
            raise logistica_track.Track17Error(estado["erro"])
        return {
            "info": {
                n: {"localizacao": None, **estado["info"][n]}
                for n in numbers
                if n in estado["info"]
            },
            "desconhecidos": [n for n in numbers if n in estado["desconhecidos"]],
        }

    async def _estado(numbers):
        return {n: estado["estado"][n] for n in numbers if n in estado["estado"]}

    def _res_retomar(numbers):
        ok = (
            numbers
            if estado["retomar_ok"] is None
            else [n for n in numbers if n in estado["retomar_ok"]]
        )
        return {
            "retomados": sorted(ok),
            "ja_retomados": [n for n in numbers if n in estado["ja_retomados"]],
            "nao_registrados": [],
            "parados": [n for n in numbers if n in estado["parados"]],
        }

    async def _parar_e_retomar(numbers):
        chamadas["parar_e_retomar"].append(sorted(numbers))
        return _res_retomar(sorted(numbers))

    async def _retomar_parados(numbers):
        chamadas["retomar_parados"].append(sorted(numbers))
        return _res_retomar(sorted(numbers))

    async def _reregistrar(numbers):
        chamadas["reregistrar"].append(sorted(numbers))
        if estado["sem_quota"]:
            return {
                "ok": [],
                "apagados_sem_registro": list(estado["apagados_sem_registro"]),
                "sem_quota": True,
            }
        ok = (
            numbers
            if estado["reregistrar_ok"] is None
            else [n for n in numbers if n in estado["reregistrar_ok"]]
        )
        return {
            "ok": sorted(ok),
            "apagados_sem_registro": list(estado["apagados_sem_registro"]),
            "sem_quota": False,
        }

    async def _quota():
        return estado["quota"]

    async def _fetch(numbers):
        chamadas["fetch"].append(sorted(numbers))
        return list(estado["eventos"])

    monkeypatch.setattr(logistica_track, "fetch_detalhado", _detalhado)
    monkeypatch.setattr(logistica_track, "estado_numeros", _estado)
    monkeypatch.setattr(logistica_track, "parar_e_retomar", _parar_e_retomar)
    monkeypatch.setattr(logistica_track, "retomar_parados", _retomar_parados)
    monkeypatch.setattr(logistica_track, "reregistrar", _reregistrar)
    monkeypatch.setattr(logistica_track, "quota_restante", _quota)
    monkeypatch.setattr(logistica_track, "fetch", _fetch)
    monkeypatch.setattr(logistica_track_sync, "ESPERA_APOS_FORCAR_S", 0)
    return chamadas, estado


def _linha_ml(pedido: str, rastreio: str, **extra) -> Logistica:
    return Logistica(
        pedido_bling=pedido,
        plataforma="Mercado Livre",
        status_bling="Em andamento",
        data=date.today(),
        rastreio=rastreio,
        rastreio_17track=rastreio,
        **extra,
    )


VELHO = datetime.now(UTC) - timedelta(hours=8)
RASTREANDO = {"tracking_status": "Tracking", "is_retracked": False}
RASTREANDO_JA_RETOMADO = {"tracking_status": "Tracking", "is_retracked": True}
PARADO = {"tracking_status": "Stopped", "is_retracked": False}
PARADO_JA_RETOMADO = {"tracking_status": "Stopped", "is_retracked": True}


async def _linha(db: AsyncSession, pedido: str) -> Logistica:
    return (
        await db.execute(select(Logistica).where(Logistica.pedido_bling == pedido))
    ).scalar_one()


@pytest.mark.asyncio
async def test_forcar_gratis_primeiro_e_pago_depois(db: AsyncSession, fake_forcar):
    """Eduardo 08/09 (294036): o 17track tinha consultado às 03:36 e os Correios
    já mostravam movimento das 06:54. Quem está velho é forçado: parar+retomar
    (grátis) pra quem nunca retomou, apagar+registrar (1 crédito) pro resto."""
    chamadas, estado = fake_forcar
    db.add_all([_linha_ml("p1", "AA111111111BR"), _linha_ml("p2", "BB222222222BR")])
    await db.commit()
    estado["info"] = {
        "AA111111111BR": {"sync_at": VELHO, "status": "InTransit"},
        "BB222222222BR": {"sync_at": VELHO, "status": "InTransit"},
    }
    estado["estado"] = {"AA111111111BR": RASTREANDO, "BB222222222BR": RASTREANDO_JA_RETOMADO}
    estado["eventos"] = [("AA111111111BR", "Passo Fundo/RS — Objeto em transferência")]

    out = await logistica_track_sync.forcar_reconsulta(db)

    assert chamadas["parar_e_retomar"] == [["AA111111111BR"]]
    assert chamadas["reregistrar"] == [["BB222222222BR"]]
    assert chamadas["fetch"] == [["AA111111111BR", "BB222222222BR"]]
    assert out["gratis"] == 1 and out["pagos"] == 1 and out["atualizados"] == 1
    p1 = await _linha(db, "p1")
    assert p1.localizacao == "Passo Fundo/RS — Objeto em transferência"
    assert p1.rastreio_17track_at is not None
    assert await logistica_track_sync._gasto_hoje() == 1


@pytest.mark.asyncio
async def test_forcar_pula_fresco_entregue_e_nao_registrado(db: AsyncSession, fake_forcar):
    """Não gasta crédito à toa: consultado há pouco, já entregue/expirado/
    devolvido e número que o 17track nem conhece ficam de fora — este último
    é desregistrado localmente pra o sync de 15 min registrar de novo."""
    chamadas, estado = fake_forcar
    db.add_all(
        [
            _linha_ml("f1", "AA111111111BR"),  # consultado há 1h: fresco
            _linha_ml("f2", "BB222222222BR"),  # entregue
            _linha_ml("f3", "CC333333333BR"),  # devolvido ao remetente
            _linha_ml("f4", "DD444444444BR"),  # 17track não conhece
            _linha_ml("f5", "EE555555555BR"),  # velho e em trânsito: este vale
            # Rastreio trocou e o novo ainda não foi registrado: fora.
            Logistica(
                pedido_bling="f6",
                plataforma="Mercado Livre",
                status_bling="Em andamento",
                data=date.today(),
                rastreio="FF666666666BR",
                rastreio_17track="XX000000000BR",
            ),
        ]
    )
    await db.commit()
    estado["info"] = {
        "AA111111111BR": {"sync_at": datetime.now(UTC) - timedelta(hours=1), "status": "InTransit"},
        "BB222222222BR": {"sync_at": VELHO, "status": "Delivered"},
        "CC333333333BR": {
            "sync_at": VELHO,
            "status": "Exception",
            "sub_status": "Exception_Returned",
        },
        "EE555555555BR": {"sync_at": VELHO, "status": "InTransit"},
    }
    estado["desconhecidos"] = ["DD444444444BR"]
    estado["estado"] = dict.fromkeys(
        ("AA111111111BR", "BB222222222BR", "CC333333333BR", "EE555555555BR"), RASTREANDO
    )

    out = await logistica_track_sync.forcar_reconsulta(db)

    assert chamadas["parar_e_retomar"] == [["EE555555555BR"]]
    assert chamadas["reregistrar"] == []
    assert out["alvo"] == 5 and out["frescos"] == 1 and out["encerrados"] == 2
    assert out["gratis"] == 1 and out["pagos"] == 0 and out["desregistrados"] == 1
    assert (await _linha(db, "f4")).rastreio_17track is None


@pytest.mark.asyncio
async def test_forcar_reativa_numero_parado_mesmo_fresco(db: AsyncSession, fake_forcar):
    """Número que ficou PARADO no 17track (rodada anterior interrompida, ou o
    próprio 17track parou após 30 dias) entra sempre: retrack direto se nunca
    retomou, apagar+registrar se já retomou. Senão fica sem rastreio pra sempre."""
    chamadas, estado = fake_forcar
    db.add_all([_linha_ml("s1", "AA111111111BR"), _linha_ml("s2", "BB222222222BR")])
    await db.commit()
    agora = datetime.now(UTC)
    estado["info"] = {
        "AA111111111BR": {"sync_at": agora, "status": "InTransit"},
        "BB222222222BR": {"sync_at": agora, "status": "InTransit"},
    }
    estado["estado"] = {"AA111111111BR": PARADO, "BB222222222BR": PARADO_JA_RETOMADO}

    out = await logistica_track_sync.forcar_reconsulta(db)

    assert chamadas["retomar_parados"] == [["AA111111111BR"]]
    assert chamadas["parar_e_retomar"] == []
    assert chamadas["reregistrar"] == [["BB222222222BR"]]
    assert out["gratis"] == 1 and out["pagos"] == 1 and out["frescos"] == 0


@pytest.mark.asyncio
async def test_forcar_retomar_recusado_vira_pago_e_parado_fica_pendente(
    db: AsyncSession, fake_forcar
):
    """`retrack` recusado com "só uma vez" (a lista não sabia) cai no caminho
    pago; parou-e-não-retomou (rede) fica pendente pra próxima rodada."""
    chamadas, estado = fake_forcar
    db.add_all(
        [
            _linha_ml("r1", "AA111111111BR"),
            _linha_ml("r2", "BB222222222BR"),
            _linha_ml("r3", "CC333333333BR"),
        ]
    )
    await db.commit()
    estado["info"] = {
        n: {"sync_at": VELHO, "status": "InTransit"}
        for n in ("AA111111111BR", "BB222222222BR", "CC333333333BR")
    }
    estado["estado"] = dict.fromkeys(
        ("AA111111111BR", "BB222222222BR", "CC333333333BR"), RASTREANDO
    )
    estado["retomar_ok"] = ["AA111111111BR"]
    estado["ja_retomados"] = ["BB222222222BR"]
    estado["parados"] = ["CC333333333BR"]

    out = await logistica_track_sync.forcar_reconsulta(db)

    assert chamadas["reregistrar"] == [["BB222222222BR"]]
    assert out["gratis"] == 1 and out["pagos"] == 1 and out["parados_pendentes"] == 1
    assert chamadas["fetch"] == [["AA111111111BR", "BB222222222BR"]]
    assert (await _linha(db, "r3")).rastreio_17track_at is None  # não foi forçado


@pytest.mark.asyncio
async def test_forcar_sem_quota_desregistra_apagados_e_marca_aviso(db: AsyncSession, fake_forcar):
    """Apagou no 17track e não conseguiu registrar de volta (saldo acabou):
    a linha perde a marca de registrado pra o sync de 15 min tentar de novo
    quando houver saldo — sem isso a Localização congelaria pra sempre."""
    chamadas, estado = fake_forcar
    db.add(_linha_ml("q1", "AA111111111BR"))
    await db.commit()
    estado["info"] = {"AA111111111BR": {"sync_at": None, "status": "InTransit"}}
    estado["estado"] = {"AA111111111BR": RASTREANDO_JA_RETOMADO}
    estado["sem_quota"] = True
    estado["apagados_sem_registro"] = ["AA111111111BR"]

    out = await logistica_track_sync.forcar_reconsulta(db)

    assert out["sem_quota"] is True and out["pagos"] == 0 and out["desregistrados"] == 1
    assert chamadas["fetch"] == []
    assert (await _linha(db, "q1")).rastreio_17track is None
    assert await logistica_track_sync.sem_quota_desde() is not None


@pytest.mark.asyncio
async def test_forcar_respeita_teto_diario_e_saldo(db: AsyncSession, fake_forcar):
    chamadas, estado = fake_forcar
    nums = [f"A{i:09d}BR" for i in range(1, 6)]
    db.add_all([_linha_ml(f"t{i}", n) for i, n in enumerate(nums)])
    await db.commit()
    estado["info"] = {n: {"sync_at": VELHO, "status": "InTransit"} for n in nums}
    estado["estado"] = dict.fromkeys(nums, RASTREANDO_JA_RETOMADO)
    # Já gastou quase o teto do dia: só sobram 2.
    await logistica_track_sync._somar_gasto(logistica_track_sync.TETO_PAGOS_DIA - 2)

    out = await logistica_track_sync.forcar_reconsulta(db)

    assert chamadas["reregistrar"] == [nums[:2]]
    assert out["pagos"] == 2 and out["cortados"] == 3

    # Saldo baixo na conta: reserva pros registros normais.
    chamadas["reregistrar"].clear()
    await redis.delete(f"{logistica_track_sync.PREFIXO_GASTO_DIA}{date.today().isoformat()}")
    estado["quota"] = logistica_track_sync.RESERVA_QUOTA + 1
    out = await logistica_track_sync.forcar_reconsulta(db)
    assert chamadas["reregistrar"] == [nums[:1]] and out["cortados"] == 4


@pytest.mark.asyncio
async def test_forcar_aborta_sem_dados_confiaveis(db: AsyncSession, fake_forcar):
    """17track fora do ar (429/5xx/rede): sem dados NÃO se gasta crédito nem se
    queima o retomar gratuito — a rodada aborta e fica pra próxima."""
    chamadas, estado = fake_forcar
    db.add(_linha_ml("e1", "AA111111111BR"))
    await db.commit()
    estado["erro"] = "gettrackinfo: HTTP 429"

    out = await logistica_track_sync.forcar_reconsulta(db)

    assert out["erro"].startswith("gettrackinfo")
    assert chamadas["parar_e_retomar"] == [] and chamadas["reregistrar"] == []


@pytest.mark.asyncio
async def test_forcar_linha_apagada_durante_a_espera_nao_derruba(
    db: AsyncSession, fake_forcar, monkeypatch
):
    """Durante os 90s de espera o ingest pode apagar pedido finalizado da
    tabela; aplicar nas linhas velhas quebraria o commit inteiro."""
    chamadas, estado = fake_forcar
    db.add_all([_linha_ml("w1", "AA111111111BR"), _linha_ml("w2", "BB222222222BR")])
    await db.commit()
    estado["info"] = {
        n: {"sync_at": VELHO, "status": "InTransit"} for n in ("AA111111111BR", "BB222222222BR")
    }
    estado["estado"] = dict.fromkeys(("AA111111111BR", "BB222222222BR"), RASTREANDO)
    estado["eventos"] = [
        ("AA111111111BR", "Marau/RS — Saiu para entrega"),
        ("BB222222222BR", "X — y"),
    ]

    async def _fetch_apagando(numbers):
        chamadas["fetch"].append(sorted(numbers))
        await db.execute(delete(Logistica).where(Logistica.pedido_bling == "w2"))
        await db.commit()
        return list(estado["eventos"])

    monkeypatch.setattr(logistica_track, "fetch", _fetch_apagando)

    out = await logistica_track_sync.forcar_reconsulta(db)

    assert out["atualizados"] == 1
    assert (await _linha(db, "w1")).localizacao == "Marau/RS — Saiu para entrega"


@pytest.mark.asyncio
async def test_forcar_nao_roda_em_dobro(db: AsyncSession, fake_forcar):
    chamadas, _ = fake_forcar
    db.add(_linha_ml("l1", "AA111111111BR"))
    await db.commit()
    await redis.set(logistica_track_sync.CHAVE_LOCK_FORCAR, "1", ex=60)

    out = await logistica_track_sync.forcar_reconsulta(db)

    assert out.get("ja_rodando") is True
    assert chamadas["parar_e_retomar"] == [] and chamadas["reregistrar"] == []
