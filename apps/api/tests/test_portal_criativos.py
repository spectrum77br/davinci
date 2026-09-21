"""A porta estreita para as agências — o que ela deixa passar e o que não.

Estes testes existem por causa de um achado concreto: em 18/09/2026 uma
revisão encontrou que o webhook do Bling nunca rejeita (a função de
verificação não tem um `raise` sequer). Este router é o contrário disso, e é
isto que os primeiros testes travam: **sem configuração, fechado**.

Depois vem o que separa uma agência da outra, e o que a lista branca esconde.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.marketing import MarketingCreative, MarketingCreativeFile

TOK_A = "tok-agencia-a-0123456789"
TOK_B = "tok-agencia-b-9876543210"


@pytest.fixture(autouse=True)
def _dois_tokens(monkeypatch):
    monkeypatch.setattr(
        get_settings(), "portal_tokens", f"{TOK_A}:alpha,{TOK_B}:beta"
    )


async def _linha(
    db: AsyncSession,
    *,
    equipe: str | None,
    roteiro: str | None = "cena 1: abre a mala",
    aprovado: bool | None = True,
    com_arquivo: bool = True,
) -> MarketingCreative:
    c = MarketingCreative(
        modelo="Mala de bordo 20kg",
        marca="poofy",
        sku="dgd23",
        equipe=equipe,
        roteiro=roteiro,
        aprovado=aprovado,
        legenda="LEGENDA INTERNA que não é da conta da agência",
        pushed_dest="/Marcas/Charlots/dgd23",
    )
    db.add(c)
    await db.flush()
    if com_arquivo:
        db.add(
            MarketingCreativeFile(
                creative_id=c.id,
                file_name="video.mp4",
                file_mime="video/mp4",
                file_size=1234,
                file_rel=f"creatives/{c.id}/video.mp4",
                sha256="a" * 64,
            )
        )
    await db.commit()
    await db.refresh(c)
    return c


# ─────────────── fecha por padrão ───────────────


async def test_sem_configuracao_o_portal_fica_fechado(client: AsyncClient, monkeypatch):
    """Segredo vazio = 401, NUNCA aberto. É a lição do webhook do Bling."""
    monkeypatch.setattr(get_settings(), "portal_tokens", "")
    r = await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_A})
    assert r.status_code == 401


async def test_sem_header_401(client: AsyncClient):
    assert (await client.get("/api/portal/criativos")).status_code == 401


async def test_token_errado_401(client: AsyncClient):
    r = await client.get("/api/portal/criativos", headers={"X-Portal-Token": "chute"})
    assert r.status_code == 401


async def test_token_de_outra_agencia_nao_vira_admin(client: AsyncClient, db: AsyncSession):
    """Token válido dá acesso à própria equipe, não a um papel."""
    await _linha(db, equipe="alpha")
    r = await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_B})
    assert r.status_code == 200
    assert r.json()["criativos"] == []


# ─────────────── cada agência vê a sua ───────────────


async def test_ve_so_a_propria_equipe(client: AsyncClient, db: AsyncSession):
    await _linha(db, equipe="alpha")
    await _linha(db, equipe="beta")
    await _linha(db, equipe=None)  # sem equipe: de ninguém, some pra todos

    a = await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_A})
    b = await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_B})
    assert a.json()["equipe"] == "alpha"
    assert len(a.json()["criativos"]) == 1
    assert len(b.json()["criativos"]) == 1


async def test_linha_sem_equipe_nao_vaza(client: AsyncClient, db: AsyncSession):
    """O `_user_equipes` do router interno trata 'sem equipe' como SEM
    RESTRIÇÃO. Aqui é o oposto: sem equipe não é de ninguém de fora."""
    await _linha(db, equipe=None)
    r = await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_A})
    assert r.json()["criativos"] == []


# ─────────────── a lista branca ───────────────


async def test_nao_vaza_campo_interno(client: AsyncClient, db: AsyncSession):
    await _linha(db, equipe="alpha")
    bruto = (
        await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_A})
    ).text
    # legenda é decisão interna; pushed_dest é o caminho da pasta no MEGA;
    # file_rel é caminho no disco do servidor.
    for proibido in ("legenda", "pushed_dest", "file_rel", "product_id", "Marcas/"):
        assert proibido not in bruto, f"vazou {proibido!r}"


async def test_entregue_e_booleano_nao_caminho(client: AsyncClient, db: AsyncSession):
    """A agência precisa saber que foi entregue, não ONDE o arquivo mora."""
    await _linha(db, equipe="alpha")
    d = (
        await client.get("/api/portal/criativos", headers={"X-Portal-Token": TOK_A})
    ).json()["criativos"][0]
    assert d["entregue"] is False
    assert set(d["arquivos"][0]) == {"id", "nome", "tamanho", "enviado_em"}


# ─────────────── roteiros ───────────────


async def test_roteiros_traz_so_quem_tem_briefing(client: AsyncClient, db: AsyncSession):
    await _linha(db, equipe="alpha", roteiro="cena 1: abre a mala")
    await _linha(db, equipe="alpha", roteiro=None)
    await _linha(db, equipe="alpha", roteiro="   ")  # só espaço não conta

    r = await client.get("/api/portal/roteiros", headers={"X-Portal-Token": TOK_A})
    assert r.status_code == 200
    assert len(r.json()["roteiros"]) == 1


async def test_nao_existe_rota_de_escrever_roteiro(client: AsyncClient, db: AsyncSession):
    """Quem escreve briefing é a equipe interna, no DaVinci."""
    linha = await _linha(db, equipe="alpha")
    for metodo in (client.patch, client.put, client.delete):
        r = await metodo(
            f"/api/portal/criativos/{linha.id}", headers={"X-Portal-Token": TOK_A}
        )
        assert r.status_code in (404, 405)


# ─────────────── upload ───────────────


async def test_envia_arquivo_e_volta_pra_pendente(client: AsyncClient, db: AsyncSession):
    linha = await _linha(db, equipe="alpha", aprovado=True, com_arquivo=False)
    r = await client.post(
        f"/api/portal/criativos/{linha.id}/arquivo",
        headers={"X-Portal-Token": TOK_A},
        files={"files": ("novo.mp4", b"\x00\x00\x00\x18ftypmp42" + b"x" * 500, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["aprovado"] is None, "arquivo novo tem que voltar pra pendente"
    assert d["arquivos"][0]["nome"] == "novo.mp4"


async def test_nao_envia_em_linha_de_outra_equipe(client: AsyncClient, db: AsyncSession):
    """404, não 403: do lado de fora, o que não é seu não existe."""
    linha = await _linha(db, equipe="beta", com_arquivo=False)
    r = await client.post(
        f"/api/portal/criativos/{linha.id}/arquivo",
        headers={"X-Portal-Token": TOK_A},
        files={"files": ("x.mp4", b"abc", "video/mp4")},
    )
    assert r.status_code == 404


async def test_linha_de_outra_equipe_da_404_e_nao_403(client: AsyncClient, db: AsyncSession):
    linha = await _linha(db, equipe="beta")
    r = await client.post(
        f"/api/portal/criativos/{linha.id}/arquivo",
        headers={"X-Portal-Token": TOK_A},
        files={"files": ("x.mp4", b"abc", "video/mp4")},
    )
    assert r.status_code == 404
    assert "fora_da_sua_equipe" not in r.text, "não conte que a linha existe"
