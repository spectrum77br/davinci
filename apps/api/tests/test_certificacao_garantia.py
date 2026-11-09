"""Garantia e validade pelo certificado (06/11/2026).

Eduardo: "tem que deixar opção para editar a validade, e em certificações
adicione o campo garantia, e aí ele passa a mudar de acordo com o certificado";
"se eu mudar em certificações muda a coluna validade lá na tabela de preços, e
deixe a opção de editar na tabela de preços".

* Migration 0461: `financeiro_suprimentos.garantia_meses` (0..120, NULL).
* Certificações: mudar a garantia do certificado muda a Garantia (meses) de
  todos os produtos dele (o gravado na coluna Modelo ou o achado pelo nome),
  com vigência e recálculo das garantias (o mesmo caminho do PATCH do produto).
* Tabela de Preços: a Garantia de produto cujo certificado tem garantia grava
  no certificado; produto sem certificado fica com a dele; trocar o
  certificado pega a garantia do novo; a Validade grava o Fim do certificado.

A tabela financeiro_suprimentos não é limpa entre os testes (conftest): cada
teste usa códigos de modelo próprios e apaga o que criou.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import inspect
import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    FinanceiroSuprimentos,
    Garantia,
    PricingGarantiaPrazo,
    UserRole,
)
from app.models.base import Base
from app.models.pricing import PricingProduct
from app.models.segment import Segment
from app.services import garantia as svc
from app.services import garantia_resumo as resumo
from app.services.certificacoes_edicao import aplicar_campos_oficiais

pytestmark = pytest.mark.asyncio

_VERSOES = Path(__file__).resolve().parent.parent / "alembic" / "versions"
EDITOR_TABELA = {
    "tabela_precos": {"view": True, "edit": True, "delete": False},
    "tabela_precos_produtos": {"view": True, "edit": True, "delete": False},
}


@pytest.fixture(autouse=True)
def _mapa_novo():
    resumo.limpar_cache()
    yield
    resumo.limpar_cache()


def _codigo() -> str:
    """Código de modelo só deste teste (letra + número, como USM003)."""
    return f"T{uuid.uuid4().hex[:6].upper()}9"


@pytest_asyncio.fixture
async def certificados(db: AsyncSession):
    """Cria linhas da Certificações e apaga no fim (a tabela não entra na
    limpeza do conftest)."""
    criados: list[uuid.UUID] = []

    async def _novo(modelo: str, **campos) -> FinanceiroSuprimentos:
        campos.setdefault("produto", "smartphone 4G")
        campos.setdefault("certificado", "anatel")
        campos.setdefault("numero", f"N-{modelo}")
        campos.setdefault("fim", date(2028, 7, 30))
        row = FinanceiroSuprimentos(modelo=modelo, **campos)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        criados.append(row.id)
        return row

    yield _novo
    await db.rollback()
    await db.execute(
        PricingProduct.__table__.update()
        .where(PricingProduct.__table__.c.certificacao_id.in_(criados))
        .values(certificacao_id=None)
    )
    await db.execute(delete(FinanceiroSuprimentos).where(FinanceiroSuprimentos.id.in_(criados)))
    await db.commit()


async def _segmento(db: AsyncSession) -> Segment:
    seg = (
        await db.execute(
            select(Segment).where(Segment.slug == "celular", Segment.parent_id.is_(None))
        )
    ).scalar_one_or_none()
    if seg is None:
        seg = Segment(name="Celular", slug="celular")
        db.add(seg)
        await db.commit()
    return seg


async def _produto(db, dono, sku, nome, *, cert=None, meses=None) -> PricingProduct:
    seg = await _segmento(db)
    p = PricingProduct(
        user_id=dono.id,
        sku=sku,
        name=nome,
        segment_id=seg.id,
        certificacao_id=cert.id if cert is not None else None,
        garantia_meses=meses,
    )
    db.add(p)
    await db.commit()
    return p


async def _venda(db, numero: str, sku: str, entrega: date) -> Garantia:
    compra = datetime(2026, 10, 1, 12, tzinfo=svc.SAO_PAULO)
    db.add(
        BlingOrder(
            numero=numero,
            numeroloja=f"MKT{numero}",
            loja="L1",
            data=compra,
            total=1000,
            situacao="15",
            item_index=0,
            item_codigo=f"{sku}.sp",
            item_descricao="Celular Uranyx",
            item_quantidade=1,
        )
    )
    g = Garantia(
        origem="robo",
        pedido_bling=numero,
        pedido_marketplace=f"MKT{numero}",
        loja="L1",
        nf_numero="9" + numero[-4:],
        cliente_nome="Cliente",
        cpf="52998224725",
        itens=[
            {"sku": f"{sku}.sp", "descricao": "Celular Uranyx", "quantidade": 1, "uranyx": True}
        ],
    )
    svc.aplicar_entrega(g, svc.Entrega(datetime.combine(entrega, time(12), UTC), "shopee"))
    db.add(g)
    await db.commit()
    return g


async def _meses(db, *produtos: PricingProduct) -> list[int | None]:
    for p in produtos:
        await db.refresh(p)
    return [p.garantia_meses for p in produtos]


async def _vigencias(db, produto: PricingProduct) -> list[tuple]:
    rows = (
        await db.execute(
            select(
                PricingGarantiaPrazo.meses,
                PricingGarantiaPrazo.meses_anterior,
                PricingGarantiaPrazo.user_id,
            )
            .where(PricingGarantiaPrazo.pricing_product_id == produto.id)
            .order_by(PricingGarantiaPrazo.id)
        )
    ).all()
    return [tuple(r) for r in rows]


# ── Migration ────────────────────────────────────────────────────────────────


def _carregar(nome: str):
    spec = importlib.util.spec_from_file_location(f"migration_{nome}", _VERSOES / f"{nome}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COLUNA_E_CHECK = text(
    "SELECT c.udt_name, c.is_nullable, pg_get_constraintdef(co.oid)"
    " FROM information_schema.columns c"
    " JOIN pg_namespace n ON n.nspname = c.table_schema"
    " JOIN pg_class cl ON cl.relnamespace = n.oid AND cl.relname = c.table_name"
    " JOIN pg_constraint co ON co.conrelid = cl.oid"
    "  AND co.conname = 'ck_financeiro_suprimentos_garantia_meses'"
    " WHERE c.table_schema = :schema AND c.table_name = 'financeiro_suprimentos'"
    "  AND c.column_name = 'garantia_meses'"
)


async def test_migration_0461_sobe_igual_ao_model_e_desce(db: AsyncSession):
    mod = _carregar("0461_certificacao_garantia")
    assert (mod.revision, mod.down_revision) == (
        "0461_certificacao_garantia",
        "0460_garantia_sem_garantia",
    )
    for passo in (mod.upgrade, mod.downgrade):
        assert "SET LOCAL lock_timeout = '5s'" in inspect.getsource(passo)
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0461"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    await db.execute(
        text(f'CREATE TABLE "{rascunho}".financeiro_suprimentos (id uuid PRIMARY KEY)')
    )
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        da_migration = (await db.execute(_COLUNA_E_CHECK, {"schema": rascunho})).all()
        do_model = (await db.execute(_COLUNA_E_CHECK, {"schema": schema_model})).all()
        assert da_migration == do_model
        assert len(da_migration) == 1
        assert da_migration[0][:2] == ("int2", "YES")
        assert "garantia_meses >= 0" in da_migration[0][2]
        assert "garantia_meses <= 120" in da_migration[0][2]
        # 0 (sem garantia), 120 e NULL entram; 121 e -1 não.
        for bom in ("0", "120", "NULL"):
            await db.execute(
                text(
                    f'INSERT INTO "{rascunho}".financeiro_suprimentos (id, garantia_meses)'
                    f" VALUES ('{uuid.uuid4()}', {bom})"
                )
            )
        await db.commit()
        for ruim in (121, -1):
            with pytest.raises(Exception, match="ck_financeiro_suprimentos_garantia_meses"):
                await db.execute(
                    text(f'UPDATE "{rascunho}".financeiro_suprimentos SET garantia_meses = {ruim}')
                )
            await db.rollback()

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert (await db.execute(_COLUNA_E_CHECK, {"schema": rascunho})).all() == []
        colunas = (
            (
                await db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema = :s AND table_name = 'financeiro_suprimentos'"
                    ),
                    {"s": rascunho},
                )
            )
            .scalars()
            .all()
        )
        assert colunas == ["id"]
        # Sobe de novo depois de descer.
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        assert len((await db.execute(_COLUNA_E_CHECK, {"schema": rascunho})).all()) == 1
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


# ── Certificações → produtos ─────────────────────────────────────────────────


async def test_garantia_do_certificado_vai_para_todos_os_produtos_dele(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cod, outro_cod = _codigo(), _codigo()
    cert = await certificados(cod)
    outro = await certificados(outro_cod)
    pelo_nome = await _produto(db, admin, "dg901", f"uranyx A17 - {cod}", meses=6)
    gravado = await _produto(db, admin, "dg902", "uranyx C17", cert=cert)
    # O gravado vence o nome: este é do outro certificado.
    do_outro = await _produto(db, admin, "dg903", f"uranyx X - {cod}", cert=outro, meses=3)
    solto = await _produto(db, admin, "dg904", "uranyx sem certificado")
    venda = await _venda(db, "610001", "dg902", date(2026, 10, 5))

    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"garantia_meses": 12})
    assert r.status_code == 200, r.text
    assert r.json()["garantia_meses"] == 12
    assert "garantia_meses" in r.json()["campos_manuais"]
    assert await _meses(db, pelo_nome, gravado, do_outro, solto) == [12, 12, 3, None]
    # A vigência de cada produto que mudou, em nome de quem mudou.
    assert await _vigencias(db, pelo_nome) == [(12, 6, admin.id)]
    assert await _vigencias(db, gravado) == [(12, None, admin.id)]
    assert await _vigencias(db, do_outro) == []
    # E a garantia da venda foi recalculada.
    await db.refresh(venda)
    assert (venda.prazo_meses, venda.fim_garantia) == (12, date(2027, 10, 5))

    # A Tabela de Preços mostra a garantia do certificado.
    r = await client.get("/api/pricing/products")
    linhas = {p["sku"]: p for p in r.json()}
    assert linhas["dg901"]["certificacao"]["garantia_meses"] == 12
    assert linhas["dg901"]["certificacao_pelo_nome"] is True
    assert linhas["dg902"]["certificacao"]["garantia_meses"] == 12
    assert linhas["dg903"]["certificacao"]["garantia_meses"] is None
    r = await client.get("/api/pricing/certificacoes")
    assert {c["id"]: c["garantia_meses"] for c in r.json()}[str(cert.id)] == 12
    # Mudou o Fim na Certificações → a coluna Validade da Tabela acompanha.
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"fim": "2031-12-31"})
    assert r.status_code == 200
    r = await client.get("/api/pricing/products")
    linhas = {p["sku"]: p for p in r.json()}
    assert linhas["dg901"]["certificacao"]["fim"] == "2031-12-31"
    assert linhas["dg902"]["certificacao"]["fim"] == "2031-12-31"
    # Mudar outro campo do certificado não mexe no prazo de ninguém.
    assert await _vigencias(db, gravado) == [(12, None, admin.id)]

    # Mandar o mesmo valor de novo não grava vigência nova.
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"garantia_meses": 12})
    assert r.status_code == 200
    assert await _vigencias(db, gravado) == [(12, None, admin.id)]
    # Mas acerta quem tinha ficado diferente.
    gravado.garantia_meses = 2
    await db.commit()
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"garantia_meses": 12})
    assert await _meses(db, gravado) == [12]

    # Garantia vazia no certificado não mexe nos produtos.
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"garantia_meses": None})
    assert r.status_code == 200 and r.json()["garantia_meses"] is None
    assert await _meses(db, pelo_nome, gravado) == [12, 12]
    # Fora de 0..120: recusa, nada muda.
    for ruim in (-1, 121):
        r = await client.patch(
            f"/api/financeiro/suprimentos/{cert.id}", json={"garantia_meses": ruim}
        )
        assert r.status_code == 422
    # 0 = sem garantia, também vai para os produtos.
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"garantia_meses": 0})
    assert r.status_code == 200
    assert await _meses(db, pelo_nome, gravado, do_outro) == [0, 0, 3]


async def test_certificado_novo_com_garantia_ja_vale_para_os_produtos(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cod = _codigo()
    p = await _produto(db, admin, "dg911", f"uranyx novo - {cod}")
    r = await client.post(
        "/api/financeiro/suprimentos",
        json={
            "produto": "smartphone",
            "modelo": cod,
            "certificado": "anatel",
            "garantia_meses": 24,
        },
    )
    assert r.status_code == 201, r.text
    novo = await db.get(FinanceiroSuprimentos, uuid.UUID(r.json()["id"]))
    try:
        assert await _meses(db, p) == [24]
        assert await _vigencias(db, p) == [(24, None, admin.id)]
    finally:
        await db.execute(
            PricingProduct.__table__.update()
            .where(PricingProduct.__table__.c.id == p.id)
            .values(certificacao_id=None)
        )
        await db.delete(novo)
        await db.commit()


# ── Tabela de Preços → certificado ───────────────────────────────────────────


async def test_garantia_editada_na_tabela_grava_no_certificado(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    # Quem edita a Tabela e não vê a Certificações: grava no certificado mesmo assim.
    editor = await make_user(permissions=EDITOR_TABELA)
    cod = _codigo()
    cert = await certificados(cod, garantia_meses=12)
    gravado = await _produto(db, admin, "dg921", "uranyx A", cert=cert, meses=12)
    pelo_nome = await _produto(db, admin, "dg922", f"uranyx B - {cod}", meses=12)
    sem_cert = await _produto(db, admin, "dg923", "uranyx sem certificado")

    auth_as(editor)
    r = await client.patch(f"/api/pricing/products/{gravado.id}", json={"garantia_meses": 24})
    assert r.status_code == 200, r.text
    assert r.json()["garantia_meses"] == 24
    assert r.json()["certificacao"]["garantia_meses"] == 24
    await db.refresh(cert)
    assert cert.garantia_meses == 24
    assert "garantia_meses" in (cert.campos_manuais or [])
    assert await _meses(db, gravado, pelo_nome, sem_cert) == [24, 24, None]
    assert await _vigencias(db, gravado) == [(24, 12, editor.id)]
    assert await _vigencias(db, pelo_nome) == [(24, 12, editor.id)]

    # Pelo produto achado pelo nome também vai para o certificado.
    r = await client.patch(f"/api/pricing/products/{pelo_nome.id}", json={"garantia_meses": 18})
    assert r.status_code == 200, r.text
    await db.refresh(cert)
    assert cert.garantia_meses == 18
    assert await _meses(db, gravado, pelo_nome) == [18, 18]

    # Produto sem certificado continua com o prazo dele.
    r = await client.patch(f"/api/pricing/products/{sem_cert.id}", json={"garantia_meses": 6})
    assert r.status_code == 200
    assert r.json()["garantia_meses"] == 6 and r.json()["certificacao"] is None
    await db.refresh(cert)
    assert cert.garantia_meses == 18
    assert await _meses(db, gravado, pelo_nome, sem_cert) == [18, 18, 6]

    # Apagar na Tabela o prazo que vem do certificado (revisão de 06/11/2026):
    # 422 — apagaria do certificado e de todos os produtos dele, e a Tabela
    # não conseguiria religar. Nada muda.
    r = await client.patch(f"/api/pricing/products/{gravado.id}", json={"garantia_meses": None})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "garantia_do_certificado"
    await db.refresh(cert)
    assert cert.garantia_meses == 18
    assert await _meses(db, gravado, pelo_nome) == [18, 18]
    assert len(await _vigencias(db, gravado)) == 2
    # 0 (sem garantia) pela Tabela vale: vai para o certificado e para todos.
    r = await client.patch(f"/api/pricing/products/{gravado.id}", json={"garantia_meses": 0})
    assert r.status_code == 200, r.text
    await db.refresh(cert)
    assert cert.garantia_meses == 0
    assert await _meses(db, gravado, pelo_nome) == [0, 0]
    # E a Tabela religa: o certificado continua definindo o prazo.
    r = await client.patch(f"/api/pricing/products/{pelo_nome.id}", json={"garantia_meses": 12})
    assert r.status_code == 200
    await db.refresh(cert)
    assert cert.garantia_meses == 12
    assert await _meses(db, gravado, pelo_nome) == [12, 12]

    # Certificado SEM garantia (apagada na Certificações): o produto edita o
    # prazo dele, o certificado fica vazio, e apagar volta a valer só na linha.
    auth_as(admin)
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"garantia_meses": None})
    assert r.status_code == 200
    auth_as(editor)
    r = await client.patch(f"/api/pricing/products/{pelo_nome.id}", json={"garantia_meses": 3})
    assert r.status_code == 200
    await db.refresh(cert)
    assert cert.garantia_meses is None
    assert await _meses(db, gravado, pelo_nome) == [12, 3]
    r = await client.patch(f"/api/pricing/products/{pelo_nome.id}", json={"garantia_meses": None})
    assert r.status_code == 200
    assert await _meses(db, gravado, pelo_nome) == [12, None]


async def test_trocar_o_certificado_pega_a_garantia_do_novo(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cod_a, cod_b = _codigo(), _codigo()
    com = await certificados(cod_a, garantia_meses=18)
    sem = await certificados(cod_b)
    p = await _produto(db, admin, "dg931", "uranyx T", cert=sem, meses=6)

    r = await client.patch(f"/api/pricing/products/{p.id}", json={"certificacao_id": str(com.id)})
    assert r.status_code == 200, r.text
    assert r.json()["garantia_meses"] == 18
    assert await _vigencias(db, p) == [(18, 6, admin.id)]
    # Voltar para um certificado sem garantia: fica com o que tem.
    r = await client.patch(f"/api/pricing/products/{p.id}", json={"certificacao_id": str(sem.id)})
    assert r.json()["garantia_meses"] == 18
    # Mesmo certificado de novo: nada muda.
    r = await client.patch(f"/api/pricing/products/{p.id}", json={"certificacao_id": str(sem.id)})
    assert len(await _vigencias(db, p)) == 1

    # Sem vínculo gravado, o nome que passa a achar o certificado também vale.
    q = await _produto(db, admin, "dg932", "uranyx Q", meses=3)
    r = await client.patch(f"/api/pricing/products/{q.id}", json={"name": f"uranyx Q - {cod_a}"})
    assert r.status_code == 200
    assert r.json()["certificacao_pelo_nome"] is True
    assert r.json()["garantia_meses"] == 18

    # Produto novo cujo nome acha o certificado nasce com a garantia dele.
    seg = await _segmento(db)
    r = await client.post(
        "/api/pricing/products",
        json={"sku": "dg933", "name": f"uranyx N - {cod_a}", "segment_id": str(seg.id)},
    )
    assert r.status_code == 201, r.text
    assert r.json()["garantia_meses"] == 18
    novo = await db.get(PricingProduct, uuid.UUID(r.json()["id"]))
    assert await _vigencias(db, novo) == [(18, None, admin.id)]


async def test_validade_editada_na_tabela_grava_o_fim_do_certificado(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    editor = await make_user(permissions=EDITOR_TABELA)
    cod = _codigo()
    # Certificado da Anatel (atualizado pela sincronização oficial).
    cert = await certificados(cod, anatel_numero=f"A-{cod}", fim=date(2028, 7, 30))
    gravado = (await _produto(db, admin, "dg941", "uranyx V", cert=cert)).id
    pelo_nome = (await _produto(db, admin, "dg942", f"uranyx W - {cod}")).id
    sem_cert = await _produto(db, admin, "dg943", "uranyx sem certificado")
    sem_cert_id = sem_cert.id

    auth_as(editor)
    r = await client.patch(
        f"/api/pricing/products/{gravado}", json={"certificado_fim": "2030-01-31"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["certificacao"]["fim"] == "2030-01-31"
    await db.refresh(cert)
    assert cert.fim == date(2030, 1, 31)
    assert cert.campos_manuais == ["fim"]
    # Vale para todos os produtos do certificado (e a Certificações mostra).
    r = await client.get("/api/pricing/products")
    linhas = {p["sku"]: p for p in r.json()}
    assert linhas["dg942"]["certificacao"]["fim"] == "2030-01-31"
    # A sincronização oficial não desfaz a data digitada.
    aplicar_campos_oficiais(cert, {"fim": date(2028, 7, 30), "numero": "OFICIAL"})
    assert (cert.fim, cert.numero) == (date(2030, 1, 31), "OFICIAL")
    db.expire(cert)  # descarta a simulação (não grava)

    # Pelo produto achado pelo nome também; apagar a data também vale.
    r = await client.patch(f"/api/pricing/products/{pelo_nome}", json={"certificado_fim": None})
    assert r.status_code == 200, r.text
    assert r.json()["certificacao"]["fim"] is None
    await db.refresh(cert)
    assert cert.fim is None

    # Produto sem certificado: 422 e nada muda.
    r = await client.patch(
        f"/api/pricing/products/{sem_cert_id}",
        json={"certificado_fim": "2030-01-01", "name": "uranyx mudou"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "sem_certificado"
    await db.refresh(sem_cert)
    assert sem_cert.name == "uranyx sem certificado"
    # Data inválida: 422 do formato.
    r = await client.patch(
        f"/api/pricing/products/{gravado}", json={"certificado_fim": "31/01/2030"}
    )
    assert r.status_code == 422
    # Sem a permissão de editar produtos: 403.
    leitor = await make_user(permissions={"tabela_precos_produtos": {"view": True}})
    auth_as(leitor)
    r = await client.patch(
        f"/api/pricing/products/{gravado}", json={"certificado_fim": "2031-01-01"}
    )
    assert r.status_code == 403
    await db.refresh(cert)
    assert cert.fim is None


# ── Produto que passa a outro certificado (revisão de 06/11/2026) ────────────


async def test_produto_que_passa_a_outro_certificado_pega_a_garantia_dele(
    client, db, make_user, auth_as, certificados
):
    """A Validade, o Modelo ou o Nº de um certificado mudou, ou um certificado
    foi criado ou excluído, e o produto achado pelo nome passou a OUTRO
    certificado: se o novo tem garantia, o produto pega a dele."""
    admin = await make_user(role=UserRole.ADMIN)
    editor = await make_user(permissions=EDITOR_TABELA)
    cod = _codigo()
    # Dois certificados com o mesmo código: o de validade mais longa vence.
    a = await certificados(cod, numero="NA", fim=date(2028, 1, 1), garantia_meses=12)
    b = await certificados(cod, numero="NB", fim=date(2027, 1, 1), garantia_meses=24)
    p = await _produto(db, admin, "dg951", f"uranyx P - {cod}", meses=12)
    q = await _produto(db, admin, "dg952", "uranyx Q", cert=b, meses=24)
    venda = await _venda(db, "610951", "dg951", date(2026, 10, 5))

    # 1) Validade do Q (certificado B) pela Tabela: B passa a vencer o
    # desempate e o P, achado pelo nome, vai para B — com a garantia de B.
    auth_as(editor)
    r = await client.patch(f"/api/pricing/products/{q.id}", json={"certificado_fim": "2030-01-01"})
    assert r.status_code == 200, r.text
    r = await client.get("/api/pricing/products")
    linha = {x["sku"]: x for x in r.json()}["dg951"]
    assert linha["certificacao"]["id"] == str(b.id)
    assert linha["garantia_meses"] == 24
    assert await _meses(db, p, q) == [24, 24]
    assert await _vigencias(db, p) == [(24, 12, editor.id)]
    assert await _vigencias(db, q) == [], "o Q não mudou de certificado nem de prazo"
    await db.refresh(venda)
    assert (venda.prazo_meses, venda.fim_garantia) == (24, date(2028, 10, 5))

    # 2) Fim do B encurtado na Certificações: o P volta para o A (12).
    auth_as(admin)
    r = await client.patch(f"/api/financeiro/suprimentos/{b.id}", json={"fim": "2026-12-31"})
    assert r.status_code == 200, r.text
    assert await _meses(db, p, q) == [12, 24]
    assert await _vigencias(db, p) == [(24, 12, editor.id), (12, 24, admin.id)]

    # 3) Modelo mudado na Certificações: o R passa a achar o C pelo nome.
    cod_r = _codigo()
    c = await certificados(_codigo(), garantia_meses=6)
    rr = await _produto(db, admin, "dg953", f"uranyx R - {cod_r}", meses=3)
    r = await client.patch(f"/api/financeiro/suprimentos/{c.id}", json={"modelo": cod_r})
    assert r.status_code == 200, r.text
    assert await _meses(db, rr) == [6]
    assert await _vigencias(db, rr) == [(6, 3, admin.id)]

    # 4) Certificado criado que tira o produto de um com garantia e não tem
    # garantia: o produto fica com o prazo que tem.
    cod_s = _codigo()
    await certificados(cod_s, numero="ND", fim=date(2027, 1, 1), garantia_meses=9)
    s_ = await _produto(db, admin, "dg954", f"uranyx S - {cod_s}", meses=9)
    r = await client.post(
        "/api/financeiro/suprimentos",
        json={
            "produto": "smartphone",
            "modelo": cod_s,
            "certificado": "anatel",
            "numero": "NE",
            "fim": "2029-01-01",
        },
    )
    assert r.status_code == 201, r.text
    e = await db.get(FinanceiroSuprimentos, uuid.UUID(r.json()["id"]))
    try:
        r = await client.get("/api/pricing/products")
        assert {x["sku"]: x for x in r.json()}["dg954"]["certificacao"]["id"] == str(e.id)
        assert await _meses(db, s_) == [9]
        assert await _vigencias(db, s_) == []

        # 5) Certificado excluído: quem o tinha gravado volta a ser achado pelo
        # nome, cai no A, e pega a garantia do A.
        t = await _produto(db, admin, "dg955", f"uranyx T - {cod}", cert=e, meses=2)
        r = await client.delete(f"/api/financeiro/suprimentos/{e.id}")
        assert r.status_code == 204, r.text
        e = None
        r = await client.get("/api/pricing/products")
        linhas = {x["sku"]: x for x in r.json()}
        assert linhas["dg955"]["certificacao"]["id"] == str(a.id)
        assert linhas["dg955"]["certificacao_pelo_nome"] is True
        assert await _meses(db, t) == [12]
        assert await _vigencias(db, t) == [(12, 2, admin.id)]
        # O S, que achava o excluído pelo nome, volta para o D (9): sem mudança.
        assert await _meses(db, s_) == [9]
        assert await _vigencias(db, s_) == []
        # Quem não trocou de certificado não ganhou vigência nova.
        assert await _vigencias(db, rr) == [(6, 3, admin.id)]
        assert len(await _vigencias(db, p)) == 2
    finally:
        if e is not None:
            await db.delete(e)
            await db.commit()


async def test_sincronizacao_que_troca_o_certificado_passa_a_garantia(
    db, make_user, certificados, monkeypatch
):
    """A sincronização da Anatel atualiza o Modelo e o Fim de um certificado
    com garantia e ele passa a vencer o do produto achado pelo nome: o produto
    pega a garantia, em nome do robô."""
    from unittest.mock import AsyncMock

    from app.models.financeiro import CertificacoesSyncHistorico, CertificacoesSyncState
    from app.services import certificacoes_sync as sync
    from app.services.garantia_prazo import ROTULO_CERTIFICADO
    from tests.test_certificacoes_sync import _payload, _record

    admin = await make_user(role=UserRole.ADMIN)
    cod = _codigo()
    await certificados(cod, numero="N-ANTIGO", fim=date(2027, 1, 1))
    # O cadastro manual do certificado que a Anatel vai confirmar (mesmo Nº).
    oficial = await certificados(
        "PRIMEIRO", numero="07197-26-18234", fim=date(2026, 12, 31), garantia_meses=24
    )
    p = await _produto(db, admin, "dg961", f"uranyx P - {cod}", meses=12)
    r = await db.execute(select(PricingProduct.certificacao_id).where(PricingProduct.id == p.id))
    assert r.scalar_one() is None, "achado pelo nome (o certificado antigo)"
    monkeypatch.setattr(
        sync,
        "fetch_certificacoes",
        AsyncMock(return_value=_payload(_record(modelos=[cod], fim="2028-07-30"))),
    )
    try:
        resultado = await sync.sincronizar_certificacoes(db, force=True)
        assert resultado["status"] == "ok", resultado
        await db.refresh(oficial)
        assert (oficial.modelo, oficial.fim) == (cod, date(2028, 7, 30))
        assert await _meses(db, p) == [24]
        vig = (
            await db.execute(
                select(
                    PricingGarantiaPrazo.meses,
                    PricingGarantiaPrazo.meses_anterior,
                    PricingGarantiaPrazo.user_id,
                    PricingGarantiaPrazo.user_nome,
                ).where(PricingGarantiaPrazo.pricing_product_id == p.id)
            )
        ).all()
        assert [tuple(v) for v in vig] == [(24, 12, None, ROTULO_CERTIFICADO)]
        # Sincronizar de novo, sem troca: nada novo.
        resultado = await sync.sincronizar_certificacoes(db, force=True)
        assert resultado["status"] == "ok"
        assert len(await _vigencias(db, p)) == 1
    finally:
        await db.rollback()
        await db.execute(delete(CertificacoesSyncHistorico))
        await db.execute(delete(CertificacoesSyncState))
        await db.commit()


async def test_falha_na_garantia_nao_derruba_a_sincronizacao(
    db, make_user, certificados, monkeypatch
):
    from unittest.mock import AsyncMock

    from app.models.financeiro import CertificacoesSyncHistorico, CertificacoesSyncState
    from app.services import certificacao_produto
    from app.services import certificacoes_sync as sync
    from tests.test_certificacoes_sync import _payload, _record

    cod = _codigo()
    oficial = await certificados("PRIMEIRO", numero="07197-26-18234", garantia_meses=24)
    monkeypatch.setattr(
        sync,
        "fetch_certificacoes",
        AsyncMock(return_value=_payload(_record(modelos=[cod], fim="2028-07-30"))),
    )

    async def quebra(*_a, **_k):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(certificacao_produto, "garantia_de_quem_trocou_de_certificado", quebra)
    try:
        resultado = await sync.sincronizar_certificacoes(db, force=True)
        assert resultado["status"] == "ok", resultado
        await db.refresh(oficial)
        assert (oficial.modelo, oficial.fim) == (cod, date(2028, 7, 30))
    finally:
        await db.rollback()
        await db.execute(delete(CertificacoesSyncHistorico))
        await db.execute(delete(CertificacoesSyncState))
        await db.commit()
