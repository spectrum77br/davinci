"""M-73 (26/11/2026): Centro de custo POR PRODUTO no cadastro do Produtos 2, e o que o DaVinci
entrega para os menus da linha 2 (Centro de custo e Categoria de venda).

- `produto_regras.centro_custo_id` (migration 0518): vazio = o departamento do produto, CALCULADO
  e não copiado — o da linha da Tabela de Preços; sem linha, o da categoria do produto.
- GET /api/produto-regras/{sku}?linha=&categoria=: o centro escolhido, o herdado (com a origem) e
  a linha (categoria de venda atual, posição e as opções ATIVAS do departamento da linha).
- PUT grava o centro (vazio volta ao departamento); recusa centro que não existe; só administrador.
- A Categoria de venda troca pela rota que a coluna "Tabela" usa (PATCH /api/pricing/products,
  product_type): a posição que o GET entrega leva à categoria escolhida, no mesmo departamento.
- Cadastros › Centro de custo: `uso_do_centro` conta os produtos que escolheram o centro (também
  num centro sem raiz, como "Brinquedos"); com uso, não sai.
- Histórico: "Centro de custo", com o nome do centro (não o id).
- A migration 0518 num schema de teste: igual ao modelo, RESTRICT, sobe/desce/sobe sem perder
  as outras colunas.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.historico import leitura, nomes
from app.models import (
    Base,
    CentroCusto,
    HistoricoAlteracao,
    PricingProduct,
    ProdutoCadastro,
    ProdutoCadastroTipo,
    ProdutoRegra,
    Segment,
    UserRole,
)
from app.models.produto_cadastro import CHAVE_TAG_ENVIO
from app.security.jwt import issue_session_token
from tests.conftest import TEST_SCHEMA

pytestmark = pytest.mark.asyncio

ROTA = "/api/produto-regras"


@pytest_asyncio.fixture
async def cena(db: AsyncSession, make_user):
    """As raízes de hoje com as categorias de venda do Celular (Apple inativa no fim só aqui, para
    provar que inativa não é opção), Eletro e o Catálogo antigo; os três centros fixos e um novo
    sem raiz ("Brinquedos"); a lista de tags da 0508; duas linhas da Tabela de Preços."""
    admin = await make_user(role=UserRole.ADMIN)
    vendedor = await make_user()
    tipo = ProdutoCadastroTipo(nome="Tag de envio", chave=CHAVE_TAG_ENVIO)
    db.add(tipo)
    await db.flush()
    db.add_all(
        ProdutoCadastro(tipo_id=tipo.id, nome=t) for t in (".cd", ".ci", ".pi", ".ra", ".sa", ".sp")
    )
    raiz = {
        n: Segment(name=n, slug=s, sort_order=i)
        for i, (n, s) in enumerate(
            (
                ("Celular", "celular"),
                ("Mala", "mala"),
                ("Eletro", "eletro"),
                ("Catálogo", "catalogo"),
            )
        )
    }
    db.add_all(raiz.values())
    await db.flush()
    cel = {}
    for i, (nome, ativo) in enumerate(
        (
            ("Acessórios", True),
            ("Diversos", True),
            ("Regular", True),
            ("Robusto", True),
            ("Apple", False),
        )
    ):
        cel[nome] = Segment(
            parent_id=raiz["Celular"].id, name=nome, slug=f"c{i}", sort_order=i, active=ativo
        )
    ele1 = Segment(parent_id=raiz["Eletro"].id, name="1", slug="e1", sort_order=0)
    cat1 = Segment(parent_id=raiz["Catálogo"].id, name="Celular", slug="k1", sort_order=0)
    db.add_all([*cel.values(), ele1, cat1])
    await db.flush()
    cc = {
        n: CentroCusto(nome=n, segment_id=raiz[n].id, fixo=True)
        for n in ("Celular", "Eletro", "Mala")
    }
    cc["Brinquedos"] = CentroCusto(nome="Brinquedos", fixo=False)
    db.add_all(cc.values())
    linha = PricingProduct(
        user_id=admin.id, sku="dg019, dg020", name="Uranyx F105", segment_id=cel["Regular"].id
    )
    linha_catalogo = PricingProduct(
        user_id=admin.id, sku="dg900", name="Catálogo antigo", segment_id=cat1.id
    )
    db.add_all([linha, linha_catalogo])
    await db.commit()
    # A margem herdada da M-74 (que o GET também devolve) lê esta tabela; a do teste nasce vazia.
    await db.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{TEST_SCHEMA}".pricing_product_variant ('
            " pricing_product_id uuid NOT NULL, segment_id uuid, variant_norm text NOT NULL,"
            " vlen int NOT NULL)"
        )
    )
    await db.execute(text(f'DELETE FROM "{TEST_SCHEMA}".pricing_product_variant'))
    await db.commit()
    return SimpleNamespace(
        admin=admin,
        vendedor=vendedor,
        raiz=raiz,
        cel=cel,
        cc=cc,
        linha=linha,
        linha_catalogo=linha_catalogo,
    )


async def _get(client: AsyncClient, sku: str, **q):
    return await client.get(f"{ROTA}/{sku}", params=q)


async def test_get_traz_o_departamento_da_linha_e_as_opcoes_ativas(client, auth_as, cena):
    auth_as(cena.admin)
    r = await _get(client, "dg019.ra+a020.ra", linha=str(cena.linha.id), categoria="Celular Kit")
    assert r.status_code == 200, r.text
    b = r.json()
    # Em branco: o departamento da linha (não a categoria), calculado.
    assert (b["centro_custo_id"], b["centro_custo"]) == (None, None)
    assert b["centro_custo_herdado"] == {"id": str(cena.cc["Celular"].id), "nome": "Celular"}
    assert b["centro_custo_origem"] == "do departamento da linha da Tabela de Preços"
    linha = b["linha"]
    assert linha == {
        "id": str(cena.linha.id),
        "nome": "Uranyx F105",
        "codigos": ["dg019", "dg020"],
        "departamento": "Celular",
        "centro_custo_id": str(cena.cc["Celular"].id),
        "centro_custo": "Celular",
        "segment_id": str(cena.cel["Regular"].id),
        "categoria_venda": "Regular",
        "categoria_venda_ativa": True,
        "product_type": 3,
        # Só as ATIVAS do departamento da linha, na ordem das colunas (Apple inativa fica fora).
        "opcoes": [
            {"segment_id": str(cena.cel[n].id), "nome": n, "product_type": i + 1}
            for i, n in enumerate(("Acessórios", "Diversos", "Regular", "Robusto"))
        ],
    }
    assert b["pode_editar"] is True
    # Nada foi gravado só por ler (calculado, não copiado).
    assert (await client.get(f"{ROTA}/dg019.ra")).json()["centro_custo_id"] is None


async def test_sem_linha_usa_a_categoria_e_o_catalogo_nao_e_centro(client, auth_as, cena):
    auth_as(cena.admin)
    casos = {
        "Celular Kit": ("Celular", "da categoria Celular Kit"),
        "mala kit": ("Mala", "da categoria mala kit"),
        "Eletro": ("Eletro", "da categoria Eletro"),
        # Centro novo, sem raiz: vale na hora pela categoria com o mesmo nome.
        "Brinquedos": ("Brinquedos", "da categoria Brinquedos"),
    }
    for categoria, (nome, origem) in casos.items():
        b = (await _get(client, "x100.sp", categoria=categoria)).json()
        assert b["linha"] is None
        assert (b["centro_custo_herdado"]["nome"], b["centro_custo_origem"]) == (nome, origem)
    for sem in ("Acessórios", "Insumos", None):
        b = (await _get(client, "x100.sp", **({"categoria": sem} if sem else {}))).json()
        assert (b["centro_custo_herdado"], b["centro_custo_origem"]) == (None, None), sem
    # Linha que não existe mais, ou id que não é de linha (cópia antiga do ERP): como sem linha,
    # e o resto da resposta vem igual.
    for linha in (str(uuid.uuid4()), "L19"):
        r = await _get(client, "x100.sp", linha=linha, categoria="Celular")
        assert r.status_code == 200, linha
        b = r.json()
        assert b["linha"] is None and b["centro_custo_herdado"]["nome"] == "Celular"
        assert b["margem_min_herdada"] == 0.09
    # Linha do Catálogo (raiz que não é centro de custo): sem centro herdado, e a categoria não
    # entra no lugar (o departamento é o da linha).
    b = (await _get(client, "dg900", linha=str(cena.linha_catalogo.id), categoria="Celular")).json()
    assert b["centro_custo_herdado"] is None
    assert b["centro_custo_origem"] == (
        "a linha da Tabela de Preços é do Catálogo, que não é centro de custo"
    )
    assert b["linha"]["departamento"] == "Catálogo" and b["linha"]["centro_custo_id"] is None


async def test_put_grava_o_centro_por_produto_e_vazio_volta_ao_departamento(
    client, auth_as, cena, db: AsyncSession
):
    auth_as(cena.admin)
    q = {"linha": str(cena.linha.id)}
    brinq = str(cena.cc["Brinquedos"].id)
    r = await client.put(f"{ROTA}/dg019.ra", params=q, json={"centro_custo_id": brinq})
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["centro_custo_id"], b["centro_custo"]) == (brinq, "Brinquedos")
    # O herdado continua junto (para a tela dizer para onde volta) e a linha também.
    assert b["centro_custo_herdado"]["nome"] == "Celular" and b["linha"]["id"] == str(cena.linha.id)
    regra = await db.get(ProdutoRegra, "dg019", populate_existing=True)
    assert regra.centro_custo_id == cena.cc["Brinquedos"].id
    assert regra.margem_min is None and regra.duimp_padrao is None
    # Vale para o código em todas as tags; o kit tem o seu.
    assert (await _get(client, "dg019.sp")).json()["centro_custo"] == "Brinquedos"
    assert (await _get(client, "dg019.ra+a020.ra")).json()["centro_custo_id"] is None
    # Margem mín junto não apaga o centro.
    r = await client.put(f"{ROTA}/dg019.ra", json={"margem_min": 0.1})
    assert r.json()["centro_custo"] == "Brinquedos"
    # Vazio volta ao departamento ("" ou null).
    for vazio in ("", None):
        await client.put(f"{ROTA}/dg019.ra", json={"centro_custo_id": brinq})
        r = await client.put(f"{ROTA}/dg019.ra", params=q, json={"centro_custo_id": vazio})
        assert r.status_code == 200
        assert r.json()["centro_custo_id"] is None
        assert r.json()["centro_custo_herdado"]["nome"] == "Celular"
    assert (await db.get(ProdutoRegra, "dg019", populate_existing=True)).centro_custo_id is None
    # Apagar o que não existe não cria linha.
    await client.put(f"{ROTA}/x999", json={"centro_custo_id": None})
    assert await db.get(ProdutoRegra, "x999") is None


async def test_put_recusa_centro_que_nao_existe_e_quem_nao_e_admin(
    client, auth_as, cena, db: AsyncSession
):
    auth_as(cena.admin)
    for ruim in (str(uuid.uuid4()), "Celular", 7, True):
        r = await client.put(f"{ROTA}/dg019.ra", json={"centro_custo_id": ruim})
        assert r.status_code == 400, ruim
        assert r.json()["detail"] == {
            "code": "centro_custo_invalido",
            "message": "Centro de custo não encontrado: ele pode ter sido excluído em Cadastros › "
            "Centro de custo. Feche e abra o produto de novo.",
        }
    assert await db.get(ProdutoRegra, "dg019") is None
    auth_as(cena.vendedor)
    b = (await _get(client, "dg019.ra", linha=str(cena.linha.id))).json()
    assert b["pode_editar"] is False and b["linha"]["categoria_venda"] == "Regular"
    r = await client.put(f"{ROTA}/dg019.ra", json={"centro_custo_id": str(cena.cc["Mala"].id)})
    assert r.status_code == 403
    assert await db.get(ProdutoRegra, "dg019") is None


async def test_categoria_de_venda_pela_rota_da_coluna_tabela(client, auth_as, cena, db):
    """O menu manda o `product_type` da opção (o mesmo que a coluna "Tabela" manda): a linha fica
    com a categoria escolhida, no MESMO departamento, e o GET seguinte já mostra."""
    auth_as(cena.admin)
    q = {"linha": str(cena.linha.id)}
    robusto = next(
        o
        for o in (await _get(client, "dg019.ra", **q)).json()["linha"]["opcoes"]
        if o["nome"] == "Robusto"
    )
    r = await client.patch(
        f"/api/pricing/products/{cena.linha.id}", json={"product_type": robusto["product_type"]}
    )
    assert r.status_code == 200, r.text
    linha = (await _get(client, "dg019.ra", **q)).json()["linha"]
    assert (linha["segment_id"], linha["categoria_venda"]) == (robusto["segment_id"], "Robusto")
    assert linha["departamento"] == "Celular"
    pp = await db.get(PricingProduct, cena.linha.id, populate_existing=True)
    assert pp.segment_id == cena.cel["Robusto"].id


async def test_uso_do_centro_conta_os_produtos_e_barra_a_exclusao(
    client, auth_as, cena, db: AsyncSession
):
    auth_as(cena.admin)
    brinq = cena.cc["Brinquedos"]
    for sku in ("dg019.ra", "b001.12.sp"):
        r = await client.put(f"{ROTA}/{sku}", json={"centro_custo_id": str(brinq.id)})
        assert r.status_code == 200
    uso = (await client.get(f"/api/centros-custo/{brinq.id}/uso")).json()
    assert uso["produtos_centro"] == 2 and uso["produtos"] == 2 and uso["bloqueia"] is True
    esperado = (
        "“Brinquedos” está em uso: 2 produtos com este centro no Produtos 2. Só dá para excluir "
        "um centro de custo que ninguém usa."
    )
    assert uso["mensagem"] == esperado
    r = await client.delete(f"/api/centros-custo/{brinq.id}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "centro_em_uso"
    assert r.json()["detail"]["mensagem"] == esperado and r.json()["detail"]["produtos"] == 2
    # O fixo conta também (entra no texto do "não pode ser excluído").
    await client.put(f"{ROTA}/z2821", json={"centro_custo_id": str(cena.cc["Mala"].id)})
    msg = (await client.get(f"/api/centros-custo/{cena.cc['Mala'].id}/uso")).json()["mensagem"]
    assert msg.endswith("Hoje usam: 1 produto com este centro no Produtos 2.")
    # Voltando os dois ao departamento, o centro novo sai.
    for sku in ("dg019.sp", "b001.12"):
        await client.put(f"{ROTA}/{sku}", json={"centro_custo_id": None})
    assert (await client.delete(f"/api/centros-custo/{brinq.id}")).status_code == 204
    assert await db.get(CentroCusto, brinq.id, populate_existing=True) is None


async def test_historico_mostra_centro_de_custo_com_o_nome(client, db: AsyncSession, cena):
    token, _, _ = issue_session_token(sub=cena.admin.open_id, role=cena.admin.role.value)
    client.cookies.set(get_settings().cookie_name, token)
    eletro, mala = str(cena.cc["Eletro"].id), str(cena.cc["Mala"].id)
    assert (
        await client.put(f"{ROTA}/dg019.ra", json={"centro_custo_id": eletro})
    ).status_code == 200
    assert (await client.put(f"{ROTA}/dg019.sp", json={"centro_custo_id": mala})).status_code == 200
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
    assert {str(a.ator_id) for a in alteracoes} == {str(cena.admin.id)}
    fk = await leitura.nomes_de_fk(db, alteracoes)
    mudou = next(c for c in leitura.campos(alteracoes[1], fk) if c["campo"] == "centro_custo_id")
    assert mudou == {
        "campo": "centro_custo_id",
        "nome": "Centro de custo",
        "antes": "Eletro",
        "depois": "Mala",
    }
    assert nomes.nome_campo("centro_custo_id") == "Centro de custo"
    assert alteracoes[1].rotulo == "dg019"


# ================================================================ migration 0518

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0518_produto_regras_centro_custo.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0518", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db: AsyncSession, schema: str) -> dict:
    """A coluna, a FK e o índice da 0518 em `produto_regras` (o resto da tabela é da 0516)."""

    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "").replace(f'"{schema}".', "") if s else s

    p = {"s": schema, "t": "produto_regras"}
    coluna = (
        await db.execute(
            text(
                "SELECT column_name, udt_name, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_schema = :s AND table_name = :t "
                "AND column_name = 'centro_custo_id'"
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
                "AND co.conname LIKE '%centro_custo%'"
            ),
            p,
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :s "
                "AND tablename = :t AND indexname LIKE '%centro_custo%'"
            ),
            p,
        )
    ).all()
    return {
        "coluna": [tuple(r) for r in coluna],
        "fks": [(r[0], limpo(r[1])) for r in fks],
        "indices": [(r[0], limpo(r[1])) for r in indices],
    }


async def test_migration_0518_igual_ao_modelo_restrict_e_desce(db: AsyncSession):
    mod = _carregar()
    assert mod.revision == "0518_produto_regras_centro_custo"
    assert mod.down_revision == "0517_centros_custo"
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    raiz = Path(__file__).resolve().parent.parent
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0518_produto_regras_centro_custo"]

    modelo = Base.metadata.schema
    rascunho = f"{modelo}_m0518"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # O que a 0518 encontra na produção: produto_regras como a 0516 deixou (a do modelo, sem a
    # coluna nova) e centros_custo como a 0517 deixou.
    for t in ("produto_regras", "centros_custo"):
        await db.execute(text(f'CREATE TABLE "{rascunho}".{t} (LIKE "{modelo}".{t} INCLUDING ALL)'))
    await db.execute(text(f'ALTER TABLE "{rascunho}".produto_regras DROP COLUMN centro_custo_id'))
    await db.execute(
        text(
            f'INSERT INTO "{rascunho}".produto_regras (sku_base, margem_min, duimp_padrao) '
            "VALUES ('dg019', 0.12, 'texto'), ('b001.12', NULL, NULL)"
        )
    )
    await db.execute(
        text(
            f'INSERT INTO "{rascunho}".centros_custo (id, nome) VALUES '
            "('00000000-0000-0000-0000-0000000000c1', 'Brinquedos')"
        )
    )
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    async def _linhas():
        return (
            await db.execute(
                text(
                    "SELECT sku_base, margem_min::text, duimp_padrao FROM "
                    f'"{rascunho}".produto_regras ORDER BY sku_base'
                )
            )
        ).all()

    antes = None
    mod.SCHEMA = rascunho
    try:
        antes = await _linhas()
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        feito = await _catalogo(db, rascunho)
        assert feito["coluna"] == [("centro_custo_id", "uuid", "YES", None)]
        assert feito == await _catalogo(db, modelo)
        # Nasce vazia em todos (nada copiado) e as outras colunas ficam como estavam.
        assert await _linhas() == antes
        vazios = (
            await db.execute(
                text(
                    f'SELECT count(*) FROM "{rascunho}".produto_regras '
                    "WHERE centro_custo_id IS NOT NULL"
                )
            )
        ).scalar_one()
        assert vazios == 0
        # Um centro escolhido por algum produto não sai (RESTRICT).
        await db.execute(
            text(
                f'UPDATE "{rascunho}".produto_regras SET centro_custo_id = '
                "'00000000-0000-0000-0000-0000000000c1' WHERE sku_base = 'dg019'"
            )
        )
        await db.commit()
        with pytest.raises(Exception, match="fk_produto_regras_centro_custo_id_centros_custo"):
            await db.execute(text(f'DELETE FROM "{rascunho}".centros_custo'))
        await db.rollback()
        # Centro que não existe não entra.
        with pytest.raises(Exception, match="fk_produto_regras_centro_custo_id_centros_custo"):
            await db.execute(
                text(
                    f'UPDATE "{rascunho}".produto_regras SET centro_custo_id = '
                    f"'{uuid.uuid4()}' WHERE sku_base = 'b001.12'"
                )
            )
        await db.rollback()

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert (await _catalogo(db, rascunho)) == {"coluna": [], "fks": [], "indices": []}
        assert await _linhas() == antes
        # O centro agora pode sair (ninguém aponta).
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        assert (await _catalogo(db, rascunho))["coluna"] == [
            ("centro_custo_id", "uuid", "YES", None)
        ]
        assert await _linhas() == antes
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
