"""Os ajustes NOSSOS na Central de e-mail do outro dev (08/10/2026, etapa A).

A Central (0386, `test_mail_central.py`) é a base e o teste dela passa SEM
mudança. Aqui, só o que acrescentamos por cima, sempre compatível:

  1. aliases: até 200 (a conta geral do Tuta tem 54 + o principal) e o PATCH
     que troca a lista recifrando o `config_enc` (o principal nunca sai); o
     lease confere de novo o remetente (alias tirado depois não sai);
  2. remetente estrito por caixa (tabela nossa `mail_mailbox_settings`):
     nunca cai no endereço principal; padrão DESLIGADO (a Goslin não muda);
  3. POST /api/mail/outbox/{job}/resolve {saiu}: o "incerto" sai da trava
     sem reenviar nada; o recibo atrasado do agente leva 409, nunca 500;
  4. caixa `empresa`: responder, ligar o envio, aliases, chave do Mac e
     resolver pela caixa crua exigem MEXER no /atendimento; e o modo teste,
     a pausa e os tetos valem na hora de enfileirar;
  5. a rota GET/PATCH da configuração;
  6. o contrato v1 do agente continua byte a byte o mesmo formato.

Postgres local de verdade + HTTP em processo; nunca conta do Tuta nem envio.
"""

import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models import UserRole
from app.models.mail import MailMailbox, MailOutbox
from app.models.mail_atendimento import MailMailboxSettings
from app.security.cipher import decrypt_json
from app.services import mail_central
from app.services.mail_atendimento import caixa as config_caixa

ROOT = "/api/mail"
OUTRO = "quem-mexe@davinci-test.com"

# A conta geral do Tuta (conferida ao vivo): o principal + 54 aliases. Aqui só
# endereços de exemplo, na mesma quantidade.
GERAL_ALIASES = [f"loja{n:02d}@example.com" for n in range(1, 55)]


def _mensagem(**mudancas) -> dict:
    base = {
        "source_id": f"tuta:{uuid4().hex}",
        "folder": "ml",
        "direction": "inbound",
        "received_at": datetime.now(UTC).isoformat(),
        "subject": "Pedido 123",
        "from_address": "cliente@example.com",
        "from_name": "Cliente",
        "to": ["loja01@example.com"],
        "cc": [],
        "reply_to": None,
        "message_id": f"<{uuid4().hex}@example.com>",
        "in_reply_to": None,
        "references": [],
        "text": "Quando chega?",
        "attachments": [],
    }
    return {**base, **mudancas}


@pytest.fixture
def todo_admin_mexe(monkeypatch):
    """Lista do ATENDIMENTO_USUARIOS vazia = todo admin MEXE no /atendimento."""
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "")


@pytest.fixture
def admin_nao_mexe(monkeypatch):
    """Só OUTRA pessoa mexe: os admins do teste só leem o /atendimento."""
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", OUTRO)


@pytest.fixture
def freio_aberto(monkeypatch):
    """O freio geral do /atendimento LIGADO para enviar (ATENDIMENTO_ENVIO_ATIVO)."""
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)


@pytest.fixture
async def caixa(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        f"{ROOT}/mailboxes",
        json={
            "label": "Geral — Tuta",
            "address": "principal@example.com",
            "aliases": GERAL_ALIASES,
        },
    )
    assert r.status_code == 201, r.text
    box = r.json()
    return {
        "admin": admin,
        "box": box,
        "id": box["id"],
        "agente": f"{ROOT}/agent/{box['id']}",
        "headers": {"Authorization": f"Bearer {box['agent_token']}"},
    }


async def _ingerir(client, caixa, **mudancas) -> str:
    item = _mensagem(**mudancas)
    r = await client.post(
        f"{caixa['agente']}/ingest", headers=caixa["headers"], json={"messages": [item]}
    )
    assert r.status_code == 200, r.text
    lista = (await client.get(f"{ROOT}/mailboxes/{caixa['id']}/messages?limit=100")).json()
    # A listagem vem da mais nova para a mais velha: a recém-chegada é a 1ª.
    return lista["items"][0]["id"]


async def _ligar_envio(client, caixa):
    r = await client.patch(f"{ROOT}/mailboxes/{caixa['id']}", json={"send_enabled": True})
    assert r.status_code == 200, r.text
    r = await client.post(
        f"{caixa['agente']}/heartbeat",
        headers=caixa["headers"],
        json={"state": "online", "can_send": True},
    )
    assert r.status_code == 200, r.text


async def _responder(client, message_id, **extra):
    body = {"request_id": str(uuid4()), "text": "Resposta de pessoa", **extra}
    return await client.post(f"{ROOT}/messages/{message_id}/reply", json=body)


async def _lease(client, caixa) -> list[dict]:
    r = await client.post(f"{caixa['agente']}/outbox/lease", json={}, headers=caixa["headers"])
    assert r.status_code == 200, r.text
    return r.json()["jobs"]


async def _config(db, caixa, **valores) -> None:
    db.add(MailMailboxSettings(mailbox_id=UUID(caixa["id"]), **valores))
    await db.commit()


# ─── 1. aliases ─────────────────────────────────────────────────────────────


async def test_geral_cabe_e_o_teto_novo_e_200(caixa, client):
    assert len(caixa["box"]["aliases"]) == 55  # o principal + 54
    assert caixa["box"]["aliases"][0] == "principal@example.com"
    demais = [f"a{n}@example.com" for n in range(201)]
    r = await client.post(
        f"{ROOT}/mailboxes", json={"label": "X", "address": "x@example.com", "aliases": demais}
    )
    assert r.status_code == 422
    r = await client.patch(f"{ROOT}/mailboxes/{caixa['id']}", json={"aliases": demais})
    assert r.status_code == 422


