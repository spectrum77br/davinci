"""Chamados — aba de Pós-venda (CRUD, histórico/réplica, anexos, status Bling,
réplica automática + acompanhamento do cron, valor ao resolver, filtro por conta)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.models import BlingOrder, Chamado, ChamadoMensagem, SituacaoBling, StoreInfo
from app.services import chamados as svc
from app.services import logistica_bling

pytestmark = pytest.mark.asyncio

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _perms(*, view: bool = True, edit: bool = True, delete: bool = True) -> dict:
    return {"chamados": {"view": view, "edit": edit, "delete": delete}}


async def _seed_pedido(db, user, *, numero: str = "293000", situacao: str = "83960") -> None:
    db.add_all(
        [
            SituacaoBling(id=83960, nome="Problemas"),
            SituacaoBling(id=545902, nome="Resolvido"),
            SituacaoBling(id=83956, nome="Perdimento"),
            # Apagada no Bling (sync marcou inativa): fica no catálogo pros
            # pedidos antigos, mas NÃO aparece nos dropdowns.
            SituacaoBling(id=99001, nome="Enviado Geral CI", ativo=False),
        ]
    )
    db.add(
        StoreInfo(
            user_id=user.id,
            platform="ml",
            account_name="aguiar",
            bling_store_id="55",
        )
    )
    db.add_all(
        [
            BlingOrder(
                id=uuid4(),
                numero=numero,
                numeroloja="2000011",
                bling_id=123456,
                situacao=situacao,
                loja="55",
                data=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
                item_index=0,
                item_codigo="uaf001m1.110",
                item_descricao="airfryer vidro UAF001 M1 110v",
                item_quantidade=1,
            ),
            BlingOrder(
                id=uuid4(),
                numero=numero,
                numeroloja="2000011",
                bling_id=123456,
                situacao=situacao,
                loja="55",
                data=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
                item_index=1,
                item_codigo="a001",
                item_descricao="embalagem",
                item_quantidade=1,
            ),
        ]
    )
    await db.commit()


class _FakeML:
    def __init__(
        self, *, closed: bool = False, actions: tuple[str, ...] = ("send_message_to_mediator",)
    ):
        self.closed = closed
        self.actions = actions
        self.sent: list[tuple[str, str, str]] = []
        self.calls = 0

    async def get_claim(self, claim_id):
        self.calls += 1
        return {
            "status": "closed" if self.closed else "opened",
            "players": [
                {
                    "role": "respondent",
                    "available_actions": [{"action": a} for a in self.actions],
                }
            ],
        }

    async def send_claim_message(
        self, claim_id, message, *, receiver_role="mediator", attachments=None
    ):
        self.sent.append((str(claim_id), message, receiver_role))
        return {"ok": True}


class _FakeBling:
    def __init__(self, ja_na_situacao: int | None = None):
        self.situacao_set: list[tuple[int, int]] = []
        # Situação em que o pedido JÁ está: o cliente real devolve False
        # (o Bling responde 400 "mesma situação" e ele engole).
        self.ja_na_situacao = ja_na_situacao

    async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> bool:
        self.situacao_set.append((bling_order_id, situacao_id))
        return situacao_id != self.ja_na_situacao


def _sem_bling_ao_resolver(monkeypatch) -> list[str]:
    """19/09 (Vinicius): resolver com `pedido_bling` EXIGE a nova situação no Bling.
    Os testes que não são sobre o Bling passam uma e este stub só anota o nome
    (sem catálogo de situações nem cliente do Bling)."""
    aplicadas: list[str] = []

    async def _aplicar(session, ch, nome):
        aplicadas.append(nome)
        ch.status_bling = nome
        session.add(svc.registrar_sistema(ch, f"Status Bling alterado para {nome}"))
        return {"bling_order_id": 0, "situacao": nome, "situacao_id": 0}

    monkeypatch.setattr(svc, "aplicar_status_bling", _aplicar)
    return aplicadas


async def test_list_requires_view_permission(client, make_user, auth_as):
    user = await make_user(permissions={})
    auth_as(user)
    assert (await client.get("/api/chamados")).status_code == 403


async def test_create_preenche_do_pedido_e_lista_status_atual(client, make_user, auth_as, db):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user)

    r = await client.post("/api/chamados", json={"origem": "logistica", "pedido_bling": "293000"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["pedido_marketplace"] == "2000011"
    assert c["plataforma"] == "ml"
    assert c["conta"] == "aguiar"
    assert c["produto"] == "airfryer vidro UAF001 M1 110v; embalagem"
    assert c["sku"] == "uaf001m1.110, a001"
    assert c["status_bling"] == "Problemas"
    assert c["data"] == "2026-09-01"
    # Padrão da planilha: Logística abre em "Problemas".
    assert c["alterar_status_bling"] == "Problemas"
    assert c["canal"] == "manual"
    assert c["mensagens_total"] == 1  # evento "Chamado registrado"

    # Margem NÃO altera status; sem pedido no espelho fica o que veio.
    r2 = await client.post("/api/chamados", json={"origem": "margem", "pedido_marketplace": "XYZ"})
    assert r2.status_code == 201
    assert r2.json()["alterar_status_bling"] is None
    assert r2.json()["plataforma"] is None

    lst = await client.get("/api/chamados")
    assert lst.status_code == 200
    body = lst.json()
    assert body["total"] == 2
    por_pedido = {i["pedido_bling"]: i for i in body["items"]}
    assert por_pedido["293000"]["status_bling_atual"] == "Problemas"
    assert body["plataformas"] == ["ml"]
    assert body["contas"] == ["aguiar"]

    # filtro por conta (Eduardo 15/09: "ex. ML Aguiar 2") — sem caixa
    assert (await client.get("/api/chamados", params={"conta": "aguiar"})).json()["total"] == 1
    assert (await client.get("/api/chamados", params={"conta": "AGUIAR"})).json()["total"] == 1
    assert (await client.get("/api/chamados", params={"conta": "outra"})).json()["total"] == 0
    # as contas do dropdown seguem a plataforma filtrada
    por_plat = await client.get("/api/chamados", params={"plataforma": "ml"})
    assert por_plat.json()["contas"] == ["aguiar"]
    por_plat = await client.get("/api/chamados", params={"plataforma": "shopee"})
    assert por_plat.json()["contas"] == []

    # pedido obrigatório
    assert (await client.post("/api/chamados", json={"origem": "margem"})).status_code == 422
    # lookup direto
    lk = await client.get("/api/chamados/pedido-lookup", params={"pedido": "2000011"})
    assert lk.status_code == 200 and lk.json()["pedido_bling"] == "293000"
    assert (
        await client.get("/api/chamados/pedido-lookup", params={"pedido": "nope"})
    ).status_code == 404
    # só situações que existem no Bling (a "Enviado Geral CI" inativa fica fora)
    sit = await client.get("/api/chamados/situacoes")
    assert sit.json()["nomes"] == ["Perdimento", "Problemas", "Resolvido"]


async def test_replica_manual_registra_e_robo_enfileira(client, make_user, auth_as, db):
    user = await make_user(permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/chamados", json={"origem": "margem", "pedido_bling": "1", "canal": "manual"}
    )
    cid = r.json()["id"]

    rep = await client.post(
        f"/api/chamados/{cid}/mensagens",
        data={"texto": "Segue a réplica"},
        files=[("files", ("foto.png", PNG, "image/png"))],
    )
    assert rep.status_code == 201, rep.text
    m = rep.json()
    assert m["status"] == "registrada"
    assert m["tipo"] == "replica"
    assert m["autor_nome"]
    assert len(m["anexos"]) == 1

    anexo = await client.get(f"/api/chamados/anexos/{m['anexos'][0]['id']}")
    assert anexo.status_code == 200
    assert anexo.headers["content-type"].startswith("image/png")
    assert anexo.content == PNG

    hist = await client.get(f"/api/chamados/{cid}/mensagens")
    tipos = [h["tipo"] for h in hist.json()]
    assert tipos == ["sistema", "replica"]

    # canal robô → fica pendente na fila (o robô de browser marca enviada)
    assert (await client.patch(f"/api/chamados/{cid}", json={"canal": "robo"})).status_code == 200
    rep2 = await client.post(f"/api/chamados/{cid}/mensagens", data={"texto": "de novo"})
    assert rep2.json()["status"] == "pendente"
    assert rep2.json()["canal"] == "robo"

    # texto vazio / anexo de tipo inválido
    assert (
        await client.post(f"/api/chamados/{cid}/mensagens", data={"texto": "  "})
    ).status_code == 422
    bad = await client.post(
        f"/api/chamados/{cid}/mensagens",
        data={"texto": "x"},
        files=[("files", ("a.txt", b"oi", "text/plain"))],
    )
    assert bad.status_code == 400


async def test_replica_api_ml_envia_ou_falha(client, make_user, auth_as, db, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeML()

    async def _fake_client(session, conta):
        return fake

    monkeypatch.setattr(svc, "_ml_client_para", _fake_client)
    r = await client.post(
        "/api/chamados",
        json={
            "origem": "logistica",
            "pedido_bling": "9",
            "plataforma": "ml",
            "conta": "aguiar",
            "canal": "api",
            "chamado": "555",
        },
    )
    cid = r.json()["id"]
    rep = await client.post(f"/api/chamados/{cid}/mensagens", data={"texto": "Olá mediador"})
    assert rep.status_code == 201
    assert rep.json()["status"] == "enviada"
    assert rep.json()["enviada_at"]
    assert fake.sent == [("555", "Olá mediador", "mediator")]

    # claim encerrado → falhou com código, mensagem NÃO se perde
    fake.closed = True
    rep2 = await client.post(f"/api/chamados/{cid}/mensagens", data={"texto": "tentativa"})
    assert rep2.status_code == 201
    assert rep2.json()["status"] == "falhou"
    assert rep2.json()["erro"] == "chamado_encerrado"


async def test_alterar_status_bling_e_resolver(client, make_user, auth_as, db, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user)
    fake = _FakeBling()

    async def _fake_bling(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_bling)
    r = await client.post("/api/chamados", json={"origem": "logistica", "pedido_bling": "293000"})
    cid = r.json()["id"]

    st = await client.post(
        f"/api/chamados/{cid}/alterar-status-bling", json={"situacao": "Problemas"}
    )
    assert st.status_code == 200, st.text
    assert st.json() == {"bling_order_id": 123456, "situacao": "Problemas", "situacao_id": 83960}
    assert fake.situacao_set == [(123456, 83960)]

    desconhecida = await client.post(
        f"/api/chamados/{cid}/alterar-status-bling", json={"situacao": "Nada"}
    )
    assert desconhecida.status_code == 422
    assert desconhecida.json()["detail"]["code"] == "chamado_status_bling_desconhecido"

    # resolver SEM o valor (lucro/prejuízo) é recusado antes de tocar no Bling
    # — Eduardo 15/09: "deixar como campo obrigatório antes de aceitar o resolver"
    sem_valor = await client.post(
        f"/api/chamados/{cid}/resolver", json={"resolvido": True, "situacao": "Perdimento"}
    )
    assert sem_valor.status_code == 422
    assert sem_valor.json()["detail"]["code"] == "chamado_valor_obrigatorio"
    assert fake.situacao_set == [(123456, 83960)]
    # 19/09 (Vinicius): com pedido no Bling, a NOVA situação é obrigatória ("status
    # atual do Bling e o que vai trocar, obrigatório")
    sem_situacao = await client.post(
        f"/api/chamados/{cid}/resolver", json={"resolvido": True, "valor_recuperado": -150.5}
    )
    assert sem_situacao.status_code == 422
    assert sem_situacao.json()["detail"]["code"] == "chamado_situacao_obrigatoria"
    assert fake.situacao_set == [(123456, 83960)]
    # a listagem traz a situação ATUAL do pedido (lookup vivo) pra janela mostrar
    assert (await client.get("/api/chamados")).json()["items"][0]["status_bling_atual"] == (
        "Problemas"
    )

    # resolver aplicando Perdimento junto, com prejuízo de R$ 150,50
    res = await client.post(
        f"/api/chamados/{cid}/resolver",
        json={"resolvido": True, "situacao": "Perdimento", "valor_recuperado": -150.5},
    )
    assert res.status_code == 200, res.text
    assert res.json()["resolvido"] is True
    assert res.json()["resolvido_at"]
    assert res.json()["status_bling"] == "Perdimento"
    assert float(res.json()["valor_recuperado"]) == -150.5
    assert fake.situacao_set[-1] == (123456, 83956)

    hist = await client.get(f"/api/chamados/{cid}/mensagens")
    textos = [h["texto"] for h in hist.json() if h["tipo"] == "sistema"]
    assert any("Status Bling alterado para Problemas" in t for t in textos)
    assert any("Perdimento" in t for t in textos)
    assert any("resolvido" in t and "prejuízo de R$ 150,50" in t for t in textos)

    # some de "abertos", aparece em "resolvidos"
    assert (await client.get("/api/chamados")).json()["total"] == 0
    assert (await client.get("/api/chamados", params={"mostrar": "resolvidos"})).json()[
        "total"
    ] == 1

    # reabrir e apagar
    re = await client.post(f"/api/chamados/{cid}/resolver", json={"resolvido": False})
    assert re.json()["resolvido"] is False
    assert (await client.delete(f"/api/chamados/{cid}")).status_code == 204
    assert (await client.get("/api/chamados", params={"mostrar": "todos"})).json()["total"] == 0


async def test_resolver_com_pedido_ja_na_situacao_nao_trava(
    client, make_user, auth_as, db, monkeypatch
):
    """Vinicius, 21/09/2026: pedido 295680 já estava Resolvido no Bling e o
    resolver escolhendo "Resolvido" de novo travava a janela com o 400 "A venda
    possui a mesma situação". Mesma situação = nada a mudar: o chamado fecha e o
    histórico diz que o status foi mantido, não "alterado"."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user)
    fake = _FakeBling(ja_na_situacao=545902)

    async def _fake_bling(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_bling)
    cid = (
        await client.post("/api/chamados", json={"origem": "logistica", "pedido_bling": "293000"})
    ).json()["id"]

    res = await client.post(
        f"/api/chamados/{cid}/resolver",
        json={"resolvido": True, "situacao": "Resolvido", "valor_recuperado": 16},
    )
    assert res.status_code == 200, res.text
    assert res.json()["resolvido"] is True
    assert res.json()["status_bling"] == "Resolvido"
    assert fake.situacao_set == [(123456, 545902)]
    textos = [
        h["texto"]
        for h in (await client.get(f"/api/chamados/{cid}/mensagens")).json()
        if h["tipo"] == "sistema"
    ]
    assert any("Status Bling já era Resolvido (mantido)" in t for t in textos)
    assert not any("alterado para Resolvido" in t for t in textos)


