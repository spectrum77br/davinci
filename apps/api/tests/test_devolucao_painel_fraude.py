# ruff: noqa: E501
"""Abas Acompanhamento × Fraude da tela Devoluções (Vinicius 17/09).

"na aba Acompanhamento uma pessoa cuida só dos pedidos que foram devolvidos e
que vão devolver; na aba Fraude é outra pessoa, ela cuida dos pedidos que o
cliente alega que chegou vazio, que pede reembolso sem devolução". Regra
automática = tipo do caso (REFUND = não volta pacote → Fraude); o botão da
tela fixa o painel na mão (`fila_manual`) e a regra não mexe mais.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DevolucaoRastreio, Logistica, UserRole
from app.routers.devolutions import _fila, _tipo_do_painel
from app.services import logistica_meli, logistica_rules, logistica_shopee

pytestmark = pytest.mark.asyncio


def test_regra_automatica_e_manual_do_painel():
    assert _fila(None, "REFUND") == ("fraude", False)
    assert _fila(None, "RETURN_AND_REFUND") == ("acompanhamento", False)
    assert _fila(None, None) == ("acompanhamento", False)  # sem caso conhecido
    assert _fila("fraude", "RETURN_AND_REFUND") == ("fraude", True)  # movido na mão
    assert _fila("acompanhamento", "REFUND") == ("acompanhamento", True)
    assert _fila("lixo", "REFUND") == ("fraude", False)  # valor inválido = automático


def test_shopee_needs_logistics_vira_tipo_do_caso():
    # Medido 17/09: ACCEPTED "NOT_RECEIPT" sem pacote → só dinheiro (Fraude).
    so_dinheiro = logistica_shopee._return_info({"status": "ACCEPTED", "needs_logistics": False, "refund_amount": 525, "update_time": 1}, "ACCEPTED")
    assert so_dinheiro.return_type == "REFUND" and so_dinheiro.tracking is None
    com_pacote = logistica_shopee._return_info({"status": "PROCESSING", "needs_logistics": True, "tracking_number": "AP456427997BR"}, "PROCESSING")
    assert com_pacote.return_type == "RETURN_AND_REFUND"
    assert logistica_shopee._return_info({"status": "REQUESTED"}, "REQUESTED").return_type is None


def test_ml_mediacao_sem_devolucao_e_so_dinheiro():
    info = logistica_meli._so_reembolso(logistica_meli._ReembolsoML(False, None, None, None), "5578079870")
    assert info.return_type == "REFUND"


def _perms(mover: bool = True) -> dict:
    p = {"devolucoes": {"view": True, "edit": True, "delete": True}}
    if mover:
        p["devolucoes_mover"] = {"view": True, "edit": True, "delete": False}
    return p


async def _monta(db: AsyncSession, pedidos: list[str]) -> None:
    """Fake da vw_devolucoes com N pedidos em Aguardando Devolução (83957)."""
    schema = get_settings().database_schema
    entrada = (datetime.now(UTC) - timedelta(days=2)).date()
    ids = {p: uuid4() for p in pedidos}
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    for p, oid in ids.items():
        await db.execute(
            text("INSERT INTO bling_orders (id, numero, aguardando_devolucao_data) VALUES (:o, :p, :e)"),
            {"o": oid, "p": p, "e": entrada},
        )
    linhas = ",\n".join(
        f"('2026-09-10T03:00:00+00:00'::timestamptz, '{p}'::text, 'MP-{p}'::text, 'shopee'::text, 'Loja'::text, NULL::bigint, "
        f"'Cliente'::text, 'Curitiba'::text, 'PR'::text, 'a001'::text, 'Fone'::text, 1::integer, '{oid}'::uuid, '83957'::text)"
        for p, oid in ids.items()
    )
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_devolucoes AS
            SELECT * FROM (VALUES {linhas}) AS t(
                data, pedido_bling, pedido_marketplace, plataforma_bling, loja_nome, bling_loja_id,
                nome_destinatario, cidade_destino, uf_destino, sku, produto, quantidade,
                bling_order_item_id, situacao
            )
            """  # noqa: S608
        )
    )
    await db.commit()


async def _derruba_view(db: AsyncSession) -> None:
    schema = get_settings().database_schema
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.commit()