async def test_patch_de_aliases_recifra_e_mantem_o_principal(caixa, client, db):
    antes = (await db.get(MailMailbox, UUID(caixa["id"]))).config_enc
    r = await client.patch(
        f"{ROOT}/mailboxes/{caixa['id']}",
        json={"aliases": ["MIA30@Example.com", "yuki31@example.com", "mia30@example.com"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["aliases"] == [
        "principal@example.com",
        "mia30@example.com",
        "yuki31@example.com",
    ]
    assert r.json()["address"] == "principal@example.com"
    caixa_db = await db.get(MailMailbox, UUID(caixa["id"]))
    await db.refresh(caixa_db)
    assert caixa_db.config_enc != antes
    assert b"mia30" not in caixa_db.config_enc  # cifrado em repouso, como sempre
    assert decrypt_json(caixa_db.config_enc) == {
        "address": "principal@example.com",
        "aliases": ["principal@example.com", "mia30@example.com", "yuki31@example.com"],
    }
    # Lista vazia = só o principal (nunca uma caixa sem remetente).
    r = await client.patch(f"{ROOT}/mailboxes/{caixa['id']}", json={"aliases": []})
    assert r.json()["aliases"] == ["principal@example.com"]
    # Só o rótulo: aliases intocados.
    r = await client.patch(f"{ROOT}/mailboxes/{caixa['id']}", json={"label": "Outro nome"})
    assert r.json()["aliases"] == ["principal@example.com"]


async def test_aliases_so_admin(caixa, client, make_user, auth_as):
    dono = await make_user()
    auth_as(dono)
    r = await client.patch(f"{ROOT}/mailboxes/{caixa['id']}", json={"aliases": ["z@example.com"]})
    assert r.status_code == 403


async def test_alias_tirado_depois_de_enfileirar_nao_sai(caixa, client, db):
    mid = await _ingerir(client, caixa)
    await _ligar_envio(client, caixa)
    r = await _responder(client, mid)
    assert r.status_code == 202, r.text
    assert r.json()["from_address"] == "loja01@example.com"
    r = await client.patch(
        f"{ROOT}/mailboxes/{caixa['id']}", json={"aliases": ["loja02@example.com"]}
    )
    assert r.status_code == 200
    assert await _lease(client, caixa) == []
    job = await db.scalar(select(MailOutbox))
    await db.refresh(job)
    assert (job.status, job.error_code) == ("failed", "sender_not_authorized")


# ─── 2. remetente estrito ───────────────────────────────────────────────────


async def test_sem_configuracao_a_central_continua_caindo_no_principal(caixa, client):
    """A Goslin de hoje: e-mail num alias que a caixa não conhece → a Central
    escolhe o principal. Sem linha de configuração, NADA muda."""
    mid = await _ingerir(client, caixa, to=["mia30@example.com"])
    detalhe = (await client.get(f"{ROOT}/messages/{mid}")).json()
    assert detalhe["reply"]["from_address"] == "principal@example.com"
    assert detalhe["reply"]["can_reply"] is True
    assert detalhe["reply"]["strict_sender"] is False
    await _ligar_envio(client, caixa)
    r = await _responder(client, mid)
    assert r.status_code == 202, r.text
    assert r.json()["from_address"] == "principal@example.com"


async def test_estrito_nunca_cai_no_principal(caixa, client, db):
    await _config(db, caixa, remetente_estrito=True)
    await _ligar_envio(client, caixa)
    # Chegou num endereço que a caixa não tem: não responde.
    mid = await _ingerir(client, caixa, to=["mia30@example.com"])
    detalhe = (await client.get(f"{ROOT}/messages/{mid}")).json()
    assert detalhe["reply"]["from_address"] is None
    assert detalhe["reply"]["can_reply"] is False
    assert detalhe["reply"]["receiving_aliases"] == []
    r = await _responder(client, mid)
    assert r.json()["detail"]["code"] == "no_receiving_alias"
    # Nem escolhendo o principal na mão.
    r = await _responder(client, mid, from_address="principal@example.com")
    assert r.json()["detail"]["code"] == "sender_not_receiving_alias"
    assert await db.scalar(select(func.count()).select_from(MailOutbox)) == 0


async def test_estrito_usa_o_alias_que_recebeu_inclusive_em_copia(caixa, client, db):
    await _config(db, caixa, remetente_estrito=True)
    await _ligar_envio(client, caixa)
    mid = await _ingerir(
        client, caixa, to=["fora@example.com"], cc=["LOJA07@example.com", "loja09@example.com"]
    )
    detalhe = (await client.get(f"{ROOT}/messages/{mid}")).json()
    assert detalhe["reply"]["from_address"] == "loja07@example.com"
    assert detalhe["reply"]["receiving_aliases"] == ["loja07@example.com", "loja09@example.com"]
    # Outro alias da caixa que NÃO recebeu: recusado.
    r = await _responder(client, mid, from_address="loja01@example.com")
    assert r.json()["detail"]["code"] == "sender_not_receiving_alias"
    r = await _responder(client, mid, from_address="loja09@example.com")
    assert r.status_code == 202, r.text
    assert r.json()["from_address"] == "loja09@example.com"


def test_alias_que_recebeu_pura():
    aliases = {"a@x.com", "b@x.com", "c@x.com"}
    conteudo = {"to": ["Z@x.com", "B@x.com"], "cc": ["a@x.com", "b@x.com"]}
    assert mail_central.receiving_aliases(aliases, conteudo) == ["b@x.com", "a@x.com"]
    # O `delivered_to` (do conector v2, quando vier) manda primeiro.
    conteudo["delivered_to"] = ["c@x.com"]
    assert mail_central.receiving_aliases(aliases, conteudo) == ["c@x.com", "b@x.com", "a@x.com"]
    assert mail_central.receiving_aliases(aliases, {"to": [], "cc": []}) == []


# ─── 3. resolver o "incerto" ────────────────────────────────────────────────


async def _incerto(client, caixa, db, mid=None) -> tuple[str, dict]:
    mid = mid or await _ingerir(client, caixa)
    await _ligar_envio(client, caixa)
    r = await _responder(client, mid)
    assert r.status_code == 202, r.text
    lease = (await _lease(client, caixa))[0]
    job = await db.get(MailOutbox, UUID(lease["id"]))
    job.leased_at = datetime.now(UTC) - timedelta(minutes=16)
    await db.commit()
    assert await _lease(client, caixa) == []  # vira "uncertain", nunca reenvia
    await db.refresh(job)
    assert job.status == "uncertain"
    return mid, lease


async def test_resolver_nao_saiu_libera_responder_de_novo(caixa, client, db):
    mid, lease = await _incerto(client, caixa, db)
    r = await _responder(client, mid)
    assert r.json()["detail"]["code"] == "reply_already_pending"
    r = await client.post(f"{ROOT}/outbox/{lease['id']}/resolve", json={"saiu": False})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["error_code"]) == ("failed", "resolved_not_sent")
    job = await db.get(MailOutbox, UUID(lease["id"]))
    await db.refresh(job)
    recibo = decrypt_json(job.receipt_enc)
    assert recibo["resolved_by"] == str(caixa["admin"].id)
    assert recibo["previous"]["status"] == "uncertain"
    assert recibo["previous"]["error_code"] == "receipt_timeout"
    # A trava saiu: a pessoa pode responder de novo (por clique).
    r = await _responder(client, mid)
    assert r.status_code == 202, r.text
    # Resolver duas vezes não existe.
    r = await client.post(f"{ROOT}/outbox/{lease['id']}/resolve", json={"saiu": True})
    assert r.json()["detail"]["code"] == "job_not_uncertain"


async def test_resolver_saiu_e_o_recibo_atrasado_leva_409(caixa, client, db):
    _, lease = await _incerto(client, caixa, db)
    r = await client.post(f"{ROOT}/outbox/{lease['id']}/resolve", json={"saiu": True})
    assert (r.json()["status"], r.json()["error_code"]) == ("sent", None)
    url = f"{caixa['agente']}/outbox/{lease['id']}/receipt"
    for status in ("sent", "failed"):
        r = await client.post(
            url,
            headers=caixa["headers"],
            json={"lease_token": lease["lease_token"], "status": status},
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "receipt_conflict"
    job = await db.get(MailOutbox, UUID(lease["id"]))
    await db.refresh(job)
    assert job.status == "sent"


async def test_resolver_lease_vencido_sem_esperar_o_mac(caixa, client, db):
    """O Mac sumiu: o job ficou 'leased' para sempre (só o próximo lease o
    marcaria). Passados os 15 min, a pessoa resolve direto."""
    mid = await _ingerir(client, caixa)
    await _ligar_envio(client, caixa)
    assert (await _responder(client, mid)).status_code == 202
    lease = (await _lease(client, caixa))[0]
    r = await client.post(f"{ROOT}/outbox/{lease['id']}/resolve", json={"saiu": False})
    assert r.json()["detail"]["code"] == "job_not_uncertain"  # ainda no prazo
    job = await db.get(MailOutbox, UUID(lease["id"]))
    job.leased_at = datetime.now(UTC) - timedelta(minutes=16)
    await db.commit()
    r = await client.post(f"{ROOT}/outbox/{lease['id']}/resolve", json={"saiu": False})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"


async def test_resolver_na_fila_ou_de_outra_pessoa(caixa, client, db, make_user, auth_as):
    mid = await _ingerir(client, caixa)
    await _ligar_envio(client, caixa)
    job = (await _responder(client, mid)).json()
    r = await client.post(f"{ROOT}/outbox/{job['id']}/resolve", json={"saiu": True})
    assert r.json()["detail"]["code"] == "job_not_uncertain"
    r = await client.post(f"{ROOT}/outbox/{uuid4()}/resolve", json={"saiu": True})
    assert r.status_code == 404
    r = await client.post(f"{ROOT}/outbox/{job['id']}/resolve", json={"saiu": "talvez"})
    assert r.status_code == 422
    r = await client.post(f"{ROOT}/outbox/{job['id']}/resolve", json={"saiu": True, "x": 1})
    assert r.status_code == 422
    estranho = await make_user()
    auth_as(estranho)
    r = await client.post(f"{ROOT}/outbox/{job['id']}/resolve", json={"saiu": True})
    assert r.status_code == 404  # nem fica sabendo que a caixa existe
    # O DONO (não admin) resolve na caixa dele, como lê e responde.
    caixa_db = await db.get(MailMailbox, UUID(caixa["id"]))
    caixa_db.owner_user_id = estranho.id
    await db.commit()
    r = await client.post(f"{ROOT}/outbox/{job['id']}/resolve", json={"saiu": True})
    assert r.json()["detail"]["code"] == "job_not_uncertain"


# ─── 4. caixa da EMPRESA ────────────────────────────────────────────────────


async def test_caixa_privada_admin_que_nao_mexe_continua_como_antes(caixa, client, admin_nao_mexe):
    mid = await _ingerir(client, caixa)
    await _ligar_envio(client, caixa)
    assert (await _responder(client, mid)).status_code == 202
    r = await client.post(f"{ROOT}/mailboxes/{caixa['id']}/token")
    assert r.status_code == 200


async def test_caixa_empresa_exige_mexer_na_caixa_crua(caixa, client, db, admin_nao_mexe):
    mid = await _ingerir(client, caixa)
    await _ligar_envio(client, caixa)
    await _config(db, caixa, visibilidade="empresa", envio_modo="real")
    negado = {"detail": {"code": "atendimento_permission_required"}}
    r = await _responder(client, mid)
    assert (r.status_code, r.json()) == (403, negado)
    for corpo in ({"send_enabled": False}, {"aliases": ["x@example.com"]}):
        r = await client.patch(f"{ROOT}/mailboxes/{caixa['id']}", json=corpo)
        assert (r.status_code, r.json()) == (403, negado)
    r = await client.post(f"{ROOT}/mailboxes/{caixa['id']}/token")
    assert (r.status_code, r.json()) == (403, negado)
    # Ler e trocar o nome continuam (dono/admin, como a Central).
    assert (await client.get(f"{ROOT}/messages/{mid}")).status_code == 200
    r = await client.patch(f"{ROOT}/mailboxes/{caixa['id']}", json={"label": "Geral"})
    assert r.status_code == 200
    caixa_db = await db.get(MailMailbox, UUID(caixa["id"]))
    await db.refresh(caixa_db)
    assert caixa_db.send_enabled is True
    assert await db.scalar(select(func.count()).select_from(MailOutbox)) == 0


async def test_caixa_empresa_resolver_exige_mexer(caixa, client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "")
    _, lease = await _incerto(client, caixa, db)
    await _config(db, caixa, visibilidade="empresa")
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", OUTRO)
    r = await client.post(f"{ROOT}/outbox/{lease['id']}/resolve", json={"saiu": False})
    assert r.status_code == 403
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "")
    r = await client.post(f"{ROOT}/outbox/{lease['id']}/resolve", json={"saiu": False})
    assert r.status_code == 200


async def test_caixa_empresa_modo_teste_so_a_lista_recebe(
    caixa, client, db, todo_admin_mexe, freio_aberto
):
    mid = await _ingerir(client, caixa, from_address="cliente@example.com")
    await _ligar_envio(client, caixa)
    await _config(db, caixa, visibilidade="empresa")  # envio_modo padrão = teste
    r = await _responder(client, mid)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "test_mode_recipient")
    teste = await _ingerir(client, caixa, from_address="Nosso.Teste@example.com")
    cfg = await db.get(MailMailboxSettings, UUID(caixa["id"]))
    cfg.destinatarios_teste = ["nosso.teste@example.com"]
    await db.commit()
    assert (await _responder(client, teste)).status_code == 202
    assert (await _responder(client, mid)).json()["detail"]["code"] == "test_mode_recipient"


async def test_caixa_empresa_pausa_e_tetos(caixa, client, db, todo_admin_mexe, freio_aberto):
    await _ligar_envio(client, caixa)
    await _config(
        db,
        caixa,
        visibilidade="empresa",
        envio_modo="real",
        envio_pausado_ate=datetime.now(UTC) + timedelta(hours=1),
        teto_hora=1,
        teto_dia=2,
    )
    primeira = await _ingerir(client, caixa)
    r = await _responder(client, primeira)
    assert r.json()["detail"]["code"] == "sending_paused"
    cfg = await db.get(MailMailboxSettings, UUID(caixa["id"]))
    cfg.envio_pausado_ate = datetime.now(UTC) - timedelta(minutes=1)  # pausa vencida
    await db.commit()
    job = (await _responder(client, primeira)).json()
    assert job["status"] == "queued"
    segunda = await _ingerir(client, caixa)
    assert (await _responder(client, segunda)).json()["detail"]["code"] == "hourly_limit"
    # O que comprovadamente não saiu não conta no teto.
    job_db = await db.get(MailOutbox, UUID(job["id"]))
    job_db.status = "failed"
    await db.commit()
    assert (await _responder(client, segunda)).status_code == 202
    # Teto do dia: a de 2 h atrás conta no dia, não na hora.
    job_db = await db.scalar(select(MailOutbox).where(MailOutbox.status == "queued"))
    job_db.created_at = datetime.now(UTC) - timedelta(hours=2)
    job_db2 = await db.get(MailOutbox, UUID(job["id"]))
    job_db2.status = "sent"
    job_db2.created_at = datetime.now(UTC) - timedelta(hours=3)
    await db.commit()
    terceira = await _ingerir(client, caixa)
    assert (await _responder(client, terceira)).json()["detail"]["code"] == "daily_limit"
    # A conta inteira do Tuta (o que o agente conta de si).
    cfg.teto_dia = 300
    cfg.agente_info = {"contadores": {"enviados_conta_hora": 100}}
    await db.commit()
    assert (await _responder(client, terceira)).json()["detail"]["code"] == "account_hourly_limit"
    cfg.agente_info = {"contadores": {"enviados_conta_hora": 99}}
    await db.commit()
    assert (await _responder(client, terceira)).status_code == 202


async def test_repeticao_do_mesmo_clique_continua_idempotente_no_teto(
    caixa, client, db, todo_admin_mexe, freio_aberto
):
    await _ligar_envio(client, caixa)
    await _config(db, caixa, visibilidade="empresa", envio_modo="real", teto_hora=1)
    mid = await _ingerir(client, caixa)
    corpo = {"request_id": str(uuid4()), "text": "Uma vez só"}
    primeira = await client.post(f"{ROOT}/messages/{mid}/reply", json=corpo)
    repetida = await client.post(f"{ROOT}/messages/{mid}/reply", json=corpo)
    assert primeira.status_code == repetida.status_code == 202
    assert primeira.json()["id"] == repetida.json()["id"]


# ─── 5. rota da configuração ────────────────────────────────────────────────


async def test_configuracao_padrao_sem_linha(caixa, client, make_user, auth_as, db):
    r = await client.get(f"{ROOT}/mailboxes/{caixa['id']}/settings")
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["configurada"] is False
    assert {
        k: cfg[k] for k in ("visibilidade", "ponte_ligada", "remetente_estrito", "envio_modo")
    } == {
        "visibilidade": "privada",
        "ponte_ligada": False,
        "remetente_estrito": False,
        "envio_modo": "teste",
    }
    assert (cfg["teto_hora"], cfg["teto_dia"], cfg["teto_conta_hora"]) == (30, 300, 100)
    assert cfg["ponte_so_aliases_de_loja"] is True
    assert r.headers["cache-control"] == "no-store"
    estranho = await make_user()
    auth_as(estranho)
    assert (await client.get(f"{ROOT}/mailboxes/{caixa['id']}/settings")).status_code == 404
    r = await client.patch(
        f"{ROOT}/mailboxes/{caixa['id']}/settings", json={"remetente_estrito": True}
    )
    assert r.status_code == 403
    # O DONO (não admin) lê a configuração da caixa dele, mas não muda.
    caixa_db = await db.get(MailMailbox, UUID(caixa["id"]))
    caixa_db.owner_user_id = estranho.id
    await db.commit()
    assert (await client.get(f"{ROOT}/mailboxes/{caixa['id']}/settings")).status_code == 200
    r = await client.patch(
        f"{ROOT}/mailboxes/{caixa['id']}/settings", json={"remetente_estrito": True}
    )
    assert r.status_code == 403
    assert await db.scalar(select(func.count()).select_from(MailMailboxSettings)) == 0


async def test_configuracao_privada_quem_pode_mudar_o_que(
    caixa, client, admin_nao_mexe, monkeypatch
):
    url = f"{ROOT}/mailboxes/{caixa['id']}/settings"
    # Remetente estrito numa caixa privada: do admin, como ligar o envio.
    r = await client.patch(url, json={"remetente_estrito": True})
    assert r.status_code == 200, r.text
    assert r.json()["remetente_estrito"] is True
    assert r.json()["configurada"] is True
    assert r.json()["updated_by"] == str(caixa["admin"].id)
    # A ponte e a visibilidade decidem o que a equipe vê: só quem mexe.
    for corpo in ({"visibilidade": "empresa"}, {"ponte_ligada": True}):
        r = await client.patch(url, json=corpo)
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "atendimento_permission_required"
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "")
    r = await client.patch(url, json={"visibilidade": "empresa"})
    assert r.json()["visibilidade"] == "empresa"
    # Numa caixa da empresa, TUDO exige mexer.
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", OUTRO)
    r = await client.patch(url, json={"remetente_estrito": False})
    assert r.status_code == 403


