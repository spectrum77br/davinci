"""Endpoint de geração da planilha de importação avulsa (Fase 3a).

`POST /api/nf-cadastro/faturamento/gerar-planilha` lê os itens do Bling
principal (bling_orders) já no davinci, aplica a regra do faturador da loja de
cada pedido e devolve o CSV no layout do relatório de vendas do Bling. Pedidos
sem faturador/itens saem em headers; 422 se nenhum gerou.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    NfCatalogoMala,
    NfFaturador,
    Product,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.services.nf_relatorio import COLUNAS


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed_pedido(
    db: AsyncSession, admin: User, faturador: NfFaturador, *, numero: str, loja: str, itens: list[dict]
) -> None:
    for i, it in enumerate(itens):
        db.add(
            BlingOrder(
                numero=numero,
                data=datetime(2026, 6, 23, tzinfo=UTC),
                loja=loja,
                situacao="15",
                item_index=i,
                item_codigo=it["sku"],
                item_descricao=it["nome"],
                item_quantidade=it["qtd"],
                itemvalor=it["unit"],
                nome_destinatario="Cleso Menezes",
                cep_destino="30570050",
                endereco_destino="Rua Emídio Beruto",
                numero_destino="30",
                bairro_destino="Cinquentenário",
                cidade_destino="Belo Horizonte",
                uf_destino="MG",
            )
        )


def _col(row: list[str], nome: str) -> str:
    return row[COLUNAS.index(nome)]


@pytest.mark.asyncio
async def test_gera_planilha_avulso_e_exclusivo(
    db: AsyncSession, client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)

    # Faturador avulso (NF cheia, SKU/nome do principal) na loja 1.
    avulso = NfFaturador(
        nome="bling avulso", modo="bling", nf_cheia=True,
        sku_fonte="principal", nome_fonte="produto", ncm="4202.12.10",
    )
    # Faturador exclusivo (0,1%, a001/embalagem) na loja 2.
    exclusivo = NfFaturador(
        nome="bling exclusivo", modo="bling", nf_cheia=False,
        percentual="0.1", sku_fonte="a001", nome_fonte="embalagem",
    )
    db.add_all([avulso, exclusivo])
    await db.flush()

    db.add(StoreInfo(user_id=admin.id, platform="amazon", account_name="l1",
                     bling_store_id="900001", nf_faturador_id=avulso.id))
    db.add(StoreInfo(user_id=admin.id, platform="shopee", account_name="l2",
                     bling_store_id="900002", nf_faturador_id=exclusivo.id))
    await db.flush()

    await _seed_pedido(db, admin, avulso, numero="800001", loja="900001",
                       itens=[{"sku": "dg053.ci", "nome": "Capa Celular", "qtd": 2, "unit": 500}])
    await _seed_pedido(db, admin, exclusivo, numero="800002", loja="900002",
                       itens=[{"sku": "x1", "nome": "Produto X", "qtd": 1, "unit": 1000}])
    await db.commit()

    r = await client.post(
        "/api/nf-cadastro/faturamento/gerar-planilha",
        json={"numeros": ["800001", "800002"]},
    )
    assert r.status_code == 200, r.text
    assert r.headers["X-Pedidos-Ok"] == "2"
    assert r.headers["X-Pedidos-Pulados"] == "0"
    assert "attachment" in r.headers["content-disposition"]

    texto = r.content.decode("utf-8-sig")
    reader = list(csv.reader(io.StringIO(texto), delimiter=";"))
    assert reader[0] == COLUNAS
    linhas = {row[COLUNAS.index("Número pedido")]: row for row in reader[1:]}

    # avulso: SKU/nome do item, valor cheio 500×2 = 1000.
    a = linhas["800001"]
    assert _col(a, "SKU") == "dg053.ci"
    assert _col(a, "Produto") == "Capa Celular"
    assert _col(a, "Valor Total") == "1.000,00"
    assert _col(a, "Cidade Entrega") == "Belo Horizonte"

    # exclusivo: a001/embalagem, 0,1% de 1000 = 1,00.
    e = linhas["800002"]
    assert _col(e, "SKU") == "a001"
    assert _col(e, "Produto") == "embalagem"
    assert _col(e, "Valor Total") == "1,00"


@pytest.mark.asyncio
async def test_pedido_sem_faturador_vai_pra_pulados(
    db: AsyncSession, client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    avulso = NfFaturador(nome="bling avulso", modo="bling", nf_cheia=True,
                         sku_fonte="principal", nome_fonte="produto")
    db.add(avulso)
    await db.flush()
    # loja COM faturador
    db.add(StoreInfo(user_id=admin.id, platform="amazon", account_name="l1",
                     bling_store_id="910001", nf_faturador_id=avulso.id))
    # loja SEM faturador
    db.add(StoreInfo(user_id=admin.id, platform="shopee", account_name="l2",
                     bling_store_id="910002"))
    await db.flush()
    await _seed_pedido(db, admin, avulso, numero="810001", loja="910001",
                       itens=[{"sku": "a", "nome": "A", "qtd": 1, "unit": 100}])
    await _seed_pedido(db, admin, avulso, numero="810002", loja="910002",
                       itens=[{"sku": "b", "nome": "B", "qtd": 1, "unit": 100}])
    await db.commit()

    r = await client.post(
        "/api/nf-cadastro/faturamento/gerar-planilha",
        json={"numeros": ["810001", "810002", "899999"]},
    )
    assert r.status_code == 200, r.text
    assert r.headers["X-Pedidos-Ok"] == "1"
    assert r.headers["X-Pedidos-Pulados"] == "2"
    detalhe = json.loads(base64.b64decode(r.headers["X-Pedidos-Pulados-Detalhe"]))
    motivos = {d["numero"]: d["motivo"] for d in detalhe}
    assert "sem faturador" in motivos["810002"]
    assert "não encontrado" in motivos["899999"]


@pytest.mark.asyncio
async def test_mala_cheia_usa_catalogo_pela_familia(
    db: AsyncSession, client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    """NF cheia de MALA usa o valor do catálogo pela família do produto (o nome
    'Mala Lisa M2' → abs), NÃO o valor de venda (itemvalor)."""
    auth_as(admin)
    avulso = NfFaturador(
        nome="bling avulso", modo="bling", nf_cheia=True,
        sku_fonte="principal", nome_fonte="produto", ncm="4202.12.10",
    )
    db.add(avulso)
    await db.flush()
    db.add(StoreInfo(user_id=admin.id, platform="amazon", account_name="l1",
                     bling_store_id="920001", nf_faturador_id=avulso.id))
    # Produto com a família no nome (M2 → abs) + catálogo abs/20 = 161.
    db.add(Product(user_id=admin.id, sku="b001.20", name="Mala Lisa M2 tamanho 20 - Roxa"))
    db.add(NfCatalogoMala(modelo="abs", tamanho="20", valor=161))
    await db.flush()
    # Venda a 50 (promocional); a NF deve sair com o cheio 161.
    await _seed_pedido(db, admin, avulso, numero="820001", loja="920001",
                       itens=[{"sku": "b001.20", "nome": "Mala Lisa M2", "qtd": 1, "unit": 50}])
    await db.commit()

    r = await client.post(
        "/api/nf-cadastro/faturamento/gerar-planilha",
        json={"numeros": ["820001"]},
    )
    assert r.status_code == 200, r.text
    texto = r.content.decode("utf-8-sig")
    reader = list(csv.reader(io.StringIO(texto), delimiter=";"))
    a = {row[COLUNAS.index("Número pedido")]: row for row in reader[1:]}["820001"]
    assert _col(a, "Valor Total") == "161,00"


@pytest.mark.asyncio
async def test_nenhum_pedido_gerado_422(
    db: AsyncSession, client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(
        "/api/nf-cadastro/faturamento/gerar-planilha",
        json={"numeros": ["000001"]},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "nf_nenhum_pedido_gerado"


def test_chunks_por_limite_bling_e_upseller():
    """Bling limita por PEDIDOS; Upseller pelos DOIS tetos (pedidos E linhas).
    Um pedido nunca é quebrado no meio."""
    from app.services import nf_emissao_gerar as g

    def ped(numero: str, n_linhas: int) -> g._PedidoMontado:
        return g._PedidoMontado(
            numero=numero, faturador_id=uuid.uuid4(), info=None, linhas=[0] * n_linhas
        )

    # Bling: teto 500 pedidos → 501 pedidos de 1 linha viram 2 arquivos.
    grupo = [ped(str(i), 1) for i in range(501)]
    chunks = g._chunks_por_limite(grupo, "bling")
    assert [len(c) for c in chunks] == [500, 1]

    # Upseller: teto 300 pedidos E 1500 linhas. Aqui as LINHAS estouram antes:
    # 300 pedidos de 6 linhas = 1800 > 1500 → corta em 250 (250×6=1500).
    grupo = [ped(str(i), 6) for i in range(300)]
    chunks = g._chunks_por_limite(grupo, "upseller")
    assert [len(c) for c in chunks] == [250, 50]

    # Nunca quebra um pedido: um único pedido com mais linhas que o teto
    # ainda cabe sozinho num arquivo.
    chunks = g._chunks_por_limite([ped("x", 2000)], "upseller")
    assert [len(c) for c in chunks] == [1]

    assert g._chunks_por_limite([], "bling") == []


@pytest.mark.asyncio
async def test_gerar_por_faturador_quebra_em_varios_blocos(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Faturador com mais pedidos que o teto do arquivo gera VÁRIOS blocos
    (comandos), cada um com um subconjunto dos pedidos e nome de arquivo único."""
    from app.services import nf_emissao_gerar as g

    monkeypatch.setattr(g, "_LIMITE_BLING_VENDAS", 2)

    avulso = NfFaturador(
        nome="bling avulso", modo="bling", nf_cheia=True,
        sku_fonte="principal", nome_fonte="produto",
    )
    db.add(avulso)
    await db.flush()
    db.add(StoreInfo(user_id=admin.id, platform="amazon", account_name="l1",
                     bling_store_id="930001", nf_faturador_id=avulso.id))
    await db.flush()
    numeros = ["83001", "83002", "83003"]
    for n in numeros:
        await _seed_pedido(db, admin, avulso, numero=n, loja="930001",
                           itens=[{"sku": "a", "nome": "A", "qtd": 1, "unit": 100}])
    await db.commit()

    res = await g.gerar_por_faturador(db, numeros)
    # 3 pedidos, teto 2 → 2 blocos (2 + 1).
    assert len(res.blocos) == 2
    assert [len(b.numeros) for b in res.blocos] == [2, 1]
    # Cobrem todos os pedidos, sem repetição.
    cobertos = [n for b in res.blocos for n in b.numeros]
    assert sorted(cobertos) == sorted(numeros)
    # Nomes de arquivo únicos.
    nomes = [b.nome_arquivo for b in res.blocos]
    assert len(set(nomes)) == 2
    assert all(nm.endswith(".csv") for nm in nomes)


