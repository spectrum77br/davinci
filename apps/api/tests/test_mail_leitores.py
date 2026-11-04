"""A Central de e-mail é exclusiva de administradores, inclusive via API.

Leitores antigos não recebem acesso HTTP à caixa, às mensagens ou aos anexos.
A configuração interna dos leitores e a sincronização autenticada do agente
continuam preservadas. A migration 0389 também continua reversível.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text

from app.config import get_settings
from app.models import Base, UserRole, UserStatus
from app.models.mail import MailMailbox, MailOutbox
from app.models.mail_atendimento import MailMailboxSettings
from tests.test_mail_central import ROOT, account, enable, ingest  # noqa: F401

QUEM_MEXE = "quem-mexe@davinci-test.com"


@pytest.fixture(autouse=True)
def _todo_admin_mexe(monkeypatch):
    # Vazio = todo admin mexe no /atendimento (`acesso.pode_mexer`).
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "")


async def _por_leitores(client, mailbox_id: str, ids: list) -> object:
    return await client.put(
        f"{ROOT}/mailboxes/{mailbox_id}/leitores", json={"leitores": [str(i) for i in ids]}
    )


def _somente_admin(response):
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "admin_only"


async def _cena(client, account, make_user, auth_as):  # noqa: F811
    """A caixa do admin com 1 e-mail (com anexo), o envio ligado e 1 resposta
    na fila; o israel (usuário comum, ativo) na lista de leitores."""
    message_id = await ingest(client, account)
    await enable(client, account)
    resposta = await client.post(
        f"{ROOT}/messages/{message_id}/reply",
        json={"request_id": str(uuid4()), "text": "Resposta do dono"},
    )
    assert resposta.status_code == 202, resposta.text
    detalhe = (await client.get(f"{ROOT}/messages/{message_id}")).json()
    israel = await make_user(role=UserRole.USER)
    r = await _por_leitores(client, account["mailbox"]["id"], [israel.id])
    assert r.status_code == 200, r.text
    return {
        "message_id": message_id,
        "attachment_id": detalhe["attachments"][0]["id"],
        "job_id": resposta.json()["id"],
        "israel": israel,
    }


async def test_leitor_nao_ve_a_caixa_as_mensagens_ou_o_anexo_mas_admin_ve(
    client,
    account,  # noqa: F811
    make_user,
    auth_as,
):
    cena = await _cena(client, account, make_user, auth_as)
    box = account["mailbox"]["id"]
    auth_as(cena["israel"])
    for caminho in (
        f"{ROOT}/mailboxes",
        f"{ROOT}/mailboxes/{box}/messages",
        f"{ROOT}/messages/{cena['message_id']}",
        f"{ROOT}/attachments/{cena['attachment_id']}",
    ):
        _somente_admin(await client.get(caminho))

    auth_as(account["admin"])
    itens = (await client.get(f"{ROOT}/mailboxes")).json()["items"]
    assert [i["id"] for i in itens] == [box]
    assert not itens[0].get("so_leitura", False)
    assert "agent_token" not in itens[0]
    lista = await client.get(f"{ROOT}/mailboxes/{box}/messages")
    assert lista.status_code == 200
    assert [i["id"] for i in lista.json()["items"]] == [cena["message_id"]]
    assert lista.headers["cache-control"] == "no-store"
    detalhe = await client.get(f"{ROOT}/messages/{cena['message_id']}")
    assert detalhe.status_code == 200
    assert detalhe.json()["subject"] == "Private customer subject"
    assert detalhe.headers["cache-control"] == "no-store"
    anexo = await client.get(f"{ROOT}/attachments/{cena['attachment_id']}")
    assert anexo.status_code == 200
    assert anexo.content == b"private attachment"
    # Os MESMOS cabeçalhos de segurança da Central.
    assert anexo.headers["content-type"] == "application/octet-stream"
    assert anexo.headers["content-disposition"] == "attachment; filename*=UTF-8''private.html"
    assert anexo.headers["x-content-type-options"] == "nosniff"
    assert anexo.headers["cache-control"] == "no-store"
    assert anexo.headers["content-security-policy"] == "sandbox; default-src 'none'"


async def test_leitor_nao_responde_resolve_configura_nem_troca_a_chave(
    client,
    account,  # noqa: F811
    make_user,
    auth_as,
    db,
):
    cena = await _cena(client, account, make_user, auth_as)
    box = account["mailbox"]["id"]
    antes = await db.scalar(select(func.count()).select_from(MailOutbox))
    auth_as(cena["israel"])
    r = await client.post(
        f"{ROOT}/messages/{cena['message_id']}/reply",
        json={"request_id": str(uuid4()), "text": "do leitor"},
    )
    _somente_admin(r)
    r = await client.post(f"{ROOT}/outbox/{cena['job_id']}/resolve", json={"saiu": True})
    _somente_admin(r)
    # As rotas de admin da Central e as nossas: o leitor nem passa do "admin".
    _somente_admin(await client.patch(f"{ROOT}/mailboxes/{box}", json={"label": "x"}))
    _somente_admin(await client.post(f"{ROOT}/mailboxes/{box}/token"))
    _somente_admin(
        await client.patch(f"{ROOT}/mailboxes/{box}/settings", json={"ponte_ligada": True})
    )
    _somente_admin(await client.get(f"{ROOT}/mailboxes/{box}/leitores"))
    _somente_admin(await _por_leitores(client, box, [cena["israel"].id]))
    _somente_admin(await client.get(f"{ROOT}/mailboxes/{box}/settings"))
    # Nada novo na fila, a chave do Mac continua valendo, a caixa igual.
    assert await db.scalar(select(func.count()).select_from(MailOutbox)) == antes
    sinal = await client.post(
        f"{account['agent']}/heartbeat",
        headers=account["headers"],
        json={"state": "online", "can_send": True},
    )
    assert sinal.status_code == 200
    mailbox = await db.get(MailMailbox, UUID(box))
    await db.refresh(mailbox)
    assert mailbox.label == "Private test inbox"


async def test_usuario_sem_permissao_ou_leitor_revogado_recebe_admin_only(
    client,
    account,  # noqa: F811
    make_user,
    auth_as,
    db,
):
    cena = await _cena(client, account, make_user, auth_as)
    box = account["mailbox"]["id"]
    caminhos = [
        "/mailboxes",
        f"/mailboxes/{box}/messages",
        f"/messages/{cena['message_id']}",
        f"/attachments/{cena['attachment_id']}",
    ]
    outro = await make_user(role=UserRole.USER)
    auth_as(outro)
    for caminho in caminhos:
        _somente_admin(await client.get(ROOT + caminho))
    # A revogação da lista não altera o bloqueio da Central por perfil.
    auth_as(account["admin"])
    r = await _por_leitores(client, box, [])
    assert r.status_code == 200 and r.json()["leitores"] == []
    auth_as(cena["israel"])
    for caminho in caminhos:
        _somente_admin(await client.get(ROOT + caminho))
    # Sem linha de configuração nenhuma: idem.
    await db.execute(text("DELETE FROM mail_mailbox_settings"))
    await db.commit()
    _somente_admin(await client.get(f"{ROOT}/mailboxes"))


async def test_leitor_de_uma_caixa_nao_ve_nenhuma_caixa_na_central(
    client,
    account,  # noqa: F811
    make_user,
    auth_as,
):
    cena = await _cena(client, account, make_user, auth_as)
    outra = await client.post(
        f"{ROOT}/mailboxes", json={"label": "Outra", "address": "outra@example.com"}
    )
    assert outra.status_code == 201
    auth_as(cena["israel"])
    _somente_admin(await client.get(f"{ROOT}/mailboxes"))
    for box in (account["mailbox"]["id"], outra.json()["id"]):
        _somente_admin(await client.get(f"{ROOT}/mailboxes/{box}/messages"))


async def test_usuario_inativo_na_lista_nao_ve_e_nao_entra(
    client,
    account,  # noqa: F811
    make_user,
    auth_as,
    db,
):
    cena = await _cena(client, account, make_user, auth_as)
    box = account["mailbox"]["id"]
    israel = cena["israel"]
    israel.status = UserStatus.SUSPENDED
    await db.commit()
    # auth_as pula require_active_user; o perfil não admin ainda bloqueia a Central.
    auth_as(israel)
    _somente_admin(await client.get(f"{ROOT}/mailboxes"))
    _somente_admin(await client.get(f"{ROOT}/mailboxes/{box}/messages"))
    _somente_admin(await client.get(f"{ROOT}/messages/{cena['message_id']}"))
    _somente_admin(await client.get(f"{ROOT}/attachments/{cena['attachment_id']}"))
    # E não entra na lista (nem quem não existe): nada muda.
    auth_as(account["admin"])
    for ids in ([israel.id], [uuid4()]):
        r = await _por_leitores(client, box, ids)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "leitor_inativo"
    visao = (await client.get(f"{ROOT}/mailboxes/{box}/leitores")).json()
    # Continua na lista guardada, marcado inativo (para o admin tirar).
    assert visao["leitores"] == [
        {"id": str(israel.id), "nome": israel.email.split("@")[0], "ativo": False}
    ]
    assert str(israel.id) not in {c["id"] for c in visao["candidatos"]}


async def test_quem_muda_a_lista_admin_que_mexe_mesmo_na_privada_de_outro(
    client,
    account,  # noqa: F811
    make_user,
    auth_as,
    db,
    monkeypatch,
):
    box = account["mailbox"]["id"]
    # A caixa é do "heisenberg" (outro admin): privada de outro dono.
    dono = await make_user(role=UserRole.ADMIN)
    mailbox = await db.get(MailMailbox, UUID(box))
    mailbox.owner_user_id = dono.id
    await db.commit()
    israel = await make_user(role=UserRole.USER)
    comum = await make_user(role=UserRole.USER)
    # Admin que NÃO mexe no /atendimento: não muda.
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", QUEM_MEXE)
    r = await _por_leitores(client, box, [israel.id])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "atendimento_permission_required"
    assert await db.get(MailMailboxSettings, UUID(box)) is None
    # Admin que mexe: muda, na caixa privada de outro dono também.
    quem_mexe = await make_user(role=UserRole.ADMIN, email=QUEM_MEXE)
    auth_as(quem_mexe)
    r = await _por_leitores(client, box, [israel.id, israel.id, dono.id, quem_mexe.id])
    assert r.status_code == 200, r.text
    visao = r.json()
    # Sem repetir; o dono e os admins saem (já veem a caixa).
    assert [p["id"] for p in visao["leitores"]] == [str(israel.id)]
    assert visao["atualizado_por"]["id"] == str(quem_mexe.id)
    assert visao["atualizado_em"]
    candidatos = {c["id"] for c in visao["candidatos"]}
    assert {str(israel.id), str(comum.id)} <= candidatos
    assert not {str(dono.id), str(quem_mexe.id), str(account["admin"].id)} & candidatos
    linha = await db.get(MailMailboxSettings, UUID(box))
    await db.refresh(linha)
    assert linha.leitores == [str(israel.id)]
    assert linha.leitores_updated_by == quem_mexe.id
    assert linha.updated_by == quem_mexe.id
    assert isinstance(linha.leitores_updated_at, datetime)
    # A configuração em si não mudou (a caixa segue privada, ponte desligada).
    assert (linha.visibilidade, linha.ponte_ligada) == ("privada", False)
    # Campo desconhecido = 422 (como a Central).
    r = await client.put(f"{ROOT}/mailboxes/{box}/leitores", json={"leitores": [], "x": 1})
    assert r.status_code == 422


# ─────────────── toda rota GET de uma caixa exige admin ───────────────

GETS_SO_ADMIN = {
    "/api/mail/mailboxes/{mailbox_id}/messages",
    "/api/mail/messages/{message_id}",
    "/api/mail/attachments/{attachment_id}",
    "/api/mail/mailboxes/{mailbox_id}/pastas",
    "/api/mail/mailboxes/{mailbox_id}/lista",
    "/api/mail/mailboxes/{mailbox_id}/settings",
    "/api/mail/mailboxes/{mailbox_id}/leitores",
    "/api/mail/mailboxes/{mailbox_id}/compose-context",
    "/api/mail/mailboxes/{mailbox_id}/drafts",
    "/api/mail/mailboxes/{mailbox_id}/outbox",
    "/api/mail/drafts/{draft_id}",
}


def _gets_de_caixa() -> set[str]:
    from app.main import app

    rotas = set()
    for rota in app.routes:
        caminho = getattr(rota, "path", "")
        if "GET" not in (getattr(rota, "methods", None) or set()):
            continue
        if not caminho.startswith("/api/mail/") or caminho.startswith("/api/mail/agent/"):
            continue
        if "{" in caminho:
            rotas.add(caminho)
    return rotas


def test_toda_rota_get_de_caixa_tem_cobertura_admin_only():
    sem_classe = _gets_de_caixa() - GETS_SO_ADMIN
    assert not sem_classe, (
        "Rota GET nova numa caixa/mensagem/anexo: inclua em GETS_SO_ADMIN "
        "para validar o bloqueio de leitores: " + ", ".join(sorted(sem_classe))
    )


async def test_leitor_nao_abre_nenhuma_rota_get_da_central(
    client,
    account,  # noqa: F811
    make_user,
    auth_as,
):
    cena = await _cena(client, account, make_user, auth_as)
    valores = {
        "mailbox_id": account["mailbox"]["id"],
        "message_id": cena["message_id"],
        "attachment_id": cena["attachment_id"],
        "draft_id": str(uuid4()),
    }
    existentes = _gets_de_caixa()
    auth_as(cena["israel"])
    for caminho in sorted(GETS_SO_ADMIN & existentes):
        r = await client.get(caminho.format(**valores))
        _somente_admin(r)


# ─────────────── a migration 0389 ───────────────

_VERSOES = Path(__file__).resolve().parent.parent / "alembic" / "versions"
REFERIDAS = [
    "users",
    "atendimento_conversas",
    "atendimento_mensagens",
    "store_info",
    "integrations",
    "marcas",
]


def _carregar(nome: str):
    caminho = _VERSOES / nome
    spec = importlib.util.spec_from_file_location(f"migration_{caminho.stem}", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_uma_ponta_e_depois_da_0387_ou_da_0388():
    config = Config()
    config.set_main_option("script_location", str(_VERSOES.parent))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    nossa = scripts.get_revision("0389_mail_leitores")
    # No merge com a outra frente, o down_revision vira a 0388.
    assert nossa.down_revision in {"0387_mail_atendimento", "0388_mail_caixa_indice"}


async def _colunas(db, schema: str) -> list:
    return [
        tuple(r)
        for r in (
            await db.execute(
                text(
                    "SELECT column_name, udt_name, is_nullable,"
                    " replace(column_default, :schema || '.', '')"
                    " FROM information_schema.columns WHERE table_schema = :schema"
                    " AND table_name = 'mail_mailbox_settings' ORDER BY column_name"
                ),
                {"schema": schema},
            )
        ).all()
    ]


async def _constraints(db, schema: str) -> list:
    return [
        (r[0], r[1].replace(f"{schema}.", ""))
        for r in (
            await db.execute(
                text(
                    "SELECT co.conname, pg_get_constraintdef(co.oid) FROM pg_constraint co"
                    " JOIN pg_class cl ON cl.oid = co.conrelid"
                    " JOIN pg_namespace n ON n.oid = cl.relnamespace"
                    " WHERE n.nspname = :schema AND cl.relname = 'mail_mailbox_settings'"
                    " ORDER BY co.conname"
                ),
                {"schema": schema},
            )
        ).all()
    ]


@pytest.mark.asyncio
async def test_migration_sobe_bate_com_o_model_e_desce(db):
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0389"
    dele = _carregar("0386_mail_central.py")
    base = _carregar("0387_mail_atendimento.py")
    nossa = _carregar("0389_mail_leitores.py")
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    for tabela in REFERIDAS:
        await db.execute(text(f'CREATE TABLE "{rascunho}".{tabela} (id uuid PRIMARY KEY)'))
    await db.commit()

    def _rodar(conn, passos) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            for mod, passo in passos:
                getattr(mod, passo)()

    for mod in (dele, base, nossa):
        mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, [(dele, "upgrade"), (base, "upgrade")])
        await db.commit()
        antes = (await _colunas(db, rascunho), await _constraints(db, rascunho))
        # Uma linha que já existia antes da 0389 ganha a lista vazia.
        await db.execute(text(f"INSERT INTO \"{rascunho}\".users (id) VALUES ('{uuid4()}')"))
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".mail_mailboxes (id, owner_user_id, label, config_enc,'
                " agent_token_hash, send_enabled, agent_can_send, state)"
                f" SELECT gen_random_uuid(), id, 'x', '\\x00', repeat('0', 64), false, false,"
                f" 'offline' FROM \"{rascunho}\".users"
            )
        )
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".mail_mailbox_settings (mailbox_id)'
                f' SELECT id FROM "{rascunho}".mail_mailboxes'
            )
        )
        await db.commit()

        conn = await db.connection()
        await conn.run_sync(_rodar, [(nossa, "upgrade")])
        await db.commit()
        assert await _colunas(db, rascunho) == await _colunas(db, schema_model)
        assert await _constraints(db, rascunho) == await _constraints(db, schema_model)
        nomes = dict(await _constraints(db, rascunho))
        assert "ON DELETE SET NULL" in nomes["fk_mail_mailbox_settings_leitores_updated_by_users"]
        assert "ck_mail_mailbox_settings_leitores_lista" in nomes
        linha = (
            await db.execute(
                text(
                    "SELECT leitores::text, leitores_updated_by, leitores_updated_at"
                    f' FROM "{rascunho}".mail_mailbox_settings'
                )
            )
        ).one()
        assert tuple(linha) == ("[]", None, None)
        with pytest.raises(Exception, match="ck_mail_mailbox_settings_leitores_lista"):
            await db.execute(
                text(f"UPDATE \"{rascunho}\".mail_mailbox_settings SET leitores = '{{}}'::jsonb")
            )
        await db.rollback()

        conn = await db.connection()
        await conn.run_sync(_rodar, [(nossa, "downgrade")])
        await db.commit()
        assert (await _colunas(db, rascunho), await _constraints(db, rascunho)) == antes
        assert (
            await db.scalar(text(f'SELECT count(*) FROM "{rascunho}".mail_mailbox_settings')) == 1
        )
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