async def test_configuracao_ponte_corte_e_privada_so_lojas(caixa, client, todo_admin_mexe):
    url = f"{ROOT}/mailboxes/{caixa['id']}/settings"
    antes = datetime.now(UTC)
    r = await client.patch(url, json={"ponte_ligada": True})
    assert r.status_code == 200, r.text
    corte = datetime.fromisoformat(r.json()["ponte_desde"])
    assert corte >= antes - timedelta(seconds=1)
    # Caixa privada não leva para a equipe o que não é de loja.
    r = await client.patch(url, json={"ponte_so_aliases_de_loja": False})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "ponte_privada_so_aliases_de_loja")
    r = await client.get(url)
    assert r.json()["ponte_so_aliases_de_loja"] is True  # nada gravado
    # Na empresa pode (a geral recebe também os e-mails dos sites).
    r = await client.patch(url, json={"visibilidade": "empresa", "ponte_so_aliases_de_loja": False})
    assert r.status_code == 200, r.text
    # Um corte escolhido fica; desligar a ponte mantém o corte.
    escolhido = "2026-10-01T03:00:00+00:00"
    r = await client.patch(url, json={"ponte_desde": escolhido, "ponte_ligada": False})
    assert datetime.fromisoformat(r.json()["ponte_desde"]) == datetime.fromisoformat(escolhido)
    assert r.json()["ponte_ligada"] is False


