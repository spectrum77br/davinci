"""M-107 (27/11/2026): a Cor do produto, e a lista do Produtos 2 numa chamada só (M-114…M-116).

- Migration 0521 num schema de teste: o tipo "Cor" (chave 'cor') nasce SEM nenhuma cor; um tipo
  "Cor" criado pela tela é reaproveitado (ganha a chave, fica com os nomes); a coluna
  `produto_regras.cor_id` igual ao modelo (FK para produto_cadastros ON DELETE SET NULL e índice),
  vazia em todos; sobe, desce (o tipo fica, sem a chave) e sobe de novo sem duplicar o tipo.
- Cadastros › Produtos (tipo Cor, regras da Categoria): só administrador inclui; nome repetido
  sem olhar maiúsculas é recusado; cor em uso não sai nem muda de tipo (409 `cor_em_uso`, também
  com confirmar=true) e a lixeira avisa antes (GET /uso bloqueia); RENOMEAR a cor em uso pode, e
  o produto mostra o nome novo; GET /cores (o menu do cadastro) em ordem alfabética, vazio sem cor.
- GET/PUT /api/produto-regras/{sku}: `cor_id`/`cor` pelo código sem a tag (dg115.ci = dg115.sp;
  o kit dg115.ci+a001.ci tem a sua); recusa id que não é Cor; vazio tira a cor; quem só vê lê e
  não grava; o Histórico mostra "Cor" com o nome.
- POST /api/produto-regras/lista: os campos da lista iguais aos do GET de um produto (margem
  herdada, centro escolhido/herdado, categoria de venda, condição, cor, foto/embalagem), com um
  número FIXO de consultas (não cresce com a lista), o código inválido em `ignorados` e a
  mesma permissão do GET.
Só dados FALSOS; nenhuma rede.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.historico import leitura, nomes
from app.models import (
    Base,
    CentroCusto,
    HistoricoAlteracao,
    PricingProduct,
    ProdutoCadastro,
    ProdutoCadastroTipo,
    ProdutoCondicaoEspecial,
    ProdutoRegra,
    Segment,
    UserRole,
)
from app.models.produto_cadastro import CHAVE_CATEGORIA, CHAVE_COR, CHAVE_TAG_ENVIO
from app.routers import produto_regras as rotas
from app.security.jwt import issue_session_token
from tests.conftest import TEST_SCHEMA

pytestmark = pytest.mark.asyncio

REGRAS = "/api/produto-regras"
CADS = "/api/produto-cadastros"
TAGS = (".cd", ".ci", ".pi", ".ra", ".sa", ".sp")


# ================================================================ cenário


@pytest_asyncio.fixture
async def cena(db: AsyncSession, make_user):
    """Os tipos da 0508 (Categoria com Celular; Tag de envio) e o Cor da 0521 com Vermelho e
    Azul; Celular/Mala com categorias de venda e centros; uma linha da Tabela de Preços para o
    dg115 com fotos e sem embalagem; a variante dela para a margem herdada (18%)."""
    admin = await make_user(role=UserRole.ADMIN)
    leitor = await make_user(permissions={"tabela_precos_produtos": {"view": True}})
    cat = ProdutoCadastroTipo(nome="Categoria", chave=CHAVE_CATEGORIA)
    tag = ProdutoCadastroTipo(nome="Tag de envio", chave=CHAVE_TAG_ENVIO)
    cor = ProdutoCadastroTipo(nome="Cor", chave=CHAVE_COR)
    db.add_all([cat, tag, cor])
    await db.flush()
    db.add_all(ProdutoCadastro(tipo_id=tag.id, nome=t) for t in TAGS)
    celular_cat = ProdutoCadastro(tipo_id=cat.id, nome="Celular")
    vermelho = ProdutoCadastro(tipo_id=cor.id, nome="Vermelho")
    azul = ProdutoCadastro(tipo_id=cor.id, nome="Azul")
    db.add_all([celular_cat, vermelho, azul])
    raiz_cel = Segment(name="Celular", slug="celular", sort_order=0)
    raiz_mala = Segment(name="Mala", slug="mala", sort_order=1)
    db.add_all([raiz_cel, raiz_mala])
    await db.flush()
    regular = Segment(
        name="Regular",
        slug="regular",
        parent_id=raiz_cel.id,
        sort_order=2,
        min_margin=Decimal("0.18"),
    )
    queima = Segment(
        name="Queima de estoque",
        slug="queima-de-estoque",
        parent_id=raiz_mala.id,
        sort_order=4,
        active=False,
        min_margin=Decimal("-0.15"),
    )
    db.add_all([regular, queima])
    await db.flush()
    cc_cel = CentroCusto(nome="Celular", segment_id=raiz_cel.id, fixo=True)
    cc_mala = CentroCusto(nome="Mala", segment_id=raiz_mala.id, fixo=True)
    db.add_all([cc_cel, cc_mala])
    linha = PricingProduct(
        user_id=admin.id,
        sku="dg115, dg116",
        name="Uranyx A17 Pro Max",
        segment_id=regular.id,
        fotos_path="/Celular/A17",
        fotos_count=4,
        videos_count=1,
    )
    db.add(linha)
    await db.commit()
    await db.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{TEST_SCHEMA}".pricing_product_variant ('
            " pricing_product_id uuid NOT NULL, segment_id uuid, variant_norm text NOT NULL,"
            " vlen int NOT NULL)"
        )
    )
    await db.execute(text(f'DELETE FROM "{TEST_SCHEMA}".pricing_product_variant'))
    await db.execute(
        text(
            f'INSERT INTO "{TEST_SCHEMA}".pricing_product_variant VALUES'
            " (:pp, :seg, 'dg115', 5), (:pp, :seg, 'dg116', 5)"
        ),
        {"pp": str(linha.id), "seg": str(regular.id)},
    )
    await db.commit()
    yield SimpleNamespace(
        admin=admin,
        leitor=leitor,
        tipo_cat=cat,
        tipo_cor=cor,
        celular_cat=celular_cat,
        vermelho=vermelho,
        azul=azul,
        regular=regular,
        cc_cel=cc_cel,
        cc_mala=cc_mala,
        linha=linha,
    )
    await db.execute(text(f'DELETE FROM "{TEST_SCHEMA}".pricing_product_variant'))
    await db.commit()


async def _put(client: AsyncClient, sku: str, **corpo):
    return await client.put(f"{REGRAS}/{sku}", json=corpo)


# ================================================================ cadastro do produto (GET/PUT)


async def test_cor_por_produto_vale_em_todas_as_tags_e_o_kit_tem_a_sua(
    client, auth_as, cena, db: AsyncSession
):
    auth_as(cena.admin)
    r = await client.get(f"{REGRAS}/dg115.ci")
    assert r.status_code == 200, r.text
    assert (r.json()["cor_id"], r.json()["cor"]) == (None, None), "nenhum produto nasce com cor"
    # Só a cor: cria a linha do produto, sem mexer em margem/DUIMP/centro.
    r = await _put(client, "dg115.ci", cor_id=str(cena.vermelho.id))
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["sku_base"], b["cor_id"], b["cor"]) == ("dg115", str(cena.vermelho.id), "Vermelho")
    assert (b["margem_min"], b["duimp_padrao"], b["centro_custo_id"]) == (None, None, None)
    regra = await db.get(ProdutoRegra, "dg115", populate_existing=True)
    assert regra.cor_id == cena.vermelho.id and regra.atualizado_por == cena.admin.id
    # O dg115.sp mostra a mesma cor; o kit tem a sua (vazia).
    assert (await client.get(f"{REGRAS}/dg115.sp")).json()["cor"] == "Vermelho"
    kit = (await client.get(f"{REGRAS}/dg115.ci+a001.ci")).json()
    assert (kit["sku_base"], kit["cor"]) == ("dg115+a001", None)
    r = await _put(client, "dg115.ci+a001.ci", cor_id=str(cena.azul.id))
    assert (r.json()["sku_base"], r.json()["cor"]) == ("dg115+a001", "Azul")
    assert (await client.get(f"{REGRAS}/dg115.sp")).json()["cor"] == "Vermelho"
    # Uma por produto: marcar outra troca; vazio deixa sem cor (a linha fica).
    r = await _put(client, "dg115.sp", cor_id=str(cena.azul.id))
    assert r.json()["cor"] == "Azul"
    for vazio in (None, ""):
        r = await _put(client, "dg115.ci", cor_id=vazio)
        assert (r.json()["cor_id"], r.json()["cor"]) == (None, None)
    assert (await db.get(ProdutoRegra, "dg115", populate_existing=True)).cor_id is None
    # Apagar o que não existe não cria linha.
    await _put(client, "x999", cor_id=None)
    assert await db.get(ProdutoRegra, "x999") is None


async def test_put_recusa_o_que_nao_e_cor_e_quem_so_ve_nao_troca(
    client, auth_as, cena, db: AsyncSession
):
    auth_as(cena.admin)
    for ruim in (str(cena.celular_cat.id), str(uuid.uuid4()), "vermelho", 7):
        r = await _put(client, "dg115.ci", cor_id=ruim)
        assert r.status_code == 400, ruim
        assert r.json()["detail"]["code"] == "cor_invalida"
        assert "Cadastros › Produtos" in r.json()["detail"]["message"]
    assert await db.get(ProdutoRegra, "dg115") is None
    await _put(client, "dg115.ci", cor_id=str(cena.vermelho.id))
    # Quem vê a Tabela de Preços › Produtos lê a cor (travado) e não grava.
    auth_as(cena.leitor)
    r = await client.get(f"{REGRAS}/dg115.sp")
    assert r.status_code == 200 and r.json()["pode_editar"] is False
    assert r.json()["cor"] == "Vermelho"
    assert (await _put(client, "dg115.sp", cor_id=str(cena.azul.id))).status_code == 403
    assert (await db.get(ProdutoRegra, "dg115", populate_existing=True)).cor_id == cena.vermelho.id


async def test_historico_mostra_cor_com_o_nome(client, db: AsyncSession, cena):
    token, _, _ = issue_session_token(sub=cena.admin.open_id, role=cena.admin.role.value)
    client.cookies.set(get_settings().cookie_name, token)
    assert (await _put(client, "dg115.ci", cor_id=str(cena.vermelho.id))).status_code == 200
    assert (await _put(client, "dg115.sp", cor_id=str(cena.azul.id))).status_code == 200
    alteracoes = (
        (
            await db.execute(
                select(HistoricoAlteracao)
                .where(HistoricoAlteracao.tabela == "produto_regras")
                .order_by(HistoricoAlteracao.id)
            )
        )
        .scalars()
        .all()
    )
    assert [a.operacao for a in alteracoes] == ["I", "U"]
    fk = await leitura.nomes_de_fk(db, alteracoes)
    mudou = next(c for c in leitura.campos(alteracoes[1], fk) if c["campo"] == "cor_id")
    assert mudou == {"campo": "cor_id", "nome": "Cor", "antes": "Vermelho", "depois": "Azul"}
    assert nomes.nome_campo("cor_id") == "Cor"
    assert alteracoes[1].rotulo == "dg115"


# ================================================================ Cadastros › Produtos (tipo Cor)


async def test_cores_do_menu_em_ordem_e_tipo_cor_na_tabela_produtos2(client, auth_as, cena, db):
    auth_as(cena.leitor)  # o menu do cadastro: qualquer pessoa logada e ativa
    r = await client.get(f"{CADS}/cores")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "cores": [
            {"id": str(cena.azul.id), "nome": "Azul"},
            {"id": str(cena.vermelho.id), "nome": "Vermelho"},
        ]
    }
    auth_as(cena.admin)
    corpo = (await client.get(CADS)).json()
    tipo = next(t for t in corpo["tipos"] if t["chave"] == "cor")
    assert (tipo["nome"], tipo["tabela"]) == ("Cor", "Produtos 2")
    linhas = [(c["tipo_nome"], c["nome"], c["tabela"]) for c in corpo["cadastros"]]
    assert ("Cor", "Azul", "Produtos 2") in linhas and ("Cor", "Vermelho", "Produtos 2") in linhas
    # Sem nenhuma cor cadastrada: lista vazia (a tela mostra "Nenhuma cor cadastrada.").
    await db.execute(
        ProdutoCadastro.__table__.delete().where(ProdutoCadastro.tipo_id == cena.tipo_cor.id)
    )
    await db.commit()
    assert (await client.get(f"{CADS}/cores")).json() == {"cores": []}


async def test_nova_cor_sem_repetir_e_so_administrador(client, auth_as, cena, make_user):
    auth_as(cena.admin)
    r = await client.post(CADS, json={"tipo_id": str(cena.tipo_cor.id), "nome": "  Verde  "})
    assert r.status_code == 201, r.text
    assert (r.json()["nome"], r.json()["tipo_chave"]) == ("Verde", "cor")
    r = await client.post(CADS, json={"tipo_id": str(cena.tipo_cor.id), "nome": "VERMELHO"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "nome_repetido"
    quase = await make_user(
        permissions={"cadastro_produtos": {"view": True, "edit": True, "delete": True}}
    )
    auth_as(quase)
    r = await client.post(CADS, json={"tipo_id": str(cena.tipo_cor.id), "nome": "Roxo"})
    assert r.status_code == 403
    nomes_cor = [c["nome"] for c in (await client.get(f"{CADS}/cores")).json()["cores"]]
    assert nomes_cor == ["Azul", "Verde", "Vermelho"]


async def test_cor_em_uso_nao_sai_mas_renomeia_e_o_produto_mostra_o_nome_novo(
    client, auth_as, cena, db: AsyncSession
):
    auth_as(cena.admin)
    await _put(client, "dg115.ci", cor_id=str(cena.vermelho.id))
    await _put(client, "a001.sp", cor_id=str(cena.vermelho.id))
    uso = (await client.get(f"{CADS}/{cena.vermelho.id}/uso")).json()
    assert uso == {
        "regra": "cor",
        "produtos": 2,
        "bloqueia": True,
        "mensagem": "2 produtos usam a cor “Vermelho”. Troque a cor desses produtos antes: "
        "nenhum produto pode perder a cor.",
    }
    for url in (f"{CADS}/{cena.vermelho.id}", f"{CADS}/{cena.vermelho.id}?confirmar=true"):
        r = await client.delete(url)
        assert r.status_code == 409, url
        assert r.json()["detail"]["code"] == "cor_em_uso"
        assert r.json()["detail"]["produtos"] == 2
    # Mudar de tipo (virar Categoria) tiraria a cor dos produtos: recusado.
    r = await client.patch(
        f"{CADS}/{cena.vermelho.id}", json={"tipo_id": str(cena.tipo_cat.id), "confirmar": True}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "cor_em_uso"
    # Renomear pode (o produto guarda o id): o cadastro do produto mostra o nome novo.
    r = await client.patch(f"{CADS}/{cena.vermelho.id}", json={"nome": "Vermelho Fosco"})
    assert r.status_code == 200, r.text
    assert (await client.get(f"{REGRAS}/dg115.sp")).json()["cor"] == "Vermelho Fosco"
    # Cor sem uso sai (e a que não tem produto nem pergunta).
    assert (await client.get(f"{CADS}/{cena.azul.id}/uso")).json()["bloqueia"] is False
    assert (await client.delete(f"{CADS}/{cena.azul.id}")).status_code == 204
    # Tirando a cor dos dois, ela também sai.
    for sku in ("dg115.ci", "a001.sp"):
        await _put(client, sku, cor_id=None)
    assert (await client.delete(f"{CADS}/{cena.vermelho.id}")).status_code == 204


async def test_cor_excluida_na_corrida_deixa_o_produto_sem_cor(client, auth_as, cena, db):
    """O SET NULL da 0521: se a cor sair por fora da regra (corrida entre a lixeira e o
    cadastro), o produto fica sem cor — nada quebra."""
    auth_as(cena.admin)
    await _put(client, "dg115.ci", cor_id=str(cena.azul.id))
    await db.execute(ProdutoCadastro.__table__.delete().where(ProdutoCadastro.id == cena.azul.id))
    await db.commit()
    assert (await db.get(ProdutoRegra, "dg115", populate_existing=True)).cor_id is None
    assert (await client.get(f"{REGRAS}/dg115.sp")).json()["cor"] is None


# ================================================================ a lista (M-114…M-116)


class _Contador:
    """Conta os comandos SQL mandados ao banco durante o bloco."""

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self):
        event.listen(_db.engine.sync_engine, "before_cursor_execute", self._conta)
        return self

    def __exit__(self, *exc):
        event.remove(_db.engine.sync_engine, "before_cursor_execute", self._conta)

    def _conta(self, *args, **kwargs) -> None:
        self.n += 1


async def test_lista_traz_os_campos_do_cadastro_de_todos_de_uma_vez(
    client, auth_as, cena, db: AsyncSession
):
    auth_as(cena.admin)
    linha = str(cena.linha.id)
    await _put(
        client,
        "dg115.ci",
        cor_id=str(cena.vermelho.id),
        margem_min="0.2",
        centro_custo_id=str(cena.cc_mala.id),
    )
    hoje = rotas._hoje()
    db.add(
        ProdutoCondicaoEspecial(
            sku_base="dg116",
            pedido_de=hoje - timedelta(days=1),
            pedido_ate=hoje + timedelta(days=3),
            piso=Decimal("0.05"),
        )
    )
    await db.commit()
    itens = [
        {"sku": "dg115.ci", "linha": linha, "categoria": "Celular"},
        {"sku": "dg115.sp", "linha": linha, "categoria": "Celular"},
        {"sku": "dg116.ra", "linha": linha, "categoria": "Celular"},
        {"sku": "z2821", "linha": None, "categoria": "Mala Kit"},
        {"sku": "a001.ci", "linha": "l1", "categoria": "Acessórios"},  # id que não é uuid
        {"sku": ".ra"},
    ]
    r = await client.post(f"{REGRAS}/lista", json={"produtos": itens})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["ignorados"] == [".ra"] and corpo["pode_editar"] is True
    p = corpo["produtos"]
    assert set(p) == {"dg115.ci", "dg115.sp", "dg116.ra", "z2821", "a001.ci"}
    assert p["dg115.ci"] == {
        "sku_base": "dg115",
        "cor_id": str(cena.vermelho.id),
        "cor": "Vermelho",
        "margem_min": 0.2,
        "margem_min_herdada": 0.18,
        "centro_custo": "Mala",
        "centro_custo_herdado": "Celular",
        "tem_linha": True,
        "categoria_venda": "Regular",
        "categoria_venda_ativa": True,
        "condicao_resumo": "—",
        "condicao_vigente": False,
        "condicao_piso": None,
        "fotos_count": 4,
        "foto": True,
        "embalagem": False,
        "embalagens_count": None,
    }
    # A mesma chave: o dg115.sp mostra o mesmo que o dg115.ci.
    assert p["dg115.sp"] == p["dg115.ci"]
    assert p["dg116.ra"]["cor"] is None and p["dg116.ra"]["margem_min"] is None
    assert (p["dg116.ra"]["condicao_vigente"], p["dg116.ra"]["condicao_piso"]) == (True, 0.05)
    assert p["dg116.ra"]["condicao_resumo"].endswith("· piso 5%")
    # Sem linha: a margem da Queima (z) e o centro pela categoria; nada de foto/embalagem.
    z = p["z2821"]
    assert (z["margem_min_herdada"], z["centro_custo_herdado"], z["tem_linha"]) == (
        -0.15,
        "Mala",
        False,
    )
    assert (z["categoria_venda"], z["foto"], z["embalagem"]) == (None, False, False)
    a = p["a001.ci"]
    assert (a["margem_min_herdada"], a["centro_custo_herdado"], a["tem_linha"]) == (
        0.09,
        None,
        False,
    )
    # Igual ao GET de um produto (o cadastro), campo a campo.
    for item in itens[:5]:
        um = (
            await client.get(
                f"{REGRAS}/{item['sku']}",
                params={k: v for k, v in item.items() if k != "sku" and v},
            )
        ).json()
        da_lista = p[item["sku"]]
        herdado = um["centro_custo_herdado"]
        linha_um = um["linha"]
        assert da_lista["margem_min"] == um["margem_min"], item
        assert da_lista["margem_min_herdada"] == um["margem_min_herdada"], item
        assert da_lista["centro_custo"] == um["centro_custo"], item
        assert da_lista["centro_custo_herdado"] == (herdado["nome"] if herdado else None), item
        assert da_lista["categoria_venda"] == (linha_um or {}).get("categoria_venda"), item
        assert da_lista["condicao_resumo"] == um["condicao_resumo"], item
        assert da_lista["cor"] == um["cor"], item


async def test_lista_nao_faz_uma_consulta_por_produto(client, auth_as, cena):
    auth_as(cena.admin)
    linha = str(cena.linha.id)

    def produtos(n: int) -> list[dict]:
        return [
            {"sku": f"dg{115 + i % 2}.{TAGS[i % len(TAGS)][1:]}x{i}", "linha": linha}
            for i in range(n)
        ]

    contagens = []
    for n in (3, 60):
        with _Contador() as c:
            r = await client.post(f"{REGRAS}/lista", json={"produtos": produtos(n)})
        assert r.status_code == 200 and len(r.json()["produtos"]) == n
        contagens.append(c.n)
    assert 0 < contagens[0] == contagens[1], contagens


async def test_lista_pede_a_permissao_do_cadastro_e_tem_limite(client, auth_as, cena, make_user):
    sem = await make_user(permissions={"cadastro_produtos": {"view": True}})
    auth_as(sem)
    r = await client.post(f"{REGRAS}/lista", json={"produtos": [{"sku": "dg115.ci"}]})
    assert r.status_code == 403
    auth_as(cena.leitor)
    r = await client.post(f"{REGRAS}/lista", json={"produtos": [{"sku": "dg115.ci"}]})
    assert r.status_code == 200 and r.json()["pode_editar"] is False
    assert (await client.post(f"{REGRAS}/lista", json={"produtos": []})).json() == {
        "produtos": {},
        "ignorados": [],
        "pode_editar": False,
    }
    grande = [{"sku": "x"}] * (rotas.LISTA_MAX + 1)
    assert (await client.post(f"{REGRAS}/lista", json={"produtos": grande})).status_code == 422


# ================================================================ migration 0521

_MIGRATION = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0521_produto_cor.py"


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0521", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db: AsyncSession, schema: str) -> dict:
    """A coluna, a FK e o índice da 0521 em `produto_regras`."""

    def limpo(s: str | None) -> str | None:
        return s.replace(f'"{schema}".', "").replace(f"{schema}.", "") if s else s

    p = {"s": schema, "t": "produto_regras"}
    coluna = (
        await db.execute(
            text(
                "SELECT column_name, udt_name, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_schema = :s AND table_name = :t "
                "AND column_name = 'cor_id'"
            ),
            p,
        )
    ).all()
    fks = (
        await db.execute(
            text(
                "SELECT co.conname, pg_get_constraintdef(co.oid) FROM pg_constraint co "
                "JOIN pg_class cl ON cl.oid = co.conrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :s AND cl.relname = :t AND co.contype = 'f' "
                "AND co.conname LIKE '%cor_id%'"
            ),
            p,
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :s "
                "AND tablename = :t AND indexname LIKE '%cor_id%'"
            ),
            p,
        )
    ).all()
    return {
        "coluna": [tuple(r) for r in coluna],
        "fks": [(r[0], limpo(r[1])) for r in fks],
        "indices": [(r[0], limpo(r[1])) for r in indices],
    }


async def test_migration_0521_cria_o_tipo_vazio_igual_ao_modelo_e_desce(db: AsyncSession):
    mod = _carregar()
    assert (mod.revision, mod.down_revision) == ("0521_produto_cor", "0520_contas_dez_categorias")
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    raiz = Path(__file__).resolve().parent.parent
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "alembic"))
    # Ponta única: a 0521 vem logo depois da 0520 (M-93).
    roteiro = ScriptDirectory.from_config(cfg)
    assert len(roteiro.get_heads()) == 1, roteiro.get_heads()
    assert roteiro.get_revision("0521_produto_cor").down_revision == "0520_contas_dez_categorias"

    modelo = Base.metadata.schema
    rascunho = f"{modelo}_m0521"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # O que a 0521 encontra na produção: as tabelas da 0508 e produto_regras sem a coluna nova.
    for t in ("produto_cadastro_tipos", "produto_cadastros", "produto_regras"):
        await db.execute(text(f'CREATE TABLE "{rascunho}".{t} (LIKE "{modelo}".{t} INCLUDING ALL)'))
    await db.execute(text(f'ALTER TABLE "{rascunho}".produto_regras DROP COLUMN cor_id'))
    for sql in (
        f'INSERT INTO "{rascunho}".produto_cadastro_tipos (id, nome, chave) VALUES '
        "('00000000-0000-0000-0000-0000000000a1', 'Categoria', 'categoria'),"
        "('00000000-0000-0000-0000-0000000000a2', 'Tag de envio', 'tag_envio')",
        f'INSERT INTO "{rascunho}".produto_cadastros (tipo_id, nome) VALUES '
        "('00000000-0000-0000-0000-0000000000a1', 'Celular'),"
        "('00000000-0000-0000-0000-0000000000a2', '.sp')",
        f'INSERT INTO "{rascunho}".produto_regras (sku_base, margem_min, duimp_padrao) '
        "VALUES ('dg115', 0.12, 'texto'), ('b001.12', NULL, NULL)",
    ):
        await db.execute(text(sql))
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    async def _passo(nome: str) -> None:
        conn = await db.connection()
        await conn.run_sync(_rodar, nome)
        await db.commit()

    async def _linhas():
        return (
            await db.execute(
                text(
                    "SELECT sku_base, margem_min::text, duimp_padrao FROM "
                    f'"{rascunho}".produto_regras ORDER BY sku_base'
                )
            )
        ).all()

    async def _tipos():
        return (
            await db.execute(
                text(
                    "SELECT t.nome, t.chave, count(c.id)"
                    f' FROM "{rascunho}".produto_cadastro_tipos t'
                    f' LEFT JOIN "{rascunho}".produto_cadastros c ON c.tipo_id = t.id'
                    " GROUP BY t.nome, t.chave ORDER BY t.nome"
                )
            )
        ).all()

    mod.SCHEMA = rascunho
    try:
        antes = await _linhas()
        await _passo("upgrade")
        feito = await _catalogo(db, rascunho)
        assert feito["coluna"] == [("cor_id", "uuid", "YES", None)]
        assert feito == await _catalogo(db, modelo)
        assert "ON DELETE SET NULL" in feito["fks"][0][1]
        # O tipo nasce com a chave e SEM nenhuma cor; nada mais muda.
        assert await _tipos() == [
            ("Categoria", "categoria", 1),
            ("Cor", "cor", 0),
            ("Tag de envio", "tag_envio", 1),
        ]
        assert await _linhas() == antes
        sem_cor = f'SELECT count(*) FROM "{rascunho}".produto_regras WHERE cor_id IS NOT NULL'
        assert (await db.execute(text(sem_cor))).scalar_one() == 0
        # A cor excluída deixa o produto sem cor (SET NULL).
        cor_id = (
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".produto_cadastros (tipo_id, nome) SELECT id, '
                    f"'Vermelho' FROM \"{rascunho}\".produto_cadastro_tipos WHERE chave = 'cor' "
                    "RETURNING id"
                )
            )
        ).scalar_one()
        await db.execute(
            text(
                f'UPDATE "{rascunho}".produto_regras SET cor_id = :c WHERE sku_base = :b'
            ).bindparams(c=cor_id, b="dg115")
        )
        await db.commit()
        await db.execute(
            text(f'DELETE FROM "{rascunho}".produto_cadastros WHERE id = :c'), {"c": cor_id}
        )
        await db.commit()
        assert (await db.execute(text(sem_cor))).scalar_one() == 0
        # Cor que não existe não entra.
        with pytest.raises(Exception, match="fk_produto_regras_cor_id_produto_cadastros"):
            await db.execute(
                text(
                    f'UPDATE "{rascunho}".produto_regras SET cor_id = :c WHERE sku_base = :b'
                ).bindparams(c=uuid.uuid4(), b="b001.12")
            )
        await db.rollback()

        # Desce: a coluna sai; o tipo fica, sem a regra (nada sai de Cadastros › Produtos).
        await _passo("downgrade")
        assert (await _catalogo(db, rascunho)) == {"coluna": [], "fks": [], "indices": []}
        assert await _linhas() == antes
        assert ("Cor", None, 0) in await _tipos()
        # Sobe de novo: reencontra o tipo pelo nome (não duplica) e devolve a chave.
        await _passo("upgrade")
        assert [t for t in await _tipos() if t[0] == "Cor"] == [("Cor", "cor", 0)]
        assert (await _catalogo(db, rascunho))["coluna"] == [("cor_id", "uuid", "YES", None)]
        assert await _linhas() == antes
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


async def test_migration_0521_usa_o_tipo_cor_criado_pela_tela(db: AsyncSession):
    """Item 1 do pedido: "se já existir, usar o existente" — o tipo "cor" criado pela tela (sem
    chave) ganha a chave e fica com as cores que já tinha."""
    mod = _carregar()
    modelo = Base.metadata.schema
    rascunho = f"{modelo}_m0521b"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    for t in ("produto_cadastro_tipos", "produto_cadastros", "produto_regras"):
        await db.execute(text(f'CREATE TABLE "{rascunho}".{t} (LIKE "{modelo}".{t} INCLUDING ALL)'))
    await db.execute(text(f'ALTER TABLE "{rascunho}".produto_regras DROP COLUMN cor_id'))
    await db.execute(
        text(
            f'INSERT INTO "{rascunho}".produto_cadastro_tipos (id, nome) VALUES '
            "('00000000-0000-0000-0000-0000000000b1', 'cor')"
        )
    )
    await db.execute(
        text(
            f'INSERT INTO "{rascunho}".produto_cadastros (tipo_id, nome) VALUES '
            "('00000000-0000-0000-0000-0000000000b1', 'Preto')"
        )
    )
    await db.commit()

    def _rodar(conn) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar)
        await db.commit()
        tipos = (
            await db.execute(
                text(f'SELECT id::text, nome, chave FROM "{rascunho}".produto_cadastro_tipos')
            )
        ).all()
        assert tipos == [("00000000-0000-0000-0000-0000000000b1", "cor", "cor")]
        nomes_cor = (
            await db.execute(text(f'SELECT nome FROM "{rascunho}".produto_cadastros'))
        ).scalars()
        assert list(nomes_cor) == ["Preto"]
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
