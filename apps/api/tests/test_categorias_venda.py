"""Categorias de venda (M-75, Harry 25/11/2026) — services/categorias_venda.py e as rotas
/api/produto-cadastros/categorias-venda (Cadastros › Produtos) e /api/segments (aba Segmentos).

São os segmentos filhos das raízes (mesma tabela `segments`), não um cadastro novo. Cobre:
- a lista de Cadastros › Produtos com as categorias de venda (Centro de custo, inativas, ordem
  das colunas) e os centros de custo;
- incluir: entra no FIM do centro (próxima posição, não 0 — nas duas telas); máximo de 5
  contando as inativas ("Este centro de custo já tem 5 categorias de venda."); o mesmo nome em
  outro centro pode, no mesmo não; "Apple"/"Queima de estoque" não são usados por outra;
- renomear: "Apple" e "Queima de estoque" não trocam de nome (nas duas telas);
- excluir: em uso na tabela dela (produto da Tabela de Preços ou conta apontando DIRETO para
  ela) pede o destino (M-90) — outra ativa do mesmo centro — e transfere e exclui; sem destino,
  recusa com as contagens; produtos da tela Produtos não travam (vão junto ou ficam sem
  segmento); os vínculos slot1..5 das contas não contam e são limpos; pela aba Segmentos só a
  última do centro sai; pela lixeira de Cadastros › Produtos (M-91) a do meio também — as
  seguintes sobem uma posição e margem/frete/vínculo de cada conta do centro (kit, par do ML,
  catálogo) andam junto: cada categoria que fica guarda a mesma margem/frete e o preço de quem
  não foi transferido não muda (conferido na grade de preços); outros centros não mudam;
- incluir na posição N preenche o slot{N} vazio das contas daquele centro (nas duas telas);
- o menu de centros de custo (sem o Catálogo) e a ordem Celular, Eletro, Mala, Catálogo;
- posição e centro de custo não mudam pela aba Segmentos; nome repetido no centro também não;
- desativar/reativar fora de ordem (aba Segmentos) é recusado;
- só administrador inclui/renomeia/exclui aqui.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    CentroCusto,
    PricingAccount,
    PricingPlatform,
    PricingProduct,
    Product,
    Segment,
    UserRole,
)
from app.services.pricing.custo_total import calcular_custo_total

pytestmark = pytest.mark.asyncio

ROTA = "/api/produto-cadastros/categorias-venda"
LIMITE_MSG = "Este centro de custo já tem 5 categorias de venda."


@pytest.fixture
async def centros(db):
    """Celular (5), Mala (5, a última "Queima de estoque" inativa), Eletro (3: espaço livre) e
    a raiz antiga Catálogo (1: "Apple", como na produção). M-76: os centros de custo Celular,
    Eletro e Mala apontando para as raízes, como a 0514 semeia (chaves "cc:<nome>")."""
    cel = Segment(name="Celular", slug="celular", sort_order=0)
    mala = Segment(name="Mala", slug="mala", sort_order=1)
    ele = Segment(name="Eletro", slug="eletro", sort_order=2)
    cat = Segment(name="Catálogo", slug="catalogo", sort_order=3)
    db.add_all([cel, mala, ele, cat])
    await db.flush()
    ccs = {r.name: CentroCusto(nome=r.name, segment_id=r.id, fixo=True) for r in (cel, ele, mala)}
    db.add_all(ccs.values())
    filhos: dict[str, Segment] = {}
    for raiz, nomes in (
        (cel, ["Acessórios", "Diversos", "Regular", "Robusto", "Apple"]),
        (mala, ["Acessórios", '12"', '18" e 20"', '24" acima', "Queima de estoque"]),
        (ele, ["1", "2", "3"]),
        (cat, ["Apple"]),
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
    return {
        "Celular": cel,
        "Mala": mala,
        "Eletro": ele,
        "Catálogo": cat,
        **filhos,
        **{f"cc:{n}": c for n, c in ccs.items()},
    }


async def _nova(client, centros, centro: str, nome: str):
    """Pelo id do centro de custo (M-76); o id da raiz, que a tela da M-75 mandava, também vale
    (test_menu_aceita_o_id_da_raiz)."""
    return await client.post(
        ROTA, json={"centro_custo_id": str(centros[f"cc:{centro}"].id), "nome": nome}
    )


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


async def _conta(db, dono, raiz: Segment, nome: str, **slots) -> PricingAccount:
    conta = PricingAccount(
        user_id=dono.id,
        name=nome,
        platform=PricingPlatform.SHOPEE,
        segment_id=raiz.id,
        **slots,
    )
    db.add(conta)
    await db.commit()
    return conta


async def _slots(db, conta: PricingAccount) -> list:
    """Os 5 vínculos da conta como estão no banco agora."""
    c = (
        await db.execute(
            select(PricingAccount)
            .where(PricingAccount.id == conta.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return [getattr(c, f"slot{n}_segment_id") for n in range(1, 6)]


# ================================================================ lista


async def test_lista_mostra_as_categorias_de_venda(client, make_user, auth_as, centros):
    auth_as(await make_user(permissions={"cadastro_produtos": {"view": True}}))
    r = await client.get("/api/produto-cadastros")
    assert r.status_code == 200, r.text
    corpo = r.json()
    vendas = [
        (c["tabela"], c["centro_custo"], c["nome"], c["ativa"])
        for c in corpo["cadastros"]
        if c["tipo_chave"] == "categoria_venda"
    ]
    # M-89: a Tabela é a da aba Contas do centro; as do Catálogo (raiz que não é centro de
    # custo) não aparecem mais aqui.
    assert vendas == [
        ("Contas Celular", "Celular", "Acessórios", True),
        ("Contas Celular", "Celular", "Diversos", True),
        ("Contas Celular", "Celular", "Regular", True),
        ("Contas Celular", "Celular", "Robusto", True),
        ("Contas Celular", "Celular", "Apple", True),
        ("Contas Eletro", "Eletro", "1", True),
        ("Contas Eletro", "Eletro", "2", True),
        ("Contas Eletro", "Eletro", "3", True),
        ("Contas Mala", "Mala", "Acessórios", True),
        ("Contas Mala", "Mala", '12"', True),
        ("Contas Mala", "Mala", '18" e 20"', True),
        ("Contas Mala", "Mala", '24" acima', True),
        ("Contas Mala", "Mala", "Queima de estoque", False),
    ]
    # A linha é o próprio segmento (mesmo id da aba Segmentos).
    apple = next(c for c in corpo["cadastros"] if c["nome"] == "Apple")
    assert apple["id"] == str(centros["Celular/Apple"].id)
    assert apple["tipo_nome"] == "Categoria de venda"
    assert not any(c["id"] == str(centros["Catálogo/Apple"].id) for c in corpo["cadastros"])
    # O menu do "Novo cadastro" (M-76: a lista de Cadastros › Centro de custo): sem o Catálogo
    # (não é centro de custo). M-89: com a tabela de Contas de cada centro.
    assert corpo["centros_custo"] == [
        {
            "id": str(centros[f"cc:{n}"].id),
            "nome": n,
            "aceita_categoria": True,
            "tabela": f"Contas {n}",
        }
        for n in ("Celular", "Eletro", "Mala")
    ]
    # M-89: Tipo de conta (tabela Lojas) = os três centros de custo, no fim da lista.
    assert [
        (c["tabela"], c["tipo_nome"], c["nome"], c["id"]) for c in corpo["cadastros"][-3:]
    ] == [
        ("Lojas", "Tipo de conta", n, str(centros[f"cc:{n}"].id))
        for n in ("Celular", "Eletro", "Mala")
    ]
    assert corpo["tipos"][-1]["nome"] == "Tipo de conta"


async def test_catalogo_continua_na_aba_segmentos(client, make_user, auth_as, centros):
    """M-89: a categoria do Catálogo sai da lista de Cadastros › Produtos, mas nada é apagado."""
    await _admin(make_user, auth_as)
    segs = {s["id"] for s in (await client.get("/api/segments")).json()}
    assert str(centros["Catálogo/Apple"].id) in segs
    assert str(centros["Catálogo"].id) in segs


# ================================================================ incluir


async def test_nova_entra_no_fim_e_aparece_na_aba_segmentos(
    client, db, make_user, auth_as, centros
):
    await _admin(make_user, auth_as)
    r = await _nova(client, centros, "Eletro", " Novo  4 ")
    assert r.status_code == 201, r.text
    nova = r.json()
    assert (nova["nome"], nova["centro_custo"], nova["ativa"]) == ("Novo 4", "Eletro", True)
    assert nova["tabela"] == "Contas Eletro"  # M-89
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
    # Centro de custo tem de ser uma raiz do menu: nem um filho, nem o Catálogo.
    for alvo in ("Eletro/1", "Catálogo"):
        r = await client.post(ROTA, json={"centro_custo_id": str(centros[alvo].id), "nome": "X"})
        assert r.status_code == 404, alvo
        assert r.json()["detail"]["code"] == "centro_nao_encontrado"


async def test_aba_segmentos_tambem_cria_no_fim(client, db, make_user, auth_as, centros):
    """Antes o filho novo nascia com sort_order 0, igual ao primeiro."""
    await _admin(make_user, auth_as)
    r = await client.post(
        "/api/segments", json={"name": "Quatro", "parent_id": str(centros["Eletro"].id)}
    )
    assert r.status_code == 201, r.text
    assert r.json()["sort_order"] == 3
    # Uma posição no pedido não fura a regra: vai para o fim do mesmo jeito.
    r = await client.post(
        "/api/segments",
        json={"name": "Cinco", "parent_id": str(centros["Eletro"].id), "sort_order": 0},
    )
    assert r.status_code == 201, r.text
    assert r.json()["sort_order"] == 4
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


async def _segmento_de(db, modelo, id_):
    """O segment_id da linha como está no banco agora (a API grava por outra sessão)."""
    return (await db.execute(select(modelo.segment_id).where(modelo.id == id_))).scalar_one()


async def test_em_uso_pede_destino_transfere_e_exclui(client, db, make_user, auth_as, centros):
    """M-90: em uso na tabela dela (Tabela de Preços / conta apontando DIRETO para ela; o
    vínculo slot não conta) a exclusão não bloqueia — pede o destino entre as outras ATIVAS do
    mesmo centro; com o destino, produtos, contas e produtos da tela Produtos passam para ele e
    a categoria sai."""
    dono = await _admin(make_user, auth_as)
    apple = centros["Celular/Apple"]
    acessorios = centros["Celular/Acessórios"]
    i1 = PricingProduct(user_id=dono.id, sku="i001", name="iPhone", segment_id=apple.id)
    i2 = PricingProduct(user_id=dono.id, sku="i002", name="iPad", segment_id=apple.id)
    velho = Product(user_id=dono.id, sku="i001.us", name="iPhone usado", segment_id=apple.id)
    db.add_all([i1, i2, velho])
    await db.commit()
    direta = await _conta(db, dono, apple, "direta")
    vinculo = await _conta(
        db, dono, centros["Celular"], "só o vínculo", slot5_segment_id=apple.id
    )

    r = await client.get(f"{ROTA}/{apple.id}/uso")
    assert r.status_code == 200, r.text
    uso = r.json()
    assert (uso["regra"], uso["produtos"], uso["contas"], uso["bloqueia"]) == (
        "categoria_venda",
        2,
        1,
        False,
    )
    assert uso["precisa_destino"] is True
    assert uso["produtos_tela_produtos"] == 1
    assert uso["centro_custo"] == "Celular"
    assert uso["mensagem"] == (
        "“Apple” (Celular) está em uso: 2 produtos da Tabela de Preços e 1 conta. Escolha para "
        "qual categoria de venda eles vão antes de excluir."
    )
    # As outras ATIVAS do Celular, na ordem das colunas (nem ela, nem outro centro).
    nomes = [d["nome"] for d in uso["destinos"]]
    assert nomes == ["Acessórios", "Diversos", "Regular", "Robusto"]
    assert uso["destinos"][0]["id"] == str(acessorios.id)
    # Com destino, os produtos da tela Produtos "vão junto" (a tela diz); fica o nome reservado.
    assert uso["avisos"] == [
        "“Apple” é um nome reservado: depois de excluída, não dá para criar de novo pela tela."
    ]

    # Sem destino: recusa (nas duas telas — a aba Segmentos não pergunta o destino).
    for rota in (f"{ROTA}/{apple.id}", f"/api/segments/{apple.id}"):
        r = await client.delete(rota)
        assert r.status_code == 409, rota
        d = r.json()["detail"]
        assert (d["code"], d["produtos"], d["contas"]) == ("categoria_venda_em_uso", 2, 1)
    # Destino que não vale: ela mesma, de outro centro, a raiz.
    for ruim in (apple.id, centros["Mala/Acessórios"].id, centros["Celular"].id):
        r = await client.delete(f"{ROTA}/{apple.id}", params={"destino_id": str(ruim)})
        assert r.status_code == 409, ruim
        assert r.json()["detail"]["code"] == "categoria_venda_destino_invalido"
    assert ("Apple", 4, True) in await _filhos(db, centros["Celular"])
    assert await _segmento_de(db, PricingProduct, i1.id) == apple.id

    r = await client.delete(f"{ROTA}/{apple.id}", params={"destino_id": str(acessorios.id)})
    assert r.status_code == 204, r.text
    assert "Apple" not in [n for n, _p, _a in await _filhos(db, centros["Celular"])]
    for modelo, id_ in (
        (PricingProduct, i1.id),
        (PricingProduct, i2.id),
        (PricingAccount, direta.id),
        (Product, velho.id),
    ):
        assert await _segmento_de(db, modelo, id_) == acessorios.id, modelo
    # O vínculo slot5 é limpo; a conta continua no centro.
    assert await _slots(db, vinculo) == [None, None, None, None, None]
    assert await _segmento_de(db, PricingAccount, vinculo.id) == centros["Celular"].id


async def test_destino_inativo_nao_vale(client, db, make_user, auth_as, centros):
    """M-90: a "Queima de estoque" (inativa) não recebe; sem nenhuma outra ativa no centro, a
    exclusão em uso fica bloqueada."""
    dono = await _admin(make_user, auth_as)
    db.add(
        PricingProduct(user_id=dono.id, sku="b001", name="Mala", segment_id=centros["Eletro/3"].id)
    )
    await db.commit()
    r = await client.delete(
        f"{ROTA}/{centros['Eletro/3'].id}",
        params={"destino_id": str(centros["Mala/Queima de estoque"].id)},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "categoria_venda_destino_invalido"

    # Eletro só com a "3" ativa: em uso e sem para onde mandar → bloqueia.
    for chave in ("Eletro/1", "Eletro/2"):
        seg = await db.get(Segment, centros[chave].id)
        seg.active = False
    await db.commit()
    uso = (await client.get(f"{ROTA}/{centros['Eletro/3'].id}/uso")).json()
    assert (uso["bloqueia"], uso["precisa_destino"], uso["destinos"]) == (True, False, [])
    assert uso["mensagem"] == (
        "“3” (Eletro) está em uso: 1 produto da Tabela de Preços, e não há outra categoria de "
        "venda ativa em Eletro para recebê-los."
    )
    r = await client.delete(f"{ROTA}/{centros['Eletro/3'].id}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "categoria_venda_sem_destino"


async def test_produtos_da_tela_produtos_nao_travam(client, db, make_user, auth_as, centros):
    """M-90, o exemplo do pedido: "Apple" (Contas Celular) sem conta e sem produto da Tabela de
    Preços, só com produtos da tela Produtos (carga antiga) → confirmação simples avisando que
    eles ficam sem segmento e do nome reservado → exclui; os produtos ficam com segment_id
    vazio."""
    dono = await _admin(make_user, auth_as)
    apple = centros["Celular/Apple"]
    velhos = [
        Product(user_id=dono.id, sku=f"i{n:03d}", name=f"iPhone {n}", segment_id=apple.id)
        for n in range(3)
    ]
    db.add_all(velhos)
    await db.commit()

    uso = (await client.get(f"{ROTA}/{apple.id}/uso")).json()
    assert (uso["produtos"], uso["contas"], uso["produtos_tela_produtos"]) == (0, 0, 3)
    assert (uso["bloqueia"], uso["precisa_destino"], uso["mensagem"]) == (False, False, None)
    assert uso["destinos"] == []
    assert uso["avisos"] == [
        "“Apple” é um nome reservado: depois de excluída, não dá para criar de novo pela tela.",
        "3 produtos da tela Produtos ficam sem segmento.",
    ]
    r = await client.delete(f"{ROTA}/{apple.id}")
    assert r.status_code == 204, r.text
    for p in velhos:
        assert await _segmento_de(db, Product, p.id) is None
    # E o nome reservado não volta pela tela.
    r = await _nova(client, centros, "Celular", "Apple")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "categoria_venda_nome_reservado"


async def test_vinculo_slot_nao_conta_e_e_limpo_ao_excluir(client, db, make_user, auth_as, centros):
    dono = await _admin(make_user, auth_as)
    e = {n: centros[f"Eletro/{n}"].id for n in ("1", "2", "3")}
    a = await _conta(
        db,
        dono,
        centros["Eletro"],
        "a",
        slot1_segment_id=e["1"],
        slot2_segment_id=e["2"],
        slot3_segment_id=e["3"],
    )
    # Vínculo trocado à mão (popover da aba Contas) também é limpo.
    b = await _conta(db, dono, centros["Eletro"], "b", slot1_segment_id=e["3"])
    r = await client.get(f"{ROTA}/{e['3']}/uso")
    assert r.json() == {
        "regra": "categoria_venda",
        "produtos": 0,
        "bloqueia": False,
        "mensagem": None,
        "contas": 0,
        "produtos_tela_produtos": 0,
        "precisa_destino": False,
        "destinos": [],
        "centro_custo": "Eletro",
        "avisos": [],
        # M-91: é a última — nenhuma anda; a margem/frete dela some nas 2 contas do Eletro.
        "sobem": [],
        "contas_centro": 2,
    }
    assert (await client.delete(f"{ROTA}/{e['3']}")).status_code == 204
    assert await _slots(db, a) == [e["1"], e["2"], None, None, None]
    assert await _slots(db, b) == [None, None, None, None, None]
    # Pela aba Segmentos, o mesmo.
    assert (await client.delete(f"/api/segments/{e['2']}")).status_code == 204
    assert await _slots(db, a) == [e["1"], None, None, None, None]


async def test_nova_na_posicao_n_preenche_o_slot_n_vazio(client, db, make_user, auth_as, centros):
    """Mesma regra de _default_slot_segments: a página Margem não fica sem "Projetado"."""
    dono = await _admin(make_user, auth_as)
    um = centros["Eletro/1"].id
    vazia = await _conta(db, dono, centros["Eletro"], "vazia", slot1_segment_id=um)
    trocada = await _conta(db, dono, centros["Eletro"], "trocada", slot4_segment_id=um)
    outra = await _conta(db, dono, centros["Celular"], "celular")

    r = await _nova(client, centros, "Eletro", "Quatro")
    assert r.status_code == 201, r.text
    quatro = r.json()["id"]
    assert [str(x) if x else None for x in await _slots(db, vazia)] == [
        str(um),
        None,
        None,
        quatro,
        None,
    ]
    # Quem já tinha o slot4 preenchido fica como estava; conta de outro centro não muda.
    assert await _slots(db, trocada) == [None, None, None, um, None]
    assert await _slots(db, outra) == [None, None, None, None, None]

    r = await client.post(
        "/api/segments", json={"name": "Cinco", "parent_id": str(centros["Eletro"].id)}
    )
    assert r.status_code == 201, r.text
    assert (await _slots(db, vazia))[4] is not None
    assert str((await _slots(db, trocada))[4]) == r.json()["id"]


async def test_aba_segmentos_continua_so_a_ultima(client, db, make_user, auth_as, centros):
    """M-91: a do meio só sai pela lixeira de Cadastros › Produtos; pela aba Segmentos continua
    recusada e nada muda. A última sai pelas duas e as outras não mudam de coluna."""
    await _admin(make_user, auth_as)
    r = await client.delete(f"/api/segments/{centros['Eletro/2'].id}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "categoria_venda_do_meio"
    assert "lixeira de Cadastros › Produtos" in r.json()["detail"]["mensagem"]
    assert [n for n, _p, _a in await _filhos(db, centros["Eletro"])] == ["1", "2", "3"]
    # Pela lixeira a do meio não bloqueia: a janela diz quais andam.
    uso = (await client.get(f"{ROTA}/{centros['Eletro/2'].id}/uso")).json()
    assert (uso["bloqueia"], uso["sobem"], uso["mensagem"]) == (False, ["3"], None)

    r = await client.get(f"{ROTA}/{centros['Eletro/3'].id}/uso")
    uso = r.json()
    assert (uso["regra"], uso["produtos"], uso["bloqueia"], uso["mensagem"]) == (
        "categoria_venda",
        0,
        False,
        None,
    )
    assert uso["precisa_destino"] is False
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


# ================================================================ M-91: excluir a do meio

D = Decimal
CELULAR = ("Acessórios", "Diversos", "Regular", "Robusto")


def _pares(*pares) -> dict:
    """[(margem, frete), …] → margin1/shipping1, margin2/shipping2…"""
    campos: dict = {}
    for n, (m, f) in enumerate(pares, start=1):
        campos[f"margin{n}"] = None if m is None else D(m)
        campos[f"shipping{n}"] = None if f is None else D(f)
    return campos


def _coluna(conta: PricingAccount, n: int) -> tuple:
    """(margem, frete, vínculo) da coluna n (1..5)."""
    return (
        getattr(conta, f"margin{n}"),
        getattr(conta, f"shipping{n}"),
        getattr(conta, f"slot{n}_segment_id"),
    )


async def _ler_conta(db, conta_id) -> PricingAccount:
    return (
        await db.execute(
            select(PricingAccount)
            .where(PricingAccount.id == conta_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _grade(client, dept: str) -> dict[tuple[str, str], str | None]:
    """{(conta, produto): preço} da grade de preços (o cálculo de verdade)."""
    r = await client.get("/api/pricing/grid", params={"department": dept})
    assert r.status_code == 200, r.text
    return {
        (c["pricing_account_id"], c["pricing_product_id"]): c["price"] for c in r.json()["cells"]
    }


async def test_excluir_do_meio_anda_as_colunas_e_nenhum_preco_muda(
    client, db, make_user, auth_as, centros
):
    """A conferência do pedido M-91: Celular com 4 categorias (Acessórios, Diversos, Regular,
    Robusto) e margens/fretes DIFERENTES em cada conta (Shopee, par do ML e a coluna de
    catálogo do Clássico). Exclui "Diversos" (2ª) mandando para "Regular":
    (i) cada categoria que fica tem, em cada conta, a mesma margem/frete/vínculo de antes;
    (ii) o preço de cada produto não transferido fica igual na grade;
    (iii) os transferidos ficam com o preço do destino;
    (iv) o Eletro não muda nada."""
    dono = await _admin(make_user, auth_as)
    await db.delete(await db.get(Segment, centros["Celular/Apple"].id))
    await db.commit()
    cel = {n: centros[f"Celular/{n}"] for n in CELULAR}
    slots = {f"slot{n}_segment_id": cel[nome].id for n, nome in enumerate(CELULAR, start=1)}
    raiz = centros["Celular"]

    def conta(nome, plataforma, **kw) -> PricingAccount:
        return PricingAccount(user_id=dono.id, name=nome, platform=plataforma, **kw)

    aguiar = conta(
        "Shopee aguiar",
        PricingPlatform.SHOPEE,
        segment_id=raiz.id,
        commission=D("0.20"),
        margem_minima_pct=D("25"),
        custo_total_pct=D("3"),
        # Diversos é a menor margem: sem ela, o Custo total (M-86) sobe.
        **_pares(("0.549", "7"), ("0.30", "35"), ("0.356", "36"), ("0.356", "37"), ("0.99", "99")),
        **slots,
    )
    par = uuid4()
    classico = conta(
        "ML londres",
        PricingPlatform.ML,
        segment_id=raiz.id,
        listing_type="ml classico",
        par_id=par,
        commission=D("0.11"),
        **_pares(("0.40", "10"), ("0.35", "22"), ("0.33", "25"), ("0.31", "30")),
        **slots,
    )
    premium = conta(
        "ML londres",
        PricingPlatform.ML,
        segment_id=raiz.id,
        listing_type="ml premium",
        par_id=par,
        commission=D("0.16"),
        **_pares(("0.45", "11"), ("0.38", "23"), ("0.36", "26"), ("0.34", "31")),
        **slots,
    )
    eletro = conta(
        "Shopee eletro",
        PricingPlatform.SHOPEE,
        segment_id=centros["Eletro"].id,
        commission=D("0.15"),
        **_pares(("0.10", "1"), ("0.20", "2"), ("0.30", "3")),
    )
    db.add_all([aguiar, classico, premium, eletro])
    await db.flush()
    # Coluna de catálogo do Clássico: margens próprias (margens_catalogo) em 1, 3 e 4; a 2
    # vazia usa a da base.
    filha = conta(
        "ML londres",
        PricingPlatform.ML,
        segment_id=raiz.id,
        listing_type="ml classico",
        canal="catalogo",
        conta_base_id=classico.id,
        margin1=D("0.50"),
        margin3=D("0.42"),
        margin4=D("0.41"),
        **slots,
    )
    db.add(filha)

    def produto(sku, seg, custo) -> PricingProduct:
        return PricingProduct(
            user_id=dono.id,
            sku=sku,
            name=sku,
            segment_id=seg.id,
            cost_kit1=D(custo),
            preco_catalogo=D(custo),
        )

    acess = produto("a1", cel["Acessórios"], "10")
    div1 = produto("d1", cel["Diversos"], "40")  # mesmo custo do r1: depois, mesmo preço
    div2 = produto("d2", cel["Diversos"], "30")
    reg = produto("r1", cel["Regular"], "40")
    rob = produto("b1", cel["Robusto"], "50")
    ele = produto("e1", centros["Eletro/2"], "60")
    db.add_all([acess, div1, div2, reg, rob, ele])
    await db.commit()
    contas = (aguiar, classico, premium, filha)
    antes = {c.id: [_coluna(c, n) for n in range(1, 6)] for c in contas}
    eletro_antes = [_coluna(eletro, n) for n in range(1, 6)]
    grade_cel = await _grade(client, "celular")
    grade_ele = await _grade(client, "eletro")
    # Os 5 produtos do Celular × 4 colunas (a de catálogo junto da base), todos com preço.
    assert len(grade_cel) == 20 and all(p is not None for p in grade_cel.values())
    for c in contas:
        assert grade_cel[(str(c.id), str(div1.id))] != grade_cel[(str(c.id), str(reg.id))], c.name

    # A janela: as que andam e em quantas contas (o par conta uma; o catálogo vai com a base).
    uso = (await client.get(f"{ROTA}/{cel['Diversos'].id}/uso")).json()
    assert (uso["bloqueia"], uso["precisa_destino"], uso["produtos"]) == (False, True, 2)
    assert (uso["sobem"], uso["contas_centro"]) == (["Regular", "Robusto"], 2)

    r = await client.delete(
        f"{ROTA}/{cel['Diversos'].id}", params={"destino_id": str(cel["Regular"].id)}
    )
    assert r.status_code == 204, r.text

    # As posições: fica 1 Acessórios, 2 Regular, 3 Robusto.
    assert await _filhos(db, raiz) == [
        ("Acessórios", 0, True),
        ("Regular", 1, True),
        ("Robusto", 2, True),
    ]
    # (i) cada categoria que fica: mesma margem, frete e vínculo, na coluna nova, em cada conta.
    for c in contas:
        depois = await _ler_conta(db, c.id)
        velho = antes[c.id]
        assert _coluna(depois, 1) == velho[0], c.name  # Acessórios
        assert _coluna(depois, 2) == velho[2], c.name  # Regular (era a 3)
        assert _coluna(depois, 3) == velho[3], c.name  # Robusto (era a 4)
        # (d) a última ocupada fica vazia; a de Diversos sumiu.
        assert _coluna(depois, 4) == (None, None, None), c.name
        # Coluna sem categoria depois da última não muda.
        assert _coluna(depois, 5) == velho[4], c.name
    # O exemplo do pedido, na Shopee aguiar.
    ag = await _ler_conta(db, aguiar.id)
    assert (ag.margin1, ag.shipping1, ag.margin2, ag.shipping2, ag.margin3, ag.shipping3) == (
        D("0.549"),
        D("7"),
        D("0.356"),
        D("36"),
        D("0.356"),
        D("37"),
    )
    # M-86: o Custo total é recalculado com as margens que ficaram.
    esperado = calcular_custo_total([D("0.549"), D("0.356"), D("0.356")], D("25"), [D("0.20")])
    assert esperado == D("6")
    assert ag.custo_total_pct == esperado

    grade_depois = await _grade(client, "celular")
    assert grade_depois.keys() == grade_cel.keys()
    for c in contas:
        # (ii) quem não foi transferido: o mesmo preço.
        for p in (acess, reg, rob):
            chave = (str(c.id), str(p.id))
            assert grade_depois[chave] == grade_cel[chave], (c.name, p.sku)
        # (iii) os transferidos: o preço do destino (d1 tem o custo do r1).
        assert grade_depois[(str(c.id), str(div1.id))] == grade_cel[(str(c.id), str(reg.id))]
        assert grade_depois[(str(c.id), str(div2.id))] is not None
    for p in (div1, div2):
        assert await _segmento_de(db, PricingProduct, p.id) == cel["Regular"].id

    # (iv) o Eletro não mudou.
    assert await _grade(client, "eletro") == grade_ele
    el = await _ler_conta(db, eletro.id)
    assert [_coluna(el, n) for n in range(1, 6)] == eletro_antes
    assert [n for n, _p, _a in await _filhos(db, centros["Eletro"])] == ["1", "2", "3"]


async def test_excluir_inativa_do_meio_sem_uso(client, db, make_user, auth_as, centros):
    """Categoria inativa no meio (dado antigo), sem uso: mesmo tratamento — a seguinte sobe e a
    margem/frete dela anda junto; botão Excluir simples (sem destino)."""
    dono = await _admin(make_user, auth_as)
    e = {n: centros[f"Eletro/{n}"] for n in ("1", "2", "3")}
    seg = await db.get(Segment, e["2"].id)
    seg.active = False
    await db.commit()
    a = await _conta(
        db,
        dono,
        centros["Eletro"],
        "a",
        **_pares(("0.10", "1"), ("0.20", "2"), ("0.30", "3")),
        slot1_segment_id=e["1"].id,
        slot2_segment_id=e["2"].id,
        slot3_segment_id=e["3"].id,
    )
    uso = (await client.get(f"{ROTA}/{e['2'].id}/uso")).json()
    assert (uso["bloqueia"], uso["precisa_destino"], uso["sobem"], uso["contas_centro"]) == (
        False,
        False,
        ["3"],
        1,
    )
    assert (await client.delete(f"{ROTA}/{e['2'].id}")).status_code == 204
    assert await _filhos(db, centros["Eletro"]) == [("1", 0, True), ("3", 1, True)]
    depois = await _ler_conta(db, a.id)
    assert [_coluna(depois, n) for n in range(1, 4)] == [
        (D("0.10"), D("1"), e["1"].id),
        (D("0.30"), D("3"), e["3"].id),
        (None, None, None),
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


async def test_aba_segmentos_nao_muda_lugar_nem_repete_nome(
    client, db, make_user, auth_as, centros
):
    await _admin(make_user, auth_as)
    dois = centros["Eletro/2"].id
    for corpo in (
        {"sort_order": 4},
        {"parent_id": str(centros["Mala"].id)},
        {"parent_id": None},
    ):
        r = await client.patch(f"/api/segments/{dois}", json=corpo)
        assert r.status_code == 409, corpo
        assert r.json()["detail"]["code"] == "categoria_venda_posicao_fixa"
    # Mandar o valor que já está não é mudança.
    r = await client.patch(f"/api/segments/{dois}", json={"sort_order": 1, "name": "Dois"})
    assert r.status_code == 200, r.text
    # Uma raiz não vira categoria de venda por troca de pai (furaria o limite de 5).
    r = await client.patch(
        f"/api/segments/{centros['Catálogo'].id}", json={"parent_id": str(centros["Eletro"].id)}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "categoria_venda_posicao_fixa"
    # Nome repetido no mesmo centro, pela aba Segmentos, também não.
    r = await client.patch(f"/api/segments/{dois}", json={"name": "3"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "nome_repetido"
    assert await _filhos(db, centros["Eletro"]) == [
        ("1", 0, True),
        ("Dois", 1, True),
        ("3", 2, True),
    ]


async def test_nome_de_uma_renomeada_pode_voltar(client, db, make_user, auth_as, centros):
    """A renomeada guarda o slug antigo: a nova com aquele nome ganha outro slug (sem 409)."""
    await _admin(make_user, auth_as)
    r = await _nova(client, centros, "Eletro", "Novo")
    assert r.status_code == 201, r.text
    novo = r.json()["id"]
    assert (await client.patch(f"{ROTA}/{novo}", json={"nome": "Outro"})).status_code == 200
    r = await _nova(client, centros, "Eletro", "Novo")
    assert r.status_code == 201, r.text
    slugs = {
        s.name: s.slug
        for s in (
            await db.execute(
                select(Segment)
                .where(Segment.parent_id == centros["Eletro"].id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    assert (slugs["Outro"], slugs["Novo"]) == ("novo", "novo-2")


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


async def test_duas_ao_mesmo_tempo_no_mesmo_centro_nao_repetem_posicao(
    client, db, make_user, auth_as, centros
):
    """Revisão M-75/M-76: duas criações simultâneas no mesmo centro esperam uma pela outra
    (trava na raiz) — nunca duas categorias na mesma posição de margem/frete."""
    import asyncio

    await _admin(make_user, auth_as)
    r1, r2 = await asyncio.gather(
        _nova(client, centros, "Eletro", "Corrida A"),
        _nova(client, centros, "Eletro", "Corrida B"),
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 201], (r1.text, r2.text)
    posicoes = [p for _n, p, _a in await _filhos(db, centros["Eletro"])]
    assert len(posicoes) == len(set(posicoes)) == 5
    assert posicoes == [0, 1, 2, 3, 4]