async def test_configuracao_validacao(caixa, client, todo_admin_mexe):
    url = f"{ROOT}/mailboxes/{caixa['id']}/settings"
    for corpo in (
        {"visibilidade": "publica"},
        {"envio_modo": "producao"},
        {"teto_hora": 101},
        {"teto_dia": -1},
        {"visibilidade": None},
        {"agente_info": {"x": 1}},
        {"agente_tipo": "v2"},
        {"destinatarios_teste": ["nao-e-email"]},
        {"ponte_desde": "2026-10-01T03:00:00"},  # sem fuso
    ):
        r = await client.patch(url, json=corpo)
        assert r.status_code == 422, corpo
    r = await client.patch(
        url,
        json={
            "destinatarios_teste": ["A@Example.com", "a@example.com", "b@example.com"],
            "envio_pausado_ate": "2026-10-09T12:00:00+00:00",
            "envio_pausa_motivo": "  conferir o piloto  ",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["destinatarios_teste"] == ["a@example.com", "b@example.com"]
    assert r.json()["envio_pausa_motivo"] == "conferir o piloto"
    # null limpa a pausa.
    r = await client.patch(url, json={"envio_pausado_ate": None, "envio_pausa_motivo": None})
    assert (r.json()["envio_pausado_ate"], r.json()["envio_pausa_motivo"]) == (None, None)


async def test_salvar_reconfere_a_permissao_com_a_linha_travada(caixa, db, admin_nao_mexe):
    """Outra aba passou a caixa para `empresa` depois que a rota leu: a
    gravação confere de novo, já com a linha travada."""
    await _config(db, caixa, visibilidade="empresa")
    with pytest.raises(config_caixa.ConfigCaixaError) as erro:
        await config_caixa.salvar(
            db, UUID(caixa["id"]), {"remetente_estrito": True}, caixa["admin"]
        )
    assert (erro.value.codigo, erro.value.status) == ("atendimento_permission_required", 403)
    await db.rollback()
    with pytest.raises(config_caixa.ConfigCaixaError) as erro:
        await config_caixa.salvar(db, UUID(caixa["id"]), {"agente_tipo": "v2"}, caixa["admin"])
    assert erro.value.codigo == "campo_nao_editavel"


async def test_configuracao_some_com_a_caixa(caixa, client, db, todo_admin_mexe):
    await client.patch(f"{ROOT}/mailboxes/{caixa['id']}/settings", json={"remetente_estrito": True})
    assert await db.scalar(select(func.count()).select_from(MailMailboxSettings)) == 1
    await db.delete(await db.get(MailMailbox, UUID(caixa["id"])))
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(MailMailboxSettings)) == 0


# ─── 6. o contrato v1 do agente não mudou ───────────────────────────────────


async def test_contrato_v1_do_agente_mesmo_formato(
    caixa, client, db, todo_admin_mexe, freio_aberto
):
    """As respostas do v1 têm EXATAMENTE as chaves de antes (o cliente dele
    pode ser estrito) — mesmo numa caixa da empresa com tudo configurado."""
    await _config(db, caixa, visibilidade="empresa", envio_modo="real", remetente_estrito=True)
    r = await client.post(
        f"{caixa['agente']}/heartbeat",
        headers=caixa["headers"],
        json={"state": "online", "can_send": True},
    )
    assert r.json() == {"ok": True, "send_enabled": False}
    item = _mensagem(
        attachments=[{"filename": "a.txt", "data_base64": base64.b64encode(b"x").decode()}]
    )
    r = await client.post(
        f"{caixa['agente']}/ingest", headers=caixa["headers"], json={"messages": [item]}
    )
    assert r.json() == {"accepted": 1, "duplicates": 0}
    r = await client.post(
        f"{caixa['agente']}/ingest", headers=caixa["headers"], json={"messages": [item]}
    )
    assert r.json() == {"accepted": 0, "duplicates": 1}
    mid = (await client.get(f"{ROOT}/mailboxes/{caixa['id']}/messages")).json()["items"][0]["id"]
    await _ligar_envio(client, caixa)
    assert (await _responder(client, mid)).status_code == 202
    r = await client.post(f"{caixa['agente']}/outbox/lease", json={}, headers=caixa["headers"])
    assert set(r.json()) == {"jobs"}
    job = r.json()["jobs"][0]
    assert set(job) == {
        "id",
        "lease_token",
        "from_address",
        "to",
        "subject",
        "text",
        "in_reply_to",
        "references",
        "message_id",
    }
    assert job["from_address"] == "loja01@example.com"
    r = await client.post(
        f"{caixa['agente']}/outbox/{job['id']}/receipt",
        headers=caixa["headers"],
        json={"lease_token": job["lease_token"], "status": "sent"},
    )
    assert r.json() == {"ok": True, "status": "sent"}
    # O lease continua só com {} (nenhum campo novo no v1).
    r = await client.post(
        f"{caixa['agente']}/outbox/lease", json={"max_jobs": 1}, headers=caixa["headers"]
    )
    assert r.status_code == 422


# ─── 7. a crítica de 08/10: o dono da privada, o freio, a pausa e o teste no lease ───


async def test_so_o_dono_abre_a_caixa_privada_para_a_equipe(
    caixa, client, make_user, auth_as, todo_admin_mexe
):
    """Outro admin (que mexe) não passa a privada para empresa, não desliga "só
    aliases de loja" nem puxa o corte para trás; desligar a ponte, pode."""
    url = f"{ROOT}/mailboxes/{caixa['id']}/settings"
    r = await client.patch(url, json={"ponte_ligada": True})
    assert r.status_code == 200, r.text
    outro_admin = await make_user(role=UserRole.ADMIN)
    auth_as(outro_admin)
    for corpo in (
        {"visibilidade": "empresa"},
        {"ponte_so_aliases_de_loja": False, "visibilidade": "empresa"},
        {"ponte_desde": "2026-01-01T00:00:00+00:00"},
    ):
        r = await client.patch(url, json=corpo)
        assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_o_dono_da_caixa"), corpo
    r = await client.patch(url, json={"ponte_ligada": False})
    assert r.status_code == 200 and r.json()["ponte_ligada"] is False
    # O dono decide.
    auth_as(caixa["admin"])
    r = await client.patch(url, json={"visibilidade": "empresa"})
    assert r.status_code == 200 and r.json()["visibilidade"] == "empresa"
    # Já da empresa: o resto segue a regra da empresa (quem mexe).
    auth_as(outro_admin)
    r = await client.patch(url, json={"ponte_so_aliases_de_loja": False})
    assert r.status_code == 200, r.text


async def test_caixa_crua_da_empresa_respeita_o_freio_geral(caixa, client, db, todo_admin_mexe):
    mid = await _ingerir(client, caixa)
    await _ligar_envio(client, caixa)
    await _config(db, caixa, visibilidade="empresa", envio_modo="real")
    r = await _responder(client, mid)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "sending_disabled")


