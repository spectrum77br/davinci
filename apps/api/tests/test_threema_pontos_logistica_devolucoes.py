"""F1/C8a da tarefa 16: os avisos da Logística e das Devoluções que liam o .env
passam a ler o Cadastro (Cadastros › Envios Threema) — itens 5 (Correios
grave), 6 (automação parada), 7 (loja sem responder), 8 (pacote de devolução
grave), 9 (prazo de contestação) e 12 (códigos do Tuta no servidor).

Um teste por ponto, PELO CAMINHO REAL da função (só o ThreemaClient é falso):

* (a) Cadastro semeado com o .env VAZIO: manda para quem está no Cadastro,
  com a chave do envio e o remetente de hoje (o portão `if not <env>` sumiu);
* (b) desligado: não manda, e o carimbo segue o `ao_desligar` do catálogo
  (descarta: o aviso não volta ao religar; acumula: sai tudo ao religar);
* (c) ninguém válido (a única pessoa da lista foi suspensa): não manda,
  acende o sino dos admins e grava o registro `ok=false`.

E a semente da fatia: `semear` + `conferir` das 6 chaves dá 0 diferenças e,
depois dela, cada ponto manda EXATAMENTE para quem o legado mandaria (as duas
leis do desenho: nenhum aviso para de sair; nenhum vai para quem foi suspenso).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    Alert,
    DevolucaoRastreio,
    Devolution,
    IntegrationPlatform,
    Logistica,
    LogisticaRoboComando,
    ThreemaEnvio,
    ThreemaEnvioRegistro,
    UserRole,
    UserStatus,
)
from app.services import (
    devolucao_prazo,
    devolucao_rastreio_sync,
    logistica_meli,
    logistica_robo,
    logistica_shopee,
    logistica_tiktok,
    logistica_track,
    logistica_track_sync,
    threema,
    threema_envios,
    threema_registro,
    tuta_devolucoes,
)
from app.services import marketplace_shipment_check as msc
from app.services.devolucao_returns import ReturnInfo
from app.services.threema_catalogo import CATALOGO, K
from tests.test_threema_resolver import _usuario, semeado
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

pytestmark = pytest.mark.asyncio

# As 6 chaves da C8a e o remetente de HOJE de cada ponto (o `contexto` que o
# código passava ao ThreemaClient antes da fatia).
REMETENTE_DE_HOJE = {
    K.LOGISTICA_CORREIOS_GRAVE: "logistica",
    K.LOGISTICA_VIGIA_AUTOMACAO: "logistica",
    K.LOGISTICA_LOJA_MUDA: "logistica",
    K.DEVOLUCOES_PACOTE_VOLTA_GRAVE: "logistica",
    K.DEVOLUCOES_PRAZO_CONTESTACAO: "devolucoes",
    K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR: "devolucoes",
}
MINHAS = list(REMETENTE_DE_HOJE)
# O .env que estes pontos liam (fica congelado como reserva de emergência).
_ENVS = (
    "threema_recipients",
    "nf_sem_estoque_threema_recipients",
    "shipment_muda_threema_recipients",
)


class _Fake:
    """ThreemaClient falso, do jeito dos fakes da bateria (`contexto` no
    construtor, `send_to_all(texto, destinos)` sem `**kw`). Guarda o
    remetente, os destinos e a marca do registro (chave e via)."""

    envios: list[dict] = []

    def __init__(self, *, contexto=None):
        self.contexto = contexto

    async def send_to_all(self, texto, destinos):
        marca = threema_registro.marca_atual()
        _Fake.envios.append(
            {
                "chave": marca.chave if marca else None,
                "via": marca.via if marca else None,
                "contexto": self.contexto,
                "destinos": list(destinos),
                "texto": texto,
            }
        )
        return {"sent": list(destinos), "failed": []}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    _Fake.envios = []
    threema_envios.limpar_cache()
    threema_registro.reiniciar_disjuntor()
    msc._lojas_mudas.clear()
    s = get_settings()
    monkeypatch.setattr(s, "threema_cadastro_ativo", True)
    monkeypatch.setattr(s, "threema_registro_ativo", True)
    for nome in _ENVS:  # o .env VAZIO: quem recebe só pode vir do Cadastro
        monkeypatch.setattr(s, nome, "")
    monkeypatch.setattr(threema, "ThreemaClient", _Fake)
    yield
    msc._lojas_mudas.clear()
    threema_envios.limpar_cache()


async def _pessoas(db):
    admin = await _usuario(db, "admin c8a", "TSTA0001", role=UserRole.ADMIN)
    joana = await _usuario(db, "joana c8a", "TSTJ0006")
    sumido = await _usuario(db, "sumido c8a", "TSTS0009", status=UserStatus.SUSPENDED)
    return admin, joana, sumido


async def _ligar(db, chave: str, ativo: bool | None) -> None:
    linha = await db.get(ThreemaEnvio, chave, populate_existing=True)
    linha.ativo = ativo
    await db.commit()


async def _registro(db, chave: str) -> list[ThreemaEnvioRegistro]:
    await db.commit()
    return list(
        (
            await db.execute(
                select(ThreemaEnvioRegistro)
                .where(ThreemaEnvioRegistro.envio_chave == chave)
                .order_by(ThreemaEnvioRegistro.id)
            )
        ).scalars()
    )


async def _sinos(db, chave: str) -> list[Alert]:
    await db.commit()
    return [
        a
        for a in (await db.execute(select(Alert))).scalars()
        if (a.dedupe_key or "").startswith(f"threema_ninguem:{chave}:")
    ]


# ─────────────────────────────────────────────── os pontos, pelo caminho real


_APREENDIDO = "Varzea Grande/MT — Objeto apreendido por: Secretaria Estadual da Fazenda"


async def _correios_grave(db, monkeypatch, *, linha: Logistica | None = None) -> Logistica:
    """Item 5: o rastreio dos Correios (cron do 17track) lê uma ocorrência
    grave nova na linha da Logística."""
    if linha is None:
        codigo = f"AD{uuid.uuid4().int % 10**9:09d}BR"
        linha = Logistica(
            pedido_bling=f"5{uuid.uuid4().hex[:6]}",
            plataforma="Mercado Livre",
            conta="jlas2",
            data=date.today(),
            rastreio=codigo,
            rastreio_17track=codigo,
            localizacao="Sao Paulo/SP — Objeto em transferência - por favor aguarde",
            status_bling="Em andamento",
        )
        db.add(linha)
    # leitura velha: a rodada pergunta de novo ao 17track
    linha.localizacao_at = datetime.now(UTC) - timedelta(days=2)
    await db.commit()
    evento = (linha.rastreio_17track, _APREENDIDO)

    async def _register(numbers):
        return {"ok": list(numbers), "sem_quota": False}

    async def _fetch(numbers):
        return [evento]

    monkeypatch.setattr(logistica_track, "register", _register)
    monkeypatch.setattr(logistica_track, "fetch", _fetch)
    await logistica_track_sync.run(db, pedidos=[linha.pedido_bling])
    return linha


class _RedisVigia:
    """O motor da Logística parado há 40 min (limite 20); o resto em dia."""

    def __init__(self) -> None:
        from app import worker as w

        agora = datetime.now(UTC)
        self.chave_ok = w._LOGISTICA_OK_KEY
        self.ok = {job: str(int(agora.timestamp())) for job in w._LOGISTICA_VIGIA_LIMITES_MIN}
        self.ok["recarregar"] = str(int((agora - timedelta(minutes=40)).timestamp()))

    async def hget(self, key, job):
        return self.ok.get(job) if key == self.chave_ok else None

    async def hset(self, key, job, val):
        return 1


async def _vigia_automacao(db, monkeypatch) -> dict:
    """Item 6: o vigia da automação da Logística (worker, :13 e :43)."""
    from app import worker as w

    return await w.logistica_vigia({"redis": _RedisVigia()})


class _Integ:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.name = "Vortan"
        self.platform = IntegrationPlatform.SHOPEE


async def _loja_muda(db, monkeypatch, *, integ: _Integ | None = None, ticks: int | None = None):
    """Item 7: a loja devolve lote vazio minuto a minuto até o 30º."""
    integ = integ or _Integ()
    for _ in range(ticks or msc._MUDA_TICKS_PRA_AVISAR):
        await msc._registrar_resposta_da_loja(integ, pedidos=32, responderam=0)
    return integ


async def _pacote_volta_grave(db, monkeypatch, *, pedido: str | None = None) -> str:
    """Item 8: o rastreio das devoluções lê o pacote de volta APREENDIDO (a
    reversa da Shopee)."""
    if pedido is None:
        pedido = f"8{uuid.uuid4().hex[:6]}"
        db.add(
            Logistica(
                pedido_bling=pedido,
                plataforma="Shopee",
                pedido_marketplace=f"mk-{pedido}",
                conta="jlas",
            )
        )
        await db.commit()
    quando = datetime(2026, 10, 5, 22, 41, 58, tzinfo=UTC)
    info = ReturnInfo(
        fonte="shopee",
        status="ACCEPTED",
        tracking="BR265869901238B",
        carrier=None,
        created_at=datetime(2026, 9, 5, tzinfo=UTC),
        updated_at=quando,
        return_id="2609RET293379",
        localizacao="Pedido apreendido pela fiscalização. Entrar em contato com a Receita Federal",
        localizacao_em=quando,
    )

    async def _shopee(session, linhas, **kw):
        return {pedido: info}

    async def _vazio(session, linhas, **kw):
        return {}

    async def _register(numbers):
        return {"ok": list(numbers)}

    monkeypatch.setattr(logistica_shopee, "returns_por_pedido", _shopee, raising=False)
    monkeypatch.setattr(logistica_tiktok, "returns_por_pedido", _vazio, raising=False)
    monkeypatch.setattr(logistica_meli, "returns_por_pedido", _vazio, raising=False)
    monkeypatch.setattr(logistica_track, "register", _register)
    await devolucao_rastreio_sync.run(db, pedidos=[pedido])
    return pedido


async def _nova_devolucao(db, pedido: str | None = None) -> Devolution:
    d = Devolution(
        conta="barbosa",
        pedido_bling=pedido or f"9{uuid.uuid4().hex[:6]}",
        pedido_marketplace="2509X",
        sku="b001.26",
        prazo_contestacao=datetime.now(UTC) + timedelta(hours=10),
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


async def _prazo_contestacao(db, monkeypatch) -> int:
    """Item 9: devolução SEM MOTIVO com o prazo de contestar acabando."""
    await _nova_devolucao(db)
    return await devolucao_prazo.avisar_prazos(db)


_CAIXA_TUTA = json.dumps(
    {"ok": True, "texto": "Correios\nCódigo de postagem da logística reversa: PJ123456789BR"}
)


async def _tuta_servidor(db, monkeypatch) -> LogisticaRoboComando:
    """Item 12: o executor devolve a leitura da caixa do Tuta
    (`logistica_robo.registrar_resultado` → `entregar_resultado`)."""
    cmd = LogisticaRoboComando(
        logistica_id=None, acao=tuta_devolucoes.ACAO, payload={"horas": 24}, status="pending"
    )
    db.add(cmd)
    await db.commit()
    await logistica_robo.registrar_resultado(db, cmd.id, status="done", result=_CAIXA_TUTA)
    return cmd


PONTOS = {
    K.LOGISTICA_CORREIOS_GRAVE: _correios_grave,
    K.LOGISTICA_VIGIA_AUTOMACAO: _vigia_automacao,
    K.LOGISTICA_LOJA_MUDA: _loja_muda,
    K.DEVOLUCOES_PACOTE_VOLTA_GRAVE: _pacote_volta_grave,
    K.DEVOLUCOES_PRAZO_CONTESTACAO: _prazo_contestacao,
    K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR: _tuta_servidor,
}


def test_as_chaves_da_c8a_estao_migradas_com_o_remetente_de_hoje():
    assert set(PONTOS) == set(MINHAS)
    for chave, remetente in REMETENTE_DE_HOJE.items():
        d = CATALOGO[chave]
        assert d.migrado and d.fatia == "F1", chave
        assert d.remetente == remetente, chave


# ─────────────────────────────────────────────── (a) Cadastro com o .env vazio


@pytest.mark.parametrize("chave", MINHAS)
async def test_cadastro_com_env_vazio_manda_pela_chave_e_pelo_remetente_de_hoje(
    db, monkeypatch, chave
):
    _admin, joana, sumido = await _pessoas(db)
    # a lista do Cadastro tem quem foi suspenso também: ele fica de fora
    await semeado(db, chave, joana, sumido)
    await PONTOS[chave](db, monkeypatch)
    assert [(e["chave"], e["via"], e["contexto"], e["destinos"]) for e in _Fake.envios] == [
        (chave, "cadastro", REMETENTE_DE_HOJE[chave], ["TSTJ0006"])
    ]


async def test_correios_grave_pelo_webhook_do_17track_tambem_le_o_cadastro(db, monkeypatch, client):
    """O push do 17track (o tempo real do item 5) chama o mesmo avisar_graves
    sem passar a chave: vale o padrão, o Correios grave."""
    from app.routers import logistica_track as rota

    _admin, joana, _ = await _pessoas(db)
    await semeado(db, K.LOGISTICA_CORREIOS_GRAVE, joana)
    monkeypatch.setattr(get_settings(), "logi_17track_webhook_secret", "segredo-de-teste-c8a")

    async def _primeira_vez(_hash):
        return True

    monkeypatch.setattr(rota, "_claim_push", _primeira_vez)
    db.add(Logistica(pedido_bling="295070", conta="jlas2", rastreio="AD890179823BR"))
    await db.commit()
    payload = {
        "data": {
            "accepted": [
                {
                    "number": "AD890179823BR",
                    "track_info": {
                        "latest_event": {
                            "address": {"city": "Varzea Grande", "state": "MT"},
                            "description": "Objeto apreendido por: Secretaria Estadual da Fazenda",
                        }
                    },
                }
            ]
        }
    }
    r = await client.post("/api/webhooks/17track/segredo-de-teste-c8a", json=payload)
    assert r.json()["rows"] == 1, r.text
    assert [(e["chave"], e["contexto"], e["destinos"]) for e in _Fake.envios] == [
        (K.LOGISTICA_CORREIOS_GRAVE, "logistica", ["TSTJ0006"])
    ]
    assert "295070" in _Fake.envios[0]["texto"]


# ─────────────────────────────────────────────── (c) ninguém válido


@pytest.mark.parametrize("chave", MINHAS)
async def test_ninguem_valido_nao_manda_acende_o_sino_e_registra(db, monkeypatch, chave):
    admin, _joana, sumido = await _pessoas(db)
    # o .env tem gente: lista vazia NUNCA pode cair nele
    monkeypatch.setattr(get_settings(), "threema_recipients", "TSTZ0009")
    await semeado(db, chave, sumido)
    await PONTOS[chave](db, monkeypatch)
    assert _Fake.envios == []
    assert [(a.user_id, a.escopo) for a in await _sinos(db, chave)] == [(None, "adm")]
    (linha,) = await _registro(db, chave)
    assert (linha.ok, linha.erro, linha.via, linha.destinatario_id, linha.contexto) == (
        False,
        "sem_destinatario_valido",
        "cadastro",
        None,
        REMETENTE_DE_HOJE[chave],
    )


# ─────────────────────────────────────────────── (b) desligado, ponto a ponto


async def test_correios_grave_desligado_descarta(db, monkeypatch):
    chave = K.LOGISTICA_CORREIOS_GRAVE
    assert CATALOGO[chave].ao_desligar == "descarta"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    linha = await _correios_grave(db, monkeypatch)
    assert _Fake.envios == []
    await db.refresh(linha)
    assert linha.problema_correios_em is not None  # a ocorrência foi carimbada
    assert await _registro(db, chave) == []  # desligado não grava registro
    assert await _sinos(db, chave) == []
    await _ligar(db, chave, None)
    # religado: a mesma ocorrência não volta (o aviso daquele período se perdeu)
    await _correios_grave(db, monkeypatch, linha=linha)
    assert _Fake.envios == []
    # ocorrência nova sai normalmente
    await _correios_grave(db, monkeypatch)
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"]]


async def test_vigia_automacao_desligado_descarta_e_o_sino_da_automacao_sai(db, monkeypatch):
    chave = K.LOGISTICA_VIGIA_AUTOMACAO
    assert CATALOGO[chave].ao_desligar == "descarta"
    admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    out = await _vigia_automacao(db, monkeypatch)
    assert out["atrasados"] == 1
    assert _Fake.envios == []
    await db.commit()
    alertas = (await db.execute(select(Alert))).scalars().all()
    assert [
        (a.user_id, a.escopo)
        for a in alertas
        if (a.dedupe_key or "").startswith("logistica_vigia:")
    ] == [(None, "adm")], "o sino da automação parada não depende do Threema"
    assert await _registro(db, chave) == []
    await _ligar(db, chave, None)
    await _vigia_automacao(db, monkeypatch)
    # religado: sai o estado de AGORA, uma mensagem (nada acumulado)
    assert len(_Fake.envios) == 1 and "recarregar" in _Fake.envios[0]["texto"]


async def test_loja_muda_desligado_descarta(db, monkeypatch):
    chave = K.LOGISTICA_LOJA_MUDA
    assert CATALOGO[chave].ao_desligar == "descarta"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    integ = await _loja_muda(db, monkeypatch)
    assert _Fake.envios == []
    await _ligar(db, chave, None)
    # religado com a loja ainda muda: o aviso do 30º minuto não volta
    await _loja_muda(db, monkeypatch, integ=integ, ticks=5)
    assert _Fake.envios == []
    assert msc._lojas_mudas[integ.id] == msc._MUDA_TICKS_PRA_AVISAR + 5
    # e quando ela volta a responder, a volta sai
    await msc._registrar_resposta_da_loja(integ, pedidos=32, responderam=32)
    assert [e["destinos"] for e in _Fake.envios] == [["TSTJ0006"]]
    assert "voltou a responder" in _Fake.envios[0]["texto"]


async def test_pacote_volta_grave_desligado_descarta(db, monkeypatch):
    chave = K.DEVOLUCOES_PACOTE_VOLTA_GRAVE
    assert CATALOGO[chave].ao_desligar == "descarta"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    pedido = await _pacote_volta_grave(db, monkeypatch)
    assert _Fake.envios == []
    row = (
        await db.execute(select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling == pedido))
    ).scalar_one()
    assert "apreendido" in (row.localizacao_auto or "")  # a volta foi gravada
    await _ligar(db, chave, None)
    await _pacote_volta_grave(db, monkeypatch, pedido=pedido)
    assert _Fake.envios == []
    # o Correios grave (item 5) não foi tocado: cada um tem a sua chave
    await semeado(db, K.LOGISTICA_CORREIOS_GRAVE)
    await _pacote_volta_grave(db, monkeypatch)
    assert [(e["chave"], e["destinos"]) for e in _Fake.envios] == [(chave, ["TSTJ0006"])]


async def test_prazo_contestacao_desligado_acumula(db, monkeypatch):
    chave = K.DEVOLUCOES_PRAZO_CONTESTACAO
    assert CATALOGO[chave].ao_desligar == "acumula"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    primeira = await _nova_devolucao(db, "291835")
    assert await devolucao_prazo.avisar_prazos(db) == 0
    segunda = await _nova_devolucao(db, "291836")
    assert await devolucao_prazo.avisar_prazos(db) == 0
    assert _Fake.envios == []
    for d in (primeira, segunda):
        await db.refresh(d)
        assert d.aviso_prazo_at is None  # não carimbou: fica para quando religar
    await _ligar(db, chave, None)
    assert await devolucao_prazo.avisar_prazos(db) == 2
    (envio,) = _Fake.envios
    assert envio["destinos"] == ["TSTJ0006"]
    assert "Pedido 291835" in envio["texto"] and "Pedido 291836" in envio["texto"]
    for d in (primeira, segunda):
        await db.refresh(d)
        assert d.aviso_prazo_at is not None
    # e não repete
    assert await devolucao_prazo.avisar_prazos(db) == 0
    assert len(_Fake.envios) == 1


async def test_tuta_servidor_desligado_descarta(db, monkeypatch):
    chave = K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR
    assert CATALOGO[chave].ao_desligar == "descarta"
    _admin, joana, _ = await _pessoas(db)
    await semeado(db, chave, joana, ativo=False)
    cmd = await _tuta_servidor(db, monkeypatch)
    assert _Fake.envios == []
    await db.refresh(cmd)
    assert cmd.status == "done" and cmd.result  # o comando do dia já está feito
    await _ligar(db, chave, None)
    # religado: nada devolve a leitura daquele dia (não há comando pendente)
    assert await logistica_robo.lease(db, limit=5) == []
    assert _Fake.envios == []
    res = await tuta_devolucoes.entregar_resultado(db, _CAIXA_TUTA)
    assert res["enviado"] is True and _Fake.envios[0]["destinos"] == ["TSTJ0006"]


# ─────────────────────────────────────────────── a semente da fatia


@pytest.mark.usefixtures("limpar_ouvidoria")
@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_semente_confere_e_cada_ponto_manda_para_quem_o_legado_mandaria(
    db, monkeypatch, variante
):
    await montar(db, monkeypatch, variante=variante)
    esperado = {
        c: list(threema_envios._dedupe((await threema_envios.legado(c, session=db)).ids))
        for c in MINHAS
    }
    await threema_envios.semear(db, fatia="F1", chaves=MINHAS, aplicar=True)
    await db.commit()
    itens = await threema_envios.conferir(db, chaves=MINHAS)
    assert [(x.chave, x.situacao) for x in itens] == [(c, "igual") for c in MINHAS]
    # o .env fica congelado como reserva, mas o ponto não lê mais: esvaziar
    # não muda quem recebe
    for nome in _ENVS:
        monkeypatch.setattr(get_settings(), nome, "")
    for chave in MINHAS:
        _Fake.envios = []
        await PONTOS[chave](db, monkeypatch)
        enviados = [(e["chave"], e["via"], e["destinos"]) for e in _Fake.envios]
        assert enviados == ([(chave, "cadastro", esperado[chave])] if esperado[chave] else []), (
            chave
        )
        if not esperado[chave] and threema_envios._lista_vazia_desliga(CATALOGO[chave]):
            # .env vazio hoje = calado: a semente desligou, nada de sino (o
            # "thatcher" sem Threema do 12 é falha, não desligado: tem sino)
            assert await _registro(db, chave) == [] and await _sinos(db, chave) == [], chave
    if variante == "cheio":
        assert esperado[K.LOGISTICA_LOJA_MUDA] == [ID["heisenberg"]]
        assert esperado[K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR] == [ID["thatcher"]]
        assert esperado[K.LOGISTICA_CORREIOS_GRAVE] == [
            ID["churchill"],
            ID["londres"],
            ID["cairo_sa"],
        ]


@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_quem_foi_suspenso_depois_da_semente_para_de_receber(db, monkeypatch):
    c = await montar(db, monkeypatch)
    await threema_envios.semear(db, fatia="F1", chaves=MINHAS, aplicar=True)
    await db.commit()
    c.usuarios["londres"].status = UserStatus.SUSPENDED
    c.usuarios["thatcher"].status = UserStatus.SUSPENDED
    await db.commit()
    await _correios_grave(db, monkeypatch)
    await _tuta_servidor(db, monkeypatch)
    assert [e["destinos"] for e in _Fake.envios] == [[ID["churchill"], ID["cairo_sa"]]]
    # o legado (o .env e o usuário pelo nome) ainda mandaria para os dois
    leg = await threema_envios.legado(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert ID["londres"] in leg.ids
    tuta = await threema_envios.legado(K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR, session=db)
    assert tuta.ids == (ID["thatcher"],)
    (linha,) = await _registro(db, K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR)
    assert (linha.ok, linha.erro) == (False, "sem_destinatario_valido")
