"""Categorias de venda (M-75, Harry 25/11/2026) — services/categorias_venda.py e as rotas
/api/produto-cadastros/categorias-venda (Cadastros › Produtos) e /api/segments (aba Segmentos).

São os segmentos filhos das raízes (mesma tabela `segments`), não um cadastro novo. Cobre:
- a lista de Cadastros › Produtos com as categorias de venda (Centro de custo, inativas, ordem
  das colunas) e os centros de custo;
- incluir: entra no FIM do centro (próxima posição, não 0 — nas duas telas); máximo de 5
  contando as inativas ("Este centro de custo já tem 5 categorias de venda."); o mesmo nome em
  outro centro pode, no mesmo não; "Apple"/"Queima de estoque" não são usados por outra;
- renomear: "Apple" e "Queima de estoque" não trocam de nome (nas duas telas);
- excluir: em uso (produto ou conta apontando DIRETO para ela) recusa com as contagens; os
  vínculos slot1..5 das contas não contam e são limpos; só a última do centro sai;
- incluir na posição N preenche o slot{N} vazio das contas daquele centro (nas duas telas);
- o menu de centros de custo (sem o Catálogo) e a ordem Celular, Eletro, Mala, Catálogo;
- posição e centro de custo não mudam pela aba Segmentos; nome repetido no centro também não;
- desativar/reativar fora de ordem (aba Segmentos) é recusado;
- só administrador inclui/renomeia/exclui aqui.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import (
    CentroCusto,
    PricingAccount,
    PricingPlatform,
    PricingProduct,
    Segment,
    UserRole,
)

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
        # A raiz antiga Catálogo vem depois de Celular, Eletro e Mala (ordem da M-23).
        ("Catálogo", "Apple", True),
    ]
    # A linha é o próprio segmento (mesmo id da aba Segmentos).
    apple = next(c for c in corpo["cadastros"] if c["nome"] == "Apple")
    assert apple["id"] == str(centros["Celular/Apple"].id)
    assert apple["tipo_nome"] == "Categoria de venda"
    # O menu do "Novo cadastro" (M-76: a lista de Cadastros › Centro de custo): sem o Catálogo
    # (não é centro de custo).
    assert corpo["centros_custo"] == [
        {"id": str(centros[f"cc:{n}"].id), "nome": n, "aceita_categoria": True}
        for n in ("Celular", "Eletro", "Mala")
    ]


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


async def test_em_uso_nao_sai_e_diz_quantos(client, db, make_user, auth_as, centros):
    """Conta usa = `pricing_accounts.segment_id` apontando DIRETO para ela; o vínculo slot não."""
    dono = await _admin(make_user, auth_as)
    apple = centros["Celular/Apple"]
    db.add_all(
        [
            PricingProduct(user_id=dono.id, sku="i001", name="iPhone", segment_id=apple.id),
            PricingProduct(user_id=dono.id, sku="i002", name="iPad", segment_id=apple.id),
        ]
    )
    await db.commit()
    await _conta(db, dono, apple, "direta")
    await _conta(db, dono, centros["Celular"], "só o vínculo", slot5_segment_id=apple.id)

    r = await client.get(f"{ROTA}/{apple.id}/uso")
    assert r.status_code == 200, r.text
    uso = r.json()
    assert (uso["regra"], uso["produtos"], uso["bloqueia"]) == ("categoria_venda", 2, True)
    assert "2 produtos da Tabela de Preços e 1 conta." in uso["mensagem"]
    assert "Passe esses produtos e essa conta para outra categoria de venda" in uso["mensagem"]
    for rota in (f"{ROTA}/{apple.id}", f"/api/segments/{apple.id}"):
        r = await client.delete(rota)
        assert r.status_code == 409, rota
        d = r.json()["detail"]
        assert (d["code"], d["produtos"], d["contas"]) == ("categoria_venda_em_uso", 2, 1)
    assert ("Apple", 4, True) in await _filhos(db, centros["Celular"])


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