async def test_sinal_do_mac_desliga_com_o_freio_ou_a_pausa_so_na_empresa(
    caixa, client, db, todo_admin_mexe, monkeypatch
):
    async def sinal() -> bool:
        r = await client.post(
            f"{caixa['agente']}/heartbeat",
            headers=caixa["headers"],
            json={"state": "online", "can_send": True},
        )
        return r.json()["send_enabled"]

    await _ligar_envio(client, caixa)
    # Privada (sem linha): o send_enabled dela, mesmo com o freio fechado.
    assert await sinal() is True
    await _config(db, caixa, visibilidade="empresa", envio_modo="real")
    assert await sinal() is False  # o freio do .env está fechado no teste
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    assert await sinal() is True
    cfg = await db.get(MailMailboxSettings, UUID(caixa["id"]))
    cfg.envio_pausado_ate = datetime.now(UTC) + timedelta(hours=1)
    await db.commit()
    assert await sinal() is False


async def test_lease_segura_o_que_ficou_na_fila_com_freio_pausa_ou_teste(
    caixa, client, db, todo_admin_mexe, freio_aberto, monkeypatch
):
    """A resposta da empresa enfileirada ANTES de alguém fechar o freio, pausar
    ou voltar para o modo teste não sai quando o Mac pede trabalho."""
    await _ligar_envio(client, caixa)
    await _config(db, caixa, visibilidade="empresa", envio_modo="real")
    mid = await _ingerir(client, caixa)
    job = (await _responder(client, mid)).json()
    assert job["status"] == "queued"
    # Freio fechado depois do clique: fica na fila (não sai, não falha).
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", False)
    assert await _lease(client, caixa) == []
    # Pausa: também fica.
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    cfg = await db.get(MailMailboxSettings, UUID(caixa["id"]))
    cfg.envio_pausado_ate = datetime.now(UTC) + timedelta(hours=1)
    await db.commit()
    assert await _lease(client, caixa) == []
    stored = await db.get(MailOutbox, UUID(job["id"]))
    await db.refresh(stored)
    assert stored.status == "queued"
    # Voltou para o modo teste e o cliente não está na lista: falha, nada sai.
    cfg.envio_pausado_ate = None
    cfg.envio_modo = "teste"
    await db.commit()
    assert await _lease(client, caixa) == []
    await db.refresh(stored)
    assert (stored.status, stored.error_code) == ("failed", "test_mode_recipient")


