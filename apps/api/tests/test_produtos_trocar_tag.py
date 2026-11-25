"""Produtos 2 › Configuração › "Tag" (M-46, Harry 24/11/2026): trocar a tag (= o código) no Bling.

Prévia com cada motivo de pular; a execução recalcula no servidor, troca só os "alterar" com
PATCH {"codigo"} (nunca PUT), confere com GET quando a resposta é duvidosa (nunca repete o PATCH),
leva o código novo ao DaVinci pelo caminho do webhook e registra cada item. Falha do Bling vira
"falhou" sem parar os outros. O Bling é sempre falso aqui."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingKitComponent,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    PricingProduct,
    Product,
    ProductLink,
    ProdutoCadastro,
    ProdutoCadastroTipo,
    ProdutoTrocaTag,
    ProdutoTrocaTagItem,
    Segment,
    UserRole,
)
from app.models.alert import Alert
from app.models.produto_cadastro import CHAVE_TAG_ENVIO
from app.security.cipher import encrypt_json
from app.services import devolution_stock_return as dsr
from app.services import produto_troca_tag as svc

pytestmark = pytest.mark.asyncio

URL = "/api/produtos/trocar-tag"
TAGS = (".cd", ".ci", ".pi", ".ra", ".sa", ".sp")


def _erro(metodo: str, status: int) -> httpx.HTTPStatusError:
    req = httpx.Request(metodo, "https://api.bling.com.br/Api/v3/produtos/1")
    resp = httpx.Response(
        status,
        request=req,
        json={"error": {"description": "Código já cadastrado"}} if status < 500 else None,
    )
    return httpx.HTTPStatusError(str(status), request=req, response=resp)


class FakeBling:
    """O "Bling" de mentira: `codigos` = id → código lá. `falhas_patch[id]` = [(exceção,
    aplicou?)]; `falhas_get[id]` = [exceção] por GET; `extras[código]` = outros produtos com
    aquele código; `consulta_falha` = códigos cuja consulta dá erro."""

    def __init__(self, codigos: dict[int, str]):
        self.codigos = dict(codigos)
        self.extras: dict[str, list[dict]] = {}
        self.chamadas: list[tuple] = []
        self.falhas_patch: dict[int, list[tuple[Exception, bool]]] = {}
        self.falhas_get: dict[int, list[Exception]] = {}
        self.consulta_falha: set[str] = set()
        self.produto_extra: dict[int, dict] = {}

    async def produtos_com_codigo(self, codigo):
        self.chamadas.append(("consulta", codigo))
        if codigo.lower() in self.consulta_falha:
            raise RuntimeError("bling fora")
        out = [
            {"id": pid, "codigo": c, "situacao": "A"}
            for pid, c in self.codigos.items()
            if c.lower() == codigo.lower()
        ]
        return out + self.extras.get(codigo.lower(), [])

    async def get_product(self, pid):
        self.chamadas.append(("GET", pid))
        fila = self.falhas_get.get(pid)
        if fila:
            raise fila.pop(0)
        base = {"id": pid, "codigo": self.codigos[pid], "formato": "S", "situacao": "A"}
        return {**base, "tipo": "P", **self.produto_extra.get(pid, {})}

    async def update_product_codigo(self, pid, codigo):
        self.chamadas.append(("PATCH", pid, codigo))
        fila = self.falhas_patch.get(pid)
        if fila:
            exc, aplicou = fila.pop(0)
            if aplicou:
                self.codigos[pid] = codigo
            raise exc
        self.codigos[pid] = codigo
        return {"id": pid}

    async def _request(self, *a, **kw):  # pragma: no cover — nada mais pode ser chamado
        raise AssertionError(f"chamada inesperada ao Bling: {a}")

    def metodos(self) -> set[str]:
        return {c[0] for c in self.chamadas}

    def patches(self) -> list[tuple[int, str]]:
        return [(c[1], c[2]) for c in self.chamadas if c[0] == "PATCH"]


PRODUTOS: dict[str, tuple[int | None, dict]] = {
    "z2819": (880501, {}),
    "z2820": (880502, {}),
    "dg050.ra": (880503, {}),
    "dg019.sa": (880504, {}),
    "dg019.ra": (880505, {}),
    "dg060.ra": (880506, {}),
    "dg053.sp+a001.sp": (880507, {"formato": "E"}),
    "kitx": (880508, {}),
    "a001.sp": (880509, {}),
    "a002.ra": (880510, {}),
    "b011": (880511, {"formato": "E"}),
    "m100": (880512, {}),
    "b045.18": (880513, {}),
    "dg070.ra": (880514, {}),
    "dg080.ra": (880515, {"situacao": "I"}),
    "dg081.ra": (None, {}),
    "dg090.ra": (880516, {}),
    "c200.ra": (880517, {}),
    "c200.ci": (880518, {}),
    "k300": (880519, {}),
}


@pytest.fixture(autouse=True)
async def _limpa_estrutura(db: AsyncSession):
    """bling_kit_components não é limpo entre testes: ids 880xxx são daqui."""

    async def limpar():
        await db.execute(
            delete(BlingKitComponent).where(
                BlingKitComponent.kit_bling_product_id.between(880000, 880999)
            )
        )
        await db.commit()

    await limpar()
    yield
    await limpar()


@pytest.fixture
async def cenario(db: AsyncSession, make_user):
    dono = await make_user()
    tipo = ProdutoCadastroTipo(nome="Tag de envio", chave=CHAVE_TAG_ENVIO)
    db.add(tipo)
    await db.flush()
    db.add_all(ProdutoCadastro(tipo_id=tipo.id, nome=t) for t in TAGS)
    mala = Segment(name="Mala", slug="mala")
    catalogo = Segment(name="Catálogo", slug="catalogo")
    db.add_all([mala, catalogo])
    await db.flush()
    produtos = {}
    for sku, (bid, extra) in PRODUTOS.items():
        base = {"situacao": "A", "formato": "S", "name": f"Produto {sku}"}
        base.update(extra)
        produtos[sku] = Product(user_id=dono.id, sku=sku, bling_product_id=bid, **base)
    db.add_all(produtos.values())
    await db.flush()
    # A Tabela de Preços casa mala e catálogo pelo código inteiro.
    db.add_all(
        [
            PricingProduct(
                user_id=dono.id,
                sku="m100,m101",
                name="Mala M100",
                segment_id=mala.id,
                cost_kit1=Decimal("0"),
            ),
            PricingProduct(
                user_id=dono.id,
                sku="k300",
                name="Catálogo K300",
                segment_id=catalogo.id,
                cost_kit1=Decimal("0"),
            ),
        ]
    )
    # Kit pela estrutura do Bling: kitx é composto; a002.ra é peça do b011.
    db.add_all(
        [
            BlingKitComponent(kit_bling_product_id=880508, component_bling_product_id=880600),
            BlingKitComponent(kit_bling_product_id=880511, component_bling_product_id=880510),
        ]
    )
    amazon = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.AMAZON,
        name="amazon-teste",
        credentials=encrypt_json({}),
    )
    db.add(amazon)
    await db.flush()
    db.add(
        ProductLink(
            user_id=dono.id,
            product_id=produtos["dg070.ra"].id,
            integration_id=amazon.id,
            platform=IntegrationPlatform.AMAZON,
            external_id="B0TESTE",
            last_sync_status=LinkSyncStatus.OK,
        )
    )
    await db.commit()
    return produtos


@pytest.fixture
def bling(monkeypatch):
    fake = FakeBling({bid: sku for sku, (bid, _x) in PRODUTOS.items() if bid})
    # dg060.sa só existe no Bling (outro produto); a consulta de dg090.sa dá erro.
    fake.extras["dg060.sa"] = [{"id": 990001, "codigo": "dg060.sa", "situacao": "I"}]
    fake.consulta_falha.add("dg090.sa")

    async def _client(_session):
        return fake

    monkeypatch.setattr(dsr, "_get_bling_client", _client)
    return fake


@pytest.fixture
async def admin(db: AsyncSession, make_user, auth_as: Callable):
    u = await make_user(role=UserRole.ADMIN)
    u.name = "heisenberg"
    await db.commit()
    auth_as(u)
    return u


async def _sku_de(db: AsyncSession, bid: int) -> str:
    return (
        await db.execute(
            select(Product.sku)
            .where(Product.bling_product_id == bid)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def test_previa_com_cada_motivo_de_pular(
    client: AsyncClient, db: AsyncSession, cenario, bling, admin
):
    skus = [*PRODUTOS, "nada999", "Z2819"]
    r = await client.post(f"{URL}/previa", json={"skus": skus, "tag": ".SA"})
    assert r.status_code == 200, r.text
    body = r.json()
    por_sku = {i["sku"]: (i["acao"], i["motivo"], i["novo"]) for i in body["itens"]}
    assert [i["sku"] for i in body["itens"]] == skus
    esperado = {
        "z2819": ("alterar", None, "z2819.sa"),
        "z2820": ("alterar", None, "z2820.sa"),
        "dg050.ra": ("alterar", None, "dg050.sa"),
        "dg019.sa": ("pular", "já está com a tag .sa", "dg019.sa"),
        "dg019.ra": ("pular", "já existe: use Transferir no estoque", "dg019.sa"),
        "dg060.ra": ("pular", "já existe: use Transferir no estoque", "dg060.sa"),
        "dg053.sp+a001.sp": ("pular", "kit não muda de tag: troque nas peças", None),
        "kitx": ("pular", "kit não muda de tag: troque nas peças", "kitx.sa"),
        "a001.sp": ("pular", "faz parte de kit (dg053.sp+a001.sp)", "a001.sa"),
        "a002.ra": ("pular", "faz parte de kit (b011)", "a002.sa"),
        "b011": ("pular", "kit não muda de tag: troque nas peças", "b011.sa"),
        "m100": ("pular", "mala: a Tabela de Preços casa pelo código inteiro", "m100.sa"),
        "b045.18": ("pular", "mala: a Tabela de Preços casa pelo código inteiro", "b045.18.sa"),
        "dg070.ra": ("pular", "tem anúncio da Amazon vinculado", "dg070.sa"),
        "dg080.ra": ("pular", "inativo no Bling", "dg080.sa"),
        "dg081.ra": ("pular", "sem id no Bling", "dg081.sa"),
        "dg090.ra": (
            "pular",
            "não deu para conferir no Bling se dg090.sa já existe: tente de novo",
            "dg090.sa",
        ),
        "c200.ra": ("alterar", None, "c200.sa"),
        "c200.ci": ("pular", "outro item da lista (c200.ra) já vai virar c200.sa", "c200.sa"),
        "k300": ("pular", "catálogo: a Tabela de Preços casa pelo código inteiro", "k300.sa"),
        "nada999": ("pular", "não encontrado", "nada999.sa"),
        "Z2819": ("pular", "repetido na lista", "Z2819.sa"),
    }
    # O "novo" de um kit não importa (é pulado); os demais têm de bater.
    for sku, (acao, motivo, novo) in esperado.items():
        got = por_sku[sku]
        assert got[:2] == (acao, motivo), (sku, got)
        if novo is not None:
            assert got[2] == novo, (sku, got)
    assert (body["alterar"], body["pular"]) == (4, len(skus) - 4)
    # Prévia não grava nem troca nada.
    assert "PATCH" not in bling.metodos()
    assert (await db.execute(select(ProdutoTrocaTag))).first() is None
    assert await _sku_de(db, 880501) == "z2819"


async def test_execucao_troca_so_os_alterar_com_patch_e_registra(
    client: AsyncClient, db: AsyncSession, cenario, bling, admin
):
    bling.falhas_patch[880502] = [(_erro("PATCH", 400), False)]
    corpo = {"skus": ["z2819", "z2820", "dg050.ra", "dg019.sa", "a001.sp"], "tag": ".sa"}
    h = {"Idempotency-Key": "chave-tag-0001"}
    r = await client.post(URL, json=corpo, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    estados = {i["sku"]: (i["estado"], i["novo"]) for i in body["itens"]}
    assert estados == {
        "z2819": ("ok", "z2819.sa"),
        "z2820": ("falhou", "z2820.sa"),
        "dg050.ra": ("ok", "dg050.sa"),
        "dg019.sa": ("pulado", "dg019.sa"),
        "a001.sp": ("pulado", "a001.sa"),
    }
    falha = next(i for i in body["itens"] if i["sku"] == "z2820")
    assert "HTTP 400" in falha["motivo"] and "Código já cadastrado" in falha["motivo"]
    assert (body["ok"], body["pulado"], body["falhou"]) == (2, 2, 1)
    # Só PATCH {"codigo"} (nunca PUT), um por item "alterar", na ordem.
    assert bling.metodos() <= {"consulta", "GET", "PATCH"}
    assert bling.patches() == [
        (880501, "z2819.sa"),
        (880502, "z2820.sa"),
        (880503, "dg050.sa"),
    ]
    # O DaVinci acompanhou na hora (caminho do webhook); a falha não mudou nada.
    assert await _sku_de(db, 880501) == "z2819.sa"
    assert await _sku_de(db, 880502) == "z2820"
    assert await _sku_de(db, 880503) == "dg050.sa"
    troca = (await db.execute(select(ProdutoTrocaTag))).scalar_one()
    assert (troca.tag, troca.criado_por, troca.criado_por_nome) == (".sa", admin.id, "heisenberg")
    assert troca.concluido_em is not None
    itens = (
        (
            await db.execute(
                select(ProdutoTrocaTagItem)
                .where(ProdutoTrocaTagItem.troca_id == troca.id)
                .order_by(ProdutoTrocaTagItem.ordem)
            )
        )
        .scalars()
        .all()
    )
    assert [(i.sku_antigo, i.sku_novo, i.estado) for i in itens] == [
        ("z2819", "z2819.sa", "ok"),
        ("z2820", "z2820.sa", "falhou"),
        ("dg050.ra", "dg050.sa", "ok"),
        ("dg019.sa", "dg019.sa", "pulado"),
        ("a001.sp", "a001.sa", "pulado"),
    ]
    assert itens[3].motivo == "já está com a tag .sa"
    assert all(i.concluido_at is not None for i in itens)
    assert itens[0].tentativa_at is not None

    # A mesma chave: o mesmo resultado, nada trocado de novo.
    antes = len(bling.chamadas)
    r2 = await client.post(URL, json=corpo, headers=h)
    assert r2.status_code == 200
    assert r2.json() == body
    assert len(bling.chamadas) == antes
    # A mesma chave com outro pedido: recusada.
    r3 = await client.post(URL, json={**corpo, "tag": ".sp"}, headers=h)
    assert r3.status_code == 409
    assert r3.json()["detail"]["code"] == "idempotency_conflito"


async def test_resposta_duvidosa_confere_com_get_e_nunca_repete_o_patch(
    client: AsyncClient, db: AsyncSession, cenario, bling, admin
):
    bling.falhas_patch[880501] = [(_erro("PATCH", 504), True)]  # gravou e respondeu 504
    bling.falhas_patch[880502] = [(httpx.ReadTimeout("timeout"), False)]  # não gravou
    bling.falhas_patch[880503] = [(_erro("PATCH", 502), False)]
    # dg050.ra: o GET de antes funciona; o GET de depois do 502 falha.
    bling.falhas_get[880503] = []
    get_original = bling.get_product
    gets: dict[int, int] = {}

    async def get_contado(pid):
        gets[pid] = gets.get(pid, 0) + 1
        if pid == 880503 and gets[pid] == 2:
            bling.chamadas.append(("GET", pid))
            raise httpx.ConnectError("caiu")
        return await get_original(pid)

    bling.get_product = get_contado
    r = await client.post(
        URL,
        json={"skus": ["z2819", "z2820", "dg050.ra"], "tag": ".sa"},
        headers={"Idempotency-Key": "chave-tag-0002"},
    )
    assert r.status_code == 200, r.text
    itens = {i["sku"]: i for i in r.json()["itens"]}
    assert itens["z2819"]["estado"] == "ok"
    # GET logo depois ainda no código antigo: o Bling pode aplicar depois — sem
    # confirmação (na tela conta como falha, com o motivo), nunca "nada mudou".
    assert itens["z2820"]["estado"] == "falhou"
    assert itens["z2820"]["situacao"] == "sem_confirmacao"
    assert itens["z2820"]["motivo"] == (
        "o Bling não confirmou a troca; o DaVinci confere de novo em alguns minutos"
    )
    assert itens["dg050.ra"]["estado"] == "falhou"
    assert itens["dg050.ra"]["situacao"] == "sem_confirmacao"
    assert "não deu para conferir" in itens["dg050.ra"]["motivo"]
    assert (r.json()["falhou"], r.json()["sem_confirmacao"]) == (2, 2)
    # Um PATCH por item, nunca repetido.
    assert bling.patches() == [
        (880501, "z2819.sa"),
        (880502, "z2820.sa"),
        (880503, "dg050.sa"),
    ]
    assert await _sku_de(db, 880501) == "z2819.sa"  # confirmado pelo GET → refletido
    assert await _sku_de(db, 880502) == "z2820"
    assert await _sku_de(db, 880503) == "dg050.ra"
    estados = dict(
        (await db.execute(select(ProdutoTrocaTagItem.sku_antigo, ProdutoTrocaTagItem.estado))).all()
    )
    assert estados == {"z2819": "ok", "z2820": "sem_confirmacao", "dg050.ra": "sem_confirmacao"}
    avisos = (
        (await db.execute(select(Alert).where(Alert.dedupe_key.like("produto_troca_tag:%"))))
        .scalars()
        .all()
    )
    assert len(avisos) == 2 and {a.user_id for a in avisos} == {admin.id}


async def test_confere_o_bling_ao_vivo_antes_de_trocar(
    client: AsyncClient, db: AsyncSession, cenario, bling, admin
):
    """O código no Bling já é outro (o DaVinci ainda não acompanhou) ou o
    produto virou kit/inativo lá: pula sem PATCH. Se o Bling já tem o código
    novo neste mesmo produto, só acompanha."""
    bling.codigos[880501] = "z2819-x"
    bling.produto_extra[880502] = {"formato": "E"}
    bling.produto_extra[880517] = {"situacao": "I"}
    bling.codigos[880503] = "dg050.sa"  # trocado no Bling por fora, mesmo produto
    r = await client.post(
        URL,
        json={"skus": ["z2819", "z2820", "c200.ra", "dg050.ra"], "tag": ".sa"},
        headers={"Idempotency-Key": "chave-tag-0003"},
    )
    assert r.status_code == 200, r.text
    itens = {i["sku"]: i for i in r.json()["itens"]}
    assert itens["z2819"]["estado"] == "pulado"
    assert "no Bling o código é z2819-x" in itens["z2819"]["motivo"]
    assert (itens["z2820"]["estado"], itens["z2820"]["motivo"]) == (
        "pulado",
        "kit não muda de tag: troque nas peças",
    )
    assert (itens["c200.ra"]["estado"], itens["c200.ra"]["motivo"]) == (
        "pulado",
        "inativo no Bling",
    )
    assert itens["dg050.ra"]["estado"] == "ok"
    assert bling.patches() == []
    assert await _sku_de(db, 880503) == "dg050.sa"
    assert await _sku_de(db, 880501) == "z2819"


async def test_codigo_criado_no_bling_depois_da_previa_e_pulado(
    client: AsyncClient, db: AsyncSession, cenario, bling, admin
):
    """A execução confere o código novo no Bling item a item, logo antes do
    PATCH: se outro produto ganhou o código depois da prévia, pula."""
    corpo = {"skus": ["z2819"], "tag": ".sa"}
    assert (await client.post(f"{URL}/previa", json=corpo)).json()["alterar"] == 1
    bling.extras["z2819.sa"] = [{"id": 990002, "codigo": "z2819.sa", "situacao": "A"}]
    r = await client.post(URL, json=corpo, headers={"Idempotency-Key": "chave-tag-0005"})
    assert r.status_code == 200, r.text
    (item,) = r.json()["itens"]
    assert (item["estado"], item["motivo"]) == ("pulado", "já existe: use Transferir no estoque")
    assert bling.patches() == []
    assert await _sku_de(db, 880501) == "z2819"


async def test_eletro_nao_troca_se_a_linha_da_tabela_mudaria(
    client: AsyncClient, db: AsyncSession, cenario, bling, admin, make_user
):
    """Eletro com o mesmo código base em várias linhas: o código mais
    específico decide a linha (sku_match.casa_pelo_codigo, 25/11/2026). A troca
    que faria a Tabela ligar o produto a outra linha (ou a nenhuma) é pulada;
    a que mantém a mesma linha passa (celular pelo código base, eletro com o
    pedaço reivindicado antes da tag)."""
    dono = await make_user()
    eletro = Segment(name="Eletro", slug="eletro")
    celular = Segment(name="Celular", slug="celular")
    db.add_all([eletro, celular])
    await db.flush()
    linhas = [
        ("uaf001m1.2l.ra", eletro),  # pote 2,5 L: casado pelo SKU inteiro com a tag
        ("uaf001m1.110", eletro),  # airfryer: mesmo código base
        ("uaf002.2l", eletro),  # casado pelo pedaço antes da tag
        ("dg050", celular),  # celular: código base
    ]
    db.add_all(
        PricingProduct(
            user_id=dono.id, sku=sku, name=sku, segment_id=seg.id, cost_kit1=Decimal("0")
        )
        for sku, seg in linhas
    )
    for sku, bid in (("uaf001m1.2l.ra", 881001), ("uaf002.2l", 881002)):
        db.add(
            Product(
                user_id=dono.id,
                sku=sku,
                bling_product_id=bid,
                situacao="A",
                formato="S",
                name=f"Produto {sku}",
            )
        )
        bling.codigos[bid] = sku
    await db.commit()
    r = await client.post(
        f"{URL}/previa", json={"skus": ["uaf001m1.2l.ra", "uaf002.2l", "dg050.ra"], "tag": ".sp"}
    )
    assert r.status_code == 200, r.text
    por_sku = {i["sku"]: (i["acao"], i["motivo"]) for i in r.json()["itens"]}
    assert por_sku["uaf001m1.2l.ra"] == ("pular", svc.MOTIVO_TABELA_LINHA)
    assert por_sku["uaf002.2l"] == ("alterar", None)
    assert por_sku["dg050.ra"] == ("alterar", None)


async def test_validacoes_e_so_admin(
    client: AsyncClient, make_user, auth_as: Callable, cenario, bling, admin
):
    h = {"Idempotency-Key": "chave-tag-0004"}
    r = await client.post(f"{URL}/previa", json={"skus": ["z2819"], "tag": ".xx"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "tag_invalida"
    r = await client.post(
        URL, json={"skus": [f"s{i}" for i in range(201)], "tag": ".sa"}, headers=h
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "skus"
    r = await client.post(f"{URL}/previa", json={"skus": [], "tag": ".sa"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "skus"
    r = await client.post(URL, json={"skus": ["z2819"], "tag": ".sa"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "idempotency_key"

    auth_as(await make_user(role=UserRole.USER, permissions={"produtos": {"edit": True}}))
    r = await client.post(f"{URL}/previa", json={"skus": ["z2819"], "tag": ".sa"})
    assert r.status_code == 403
    r = await client.post(URL, json={"skus": ["z2819"], "tag": ".sa"}, headers=h)
    assert r.status_code == 403
    assert bling.chamadas == []


# ── Cliente do Bling: PATCH só do código e consulta estrita ──────────────


def _cliente_http(monkeypatch, respostas: list[httpx.Response], chamadas: list):
    from app.services.marketplaces import bling as bling_mod

    class _Http:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kw):
            chamadas.append((method, url, kw))
            r = respostas.pop(0)
            r.request = httpx.Request(method, url)
            return r

    async def _slot(*_a, **_kw):
        return None

    monkeypatch.setattr(bling_mod.httpx, "AsyncClient", _Http)
    monkeypatch.setattr(bling_mod, "_acquire_bling_rate_slot", _slot)
    return bling_mod.BlingClient({"access_token": "x", "expires_at": 4102444800})


async def test_bling_troca_o_codigo_com_patch_e_nao_repete_503(monkeypatch):
    chamadas: list = []
    c = _cliente_http(monkeypatch, [httpx.Response(200, json={"data": {"id": 880501}})], chamadas)
    await c.update_product_codigo(880501, "z2819.sa")
    ((metodo, url, kw),) = chamadas
    assert metodo == "PATCH"
    assert url.endswith("/produtos/880501")
    assert kw["json"] == {"codigo": "z2819.sa"}

    chamadas.clear()
    c = _cliente_http(monkeypatch, [httpx.Response(503), httpx.Response(200)], chamadas)
    with pytest.raises(httpx.HTTPStatusError):
        await c.update_product_codigo(880501, "z2819.sa")
    assert [m for m, _u, _k in chamadas] == ["PATCH"]  # 503 não é repetido: é dúvida


async def test_bling_consulta_do_codigo_e_estrita(monkeypatch):
    chamadas: list = []
    dados = {
        "data": [
            {"id": 1, "codigo": "Z2819.SA", "situacao": "E"},
            {"id": 2, "codigo": "z2819.sa.velho", "situacao": "A"},
        ]
    }
    c = _cliente_http(monkeypatch, [httpx.Response(200, json=dados)], chamadas)
    achados = await c.produtos_com_codigo("z2819.sa")
    assert [a["id"] for a in achados] == [1]  # só o código igual, em qualquer situação
    assert chamadas[0][2]["params"]["criterio"] == 5
    # Filtro ignorado pelo Bling (veio produto sem nada a ver): não prova nada → erro.
    c = _cliente_http(
        monkeypatch, [httpx.Response(200, json={"data": [{"id": 3, "codigo": "a001"}]})], []
    )
    with pytest.raises(RuntimeError):
        await c.produtos_com_codigo("z2819.sa")
    # Erro do Bling sobe (na dúvida não troca), em vez de virar "não existe".
    c = _cliente_http(monkeypatch, [httpx.Response(500)], [])
    with pytest.raises(httpx.HTTPStatusError):
        await c.produtos_com_codigo("z2819.sa")


# ── Reconferência das "sem_confirmacao" (worker) ─────────────────────────


async def _itens(db: AsyncSession) -> dict[str, ProdutoTrocaTagItem]:
    return {
        i.sku_antigo: i
        for i in (
            await db.execute(select(ProdutoTrocaTagItem).execution_options(populate_existing=True))
        )
        .scalars()
        .all()
    }


async def test_sem_confirmacao_o_worker_reconfere_sem_outro_patch(
    client: AsyncClient, db: AsyncSession, cenario, bling, admin
):
    """C4: PATCH com resposta duvidosa e o GET logo depois ainda no código
    antigo. O Bling aplica depois (z2819): o worker vê o código novo, leva ao
    DaVinci e marca ok. O Bling não aplica (z2820): passados 30 min, falhou
    com aviso. Nunca outro PATCH."""
    bling.falhas_patch[880501] = [(httpx.ReadTimeout("timeout"), False)]
    bling.falhas_patch[880502] = [(_erro("PATCH", 504), False)]
    corpo = {"skus": ["z2819", "z2820"], "tag": ".sa"}
    h = {"Idempotency-Key": "chave-tag-0010"}
    r = await client.post(URL, json=corpo, headers=h)
    assert r.status_code == 200, r.text
    assert [i["situacao"] for i in r.json()["itens"]] == ["sem_confirmacao"] * 2
    itens = await _itens(db)
    assert {k: i.estado for k, i in itens.items()} == {
        "z2819": "sem_confirmacao",
        "z2820": "sem_confirmacao",
    }
    t0 = itens["z2820"].tentativa_at
    assert await _sku_de(db, 880501) == "z2819"

    # O Bling aplicou o PATCH do z2819 depois de responder (o GET de agora vê).
    bling.codigos[880501] = "z2819.sa"
    out = await svc.reconferir_sem_confirmacao(bling, agora=t0 + timedelta(minutes=5))
    assert out == {"conferidos": 2, "confirmados": 1, "falharam": 0}
    itens = await _itens(db)
    assert (itens["z2819"].estado, itens["z2819"].motivo) == (
        "ok",
        "o Bling confirmou a troca depois: o DaVinci acompanhou",
    )
    assert itens["z2820"].estado == "sem_confirmacao"  # antes de 30 min: espera
    assert await _sku_de(db, 880501) == "z2819.sa"  # o mesmo reflexo da troca certa

    # 30 min depois o z2820 continua o antigo no Bling: falhou, com aviso.
    out = await svc.reconferir_sem_confirmacao(bling, agora=t0 + timedelta(minutes=31))
    assert out == {"conferidos": 1, "confirmados": 0, "falharam": 1}
    itens = await _itens(db)
    assert itens["z2820"].estado == "falhou"
    assert "30 min depois o código continua z2820: nada mudou" in itens["z2820"].motivo
    assert await _sku_de(db, 880502) == "z2820"
    avisos = (
        (
            await db.execute(
                select(Alert.dedupe_key).where(
                    Alert.dedupe_key.like(f"produto_troca_tag:{itens['z2820'].id}%")
                )
            )
        )
        .scalars()
        .all()
    )
    # O "confira" da hora e o "não mudou" da reconferência.
    assert sorted(avisos) == [
        f"produto_troca_tag:{itens['z2820'].id}",
        f"produto_troca_tag:{itens['z2820'].id}:falhou",
    ]
    # Um PATCH por item, nunca repetido; a reconferência só fez GET.
    assert bling.patches() == [(880501, "z2819.sa"), (880502, "z2820.sa")]
    # Nada mais a reconferir: zero Bling.
    antes = len(bling.chamadas)
    out = await svc.reconferir_sem_confirmacao(bling, agora=t0 + timedelta(minutes=40))
    assert out == {"conferidos": 0, "confirmados": 0, "falharam": 0}
    assert len(bling.chamadas) == antes
    # A mesma chave devolve o resultado reconferido.
    r2 = await client.post(URL, json=corpo, headers=h)
    estados = {i["sku"]: (i["estado"], i["situacao"]) for i in r2.json()["itens"]}
    assert estados == {"z2819": ("ok", "ok"), "z2820": ("falhou", "falhou")}


async def test_reconferencia_pelo_worker_e_janela_de_24h(
    client: AsyncClient, db: AsyncSession, cenario, bling, admin
):
    """O tick do worker (o mesmo caminho) resolve na hora quando o Bling já
    está com o código novo; item de mais de 24 h não é mais lido; GET com
    erro ou outro código no Bling: fica para a pessoa/próximo tick."""
    from app.worker import produto_troca_tag_tick

    for pid in (880501, 880502, 880503):
        bling.falhas_patch[pid] = [(_erro("PATCH", 502), False)]
    r = await client.post(
        URL,
        json={"skus": ["z2819", "z2820", "dg050.ra"], "tag": ".sa"},
        headers={"Idempotency-Key": "chave-tag-0011"},
    )
    assert r.status_code == 200, r.text
    itens = await _itens(db)
    # dg050.ra: de mais de 24 h — não é mais reconferido.
    await db.execute(
        ProdutoTrocaTagItem.__table__.update()
        .where(ProdutoTrocaTagItem.id == itens["dg050.ra"].id)
        .values(tentativa_at=datetime.now(UTC) - timedelta(hours=25))
    )
    await db.commit()
    bling.codigos[880501] = "z2819.sa"  # aplicado depois
    bling.codigos[880503] = "dg050.sa"
    bling.codigos[880502] = "z2820-x"  # alguém mexeu no Bling: a pessoa confere

    gets_dg050 = bling.chamadas.count(("GET", 880503))
    await produto_troca_tag_tick({})
    itens = await _itens(db)
    assert {k: i.estado for k, i in itens.items()} == {
        "z2819": "ok",
        "z2820": "sem_confirmacao",
        "dg050.ra": "sem_confirmacao",
    }
    assert await _sku_de(db, 880501) == "z2819.sa"
    assert await _sku_de(db, 880503) == "dg050.ra"  # fora da janela: não mexe
    assert bling.chamadas.count(("GET", 880503)) == gets_dg050
    # GET com erro: fica (e o outro código também), mesmo depois de 30 min.
    bling.falhas_get[880502] = [httpx.ConnectError("caiu")]
    out = await svc.reconferir_sem_confirmacao(
        bling, agora=datetime.now(UTC) + timedelta(minutes=31)
    )
    assert out == {"conferidos": 1, "confirmados": 0, "falharam": 0}
    out = await svc.reconferir_sem_confirmacao(
        bling, agora=datetime.now(UTC) + timedelta(minutes=32)
    )
    assert out == {"conferidos": 1, "confirmados": 0, "falharam": 0}
    assert (await _itens(db))["z2820"].estado == "sem_confirmacao"
    assert len(bling.patches()) == 3


async def test_carrefour_vinculada_e_pulada_na_previa_e_na_execucao(
    client: AsyncClient, db: AsyncSession, cenario, bling, admin
):
    """C3: a Carrefour acha a oferta pelo SKU e exige o SKU do vínculo igual ao
    do produto — depois da troca a oferta pararia de receber estoque e preço.
    Pula com o motivo do canal (vínculo com SKU ou sem; pelo canal do vínculo
    ou da integração). Mercado Livre, Shopee, TikTok, Temu e Magalu endereçam
    pelo id do anúncio: não pulam. A troca leva o código novo também ao
    vínculo do próprio Bling (a Carrefour de um lote irmão confere esse SKU)."""
    dono = cenario["z2819"].user_id

    def integ(plataforma, nome):
        return Integration(
            user_id=dono,
            platform=plataforma,
            name=nome,
            credentials=encrypt_json({}),
        )

    carrefour = integ(IntegrationPlatform.CARREFOUR, "carrefour-teste")
    outros = {
        p: integ(p, f"{p.value}-teste")
        for p in (
            IntegrationPlatform.ML,
            IntegrationPlatform.SHOPEE,
            IntegrationPlatform.TIKTOK,
            IntegrationPlatform.TEMU,
            IntegrationPlatform.MAGALU,
        )
    }
    bling_integ = integ(IntegrationPlatform.BLING, "bling-teste")
    db.add_all([carrefour, bling_integ, *outros.values()])
    await db.flush()

    def link(sku, integracao, plataforma, ext_id, ext_sku):
        return ProductLink(
            user_id=dono,
            product_id=cenario[sku].id,
            integration_id=integracao.id,
            platform=plataforma,
            external_id=ext_id,
            external_sku=ext_sku,
            last_sync_status=LinkSyncStatus.OK,
        )

    db.add_all(
        [
            # dg050.ra: oferta da Carrefour com o SKU do produto.
            link("dg050.ra", carrefour, IntegrationPlatform.CARREFOUR, "OF-1", "dg050.ra"),
            # z2820: vínculo sem SKU (a Carrefour usa o do produto) e com o
            # canal só na integração.
            link("z2820", carrefour, IntegrationPlatform.BLING, "OF-2", None),
            # z2819: só canais que endereçam pelo id do anúncio + o do Bling.
            *[link("z2819", i, p, f"{p.value}-1", "z2819") for p, i in outros.items()],
            link("z2819", bling_integ, IntegrationPlatform.BLING, "880501", "z2819"),
        ]
    )
    await db.commit()

    corpo = {"skus": ["z2819", "z2820", "dg050.ra"], "tag": ".sa"}
    r = await client.post(f"{URL}/previa", json=corpo)
    assert r.status_code == 200, r.text
    previa = {i["sku"]: (i["acao"], i["motivo"]) for i in r.json()["itens"]}
    assert previa == {
        "z2819": ("alterar", None),
        "z2820": ("pular", "tem anúncio da Carrefour vinculado"),
        "dg050.ra": ("pular", "tem anúncio da Carrefour vinculado"),
    }
    r = await client.post(URL, json=corpo, headers={"Idempotency-Key": "chave-tag-0012"})
    assert r.status_code == 200, r.text
    estados = {i["sku"]: (i["estado"], i["motivo"]) for i in r.json()["itens"]}
    assert estados == {
        "z2819": ("ok", None),
        "z2820": ("pulado", "tem anúncio da Carrefour vinculado"),
        "dg050.ra": ("pulado", "tem anúncio da Carrefour vinculado"),
    }
    assert bling.patches() == [(880501, "z2819.sa")]
    assert await _sku_de(db, 880503) == "dg050.ra"
    links = {
        (lk.platform, lk.external_id): lk.external_sku
        for lk in (
            await db.execute(
                select(ProductLink)
                .where(ProductLink.product_id == cenario["z2819"].id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    }
    # O vínculo do Bling acompanha o código; os anúncios guardam o SKU deles.
    assert links[(IntegrationPlatform.BLING, "880501")] == "z2819.sa"
    assert all(v == "z2819" for k, v in links.items() if k[0] != IntegrationPlatform.BLING)
