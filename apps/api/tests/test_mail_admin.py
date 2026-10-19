# ruff: noqa: F401, F811 — fixtures compartilhadas da Central e da ponte
"""Administração real em PostgreSQL local: escopo em todas as portas da Central.

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
from app.models.mail_admin import MailAcesso, MailAdminEvento, MailLixeira
from app.models.mail_atendimento import MailCaixaIndice, MailMailboxSettings, MailMessageMeta
from app.security.cipher import decrypt_json
from tests.test_mail_central import account, claim, enable, ingest, queued
from tests.test_mail_ponte import _config, cena, entrar, meta_de, pedido_existe, rodar
from tests.test_mail_venda_rotas import X, Y, _caso

ADMIN = "/api/atendimento/email/admin"


def _regra(**extra):
    return {
        "modo": "padrao",
        "lojas_permitidas": [],
        "lojas_bloqueadas": [],
        "tipos_permitidos": ["informativo", "chamado", "venda"],
        "pedidos_permitidos": [],
        "expected_updated_at": None,
        **extra,
    }


async def _salvar(client, cena, pessoa, **extra):
    r = await client.put(f"{ADMIN}/acessos/{cena.geral.id}/{pessoa.id}", json=_regra(**extra))
    assert r.status_code == 200, r.text
    return r.json()


async def test_painel_e_mutacoes_exigem_admin_que_mexe(
    client, cena, make_user, auth_as, monkeypatch
):
    pessoa = await make_user(role=UserRole.USER)
    auth_as(pessoa)
    for path in (ADMIN, f"{ADMIN}/lixeira"):
        assert (await client.get(path)).status_code == 403
    assert (
        await client.put(f"{ADMIN}/acessos/{cena.geral.id}/{pessoa.id}", json=_regra())
    ).status_code == 403
    assert (await client.post(f"{ADMIN}/emails/{uuid4()}/excluir", json={})).status_code == 403
    assert (await client.post(f"{ADMIN}/emails/{uuid4()}/restaurar")).status_code == 403
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "outro@teste.invalid")
    auth_as(cena.dono)
    assert (await client.get(ADMIN)).status_code == 403


async def test_painel_informa_cadastro_sem_gravar_politicas(client, cena, make_user, auth_as, db):
    pessoa = await make_user(role=UserRole.USER)
    auth_as(cena.dono)
    r = await client.get(ADMIN)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    result = r.json()
    assert any(x["id"] == str(pessoa.id) for x in result["pessoas"])
    assert not any(x["id"] == str(cena.dono.id) for x in result["pessoas"])
    assert any(x["chave"] == f"f:{cena.loja_ml.id}" for x in result["lojas"])
    assert result["politicas"] == [] and result["lixeira_total"] == 0
    assert await db.scalar(select(func.count()).select_from(MailAcesso)) == 0


async def test_politica_validacao_concorrencia_e_chave_canonica(
    client, cena, make_user, auth_as, db
):
    pessoa = await make_user(role=UserRole.USER)
    auth_as(cena.dono)
    url = f"{ADMIN}/acessos/{cena.geral.id}/{pessoa.id}"
    for invalid in (["qualquer"], [f"i:{uuid4()}"], [f"f:{uuid4()}"]):
        r = await client.put(url, json=_regra(modo="selecionadas", lojas_permitidas=invalid))
        assert r.status_code == 422
    primeira = await _salvar(
        client, cena, pessoa, modo="selecionadas", lojas_permitidas=[f"i:{cena.ml.id}"]
    )
    assert primeira["lojas_permitidas"] == [f"f:{cena.loja_ml.id}"]
    assert primeira["updated_by_nome"]
    assert (await client.put(url, json=_regra(modo="bloqueado"))).status_code == 409
    segunda = await _salvar(
        client,
        cena,
        pessoa,
        modo="padrao",
        lojas_permitidas=primeira["lojas_permitidas"],
        expected_updated_at=primeira["updated_at"],
    )
    assert segunda["lojas_permitidas"] == []
    assert segunda["updated_at"] != primeira["updated_at"]
    assert (
        await client.put(url, json=_regra(expected_updated_at=primeira["updated_at"]))
    ).status_code == 409
    eventos = (
        await db.scalars(select(MailAdminEvento).where(MailAdminEvento.acao == "acesso_salvo"))
    ).all()
    assert len(eventos) == 2
    assert not any("assunto" in e.dados or "texto" in e.dados for e in eventos)


async def test_duas_telas_nao_sobrescrevem_criacao_ou_edicao(client, cena, make_user, auth_as, db):
    pessoa = await make_user(role=UserRole.USER)
    auth_as(cena.dono)
    url = f"{ADMIN}/acessos/{cena.geral.id}/{pessoa.id}"
    respostas = await asyncio.gather(
        client.put(url, json=_regra(modo="bloqueado")),
        client.put(url, json=_regra(modo="padrao")),
    )
    assert sorted(r.status_code for r in respostas) == [200, 409]
    primeira = next(r.json() for r in respostas if r.status_code == 200)
    respostas = await asyncio.gather(
        client.put(url, json=_regra(modo="bloqueado", expected_updated_at=primeira["updated_at"])),
        client.put(url, json=_regra(modo="padrao", expected_updated_at=primeira["updated_at"])),
    )
    assert sorted(r.status_code for r in respostas) == [200, 409]
    assert await db.scalar(select(func.count()).select_from(MailAcesso)) == 1
    assert await db.scalar(select(func.count()).select_from(MailAdminEvento)) == 2


async def test_selecionadas_filtra_caixa_indice_contagens_detalhe_anexos_e_nao_envia(
    db, cena, client, make_user, auth_as
):
    x = await _caso(db, cena, "12345678", X, anexos=[("x.txt", "text/plain", b"X")])
    outro_id = await entrar(
        db,
        cena.geral,
        pasta="problema shopee",
        de="suporte@shopee.com.br",
        assunto="Chamado ABC12345 aberto",
        anexos=[("y.txt", "text/plain", b"Y")],
    )
    await rodar(db)
    outro_anexo = await db.scalar(
        select(MailAttachment.id).where(MailAttachment.message_id == outro_id)
    )
    pessoa = await make_user(role=UserRole.USER)
    auth_as(cena.dono)
    await _salvar(client, cena, pessoa, modo="selecionadas", lojas_permitidas=[f"i:{cena.ml.id}"])
    auth_as(pessoa)
    boxes = (await client.get("/api/mail/mailboxes")).json()["items"]
    assert len(boxes) == 1 and boxes[0]["so_leitura"] is True
    assert boxes[0]["aliases"] == [] and boxes[0]["address"] == ""
    for path, chave in (
        (f"/api/mail/mailboxes/{cena.geral.id}/messages", "items"),
        (f"/api/mail/mailboxes/{cena.geral.id}/lista", "itens"),
    ):
        r = await client.get(path)
        assert r.status_code == 200, r.text
        assert [i["id"] for i in r.json()[chave]] == [str(x.message_id)]
    pastas = (await client.get(f"/api/mail/mailboxes/{cena.geral.id}/pastas")).json()
    assert pastas["pastas"][0]["total"] == 1
    assert sum(x["total"] for x in pastas["lojas"]) == 1
    assert (await client.get(f"/api/mail/messages/{x.message_id}")).status_code == 200
    assert (await client.get(f"/api/mail/messages/{outro_id}")).status_code == 404
    assert (await client.get(f"/api/mail/attachments/{outro_anexo}")).status_code == 404
    assert (
        await client.post(
            f"/api/mail/messages/{x.message_id}/reply",
            json={"request_id": str(uuid4()), "text": "Sem permissão"},
        )
    ).status_code == 404


async def test_padrao_nao_concede_caixa_e_bloqueio_nega_ate_dono(
    db, cena, client, make_user, auth_as
):
    pessoa = await make_user(role=UserRole.USER)
    auth_as(cena.dono)
    regra = await _salvar(client, cena, pessoa)
    auth_as(pessoa)
    assert (await client.get("/api/mail/mailboxes")).json()["items"] == []
    assert (await client.get(f"/api/mail/mailboxes/{cena.geral.id}/messages")).status_code == 404
    cena.geral.owner_user_id = pessoa.id
    await db.commit()
    auth_as(cena.dono)
    await _salvar(client, cena, pessoa, modo="bloqueado", expected_updated_at=regra["updated_at"])
    auth_as(pessoa)
    assert (await client.get("/api/mail/mailboxes")).json()["items"] == []
    assert (await client.get(f"/api/mail/mailboxes/{cena.geral.id}/messages")).status_code == 404


async def test_bloqueio_ficha_cobre_integracao_ambigua_de_email_legado(
    db, cena, client, make_user, auth_as
):
    meta = await _caso(db, cena)
    meta.store_info_id = None
    db.add(
        StoreInfo(
            user_id=cena.dono.id,
            account_name="Outra ficha",
            platform="mercadolivre",
            email="outro-alias",
            integration_id=cena.ml.id,
        )
    )
    pessoa = await make_user(role=UserRole.USER)
    cena.geral.owner_user_id = pessoa.id
    await db.commit()
    auth_as(cena.dono)
    await _salvar(client, cena, pessoa, lojas_bloqueadas=[f"f:{cena.loja_ml.id}"])
    auth_as(pessoa)
    assert (await client.get(f"/api/mail/messages/{meta.message_id}")).status_code == 404


async def test_lixeira_local_idempotente_restaura_sem_tocar_original_ponte_ou_tuta(
    db, cena, client, auth_as
):
    m = await _caso(db, cena, "12345678", X, corpo="Corpo privado não é auditado")
    source = await db.get(MailMessage, m.message_id)
    original, estado = bytes(source.content_enc), m.estado
    auth_as(cena.dono)
    url = f"{ADMIN}/emails/{source.id}"
    for _ in range(2):
        assert (
            await client.post(f"{url}/excluir", json={"motivo": "Duplicado visual"})
        ).status_code == 200
    assert (await client.get(f"/api/mail/messages/{source.id}")).status_code == 404
    lista = (await client.get(f"{ADMIN}/lixeira", params={"q": X})).json()
    assert [i["message_id"] for i in lista["itens"]] == [str(source.id)]
    assert lista["itens"][0]["motivo"] == "Duplicado visual"
    for _ in range(2):
        assert (await client.post(f"{url}/restaurar")).status_code == 200
    assert (await client.get(f"/api/mail/messages/{source.id}")).status_code == 200
    await db.refresh(source)
    await db.refresh(m)
    assert bytes(source.content_enc) == original and m.estado == estado
    eventos = (
        await db.scalars(select(MailAdminEvento).where(MailAdminEvento.acao.like("email_%")))
    ).all()
    assert [e.acao for e in eventos] == ["email_excluido", "email_restaurado"]
    assert all(set(e.dados) == {"message_id"} for e in eventos)
    assert (await client.get(f"{ADMIN}/lixeira")).json()["itens"] == []


async def test_lixeira_busca_e_cursor_nao_repetem_nem_pulam(db, cena, client, auth_as):
    mids = [await entrar(db, cena.geral, assunto=f"Aviso venda 00{n}") for n in range(3)]
    auth_as(cena.dono)
    for mid in mids:
        assert (await client.post(f"{ADMIN}/emails/{mid}/excluir", json={})).status_code == 200
    ids, cursor = [], None
    for _ in range(4):
        p = {"limite": 1, "q": "AVISO"}
        if cursor:
            p["antes"] = cursor
        r = await client.get(f"{ADMIN}/lixeira", params=p)
        assert r.status_code == 200, r.text
        ids.extend(i["message_id"] for i in r.json()["itens"])
        cursor = r.json()["proximo"]
        if not cursor:
            break
    assert set(ids) == {str(m) for m in mids} and len(ids) == 3
    for cursor in ("x", "abc", "bm9uZQ"):
        assert (await client.get(f"{ADMIN}/lixeira", params={"antes": cursor})).status_code == 422


@pytest.mark.parametrize("estado", ["queued", "leased", "uncertain"])
async def test_excluir_recusa_envio_pendente_sem_cancelar(client, account, db, estado):
    mid, _, job = await queued(client, account)
    row = await db.get(MailOutbox, UUID(job["id"]))
    row.status = estado
    await db.commit()
    r = await client.post(f"{ADMIN}/emails/{mid}/excluir", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "email_envio_pendente"
    assert await db.get(MailLixeira, UUID(mid)) is None
    await db.refresh(row)
    assert row.status == estado


async def test_revogar_politica_impede_lease_ja_enfileirado(
    client, account, db, make_user, auth_as
):
    mid = await ingest(client, account)
    pessoa = await make_user(role=UserRole.USER)
    box = await db.get(MailMailbox, UUID(account["mailbox"]["id"]))
    box.owner_user_id = pessoa.id
    await db.commit()
    await enable(client, account)
    auth_as(pessoa)
    r = await client.post(
        f"/api/mail/messages/{mid}/reply",
        json={"request_id": str(uuid4()), "text": "Resposta da pessoa"},
    )
    assert r.status_code == 202, r.text
    auth_as(account["admin"])
    r = await client.put(f"{ADMIN}/acessos/{box.id}/{pessoa.id}", json=_regra(modo="bloqueado"))
    assert r.status_code == 200, r.text
    assert await claim(client, account) == []
    job = await db.scalar(select(MailOutbox).where(MailOutbox.message_id == UUID(mid)))
    await db.refresh(job)
    assert job.status == "failed" and job.error_code == "author_access_revoked"


def test_migration_tem_uma_ponta_e_tres_tabelas_laterais():
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    assert "0415_mail_admin" in {rev.revision for rev in scripts.walk_revisions()}
    assert (
        scripts.get_revision("0415_mail_admin").down_revision
        == "0414_vigia_conciliacao_chave_etapa"
    )


async def test_migration_sobe_bate_colunas_modelo_e_desce(db):
    path = Path(__file__).parents[1] / "alembic/versions/0415_mail_admin.py"
    spec = importlib.util.spec_from_file_location("migration_mail_admin", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    schema = f"{Base.metadata.schema}_mig0415"
    mod.SCHEMA = schema
    await db.execute(text(f'CREATE SCHEMA "{schema}"'))
    for table in ("users", "mail_mailboxes", "mail_messages"):
        await db.execute(text(f'CREATE TABLE "{schema}".{table} (id uuid PRIMARY KEY)'))
    await db.commit()

    def rodar(conn, passo):
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    try:
        connection = await db.connection()
        await connection.run_sync(rodar, "upgrade")
        await db.commit()
        for table in ("mail_acessos", "mail_lixeira", "mail_admin_eventos"):
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
