"""Conferência do preço vivo na Amazon depois do envio da Tabela de Preços.

Eduardo (16/09/2026): enviou R$ 445, o DaVinci disse "ok", a Amazon guardou 445
no atributo e continuou vendendo a R$ 599. Lendo a família inteira: 32
anúncios divergentes, todos exatamente os que já tinham sido enviados. O envio
não consegue forçar a Amazon; o que ele não pode é ficar calado.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.models import PricingPushConfirmacao, PricingPushIdempotency
from app.services import pricing_confirmacao_amazon as conf

pytestmark = pytest.mark.asyncio


def test_parse_key_produto_vem_primeiro_na_chave():
    """`cell:{produto}:{conta}:{ts}` — a ordem inversa foi o bug do primeiro
    tick em produção (vistos=0)."""
    conta, produto = uuid4(), uuid4()
    assert conf.parse_key(f"cell:{produto}:{conta}:1789572929233") == (conta, produto)
    assert conf.parse_key("cell:nao-e-uuid:x:1") is None
    assert conf.parse_key("outra:coisa") is None
    assert conf.parse_key("") is None


def test_diverge_compara_em_reais_inteiros():
    # A Amazon BR só aceita reais inteiros; o envio arredonda antes.
    assert conf.diverge(445, 599.0) is True
    assert conf.diverge(445, 445.0) is False
    assert conf.diverge(444.6, 445.0) is False
    # Sem leitura não é divergência (é "sem_leitura").
    assert conf.diverge(445, None) is False


def test_texto_do_aviso_lista_os_anuncios_e_o_motivo_provavel():
    t = conf._texto_aviso("kfa", 445, [{"sku": "b111.20+a075", "vivo": 599.0}])
    assert "kfa" in t and "445" in t and "b111.20+a075" in t and "599" in t
    assert "regra de precificação automática" in t.lower()


async def _push(db, user, *, preco: str, links: list[str], minutos_atras: int = 30):
    key = f"cell:{uuid4()}:{uuid4()}:{int(datetime.now(UTC).timestamp()*1000)}"
    row = PricingPushIdempotency(
        key=key,
        user_id=user.id,
        request_hash="x",
        response={
            "ok": True, "code": "ok", "price": preco,
            "payload": {"links": [{"externalId": s, "success": True} for s in links]},
        },
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db.add(row)
    await db.commit()
    await db.execute(
        text(
            "UPDATE pricing_push_idempotency "
            "SET created_at = now() - make_interval(mins => :m) WHERE key = :k"
        ),
        {"m": minutos_atras, "k": key},
    )
    await db.commit()
    return key


class _FakeAmazon:
    def __init__(self, precos: dict[str, float | None]):
        self.precos = precos

    async def get_listing_price(self, link):
        return self.precos.get(link.external_id)


def _arma(monkeypatch, precos: dict[str, float | None]):
    integ = SimpleNamespace(id=uuid4(), name="kfa", credentials=b"x")

    async def fake_integ(session, account_id):
        return integ

    async def fake_links(session, integration_id, external_ids):
        return {e: SimpleNamespace(external_id=e, external_sku=None) for e in external_ids}

    monkeypatch.setattr(conf, "_integracao_amazon_da_conta", fake_integ)
    monkeypatch.setattr(conf, "_links_por_external_id", fake_links)
    monkeypatch.setattr(conf.get_settings(), "nf_sem_estoque_threema_recipients", "", raising=False)
    return lambda _i: _FakeAmazon(precos)


async def _limpa(db):
    await db.execute(text("DELETE FROM pricing_push_confirmacao"))
    await db.execute(text("DELETE FROM pricing_push_idempotency"))
    await db.commit()


async def test_envio_aplicado_vira_confirmado(db, make_user, monkeypatch):
    await _limpa(db)
    user = await make_user()
    key = await _push(db, user, preco="445", links=["b111", "b112"])
    fab = _arma(monkeypatch, {"b111": 445.0, "b112": 445.0})

    r = await conf.confirmar_pushes_recentes(db, client_factory=fab)

    assert r["confirmados"] == 1 and r["divergentes"] == 0
    row = (
        await db.execute(
            select(PricingPushConfirmacao).where(PricingPushConfirmacao.push_key == key)
        )
    ).scalar_one()
    assert row.status == "confirmado" and row.conferidos == 2 and row.divergentes == []


async def test_envio_nao_aplicado_vira_divergente_e_avisa(db, make_user, monkeypatch):
    """O caso real: atributo 445, loja 599."""
    await _limpa(db)
    user = await make_user()
    key = await _push(db, user, preco="445", links=["b111", "b112", "b113"])
    fab = _arma(monkeypatch, {"b111": 599.0, "b112": 445.0, "b113": 599.0})

    avisos: list[dict] = []

    async def fake_alert(session, **kw):
        avisos.append(kw)
        return None

    monkeypatch.setattr(conf, "emit_alert", fake_alert)

    r = await conf.confirmar_pushes_recentes(db, client_factory=fab)

    assert r["divergentes"] == 1
    row = (
        await db.execute(
            select(PricingPushConfirmacao).where(PricingPushConfirmacao.push_key == key)
        )
    ).scalar_one()
    assert row.status == "divergente"
    assert {d["sku"] for d in row.divergentes} == {"b111", "b113"}
    assert len(avisos) == 1
    conta, produto = conf.parse_key(key)
    # Dedupe é por célula e preço, não por envio: clicar 3x não gera 3 avisos.
    assert avisos[0]["dedupe_key"] == f"pricing_confirmacao:{conta}:{produto}:445"
    assert "599" in avisos[0]["message"]


async def test_sem_leitura_nao_vira_divergente(db, make_user, monkeypatch):
    await _limpa(db)
    user = await make_user()
    key = await _push(db, user, preco="445", links=["b111"])
    fab = _arma(monkeypatch, {"b111": None})

    r = await conf.confirmar_pushes_recentes(db, client_factory=fab)

    assert r["sem_leitura"] == 1 and r["divergentes"] == 0
    row = (
        await db.execute(
            select(PricingPushConfirmacao).where(PricingPushConfirmacao.push_key == key)
        )
    ).scalar_one()
    assert row.status == "sem_leitura"


async def test_nao_confere_duas_vezes_nem_cedo_demais(db, make_user, monkeypatch):
    await _limpa(db)
    user = await make_user()
    await _push(db, user, preco="445", links=["b111"], minutos_atras=1)  # cedo demais
    key = await _push(db, user, preco="445", links=["b111"], minutos_atras=30)
    fab = _arma(monkeypatch, {"b111": 445.0})

    r1 = await conf.confirmar_pushes_recentes(db, client_factory=fab)
    r2 = await conf.confirmar_pushes_recentes(db, client_factory=fab)

    assert r1["vistos"] == 1 and r2["vistos"] == 0
    n = (await db.execute(select(PricingPushConfirmacao))).scalars().all()
    assert [x.push_key for x in n] == [key]
