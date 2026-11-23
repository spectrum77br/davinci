"""Prazo da plataforma pra contestar a devolução + aviso Threema
(services/devolucao_prazo). Eduardo 09/09/2026: o 291835 perdeu a contestação
porque o motivo foi preenchido depois do `return_seller_due_date` da Shopee."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import BlingOrder, DevolucaoRastreio, Devolution, StoreInfo
from app.services import devolucao_prazo as svc

pytestmark = pytest.mark.asyncio


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def test_prazo_shopee_e_tiktok():
    alvo = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    assert svc.prazo_shopee({"return_seller_due_date": _ts(alvo)}) == alvo
    assert svc.prazo_shopee({"return_seller_due_date": 0}) is None
    assert svc.prazo_shopee({}) is None
    assert svc.prazo_shopee(None) is None  # type: ignore[arg-type]

    cedo = alvo - timedelta(days=1)
    caso = {
        "seller_next_action_response": [
            {"action": "RESPOND_RETURN_REQUEST", "deadline": _ts(alvo)},
            {"action": "CONFIRM_RECEIVED", "deadline": _ts(cedo)},
            {"action": "SEM_PRAZO"},
            "lixo",
        ]
    }
    assert svc.prazo_tiktok(caso) == cedo
    assert svc.prazo_tiktok({"seller_next_action_response": []}) is None
    assert svc.prazo_tiktok({}) is None


async def _dev(db, **kw) -> Devolution:
    base = {
        "conta": "barbosa",
        "pedido_bling": "291835",
        "pedido_marketplace": "2509X",
        "sku": "b001.26",
    }
    base.update(kw)
    d = Devolution(**base)
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


async def test_atualizar_prazos_grava_e_respeita_reconsulta(db, monkeypatch):
    agora = datetime.now(UTC)
    prazo = agora + timedelta(hours=30)
    nova = await _dev(db)
    fresca = await _dev(db, pedido_bling="2", prazo_contestacao_at=agora - timedelta(hours=1))
    com_motivo = await _dev(
        db,
        pedido_bling="6",
        motivo_devolucao="Item faltando",
        prazo_contestacao_at=agora - timedelta(hours=20),
    )
    quebrada = await _dev(db, pedido_bling="3", conta="ttk")
    velha = await _dev(db, pedido_bling="4", created_at=agora - timedelta(days=60))
    vencida_ha_dias = await _dev(
        db,
        pedido_bling="5",
        prazo_contestacao=agora - timedelta(days=5),
        prazo_contestacao_at=agora - timedelta(days=1),
    )

    consultadas: list[str] = []

    async def fake(session, dev):
        consultadas.append(dev.pedido_bling)
        if dev.conta == "ttk":
            raise RuntimeError("tiktok fora do ar")
        return svc.cd.PLAT_SHOPEE, prazo

    monkeypatch.setattr(svc, "_prazo_da_plataforma", fake)
    resumo = await svc.atualizar_prazos(db)

    assert sorted(consultadas) == ["291835", "3"]
    assert resumo == {"consultadas": 1, "com_prazo": 1, "sem_api": 0, "erros": 1}
    for d in (nova, fresca, com_motivo, quebrada, velha, vencida_ha_dias):
        await db.refresh(d)
    assert nova.prazo_contestacao == prazo and nova.prazo_contestacao_at is not None
    assert fresca.prazo_contestacao is None  # sem motivo, mas consultada há 1 h: espera 6 h
    # já tem motivo (o chamado sai sozinho): consultada uma vez só, não repete
    assert com_motivo.prazo_contestacao is None
    assert quebrada.prazo_contestacao is None and quebrada.prazo_contestacao_at is not None
    assert velha.prazo_contestacao_at is None  # fora da janela de 45 dias
    assert vencida_ha_dias.prazo_contestacao_at < agora  # vencida há 5 dias: não gasta API

    # sem prazo na plataforma (ML / TikTok sem ação pendente): só marca a consulta
    async def sem_prazo(session, dev):
        return svc.cd.PLAT_ML, None

    monkeypatch.setattr(svc, "_prazo_da_plataforma", sem_prazo)
    nova.prazo_contestacao_at = agora - timedelta(hours=7)
    fresca.prazo_contestacao_at = agora
    await db.commit()
    resumo = await svc.atualizar_prazos(db)
    assert resumo["consultadas"] == 1 and resumo["sem_api"] == 1 and resumo["com_prazo"] == 0
    await db.refresh(nova)
    assert nova.prazo_contestacao == prazo  # não apaga o que já sabia


async def test_atualizar_prazos_ignora_prazo_absurdo(db, monkeypatch):
    """Se a plataforma devolver o campo em outra unidade (ms) ou lixo, a data
    sai lá no ano 57000 — não grava, senão a coluna mente."""
    d = await _dev(db)

    async def ms(session, dev):
        return svc.cd.PLAT_TIKTOK, datetime.now(UTC) + timedelta(days=900)

    monkeypatch.setattr(svc, "_prazo_da_plataforma", ms)
    resumo = await svc.atualizar_prazos(db)
    assert resumo["com_prazo"] == 0
    await db.refresh(d)
    assert d.prazo_contestacao is None and d.prazo_contestacao_at is not None


async def test_atualizar_prazos_pendente_nao_conta_como_erro(db, monkeypatch):
    d = await _dev(db)

    async def pendente(session, dev):
        raise svc.cd._PendenteError("devolucao_sem_pedido_marketplace")

    monkeypatch.setattr(svc, "_prazo_da_plataforma", pendente)
    resumo = await svc.atualizar_prazos(db)
    assert resumo["erros"] == 0 and resumo["consultadas"] == 1
    await db.refresh(d)
    assert d.prazo_contestacao is None and d.prazo_contestacao_at is not None


# ---------------------------------------------------------------- TikTok de verdade
# Os testes acima trocam `_prazo_da_plataforma` inteiro — por isso não viram que
# o ramo TikTok chamava `_return_tiktok(client, …)` (a função é síncrona e recebe
# os CASOS): TypeError engolido em `atualizar_prazos`, e devolução TikTok nunca
# ganhava prazo nem aviso (achado 16/11/2026). Aqui só o client é falso.

OID_TT = "585585025945338891"
RID_VIGIA = "4042116781741081611"


class _FakeTikTok:
    """`get_return_list` como a TikTok devolve: o caso que o Acompanhamento
    conhece, um caso mais novo do mesmo pedido e um só-reembolso (sem pacote)
    com prazo mais cedo, que não pode contar."""

    def __init__(self, casos: list[dict]) -> None:
        self.casos = casos
        self.pedidos: list[list[str]] = []

    async def get_return_list(self, *, order_ids=None, **kw):
        self.pedidos.append(list(order_ids or []))
        return [dict(c) for c in self.casos]


def _caso(rid: str, tipo: str, prazo: datetime, update_time: int) -> dict:
    return {
        "order_id": OID_TT,
        "return_id": rid,
        "return_type": tipo,
        "return_status": "BUYER_SHIPPED_ITEM",
        "seller_next_action_response": [
            {"action": "SELLER_RESPOND_RECEIVE_PACKAGE", "deadline": _ts(prazo)}
        ],
        "update_time": update_time,
    }


@pytest.fixture
async def tiktok(db, make_user, monkeypatch):
    """Pedido TikTok no espelho do Bling (loja → store_info tiktok), linha do
    Acompanhamento com o id do caso e o client falso no lugar da integração."""
    user = await make_user()
    db.add(StoreInfo(user_id=user.id, platform="tiktok", account_name="injox", bling_store_id="77"))
    db.add(
        BlingOrder(
            id=uuid4(),
            numero="290845",
            numeroloja=OID_TT,
            bling_id=123456,
            situacao="83957",
            loja="77",
            data=datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
            item_index=0,
            item_codigo="b001.26",
            item_descricao="Mala Listrada tamanho 26",
            item_quantidade=1,
        )
    )
    db.add(
        DevolucaoRastreio(pedido_bling="290845", fonte_auto="tiktok", devolucao_id_auto=RID_VIGIA)
    )
    await db.commit()

    agora = datetime.now(UTC).replace(microsecond=0)
    prazos = {
        "vigia": agora + timedelta(hours=20),
        "outro": agora + timedelta(days=3),
        "reembolso": agora + timedelta(hours=2),
    }
    fake = _FakeTikTok(
        [
            _caso(RID_VIGIA, "RETURN_AND_REFUND", prazos["vigia"], 10),
            _caso("4042116781741099999", "RETURN_AND_REFUND", prazos["outro"], 20),
            _caso("4042116781741000001", "REFUND", prazos["reembolso"], 30),
        ]
    )
    contas: list[str] = []

    async def _client(session, ch, dev):
        contas.append(dev.conta)
        return fake

    monkeypatch.setattr(svc.cd, "_tiktok_client_para", _client)
    fake.prazos = prazos  # type: ignore[attr-defined]
    fake.contas = contas  # type: ignore[attr-defined]
    return fake


async def test_prazo_da_plataforma_tiktok_caminho_real(db, tiktok):
    dev = await _dev(db, conta="injox", pedido_bling="290845", pedido_marketplace=OID_TT)

    plat, prazo = await svc._prazo_da_plataforma(db, dev)

    assert plat == svc.cd.PLAT_TIKTOK
    # o caso do Acompanhamento (não o mais novo, nem o só-reembolso de prazo mais cedo)
    assert prazo == tiktok.prazos["vigia"]
    assert tiktok.pedidos == [[OID_TT]] and tiktok.contas == ["injox"]


async def test_prazo_da_plataforma_tiktok_sem_caso_com_pacote(db, tiktok):
    dev = await _dev(db, conta="injox", pedido_bling="290845", pedido_marketplace=OID_TT)
    tiktok.casos = [c for c in tiktok.casos if c["return_type"] == "REFUND"]

    assert await svc._prazo_da_plataforma(db, dev) == (svc.cd.PLAT_TIKTOK, None)


async def test_atualizar_prazos_tiktok_grava_e_avisa(db, tiktok, threema):
    """Ponta a ponta sem trocar `_prazo_da_plataforma`: a rodada grava o prazo
    da TikTok (sem contar erro) e o aviso de 24 h dispara pra motivo vazio."""
    dev = await _dev(db, conta="injox", pedido_bling="290845", pedido_marketplace=OID_TT)

    resumo = await svc.run(db)

    assert resumo["erros"] == 0 and resumo["consultadas"] == 1
    assert resumo["com_prazo"] == 1 and resumo["sem_api"] == 0
    assert resumo["avisadas"] == 1
    await db.refresh(dev)
    assert dev.prazo_contestacao == tiktok.prazos["vigia"]
    assert dev.aviso_prazo_at is not None
    assert "Pedido 290845 (injox)" in threema.enviados[0][0]


class _FakeThreema:
    enviados: list[tuple[str, list[str]]] = []
    falhar = False

    def __init__(self, *, contexto: str | None = None) -> None:
        pass

    async def send_to_all(self, text, recipients=None):
        if self.falhar:
            raise RuntimeError("gateway 503")
        _FakeThreema.enviados.append((text, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}


@pytest.fixture
def threema(monkeypatch):
    # O aviso sai pelo núcleo do Cadastro (threema_envios, tarefa 16): sem
    # semente no banco de teste, vale a configuração antiga (o .env).
    from app.config import get_settings
    from app.services import threema as threema_mod
    from app.services import threema_envios

    threema_envios.limpar_cache()
    _FakeThreema.enviados = []
    _FakeThreema.falhar = False
    monkeypatch.setattr(threema_mod, "ThreemaClient", _FakeThreema)
    monkeypatch.setattr(get_settings(), "threema_recipients", "ABCDEFGH")
    return _FakeThreema


async def test_avisar_prazos_so_motivo_vazio_perto_do_prazo_uma_vez(db, threema):
    agora = datetime.now(UTC)
    perto = agora + timedelta(hours=10)
    alvo = await _dev(db, prazo_contestacao=perto)
    vazio_str = await _dev(db, pedido_bling="2", motivo_devolucao="", prazo_contestacao=perto)
    com_motivo = await _dev(
        db, pedido_bling="3", motivo_devolucao="Item faltando", prazo_contestacao=perto
    )
    longe = await _dev(db, pedido_bling="4", prazo_contestacao=agora + timedelta(days=3))
    vencida = await _dev(db, pedido_bling="5", prazo_contestacao=agora - timedelta(hours=1))
    sem_prazo = await _dev(db, pedido_bling="6")

    assert await svc.avisar_prazos(db) == 2
    assert len(threema.enviados) == 1
    texto, destinos = threema.enviados[0]
    assert destinos == ["ABCDEFGH"]
    assert "SEM MOTIVO" in texto and "Pedido 291835 (barbosa)" in texto and "Pedido 2 (" in texto
    assert "Pedido 3" not in texto and "Pedido 4" not in texto and "Pedido 5" not in texto
    assert perto.astimezone(svc.BRT).strftime("%d/%m %H:%M") in texto

    for d in (alvo, vazio_str, com_motivo, longe, vencida, sem_prazo):
        await db.refresh(d)
    assert alvo.aviso_prazo_at is not None and vazio_str.aviso_prazo_at is not None
    assert com_motivo.aviso_prazo_at is None and longe.aviso_prazo_at is None
    assert vencida.aviso_prazo_at is None and sem_prazo.aviso_prazo_at is None

    # segunda rodada: ninguém novo, não repete
    assert await svc.avisar_prazos(db) == 0
    assert len(threema.enviados) == 1


async def test_avisar_prazos_threema_fora_nao_marca(db, threema):
    d = await _dev(db, prazo_contestacao=datetime.now(UTC) + timedelta(hours=2))
    threema.falhar = True
    assert await svc.avisar_prazos(db) == 0
    await db.refresh(d)
    assert d.aviso_prazo_at is None  # tenta de novo na próxima rodada
    threema.falhar = False
    assert await svc.avisar_prazos(db) == 1
    rows = (await db.execute(select(Devolution))).scalars().all()
    assert all(r.aviso_prazo_at is not None for r in rows)


async def test_avisar_prazos_ignora_novo_e_junta_o_kit(db, threema):
    """Vinicius 26/11: voltou "Novo" sem motivo é devolução comum (não avisa), o
    kit aparecia uma vez por peça e a peça sem motivo de um pedido que já tem
    motivo em outra peça também avisava."""
    agora = datetime.now(UTC)
    cedo, tarde = agora + timedelta(hours=5), agora + timedelta(hours=9)
    novo = await _dev(db, pedido_bling="10", condicao_produto="Novo", prazo_contestacao=cedo)
    kit = [
        await _dev(
            db, pedido_bling="11", sku=f"k{i}", condicao_produto="Manutenção", prazo_contestacao=p
        )
        for i, p in enumerate((tarde, cedo, tarde))
    ]
    fone = await _dev(db, pedido_bling="12", sku="fone", prazo_contestacao=cedo)
    await _dev(db, pedido_bling="12", sku="cel", motivo_devolucao="Dano funcional / Não funciona")
    misto = await _dev(db, pedido_bling="13", condicao_produto="Novo", prazo_contestacao=cedo)
    misto_sem_cond = await _dev(db, pedido_bling="13", sku="b2", prazo_contestacao=cedo)

    assert await svc.avisar_prazos(db) == 2  # 11 (kit) e 13 (uma peça sem condição)
    texto = threema.enviados[0][0]
    assert texto.count("Pedido 11 (") == 1 and texto.count("Pedido 13 (") == 1
    assert "Pedido 10" not in texto and "Pedido 12" not in texto
    assert (
        f"Pedido 11 (barbosa) — prazo {cedo.astimezone(svc.BRT).strftime('%d/%m %H:%M')}" in texto
    )

    for d in (novo, fone, misto, misto_sem_cond, *kit):
        await db.refresh(d)
    assert all(d.aviso_prazo_at is not None for d in (*kit, misto, misto_sem_cond))
    assert novo.aviso_prazo_at is None and fone.aviso_prazo_at is None

    # o "Novo" muda pra Manutenção antes do prazo: aí avisa
    novo.condicao_produto = "Manutenção"
    await db.commit()
    assert await svc.avisar_prazos(db) == 1
    assert "Pedido 10 (barbosa)" in threema.enviados[1][0]
    assert "Pedido 11" not in threema.enviados[1][0]


async def test_run_junta_os_dois(db, threema, monkeypatch):
    await _dev(db)

    async def fake(session, dev):
        return svc.cd.PLAT_SHOPEE, datetime.now(UTC) + timedelta(hours=5)

    monkeypatch.setattr(svc, "_prazo_da_plataforma", fake)
    resumo = await svc.run(db)
    assert resumo["com_prazo"] == 1 and resumo["avisadas"] == 1
    assert len(threema.enviados) == 1
