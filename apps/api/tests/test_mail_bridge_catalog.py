"""Live folder catalog adds empty folders without widening mailbox access."""

from uuid import uuid4

from sqlalchemy import func, select

from app.models import UserRole
from app.models.mail_admin import MailAcesso
from app.models.mail_atendimento import MailFolder
from app.security.cipher import decrypt_json, encrypt_json
from tests.test_mail_caixa import _pastas
from tests.test_mail_ponte import _caixa, entrar
from tests.test_mail_ponte import _config as _config  # noqa: F401
from tests.test_mail_ponte import cena as cena  # noqa: F401


async def _catalog(db, caixa, folders, observed_at="2026-10-27T12:00:00Z"):
    config = decrypt_json(caixa.config_enc)
    config["bridge_folder_catalog"] = {
        "version": 1,
        "observed_at": observed_at,
        "folders": [
            {"id": folder_id, "name": name, "kind": kind} for folder_id, name, kind in folders
        ],
    }
    caixa.config_enc = encrypt_json(config)
    await db.commit()


async def test_empty_catalog_folders_appear_without_materializing_private_names(
    db, cena, client, auth_as
):
    await _catalog(
        db,
        cena.geral,
        [
            ("inbox", "INBOX", "inbox"),
            ("new", "informativo", "custom"),
            ("trash-child", "Trash/pasta antiga", "trash"),
        ],
    )
    auth_as(cena.dono)
    result = await _pastas(client, cena.geral)
    assert [(x["nome"], x["total"], x["quantidade"]) for x in result["pastas"]] == [
        ("Todas", 0, 0),
        ("Entrada", 0, 0),
        ("Lixeira", 0, 0),
        ("informativo", 0, 0),
    ]
    assert result["lojas"] == []
    assert await db.scalar(select(func.count()).select_from(MailFolder)) == 0


async def test_renamed_empty_folder_replaces_old_name_and_removal_leaves_no_phantom(
    db, cena, client, auth_as
):
    auth_as(cena.dono)
    await _catalog(db, cena.geral, [("stable", "antiga", "custom")])
    old = (await _pastas(client, cena.geral))["pastas"][-1]
    await _catalog(db, cena.geral, [("stable", "informativo", "custom")])
    renamed = (await _pastas(client, cena.geral))["pastas"]
    assert [item["nome"] for item in renamed] == ["Todas", "informativo"]
    assert renamed[-1]["id"] != old["id"]  # Existing index keys follow the current name.
    await _catalog(db, cena.geral, [])
    assert [item["nome"] for item in (await _pastas(client, cena.geral))["pastas"]] == ["Todas"]


async def test_catalog_merges_existing_counts_and_keeps_store_filter_totals(
    db, cena, client, auth_as
):
    for _ in range(2):
        await entrar(db, cena.geral, pasta="informativo")
    await _catalog(
        db,
        cena.geral,
        [
            ("info", "informativo", "custom"),
            ("empty", "nova vazia", "custom"),
        ],
    )
    auth_as(cena.dono)
    result = await _pastas(client, cena.geral, loja=f"f:{uuid4()}")
    assert [(x["nome"], x["quantidade"], x["total"]) for x in result["pastas"]] == [
        ("Todas", 0, 2),
        ("informativo", 0, 2),
        ("nova vazia", 0, 0),
    ]


async def test_restricted_reader_does_not_learn_empty_catalog_names(
    db, cena, client, make_user, auth_as
):
    person = await make_user(role=UserRole.USER)
    caixa = _caixa(person, "Própria", "own@example.com", [], "catalog-test-token-0123456789")
    db.add(caixa)
    await db.commit()
    db.add(MailAcesso(mailbox_id=caixa.id, user_id=person.id, modo="padrao"))
    await _catalog(db, caixa, [("private", "Banco particular", "custom")])
    auth_as(person)
    assert [x["nome"] for x in (await _pastas(client, caixa))["pastas"]] == ["Todas"]
    auth_as(cena.dono)
    assert [x["nome"] for x in (await _pastas(client, caixa))["pastas"]] == [
        "Todas",
        "Banco particular",
    ]


async def test_inventory_refresh_does_not_reset_search_or_open_draft(db, cena, client, auth_as):
    auth_as(cena.dono)
    before = (await _pastas(client, cena.geral))["acesso_versao"]
    await _catalog(db, cena.geral, [("info", "informativo", "custom")])
    assert (await _pastas(client, cena.geral))["acesso_versao"] == before
    await _catalog(db, cena.geral, [("info", "renomeada", "custom")], "2026-10-27T12:01:00Z")
    assert (await _pastas(client, cena.geral))["acesso_versao"] == before
    config = decrypt_json(cena.geral.config_enc)
    config["aliases"].append("new@example.com")
    cena.geral.config_enc = encrypt_json(config)
    await db.commit()
    assert (await _pastas(client, cena.geral))["acesso_versao"] != before
