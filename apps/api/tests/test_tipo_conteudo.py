"""Ideias e vídeos de HISTÓRIA ou de PRODUTO (migration 0444).

Eduardo, 03/11/2026: a aba Ideias do portal vira "Ideias — Histórias" e
"Ideias — Produtos"; "essas de produto vão ali automaticamente para o feed
também, o das histórias só shorts". O tipo nasce na ideia, desce para o vídeo
entregue e é o que o robô lê para decidir o `share_to_feed` do Instagram.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import MarketingCreative, UserRole
from app.models.marketing_roteiro import MarketingRoteiro

TOK = "tok-agencia-a-0123456789"
A = {"X-Portal-Token": TOK}
R = "/api/marketing/roteiros"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(get_settings(), "portal_tokens", f"{TOK}:alpha")


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _ideia(db: AsyncSession, *, titulo: str, tipo: str = "historia") -> MarketingRoteiro:
    r = MarketingRoteiro(
        titulo=titulo, texto="cena 1", marca="charlots", sku="b086.24",
        equipe_destino="alpha", ativo=True, tipo_conteudo=tipo,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def test_sem_tipo_a_ideia_e_o_video_sao_historia(db: AsyncSession):
    """O que já existe (e o que chega sem tipo) é história: não entra na grade."""
    r = MarketingRoteiro(titulo="x", texto="y")
    c = MarketingCreative(modelo="z")
    db.add_all([r, c])
    await db.commit()
    await db.refresh(r)
    await db.refresh(c)
    assert (r.tipo_conteudo, c.tipo_conteudo) == ("historia", "historia")


async def test_portal_separa_as_duas_abas(client: AsyncClient, db: AsyncSession):
    await _ideia(db, titulo="Josi perde a mala", tipo="historia")
    await _ideia(db, titulo="Mala Sorriso 24", tipo="produto")

    def titulos(resp):
        assert resp.status_code == 200, resp.text
        return {(i["titulo"], i["tipo_conteudo"]) for i in resp.json()["roteiros"]}

    assert titulos(await client.get("/api/portal/roteiros?tipo_conteudo=produto", headers=A)) == {
        ("Mala Sorriso 24", "produto")
    }
    assert titulos(await client.get("/api/portal/roteiros?tipo_conteudo=historia", headers=A)) == {
        ("Josi perde a mala", "historia")
    }
    assert len(titulos(await client.get("/api/portal/roteiros", headers=A))) == 2
    r = await client.get("/api/portal/roteiros?tipo_conteudo=outro", headers=A)
    assert r.status_code == 422


async def test_ideia_criada_pelo_portal_guarda_o_tipo(client: AsyncClient):
    r = await client.post(
        "/api/portal/roteiros",
        headers=A,
        json={"titulo": "Mala Sorriso", "texto": "close na fechadura", "tipo_conteudo": "produto"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["roteiro"]["tipo_conteudo"] == "produto"
    r = await client.post(
        "/api/portal/roteiros", headers=A, json={"titulo": "Josi", "texto": "aeroporto"}
    )
    assert r.json()["roteiro"]["tipo_conteudo"] == "historia"


async def test_video_entregue_herda_o_tipo_da_ideia(client: AsyncClient, db: AsyncSession):
    ideia = await _ideia(db, titulo="Mala Sorriso 24", tipo="produto")
    r = await client.post(
        f"/api/portal/roteiros/{ideia.id}/entrega",
        headers=A,
        files={"files": ("peca.mp4", b"bytes de video", "video/mp4")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["tipo_conteudo"] == "produto"


async def test_versao_fica_na_mesma_aba_da_ideia(client: AsyncClient, db: AsyncSession):
    ideia = await _ideia(db, titulo="Mala Sorriso 24", tipo="produto")
    r = await client.post(
        f"/api/portal/roteiros/{ideia.id}/versao", headers=A, json={"texto": "outra cena"}
    )
    assert r.status_code in (200, 201), r.text
    versao = (
        await db.execute(select(MarketingRoteiro).where(MarketingRoteiro.origem_id == ideia.id))
    ).scalar_one()
    assert versao.tipo_conteudo == "produto"


async def test_video_autoral_traz_o_tipo_no_envio(client: AsyncClient, db: AsyncSession):
    r = await client.post(
        "/api/portal/criativos/proposta",
        headers=A,
        data={"titulo": "Mala no aeroporto", "tipo_conteudo": "produto"},
        files={"files": ("v.mp4", b"x" * 100, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["tipo_conteudo"] == "produto"
    linha = await db.get(MarketingCreative, UUID(r.json()["id"]))
    assert linha.tipo_conteudo == "produto"


async def test_video_autoral_com_tipo_torto_e_recusado_antes_de_gravar(
    client: AsyncClient, db: AsyncSession
):
    antes = len((await db.execute(select(MarketingCreative))).scalars().all())
    r = await client.post(
        "/api/portal/criativos/proposta",
        headers=A,
        data={"titulo": "Mala no aeroporto", "tipo_conteudo": "feed"},
        files={"files": ("v.mp4", b"x" * 100, "video/mp4")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "tipo_conteudo_invalido"
    assert len((await db.execute(select(MarketingCreative))).scalars().all()) == antes


async def test_reclassificar_a_ideia_leva_junto_versoes_e_videos(
    client: AsyncClient, db: AsyncSession, admin
):
    """Vídeo já na fila do robô com o tipo velho deixaria o Reel de produto
    fora da grade — o tipo da ideia vale para o que saiu dela."""
    r = await client.post(R, json={"titulo": "Mala Sorriso 24", "texto": "cena 1"})
    assert r.status_code == 200, r.text
    assert r.json()["tipo_conteudo"] == "historia"
    rid = UUID(r.json()["id"])
    versao = MarketingRoteiro(titulo="v", texto="t", origem_id=rid)
    video = MarketingCreative(modelo="m", roteiro_id=rid)
    outro = MarketingCreative(modelo="sem ideia")
    db.add_all([versao, video, outro])
    await db.commit()

    r = await client.patch(f"{R}/{rid}", json={"tipo_conteudo": "produto"})
    assert r.status_code == 200, r.text
    assert r.json()["tipo_conteudo"] == "produto"
    for row in (versao, video, outro):
        await db.refresh(row)
    assert (versao.tipo_conteudo, video.tipo_conteudo) == ("produto", "produto")
    assert outro.tipo_conteudo == "historia", "o que não saiu da ideia não muda"


async def test_ligar_video_a_ideia_herda_o_tipo_e_da_pra_trocar_na_mao(
    client: AsyncClient, db: AsyncSession, admin
):
    ideia = await _ideia(db, titulo="Mala Sorriso 24", tipo="produto")
    video = MarketingCreative(modelo="m")
    db.add(video)
    await db.commit()

    r = await client.patch(f"/api/marketing/creatives/{video.id}", json={"roteiro_id": str(ideia.id)})
    assert r.status_code == 200, r.text
    assert r.json()["tipo_conteudo"] == "produto"

    r = await client.patch(f"/api/marketing/creatives/{video.id}", json={"tipo_conteudo": "historia"})
    assert r.status_code == 200, r.text
    assert r.json()["tipo_conteudo"] == "historia"

    r = await client.patch(f"/api/marketing/creatives/{video.id}", json={"tipo_conteudo": "feed"})
    assert r.status_code == 422
