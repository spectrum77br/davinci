# ruff: noqa: F401, F811 — fixtures compartilhadas da Central e da ponte
"""Lixeira local, revogação e migrations da administração do e-mail (PostgreSQL local).

12/11/2026: a regra por pessoa × caixa × loja saiu (0476); a lista de usuários
nova tem os testes em `test_mail_usuarios.py`.

Nenhuma conta externa é consultada ou alterada pelos testes.
"""

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text

from app.models import Base, StoreInfo, UserRole
from app.models.mail import MailAttachment, MailMailbox, MailMessage, MailOutbox
from app.models.mail_admin import MailAdminEvento, MailLixeira
from app.models.mail_atendimento import MailCaixaIndice, MailMailboxSettings, MailMessageMeta
from app.schemas.mail import ReplyIn
from app.security.cipher import decrypt_json
from app.services import mail_central
from app.services.mail_atendimento import administracao as mail_admin
from tests.test_mail_central import account, claim, enable, ingest, queued
from tests.test_mail_ponte import _config, cena, entrar, meta_de, pedido_existe, rodar
from tests.test_mail_venda_rotas import X, Y, _caso

ADMIN = "/api/atendimento/email/admin"


async def test_lixeira_local_idempotente_restaura_sem_tocar_original_ponte_ou_tuta(
    db, cena, client, auth_as
):
    m = await _caso(db, cena, "12345678", X, corpo="Corpo privado não é auditado")
    source = await db.get(MailMessage, m.message_id)
    original, estado = bytes(source.content_enc), m.estado
    auth_as(cena.dono)
    url = f"/api/mail/messages/{source.id}"
    for _ in range(2):
        assert (
            await client.post(f"{url}/trash", json={"motivo": "Duplicado visual"})
        ).status_code == 200
    assert (await client.get(url)).status_code == 404
    lista = (await client.get(f"/api/mail/mailboxes/{cena.geral.id}/trash")).json()
    assert [i["id"] for i in lista["items"]] == [str(source.id)]
    assert (await db.get(MailLixeira, source.id)).motivo == "Duplicado visual"
    for _ in range(2):
        assert (await client.post(f"{url}/restore")).status_code == 200
    assert (await client.get(f"/api/mail/messages/{source.id}")).status_code == 200
    await db.refresh(source)
    await db.refresh(m)
    assert bytes(source.content_enc) == original and m.estado == estado
    eventos = (
        await db.scalars(select(MailAdminEvento).where(MailAdminEvento.acao.like("email_%")))
    ).all()
    assert [e.acao for e in eventos] == ["email_excluido", "email_restaurado"]
    assert all(set(e.dados) == {"message_id"} for e in eventos)
    assert (await client.get(f"/api/mail/mailboxes/{cena.geral.id}/trash")).json()["items"] == []


