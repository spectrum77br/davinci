"""Kit, comissão, margens e fretes da conta só para o administrador (M-10,
18/11/2026).

Eduardo: "Margem, frete, kit e comissão são informações restritas. Esconder
só na tela não protege". Quem não é ADM (mesmo com "ver Tabela" e "ver/
editar Contas") recebe esses campos null no /accounts, no /grid e nas
respostas de uma conta — inclusive os do Catálogo ML — e não consegue
mudá-los; salvar outro campo não mexe neles. O preço da grade continua
calculado no servidor com os números de verdade, igual para todos.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PricingAccount, PricingPlatform, PricingProduct, Segment, UserRole
from app.services.pricing.anuncios import CANAL_CATALOGO

pytestmark = pytest.mark.asyncio

PERM_VENDEDOR = {
    "tabela_precos": {"view": True, "edit": False, "delete": False},
    "tabela_precos_contas": {"view": True, "edit": True, "delete": False},
}

CAMPOS_SO_ADMIN = (
    "kit_number",
    "commission",
    *(f"margin{i}" for i in range(1, 6)),
    *(f"shipping{i}" for i in range(1, 6)),
)


@pytest_asyncio.fixture
async def cenario(db: AsyncSession, make_user) -> SimpleNamespace:
    """Celular: "kia" (ML, Kit 2) com o Catálogo ML ligado e margem própria
    de 40% no tipo 1; "mega" (Shopee, Kit 1). Linha a003 no tipo 1."""
    admin = await make_user(role=UserRole.ADMIN)
    vendedor = await make_user(role=UserRole.USER, permissions=PERM_VENDEDOR)

    celular = Segment(name="Celular", slug="celular", sort_order=0)
    db.add(celular)
    await db.flush()
    tipos = [
        Segment(name=f"tipo {i + 1}", slug=f"cel-{i + 1}", sort_order=i, parent_id=celular.id)
        for i in range(5)
    ]
    db.add_all(tipos)
    await db.flush()

    kia = PricingAccount(
        user_id=admin.id,
        name="kia",
        platform=PricingPlatform.ML,
        listing_type="ml classico",
        segment_id=celular.id,
        kit_number=2,
        commission=Decimal("0.10"),
        margin1=Decimal("0.20"),
        shipping1=Decimal("5.00"),
        margin3=Decimal("0.30"),
        shipping3=Decimal("10.00"),
        discount="5%",
    )
    mega = PricingAccount(
        user_id=admin.id,
        name="mega",
        platform=PricingPlatform.SHOPEE,
        segment_id=celular.id,
        commission=Decimal("0.14"),
        margin1=Decimal("0.20"),
        shipping1=Decimal("3.00"),
    )
    db.add_all([kia, mega])
    await db.flush()
    catalogo = PricingAccount(
        user_id=admin.id,
        name="kia",
        platform=PricingPlatform.ML,
        listing_type="ml classico",
        segment_id=celular.id,
        kit_number=2,
        canal=CANAL_CATALOGO,
        conta_base_id=kia.id,
        margin1=Decimal("0.40"),
    )
    a003 = PricingProduct(
        user_id=admin.id,
        sku="a003",
        name="Fone a003",
        segment_id=tipos[0].id,
        cost_kit1=Decimal("40"),
        cost_kit2=Decimal("70"),
        preco_catalogo=Decimal("55"),
    )
    db.add_all([catalogo, a003])
    await db.commit()
    for o in (kia, mega, catalogo, a003):
        await db.refresh(o)
    return SimpleNamespace(
        admin=admin, vendedor=vendedor, kia=kia, mega=mega, catalogo=catalogo, a003=a003
    )


async def _contas(client: AsyncClient) -> dict[str, dict]:
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    assert r.status_code == 200, r.text
    return {c["id"]: c for c in r.json()}


async def _grade(client: AsyncClient) -> dict:
    r = await client.get("/api/pricing/grid", params={"department": "celular"})
    assert r.status_code == 200, r.text
    return r.json()


def _precos(grade: dict) -> dict[str, str | None]:
    return {c["pricing_account_id"]: c["price"] for c in grade["cells"]}


def _sem_restritos(conta: dict) -> None:
    for campo in CAMPOS_SO_ADMIN:
        assert conta[campo] is None, (conta["name"], campo, conta[campo])
    assert conta["margens_catalogo"] is None


async def test_nao_admin_nao_recebe_kit_comissao_margens_e_fretes(
    client: AsyncClient, auth_as: Callable, cenario
):
    auth_as(cenario.vendedor)
    contas = await _contas(client)
    assert set(contas) == {str(cenario.kia.id), str(cenario.mega.id), str(cenario.catalogo.id)}
    for conta in contas.values():
        _sem_restritos(conta)
    # O resto da conta continua indo (nome, plataforma, anotações, catálogo).
    kia = contas[str(cenario.kia.id)]
    assert (kia["name"], kia["platform"], kia["discount"]) == ("kia", "mercadolivre", "5%")
    assert kia["conta_catalogo_id"] == str(cenario.catalogo.id)
    assert contas[str(cenario.catalogo.id)]["discount"] == "5%"

    grade = await _grade(client)
    for conta in grade["accounts"]:
        _sem_restritos(conta)
    # O preço vem pronto do servidor, com os números de verdade:
    # kia Kit 2: (70 × 1,2 + 5) / 0,9 = 98,89 → 99; catálogo com a margem
    # própria: (55 × 1,4 + 5) / 0,9 = 91,11 → 91; mega: (40 × 1,2 + 3) / 0,86 → 59.
    precos = _precos(grade)
    assert Decimal(precos[str(cenario.kia.id)]) == Decimal("99")
    assert Decimal(precos[str(cenario.catalogo.id)]) == Decimal("91")
    assert Decimal(precos[str(cenario.mega.id)]) == Decimal("59")

    # O ADM vê os mesmos preços.
    auth_as(cenario.admin)
    assert _precos(await _grade(client)) == precos


async def test_admin_recebe_tudo(client: AsyncClient, auth_as: Callable, cenario):
    auth_as(cenario.admin)
    for contas in (
        await _contas(client),
        {c["id"]: c for c in (await _grade(client))["accounts"]},
    ):
        kia = contas[str(cenario.kia.id)]
        assert kia["kit_number"] == 2
        assert Decimal(kia["commission"]) == Decimal("0.10")
        assert Decimal(kia["margin1"]) == Decimal("0.20")
        assert Decimal(kia["shipping1"]) == Decimal("5.00")
        assert Decimal(kia["shipping3"]) == Decimal("10.00")
        filha = contas[str(cenario.catalogo.id)]
        # A coluna de catálogo sai com os números da base e a margem própria.
        assert filha["kit_number"] == 2
        assert Decimal(filha["commission"]) == Decimal("0.10")
        assert Decimal(filha["shipping1"]) == Decimal("5.00")
        assert Decimal(filha["margens_catalogo"]["1"]) == Decimal("0.40")
        mega = contas[str(cenario.mega.id)]
        assert mega["kit_number"] == 1
        assert Decimal(mega["commission"]) == Decimal("0.14")


async def test_nao_admin_nao_muda_nem_zera(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cenario
):
    kia_id = cenario.kia.id
    auth_as(cenario.vendedor)

    # Outro campo: grava, a resposta sai sem os números e eles ficam no banco.
    r = await client.patch(f"/api/pricing/accounts/{kia_id}", json={"name": "kia nova"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "kia nova"
    _sem_restritos(r.json())

    # Qualquer um dos campos restritos (com valor ou null): recusa o pedido
    # inteiro, sem mudar nada — nem o nome que veio junto.
    for corpo in (
        {"commission": "0.20"},
        {"kit_number": 3, "name": "outro nome"},
        {"margin1": None},
        {"shipping3": "0"},
        {"margin5": "0.5", "shipping5": "1"},
    ):
        r = await client.patch(f"/api/pricing/accounts/{kia_id}", json=corpo)
        assert r.status_code == 403, (corpo, r.text)
        assert r.json()["detail"]["code"] == "campos_so_admin"

    # Margem própria do Catálogo ML também é do ADM.
    r = await client.patch(
        f"/api/pricing/accounts/{cenario.catalogo.id}", json={"margin1": "0.10"}
    )
    assert r.status_code == 403, r.text

    # Ligar/desligar o catálogo devolve as duas contas sem os números.
    r = await client.post(f"/api/pricing/accounts/{kia_id}/catalogo", json={"ativo": True})
    assert r.status_code == 200, r.text
    _sem_restritos(r.json()["conta"])
    _sem_restritos(r.json()["conta_catalogo"])

    # Conta nova: com kit/comissão → 403; sem → cria e a resposta sai sem eles.
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": "nova", "platform": "shopee", "department": "celular", "commission": "0.1"},
    )
    assert r.status_code == 403, r.text
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": "nova", "platform": "shopee", "department": "celular"},
    )
    assert r.status_code == 201, r.text
    _sem_restritos(r.json())

    for linha in (cenario.kia, cenario.catalogo):
        await db.refresh(linha)
    kia = cenario.kia
    assert kia.name == "kia nova"
    assert kia.kit_number == 2
    assert kia.commission == Decimal("0.10")
    assert (kia.margin1, kia.shipping1) == (Decimal("0.20"), Decimal("5.00"))
    assert (kia.margin3, kia.shipping3) == (Decimal("0.30"), Decimal("10.00"))
    assert (kia.margin5, kia.shipping5) == (None, None)
    assert cenario.catalogo.margin1 == Decimal("0.40")

    # O ADM continua mudando como hoje.
    auth_as(cenario.admin)
    r = await client.patch(f"/api/pricing/accounts/{kia_id}", json={"commission": "0.12"})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["commission"]) == Decimal("0.12")
    assert r.json()["kit_number"] == 2


async def test_enviar_de_quem_nao_e_adm_sai_sem_os_numeros_da_conta(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, make_user, cenario
):
    """Revisão M-7..M-12: o Enviar devolvia as entradas do cálculo (kit,
    comissão, margem e frete) em `payload.inputs`/`payload.calc` e o Enviar
    em lote as gravava no job que o criador lê. Quem não é ADM recebe o
    resto (código, detalhe, bloqueio, anúncios); o ADM, tudo como antes."""
    from app.models import BackgroundJob, BackgroundJobStatus, BackgroundJobType
    from app.services.pricing.batch import run_push_prices_batch
    from app.services.pricing.push import payload_para

    enviar_tabela = {"tabela_precos": {"view": True, "edit": True, "delete": False}}
    enviador = await make_user(role=UserRole.USER, permissions={**PERM_VENDEDOR, **enviar_tabela})
    # Sem custo nem preço de catálogo: o cálculo para antes do marketplace.
    sem_custo = PricingProduct(
        user_id=cenario.admin.id, sku="z999", name="Sem custo",
        segment_id=cenario.a003.segment_id,
    )
    db.add(sem_custo)
    await db.commit()
    kit = {"pricing_account_id": str(cenario.kia.id), "pricing_product_id": str(sem_custo.id)}
    cat = {"pricing_account_id": str(cenario.catalogo.id), "pricing_product_id": str(sem_custo.id)}

    async def enviar(item: dict) -> dict:
        r = await client.post("/api/pricing/push", json={"items": [item]})
        assert r.status_code == 200, r.text
        return r.json()["results"][0]

    auth_as(enviador)
    r = await enviar(kit)
    assert (r["code"], r["payload"]) == ("missing_inputs", None)
    r = await enviar(cat)
    assert (r["code"], r["payload"]) == ("bloqueado", {"bloqueio": "sem_preco_catalogo"})

    # O envio que chega ao marketplace leva "calc" junto dos anúncios.
    envio = {"calc": {"commission": "0.1000"}, "links": [{"externalId": "MLB1"}], "pending": True}
    assert payload_para(enviador, envio) == {"links": [{"externalId": "MLB1"}], "pending": True}
    assert payload_para(cenario.admin, envio) == envio

    # Enviar em lote: o job que o criador lê também sai sem os números.
    job = BackgroundJob(
        type=BackgroundJobType.PUSH_PRICES_BATCH,
        status=BackgroundJobStatus.PENDING,
        created_by=enviador.id,
        payload={"count": 2},
        total=2,
    )
    db.add(job)
    await db.commit()
    await run_push_prices_batch(
        db, job_id=job.id, user_id=enviador.id, items=[kit, cat], notify_telegram=False
    )
    await db.refresh(job)
    assert job.result["summary"]["failed"] == 2
    assert [d["payload"] for d in job.result["details"]] == [{}, {"bloqueio": "sem_preco_catalogo"}]

    # O ADM continua recebendo as entradas.
    auth_as(cenario.admin)
    r = await enviar(kit)
    assert r["payload"]["inputs"]["commission"] == "0.1000"
    r = await enviar(cat)
    assert r["payload"]["inputs"]["margem_propria"] is True
