"""Cadastros › Centro de custo (M-76, Harry 25/11/2026) — routers/centros_custo.py,
services/centros_custo.py, a ligação com as Categorias de venda (M-75) e a migration 0514.

Cobre:
- a lista (quem vê Cadastros › Produtos): Celular, Eletro, Mala e depois os novos; sem o Catálogo;
- incluir: só o nome, até 60 letras, sem repetir (sem olhar maiúsculas), nem o nome da raiz antiga
  "Catálogo"; nasce sem raiz (nada muda em Segmentos) e aparece no menu de Categoria de venda;
- centro novo ainda não recebe Categoria de venda ("Categoria de venda só em Celular, Eletro e
  Mala por enquanto.") e nenhum segmento é criado;
- renomear: Celular, Eletro e Mala não (aqui nem pela aba Segmentos); um novo, sim; um novo com
  raiz troca o nome da raiz junto (o slug não) — e pela aba Segmentos troca o do centro;
- excluir: fixo nunca (o exemplo do pedido: "Mala" diz quantas categorias, contas, lojas e
  produtos usam); novo sem uso sai e nenhum segmento é apagado; com uso, recusa com as contagens;
- a raiz de um centro não sai nem vira filha pela aba Segmentos (antes do RESTRICT do banco);
- só administrador inclui, renomeia e exclui;
- a 0514: igual ao model, semente das 3 raízes (Catálogo fora, filho de mesmo slug fora),
  downgrade e upgrade de novo.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text

from app.models import (
    Base,
    CentroCusto,
    PricingAccount,
    PricingPlatform,
    PricingProduct,
    Product,
    Segment,
    StoreInfo,
    UserRole,
)

pytestmark = pytest.mark.asyncio

ROTA = "/api/centros-custo"
VENDA = "/api/produto-cadastros/categorias-venda"
MSG_FIXO = (
    "Celular, Eletro e Mala não mudam de nome: o sistema procura esses nomes em outras telas."
)
MSG_SO_FIXOS = "Categoria de venda só em Celular, Eletro e Mala por enquanto."


@pytest.fixture
async def base(db):
    """As raízes de hoje (Celular, Mala, Eletro e o Catálogo antigo), 2 categorias de venda em
    cada e os centros de custo como a 0514 semeia (só os três, fixos)."""
    pares = (("Celular", "celular"), ("Mala", "mala"), ("Eletro", "eletro"))
    raizes = {
        nome: Segment(name=nome, slug=slug, sort_order=i)
        for i, (nome, slug) in enumerate((*pares, ("Catálogo", "catalogo")))
    }
    db.add_all(raizes.values())
    await db.flush()
    filhos = {}
    for nome, raiz in raizes.items():
        for i in range(2):
            s = Segment(parent_id=raiz.id, name=f"{nome} {i + 1}", slug=f"t{i}", sort_order=i)
            db.add(s)
            filhos[f"{nome}/{i + 1}"] = s
    ccs = {
        n: CentroCusto(nome=n, segment_id=raizes[n].id, fixo=True)
        for n in ("Celular", "Eletro", "Mala")
    }
    db.add_all(ccs.values())
    await db.commit()
    return {"raiz": raizes, "filho": filhos, "cc": ccs}


async def _admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _centros(db) -> list[tuple[str, bool, object]]:
    linhas = (
        await db.execute(
            select(CentroCusto).order_by(CentroCusto.nome).execution_options(populate_existing=True)
        )
    ).scalars()
    return [(c.nome, c.fixo, c.segment_id) for c in linhas]


async def _qtd_segmentos(db) -> int:
    return len((await db.execute(select(Segment.id))).all())


# ================================================================ lista


async def test_lista_quem_ve_produtos(client, make_user, auth_as, base, db):
    db.add_all([CentroCusto(nome="Brinquedos"), CentroCusto(nome="acessórios gerais")])
    await db.commit()
    auth_as(await make_user(permissions={"cadastro_produtos": {"view": True}}))
    r = await client.get(ROTA)
    assert r.status_code == 200, r.text
    assert [(c["nome"], c["fixo"]) for c in r.json()] == [
        ("Celular", True),
        ("Eletro", True),
        ("Mala", True),
        # Os novos depois, em ordem alfabética (sem olhar maiúsculas).
        ("acessórios gerais", False),
        ("Brinquedos", False),
    ]
    mala = next(c for c in r.json() if c["nome"] == "Mala")
    assert mala["segment_id"] == str(base["raiz"]["Mala"].id)
    assert next(c for c in r.json() if c["nome"] == "Brinquedos")["segment_id"] is None

    auth_as(await make_user(permissions={"segmentos": {"view": True}}))
    r = await client.get(ROTA)
    assert r.status_code == 403


# ================================================================ incluir


async def test_incluir_brinquedos_aparece_no_menu_sem_raiz(client, db, make_user, auth_as, base):
    """O exemplo do pedido: "Brinquedos" entra na lista e no menu Centro de custo; nenhum
    segmento é criado (não vira botão em tela nenhuma)."""
    await _admin(make_user, auth_as)
    antes = await _qtd_segmentos(db)
    r = await client.post(ROTA, json={"nome": "  Brinquedos  "})
    assert r.status_code == 201, r.text
    novo = r.json()
    assert (novo["nome"], novo["fixo"], novo["segment_id"]) == ("Brinquedos", False, None)
    assert await _qtd_segmentos(db) == antes
    nomes = [c["nome"] for c in (await client.get(ROTA)).json()]
    assert nomes == ["Celular", "Eletro", "Mala", "Brinquedos"]
    # O menu "Centro de custo" do Novo cadastro de Categoria de venda (Cadastros › Produtos).
    menu = (await client.get("/api/produto-cadastros")).json()["centros_custo"]
    assert [(c["nome"], c["aceita_categoria"]) for c in menu] == [
        ("Celular", True),
        ("Eletro", True),
        ("Mala", True),
        ("Brinquedos", False),
    ]
    assert menu[-1]["id"] == novo["id"]
    feito = (
        await db.execute(
            select(CentroCusto)
            .where(CentroCusto.id == novo["id"])
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert feito.criado_por is not None and feito.criado_em is not None


async def test_incluir_regras_do_nome(client, make_user, auth_as, base):
    await _admin(make_user, auth_as)
    for nome in ("celular", "MALA", " Eletro "):
        r = await client.post(ROTA, json={"nome": nome})
        assert r.status_code == 409, nome
        assert r.json()["detail"]["code"] == "nome_repetido"
    r = await client.post(ROTA, json={"nome": "catálogo"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "nome_de_segmento"
    r = await client.post(ROTA, json={"nome": "x" * 61})
    assert r.status_code == 422
    assert r.json()["detail"]["mensagem"] == "O nome do centro de custo vai até 60 letras."
    r = await client.post(ROTA, json={"nome": "x" * 60})
    assert r.status_code == 201, r.text
    r = await client.post(ROTA, json={"nome": "   "})
    assert r.status_code == 422
    r = await client.post(ROTA, json={"nome": "Brinquedos"})
    assert r.status_code == 201
    r = await client.post(ROTA, json={"nome": "BRINQUEDOS"})
    assert r.status_code == 409
    assert r.json()["detail"]["mensagem"] == "Já existe o centro de custo “Brinquedos”."


async def test_centro_novo_nao_recebe_categoria_de_venda(client, db, make_user, auth_as, base):
    await _admin(make_user, auth_as)
    novo = (await client.post(ROTA, json={"nome": "Brinquedos"})).json()
    antes = await _qtd_segmentos(db)
    r = await client.post(VENDA, json={"centro_custo_id": novo["id"], "nome": "Pelúcias"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {
        "code": "categoria_venda_centro_sem_raiz",
        "mensagem": MSG_SO_FIXOS,
    }
    assert await _qtd_segmentos(db) == antes, "nenhuma raiz nem categoria criada"
    # Nos fixos continua: pelo id do centro (M-76) e pelo da raiz (a tela da M-75).
    r = await client.post(
        VENDA, json={"centro_custo_id": str(base["cc"]["Eletro"].id), "nome": "Eletro 3"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["centro_custo"] == "Eletro"
    r = await client.post(
        VENDA, json={"centro_custo_id": str(base["raiz"]["Eletro"].id), "nome": "Eletro 4"}
    )
    assert r.status_code == 201, r.text
    # O Catálogo não é centro de custo.
    r = await client.post(
        VENDA, json={"centro_custo_id": str(base["raiz"]["Catálogo"].id), "nome": "X"}
    )
    assert r.status_code == 404


# ================================================================ renomear


async def test_fixos_nao_mudam_de_nome_nem_pela_aba_segmentos(client, make_user, auth_as, base):
    await _admin(make_user, auth_as)
    for nome in ("Celular", "Eletro", "Mala"):
        r = await client.patch(f"{ROTA}/{base['cc'][nome].id}", json={"nome": f"{nome}s"})
        assert r.status_code == 400, nome
        assert r.json()["detail"] == {"code": "centro_fixo_nome", "mensagem": MSG_FIXO}
        # O mesmo nome volta sem erro (nada muda).
        r = await client.patch(f"{ROTA}/{base['cc'][nome].id}", json={"nome": nome})
        assert r.status_code == 200
        r = await client.patch(f"/api/segments/{base['raiz'][nome].id}", json={"name": f"{nome}s"})
        assert r.status_code == 400, nome
        assert r.json()["detail"]["mensagem"] == MSG_FIXO
    segs = {s["id"]: s["name"] for s in (await client.get("/api/segments")).json()}
    assert segs[str(base["raiz"]["Mala"].id)] == "Mala"
    # Outros campos da raiz continuam pela aba Segmentos (ex.: DUIMP/mensagem).
    r = await client.patch(
        f"/api/segments/{base['raiz']['Mala'].id}", json={"duimp_padrao": "Texto da Mala"}
    )
    assert r.status_code == 200, r.text
    # A raiz antiga Catálogo não é centro de custo: renomeia como antes.
    r = await client.patch(f"/api/segments/{base['raiz']['Catálogo'].id}", json={"name": "Cat"})
    assert r.status_code == 200


async def test_renomear_centro_novo(client, db, make_user, auth_as, base):
    await _admin(make_user, auth_as)
    novo = (await client.post(ROTA, json={"nome": "Brinquedos"})).json()
    r = await client.patch(f"{ROTA}/{novo['id']}", json={"nome": " Jogos  e brinquedos "})
    assert r.status_code == 200, r.text
    assert r.json()["nome"] == "Jogos e brinquedos"
    r = await client.patch(f"{ROTA}/{novo['id']}", json={"nome": "mala"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "nome_repetido"
    r = await client.patch(f"{ROTA}/{novo['id']}", json={"nome": "Catálogo"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "nome_de_segmento"
    # Só maiúsculas no próprio nome: pode.
    r = await client.patch(f"{ROTA}/{novo['id']}", json={"nome": "JOGOS E BRINQUEDOS"})
    assert r.status_code == 200 and r.json()["nome"] == "JOGOS E BRINQUEDOS"


async def test_centro_novo_com_raiz_troca_o_nome_junto(client, db, make_user, auth_as, base):
    """Ninguém cria raiz para centro novo hoje, mas a regra do contrato fica segura: o nome do
    centro e o da raiz andam juntos (o slug não muda), pelas duas telas."""
    await _admin(make_user, auth_as)
    raiz = Segment(name="Brinquedos", slug="brinquedos", sort_order=9, active=False)
    db.add(raiz)
    await db.flush()
    centro = CentroCusto(nome="Brinquedos", segment_id=raiz.id)
    db.add(centro)
    await db.commit()
    r = await client.patch(f"{ROTA}/{centro.id}", json={"nome": "Jogos"})
    assert r.status_code == 200, r.text
    segs = {s["id"]: s for s in (await client.get("/api/segments")).json()}
    assert (segs[str(raiz.id)]["name"], segs[str(raiz.id)]["slug"]) == ("Jogos", "brinquedos")
    r = await client.patch(f"/api/segments/{raiz.id}", json={"name": "Brinquedos e jogos"})
    assert r.status_code == 200, r.text
    assert ("Brinquedos e jogos", False, raiz.id) in await _centros(db)
    r = await client.patch(f"/api/segments/{raiz.id}", json={"name": "Mala"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "nome_repetido"


# ================================================================ excluir


async def test_excluir_mala_recusa_e_diz_quantos_usam(client, db, make_user, auth_as, base):
    """O exemplo do pedido: tentar excluir "Mala" recusa e mostra quantos usam."""
    dono = await _admin(make_user, auth_as)
    mala = base["raiz"]["Mala"]
    m1, m2 = base["filho"]["Mala/1"], base["filho"]["Mala/2"]
    loja = StoreInfo(user_id=dono.id, platform="shopee", account_name="loja mala")
    site = StoreInfo(user_id=dono.id, platform="site", manual_departments=["mala"])
    outra = StoreInfo(user_id=dono.id, platform="shopee", account_name="loja celular")
    db.add_all([loja, site, outra])
    await db.flush()
    db.add_all(
        [
            PricingAccount(
                user_id=dono.id,
                name="loja mala",
                platform=PricingPlatform.SHOPEE,
                segment_id=mala.id,
                store_info_id=loja.id,
            ),
            PricingAccount(
                user_id=dono.id,
                name="loja celular",
                platform=PricingPlatform.SHOPEE,
                segment_id=base["raiz"]["Celular"].id,
                store_info_id=outra.id,
            ),
            PricingProduct(user_id=dono.id, sku="b001", name="Mala 1", segment_id=m1.id),
            PricingProduct(user_id=dono.id, sku="b002", name="Mala 2", segment_id=m2.id),
            PricingProduct(user_id=dono.id, sku="b003", name="Mala 3", segment_id=m2.id),
            Product(user_id=dono.id, sku="b001.sp", name="Mala 1", segment_id=m1.id),
        ]
    )
    await db.commit()
    cc = base["cc"]["Mala"]
    esperado = (
        "“Mala” não pode ser excluído: Celular, Eletro e Mala são fixos do sistema. Hoje usam: "
        "2 categorias de venda, 1 conta, 2 lojas, 3 produtos da Tabela de Preços e 1 produto "
        "do cadastro."
    )
    uso = (await client.get(f"{ROTA}/{cc.id}/uso")).json()
    assert uso == {
        "categorias": 2,
        "contas": 1,
        "lojas": 2,
        "produtos": 4,
        "produtos_tabela": 3,
        "produtos_cadastro": 1,
        "bloqueia": True,
        "mensagem": esperado,
    }
    r = await client.delete(f"{ROTA}/{cc.id}")
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "centro_fixo",
        "mensagem": esperado,
        "categorias": 2,
        "contas": 1,
        "lojas": 2,
        "produtos": 4,
    }
    assert ("Mala", True, mala.id) in await _centros(db)
    # O Eletro, mesmo sem conta nem produto, também não sai (fixo).
    r = await client.delete(f"{ROTA}/{base['cc']['Eletro'].id}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "centro_fixo"


async def test_excluir_centro_novo(client, db, make_user, auth_as, base):
    await _admin(make_user, auth_as)
    novo = (await client.post(ROTA, json={"nome": "Brinquedos"})).json()
    antes = await _qtd_segmentos(db)
    uso = (await client.get(f"{ROTA}/{novo['id']}/uso")).json()
    assert (uso["bloqueia"], uso["mensagem"], uso["categorias"]) == (False, None, 0)
    r = await client.delete(f"{ROTA}/{novo['id']}")
    assert r.status_code == 204
    assert [n for n, _f, _s in await _centros(db)] == ["Celular", "Eletro", "Mala"]
    assert await _qtd_segmentos(db) == antes, "nenhum segmento apagado"
    r = await client.delete(f"{ROTA}/{novo['id']}")
    assert r.status_code == 404


async def test_centro_novo_com_uso_nao_sai(client, db, make_user, auth_as, base):
    await _admin(make_user, auth_as)
    raiz = Segment(name="Brinquedos", slug="brinquedos", sort_order=9, active=False)
    db.add(raiz)
    await db.flush()
    db.add(Segment(parent_id=raiz.id, name="Pelúcias", slug="pelucias", sort_order=0))
    centro = CentroCusto(nome="Brinquedos", segment_id=raiz.id)
    db.add(centro)
    await db.commit()
    r = await client.delete(f"{ROTA}/{centro.id}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "centro_em_uso"
    assert r.json()["detail"]["mensagem"] == (
        "“Brinquedos” está em uso: 1 categoria de venda. Só dá para excluir um centro de custo "
        "que ninguém usa."
    )


async def test_raiz_de_centro_nao_sai_nem_vira_filha_pela_aba_segmentos(
    client, db, make_user, auth_as, base
):
    await _admin(make_user, auth_as)
    mala = base["raiz"]["Mala"]
    r = await client.delete(f"/api/segments/{mala.id}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "centro_custo_raiz"
    r = await client.patch(
        f"/api/segments/{mala.id}", json={"parent_id": str(base["filho"]["Celular/1"].id)}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "centro_custo_raiz"
    assert ("Mala", True, mala.id) in await _centros(db)
    segs = {s["id"]: s for s in (await client.get("/api/segments")).json()}
    assert segs[str(mala.id)]["parent_id"] is None


# ================================================================ quem pode


async def test_so_administrador_mexe(client, make_user, auth_as, base):
    auth_as(
        await make_user(
            permissions={"cadastro_produtos": {"view": True, "edit": True, "delete": True}}
        )
    )
    alvo = base["cc"]["Mala"].id
    for pedido in (
        client.post(ROTA, json={"nome": "X"}),
        client.patch(f"{ROTA}/{alvo}", json={"nome": "X"}),
        client.get(f"{ROTA}/{alvo}/uso"),
        client.delete(f"{ROTA}/{alvo}"),
    ):
        r = await pedido
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "admin_only"


# ================================================================ migration 0514

_MIGRATION = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0514_centros_custo.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0514", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db, schema: str) -> dict:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "") if s else s

    p = {"s": schema, "t": "centros_custo"}
    colunas = (
        await db.execute(
            text(
                "SELECT column_name, udt_name, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t ORDER BY column_name"
            ),
            p,
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                "SELECT co.conname, co.contype::text, pg_get_constraintdef(co.oid) "
                "FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :s AND cl.relname = :t ORDER BY co.conname"
            ),
            p,
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = :s AND tablename = :t ORDER BY indexname"
            ),
            p,
        )
    ).all()
    return {
        "colunas": [tuple(r[:4]) + (limpo(r[4]),) for r in colunas],
        "constraints": [(r[0], r[1], limpo(r[2])) for r in constraints],
        "indices": [(r[0], limpo(r[1])) for r in indices],
    }


async def test_0514_igual_ao_model_semeia_as_tres_raizes_e_desce(db):
    mod = _carregar()
    assert mod.revision == "0514_centros_custo"
    assert mod.down_revision == "0511_contas_ml_par_custo_total"
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    raiz = Path(__file__).resolve().parent.parent
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "alembic"))
    roteiro = ScriptDirectory.from_config(cfg)
    assert roteiro.get_heads() == ["0514_centros_custo"], roteiro.get_heads()

    modelo = Base.metadata.schema
    rascunho = f"{modelo}_m0514"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a 0514 toca: as chaves de users e segments (como na produção).
    await db.execute(text(f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY)'))
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".segments (id uuid PRIMARY KEY, parent_id uuid, '
            "name varchar(128) NOT NULL, slug varchar(128) NOT NULL)"
        )
    )
    # As raízes da produção (Celular, Mala, Eletro, Catálogo) e um filho com slug "celular"
    # (não é raiz: não entra).
    await db.execute(
        text(
            f'INSERT INTO "{rascunho}".segments (id, parent_id, name, slug) VALUES '
            "('00000000-0000-0000-0000-000000000001', NULL, 'Celular', 'celular'), "
            "('00000000-0000-0000-0000-000000000002', NULL, 'Mala', 'mala'), "
            "('00000000-0000-0000-0000-000000000003', NULL, 'Eletro', 'eletro'), "
            "('00000000-0000-0000-0000-000000000004', NULL, 'Catálogo', 'catalogo'), "
            "('00000000-0000-0000-0000-000000000005', "
            "'00000000-0000-0000-0000-000000000004', 'Celular', 'celular')"
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
                    "SELECT nome, segment_id::text, fixo, criado_por "
                    f'FROM "{rascunho}".centros_custo ORDER BY nome'
                )
            )
        ).all()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        da_migration = await _catalogo(db, rascunho)
        assert da_migration["colunas"], "a migration não criou a tabela"
        assert da_migration == await _catalogo(db, modelo)
        assert await _linhas() == [
            ("Celular", "00000000-0000-0000-0000-000000000001", True, None),
            ("Eletro", "00000000-0000-0000-0000-000000000003", True, None),
            ("Mala", "00000000-0000-0000-0000-000000000002", True, None),
        ]
        # Nome repetido sem olhar maiúsculas; a raiz de um centro não sai (RESTRICT).
        with pytest.raises(Exception, match="uq_centros_custo_nome"):
            await db.execute(
                text(f"INSERT INTO \"{rascunho}\".centros_custo (nome) VALUES ('MALA')")
            )
        await db.rollback()
        with pytest.raises(Exception, match="fk_centros_custo_segment_id_segments"):
            await db.execute(
                text(
                    f'DELETE FROM "{rascunho}".segments '
                    "WHERE id = '00000000-0000-0000-0000-000000000002'"
                )
            )
        await db.rollback()

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert (await _catalogo(db, rascunho))["colunas"] == []
        # Os segmentos ficam como estavam.
        assert (
            await db.execute(text(f'SELECT count(*) FROM "{rascunho}".segments'))
        ).scalar_one() == 5

        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        assert len(await _linhas()) == 3
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
