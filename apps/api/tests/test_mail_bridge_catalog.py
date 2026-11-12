"""Live folder catalog adds empty folders without widening mailbox access."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import MailMessage, UserRole
from app.models.mail_atendimento import MailFolder, MailMailboxSettings
from app.security.cipher import decrypt_json, encrypt_json
from tests.test_mail_caixa import _lista, _pastas
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


async def test_marked_reader_does_not_learn_owner_empty_catalog_names(
    db, cena, client, make_user, auth_as
):
    """12/11/2026: quem o admin marcou na caixa não conhece as pastas vazias
    (o nome de uma pasta particular do dono); o dono, sim."""
    person = await make_user(
        role=UserRole.USER, permissions={"atendimento_email_leitura": {"view": True}}
    )
    caixa = _caixa(cena.dono, "Do dono", "own@example.com", [], "catalog-test-token-0123456789")
    db.add(caixa)
    await db.commit()
    db.add(MailMailboxSettings(mailbox_id=caixa.id, leitores=[str(person.id)]))
    await db.commit()
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


_SYSTEM_FOLDERS = [
    ("inbox", "INBOX", "inbox"),
    ("trash", "Trash", "trash"),
    ("archive", "Archive", "archive"),
]


async def _full_catalog(db, caixa, folders):
    await _catalog(db, caixa, [*_SYSTEM_FOLDERS, *folders])
    config = decrypt_json(caixa.config_enc)
    config["bridge_folder_catalog"]["inventory_ready"] = True
    caixa.config_enc = encrypt_json(config)
    await db.commit()


@pytest.mark.parametrize("with_folder_row", [False, True])
async def test_deleted_nonempty_folder_disappears_but_mail_and_totals_remain(
    db, cena, client, auth_as, with_folder_row
):
    old = await entrar(db, cena.geral, pasta="pasta removida", assunto="historico preservado")
    current = await entrar(db, cena.geral, pasta="atual")
    await entrar(db, cena.geral, pasta="Sent")
    if with_folder_row:
        db.add(
            MailFolder(
                mailbox_id=cena.geral.id,
                chave="pasta removida",
                nome="pasta removida",
                caminho="pasta removida",
                tipo_tuta="0",
                sumiu_em=datetime.now(UTC),
            )
        )
        await db.commit()
    before = (await db.get(MailMessage, old)).content_enc
    await _full_catalog(db, cena.geral, [("current", "atual", "custom")])
    auth_as(cena.dono)
    result = await _pastas(client, cena.geral)
    names = {item["nome"] for item in result["pastas"]}
    assert "pasta removida" not in names
    assert {"Todas", "atual", "Enviados"} <= names
    assert result["pastas"][0]["total"] == result["pastas"][0]["quantidade"] == 3
    assert {str(old), str(current)} <= {
        item["id"] for item in (await _lista(client, cena.geral))["itens"]
    }
    found = await _lista(client, cena.geral, q="historico preservado")
    assert [item["id"] for item in found["itens"]] == [str(old)]
    assert (await db.get(MailMessage, old, populate_existing=True)).content_enc == before
    assert await db.scalar(select(func.count()).select_from(MailMessage)) == 3
    empty_store = await _pastas(client, cena.geral, loja=f"f:{uuid4()}")
    assert empty_store["pastas"][0]["total"] == 3
    assert empty_store["pastas"][0]["quantidade"] == 0
    admin = await client.get("/api/atendimento/email/admin/usuarios")
    assert admin.status_code == 200, admin.text
    box = next(item for item in admin.json()["caixas"] if item["id"] == str(cena.geral.id))
    assert "pasta removida" not in {item["nome"] for item in box["pastas"]}


@pytest.mark.parametrize(
    "invalid", ["unready", "version", "missing_system", "duplicate", "timestamp"]
)
async def test_unacknowledged_or_invalid_catalog_never_hides_historical_folder(
    db, cena, client, auth_as, invalid
):
    await entrar(db, cena.geral, pasta="historica")
    await _full_catalog(db, cena.geral, [])
    config = decrypt_json(cena.geral.config_enc)
    catalog = config["bridge_folder_catalog"]
    if invalid == "unready":
        catalog["inventory_ready"] = False
    elif invalid == "version":
        catalog["version"] = 2
    elif invalid == "missing_system":
        catalog["folders"] = catalog["folders"][:1]
    elif invalid == "duplicate":
        catalog["folders"].append(catalog["folders"][0])
    else:
        catalog["observed_at"] = "invalid"
    cena.geral.config_enc = encrypt_json(config)
    await db.commit()
    auth_as(cena.dono)
    result = await _pastas(client, cena.geral)
    assert "historica" in {item["nome"] for item in result["pastas"]}
    assert result["pastas"][0]["total"] == 1


async def test_marked_reader_hides_removed_folder_without_learning_private_catalog_names(
    db, cena, client, make_user, auth_as
):
    person = await make_user(
        role=UserRole.USER, permissions={"atendimento_email_leitura": {"view": True}}
    )
    # The existing private mailbox has one shop alias. A marked reader may see
    # that shop's mail, never the owner's personal mail or empty folder names.
    caixa = cena.goslin
    settings = await db.get(MailMailboxSettings, caixa.id)
    settings.leitores = [str(person.id)]
    await db.commit()
    message = await entrar(db, caixa, pasta="removida", para=["mia30@tuta.com"])
    private = await entrar(
        db, caixa, pasta="Banco particular", para=["goslin@tuta.com"], assunto="Extrato privado"
    )
    await _full_catalog(db, caixa, [("private", "Banco particular", "custom")])
    auth_as(person)
    result = await _pastas(client, caixa)
    assert [item["nome"] for item in result["pastas"]] == ["Todas"]
    assert result["pastas"][0]["total"] == 1
    visible = (await _lista(client, caixa))["itens"]
    assert [item["id"] for item in visible] == [str(message)]
    assert str(private) not in {item["id"] for item in visible}
    assert (await client.get(f"/api/mail/messages/{private}")).status_code == 404