@pytest.mark.parametrize("estado", ["queued", "leased", "uncertain"])
async def test_excluir_recusa_envio_pendente_sem_cancelar(client, account, db, estado):
    mid, _, job = await queued(client, account)
    row = await db.get(MailOutbox, UUID(job["id"]))
    row.status = estado
    await db.commit()
    r = await client.post(f"/api/mail/messages/{mid}/trash", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "email_envio_pendente"
    assert await db.get(MailLixeira, UUID(mid)) is None
    await db.refresh(row)
    assert row.status == estado


async def test_desligar_usuario_impede_lease_ja_enfileirado(
    client, account, db, make_user, auth_as
):
    """12/11/2026: desmarcar a caixa / desligar o usuário na lista de usuários do
    e-mail também segura a resposta que ele já tinha posto na fila."""
    mid = await ingest(client, account)
    pessoa = await make_user(
        role=UserRole.USER,
        permissions={
            "atendimento_email_leitura": {"view": True},
            "atendimento_email_operacao": {"view": True, "edit": True},
        },
    )
    box = await db.get(MailMailbox, UUID(account["mailbox"]["id"]))
    db.add(MailMailboxSettings(mailbox_id=box.id, leitores=[str(pessoa.id)]))
    await db.commit()
    await enable(client, account)
    await db.refresh(box)  # heartbeat e habilitação ocorreram em outra sessão HTTP
    message = await db.get(MailMessage, UUID(mid))
    await mail_central.queue_reply(
        db, box, message, pessoa, ReplyIn(request_id=uuid4(), text="Resposta anterior")
    )
    await db.commit()
    auth_as(account["admin"])
    r = await client.put(f"{ADMIN}/usuarios/{pessoa.id}", json={"ligado": False, "caixas": []})
    assert r.status_code == 200, r.text
    assert await claim(client, account) == []
    job = await db.scalar(select(MailOutbox).where(MailOutbox.message_id == UUID(mid)))
    await db.refresh(job)
    assert job.status == "failed" and job.error_code == "author_access_revoked"


def test_migration_tem_uma_ponta_e_a_regra_antiga_saiu():
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    assert "0415_mail_admin" in {rev.revision for rev in scripts.walk_revisions()}
    assert (
        scripts.get_revision("0415_mail_admin").down_revision
        == "0414_vigia_conciliacao_chave_etapa"
    )
    assert scripts.get_revision("0476_mail_usuarios_pastas").down_revision == "0475_flex_ajuste_kits"


def _migration(nome):
    path = Path(__file__).parents[1] / f"alembic/versions/{nome}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{nome}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_migration_sobe_bate_colunas_modelo_e_desce(db):
    mod, sai = _migration("0415_mail_admin"), _migration("0476_mail_usuarios_pastas")
    schema = f"{Base.metadata.schema}_mig0415"
    mod.SCHEMA = sai.SCHEMA = schema
    await db.execute(text(f'CREATE SCHEMA "{schema}"'))
    for table in ("users", "mail_mailboxes", "mail_messages"):
        await db.execute(text(f'CREATE TABLE "{schema}".{table} (id uuid PRIMARY KEY)'))
    await db.commit()

    def rodar(conn, passo, m=None):
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(m or mod, passo)()

    async def tabelas():
        return set(
            (
                await db.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema=:schema AND table_name LIKE 'mail_%'"
                    ),
                    {"schema": schema},
                )
            ).scalars()
        )

    try:
        connection = await db.connection()
        await connection.run_sync(rodar, "upgrade")
        await db.commit()
        assert "mail_acessos" in await tabelas()
        connection = await db.connection()
        await connection.run_sync(rodar, "upgrade", sai)
        await db.commit()
        assert "mail_acessos" not in await tabelas()
        for table in ("mail_lixeira", "mail_admin_eventos", "mail_leitor_pastas"):
            q = text(
                "SELECT column_name,udt_name,is_nullable FROM information_schema.columns "
                "WHERE table_schema=:schema AND table_name=:table ORDER BY column_name"
            )
            actual = (await db.execute(q, {"schema": schema, "table": table})).all()
            expected = (await db.execute(q, {"schema": Base.metadata.schema, "table": table})).all()
            assert actual == expected
            q = text(
                "SELECT c.conname, pg_get_constraintdef(c.oid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
                "WHERE n.nspname=:schema AND t.relname=:table ORDER BY c.conname"
            )
            actual = (await db.execute(q, {"schema": schema, "table": table})).all()
            expected = (await db.execute(q, {"schema": Base.metadata.schema, "table": table})).all()
            assert [(name, value.replace(schema + ".", "")) for name, value in actual] == [
                (name, value.replace(Base.metadata.schema + ".", "")) for name, value in expected
            ]
        connection = await db.connection()
        await connection.run_sync(rodar, "downgrade", sai)
        await db.commit()
        assert "mail_acessos" in await tabelas()
        assert "mail_leitor_pastas" not in await tabelas()
        connection = await db.connection()
        await connection.run_sync(rodar, "downgrade")
        await db.commit()
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema=:schema "
                    "AND table_name LIKE 'mail_%' "
                    "AND table_name NOT IN ('mail_mailboxes','mail_messages')"
                ),
                {"schema": schema},
            )
            == 0
        )
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await db.commit()
