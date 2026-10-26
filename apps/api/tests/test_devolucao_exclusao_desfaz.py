"""Excluir lançamento de devolução desfaz o que o DaVinci fez sozinho por ele.

Caso real 300978 (26/10/2026, Shopee Inova, kit de 5 malas b045): às 10:07 a
operadora lançou 6 linhas "Extraviado / Não recebido" (a b045.12 duplicada). O
DaVinci mudou o pedido de Aguardando Devolução (83957) para Problemas (83960),
criou 6 prejuízos Extraviado (R$ 441,90) e abriu 1 chamado. Às 10:10 ela
excluiu as 6 linhas e ficou tudo para trás — o pedido sumiu da aba
Acompanhamento. Eduardo: "se mudou para Problemas errado e ele excluiu, volta a
mudança de situação", e o que o DaVinci criou sozinho ele mesmo apaga.

Bling FALSO (monkeypatch no _get_bling_client), sem rede.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select, update

from app import db as app_db
from app.models import (
    Alert,
    BlingOrder,
    Chamado,
    ChamadoMensagem,
    Devolution,
    MargemAudit,
    Refund,
    SituacaoBling,
    StoreInfo,
)
from app.routers import devolutions as devolutions_router
from app.services import chamados_devolucao, devolucao_exclusao, logistica_bling
from app.services import devolution_stock_return as dsr

pytestmark = pytest.mark.asyncio

PEDIDO = "300978"
CONTA = "Shopee Inova"
BLING_ID = 2_778_300_978
AGUARDANDO, PROBLEMAS, PERDIMENTO, MANUTENCAO, RESOLVIDO = 83957, 83960, 83956, 84677, 545902
# O kit: 5 malas b045, a 12 lançada duas vezes — R$ 441,90 no total.
KIT = [
    ("b045.12", 61.90),
    ("b045.12", 61.90),
    ("b045.16", 68.50),
    ("b045.20", 75.20),
    ("b045.24", 83.40),
    ("b045.28", 91.00),
]


def _perms() -> dict:
    return {
        "devolucoes": {"view": True, "edit": True, "delete": True},
        "chamados": {"view": True, "edit": True, "delete": True},
        "reembolso": {"view": True, "edit": True, "delete": True},
    }


class _FakeBling:
    """Situação do pedido + estoque. `falha` = exceção para as mudanças de
    situação a partir de agora (o Bling fora do ar)."""

    def __init__(self) -> None:
        self.situacoes: list[tuple[int, int]] = []
        self.estoque: list[dict] = []
        self.falha: Exception | None = None

    async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> bool:
        if self.falha is not None:
            raise self.falha
        self.situacoes.append((int(bling_order_id), int(situacao_id)))
        return True

    async def update_stock_by_id(self, pid, *, qty, operation, observacao="", custo=None, **_kw):
        self.estoque.append({"pid": pid, "qty": qty, "op": operation})
        return {}


@pytest.fixture
def bling(monkeypatch) -> _FakeBling:
    fake = _FakeBling()

    async def _client(_session):
        return fake

    async def _pid(_session, _client, _sku):
        return 16377501606

    async def _sem_disparo(_session, _ch, _dev):
        return None  # a contestação não sai daqui (worker/robô)

    monkeypatch.setattr(dsr, "_get_bling_client", _client)
    monkeypatch.setattr(devolutions_router, "_get_bling_client", _client)
    monkeypatch.setattr(logistica_bling, "_bling_client", _client)
    monkeypatch.setattr(dsr, "_resolve_product_id", _pid)
    monkeypatch.setattr(chamados_devolucao, "agendar_disparo", _sem_disparo)
    return fake


async def _seed(db, user, *, pedido: str = PEDIDO, situacao: int = AGUARDANDO) -> None:
    if (await db.get(SituacaoBling, AGUARDANDO)) is None:
        db.add_all(
            [
                SituacaoBling(id=AGUARDANDO, nome="Aguardando Devolução"),
                SituacaoBling(id=PROBLEMAS, nome="Problemas"),
                SituacaoBling(id=PERDIMENTO, nome="Perdimento"),
                SituacaoBling(id=MANUTENCAO, nome="Manutenção"),
                SituacaoBling(id=RESOLVIDO, nome="Resolvido"),
            ]
        )
        db.add(
            StoreInfo(user_id=user.id, platform="shopee", account_name=CONTA, bling_store_id="77")
        )
    db.add(
        BlingOrder(
            id=uuid4(),
            numero=pedido,
            numeroloja=f"2610{pedido}INOVA",
            bling_id=BLING_ID,
            situacao=str(situacao),
            loja="77",
            data=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
            item_index=0,
            item_codigo="b045.12",
            item_descricao="Mala b045 kit 5",
            item_quantidade=1,
        )
    )
    await db.commit()


def _body(sku: str = "b045.12", custo: float = 61.90, **kw) -> dict:
    body = {
        "pedido_bling": PEDIDO,
        "pedido_marketplace": f"2610{PEDIDO}INOVA",
        "conta": CONTA,
        "sku": sku,
        "produtos": f"Mala {sku}",
        "custo_produto": custo,
        "condicao_produto": "Extraviado",
        "motivo_devolucao": "Não recebido",
        "link_abertura": "https://seller.shopee.com.br/x",
        "quantidade": 1,
    }
    body.update(kw)
    return body


async def _lancar(client, **kw) -> dict:
    r = await client.post("/api/devolutions", json=_body(**kw))
    assert r.status_code == 201, r.text
    return r.json()


async def _refunds(db, pedido: str = PEDIDO) -> list[Refund]:
    return list(
        (
            await db.execute(
                select(Refund)
                .where(Refund.pedido_bling == pedido)
                .order_by(Refund.created_at)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _chamados(db, pedido: str = PEDIDO) -> list[Chamado]:
    return list(
        (
            await db.execute(
                select(Chamado)
                .where(Chamado.pedido_bling == pedido, Chamado.origem == "devolucao")
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _espelho(db, pedido: str = PEDIDO) -> str | None:
    return (
        await db.execute(
            select(BlingOrder.situacao)
            .where(BlingOrder.numero == pedido)
            .execution_options(populate_existing=True)
        )
    ).scalars().first()


async def _sync_espelho(db, situacao: int, pedido: str = PEDIDO) -> None:
    """O sync do Bling traz a situação nova pro espelho (o fluxo não grava)."""
    for o in (await db.execute(select(BlingOrder).where(BlingOrder.numero == pedido))).scalars():
        o.situacao = str(situacao)
    await db.commit()


async def _audits(db, pedido: str = PEDIDO) -> list[MargemAudit]:
    return list(
        (
            await db.execute(
                select(MargemAudit)
                .where(MargemAudit.pedido_bling == pedido, MargemAudit.acao == "situacao")
                .order_by(MargemAudit.created_at)
            )
        )
        .scalars()
        .all()
    )


async def _alerta(db, chave: str) -> Alert | None:
    return (
        await db.execute(select(Alert).where(Alert.dedupe_key == chave))
    ).scalar_one_or_none()


async def _existe(db, model, row_id) -> bool:
    return (
        await db.execute(select(model.id).where(model.id == row_id))
    ).scalar_one_or_none() is not None


# ── O caso 300978 inteiro ─────────────────────────────────────────────────


async def test_caso_300978_excluir_as_6_linhas_desfaz_tudo(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linhas = [await _lancar(client, sku=sku, custo=custo) for sku, custo in KIT]

    # O que o DaVinci fez sozinho às 10:07:
    assert {s for _, s in bling.situacoes} == {PROBLEMAS}
    refunds = await _refunds(db)
    assert len(refunds) == 6 and {r.tipo for r in refunds} == {"Extraviado"}
    assert round(sum(r.prejuizo for r in refunds), 2) == 441.90
    # vínculo exato: o refund nasce no mesmo INSERT/commit da linha
    criadas = set(
        (
            await db.execute(
                select(Devolution.created_at).where(Devolution.pedido_bling == PEDIDO)
            )
        ).scalars()
    )
    assert {r.created_at for r in refunds} == criadas
    (ch,) = await _chamados(db)
    assert ch.origem_ref == linhas[0]["id"]
    assert not (ch.chamado or "").strip()
    # A Shopee não aceita "não recebi" pela API: a abertura foi pro robô (canal robo).
    abertura = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "abertura"
            )
        )
    ).scalar_one()
    chamados_devolucao._encaminhar_robo(
        db, ch, abertura, [], abertura.texto,
        chamados_devolucao._nota_robo_nao_recebido({}, "RSN300978", None),
    )
    await db.commit()
    assert ch.canal == "robo"
    # O sync do Bling trouxe Problemas pro espelho — o pedido saiu da aba
    # Acompanhamento (vw_devolucoes lista só quem está em 83957 no espelho).
    await _sync_espelho(db, PROBLEMAS)
    bling.situacoes.clear()

    # 10:10 — exclui as 6, uma a uma (a lixeira da aba Devoluções).
    respostas = []
    for i, linha in enumerate(linhas):
        r = await client.delete(f"/api/devolutions/{linha['id']}")
        assert r.status_code == 200, r.text
        respostas.append(r.json())
        if i < 5:
            # Ainda sobram linhas Extraviado: situação fica, chamado fica.
            assert "situacao_revertida" not in r.json()
            assert r.json()["chamado_apagado"] is False
            assert len(await _chamados(db)) == 1
    assert respostas[0]["chamado_reapontado"] is True
    assert all(r["prejuizos_apagados"]["qtd"] == 1 for r in respostas)
    assert all(r["prejuizos_mantidos"] == [] for r in respostas)
    assert round(sum(r["prejuizos_apagados"]["total"] for r in respostas), 2) == 441.90
    # A duplicata b045.12: cada linha levou o SEU prejuízo, nunca dois.
    assert respostas[0]["prejuizos_apagados"]["total"] == 61.90
    assert respostas[1]["prejuizos_apagados"]["total"] == 61.90

    ultima = respostas[-1]
    assert ultima["situacao_revertida"]["de"] == str(PROBLEMAS)
    assert ultima["situacao_revertida"]["para"] == AGUARDANDO
    assert ultima["situacao_revertida"]["nome"] == "Aguardando Devolução"
    assert ultima["chamado_apagado"] is True and ultima["chamado_mantido"] is None
    assert "situacao_aviso" not in ultima

    assert await _refunds(db) == []
    assert await _chamados(db) == []
    assert not await _existe(db, Chamado, ch.id)
    # Bling falso, espelho e trilha de auditoria.
    assert bling.situacoes == [(BLING_ID, AGUARDANDO)]
    # Espelho em 83957 na hora: a aba Acompanhamento mostra o pedido de novo no
    # próximo carregamento, sem esperar o sync.
    assert await _espelho(db) == str(AGUARDANDO)
    volta = (await _audits(db))[-1]
    assert (volta.valor_antigo, volta.valor_novo) == (str(PROBLEMAS), str(AGUARDANDO))
    assert volta.origem == "devolucao_exclusao" and volta.mudado_por == user.id


# ── Prejuízos ─────────────────────────────────────────────────────────────


async def test_prejuizo_com_reembolso_lancado_fica_e_avisa(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (refund,) = await _refunds(db)
    refund.reembolso = 40.0  # a agência já lançou o que a Shopee pagou
    await db.commit()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prejuizos_apagados"] == {"qtd": 0, "total": 0}
    assert len(body["prejuizos_mantidos"]) == 1
    assert body["prejuizos_mantidos"][0]["tipo"] == "Extraviado"
    assert "R$ 40,00" in body["prejuizos_mantidos"][0]["motivo"]
    assert [x.id for x in await _refunds(db)] == [refund.id]
    alerta = await _alerta(db, f"devolucao_exclusao_refund:{refund.id}")
    assert alerta is not None and "revisar" in alerta.title and alerta.user_id == user.id


async def test_prejuizo_conferido_fica(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (refund,) = await _refunds(db)
    refund.conferido = True
    await db.commit()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_mantidos"][0]["motivo"] == "já foi conferido na tela Reembolso"
    assert len(await _refunds(db)) == 1


async def test_prejuizo_criado_a_mao_nunca_e_apagado(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    await _seed(db, user, pedido="300979")
    linha = await _lancar(client)
    (auto,) = await _refunds(db)
    # Alguém apagou o automático na tela Reembolso e lançou um à mão, igualzinho.
    await db.delete(auto)
    manual = Refund(
        pedido_bling=PEDIDO, conta=CONTA, tipo="Extraviado", prejuizo=61.90, reembolso=0,
        created_by=user.id,
    )
    # E os de outro pedido / outra conta, que nunca são desta linha.
    outro_pedido = Refund(pedido_bling="300979", conta=CONTA, tipo="Extraviado", prejuizo=61.90)
    outra_conta = Refund(pedido_bling=PEDIDO, conta="Shopee KFA", tipo="Extraviado", prejuizo=61.90)
    db.add_all([manual, outro_pedido, outra_conta])
    await db.commit()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_apagados"]["qtd"] == 0
    for refund in (manual, outro_pedido, outra_conta):
        assert await _existe(db, Refund, refund.id)


async def test_duplicata_por_patch_apaga_um_por_linha(client, db, make_user, auth_as, bling):
    """Sem o vínculo exato (as duas b045.12 viraram Extraviado num PATCH depois):
    cai na heurística do custo — e cada exclusão leva UM refund."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _lancar(client, condicao_produto="Entregue", motivo_devolucao=None)
    b = await _lancar(client, condicao_produto="Entregue", motivo_devolucao=None)
    for linha in (a, b):
        p = await client.patch(
            f"/api/devolutions/{linha['id']}", json={"condicao_produto": "Extraviado"}
        )
        assert p.status_code == 200, p.text
    assert len(await _refunds(db)) == 2

    r = await client.delete(f"/api/devolutions/{a['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_apagados"] == {"qtd": 1, "total": 61.90}
    assert len(await _refunds(db)) == 1
    r = await client.delete(f"/api/devolutions/{b['id']}")
    assert r.json()["prejuizos_apagados"] == {"qtd": 1, "total": 61.90}
    assert await _refunds(db) == []


