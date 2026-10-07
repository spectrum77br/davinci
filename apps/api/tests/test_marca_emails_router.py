"""Cadastros › E-mails: só marcas com conta em Redes Sociais, três e-mails do Tuta por marca."""

import uuid

import pytest
from sqlalchemy import func, select

from app.models import Marca, MarcaEmail, RedeSocial

pytestmark = pytest.mark.asyncio


async def setup(db, make_user, auth_as, *, edit=True):
    user = await make_user(permissions={"email_padroes": {"view": True, "edit": edit}})
    auth_as(user)
    redes = Marca(
        nome="uranyx",
        slug="uranyx",
        site="https://uranyx.com.br",
        sac_email="sac@uranyx.com.br",
        sac_fone="11983517003",
    )
    reservada = Marca(nome="doscatos", slug="doscatos")
    sem_redes = Marca(nome="12paxion", slug="12paxion")
    db.add_all([redes, reservada, sem_redes])
    await db.flush()
    db.add_all(
        [
            RedeSocial(marca_id=redes.id, plataforma="instagram", conta="uranyx"),
            # Linha sem @ (só e-mail reservado) não faz a marca entrar.
            RedeSocial(
                marca_id=reservada.id, plataforma="tiktok", conta=None, email="x@doscatos.com.br"
            ),
        ]
    )
    await db.commit()
    return redes, reservada, sem_redes


async def test_grid_so_marcas_com_conta_em_redes(client, db, make_user, auth_as):
    redes, _, _ = await setup(db, make_user, auth_as)
    r = await client.get("/api/marca-emails")
    assert r.status_code == 200
    data = r.json()
    assert data["tipos"] == ["sac", "duvidas", "atacado"]
    assert [row["marca"]["nome"] for row in data["rows"]] == ["uranyx"]
    row = data["rows"][0]
    assert row["marca"]["site"] == "https://uranyx.com.br"
    # WhatsApp da marca vai pra assinatura do Tuta.
    assert row["marca"]["sac_fone"] == "11983517003"
    # O e-mail de login das redes (sac_email) não é copiado pra cá.
    assert row["emails"] == {"sac": None, "duvidas": None, "atacado": None}
    assert "senha" not in str(data)


async def test_salva_edita_e_apaga_por_tipo(client, db, make_user, auth_as):
    redes, _, _ = await setup(db, make_user, auth_as)
    url = f"/api/marca-emails/{redes.id}"
    r = await client.put(
        url, json={"sac": " SAC@Uranyx.com.br ", "duvidas": "duvidas@uranyx.com.br"}
    )
    assert r.status_code == 200
    assert r.json()["emails"] == {
        "sac": "sac@uranyx.com.br",
        "duvidas": "duvidas@uranyx.com.br",
        "atacado": None,
    }
    primeiro = await db.scalar(select(MarcaEmail.id).where(MarcaEmail.tipo == "sac"))
    r = await client.put(
        url, json={"sac": "sac@uranyx.com.br", "duvidas": "", "atacado": "atacado@uranyx.com.br"}
    )
    assert r.status_code == 200
    linhas = {
        e.tipo: e
        for e in (
            await db.scalars(select(MarcaEmail).execution_options(populate_existing=True))
        ).all()
    }
    assert set(linhas) == {"sac", "atacado"}
    assert linhas["sac"].id == primeiro
    assert linhas["atacado"].email == "atacado@uranyx.com.br"
    grid = (await client.get("/api/marca-emails")).json()
    assert grid["rows"][0]["emails"] == {
        "sac": "sac@uranyx.com.br",
        "duvidas": None,
        "atacado": "atacado@uranyx.com.br",
    }
    await db.refresh(redes)
    assert redes.sac_email == "sac@uranyx.com.br"


async def test_recusa_email_invalido_e_marca_fora_das_redes(client, db, make_user, auth_as):
    redes, reservada, sem_redes = await setup(db, make_user, auth_as)
    r = await client.put(f"/api/marca-emails/{redes.id}", json={"sac": "sem-arroba"})
    assert r.status_code == 422
    r = await client.put(f"/api/marca-emails/{redes.id}", json={"assinatura": "x"})
    assert r.status_code == 422
    for m in (reservada, sem_redes):
        r = await client.put(f"/api/marca-emails/{m.id}", json={"sac": "sac@x.com.br"})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "marca_sem_redes_sociais"
    r = await client.put(f"/api/marca-emails/{uuid.uuid4()}", json={"sac": "sac@x.com.br"})
    assert r.status_code == 404
    assert await db.scalar(select(func.count()).select_from(MarcaEmail)) == 0


async def test_so_view_nao_salva(client, db, make_user, auth_as):
    redes, _, _ = await setup(db, make_user, auth_as, edit=False)
    assert (await client.get("/api/marca-emails")).status_code == 200
    r = await client.put(f"/api/marca-emails/{redes.id}", json={"sac": "sac@uranyx.com.br"})
    assert r.status_code == 403
    assert await db.scalar(select(func.count()).select_from(MarcaEmail)) == 0


async def test_apagar_marca_leva_os_emails(client, db, make_user, auth_as):
    redes, _, _ = await setup(db, make_user, auth_as)
    await client.put(f"/api/marca-emails/{redes.id}", json={"sac": "sac@uranyx.com.br"})
    await db.delete(redes)
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(MarcaEmail)) == 0
