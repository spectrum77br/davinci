"""Conferência de preços da Tabela de Preços (02/11/2026) — núcleo, rotas,
migration 0442 e o registro do job.

Os marketplaces são SIMULADOS: leitores falsos no lugar de `LEITORES` e um
cliente falso no lugar de `cliente_da_integracao` (nenhuma chamada de verdade).
"""

# ruff: noqa: S608
from __future__ import annotations

import asyncio
import importlib.util
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from arq.constants import in_progress_key_prefix, result_key_prefix
from httpx import AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker_pool
from app.config import get_settings
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Base,
    CellStatus,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    PricingAccount,
    PricingConferencia,
    PricingOverride,
    PricingPlatform,
    PricingProduct,
    Product,
    ProductLink,
    Segment,
    User,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services.pricing import conferencia
from app.services.pricing.anuncios import CANAL_CATALOGO, CANAL_KIT, kit_pula_catalogo
from app.services.pricing.conferencia_leitores import LeituraAnuncio
from app.services.pricing.push import bases_com_catalogo_ligado, resolver_para_envio
from tests.conftest import TEST_SCHEMA

PERM_VER = {"tabela_precos": {"view": True, "edit": False, "delete": False}}


@pytest_asyncio.fixture(autouse=True)
async def _tipo_do_job_no_schema_de_teste(db: AsyncSession):
    """O conftest cria `background_job_type` com a lista fixa de antes; a 0442
    acrescenta 'conferir_precos' — aqui também."""
    await db.execute(
        text(
            f'ALTER TYPE "{TEST_SCHEMA}".background_job_type '
            "ADD VALUE IF NOT EXISTS 'conferir_precos'"
        )
    )
    await db.commit()


# =========================================================== cenário


async def _segmentos(db: AsyncSession) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ordem, slug in enumerate(("celular", "mala")):
        raiz = Segment(name=slug.title(), slug=slug, sort_order=ordem)
        db.add(raiz)
        await db.flush()
        filhos = []
        for i in range(5):
            f = Segment(
                name=f"{slug}-{i + 1}", slug=f"{slug}-{i + 1}", sort_order=i, parent_id=raiz.id
            )
            db.add(f)
            filhos.append(f)
        await db.flush()
        out[slug] = raiz
        out[f"{slug}_filhos"] = filhos
    return out


async def _integracao(
    db: AsyncSession, dono: User, nome: str, plataforma: IntegrationPlatform, **campos
) -> Integration:
    integ = Integration(
        user_id=dono.id,
        platform=plataforma,
        name=nome,
        credentials=encrypt_json({"access_token": "tok-teste"}),
        **campos,
    )
    db.add(integ)
    await db.flush()
    return integ


def _link(dono: User, integ: Integration, produto: Product, ext: str, **campos) -> ProductLink:
    return ProductLink(
        user_id=dono.id,
        product_id=produto.id,
        integration_id=integ.id,
        platform=integ.platform,
        external_id=ext,
        last_sync_status=LinkSyncStatus.OK,
        **campos,
    )


def _conta(dono: User, seg: Segment, nome: str, plataforma: PricingPlatform, **campos):
    padrao = {
        "commission": Decimal("0.10"),
        "margin1": Decimal("0.20"),
        "shipping1": Decimal("5.00"),
    }
    return PricingAccount(
        user_id=dono.id,
        name=nome,
        platform=plataforma,
        segment_id=seg.id,
        **{**padrao, **campos},
    )


def _linha(dono: User, folha: Segment, sku: str, **campos) -> PricingProduct:
    return PricingProduct(
        user_id=dono.id, sku=sku, name=f"Linha {sku}", segment_id=folha.id, **campos
    )


@pytest_asyncio.fixture
async def admin(make_user) -> User:
    return await make_user(role=UserRole.ADMIN)


@pytest_asyncio.fixture
async def cenario(db: AsyncSession, admin: User) -> SimpleNamespace:
    """Celular:
      - "counhago" (ML clássico, catálogo LIGADO): a003.sa (MLB100), o kit
        a003.sa+a001 (MLB101), o catálogo do a003 (MLB200) e x010.sp (MLB300);
      - "amazon nexus" (Amazon): a003.sa (B0AMZ1, SKU do anúncio diferente);
      - "shopee luno" (Shopee): a003.sa;
      - "ml quebrado" (ML): a003.sa (MLB900);
      - "sem integração" (ML, sem integração) e "arquivada" (integração
        arquivada — some do grid e da conferência).
    Linhas: a003 (custo 40, catálogo 55), x010 (100), z999 (sem anúncio),
    b200 (NA na counhago), c300 (desligada na counhago), d400 (sem custo).
    """
    seg = await _segmentos(db)
    acess = seg["celular_filhos"][0]
    ml = await _integracao(db, admin, "counhago", IntegrationPlatform.ML)
    amz = await _integracao(db, admin, "amazon nexus", IntegrationPlatform.AMAZON)
    sh = await _integracao(db, admin, "shopee luno", IntegrationPlatform.SHOPEE)
    ml2 = await _integracao(db, admin, "ml quebrado", IntegrationPlatform.ML)
    velha = await _integracao(
        db, admin, "ml arquivada", IntegrationPlatform.ML, archived_at=datetime.now(UTC)
    )

    p_a003 = Product(user_id=admin.id, sku="a003.sa", name="Fone")
    p_kit = Product(user_id=admin.id, sku="a003.sa+a001", name="Fone kit")
    p_x010 = Product(user_id=admin.id, sku="x010.sp", name="Capa")
    db.add_all([p_a003, p_kit, p_x010])
    await db.flush()
    db.add_all(
        [
            _link(admin, ml, p_a003, "MLB100", listing_type="gold_special", catalog_listing=False),
            _link(admin, ml, p_kit, "MLB101", listing_type="gold_special", catalog_listing=False),
            _link(
                admin,
                ml,
                p_a003,
                "MLB200",
                listing_type="gold_special",
                catalog_listing=True,
                anuncio_status="active",
            ),
            _link(admin, ml, p_x010, "MLB300", listing_type="gold_special", catalog_listing=False),
            _link(admin, amz, p_a003, "B0AMZ1", external_sku="A003-AMAZON"),
            _link(admin, sh, p_a003, "555", variation_id="1"),
            _link(admin, ml2, p_a003, "MLB900", listing_type="gold_special"),
            _link(admin, velha, p_a003, "MLB999", listing_type="gold_special"),
        ]
    )

    cel = seg["celular"]
    kit = _conta(
        admin,
        cel,
        "counhago",
        PricingPlatform.ML,
        listing_type="ml classico",
        integration_id=ml.id,
        sort_order=1,
    )
    conta_amz = _conta(
        admin,
        cel,
        "amazon nexus",
        PricingPlatform.AMAZON,
        commission=Decimal("0.15"),
        shipping1=None,
        integration_id=amz.id,
    )
    conta_sh = _conta(admin, cel, "shopee luno", PricingPlatform.SHOPEE, integration_id=sh.id)
    quebrada = _conta(
        admin,
        cel,
        "ml quebrado",
        PricingPlatform.ML,
        listing_type="ml classico",
        integration_id=ml2.id,
    )
    sem_integ = _conta(admin, cel, "sem integração", PricingPlatform.ML)
    arquivada = _conta(admin, cel, "arquivada", PricingPlatform.ML, integration_id=velha.id)
    db.add_all([kit, conta_amz, conta_sh, quebrada, sem_integ, arquivada])
    await db.flush()
    filha = PricingAccount(
        user_id=admin.id,
        name="counhago",
        platform=PricingPlatform.ML,
        listing_type="ml classico",
        segment_id=cel.id,
        canal=CANAL_CATALOGO,
        conta_base_id=kit.id,
    )
    db.add(filha)

    a003 = _linha(admin, acess, "a003", cost_kit1=Decimal("40"), preco_catalogo=Decimal("55"))
    x010 = _linha(admin, acess, "x010", cost_kit1=Decimal("100"))
    z999 = _linha(admin, acess, "z999", cost_kit1=Decimal("10"))
    b200 = _linha(admin, acess, "b200", cost_kit1=Decimal("20"))
    c300 = _linha(admin, acess, "c300", cost_kit1=Decimal("30"))
    d400 = _linha(admin, acess, "d400", cost_kit1=Decimal("0"))
    mala = _linha(admin, seg["mala_filhos"][0], "m001", cost_kit1=Decimal("50"))
    db.add_all([a003, x010, z999, b200, c300, d400, mala])
    await db.flush()
    db.add_all(
        [
            PricingOverride(
                user_id=admin.id,
                pricing_product_id=b200.id,
                pricing_account_id=kit.id,
                cell_status=CellStatus.NA,
            ),
            PricingOverride(
                user_id=admin.id,
                pricing_product_id=c300.id,
                pricing_account_id=kit.id,
                cell_status=CellStatus.DISABLED,
            ),
        ]
    )
    await db.commit()
    return SimpleNamespace(
        seg=seg,
        ml=ml,
        amz=amz,
        sh=sh,
        ml2=ml2,
        kit=kit,
        filha=filha,
        conta_amz=conta_amz,
        conta_sh=conta_sh,
        quebrada=quebrada,
        sem_integ=sem_integ,
        arquivada=arquivada,
        a003=a003,
        x010=x010,
        z999=z999,
        b200=b200,
        c300=c300,
        d400=d400,
        mala=mala,
    )


# =========================================================== marketplaces falsos


class LeitorFalso:
    """Lê o preço de uma tabela {external_id: preço}; registra as chamadas."""

    def __init__(self, precos: dict[str, str], *, falha_em: set[str] | None = None) -> None:
        self.precos = precos
        self.falha_em = falha_em or set()
        self.chamadas: list[tuple[str, list[ProductLink]]] = []

    async def __call__(self, cliente, vinculos):
        self.chamadas.append((cliente.nome, list(vinculos)))
        if cliente.nome in self.falha_em:
            raise RuntimeError("o Mercado Livre recusou o acesso da conta (403)")
        saida = {}
        for lk in vinculos:
            preco = self.precos.get(lk.external_id)
            saida[lk.id] = LeituraAnuncio(
                preco=Decimal(preco) if preco is not None else None,
                ativo=True,
                motivo=None if preco is not None else "anúncio não encontrado",
            )
        return saida