async def test_lease_nao_solta_resposta_velha_e_a_segurada_nao_trava_as_outras(
    caixa, client, db, todo_admin_mexe, freio_aberto
):
    await _ligar_envio(client, caixa)
    await _config(db, caixa, visibilidade="empresa", envio_modo="real", teto_hora=100)
    velhas = []
    for _ in range(6):
        mid = await _ingerir(client, caixa)
        velhas.append((await _responder(client, mid)).json()["id"])
    nova_mid = await _ingerir(client, caixa)
    nova = (await _responder(client, nova_mid)).json()["id"]
    for jid in velhas:
        j = await db.get(MailOutbox, UUID(jid))
        j.created_at = datetime.now(UTC) - timedelta(hours=3)
    await db.commit()
    # As 6 velhas (mais que um lote de 5) viram failed e a nova sai na mesma volta.
    jobs = await _lease(client, caixa)
    assert [j["id"] for j in jobs] == [nova]
    for jid in velhas:
        j = await db.get(MailOutbox, UUID(jid))
        await db.refresh(j)
        assert (j.status, j.error_code) == ("failed", "queued_timeout")


async def test_lease_da_caixa_crua_privada_nao_muda(caixa, client, db):
    """Sem linha de configuração (a Goslin dele): freio fechado, job de 3 h — sai."""
    await _ligar_envio(client, caixa)
    mid = await _ingerir(client, caixa)
    job = (await _responder(client, mid)).json()
    j = await db.get(MailOutbox, UUID(job["id"]))
    j.created_at = datetime.now(UTC) - timedelta(hours=3)
    await db.commit()
    assert [x["id"] for x in await _lease(client, caixa)] == [job["id"]]