async def test_manutencao_apaga_o_debito_automatico(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(
        client, condicao_produto="Manutenção", motivo_devolucao=None,
        custo_manutencao=30, tecnico="Zé",
    )
    (refund,) = await _refunds(db)
    assert refund.tipo == "Manutenção" and refund.reembolso == -30
    assert bling.situacoes == [(BLING_ID, MANUTENCAO)]

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_apagados"] == {"qtd": 1, "total": 30}
    assert await _refunds(db) == []
    assert r.json()["situacao_revertida"]["para"] == AGUARDANDO
    assert bling.situacoes[-1] == (BLING_ID, AGUARDANDO)


async def test_manutencao_com_reembolso_mexido_fica(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(
        client, condicao_produto="Manutenção", motivo_devolucao=None,
        custo_manutencao=30, tecnico="Zé",
    )
    (refund,) = await _refunds(db)
    refund.reembolso = -10.0  # alguém acertou o valor na tela Reembolso
    await db.commit()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_apagados"]["qtd"] == 0
    assert "débito do conserto" in r.json()["prejuizos_mantidos"][0]["motivo"]
    assert len(await _refunds(db)) == 1


async def test_sucata_apaga_prejuizo_e_volta_de_perdimento(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client, condicao_produto="Sucata", motivo_devolucao=None, custo=180)
    (refund,) = await _refunds(db)
    assert refund.tipo == "Sucata" and refund.prejuizo == 180
    assert bling.situacoes == [(BLING_ID, PERDIMENTO)]
    await _sync_espelho(db, PERDIMENTO)

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prejuizos_apagados"] == {"qtd": 1, "total": 180}
    assert body["situacao_revertida"]["de"] == str(PERDIMENTO)
    assert body["situacao_revertida"]["nome"] == "Aguardando Devolução"
    assert await _refunds(db) == []
    assert await _espelho(db) == str(AGUARDANDO)


async def test_novo_continua_estornando_o_estoque(client, db, make_user, auth_as, bling):
    """Linha Novo (sem prejuízo): a exclusão estorna o estoque como antes — e o
    Resolvido que a entrada pôs também volta."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(
        client, sku="x020.sp", condicao_produto="Novo", motivo_devolucao=None,
        devolver_estoque=True, estoque_destino_sku="x020.sp", link_abertura=None,
    )
    assert linha["estoque_status"] == "ok"
    assert bling.situacoes == [(BLING_ID, RESOLVIDO)]
    assert await _refunds(db) == []

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estoque_estornado"] is True
    assert [e["op"] for e in bling.estoque] == ["E", "S"]
    assert body["prejuizos_apagados"] == {"qtd": 0, "total": 0}
    assert body["situacao_revertida"]["para"] == AGUARDANDO
    assert not await _existe(db, Devolution, linha["id"])


# ── Chamado ───────────────────────────────────────────────────────────────


async def test_chamado_com_protocolo_fica_e_avisa(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (ch,) = await _chamados(db)
    ch.chamado = "SC-2610-778"  # o robô abriu a solicitação no Seller Center
    await db.commit()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chamado_apagado"] is False
    assert body["chamado_mantido"]["id"] == str(ch.id)
    assert "protocolo" in body["chamado_mantido"]["motivo"]
    assert await _existe(db, Chamado, ch.id)
    assert await _alerta(db, f"devolucao_exclusao_chamado:{ch.id}") is not None
    # O resto continua sendo desfeito.
    assert body["prejuizos_apagados"]["qtd"] == 1
    assert body["situacao_revertida"]["para"] == AGUARDANDO


async def test_chamado_com_resposta_da_plataforma_fica(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (ch,) = await _chamados(db)
    db.add(
        ChamadoMensagem(
            chamado_id=ch.id, direcao="recebida", tipo="resposta", texto="Shopee: em análise",
            canal="robo", status="registrada", autor_nome="página do caso",
        )
    )
    await db.commit()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["chamado_mantido"]["motivo"] == "tem mensagens além da abertura automática"
    assert await _existe(db, Chamado, ch.id)


# ── Situação ──────────────────────────────────────────────────────────────


async def test_situacao_trocada_a_mao_depois_nao_volta(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    # Alguém escolheu Resolvido na aba Lançamentos depois do lançamento.
    r = await client.post(
        f"/api/devolutions/pedido/{PEDIDO}/situacao-bling", json={"situacao_id": RESOLVIDO}
    )
    assert r.status_code == 200, r.text
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "situacao_revertida" not in body
    assert "alguém mudou" in body["situacao_aviso"]
    assert bling.situacoes == []
    assert await _espelho(db) == str(RESOLVIDO)
    assert (await _audits(db))[-1].origem == "devolucoes_manual"


async def test_situacao_mudada_no_painel_do_bling_nao_volta(client, db, make_user, auth_as, bling):
    """Sem trilha no DaVinci (mudou no painel do Bling), mas o sync trouxe pro
    espelho uma situação que o fluxo nunca pôs: é de alguém, não mexe."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    await _sync_espelho(db, 15)
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    assert "alguém mudou" in r.json()["situacao_aviso"]
    assert bling.situacoes == []
    assert await _espelho(db) == "15"


async def test_exclusao_parcial_recalcula_e_reaponta_o_chamado(
    client, db, make_user, auth_as, bling
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    extraviada = await _lancar(client)
    sucata = await _lancar(client, sku="b045.16", custo=68.50, condicao_produto="Sucata")
    (ch,) = await _chamados(db)
    assert ch.origem_ref == extraviada["id"]
    bling.situacoes.clear()

    # Sai a Extraviado: sobra a Sucata → o pedido vai para Perdimento.
    r = await client.delete(f"/api/devolutions/{extraviada['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["situacao_recalculada"] == {"para": PERDIMENTO, "nome": "Perdimento"}
    assert "situacao_revertida" not in body
    assert bling.situacoes == [(BLING_ID, PERDIMENTO)]
    assert body["chamado_apagado"] is False and body["chamado_reapontado"] is True
    (ch,) = await _chamados(db)
    assert ch.origem_ref == sucata["id"]
    assert [r.tipo for r in await _refunds(db)] == ["Sucata"]


async def test_bling_fora_exclui_avisa_e_nao_da_500(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    await _sync_espelho(db, PROBLEMAS)
    bling.falha = httpx.ConnectError("bling fora do ar")

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert not await _existe(db, Devolution, linha["id"])
    assert body["prejuizos_apagados"]["qtd"] == 1
    assert "situacao_revertida" not in body
    assert "NÃO voltou" in body["situacao_aviso"]
    assert "Aguardando Devolução" in body["situacao_aviso"]
    assert body["situacao_devia_ser"] == {"para": AGUARDANDO, "nome": "Aguardando Devolução"}
    assert await _espelho(db) == str(PROBLEMAS)
    assert (await _audits(db))[-1].origem == "devolucao"
    assert await _alerta(db, f"devolucao_exclusao_situacao:{PEDIDO}:{AGUARDANDO}") is not None


async def test_bling_recusa_a_volta_direta_passa_pelo_desvio(client, db, make_user, auth_as, bling):
    """Situação de antes que não é Aguardando Devolução (pedido estava em 15):
    o Bling recusa a ida direta → mesma regra de desvio do endpoint manual."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user, situacao=15)
    linha = await _lancar(client)
    await _sync_espelho(db, PROBLEMAS)
    bling.situacoes.clear()
    recusas = [15]
    original = bling.update_order_situacao

    async def _mao_unica(bid, sid):
        if sid in recusas:
            recusas.remove(sid)
            req = httpx.Request("PATCH", "https://api.bling.com.br/x")
            raise httpx.HTTPStatusError(
                "400", request=req, response=httpx.Response(400, text="transição", request=req)
            )
        return await original(bid, sid)

    bling.update_order_situacao = _mao_unica

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["situacao_revertida"]["para"] == 15
    assert bling.situacoes == [(BLING_ID, AGUARDANDO), (BLING_ID, 15)]
    assert await _espelho(db) == "15"


# ── Lixeira do chamado ────────────────────────────────────────────────────


async def test_lixeira_do_chamado_apaga_prejuizos_e_respeita_a_situacao(
    client, db, make_user, auth_as, bling
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _lancar(client)
    b = await _lancar(client, sku="b045.16", custo=68.50)
    (ch,) = await _chamados(db)
    bling.situacoes.clear()

    r = await client.post(
        f"/api/chamados/{ch.id}/excluir",
        json={"devolucoes": [a["id"], b["id"]], "situacao": "Resolvido"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lancamentos_excluidos"] == 2
    assert body["prejuizos_apagados"] == {"qtd": 2, "total": 130.40}
    assert "prejuizos_mantidos" not in body
    assert await _refunds(db) == []
    assert not await _existe(db, Chamado, ch.id)
    # Só a situação que a pessoa escolheu — nada de voltar para Aguardando Devolução.
    assert bling.situacoes == [(BLING_ID, RESOLVIDO)]


# ── Revisão: prejuízo sem vínculo exato (achar_refund_auto) ───────────────


async def _patch(client, dev_id: str, **campos) -> dict:
    r = await client.patch(f"/api/devolutions/{dev_id}", json=campos)
    assert r.status_code == 200, r.text
    return r.json()


async def _alertas(db, prefixo: str) -> list[Alert]:
    return list(
        (
            await db.execute(
                select(Alert)
                .where(Alert.dedupe_key.like(f"{prefixo}%"))
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _por_patch(client, *, sku: str, custo: float) -> dict:
    """Linha lançada Entregue que vira Extraviado num PATCH: o refund nasce no
    PATCH, sem o vínculo exato do created_at."""
    linha = await _lancar(
        client, sku=sku, custo=custo, condicao_produto="Entregue", motivo_devolucao=None
    )
    await _patch(client, linha["id"], condicao_produto="Extraviado")
    return linha


async def test_custo_corrigido_depois_do_patch_nao_apaga_o_prejuizo_da_irma(
    client, db, make_user, auth_as, bling
):
    """A (100) e B (150) viram Extraviado por PATCH; o custo da A é corrigido
    para 110. Excluir A NÃO pode levar o prejuízo da B (o mais recente)."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _por_patch(client, sku="b045.12", custo=100)
    b = await _por_patch(client, sku="b045.16", custo=150)
    await _patch(client, a["id"], custo_produto=110)
    r_a, r_b = await _refunds(db)
    assert (r_a.prejuizo, r_b.prejuizo) == (100, 150)

    r = await client.delete(f"/api/devolutions/{a['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_apagados"] == {"qtd": 0, "total": 0}
    assert {x.id for x in await _refunds(db)} == {r_a.id, r_b.id}
    # Na dúvida, nada sai — e quem excluiu é avisado pra conferir.
    assert "não consegui saber" in r.json()["prejuizos_mantidos"][0]["motivo"]
    assert await _alertas(db, f"devolucao_exclusao_refund_duvida:{a['id']}")
    # A B continua com o prejuízo dela.
    r = await client.delete(f"/api/devolutions/{b['id']}")
    assert r.json()["prejuizos_apagados"] == {"qtd": 1, "total": 150}
    assert [x.id for x in await _refunds(db)] == [r_a.id]


async def test_custo_corrigido_patch_que_sai_de_extraviado_nao_leva_o_da_irma(
    client, db, make_user, auth_as, bling
):
    """Mesma regra no PATCH que tira a linha de Extraviado (o produto apareceu)."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _por_patch(client, sku="b045.12", custo=100)
    await _por_patch(client, sku="b045.16", custo=150)
    await _patch(client, a["id"], custo_produto=110)
    r_a, r_b = await _refunds(db)

    await _patch(client, a["id"], condicao_produto="Entregue")
    assert {x.id for x in await _refunds(db)} == {r_a.id, r_b.id}


async def test_refund_proprio_apagado_a_mao_nao_leva_o_da_irma(
    client, db, make_user, auth_as, bling
):
    """A lançada Extraviado (refund com vínculo exato), B virou Extraviado por
    PATCH. Alguém apagou o refund da A na tela Reembolso: excluir A não pode
    apagar o da B, que continua no pedido."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _lancar(client, sku="b045.12", custo=61.90)
    await _por_patch(client, sku="b045.16", custo=68.50)
    r_a, r_b = await _refunds(db)
    assert (await client.delete(f"/api/refunds/{r_a.id}")).status_code == 204

    r = await client.delete(f"/api/devolutions/{a['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_apagados"]["qtd"] == 0
    assert [x.id for x in await _refunds(db)] == [r_b.id]


async def test_refund_proprio_apagado_a_mao_irma_de_mesmo_custo(
    client, db, make_user, auth_as, bling
):
    """Variante com a irmã de MESMO custo (b045.12 duas vezes): o refund que
    sobrou é o da irmã — fica."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _lancar(client, sku="b045.12", custo=61.90)
    await _por_patch(client, sku="b045.12", custo=61.90)
    r_a, r_b = await _refunds(db)
    assert (await client.delete(f"/api/refunds/{r_a.id}")).status_code == 204

    r = await client.delete(f"/api/devolutions/{a['id']}")
    assert r.json()["prejuizos_apagados"]["qtd"] == 0
    assert [x.id for x in await _refunds(db)] == [r_b.id]


async def test_duplicata_por_patch_com_um_mexido_apaga_o_intacto(
    client, db, make_user, auth_as, bling
):
    """As duas b045.12 viram Extraviado por PATCH; a agência lança R$ 61,90 no
    refund mais novo. Excluir a 1ª leva o INTACTO; o mexido fica para a 2ª."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _por_patch(client, sku="b045.12", custo=61.90)
    b = await _por_patch(client, sku="b045.12", custo=61.90)
    rx, ry = await _refunds(db)
    ry.reembolso = 61.90
    await db.commit()

    r = await client.delete(f"/api/devolutions/{a['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_apagados"] == {"qtd": 1, "total": 61.90}
    assert r.json()["prejuizos_mantidos"] == []
    assert await _alertas(db, "devolucao_exclusao_refund") == []
    assert [x.id for x in await _refunds(db)] == [ry.id]

    r = await client.delete(f"/api/devolutions/{b['id']}")
    assert r.json()["prejuizos_apagados"]["qtd"] == 0
    assert "R$ 61,90" in r.json()["prejuizos_mantidos"][0]["motivo"]
    (alerta,) = await _alertas(db, f"devolucao_exclusao_refund:{ry.id}")
    # Sem vínculo exato, o aviso não afirma que o refund era "desta linha".
    assert "que o DaVinci criou para ele" not in alerta.message
    assert [x.id for x in await _refunds(db)] == [ry.id]


async def test_refund_manual_de_antes_do_created_by_nunca_e_apagado(
    client, db, make_user, auth_as, bling
):
    """Linha Extraviado de 22/05/2026 (antes do refund automático) e refund
    lançado À MÃO em 25/05 — antes da 0133, então sem created_by. Fica e avisa."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = Devolution(
        pedido_bling=PEDIDO, conta=CONTA, sku="b045.12", custo_produto=61.90,
        condicao_produto="Extraviado",
        created_at=datetime(2026, 5, 22, 13, 0, tzinfo=UTC),
    )
    manual = Refund(
        pedido_bling=PEDIDO, conta=CONTA, tipo="Extraviado", prejuizo=61.90, reembolso=0,
        created_at=datetime(2026, 5, 25, 15, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 25, 15, 0, tzinfo=UTC),
    )
    db.add_all([linha, manual])
    await db.commit()

    r = await client.delete(f"/api/devolutions/{linha.id}")
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_apagados"]["qtd"] == 0
    assert "antes de o DaVinci registrar" in r.json()["prejuizos_mantidos"][0]["motivo"]
    assert await _existe(db, Refund, manual.id)
    assert await _alerta(db, f"devolucao_exclusao_refund:{manual.id}") is not None


# ── Revisão: "ninguém mexeu" no prejuízo ─────────────────────────────────


async def test_prejuizo_com_protocolo_e_observacao_fica(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (refund,) = await _refunds(db)
    p = await client.patch(
        f"/api/refunds/{refund.id}",
        json={
            "chamado": "SC-123456",
            "chamado_url": "https://seller.shopee.com.br/disputa/123456",
            "observacao": "Aberta disputa na Shopee, aguardando",
        },
    )
    assert p.status_code == 200, p.text

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_apagados"]["qtd"] == 0
    assert "SC-123456" in r.json()["prejuizos_mantidos"][0]["motivo"]
    assert await _existe(db, Refund, refund.id)
    assert await _alerta(db, f"devolucao_exclusao_refund:{refund.id}") is not None


async def test_prejuizo_com_chamado_resolvido_fica(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (refund,) = await _refunds(db)
    p = await client.patch(f"/api/refunds/{refund.id}", json={"chamado_resolvido": True})
    assert p.status_code == 200, p.text

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert "resolvido" in r.json()["prejuizos_mantidos"][0]["motivo"]
    assert await _existe(db, Refund, refund.id)


async def test_prejuizo_editado_na_tela_reembolso_fica(client, db, make_user, auth_as, bling):
    """A agência ajustou o prejuízo (reembolso continua 0): foi editado — fica."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (refund,) = await _refunds(db)
    p = await client.patch(f"/api/refunds/{refund.id}", json={"prejuizo": 55})
    assert p.status_code == 200, p.text

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.json()["prejuizos_mantidos"][0]["motivo"] == "foi editado na tela Reembolso"
    assert await _existe(db, Refund, refund.id)


async def test_patch_que_sai_de_extraviado_mantem_prejuizo_anotado(
    client, db, make_user, auth_as, bling
):
    """Mesma regra no PATCH Extraviado → Entregue."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client, motivo_devolucao=None)
    (refund,) = await _refunds(db)
    p = await client.patch(f"/api/refunds/{refund.id}", json={"observacao": "abri reclamação"})
    assert p.status_code == 200, p.text

    await _patch(client, linha["id"], condicao_produto="Entregue")
    assert await _existe(db, Refund, refund.id)
    assert await _alerta(db, f"devolucao_refund_revisar:{refund.id}") is not None


async def test_refund_salvo_pela_agencia_durante_a_exclusao_fica(
    client, db, make_user, auth_as, bling
):
    """Corrida: a agência salva reembolso=40 no mesmo refund enquanto a linha é
    excluída. A busca do refund trava a linha (FOR UPDATE) e relê o banco: vê
    o 40 e mantém, em vez de apagar o refund já com o valor."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (refund,) = await _refunds(db)

    async with app_db.SessionLocal() as agencia:
        travado = (
            await agencia.execute(
                select(Refund).where(Refund.id == refund.id).with_for_update()
            )
        ).scalar_one()
        travado.reembolso = 40.0
        await agencia.flush()
        tarefa = asyncio.create_task(client.delete(f"/api/devolutions/{linha['id']}"))
        await asyncio.sleep(0.5)
        await agencia.commit()
    r = await asyncio.wait_for(tarefa, timeout=20)
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_apagados"]["qtd"] == 0
    assert "R$ 40,00" in r.json()["prejuizos_mantidos"][0]["motivo"]
    (ficou,) = await _refunds(db)
    assert ficou.reembolso == 40.0


# ── Revisão: Manutenção ───────────────────────────────────────────────────


async def test_manutencao_que_saiu_para_usado_leva_o_debito(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(
        client, condicao_produto="Manutenção", motivo_devolucao=None,
        custo_manutencao=30, tecnico="Zé",
    )
    await _patch(client, linha["id"], condicao_produto="Usado")
    (refund,) = await _refunds(db)
    assert refund.tipo == "Manutenção" and refund.reembolso == -30

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["prejuizos_apagados"] == {"qtd": 1, "total": 30}
    assert await _refunds(db) == []


async def test_manutencao_que_saiu_com_debito_mexido_fica(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(
        client, condicao_produto="Manutenção", motivo_devolucao=None,
        custo_manutencao=30, tecnico="Zé",
    )
    await _patch(client, linha["id"], condicao_produto="Usado")
    (refund,) = await _refunds(db)
    refund.reembolso = -10.0
    await db.commit()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.json()["prejuizos_apagados"]["qtd"] == 0
    assert r.json()["prejuizos_mantidos"][0]["tipo"] == "Manutenção"
    assert len(await _refunds(db)) == 1


async def test_kit_com_duas_manutencoes_e_custo_editado(client, db, make_user, auth_as, bling):
    """O delta do custo vai para o refund da PRÓPRIA linha (não o mais recente
    do pedido) — e a exclusão apaga os dois débitos, sem alerta falso."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    um = await _lancar(
        client, sku="b045.12", condicao_produto="Manutenção", motivo_devolucao=None,
        custo_manutencao=30, tecnico="Zé",
    )
    dois = await _lancar(
        client, sku="b045.16", condicao_produto="Manutenção", motivo_devolucao=None,
        custo_manutencao=20, tecnico="Zé",
    )
    await _patch(client, um["id"], custo_manutencao=35)
    r1, r2 = await _refunds(db)
    assert (r1.reembolso, r2.reembolso) == (-35, -20)

    for linha, total in ((um, 35), (dois, 20)):
        r = await client.delete(f"/api/devolutions/{linha['id']}")
        assert r.status_code == 200, r.text
        assert r.json()["prejuizos_apagados"] == {"qtd": 1, "total": total}
        assert r.json()["prejuizos_mantidos"] == []
    assert await _refunds(db) == []
    assert await _alertas(db, "devolucao_exclusao_refund") == []


async def test_manutencao_com_custo_depois_soma_o_debito(client, db, make_user, auth_as, bling):
    """Lançada sem custo (prejuízo 0); o custo 80 entra depois: o total
    apagado é o débito de R$ 80, não R$ 0."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client, condicao_produto="Manutenção", motivo_devolucao=None)
    await _patch(client, linha["id"], custo_manutencao=80)
    (refund,) = await _refunds(db)
    assert (refund.prejuizo, refund.reembolso) == (0, -80)

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.json()["prejuizos_apagados"] == {"qtd": 1, "total": 80}


# ── Revisão: chamado ──────────────────────────────────────────────────────


async def test_chamado_com_observacao_de_pessoa_fica(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (ch,) = await _chamados(db)
    p = await client.patch(
        f"/api/chamados/{ch.id}",
        json={"observacao": "Liguei na transportadora, protocolo 99 por telefone"},
    )
    assert p.status_code == 200, p.text

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.json()["chamado_apagado"] is False
    assert "observação" in r.json()["chamado_mantido"]["motivo"]
    assert await _existe(db, Chamado, ch.id)


async def test_chamado_com_situacao_escolhida_por_pessoa_fica(
    client, db, make_user, auth_as, bling
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (ch,) = await _chamados(db)
    p = await client.patch(f"/api/chamados/{ch.id}", json={"alterar_status_bling": "Problemas"})
    assert p.status_code == 200, p.text

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.json()["chamado_apagado"] is False
    assert "situação do Bling" in r.json()["chamado_mantido"]["motivo"]


async def test_chamado_sendo_processado_fica_e_nao_trava_a_exclusao(
    client, db, make_user, auth_as, bling
):
    """O robô (lease) ou o cron das pendentes está com a abertura travada: a
    exclusão não espera o lote deles — mantém o chamado e avisa."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (ch,) = await _chamados(db)

    async with app_db.SessionLocal() as robo:
        await robo.execute(
            select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id).with_for_update()
        )
        try:
            r = await asyncio.wait_for(
                client.delete(f"/api/devolutions/{linha['id']}"), timeout=10
            )
        finally:
            await robo.rollback()
    assert r.status_code == 200, r.text
    assert r.json()["chamado_apagado"] is False
    assert "processado agora" in r.json()["chamado_mantido"]["motivo"]
    assert await _existe(db, Chamado, ch.id)
    # O resto foi desfeito normalmente.
    assert r.json()["prejuizos_apagados"]["qtd"] == 1


async def test_caso_293843_chamado_encerrado_pelo_motivo_e_apagado(
    client, db, make_user, auth_as, bling
):
    """Lançado Extraviado + Não recebido; a operadora limpa o motivo (o
    DaVinci encerra o chamado e cancela a abertura que nem tinha saído).
    Excluir a linha apaga o chamado — ninguém agiu nele — sem alerta."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (ch,) = await _chamados(db)
    await _patch(client, linha["id"], motivo_devolucao=None)
    (ch,) = await _chamados(db)
    assert ch.status_plataforma == "encerrado"

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.json()["chamado_apagado"] is True, r.json()
    assert r.json()["chamado_mantido"] is None
    assert await _alertas(db, "devolucao_exclusao_chamado") == []


async def test_kit_parcial_chamado_apagado_na_ultima_exclusao(
    client, db, make_user, auth_as, bling
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _lancar(client, sku="b045.12")
    b = await _lancar(client, sku="b045.16", custo=68.50)
    await _patch(client, a["id"], motivo_devolucao=None)  # "O chamado continua pelos outros"

    r = await client.delete(f"/api/devolutions/{a['id']}")
    assert r.json()["chamado_apagado"] is False
    r = await client.delete(f"/api/devolutions/{b['id']}")
    assert r.json()["chamado_apagado"] is True, r.json()
    assert await _chamados(db) == []


async def test_chamado_de_episodio_anterior_nao_gera_aviso(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    antigo = Chamado(
        pedido_bling=PEDIDO, conta=CONTA, origem="devolucao", origem_ref=str(uuid4()),
        canal="manual", resolvido=True,
    )
    db.add(antigo)
    await db.commit()
    linha = await _lancar(client, condicao_produto="Sucata", motivo_devolucao=None, custo=180)

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["chamado_mantido"] is None
    assert await _alertas(db, "devolucao_exclusao_chamado") == []
    assert await _existe(db, Chamado, antigo.id)


async def test_motivo_trocado_noutra_linha_nao_gera_aviso(client, db, make_user, auth_as, bling):
    """A (Não recebido, origem do chamado) tem o motivo limpo (chamado
    Encerrado); excluir B não mexe nem avisa sobre esse chamado."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _lancar(client, sku="b045.12")
    b = await _lancar(client, sku="b045.16", custo=68.50, condicao_produto="Sucata",
                      motivo_devolucao=None)
    await _patch(client, a["id"], motivo_devolucao=None)
    (ch,) = await _chamados(db)
    assert ch.status_plataforma == "encerrado"

    r = await client.delete(f"/api/devolutions/{b['id']}")
    assert r.json()["chamado_mantido"] is None
    assert await _alertas(db, "devolucao_exclusao_chamado") == []
    assert await _existe(db, Chamado, ch.id)


async def test_cron_das_pendentes_pula_chamado_apagado_no_meio(db, make_user, bling, monkeypatch):
    """O cron processa uma abertura por vez e commita cada uma: o chamado do
    pedido Y apagado (exclusão da última linha) enquanto o cron conversava com
    a plataforma no X não derruba o "enviada" do X — Y é só pulado."""
    user = await make_user(permissions=_perms())
    await _seed(db, user)
    ids = {}
    for nome in ("X", "Y"):
        dev = Devolution(pedido_bling=f"9{nome}", conta=CONTA, sku="b045.12",
                         motivo_devolucao="Não recebido")
        db.add(dev)
        await db.flush()
        ch = Chamado(pedido_bling=f"9{nome}", conta=CONTA, origem="devolucao",
                     origem_ref=str(dev.id), canal="api")
        db.add(ch)
        await db.flush()
        db.add(ChamadoMensagem(chamado_id=ch.id, direcao="enviada", tipo="abertura",
                               texto="contestação", canal="api", status="pendente"))
        await db.commit()
        ids[nome] = (dev.id, ch.id)

    async def _disparar(session, ch, dev, **_kw):
        msg = await chamados_devolucao.mensagem_abertura(session, ch)
        if ch.id == ids["X"][1]:
            # A operadora excluiu a última linha do Y bem agora.
            async with app_db.SessionLocal() as outra:
                await outra.execute(delete(Chamado).where(Chamado.id == ids["Y"][1]))
                await outra.execute(delete(Devolution).where(Devolution.id == ids["Y"][0]))
                await outra.commit()
        msg.status = "enviada"
        return msg

    monkeypatch.setattr(chamados_devolucao, "disparar", _disparar)
    db.expunge_all()  # o cron roda numa sessão nova (sem nada em cache)
    resumo = await chamados_devolucao.processar_pendentes(db)
    assert resumo["abertos"] == 1
    status = (
        await db.execute(
            select(ChamadoMensagem.status).where(ChamadoMensagem.chamado_id == ids["X"][1])
        )
    ).scalar_one()
    assert status == "enviada"


async def test_lease_do_robo_pula_mensagem_travada(client, db, make_user, auth_as, bling,
                                                   monkeypatch):
    """A exclusão está com a abertura do robô travada: o lease não a entrega
    (nem espera) — senão marcaria uma mensagem que some e responderia 500."""
    from app.config import get_settings

    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    await _lancar(client)
    (ch,) = await _chamados(db)
    abertura = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "abertura"
            )
        )
    ).scalar_one()
    chamados_devolucao._encaminhar_robo(
        db, ch, abertura, [], abertura.texto,
        chamados_devolucao._nota_robo_nao_recebido({}, "RSN300978", None),
    )
    await db.commit()
    monkeypatch.setattr(get_settings(), "nf_agent_token", "tok-teste")
    hdr = {"X-Agent-Token": "tok-teste"}
    corpo = {"limite": 10, "plataforma": "shopee"}

    async with app_db.SessionLocal() as exclusao:
        await exclusao.execute(
            select(ChamadoMensagem).where(ChamadoMensagem.id == abertura.id).with_for_update()
        )
        try:
            r = await asyncio.wait_for(
                client.post("/api/chamados/agent/lease", headers=hdr, json=corpo), timeout=10
            )
        finally:
            await exclusao.rollback()
    assert r.status_code == 200, r.text
    assert r.json()["tarefas"] == []
    # Solta a trava: a abertura volta a sair normalmente.
    r = await client.post("/api/chamados/agent/lease", headers=hdr, json=corpo)
    assert [t["mensagem_id"] for t in r.json()["tarefas"]] == [str(abertura.id)]


# ── Revisão: situação ─────────────────────────────────────────────────────


async def test_sobra_linha_do_backfill_nao_reabre_o_pedido(client, db, make_user, auth_as, bling):
    """Pedido Resolvido com 2 linhas Novo: A é do backfill 0421 (sem
    confirmação, nunca tentada). Excluir B (duplicada) não reabre o pedido."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _lancar(
        client, sku="x020.sp", condicao_produto="Novo", motivo_devolucao=None,
        devolver_estoque=True, estoque_destino_sku="x020.sp", link_abertura=None,
    )
    b = await _lancar(
        client, sku="x020.sp", condicao_produto="Novo", motivo_devolucao=None,
        devolver_estoque=True, estoque_destino_sku="x020.sp", link_abertura=None,
    )
    await db.execute(
        update(Devolution)
        .where(Devolution.id == a["id"])
        .values(estoque_status="sem_confirmacao", estoque_tentativa_at=None)
    )
    await db.commit()
    await _sync_espelho(db, RESOLVIDO)
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{b['id']}")
    assert r.status_code == 200, r.text
    assert "situacao_revertida" not in r.json()
    assert bling.situacoes == []
    assert await _espelho(db) == str(RESOLVIDO)


async def test_sobra_linha_entregue_continua_revertendo(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    await _lancar(client, sku="b045.16", custo=68.50, condicao_produto="Entregue",
                  motivo_devolucao=None)
    extraviada = await _lancar(client)
    await _sync_espelho(db, PROBLEMAS)
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{extraviada['id']}")
    assert r.json()["situacao_revertida"]["para"] == AGUARDANDO
    assert bling.situacoes == [(BLING_ID, AGUARDANDO)]


async def test_sobram_linhas_que_nao_definem_a_situacao_volta(
    client, db, make_user, auth_as, bling
):
    """A b045.16 ainda sem condição (o pacote não chegou) e a b045.12 lançada
    Extraviado por engano: excluir a b045.12 volta o pedido para Aguardando
    Devolução, em vez de deixá-lo em Problemas, fora do Acompanhamento."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    await _lancar(client, sku="b045.16", custo=68.50, condicao_produto=None,
                  motivo_devolucao=None, link_abertura=None)
    extraviada = await _lancar(client)
    await _sync_espelho(db, PROBLEMAS)
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{extraviada['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["situacao_revertida"]["para"] == AGUARDANDO
    assert bling.situacoes == [(BLING_ID, AGUARDANDO)]
    assert await _espelho(db) == str(AGUARDANDO)
    assert (await _audits(db))[-1].origem == "devolucao_exclusao"


async def test_irma_mudada_no_meio_da_exclusao_entra_no_recalculo(
    client, db, make_user, auth_as, bling, monkeypatch
):
    """A e B Extraviado; durante a exclusão de B (estorno no Bling), outra
    pessoa troca A para Novo com entrada ok. O recálculo usa o estado do banco:
    o pedido vai para Resolvido."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _lancar(client, motivo_devolucao=None)
    b = await _lancar(client, sku="b045.16", custo=68.50, motivo_devolucao=None)
    bling.situacoes.clear()
    original = devolutions_router.excluir_lancamento

    async def _no_meio(session, row, **kw):
        async with app_db.SessionLocal() as outra:
            await outra.execute(
                update(Devolution)
                .where(Devolution.id == a["id"])
                .values(condicao_produto="Novo", estoque_status="ok")
            )
            await outra.commit()
        return await original(session, row, **kw)

    monkeypatch.setattr(devolutions_router, "excluir_lancamento", _no_meio)
    r = await client.delete(f"/api/devolutions/{b['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["situacao_recalculada"] == {"para": RESOLVIDO, "nome": "Resolvido"}
    assert bling.situacoes == [(BLING_ID, RESOLVIDO)]


async def test_erro_no_chamado_nao_esconde_a_situacao_que_voltou(
    client, db, make_user, auth_as, bling, monkeypatch
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    await _sync_espelho(db, PROBLEMAS)

    async def _quebra(*_a, **_kw):
        raise RuntimeError("deadlock detected")

    monkeypatch.setattr(devolucao_exclusao, "desfazer_chamado", _quebra)
    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["situacao_revertida"]["de"] == str(PROBLEMAS)
    assert body["situacao_revertida"]["para"] == AGUARDANDO
    assert "situacao_aviso" not in body
    assert "chamado" in body["chamado_aviso"] and "deadlock" in body["chamado_aviso"]
    assert await _espelho(db) == str(AGUARDANDO)


async def test_sem_trilha_avisa_que_o_pedido_ficou_em_problemas(
    client, db, make_user, auth_as, bling
):
    """Linha de antes da trilha de situação (01/06): não há de onde voltar, mas
    o pedido parado em Problemas some do Acompanhamento — avisa."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    await _sync_espelho(db, PROBLEMAS)
    await db.execute(delete(MargemAudit).where(MargemAudit.pedido_bling == PEDIDO))
    await db.commit()
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.status_code == 200, r.text
    assert "Não achei de onde o pedido veio" in r.json()["situacao_aviso"]
    assert "Problemas" in r.json()["situacao_aviso"]
    assert bling.situacoes == []


async def test_situacao_do_meio_da_cadeia_no_espelho_e_de_alguem(
    client, db, make_user, auth_as, bling
):
    """Extraviado (→ Problemas) e depois Sucata (→ Perdimento). Muito depois,
    o espelho mostra Problemas de novo: alguém pôs (painel do Bling) — não é
    atraso do sync. Não volta."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client, motivo_devolucao=None)
    await _sync_espelho(db, PROBLEMAS)
    await _patch(client, linha["id"], condicao_produto="Sucata")
    passos = await _audits(db)
    assert [(a.valor_antigo, a.valor_novo) for a in passos][-1] == (str(PROBLEMAS), str(PERDIMENTO))
    await db.execute(
        update(MargemAudit)
        .where(MargemAudit.pedido_bling == PEDIDO)
        .values(created_at=MargemAudit.created_at - timedelta(hours=2))
    )
    await db.commit()
    await _sync_espelho(db, PROBLEMAS)
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert "alguém mudou" in r.json()["situacao_aviso"]
    assert bling.situacoes == []


async def test_alterar_status_pela_aba_chamados_nao_e_desfeito(
    client, db, make_user, auth_as, bling
):
    """Extraviado → Sucata → 'Problemas' escolhido pela aba Chamados: a escolha
    grava auditoria e espelho, e a exclusão não passa por cima dela."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    await _patch(client, linha["id"], condicao_produto="Sucata")
    (ch,) = await _chamados(db)
    r = await client.post(
        f"/api/chamados/{ch.id}/alterar-status-bling", json={"situacao": "Problemas"}
    )
    assert r.status_code == 200, r.text
    assert await _espelho(db) == str(PROBLEMAS)
    volta = (await _audits(db))[-1]
    assert (volta.origem, volta.valor_novo) == ("chamados", str(PROBLEMAS))
    assert volta.mudado_por == user.id
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert "situacao_revertida" not in r.json()
    assert "alguém mudou" in r.json()["situacao_aviso"]
    assert bling.situacoes == []


async def test_resolvido_pela_aba_chamados_antes_do_sync_nao_e_desfeito(
    client, db, make_user, auth_as, bling
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (ch,) = await _chamados(db)
    r = await client.post(
        f"/api/chamados/{ch.id}/alterar-status-bling", json={"situacao": "Resolvido"}
    )
    assert r.status_code == 200, r.text
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert "situacao_revertida" not in r.json()
    assert "alguém mudou" in r.json()["situacao_aviso"]
    assert bling.situacoes == []
    assert await _espelho(db) == str(RESOLVIDO)


async def test_resolver_chamado_com_situacao_nao_e_desfeito(client, db, make_user, auth_as, bling):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    linha = await _lancar(client)
    (ch,) = await _chamados(db)
    r = await client.post(
        f"/api/chamados/{ch.id}/resolver",
        json={"resolvido": True, "valor_recuperado": "0", "situacao": "Resolvido"},
    )
    assert r.status_code == 200, r.text
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert "situacao_revertida" not in r.json()
    assert bling.situacoes == []
    # Chamado concluído: fica, sem aviso de "revisar".
    assert r.json()["chamado_mantido"] is None
    assert await _existe(db, Chamado, ch.id)


async def test_lixeira_do_chamado_e_depois_a_linha_que_sobrou(
    client, db, make_user, auth_as, bling
):
    """A lixeira do chamado exclui as 2 Extraviado escolhendo Resolvido; antes
    do sync, a operadora exclui a Novo que sobrou: a escolha da pessoa fica."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user)
    a = await _lancar(client)
    b = await _lancar(client, sku="b045.16", custo=68.50)
    novo = await _lancar(
        client, sku="x020.sp", condicao_produto="Novo", motivo_devolucao=None,
        devolver_estoque=True, estoque_destino_sku="x020.sp", link_abertura=None,
    )
    await _sync_espelho(db, PROBLEMAS)
    (ch,) = await _chamados(db)
    r = await client.post(
        f"/api/chamados/{ch.id}/excluir",
        json={"devolucoes": [a["id"], b["id"]], "situacao": "Resolvido"},
    )
    assert r.status_code == 200, r.text
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{novo['id']}")
    assert "situacao_revertida" not in r.json()
    assert bling.situacoes == []


async def test_episodio_anterior_nao_vira_alvo_da_volta(client, db, make_user, auth_as, bling):
    """Pedido Atendido (9) recebeu uma Novo (→ Resolvido) excluída antes do
    conserto, sem volta; a Logística pôs o pedido em Aguardando Devolução (sem
    trilha). Uma Extraviado lançada por engano e excluída volta para 83957 —
    não para o Atendido de meses atrás."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed(db, user, situacao=9)
    db.add(
        MargemAudit(
            acao="situacao", pedido_bling=PEDIDO, bling_id=str(BLING_ID), valor_antigo="9",
            valor_novo=str(RESOLVIDO), origem="devolucao",
            created_at=datetime.now(UTC) - timedelta(days=30),
        )
    )
    await db.commit()
    await _sync_espelho(db, AGUARDANDO)
    linha = await _lancar(client)
    await _sync_espelho(db, PROBLEMAS)
    bling.situacoes.clear()

    r = await client.delete(f"/api/devolutions/{linha['id']}")
    assert r.json()["situacao_revertida"]["para"] == AGUARDANDO
    assert bling.situacoes == [(BLING_ID, AGUARDANDO)]
