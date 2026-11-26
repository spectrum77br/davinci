"""Categorias de venda (M-75, Harry 25/11/2026) — services/categorias_venda.py e as rotas
/api/produto-cadastros/categorias-venda (Cadastros › Produtos) e /api/segments (aba Segmentos).

São os segmentos filhos das raízes (mesma tabela `segments`), não um cadastro novo. Cobre:
- a lista de Cadastros › Produtos com as categorias de venda (Centro de custo, inativas, ordem
  das colunas) e os centros de custo;
- incluir: entra no FIM do centro (próxima posição, não 0 — nas duas telas); máximo de 5
  contando as inativas ("Este centro de custo já tem 5 categorias de venda."); o mesmo nome em
  outro centro pode, no mesmo não; "Apple"/"Queima de estoque" não são usados por outra;
- renomear: "Apple" e "Queima de estoque" não trocam de nome (nas duas telas);
- excluir: em uso (produto ou conta) recusa com as contagens; só a última do centro sai;
- desativar/reativar fora de ordem (aba Segmentos) é recusado;
- só administrador inclui/renomeia/exclui aqui.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import PricingAccount, PricingPlatform, PricingProduct, Segment, UserRole

pytestmark = pytest.mark.asyncio

ROTA = "/api/produto-cadastros/categorias-venda"
LIMITE_MSG = "Este centro de custo já tem 5 categorias de venda."


@pytest.fixture
async def centros(db):
    """Celular (5), Mala (5, a última "Queima de estoque" inativa) e Eletro (3: espaço livre)."""
    cel = Segment(name="Celular", slug="celular", sort_order=0)
    mala = Segment(name="Mala", slug="mala", sort_order=1)
    ele = Segment(name="Eletro", slug="eletro", sort_order=2)
    db.add_all([cel, mala, ele])
    await db.flush()
    filhos: dict[str, Segment] = {}
    for raiz, nomes in (
        (cel, ["Acessórios", "Diversos", "Regular", "Robusto", "Apple"]),
        (mala, ["Acessórios", '12"', '18" e 20"', '24" acima', "Queima de estoque"]),
        (ele, ["1", "2", "3"]),
    ):
        for i, nome in enumerate(nomes):
            s = Segment(
                parent_id=raiz.id,
                name=nome,
                slug=f"{raiz.slug}-{i}",
                sort_order=i,
                active=nome != "Queima de estoque",
            )
            db.add(s)
            filhos[f"{raiz.name}/{nome}"] = s
    await db.commit()
    return {"Celular": cel, "Mala": mala, "Eletro": ele, **filhos}


async def _nova(client, centros, centro: str, nome: str):
    return await client.post(ROTA, json={"centro_custo_id": str(centros[centro].id), "nome": nome})


async def _admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _filhos(db, raiz: Segment) -> list[tuple[str, int, bool]]:
    """O que está no banco agora (a API grava por outra sessão)."""
    linhas = (
        await db.execute(
            select(Segment)
            .where(Segment.parent_id == raiz.id)
            .order_by(Segment.sort_order)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return [(s.name, s.sort_order, s.active) for s in linhas]


# ================================================================ lista


async def test_lista_mostra_as_categorias_de_venda(client, make_user, auth_as, centros):
    auth_as(await make_user(permissions={"cadastro_produtos": {"view": True}}))
    r = await client.get("/api/produto-cadastros")
    assert r.status_code == 200, r.text
    corpo = r.json()
    vendas = [
        (c["centro_custo"], c["nome"], c["ativa"])
        for c in corpo["cadastros"]
        if c["tipo_chave"] == "categoria_venda"
    ]
    assert vendas == [
        ("Celular", "Acessórios", True),
        ("Celular", "Diversos", True),
        ("Celular", "Regular", True),
        ("Celular", "Robusto", True),
        ("Celular", "Apple", True),
        ("Eletro", "1", True),
        ("Eletro", "2", True),
        ("Eletro", "3", True),
        ("Mala", "Acessórios", True),
        ("Mala", '12"', True),
        ("Mala", '18" e 20"', True),
        ("Mala", '24" acima', True),
        ("Mala", "Queima de estoque", False),
    ]
    # A linha é o próprio segmento (mesmo id da aba Segmentos).
    apple = next(c for c in corpo["cadastros"] if c["nome"] == "Apple")
    assert apple["id"] == str(centros["Celular/Apple"].id)
    assert apple["tipo_nome"] == "Categoria de venda"
    assert [c["nome"] for c in corpo["centros_custo"]] == ["Celular", "Eletro", "Mala"]


# ================================================================ incluir


async def test_nova_entra_no_fim_e_aparece_na_aba_segmentos(
    client, db, make_user, auth_as, centros
):
    await _admin(make_user, auth_as)
    r = await _nova(client, centros, "Eletro", " Novo  4 ")
    assert r.status_code == 201, r.text
    nova = r.json()
    assert (nova["nome"], nova["centro_custo"], nova["ativa"]) == ("Novo 4", "Eletro", True)
    assert await _filhos(db, centros["Eletro"]) == [
        ("1", 0, True),
        ("2", 1, True),
        ("3", 2, True),
        ("Novo 4", 3, True),
    ]
    segs = (await client.get("/api/segments")).json()
    assert any(s["id"] == nova["id"] and s["sort_order"] == 3 for s in segs)
    # O mesmo nome em outro centro pode (Acessórios existe no Celular e na Mala)...
    r = await _nova(client, centros, "Eletro", "Acessórios")
    assert r.status_code == 201, r.text
    # ...e agora o Eletro tem 5.
    r = await _nova(client, centros, "Eletro", "Mais um")
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "categoria_venda_limite", "mensagem": LIMITE_MSG}


async def test_limite_de_5_contando_as_inativas(client, db, make_user, auth_as, centros):
    """O exemplo do pedido: "Mochilas" na Mala (5 com a Queima de estoque inativa) é recusada."""
    await _admin(make_user, auth_as)
    for centro in ("Mala", "Celular"):
        r = await _nova(client, centros, centro, "Mochilas")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["mensagem"] == LIMITE_MSG
    assert len(await _filhos(db, centros["Mala"])) == 5


async def test_nome_repetido_no_centro_e_nomes_fixos(client, make_user, auth_as, centros):
    await _admin(make_user, auth_as)
    eletro = str(centros["Eletro"].id)
    r = await client.post(ROTA, json={"centro_custo_id": eletro, "nome": "2"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "nome_repetido"
    for nome in ("apple", "Queima  de ESTOQUE"):
        r = await client.post(ROTA, json={"centro_custo_id": eletro, "nome": nome})
        assert r.status_code == 409, nome
        assert r.json()["detail"]["code"] == "categoria_venda_nome_reservado"
    # Centro de custo tem de ser uma raiz.
    r = await client.post(ROTA, json={"centro_custo_id": str(centros["Eletro/1"].id), "nome": "X"})
    assert r.status_code == 404


async def test_aba_segmentos_tambem_cria_no_fim(client, db, make_user, auth_as, centros):
    """Antes o filho novo nascia com sort_order 0, igual ao primeiro."""
    await _admin(make_user, auth_as)
    r = await client.post(
        "/api/segments", json={"name": "Quatro", "parent_id": str(centros["Eletro"].id)}
    )
    assert r.status_code == 201, r.text
    assert r.json()["sort_order"] == 3
    r = await client.post(
        "/api/segments", json={"name": "Mochilas", "parent_id": str(centros["Mala"].id)}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["mensagem"] == LIMITE_MSG


async def test_nova_nao_entra_depois_de_inativa(client, make_user, auth_as, centros):
    await _admin(make_user, auth_as)
    r = await client.patch(f"/api/segments/{centros['Eletro/3'].id}", json={"active": False})
    assert r.status_code == 200, r.text
    r = await client.post(ROTA, json={"centro_custo_id": str(centros["Eletro"].id), "nome": "Novo"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "categoria_venda_depois_de_inativa"


# ================================================================ renomear


async def test_renomear_e_nomes_fixos(client, make_user, auth_as, centros):
    await _admin(make_user, auth_as)
    r = await client.patch(f"{ROTA}/{centros['Celular/Diversos'].id}", json={"nome": "Diversos 2"})
    assert r.status_code == 200 and r.json()["nome"] == "Diversos 2"
    # A aba Segmentos (e a aba Contas, que lê de lá) vê o nome novo.
    segs = {s["id"]: s for s in (await client.get("/api/segments")).json()}
    assert segs[str(centros["Celular/Diversos"].id)]["name"] == "Diversos 2"

    for chave in ("Celular/Apple", "Mala/Queima de estoque"):
        r = await client.patch(f"{ROTA}/{centros[chave].id}", json={"nome": "Outro"})
        assert r.status_code == 409, chave
        assert r.json()["detail"]["code"] == "categoria_venda_nome_fixo"
        # Pela aba Segmentos também não.
        r = await client.patch(f"/api/segments/{centros[chave].id}", json={"name": "Outro"})
        assert r.status_code == 409, chave
    r = await client.patch(f"{ROTA}/{centros['Celular/Regular'].id}", json={"nome": "Apple"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "categoria_venda_nome_reservado"
    r = await client.patch(f"{ROTA}/{centros['Celular/Regular'].id}", json={"nome": "robusto"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "nome_repetido"
    # Linha que não é categoria de venda (a raiz) não é achada por aqui.
    r = await client.patch(f"{ROTA}/{centros['Celular'].id}", json={"nome": "X"})
    assert r.status_code == 404


# ================================================================ excluir


async def test_em_uso_nao_sai_e_diz_quantos(client, db, make_user, auth_as, centros):
    dono = await _admin(make_user, auth_as)
    apple = centros["Celular/Apple"]
    db.add_all(
        [
            PricingProduct(user_id=dono.id, sku="i001", name="iPhone", segment_id=apple.id),
            PricingProduct(user_id=dono.id, sku="i002", name="iPad", segment_id=apple.id),
            PricingAccount(
                user_id=dono.id,
                name="loja",
                platform=PricingPlatform.SHOPEE,
                segment_id=centros["Celular"].id,
                slot5_segment_id=apple.id,
            ),
        ]
    )
    await db.commit()

    r = await client.get(f"{ROTA}/{apple.id}/uso")
    assert r.status_code == 200, r.text
    uso = r.json()
    assert (uso["regra"], uso["produtos"], uso["bloqueia"]) == ("categoria_venda", 2, True)
    assert "2 produtos da Tabela de Preços e 1 conta" in uso["mensagem"]
    for rota in (f"{ROTA}/{apple.id}", f"/api/segments/{apple.id}"):
        r = await client.delete(rota)
        assert r.status_code == 409, rota
        d = r.json()["detail"]
        assert (d["code"], d["produtos"], d["contas"]) == ("categoria_venda_em_uso", 2, 1)
    assert ("Apple", 4, True) in await _filhos(db, centros["Celular"])


async def test_so_a_ultima_sai_e_nenhuma_muda_de_coluna(client, db, make_user, auth_as, centros):
    await _admin(make_user, auth_as)
    r = await client.get(f"{ROTA}/{centros['Eletro/2'].id}/uso")
    assert r.json()["bloqueia"] is True and r.json()["produtos"] == 0
    for rota in (f"{ROTA}/{centros['Eletro/2'].id}", f"/api/segments/{centros['Eletro/2'].id}"):
        r = await client.delete(rota)
        assert r.status_code == 409, rota
        assert r.json()["detail"]["code"] == "categoria_venda_do_meio"

    r = await client.get(f"{ROTA}/{centros['Eletro/3'].id}/uso")
    assert r.json() == {
        "regra": "categoria_venda",
        "produtos": 0,
        "bloqueia": False,
        "mensagem": None,
    }
    assert (await client.delete(f"{ROTA}/{centros['Eletro/3'].id}")).status_code == 204
    assert await _filhos(db, centros["Eletro"]) == [("1", 0, True), ("2", 1, True)]
    # A próxima ocupa a posição que ficou livre no fim.
    r = await _nova(client, centros, "Eletro", "Três")
    assert r.status_code == 201
    assert await _filhos(db, centros["Eletro"]) == [
        ("1", 0, True),
        ("2", 1, True),
        ("Três", 2, True),
    ]


# ================================================================ ativar/desativar (Segmentos)


async def test_desativar_e_reativar_so_na_ordem(client, db, make_user, auth_as, centros):
    await _admin(make_user, auth_as)

    async def ativa(chave: str, valor: bool) -> int | str:
        r = await client.patch(f"/api/segments/{centros[chave].id}", json={"active": valor})
        return r.status_code if r.status_code != 409 else r.json()["detail"]["code"]

    assert await ativa("Eletro/2", False) == "categoria_venda_desativar_do_meio"
    assert await ativa("Eletro/3", False) == 200
    assert await ativa("Eletro/2", False) == 200
    assert await ativa("Eletro/3", True) == "categoria_venda_reativar_depois_de_inativa"
    assert await ativa("Eletro/2", True) == 200
    assert await ativa("Eletro/3", True) == 200
    # Mala: a "Queima de estoque" (a última) continua podendo voltar.
    assert await ativa("Mala/Queima de estoque", True) == 200
    # Sem mudar o estado, nada a conferir (a tela manda o campo como está).
    assert await ativa("Eletro/1", True) == 200


# ================================================================ quem pode


async def test_so_administrador_mexe(client, make_user, auth_as, centros):
    auth_as(
        await make_user(
            permissions={"cadastro_produtos": {"view": True, "edit": True, "delete": True}}
        )
    )
    alvo = centros["Eletro/3"].id
    for pedido in (
        client.post(ROTA, json={"centro_custo_id": str(centros["Eletro"].id), "nome": "X"}),
        client.patch(f"{ROTA}/{alvo}", json={"nome": "X"}),
        client.get(f"{ROTA}/{alvo}/uso"),
        client.delete(f"{ROTA}/{alvo}"),
    ):
        r = await pedido
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "admin_only"
