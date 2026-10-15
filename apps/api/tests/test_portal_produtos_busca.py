"""Studio DaVinci, 15/10/2026: o Claude do portal acha o CÓDIGO pelo nome.

Ele criava ideia com o nome do produto no campo `sku` ("Uranyx A18 Pro Max S5
Edition laranja", "Uranyx_C68 Plus_Preto") porque `GET /api/portal/produtos`
só mandava produto com foto — e o A18 (dg088/dg089) e os C68 Plus estão com
a pasta vazia — e a linha "dg088,dg089" não dizia qual código é a laranja.

O que estes testes travam:

1. Sem parâmetro, a lista é a de antes (o portal no ar lê esta rota), só com
   `variantes` a mais.
2. `busca`: palavras sem maiúscula nem acento, "_" e "-" como espaço, no nome
   do produto, no nome da variante ou no começo do código; teto de 20.
3. `todos=1`: entra o produto da família com a pasta vazia, e continua de
   fora o Apple, o que não é família do portal e o que não tem pasta.
4. `variantes`: o nome do cadastro por código, lote de venda na ordem
   .pi/.ci/.sa/.sp/.ra (nunca kit, usado ou excluído), e a cor.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.pricing import PricingProduct
from app.models.product import Product
from app.models.segment import Segment

TOK_A = "tok-agencia-a-0123456789"
A = {"X-Portal-Token": TOK_A}
URL = "/api/portal/produtos"

A18 = "uranyx A18 pro max - S5 edition 16.128 laranja/prata - USM003"
C68 = "uranyx C68 plus 16.128 - USM002"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(get_settings(), "portal_tokens", f"{TOK_A}:alpha")


async def _produto(
    db: AsyncSession,
    dono,
    *,
    sku: str,
    nome: str,
    pasta: str | None,
    fotos: int | None = None,
    segmento: str = "teste",
) -> PricingProduct:
    """Linha da tabela de preços. O segmento vem junto porque `segment_id` é
    NOT NULL — é esqueleto de teste, não parte do que se afirma."""
    seg = Segment(user_id=dono.id, name=segmento, slug=f"seg-{uuid4().hex[:8]}")
    db.add(seg)
    await db.flush()
    row = PricingProduct(
        user_id=dono.id, segment_id=seg.id, sku=sku, name=nome,
        fotos_path=pasta, fotos_count=fotos,
    )
    db.add(row)
    await db.flush()
    return row


def _cadastro(db: AsyncSession, dono, sku: str, nome: str, situacao: str = "A") -> None:
    """Linha de `products` — o cadastro do Bling, onde mora o nome por cor."""
    db.add(Product(user_id=dono.id, sku=sku, name=nome, situacao=situacao))


async def _catalogo(db: AsyncSession, make_user):
    """O recorte real de 15/10/2026, encolhido: um com foto, os dois sem foto
    que o Claude não achava, e o que nunca pode sair."""
    dono = await make_user()
    await _produto(db, dono, sku="dg048,dg049", nome="uranyx F117",
                   pasta="/Celular/Fossibot F117", fotos=32)
    await _produto(db, dono, sku="b039.24,b040.24", nome="ABS 24",
                   pasta="/Malas/ABS 24", fotos=46)
    await _produto(db, dono, sku="dg088,dg089", nome=A18,
                   pasta="/Celular/Fossibot S5", fotos=0)
    # Contagem nunca feita (NULL) também é "sem foto".
    await _produto(db, dono, sku="dg060,dg061,dg062,dg063", nome=C68,
                   pasta="/Celular/Oukitel C68 plus", fotos=None)
    await _produto(db, dono, sku="ap001", nome="apple iphone 17 pro",
                   pasta="/Celular/apple iphone 17 pro", fotos=0, segmento="Apple")
    await _produto(db, dono, sku="ap002", nome="apple watch uranyx",
                   pasta="/Celular/apple watch", fotos=9, segmento="Apple")
    await _produto(db, dono, sku="xx001", nome="Coisa interna uranyx",
                   pasta="/Financeiro/Notas", fotos=0)
    await _produto(db, dono, sku="dg102", nome="uranyx wp53 pro sem pasta", pasta=None)

    for sku, nome in (
        ("dg088.ci", "Uranyx A18 Pro Max - S5 Edition 16.128 - Laranja"),
        ("dg089.ci", "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata"),
        ("dg060.pi", "Uranyx C68 Plus 16.128 - cinza"),
        ("dg061.pi", "Uranyx C68 Plus 16.128 - Dourado"),
        ("dg062.pi", "Uranyx C68 Plus 16.128 - Azul"),
        ("dg048.pi", "Uranyx F117 - Preto"),
        # A mala não tem lote: o SKU É o código, e o fim do nome é o código
        # do fornecedor entre parênteses.
        ("b039.24", "Mala Losangulo M4 tamanho 24 - Cinza Escuro (DT - XS5426 - DT14)"),
    ):
        _cadastro(db, dono, sku, nome)
    await db.commit()
    return dono


# ─────────── 1. sem parâmetro: a lista de antes ───────────


async def test_sem_parametro_a_lista_e_a_de_antes(
    client: AsyncClient, db: AsyncSession, make_user
):
    """O portal no ar chama sem parâmetro. Mesmos produtos, mesma ordem, mesmos
    campos — só `variantes` a mais, e nenhum cabeçalho de total."""
    await _catalogo(db, make_user)

    r = await client.get(URL, headers=A)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["equipe"] == "alpha"
    produtos = corpo["produtos"]
    # Só com foto, na família do portal, sem Apple — por nome.
    assert [p["nome"] for p in produtos] == ["ABS 24", "uranyx F117"]
    assert "X-Total-Count" not in r.headers
    for p in produtos:
        assert set(p) == {"id", "nome", "skus", "pasta", "marca", "fotos", "variantes"}
    f117 = produtos[1]
    sem_variantes = {k: v for k, v in f117.items() if k != "variantes"}
    assert sem_variantes == {
        "id": sem_variantes["id"],
        "nome": "uranyx F117",
        "skus": ["dg048", "dg049"],
        "pasta": "/Celular/Fossibot F117",
        "marca": "uranyx",
        "fotos": 32,
    }


async def test_sem_token_nao_busca(client: AsyncClient):
    r = await client.get(URL, params={"busca": "uranyx", "todos": "1"})
    assert r.status_code in (401, 403)


# ─────────── 2. busca ───────────


@pytest.mark.parametrize(
    "busca",
    [
        "uranyx_a18 laranja",
        # Acento e maiúscula do lado de quem busca...
        "URÂNYX_A18 Laranja",
        # ...e o "-" e o "_" do jeito que o Claude escreveu o SKU.
        "Uranyx-A18_Pro_Max S5 Edition laranja",
    ],
)
async def test_busca_por_nome_sem_acento_nem_underscore(
    client: AsyncClient, db: AsyncSession, make_user, busca: str
):
    await _catalogo(db, make_user)

    r = await client.get(URL, params={"busca": busca, "todos": "1"}, headers=A)
    assert r.status_code == 200, r.text
    assert [p["nome"] for p in r.json()["produtos"]] == [A18]
    assert r.headers["X-Total-Count"] == "1"


async def test_busca_sem_todos_so_olha_o_que_tem_foto(
    client: AsyncClient, db: AsyncSession, make_user
):
    """`busca` sozinha não muda o recorte: o A18 está com a pasta vazia."""
    await _catalogo(db, make_user)

    r = await client.get(URL, params={"busca": "uranyx a18"}, headers=A)
    assert r.status_code == 200
    assert r.json()["produtos"] == []
    assert r.headers["X-Total-Count"] == "0"


async def test_busca_exige_todas_as_palavras(
    client: AsyncClient, db: AsyncSession, make_user
):
    await _catalogo(db, make_user)

    r = await client.get(URL, params={"busca": "a18 dourado", "todos": "1"}, headers=A)
    assert r.json()["produtos"] == []
    # O separador entre o nome do produto e os das variantes não casa sozinho.
    r = await client.get(URL, params={"busca": "f117 /", "todos": "1"}, headers=A)
    assert r.json()["produtos"] == []


async def test_busca_acha_pelo_nome_da_variante(
    client: AsyncClient, db: AsyncSession, make_user
):
    """O C68 Plus da tabela de preços não diz cor; a cor está no cadastro."""
    await _catalogo(db, make_user)

    r = await client.get(URL, params={"busca": "c68 dourado", "todos": "1"}, headers=A)
    assert [p["nome"] for p in r.json()["produtos"]] == [C68]
    # E acento do lado do catálogo: "Losangulo" escrito com acento acha a mala.
    r = await client.get(URL, params={"busca": "losângulo"}, headers=A)
    assert [p["nome"] for p in r.json()["produtos"]] == ["ABS 24"]


async def test_busca_por_prefixo_de_codigo(
    client: AsyncClient, db: AsyncSession, make_user
):
    await _catalogo(db, make_user)

    def nomes(r):
        assert r.status_code == 200, r.text
        return [p["nome"] for p in r.json()["produtos"]]

    async def busca(termo: str):
        return nomes(await client.get(URL, params={"busca": termo, "todos": "1"}, headers=A))

    assert await busca("dg08") == [A18]
    assert await busca("DG089") == [A18]
    # O SKU do cadastro (com o lote) também acha a linha.
    assert await busca("dg088.ci") == [A18]
    assert await busca("b039.2") == ["ABS 24"]
    # Código vale pelo COMEÇO: "088" não é começo de código nenhum.
    assert await busca("088") == []
    # Palavra de nome e código juntos.
    assert await busca("fossibot dg04") == [] and await busca("f117 dg04") == ["uranyx F117"]


async def test_busca_com_caractere_de_controle_nao_derruba(
    client: AsyncClient, db: AsyncSession, make_user
):
    """O controle sai da busca (como no `q` das ideias) e o resto ainda busca."""
    await _catalogo(db, make_user)

    r = await client.get(URL, params={"busca": "a18\x00 laranja\x1f", "todos": "1"}, headers=A)
    assert r.status_code == 200, r.text
    assert [p["nome"] for p in r.json()["produtos"]] == [A18]

    # Sem palavra nenhuma sobrando, é a listagem de sempre.
    r = await client.get(URL, params={"busca": "\x00 _ -"}, headers=A)
    assert r.status_code == 200
    assert [p["nome"] for p in r.json()["produtos"]] == ["ABS 24", "uranyx F117"]


async def test_parametro_invalido_e_422(client: AsyncClient):
    r = await client.get(URL, params={"busca": "x" * 81}, headers=A)
    assert r.status_code == 422
    r = await client.get(URL, params={"busca": "x" * 80}, headers=A)
    assert r.status_code == 200
    r = await client.get(URL, params={"todos": "talvez"}, headers=A)
    assert r.status_code == 422


async def test_busca_corta_em_20_e_conta_o_total(
    client: AsyncClient, db: AsyncSession, make_user
):
    dono = await make_user()
    for i in range(25):
        await _produto(db, dono, sku=f"dg5{i:02d}", nome=f"uranyx teste {i:02d}",
                       pasta=f"/Celular/teste {i:02d}", fotos=1)
    await db.commit()

    r = await client.get(URL, params={"busca": "uranyx teste"}, headers=A)
    assert r.status_code == 200
    produtos = r.json()["produtos"]
    assert len(produtos) == 20
    assert r.headers["X-Total-Count"] == "25"
    # Os 20 primeiros por nome, a mesma ordem da listagem.
    assert [p["nome"] for p in produtos] == [f"uranyx teste {i:02d}" for i in range(20)]

    # Sem busca, nada é cortado.
    r = await client.get(URL, headers=A)
    assert len(r.json()["produtos"]) == 25


# ─────────── 3. todos=1 ───────────


async def test_todos_traz_o_sem_foto_e_continua_sem_apple(
    client: AsyncClient, db: AsyncSession, make_user
):
    await _catalogo(db, make_user)

    r = await client.get(URL, params={"todos": "1"}, headers=A)
    assert r.status_code == 200, r.text
    produtos = {p["nome"]: p for p in r.json()["produtos"]}
    assert set(produtos) == {"ABS 24", A18, C68, "uranyx F117"}
    assert produtos[A18]["fotos"] == 0 and produtos[C68]["fotos"] == 0
    assert produtos[A18]["marca"] == "uranyx"
    assert "apple" not in r.text.lower(), "revenda Apple não é marca da casa"
    assert "Coisa interna" not in r.text, "fora das famílias não vaza nem o nome"
    assert "sem pasta" not in r.text, "sem pasta não há família a provar"

    # E a busca não fura nenhuma das travas: "uranyx" está no nome dos três.
    r = await client.get(URL, params={"todos": "true", "busca": "uranyx"}, headers=A)
    nomes = [p["nome"] for p in r.json()["produtos"]]
    assert nomes == [A18, C68, "uranyx F117"]


# ─────────── 4. variantes ───────────


async def test_variantes_com_nome_e_cor(client: AsyncClient, db: AsyncSession, make_user):
    dono = await _catalogo(db, make_user)
    # O lote de venda tem ordem: .pi antes do .ci — o dg089 ganha o nome do .pi.
    _cadastro(db, dono, "dg089.pi", "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata PI")
    # Kit, usado, CD e excluído nunca dão nome à variante.
    _cadastro(db, dono, "dg060.pi+a001.pi", "Uranyx C68 Plus 16.128 - cinza + Fone")
    _cadastro(db, dono, "dg063.us", "Oukitel C68 Plus 16.128 - Rosa - USADO - AVULSO")
    _cadastro(db, dono, "dg063.cd", "Uranyx C68 Plus 16.128 - Rosa CD")
    _cadastro(db, dono, "dg062.ci", "Uranyx C68 Plus 16.128 - Azul velho", situacao="E")
    # Lote fora do cadastro em maiúscula: o código casa sem diferenciar.
    _cadastro(db, dono, "DG049.SA", "Uranyx F117 - Laranja")
    await db.commit()

    r = await client.get(URL, params={"todos": "1"}, headers=A)
    assert r.status_code == 200, r.text
    produtos = {p["nome"]: p for p in r.json()["produtos"]}

    assert produtos[A18]["variantes"] == [
        {
            "codigo": "dg088",
            "nome": "Uranyx A18 Pro Max - S5 Edition 16.128 - Laranja",
            "cor": "Laranja",
        },
        {
            "codigo": "dg089",
            "nome": "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata PI",
            "cor": "Prata PI",
        },
    ]
    assert produtos[C68]["variantes"] == [
        {"codigo": "dg060", "nome": "Uranyx C68 Plus 16.128 - cinza", "cor": "cinza"},
        {"codigo": "dg061", "nome": "Uranyx C68 Plus 16.128 - Dourado", "cor": "Dourado"},
        {"codigo": "dg062", "nome": "Uranyx C68 Plus 16.128 - Azul", "cor": "Azul"},
        # Só usado, CD e excluído: o código sai, sem nome a inventar.
        {"codigo": "dg063", "nome": None, "cor": None},
    ]
    assert produtos["uranyx F117"]["variantes"] == [
        {"codigo": "dg048", "nome": "Uranyx F117 - Preto", "cor": "Preto"},
        {"codigo": "dg049", "nome": "Uranyx F117 - Laranja", "cor": "Laranja"},
    ]
    # A mala casa pelo SKU igual ao código, e o código do fornecedor entre
    # parênteses não vira cor.
    assert produtos["ABS 24"]["variantes"] == [
        {
            "codigo": "b039.24",
            "nome": "Mala Losangulo M4 tamanho 24 - Cinza Escuro (DT - XS5426 - DT14)",
            "cor": "Cinza Escuro",
        },
        {"codigo": "b040.24", "nome": None, "cor": None},
    ]


async def test_nome_sem_tracinho_nao_tem_cor(client: AsyncClient, db: AsyncSession, make_user):
    dono = await make_user()
    await _produto(db, dono, sku="a019", nome="uranyx airtag - UAT001",
                   pasta="/uranyx/airtag UAT001", fotos=3)
    _cadastro(db, dono, "a019.pi", "Uranyx AirTag IOS/Android UAT001")
    await db.commit()

    r = await client.get(URL, headers=A)
    (airtag,) = r.json()["produtos"]
    assert airtag["variantes"] == [
        {"codigo": "a019", "nome": "Uranyx AirTag IOS/Android UAT001", "cor": None}
    ]
