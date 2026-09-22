"""Vigia de importação (fase 2, robô da Ouvidoria) — regras por plataforma,
tolerância, conferência ao vivo no Bling, conta que falha, sumiço e modo.

O que este arquivo trava (21/09/2026):

- a regra "DEVE estar no Bling" de cada plataforma: ML só `paid` e sem tag
  test_order/fraud_risk_detected; Shopee fora de UNPAID/CANCELLED/IN_CANCEL;
  TikTok fora de UNPAID/ON_HOLD/CANCELLED; Amazon em Unshipped/
  PartiallyShipped/Shipped;
- as três peneiras da rodada: tolerância (pago há < 90 min não acusa),
  espelho `bling_orders` e Bling AO VIVO — pedido que o Bling já tem mas o
  espelho ainda não viu NÃO vira ocorrência;
- conta cuja API falhou vira `conta:<integration_id>` e as ocorrências dela
  NÃO fecham como "sumiu"; quando a conta volta, a de conta fecha sozinha;
- pedido que saiu da listagem (ou apareceu no Bling) fecha como "sumiu";
- Bling fora do ar: não abre nem fecha nada; teto de 40 conferências por
  rodada: o resto é marcado como visto e fica pra próxima;
- Amazon só a cada N rodadas (contador nos contadores das rodadas), e nas
  rodadas puladas as ocorrências Amazon ficam de pé; rodada que quebrou não
  conta como "Amazon conferida";
- candidato dentro da tolerância conta como VISTO (a aberta não fecha só
  porque a âncora avançou); TikTok conta a tolerância de quando saiu do
  ON_HOLD, não do pagamento;
- aberta que a listagem não trouxe: cancelou dentro da janela → sumiu;
  envelheceu (saiu da janela) → confere no Bling e só fecha se achou;
- conta com soluço de rede/cota vira `info` na 1ª rodada e só "sem acesso"
  (pessoa + Threema) na 2ª seguida; erro de credencial avisa na hora;
- Shopee sem detalhe (get_order_detail falhou) não abre nem fecha;
- o tick do worker sai sem rodar quando o robô está `desligado`.

Clients de marketplace e o Bling ao vivo são substituídos por monkeypatch
(as chamadas HTTP já têm teste próprio em test_marketplace_order_listing).
"""
# ruff: noqa: S608
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Integration,
    IntegrationPlatform,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
    OuvidoriaRodada,
    User,
)
from app.security.cipher import encrypt_json
from app.services import ouvidoria as svc
from app.services import vigia_importacao as vigia

pytestmark = pytest.mark.asyncio

ROBO = vigia.ROBO


# ─── fixtures ──────────────────────────────────────────────────────────────


async def _limpar(db: AsyncSession) -> None:
    for tbl in ("ouvidoria_ocorrencias", "ouvidoria_rodadas", "ouvidoria_robos"):
        await db.execute(text(f"DELETE FROM {tbl}"))
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _limpa_ouvidoria(db: AsyncSession):
    await _limpar(db)
    yield
    await _limpar(db)


