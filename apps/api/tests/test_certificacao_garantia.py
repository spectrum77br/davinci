"""Garantia e validade pelo certificado (06/11/2026).

Eduardo: "tem que deixar opção para editar a validade, e em certificações
adicione o campo garantia, e aí ele passa a mudar de acordo com o certificado";
"se eu mudar em certificações muda a coluna validade lá na tabela de preços, e
deixe a opção de editar na tabela de preços".

* Migration 0461: `financeiro_suprimentos.garantia_meses` (0..120, NULL).
* Certificações: mudar a garantia do certificado muda a Garantia (meses) de
  todos os produtos dele (desde a M-69: pela coluna SKU da Certificações ou,
  sem ela, pelo gravado na linha), com vigência e recálculo das garantias (o
  mesmo caminho do PATCH do produto).
* Tabela de Preços: a Garantia de produto cujo certificado tem garantia grava
  no certificado; produto sem certificado fica com a dele; trocar o
  certificado (desde a M-69, pela coluna SKU) pega a garantia do novo; a
  Validade grava o Fim do certificado.

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
    FinanceiroSuprimentoSku,
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

    async def _novo(modelo: str, skus: list[str] | None = None, **campos) -> FinanceiroSuprimentos:
        campos.setdefault("produto", "smartphone 4G")
        campos.setdefault("certificado", "anatel")
        campos.setdefault("numero", f"N-{modelo}")
        campos.setdefault("fim", date(2028, 7, 30))
        row = FinanceiroSuprimentos(modelo=modelo, **campos)
        db.add(row)
        await db.flush()
        # Coluna SKU (M-69): é ela que liga o certificado aos produtos.
        for sku in skus or []:
            db.add(FinanceiroSuprimentoSku(sku=sku, suprimento_id=row.id))
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
# Desde a M-69 (26/11/2026) o produto é do certificado pela coluna SKU da
# Certificações (sem ela, pelo gravado na linha). O código no nome não liga
# mais ninguém.


async def test_garantia_do_certificado_vai_para_todos_os_produtos_dele(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cod, outro_cod = _codigo(), _codigo()
    cert = await certificados(cod, skus=["dg901"])
    outro = await certificados(outro_cod)
    pelo_sku = await _produto(db, admin, "dg901", "uranyx A17", meses=6)
    gravado = await _produto(db, admin, "dg902", "uranyx C17", cert=cert)
    # O código no nome não liga mais: este é do outro certificado (gravado).
    do_outro = await _produto(db, admin, "dg903", f"uranyx X - {cod}", cert=outro, meses=3)
    solto = await _produto(db, admin, "dg904", f"uranyx sem certificado - {cod}")
    venda = await _venda(db, "610001", "dg902", date(2026, 10, 5))

    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"garantia_meses": 12})
    assert r.status_code == 200, r.text
    assert r.json()["garantia_meses"] == 12
    assert "garantia_meses" in r.json()["campos_manuais"]
    assert await _meses(db, pelo_sku, gravado, do_outro, solto) == [12, 12, 3, None]
    # A vigência de cada produto que mudou, em nome de quem mudou.
    assert await _vigencias(db, pelo_sku) == [(12, 6, admin.id)]
    assert await _vigencias(db, gravado) == [(12, None, admin.id)]
    assert await _vigencias(db, do_outro) == []
    # E a garantia da venda foi recalculada.
    await db.refresh(venda)
    assert (venda.prazo_meses, venda.fim_garantia) == (12, date(2027, 10, 5))

    # A Tabela de Preços mostra a garantia do certificado.
    r = await client.get("/api/pricing/products")
    linhas = {p["sku"]: p for p in r.json()}
    assert linhas["dg901"]["certificacao"]["garantia_meses"] == 12
    assert linhas["dg901"]["certificacao_conflito"] == []
    assert linhas["dg902"]["certificacao"]["garantia_meses"] == 12
    assert linhas["dg903"]["certificacao"]["garantia_meses"] is None
    assert linhas["dg904"]["certificacao"] is None
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
    assert await _meses(db, pelo_sku, gravado) == [12, 12]
    # Fora de 0..120: recusa, nada muda.
    for ruim in (-1, 121):
        r = await client.patch(
            f"/api/financeiro/suprimentos/{cert.id}", json={"garantia_meses": ruim}
        )
        assert r.status_code == 422
    # 0 = sem garantia, também vai para os produtos.
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"garantia_meses": 0})
    assert r.status_code == 200
    assert await _meses(db, pelo_sku, gravado, do_outro) == [0, 0, 3]


async def test_certificado_novo_com_garantia_ja_vale_para_os_produtos(
    client, db, make_user, auth_as, certificados
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cod = _codigo()
    p = await _produto(db, admin, "dg911", "uranyx novo")
    # Só o nome com o código não basta mais (M-69): sem SKU, ninguém entra.
    q = await _produto(db, admin, "dg912", f"uranyx novo - {cod}")
    r = await client.post(
        "/api/financeiro/suprimentos",
        json={
            "produto": "smartphone",
            "modelo": cod,
            "certificado": "anatel",
            "garantia_meses": 24,
            "skus": "dg911",
        },
    )
    assert r.status_code == 201, r.text
    novo = await db.get(FinanceiroSuprimentos, uuid.UUID(r.json()["id"]))
    try:
        assert await _meses(db, p, q) == [24, None]
        assert await _vigencias(db, p) == [(24, None, admin.id)]
    finally:
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
    cert = await certificados(cod, garantia_meses=12, skus=["dg922"])
    gravado = await _produto(db, admin, "dg921", "uranyx A", cert=cert, meses=12)
    pelo_sku = await _produto(db, admin, "dg922", "uranyx B", meses=12)
    sem_cert = await _produto(db, admin, "dg923", "uranyx sem certificado")

    auth_as(editor)
    r = await client.patch(f"/api/pricing/products/{gravado.id}", json={"garantia_meses": 24})
    assert r.status_code == 200, r.text
    assert r.json()["garantia_meses"] == 24
    assert r.json()["certificacao"]["garantia_meses"] == 24
    await db.refresh(cert)
    assert cert.garantia_meses == 24
    assert "garantia_meses" in (cert.campos_manuais or [])
    assert await _meses(db, gravado, pelo_sku, sem_cert) == [24, 24, None]
    assert await _vigencias(db, gravado) == [(24, 12, editor.id)]
    assert await _vigencias(db, pelo_sku) == [(24, 12, editor.id)]

    # Pelo produto ligado pela coluna SKU também vai para o certificado.
    r = await client.patch(f"/api/pricing/products/{pelo_sku.id}", json={"garantia_meses": 18})
    assert r.status_code == 200, r.text
    await db.refresh(cert)
    assert cert.garantia_meses == 18
    assert await _meses(db, gravado, pelo_sku) == [18, 18]

    # Produto sem certificado continua com o prazo dele.
    r = await client.patch(f"/api/pricing/products/{sem_cert.id}", json={"garantia_meses": 6})
    assert r.status_code == 200
    assert r.json()["garantia_meses"] == 6 and r.json()["certificacao"] is None
    await db.refresh(cert)
    assert cert.garantia_meses == 18
    assert await _meses(db, gravado, pelo_sku, sem_cert) == [18, 18, 6]

    # Apagar na Tabela o prazo que vem do certificado (revisão de 06/11/2026):
    # 422 — apagaria do certificado e de todos os produtos dele, e a Tabela
    # não conseguiria religar. Nada muda.
    r = await client.patch(f"/api/pricing/products/{gravado.id}", json={"garantia_meses": None})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "garantia_do_certificado"
    await db.refresh(cert)
    assert cert.garantia_meses == 18
    assert await _meses(db, gravado, pelo_sku) == [18, 18]
    assert len(await _vigencias(db, gravado)) == 2
    # 0 (sem garantia) pela Tabela vale: vai para o certificado e para todos.
    r = await client.patch(f"/api/pricing/products/{gravado.id}", json={"garantia_meses": 0})
    assert r.status_code == 200, r.text
    await db.refresh(cert)
    assert cert.garantia_meses == 0
    assert await _meses(db, gravado, pelo_sku) == [0, 0]
    # E a Tabela religa: o certificado continua definindo o prazo.
    r = await client.patch(f"/api/pricing/products/{pelo_sku.id}", json={"garantia_meses": 12})
    assert r.status_code == 200
    await db.refresh(cert)
    assert cert.garantia_meses == 12
    assert await _meses(db, gravado, pelo_sku) == [12, 12]

    # Certificado SEM garantia (apagada na Certificações): o produto edita o
    # prazo dele, o certificado fica vazio, e apagar volta a valer só na linha.
    auth_as(admin)
    r = await client.patch(f"/api/financeiro/suprimentos/{cert.id}", json={"garantia_meses": None})
    assert r.status_code == 200
    auth_as(editor)
    r = await client.patch(f"/api/pricing/products/{pelo_sku.id}", json={"garantia_meses": 3})
    assert r.status_code == 200
    await db.refresh(cert)
    assert cert.garantia_meses is None
    assert await _meses(db, gravado, pelo_sku) == [12, 3]
    r = await client.patch(f"/api/pricing/products/{pelo_sku.id}", json={"garantia_meses": None})
    assert r.status_code == 200
    assert await _meses(db, gravado, pelo_sku) == [12, None]


async def test_trocar_o_certificado_pega_a_garantia_do_novo(
    client, db, make_user, auth_as, certificados
):
    """Desde a M-69 o produto troca de certificado pela coluna SKU da
    Certificações (ou pelo SKU da linha); a coluna Modelo da Tabela é só
    leitura."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cod_a, cod_b = _codigo(), _codigo()
    com = await certificados(cod_a, garantia_meses=18)
    sem = await certificados(cod_b, skus=["dg931"])
    p = await _produto(db, admin, "dg931", "uranyx T", meses=6)
    url = "/api/financeiro/suprimentos"

    # O SKU sai do "sem" e entra no "com": o produto pega a garantia do novo.
    r = await client.patch(f"{url}/{sem.id}", json={"skus": []})
    assert r.status_code == 200, r.text
    r = await client.patch(f"{url}/{com.id}", json={"skus": ["dg931", "dg932", "dg933"]})
    assert r.status_code == 200, r.text
    assert await _meses(db, p) == [18]
    assert await _vigencias(db, p) == [(18, 6, admin.id)]
    # Voltar para um certificado sem garantia: fica com o que tem.
    r = await client.patch(f"{url}/{com.id}", json={"skus": ["dg932", "dg933"]})
    r = await client.patch(f"{url}/{sem.id}", json={"skus": ["dg931"]})
    assert await _meses(db, p) == [18]
    # Mesmo certificado de novo: nada muda.
    r = await client.patch(f"{url}/{sem.id}", json={"skus": ["dg931.sp"]})
    assert r.json()["skus"] == ["dg931"]
    assert len(await _vigencias(db, p)) == 1

    # O SKU da linha que passa a cair num certificado também vale.
    q = await _produto(db, admin, "dg939", "uranyx Q", meses=3)
    r = await client.patch(f"/api/pricing/products/{q.id}", json={"sku": "dg932"})
    assert r.status_code == 200
    assert r.json()["certificacao"]["id"] == str(com.id)
    assert r.json()["garantia_meses"] == 18

    # Produto novo cujo SKU está num certificado nasce com a garantia dele.
    seg = await _segmento(db)
    r = await client.post(
        "/api/pricing/products",
        json={"sku": "dg933", "name": "uranyx N", "segment_id": str(seg.id)},
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
    cert = await certificados(cod, anatel_numero=f"A-{cod}", fim=date(2028, 7, 30), skus=["dg942"])
    gravado = (await _produto(db, admin, "dg941", "uranyx V", cert=cert)).id
    pelo_sku = (await _produto(db, admin, "dg942", "uranyx W")).id
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

    # Pelo produto ligado pela coluna SKU também; apagar a data também vale.
    r = await client.patch(f"/api/pricing/products/{pelo_sku}", json={"certificado_fim": None})
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
    """Desde a M-69 só a coluna SKU, um certificado excluído (ou o SKU da
    própria linha) mudam o certificado de um produto. A Validade, o Modelo e o
    Nº não mudam mais ninguém de lugar (o desempate pelo nome saiu)."""
    admin = await make_user(role=UserRole.ADMIN)
    editor = await make_user(permissions=EDITOR_TABELA)
    cod_a, cod_b = _codigo(), _codigo()
    a = await certificados(
        cod_a, numero="NA", fim=date(2028, 1, 1), garantia_meses=12, skus=["dg951"]
    )
    b = await certificados(cod_b, numero="NB", fim=date(2027, 1, 1), garantia_meses=24)
    p = await _produto(db, admin, "dg951", f"uranyx P - {cod_b}", meses=12)
    q = await _produto(db, admin, "dg952", "uranyx Q", cert=b, meses=24)

    # 1) Validade do Q (certificado B) pela Tabela: antes o P (achado pelo
    # nome) ia para o B; agora fica no A, com o prazo dele.
    auth_as(editor)
    r = await client.patch(f"/api/pricing/products/{q.id}", json={"certificado_fim": "2030-01-01"})
    assert r.status_code == 200, r.text
    r = await client.get("/api/pricing/products")
    assert {x["sku"]: x for x in r.json()}["dg951"]["certificacao"]["id"] == str(a.id)
    assert await _meses(db, p, q) == [12, 24]
    assert await _vigencias(db, p) == []

    # 2) Modelo do B mudado para o do A na Certificações: ninguém muda.
    auth_as(admin)
    r = await client.patch(f"/api/financeiro/suprimentos/{b.id}", json={"modelo": cod_a})
    assert r.status_code == 200, r.text
    assert await _meses(db, p, q) == [12, 24]
    assert await _vigencias(db, p) == []

    # 3) Certificado novo com o SKU de um produto que tinha outro gravado: a
    # coluna SKU vence; sem garantia no novo, o produto fica com o prazo que tem.
    t = await _produto(db, admin, "dg955", "uranyx T", cert=a, meses=2)
    venda = await _venda(db, "610955", "dg955", date(2026, 10, 5))
    r = await client.post(
        "/api/financeiro/suprimentos",
        json={"produto": "smartphone", "modelo": _codigo(), "numero": "NE", "skus": ["dg955"]},
    )
    assert r.status_code == 201, r.text
    e = await db.get(FinanceiroSuprimentos, uuid.UUID(r.json()["id"]))
    try:
        r = await client.get("/api/pricing/products")
        assert {x["sku"]: x for x in r.json()}["dg955"]["certificacao"]["id"] == str(e.id)
        assert await _meses(db, t) == [2]
        assert await _vigencias(db, t) == []

        # 4) Certificado excluído: a coluna SKU dele sai junto, o produto volta
        # para o gravado (A) e pega a garantia do A; a venda é recalculada.
        r = await client.delete(f"/api/financeiro/suprimentos/{e.id}")
        assert r.status_code == 204, r.text
        e = None
        r = await client.get("/api/pricing/products")
        linhas = {x["sku"]: x for x in r.json()}
        assert linhas["dg955"]["certificacao"]["id"] == str(a.id)
        assert await _meses(db, t) == [12]
        assert await _vigencias(db, t) == [(12, 2, admin.id)]
        await db.refresh(venda)
        assert (venda.prazo_meses, venda.fim_garantia) == (12, date(2027, 10, 5))
        # Quem não trocou de certificado não ganhou vigência nova.
        assert await _vigencias(db, p) == []
        assert await _vigencias(db, q) == []
    finally:
        if e is not None:
            await db.delete(e)
            await db.commit()


async def test_sincronizacao_nao_troca_mais_o_certificado_de_ninguem(
    db, make_user, certificados, monkeypatch
):
    """Até a M-69 a sincronização da Anatel podia mudar o Modelo e o Fim de um
    certificado e tirar do outro um produto achado pelo nome. Desde a M-69 a
    ligação é pela coluna SKU (a sincronização não mexe nela): o produto fica
    onde está, com o prazo que tem."""
    from unittest.mock import AsyncMock

    from app.models.financeiro import CertificacoesSyncHistorico, CertificacoesSyncState
    from app.services import certificacoes_sync as sync
    from tests.test_certificacoes_sync import _payload, _record

    admin = await make_user(role=UserRole.ADMIN)
    cod = _codigo()
    antigo = await certificados(cod, numero="N-ANTIGO", fim=date(2027, 1, 1), skus=["dg961"])
    # O cadastro manual do certificado que a Anatel vai confirmar (mesmo Nº).
    oficial = await certificados(
        "PRIMEIRO",
        numero="07197-26-18234",
        fim=date(2026, 12, 31),
        garantia_meses=24,
        skus=["dg962"],
    )
    p = await _produto(db, admin, "dg961", f"uranyx P - {cod}", meses=12)
    q = await _produto(db, admin, "dg962", "uranyx Q", meses=24)
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
        # O P continua no certificado antigo (coluna SKU), com o prazo dele.
        assert await _meses(db, p, q) == [12, 24]
        assert await _vigencias(db, p) == []
        assert await _vigencias(db, q) == []
        await db.refresh(antigo)
        assert antigo.modelo == cod
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