@pytest.fixture
def marketplaces(monkeypatch) -> SimpleNamespace:
    """Produção ligada, ML e Amazon com leitor (Shopee sem) e o cliente falso."""
    monkeypatch.setattr(get_settings(), "env", "production")
    ml = LeitorFalso({"MLB101": "59", "MLB200": "85", "MLB300": "128", "MLB900": "59"})
    amz = LeitorFalso({"B0AMZ1": "60"})
    montados: list[str] = []

    async def _cliente(integ):
        montados.append(integ.name)
        return SimpleNamespace(nome=integ.name)

    falsos = SimpleNamespace(ml=ml, amz=amz, montados=montados, leitores={"ml": ml, "amazon": amz})
    monkeypatch.setattr(conferencia, "cliente_da_integracao", _cliente)
    monkeypatch.setattr(conferencia, "carregar_leitores", lambda: dict(falsos.leitores))
    return falsos


async def _novo_job(db: AsyncSession, dono: User, department: str = "celular", **campos):
    job = BackgroundJob(
        type=BackgroundJobType.CONFERIR_PRECOS,
        status=campos.pop("status", BackgroundJobStatus.PENDING),
        created_by=dono.id,
        payload={"department": department},
        **campos,
    )
    db.add(job)
    await db.commit()
    return job


async def _rodar(db: AsyncSession, dono: User, department: str = "celular") -> BackgroundJob:
    job = await _novo_job(db, dono, department)
    await conferencia.run_conferir_precos(job_id=job.id, department=department, user_id=dono.id)
    await db.refresh(job)
    return job


