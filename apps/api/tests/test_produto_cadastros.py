"""Cadastros › Produtos (M-29, 24/11/2026) — routers/produto_cadastros.py.

Pedido do Harry: tabela Tipo | Nome com "Categoria" e "Tag de envio". Cobre:
- permissões: ver = `cadastro_produtos` (view); criar/editar/excluir = só administrador, mesmo
  com edit/delete marcados; /listas (os filtros do Produtos 2) para qualquer pessoa ativa;
- nomes únicos no tipo sem olhar maiúsculas (e o mesmo nome em outro tipo pode);
- Categoria com produto na base do ERP: excluir, renomear e trocar de tipo RECUSADOS com a
  contagem; ERP fora do ar também recusa; trocar só maiúsculas passa sem perguntar ao ERP;
- Tag de envio com produto: pede confirmação com a contagem; com `confirmar`, exclui;
- renomear o tipo Categoria não perde a regra (vale a `chave`);
- a pergunta ao ERP (rede interna, cabeçalhos de pessoa comum) e a resposta quebrada.
O ERP é falso aqui (respx / monkeypatch): nenhuma rede.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import get_settings
from app.models import ProdutoCadastro, ProdutoCadastroTipo, UserRole
from app.services import produto_cadastros as svc

pytestmark = pytest.mark.asyncio

CATEGORIAS = (
    "Mala Kit",
    "Acessórios",
    "Celular",
    "Celular Kit",
    "Eletro",
    "Eletro Kit",
    "Insumos",
    "Mala",
)
TAGS = (".sp", ".cd", ".ci", ".pi", ".ra", ".sa")
USO = {
    "products": 2753,
    "categories": {"Celular": 178, "Mala": 441, "Insumos": 42},
    "without_category": 97,
    "tags": {},
}


@pytest.fixture
async def base(db):
    """A tabela como a 0508 deixa: 2 tipos com chave e os 14 nomes."""
    cat = ProdutoCadastroTipo(nome="Categoria", chave="categoria")
    tag = ProdutoCadastroTipo(nome="Tag de envio", chave="tag_envio")
    db.add_all([cat, tag])
    await db.flush()
    db.add_all([ProdutoCadastro(tipo_id=cat.id, nome=n) for n in CATEGORIAS])
    db.add_all([ProdutoCadastro(tipo_id=tag.id, nome=n) for n in TAGS])
    await db.commit()
    return {"categoria": cat.id, "tag": tag.id}


@pytest.fixture
def erp(monkeypatch):
    """ERP falso: guarda o que perguntaram e responde `uso` (None = fora do ar)."""
    estado = {"uso": USO, "perguntas": []}

    async def falso(tags=None):
        estado["perguntas"].append(list(tags or []))
        u = estado["uso"]
        if u is None:
            return None
        contagem = {".sp": 639, ".pi": 705, ".sa": 0}
        return {**u, "tags": {t: contagem.get(t, 0) for t in tags or []}}

    monkeypatch.setattr(svc, "uso_no_erp", falso)
    return estado


async def _admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _id(client, tipo: str, nome: str) -> str:
    r = await client.get("/api/produto-cadastros")
    assert r.status_code == 200, r.text
    return next(
        c["id"] for c in r.json()["cadastros"] if (c["tipo_nome"], c["nome"]) == (tipo, nome)
    )


# ================================================================ leitura e permissões


async def test_lista_em_ordem_de_tipo_e_nome(client, make_user, auth_as, base):
    await _admin(make_user, auth_as)
    r = await client.get("/api/produto-cadastros")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert [(c["tipo_nome"], c["nome"]) for c in corpo["cadastros"]] == [
        ("Categoria", "Acessórios"),
        ("Categoria", "Celular"),
        ("Categoria", "Celular Kit"),
        ("Categoria", "Eletro"),
        ("Categoria", "Eletro Kit"),
        ("Categoria", "Insumos"),
        ("Categoria", "Mala"),
        ("Categoria", "Mala Kit"),
        ("Tag de envio", ".cd"),
        ("Tag de envio", ".ci"),
        ("Tag de envio", ".pi"),
        ("Tag de envio", ".ra"),
        ("Tag de envio", ".sa"),
        ("Tag de envio", ".sp"),
    ]
    # M-75: "Categoria de venda" (os segmentos filhos) entre Categoria e Tag de envio.
    assert [(t["nome"], t["chave"]) for t in corpo["tipos"]] == [
        ("Categoria", "categoria"),
        ("Categoria de venda", "categoria_venda"),
        ("Tag de envio", "tag_envio"),
    ]
    assert all(c["centro_custo"] is None and c["ativa"] is True for c in corpo["cadastros"])
    assert corpo["centros_custo"] == []


async def test_ver_exige_a_permissao_da_aba(client, make_user, auth_as, base):
    sem = await make_user(permissions={"segmentos": {"view": True}})
    auth_as(sem)
    assert (await client.get("/api/produto-cadastros")).status_code == 403
    com = await make_user(permissions={"cadastro_produtos": {"view": True}})
    auth_as(com)
    r = await client.get("/api/produto-cadastros")
    assert r.status_code == 200
    assert len(r.json()["cadastros"]) == 14


async def test_so_administrador_cria_edita_e_exclui(client, make_user, auth_as, base, erp):
    quase = await make_user(
        permissions={"cadastro_produtos": {"view": True, "edit": True, "delete": True}}
    )
    auth_as(quase)
    alvo = await _id(client, "Tag de envio", ".sa")
    tentativas = [
        client.post("/api/produto-cadastros/tipos", json={"nome": "Cor"}),
        client.patch(f"/api/produto-cadastros/tipos/{base['categoria']}", json={"nome": "X"}),
        client.post("/api/produto-cadastros", json={"tipo_id": str(base["tag"]), "nome": ".us"}),
        client.patch(f"/api/produto-cadastros/{alvo}", json={"nome": ".sx"}),
        client.delete(f"/api/produto-cadastros/{alvo}?confirmar=true"),
        client.get(f"/api/produto-cadastros/{alvo}/uso"),
    ]
    for t in tentativas:
        r = await t
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "admin_only"
    assert erp["perguntas"] == []


async def test_listas_dos_filtros_para_qualquer_pessoa_ativa(client, make_user, auth_as, base):
    auth_as(await make_user(permissions={}))
    r = await client.get("/api/produto-cadastros/listas")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "categorias": [
            "Acessórios",
            "Celular",
            "Celular Kit",
            "Eletro",
            "Eletro Kit",
            "Insumos",
            "Mala",
            "Mala Kit",
        ],
        "tags_envio": [".cd", ".ci", ".pi", ".ra", ".sa", ".sp"],
    }
    # Tipo sem chave (criado na tela) não entra nos filtros.
    auth_as(await make_user(role=UserRole.ADMIN))
    tipo = (await client.post("/api/produto-cadastros/tipos", json={"nome": "Cor"})).json()
    r = await client.post("/api/produto-cadastros", json={"tipo_id": tipo["id"], "nome": "Azul"})
    assert r.status_code == 201
    r = await client.get("/api/produto-cadastros/listas")
    assert "Azul" not in r.json()["categorias"] + r.json()["tags_envio"]


async def test_sem_login_nada(client, base):
    assert (await client.get("/api/produto-cadastros")).status_code == 401
    assert (await client.get("/api/produto-cadastros/listas")).status_code == 401


# ================================================================ criar e nomes únicos


async def test_cria_tipo_e_cadastro_e_recusa_nome_repetido(client, make_user, auth_as, base):
    await _admin(make_user, auth_as)
    r = await client.post("/api/produto-cadastros/tipos", json={"nome": "  Cor   do  produto "})
    assert r.status_code == 201, r.text
    cor = r.json()
    assert cor["nome"] == "Cor do produto" and cor["chave"] is None
    r = await client.post("/api/produto-cadastros/tipos", json={"nome": "cor do PRODUTO"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "tipo_repetido"
    # M-75: "Categoria de venda" já existe (é o tipo dos segmentos filhos).
    r = await client.post("/api/produto-cadastros/tipos", json={"nome": "categoria de VENDA"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "tipo_repetido"

    r = await client.post(
        "/api/produto-cadastros", json={"tipo_id": str(base["tag"]), "nome": ".us"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["tipo_nome"] == "Tag de envio" and r.json()["nome"] == ".us"
    r = await client.post(
        "/api/produto-cadastros", json={"tipo_id": str(base["categoria"]), "nome": "celular"}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "nome_repetido"
    assert "Categoria" in r.json()["detail"]["mensagem"]
    # O mesmo nome em outro tipo pode.
    r = await client.post("/api/produto-cadastros", json={"tipo_id": cor["id"], "nome": "Celular"})
    assert r.status_code == 201
    r = await client.post("/api/produto-cadastros", json={"tipo_id": cor["id"], "nome": "   "})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "nome_vazio"
    r = await client.post("/api/produto-cadastros", json={"tipo_id": cor["id"], "nome": "x" * 61})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "nome_longo"
    r = await client.post(
        "/api/produto-cadastros",
        json={"tipo_id": "00000000-0000-0000-0000-000000000000", "nome": "X"},
    )
    assert r.status_code == 404


async def test_indice_unico_segura_a_corrida(db, base):
    """Se duas pessoas gravam ao mesmo tempo, o índice (tipo, lower(nome)) recusa a segunda."""
    from sqlalchemy.exc import IntegrityError

    db.add(ProdutoCadastro(tipo_id=base["categoria"], nome="MALA"))
    with pytest.raises(IntegrityError, match="uq_produto_cadastros_tipo_nome"):
        await db.commit()
    await db.rollback()


# ================================================================ categoria em uso


async def test_categoria_com_produto_nao_sai(client, make_user, auth_as, base, erp):
    await _admin(make_user, auth_as)
    celular = await _id(client, "Categoria", "Celular")

    r = await client.get(f"/api/produto-cadastros/{celular}/uso")
    assert r.json() == {
        "regra": "categoria",
        "produtos": 178,
        "bloqueia": True,
        "mensagem": "178 produtos usam a categoria “Celular”. Troque a categoria desses produtos "
        "antes: nenhum produto pode perder a categoria.",
    }
    for pedido in (
        client.delete(f"/api/produto-cadastros/{celular}?confirmar=true"),
        client.patch(f"/api/produto-cadastros/{celular}", json={"nome": "Celulares"}),
        client.patch(
            f"/api/produto-cadastros/{celular}",
            json={"tipo_id": str(base["tag"]), "confirmar": True},
        ),
    ):
        r = await pedido
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "categoria_em_uso"
        assert r.json()["detail"]["produtos"] == 178
    assert await _id(client, "Categoria", "Celular") == celular


async def test_categoria_sem_produto_sai_e_renomeia(client, make_user, auth_as, base, erp):
    await _admin(make_user, auth_as)
    eletro_kit = await _id(client, "Categoria", "Eletro Kit")
    r = await client.get(f"/api/produto-cadastros/{eletro_kit}/uso")
    assert r.json()["produtos"] == 0 and r.json()["bloqueia"] is False
    r = await client.patch(f"/api/produto-cadastros/{eletro_kit}", json={"nome": "Eletro Kits"})
    assert r.status_code == 200 and r.json()["nome"] == "Eletro Kits"
    r = await client.delete(f"/api/produto-cadastros/{eletro_kit}")
    assert r.status_code == 204
    nomes = [c["nome"] for c in (await client.get("/api/produto-cadastros")).json()["cadastros"]]
    assert "Eletro Kits" not in nomes and "Eletro Kit" not in nomes


async def test_so_maiusculas_nao_pergunta_ao_erp(client, make_user, auth_as, base, erp):
    await _admin(make_user, auth_as)
    insumos = await _id(client, "Categoria", "Insumos")
    r = await client.patch(f"/api/produto-cadastros/{insumos}", json={"nome": "INSUMOS"})
    assert r.status_code == 200 and r.json()["nome"] == "INSUMOS"
    assert erp["perguntas"] == []


async def test_erp_fora_do_ar_categoria_fica(client, make_user, auth_as, base, erp):
    await _admin(make_user, auth_as)
    erp["uso"] = None
    mala = await _id(client, "Categoria", "Mala")
    r = await client.delete(f"/api/produto-cadastros/{mala}?confirmar=true")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "erp_indisponivel"
    assert await _id(client, "Categoria", "Mala") == mala


async def test_renomear_o_tipo_categoria_mantem_a_regra(client, make_user, auth_as, base, erp):
    await _admin(make_user, auth_as)
    r = await client.patch(
        f"/api/produto-cadastros/tipos/{base['categoria']}", json={"nome": "Categorias"}
    )
    assert r.status_code == 200 and r.json() == {
        "id": str(base["categoria"]),
        "nome": "Categorias",
        "chave": "categoria",
    }
    r = await client.patch(
        f"/api/produto-cadastros/tipos/{base['categoria']}", json={"nome": "tag DE envio"}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "tipo_repetido"
    celular = await _id(client, "Categorias", "Celular")
    r = await client.delete(f"/api/produto-cadastros/{celular}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "categoria_em_uso"
    r = await client.get("/api/produto-cadastros/listas")
    assert "Celular" in r.json()["categorias"]


# ================================================================ tag de envio em uso


async def test_tag_em_uso_pede_confirmacao(client, make_user, auth_as, base, erp):
    await _admin(make_user, auth_as)
    sp = await _id(client, "Tag de envio", ".sp")
    r = await client.get(f"/api/produto-cadastros/{sp}/uso")
    assert r.json() == {
        "regra": "tag_envio",
        "produtos": 639,
        "bloqueia": False,
        "mensagem": "639 produtos terminam com “.sp”.",
    }
    r = await client.delete(f"/api/produto-cadastros/{sp}")
    assert r.status_code == 409
    d = r.json()["detail"]
    assert (d["code"], d["produtos"], d["precisa_confirmar"]) == ("tag_em_uso", 639, True)
    r = await client.patch(f"/api/produto-cadastros/{sp}", json={"nome": ".sp2"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "tag_em_uso"
    r = await client.delete(f"/api/produto-cadastros/{sp}?confirmar=true")
    assert r.status_code == 204
    assert erp["perguntas"][-1] == [".sp"]


async def test_tag_sem_produto_sai_sem_perguntar(client, make_user, auth_as, base, erp):
    await _admin(make_user, auth_as)
    sa = await _id(client, "Tag de envio", ".sa")
    assert (await client.delete(f"/api/produto-cadastros/{sa}")).status_code == 204


async def test_tag_com_erp_fora_pede_confirmacao_sem_numero(client, make_user, auth_as, base, erp):
    await _admin(make_user, auth_as)
    erp["uso"] = None
    pi = await _id(client, "Tag de envio", ".pi")
    r = await client.delete(f"/api/produto-cadastros/{pi}")
    assert r.status_code == 409
    assert r.json()["detail"]["produtos"] is None
    assert "Não consegui contar" in r.json()["detail"]["mensagem"]
    assert (await client.delete(f"/api/produto-cadastros/{pi}?confirmar=true")).status_code == 204


async def test_cadastro_de_tipo_sem_regra_sai_direto(client, make_user, auth_as, base, erp):
    await _admin(make_user, auth_as)
    cor = (await client.post("/api/produto-cadastros/tipos", json={"nome": "Cor"})).json()
    azul = (
        await client.post("/api/produto-cadastros", json={"tipo_id": cor["id"], "nome": "Azul"})
    ).json()
    assert (await client.delete(f"/api/produto-cadastros/{azul['id']}")).status_code == 204
    assert erp["perguntas"] == []


# ================================================================ a pergunta ao ERP


async def test_pergunta_ao_erp_pela_rede_interna(monkeypatch):
    base_url = get_settings().erp_interno_url.rstrip("/")
    with respx.mock(assert_all_called=True) as mock:
        rota = mock.get(f"{base_url}/api/erp-local/catalog/usage").mock(
            return_value=httpx.Response(200, json={**USO, "tags": {".sp": 639}})
        )
        uso = await svc.uso_no_erp([".sp"])
    assert uso["tags"] == {".sp": 639}
    pedido = rota.calls.last.request
    assert pedido.url.params.get_list("tag") == [".sp"]
    assert pedido.headers["x-erp-role"] == "user"
    assert pedido.headers["x-erp-actor"] == svc.ATOR_ERP
    assert svc.produtos_na_categoria(uso, "CELULAR") == 178
    assert svc.produtos_com_tag(uso, ".SP") == 639


@pytest.mark.parametrize(
    "resposta",
    [
        httpx.Response(403, json={"code": "access_unknown"}),
        httpx.Response(200, text="não é json"),
        httpx.Response(200, json={"products": 1}),
        httpx.ConnectError("sem rota"),
    ],
)
async def test_erp_que_nao_responde_direito_vira_none(resposta):
    base_url = get_settings().erp_interno_url.rstrip("/")
    with respx.mock() as mock:
        rota = mock.get(f"{base_url}/api/erp-local/catalog/usage")
        if isinstance(resposta, Exception):
            rota.mock(side_effect=resposta)
        else:
            rota.mock(return_value=resposta)
        assert await svc.uso_no_erp() is None
