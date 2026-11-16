"""DUIMP padrão do segmento (migration 0487, Eduardo 16/11/2026).

"quando um produto não tiver duimp, ir a mensagem ou duimp que colocarmos,
deixar por segmento" — usado, avulso e produto fora da tela Importação. Regras
(decisões do dono no mesmo dia):
  - a DUIMP da Importação vence (SKU exato e código-base — o usado b001.20us
    continua com a DUIMP do novo b001);
  - o SKU que sobra leva o texto do segmento (subsegmento vazio → raiz);
  - mesmo portão de hoje: só faturador com `observacao_duimp` (nota cheia).
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    ImportProduct,
    NfFaturador,
    PricingProduct,
    Product,
    Segment,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.services import nf_emissao_gerar as g

_DUIMP_MALA = "produto importado pela duimp 26BR0001398015-8"
_MSG_MALA = "produto importado pela duimp 26BR0009999999-1"
_MSG_24 = "mala 24 importada pela duimp 26BR0002222222-2"
_MSG_CEL = "celular importado pela duimp 26BR0003333333-3"
_MSG_ACESS = "acessorio importado pela duimp 26BR0004444444-4"


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _arvore(db: AsyncSession, **textos: str | None) -> dict[str, Segment]:
    """Árvore igual à de produção (slugs das raízes = os do departamento)."""
    raizes = {
        "celular": Segment(name="Celular", slug="celular", duimp_padrao=textos.get("celular")),
        "mala": Segment(name="Mala", slug="mala", duimp_padrao=textos.get("mala")),
        "eletro": Segment(name="Eletro", slug="eletro", duimp_padrao=textos.get("eletro")),
        "catalogo": Segment(name="Catálogo", slug="catalogo",
                            duimp_padrao=textos.get("catalogo")),
    }
    db.add_all(raizes.values())
    await db.flush()
    filhos = {
        "cel_regular": Segment(name="Regular", slug="regular",
                               parent_id=raizes["celular"].id,
                               duimp_padrao=textos.get("cel_regular")),
        "cel_acess": Segment(name="Acessórios", slug="acessorios",
                             parent_id=raizes["celular"].id,
                             duimp_padrao=textos.get("cel_acess")),
        "mala_acess": Segment(name="Acessórios", slug="acessorios",
                              parent_id=raizes["mala"].id,
                              duimp_padrao=textos.get("mala_acess")),
        "mala_24": Segment(name='24" acima', slug="24-acima",
                           parent_id=raizes["mala"].id,
                           duimp_padrao=textos.get("mala_24")),
        "queima": Segment(name="Queima de estoque", slug="queima-de-estoque",
                          parent_id=raizes["mala"].id, active=False,
                          duimp_padrao=textos.get("queima")),
        "cat_acess": Segment(name="Acessórios", slug="acessorios",
                             parent_id=raizes["catalogo"].id,
                             duimp_padrao=textos.get("cat_acess")),
    }
    db.add_all(filhos.values())
    await db.flush()
    return {**raizes, **filhos}


def _produto(admin: User, sku: str, seg: Segment | None = None, **kw) -> Product:
    return Product(user_id=admin.id, sku=sku, name=f"Produto {sku}",
                   segment_id=seg.id if seg else None, **kw)


async def _faturador_e_loja(
    db: AsyncSession, admin: User, *, loja: str, duimp: bool = True
) -> NfFaturador:
    fat = NfFaturador(nome="bling 100%", modo="bling", nf_cheia=True,
                      sku_fonte="principal", nome_fonte="produto",
                      observacao_duimp=duimp)
    db.add(fat)
    await db.flush()
    db.add(StoreInfo(user_id=admin.id, platform="amazon", account_name="am1",
                     bling_store_id=loja, nf_faturador_id=fat.id))
    await db.flush()
    return fat


def _pedido(numero: str, loja: str, skus: list[str]) -> list[BlingOrder]:
    return [
        BlingOrder(
            numero=numero, data=datetime(2026, 11, 16, tzinfo=UTC), loja=loja,
            situacao="15", item_index=i, item_codigo=sku, item_descricao=f"Item {sku}",
            item_quantidade=1, itemvalor=100, nome_destinatario="Cliente Teste",
            cep_destino="30570050", endereco_destino="Rua A", numero_destino="1",
            bairro_destino="Centro", cidade_destino="Belo Horizonte", uf_destino="MG",
        )
        for i, sku in enumerate(skus)
    ]


async def _observacao(db: AsyncSession, numero: str) -> str:
    res = await g.gerar_por_faturador(db, [numero])
    linhas = list(csv.reader(
        io.StringIO(res.blocos[0].planilha.decode("utf-8-sig")), delimiter=";"
    ))
    i = linhas[0].index("Observações")
    return linhas[1][i]


# ── junção (pura) ─────────────────────────────────────────────────────────────


def test_juntar_duimps_sem_repetir_e_parte_a_parte():
    assert g._juntar_duimps(["A", None, "", "B", "A"]) == "A | B"
    # kit com peças de segmentos diferentes já vem com " | ": entra parte a parte
    assert g._juntar_duimps(["A | B", "B", "C"]) == "A | B | C"
    assert g._juntar_duimps([None, "  "]) is None


def test_pecas_e_lote():
    assert g._pecas_do_sku("DG053.sp + a001.sp") == ["dg053.sp", "a001.sp"]
    assert g._candidatos_da_peca("dg053.us") == ["dg053.us", "dg053"]
    # número não é lote (tamanho/voltagem)
    assert g._candidatos_da_peca("b027.24") == ["b027.24"]


# ── resolução do texto ────────────────────────────────────────────────────────


async def test_ninguem_preencheu_nao_devolve_nada(db: AsyncSession, admin: User):
    seg = await _arvore(db)
    db.add(_produto(admin, "b027.24", seg["mala_24"]))
    await db.commit()
    assert await g._texto_segmento_por_sku(db, {"b027.24"}) == {}


async def test_texto_do_subsegmento_e_heranca_da_raiz(db: AsyncSession, admin: User):
    seg = await _arvore(db, mala=_MSG_MALA, mala_24=_MSG_24)
    db.add_all([
        _produto(admin, "b027.24", seg["mala_24"]),     # subsegmento com texto
        _produto(admin, "a006", seg["mala_acess"]),     # subsegmento vazio → raiz
    ])
    await db.commit()
    assert await g._texto_segmento_por_sku(db, {"b027.24", "a006"}) == {
        "b027.24": _MSG_24,
        "a006": _MSG_MALA,
    }


async def test_segmento_desativado_vale(db: AsyncSession, admin: User):
    """A "Queima de estoque" está desativada e é onde estão os usados de mala."""
    seg = await _arvore(db, mala=_MSG_MALA, queima="mala usada - sem DUIMP")
    db.add(_produto(admin, "z0331.mala", seg["queima"]))
    await db.commit()
    assert await g._texto_segmento_por_sku(db, {"z0331.mala"}) == {
        "z0331.mala": "mala usada - sem DUIMP",
    }


async def test_tabela_de_precos_pela_variante_exata(db: AsyncSession, admin: User):
    """Produto sem segmento: vale a Tabela de Preços pela variante EXATA, com
    Celular/Mala/Eletro antes de Catálogo; código-base não conta."""
    seg = await _arvore(db, mala_24=_MSG_24, cel_acess=_MSG_ACESS, cat_acess="CATALOGO")
    db.add_all([
        _produto(admin, "b028.24"),  # sem segmento no cadastro
        PricingProduct(user_id=admin.id, sku="b028.12, b028.24", name="Chanfrada M3",
                       segment_id=seg["mala_24"].id),
        PricingProduct(user_id=admin.id, sku="a082", name="Capa (catálogo)",
                       segment_id=seg["cat_acess"].id),
        PricingProduct(user_id=admin.id, sku="a082", name="Capa",
                       segment_id=seg["cel_acess"].id),
    ])
    await db.commit()
    out = await g._texto_segmento_por_sku(db, {"b028.24", "a082", "b028"})
    assert out == {"b028.24": _MSG_24, "a082": _MSG_ACESS}


async def test_sem_segmento_cai_na_raiz_do_departamento(db: AsyncSession, admin: User):
    """z0217 (Fone AVULSO): sem segmento e fora da Tabela — a categoria do Bling
    ("Acessórios Celular") manda para a raiz Celular."""
    await _arvore(db, celular=_MSG_CEL)
    db.add(_produto(admin, "z0217", categoria_nome="Acessórios Celular"))
    await db.commit()
    assert await g._texto_segmento_por_sku(db, {"z0217", "fake.fake"}) == {
        "z0217": _MSG_CEL,
    }


async def test_kit_peca_importada_leva_a_duimp_e_nao_depende_da_ordem(
    db: AsyncSession, admin: User
):
    """Revisão de 16/11: no kit, cada peça segue a mesma ordem do SKU simples —
    a peça com DUIMP na Importação leva a DUIMP dela, só a peça sem DUIMP leva o
    texto do segmento, e trocar a ordem das peças só troca a ordem das linhas."""
    seg = await _arvore(db, cel_regular=_MSG_CEL, cel_acess=_MSG_ACESS, mala=_MSG_MALA)
    db.add_all([
        ImportProduct(categoria="celular", sku="a001.cd", custo_bling=Decimal("0"),
                      duimp=_DUIMP_MALA),           # código-base a001 tem DUIMP
        ImportProduct(categoria="mala", sku="b104.20", custo_bling=Decimal("0"),
                      duimp=_DUIMP_MALA),
        ImportProduct(categoria="mala", sku="a075", custo_bling=Decimal("0")),  # sem DUIMP
        _produto(admin, "dg1022.pi", seg["cel_regular"]),
        _produto(admin, "a001.pi", seg["cel_acess"]),
        _produto(admin, "a075", seg["mala_acess"]),
    ])
    await db.commit()
    out = await g._duimp_por_sku(
        db, {"dg1022.pi+a001.pi", "a001.pi+dg1022.pi", "b104.20+a075", "a001.pi"}
    )
    assert out == {
        "dg1022.pi+a001.pi": f"{_MSG_CEL} | {_DUIMP_MALA}",
        "a001.pi+dg1022.pi": f"{_DUIMP_MALA} | {_MSG_CEL}",
        # 1ª peça com DUIMP: a outra peça (sem DUIMP) ainda leva o texto (raiz Mala)
        "b104.20+a075": f"{_DUIMP_MALA} | {_MSG_MALA}",
        "a001.pi": _DUIMP_MALA,
    }


async def test_kit_sem_texto_em_segmento_nenhum_so_leva_duimps(
    db: AsyncSession, admin: User
):
    """Sem texto nos segmentos (logo depois de publicar), o kit leva só as
    DUIMPs da Importação das peças."""
    await _arvore(db)
    db.add_all([
        ImportProduct(categoria="mala", sku="b104.20", custo_bling=Decimal("0"),
                      duimp=_DUIMP_MALA),
        ImportProduct(categoria="mala", sku="a075", custo_bling=Decimal("0")),
    ])
    await db.commit()
    assert await g._duimp_por_sku(db, {"b104.20+a075+bp003"}) == {
        "b104.20+a075+bp003": _DUIMP_MALA,
    }


async def test_kit_peca_a_peca_e_lote(db: AsyncSession, admin: User):
    seg = await _arvore(db, cel_regular=_MSG_CEL, cel_acess=_MSG_ACESS)
    db.add_all([
        _produto(admin, "dg053", seg["cel_regular"]),   # só o SKU sem o lote
        _produto(admin, "a001", seg["cel_acess"]),
    ])
    await db.commit()
    out = await g._texto_segmento_por_sku(db, {"dg053.sp+a001.sp"})
    assert out == {"dg053.sp+a001.sp": f"{_MSG_CEL} | {_MSG_ACESS}"}


# ── ponta a ponta: nota e etiqueta ────────────────────────────────────────────


async def test_nota_leva_duimp_e_texto_do_segmento(db: AsyncSession, admin: User):
    seg = await _arvore(db, mala=_MSG_MALA, mala_24=_MSG_24)
    fat = await _faturador_e_loja(db, admin, loja="950001")
    db.add_all([
        ImportProduct(categoria="mala", sku="b001.20", custo_bling=Decimal("0"),
                      duimp=_DUIMP_MALA),
        _produto(admin, "b001.20", seg["mala_24"]),
        _produto(admin, "b027.24", seg["mala_24"]),
        _produto(admin, "b036.24", seg["mala_24"]),
        _produto(admin, "a006", seg["mala_acess"]),
        *_pedido("85001", "950001", ["b001.20", "b027.24", "b036.24", "a006"]),
    ])
    await db.commit()
    assert fat.observacao_duimp
    # Importação primeiro; o texto do segmento sem repetir; a006 herda da raiz.
    assert await _observacao(db, "85001") == f"{_DUIMP_MALA} | {_MSG_24} | {_MSG_MALA}"


async def test_usado_continua_com_a_duimp_do_novo(db: AsyncSession, admin: User):
    """Decisão do dono: b001.20us acha a DUIMP do b001 pelo código-base — o
    texto do segmento (Queima) não passa por cima."""
    seg = await _arvore(db, queima="mala usada")
    await _faturador_e_loja(db, admin, loja="950002")
    db.add_all([
        ImportProduct(categoria="mala", sku="b001.20", custo_bling=Decimal("0"),
                      duimp=_DUIMP_MALA),
        _produto(admin, "b001.20us", seg["queima"]),
        *_pedido("85002", "950002", ["b001.20us"]),
    ])
    await db.commit()
    assert await _observacao(db, "85002") == _DUIMP_MALA


async def test_faturador_sem_duimp_nao_leva_texto_do_segmento(
    db: AsyncSession, admin: User
):
    """Mesmo portão de 15/09: nota a 1%/2% (sem observacao_duimp) sai sem nada."""
    seg = await _arvore(db, mala=_MSG_MALA)
    await _faturador_e_loja(db, admin, loja="950003", duimp=False)
    db.add_all([
        _produto(admin, "b027.24", seg["mala_24"]),
        *_pedido("85003", "950003", ["b027.24"]),
    ])
    await db.commit()
    assert await _observacao(db, "85003") == ""


async def test_etiqueta_leva_o_texto_do_segmento(db: AsyncSession, admin: User):
    """A etiqueta (/agent/etiqueta) usa a mesma busca: a linha DUIMP sai com o
    texto do segmento; faturador sem observacao_duimp → sem linha."""
    from app.routers import nf as nf_router

    seg = await _arvore(db, mala=_MSG_MALA, mala_24=_MSG_24)
    await _faturador_e_loja(db, admin, loja="950004")
    await _faturador_e_loja(db, admin, loja="950005", duimp=False)
    db.add_all([
        ImportProduct(categoria="mala", sku="b001.20", custo_bling=Decimal("0"),
                      duimp=_DUIMP_MALA),
        _produto(admin, "b027.24", seg["mala_24"]),
        *_pedido("85004", "950004", ["b027.24", "b001.20", "b027.24"]),
        *_pedido("85005", "950005", ["b027.24"]),
    ])
    await db.commit()
    assert await nf_router._duimp_do_pedido(db, "85004") == f"{_MSG_24} | {_DUIMP_MALA}"
    assert await nf_router._duimp_do_pedido(db, "85005") is None
