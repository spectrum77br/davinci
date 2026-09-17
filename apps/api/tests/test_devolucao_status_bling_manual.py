# ruff: noqa: E501
"""Coluna "Status Bling" da aba Lançamentos (Vinicius 17/09): mudar a situação
do pedido no Bling na mão — o lançamento sozinho só mexe nela em Extraviado /
Sucata / Manutenção / Novo-Usado-Trocado, e o 292022 (lançado como "Não
devolvido") ficou preso em Aguardando Devolução, logo na aba Fraude."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


from app.routers import devolutions as router

pytestmark = pytest.mark.asyncio


def _perms() -> dict:
    return {"devolucoes": {"view": True, "edit": True, "delete": True}}


class _FakeBling:
    def __init__(self, *, recusa: dict[tuple[int, int], int] | None = None, recusa_vezes: int | None = None):
        self.calls: list[tuple[int, int]] = []
        self.recusa = recusa or {}  # (bling_id, situacao) → status HTTP recusado
        self.recusa_vezes = recusa_vezes  # None = sempre; N = só as N primeiras recusas

    async def update_order_situacao(self, bling_id: int, situacao_id: int) -> None:
        self.calls.append((bling_id, situacao_id))
        st = self.recusa.get((bling_id, situacao_id))
        if st and (self.recusa_vezes is None or self.recusa_vezes > 0):
            if self.recusa_vezes is not None:
                self.recusa_vezes -= 1
            req = httpx.Request("PATCH", "https://api.bling.com.br/x")
            body = '{"error":{"type":"VALIDATION_ERROR","message":"transição inválida"}}'
            raise httpx.HTTPStatusError("recusou", request=req, response=httpx.Response(st, text=body, request=req))


async def _seed(db: AsyncSession, pedido: str, situacao: str = "83957") -> int:
    bling_id = int(uuid4().int % 10**9)
    await db.execute(
        text("INSERT INTO bling_orders (id, numero, bling_id, situacao, data) VALUES (:i, :n, :b, :s, :d)"),
        {"i": uuid4(), "n": pedido, "b": bling_id, "s": situacao, "d": datetime.now(UTC)},
    )
    for sid, nome, ativo in ((83957, "Aguardando Devolução", True), (545902, "Resolvido", True), (83953, "Entregue", True), (84686, "Golpe", False)):
        await db.execute(
            text("INSERT INTO situacao_bling (id, nome, ativo) VALUES (:i, :n, :a) ON CONFLICT (id) DO UPDATE SET nome = :n, ativo = :a"),
            {"i": sid, "n": nome, "a": ativo},
        )
    await db.commit()
    return bling_id


async def test_lista_so_situacoes_ativas(client, db, make_user, auth_as):
    auth_as(await make_user(permissions=_perms()))
    await _seed(db, f"5{uuid4().hex[:6]}")
    r = await client.get("/api/devolutions/situacoes-bling")
    assert r.status_code == 200
    nomes = {i["nome"] for i in r.json()["items"]}
    assert {"Aguardando Devolução", "Resolvido", "Entregue"} <= nomes
    assert "Golpe" not in nomes  # apagada no Bling


async def test_muda_no_bling_espelha_local_e_audita(client, db, make_user, auth_as, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    pedido = f"5{uuid4().hex[:6]}"
    bling_id = await _seed(db, pedido)
    fake = _FakeBling()

    async def _client(session):
        return fake

    monkeypatch.setattr(router, "_get_bling_client", _client)

    r = await client.post(f"/api/devolutions/pedido/{pedido}/situacao-bling", json={"situacao_id": 545902})
    assert r.status_code == 200, r.text
    assert r.json() == {"pedido_bling": pedido, "situacao_id": 545902, "situacao_nome": "Resolvido", "via_desvio": False}
    assert fake.calls == [(bling_id, 545902)]
    # Espelho local na hora: as abas Acompanhamento/Fraude leem daqui.
    assert (await db.execute(text("SELECT situacao FROM bling_orders WHERE numero = :n"), {"n": pedido})).scalar_one() == "545902"
    aud = (await db.execute(text("SELECT valor_antigo, valor_novo, origem, mudado_por FROM margem_audit WHERE pedido_bling = :n AND acao = 'situacao'"), {"n": pedido})).one()
    assert (aud.valor_antigo, aud.valor_novo, aud.origem, str(aud.mudado_por)) == ("83957", "545902", "devolucoes_manual", str(user.id))

    # Situação inexistente/apagada → 422; pedido desconhecido → 404.
    r = await client.post(f"/api/devolutions/pedido/{pedido}/situacao-bling", json={"situacao_id": 84686})
    assert r.status_code == 422
    r = await client.post("/api/devolutions/pedido/000000/situacao-bling", json={"situacao_id": 545902})
    assert r.status_code == 404


async def test_bling_recusa_direto_passa_pelo_desvio(client, db, make_user, auth_as, monkeypatch):
    """Caminhos de mão única do Bling: Entregue→Resolvido recusado na primeira,
    então vai por Aguardando Devolução e depois o alvo — e a tela fica sabendo."""
    auth_as(await make_user(permissions=_perms()))
    pedido = f"5{uuid4().hex[:6]}"
    bling_id = await _seed(db, pedido, situacao="83953")  # hoje: Entregue
    fake = _FakeBling(recusa={(bling_id, 545902): 400}, recusa_vezes=1)

    async def _client(session):
        return fake

    monkeypatch.setattr(router, "_get_bling_client", _client)

    r = await client.post(f"/api/devolutions/pedido/{pedido}/situacao-bling", json={"situacao_id": 545902})
    assert r.status_code == 200, r.text
    assert r.json()["via_desvio"] is True
    assert fake.calls == [(bling_id, 545902), (bling_id, 83957), (bling_id, 545902)]
    assert (await db.execute(text("SELECT situacao FROM bling_orders WHERE numero = :n"), {"n": pedido})).scalar_one() == "545902"


async def test_bling_recusa_tudo_reporta_o_motivo_e_nao_mexe_local(client, db, make_user, auth_as, monkeypatch):
    auth_as(await make_user(permissions=_perms()))
    pedido = f"5{uuid4().hex[:6]}"
    bling_id = await _seed(db, pedido, situacao="83953")
    fake = _FakeBling(recusa={(bling_id, 545902): 400})  # recusa SEMPRE

    async def _client(session):
        return fake

    monkeypatch.setattr(router, "_get_bling_client", _client)

    r = await client.post(f"/api/devolutions/pedido/{pedido}/situacao-bling", json={"situacao_id": 545902})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bling_recusou_situacao"
    assert "transi" in r.json()["detail"]["message"]
    assert fake.calls == [(bling_id, 545902), (bling_id, 83957), (bling_id, 545902)]
    assert (await db.execute(text("SELECT situacao FROM bling_orders WHERE numero = :n"), {"n": pedido})).scalar_one() == "83953"

    # Já em Aguardando Devolução: não há desvio a fazer — recusa direta, uma chamada só.
    pedido2 = f"5{uuid4().hex[:6]}"
    bling2 = await _seed(db, pedido2, situacao="83957")
    fake.recusa = {(bling2, 545902): 400}
    r = await client.post(f"/api/devolutions/pedido/{pedido2}/situacao-bling", json={"situacao_id": 545902})
    assert r.status_code == 400
    assert fake.calls[-1:] == [(bling2, 545902)]


async def test_listagem_e_busca_trazem_a_situacao_atual(client, db, make_user, auth_as):
    auth_as(await make_user(permissions=_perms()))
    pedido = f"5{uuid4().hex[:6]}"
    await _seed(db, pedido, situacao="83957")
    r = await client.post("/api/devolutions", json={"pedido_bling": pedido, "conta": "Loja", "sku": "a001", "produtos": "Fone", "condicao_produto": "Não devolvido", "quantidade": 1})
    assert r.status_code == 201, r.text
    assert (r.json()["situacao_bling_id"], r.json()["situacao_bling_nome"]) == (83957, "Aguardando Devolução")
    r = await client.get("/api/devolutions", params={"search": pedido})
    item = next(i for i in r.json()["items"] if i["pedido_bling"] == pedido)
    assert (item["situacao_bling_id"], item["situacao_bling_nome"]) == (83957, "Aguardando Devolução")