async def test_get_classifica_e_patch_move_entre_os_paineis(client, db, make_user, auth_as):
    user = await make_user(permissions=_perms())
    auth_as(user)
    vazio, pacote, nada = (f"4{uuid4().hex[:6]}" for _ in range(3))
    await _monta(db, [vazio, pacote, nada])
    db.add_all([
        # "Chegou vazio": Shopee só reembolso, disputa aberta → Fraude.
        DevolucaoRastreio(pedido_bling=vazio, fonte_auto="shopee", devolucao_status_auto="REQUESTED", devolucao_tipo_auto="REFUND"),
        # Pacote voltando e já reembolsado → continua no Acompanhamento (Vinicius: "esses 38 ficariam").
        DevolucaoRastreio(pedido_bling=pacote, fonte_auto="shopee", devolucao_status_auto="ACCEPTED", devolucao_tipo_auto="RETURN_AND_REFUND",
                          rastreio_auto="AP1BR", reembolso_auto=True, reembolso_valor_auto=Decimal("100")),
    ])
    await db.commit()
    try:
        r = await client.get("/api/devolutions/acompanhamento")
        assert r.status_code == 200
        por = {i["pedido_bling"]: i for i in r.json()["items"]}
        assert (por[vazio]["fila"], por[vazio]["fila_manual"]) == ("fraude", False)
        assert (por[pacote]["fila"], por[pacote]["fila_manual"]) == ("acompanhamento", False)
        assert (por[nada]["fila"], por[nada]["fila_manual"]) == ("acompanhamento", False)  # sem caso: fica onde sempre esteve

        # Botão "mover": fixa na mão e vale mais que a regra.
        r = await client.patch(f"/api/devolutions/acompanhamento/{vazio}", json={"fila": "acompanhamento"})
        assert r.status_code == 200
        assert (r.json()["fila"], r.json()["fila_manual"]) == ("acompanhamento", True)
        r = await client.patch(f"/api/devolutions/acompanhamento/{nada}", json={"fila": "fraude"})
        assert r.status_code == 200 and r.json()["fila"] == "fraude"
        # Valor inválido é recusado; omitido não mexe.
        r = await client.patch(f"/api/devolutions/acompanhamento/{nada}", json={"fila": "outra"})
        assert r.status_code == 422
        r = await client.patch(f"/api/devolutions/acompanhamento/{nada}", json={"observacao": "x"})
        assert r.status_code == 200 and r.json()["fila"] == "fraude" and r.json()["fila_manual"] is True

        r = await client.get("/api/devolutions/acompanhamento")
        por = {i["pedido_bling"]: i for i in r.json()["items"]}
        assert por[vazio]["fila"] == "acompanhamento" and por[vazio]["fila_manual"] is True
        assert por[nada]["fila"] == "fraude" and por[nada]["fila_manual"] is True
        row = await db.get(DevolucaoRastreio, nada)
        await db.refresh(row)
        assert row.fila_manual == "fraude" and row.fila_manual_at is not None
    finally:
        await _derruba_view(db)


async def test_mover_painel_so_admin_ou_quem_tem_permissao(client, db, make_user, auth_as):
    """Cairo 10/11: a equipe ficava passando o mesmo pedido de um painel pro
    outro. Quem só tem `devolucoes` edita a linha mas não muda o painel."""
    equipe = await make_user(permissions=_perms(mover=False))
    auth_as(equipe)
    vazio = f"4{uuid4().hex[:6]}"
    await _monta(db, [vazio])
    db.add(DevolucaoRastreio(pedido_bling=vazio, fonte_auto="shopee", devolucao_tipo_auto="REFUND"))
    await db.commit()
    try:
        r = await client.patch(f"/api/devolutions/acompanhamento/{vazio}", json={"fila": "acompanhamento", "observacao": "x"})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "mover_painel_sem_permissao"
        r = await client.patch(f"/api/devolutions/acompanhamento/{vazio}", json={"fila": "regra"})
        assert r.status_code == 403
        # o resto da linha continua editável
        r = await client.patch(f"/api/devolutions/acompanhamento/{vazio}", json={"observacao": "conferido"})
        assert r.status_code == 200 and (r.json()["fila"], r.json()["fila_manual"]) == ("fraude", False)
        row = await db.get(DevolucaoRastreio, vazio)
        await db.refresh(row)
        assert row.fila_manual is None and row.observacao == "conferido"

        admin = await make_user(role=UserRole.ADMIN)
        auth_as(admin)
        r = await client.patch(f"/api/devolutions/acompanhamento/{vazio}", json={"fila": "acompanhamento"})
        assert r.status_code == 200 and r.json()["fila"] == "acompanhamento"
    finally:
        await _derruba_view(db)