def test_extrair_destinatario_nome_vem_do_contato_nao_da_etiqueta():
    """O nome do Comprador tem que casar com o CPF (ambos do `contato`). A
    etiqueta guarda quem RECEBE (Amazon pode mandar outra pessoa) — se virasse
    o nome, a NF sairia com nome que não bate com o CPF."""
    from app.services import nf_emissao_gerar as g
    order = {
        "contato": {"nome": "Leonardo Scorsatto", "numeroDocumento": "03244034101",
                    "tipoPessoa": "F"},
        "transporte": {"etiqueta": {"nome": "Sheila Alves", "endereco": "Quadra CNB 10",
                                    "numero": "301", "municipio": "Brasília",
                                    "uf": "DF", "cep": "72115105"}},
    }
    d = g._extrair_destinatario(order)
    assert d["nome_destinatario"] == "Leonardo Scorsatto"
    assert d["documento"] == "03244034101"
    # Endereço de entrega segue vindo da etiqueta.
    assert d["cidade_destino"] == "Brasília"
    assert d["cep_destino"] == "72115105"


def test_extrair_destinatario_cai_na_etiqueta_sem_contato_nome():
    """Sem nome no contato, usa o da etiqueta como fallback (melhor que vazio)."""
    from app.services import nf_emissao_gerar as g
    order = {
        "contato": {"numeroDocumento": "03244034101"},
        "transporte": {"etiqueta": {"nome": "Sheila Alves"}},
    }
    d = g._extrair_destinatario(order)
    assert d["nome_destinatario"] == "Sheila Alves"
