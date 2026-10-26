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

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

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
from app.services import chamados_devolucao, logistica_bling
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
