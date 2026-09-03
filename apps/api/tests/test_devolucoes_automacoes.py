"""Automações de Devoluções pedidas pelo Eduardo (03/09):

- motivo que pede chamado (Golpe, Bloqueado, Item faltando, Não recebido,
  Danificado (Outros)) → chamado aberto SOZINHO no create/PATCH, com dedupe
  por pedido;
- custo de manutenção + técnico preenchidos → checkbox Reembolso liga sozinho
  (e desmarcar na mão não religa em edições de outros campos);
- coluna "Chamado" na listagem (chamado mais recente do pedido);
- Acompanhamento: rastreio/localização AUTOMÁTICOS vindos da Logística, com a
  edição manual da aba valendo mais (COALESCE), inclusive na resposta do PATCH;
- botão Informar de Devoluções (contexto novo, admin-only) mandando a lista
  da aba Acompanhamento no Threema.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Chamado, DevolucaoRastreio, Logistica

pytestmark = pytest.mark.asyncio


def _perms(*, view: bool = True, edit: bool = True, delete: bool = True) -> dict:
    return {"devolucoes": {"view": view, "edit": edit, "delete": delete}}


async def _n_chamados(db: AsyncSession, pedido: str) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(Chamado).where(Chamado.pedido_bling == pedido)
        )
    ).scalar_one()


# ------------------------------------------------- auto-chamado por motivo


async def test_create_motivo_golpe_abre_chamado_e_nao_duplica(client, db, make_user, auth_as):
    user = await make_user(permissions=_perms())
    auth_as(user)
    pedido = f"31{uuid4().hex[:6]}"

    r = await client.post(
        "/api/devolutions",
        json={
            "conta": "Shopee Jlas",
            "pedido_bling": pedido,
            "sku": "a001.pi",
            "produtos": "Fone Uranyx UFB10",
            "motivo_devolucao": "Golpe",
        },
    )
    assert r.status_code == 201

    chamados = (
        (await db.execute(select(Chamado).where(Chamado.pedido_bling == pedido)))
        .scalars()
        .all()
    )
    assert len(chamados) == 1
    ch = chamados[0]
    assert ch.origem == "devolucao"
    # canal "manual" = REGISTRO na aba Chamados (não entra na fila do robô).
    assert ch.canal == "manual"
    assert ch.conta == "Shopee Jlas"
    assert ch.produto == "Fone Uranyx UFB10"
    assert "Golpe" in (ch.observacao or "")

    # Kit: segunda linha do MESMO pedido (outro item) não duplica o chamado.
    r = await client.post(
        "/api/devolutions",
        json={
            "conta": "Shopee Jlas",
            "pedido_bling": pedido,
            "sku": "a002.pi",
            "motivo_devolucao": "Não recebido",
        },
    )
    assert r.status_code == 201
    assert await _n_chamados(db, pedido) == 1


async def test_patch_motivo_abre_chamado_so_para_motivos_da_lista(
    client, db, make_user, auth_as
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    pedido = f"31{uuid4().hex[:6]}"

    # Motivo fora da lista → nenhum chamado no create.
    r = await client.post(
        "/api/devolutions",
        json={"conta": "ml X", "pedido_bling": pedido, "motivo_devolucao": "Item Incorreto"},
    )
    assert r.status_code == 201
    dev_id = r.json()["id"]
    assert await _n_chamados(db, pedido) == 0

    # PATCH que NÃO mexe no motivo → continua sem chamado.
    r = await client.patch(f"/api/devolutions/{dev_id}", json={"observacao": "obs"})
    assert r.status_code == 200
    assert await _n_chamados(db, pedido) == 0

    # Motivo trocado pra um da lista → chamado aberto sozinho.
    r = await client.patch(
        f"/api/devolutions/{dev_id}", json={"motivo_devolucao": "Bloqueado"}
    )
    assert r.status_code == 200
    assert await _n_chamados(db, pedido) == 1

    # Repetir o PATCH não duplica.
    r = await client.patch(
        f"/api/devolutions/{dev_id}", json={"motivo_devolucao": "Bloqueado"}
    )
    assert r.status_code == 200
    assert await _n_chamados(db, pedido) == 1


# --------------------------------------- custo + técnico → Reembolso sozinho


async def test_custo_e_tecnico_ligam_reembolso_sozinho(client, make_user, auth_as):
    user = await make_user(permissions=_perms())
    auth_as(user)

    # No CREATE já completo → nasce com Reembolso ligado.
    r = await client.post(
        "/api/devolutions",
        json={"conta": "ml X", "custo_manutencao": 50, "tecnico": "Shark"},
    )
    assert r.status_code == 201
    assert r.json()["reembolso"] is True

    # Incompleto → nasce desligado; completa por PATCH em duas etapas.
    r = await client.post("/api/devolutions", json={"conta": "ml X"})
    assert r.status_code == 201
    body = r.json()
    assert body["reembolso"] is False
    dev_id = body["id"]

    r = await client.patch(f"/api/devolutions/{dev_id}", json={"custo_manutencao": 80})
    assert r.status_code == 200
    assert r.json()["reembolso"] is False  # só custo ainda não basta

    r = await client.patch(f"/api/devolutions/{dev_id}", json={"tecnico": "Bogota"})
    assert r.status_code == 200
    assert r.json()["reembolso"] is True  # custo + técnico → ligou

    # Desmarcado NA MÃO → edições de OUTROS campos não religam.
    r = await client.patch(f"/api/devolutions/{dev_id}", json={"reembolso": False})
    assert r.status_code == 200
    assert r.json()["reembolso"] is False
    r = await client.patch(f"/api/devolutions/{dev_id}", json={"observacao": "conferido"})
    assert r.status_code == 200
    assert r.json()["reembolso"] is False


# ------------------------------------------------- coluna Chamado na listagem


async def test_listagem_traz_chamado_mais_recente_do_pedido(client, db, make_user, auth_as):
    user = await make_user(permissions=_perms())
    auth_as(user)
    pedido = f"31{uuid4().hex[:6]}"

    r = await client.post(
        "/api/devolutions",
        json={"conta": "shopee Y", "pedido_bling": pedido, "motivo_devolucao": "Golpe"},
    )
    assert r.status_code == 201

    # O chamado auto ganha número da plataforma depois (operador preenche).
    ch = (
        await db.execute(select(Chamado).where(Chamado.pedido_bling == pedido))
    ).scalar_one()
    ch.chamado = "CH-777"
    await db.commit()

    r = await client.get("/api/devolutions", params={"search": pedido})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["tem_chamado"] is True
    assert items[0]["chamado_numero"] == "CH-777"
    assert items[0]["chamado_resolvido"] is False


# ------------------------- Acompanhamento: rastreio automático da Logística


async def _monta_acompanhamento(db: AsyncSession, p1: str, p2: str) -> None:
    """Fake da vw_devolucoes com DOIS pedidos em Aguardando Devolução (83957):
    `p1` shopee/Loja SP, parado há 3 dias, com rastreio+localização na
    Logística e localização MANUAL por cima; `p2` sem nada preenchido."""
    schema = get_settings().database_schema
    entrada = (datetime.now(UTC) - timedelta(days=3)).date()
    o1, o2 = uuid4(), uuid4()
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.execute(
        text(
            """
            INSERT INTO bling_orders (id, numero, aguardando_devolucao_data)
            VALUES (:o1, :p1, :entrada), (:o2, :p2, NULL)
            """
        ),
        {"o1": o1, "o2": o2, "p1": p1, "p2": p2, "entrada": entrada},
    )
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_devolucoes AS
            SELECT * FROM (VALUES
                ('2025-08-30T03:00:00+00:00'::timestamptz, '{p1}'::text,
                 'MP-{p1}'::text, 'shopee'::text, 'Loja SP'::text, NULL::bigint,
                 'Cliente Um'::text, 'Curitiba'::text, 'PR'::text,
                 'a001.pi'::text, 'Fone'::text, 1::integer, '{o1}'::uuid,
                 '83957'::text),
                ('2025-08-31T03:00:00+00:00'::timestamptz, '{p2}'::text,
                 NULL::text, NULL::text, NULL::text, NULL::bigint,
                 NULL::text, NULL::text, NULL::text,
                 'a002.pi'::text, 'Relogio'::text, 1::integer, '{o2}'::uuid,
                 '83957'::text)
            ) AS t(
                data, pedido_bling, pedido_marketplace, plataforma_bling,
                loja_nome, bling_loja_id, nome_destinatario, cidade_destino,
                uf_destino, sku, produto, quantidade, bling_order_item_id,
                situacao
            )
            """  # noqa: S608
        )
    )
    # Rastreio AUTOMÁTICO: o time preenche na aba Logística.
    db.add(
        Logistica(
            pedido_bling=p1,
            plataforma="shopee",
            rastreio="BR123456789",
            localizacao="Centro de triagem SP",
        )
    )
    # Localização MANUAL da aba Acompanhamento vale mais que a automática.
    db.add(
        DevolucaoRastreio(
            pedido_bling=p1,
            localizacao="Recebido no CD",
            localizacao_data=datetime.now(UTC),
        )
    )
    await db.commit()