async def _gravadas(
    db: AsyncSession, department: str = "celular"
) -> dict[tuple, PricingConferencia]:
    linhas = (
        (
            await db.execute(
                select(PricingConferencia)
                .where(PricingConferencia.department == department)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {(c.pricing_account_id, c.pricing_product_id): c for c in linhas}


# =========================================================== núcleo


@pytest.mark.asyncio
async def test_conferencia_le_so_as_celulas_que_mandam_preco(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    c = cenario
    job = await _rodar(db, admin)
    assert job.status == BackgroundJobStatus.SUCCEEDED, job.error
    gravadas = await _gravadas(db)

    # Kit (celular ML, havendo kit só o kit) e catálogo (pela integração da base).
    kit = gravadas[(c.kit.id, c.a003.id)]
    assert (kit.situacao, kit.motivo, kit.preco_tabela) == ("conferida", None, Decimal("59.00"))
    assert kit.anuncios == [
        {
            "anuncio": "MLB101",
            "variacao": None,
            "preco": 59.0,
            "preco_promocional": None,
            "ativo": True,
            "motivo": None,
        }
    ]
    cat = gravadas[(c.filha.id, c.a003.id)]
    assert (cat.situacao, cat.preco_tabela) == ("conferida", Decimal("79.00"))
    assert [a["anuncio"] for a in cat.anuncios] == ["MLB200"]
    assert cat.anuncios[0]["preco"] == 85.0
    assert [a["anuncio"] for a in gravadas[(c.kit.id, c.x010.id)].anuncios] == ["MLB300"]
    # Amazon: (40 × 1.2) / 0.85 = 56.
    amz = gravadas[(c.conta_amz.id, c.a003.id)]
    assert (amz.situacao, amz.preco_tabela) == ("conferida", Decimal("56.00"))
    assert amz.anuncios[0]["anuncio"] == "B0AMZ1"
    # Sem leitor, sem integração, sem anúncio.
    sh = gravadas[(c.conta_sh.id, c.a003.id)]
    assert (sh.situacao, sh.anuncios) == ("nao_suportada", [])
    assert "shopee" in sh.motivo
    # Plataforma sem leitor e SEM anúncio: "sem anúncio", não "não conferido"
    # (02/11/2026: 68 células Magalu da prévia viravam "?" à toa).
    sh_z = gravadas[(c.conta_sh.id, c.z999.id)]
    assert (sh_z.situacao, sh_z.motivo) == ("sem_anuncio", "sem anúncio vinculado")
    assert gravadas[(c.sem_integ.id, c.a003.id)].situacao == "sem_anuncio"
    assert gravadas[(c.sem_integ.id, c.a003.id)].motivo == "conta sem integração"
    z = gravadas[(c.kit.id, c.z999.id)]
    assert (z.situacao, z.motivo, z.anuncios) == ("sem_anuncio", "sem anúncio vinculado", [])

    # Fora: NA, desligada, sem preço, conta arquivada; catálogo só onde há preço
    # de catálogo; a linha da mala é de outro departamento.
    assert (c.kit.id, c.b200.id) not in gravadas
    assert (c.kit.id, c.c300.id) not in gravadas
    assert (c.conta_amz.id, c.b200.id) in gravadas  # NA só na counhago
    assert not any(p == c.d400.id for _, p in gravadas)
    assert not any(a == c.arquivada.id for a, _ in gravadas)
    assert {p for a, p in gravadas if a == c.filha.id} == {c.a003.id}
    assert not any(p == c.mala.id for _, p in gravadas)

    # Uma leitura por integração (kit + catálogo juntos), vínculos sem repetir;
    # cliente só das contas com leitor (nem Shopee, nem a arquivada).
    assert sorted(marketplaces.montados) == ["amazon nexus", "counhago", "ml quebrado"]
    lidos_ml = {
        nome: sorted(lk.external_id for lk in lks) for nome, lks in marketplaces.ml.chamadas
    }
    assert lidos_ml == {"counhago": ["MLB101", "MLB200", "MLB300"], "ml quebrado": ["MLB900"]}
    # A Amazon recebe o SKU do PRODUTO (o do envio), não o do anúncio.
    ((_, vinculos_amz),) = marketplaces.amz.chamadas
    assert [getattr(lk, conferencia.ATRIBUTO_SKU_DO_PRODUTO) for lk in vinculos_amz] == ["a003.sa"]

    assert (job.total, job.processed) == (3, 3)
    assert job.started_at is not None and job.finished_at is not None
    assert job.result == {
        "celulas": len(gravadas),
        "anuncios": 5,
        "lidos": 5,
        "sem_leitura": 0,
        "contas_com_erro": [],
        "situacoes": {
            "conferida": sum(g.situacao == "conferida" for g in gravadas.values()),
            "sem_anuncio": sum(g.situacao == "sem_anuncio" for g in gravadas.values()),
            "erro_conta": 0,
            "nao_suportada": sum(g.situacao == "nao_suportada" for g in gravadas.values()),
        },
    }
    assert all(g.job_id == job.id for g in gravadas.values())


@pytest.mark.asyncio
async def test_erro_na_conta_marca_erro_conta_e_o_job_segue(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    marketplaces.ml.falha_em = {"ml quebrado"}
    marketplaces.amz.precos = {}  # o anúncio não voltou
    job = await _rodar(db, admin)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    gravadas = await _gravadas(db)
    quebrada = gravadas[(cenario.quebrada.id, cenario.a003.id)]
    assert quebrada.situacao == "erro_conta"
    assert quebrada.motivo == "o Mercado Livre recusou o acesso da conta (403)"
    assert quebrada.anuncios == [
        {
            "anuncio": "MLB900",
            "variacao": None,
            "preco": None,
            "preco_promocional": None,
            "ativo": None,
            "motivo": None,
        }
    ]
    # As outras contas seguem conferidas.
    assert gravadas[(cenario.kit.id, cenario.a003.id)].situacao == "conferida"
    amz = gravadas[(cenario.conta_amz.id, cenario.a003.id)]
    assert amz.situacao == "conferida"
    assert amz.anuncios[0]["preco"] is None
    assert amz.anuncios[0]["motivo"] == "anúncio não encontrado"
    assert job.result["contas_com_erro"] == [
        {
            "conta": "ml quebrado",
            "contas": [str(cenario.quebrada.id)],
            "motivo": "o Mercado Livre recusou o acesso da conta (403)",
        }
    ]
    assert (job.result["anuncios"], job.result["lidos"], job.result["sem_leitura"]) == (5, 3, 2)
    assert job.result["situacoes"]["erro_conta"] == 1
    assert job.processed == job.total == 3


@pytest.mark.asyncio
async def test_leitor_que_devolve_lixo_vira_erro_conta(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    async def _lixo(cliente, vinculos):
        return None

    marketplaces.leitores["amazon"] = _lixo
    job = await _rodar(db, admin)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    amz = (await _gravadas(db))[(cenario.conta_amz.id, cenario.a003.id)]
    assert (amz.situacao, amz.motivo) == (
        "erro_conta",
        "o leitor de amazon não devolveu as leituras",
    )


@pytest.mark.asyncio
async def test_sinal_de_vida_durante_a_leitura(
    db: AsyncSession, admin: User, cenario, marketplaces, monkeypatch
):
    """Conta lenta: o job carimba o sinal de vida enquanto lê (senão a trava e
    o background_jobs_gc o dariam como morto) e para de carimbar no fim."""
    import asyncio

    monkeypatch.setattr(conferencia, "BATIMENTO_S", 0.01)
    carimbos: list[dict] = []
    original = conferencia._atualizar_job

    async def _registra(job_id, **valores):
        carimbos.append(valores)
        await original(job_id, **valores)

    monkeypatch.setattr(conferencia, "_atualizar_job", _registra)

    async def _lento(cliente, vinculos):
        await asyncio.sleep(0.1)
        return await marketplaces.ml(cliente, vinculos)

    marketplaces.leitores["ml"] = _lento
    job = await _rodar(db, admin)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    so_sinal = [c for c in carimbos if set(c) == {"last_heartbeat_at"}]
    assert len(so_sinal) >= 2
    depois = len(carimbos)
    await asyncio.sleep(0.05)
    assert len(carimbos) == depois  # parou junto com o job


@pytest.mark.asyncio
async def test_trava_fora_da_producao_nao_chama_nada(
    db: AsyncSession, admin: User, cenario, marketplaces, monkeypatch
):
    monkeypatch.setattr(get_settings(), "env", "development")
    db.add(
        PricingConferencia(
            pricing_account_id=cenario.kit.id,
            pricing_product_id=cenario.a003.id,
            department="celular",
            situacao="conferida",
            preco_tabela=Decimal("1"),
        )
    )
    await db.commit()
    job = await _rodar(db, admin)
    assert (job.status, job.error) == (BackgroundJobStatus.FAILED, "so_no_servidor")
    assert job.finished_at is not None
    assert marketplaces.montados == []
    assert marketplaces.ml.chamadas == marketplaces.amz.chamadas == []
    # A conferência anterior fica como estava.
    assert list(await _gravadas(db)) == [(cenario.kit.id, cenario.a003.id)]


@pytest.mark.asyncio
async def test_fim_com_sucesso_tira_so_as_celulas_que_sairam_e_departamento_invalido(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    """A conferência anterior fica enquanto as contas são lidas; no fim, com
    sucesso, saem só as células do departamento que não estão nesta."""
    db.add_all(
        [
            # De uma conferência velha da celular (a célula nem manda mais preço).
            PricingConferencia(
                pricing_account_id=cenario.kit.id,
                pricing_product_id=cenario.b200.id,
                department="celular",
                situacao="conferida",
            ),
            PricingConferencia(
                pricing_account_id=cenario.kit.id,
                pricing_product_id=cenario.mala.id,
                department="mala",
                situacao="sem_anuncio",
            ),
        ]
    )
    await db.commit()
    no_meio: list[set] = []

    async def _olha_no_meio(cliente, vinculos):
        # Outra sessão (a da tela): as contas são lidas ao mesmo tempo.
        async with conferencia._db.SessionLocal() as s:
            linhas = await s.execute(
                select(
                    PricingConferencia.pricing_account_id, PricingConferencia.pricing_product_id
                ).where(PricingConferencia.department == "celular")
            )
            no_meio.append({tuple(ln) for ln in linhas.all()})
        return await marketplaces.ml(cliente, vinculos)

    marketplaces.leitores["ml"] = _olha_no_meio
    job = await _rodar(db, admin)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["contas_com_erro"] == []
    # Durante a leitura a célula velha ainda estava lá (o começo não apaga mais).
    assert len(no_meio) == 2
    assert all((cenario.kit.id, cenario.b200.id) in vistas for vistas in no_meio)
    gravadas = await _gravadas(db)
    assert (cenario.kit.id, cenario.b200.id) not in gravadas
    assert all(g.job_id == job.id for g in gravadas.values())
    assert list(await _gravadas(db, "mala")) == [(cenario.kit.id, cenario.mala.id)]

    job = await _rodar(db, admin, "nao-existe")
    assert (job.status, job.error) == (BackgroundJobStatus.FAILED, "invalid_department")


@pytest.mark.asyncio
async def test_conferencia_so_das_contas_escolhidas_nao_mexe_nas_outras(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    """M-14, 18/11/2026 (botão "Atualizar ▾"): com `contas` no payload, só as
    células dessas contas são lidas, gravadas e limpas; as das outras contas
    ficam com a conferência anterior."""
    c = cenario
    primeira = await _rodar(db, admin)
    assert primeira.status == BackgroundJobStatus.SUCCEEDED
    antes = await _gravadas(db)
    assert (c.kit.id, c.x010.id) in antes and (c.conta_amz.id, c.a003.id) in antes
    chamadas_ml = len(marketplaces.ml.chamadas)
    chamadas_amz = len(marketplaces.amz.chamadas)
    # As duas células deixam de mandar preço (NA): só a da conta escolhida sai.
    db.add_all(
        [
            PricingOverride(
                user_id=admin.id,
                pricing_product_id=c.x010.id,
                pricing_account_id=c.kit.id,
                cell_status=CellStatus.NA,
            ),
            PricingOverride(
                user_id=admin.id,
                pricing_product_id=c.a003.id,
                pricing_account_id=c.conta_amz.id,
                cell_status=CellStatus.NA,
            ),
        ]
    )
    await db.commit()

    job = BackgroundJob(
        type=BackgroundJobType.CONFERIR_PRECOS,
        status=BackgroundJobStatus.PENDING,
        created_by=admin.id,
        payload={"department": "celular", "contas": [str(c.kit.id)]},
    )
    db.add(job)
    await db.commit()
    await conferencia.run_conferir_precos(job_id=job.id, department="celular", user_id=admin.id)
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED, job.error

    # Só a conta escolhida foi lida (nem "ml quebrado", nem a Amazon).
    assert [nome for nome, _ in marketplaces.ml.chamadas[chamadas_ml:]] == ["counhago"]
    assert len(marketplaces.amz.chamadas) == chamadas_amz
    depois = await _gravadas(db)
    # A célula da conta escolhida que não manda mais preço saiu; a da Amazon,
    # que também não manda mais, ficou (não era desta conferência).
    assert (c.kit.id, c.x010.id) not in depois
    assert depois[(c.conta_amz.id, c.a003.id)].job_id == primeira.id
    # As da conta escolhida são deste job; as das outras contas, da primeira.
    for (conta_id, _linha_id), cel in depois.items():
        esperado = job.id if conta_id == c.kit.id else primeira.id
        assert cel.job_id == esperado, (conta_id, cel.situacao)
    assert set(depois) == set(antes) - {(c.kit.id, c.x010.id)}


async def _rodar_parcial(db: AsyncSession, dono: User, contas: list) -> BackgroundJob:
    job = BackgroundJob(
        type=BackgroundJobType.CONFERIR_PRECOS,
        status=BackgroundJobStatus.PENDING,
        created_by=dono.id,
        payload={"department": "celular", "contas": [str(i) for i in contas]},
    )
    db.add(job)
    await db.commit()
    await conferencia.run_conferir_precos(job_id=job.id, department="celular", user_id=dono.id)
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED, job.error
    return job


@pytest.mark.asyncio
async def test_parcial_leva_adiante_o_aviso_das_contas_que_nao_releu(
    db: AsyncSession,
    client: AsyncClient,
    admin: User,
    cenario,
    auth_as: Callable,
    marketplaces,
):
    """Revisão M-14: a parcial vira o último job do departamento (a data e o
    ícone laranja da linha Conferir). A conferência inteira deixou a "ml
    quebrado" sem leitura; uma parcial só da counhago não pode apagar esse
    aviso. Quando a parcial relê a "ml quebrado" e dá certo, o aviso sai."""
    c = cenario
    marketplaces.ml.falha_em = {"ml quebrado"}
    inteira = await _rodar(db, admin)
    (aviso,) = inteira.result["contas_com_erro"]
    assert (aviso["conta"], aviso["contas"]) == ("ml quebrado", [str(c.quebrada.id)])

    parcial = await _rodar_parcial(db, admin, [c.kit.id])
    assert parcial.result["contas_com_erro"] == [aviso]
    auth_as(admin)
    corpo = (await client.get("/api/pricing/conferencia", params={"department": "celular"})).json()
    assert corpo["job"]["id"] == str(parcial.id)
    assert corpo["job"]["parcial"] == 1
    assert corpo["job"]["result"]["contas_com_erro"] == [aviso]

    # A "ml quebrado" volta a ler: a parcial dela tira o aviso.
    marketplaces.ml.falha_em = set()
    assert (await _rodar_parcial(db, admin, [c.quebrada.id])).result["contas_com_erro"] == []
    # A inteira não é parcial.
    await _rodar(db, admin)
    corpo = (await client.get("/api/pricing/conferencia", params={"department": "celular"})).json()
    assert corpo["job"]["parcial"] is None


@pytest.mark.asyncio
async def test_limpeza_do_fim_nao_apaga_celula_conferida_sozinha_no_meio(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    """M-14 × M-15: a x010 estava NA quando a conferência (só da counhago)
    começou, então não é desta conferência. No meio, alguém tira o NA e clica
    no cartão: a célula é conferida sozinha. A limpeza do fim (que só mexe nas
    contas escolhidas) não apaga essa leitura, mais nova que a montagem do job;
    a leitura de célula conferida sozinha ANTES do job sai, como sempre."""
    c = cenario
    na = PricingOverride(
        user_id=admin.id,
        pricing_product_id=c.x010.id,
        pricing_account_id=c.kit.id,
        cell_status=CellStatus.NA,
    )
    db.add(na)
    await db.commit()
    clicou = asyncio.Event()
    ml = marketplaces.ml

    async def _ml(cliente, vinculos):
        if cliente.nome == "counhago" and not clicou.is_set():
            clicou.set()
            await db.delete(na)
            await db.commit()
            assert await conferencia.conferir_celula("celular", c.kit.id, c.x010.id)
        return await ml(cliente, vinculos)

    marketplaces.leitores["ml"] = _ml
    job = BackgroundJob(
        type=BackgroundJobType.CONFERIR_PRECOS,
        status=BackgroundJobStatus.PENDING,
        created_by=admin.id,
        payload={"department": "celular", "contas": [str(c.kit.id)]},
    )
    db.add(job)
    await db.commit()
    await conferencia.run_conferir_precos(job_id=job.id, department="celular", user_id=admin.id)
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED, job.error
    assert clicou.is_set()
    gravadas = await _gravadas(db)
    assert gravadas[(c.kit.id, c.x010.id)].job_id is None
    assert gravadas[(c.kit.id, c.a003.id)].job_id == job.id

    # Conferida sozinha antes, e a célula volta a NA: a próxima conferência
    # do departamento tira a leitura dela.
    db.add(
        PricingOverride(
            user_id=admin.id,
            pricing_product_id=c.x010.id,
            pricing_account_id=c.kit.id,
            cell_status=CellStatus.NA,
        )
    )
    await db.commit()
    marketplaces.leitores["ml"] = ml
    await _rodar(db, admin)
    assert (c.kit.id, c.x010.id) not in await _gravadas(db)


@pytest.mark.asyncio
async def test_falha_ao_gravar_uma_conta_mantem_a_leitura_anterior_dela(
    db: AsyncSession, admin: User, cenario, marketplaces, monkeypatch
):
    primeiro = await _rodar(db, admin)
    original = conferencia._gravar_vivas

    async def _banco_pisca_na_amazon(session, linhas, **kw):
        if any(
            ln["pricing_account_id"] == cenario.conta_amz.id and ln["situacao"] == "conferida"
            for ln in linhas
        ):
            raise RuntimeError("o banco piscou")
        return await original(session, linhas, **kw)

    monkeypatch.setattr(conferencia, "_gravar_vivas", _banco_pisca_na_amazon)
    segundo = await _rodar(db, admin)
    assert segundo.status == BackgroundJobStatus.SUCCEEDED
    assert segundo.result["contas_com_erro"] == [
        {
            "conta": "amazon nexus",
            "contas": [str(cenario.conta_amz.id)],
            "motivo": "falha ao gravar o resultado",
        }
    ]
    gravadas = await _gravadas(db)
    # A célula da Amazon fica com a leitura anterior (a limpeza do fim não a
    # tira: ela é desta conferência); as outras, com a nova.
    assert gravadas[(cenario.conta_amz.id, cenario.a003.id)].job_id == primeiro.id
    assert gravadas[(cenario.kit.id, cenario.a003.id)].job_id == segundo.id
    assert segundo.result["situacoes"]["conferida"] == primeiro.result["situacoes"]["conferida"] - 1
    assert segundo.result["celulas"] == primeiro.result["celulas"] - 1


async def _apagar(modelo, id_: uuid.UUID) -> None:
    """Exclusão de outra sessão (a tela), no meio da leitura."""
    async with conferencia._db.SessionLocal() as s:
        await s.execute(delete(modelo).where(modelo.id == id_))
        await s.commit()


@pytest.mark.asyncio
async def test_linha_excluida_no_meio_perde_so_as_celulas_dela(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    """A linha a003 é excluída enquanto a counhago é lida (as outras contas só
    gravam depois): as células dela somem, e o resto da integração grava."""
    apagou = asyncio.Event()

    async def _ml(cliente, vinculos):
        if cliente.nome == "counhago":
            await _apagar(PricingProduct, cenario.a003.id)
            apagou.set()
        else:
            await asyncio.wait_for(apagou.wait(), timeout=5)
        return await marketplaces.ml(cliente, vinculos)

    async def _amz(cliente, vinculos):
        await asyncio.wait_for(apagou.wait(), timeout=5)
        return await marketplaces.amz(cliente, vinculos)

    marketplaces.leitores.update(ml=_ml, amazon=_amz)
    job = await _rodar(db, admin)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["contas_com_erro"] == []
    gravadas = await _gravadas(db)
    assert not any(p == cenario.a003.id for _, p in gravadas)
    x010 = gravadas[(cenario.kit.id, cenario.x010.id)]
    assert (x010.situacao, x010.job_id) == ("conferida", job.id)
    # Contadas só as gravadas: das lidas, só a x010 (as da a003 ficaram de fora).
    assert job.result["situacoes"]["conferida"] == 1
    assert job.result["celulas"] == sum(job.result["situacoes"].values())


@pytest.mark.asyncio
async def test_coluna_de_catalogo_apagada_no_meio_nao_derruba_a_integracao(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    """Desligar o Catálogo apaga a coluna enquanto a counhago é lida: as
    células da conta de Kit, da mesma integração, gravam normalmente."""

    async def _ml(cliente, vinculos):
        if cliente.nome == "counhago":
            await _apagar(PricingAccount, cenario.filha.id)
        return await marketplaces.ml(cliente, vinculos)

    marketplaces.leitores["ml"] = _ml
    job = await _rodar(db, admin)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["contas_com_erro"] == []
    gravadas = await _gravadas(db)
    assert not any(a == cenario.filha.id for a, _ in gravadas)
    for linha in (cenario.a003, cenario.x010):
        cel = gravadas[(cenario.kit.id, linha.id)]
        assert (cel.situacao, cel.job_id) == ("conferida", job.id)
    # kit×a003, kit×x010, Amazon e "ml quebrado": a do catálogo não conta.
    assert job.result["situacoes"]["conferida"] == 4
    assert job.result["celulas"] == len(gravadas)


@pytest.mark.asyncio
async def test_uma_loja_shopee_por_vez(db: AsyncSession, admin: User, cenario, marketplaces):
    """A cota da Shopee é do partner-id: as lojas Shopee são lidas uma de cada
    vez, e as outras plataformas seguem nas vagas livres."""
    sol = await _integracao(db, admin, "shopee sol", IntegrationPlatform.SHOPEE)
    fone = (await db.execute(select(Product).where(Product.sku == "a003.sa"))).scalar_one()
    db.add(_link(admin, sol, fone, "777", variation_id="2"))
    conta_sol = _conta(
        admin, cenario.seg["celular"], "shopee sol", PricingPlatform.SHOPEE, integration_id=sol.id
    )
    db.add(conta_sol)
    await db.commit()

    ativos: dict[str, int] = {"shopee": 0, "todas": 0}
    picos: dict[str, int] = {"shopee": 0, "todas": 0}

    def _medido(leitor, *, shopee: bool = False):
        async def _ler(cliente, vinculos):
            chaves = ("todas", "shopee") if shopee else ("todas",)
            for k in chaves:
                ativos[k] += 1
                picos[k] = max(picos[k], ativos[k])
            await asyncio.sleep(0.05)
            for k in chaves:
                ativos[k] -= 1
            return await leitor(cliente, vinculos)

        return _ler

    marketplaces.leitores.update(
        ml=_medido(marketplaces.ml),
        amazon=_medido(marketplaces.amz),
        shopee=_medido(LeitorFalso({"555": "60", "777": "61"}), shopee=True),
    )
    job = await _rodar(db, admin)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert picos == {"shopee": 1, "todas": conferencia.CONTAS_AO_MESMO_TEMPO}
    gravadas = await _gravadas(db)
    for conta in (cenario.conta_sh, conta_sol):
        assert gravadas[(conta.id, cenario.a003.id)].situacao == "conferida"


async def _conferencia_anterior(db: AsyncSession, admin: User) -> BackgroundJob:
    job = await _rodar(db, admin)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    return job


@pytest.mark.asyncio
async def test_prazo_proprio_estourado_e_tempo_esgotado_e_mantem_as_nao_lidas(
    db: AsyncSession, admin: User, cenario, marketplaces, monkeypatch
):
    anterior = await _conferencia_anterior(db, admin)
    monkeypatch.setattr(conferencia, "PRAZO_S", 1.0)

    async def _counhago_trava(cliente, vinculos):
        if cliente.nome == "counhago":
            await asyncio.sleep(30)
        return await marketplaces.ml(cliente, vinculos)

    marketplaces.leitores["ml"] = _counhago_trava
    job = await _rodar(db, admin)  # termina sem levantar: o prazo é do job
    assert (job.status, job.error) == (BackgroundJobStatus.FAILED, "tempo_esgotado")
    assert job.finished_at is not None
    gravadas = await _gravadas(db)
    # A counhago não terminou: as células dela ficam com a leitura anterior.
    assert gravadas[(cenario.kit.id, cenario.a003.id)].job_id == anterior.id
    assert gravadas[(cenario.filha.id, cenario.a003.id)].job_id == anterior.id
    assert gravadas[(cenario.conta_amz.id, cenario.a003.id)].job_id == job.id


@pytest.mark.asyncio
async def test_cancelamento_de_fora_e_interrompida_e_mantem_as_nao_lidas(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    """Deploy no meio: o arq cancela o job no SIGTERM. Não foi o tempo — é
    'interrompida' —, e as contas que não terminaram ficam com a leitura
    anterior (o começo não apaga mais o departamento)."""
    anterior = await _conferencia_anterior(db, admin)
    travou = asyncio.Event()

    async def _counhago_trava(cliente, vinculos):
        if cliente.nome == "counhago":
            travou.set()
            await asyncio.Event().wait()
        return await marketplaces.ml(cliente, vinculos)

    marketplaces.leitores["ml"] = _counhago_trava
    job = await _novo_job(db, admin)
    tarefa = asyncio.create_task(
        conferencia.run_conferir_precos(job_id=job.id, department="celular", user_id=admin.id)
    )
    await asyncio.wait_for(travou.wait(), timeout=5)
    # As outras duas contas gravam antes do corte.
    for _ in range(500):
        await db.refresh(job)
        if job.processed == 2:
            break
        await asyncio.sleep(0.01)
    assert job.processed == 2
    tarefa.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarefa
    await db.refresh(job)
    assert (job.status, job.error) == (BackgroundJobStatus.FAILED, "interrompida")
    assert job.finished_at is not None
    gravadas = await _gravadas(db)
    assert gravadas[(cenario.kit.id, cenario.a003.id)].job_id == anterior.id
    assert gravadas[(cenario.conta_amz.id, cenario.a003.id)].job_id == job.id
    assert gravadas[(cenario.quebrada.id, cenario.a003.id)].job_id == job.id


@pytest.mark.asyncio
async def test_cancelamento_depois_do_fim_nao_muda_o_desfecho(
    db: AsyncSession, admin: User, cenario, marketplaces, monkeypatch
):
    """O SIGTERM que chega enquanto o "succeeded" é gravado não o troca por
    'interrompida'."""
    original = conferencia._encerrar

    async def _cancela_depois_de_gravar(job_id, status, **kw):
        await original(job_id, status, **kw)
        if status == BackgroundJobStatus.SUCCEEDED:
            raise asyncio.CancelledError

    monkeypatch.setattr(conferencia, "_encerrar", _cancela_depois_de_gravar)
    job = await _novo_job(db, admin)
    with pytest.raises(asyncio.CancelledError):
        await conferencia.run_conferir_precos(job_id=job.id, department="celular", user_id=admin.id)
    await db.refresh(job)
    assert (job.status, job.error) == (BackgroundJobStatus.SUCCEEDED, None)


@pytest.mark.asyncio
async def test_job_nao_roda_com_outro_do_mesmo_departamento_rodando(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    agora = datetime.now(UTC)
    vivo = await _novo_job(
        db, admin, status=BackgroundJobStatus.RUNNING, started_at=agora, last_heartbeat_at=agora
    )
    job = await _rodar(db, admin)
    assert (job.status, job.error) == (BackgroundJobStatus.FAILED, "conferencia_em_andamento")
    assert marketplaces.montados == []
    # O que parou de dar sinal não segura ninguém.
    vivo.last_heartbeat_at = agora - conferencia.SEM_SINAL - timedelta(minutes=1)
    db.add(vivo)
    await db.commit()
    assert (await _rodar(db, admin)).status == BackgroundJobStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_celulas_e_anuncios_iguais_ao_grid_e_ao_envio(
    db: AsyncSession, admin: User, cenario, client: AsyncClient, auth_as: Callable
):
    """As células são as do /grid que mandariam preço, e os anúncios de cada
    uma são os que o envio (`push.resolver_para_envio`) usaria."""
    # Um kit de outra linha e um anúncio comum velho no índice: ruído.
    p_b = Product(user_id=admin.id, sku="b200.sa+a003.sa", name="Kit b200")
    db.add(p_b)
    await db.flush()
    db.add(_link(admin, cenario.ml, p_b, "MLB400", listing_type="gold_special"))
    await db.commit()

    celulas = await conferencia.montar_celulas(db, "celular", {"ml", "amazon"})
    por_par = {(cel.conta.id, cel.produto.id): cel for cel in celulas}

    auth_as(admin)
    grade = (await client.get("/api/pricing/grid", params={"department": "celular"})).json()
    esperado = {
        (uuid.UUID(g["pricing_account_id"]), uuid.UUID(g["pricing_product_id"]))
        for g in grade["cells"]
        if g["price"] is not None and g["cell_status"] != "NA"
    }
    assert set(por_par) == esperado
    assert await _paridade_com_o_envio(db, "celular", celulas) == 5


async def _paridade_com_o_envio(
    db: AsyncSession, department: str, celulas: list[conferencia.Celula]
) -> int:
    """Cada célula tem os anúncios que o envio (`push_one` →
    `resolver_para_envio`) usaria. Devolve quantas têm anúncio."""
    contas = {a.id: a for a in (await db.execute(select(PricingAccount))).scalars().all()}
    com_anuncio = 0
    for cel in celulas:
        conta = contas[cel.conta.id]
        base = contas.get(conta.conta_base_id) if conta.canal == CANAL_CATALOGO else None
        dona = base or conta
        if dona.integration_id is None or cel.situacao == "nao_suportada":
            continue
        integ = await db.get(Integration, dona.integration_id)
        canal = conta.canal or CANAL_KIT
        pula = (
            canal == CANAL_KIT
            and integ.platform == IntegrationPlatform.ML
            and kit_pula_catalogo(conta.id in await bases_com_catalogo_ligado(db, [conta.id]))
        )
        envio = await resolver_para_envio(
            db,
            account=conta,
            pricing_product=cel.produto,
            department=department,
            platform_str=integ.platform.value,
            conta_base=base,
            pula_catalogo=pula,
        )
        assert [lk.id for lk in cel.links] == [lk.id for lk in envio.links], (conta.name, cel)
        com_anuncio += bool(cel.links)
    return com_anuncio


@pytest.mark.asyncio
async def test_mala_casa_pelo_sku_inteiro_como_o_envio(db: AsyncSession, admin: User, cenario):
    """Mala: o anúncio casa pelo SKU inteiro ou pelo lado esquerdo do kit
    (`sku_casa_no_departamento`); o índice da conferência não perde nenhum."""
    folha = cenario.seg["mala_filhos"][0]
    produtos = {}
    for sku in ("b109.20", "b109.20+a075", "b109.20+a999", "b109.24", "B109.30"):
        produtos[sku] = Product(user_id=admin.id, sku=sku, name=f"Mala {sku}")
    db.add_all(produtos.values())
    await db.flush()
    for i, prod in enumerate(produtos.values()):
        db.add(_link(admin, cenario.ml, prod, f"MLB5{i}", listing_type="gold_special"))
        db.add(_link(admin, cenario.amz, prod, f"B0MALA{i}"))
    conta_ml = _conta(
        admin,
        cenario.seg["mala"],
        "counhago mala",
        PricingPlatform.ML,
        listing_type="ml classico",
        integration_id=cenario.ml.id,
    )
    conta_amz = _conta(
        admin,
        cenario.seg["mala"],
        "amazon mala",
        PricingPlatform.AMAZON,
        integration_id=cenario.amz.id,
    )
    db.add_all([conta_ml, conta_amz])
    for sku in ("b109.20", "b109.20+a075", "b109.24", "b109.30", "b109.99"):
        db.add(_linha(admin, folha, sku, cost_kit1=Decimal("100")))
    await db.commit()

    celulas = await conferencia.montar_celulas(db, "mala", {"ml", "amazon"})
    links = {
        (cel.conta.name, cel.produto.sku): sorted(lk.external_id for lk in cel.links)
        for cel in celulas
    }
    assert links[("counhago mala", "b109.20")] == ["MLB50", "MLB51", "MLB52"]
    assert links[("counhago mala", "b109.20+a075")] == ["MLB51"]
    assert links[("counhago mala", "b109.24")] == ["MLB53"]
    assert links[("counhago mala", "b109.30")] == ["MLB54"]  # SKU em maiúsculas no Bling
    assert links[("counhago mala", "b109.99")] == []
    assert links[("amazon mala", "b109.20")] == ["B0MALA0", "B0MALA1", "B0MALA2"]
    assert await _paridade_com_o_envio(db, "mala", celulas) == 8


@pytest.mark.asyncio
async def test_anuncio_de_catalogo_disputado_fica_fora_das_duas_linhas(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    """Duas linhas (q100.a e q100.b) mandam preço no mesmo anúncio de catálogo:
    o envio não manda para ele em nenhuma — a conferência também não lê."""
    acess = cenario.seg["celular_filhos"][0]
    p_q = Product(user_id=admin.id, sku="q100.a", name="Q100")
    db.add(p_q)
    await db.flush()
    db.add(
        _link(
            admin,
            cenario.ml,
            p_q,
            "MLB700",
            listing_type="gold_special",
            catalog_listing=True,
            anuncio_status="active",
        )
    )
    qa = _linha(admin, acess, "q100.a", cost_kit1=Decimal("10"), preco_catalogo=Decimal("20"))
    qb = _linha(admin, acess, "q100.b", cost_kit1=Decimal("10"), preco_catalogo=Decimal("25"))
    db.add_all([qa, qb])
    await db.commit()
    await _rodar(db, admin)
    gravadas = await _gravadas(db)
    for linha, outra in ((qa, "q100.b"), (qb, "q100.a")):
        cel = gravadas[(cenario.filha.id, linha.id)]
        assert (cel.situacao, cel.anuncios) == ("sem_anuncio", [])
        assert f"MLB700 também casa com a linha {outra}" in cel.motivo
    assert "MLB700" not in {lk.external_id for _, lks in marketplaces.ml.chamadas for lk in lks}


# =========================================================== rotas


class _Fila:
    """A fila do arq: guarda o que foi enfileirado e responde o `Job.status()`
    do arq (o pipeline dele lê a chave do resultado, a de "em andamento" e a
    posição na fila)."""

    def __init__(self) -> None:
        self.jobs: list[tuple] = []
        # Status de cada job no arq; o que não está aqui é 'not_found'.
        self.status_no_arq: dict[str, str] = {}
        self.consultas: list[tuple[str, str]] = []
        self.redis_fora = False
        self.demora_s = 0.0

    async def enqueue_job(self, nome, *args):
        self.jobs.append((nome, *args))
        return SimpleNamespace(job_id=f"arq-{len(self.jobs)}")

    def pipeline(self, transaction: bool = True) -> _PipelineFalso:
        return _PipelineFalso(self)


class _PipelineFalso:
    def __init__(self, fila: _Fila) -> None:
        self.fila = fila
        self.respostas: list[Any] = []

    async def __aenter__(self) -> _PipelineFalso:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _status(self, job_id: str) -> str:
        return self.fila.status_no_arq.get(job_id, "not_found")

    def exists(self, chave: str) -> None:
        if chave.startswith(result_key_prefix):
            terminou = self._status(chave.removeprefix(result_key_prefix)) == "complete"
            self.respostas.append(int(terminou))
        else:
            rodando = self._status(chave.removeprefix(in_progress_key_prefix)) == "in_progress"
            self.respostas.append(int(rodando))

    def zscore(self, fila: str, job_id: str) -> None:
        self.fila.consultas.append((fila, job_id))
        na_fila = self._status(job_id) in ("queued", "in_progress")
        self.respostas.append(1.0 if na_fila else None)

    async def execute(self) -> list[Any]:
        if self.fila.demora_s:
            await asyncio.sleep(self.fila.demora_s)
        if self.fila.redis_fora:
            raise ConnectionError("redis fora")
        return self.respostas


@pytest.fixture
def fila(monkeypatch) -> _Fila:
    f = _Fila()

    async def _pool():
        return f

    monkeypatch.setattr(worker_pool, "get_arq_sync_pool", _pool)
    return f


@pytest.mark.asyncio
async def test_conferir_e_ver_para_quem_ve_a_tabela(
    db: AsyncSession,
    client: AsyncClient,
    admin: User,
    make_user,
    cenario,
    auth_as: Callable,
    fila: _Fila,
    monkeypatch,
):
    """M-3, 17/11/2026: o cartão vermelho e o (!) são de todos que veem a
    Tabela — o GET vale para `tabela_precos` (ver). M-7, 18/11/2026: conferir
    também; a margem continua só do administrador (o teste da cerca de equipe
    está em test_pricing_conferencia_margem.py)."""
    monkeypatch.setattr(get_settings(), "env", "production")
    vendedor = await make_user(role=UserRole.USER, permissions=PERM_VER)
    auth_as(vendedor)
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    assert fila.jobs == [("conferir_precos_run", job_id, "celular", str(vendedor.id))]
    job = await db.get(BackgroundJob, uuid.UUID(job_id))
    assert job.created_by == vendedor.id
    # O balão dele mostra o andamento (o job vem, sem o resumo).
    r = await client.get("/api/pricing/conferencia", params={"department": "celular"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert (corpo["pode_conferir"], corpo["margem"]["pode_editar"]) == (True, False)
    assert (corpo["job"]["id"], corpo["job"]["status"], corpo["job"]["result"]) == (
        job_id,
        "pending",
        {},
    )
    # O /api/jobs não mostra a conferência a quem não é administrador (o
    # resumo do departamento inteiro iria junto); ao administrador, sim.
    assert (await client.get(f"/api/jobs/{job_id}/status")).status_code == 404
    assert (await client.get(f"/api/jobs/{job_id}")).status_code == 404
    lista = (await client.get("/api/jobs")).json()
    assert lista["total"] == 0 and lista["items"] == []
    # Outra pessoa (o administrador) clica no mesmo departamento: o aviso de sempre.
    auth_as(admin)
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert r.json()["detail"] == {"code": "conferencia_em_andamento", "job_id": job_id}
    assert (await client.get(f"/api/jobs/{job_id}/status")).status_code == 200
    assert len(fila.jobs) == 1

    sem_tabela = await make_user(role=UserRole.USER, permissions={})
    auth_as(sem_tabela)
    r = await client.get("/api/pricing/conferencia", params={"department": "celular"})
    assert r.status_code == 403
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert r.status_code == 403
    assert len(fila.jobs) == 1


@pytest.mark.asyncio
async def test_post_fora_da_producao_e_departamento_invalido(
    client: AsyncClient, admin: User, cenario, auth_as: Callable, fila: _Fila, monkeypatch
):
    auth_as(admin)
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert (r.status_code, r.json()["detail"]) == (409, {"code": "so_no_servidor"})
    monkeypatch.setattr(get_settings(), "env", "production")
    r = await client.post("/api/pricing/conferencia", json={"department": "xyz"})
    assert (r.status_code, r.json()["detail"]) == (400, {"code": "invalid_department"})
    r = await client.get("/api/pricing/conferencia", params={"department": "xyz"})
    assert r.status_code == 400
    assert fila.jobs == []


@pytest.mark.asyncio
async def test_post_enfileira_e_recusa_outra_em_andamento(
    db: AsyncSession,
    client: AsyncClient,
    admin: User,
    cenario,
    auth_as: Callable,
    fila: _Fila,
    monkeypatch,
):
    monkeypatch.setattr(get_settings(), "env", "production")
    auth_as(admin)
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    assert fila.jobs == [("conferir_precos_run", job_id, "celular", str(admin.id))]
    job = await db.get(BackgroundJob, uuid.UUID(job_id))
    assert job.type == BackgroundJobType.CONFERIR_PRECOS
    assert job.status == BackgroundJobStatus.PENDING
    assert (job.payload, job.created_by, job.arq_job_id) == (
        {"department": "celular"},
        admin.id,
        "arq-1",
    )

    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "conferencia_em_andamento", "job_id": job_id}
    # Outro departamento não espera.
    r = await client.post("/api/pricing/conferencia", json={"department": "mala"})
    assert r.status_code == 201

    # Rodando sem sinal de vida (worker caiu): não segura o próximo clique.
    job.status = BackgroundJobStatus.RUNNING
    job.last_heartbeat_at = datetime.now(UTC) - conferencia.SEM_SINAL - timedelta(minutes=1)
    db.add(job)
    await db.commit()
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert r.status_code == 201
    assert len(fila.jobs) == 3


@pytest.mark.asyncio
async def test_post_com_contas_escolhidas_vai_no_payload_e_recusa_conta_de_fora(
    db: AsyncSession,
    client: AsyncClient,
    admin: User,
    cenario,
    auth_as: Callable,
    fila: _Fila,
    monkeypatch,
):
    """M-14, 18/11/2026: o "Atualizar ▾" confere só as contas escolhidas — elas
    vão no payload do job (sem repetir); conta que não é coluna do departamento
    é recusada; lista vazia, também."""
    monkeypatch.setattr(get_settings(), "env", "production")
    auth_as(admin)
    de_fora = str(uuid.uuid4())
    r = await client.post(
        "/api/pricing/conferencia",
        json={"department": "celular", "contas": [str(cenario.kit.id), de_fora]},
    )
    assert (r.status_code, r.json()["detail"]) == (400, {"code": "contas_invalidas"})
    r = await client.post("/api/pricing/conferencia", json={"department": "celular", "contas": []})
    assert r.status_code == 422
    assert fila.jobs == []

    escolhidas = sorted([str(cenario.kit.id), str(cenario.conta_amz.id)])
    r = await client.post(
        "/api/pricing/conferencia",
        json={"department": "celular", "contas": [*escolhidas, str(cenario.kit.id)]},
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    assert fila.jobs == [("conferir_precos_run", job_id, "celular", str(admin.id))]
    job = await db.get(BackgroundJob, uuid.UUID(job_id))
    assert job.payload == {"department": "celular", "contas": escolhidas}
    # A conta é do departamento celular: na mala, é recusada.
    r = await client.post(
        "/api/pricing/conferencia",
        json={"department": "mala", "contas": [str(cenario.kit.id)]},
    )
    assert (r.status_code, r.json()["detail"]) == (400, {"code": "contas_invalidas"})


@pytest.mark.asyncio
async def test_post_com_a_fila_fora_do_ar(
    db: AsyncSession,
    client: AsyncClient,
    admin: User,
    cenario,
    auth_as: Callable,
    marketplaces,
    monkeypatch,
):
    async def _sem_redis():
        raise ConnectionError("redis fora")

    monkeypatch.setattr(worker_pool, "get_arq_sync_pool", _sem_redis)
    auth_as(admin)
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert (r.status_code, r.json()["detail"]) == (503, {"code": "fila_indisponivel"})
    (job,) = (await db.execute(select(BackgroundJob))).scalars().all()
    assert (job.status, job.error) == (BackgroundJobStatus.FAILED, "fila_indisponivel")
    assert job.finished_at is not None
    # Não trava o próximo clique.
    assert await conferencia.job_em_andamento(db, "celular") is None
    # Se o arq recebeu o job mesmo assim, ele não roda: já foi dado como falho.
    await conferencia.run_conferir_precos(job_id=job.id, department="celular", user_id=admin.id)
    await db.refresh(job)
    assert (job.status, job.error) == (BackgroundJobStatus.FAILED, "fila_indisponivel")
    assert marketplaces.montados == []
    assert await _gravadas(db) == {}


@pytest.mark.asyncio
async def test_get_devolve_o_ultimo_job_e_as_celulas(
    db: AsyncSession, client: AsyncClient, admin: User, cenario, auth_as: Callable, marketplaces
):
    auth_as(admin)
    r = await client.get("/api/pricing/conferencia", params={"department": "celular"})
    assert r.json() == {
        "job": None,
        "ultimo_concluido": None,
        "celulas": [],
        # M-2 (17/11/2026): a margem do servidor (nada gravado = 5%) e quem confere.
        "margem": {
            "department": "celular",
            "margem_pct": 5.0,
            "geral_ligada": True,
            "por_produto_ligada": True,
            "produtos": {},
            "pode_editar": True,
        },
        "pode_conferir": True,
    }

    job = await _rodar(db, admin)
    r = await client.get("/api/pricing/conferencia", params={"department": "celular"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["job"]["id"] == str(job.id)
    assert corpo["job"]["status"] == "succeeded"
    assert corpo["ultimo_concluido"] == str(job.id)
    assert {c["job_id"] for c in corpo["celulas"]} == {str(job.id)}
    assert (corpo["job"]["total"], corpo["job"]["processed"]) == (3, 3)
    assert corpo["job"]["result"]["celulas"] == len(corpo["celulas"])
    assert set(corpo["job"]) == {
        "id",
        "status",
        "total",
        "processed",
        "created_at",
        "finished_at",
        "error",
        "result",
        "parcial",
    }
    assert corpo["job"]["parcial"] is None
    por_par = {(c["pricing_account_id"], c["pricing_product_id"]): c for c in corpo["celulas"]}
    kit = por_par[(str(cenario.kit.id), str(cenario.a003.id))]
    assert kit["preco_tabela"] == 59.0
    assert kit["situacao"] == "conferida"
    assert kit["anuncios"][0]["anuncio"] == "MLB101"
    assert set(kit) == {
        "pricing_account_id",
        "pricing_product_id",
        "preco_tabela",
        "situacao",
        "motivo",
        "conferido_em",
        "anuncios",
        "job_id",
    }

    # Um job novo rodando que parou de dar sinal aparece como falho na tela.
    morto = await _novo_job(
        db,
        admin,
        status=BackgroundJobStatus.RUNNING,
        started_at=datetime.now(UTC) - timedelta(hours=1),
        last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    corpo = (await client.get("/api/pricing/conferencia", params={"department": "celular"})).json()
    assert corpo["job"]["id"] == str(morto.id)
    assert (corpo["job"]["status"], corpo["job"]["error"]) == ("failed", "parou_sem_sinal")
    # As células gravadas continuam lá, e o último que terminou bem é o de antes.
    assert len(corpo["celulas"]) == len(por_par)
    assert corpo["ultimo_concluido"] == str(job.id)


@pytest.mark.asyncio
async def test_pendente_que_o_worker_nao_executou_nao_trava_a_conferencia(
    db: AsyncSession,
    client: AsyncClient,
    admin: User,
    cenario,
    auth_as: Callable,
    fila: _Fila,
    monkeypatch,
):
    """O worker_sync no código velho descarta o job ("function not found"): o
    arq o dá como terminado e a linha daqui fica 'pending'. Passados 10 min, o
    GET pergunta ao arq e o dá como falho, gravado no job."""
    monkeypatch.setattr(get_settings(), "env", "production")
    auth_as(admin)
    velho = datetime.now(UTC) - conferencia.PENDENTE_SUSPEITO - timedelta(minutes=1)
    job = await _novo_job(db, admin, created_at=velho, arq_job_id="arq-velho")

    async def _job_na_tela() -> dict:
        r = await client.get("/api/pricing/conferencia", params={"department": "celular"})
        assert r.status_code == 200, r.text
        return r.json()["job"]

    # Ainda na fila do arq (as vagas do worker ocupadas): segue pendente.
    fila.status_no_arq["arq-velho"] = "queued"
    visto = await _job_na_tela()
    assert (visto["status"], visto["error"]) == ("pending", None)
    assert fila.consultas == [(worker_pool.ARQ_SYNC_QUEUE, "arq-velho")]
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert r.status_code == 409

    # Redis fora do ar ou lento: a regra de sempre, sem quebrar o GET.
    fila.redis_fora = True
    assert (await _job_na_tela())["status"] == "pending"
    fila.redis_fora = False
    monkeypatch.setattr(conferencia, "CONSULTA_AO_ARQ_S", 0.05)
    fila.demora_s = 1.0
    assert (await _job_na_tela())["status"] == "pending"
    fila.demora_s = 0.0

    # O worker descartou: o arq guardou o resultado ("function not found").
    fila.status_no_arq["arq-velho"] = "complete"
    visto = await _job_na_tela()
    assert (visto["status"], visto["error"]) == ("failed", "worker_nao_executou")
    await db.refresh(job)
    assert (job.status, job.error) == (BackgroundJobStatus.FAILED, "worker_nao_executou")
    assert job.finished_at is not None
    # E o clique passa.
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_clique_da_baixa_no_pendente_que_sumiu_do_arq_mas_nao_pergunta_cedo(
    db: AsyncSession,
    client: AsyncClient,
    admin: User,
    cenario,
    auth_as: Callable,
    fila: _Fila,
    monkeypatch,
):
    monkeypatch.setattr(get_settings(), "env", "production")
    auth_as(admin)
    job = await _novo_job(db, admin, arq_job_id="arq-sumido")
    # Pendente há pouco: nem pergunta ao arq (o worker pode só estar ocupado).
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert r.status_code == 409
    assert fila.consultas == []

    # Mais de 10 min e fora do arq (o resultado guardado expira em 1 h): o
    # clique dá o job como falho no mesmo commit do job novo.
    job.created_at = datetime.now(UTC) - conferencia.PENDENTE_SUSPEITO - timedelta(minutes=1)
    db.add(job)
    await db.commit()
    r = await client.post("/api/pricing/conferencia", json={"department": "celular"})
    assert r.status_code == 201, r.text
    assert fila.consultas == [(worker_pool.ARQ_SYNC_QUEUE, "arq-sumido")]
    await db.refresh(job)
    assert (job.status, job.error) == (BackgroundJobStatus.FAILED, "worker_nao_executou")
    novo = await db.get(BackgroundJob, uuid.UUID(r.json()["job_id"]))
    assert novo.status == BackgroundJobStatus.PENDING

    # Se o job velho rodasse depois mesmo assim, o começo o barra.
    await conferencia.run_conferir_precos(job_id=job.id, department="celular", user_id=admin.id)
    await db.refresh(job)
    assert (job.status, job.error) == (BackgroundJobStatus.FAILED, "worker_nao_executou")


# =========================================================== uma célula (M-15)


def _retrato(gravadas: dict[tuple, PricingConferencia]) -> dict[tuple, tuple]:
    """O que cada célula tem agora (o `_gravadas` reaproveita os objetos)."""
    return {
        chave: (g.job_id, g.conferido_em, g.situacao, g.motivo, g.anuncios)
        for chave, g in gravadas.items()
    }


@pytest.mark.asyncio
async def test_conferir_uma_celula_le_e_grava_so_ela(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    """Clique no cartão da x010 na counhago: só o anúncio dela é lido, só a
    linha dela é regravada (sem job), e o job e a data da última conferência
    do departamento ficam como estavam."""
    c = cenario
    job = await _rodar(db, admin)
    terminou_em = job.finished_at
    antes = _retrato(await _gravadas(db))
    marketplaces.ml.chamadas.clear()
    marketplaces.montados.clear()
    marketplaces.ml.precos = {**marketplaces.ml.precos, "MLB300": "140"}

    linha = await conferencia.conferir_celula("celular", c.kit.id, c.x010.id)

    assert (linha["situacao"], linha["job_id"]) == ("conferida", None)
    assert [(a["anuncio"], a["preco"]) for a in linha["anuncios"]] == [("MLB300", 140.0)]
    lidos = [(nome, [lk.external_id for lk in lks]) for nome, lks in marketplaces.ml.chamadas]
    assert lidos == [("counhago", ["MLB300"])]
    assert marketplaces.montados == ["counhago"]
    depois = _retrato(await _gravadas(db))
    assert set(depois) == set(antes)
    job_id, conferido_em, situacao, _motivo, anuncios = depois[(c.kit.id, c.x010.id)]
    assert (job_id, situacao, anuncios[0]["preco"]) == (None, "conferida", 140.0)
    assert conferido_em > antes[(c.kit.id, c.x010.id)][1]
    for chave, velha in antes.items():
        if chave != (c.kit.id, c.x010.id):
            assert depois[chave] == velha, chave
    await db.refresh(job)
    assert job.finished_at == terminou_em
    assert len((await db.execute(select(BackgroundJob))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_conferir_celula_do_catalogo_e_leitura_que_falha(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    c = cenario
    await _rodar(db, admin)
    marketplaces.ml.chamadas.clear()
    # Coluna de catálogo: pela integração da base, só o anúncio de catálogo.
    linha = await conferencia.conferir_celula("celular", c.filha.id, c.a003.id)
    assert [a["anuncio"] for a in linha["anuncios"]] == ["MLB200"]
    lidos = [[lk.external_id for lk in lks] for _, lks in marketplaces.ml.chamadas]
    assert lidos == [["MLB200"]]
    # A leitura falhou: nada é gravado, a célula fica com a leitura anterior.
    antes = _retrato(await _gravadas(db))
    marketplaces.ml.falha_em = {"counhago"}
    with pytest.raises(conferencia.LeituraDaCelulaFalhouError, match="403"):
        await conferencia.conferir_celula("celular", c.kit.id, c.a003.id)
    assert _retrato(await _gravadas(db)) == antes


@pytest.mark.asyncio
async def test_conferir_celula_sem_anuncio_para_ler(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    c = cenario
    linha = await conferencia.conferir_celula("celular", c.sem_integ.id, c.a003.id)
    assert (linha["situacao"], linha["motivo"]) == ("sem_anuncio", "conta sem integração")
    linha = await conferencia.conferir_celula("celular", c.kit.id, c.z999.id)
    assert (linha["situacao"], linha["motivo"]) == ("sem_anuncio", "sem anúncio vinculado")
    assert marketplaces.montados == []
    # NA na counhago: a célula não manda preço — a leitura antiga dela sai.
    db.add(
        PricingConferencia(
            pricing_account_id=c.kit.id,
            pricing_product_id=c.b200.id,
            department="celular",
            situacao="conferida",
        )
    )
    await db.commit()
    assert await conferencia.conferir_celula("celular", c.kit.id, c.b200.id) is None
    assert (c.kit.id, c.b200.id) not in await _gravadas(db)
    assert marketplaces.montados == []


@pytest.mark.asyncio
async def test_departamento_nao_passa_por_cima_da_celula_conferida_no_meio(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    """A counhago já começou a ser lida pelo departamento quando a pessoa
    clica no cartão da x010: a célula grava o preço novo, e a gravação do
    departamento (lida antes) não passa por cima dela."""
    c = cenario
    ml = marketplaces.ml
    clicou = asyncio.Event()

    async def _ml(cliente, vinculos):
        if cliente.nome == "counhago" and not clicou.is_set():
            clicou.set()
            ml.precos = {**ml.precos, "MLB300": "140"}
            await conferencia.conferir_celula("celular", c.kit.id, c.x010.id)
            ml.precos = {**ml.precos, "MLB300": "128"}
        return await ml(cliente, vinculos)

    marketplaces.leitores["ml"] = _ml
    job = await _rodar(db, admin)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    gravadas = await _gravadas(db)
    x010 = gravadas[(c.kit.id, c.x010.id)]
    assert (x010.job_id, x010.anuncios[0]["preco"]) == (None, 140.0)
    # O resto da conta grava normalmente.
    assert gravadas[(c.kit.id, c.a003.id)].job_id == job.id
    # A próxima conferência do departamento (lida depois) regrava a célula.
    seguinte = await _rodar(db, admin)
    x010 = (await _gravadas(db))[(c.kit.id, c.x010.id)]
    assert (x010.job_id, x010.anuncios[0]["preco"]) == (seguinte.id, 128.0)


@pytest.mark.asyncio
async def test_dois_cliques_na_mesma_celula_fazem_uma_leitura(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    liberar = asyncio.Event()

    async def _lento(cliente, vinculos):
        await liberar.wait()
        return await marketplaces.ml(cliente, vinculos)

    marketplaces.leitores["ml"] = _lento
    primeira = asyncio.create_task(
        conferencia.conferir_celula("celular", cenario.kit.id, cenario.x010.id)
    )
    await asyncio.sleep(0)
    with pytest.raises(conferencia.CelulaEmConferenciaError):
        await conferencia.conferir_celula("celular", cenario.kit.id, cenario.x010.id)
    liberar.set()
    linha = await asyncio.wait_for(primeira, timeout=10)
    assert linha["situacao"] == "conferida"
    assert len(marketplaces.ml.chamadas) == 1
    # Terminou: a célula pode ser conferida de novo.
    await conferencia.conferir_celula("celular", cenario.kit.id, cenario.x010.id)
    assert len(marketplaces.ml.chamadas) == 2


@pytest.mark.asyncio
async def test_rota_da_celula_para_quem_ve_a_tabela(
    db: AsyncSession,
    client: AsyncClient,
    make_user,
    cenario,
    auth_as: Callable,
    marketplaces,
    monkeypatch,
):
    c = cenario
    url = "/api/pricing/conferencia/celula"
    corpo = {"pricing_account_id": str(c.kit.id), "pricing_product_id": str(c.x010.id)}
    vendedor = await make_user(role=UserRole.USER, permissions=PERM_VER)
    auth_as(vendedor)
    r = await client.post(url, json=corpo)
    assert r.status_code == 200, r.text
    assert r.json()["erro"] is None
    cel = r.json()["celula"]
    assert (cel["pricing_account_id"], cel["pricing_product_id"]) == (
        str(c.kit.id),
        str(c.x010.id),
    )
    assert (cel["situacao"], cel["job_id"], cel["anuncios"][0]["preco"]) == (
        "conferida",
        None,
        128.0,
    )
    # Sem job: a conferência do departamento não muda.
    assert (await db.execute(select(BackgroundJob))).scalars().all() == []
    # A leitura falhou: 200 com o erro e nada gravado.
    marketplaces.ml.falha_em = {"counhago"}
    r = await client.post(url, json=corpo)
    assert r.json() == {
        "celula": None,
        "erro": "o Mercado Livre recusou o acesso da conta (403)",
        "sem_vinculo_tirado": False,
    }
    # Célula que não manda preço (NA): celula null.
    r = await client.post(
        url, json={"pricing_account_id": str(c.kit.id), "pricing_product_id": str(c.b200.id)}
    )
    assert r.json() == {"celula": None, "erro": None, "sem_vinculo_tirado": False}
    # Linha que não existe.
    r = await client.post(
        url, json={"pricing_account_id": str(c.kit.id), "pricing_product_id": str(uuid.uuid4())}
    )
    assert (r.status_code, r.json()["detail"]) == (404, {"code": "product_not_found"})
    # Conta de outro departamento (a linha é do celular).
    r = await client.post(
        url, json={"pricing_account_id": str(c.kit.id), "pricing_product_id": str(c.mala.id)}
    )
    assert (r.status_code, r.json()["detail"]) == (404, {"code": "account_not_found"})

    # Cerca de equipe: equipe sem loja nenhuma não vê a coluna.
    de_fora = await make_user(role=UserRole.USER, permissions=PERM_VER)
    de_fora.sales_teams = [999]
    await db.commit()
    auth_as(de_fora)
    r = await client.post(url, json=corpo)
    assert (r.status_code, r.json()["detail"]) == (404, {"code": "account_not_found"})
    # Sem a Tabela: 403.
    auth_as(await make_user(role=UserRole.USER, permissions={}))
    assert (await client.post(url, json=corpo)).status_code == 403
    # Fora do servidor não lê nada.
    monkeypatch.setattr(get_settings(), "env", "development")
    auth_as(vendedor)
    r = await client.post(url, json=corpo)
    assert (r.status_code, r.json()["detail"]) == (409, {"code": "so_no_servidor"})


# =========================================================== "sem anúncio vinculado" vencido
# 19/11/2026: o envio que não acha vínculo grava cell_status `no_link` e só um
# envio com sucesso o tirava. Vínculo criado depois (wp60 4g 36.512 na fiore:
# envio 10:00, vínculo 10:05 de 18/11) deixava o cartão vermelho preso — nem o
# clique no cartão nem a conferência inteira o apagavam.


async def _marcas(db: AsyncSession, c: SimpleNamespace, **estados: CellStatus) -> None:
    """Grava cell_status nas células "linha__conta" do cenário (a003__kit...)."""
    for chave, estado in estados.items():
        linha, conta = chave.split("__")
        db.add(
            PricingOverride(
                user_id=c.kit.user_id,
                pricing_product_id=getattr(c, linha).id,
                pricing_account_id=getattr(c, conta).id,
                cell_status=estado,
            )
        )
    await db.commit()


async def _estado(db: AsyncSession, c: SimpleNamespace, chave: str) -> CellStatus:
    linha, conta = chave.split("__")
    return (
        await db.execute(
            select(PricingOverride.cell_status).where(
                PricingOverride.pricing_product_id == getattr(c, linha).id,
                PricingOverride.pricing_account_id == getattr(c, conta).id,
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_conferencia_tira_o_sem_vinculo_vencido_e_so_ele(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    """Célula com anúncio vinculado (ML com leitor, Shopee sem leitor e a
    leitura que falhou na conta: o vínculo é do banco) perde o no_link; sem
    vínculo de verdade ele fica; os outros estados não mudam."""
    c = cenario
    marketplaces.ml.falha_em = {"ml quebrado"}
    await _marcas(
        db,
        c,
        a003__kit=CellStatus.NO_LINK,  # MLB100: tem vínculo
        a003__conta_sh=CellStatus.NO_LINK,  # Shopee sem leitor, com vínculo
        a003__quebrada=CellStatus.NO_LINK,  # MLB900, a leitura da conta falha
        z999__kit=CellStatus.NO_LINK,  # sem anúncio de verdade
        x010__quebrada=CellStatus.NO_LINK,  # a ml quebrado não tem x010
        a003__conta_amz=CellStatus.ERROR,  # erro do envio: não é desta regra
        x010__kit=CellStatus.SV,
    )
    job = await _rodar(db, admin)
    assert job.status == BackgroundJobStatus.SUCCEEDED, job.error

    esperado = {
        "a003__kit": CellStatus.AUTO,
        "a003__conta_sh": CellStatus.AUTO,
        "a003__quebrada": CellStatus.AUTO,
        "z999__kit": CellStatus.NO_LINK,
        "x010__quebrada": CellStatus.NO_LINK,
        "a003__conta_amz": CellStatus.ERROR,
        "x010__kit": CellStatus.SV,
    }
    assert {k: await _estado(db, c, k) for k in esperado} == esperado
    # NA e desligada do cenário continuam.
    assert await _estado(db, c, "b200__kit") == CellStatus.NA
    assert await _estado(db, c, "c300__kit") == CellStatus.DISABLED


@pytest.mark.asyncio
async def test_conferencia_parcial_tira_so_nas_contas_escolhidas(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    c = cenario
    await _marcas(db, c, a003__kit=CellStatus.NO_LINK, a003__conta_sh=CellStatus.NO_LINK)
    await _rodar_parcial(db, admin, [c.conta_sh.id])
    assert await _estado(db, c, "a003__conta_sh") == CellStatus.AUTO
    assert await _estado(db, c, "a003__kit") == CellStatus.NO_LINK


@pytest.mark.asyncio
async def test_clique_no_cartao_tira_o_sem_vinculo_vencido(
    db: AsyncSession, admin: User, cenario, marketplaces
):
    """O clique tira a marca e avisa a tela; a leitura que falhou não mexe em
    nada; o segundo clique não tem o que tirar; sem vínculo a marca fica."""
    c = cenario
    await _marcas(db, c, a003__kit=CellStatus.NO_LINK, z999__kit=CellStatus.NO_LINK)

    marketplaces.ml.falha_em = {"counhago"}
    with pytest.raises(conferencia.LeituraDaCelulaFalhouError):
        await conferencia.conferir_celula("celular", c.kit.id, c.a003.id)
    assert await _estado(db, c, "a003__kit") == CellStatus.NO_LINK

    marketplaces.ml.falha_em = set()
    linha = await conferencia.conferir_celula("celular", c.kit.id, c.a003.id)
    assert (linha["situacao"], linha["sem_vinculo_tirado"]) == ("conferida", True)
    assert await _estado(db, c, "a003__kit") == CellStatus.AUTO
    linha = await conferencia.conferir_celula("celular", c.kit.id, c.a003.id)
    assert linha["sem_vinculo_tirado"] is False

    linha = await conferencia.conferir_celula("celular", c.kit.id, c.z999.id)
    assert (linha["situacao"], linha["sem_vinculo_tirado"]) == ("sem_anuncio", False)
    assert await _estado(db, c, "z999__kit") == CellStatus.NO_LINK


@pytest.mark.asyncio
async def test_rota_da_celula_avisa_que_tirou_o_sem_vinculo(
    db: AsyncSession, client: AsyncClient, make_user, cenario, auth_as: Callable, marketplaces
):
    c = cenario
    await _marcas(db, c, a003__conta_sh=CellStatus.NO_LINK)
    auth_as(await make_user(role=UserRole.USER, permissions=PERM_VER))
    corpo = {"pricing_account_id": str(c.conta_sh.id), "pricing_product_id": str(c.a003.id)}
    r = await client.post("/api/pricing/conferencia/celula", json=corpo)
    assert r.status_code == 200, r.text
    assert (r.json()["celula"]["situacao"], r.json()["sem_vinculo_tirado"]) == (
        "nao_suportada",
        True,
    )
    assert await _estado(db, c, "a003__conta_sh") == CellStatus.AUTO
    r = await client.post("/api/pricing/conferencia/celula", json=corpo)
    assert r.json()["sem_vinculo_tirado"] is False


# =========================================================== job e migration


def test_job_registrado_na_fila_de_sync():
    from app import worker

    (fn,) = [
        f
        for f in worker.WorkerSettingsSync.functions
        if getattr(f, "name", None) == "conferir_precos_run"
    ]
    assert (fn.timeout_s, fn.max_tries) == (3600, 1)
    assert fn.coroutine is worker.conferir_precos_run
    assert "conferir_precos_run" in worker.__all__
    # O prazo próprio vence antes do teto do arq (senão o estouro de tempo
    # chegaria como cancelamento de fora, 'interrompida').
    assert conferencia.TETO_DO_ARQ_S == fn.timeout_s
    assert 0 < conferencia.PRAZO_S < fn.timeout_s


_MIGRATION = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0442_pricing_conferencia.py"
)


def _carregar_migration():
    spec = importlib.util.spec_from_file_location("migration_0442", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _estrutura(db: AsyncSession, schema: str) -> dict[str, list]:
    colunas = (
        await db.execute(
            text(
                "SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable,"
                " column_default FROM information_schema.columns"
                " WHERE table_schema = :s AND table_name = 'pricing_conferencia'"
                " ORDER BY ordinal_position"
            ),
            {"s": schema},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                "SELECT co.conname, pg_get_constraintdef(co.oid) FROM pg_constraint co"
                " JOIN pg_class cl ON cl.oid = co.conrelid"
                " JOIN pg_namespace n ON n.oid = cl.relnamespace"
                " WHERE n.nspname = :s AND cl.relname = 'pricing_conferencia'"
                " ORDER BY co.conname"
            ),
            {"s": schema},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes"
                " WHERE schemaname = :s AND tablename = 'pricing_conferencia'"
                " ORDER BY indexname"
            ),
            {"s": schema},
        )
    ).all()
    return {
        "colunas": [tuple(r) for r in colunas],
        "constraints": [(r[0], r[1].replace(f"{schema}.", "")) for r in constraints],
        "indices": [(r[0], r[1].replace(f"{schema}.", "")) for r in indices],
    }


@pytest.mark.asyncio
async def test_migration_0442_cria_o_que_o_model_declara_e_o_downgrade_desfaz(db: AsyncSession):
    mod = _carregar_migration()
    assert mod.revision == "0442_pricing_conferencia"
    assert mod.down_revision == "0441_equipes_londres_israel_marrocos"
    schema_model = Base.metadata.schema
    rascunho = f"{schema_model}_mig0442"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a 0442 toca: as tabelas das FKs e o enum do job (e o tipo do job,
    # que o downgrade usa para apagar os da conferência).
    await db.execute(text(f"CREATE TYPE \"{rascunho}\".background_job_type AS ENUM ('sync_all')"))
    for tabela in ("pricing_accounts", "pricing_products"):
        await db.execute(text(f'CREATE TABLE "{rascunho}".{tabela} (id uuid PRIMARY KEY)'))
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".background_jobs'
            f' (id uuid PRIMARY KEY, type "{rascunho}".background_job_type)'
        )
    )
    await db.commit()

    def _rodar_passo(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar_passo, "upgrade")
        await db.commit()

        da_migration = await _estrutura(db, rascunho)
        do_model = await _estrutura(db, schema_model)
        assert len(da_migration["colunas"]) == 9
        assert da_migration == do_model
        valores = (
            (
                await db.execute(
                    text(f'SELECT unnest(enum_range(NULL::"{rascunho}".background_job_type))::text')
                )
            )
            .scalars()
            .all()
        )
        assert valores == ["sync_all", "conferir_precos"]

        conta, linha, job = (str(uuid.uuid4()) for _ in range(3))
        await db.execute(text(f"INSERT INTO \"{rascunho}\".pricing_accounts VALUES ('{conta}')"))
        await db.execute(text(f"INSERT INTO \"{rascunho}\".pricing_products VALUES ('{linha}')"))
        await db.execute(text(f"INSERT INTO \"{rascunho}\".background_jobs VALUES ('{job}')"))
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".pricing_conferencia'
                " (pricing_account_id, pricing_product_id, department, situacao, job_id)"
                f" VALUES ('{conta}', '{linha}', 'celular', 'conferida', '{job}')"
            )
        )
        await db.commit()
        assert (
            await db.execute(
                text(
                    "SELECT anuncios::text, conferido_em IS NOT NULL"
                    f' FROM "{rascunho}".pricing_conferencia'
                )
            )
        ).one() == ("[]", True)
        with pytest.raises(IntegrityError):
            await db.execute(
                text(f"UPDATE \"{rascunho}\".pricing_conferencia SET situacao = 'outra'")
            )
        await db.rollback()
        # Apagar o job zera o vínculo; apagar a linha da tabela leva a célula.
        await db.execute(text(f'DELETE FROM "{rascunho}".background_jobs'))
        assert (
            await db.execute(text(f'SELECT job_id FROM "{rascunho}".pricing_conferencia'))
        ).scalar_one() is None
        await db.execute(text(f'DELETE FROM "{rascunho}".pricing_products'))
        assert (
            await db.execute(text(f'SELECT count(*) FROM "{rascunho}".pricing_conferencia'))
        ).scalar_one() == 0
        # Um job da conferência e um de outro tipo, para o downgrade.
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".background_jobs (id, type) VALUES'
                f" ('{uuid.uuid4()}', 'conferir_precos'), ('{uuid.uuid4()}', 'sync_all')"
            )
        )
        await db.commit()

        conn = await db.connection()
        await conn.run_sync(_rodar_passo, "downgrade")
        await db.commit()
        assert await _estrutura(db, rascunho) == {"colunas": [], "constraints": [], "indices": []}
        # O código de antes não lê job 'conferir_precos': o downgrade os apaga.
        tipos = (
            (await db.execute(text(f'SELECT type::text FROM "{rascunho}".background_jobs')))
            .scalars()
            .all()
        )
        assert tipos == ["sync_all"]
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
