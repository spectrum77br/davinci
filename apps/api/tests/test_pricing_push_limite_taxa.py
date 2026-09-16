"""Envio de preço não pode parar no meio quando o marketplace limita a taxa.

Eduardo (16/09/2026): "não pode ser parcial tem que ser completamente". Ele
enviou o preço de uma coluna e voltou "partial R$ 110,00 — 96/136 variations
ok; last error: http_429". As outras 40 variações ficaram com o preço velho, em
silêncio.

O cliente da Amazon já classificava o 429 como RETRYABLE; quem ignorava era o
envio, que tratava tudo que não fosse OK como falha definitiva e só fazia uma
passada.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.marketplaces.base import SyncStatus
from app.services.pricing import push as P

pytestmark = pytest.mark.asyncio


def _link(nome: str):
    return SimpleNamespace(
        id=uuid4(), external_id=nome, variation_id=None, product_id=uuid4(), listing_type=None
    )


def _res(status: SyncStatus, code: str | None = None):
    return SimpleNamespace(status=status, error_code=code, error_detail=None, qty_before=None)


@pytest.fixture(autouse=True)
def _sem_espera(monkeypatch):
    """Não segura o teste nas esperas reais entre as rodadas."""
    async def _dorme(_s):
        return None

    monkeypatch.setattr(P.asyncio, "sleep", _dorme)
    monkeypatch.setattr(P, "_reclassify_skipped", lambda r: r)


async def test_anuncio_que_recusou_por_limite_e_reenviado_ate_passar(monkeypatch):
    links = [_link("a"), _link("b"), _link("c")]
    tentativas: dict[str, int] = {}

    async def fake(client, platform, link, preco, *, product_sku=None):
        n = tentativas[link.external_id] = tentativas.get(link.external_id, 0) + 1
        # "b" só aceita na terceira tentativa.
        if link.external_id == "b" and n < 3:
            return _res(SyncStatus.RETRYABLE, "http_429")
        return _res(SyncStatus.OK)

    monkeypatch.setattr(P, "_dispatch_price_update_link", fake)
    r = await P.enviar_preco_para_links(None, None, links, 110.0, sku_by_product={})

    assert all(r[lk.id].status == SyncStatus.OK for lk in links)
    assert tentativas["b"] == 3
    # Quem já passou não é reenviado.
    assert tentativas["a"] == 1 and tentativas["c"] == 1


async def test_erro_de_verdade_nao_e_reenviado(monkeypatch):
    """Preço inválido ou anúncio encerrado não melhora com insistência — e
    reenviar gastaria a cota que o limitado precisa."""
    links = [_link("a")]
    tentativas = {"n": 0}

    async def fake(client, platform, link, preco, *, product_sku=None):
        tentativas["n"] += 1
        return _res(SyncStatus.FATAL, "amazon_auth_403")

    monkeypatch.setattr(P, "_dispatch_price_update_link", fake)
    r = await P.enviar_preco_para_links(None, None, links, 110.0, sku_by_product={})

    assert tentativas["n"] == 1
    assert r[links[0].id].status == SyncStatus.FATAL


async def test_desiste_depois_das_rodadas_e_devolve_o_ultimo_resultado(monkeypatch):
    """Se o marketplace nunca abrir, o envio termina — não fica preso — e o
    resultado continua honesto (parcial), em vez de mentir que deu certo."""
    links = [_link("a")]
    tentativas = {"n": 0}

    async def fake(client, platform, link, preco, *, product_sku=None):
        tentativas["n"] += 1
        return _res(SyncStatus.RETRYABLE, "http_429")

    monkeypatch.setattr(P, "_dispatch_price_update_link", fake)
    r = await P.enviar_preco_para_links(None, None, links, 110.0, sku_by_product={})

    assert tentativas["n"] == len(P._ESPERAS_LIMITE_S) + 1
    assert r[links[0].id].status == SyncStatus.RETRYABLE


async def test_todo_anuncio_recebe_resultado(monkeypatch):
    """O laço de fora indexa por link.id: faltar uma chave quebraria o envio."""
    links = [_link(f"v{i}") for i in range(12)]

    async def fake(client, platform, link, preco, *, product_sku=None):
        return _res(SyncStatus.OK) if int(link.external_id[1:]) % 2 else _res(
            SyncStatus.RETRYABLE, "http_429"
        )

    monkeypatch.setattr(P, "_dispatch_price_update_link", fake)
    r = await P.enviar_preco_para_links(None, None, links, 110.0, sku_by_product={})

    assert set(r.keys()) == {lk.id for lk in links}