async def _derruba_view(db: AsyncSession) -> None:
    schema = get_settings().database_schema
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.commit()


async def test_acompanhamento_puxa_rastreio_da_logistica_e_manual_vence(
    client, db, make_user, auth_as
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    p1, p2 = f"32{uuid4().hex[:6]}", f"33{uuid4().hex[:6]}"
    await _monta_acompanhamento(db, p1, p2)

    try:
        r = await client.get("/api/devolutions/acompanhamento")
        assert r.status_code == 200
        por_pedido = {i["pedido_bling"]: i for i in r.json()["items"]}
        assert set(por_pedido) >= {p1, p2}

        # p1: rastreio veio SOZINHO da Logística; localização manual venceu.
        assert por_pedido[p1]["rastreio"] == "BR123456789"
        assert por_pedido[p1]["localizacao"] == "Recebido no CD"
        assert por_pedido[p1]["localizacao_data"] is not None
        assert por_pedido[p1]["dias_em_devolucao"] == 3

        # p2: nada em lugar nenhum → campos vazios.
        assert por_pedido[p2]["rastreio"] is None
        assert por_pedido[p2]["localizacao"] is None

        # PATCH limpando a localização manual → resposta REVELA a automática
        # (mesma regra do GET; o front espelha a resposta na linha).
        r = await client.patch(
            f"/api/devolutions/acompanhamento/{p1}", json={"localizacao": ""}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["rastreio"] == "BR123456789"  # automático continua visível
        assert body["localizacao"] == "Centro de triagem SP"  # caiu no automático
        # Sem manual, a data vem da Logística — e a Logística de p1 não tem
        # carimbo nenhum (status_datas vazio) → fica em branco.
        assert body["localizacao_data"] is None
    finally:
        await _derruba_view(db)


async def test_acompanhamento_data_ultima_movimentacao_vem_da_logistica(
    client, db, make_user, auth_as
):
    """Eduardo 03/09: "a data da ult movimentação não está aparecendo". Com a
    localização automática (Logística), a data também é automática: o
    carimbo MAIS RECENTE do status_datas da linha (logistica_datas). Manual
    continua mandando quando existe."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    p1, p2 = f"34{uuid4().hex[:6]}", f"35{uuid4().hex[:6]}"
    await _monta_acompanhamento(db, p1, p2)
    # p2: só automático (Amazon sem rastreio, como em produção) com dois
    # carimbos — a data exibida tem que ser o mais recente.
    db.add(
        Logistica(
            pedido_bling=p2,
            plataforma="amazon",
            localizacao="Coletado → Macapá/AP",
            meli_status={"order_status": "Shipped", "easyship_status": "PickedUp"},
            status_datas={
                "order_status": {"em": "2026-08-20T10:00:00+00:00", "fonte": "aprox"},
                "easyship_status": {"em": "2026-08-25T15:30:00+00:00", "fonte": "aprox"},
            },
        )
    )
    await db.commit()

    try:
        r = await client.get("/api/devolutions/acompanhamento")
        assert r.status_code == 200
        por_pedido = {i["pedido_bling"]: i for i in r.json()["items"]}

        assert por_pedido[p2]["rastreio"] is None  # Amazon não expõe o código
        assert por_pedido[p2]["localizacao"] == "Coletado → Macapá/AP"
        assert por_pedido[p2]["localizacao_data"] is not None
        assert por_pedido[p2]["localizacao_data"].startswith("2026-08-25T15:30")

        # p1 tem localização MANUAL → data do manual (carimbada agora), não da
        # Logística.
        assert por_pedido[p1]["localizacao"] == "Recebido no CD"
        assert por_pedido[p1]["localizacao_data"].startswith(
            datetime.now(UTC).strftime("%Y-%m-%d")
        )

        # PATCH só do rastreio de p2 (localização segue automática) → a
        # resposta mantém a data da Logística (front espelha a resposta).
        r = await client.patch(
            f"/api/devolutions/acompanhamento/{p2}", json={"rastreio": "TBA000"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["rastreio"] == "TBA000"
        assert body["localizacao"] == "Coletado → Macapá/AP"
        assert body["localizacao_data"].startswith("2026-08-25T15:30")
    finally:
        await _derruba_view(db)


async def test_acompanhamento_mostra_status_da_devolucao_viva(
    client, db, make_user, auth_as
):
    """Eduardo 03/09: "tem mais um monte de pedido entregue e só vem em
    acompanhamentos" — a coluna mostrava a ENTREGA ("Pedido entregue") enquanto
    a devolução, aberta depois, seguia viva. Com devolução viva, a coluna passa
    a mostrar o status da devolução; a entrega vai pro `entrega_localizacao`.
    Localização MANUAL continua mandando; devolução encerrada volta à entrega."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    p1, p2 = f"36{uuid4().hex[:6]}", f"37{uuid4().hex[:6]}"
    await _monta_acompanhamento(db, p1, p2)
    # p2: Shopee entregue ao cliente + devolução em processamento.
    db.add(
        Logistica(
            pedido_bling=p2,
            plataforma="Shopee",
            rastreio="BR999",
            localizacao="Pedido entregue",
            meli_status={"order_status": "COMPLETED", "return_status": "PROCESSING"},
        )
    )
    await db.commit()

    try:
        r = await client.get("/api/devolutions/acompanhamento")
        assert r.status_code == 200
        por_pedido = {i["pedido_bling"]: i for i in r.json()["items"]}

        assert por_pedido[p2]["localizacao"] == "Devolução em processamento (Shopee)"
        assert por_pedido[p2]["entrega_localizacao"] == "Pedido entregue"
        assert por_pedido[p2]["rastreio"] == "BR999"

        # p1 tem localização MANUAL ("Recebido no CD") → manual manda, e a
        # Logística de p1 nem tem devolução.
        assert por_pedido[p1]["localizacao"] == "Recebido no CD"
        assert por_pedido[p1]["entrega_localizacao"] is None

        # PATCH só do rastreio de p2 → resposta mantém o status da devolução.
        r = await client.patch(
            f"/api/devolutions/acompanhamento/{p2}", json={"rastreio": "BR1000"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["localizacao"] == "Devolução em processamento (Shopee)"
        assert body["entrega_localizacao"] == "Pedido entregue"

        # Devolução cancelada → volta a mostrar a entrega.
        lg = (
            await db.execute(select(Logistica).where(Logistica.pedido_bling == p2))
        ).scalar_one()
        lg.meli_status = {"order_status": "COMPLETED", "return_status": "CANCELLED"}
        await db.commit()
        r = await client.get("/api/devolutions/acompanhamento")
        por_pedido = {i["pedido_bling"]: i for i in r.json()["items"]}
        assert por_pedido[p2]["localizacao"] == "Pedido entregue"
        assert por_pedido[p2]["entrega_localizacao"] is None
    finally:
        await _derruba_view(db)


async def test_acompanhamento_usa_rastreio_do_pacote_que_volta(
    client, db, make_user, auth_as
):
    """Eduardo 03/09: "o TikTok não está pegando o número de rastreio correto
    (291869)" / "em devolução desde todas as datas estão iguais". Com o sync do
    retorno gravado (devolucao_rastreio.*_auto): o Rastreio é o código do
    pacote que VOLTA (não o da entrega), a localização é o status da devolução
    + o último evento do 17track, e "Em devolução desde" é o dia em que o
    cliente abriu a devolução."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    p1, p2 = f"38{uuid4().hex[:6]}", f"39{uuid4().hex[:6]}"
    await _monta_acompanhamento(db, p1, p2)
    db.add(
        Logistica(
            pedido_bling=p2,
            plataforma="TikTok",
            rastreio="999881795110423",  # entrega original
            localizacao="Package has been delivered!",
            meli_status={"order_status": "DELIVERED", "return_status": "BUYER_SHIPPED_ITEM"},
        )
    )
    db.add(
        DevolucaoRastreio(
            pedido_bling=p2,
            rastreio_auto="AP418496864BR",
            transportadora_auto="Correios",
            localizacao_auto="Piracicaba/SP — Saiu para entrega",
            localizacao_auto_data=datetime(2026, 9, 3, 13, 57, tzinfo=UTC),
            devolucao_status_auto="BUYER_SHIPPED_ITEM",
            fonte_auto="tiktok",
            devolucao_criada_em=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
        )
    )
    await db.commit()

    try:
        r = await client.get("/api/devolutions/acompanhamento")
        assert r.status_code == 200
        item = {i["pedido_bling"]: i for i in r.json()["items"]}[p2]
        assert item["rastreio"] == "AP418496864BR"
        assert item["localizacao"] == (
            "Cliente enviou o item de volta · Piracicaba/SP — Saiu para entrega"
        )
        assert item["entrega_localizacao"] == "Package has been delivered!"
        assert item["localizacao_data"].startswith("2026-09-03T13:57")
        assert item["aguardando_devolucao_data"] == "2026-08-24"
        esperado = (datetime.now(UTC).date() - datetime(2026, 8, 24).date()).days
        assert item["dias_em_devolucao"] == esperado

        # PATCH só do rastreio manual → resposta mantém status + evento do retorno.
        r = await client.patch(
            f"/api/devolutions/acompanhamento/{p2}", json={"rastreio": "MANUAL1"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["rastreio"] == "MANUAL1"
        assert body["localizacao"].startswith("Cliente enviou o item de volta")
    finally:
        await _derruba_view(db)


async def test_em_devolucao_desde_estimada_e_manual(client, db, make_user, auth_as):
    """Eduardo 03/09 (287144): "está aparecendo um dia mas a data está 19/08".
    Sem caso de devolução no marketplace, a data vem do carimbo do sinal na
    Logística (pacote voltando); e dá pra corrigir na mão — vazio volta ao
    automático."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    p1, p2 = f"40{uuid4().hex[:6]}", f"41{uuid4().hex[:6]}"
    await _monta_acompanhamento(db, p1, p2)
    db.add(
        Logistica(
            pedido_bling=p2,
            plataforma="Mercado Livre",
            rastreio="AP123",
            localizacao="Retornando ao remetente → São João da Barra/RJ",
            meli_status={
                "ship_status": "not_delivered", "cancel_group": "shipment",
                "order_status": "cancelled", "ship_substatus": "returning_to_sender",
            },
            status_datas={
                "ship_status": {"em": "2026-08-21T10:00:00+00:00", "fonte": "plataforma"},
                "ship_substatus": {"em": "2026-08-22T10:00:00+00:00", "fonte": "aprox"},
                "order_status": {"em": "2026-08-22T12:00:00+00:00", "fonte": "plataforma"},
            },
        )
    )
    await db.commit()
    hoje = datetime.now(UTC).date()

    try:
        r = await client.get("/api/devolutions/acompanhamento")
        por_pedido = {i["pedido_bling"]: i for i in r.json()["items"]}
        # p2: sem devolução no marketplace → carimbo do sinal (22/08), ESTIMADA
        assert por_pedido[p2]["aguardando_devolucao_data"] == "2026-08-22"
        assert por_pedido[p2]["aguardando_devolucao_data_estimada"] is True
        assert por_pedido[p2]["dias_em_devolucao"] == (hoje - datetime(2026, 8, 22).date()).days
        # p1: sem sinal nenhum → entrada no Bling (seed = hoje - 3), real
        assert por_pedido[p1]["dias_em_devolucao"] == 3
        assert por_pedido[p1]["aguardando_devolucao_data_estimada"] is False

        # Corrige na mão → vale a data digitada (não é mais estimativa).
        r = await client.patch(
            f"/api/devolutions/acompanhamento/{p2}", json={"em_devolucao_desde": "2026-08-19"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["aguardando_devolucao_data"] == "2026-08-19"
        assert r.json()["aguardando_devolucao_data_estimada"] is False
        assert r.json()["dias_em_devolucao"] == (hoje - datetime(2026, 8, 19).date()).days
        r = await client.get("/api/devolutions/acompanhamento")
        por_pedido = {i["pedido_bling"]: i for i in r.json()["items"]}
        assert por_pedido[p2]["aguardando_devolucao_data"] == "2026-08-19"

        # PATCH de outro campo não mexe na data manual.
        r = await client.patch(
            f"/api/devolutions/acompanhamento/{p2}", json={"rastreio": "X1"}
        )
        assert r.json()["aguardando_devolucao_data"] == "2026-08-19"

        # null limpa → volta ao automático (22/08).
        r = await client.patch(
            f"/api/devolutions/acompanhamento/{p2}", json={"em_devolucao_desde": None}
        )
        assert r.status_code == 200
        assert r.json()["aguardando_devolucao_data"] == "2026-08-22"
    finally:
        await _derruba_view(db)


# ------------------------------- Informar de Devoluções (admin-only, Threema)


async def test_informar_devolucoes_gate_e_envio(
    client, db, make_user, auth_as, monkeypatch
):
    from app.services import threema

    nao_admin = await make_user(permissions=_perms())
    auth_as(nao_admin)
    assert (await client.get("/api/informar/devolucoes")).status_code == 403

    from app.models import UserRole

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    assert (await client.get("/api/informar/devolucoes")).status_code == 200

    p1, p2 = f"34{uuid4().hex[:6]}", f"35{uuid4().hex[:6]}"
    await _monta_acompanhamento(db, p1, p2)

    enviados: list[str] = []

    class _FakeThreema:
        def __init__(self, *a: object, **k: object) -> None: ...

        async def send_to_all(self, msg: str, recipients: list[str]) -> dict:
            enviados.append(msg)
            return {"sent": list(recipients), "failed": []}

    monkeypatch.setattr(threema, "ThreemaClient", _FakeThreema)
    monkeypatch.setattr(
        get_settings(), "threema_recipient_names", "AAAA1111:Ana", raising=False
    )
    monkeypatch.setattr(get_settings(), "threema_recipients", "", raising=False)

    try:
        r = await client.put("/api/informar/devolucoes", json={"recipients": ["AAAA1111"]})
        assert r.status_code == 200
        r = await client.post("/api/informar/devolucoes/enviar")
        assert r.status_code == 200
        body = r.json()
        assert body["pedidos"] == 2
        assert body["mensagens"] == 1
        assert body["sent"] == ["AAAA1111"]

        linhas = enviados[0].splitlines()
        assert linhas[0] == "DaVinci — Devoluções: pedidos aguardando devolução (2)"
        # Ordem da aba: sem data de entrada primeiro (NULLS FIRST), depois p1.
        assert linhas[1] == f"Pedido {p2} (Sem loja) — sem localização"
        # p1 usa a localização MANUAL (vence a automática) e o tempo parado.
        assert linhas[2] == f"Pedido {p1} (shopee Loja SP) — 3 dias — Recebido no CD"
    finally:
        await _derruba_view(db)
