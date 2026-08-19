import pytest

from app.models import UserRole

VALID_CNPJ = "11444777000161"  # valid DV
INVALID_CNPJ = "11444777000162"


@pytest.mark.asyncio
async def test_create_company_valid_cnpj(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        "/api/companies",
        json={
            "razao_social": "AGUIAR INTERMEDIACOES LTDA",
            "apelido": "aguiar",
            "uf": "sp",
            "cnpj": "11.444.777/0001-61",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["cnpj"] == VALID_CNPJ
    assert body["uf"] == "SP"
    assert body["apelido"] == "aguiar"


@pytest.mark.asyncio
async def test_create_company_invalid_cnpj(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        "/api/companies",
        json={"razao_social": "X", "apelido": "x", "cnpj": INVALID_CNPJ},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_obs_preserves_cnpj_and_uf(client, make_user, auth_as):
    # Regression: a PATCH carrying only `obs` must not wipe the stored
    # CNPJ/UF. The mode="before" validator used to inject cnpj=None/uf=None,
    # which `exclude_unset` then persisted as None.
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        "/api/companies",
        json={"razao_social": "A", "apelido": "a", "uf": "sp", "cnpj": VALID_CNPJ},
    )
    assert r.status_code == 201, r.text
    company_id = r.json()["id"]

    r2 = await client.patch(
        f"/api/companies/{company_id}",
        json={"obs": "observação qualquer"},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["obs"] == "observação qualquer"
    assert body["cnpj"] == VALID_CNPJ
    assert body["uf"] == "SP"


@pytest.mark.asyncio
async def test_create_company_duplicate_cnpj_409(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    body = {"razao_social": "A", "apelido": "a", "cnpj": VALID_CNPJ}
    r1 = await client.post("/api/companies", json=body)
    assert r1.status_code == 201
    r2 = await client.post(
        "/api/companies",
        json={"razao_social": "B", "apelido": "b", "cnpj": VALID_CNPJ},
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "cnpj_exists"


@pytest.mark.asyncio
async def test_companies_grid_basic(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        "/api/companies",
        json={"razao_social": "AGUIAR", "apelido": "aguiar"},
    )
    company_id = r.json()["id"]
    r = await client.post(
        "/api/stores",
        json={"company_id": company_id, "marketplace": "ml", "status": "active"},
    )
    assert r.status_code == 201, r.text

    g = await client.get("/api/companies/grid")
    assert g.status_code == 200
    body = g.json()
    assert "ml" in body["marketplaces"]
    row = next(r for r in body["rows"] if r["company"]["id"] == company_id)
    assert row["stores"]["ml"]["status"] == "active"
    assert row["stores"]["shopee"] is None


@pytest.mark.asyncio
async def test_store_unique_per_company_marketplace(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post("/api/companies", json={"razao_social": "A", "apelido": "a"})
    cid = r.json()["id"]
    r1 = await client.post("/api/stores", json={"company_id": cid, "marketplace": "ml"})
    assert r1.status_code == 201
    r2 = await client.post("/api/stores", json={"company_id": cid, "marketplace": "ml"})
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "store_already_exists"


@pytest.mark.asyncio
async def test_user_without_perm_blocked(client, make_user, auth_as):
    user = await make_user(role=UserRole.USER, permissions={})
    auth_as(user)
    r = await client.get("/api/companies")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_cadastro_grid_with_alias(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post("/api/companies", json={"razao_social": "AGUIAR", "apelido": "aguiar"})
    c1 = r.json()["id"]
    r = await client.post("/api/companies", json={"razao_social": "AGUIAR2", "apelido": "aguiar2"})
    c2 = r.json()["id"]
    body_s = {"marketplace": "ml", "status": "active"}
    s1 = (await client.post("/api/stores", json={**body_s, "company_id": c1})).json()["id"]
    s2 = (await client.post("/api/stores", json={**body_s, "company_id": c2})).json()["id"]

    r = await client.post(
        "/api/cadastros",
        json={"tipo": "fone", "codigo": "11951091238", "store_ids": [s1, s2]},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    # Override alias on second link
    r = await client.put(
        f"/api/cadastros/{cid}/stores",
        json={"links": [{"store_id": s1}, {"store_id": s2, "alias": "aguiar2"}]},
    )
    assert r.status_code == 200, r.text

    g = await client.get("/api/cadastros/grid")
    assert g.status_code == 200
    body = g.json()
    row = next(r for r in body["rows"] if r["cadastro"]["id"] == cid)
    cells = row["cells"]["ml"]
    labels = sorted(c["alias"] or c["company_apelido"] for c in cells)
    assert labels == ["aguiar", "aguiar2"]


@pytest.mark.asyncio
async def test_cadastro_resolve_raw_link(client, make_user, auth_as, db):
    from sqlalchemy import text as _text
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post("/api/companies", json={"razao_social": "AGUIAR", "apelido": "aguiar"})
    c1 = r.json()["id"]
    s1 = (await client.post(
        "/api/stores", json={"company_id": c1, "marketplace": "ml", "status": "active"}
    )).json()["id"]

    r = await client.post(
        "/api/cadastros",
        json={"tipo": "fone", "codigo": "11999999999"},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    # Seed raw_links via direct SQL (xlsx populate path produces these).
    await db.execute(
        _text("UPDATE davinci_test.cadastros SET raw_links = '{\"ml\": \"aguiar\"}'::jsonb WHERE id = CAST(:i AS uuid)"),
        {"i": cid},
    )
    await db.commit()

    r = await client.post(
        f"/api/cadastros/{cid}/raw-links/ml/resolve",
        json={"store_id": s1, "alias": "aguiar"},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["raw_links"] == {}

    # Marketplace mismatch: use a shopee store_id but resolve under "ml" → 400.
    s2 = (await client.post(
        "/api/stores", json={"company_id": c1, "marketplace": "shopee", "status": "active"}
    )).json()["id"]
    await db.execute(
        _text("UPDATE davinci_test.cadastros SET raw_links = '{\"ml\": \"x\"}'::jsonb WHERE id = CAST(:i AS uuid)"),
        {"i": cid},
    )
    await db.commit()
    r = await client.post(
        f"/api/cadastros/{cid}/raw-links/ml/resolve",
        json={"store_id": s2},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "marketplace_mismatch"


@pytest.mark.asyncio
async def test_companies_grid_store_info_fallback_ignora_espacos(
    client, make_user, auth_as, db
):
    """store_info "dream2" (sem espaço) acende o X da empresa "dream 2"."""
    from sqlalchemy import text as _text

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        "/api/companies",
        json={"razao_social": "DREAM COMERCIO", "apelido": "dream 2"},
    )
    company_id = r.json()["id"]
    await db.execute(
        _text(
            "INSERT INTO davinci_test.store_info (id, user_id, platform, account_name)"
            " VALUES (gen_random_uuid(), CAST(:u AS uuid), 'ml', 'dream2')"
        ),
        {"u": str(admin.id)},
    )
    await db.commit()

    g = await client.get("/api/companies/grid")
    assert g.status_code == 200
    row = next(r for r in g.json()["rows"] if r["company"]["id"] == company_id)
    assert row["stores"]["ml"] is not None, "match deve ignorar espaços"
    assert row["stores"]["ml"]["from_store_info"] is True
    assert row["stores"]["shopee"] is None