async def test_resolver_grava_observacao_na_coluna_e_no_historico(
    client, make_user, auth_as, db, monkeypatch
):
    """Vinicius, 23/09/2026 (pedido 294554): a janela Resolver tem a Observação —
    a mesma da coluna — pra registrar o que aconteceu ("fizemos duas disputas e a
    Shopee recusou"). Grava na coluna e fica no evento "resolvido" do histórico;
    cliente que não manda o campo não apaga a observação que já estava lá."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user)
    fake = _FakeBling()

    async def _fake_bling(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_bling)
    cid = (
        await client.post(
            "/api/chamados",
            json={"origem": "logistica", "pedido_bling": "293000", "observacao": "antiga"},
        )
    ).json()["id"]

    obs = "fizemos duas disputas e a Shopee recusou as duas"
    res = await client.post(
        f"/api/chamados/{cid}/resolver",
        json={
            "resolvido": True,
            "situacao": "Perdimento",
            "valor_recuperado": -1704.5,
            "observacao": f"  {obs}  ",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["observacao"] == obs
    textos = [
        h["texto"]
        for h in (await client.get(f"/api/chamados/{cid}/mensagens")).json()
        if h["tipo"] == "sistema"
    ]
    assert any(
        t.startswith("Chamado marcado como resolvido")
        and "prejuízo de R$ 1.704,50" in t
        and t.endswith(f"\nObs.: {obs}")
        for t in textos
    )

    # reabre e resolve de novo SEM o campo (cliente antigo): a coluna fica como estava
    await client.post(f"/api/chamados/{cid}/resolver", json={"resolvido": False})
    de_novo = await client.post(
        f"/api/chamados/{cid}/resolver",
        json={"resolvido": True, "situacao": "Resolvido", "valor_recuperado": 0},
    )
    assert de_novo.status_code == 200, de_novo.text
    assert de_novo.json()["observacao"] == obs

    # mandou vazio = limpou
    await client.post(f"/api/chamados/{cid}/resolver", json={"resolvido": False})
    limpa = await client.post(
        f"/api/chamados/{cid}/resolver",
        json={
            "resolvido": True,
            "situacao": "Resolvido",
            "valor_recuperado": 0,
            "observacao": "   ",
        },
    )
    assert limpa.status_code == 200, limpa.text
    assert limpa.json()["observacao"] is None


async def test_valor_recuperado_grava_e_valida(client, make_user, auth_as):
    """Coluna "Valor" do Controle (Eduardo 03/09): resultado do chamado em R$;
    negativo = prejuízo (Eduardo 15/09); vazio (null) limpa."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/chamados", json={"origem": "margem", "pedido_bling": "8", "canal": "manual"}
    )
    cid = r.json()["id"]
    assert r.json()["valor_recuperado"] is None

    p = await client.patch(f"/api/chamados/{cid}", json={"valor_recuperado": 123.45})
    assert p.status_code == 200, p.text
    assert float(p.json()["valor_recuperado"]) == 123.45

    lst = await client.get("/api/chamados", params={"search": "8"})
    assert float(lst.json()["items"][0]["valor_recuperado"]) == 123.45

    neg = await client.patch(f"/api/chamados/{cid}", json={"valor_recuperado": -1})
    assert neg.status_code == 200, neg.text
    assert float(neg.json()["valor_recuperado"]) == -1

    p = await client.patch(f"/api/chamados/{cid}", json={"valor_recuperado": None})
    assert p.status_code == 200
    assert p.json()["valor_recuperado"] is None


async def test_replica_automatica_respeita_dias(client, make_user, auth_as, db, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    _sem_bling_ao_resolver(monkeypatch)
    r = await client.post(
        "/api/chamados", json={"origem": "margem", "pedido_bling": "7", "canal": "manual"}
    )
    cid = r.json()["id"]

    # ligar carimba o "último envio" = agora → 1ª réplica só depois de N dias
    p = await client.patch(
        f"/api/chamados/{cid}",
        json={"auto_ligada": True, "auto_dias": 2, "auto_mensagem": "Aguardo retorno."},
    )
    assert p.status_code == 200, p.text
    assert p.json()["auto_ultimo_envio_at"]
    assert p.json()["auto_proximo_envio_at"]
    ligado_em = datetime.fromisoformat(p.json()["auto_ultimo_envio_at"])

    # anexo da réplica automática
    up = await client.post(
        f"/api/chamados/{cid}/anexos-auto",
        files={"file": ("auto.png", PNG, "image/png")},
    )
    assert up.status_code == 201
    lst = await client.get("/api/chamados")
    assert [a["mensagem_id"] for a in lst.json()["items"][0]["anexos_auto"]] == [None]

    # 1 dia depois: nada
    out = await svc.run_replica_automatica(db, agora=ligado_em + timedelta(days=1))
    assert out["enviados"] == 0
    # 2 dias + 1 min: envia
    out = await svc.run_replica_automatica(db, agora=ligado_em + timedelta(days=2, minutes=1))
    assert out["enviados"] == 1
    msgs = (
        (
            await db.execute(
                select(ChamadoMensagem)
                .where(ChamadoMensagem.chamado_id == cid)
                .order_by(ChamadoMensagem.created_at)
            )
        )
        .scalars()
        .all()
    )
    auto = [m for m in msgs if m.tipo == "replica_auto"]
    assert len(auto) == 1
    assert auto[0].texto == "Aguardo retorno."
    assert auto[0].status == "registrada"  # canal manual
    ch = (await db.execute(select(Chamado).where(Chamado.id == cid))).scalar_one()
    assert ch.auto_ultimo_envio_at == ligado_em + timedelta(days=2, minutes=1)

    # mesma hora de novo: não repete
    out = await svc.run_replica_automatica(db, agora=ligado_em + timedelta(days=2, minutes=2))
    assert out["enviados"] == 0

    # resolvido → desliga e para (valor é obrigatório ao resolver — 15/09)
    fechado = await client.post(
        f"/api/chamados/{cid}/resolver",
        json={"resolvido": True, "valor_recuperado": 0, "situacao": "Resolvido"},
    )
    assert fechado.status_code == 200, fechado.text
    out = await svc.run_replica_automatica(db, agora=ligado_em + timedelta(days=10))
    assert out["enviados"] == 0
    await db.refresh(ch)
    assert ch.auto_ligada is False


async def test_replica_automatica_para_no_encerrado_pela_plataforma(
    client, make_user, auth_as, db
):
    """19/09 (ajuste A2): a Shopee decidiu (compensação paga → Encerrado) num chamado
    com réplica automática ligada — a próxima passada do cron NÃO pode cobrar a
    plataforma de novo: o closer desliga `auto_ligada` e a query do cron pula os
    Encerrados de qualquer jeito."""
    from decimal import Decimal

    from app.services import chamados_devolucao_sync as sync

    user = await make_user(permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/chamados",
        json={"origem": "devolucao", "pedido_bling": "7", "canal": "api", "plataforma": "shopee"},
    )
    cid = r.json()["id"]
    p = await client.patch(
        f"/api/chamados/{cid}",
        json={"auto_ligada": True, "auto_dias": 2, "auto_mensagem": "Aguardo retorno."},
    )
    assert p.status_code == 200, p.text
    ligado_em = datetime.fromisoformat(p.json()["auto_ultimo_envio_at"])
    ch = (await db.execute(select(Chamado).where(Chamado.id == cid))).scalar_one()
    ch.status_plataforma = svc.STATUS_GANHAMOS
    ch.status_plataforma_at = datetime.now(UTC)
    await sync._encerrado_na_plataforma(
        db, ch, "shopee:compensacao_paga", valor=Decimal("120.00")
    )
    await db.commit()
    await db.refresh(ch)
    assert ch.auto_ligada is False and ch.status_plataforma == svc.STATUS_GANHAMOS
    assert float(ch.valor_sugerido) == 120.0 and ch.resolvido is False
    # mesmo que alguém religasse a flag, o cron não enfileira nada num Encerrado
    ch.auto_ligada = True
    await db.commit()
    out = await svc.run_replica_automatica(db, agora=ligado_em + timedelta(days=10))
    assert out["enviados"] == 0 and out["verificados"] == 0
    msgs = (
        await db.execute(select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == cid))
    ).scalars().all()
    assert not [m for m in msgs if m.tipo == "replica_auto"]
    row = (await client.get("/api/chamados")).json()["items"][0]
    assert row["auto_proximo_envio_at"] is None and row["status_aba"] == "encerrado"


