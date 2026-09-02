"""Chamados — aba de Pós-venda (CRUD, histórico/réplica, anexos, status Bling,
réplica automática + monitoramento do cron)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

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

    async def get_claim(self, claim_id):
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
    def __init__(self):
        self.situacao_set: list[tuple[int, int]] = []

    async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> None:
        self.situacao_set.append((bling_order_id, situacao_id))


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

    # pedido obrigatório
    assert (await client.post("/api/chamados", json={"origem": "margem"})).status_code == 422
    # lookup direto
    lk = await client.get("/api/chamados/pedido-lookup", params={"pedido": "2000011"})
    assert lk.status_code == 200 and lk.json()["pedido_bling"] == "293000"
    assert (
        await client.get("/api/chamados/pedido-lookup", params={"pedido": "nope"})
    ).status_code == 404
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

    # resolver aplicando Perdimento junto
    res = await client.post(
        f"/api/chamados/{cid}/resolver", json={"resolvido": True, "situacao": "Perdimento"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["resolvido"] is True
    assert res.json()["resolvido_at"]
    assert res.json()["status_bling"] == "Perdimento"
    assert fake.situacao_set[-1] == (123456, 83956)

    hist = await client.get(f"/api/chamados/{cid}/mensagens")
    textos = [h["texto"] for h in hist.json() if h["tipo"] == "sistema"]
    assert any("Status Bling alterado para Problemas" in t for t in textos)
    assert any("Perdimento" in t for t in textos)
    assert any("resolvido" in t for t in textos)

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


async def test_replica_automatica_respeita_dias(client, make_user, auth_as, db):
    user = await make_user(permissions=_perms())
    auth_as(user)
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

    # resolvido → desliga e para
    await client.post(f"/api/chamados/{cid}/resolver", json={"resolvido": True})
    out = await svc.run_replica_automatica(db, agora=ligado_em + timedelta(days=10))
    assert out["enviados"] == 0
    await db.refresh(ch)
    assert ch.auto_ligada is False


_TOKEN = "tok-chamados-teste"  # noqa: S105


async def test_agent_fluxo_completo(client, make_user, auth_as, db, monkeypatch):
    """Robô registra o chamado aberto (protocolo) → operador responde pela aba
    (canal robô = pendente) → lease entrega a tarefa → resultado marca enviada
    → monitor grava a resposta e resolve."""
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

    # 5) monitor lê a resposta do ML e fecha
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
    assert rec.json()["resolvido"] is True
    hist = await client.get(f"/api/chamados/{cid}/mensagens")
    direcoes = [(h["direcao"], h["tipo"], h["status"]) for h in hist.json()]
    assert ("recebida", "resposta", "registrada") in direcoes
    assert any(h["texto"].startswith("Reembolso aprovado") for h in hist.json())
    assert (await client.get("/api/chamados")).json()["total"] == 0  # resolvido saiu dos abertos


async def test_agent_lease_abrir_e_falha(client, make_user, auth_as, db, monkeypatch):
    """Chamado criado na aba com canal robô e SEM protocolo → tarefa `abrir`;
    resultado ok com protocolo grava na linha; falha fica visível."""
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
    falha = await client.post(
        "/api/chamados/agent/resultado",
        headers=hdr,
        json={"mensagem_id": m2["id"], "ok": False, "erro": "formulário mudou"},
    )
    assert falha.json()["status"] == "falhou" and falha.json()["erro"] == "formulário mudou"
    assert (
        await client.post(
            "/api/chamados/agent/resultado",
            headers=hdr,
            json={"mensagem_id": str(uuid4()), "ok": True},
        )
    ).status_code == 404


async def test_monitoramento_fecha_quando_ml_encerra(client, make_user, auth_as, db, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeML(closed=True)

    async def _fake_client(session, conta):
        return fake

    monkeypatch.setattr(svc, "_ml_client_para", _fake_client)
    r = await client.post(
        "/api/chamados",
        json={
            "origem": "logistica",
            "pedido_bling": "8",
            "plataforma": "ml",
            "conta": "aguiar",
            "canal": "api",
            "chamado": "777",
            "monitoramento": True,
        },
    )
    cid = r.json()["id"]
    out = await svc.run_replica_automatica(db)
    assert out["resolvidos"] == 1
    ch = (await db.execute(select(Chamado).where(Chamado.id == cid))).scalar_one()
    assert ch.resolvido is True
    hist = await client.get(f"/api/chamados/{cid}/mensagens")
    assert any("encerrado na plataforma" in h["texto"] for h in hist.json())
