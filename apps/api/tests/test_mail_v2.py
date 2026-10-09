"""O contrato v2 do agente do Mac (o NOSSO conector do Tuta) — 08/10/2026.

O v1 da Central é CONGELADO: o teste "golden" roda a mesma sequência v1 numa
caixa só v1 e noutra com o v2 no meio, e as respostas do v1 saem byte a byte
iguais (e iguais às gravadas aqui). Depois, o que o v2 acrescenta:

  • /sync — pastas (a regra decide o que se lê com corpo; renomeada é
    reclassificada; a que some sai da lista), os aliases ATIVOS da conta, os
    contadores (o teto da conta na hora de enfileirar) e o "dois agentes";
  • /ingest — cada e-mail sozinho (um ruim não derruba os outros), só de
    pasta lida com corpo, e o bloco do Tuta guardado cifrado (a ponte lê);
  • /count — o que falta, o que mudou de pasta (aplicado, o conteúdo
    cifrado mostra a pasta nova), o que saiu, a conciliação do dia;
  • /changes — movido e apagado;
  • a mesma chave por caixa do v1 (a de outra caixa não serve).

Postgres local de verdade + HTTP em processo; nunca conta do Tuta nem envio.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import UserRole
from app.models.mail import MailMessage
from app.models.mail_atendimento import (
    MailAgenteV2,
    MailFolder,
    MailMailboxSettings,
    MailMessageMeta,
    MailMessageTuta,
    MailReconciliation,
)
from app.security.cipher import decrypt_json
from app.services import mail_v2
from app.services.atendimento import acesso
from app.services.mail_atendimento import codigos, pastas, ponte

ROOT = "/api/mail"
ALIASES = ["21max@tuta.com", "16tr@tuta.com", "adm@poofy.com.br", "sac@uranyx.com.br"]

# As pastas da conta como o conector manda (chaves = ids do MailSet).
PASTAS = [
    {"key": "Pentrada", "name": "Entrada", "path": "Entrada", "kind": 1},
    {"key": "Penviados", "name": "Enviados", "path": "Enviados", "kind": 2},
    {"key": "Plixeira", "name": "Lixeira", "path": "Lixeira", "kind": 3},
    {"key": "Pspam", "name": "Spam", "path": "Spam", "kind": 5},
    {"key": "Pproblema", "name": "problema ml", "path": "problema ml", "kind": 0},
    {"key": "Pvendas", "name": "vendas shopee", "path": "vendas shopee", "kind": 0},
    {"key": "Pfinanceiro", "name": "financeiro", "path": "financeiro", "kind": 0},
    {"key": "Psac", "name": "*uranyx sac", "path": "*uranyx sac", "kind": 0},
    {"key": "Protulo", "name": "importante", "path": "importante", "kind": 8},
    {"key": "Pnova", "name": "coisas", "path": "coisas", "kind": 0},
]
INSTANCIA = "instancia-do-mac-mini-1"


async def _caixa(client, make_user, auth_as, rotulo: str, principal: str) -> dict:
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        f"{ROOT}/mailboxes", json={"label": rotulo, "address": principal, "aliases": ALIASES}
    )
    assert r.status_code == 201, r.text
    box = r.json()
    return {
        "admin": admin,
        "id": box["id"],
        "agente": f"{ROOT}/agent/{box['id']}",
        "v2": f"{ROOT}/agent/{box['id']}/v2",
        "headers": {"Authorization": f"Bearer {box['agent_token']}"},
    }


@pytest.fixture
async def caixa(client, make_user, auth_as):
    return await _caixa(client, make_user, auth_as, "Geral — Tuta", "geral@tuta.com")


def _v1(**mudancas) -> dict:
    base = {
        "source_id": f"imap:{uuid4().int % 10**9}",
        "folder": "INBOX",
        "direction": "inbound",
        "received_at": "2026-10-08T12:00:00+00:00",
        "subject": "Pedido",
        "from_address": "cliente@example.com",
        "from_name": "Cliente",
        "to": ["21max@tuta.com"],
        "cc": [],
        "reply_to": None,
        "message_id": f"<{uuid4().hex}@example.com>",
        "in_reply_to": None,
        "references": [],
        "text": "Quando chega?",
        "attachments": [],
    }
    return {**base, **mudancas}


def _v2(pasta: str = "Pproblema", caminho: str = "problema ml", kind: str = "0", **mudancas):
    lista, elemento = uuid4().hex[:12], uuid4().hex[:12]
    tuta = {
        "mail_id": f"{lista}/{elemento}",
        "folder_key": pasta,
        "folder_kind": kind,
        "folder_path": caminho,
        "conversation_id": "Fio" + uuid4().hex[:8],
        "state": 2,
        "unread": True,
        "replied": 0,
        "phishing_status": "0",
        "auth_status": "0",
        "labels": [],
        "codes_masked": False,
        "links_removed": 0,
    }
    tuta.update(mudancas.pop("tuta", {}))
    base = _v1(
        source_id=f"tuta:{tuta['mail_id']}",
        folder=caminho,
        received_at=datetime.now(UTC).isoformat(),
    )
    base.update(
        tuta=tuta,
        delivered_to=["21max@tuta.com"],
        text_from_html=True,
        omitted_attachments=[],
    )
    base.update(mudancas)
    return base


async def _sync(client, caixa, **mudancas):
    corpo = {
        "instance": INSTANCIA,
        "agent_version": "tuta-conector/0.2.0 sdk/361.260929.0",
        "tuta_version": "361.260929.0",
        "counters": {"lidos": 3},
        "folders": PASTAS,
        "aliases": ["geral@tuta.com", *ALIASES],
    }
    corpo.update(mudancas)
    return await client.post(f"{caixa['v2']}/sync", headers=caixa["headers"], json=corpo)


async def _ingerir(client, caixa, *itens):
    r = await client.post(
        f"{caixa['v2']}/ingest", headers=caixa["headers"], json={"messages": list(itens)}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _mensagem(db, caixa, source_id: str) -> MailMessage:
    m = await db.scalar(
        select(MailMessage).where(
            MailMessage.mailbox_id == UUID(caixa["id"]), MailMessage.source_id == source_id
        )
    )
    assert m is not None
    await db.refresh(m)
    return m


# ─── O v1 não muda (golden) ─────────────────────────────────────────────────

_UUID = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TOKEN = re.compile(rb'"lease_token":"[^"]+"')
_MSGID = re.compile(rb"<[0-9a-f-]{36}@mail\.davinci\.local>")


def _normalizar(corpo: bytes) -> bytes:
    corpo = _TOKEN.sub(b'"lease_token":"T"', corpo)
    corpo = _MSGID.sub(b"<ID@mail.davinci.local>", corpo)
    return _UUID.sub(b"U", corpo)


async def _sequencia_v1(client, caixa, *, com_v2: bool) -> list[tuple[int, bytes]]:
    """A mesma conversa do agente v1, do sinal ao recibo (com o v2 no meio ou não)."""
    saida: list[tuple[int, bytes]] = []

    async def v1(caminho: str, corpo, headers=None) -> dict | None:
        r = await client.post(
            f"{caixa['agente']}{caminho}",
            headers=caixa["headers"] if headers is None else headers,
            content=corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode(),
        )
        saida.append((r.status_code, _normalizar(r.content)))
        return (
            r.json() if r.headers.get("content-type", "").startswith("application/json") else None
        )

    if com_v2:
        assert (await _sync(client, caixa)).status_code == 200
    await v1("/heartbeat", {"state": "online", "can_send": True})
    fixa = _v1(source_id="imap:39", message_id="<fixo@example.com>")
    await v1("/ingest", {"messages": [fixa]})
    if com_v2:
        await _ingerir(client, caixa, _v2())
    await v1("/ingest", {"messages": [fixa]})
    await v1("/ingest", {"messages": [{**fixa, "html": "<b>x</b>"}]})
    await v1("/ingest", {"messages": [{**fixa, "to": ["sem-arroba"]}]})
    await v1("/heartbeat", {"state": "online", "can_send": True}, headers={})
    await v1(
        "/heartbeat", {"state": "online", "can_send": True}, headers={"Authorization": "Bearer x"}
    )
    await v1("/outbox/lease", {})
    # Uma resposta de pessoa → lease → recibo (e o recibo repetido).
    r = await client.patch(f"{ROOT}/mailboxes/{caixa['id']}", json={"send_enabled": True})
    assert r.status_code == 200
    await v1("/heartbeat", {"state": "online", "can_send": True})
    lista = (await client.get(f"{ROOT}/mailboxes/{caixa['id']}/messages?limit=100")).json()
    original = next(
        i for i in lista["items"] if i["subject"] == "Pedido" and i["folder"] == "INBOX"
    )
    r = await client.post(
        f"{ROOT}/messages/{original['id']}/reply",
        json={"request_id": str(uuid4()), "text": "Chega amanhã."},
    )
    assert r.status_code == 202, r.text
    if com_v2:
        r = await client.post(
            f"{caixa['v2']}/count",
            headers=caixa["headers"],
            json={"folder_key": "Pproblema", "ids": [], "complete": False},
        )
        assert r.status_code == 200
    lease = await v1("/outbox/lease", {})
    job = lease["jobs"][0]
    recibo = {"lease_token": job["lease_token"], "status": "sent", "message_id": "<x@tuta.com>"}
    await v1(f"/outbox/{job['id']}/receipt", recibo)
    await v1(f"/outbox/{job['id']}/receipt", recibo)
    await v1(f"/outbox/{job['id']}/receipt", {**recibo, "status": "failed"})
    await v1("/outbox/lease", b"")
    return saida


async def test_v1_responde_byte_a_byte_igual_com_o_v2_no_meio(client, make_user, auth_as):
    so_v1 = await _caixa(client, make_user, auth_as, "Só v1", "um@tuta.com")
    so_v1_saida = await _sequencia_v1(client, so_v1, com_v2=False)
    com_v2 = await _caixa(client, make_user, auth_as, "Com v2", "dois@tuta.com")
    com_v2_saida = await _sequencia_v1(client, com_v2, com_v2=True)
    assert so_v1_saida == com_v2_saida
    # E igual ao gravado (o formato de hoje da Central, 933e44f4).
    assert so_v1_saida == [
        (200, b'{"ok":true,"send_enabled":false}'),
        (200, b'{"accepted":1,"duplicates":0}'),
        (200, b'{"accepted":0,"duplicates":1}'),
        (
            422,
            b'{"detail":{"code":"invalid_body","fields":[{"field":"messages.0.html",'
            b'"type":"extra_forbidden"}]}}',
        ),
        (
            422,
            b'{"detail":{"code":"invalid_body","fields":[{"field":"messages.0.to.0",'
            b'"type":"value_error"}]}}',
        ),
        (401, b'{"detail":{"code":"agent_unauthorized"}}'),
        (401, b'{"detail":{"code":"agent_unauthorized"}}'),
        (200, b'{"jobs":[]}'),
        (200, b'{"ok":true,"send_enabled":true}'),
        (
            200,
            b'{"jobs":[{"id":"U","lease_token":"T","from_address":"21max@tuta.com",'
            b'"in_reply_to":"<fixo@example.com>","message_id":"<ID@mail.davinci.local>",'
            b'"references":["<fixo@example.com>"],"subject":"Re: Pedido","text":"'
            + "Chega amanhã.".encode()
            + b'","to":"cliente@example.com"}]}',
        ),
        (200, b'{"ok":true,"status":"sent"}'),
        (200, b'{"ok":true,"status":"sent"}'),
        (409, b'{"detail":{"code":"receipt_conflict"}}'),
        (200, b'{"jobs":[]}'),
    ]


# ─── /sync ──────────────────────────────────────────────────────────────────


async def test_sync_grava_pastas_e_diz_o_que_ler(client, caixa, db):
    r = await _sync(client, caixa)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["contract"] == 2
    leitura = {f["key"]: f["read"] for f in corpo["folders"]}
    assert leitura == {
        "Pentrada": "corpo",
        "Penviados": "corpo",
        "Plixeira": "so_contar",
        "Pspam": "so_contar",
        "Pproblema": "corpo",
        "Pvendas": "corpo",
        # Sem plataforma (decisão padrão: só contar até o dono liberar).
        "Pfinanceiro": "so_contar",
        "Psac": "corpo",
        "Protulo": "so_contar",
        # Pasta pessoal sem palavra de plataforma: só contada até alguém revisar.
        "Pnova": "so_contar",
    }
    # O endereço interno sem loja só se conta (o corpo nem sobe).
    assert corpo["count_only_aliases"] == ["adm@poofy.com.br"]
    agente = await db.get(MailAgenteV2, UUID(caixa["id"]))
    assert agente.instancia == INSTANCIA
    assert agente.contadores == {"lidos": 3}
    # Caixa PRIVADA (sem configuração): só os aliases de loja e os "só contar"
    # ficam em claro no banco — os outros endereços da conta, nunca.
    assert agente.aliases_conta == ["adm@poofy.com.br"]
    db.add(MailMailboxSettings(mailbox_id=UUID(caixa["id"]), visibilidade="empresa"))
    await db.commit()
    assert (await _sync(client, caixa)).status_code == 200
    await db.refresh(agente)
    assert "21max@tuta.com" in agente.aliases_conta and "geral@tuta.com" in agente.aliases_conta
    # Renomear reclassifica (a chave não muda) e a pasta que some sai da lista.
    novas = [p for p in PASTAS if p["key"] != "Pspam"]
    novas = [
        {**p, "name": "reclamação ml", "path": "reclamação ml"} if p["key"] == "Pnova" else p
        for p in novas
    ]
    corpo = (await _sync(client, caixa, folders=novas)).json()
    leitura = {f["key"]: f["read"] for f in corpo["folders"]}
    assert leitura["Pnova"] == "corpo"
    assert "Pspam" not in leitura
    spam = await db.scalar(select(MailFolder).where(MailFolder.chave == "Pspam"))
    await db.refresh(spam)
    assert spam.sumiu_em is not None
    # Lista PARCIAL (uma pasta não decifrou no Mac): ninguém some por isso.
    sem_vendas = [p for p in novas if p["key"] != "Pvendas"]
    corpo = (await _sync(client, caixa, folders=sem_vendas, folders_complete=False)).json()
    assert "Pvendas" in {f["key"] for f in corpo["folders"]}
    # Uma pessoa mandou ignorar: não se lê nem conta.
    pasta = await db.scalar(select(MailFolder).where(MailFolder.chave == "Pvendas"))
    pasta.ignorar = True
    pasta.ler = "nao"
    await db.commit()
    corpo = (await _sync(client, caixa, folders=None, aliases=None)).json()
    assert {f["key"]: f["read"] for f in corpo["folders"]}["Pvendas"] == "nao"


async def test_sync_recusa_dois_agentes_na_mesma_caixa(client, caixa, db):
    assert (await _sync(client, caixa)).status_code == 200
    # O mesmo Mac reiniciando manda a MESMA instância: não é alarme.
    assert (await _sync(client, caixa)).status_code == 200
    outro = await _sync(client, caixa, instance="outra-instancia-de-mac")
    assert outro.status_code == 409
    assert outro.json()["detail"]["code"] == "another_agent_active"
    agente = await db.get(MailAgenteV2, UUID(caixa["id"]))
    await db.refresh(agente)
    assert agente.instancia == INSTANCIA
    assert agente.dois_agentes_em is not None
    # Depois da janela de 3 min sem sinal da primeira, a outra assume.
    agente.visto_em = datetime.now(UTC) - timedelta(minutes=4)
    await db.commit()
    assert (await _sync(client, caixa, instance="outra-instancia-de-mac")).status_code == 200


async def test_sync_confere_o_corpo(client, caixa):
    ruins = [
        {"counters": {"Lidos!": 1}},
        {"counters": {"lidos": -1}},
        {"instance": "curta"},
        {"folders": [{"key": "a/b", "kind": 0}]},
        {"segredo": "VALOR-SECRETO-123"},
        {"instance": "VALOR-SECRETO-123/!"},
    ]
    for mudanca in ruins:
        r = await _sync(client, caixa, **mudanca)
        assert r.status_code == 422, mudanca
        assert "SECRETO" not in r.text


async def test_chave_de_outra_caixa_nao_serve_no_v2(client, make_user, auth_as, caixa):
    outra = await _caixa(client, make_user, auth_as, "Outra", "outra@tuta.com")
    for rota in ("sync", "ingest", "count", "changes"):
        r = await client.post(f"{caixa['v2']}/{rota}", headers=outra["headers"], json={})
        assert r.status_code == 401, rota
        assert r.json() == {"detail": {"code": "agent_unauthorized"}}
        r = await client.post(f"{caixa['v2']}/{rota}", json={})
        assert r.status_code == 401


# ─── /ingest ────────────────────────────────────────────────────────────────


async def test_ingest_um_por_um_e_so_de_pasta_lida(client, caixa, db):
    await _sync(client, caixa)
    bom = _v2(tuta={"phishing_status": "1", "envelope_sender": "bounce@ses.example.com"})
    repetido = dict(bom)
    invalido = _v2(to=["sem-arroba"], text="segredo do cliente")
    html = _v2(html="<b>x</b>")
    outro_id = _v2(source_id="tuta:aaa/bbb")
    financeiro = _v2(pasta="Pfinanceiro", caminho="financeiro")
    desconhecida = _v2(pasta="Pnaoexiste", caminho="x")
    r = await _ingerir(client, caixa, bom, invalido, html, outro_id, financeiro, desconhecida)
    assert (r["accepted"], r["duplicates"], r["rejected"]) == (1, 0, 5)
    status = [(x["status"], x.get("code")) for x in r["results"]]
    assert status == [
        ("accepted", None),
        ("rejected", "invalid_message"),
        ("rejected", "invalid_message"),
        ("rejected", "invalid_message"),
        ("rejected", "folder_not_read"),
        ("rejected", "folder_unknown"),
    ]
    # O 422 por e-mail diz o campo, nunca o valor.
    assert r["results"][1]["fields"] == [{"field": "to.0", "type": "value_error"}]
    assert "segredo" not in json.dumps(r)
    assert r["results"][2]["fields"][0]["field"] == "html"
    r = await _ingerir(client, caixa, repetido)
    assert r["results"] == [{"source_id": bom["source_id"], "status": "duplicate"}]
    # Guardado como o v1 guarda (cifrado), com o bloco do Tuta para a ponte.
    m = await _mensagem(db, caixa, bom["source_id"])
    conteudo = decrypt_json(m.content_enc)
    assert conteudo["tuta"]["folder_key"] == "Pproblema"
    assert conteudo["tuta"]["phishing_status"] == "1"
    assert conteudo["delivered_to"] == ["21max@tuta.com"]
    assert "source_id" not in conteudo
    assert pastas.do_conteudo(conteudo).chave == "Pproblema"
    assert ponte.tuta_id_de(m, conteudo["tuta"]) == bom["tuta"]["mail_id"]
    local = await db.get(MailMessageTuta, m.id)
    assert local.folder_key == "Pproblema"
    # Só o bom entrou na caixa.
    total = await db.scalar(
        select(MailMessage.id).where(MailMessage.mailbox_id == UUID(caixa["id"])).limit(2)
    )
    assert total == m.id


async def test_ingest_com_lote_grande_demais_ou_corpo_ruim(client, caixa):
    await _sync(client, caixa)
    r = await client.post(
        f"{caixa['v2']}/ingest", headers=caixa["headers"], json={"messages": [_v2()] * 21}
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_body"
    r = await client.post(
        f"{caixa['v2']}/ingest", headers=caixa["headers"], json={"messages": [_v2()], "x": 1}
    )
    assert r.status_code == 422


async def test_a_ponte_le_o_que_o_v2_mandou(client, caixa, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "")
    await _sync(client, caixa)
    item = _v2(
        subject="Problema com o pedido",
        text="Recebi quebrado.",
        tuta={"phishing_status": "1", "conversation_id": "FioDoTuta1"},
    )
    await _ingerir(client, caixa, item)
    db.add(
        MailMailboxSettings(
            mailbox_id=UUID(caixa["id"]),
            visibilidade="empresa",
            ponte_ligada=True,
            ponte_desde=datetime.now(UTC) - timedelta(hours=1),
            ponte_so_aliases_de_loja=False,
        )
    )
    await db.commit()
    m = await _mensagem(db, caixa, item["source_id"])
    await ponte.processar(db, m.id)
    await db.commit()
    meta = await db.get(MailMessageMeta, m.id)
    assert meta.tuta_id == item["tuta"]["mail_id"]
    assert meta.fio_tuta == "FioDoTuta1"
    assert meta.phishing_status == "1"
    assert meta.suspeito is True
    pasta = await db.get(MailFolder, meta.folder_id)
    assert pasta.chave == "Pproblema"


# ─── /count e /changes ──────────────────────────────────────────────────────


async def test_count_falta_movido_saiu_e_conciliacao(client, caixa, db):
    await _sync(client, caixa)
    a, b, c = _v2(), _v2(), _v2()
    await _ingerir(client, caixa, a, b, c)
    nao_entrou = "tuta:naoentrou/ainda1"
    desde = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    # Na pasta problema: `a` e um que a Central não tem. `b` e `c` saíram dela.
    r = await client.post(
        f"{caixa['v2']}/count",
        headers=caixa["headers"],
        json={
            "folder_key": "Pproblema",
            "ids": [a["source_id"], nao_entrou],
            "complete": True,
            "since": desde,
            "day": "2026-10-07",
            "total": 2,
        },
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["missing"] == [nao_entrou]
    assert corpo["moved"] == 0
    assert sorted(corpo["left"]) == sorted([b["source_id"], c["source_id"]])
    linha = await db.scalar(select(MailReconciliation))
    assert (linha.dia, linha.no_tuta, linha.gravados, linha.faltando, linha.a_mais, linha.ok) == (
        date(2026, 10, 7),
        2,
        1,
        1,
        2,
        False,
    )
    # `b` está na Lixeira (pasta só contada): os ids vêm, o corpo nunca.
    r = await client.post(
        f"{caixa['v2']}/count",
        headers=caixa["headers"],
        json={"folder_key": "Plixeira", "ids": [b["source_id"], "tuta:x/y"], "complete": False},
    )
    corpo = r.json()
    assert corpo == {"missing": [], "moved": 1, "left": []}
    mb = await _mensagem(db, caixa, b["source_id"])
    local = await db.get(MailMessageTuta, mb.id)
    await db.refresh(local)
    assert (local.folder_key, local.apagado_em is not None) == ("Plixeira", True)
    conteudo = decrypt_json(mb.content_enc)
    assert conteudo["folder"] == "Lixeira"
    assert conteudo["tuta"]["folder_key"] == "Plixeira"
    assert conteudo["tuta"]["deleted"] is True
    # Pasta que o /sync não trouxe: o conector sincroniza antes.
    r = await client.post(
        f"{caixa['v2']}/count",
        headers=caixa["headers"],
        json={"folder_key": "Pnaoexiste", "ids": []},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "folder_unknown"


async def test_changes_move_e_apaga(client, caixa, db):
    await _sync(client, caixa)
    a, b, c = _v2(), _v2(), _v2()
    await _ingerir(client, caixa, a, b, c)
    ma = await _mensagem(db, caixa, a["source_id"])
    mc = await _mensagem(db, caixa, c["source_id"])
    # A meta da ponte (o e-mail passou no filtro) acompanha a pasta nova.
    problema = await db.scalar(select(MailFolder).where(MailFolder.chave == "Pproblema"))
    db.add(MailMessageMeta(message_id=ma.id, mailbox_id=ma.mailbox_id, folder_id=problema.id))
    # A do e-mail PRIVADO não tem nada em claro — e continua sem a pasta.
    db.add(MailMessageMeta(message_id=mc.id, mailbox_id=mc.mailbox_id, estado="privado"))
    await db.commit()
    r = await client.post(
        f"{caixa['v2']}/changes",
        headers=caixa["headers"],
        json={
            "changes": [
                {"source_id": a["source_id"], "folder_key": "Pvendas"},
                {"source_id": b["source_id"], "deleted": True},
                {"source_id": c["source_id"], "folder_key": "Pvendas"},
                {"source_id": "tuta:nao/existe"},
                {"source_id": a["source_id"], "folder_key": "Pnaoexiste"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 3, "unknown": 2}
    meta_privada = await db.get(MailMessageMeta, mc.id)
    await db.refresh(meta_privada)
    assert meta_privada.folder_id is None, "o e-mail privado não ganha pasta em claro"
    ma = await _mensagem(db, caixa, a["source_id"])
    assert decrypt_json(ma.content_enc)["folder"] == "vendas shopee"
    meta = await db.get(MailMessageMeta, ma.id)
    await db.refresh(meta)
    vendas = await db.scalar(select(MailFolder).where(MailFolder.chave == "Pvendas"))
    assert meta.folder_id == vendas.id
    mb = await _mensagem(db, caixa, b["source_id"])
    local = await db.get(MailMessageTuta, mb.id)
    await db.refresh(local)
    assert local.apagado_em is not None
    assert decrypt_json(mb.content_enc)["tuta"]["deleted"] is True


# ─── o teto da conta e a privacidade das pastas ────────────────────────────


async def test_contadores_do_v2_valem_no_teto_da_conta(client, caixa, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "")
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    await _sync(client, caixa)
    item = _v1(source_id="imap:777", message_id="<teto@example.com>")
    r = await client.post(
        f"{caixa['agente']}/ingest", headers=caixa["headers"], json={"messages": [item]}
    )
    assert r.status_code == 200
    db.add(
        MailMailboxSettings(mailbox_id=UUID(caixa["id"]), visibilidade="empresa", envio_modo="real")
    )
    await db.commit()
    assert (
        await client.patch(f"{ROOT}/mailboxes/{caixa['id']}", json={"send_enabled": True})
    ).status_code == 200
    await client.post(
        f"{caixa['agente']}/heartbeat",
        headers=caixa["headers"],
        json={"state": "online", "can_send": True},
    )
    await _sync(client, caixa, counters={"enviados_conta_hora": 100}, folders=None, aliases=None)
    lista = (await client.get(f"{ROOT}/mailboxes/{caixa['id']}/messages")).json()
    mid = lista["items"][0]["id"]
    r = await client.post(
        f"{ROOT}/messages/{mid}/reply", json={"request_id": str(uuid4()), "text": "Oi"}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "account_hourly_limit"
    await _sync(client, caixa, counters={"enviados_conta_hora": 99}, folders=None, aliases=None)
    r = await client.post(
        f"{ROOT}/messages/{mid}/reply", json={"request_id": str(uuid4()), "text": "Oi"}
    )
    assert r.status_code == 202, r.text
    # Contador velho (o conector parou há mais de 10 min) não trava nem libera.
    agente = await db.get(MailAgenteV2, UUID(caixa["id"]))
    agente.visto_em = datetime.now(UTC) - timedelta(minutes=11)
    agente.contadores = {"enviados_conta_hora": 500}
    await db.commit()
    assert await mail_v2.contadores_do_agente(db, UUID(caixa["id"])) is None


async def test_pastas_da_caixa_privada_so_para_quem_abre_a_caixa(
    client, make_user, auth_as, caixa, monkeypatch
):
    await _sync(client, caixa)
    # Alguém que MEXE mas não é dono nem admin (hoje mexe = admin; a trava
    # vale para quando um dia não for).
    monkeypatch.setattr(acesso, "pode_mexer", lambda user: True)
    pessoa = await make_user(role=UserRole.USER)
    auth_as(pessoa)
    r = await client.get("/api/atendimento/email/pastas")
    assert r.status_code == 200, r.text
    assert r.json()["itens"] == []
    auth_as(caixa["admin"])
    r = await client.get("/api/atendimento/email/pastas")
    assert {p["chave"] for p in r.json()["itens"]} >= {"Pproblema", "Pfinanceiro"}


# ─── o código que o conector já mascarou ainda é "de segurança" ────────────


def test_codigo_mascarado_no_mac_continua_de_seguranca():
    assert codigos.e_de_seguranca("Seu código", "Use o código •••••• para entrar na sua conta.")
    assert codigos.e_de_seguranca("Login", "seu codigo: ••• ••• (login)")
    # De pessoa: só o código de acesso de verdade.
    assert codigos.e_de_seguranca("Ajuda", "meu código de verificação é ••••••", de_pessoa=True)
    assert not codigos.e_de_seguranca("Pedido", "Seu pedido ••••• chegou", de_pessoa=True)
    assert not codigos.e_de_seguranca("Oi", "pontinhos •• soltos")