async def test_recibo_que_chega_depois_da_conferencia_fica_registrado(caixa, client, db):
    from app.models.mail_atendimento import MailOutboxMeta

    _, lease = await _incerto(client, caixa, db)
    stored = await db.get(MailOutbox, UUID(lease["id"]))
    r = await client.post(f"{ROOT}/outbox/{lease['id']}/resolve", json={"saiu": False})
    assert r.status_code == 200, r.text
    r = await client.post(
        f"{caixa['agente']}/outbox/{lease['id']}/receipt",
        headers=caixa["headers"],
        json={"lease_token": lease["lease_token"], "status": "sent", "message_id": "<x@y>"},
    )
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "receipt_conflict")
    await db.refresh(stored)
    assert (stored.status, stored.error_code) == ("failed", "resolved_not_sent")  # não muda
    liga = await db.get(MailOutboxMeta, stored.id)
    await db.refresh(liga)
    assert liga.recibo_tardio == "sent" and liga.recibo_tardio_em is not None


# ─── 7. conferência pré-subida de 08/10: o principal e a caixa que volta a privada ───


async def _jobs_do_principal(client, caixa, db) -> list:
    """O lease nunca entrega nada pelo principal; e nada do principal fica na fila."""
    jobs = await _lease(client, caixa)
    assert not any(j["from_address"] == "principal@example.com" for j in jobs)
    vivos = (
        await db.scalars(select(MailOutbox).where(MailOutbox.status.in_(("queued", "leased"))))
    ).all()
    return [
        j for j in vivos if decrypt_json(j.content_enc)["from_address"] == "principal@example.com"
    ]


