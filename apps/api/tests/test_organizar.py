"""Produtos › Organizar (17/11/2026): esconde da TELA o que é inválido.

Decisões do dono (17/11): só ADM; só esconde (nada é apagado, nada muda no
Bling nem no marketplace, nenhum robô lê os ocultos); a prévia guarda no
servidor os ids que MOSTROU e o "Ocultar" grava só esses, se ainda valem; a
marca só vale enquanto o motivo continua verdadeiro (conferido na leitura).
O "Remover os N mortos" saiu da tela: o endpoint ficou só para ADM e pulando
o que foi ocultado.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    OrganizarOculto,
    OrganizarPrevia,
    Product,
    ProductLink,
    User,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services import organizar
from app.services.advisory_lock import SYNC_NAMESPACE

PADRAO = [
    "vinculo_morto",
    "produto_excluido_sem_anuncio",
    "anuncio_loja_arquivada",
    "anuncio_encerrado",
]
TODAS = list(organizar.MOTIVOS)
COMUM_PERMS = {
    "produtos": {"view": True, "edit": True, "delete": True},
    "anuncios": {"view": True, "edit": True, "delete": True},
}


async def _integracao(db: AsyncSession, user: User, nome: str, *, arquivada: bool = False):
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.ML,
        name=nome,
        status="active",
        credentials=encrypt_json({"access_token": "x"}),
        archived_at=datetime.now(UTC) if arquivada else None,
    )
    db.add(integ)
    await db.flush()
    return integ


def _produto(user: User, sku: str, situacao: str = "A") -> Product:
    return Product(
        user_id=user.id, sku=sku, name=f"Produto {sku}", stock=0, min_stock=0, situacao=situacao
    )


def _link(user, integ, produto, ext: str, *, morto: bool = False) -> ProductLink:
    return ProductLink(
        user_id=user.id,
        product_id=produto.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.ML,
        external_id=ext,
        external_sku=produto.sku,
        morto_desde=datetime.now(UTC) if morto else None,
        morto_motivo="anúncio encerrado no Mercado Livre" if morto else None,
    )


def _anuncio(user, integ, ext: str, *, status=ListingStatus.ACTIVE, produto=None) -> Listing:
    return Listing(
        user_id=user.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.ML,
        external_id=ext,
        sku=produto.sku if produto else None,
        title=f"Anúncio {ext}",
        status=status,
        product_id=produto.id if produto else None,
    )


async def _cenario(db: AsyncSession, dono: User) -> dict:
    """Um item por chave (e os válidos que não podem sumir).

    vinculo_morto: MLB2 (de pA) e MLB3 (de pE1); MLB6 é morto de loja
    arquivada (a tela já não mostra). produto_excluido_sem_anuncio: pE1 e pE3
    (o vínculo vivo dele é de loja arquivada). produto_excluido_com_anuncio:
    pE2. produto_inativo: pI. anuncio_loja_arquivada: L1. anuncio_encerrado: L2
    e L5 (L5 também é de produto E: cai só em encerrado). anuncio_produto_excluido:
    L3. Válidos: pA, MLB1, MLB4, L4.
    """
    ativa = await _integracao(db, dono, "loja-ativa")
    arquivada = await _integracao(db, dono, "loja-arquivada", arquivada=True)
    p = {
        "pA": _produto(dono, "org-a"),
        "pE1": _produto(dono, "org-e1", "E"),
        "pE2": _produto(dono, "org-e2", "E"),
        "pE3": _produto(dono, "org-e3", "E"),
        "pI": _produto(dono, "org-i", "I"),
    }
    db.add_all(p.values())
    await db.flush()
    links = {
        "MLB1": _link(dono, ativa, p["pA"], "MLB1"),
        "MLB2": _link(dono, ativa, p["pA"], "MLB2", morto=True),
        "MLB3": _link(dono, ativa, p["pE1"], "MLB3", morto=True),
        "MLB4": _link(dono, ativa, p["pE2"], "MLB4"),
        "MLB5": _link(dono, arquivada, p["pE3"], "MLB5"),
        "MLB6": _link(dono, arquivada, p["pA"], "MLB6", morto=True),
    }
    db.add_all(links.values())
    anuncios = {
        "L1": _anuncio(dono, arquivada, "L1"),
        "L2": _anuncio(dono, ativa, "L2", status=ListingStatus.CLOSED),
        "L3": _anuncio(dono, ativa, "L3", produto=p["pE2"]),
        "L4": _anuncio(dono, ativa, "L4", produto=p["pA"]),
        "L5": _anuncio(dono, ativa, "L5", status=ListingStatus.CLOSED, produto=p["pE2"]),
    }
    db.add_all(anuncios.values())
    await db.commit()
    return {"ativa": ativa, "arquivada": arquivada, **p, **links, **anuncios}


async def _admin(make_user, auth_as) -> User:
    admin = await make_user(role=UserRole.ADMIN)
    admin.name = "Eduardo"
    auth_as(admin)
    return admin


def _qtd(previa: dict) -> dict:
    return {c["chave"]: c["quantidade"] for c in previa["chaves"]}


async def _aplicar(client, chaves, previa=None):
    previa = previa or (await client.get("/api/produtos/organizar/previa")).json()
    return await client.post(
        "/api/produtos/organizar/aplicar", json={"token": previa["token"], "chaves": chaves}
    )


async def _skus(client, **params) -> list[str]:
    r = await client.get("/api/products", params={"page_size": 200, **params})
    assert r.status_code == 200, r.text
    return sorted(i["sku"] for i in r.json()["items"])


# ── permissão ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_so_admin_nos_4_endpoints_e_no_remover_mortos(client, make_user, auth_as):
    comum = await make_user(permissions=COMUM_PERMS)
    auth_as(comum)
    respostas = [
        await client.get("/api/produtos/organizar/previa"),
        await client.get("/api/produtos/organizar/itens", params={"chave": "vinculo_morto"}),
        await client.post(
            "/api/produtos/organizar/aplicar", json={"token": str(uuid.uuid4()), "chaves": PADRAO}
        ),
        await client.post("/api/produtos/organizar/reexibir", json={"chave": "vinculo_morto"}),
        # decisão 3: o botão saiu da tela; o endpoint ficou só para ADM
        await client.post("/api/product-links/remover-mortos"),
    ]
    for r in respostas:
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == {"code": "admin_only"}


# ── prévia e lista ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_previa_conta_por_chave_e_guarda_os_ids(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    c = await _cenario(db, admin)
    r = await client.get("/api/produtos/organizar/previa")
    assert r.status_code == 200, r.text
    previa = r.json()
    assert _qtd(previa) == {
        "vinculo_morto": 2,
        "produto_excluido_sem_anuncio": 2,
        "produto_excluido_com_anuncio": 1,
        "produto_inativo": 1,
        "anuncio_loja_arquivada": 1,
        "anuncio_encerrado": 2,
        "anuncio_produto_excluido": 1,
    }
    padrao = [ch["chave"] for ch in previa["chaves"] if ch["marcado_por_padrao"]]
    assert padrao == PADRAO
    assert all(ch["rotulo"] and ch["explicacao"] and ch["tabela"] for ch in previa["chaves"])
    assert previa["ocultos"] == {"total": 0, "por_chave": dict.fromkeys(TODAS, 0)}
    assert previa["lotes"] == []
    # os ids mostrados ficam no servidor (30 min), por chave
    guardada = await db.get(OrganizarPrevia, uuid.UUID(previa["token"]))
    assert guardada.criado_por == admin.id
    assert sorted(guardada.ids["vinculo_morto"]) == sorted([str(c["MLB2"].id), str(c["MLB3"].id)])
    assert sorted(guardada.ids["anuncio_encerrado"]) == sorted([str(c["L2"].id), str(c["L5"].id)])
    assert guardada.expira_em - datetime.now(UTC) > timedelta(minutes=29)


@pytest.mark.asyncio
async def test_previa_apaga_as_vencidas(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    velha = OrganizarPrevia(
        criado_por=admin.id, expira_em=datetime.now(UTC) - timedelta(minutes=1), ids={}
    )
    db.add(velha)
    await db.commit()
    token_velho = velha.token
    await client.get("/api/produtos/organizar/previa")
    tokens = (await db.execute(select(OrganizarPrevia.token))).scalars().all()
    assert token_velho not in tokens and len(tokens) == 1


@pytest.mark.asyncio
async def test_itens_paginados_de_100(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    c = await _cenario(db, admin)
    db.add_all([_produto(admin, f"org-inativo-{i:03d}", "I") for i in range(104)])
    await db.commit()
    p1 = (
        await client.get("/api/produtos/organizar/itens", params={"chave": "produto_inativo"})
    ).json()
    assert p1["total"] == 105 and p1["por_pagina"] == 100 and p1["pagina"] == 1
    assert len(p1["itens"]) == 100
    p2 = (
        await client.get(
            "/api/produtos/organizar/itens", params={"chave": "produto_inativo", "pagina": 2}
        )
    ).json()
    assert len(p2["itens"]) == 5
    assert {i["sku"] for i in p1["itens"]} & {i["sku"] for i in p2["itens"]} == set()
    # os campos de exibição do vínculo morto
    mortos = (
        await client.get("/api/produtos/organizar/itens", params={"chave": "vinculo_morto"})
    ).json()["itens"]
    mlb2 = next(i for i in mortos if i["anuncio"] == "MLB2")
    assert mlb2 == {
        "registro_id": str(c["MLB2"].id),
        "sku": "org-a",
        "nome": "Produto org-a",
        "conta": "loja-ativa",
        "anuncio": "MLB2",
        "variacao": None,
        "sku_anuncio": "org-a",
        "motivo": "anúncio encerrado no Mercado Livre",
        "data": mlb2["data"],
    }
    assert mlb2["data"]
    r = await client.get("/api/produtos/organizar/itens", params={"chave": "nao_existe"})
    assert r.status_code == 400 and r.json()["detail"] == {"code": "chave_invalida"}


# ── aplicar: as listas da tela ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aplicar_o_padrao_tira_das_telas_e_sem_parametro_fica_igual(
    client, db, make_user, auth_as
):
    admin = await _admin(make_user, auth_as)
    await _cenario(db, admin)
    todos = ["org-a", "org-e1", "org-e2", "org-e3", "org-i"]
    antes = (await client.get("/api/products", params={"page_size": 200})).json()
    assert sorted(i["sku"] for i in antes["items"]) == todos
    assert antes["saude"]["mortos"] == 2
    deps_antes = (await client.get("/api/products/departamentos")).json()
    assert sum(deps_antes["produtos"].values()) == 5
    assert sum(deps_antes["anuncios"].values()) == 5
    lst_antes = (await client.get("/api/listings")).json()
    assert lst_antes["total"] == 5 and lst_antes["resumo"]["total"] == 5

    r = await _aplicar(client, PADRAO)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ocultados"] == 7
    assert out["por_chave"] == {
        "vinculo_morto": 2,
        "produto_excluido_sem_anuncio": 2,
        "anuncio_loja_arquivada": 1,
        "anuncio_encerrado": 2,
    }
    assert out["sairam_desde_previa"] == 0
    assert uuid.UUID(out["lote_id"])

    # Produtos com ocultos=esconder (o que a tela pede): lista, total, vínculos
    # da linha, faixa de saúde e o filtro ?vinculos=mortos
    tela = (await client.get("/api/products", params={"ocultos": "esconder"})).json()
    assert sorted(i["sku"] for i in tela["items"]) == ["org-a", "org-e2", "org-i"]
    assert tela["total"] == 3
    linha_a = next(i for i in tela["items"] if i["sku"] == "org-a")
    assert sorted(lk["external_id"] for lk in linha_a["links"]) == ["MLB1"]
    assert tela["saude"]["mortos"] == 0
    assert await _skus(client, ocultos="esconder", vinculos="mortos") == []
    # ocultos=so: só os escondidos
    assert await _skus(client, ocultos="so") == ["org-e1", "org-e3"]
    # sem o parâmetro (legendas e o seletor da aba Anúncios): igual a antes
    igual = (await client.get("/api/products", params={"page_size": 200})).json()
    assert sorted(i["sku"] for i in igual["items"]) == todos
    assert igual["total"] == 5 and igual["saude"]["mortos"] == 2
    linha_a = next(i for i in igual["items"] if i["sku"] == "org-a")
    assert sorted(lk["external_id"] for lk in linha_a["links"]) == ["MLB1", "MLB2"]
    assert await _skus(client, vinculos="mortos") == ["org-a", "org-e1"]
    # números das abas e aba Anúncios (lista, total e cartões)
    deps = (await client.get("/api/products/departamentos")).json()
    assert sum(deps["produtos"].values()) == 3
    assert sum(deps["anuncios"].values()) == 2
    lst = (await client.get("/api/listings")).json()
    assert sorted(i["external_id"] for i in lst["items"]) == ["L3", "L4"]
    assert lst["total"] == 2 and lst["resumo"]["total"] == 2

    # nada foi apagado nem alterado
    assert len((await db.execute(select(Product.id))).all()) == 5
    assert len((await db.execute(select(ProductLink.id))).all()) == 6
    assert len((await db.execute(select(Listing.id))).all()) == 5

    # a prévia de depois: o que foi oculto sai das chaves e entra em ocultos/lotes
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    assert _qtd(previa) == {
        "vinculo_morto": 0,
        "produto_excluido_sem_anuncio": 0,
        "produto_excluido_com_anuncio": 1,
        "produto_inativo": 1,
        "anuncio_loja_arquivada": 0,
        "anuncio_encerrado": 0,
        "anuncio_produto_excluido": 1,
    }
    assert previa["ocultos"]["total"] == 7
    assert previa["ocultos"]["por_chave"]["anuncio_encerrado"] == 2
    [lote] = previa["lotes"]
    assert lote["lote_id"] == out["lote_id"]
    assert lote["quantidade"] == 7 and lote["oculto_por_nome"] == "Eduardo"
    assert lote["por_chave"] == out["por_chave"]
    ocultos = (
        await client.get(
            "/api/produtos/organizar/itens", params={"chave": "vinculo_morto", "ocultos": "true"}
        )
    ).json()
    assert ocultos["total"] == 2
    assert all(i["marcador_id"] and i["oculto_em"] for i in ocultos["itens"])
    assert {i["anuncio"] for i in ocultos["itens"]} == {"MLB2", "MLB3"}
    sem_marca = (
        await client.get("/api/produtos/organizar/itens", params={"chave": "vinculo_morto"})
    ).json()
    assert sem_marca["total"] == 0


@pytest.mark.asyncio
async def test_aplicar_so_o_que_a_previa_mostrou_e_ainda_vale(client, db, make_user, auth_as):
    """O ADM leva minutos olhando a lista e o conjunto muda (vínculos morrem a
    toda hora): o "Ocultar" grava só o que foi mostrado e ainda vale; o que
    saiu é contado; o que entrou fica para a próxima prévia."""
    admin = await _admin(make_user, auth_as)
    c = await _cenario(db, admin)
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    # depois da prévia: pI voltou a ativo no Bling e outro produto ficou inativo
    c["pI"].situacao = "A"
    novo = _produto(admin, "org-i2", "I")
    db.add(novo)
    await db.commit()
    out = (await _aplicar(client, ["produto_inativo"], previa)).json()
    assert out == {
        "lote_id": None,
        "ocultados": 0,
        "por_chave": {"produto_inativo": 0},
        "sairam_desde_previa": 1,
    }
    assert "org-i2" in await _skus(client, ocultos="esconder")
    marcas = (await db.execute(select(OrganizarOculto))).scalars().all()
    assert marcas == []


@pytest.mark.asyncio
async def test_previa_vencida_de_outro_ou_invalida_da_409(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    await _cenario(db, admin)
    for token in ("nao-e-uuid", str(uuid.uuid4())):
        r = await client.post(
            "/api/produtos/organizar/aplicar", json={"token": token, "chaves": PADRAO}
        )
        assert r.status_code == 409 and r.json()["detail"] == {"code": "previa_expirada"}
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    # a prévia é de quem a abriu
    outro = await make_user(role=UserRole.ADMIN)
    auth_as(outro)
    r = await _aplicar(client, PADRAO, previa)
    assert r.status_code == 409 and r.json()["detail"] == {"code": "previa_expirada"}
    auth_as(admin)
    await db.execute(
        update(OrganizarPrevia).values(expira_em=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()
    r = await _aplicar(client, PADRAO, previa)
    assert r.status_code == 409 and r.json()["detail"] == {"code": "previa_expirada"}
    # chave desconhecida ou nenhuma
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    r = await _aplicar(client, ["apagar_tudo"], previa)
    assert r.status_code == 400 and r.json()["detail"] == {"code": "chave_invalida"}
    r = await _aplicar(client, [], previa)
    assert r.status_code == 400 and r.json()["detail"] == {"code": "sem_chaves"}
    assert (await db.execute(select(OrganizarOculto.id))).all() == []


@pytest.mark.asyncio
async def test_aplicar_duas_vezes_nao_duplica(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    await _cenario(db, admin)
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    primeira = (await _aplicar(client, TODAS, previa)).json()
    assert primeira["ocultados"] == 10
    segunda = (await _aplicar(client, TODAS, previa)).json()
    assert segunda["ocultados"] == 0 and segunda["lote_id"] is None
    # todos os mostrados já estão ocultos: saíram da lista
    assert segunda["sairam_desde_previa"] == 10
    marcas = (await db.execute(select(OrganizarOculto))).scalars().all()
    assert len(marcas) == 10
    assert len({(m.tabela, m.registro_id) for m in marcas}) == 10
    assert {m.motivo for m in marcas} == set(TODAS)
    assert all(m.oculto_por == admin.id and m.reexibido_em is None for m in marcas)


@pytest.mark.asyncio
async def test_dois_cliques_ao_mesmo_tempo_o_segundo_recebe_409(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    await _cenario(db, admin)
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    import app.db as app_db

    async with app_db.SessionLocal() as outra:
        # a outra "requisição" segura a trava 'orgz' (transação aberta)
        await outra.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": organizar.ORGANIZAR_LOCK_KEY},
        )
        r = await _aplicar(client, PADRAO, previa)
        assert r.status_code == 409
        assert r.json()["detail"] == {"code": "organizar_em_andamento"}
        await outra.rollback()
    r = await _aplicar(client, PADRAO, previa)
    assert r.status_code == 200 and r.json()["ocultados"] == 7


# ── mostrar de novo ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reexibir_por_item_por_chave_e_por_lote(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    c = await _cenario(db, admin)
    lote = (await _aplicar(client, TODAS)).json()["lote_id"]
    assert await _skus(client, ocultos="esconder") == ["org-a"]

    r = await client.post("/api/produtos/organizar/reexibir", json={})
    assert r.status_code == 400 and r.json()["detail"] == {"code": "sem_filtro"}
    r = await client.post("/api/produtos/organizar/reexibir", json={"chave": "xx"})
    assert r.status_code == 400 and r.json()["detail"] == {"code": "chave_invalida"}

    # por item (o "Mostrar de novo" da aba Ocultos)
    itens = (
        await client.get(
            "/api/produtos/organizar/itens", params={"chave": "produto_inativo", "ocultos": "true"}
        )
    ).json()["itens"]
    assert [i["sku"] for i in itens] == ["org-i"]
    r = await client.post(
        "/api/produtos/organizar/reexibir", json={"marcador_ids": [itens[0]["marcador_id"]]}
    )
    assert r.json() == {"reexibidos": 1}
    assert await _skus(client, ocultos="esconder") == ["org-a", "org-i"]

    # por chave
    r = await client.post("/api/produtos/organizar/reexibir", json={"chave": "anuncio_encerrado"})
    assert r.json() == {"reexibidos": 2}
    lst = (await client.get("/api/listings")).json()
    assert sorted(i["external_id"] for i in lst["items"]) == ["L2", "L4", "L5"]

    # por lote: o resto
    r = await client.post("/api/produtos/organizar/reexibir", json={"lote_id": lote})
    assert r.json() == {"reexibidos": 7}
    todos = ["org-a", "org-e1", "org-e2", "org-e3", "org-i"]
    assert await _skus(client, ocultos="esconder") == todos
    assert (await client.get("/api/listings")).json()["total"] == 5
    # nada apagado: a marca fica com quem e quando mostrou de novo
    marcas = (await db.execute(select(OrganizarOculto))).scalars().all()
    assert len(marcas) == 10
    assert all(m.reexibido_em is not None and m.reexibido_por == admin.id for m in marcas)
    assert c["pI"].id in {m.registro_id for m in marcas}
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    assert previa["ocultos"]["total"] == 0 and previa["lotes"] == []


# ── a marca só vale enquanto o motivo vale ───────────────────────────────────


@pytest.mark.asyncio
async def test_volta_sozinho_quando_o_motivo_deixa_de_valer(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    c = await _cenario(db, admin)
    await _aplicar(client, TODAS)
    assert await _skus(client, ocultos="esconder") == ["org-a"]
    assert (await client.get("/api/listings")).json()["total"] == 1  # só L4

    # produto E que volta a A no Bling
    c["pE1"].situacao = "A"
    # vínculo morto que revive
    c["MLB2"].morto_desde = None
    c["MLB2"].morto_motivo = None
    # anúncio que o VINCULAR moveu para um produto A
    c["L3"].product_id = c["pA"].id
    await db.commit()
    assert await _skus(client, ocultos="esconder") == ["org-a", "org-e1"]
    tela = (await client.get("/api/products", params={"ocultos": "esconder"})).json()
    linha_a = next(i for i in tela["items"] if i["sku"] == "org-a")
    assert sorted(lk["external_id"] for lk in linha_a["links"]) == ["MLB1", "MLB2"]
    # o MLB3 (de pE1) continua morto: a marca dele continua valendo
    linha_e1 = next(i for i in tela["items"] if i["sku"] == "org-e1")
    assert linha_e1["links"] == []
    lst = (await client.get("/api/listings")).json()
    assert sorted(i["external_id"] for i in lst["items"]) == ["L3", "L4"]

    # loja desarquivada: L1 volta. O pE3 (excluído, oculto "sem anúncio")
    # ganha o vínculo vivo dela, mas continua EXCLUÍDO: a marca vale pela
    # família (inválido por qualquer chave da tabela) e ele fica escondido.
    c["arquivada"].archived_at = None
    await db.commit()
    lst = (await client.get("/api/listings")).json()
    assert sorted(i["external_id"] for i in lst["items"]) == ["L1", "L3", "L4"]
    assert "org-e3" not in await _skus(client, ocultos="esconder")
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    assert _qtd(previa)["produto_excluido_com_anuncio"] == 0
    assert previa["ocultos"]["por_chave"]["produto_excluido_sem_anuncio"] == 1  # o pE3


@pytest.mark.asyncio
async def test_mais_invalido_continua_escondido(client, db, make_user, auth_as):
    """A marca não vence quando o item fica MAIS inválido (muda de chave dentro
    da mesma tabela): inativo que vira excluído, anúncio de produto E que é
    encerrado, excluído com anúncio que perde o último vínculo vivo e
    encerrado de loja que foi arquivada depois."""
    admin = await _admin(make_user, auth_as)
    c = await _cenario(db, admin)
    assert (await _aplicar(client, TODAS)).status_code == 200
    assert await _skus(client, ocultos="esconder") == ["org-a"]
    c["pI"].situacao = "E"
    c["L3"].status = ListingStatus.CLOSED
    c["MLB4"].morto_desde = datetime.now(UTC)
    c["MLB4"].morto_motivo = "anúncio encerrado"
    await db.commit()
    assert await _skus(client, ocultos="esconder") == ["org-a"]
    lst = (await client.get("/api/listings")).json()
    assert [i["external_id"] for i in lst["items"]] == ["L4"]
    # a loja ativa é arquivada (conta suspensa): os encerrados L2/L5 continuam fora
    c["ativa"].archived_at = datetime.now(UTC)
    await db.commit()
    lst = (await client.get("/api/listings")).json()
    assert "L2" not in {i["external_id"] for i in lst["items"]}
    assert "L5" not in {i["external_id"] for i in lst["items"]}
    deps = (await client.get("/api/products/departamentos")).json()
    assert sum(deps["anuncios"].values()) == 1  # só o L4
    # nenhuma marca foi fechada: a prévia ainda vê os 10 escondidos
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    assert previa["ocultos"]["total"] == 10


@pytest.mark.asyncio
async def test_marca_vencida_fecha_e_nao_volta_a_esconder(client, db, make_user, auth_as):
    """Item que voltou a valer reaparece; a próxima prévia fecha a marca
    vencida (sem autor). Se ele ficar inválido de novo, NÃO some sozinho:
    aparece de novo como candidato para o ADM decidir."""
    admin = await _admin(make_user, auth_as)
    c = await _cenario(db, admin)
    assert (await _aplicar(client, ["produto_inativo"])).json()["ocultados"] == 1
    c["pI"].situacao = "A"
    await db.commit()
    assert "org-i" in await _skus(client, ocultos="esconder")
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    assert previa["ocultos"]["total"] == 0 and previa["lotes"] == []
    marca = (await db.execute(select(OrganizarOculto))).scalar_one()
    await db.refresh(marca)
    assert marca.reexibido_em is not None and marca.reexibido_por is None
    c["pI"].situacao = "I"
    await db.commit()
    assert "org-i" in await _skus(client, ocultos="esconder")
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    assert _qtd(previa)["produto_inativo"] == 1
    # ocultar de novo grava marca nova
    out = (await _aplicar(client, ["produto_inativo"], previa)).json()
    assert out["ocultados"] == 1
    assert "org-i" not in await _skus(client, ocultos="esconder")


@pytest.mark.asyncio
async def test_morto_de_novo_ou_loja_arquivada_de_novo_nao_reativa_a_marca(
    client, db, make_user, auth_as
):
    """Sem prévia no meio: vínculo que reviveu e morreu de novo, e loja
    desarquivada e arquivada de novo, voltam para a tela (a data é de depois
    da marca)."""
    admin = await _admin(make_user, auth_as)
    c = await _cenario(db, admin)
    await _aplicar(client, ["vinculo_morto", "anuncio_loja_arquivada"])
    c["MLB2"].morto_desde = None
    c["arquivada"].archived_at = None
    await db.commit()
    c["MLB2"].morto_desde = datetime.now(UTC) + timedelta(seconds=1)
    c["arquivada"].archived_at = datetime.now(UTC) + timedelta(seconds=1)
    await db.commit()
    tela = (await client.get("/api/products", params={"ocultos": "esconder"})).json()
    linha_a = next(i for i in tela["items"] if i["sku"] == "org-a")
    assert "MLB2" in {lk["external_id"] for lk in linha_a["links"]}
    assert tela["saude"]["mortos"] == 1  # o MLB2 (o MLB3 continua oculto)
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    assert _qtd(previa)["vinculo_morto"] == 1
    assert _qtd(previa)["anuncio_loja_arquivada"] == 1
    assert previa["ocultos"]["por_chave"]["vinculo_morto"] == 1  # o MLB3


@pytest.mark.asyncio
async def test_reexibir_conta_so_o_que_estava_escondido(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    c = await _cenario(db, admin)
    lote = (await _aplicar(client, ["produto_inativo", "produto_excluido_sem_anuncio"])).json()
    assert lote["ocultados"] == 3
    c["pI"].situacao = "A"  # voltou sozinho: a marca dele venceu
    await db.commit()
    r = await client.post("/api/produtos/organizar/reexibir", json={"lote_id": lote["lote_id"]})
    assert r.json() == {"reexibidos": 2}


@pytest.mark.asyncio
async def test_ver_lista_com_token_mostra_o_que_a_previa_guardou(client, db, make_user, auth_as):
    """O "Ver lista" com o token bate com o botão "Ocultar N": o vínculo que
    morreu depois da prévia fica para a próxima."""
    admin = await _admin(make_user, auth_as)
    c = await _cenario(db, admin)
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    assert _qtd(previa)["vinculo_morto"] == 2
    db.add(_link(admin, c["ativa"], c["pA"], "MLB9", morto=True))
    await db.commit()
    params = {"chave": "vinculo_morto", "token": previa["token"]}
    lista = (await client.get("/api/produtos/organizar/itens", params=params)).json()
    assert lista["total"] == 2
    assert {i["anuncio"] for i in lista["itens"]} == {"MLB2", "MLB3"}
    # sem token: o conjunto de agora
    agora = (
        await client.get("/api/produtos/organizar/itens", params={"chave": "vinculo_morto"})
    ).json()
    assert agora["total"] == 3
    out = (await _aplicar(client, ["vinculo_morto"], previa)).json()
    assert out["ocultados"] == 2 and out["sairam_desde_previa"] == 0
    # token de outro ou vencido: 409 (a tela relê a prévia)
    r = await client.get(
        "/api/produtos/organizar/itens",
        params={"chave": "vinculo_morto", "token": str(uuid.uuid4())},
    )
    assert r.status_code == 409 and r.json()["detail"] == {"code": "previa_expirada"}


# ── vínculos mortos: só o remover-mortos (ADM) pula o oculto ─────────────────


@pytest.mark.asyncio
async def test_remover_mortos_pula_o_oculto_e_a_limpeza_de_30_dias_nao(
    client, db, make_user, auth_as
):
    from app.services.vinculo_saude import apagar_mortos_em_lotes

    admin = await _admin(make_user, auth_as)
    c = await _cenario(db, admin)
    # só o MLB2 com marca própria; o MLB3 fica protegido porque o produto
    # dele (pE1) foi oculto; o MLB6 (loja arquivada) não tem marca nenhuma
    previa = (await client.get("/api/produtos/organizar/previa")).json()
    token = previa["token"]
    guardada = await db.get(OrganizarPrevia, uuid.UUID(token))
    await db.execute(
        update(OrganizarPrevia)
        .where(OrganizarPrevia.token == guardada.token)
        .values(ids={**guardada.ids, "vinculo_morto": [str(c["MLB2"].id)]})
    )
    await db.commit()
    out = (
        await _aplicar(client, ["vinculo_morto", "produto_excluido_sem_anuncio"], previa)
    ).json()
    assert out["por_chave"] == {"vinculo_morto": 1, "produto_excluido_sem_anuncio": 2}

    # o "Remover os N mortos" (só ADM) conta só o que a tela mostra e pode
    # sair, e manda o job pular os ocultos
    agendados = []

    class _Pool:
        async def enqueue_job(self, *a, **k):
            agendados.append((a, k))

    async def _pool():
        return _Pool()

    with patch("app.routers.products.get_arq_pool", _pool):
        r = await client.post("/api/product-links/remover-mortos")
    assert r.status_code == 200
    assert r.json() == {"agendado": True, "quantidade": 0}
    assert agendados == []

    # o job do botão (pular_ocultos=True): apaga só o MLB6; a marca protege
    # mesmo vencida (a loja do MLB2 é arquivada depois de ocultar)
    c["ativa"].archived_at = datetime.now(UTC)
    await db.commit()
    assert await apagar_mortos_em_lotes(pausa=0, pular_ocultos=True) == 1
    restantes = set((await db.execute(select(ProductLink.external_id))).scalars().all())
    assert restantes == {"MLB1", "MLB2", "MLB3", "MLB4", "MLB5"}
    # a limpeza de 30 dias da varredura NÃO olha o Organizar: apaga o morto
    # oculto como se a marca não existisse
    assert await apagar_mortos_em_lotes(pausa=0) == 2
    restantes = set((await db.execute(select(ProductLink.external_id))).scalars().all())
    assert restantes == {"MLB1", "MLB4", "MLB5"}


@pytest.mark.asyncio
async def test_remover_mortos_agenda_com_pular_ocultos(client, db, make_user, auth_as):
    admin = await _admin(make_user, auth_as)
    await _cenario(db, admin)
    agendados = []

    class _Pool:
        async def enqueue_job(self, *a, **k):
            agendados.append((a, k))

    async def _pool():
        return _Pool()

    with patch("app.routers.products.get_arq_pool", _pool):
        r = await client.post("/api/product-links/remover-mortos")
    assert r.json() == {"agendado": True, "quantidade": 2}
    [(args, kw)] = agendados
    assert args[0] == "remover_vinculos_mortos_run" and kw["pular_ocultos"] is True


@pytest.mark.asyncio
async def test_varredura_apaga_o_morto_oculto_e_o_flex_volta_a_poder_ligar(db, make_user):
    """Achado da revisão: segurar o morto oculto deixava o anúncio Shopee
    inelegível no Flex para sempre (montar_anuncios lê todo morto como
    VINCULO_MORTO). A limpeza de 30 dias apaga o morto mesmo oculto."""
    from app.services import flex_motor
    from app.services.vinculo_saude import apagar_mortos_em_lotes

    dono = await make_user()
    integ = Integration(
        user_id=dono.id, platform=IntegrationPlatform.SHOPEE, name="Shopee org",
        credentials=encrypt_json({"access_token": "x"}),
    )
    p = Product(user_id=dono.id, sku="org1.sp", name="Org", situacao="A")
    db.add_all([integ, p])
    await db.commit()
    vivo = ProductLink(
        user_id=dono.id, product_id=p.id, integration_id=integ.id,
        platform=IntegrationPlatform.SHOPEE, external_id="111", variation_id="1",
    )
    morto = ProductLink(
        user_id=dono.id, product_id=p.id, integration_id=integ.id,
        platform=IntegrationPlatform.SHOPEE, external_id="111", variation_id="2",
        morto_desde=datetime.now(UTC) - timedelta(days=40),
        morto_motivo="variação excluída na Shopee",
    )
    db.add_all([vivo, morto])
    await db.commit()
    db.add(OrganizarOculto(tabela="product_links", registro_id=morto.id,
                           motivo="vinculo_morto", lote_id=uuid.uuid4(), oculto_por=dono.id))
    await db.commit()

    apagados = await apagar_mortos_em_lotes(
        antes_de=datetime.now(UTC) - timedelta(days=30), pausa=0
    )
    assert apagados == 1
    anuncios = await flex_motor.montar_anuncios(db, {integ.id: integ})
    vinculos = [(v.ref, v.vinculo) for a in anuncios for v in a.variacoes]
    assert ("2", flex_motor.VINCULO_MORTO) not in vinculos


# ── nenhum robô lê os ocultos ─────────────────────────────────────────────────

_APP = Path(__file__).resolve().parent.parent / "app"
# Quem pode importar services.organizar: só as leituras da TELA. A limpeza de
# vínculos mortos não importa (o remover-mortos de ADM usa a marca pelo model,
# e a limpeza de 30 dias nem olha).
_PODE_IMPORTAR = {
    "routers/organizar.py",
    "routers/products.py",
    "routers/listings.py",
}
_ROBOS = (
    "worker.py",
    "services/vinculo_saude.py",
    "services/sync_orchestrator.py",
    "services/auto_link.py",
    "services/listings_import.py",
    "services/flex_motor.py",
    "services/vigia_flex.py",
    "services/flex_gtin.py",
    "services/bling_orders.py",
    "services/estoque_familia.py",
    "services/prioridade_estoque.py",
    "services/marketing/legenda.py",
    "services/audit/runner.py",
    "services/refresh_bling_stock.py",
    "services/bling_produtos_novos.py",
)


def _importa_organizar(caminho: Path) -> bool:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and no.module:
            if no.module == "app.services.organizar" or no.module.startswith(
                "app.services.organizar."
            ):
                return True
            if no.module == "app.services" and any(a.name == "organizar" for a in no.names):
                return True
        if isinstance(no, ast.Import) and any(
            a.name.startswith("app.services.organizar") for a in no.names
        ):
            return True
    return False


def test_nenhum_robo_importa_services_organizar():
    importadores = {
        str(p.relative_to(_APP))
        for p in _APP.rglob("*.py")
        if _importa_organizar(p)
    }
    assert importadores <= _PODE_IMPORTAR, importadores - _PODE_IMPORTAR
    assert importadores >= {"routers/organizar.py", "routers/products.py", "routers/listings.py"}
    for robo in _ROBOS:
        caminho = _APP / robo
        if caminho.exists():
            assert not _importa_organizar(caminho), robo
    for pasta in ("services/pricing", "services/atendimento", "services/dashboard"):
        for p in (_APP / pasta).rglob("*.py") if (_APP / pasta).exists() else ():
            assert not _importa_organizar(p), p
    # e o SQL cru: a tabela só aparece no model, no serviço e na migration
    citam = {
        str(p.relative_to(_APP))
        for p in _APP.rglob("*.py")
        if "organizar_ocultos" in p.read_text(encoding="utf-8")
    }
    assert citam <= {"models/organizar.py", "services/organizar.py", "historico/sql.py"}, citam
