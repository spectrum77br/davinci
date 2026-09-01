"""Situação do pedido a partir das devoluções — regras novas:

* "Não devolvido" (legado) é neutro como "Entregue": não trava o "todos
  resolvidos" (erro 3 do Eduardo);
* transição direta rejeitada faz DESVIO por Aguardando Devolução (83957) e
  reaplica o alvo (erros 1 e 2 — Extraviado e Manutenção a partir de
  Resolvido);
* se até o desvio for recusado (4xx), alerta o operador (sino/Telegram);
* "mesma situação" (400 código 50) é sucesso idempotente, sem desvio.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import app.services.devolution_stock_return as dsr
from app.services.devolution_stock_return import (
    SITUACAO_AGUARDANDO_DEVOLUCAO,
    SITUACAO_EXTRAVIADO,
    SITUACAO_MANUTENCAO,
    SITUACAO_RESOLVIDO,
    _is_same_situacao_error,
    _order_situacao_target,
    apply_order_situacao,
)


def _row(condicao: str, destino: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(condicao_produto=condicao, manutencao_destino=destino)


# ── _order_situacao_target ────────────────────────────────────────────────


def test_target_nao_devolvido_e_neutro_nao_trava_resolvido():
    # Erro 3: linha legada "Não devolvido" não pode impedir o Resolvido.
    rows = [_row("Não devolvido"), _row("Novo")]
    assert _order_situacao_target(rows) == SITUACAO_RESOLVIDO


def test_target_somente_neutros_nao_patcha():
    assert _order_situacao_target([_row("Entregue"), _row("Não devolvido")]) is None


def test_target_extraviado_tem_precedencia():
    rows = [_row("Extraviado"), _row("Novo"), _row("Não devolvido")]
    assert _order_situacao_target(rows) == SITUACAO_EXTRAVIADO


def test_target_manutencao_pendente():
    rows = [_row("Manutenção"), _row("Novo")]
    assert _order_situacao_target(rows) == SITUACAO_MANUTENCAO


def test_target_manutencao_com_destino_resolve():
    rows = [_row("Manutenção", "Usado"), _row("Não devolvido")]
    assert _order_situacao_target(rows) == SITUACAO_RESOLVIDO


def test_target_condicao_vazia_continua_travando():
    # Linha sem condição escolhida ainda segura o pedido (não é neutra).
    assert _order_situacao_target([_row(""), _row("Novo")]) is None


# ── _is_same_situacao_error ───────────────────────────────────────────────


def test_same_situacao_error_detect():
    assert _is_same_situacao_error(400, "A venda possui a mesma situação.")
    assert not _is_same_situacao_error(400, "transição inválida")
    assert not _is_same_situacao_error(422, "A venda possui a mesma situação.")


# ── apply_order_situacao (fakes) ──────────────────────────────────────────


def _http_error(status: int, body: str) -> httpx.HTTPStatusError:
    req = httpx.Request("PATCH", "https://api.bling.com.br/x")
    resp = httpx.Response(status, request=req, text=body)
    return httpx.HTTPStatusError(body, request=req, response=resp)


class _FakeResult:
    def __init__(self, *, rows=None, first=None):
        self._rows = rows or []
        self._first = first

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._first


class _FakeSession:
    """1ª execute → devoluções; 2ª → pedido (bling_id/situacao)."""

    def __init__(self, dev_rows, order_row):
        self._queue = [
            _FakeResult(rows=dev_rows),
            _FakeResult(first=order_row),
        ]
        self.committed = 0

    async def execute(self, *_a, **_k):
        return self._queue.pop(0)

    async def commit(self):
        self.committed += 1


class _FakeClient:
    def __init__(self, outcomes):
        # outcomes: lista por chamada — exceção para levantar ou None p/ ok.
        self.outcomes = list(outcomes)
        self.calls: list[tuple[int, int]] = []

    async def update_order_situacao(self, bling_id: int, situacao: int) -> None:
        self.calls.append((bling_id, situacao))
        out = self.outcomes.pop(0) if self.outcomes else None
        if out is not None:
            raise out


@pytest.fixture
def patched(monkeypatch):
    audits: list[dict] = []
    alerts: list[dict] = []

    async def fake_audit(_session, **kw):
        audits.append(kw)

    async def fake_alert(_session, **kw):
        alerts.append(kw)

    monkeypatch.setattr(dsr, "record_margem_audit", fake_audit)
    monkeypatch.setattr(dsr, "emit_alert", fake_alert)
    return audits, alerts


def _wire_client(monkeypatch, client: _FakeClient) -> None:
    async def fake_get_client(_session):
        return client

    monkeypatch.setattr(dsr, "_get_bling_client", fake_get_client)


ORDER = SimpleNamespace(bling_id=111, situacao="84677")


async def test_apply_desvio_extraviado(monkeypatch, patched):
    # Erro 1: Bling rejeita →Extraviado direto → desvio 83957 e reaplica.
    audits, alerts = patched
    client = _FakeClient([_http_error(400, "transição de situação inválida"), None, None])
    _wire_client(monkeypatch, client)
    session = _FakeSession([_row("Extraviado")], ORDER)

    res = await apply_order_situacao(session, "273745", actor_id=uuid4())

    assert [s for _, s in client.calls] == [
        SITUACAO_EXTRAVIADO, SITUACAO_AGUARDANDO_DEVOLUCAO, SITUACAO_EXTRAVIADO,
    ]
    assert res["ok"] and res["action"] == "situacao_patched"
    assert audits[0]["valor_novo"] == SITUACAO_EXTRAVIADO
    assert alerts == []


async def test_apply_desvio_manutencao(monkeypatch, patched):
    # Erro 2: pedido já Resolvido no Bling → desvio traz de volta p/ Manutenção.
    audits, alerts = patched
    client = _FakeClient([_http_error(400, "transição de situação inválida"), None, None])
    _wire_client(monkeypatch, client)
    session = _FakeSession([_row("Manutenção")], SimpleNamespace(bling_id=222, situacao="545902"))

    res = await apply_order_situacao(session, "288575", actor_id=uuid4())

    assert [s for _, s in client.calls] == [
        SITUACAO_MANUTENCAO, SITUACAO_AGUARDANDO_DEVOLUCAO, SITUACAO_MANUTENCAO,
    ]
    assert res["ok"] and audits[0]["valor_novo"] == SITUACAO_MANUTENCAO
    assert alerts == []


async def test_apply_mesma_situacao_e_sucesso_idempotente(monkeypatch, patched):
    audits, alerts = patched
    client = _FakeClient([_http_error(400, "A venda possui a mesma situação.")])
    _wire_client(monkeypatch, client)
    session = _FakeSession([_row("Novo")], ORDER)

    res = await apply_order_situacao(session, "111111", actor_id=uuid4())

    assert res["ok"] and res["action"] == "situacao_unchanged"
    assert len(client.calls) == 1  # sem desvio inútil
    assert audits == [] and alerts == []


async def test_apply_desvio_rejeitado_alerta_manual(monkeypatch, patched):
    # Direto E desvio recusados (4xx) → alerta sino/Telegram p/ ajuste manual.
    audits, alerts = patched
    client = _FakeClient([
        _http_error(400, "transição de situação inválida"),
        _http_error(400, "transição de situação inválida"),
    ])
    _wire_client(monkeypatch, client)
    session = _FakeSession([_row("Manutenção")], SimpleNamespace(bling_id=222, situacao="545902"))
    actor = uuid4()

    res = await apply_order_situacao(session, "288575", actor_id=actor)

    assert res["ok"] is False and res["action"] == "situacao_error"
    assert len(alerts) == 1
    assert alerts[0]["dedupe_key"] == f"devolucao_situacao_manual:288575:{SITUACAO_MANUTENCAO}"
    assert alerts[0]["user_id"] == actor
    assert audits == []


async def test_apply_desvio_rejeitado_sem_actor_nao_alerta(monkeypatch, patched):
    audits, alerts = patched
    client = _FakeClient([
        _http_error(400, "transição de situação inválida"),
        _http_error(400, "transição de situação inválida"),
    ])
    _wire_client(monkeypatch, client)
    session = _FakeSession([_row("Manutenção")], ORDER)

    res = await apply_order_situacao(session, "288575", actor_id=None)

    assert res["ok"] is False and alerts == [] and audits == []


async def test_apply_sucesso_normal_audita_aplicado(monkeypatch, patched):
    audits, _alerts = patched
    client = _FakeClient([None])
    _wire_client(monkeypatch, client)
    session = _FakeSession([_row("Não devolvido"), _row("Usado")], ORDER)

    res = await apply_order_situacao(session, "245398", actor_id=uuid4())

    assert client.calls == [(111, SITUACAO_RESOLVIDO)]
    assert res["ok"] and audits[0]["valor_novo"] == SITUACAO_RESOLVIDO


async def test_apply_todos_neutros_nao_chama_bling(monkeypatch, patched):
    client = _FakeClient([])
    _wire_client(monkeypatch, client)
    session = _FakeSession([_row("Entregue"), _row("Não devolvido")], ORDER)

    res = await apply_order_situacao(session, "999999", actor_id=uuid4())

    assert res is None and client.calls == []


async def test_apply_erro_rede_nao_alerta(monkeypatch, patched):
    # 5xx/timeout: retry natural em interação futura; alerta só p/ rejeição 4xx.
    audits, alerts = patched
    client = _FakeClient([_http_error(500, "internal error"), _http_error(500, "internal error")])
    _wire_client(monkeypatch, client)
    session = _FakeSession([_row("Extraviado")], ORDER)

    res = await apply_order_situacao(session, "260987", actor_id=uuid4())

    assert res["ok"] is False and res["action"] == "situacao_error"
    assert alerts == [] and audits == []