async def test_soltar_volta_pra_regra(client, db, make_user, auth_as):
    """"regra" desfaz o mover: o pedido volta pro painel que a plataforma indica."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    vazio = f"4{uuid4().hex[:6]}"
    await _monta(db, [vazio])
    db.add(DevolucaoRastreio(pedido_bling=vazio, fonte_auto="tiktok", devolucao_tipo_auto="REFUND",
                             fila_manual="acompanhamento", fila_manual_at=datetime.now(UTC)))
    await db.commit()
    try:
        r = await client.patch(f"/api/devolutions/acompanhamento/{vazio}", json={"fila": "regra"})
        assert r.status_code == 200, r.text
        assert (r.json()["fila"], r.json()["fila_manual"]) == ("fraude", False)
        row = await db.get(DevolucaoRastreio, vazio)
        await db.refresh(row)
        assert row.fila_manual is None and row.fila_manual_at is None
        r = await client.get("/api/devolutions/acompanhamento")
        por = {i["pedido_bling"]: i for i in r.json()["items"]}
        assert (por[vazio]["fila"], por[vazio]["fila_manual"]) == ("fraude", False)
    finally:
        await _derruba_view(db)


# --- 307271 (13/11/2026): "Não recebi meu pacote", Apenas reembolso ----------
# Caiu em Acompanhamento e o Vinicius moveu na mão pra Fraude. Duas lacunas:
# (1) o tipo só olhava `needs_logistics` — a "Solução do Reembolso: Apenas
# reembolso" do Seller Center é o `return_solution` 1, que o robô de Chamados
# já lia; (2) o pedido entra na aba logo que a varredura de pós-venda vê o caso,
# mas o tipo só chegava com o sync do retorno (:10/:40) — até 30 min em
# Acompanhamento. Agora a varredura grava o tipo na Logística junto do status e
# o painel usa esse tipo enquanto o sync não lê o pedido.


def test_shopee_apenas_reembolso_pela_solucao_do_caso():
    tipo = logistica_shopee._tipo_do_caso
    assert tipo({"status": "REQUESTED", "return_solution": 1, "needs_logistics": True}) == "REFUND"
    assert tipo({"return_solution": "1"}) == "REFUND"
    assert tipo({"return_solution": 0, "needs_logistics": False}) == "REFUND"
    assert tipo({"return_solution": 0}) == "RETURN_AND_REFUND"  # 0 não é "ausente"
    assert tipo({"return_solution": None}) is None
    assert tipo({}) is None


def test_tipo_do_painel_antes_e_depois_do_sync():
    lg = {"order_status": "TO_RETURN", "return_status": "REQUESTED", "return_type": "REFUND"}
    assert _tipo_do_painel(None, None, lg) == "REFUND"  # sync ainda não leu → tipo da Logística
    assert _tipo_do_painel(None, "", lg) == "REFUND"
    # Sync já leu: manda ele — inclusive o None de propósito do pacote voltando pela ida (303572).
    assert _tipo_do_painel(None, "shopee", lg) is None
    assert _tipo_do_painel("RETURN_AND_REFUND", "shopee", lg) == "RETURN_AND_REFUND"
    assert _tipo_do_painel(None, None, {"return_status": "REQUESTED"}) is None  # ML/Amazon: sem tipo
    assert _tipo_do_painel(None, None, None) is None


async def test_get_e_patch_usam_tipo_da_logistica_ate_o_sync_ler(client, db, make_user, auth_as):
    user = await make_user(permissions=_perms())
    auth_as(user)
    novo, ida, sem_tipo = (f"5{uuid4().hex[:6]}" for _ in range(3))
    await _monta(db, [novo, ida, sem_tipo])

    def _lg(pedido: str, **meli) -> Logistica:
        return Logistica(
            plataforma="Shopee", conta="mega", pedido_bling=pedido, pedido_marketplace=f"MP-{pedido}",
            rastreio=f"BR{pedido}", meli_status={"order_status": "TO_RETURN", **meli},
        )

    db.add_all([
        # Acabou de entrar: a varredura gravou o caso, o sync ainda não passou.
        _lg(novo, return_status="REQUESTED", return_type="REFUND"),
        # Sync já leu e decidiu que o pacote volta pela ida (tipo None de propósito).
        _lg(ida, return_status="ACCEPTED", return_type="REFUND"),
        DevolucaoRastreio(pedido_bling=ida, fonte_auto="shopee", devolucao_status_auto="ACCEPTED"),
        # Linha da Logística sem tipo (caso antigo, ML/Amazon): fica onde sempre esteve.
        _lg(sem_tipo, return_status="REQUESTED"),
    ])
    await db.commit()
    try:
        r = await client.get("/api/devolutions/acompanhamento")
        assert r.status_code == 200
        por = {i["pedido_bling"]: i for i in r.json()["items"]}
        assert (por[novo]["fila"], por[novo]["fila_manual"]) == ("fraude", False)
        assert (por[ida]["fila"], por[ida]["fila_manual"]) == ("acompanhamento", False)
        assert (por[sem_tipo]["fila"], por[sem_tipo]["fila_manual"]) == ("acompanhamento", False)

        # Editar a linha cria o devolucao_rastreio SEM fonte_auto: a resposta
        # (que o front espelha na tela) segue a mesma regra do GET.
        r = await client.patch(f"/api/devolutions/acompanhamento/{novo}", json={"observacao": "olhar"})
        assert r.status_code == 200, r.text
        assert (r.json()["fila"], r.json()["fila_manual"]) == ("fraude", False)
    finally:
        await _derruba_view(db)


class _FakeSweepShopee:
    def __init__(self, returns: list[dict]):
        self.returns = returns

    async def get_order_status_map(self, order_sns):
        return {str(s): {"status": "TO_RETURN", "update_time": 1789650000} for s in order_sns}

    async def get_return_list(self, **_):
        return [dict(d) for d in self.returns]


async def test_sweep_shopee_grava_o_tipo_junto_do_status(db, monkeypatch):
    sn = f"2611{uuid4().hex[:10].upper()}"
    linha = Logistica(
        plataforma="Shopee", conta="mega", pedido_bling=f"6{uuid4().hex[:6]}", pedido_marketplace=sn,
        data=date.today(), meli_status={"order_status": "TO_RETURN", "logistics_status": "LOGISTICS_PICKUP_DONE"},
    )
    db.add(linha)
    await db.commit()
    caso = {"order_sn": sn, "return_sn": "R307271", "status": "REQUESTED", "create_time": 1789640000,
            "update_time": 1789650000, "return_solution": 1, "needs_logistics": True}
    fake = _FakeSweepShopee([caso])

    async def _integ(session, conta):
        return conta

    monkeypatch.setattr(logistica_shopee, "_shopee_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_shopee, "_build_shopee_client", lambda s, i, *, lock=None: fake)

    out = await logistica_shopee.sweep_pos_venda(db)
    await db.refresh(linha)
    assert linha.meli_status["return_status"] == "REQUESTED"
    assert linha.meli_status["return_type"] == "REFUND"
    assert linha.id in out["ids"]  # status novo → regra da aba Status roda
    assert "return_type" in (linha.status_datas or {})
    assert {"campo": "return_type", "rotulo": "Tipo do caso", "valor": "Só reembolso (produto fica com o cliente)"} in (
        logistica_rules.detalhe_para("Shopee", linha.meli_status)
    )

    # Só o tipo mudou: grava, mas não conta como mudança (a assinatura não usa o tipo).
    fake.returns[0].update(return_solution=0, needs_logistics=True)
    out = await logistica_shopee.sweep_pos_venda(db)
    await db.refresh(linha)
    assert linha.meli_status["return_type"] == "RETURN_AND_REFUND"
    assert linha.meli_status["return_status"] == "REQUESTED"
    assert linha.id not in out["ids"]

    # Nada mudou: não regrava.
    antes = dict(linha.status_datas or {})
    out = await logistica_shopee.sweep_pos_venda(db)
    await db.refresh(linha)
    assert linha.status_datas == antes and linha.id not in out["ids"]


async def test_reenrich_shopee_preserva_o_tipo_do_caso(db, monkeypatch):
    linha = Logistica(
        plataforma="Shopee", conta="mega", pedido_bling=f"7{uuid4().hex[:6]}", pedido_marketplace="2611XYZ",
        meli_status={"order_status": "TO_RETURN", "return_status": "REQUESTED", "return_type": "REFUND"},
    )
    db.add(linha)
    await db.commit()

    async def _enr(client, order_sn):
        return {"meli_status": {"order_status": "TO_RETURN", "logistics_status": "LOGISTICS_PICKUP_DONE"}}

    monkeypatch.setattr(logistica_shopee, "build_enrichment", _enr)
    await logistica_shopee.enrich_row(db, linha, client_cache={"mega": object()})
    assert linha.meli_status["return_status"] == "REQUESTED"
    assert linha.meli_status["return_type"] == "REFUND"
    assert linha.meli_status["logistics_status"] == "LOGISTICS_PICKUP_DONE"