async def test_caixa_crua_da_empresa_nunca_responde_pelo_principal(
    caixa, client, db, todo_admin_mexe, freio_aberto
):
    await _ligar_envio(client, caixa)
    await _config(db, caixa, visibilidade="empresa", envio_modo="real")
    # Padrão da linha (estrito desligado), e-mail que chegou por lista/Cco: a
    # Central não cai mais no principal — não sugere remetente nenhum.
    mid = await _ingerir(client, caixa, to=["lista@fora.example"])
    detalhe = (await client.get(f"{ROOT}/messages/{mid}")).json()
    assert detalhe["reply"]["from_address"] is None
    r = await _responder(client, mid)
    assert r.json()["detail"]["code"] == "no_receiving_alias"
    # Escolher o principal na mão: recusado com o motivo.
    r = await _responder(client, mid, from_address="principal@example.com")
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "main_address_not_allowed")
    # E-mail que chegou SÓ no principal: nem ele responde.
    so_principal = await _ingerir(client, caixa, to=["principal@example.com"])
    detalhe = (await client.get(f"{ROOT}/messages/{so_principal}")).json()
    assert detalhe["reply"]["from_address"] is None
    assert detalhe["reply"]["receiving_aliases"] == []
    r = await _responder(client, so_principal, from_address="principal@example.com")
    assert r.json()["detail"]["code"] == "main_address_not_allowed"
    assert await _jobs_do_principal(client, caixa, db) == []
    # Um alias da caixa escolhido na mão continua valendo (a pessoa decide por qual).
    r = await _responder(client, mid, from_address="loja02@example.com")
    assert r.status_code == 202, r.text
    assert r.json()["from_address"] == "loja02@example.com"


async def test_caixa_crua_da_empresa_estrita_e_o_principal(
    caixa, client, db, todo_admin_mexe, freio_aberto
):
    await _ligar_envio(client, caixa)
    await _config(db, caixa, visibilidade="empresa", envio_modo="real", remetente_estrito=True)
    mid = await _ingerir(client, caixa, to=["principal@example.com", "loja03@example.com"])
    detalhe = (await client.get(f"{ROOT}/messages/{mid}")).json()
    # O principal recebeu também, mas a sugestão é o alias (e só ele "recebeu").
    assert detalhe["reply"]["from_address"] == "loja03@example.com"
    assert detalhe["reply"]["receiving_aliases"] == ["loja03@example.com"]
    r = await _responder(client, mid, from_address="principal@example.com")
    assert r.json()["detail"]["code"] == "main_address_not_allowed"
    assert await _jobs_do_principal(client, caixa, db) == []


async def test_caixa_privada_continua_caindo_no_principal(caixa, client):
    """A Goslin (privada): a regra da Central de sempre, nada muda."""
    mid = await _ingerir(client, caixa, to=["lista@fora.example"])
    detalhe = (await client.get(f"{ROOT}/messages/{mid}")).json()
    assert detalhe["reply"]["from_address"] == "principal@example.com"


async def test_lease_falha_o_job_do_principal_numa_caixa_empresa(
    caixa, client, db, todo_admin_mexe, freio_aberto
):
    # Enfileirado pelo principal quando a caixa era privada; depois virou empresa.
    await _ligar_envio(client, caixa)
    mid = await _ingerir(client, caixa, to=["lista@fora.example"])
    job = (await _responder(client, mid)).json()
    assert job["from_address"] == "principal@example.com"
    await _config(db, caixa, visibilidade="empresa", envio_modo="real")
    assert await _lease(client, caixa) == []
    stored = await db.get(MailOutbox, UUID(job["id"]))
    await db.refresh(stored)
    assert (stored.status, stored.error_code) == ("failed", "main_address_not_allowed")


async def test_job_segurado_da_empresa_nao_sai_quando_a_caixa_volta_a_privada(
    caixa, client, db, todo_admin_mexe, freio_aberto, monkeypatch
):
    """O freio segurou o job da empresa; a caixa volta para privada (direto no
    banco, sem a rota): o job continua segurado pela regra da empresa, que vale
    pelo que a caixa era quando ele NASCEU (`mail_outbox_meta.caixa_empresa`)."""
    from app.models.mail_atendimento import MailOutboxMeta

    await _ligar_envio(client, caixa)
    await _config(db, caixa, visibilidade="empresa", envio_modo="real")
    mid = await _ingerir(client, caixa)
    job = (await _responder(client, mid)).json()
    liga = await db.get(MailOutboxMeta, UUID(job["id"]))
    assert liga is not None and liga.caixa_empresa is True and liga.origem == "caixa"
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", False)
    assert await _lease(client, caixa) == []
    cfg = await db.get(MailMailboxSettings, UUID(caixa["id"]))
    cfg.visibilidade = "privada"
    await db.commit()
    assert await _lease(client, caixa) == []
    # A pausa da configuração também continua valendo para ele.
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    cfg.envio_pausado_ate = datetime.now(UTC) + timedelta(hours=1)
    await db.commit()
    assert await _lease(client, caixa) == []
    stored = await db.get(MailOutbox, UUID(job["id"]))
    await db.refresh(stored)
    assert stored.status == "queued"


async def test_mudar_de_empresa_para_privada_pela_tela_cancela_a_fila(
    caixa, client, db, todo_admin_mexe, freio_aberto, monkeypatch
):
    await _ligar_envio(client, caixa)
    await _config(db, caixa, visibilidade="empresa", envio_modo="real")
    mid = await _ingerir(client, caixa)
    job = (await _responder(client, mid)).json()
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", False)
    assert await _lease(client, caixa) == []
    r = await client.patch(
        f"{ROOT}/mailboxes/{caixa['id']}/settings", json={"visibilidade": "privada"}
    )
    assert r.status_code == 200, r.text
    stored = await db.get(MailOutbox, UUID(job["id"]))
    await db.refresh(stored)
    assert (stored.status, stored.error_code) == ("failed", "visibilidade_mudou")
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    assert await _lease(client, caixa) == []
    # A tela da Central diz por quê.
    detalhe = (await client.get(f"{ROOT}/messages/{mid}")).json()
    assert detalhe["outbox"][0]["error_code"] == "visibilidade_mudou"