class _Threema:
    enviados: list[tuple[str, list[str]]] = []

    async def send_to_all(self, texto: str, recipients=None) -> dict:
        self.enviados.append((texto, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}


@pytest.fixture(autouse=True)
def _sem_threema(monkeypatch) -> list[tuple[str, list[str]]]:
    _Threema.enviados = []
    monkeypatch.setattr(svc.threema, "ThreemaClient", _Threema)
    return _Threema.enviados


@pytest_asyncio.fixture
async def user(make_user) -> User:
    return await make_user()


async def _integ(
    db: AsyncSession, user: User, platform: IntegrationPlatform, nome: str
) -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=platform,
        name=nome,
        credentials=encrypt_json(
            {"access_token": "x", "user_id": 7, "shop_id": 1, "expires_at": 9999999999}
        ),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _espelho(db: AsyncSession, numeroloja: str) -> None:
    db.add(
        BlingOrder(
            bling_id=int(uuid.uuid4().int % 10**9),
            numero=numeroloja,
            numeroloja=numeroloja,
            item_index=0,
            item_codigo="sku-x",
        )
    )
    await db.commit()


def _t(**kw) -> datetime:
    return datetime.now(UTC) - timedelta(**kw)


def _cand(plataforma: str, numero: str, *, pago_ha: timedelta | None, **kw) -> vigia.Candidato:
    return vigia.Candidato(
        plataforma=plataforma,
        numero=numero,
        pago_em=(datetime.now(UTC) - pago_ha) if pago_ha is not None else None,
        **kw,
    )


class _Listagem:
    """Substitui `_pedidos_<plataforma>_por_conta`: devolve os candidatos
    configurados pra conta (por rótulo) ou levanta quando a conta está em
    `falhas`. Guarda as chamadas pra afirmar quem foi listado."""

    def __init__(self) -> None:
        self.por_conta: dict[str, list[vigia.Candidato]] = {}
        self.falhas: set[str] = set()
        self.chamadas: list[str] = []

    def lister(self):
        async def _f(client, creds, *, conta, desde, ate, tolerancia):
            self.chamadas.append(conta)
            if conta in self.falhas:
                raise RuntimeError("token vencido (401)")
            out = []
            for c in self.por_conta.get(conta, []):
                c.conta = conta
                out.append(c)
            return out

        return _f


class _Bling:
    """Bling ao vivo falso: `existentes` = números que o Bling já tem;
    `falhar` = fora do ar. Guarda o que foi consultado."""

    def __init__(self, existentes: set[str] | None = None, *, falhar: bool = False) -> None:
        self.existentes = set(existentes or ())
        self.falhar = falhar
        self.consultados: list[list[str]] = []

    async def pedidos_por_numero_loja(self, numeros: list[str]) -> dict[str, dict]:
        self.consultados.append(list(numeros))
        if self.falhar:
            raise RuntimeError("Bling 503")
        return {n: {"numeroLoja": n} for n in numeros if n in self.existentes}


@pytest.fixture
def cenario(monkeypatch):
    """Todas as plataformas com a listagem falsa (uma só, roteada por conta)
    e o Bling ao vivo falso. Devolve (listagem, bling)."""
    listagem = _Listagem()
    bling = _Bling()
    for nome in (
        "_pedidos_ml_por_conta",
        "_pedidos_shopee_por_conta",
        "_pedidos_tiktok_por_conta",
        "_pedidos_amazon_por_conta",
    ):
        monkeypatch.setattr(vigia, nome, listagem.lister())

    async def _cliente_bling(session):
        return bling

    async def _detalhe_ok(client, candidatos):
        return True  # o detalhe da Shopee tem teste próprio; aqui "respondeu"

    monkeypatch.setattr(vigia, "_cliente_bling", _cliente_bling)
    monkeypatch.setattr(vigia, "_detalhar_shopee", _detalhe_ok)
    return listagem, bling


async def _abertas(db: AsyncSession) -> dict[str, OuvidoriaOcorrencia]:
    rows = (
        await db.execute(
            select(OuvidoriaOcorrencia).where(OuvidoriaOcorrencia.fechada_em.is_(None))
        )
    ).scalars()
    return {o.chave: o for o in rows}


async def _todas(db: AsyncSession, chave: str) -> list[OuvidoriaOcorrencia]:
    return list(
        (
            await db.execute(
                select(OuvidoriaOcorrencia)
                .where(OuvidoriaOcorrencia.chave == chave)
                .order_by(OuvidoriaOcorrencia.aberta_em)
            )
        ).scalars()
    )


async def _rodadas(db: AsyncSession) -> list[OuvidoriaRodada]:
    return list(
        (
            await db.execute(
                select(OuvidoriaRodada).order_by(OuvidoriaRodada.iniciada_em)
            )
        ).scalars()
    )


# ─── regras por plataforma (puras) ─────────────────────────────────────────


def _ml(status="paid", tags=(), **extra) -> dict:
    return {
        "id": 2000012345,
        "status": status,
        "tags": list(tags),
        "pack_id": None,
        "total_amount": 739.19,
        "date_created": "2026-09-21T10:00:00.000-03:00",
        "payments": [{"date_approved": "2026-09-21T10:08:00.000-03:00"}],
        "order_items": [{"item": {"seller_sku": "i222.sa"}}],
        **extra,
    }


async def test_regra_ml_so_paid_sem_tag_de_teste_ou_fraude():
    c = vigia.candidato_ml(_ml())
    assert c is not None
    assert c.chave == "ml:2000012345" and c.numeros_bling == ["2000012345"]
    assert c.pago_em == datetime(2026, 9, 21, 13, 8, tzinfo=UTC)
    assert c.valor == 739.19 and c.sku == "i222.sa" and c.status == "paid"

    assert vigia.candidato_ml(_ml(status="cancelled")) is None
    assert vigia.candidato_ml(_ml(status="payment_required")) is None
    assert vigia.candidato_ml(_ml(tags=["test_order"])) is None
    assert vigia.candidato_ml(_ml(tags=["fraud_risk_detected", "paid"])) is None
    assert vigia.candidato_ml(_ml(tags=["paid", "not_delivered"])) is not None


async def test_regra_ml_carrinho_casa_pelo_pedido_ou_pelo_pack():
    c = vigia.candidato_ml(_ml(pack_id=2000099999))
    assert c is not None and c.numeros_bling == ["2000012345", "2000099999"]
    assert c.link == "https://www.mercadolivre.com.br/vendas/2000099999/detalhe"
    # Sem payments aprovados cai no date_created.
    c = vigia.candidato_ml(_ml(payments=[]))
    assert c is not None and c.pago_em == datetime(2026, 9, 21, 13, 0, tzinfo=UTC)


async def test_regra_shopee_unpaid_e_cancelamento_ficam_fora():
    for st in ("READY_TO_SHIP", "PROCESSED", "SHIPPED", "COMPLETED", "INVOICE_PENDING"):
        c = vigia.candidato_shopee({"order_sn": "2509SN1", "order_status": st})
        assert c is not None and c.chave == "shopee:2509SN1" and c.pago_em is None
    for st in ("UNPAID", "CANCELLED", "IN_CANCEL"):
        assert vigia.candidato_shopee({"order_sn": "2509SN1", "order_status": st}) is None
    assert vigia.candidato_shopee({"order_sn": "", "order_status": "SHIPPED"}) is None


async def test_regra_tiktok_on_hold_fica_fora_e_a_tolerancia_conta_do_update_time():
    base = {
        "id": "586188801488290979",
        "create_time": 1758456000,
        "paid_time": 1758456600,
        # Saiu do ON_HOLD ~24 h depois de pago: é daí que o Bling pode importar.
        "update_time": 1758456600 + 24 * 3600,
        "payment": {"total_amount": "129.90"},
        "line_items": [{"seller_sku": "i222.sa"}, {"seller_sku": "i222.sa"}],
    }
    c = vigia.candidato_tiktok({**base, "status": "AWAITING_SHIPMENT"})
    assert c is not None and c.chave == "tiktok:586188801488290979"
    assert c.pago_em == datetime.fromtimestamp(1758456600, tz=UTC)  # o título
    assert c.criado_em == datetime.fromtimestamp(1758456000, tz=UTC)
    assert c.tolerancia_desde == datetime.fromtimestamp(1758456600 + 24 * 3600, tz=UTC)
    assert c.valor == 129.9 and c.sku == "i222.sa"
    assert c.link == "https://seller-br.tiktok.com/order/detail?order_no=586188801488290979"
    for st in ("UNPAID", "ON_HOLD", "CANCELLED"):
        assert vigia.candidato_tiktok({**base, "status": st}) is None
    # Sem paid_time cai no create_time; sem update_time a tolerância conta do
    # pagamento (nunca de antes dele).
    c = vigia.candidato_tiktok(
        {**base, "paid_time": None, "update_time": None, "status": "IN_TRANSIT"}
    )
    assert c is not None and c.pago_em == datetime.fromtimestamp(1758456000, tz=UTC)
    assert c.tolerancia_desde == c.pago_em
    c = vigia.candidato_tiktok({**base, "update_time": 1758456000, "status": "IN_TRANSIT"})
    assert c is not None and c.tolerancia_desde == c.pago_em


async def test_regra_amazon_unshipped_dentro_pending_fora():
    base = {
        "AmazonOrderId": "701-1234567-1234567",
        "PurchaseDate": "2026-09-19T12:00:00Z",
        "LastUpdateDate": "2026-09-21T09:30:00Z",
        "OrderTotal": {"Amount": "199.00", "CurrencyCode": "BRL"},
    }
    for st in ("Unshipped", "PartiallyShipped", "Shipped"):
        c = vigia.candidato_amazon({**base, "OrderStatus": st})
        assert c is not None and c.chave == "amazon:701-1234567-1234567"
        # O título usa a compra (a API não diz quando o pagamento caiu); a
        # tolerância conta da última atualização (Unshipped começa quando a
        # Amazon confirma o pagamento — boleto pode ser dias depois da compra).
        assert c.pago_em == datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
        assert c.criado_em == c.pago_em
        assert c.tolerancia_desde == datetime(2026, 9, 21, 9, 30, tzinfo=UTC)
        assert c.valor == 199.0
    for st in ("Pending", "Canceled", "Unfulfillable", ""):
        assert vigia.candidato_amazon({**base, "OrderStatus": st}) is None
    c = vigia.candidato_amazon({**base, "OrderStatus": "Unshipped", "LastUpdateDate": None})
    assert c is not None and c.tolerancia_desde == datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


async def test_titulo_em_portugues_com_hora_de_brasilia_e_valor():
    c = vigia.Candidato(
        plataforma="ml", numero="1", pago_em=datetime(2026, 9, 21, 17, 8, tzinfo=UTC),
        valor=1739.19,
    )
    assert vigia.titulo_ocorrencia(c) == "Pago 21/09 14:08 (R$ 1.739,19) e não caiu no Bling"
    assert vigia.titulo_ocorrencia(vigia.Candidato(plataforma="shopee", numero="1")) == (
        "Pago e não caiu no Bling"
    )
    detalhe = vigia.detalhe_ocorrencia(
        vigia.Candidato(plataforma="ml", numero="1", conta="Mercado Livre x", pack="9", sku="s"),
        datetime(2026, 9, 21, 17, 8, tzinfo=UTC),
    )
    assert "carrinho 9" in detalhe and "SKU s" in detalhe and "14:08" in detalhe


# ─── listagem por conta (clients falsos) ───────────────────────────────────


async def test_pedidos_ml_por_conta_pagina_e_aplica_a_regra():
    class _Client:
        chamadas: list[dict] = []

        async def search_orders(self, **kw):
            self.chamadas.append(kw)
            if kw["offset"] == 0:
                return {
                    "results": [_ml(), _ml(id=2, status="cancelled")],
                    "paging": {"total": 51},
                }
            return {"results": [_ml(id=3, tags=["test_order"]), _ml(id=4)], "paging": {"total": 51}}

    pedidos = await vigia._pedidos_ml_por_conta(
        _Client(), {"user_id": 7}, conta="Mercado Livre x", desde=_t(hours=72), ate=_t(),
        tolerancia=timedelta(minutes=90),
    )
    assert [c.numero for c in pedidos] == ["2000012345", "4"]
    assert all(c.conta == "Mercado Livre x" for c in pedidos)
    assert [c["offset"] for c in _Client.chamadas] == [0, 50]
    assert _Client.chamadas[0]["seller_id"] == "7"

    with pytest.raises(RuntimeError, match="user_id"):
        await vigia._pedidos_ml_por_conta(
            _Client(), {}, conta="x", desde=_t(hours=1), ate=_t(),
            tolerancia=timedelta(minutes=90),
        )


async def test_pedidos_shopee_por_conta_desconta_a_tolerancia_da_janela():
    """A lista da Shopee não traz hora de pagamento: a janela termina em
    agora − tolerância, então pedido criado há menos de 90 min nem é listado."""
    janelas: list[tuple[int, int]] = []

    class _Client:
        async def iter_orders(self, *, time_from, time_to, **kw):
            janelas.append((time_from, time_to))
            yield {"order_sn": "A", "order_status": "READY_TO_SHIP"}
            yield {"order_sn": "B", "order_status": "UNPAID"}

    ate = _t()
    pedidos = await vigia._pedidos_shopee_por_conta(
        _Client(), {}, conta="Shopee x", desde=ate - timedelta(hours=72), ate=ate,
        tolerancia=timedelta(minutes=90),
    )
    assert [c.numero for c in pedidos] == ["A"]
    assert janelas == [
        (
            int((ate - timedelta(hours=72)).timestamp()),
            int((ate - timedelta(minutes=90)).timestamp()),
        )
    ]


async def test_pedidos_tiktok_e_amazon_por_conta():
    class _TikTok:
        async def iter_orders(self, *, create_time_ge, create_time_lt, **kw):
            yield {"id": "1", "status": "ON_HOLD", "create_time": 1}
            yield {"id": "2", "status": "AWAITING_SHIPMENT", "paid_time": 2}

    class _Amazon:
        kw: dict = {}

        async def iter_orders(self, **kw):
            self.kw.update(kw)
            data = "2026-09-21T00:00:00Z"
            yield {"AmazonOrderId": "A1", "OrderStatus": "Pending", "PurchaseDate": data}
            yield {"AmazonOrderId": "A2", "OrderStatus": "Unshipped", "PurchaseDate": data}

    desde = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    tk = await vigia._pedidos_tiktok_por_conta(
        _TikTok(), {}, conta="TikTok x", desde=desde, ate=_t(), tolerancia=timedelta(minutes=90)
    )
    assert [c.numero for c in tk] == ["2"]
    am = await vigia._pedidos_amazon_por_conta(
        _Amazon(), {}, conta="Amazon x", desde=desde, ate=_t(), tolerancia=timedelta(minutes=90)
    )
    assert [c.numero for c in am] == ["A2"]
    assert _Amazon.kw["created_after"] == "2026-09-18T12:00:00Z"
    assert _Amazon.kw["order_statuses"] == ["PartiallyShipped", "Shipped", "Unshipped"]


async def test_detalhar_shopee_completa_pago_em_valor_e_sku_best_effort():
    class _Client:
        falhar = False

        async def _call(self, method, path, *, params, what):
            assert path == "/api/v2/order/get_order_detail"
            assert params["order_sn_list"] == "A,B"
            if self.falhar:
                raise RuntimeError("shopee error_auth")
            return {
                "order_list": [
                    {"order_sn": "A", "pay_time": 1758456600, "create_time": 1758456000,
                     "total_amount": 88.5,
                     "item_list": [{"model_sku": "m1", "item_sku": "i1"}, {"item_sku": "i2"}]},
                ]
            }

    a = vigia.Candidato(plataforma="shopee", numero="A")
    b = vigia.Candidato(plataforma="shopee", numero="B")
    assert await vigia._detalhar_shopee(_Client(), [a, b]) is True
    assert a.pago_em == datetime.fromtimestamp(1758456600, tz=UTC)
    assert a.criado_em == datetime.fromtimestamp(1758456000, tz=UTC)
    assert a.valor == 88.5 and a.sku == "m1, i2"
    assert b.pago_em is None  # a Shopee não devolveu → segue sem hora
    _Client.falhar = True
    assert await vigia._detalhar_shopee(_Client(), [b]) is False  # não levanta
    assert b.pago_em is None


# ─── a rodada ──────────────────────────────────────────────────────────────


async def test_rodada_abre_so_quem_passou_da_tolerancia_e_nao_esta_no_bling(db, user, cenario):
    listagem, bling = cenario
    ml = await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    conta = "Mercado Livre marquezini"
    listagem.por_conta[conta] = [
        _cand("ml", "111", pago_ha=timedelta(hours=3), valor=739.19, sku="i222.sa"),
        _cand("ml", "222", pago_ha=timedelta(minutes=10)),  # dentro da tolerância
        _cand("ml", "333", pago_ha=timedelta(hours=3)),  # já no espelho
        _cand("ml", "444", pago_ha=timedelta(hours=3)),  # no Bling ao vivo, espelho atrasado
        _cand("ml", "555", pago_ha=timedelta(hours=3), pack="999"),  # pack no espelho
    ]
    await _espelho(db, "333")
    await _espelho(db, "999")
    bling.existentes = {"444"}

    resultado = await vigia.vigia_importacao_run(db)

    abertas = await _abertas(db)
    assert set(abertas) == {"ml:111"}
    o = abertas["ml:111"]
    assert o.plataforma == "ml" and o.conta == conta and o.pedido == "111"
    assert o.titulo.startswith("Pago ") and "(R$ 739,19)" in o.titulo
    assert o.acao == vigia.ACAO_IMPORTAR and o.severidade == "pessoa" and o.precisa_pessoa
    assert o.link == "https://www.mercadolivre.com.br/vendas/111/detalhe"
    assert o.dados["sku"] == "i222.sa" and o.dados["status_plataforma"] is None
    # Só quem passou das duas primeiras peneiras foi ao Bling ao vivo.
    assert bling.consultados == [["111", "444"]]
    assert listagem.chamadas == [conta]
    assert resultado["pedidos"] == 5 and resultado["no_espelho"] == 2
    assert resultado["dentro_tolerancia"] == 1 and resultado["no_bling_vivo"] == 1
    assert resultado["novas"] == 1 and resultado["contas_falha"] == 0
    assert resultado["resumo"] == "5 pedidos conferidos · 1 nova · 0 contas falhou"

    rodadas = await _rodadas(db)
    assert len(rodadas) == 1 and rodadas[0].ok and rodadas[0].contadores["novas"] == 1
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.ultima_rodada_ok is True and robo.ultima_rodada_resumo == resultado["resumo"]
    assert str(ml.id)  # a integração segue intacta (o client foi montado com ela)


async def test_rodada_re_ve_a_ocorrencia_e_fecha_quando_o_pedido_cai_no_bling(db, user, cenario):
    listagem, bling = cenario
    await _integ(db, user, IntegrationPlatform.TIKTOK, "injox")
    conta = "TikTok injox"
    listagem.por_conta[conta] = [
        _cand("tiktok", "586", pago_ha=timedelta(hours=2), status="AWAITING_SHIPMENT")
    ]
    r1 = await vigia.vigia_importacao_run(db)
    assert r1["novas"] == 1
    primeira = (await _abertas(db))["tiktok:586"]
    vista1 = primeira.ultima_vista_em

    # 2ª rodada: ainda falta → mesma linha, só carimba.
    r2 = await vigia.vigia_importacao_run(db)
    assert r2["novas"] == 0 and r2["persistem"] == 1
    await db.refresh(primeira)
    assert primeira.fechada_em is None and primeira.ultima_vista_em > vista1
    assert len(await _todas(db, "tiktok:586")) == 1

    # 3ª rodada: alguém importou (Bling ao vivo tem) → fecha sozinha.
    bling.existentes = {"586"}
    r3 = await vigia.vigia_importacao_run(db)
    assert r3["no_bling_vivo"] == 1 and r3["sumiram"] == 1
    await db.refresh(primeira)
    assert primeira.fechamento == "sumiu" and primeira.fechada_por == "robô"


async def test_pedido_que_saiu_da_listagem_fecha_como_sumiu(db, user, cenario):
    listagem, _bling = cenario
    await _integ(db, user, IntegrationPlatform.SHOPEE, "mega")
    conta = "Shopee mega"
    listagem.por_conta[conta] = [_cand("shopee", "SN1", pago_ha=None, status="READY_TO_SHIP")]
    await vigia.vigia_importacao_run(db)
    assert set(await _abertas(db)) == {"shopee:SN1"}

    # A Shopee cancelou (saiu da listagem) → o robô parou de ver → sumiu.
    listagem.por_conta[conta] = []
    r = await vigia.vigia_importacao_run(db)
    assert r["sumiram"] == 1 and await _abertas(db) == {}
    assert (await _todas(db, "shopee:SN1"))[0].fechamento == "sumiu"


async def test_conta_que_falha_abre_ocorrencia_de_conta_e_nao_fecha_as_dela(db, user, cenario):
    listagem, _bling = cenario
    ml = await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    await _integ(db, user, IntegrationPlatform.ML, "duoker")
    conta_ml, conta_ok = "Mercado Livre marquezini", "Mercado Livre duoker"
    listagem.por_conta[conta_ml] = [_cand("ml", "111", pago_ha=timedelta(hours=3))]
    listagem.por_conta[conta_ok] = [_cand("ml", "222", pago_ha=timedelta(hours=3))]
    await vigia.vigia_importacao_run(db)
    assert set(await _abertas(db)) == {"ml:111", "ml:222"}

    # Token da marquezini venceu: a conta vira ocorrência; o 111 dela FICA.
    listagem.falhas.add(conta_ml)
    r = await vigia.vigia_importacao_run(db)
    abertas = await _abertas(db)
    assert set(abertas) == {"ml:111", "ml:222", f"conta:{ml.id}"}
    conta_oc = abertas[f"conta:{ml.id}"]
    assert conta_oc.titulo == "Conta sem acesso à API" and conta_oc.conta == conta_ml
    assert conta_oc.plataforma == "ml" and conta_oc.acao == vigia.ACAO_REAUTORIZAR
    assert "token vencido" in conta_oc.dados["erro"] and conta_oc.precisa_pessoa
    assert r["contas_falha"] == 1 and r["sumiram"] == 0
    assert "· 1 conta falhou" in r["resumo"]
    rodada = (await _rodadas(db))[-1]
    assert rodada.ok is True  # falha de conta é best-effort, a rodada é ok

    # Conta voltou e o 111 sumiu da listagem: fecha a de conta E o 111.
    listagem.falhas.clear()
    listagem.por_conta[conta_ml] = []
    r = await vigia.vigia_importacao_run(db)
    assert set(await _abertas(db)) == {"ml:222"} and r["sumiram"] == 2


async def test_bling_fora_do_ar_nao_abre_nem_fecha(db, user, cenario, monkeypatch):
    listagem, bling = cenario
    await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    conta = "Mercado Livre marquezini"
    listagem.por_conta[conta] = [_cand("ml", "111", pago_ha=timedelta(hours=3))]
    await vigia.vigia_importacao_run(db)
    assert set(await _abertas(db)) == {"ml:111"}

    listagem.por_conta[conta].append(_cand("ml", "222", pago_ha=timedelta(hours=3)))
    bling.falhar = True
    r = await vigia.vigia_importacao_run(db)
    assert r["bling_falhou"] == 1 and r["novas"] == 0 and r["sumiram"] == 0
    assert set(await _abertas(db)) == {"ml:111"}  # 222 não abriu; 111 não fechou
    assert "Bling não respondeu" in r["resumo"]

    # Sem integração Bling cadastrada = mesma coisa.
    async def _sem_bling(session):
        return None

    monkeypatch.setattr(vigia, "_cliente_bling", _sem_bling)
    r = await vigia.vigia_importacao_run(db)
    assert r["bling_falhou"] == 1 and set(await _abertas(db)) == {"ml:111"}


async def test_teto_de_conferencias_por_rodada_deixa_o_resto_pra_proxima(
    db, user, cenario, monkeypatch
):
    monkeypatch.setattr(vigia, "_MAX_CONFERENCIAS_BLING", 3)
    listagem, bling = cenario
    await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    conta = "Mercado Livre marquezini"
    # 5 vencidos; os mais antigos vão primeiro. O "5" (mais novo) já tinha
    # ocorrência aberta e não pode fechar só porque a rodada não chegou nele.
    listagem.por_conta[conta] = [
        _cand("ml", str(i), pago_ha=timedelta(hours=10 - i)) for i in range(1, 6)
    ]
    await svc.sincronizar_catalogo(db)
    await svc.registrar(db, ROBO, "ml:5", titulo="x", plataforma="ml", conta=conta, pedido="5")
    await db.commit()

    r = await vigia.vigia_importacao_run(db)
    assert bling.consultados == [["1", "2", "3"]]
    assert r["conferidos_bling"] == 3 and r["adiados"] == 2 and r["novas"] == 3
    assert set(await _abertas(db)) == {"ml:1", "ml:2", "ml:3", "ml:5"}
    assert "2 ficaram pra próxima rodada" in r["resumo"]


async def test_dentro_da_tolerancia_conta_como_visto_e_a_aberta_fica(db, user, cenario):
    """Amazon: `LastUpdateDate` (âncora da tolerância) avança a cada mexida
    da Amazon no pedido. A ocorrência aberta não pode fechar como "sumiu" e
    voltar 90 min depois como linha NOVA (furando o re-aviso de 24 h)."""
    listagem, _bling = cenario
    await _integ(db, user, IntegrationPlatform.AMAZON, "KFA")
    conta = "Amazon KFA"
    listagem.por_conta[conta] = [
        _cand("amazon", "701-1", pago_ha=timedelta(hours=30), status="Unshipped",
              importavel_em=_t(hours=3)),
    ]
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.config = {**robo.config, "amazon_a_cada_rodadas": 1}
    await db.commit()
    r1 = await vigia.vigia_importacao_run(db)
    assert r1["novas"] == 1 and set(await _abertas(db)) == {"amazon:701-1"}
    aberta = (await _abertas(db))["amazon:701-1"]
    assert "Pago " in aberta.titulo and aberta.dados["importavel_em"]

    # A Amazon mexeu no pedido há 5 min: dentro da tolerância de novo.
    listagem.por_conta[conta][0].importavel_em = _t(minutes=5)
    r2 = await vigia.vigia_importacao_run(db)
    assert r2["dentro_tolerancia"] == 1 and r2["sumiram"] == 0 and r2["novas"] == 0
    assert set(await _abertas(db)) == {"amazon:701-1"}
    assert len(await _todas(db, "amazon:701-1")) == 1


async def test_tiktok_recem_saido_do_hold_nao_acusa_antes_da_tolerancia(db, user, cenario):
    listagem, bling = cenario
    await _integ(db, user, IntegrationPlatform.TIKTOK, "injox")
    conta = "TikTok injox"
    # Pago ontem, mas só saiu do ON_HOLD (update_time) há 10 min: o Bling
    # ainda não teve como puxar — não é ocorrência.
    listagem.por_conta[conta] = [
        _cand("tiktok", "586", pago_ha=timedelta(hours=24), status="AWAITING_SHIPMENT",
              importavel_em=_t(minutes=10)),
    ]
    r = await vigia.vigia_importacao_run(db)
    assert r["dentro_tolerancia"] == 1 and r["novas"] == 0 and bling.consultados == []
    assert await _abertas(db) == {}

    # Passou a tolerância desde que saiu do hold e continua fora → abre.
    listagem.por_conta[conta][0].importavel_em = _t(hours=2)
    r = await vigia.vigia_importacao_run(db)
    assert r["novas"] == 1 and set(await _abertas(db)) == {"tiktok:586"}


async def test_aberta_que_envelheceu_confere_no_bling_e_so_fecha_se_achou(db, user, cenario):
    """Pedido que NUNCA entrou no Bling sai da janela de 72 h da listagem
    depois de 3 dias — o pior caso do robô não pode virar "Fechou sozinha"."""
    listagem, bling = cenario
    await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    conta = "Mercado Livre marquezini"
    await svc.sincronizar_catalogo(db)
    # Aberta há 3 dias, pedido criado há 4 (fora da janela), com pack.
    velha = await svc.registrar(
        db, ROBO, "ml:111", titulo="Pago e não caiu no Bling", plataforma="ml", conta=conta,
        pedido="111", dados={"pack_id": "999", "criado_em": _t(days=4).isoformat()},
        agora=_t(days=3),
    )
    # Aberta ontem, pedido criado ontem (dentro da janela): a listagem não
    # trouxe → cancelou → sumiu, como sempre.
    await svc.registrar(
        db, ROBO, "ml:222", titulo="Pago e não caiu no Bling", plataforma="ml", conta=conta,
        pedido="222", dados={"criado_em": _t(days=1).isoformat()}, agora=_t(days=1),
    )
    await db.commit()
    vista_antes = velha.ultima_vista_em

    r = await vigia.vigia_importacao_run(db)
    assert r["antigas"] == 1 and r["sumiram"] == 1 and r["persistem"] == 1
    assert bling.consultados == [["111", "999"]]  # pedido + carrinho
    assert set(await _abertas(db)) == {"ml:111"}
    await db.refresh(velha)
    assert velha.ultima_vista_em > vista_antes
    assert (await _todas(db, "ml:222"))[0].fechamento == "sumiu"

    # Bling fora do ar: continua aberta (não conferiu ≠ sumiu).
    bling.falhar = True
    r = await vigia.vigia_importacao_run(db)
    assert r["bling_falhou"] == 1 and set(await _abertas(db)) == {"ml:111"}

    # Alguém importou (o Bling gravou o carrinho): fecha sozinha.
    bling.falhar = False
    bling.existentes = {"999"}
    r = await vigia.vigia_importacao_run(db)
    assert r["no_bling_vivo"] == 1 and r["sumiram"] == 1 and await _abertas(db) == {}

    # Já no espelho: nem vai ao Bling ao vivo.
    await svc.registrar(
        db, ROBO, "ml:333", titulo="x", plataforma="ml", conta=conta, pedido="333",
        dados={"criado_em": _t(days=5).isoformat()}, agora=_t(days=4),
    )
    await db.commit()
    await _espelho(db, "333")
    bling.consultados.clear()
    r = await vigia.vigia_importacao_run(db)
    assert bling.consultados == [] and r["sumiram"] == 1 and await _abertas(db) == {}


async def test_conta_instavel_so_vira_sem_acesso_na_segunda_rodada_seguida(
    db, user, cenario, _sem_threema, monkeypatch
):
    listagem, _bling = cenario
    ml = await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    conta = "Mercado Livre marquezini"
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.threema_recipients = "ABCDEFGH"
    await db.commit()
    chave = f"conta:{ml.id}"

    # 1ª rodada: 503 → só registro (info), ninguém avisado.
    async def _instavel(client, creds, *, conta, desde, ate, tolerancia):
        raise RuntimeError("ml_search_orders status=503 body=upstream")

    monkeypatch.setattr(vigia, "_pedidos_ml_por_conta", _instavel)
    r = await vigia.vigia_importacao_run(db)
    oc = (await _abertas(db))[chave]
    assert r["contas_falha"] == 1 and r["avisadas"] == 0 and _sem_threema == []
    assert oc.severidade == "info" and oc.precisa_pessoa is False and oc.acao is None
    assert oc.titulo.startswith("Conta não respondeu")
    assert oc.dados["falhas_seguidas"] == 1 and oc.dados["erro_tipo"] == "instavel"

    # 2ª rodada seguida falhando: promove pra pessoa e avisa.
    r = await vigia.vigia_importacao_run(db)
    await db.refresh(oc)
    assert oc.titulo == "Conta sem acesso à API" and oc.precisa_pessoa is True
    assert oc.acao == vigia.ACAO_REAUTORIZAR and oc.dados["falhas_seguidas"] == 2
    assert r["avisadas"] == 1 and len(_sem_threema) == 1
    assert len(await _todas(db, chave)) == 1  # mesma linha, promovida

    # Conta voltou: fecha sozinha.
    monkeypatch.setattr(vigia, "_pedidos_ml_por_conta", listagem.lister())
    r = await vigia.vigia_importacao_run(db)
    assert r["sumiram"] == 1 and await _abertas(db) == {}

    # Erro de credencial não espera: pessoa na 1ª rodada.
    listagem.falhas.add(conta)
    r = await vigia.vigia_importacao_run(db)
    oc = (await _abertas(db))[chave]
    assert oc.precisa_pessoa is True and oc.dados["erro_tipo"] == "acesso"
    assert oc.dados["falhas_seguidas"] == 1


async def test_erro_de_acesso_classifica_credencial_vs_instabilidade():
    import httpx

    def _http(status: int) -> httpx.HTTPStatusError:
        resp = httpx.Response(status, request=httpx.Request("GET", "https://x"))
        return httpx.HTTPStatusError("x", request=resp.request, response=resp)

    assert vigia._erro_de_acesso(_http(401)) and vigia._erro_de_acesso(_http(403))
    assert not vigia._erro_de_acesso(_http(429)) and not vigia._erro_de_acesso(_http(503))
    assert vigia._erro_de_acesso(RuntimeError("ml_refresh_failed body=invalid_grant"))
    assert vigia._erro_de_acesso(RuntimeError("shopee_order_list error_auth: Invalid access_token"))
    assert vigia._erro_de_acesso(RuntimeError("missing refresh_token"))
    assert not vigia._erro_de_acesso(RuntimeError("amazon_get_orders_failed status=429 body="))
    assert not vigia._erro_de_acesso(httpx.ReadTimeout("timed out"))
    assert not vigia._erro_de_acesso(RuntimeError("tiktok_search_orders 105000: system busy"))


async def test_shopee_sem_detalhe_nao_abre_nem_fecha(db, user, cenario, monkeypatch):
    listagem, bling = cenario
    await _integ(db, user, IntegrationPlatform.SHOPEE, "mega")
    conta = "Shopee mega"
    listagem.por_conta[conta] = [_cand("shopee", "SN1", pago_ha=None, status="READY_TO_SHIP")]
    await svc.sincronizar_catalogo(db)
    await svc.registrar(db, ROBO, "shopee:SN2", titulo="x", plataforma="shopee", conta=conta,
                        pedido="SN2")
    await db.commit()
    listagem.por_conta[conta].append(_cand("shopee", "SN2", pago_ha=None, status="SHIPPED"))

    async def _falha(client, candidatos):
        return False

    monkeypatch.setattr(vigia, "_detalhar_shopee", _falha)
    r = await vigia.vigia_importacao_run(db)
    # SN1 (pago há 10 min, quem sabe) não abre; SN2 (já aberta) não fecha.
    assert r["shopee_sem_detalhe"] == 2 and r["novas"] == 0 and r["sumiram"] == 0
    assert bling.consultados == [] and set(await _abertas(db)) == {"shopee:SN2"}
    assert "Shopee sem detalhe de 2" in r["resumo"]


async def test_rodada_que_quebrou_nao_conta_como_amazon_conferida(db, user, cenario, monkeypatch):
    listagem, _bling = cenario
    await _integ(db, user, IntegrationPlatform.AMAZON, "KFA")
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.config = {**robo.config, "amazon_a_cada_rodadas": 3}
    await db.commit()

    async def _quebra(session, numeros):
        raise RuntimeError("banco caiu")

    original = vigia._no_espelho
    monkeypatch.setattr(vigia, "_no_espelho", _quebra)
    with pytest.raises(RuntimeError):
        await vigia.vigia_importacao_run(db)
    monkeypatch.setattr(vigia, "_no_espelho", original)
    # A rodada quebrada (depois de listar a Amazon) não vale: a próxima
    # confere a Amazon de novo em vez de pular 2 rodadas.
    r = await vigia.vigia_importacao_run(db)
    assert r["amazon_rodou"] == 1 and listagem.chamadas[-1] == "Amazon KFA"


async def test_amazon_so_a_cada_n_rodadas_e_as_ocorrencias_dela_ficam(db, user, cenario):
    listagem, _bling = cenario
    await _integ(db, user, IntegrationPlatform.AMAZON, "KFA")
    await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    listagem.por_conta["Amazon KFA"] = [
        _cand("amazon", "701-1", pago_ha=timedelta(hours=3), status="Unshipped")
    ]
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.config = {**robo.config, "amazon_a_cada_rodadas": 2}
    await db.commit()

    r1 = await vigia.vigia_importacao_run(db)
    assert r1["amazon_rodou"] == 1 and set(await _abertas(db)) == {"amazon:701-1"}
    assert listagem.chamadas == ["Mercado Livre marquezini", "Amazon KFA"]

    r2 = await vigia.vigia_importacao_run(db)  # pulada: só a ML
    assert r2["amazon_rodou"] == 0 and r2["contas_puladas"] == 1
    assert listagem.chamadas[2:] == ["Mercado Livre marquezini"]
    assert set(await _abertas(db)) == {"amazon:701-1"} and r2["sumiram"] == 0
    assert "Amazon pulada (roda a cada 2)" in r2["resumo"]

    r3 = await vigia.vigia_importacao_run(db)
    assert r3["amazon_rodou"] == 1 and listagem.chamadas[-1] == "Amazon KFA"


async def test_rodada_avisa_no_threema_quando_ligado_e_cala_em_silencioso(
    db, user, cenario, _sem_threema
):
    listagem, _bling = cenario
    await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    listagem.por_conta["Mercado Livre marquezini"] = [
        _cand("ml", "111", pago_ha=timedelta(hours=3))
    ]
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.threema_recipients = "ABCDEFGH"
    robo.modo = "silencioso"
    await db.commit()

    r = await vigia.vigia_importacao_run(db)
    assert r["novas"] == 1 and r["avisadas"] == 0 and _sem_threema == []

    robo.modo = "ligado"
    await db.commit()
    r = await vigia.vigia_importacao_run(db)
    assert r["avisadas"] == 1 and len(_sem_threema) == 1
    texto, quem = _sem_threema[0]
    assert quem == ["ABCDEFGH"]
    assert texto.startswith("Vigia de importação — 1 ocorrência:")
    assert "Mercado Livre marquezini · 111 · Pago" in texto and vigia.ACAO_IMPORTAR in texto


async def test_rodada_com_erro_grava_rodada_falha_e_sobe(db, user, cenario, monkeypatch):
    async def _quebra(session, numeros):
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(vigia, "_no_espelho", _quebra)
    await _integ(db, user, IntegrationPlatform.ML, "marquezini")
    with pytest.raises(RuntimeError, match="banco caiu"):
        await vigia.vigia_importacao_run(db)
    rodada = (await _rodadas(db))[-1]
    assert rodada.ok is False and "banco caiu" in (rodada.erro or "")
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.ultima_rodada_ok is False


async def test_sweep_e_serializado_pelo_advisory_lock(db, cenario, monkeypatch):
    """Sessão que segura o lock do sweep → o outro sweep sai na hora."""
    from app.services.advisory_lock import SYNC_NAMESPACE

    got = (
        await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_NAMESPACE, "key": vigia._SWEEP_LOCK_KEY},
        )
    ).scalar()
    assert got
    assert await vigia.vigia_importacao_sweep() == {"skipped": "lock_busy"}
    await db.rollback()  # solta o lock

    chamado = []

    async def _run(session):
        chamado.append(1)
        return {"ok": True}

    monkeypatch.setattr(vigia, "vigia_importacao_run", _run)
    assert await vigia.vigia_importacao_sweep() == {"ok": True} and chamado == [1]


# ─── tick do worker ────────────────────────────────────────────────────────


async def test_tick_nao_roda_com_o_robo_desligado(db, monkeypatch):
    from app import worker

    chamadas: list[int] = []

    async def _sweep():
        chamadas.append(1)
        return {"novas": 0}

    monkeypatch.setattr(worker, "vigia_importacao_sweep", _sweep)
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.modo = "desligado"
    await db.commit()
    await worker.vigia_importacao_tick({})
    assert chamadas == [] and await _rodadas(db) == []

    for modo in ("silencioso", "ligado"):
        robo.modo = modo
        await db.commit()
        await worker.vigia_importacao_tick({})
    assert chamadas == [1, 1]


async def test_startup_do_worker_sincroniza_o_catalogo(db, monkeypatch):
    from app import worker

    monkeypatch.setattr("app.services.sentry.init_sentry", lambda **kw: None)
    assert await db.get(OuvidoriaRobo, ROBO) is None
    await worker.startup({})
    db.expire_all()
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo is not None and robo.nome == "Vigia de importação"