_TOKEN = "tok-chamados-teste"  # noqa: S105


async def test_agent_fluxo_completo(client, make_user, auth_as, db, monkeypatch):
    """Robô registra o chamado aberto (protocolo) → operador responde pela aba
    (canal robô = pendente) → lease entrega a tarefa → resultado marca enviada
    → monitor grava a resposta e diz que a plataforma encerrou → Encerrado (19/09:
    nada fecha sozinho) → pessoa conclui."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    hdr = {"X-Agent-Token": _TOKEN}
    user = await make_user(permissions=_perms())
    auth_as(user)

    # sem token → 401
    assert (await client.post("/api/chamados/agent/lease", json={})).status_code == 401

    # 1) robô abriu o chamado de frete e capturou o protocolo
    r = await client.post(
        "/api/chamados/agent/registrar",
        headers=hdr,
        json={
            "pedido_bling": "293412",
            "origem": "margem",
            "conta": "aguiar",
            "pedido_marketplace": "2000018202286338",
            "origem_ref": "refund-1",
            "chamado": "462456014",
            "chamado_url": "https://www.mercadolivre.com.br/cases/detail/462456014",
            "mensagem": "Pedido 2000018202286338: diferença de frete R$ 12,00.",
            "status_envio": "enviada",
        },
    )
    assert r.status_code == 200, r.text
    reg = r.json()
    assert reg["criado"] is True and reg["mensagem_id"]
    cid = reg["chamado_id"]

    # registrar de novo o mesmo pedido NÃO duplica (atualiza)
    r2 = await client.post(
        "/api/chamados/agent/registrar",
        headers=hdr,
        json={"pedido_bling": "293412", "origem": "margem", "observacao": "quarentena"},
    )
    assert r2.json()["criado"] is False and r2.json()["chamado_id"] == cid

    lst = await client.get("/api/chamados")
    item = lst.json()["items"][0]
    assert item["canal"] == "robo" and item["chamado"] == "462456014"
    assert item["mensagens_total"] == 2  # sistema + abertura

    # 2) operador responde pela aba → fica pendente na fila do robô
    rep = await client.post(
        f"/api/chamados/{cid}/mensagens", data={"texto": "Segue o comprovante."}
    )
    assert rep.json()["status"] == "pendente"
    mid = rep.json()["id"]

    # 3) lease entrega a tarefa (responder, pois já tem protocolo) e marca enviando
    lease = await client.post("/api/chamados/agent/lease", headers=hdr, json={"limite": 10})
    assert lease.status_code == 200, lease.text
    tarefas = lease.json()["tarefas"]
    assert len(tarefas) == 1
    t = tarefas[0]
    assert t["tipo"] == "responder" and t["mensagem_id"] == mid and t["chamado"] == "462456014"
    assert t["texto"] == "Segue o comprovante."
    # segundo lease não entrega de novo (está enviando)
    assert (await client.post("/api/chamados/agent/lease", headers=hdr, json={})).json()[
        "tarefas"
    ] == []

    # 4) robô guarda um print da evidência e devolve enviada
    up = await client.post(
        "/api/chamados/agent/anexo",
        headers=hdr,
        data={"chamado_id": cid, "mensagem_id": mid},
        files={"file": ("print.png", PNG, "image/png")},
    )
    assert up.status_code == 201, up.text
    got = await client.get(f"/api/chamados/agent/anexos/{up.json()['id']}", headers=hdr)
    assert got.status_code == 200 and got.content == PNG

    res = await client.post(
        "/api/chamados/agent/resultado", headers=hdr, json={"mensagem_id": mid, "ok": True}
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "enviada" and res.json()["enviada_at"]
    assert len(res.json()["anexos"]) == 1

    # 5) monitor lê a resposta do ML e diz que a plataforma encerrou → Encerrado, não fecha
    rec = await client.post(
        "/api/chamados/agent/recebida",
        headers=hdr,
        json={
            "pedido_bling": "293412",
            "chamado": "462456014",
            "texto": "Analisamos e o reembolso de R$ 12,00 será creditado.",
            "resumo": "Reembolso aprovado R$ 12,00",
            "resolvido": True,
        },
    )
    assert rec.status_code == 200, rec.text
    assert rec.json()["resolvido"] is False
    hist = await client.get(f"/api/chamados/{cid}/mensagens")
    direcoes = [(h["direcao"], h["tipo"], h["status"]) for h in hist.json()]
    assert ("recebida", "resposta", "registrada") in direcoes
    assert any(h["texto"].startswith("Reembolso aprovado") for h in hist.json())
    assert any("Monitor: plataforma encerrou" in h["texto"] for h in hist.json())
    item = (await client.get("/api/chamados")).json()["items"][0]  # continua nos abertos
    assert item["resolvido"] is False and item["status_plataforma"] == "encerrado"
    assert item["status_aba"] == "encerrado"
    assert item["status_aba_motivo"] == "plataforma encerrou sem decisão"
    # monitor repetindo `resolvido` (relê a caixa) não duplica o evento
    rec2 = await client.post(
        "/api/chamados/agent/recebida", headers=hdr,
        json={"chamado": "462456014", "resumo": "Reembolso aprovado R$ 12,00", "resolvido": True,
              "texto": "Analisamos e o reembolso de R$ 12,00 será creditado."},
    )
    assert rec2.json()["mensagem_id"] == rec.json()["mensagem_id"]
    hist2 = (await client.get(f"/api/chamados/{cid}/mensagens")).json()
    assert sum(1 for h in hist2 if "Monitor: plataforma encerrou" in h["texto"]) == 1
    # 6) só a pessoa fecha (com a nova situação do Bling — obrigatória, 19/09)
    aplicadas = _sem_bling_ao_resolver(monkeypatch)
    ok = await client.post(
        f"/api/chamados/{cid}/resolver",
        json={"resolvido": True, "valor_recuperado": 12, "situacao": "Resolvido"},
    )
    assert ok.status_code == 200 and ok.json()["status_aba"] == "concluido"
    assert aplicadas == ["Resolvido"] and ok.json()["status_bling"] == "Resolvido"
    assert (await client.get("/api/chamados")).json()["total"] == 0  # concluído saiu dos abertos


async def test_agent_recebida_por_protocolo_e_idempotente(
    client, make_user, auth_as, db, monkeypatch
):
    """Monitor do Tuta só conhece o PROTOCOLO (assunto "Serviço ao Cliente
    [Case: N]"): `recebida` acha o chamado só pelo `chamado`; reler a caixa
    não duplica a mesma resposta no histórico (e `resolvido` põe em Encerrado)."""
    from app.config import get_settings

    monkeypatch.setenv("NF_AGENT_TOKEN", _TOKEN)
    get_settings.cache_clear()
    hdr = {"X-Agent-Token": _TOKEN}
    user = await make_user(permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/chamados/agent/registrar",
        headers=hdr,
        json={
            "pedido_bling": "293413",
            "origem": "margem",
            "conta": "forpaper",
            "chamado": "479770243",
            "chamado_url": "https://www.mercadolivre.com.br/cases/detail/479770243",
            "mensagem": "Diferença de frete R$ 31,50.",
        },
    )
    assert r.status_code == 200, r.text
    cid = r.json()["chamado_id"]

    # só o protocolo, sem pedido_bling
    rec = await client.post(
        "/api/chamados/agent/recebida",
        headers=hdr,
        json={"chamado": "479770243", "texto": "Olá! Estamos analisando o seu caso."},
    )
    assert rec.status_code == 200, rec.text
    assert rec.json()["chamado_id"] == cid and rec.json()["resolvido"] is False
    mid = rec.json()["mensagem_id"]

    # mesma resposta de novo (monitor releu a caixa) → mesma mensagem, sem duplicar
    rec2 = await client.post(
        "/api/chamados/agent/recebida",
        headers=hdr,
        json={"chamado": "479770243", "texto": "Olá! Estamos analisando o seu caso."},
    )
    assert rec2.status_code == 200 and rec2.json()["mensagem_id"] == mid
    hist = (await client.get(f"/api/chamados/{cid}/mensagens")).json()
    assert sum(1 for h in hist if h["direcao"] == "recebida") == 1

    # resposta nova com `resolvido`: Encerrado (falta a pessoa fechar), não resolvido
    rec3 = await client.post(
        "/api/chamados/agent/recebida",
        headers=hdr,
        json={
            "chamado": "479770243",
            "texto": "Reembolso de R$ 31,50 creditado.",
            "resolvido": True,
        },
    )
    assert rec3.status_code == 200 and rec3.json()["resolvido"] is False
    assert rec3.json()["mensagem_id"] != mid
    ch = (await db.execute(select(Chamado).where(Chamado.id == UUID(cid)))).scalar_one()
    await db.refresh(ch)
    assert ch.resolvido is False and ch.status_plataforma == svc.STATUS_ENCERRADO

    # protocolo desconhecido → 404
    nf = await client.post(
        "/api/chamados/agent/recebida", headers=hdr, json={"chamado": "000", "texto": "x"}
    )
    assert nf.status_code == 404


async def test_agent_lease_abrir_e_falha(client, make_user, auth_as, db, monkeypatch):
    """Chamado criado na aba com canal robô e SEM protocolo → tarefa `abrir`;
    resultado ok com protocolo grava na linha. 19/09 (Vinicius: "envio falhou →
    fila do robô; se não conseguir, humano"): falha volta pra fila (o lease
    reentrega) até 3 tentativas; na 3ª fica `falhou`; erro que pede gente falha
    de primeira."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    hdr = {"X-Agent-Token": _TOKEN}
    user = await make_user(permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/chamados", json={"origem": "devolucao", "pedido_bling": "5", "canal": "robo"}
    )
    cid = r.json()["id"]
    m1 = (
        await client.post(f"/api/chamados/{cid}/mensagens", data={"texto": "Abrir chamado"})
    ).json()
    lease = (await client.post("/api/chamados/agent/lease", headers=hdr, json={})).json()["tarefas"]
    assert lease[0]["tipo"] == "abrir" and lease[0]["mensagem_id"] == m1["id"]
    ok = await client.post(
        "/api/chamados/agent/resultado",
        headers=hdr,
        json={
            "mensagem_id": m1["id"],
            "ok": True,
            "chamado": "999",
            "chamado_url": "https://x/cases/detail/999",
        },
    )
    assert ok.status_code == 200
    item = (await client.get("/api/chamados")).json()["items"][0]
    assert item["chamado"] == "999" and item["chamado_url"].endswith("/999")

    m2 = (await client.post(f"/api/chamados/{cid}/mensagens", data={"texto": "de novo"})).json()
    lease2 = (await client.post("/api/chamados/agent/lease", headers=hdr, json={})).json()[
        "tarefas"
    ]
    assert lease2[0]["tipo"] == "responder"
    async def falhar(mid: str, erro: str) -> dict:
        r = await client.post(
            "/api/chamados/agent/resultado", headers=hdr,
            json={"mensagem_id": mid, "ok": False, "erro": erro},
        )
        assert r.status_code == 200, r.text
        return r.json()

    async def linha() -> dict:
        items = (await client.get("/api/chamados")).json()["items"]
        return next(i for i in items if i["id"] == cid)

    # 1ª e 2ª falhas: volta pra `pendente` com o erro, o lease entrega de novo
    for n in (1, 2):
        falha = await falhar(m2["id"], "formulário mudou")
        assert falha["status"] == "pendente" and falha["erro"] == "formulário mudou", falha
        row = await linha()
        assert row["status_aba"] == "analise_robo"
        assert row["status_aba_motivo"] == (
            f"na fila do robô — tentativa {n} de 3 falhou: formulário mudou"
        )
        de_novo = (await client.post("/api/chamados/agent/lease", headers=hdr, json={})).json()
        de_novo = de_novo["tarefas"]
        assert [t["mensagem_id"] for t in de_novo] == [m2["id"]]
    hist = (await client.get(f"/api/chamados/{cid}/mensagens")).json()
    assert [h["texto"] for h in hist if h["texto"].startswith("Envio falhou")] == [
        "Envio falhou (tentativa 1 de 3): formulário mudou — volta pra fila do robô",
        "Envio falhou (tentativa 2 de 3): formulário mudou — volta pra fila do robô",
    ]
    # 3ª: esgotou → falhou, vira Análise Humano
    falha = await falhar(m2["id"], "formulário mudou")
    assert falha["status"] == "falhou" and falha["erro"] == "formulário mudou"
    row = await linha()
    assert row["status_aba"] == "analise_humano"
    assert row["status_aba_motivo"] == "envio falhou: formulário mudou"
    vazio = (await client.post("/api/chamados/agent/lease", headers=hdr, json={})).json()
    assert vazio["tarefas"] == []
    # erro que pede gente não volta pra fila: falha de primeira
    m3 = (await client.post(f"/api/chamados/{cid}/mensagens", data={"texto": "outra"})).json()
    await client.post("/api/chamados/agent/lease", headers=hdr, json={})
    falha = await falhar(m3["id"], "shopee_captcha_humano")
    assert falha["status"] == "falhou"
    assert (await linha())["status_aba_motivo"] == "formulário pronto — falta o quebra-cabeça"
    assert (
        await client.post(
            "/api/chamados/agent/resultado",
            headers=hdr,
            json={"mensagem_id": str(uuid4()), "ok": True},
        )
    ).status_code == 404


async def test_chamado_api_ml_fecha_sozinho_quando_ml_encerra(
    client, make_user, auth_as, db, monkeypatch
):
    """Eduardo 15/09: o robô acompanha TODOS os chamados — sem flag. Todo
    chamado aberto de canal API do ML com nº de claim é consultado; canal
    manual não é (não tem API), mesmo com nº. 19/09 (Vinicius): claim fechado
    põe em Encerrado — não resolve; a pessoa conclui."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeML(closed=True)

    async def _fake_client(session, conta):
        return fake

    monkeypatch.setattr(svc, "_ml_client_para", _fake_client)
    base = {"origem": "logistica", "plataforma": "ml", "conta": "aguiar"}
    r = await client.post(
        "/api/chamados", json={**base, "pedido_bling": "8", "canal": "api", "chamado": "777"}
    )
    cid = r.json()["id"]
    r2 = await client.post(
        "/api/chamados", json={**base, "pedido_bling": "9", "canal": "manual", "chamado": "778"}
    )
    cid_manual = r2.json()["id"]
    out = await svc.run_replica_automatica(db)
    assert out["encerrados"] == 1
    ch = (await db.execute(select(Chamado).where(Chamado.id == cid))).scalar_one()
    assert ch.resolvido is False and ch.resolvido_at is None
    # coluna Status (17/09): claim fechado sem `resolution` = encerrado sem vencedor
    assert ch.status_plataforma == svc.STATUS_ENCERRADO
    lst = (await client.get("/api/chamados", params={"mostrar": "abertos"})).json()["items"]
    assert next(i for i in lst if i["id"] == cid)["status_aba"] == "encerrado"
    manual = (await db.execute(select(Chamado).where(Chamado.id == cid_manual))).scalar_one()
    assert manual.resolvido is False and manual.status_plataforma is None
    hist = await client.get(f"/api/chamados/{cid}/mensagens")
    assert any(
        "encerrada no Mercado Livre — aguardando fechamento" in h["texto"] for h in hist.json()
    )
    # já Encerrado: o cron não consulta de novo (nem repete o evento)
    fake.calls = 0
    out = await svc.run_replica_automatica(db)
    assert out["encerrados"] == 0 and fake.calls == 0


async def test_agent_analisar_e_analise_do_cerebro(client, make_user, auth_as, db, monkeypatch):
    """Cérebro dos chamados (08/09): lista chamados robô com resposta do ML
    ainda não analisada, registra a análise, enfileira a réplica com os prints
    da abertura. 19/09 (Vinicius): `resolver` NÃO fecha — põe em Encerrado com
    o valor como SUGESTÃO; a pessoa conclui. Encerrado só volta pro cérebro com
    instrução, e a instrução tira o chamado do Encerrado quando ele responde."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    hdr = {"X-Agent-Token": _TOKEN}
    user = await make_user(permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/chamados/agent/registrar",
        headers=hdr,
        json={
            "pedido_bling": "293413",
            "origem": "margem",
            "conta": "forpaper",
            "pedido_marketplace": "2000013416886045",
            "chamado": "479765445",
            "chamado_url": "https://www.mercadolivre.com.br/cases/detail/479765445",
            "mensagem": "Pedido 2000013416886045: frete anúncio R$ 32,48; frete cobrado R$ 54,08",
            "status_envio": "enviada",
        },
    )
    assert r.status_code == 200, r.text
    cid = r.json()["chamado_id"]
    up = await client.post(
        "/api/chamados/agent/anexo",
        headers=hdr,
        data={"chamado_id": cid, "mensagem_id": r.json()["mensagem_id"]},
        files={"file": ("venda_frete_cobrado.png", PNG, "image/png")},
    )
    assert up.status_code == 201, up.text

    async def analisar(**kw) -> list[dict]:
        r = await client.post("/api/chamados/agent/analisar", headers=hdr, json=kw)
        assert r.status_code == 200, r.text
        return r.json()["chamados"]

    async def linha() -> dict:
        body = (await client.get("/api/chamados", params={"mostrar": "todos"})).json()
        return next(i for i in body["items"] if i["id"] == cid)

    # sem resposta do ML → nada a analisar
    assert await analisar() == []

    # monitor grava a resposta
    rec = await client.post(
        "/api/chamados/agent/recebida",
        headers=hdr,
        json={
            "chamado": "479765445",
            "texto": "Preciso que informe as medidas da embalagem final usada no envio.",
        },
    )
    assert rec.status_code == 200, rec.text
    lst = await analisar()
    assert len(lst) == 1
    c = lst[0]
    assert c["chamado"] == "479765445" and c["resolvido"] is False and c["analises"] == 0
    assert c["instrucao"] is None and c["bloqueio"] is None and c["status_plataforma"] is None
    assert {m["tipo"] for m in c["mensagens"]} >= {"abertura", "resposta", "sistema"}
    assert len(c["anexos_abertura"]) == 1
    assert (await linha())["status_aba"] == "analise_robo"
    # outra plataforma não entra
    assert await analisar(plataforma="shopee") == []

    # cérebro responde com as medidas, reanexando o print → enfileira pro Tuta
    an = await client.post(
        "/api/chamados/agent/analise",
        headers=hdr,
        json={
            "chamado_id": cid,
            "classe": "pede_medidas",
            "resumo": "ML pediu as medidas da embalagem",
            "acao": "responder",
            "texto_replica": "Medidas da embalagem final: 52×35×23 cm, 7 kg.",
            "reanexar_abertura": True,
        },
    )
    assert an.status_code == 200, an.text
    assert an.json()["replica_id"] and an.json()["resolvido"] is False
    lease = await client.post(
        "/api/chamados/agent/lease", headers=hdr, json={"tipo": "responder"}
    )
    tarefas = lease.json()["tarefas"]
    assert len(tarefas) == 1
    assert tarefas[0]["texto"].startswith("Medidas") and len(tarefas[0]["anexos"]) == 1
    # já analisada: some da lista até chegar resposta nova
    assert await analisar() == []
    # `responder` sem texto é recusado
    ruim = await client.post(
        "/api/chamados/agent/analise",
        headers=hdr,
        json={"chamado_id": cid, "classe": "x", "resumo": "y", "acao": "responder"},
    )
    assert ruim.status_code == 422

    # ML devolve o dinheiro → cérebro SUGERE fechar com o valor: Encerrado, não resolvido
    await client.post(
        "/api/chamados/agent/recebida",
        headers=hdr,
        json={"chamado": "479765445", "texto": "Te devolvemos R$21,60 pela diferença."},
    )
    lst2 = await analisar()
    assert len(lst2) == 1
    assert lst2[0]["replicas_robo"] == 1 and lst2[0]["analises"] == 1
    fim = await client.post(
        "/api/chamados/agent/analise",
        headers=hdr,
        json={
            "chamado_id": cid,
            "classe": "credito",
            "resumo": "ML devolveu R$ 21,60",
            "acao": "resolver",
            "valor_recuperado": "21.60",
            "observacao": "crédito confirmado pelo ML",
        },
    )
    assert fim.status_code == 200, fim.text
    assert fim.json()["resolvido"] is False
    ch = (await db.execute(select(Chamado).where(Chamado.id == cid))).scalar_one()
    await db.refresh(ch)
    assert ch.resolvido is False and ch.valor_recuperado is None
    assert float(ch.valor_sugerido) == 21.6
    assert ch.status_plataforma == svc.STATUS_ENCERRADO
    assert "crédito confirmado" in (ch.observacao or "")
    hist = (await client.get(f"/api/chamados/{cid}/mensagens")).json()
    assert sum(1 for h in hist if h["tipo"] == "analise") == 2
    assert any(h["texto"].endswith("→ robô sugere fechar") for h in hist)
    assert any(
        "Robô sugere fechar" in h["texto"] and "aguardando fechamento" in h["texto"] for h in hist
    )
    row = await linha()
    assert row["status_aba"] == "encerrado"
    assert row["status_aba_motivo"] == (
        "plataforma encerrou sem decisão · robô sugere lucro de R$ 21,60"
    )
    assert float(row["valor_sugerido"]) == 21.6
    # Encerrado não volta pro cérebro (mesmo com resposta nova)…
    assert await analisar() == []
    await client.post(
        "/api/chamados/agent/recebida",
        headers=hdr,
        json={"chamado": "479765445", "texto": "Algo mais?"},
    )
    assert await analisar() == []
    assert (await linha())["status_aba"] == "encerrado"

    # …só com instrução de uma pessoa (19/09)
    ins = await client.post(
        f"/api/chamados/{cid}/instrucao",
        json={"texto": "  Responde que o crédito não caiu na conta ainda.  "},
    )
    assert ins.status_code == 200, ins.text
    # 21/09 (Vinicius): a instrução tira do Encerrado até o cérebro consumir
    assert ins.json()["status_aba"] == "analise_robo"
    assert ins.json()["instrucao_pendente"] == "Responde que o crédito não caiu na conta ainda."
    vazia = await client.post(f"/api/chamados/{cid}/instrucao", json={"texto": "  "})
    assert vazia.status_code == 422
    lst3 = await analisar()
    assert len(lst3) == 1 and lst3[0]["status_plataforma"] == "encerrado"
    assert lst3[0]["instrucao"]["texto"] == "Responde que o crédito não caiu na conta ainda."
    assert lst3[0]["instrucao"]["autor"] == (user.name or user.email)
    assert float(lst3[0]["valor_sugerido"]) == 21.6
    assert any(m["tipo"] == "instrucao" for m in lst3[0]["mensagens"])
    # a análise consome a instrução; `responder` por instrução tira do Encerrado
    re_ = await client.post(
        "/api/chamados/agent/analise",
        headers=hdr,
        json={"chamado_id": cid, "classe": "instrucao", "resumo": "pessoa mandou cobrar",
              "acao": "responder", "texto_replica": "O crédito ainda não caiu."},
    )
    assert re_.status_code == 200 and re_.json()["resolvido"] is False
    assert await analisar() == []
    row = await linha()
    assert row["instrucao_pendente"] is None and row["status_plataforma"] is None
    assert row["status_aba"] == "analise_robo" and row["status_aba_motivo"] == "na fila do robô"
    hist = (await client.get(f"/api/chamados/{cid}/mensagens")).json()
    assert any("saiu de Encerrado" in h["texto"] for h in hist)

    # `esperar` + `reabrir`: chamado que o monitor antigo fechou cedo volta a
    # ficar aberto (o caso segue vivo no ML)
    await db.refresh(ch)
    db.add(svc.marcar_resolvido(ch, True, autor_nome="monitor"))
    await db.commit()
    await client.post(
        "/api/chamados/agent/recebida",
        headers=hdr,
        json={"chamado": "479765445", "texto": "Preciso das medidas."},
    )
    assert len(await analisar()) == 1
    re2 = await client.post(
        "/api/chamados/agent/analise",
        headers=hdr,
        json={
            "chamado_id": cid,
            "classe": "ja_respondido",
            "resumo": "monitor fechou cedo",
            "acao": "esperar",
            "reabrir": True,
        },
    )
    assert re2.status_code == 200 and re2.json()["resolvido"] is False


async def test_cerebro_nao_reabre_chamado_fechado_pela_plataforma_ou_pessoa(
    client, make_user, auth_as, db, monkeypatch
):
    """17/09: o acompanhamento fechou 290985/289899 (compensação paga = ganhamos) às
    08:25 e o cérebro, às 09:00, leu "Shopee PAGOU a compensação" como resposta nova,
    mandou pra humano e REABRIU. Fechado pela plataforma ou por pessoa não volta pro
    cérebro nem é reaberto; fechado pelo monitor antigo/cérebro continua reabrível."""
    from decimal import Decimal

    from app.config import get_settings
    from app.services import chamados as svc

    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    hdr = {"X-Agent-Token": _TOKEN}
    user = await make_user(permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/chamados/agent/registrar",
        headers=hdr,
        json={"pedido_bling": "290985", "origem": "margem", "conta": "kfa",
              "pedido_marketplace": "2000013416880001", "chamado": "479700001",
              "mensagem": "abertura", "status_envio": "enviada"},
    )
    cid = r.json()["chamado_id"]
    ch = (await db.execute(select(Chamado).where(Chamado.id == cid))).scalar_one()
    for quem in ("acompanhamento", "cairo sa"):
        db.add(svc.marcar_resolvido(ch, True, autor_nome=quem, valor=Decimal("865.82")))
        await db.commit()
        rec = await client.post(
            "/api/chamados/agent/recebida", headers=hdr,
            json={"chamado": "479700001", "texto": f"Shopee PAGOU a compensação ({quem})"},
        )
        assert rec.status_code == 200, rec.text
        lst = (await client.post("/api/chamados/agent/analisar", headers=hdr, json={})).json()
        assert lst["chamados"] == [], (quem, lst)
        an = await client.post(
            "/api/chamados/agent/analise", headers=hdr,
            json={"chamado_id": cid, "classe": "dev_indefinido", "resumo": "?",
                  "acao": "humano", "reabrir": True},
        )
        assert an.status_code == 200 and an.json()["resolvido"] is True, (quem, an.json())
    # fechado pelo monitor antigo: continua voltando e reabrindo
    db.add(svc.marcar_resolvido(ch, True, autor_nome="monitor"))
    await db.commit()
    await client.post("/api/chamados/agent/recebida", headers=hdr,
                      json={"chamado": "479700001", "texto": "Preciso das medidas da embalagem."})
    lst = (await client.post("/api/chamados/agent/analisar", headers=hdr, json={})).json()
    assert len(lst["chamados"]) == 1
    an = await client.post(
        "/api/chamados/agent/analise", headers=hdr,
        json={"chamado_id": cid, "classe": "pede_medidas", "resumo": "medidas",
              "acao": "esperar", "reabrir": True},
    )
    assert an.json()["resolvido"] is False


async def test_agent_analisar_canais_manual_e_api_sem_replica(
    client, make_user, auth_as, db, monkeypatch
):
    """09/09: o cérebro olha também os chamados `manual` (aberto por pessoa) e
    `api` (devolução) — só pra decidir/avisar. `responder` (réplica do robô do
    Tuta) continua exclusivo do canal robô."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    hdr = {"X-Agent-Token": _TOKEN}
    user = await make_user(permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/chamados",
        json={
            "origem": "logistica",
            "pedido_bling": "293000",
            "plataforma": "ml",
            "conta": "kfa2",
            "chamado": "478272401",
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["canal"] == "manual"
    rec = await client.post(
        "/api/chamados/agent/recebida",
        headers=hdr,
        json={"chamado": "478272401", "texto": "Já processamos a devolução do valor."},
    )
    assert rec.status_code == 200, rec.text
    # padrão (canal robô) não lista o manual
    assert (await client.post("/api/chamados/agent/analisar", headers=hdr, json={})).json()[
        "chamados"
    ] == []
    lst = (
        await client.post(
            "/api/chamados/agent/analisar", headers=hdr, json={"canais": ["manual", "api"]}
        )
    ).json()["chamados"]
    assert len(lst) == 1
    assert lst[0]["canal"] == "manual" and lst[0]["plataforma"] == "ml"
    assert lst[0]["chamado"] == "478272401"
    # manual do ML: o robô ASSUME o chamado (canal vira robô) e a réplica vai
    # pra fila do Tuta ("para os manuais nós vamos tomar conta", 09/09)
    ok = await client.post(
        "/api/chamados/agent/analise",
        headers=hdr,
        json={
            "chamado_id": cid,
            "classe": "pede_fotos",
            "resumo": "ML pediu fotos",
            "acao": "responder",
            "texto_replica": "Segue em anexo.",
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["replica_id"]
    ch = (await db.execute(select(Chamado).where(Chamado.id == cid))).scalar_one()
    await db.refresh(ch)
    assert ch.canal == "robo"
    hist = (await client.get(f"/api/chamados/{cid}/mensagens")).json()
    assert any("assumido pelo robô" in h["texto"] for h in hist)
    lease = await client.post("/api/chamados/agent/lease", headers=hdr, json={"tipo": "responder"})
    assert [t["texto"] for t in lease.json()["tarefas"]] == ["Segue em anexo."]
    assert (
        await client.post(
            "/api/chamados/agent/analisar", headers=hdr, json={"canais": ["manual", "api"]}
        )
    ).json()["chamados"] == []
    # manual de OUTRA plataforma (Seller Center, sem robô) e canal api: recusados
    r2 = await client.post(
        "/api/chamados",
        json={"origem": "logistica", "pedido_bling": "293001", "plataforma": "shopee",
              "conta": "atv", "chamado": "2085398968062533689"},
    )
    assert r2.status_code == 201, r2.text
    for cid2, canal in ((r2.json()["id"], "manual"),):
        ruim = await client.post(
            "/api/chamados/agent/analise",
            headers=hdr,
            json={"chamado_id": cid2, "classe": "x", "resumo": "y", "acao": "responder",
                  "texto_replica": "z"},
        )
        assert ruim.status_code == 422
        assert ruim.json()["detail"]["code"] == "canal_sem_robo"
        assert ruim.json()["detail"]["canal"] == canal
    r3 = await client.post(
        "/api/chamados",
        json={"origem": "devolucao", "pedido_bling": "293002", "plataforma": "ml",
              "conta": "kfa", "canal": "api", "chamado": "5560249689"},
    )
    assert r3.status_code == 201, r3.text
    ruim = await client.post(
        "/api/chamados/agent/analise",
        headers=hdr,
        json={"chamado_id": r3.json()["id"], "classe": "x", "resumo": "y", "acao": "responder",
              "texto_replica": "z"},
    )
    assert ruim.status_code == 422 and ruim.json()["detail"]["canal"] == "api"
    # humano/esperar continuam livres em qualquer canal
    assert (
        await client.post(
            "/api/chamados/agent/analise",
            headers=hdr,
            json={"chamado_id": r3.json()["id"], "classe": "dev_esperar", "resumo": "em análise",
                  "acao": "esperar"},
        )
    ).status_code == 200
    # valor inválido de canal é recusado pelo schema
    assert (
        await client.post("/api/chamados/agent/analisar", headers=hdr, json={"canais": ["x"]})
    ).status_code == 422


async def test_lista_status_da_aba_e_ultima_resposta(
    client, make_user, auth_as, db, monkeypatch
):
    """17/09 (Vinicius): a coluna Status diz o que a plataforma diz do chamado —
    oficial da API quando há, senão derivado de quem falou por último e do que o
    cérebro pediu — e a Últ. resposta mostra a última FALA (análise e evento do
    sistema não contam)."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    _sem_bling_ao_resolver(monkeypatch)
    r = await client.post(
        "/api/chamados",
        json={"origem": "margem", "pedido_bling": "1", "canal": "manual", "plataforma": "shopee"},
    )
    cid = r.json()["id"]

    async def linha() -> dict:
        body = (await client.get("/api/chamados", params={"mostrar": "todos"})).json()
        return next(i for i in body["items"] if i["id"] == cid)

    def fala(ch, **kw):
        # created_at é `now()` do Postgres = início da transação da fixture `db`,
        # anterior às réplicas feitas pela API — carimba a hora real.
        m = svc.nova_mensagem(ch, status="registrada", **kw)
        m.created_at = datetime.now(UTC)
        return m

    # só o evento "Chamado registrado": nada consulta essa plataforma → é de gente
    row = await linha()
    assert row["status_aba"] == "analise_humano"
    assert row["status_aba_motivo"] == "registrado à mão — acompanhar no site"
    assert row["ultima_resposta_at"] is None

    # nós falamos (réplica manual registrada) → aguardando plataforma
    rep = await client.post(f"/api/chamados/{cid}/mensagens", data={"texto": "Segue a réplica"})
    assert rep.status_code == 201, rep.text
    row = await linha()
    assert row["status_aba"] == "aguard_plataforma"
    assert row["ultima_resposta_direcao"] == "enviada"
    assert row["ultima_resposta_autor"] == rep.json()["autor_nome"]
    assert row["status_aba_at"] == row["ultima_resposta_at"]

    # a plataforma respondeu (monitor) → respondeu; última fala é dela
    ch = (await db.execute(select(Chamado).where(Chamado.id == UUID(cid)))).scalar_one()
    db.add(
        fala(
            ch, texto="Precisamos de fotos", tipo="resposta", direcao="recebida",
            autor_nome="monitor"
        )
    )
    await db.commit()
    row = await linha()
    # manual da Shopee: não há robô que responda → gente (Seller Center)
    assert row["status_aba"] == "analise_humano"
    assert row["status_aba_motivo"] == "plataforma respondeu — responder no Seller Center"
    assert row["ultima_resposta_direcao"] == "recebida"
    assert row["ultima_resposta_autor"] == "monitor"

    # cérebro pediu gente → precisa de humano (a análise não vira "última resposta")
    db.add(
        fala(
            ch, texto="Análise do robô [duvida]: não sei o que responder → precisa de humano",
            tipo="analise", direcao="sistema", autor_nome="cérebro"
        )
    )
    await db.commit()
    row = await linha()
    assert row["status_aba"] == "analise_humano"
    assert row["status_aba_motivo"] == "o robô pediu revisão humana"
    assert row["ultima_resposta_direcao"] == "recebida"

    # status oficial da API: vale, até a plataforma falar de novo depois dele
    ch.status_plataforma = svc.STATUS_EM_ANALISE
    ch.status_plataforma_at = datetime.now(UTC)
    await db.commit()
    # cérebro pediu gente e ninguém falou depois
    assert (await linha())["status_aba"] == "analise_humano"
    db.add(
        fala(
            ch, texto="Decisão em até 3 dias", tipo="resposta", direcao="recebida",
            autor_nome="Shopee"
        )
    )
    await db.commit()
    assert (await linha())["status_aba"] == "analise_humano"
    # nós replicamos de novo → volta pro oficial (em análise = bola com a plataforma)
    assert (
        await client.post(f"/api/chamados/{cid}/mensagens", data={"texto": "Seguem as fotos"})
    ).status_code == 201
    row = await linha()
    assert row["status_aba"] == "aguard_plataforma"
    assert row["status_aba_motivo"] == "em análise na plataforma"
    assert row["ultima_resposta_direcao"] == "enviada"

    # réplica ainda na fila do robô conta como status, mas não como resposta dada
    assert (await client.patch(f"/api/chamados/{cid}", json={"canal": "robo"})).status_code == 200
    ch.status_plataforma = None
    await db.commit()
    fila = await client.post(f"/api/chamados/{cid}/mensagens", data={"texto": "na fila"})
    assert fila.json()["status"] == "pendente"
    row = await linha()
    assert row["status_aba"] == "analise_robo"
    assert row["status_aba_motivo"] == "na fila do robô"
    assert row["ultima_resposta_autor"] == rep.json()["autor_nome"]  # a última ENTREGUE

    # instrução nossa pro robô → Análise Robô com o texto (mesmo com o cérebro tendo pedido gente)
    ins = await client.post(
        f"/api/chamados/{cid}/instrucao", json={"texto": "Cobra a Shopee de novo"}
    )
    assert ins.status_code == 200, ins.text
    assert ins.json()["status_aba"] == "analise_robo"
    assert ins.json()["status_aba_motivo"] == "instrução pendente pro robô: Cobra a Shopee de novo"
    assert ins.json()["instrucao_pendente"] == "Cobra a Shopee de novo"
    hist = (await client.get(f"/api/chamados/{cid}/mensagens")).json()
    i = next(h for h in hist if h["tipo"] == "instrucao")
    assert i["direcao"] == "sistema" and i["status"] == "registrada"
    assert i["autor_nome"] == rep.json()["autor_nome"]

    # a plataforma decidiu (status final) e ninguém fechou → Encerrado. 21/09
    # (Vinicius): enquanto a instrução estiver pendente, a linha fica em Análise
    # Robô mesmo assim — só depois que o cérebro consome (análise) é Encerrado.
    ch.status_plataforma = svc.STATUS_GANHAMOS
    ch.status_plataforma_at = datetime.now(UTC)
    await db.commit()
    row = await linha()
    assert row["status_aba"] == "analise_robo"
    assert row["status_aba_motivo"] == "instrução pendente pro robô: Cobra a Shopee de novo"
    db.add(fala(ch, texto="lido", tipo="analise", direcao="sistema"))
    await db.commit()
    row = await linha()
    assert row["status_aba"] == "encerrado" and row["status_aba_motivo"] == "ganhamos"
    assert row["status_aba_at"] == row["status_plataforma_at"]

    # só a pessoa conclui: Concluído, com o resultado no motivo
    ok = await client.post(
        f"/api/chamados/{cid}/resolver",
        json={"resolvido": True, "valor_recuperado": 10, "situacao": "Resolvido"},
    )
    assert ok.status_code == 200, ok.text
    row = await linha()
    assert row["status_aba"] == "concluido"
    assert row["status_aba_motivo"] == "ganhamos — lucro de R$ 10,00"
    assert row["status_aba_at"] == row["resolvido_at"]
    # 19/09 (ajuste A4): instrução NÃO entra em Concluído — a pessoa reabre pela aba antes
    neg = await client.post(f"/api/chamados/{cid}/instrucao", json={"texto": "Cobra de novo"})
    assert neg.status_code == 422 and neg.json()["detail"]["code"] == "chamado_concluido"
    assert (await linha())["status_aba"] == "concluido"

    # filtro/ordem dos códigos: um status final nunca vira intermediário
    assert svc.set_status_plataforma(ch, svc.STATUS_EM_ANALISE) is False
    assert ch.status_plataforma == svc.STATUS_GANHAMOS


async def test_lista_status_do_caso_junta_linhas_irmas(client, make_user, auth_as, db):
    """18/09 (Vinicius, consulta 478538390 × 3 pedidos): a mesma consulta do ML
    vale pra vários pedidos (uma linha por pedido) e a conversa fica espalhada —
    o leitor grava a resposta do ML numa linha, o cérebro replica por outra. O
    Status e a Últ. resposta são do CASO (mesmo protocolo + conta), como o
    histórico já era; olhando só a própria linha, o 290397 ficava "Plataforma
    respondeu" depois de o robô já ter replicado."""
    user = await make_user(permissions=_perms())
    auth_as(user)

    async def cria(pedido: str, *, conta: str = "kfa", chamado: str = "478538390") -> str:
        r = await client.post(
            "/api/chamados",
            json={
                "origem": "logistica", "pedido_bling": pedido, "canal": "manual",
                "plataforma": "ml", "conta": conta, "chamado": chamado,
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    a = await cria("292529")
    b = await cria("290490")
    c = await cria("290397")
    outra_conta = await cria("300001", conta="aguiar")  # mesmo nº, outra conta: caso diferente
    texto_livre = await cria("288184", chamado="Disputa na venda")  # texto livre não agrupa

    async def linhas() -> dict[str, dict]:
        body = (await client.get("/api/chamados", params={"mostrar": "todos"})).json()
        return {i["id"]: i for i in body["items"]}

    t0 = datetime.now(UTC)

    def fala(ch, minutos: int, **kw):
        m = svc.nova_mensagem(ch, status="registrada", **kw)
        m.created_at = t0 + timedelta(minutes=minutos)
        return m

    chs = {
        cid: (await db.execute(select(Chamado).where(Chamado.id == UUID(cid)))).scalar_one()
        for cid in (a, b, c, outra_conta, texto_livre)
    }
    # ML respondeu (monitor) — gravado só na linha C
    db.add(fala(chs[c], 1, texto="Recebi sua solicitação", tipo="resposta", direcao="recebida", autor_nome="monitor"))
    # réplica manual gravada em TODAS as linhas no mesmo instante (conta uma vez)
    for cid in (a, b, c):
        db.add(fala(chs[cid], 2, texto="Retomo o caso", tipo="replica", direcao="enviada", autor_nome="robô (página do caso)"))
    # ML respondeu de novo (linha C) e o cérebro replicou depois — pela linha B
    db.add(fala(chs[c], 3, texto="Estamos analisando", tipo="resposta", direcao="recebida", autor_nome="monitor"))
    db.add(fala(chs[b], 4, texto="Análise do robô [cutucao]: cobrança → réplica enfileirada", tipo="analise", direcao="sistema", autor_nome="cérebro"))
    db.add(fala(chs[b], 4, texto="Olá, tudo bem? Retomo o caso", tipo="replica", direcao="enviada", autor_nome="cérebro"))
    # a outra conta só recebeu resposta da plataforma
    db.add(fala(chs[outra_conta], 5, texto="Oi", tipo="resposta", direcao="recebida", autor_nome="monitor"))
    await db.commit()

    rows = await linhas()
    # as três linhas do caso mostram a mesma coisa: nós (robô) falamos por último
    for cid in (a, b, c):
        row = rows[cid]
        assert row["status_aba"] == "aguard_plataforma", (cid, row["status_aba"])
        assert row["ultima_resposta_direcao"] == "enviada"
        assert row["ultima_resposta_autor"] == "cérebro"
        # "registrado" ×3 e a réplica manual ×3 nasceram no mesmo minuto com o mesmo
        # texto → contam uma vez (como no histórico): 1 + 2 respostas + 1 + análise + réplica do robô
        assert row["mensagens_total"] == 6
    # mesmo nº em outra conta e texto livre: cada um só com a própria conversa
    # (manual do ML: o robô assume → a resposta é do robô)
    assert rows[outra_conta]["status_aba"] == "analise_robo"
    assert rows[outra_conta]["mensagens_total"] == 2
    assert rows[texto_livre]["status_aba"] == "analise_humano"
    assert rows[texto_livre]["mensagens_total"] == 1

    # o histórico da linha C (o modal) conta a mesma conversa que a lista
    hist = (await client.get(f"/api/chamados/{c}/mensagens")).json()
    assert len(hist) == 6


async def test_agent_bloqueio_robo_assume_canal_api_por_outro_caminho(
    client, make_user, auth_as, db, monkeypatch
):
    """19/09 (Vinicius): a plataforma não libera pela API (abertura presa com
    `shopee_motivo_indisponivel`) → não é "esperar": o robô procura outro caminho.
    `/agent/analisar` traz o chamado com `bloqueio` (sem resposta nova) e
    `responder` em canal api COM bloqueio é aceito: canal vira robô, a fala presa
    sai da fila (`substituida_pelo_robo`) e a réplica do robô vai pro lease da
    Shopee. `esperar` num bloqueado deixa a linha em Aguard. Plataforma."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    hdr = {"X-Agent-Token": _TOKEN}
    user = await make_user(permissions=_perms())
    auth_as(user)

    async def cria(pedido: str, sn: str) -> tuple[str, Chamado]:
        r = await client.post(
            "/api/chamados",
            json={"origem": "devolucao", "pedido_bling": pedido, "plataforma": "shopee",
                  "conta": "minas", "canal": "api", "chamado": sn},
        )
        assert r.status_code == 201, r.text
        ch = (
            await db.execute(select(Chamado).where(Chamado.id == UUID(r.json()["id"])))
        ).scalar_one()
        m = svc.nova_mensagem(
            ch, texto="Contestação da devolução", tipo="abertura", status="pendente"
        )
        m.canal = "api"
        m.erro = "shopee_motivo_indisponivel"
        m.created_at = datetime.now(UTC)
        db.add(m)
        await db.commit()
        return r.json()["id"], ch

    cid, ch = await cria("292270", "2608258J2C6V3C")
    cid2, _ch2 = await cria("293406", "2608258J2C6V3D")

    async def analisar(**kw) -> list[dict]:
        r = await client.post(
            "/api/chamados/agent/analisar", headers=hdr,
            json={"plataforma": "shopee", "canais": ["robo", "api", "manual"], **kw},
        )
        assert r.status_code == 200, r.text
        return r.json()["chamados"]

    async def linha(i: str) -> dict:
        body = (await client.get("/api/chamados", params={"mostrar": "todos"})).json()
        return next(x for x in body["items"] if x["id"] == i)

    row = await linha(cid)
    assert row["status_aba"] == "analise_robo"
    assert row["status_aba_motivo"] == (
        "plataforma não libera: Shopee ainda não libera o motivo da contestação — "
        "robô procura outro caminho"
    )
    lst = await analisar()
    assert {c["chamado_id"] for c in lst} == {cid, cid2}
    c = next(x for x in lst if x["chamado_id"] == cid)
    assert c["bloqueio"]["erro"] == "shopee_motivo_indisponivel"
    assert c["bloqueio"]["motivo"] == "Shopee ainda não libera o motivo da contestação"
    assert c["bloqueio"]["desde"] and c["instrucao"] is None
    # 19/09 (ajuste A5): bloqueio sai pra quem chamar — os filtros `canais` e
    # `plataforma` valem só pra "resposta nova"; a chamada padrão (canal robô, sem
    # plataforma) e uma de outra plataforma também trazem os dois
    padrao = (await client.post("/api/chamados/agent/analisar", headers=hdr, json={})).json()
    assert {x["chamado_id"] for x in padrao["chamados"]} == {cid, cid2}
    assert {x["chamado_id"] for x in await analisar(canais=["robo"])} == {cid, cid2}
    assert {x["chamado_id"] for x in await analisar(plataforma="ml")} == {cid, cid2}

    # `esperar` no bloqueado: gravado, sai da lista e a linha fica Aguard. Plataforma
    esp = await client.post(
        "/api/chamados/agent/analise", headers=hdr,
        json={"chamado_id": cid2, "classe": "aguardar_pacote", "resumo": "sem caminho ainda",
              "acao": "esperar"},
    )
    assert esp.status_code == 200, esp.text
    assert [x["chamado_id"] for x in await analisar()] == [cid]
    row2 = await linha(cid2)
    assert row2["status_aba"] == "aguard_plataforma"
    assert row2["status_aba_motivo"] == (
        "Shopee ainda não libera o motivo da contestação — o robô decidiu aguardar"
    )

    # `responder` no bloqueado: o robô assume por outro caminho
    ok = await client.post(
        "/api/chamados/agent/analise", headers=hdr,
        json={"chamado_id": cid, "classe": "outro_caminho", "resumo": "abrir no Seller Center",
              "acao": "responder", "texto_replica": "Solicitamos a compensação pelo extravio."},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["replica_id"]
    await db.refresh(ch)
    assert ch.canal == "robo"
    msgs = (await db.execute(
        select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id)
        .order_by(ChamadoMensagem.created_at, ChamadoMensagem.id)
    )).scalars().all()
    presa = next(m for m in msgs if m.erro == "substituida_pelo_robo")
    assert presa.status == "falhou" and presa.tipo == "abertura"
    replica = next(m for m in msgs if m.id == UUID(ok.json()["replica_id"]))
    assert replica.canal == "robo" and replica.status == "pendente" and replica.tipo == "replica"
    assert any("assumido pelo robô" in m.texto and "Seller Center" in m.texto for m in msgs)
    assert await analisar() == []
    row = await linha(cid)
    assert row["canal"] == "robo" and row["status_aba"] == "analise_robo"
    assert row["status_aba_motivo"] == "na fila do robô"
    lease = await client.post(
        "/api/chamados/agent/lease", headers=hdr, json={"plataforma": "shopee"}
    )
    tarefas = lease.json()["tarefas"]
    assert [t["mensagem_id"] for t in tarefas] == [ok.json()["replica_id"]]
    assert tarefas[0]["tipo"] == "responder" and tarefas[0]["chamado"] == "2608258J2C6V3C"
    # a fala substituída não é "envio falhou" pra ninguém
    await client.post(
        "/api/chamados/agent/resultado", headers=hdr,
        json={"mensagem_id": ok.json()["replica_id"], "ok": True},
    )
    assert (await linha(cid))["status_aba"] == "aguard_plataforma"

    # canal api SEM bloqueio continua recusado
    r3 = await client.post(
        "/api/chamados",
        json={"origem": "devolucao", "pedido_bling": "293002", "plataforma": "shopee",
              "conta": "minas", "canal": "api", "chamado": "2608258J2C6V3E"},
    )
    ruim = await client.post(
        "/api/chamados/agent/analise", headers=hdr,
        json={"chamado_id": r3.json()["id"], "classe": "x", "resumo": "y", "acao": "responder",
              "texto_replica": "z"},
    )
    assert ruim.status_code == 422 and ruim.json()["detail"]["code"] == "canal_sem_robo"


async def test_ganhamos_lido_da_api_nao_vira_encerrado_sem_decisao(
    client, make_user, auth_as, db, monkeypatch
):
    """19/09 (ajuste A1): o sync leu "ganhamos" da API; depois o monitor do Tuta
    diz `resolvido=true` e o cérebro manda `resolver`. Nenhum dos dois pode
    rebaixar pra "encerrado sem decisão" (`set_status_plataforma` aceita trocar um
    final por outro) nem repetir o evento — o `resolver` só grava a sugestão."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    hdr = {"X-Agent-Token": _TOKEN}
    user = await make_user(permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/chamados/agent/registrar",
        headers=hdr,
        json={"pedido_bling": "295001", "origem": "margem", "conta": "kfa",
              "pedido_marketplace": "2000013416880010", "chamado": "479700010",
              "mensagem": "abertura", "status_envio": "enviada"},
    )
    cid = r.json()["chamado_id"]
    ch = (await db.execute(select(Chamado).where(Chamado.id == cid))).scalar_one()
    decidido_em = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
    assert svc.set_status_plataforma(ch, svc.STATUS_GANHAMOS, decidido_em) is True
    ch.auto_ligada = True
    ch.auto_dias = 3
    ch.auto_mensagem = "Aguardo."
    await db.commit()

    async def linha() -> dict:
        body = (await client.get("/api/chamados", params={"mostrar": "todos"})).json()
        return next(i for i in body["items"] if i["id"] == cid)

    async def eventos_encerrou() -> int:
        hist = (await client.get(f"/api/chamados/{cid}/mensagens")).json()
        # só eventos de sistema (a própria análise termina em "robô sugere fechar")
        return sum(
            1 for h in hist
            if h["tipo"] == "sistema" and "aguardando fechamento" in h["texto"]
        )

    # monitor: "plataforma encerrou" (duas vezes — relê a caixa)
    for _ in range(2):
        rec = await client.post(
            "/api/chamados/agent/recebida", headers=hdr,
            json={"chamado": "479700010", "texto": "Caso encerrado a favor do vendedor.",
                  "resolvido": True},
        )
        assert rec.status_code == 200 and rec.json()["resolvido"] is False
    await db.refresh(ch)
    assert ch.status_plataforma == svc.STATUS_GANHAMOS
    assert ch.status_plataforma_at == decidido_em
    assert ch.resolvido is False
    assert await eventos_encerrou() == 0
    row = await linha()
    assert row["status_aba"] == "encerrado" and row["status_aba_motivo"] == "ganhamos"

    # cérebro: `resolver` com valor → só a sugestão; status e data intactos
    an = await client.post(
        "/api/chamados/agent/analise", headers=hdr,
        json={"chamado_id": cid, "classe": "ganhamos", "resumo": "ML deu ganho de causa",
              "acao": "resolver", "valor_recuperado": "45.90"},
    )
    assert an.status_code == 200 and an.json()["resolvido"] is False
    await db.refresh(ch)
    assert ch.status_plataforma == svc.STATUS_GANHAMOS
    assert ch.status_plataforma_at == decidido_em
    assert float(ch.valor_sugerido) == 45.9 and ch.valor_recuperado is None
    assert await eventos_encerrou() == 0
    row = await linha()
    assert row["status_aba_motivo"] == "ganhamos · robô sugere lucro de R$ 45,90"
    # (A2) a réplica automática segue ligada no banco, mas Encerrado não tem próxima
    assert row["auto_proximo_envio_at"] is None


async def test_resolver_do_cerebro_tira_da_fila_o_que_estava_pendente(
    client, make_user, auth_as, db, monkeypatch
):
    """19/09 (ajuste A3): o cérebro mandou fechar (Encerrado) enquanto uma réplica
    do robô ainda estava `pendente` na fila — ela vira `registrada` com evento;
    senão o lease a entregava de novo num caso que já acabou. Encerrado também
    desliga a réplica automática."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    hdr = {"X-Agent-Token": _TOKEN}
    user = await make_user(permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/chamados/agent/registrar",
        headers=hdr,
        json={"pedido_bling": "295002", "origem": "margem", "conta": "kfa",
              "pedido_marketplace": "2000013416880011", "chamado": "479700011",
              "mensagem": "abertura", "status_envio": "enviada"},
    )
    cid = r.json()["chamado_id"]
    rep = await client.post(f"/api/chamados/{cid}/mensagens", data={"texto": "Cobrando."})
    assert rep.status_code == 201 and rep.json()["status"] == "pendente"
    await client.patch(
        f"/api/chamados/{cid}",
        json={"auto_ligada": True, "auto_dias": 2, "auto_mensagem": "Aguardo retorno."},
    )
    an = await client.post(
        "/api/chamados/agent/analise", headers=hdr,
        json={"chamado_id": cid, "classe": "sem_saida", "resumo": "ML não vai devolver",
              "acao": "resolver", "valor_recuperado": "-30"},
    )
    assert an.status_code == 200, an.text
    ch = (await db.execute(select(Chamado).where(Chamado.id == cid))).scalar_one()
    await db.refresh(ch)
    assert ch.status_plataforma == svc.STATUS_ENCERRADO and ch.auto_ligada is False
    m = (
        await db.execute(select(ChamadoMensagem).where(ChamadoMensagem.id == rep.json()["id"]))
    ).scalar_one()
    assert m.status == "registrada" and m.erro is None
    hist = (await client.get(f"/api/chamados/{cid}/mensagens")).json()
    assert any("pendente(s) na fila cancelada(s)" in h["texto"] for h in hist)
    lease = (await client.post("/api/chamados/agent/lease", headers=hdr, json={})).json()
    assert lease["tarefas"] == []
    row = (await client.get("/api/chamados")).json()["items"][0]
    assert row["status_aba"] == "encerrado" and row["auto_proximo_envio_at"] is None


async def test_instrucao_em_encerrado_ganhamos_mantem_decisao_e_enfileira(
    client, make_user, auth_as, db, monkeypatch
):
    """19/09 (ajuste A4/A5): instrução num chamado Shopee (canal api) Encerrado
    `ganhamos` sai pro cérebro na chamada PADRÃO (sem canais/plataforma — os
    filtros valem só pra resposta nova) e `responder` mantém o `ganhamos`
    (decisão lida da API) — só o Encerrado SEM decisão sai do estado."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    hdr = {"X-Agent-Token": _TOKEN}
    user = await make_user(permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/chamados",
        json={"origem": "devolucao", "pedido_bling": "295003", "plataforma": "shopee",
              "conta": "minas", "canal": "api", "chamado": "2608258J2C6V99"},
    )
    cid = r.json()["id"]
    ch = (await db.execute(select(Chamado).where(Chamado.id == cid))).scalar_one()
    decidido_em = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
    svc.set_status_plataforma(ch, svc.STATUS_GANHAMOS, decidido_em)
    await db.commit()
    # chamada padrão (canal robô, sem plataforma): nada antes da instrução…
    padrao = (await client.post("/api/chamados/agent/analisar", headers=hdr, json={})).json()
    assert padrao["chamados"] == []
    ins = await client.post(
        f"/api/chamados/{cid}/instrucao", json={"texto": "Pede a compensação do frete também."}
    )
    assert ins.status_code == 200, ins.text
    # 21/09 (Vinicius): a instrução tira a linha do Encerrado — vai pra Análise Robô
    assert ins.json()["status_aba"] == "analise_robo"
    # …e com ela o chamado Shopee/api aparece mesmo sem `canais`/`plataforma`
    padrao = (await client.post("/api/chamados/agent/analisar", headers=hdr, json={})).json()
    assert [x["chamado_id"] for x in padrao["chamados"]] == [cid]
    assert padrao["chamados"][0]["instrucao"]["texto"] == "Pede a compensação do frete também."
    assert padrao["chamados"][0]["canal"] == "api"
    assert padrao["chamados"][0]["plataforma"] == "shopee"
    # filtro de outra plataforma/canal também não esconde a instrução
    outra = (
        await client.post(
            "/api/chamados/agent/analisar", headers=hdr,
            json={"plataforma": "ml", "canais": ["robo"]},
        )
    ).json()
    assert [x["chamado_id"] for x in outra["chamados"]] == [cid]
    # canal api sem bloqueio: `responder` continua sem por onde sair (422) — o robô
    # responde `humano` com o motivo; canal robô responde de verdade
    ruim = await client.post(
        "/api/chamados/agent/analise", headers=hdr,
        json={"chamado_id": cid, "classe": "x", "resumo": "y", "acao": "responder",
              "texto_replica": "z"},
    )
    assert ruim.status_code == 422 and ruim.json()["detail"]["code"] == "canal_sem_robo"
    ch.canal = "robo"
    await db.commit()
    ok = await client.post(
        "/api/chamados/agent/analise", headers=hdr,
        json={"chamado_id": cid, "classe": "instrucao", "resumo": "pessoa mandou pedir o frete",
              "acao": "responder", "texto_replica": "Solicitamos também a compensação do frete."},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["replica_id"] and ok.json()["resolvido"] is False
    await db.refresh(ch)
    assert ch.status_plataforma == svc.STATUS_GANHAMOS
    assert ch.status_plataforma_at == decidido_em
    hist = (await client.get(f"/api/chamados/{cid}/mensagens")).json()
    assert not any("saiu de Encerrado" in h["texto"] for h in hist)
    # a réplica enfileirada por instrução sai pelo lease mesmo em Encerrado
    lease = (
        await client.post("/api/chamados/agent/lease", headers=hdr, json={"plataforma": "shopee"})
    ).json()
    assert [t["mensagem_id"] for t in lease["tarefas"]] == [ok.json()["replica_id"]]
    assert (await client.post("/api/chamados/agent/analisar", headers=hdr, json={})).json()[
        "chamados"
    ] == []
    row = (await client.get("/api/chamados")).json()["items"][0]
    assert row["status_aba"] == "encerrado" and row["instrucao_pendente"] is None


async def test_lista_custo_do_produto_so_mostrar(client, make_user, auth_as, db):
    """19/09: a janela Resolver mostra o custo dos itens do pedido (espelho
    bling_orders) pra pessoa decidir lucro/prejuízo — SUM(preco_custo × qtd),
    linhas sem custo não somam mas aparecem no detalhe."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293000")
    rows = (
        await db.execute(select(BlingOrder).where(BlingOrder.numero == "293000"))
    ).scalars().all()
    for r in rows:
        if r.item_codigo == "uaf001m1.110":
            r.preco_custo = 210.5
            r.item_quantidade = 2
        else:
            r.preco_custo = None  # embalagem sem custo cadastrado
    await db.commit()
    r = await client.post("/api/chamados", json={"origem": "devolucao", "pedido_bling": "293000"})
    assert r.status_code == 201, r.text
    assert float(r.json()["custo_produto"]) == 421.0
    assert r.json()["custo_detalhe"] == "uaf001m1.110 × 2; a001 × 1"
    assert r.json()["valor_sugerido"] is None
    # pedido fora do espelho: sem custo
    r2 = await client.post("/api/chamados", json={"origem": "margem", "pedido_bling": "999999"})
    assert r2.json()["custo_produto"] is None and r2.json()["custo_detalhe"] is None
    lst = (await client.get("/api/chamados")).json()["items"]
    assert float(next(i for i in lst if i["id"] == r.json()["id"])["custo_produto"]) == 421.0

