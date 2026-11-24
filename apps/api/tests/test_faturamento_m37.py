"""Faturamento M-37 (24/11/2026, prévia aprovada pelo usuário em 24/11): a
coluna Margem mostra a margem real da Conciliação —
(Σ Sobrou de TODOS os pedidos − Ads) ÷ Σ custo.

Cobre cada regra do pedido:
  * com Sobrou (pago ou cancelado): o `sobrou`; sem Sobrou e com valor
    previsto: venda_liquido − custo − reembolso − venda_frete_pago − imposto,
    em qualquer situação do Bling (Aguardando Devolução, Manutenção…);
    "Venda não lida" fora da conta (nem sobrou nem custo), só na contagem;
    "fora da loja" fora de tudo; custo vazio = 0;
  * período pela data do pedido no dia de Brasília, nos dias do filtro;
  * cada pedido vai para a loja do Bling DELE (bling_id), nunca pelo nome e
    sem contar uma vez por item; pedido sem linha no Bling fica de fora;
  * Ads = SÓ a parte de Ads do Investimento da M-36 (os afiliados não saem de
    novo), do Ads por dia do Marketing ou da Conferência; sem dado = 0;
  * "—": loja fora da Conciliação (ou conta desligada) e período que começa
    antes do início da Conciliação (24/10/2026); a loja com conta e sem
    pedido no período vem com 0 pedidos (o Ads dela conta no Total);
  * a órfã "Sem cadastro" (Temu, pela loja do Bling da conta) tem a dela;
  * só administrador recebe o campo — e a Conciliação nem é lida para os outros.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    ConciliacaoLoja,
    ConciliacaoPedido,
    Integration,
    IntegrationPlatform,
    MarketingAccount,
    MarketingGastoDia,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.models.conferencia_shopee import (
    ConferenciaShopeeColeta,
    ConferenciaShopeeConta,
    ConferenciaShopeeExecucao,
)
from app.services import faturamento_margem as margem_svc

PERM = {"faturamento": {"view": True, "edit": False, "delete": False}}
NOVEMBRO = {"start": "2026-11-01T00:00:00", "end": "2026-12-01T00:00:00"}
OUTUBRO = {"start": "2026-10-01T00:00:00", "end": "2026-11-01T00:00:00"}
D = Decimal


@pytest_asyncio.fixture(autouse=True)
async def _limpa_marketing(db: AsyncSession):
    """marketing_accounts não está na limpeza do conftest (o gasto por dia vai
    em CASCADE com a conta). A Conciliação vai em CASCADE com a integração."""

    async def wipe():
        await db.execute(text("DELETE FROM marketing_accounts"))
        await db.commit()

    await wipe()
    yield
    await wipe()


async def _usuario(db: AsyncSession, *, role: UserRole = UserRole.ADMIN) -> User:
    email = f"m37-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=role,
        status=UserStatus.ACTIVE,
        permissions=PERM,
        sales_teams=None,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _brt(dia: str, hora: int = 12) -> datetime:
    """Meio-dia de Brasília (15:00 UTC) do dia."""
    return datetime.fromisoformat(f"{dia}T{hora:02d}:00:00-03:00")


class Cena:
    def __init__(self, db: AsyncSession, dono: User):
        self.db = db
        self.dono = dono
        self.n = 0

    async def integ(self, nome: str, plataforma: IntegrationPlatform) -> Integration:
        i = Integration(
            user_id=self.dono.id, platform=plataforma, name=nome, credentials=b"x", status="active"
        )
        self.db.add(i)
        await self.db.flush()
        return i

    async def loja(self, nome, plataforma, bling, integ=None) -> StoreInfo:
        s = StoreInfo(
            user_id=self.dono.id,
            platform=plataforma,
            account_name=nome,
            bling_store_id=bling,
            sales_team=101,
            commercial_team=1,
            integration_id=integ.id if integ is not None else None,
        )
        self.db.add(s)
        await self.db.flush()
        return s

    def conciliacao(self, integ: Integration, *, ativa=True, bling_loja=None, inicio=None):
        self.db.add(
            ConciliacaoLoja(
                integration_id=integ.id,
                platform=integ.platform,
                conta=integ.name,
                ativa=ativa,
                inicio=inicio or date(2026, 10, 24),
                bling_loja=bling_loja,
            )
        )

    def pedido(
        self,
        integ: Integration,
        quando: datetime,
        *,
        loja_bling: str | None,
        situacao: str = "bateu",
        situacao_bling: str = "Atendido",
        sobrou=None,
        liquido=None,
        custo=None,
        reembolso=None,
        frete_pago=None,
        imposto=None,
        itens: int = 1,
        faturavel: bool = False,
    ) -> None:
        """Um pedido da Conciliação e as linhas dele no Bling (`loja_bling`
        None = sem linha no Bling). `faturavel`: Entregue no Bling (entra no
        Faturamento; os outros, Atendido, não contam lá)."""
        self.n += 1
        bling_id = 880000 + self.n

        def dec(v):
            return None if v is None else D(str(v))

        self.db.add(
            ConciliacaoPedido(
                integration_id=integ.id,
                platform=integ.platform,
                pedido_bling=str(self.n),
                bling_id=bling_id,
                conta=integ.name,
                data_pedido=quando,
                situacao_bling=situacao_bling,
                situacao=situacao,
                sobrou=dec(sobrou),
                venda_liquido=dec(liquido),
                custo=dec(custo),
                reembolso=dec(reembolso),
                venda_frete_pago=dec(frete_pago),
                imposto=dec(imposto),
            )
        )
        if loja_bling is None:
            return
        for item in range(itens):
            self.db.add(
                BlingOrder(
                    bling_id=bling_id,
                    numero=str(self.n),
                    item_codigo=f"sku-{item}",
                    item_index=item,
                    situacao="83953" if faturavel else "9",
                    total=D("100.00"),
                    data=quando,
                    loja=loja_bling,
                )
            )

    async def ads(self, nome, plataforma, dias, integ=None) -> MarketingAccount:
        """Conta de Ads com o gasto por dia [(dia, ads, afiliados)]."""
        conta = MarketingAccount(
            integration_id=integ.id if integ is not None else None,
            name=nome,
            platform=plataforma,
            department="geral",
            acos_target=7.0,
            agent_enabled=False,
        )
        self.db.add(conta)
        await self.db.flush()
        for dia, ads, afiliados in dias:
            self.db.add(
                MarketingGastoDia(
                    marketing_account_id=conta.id,
                    dia=dia,
                    gasto_ads=D(ads),
                    gasto_afiliados=D(afiliados) if afiliados is not None else None,
                    origem=f"{plataforma}_ads",
                )
            )
        return conta


async def _novembro(db: AsyncSession) -> tuple[Cena, dict]:
    """A cena de nov/2026 (os nomes dos exemplos do pedido):

    - mega Shopee (Bling 3001): um pedido de cada tipo; Ads da Shopee guardado
      por dia COM afiliados (que não saem da Margem);
    - outra Shopee (3005, sem conta própria): recebe o pedido da conta da
      mega cuja linha no Bling é dela — a ligação é pelo pedido do Bling;
    - marquezini ML (3002): os números do exemplo (sobrou 18.336,38, custo
      53.889,20, Ads por dia 7.442,49 → 20,2%);
    - kfa Amazon (3003): sem Ads (conta 0) → 4,1%;
    - atlas ML (3004): conta da Conciliação DESLIGADA → "—";
    - parada ML (3006): conta na Conciliação sem pedido em novembro, com Ads;
    - Temu (3009): sem cadastro na tela Lojas; a conta da Conciliação traz a
      loja do Bling escrita nela (`bling_loja`) → a linha "Sem cadastro"."""
    dono = await _usuario(db)
    c = Cena(db, dono)
    lj: dict = {}
    i_mega = await c.integ("mega", IntegrationPlatform.SHOPEE)
    i_marq = await c.integ("marquezini", IntegrationPlatform.ML)
    i_kfa = await c.integ("kfa", IntegrationPlatform.AMAZON)
    i_atlas = await c.integ("atlas", IntegrationPlatform.ML)
    i_parada = await c.integ("parada", IntegrationPlatform.ML)
    i_temu = await c.integ("ancora temu", IntegrationPlatform.TEMU)
    lj["mega"] = await c.loja("mega", "shopee", "3001", i_mega)
    lj["outra"] = await c.loja("outra", "shopee", "3005")
    lj["marquezini"] = await c.loja("marquezini", "ml", "3002", i_marq)
    lj["kfa"] = await c.loja("kfa", "amazon", "3003", i_kfa)
    lj["atlas"] = await c.loja("atlas", "ml", "3004", i_atlas)
    lj["parada"] = await c.loja("parada", "ml", "3006", i_parada)
    # Mesmo nome da mega, outra plataforma e outra loja do Bling: nada da
    # Conciliação da mega vem para cá (o nome não liga nada).
    lj["mega_ml"] = await c.loja("mega", "ml", "3007")
    for integ in (i_mega, i_marq, i_kfa, i_parada):
        c.conciliacao(integ)
    c.conciliacao(i_atlas, ativa=False)
    c.conciliacao(i_temu, bling_loja="3009")
    await db.flush()

    nov = _brt("2026-11-10")
    p = c.pedido
    # mega — novembro. Sobrou = 100 − 20 − 5 + 170 − 20 + 60 + 12 + 7 = 304;
    # custo = 200 + 50 + 0 + 300 + 100 + 0 + 10 + 3 = 663.
    p(i_mega, nov, loja_bling="3001", sobrou=100, liquido=300, custo=200, itens=2, faturavel=True)
    p(i_mega, nov, loja_bling="3001", situacao="recebido_a_menos", sobrou=-20, liquido=40, custo=50)
    p(i_mega, nov, loja_bling="3001", situacao="cancelado", situacao_bling="Cancelado",
      sobrou=-5, liquido=0, custo=0)  # fmt: skip
    # Aguardando repasse: 500 − 300 − 10 − 15 − 5 = 170 (previsto, conta nova).
    p(i_mega, nov, loja_bling="3001", situacao="aguardando", situacao_bling="Em andamento",
      liquido=500, custo=300, reembolso=10, frete_pago=15, imposto=5)  # fmt: skip
    # Vencido em Aguardando Devolução também conta: 80 − 100 = −20.
    p(i_mega, nov, loja_bling="3001", situacao="vencido", situacao_bling="Aguardando Devolução",
      liquido=80, custo=100)  # fmt: skip
    # Manutenção no Bling (a Conciliação já zerou o custo): 60 − 0 = 60.
    p(i_mega, nov, loja_bling="3001", situacao="aguardando", situacao_bling="Manutenção",
      liquido=60, custo=0)  # fmt: skip
    # Venda não lida: fora da conta (o custo também), só na contagem.
    p(i_mega, nov, loja_bling="3001", situacao="sem_venda", custo=400)
    # "Venda não lida" com dinheiro já tem Sobrou: entra como pago.
    p(i_mega, nov, loja_bling="3001", situacao="sem_venda", sobrou=12, custo=10)
    # Saiu da loja no Bling: fora de tudo.
    p(i_mega, nov, loja_bling="3001", situacao="fora_da_loja", sobrou=1000, liquido=1000, custo=1)
    # Data do pedido no dia de Brasília: 01/11 01:00 UTC é 31/10 (fora de
    # novembro); 01/12 02:00 UTC é 30/11 (dentro).
    p(i_mega, datetime(2026, 11, 1, 1, tzinfo=UTC), loja_bling="3001", sobrou=77, custo=7)
    p(i_mega, datetime(2026, 12, 1, 2, tzinfo=UTC), loja_bling="3001", sobrou=7, custo=3)
    p(i_mega, _brt("2026-12-02"), loja_bling="3001", sobrou=999, custo=1)  # dezembro
    p(i_mega, _brt("2026-10-25"), loja_bling="3001", sobrou=10, liquido=110, custo=100)  # outubro
    # Pedido da conta da mega cuja linha no Bling é da loja "outra".
    p(i_mega, nov, loja_bling="3005", sobrou=50, custo=25, faturavel=True)
    # Pedido da Conciliação sem linha no Bling: sem loja, fica de fora.
    p(i_mega, nov, loja_bling=None, sobrou=5000, custo=1)
    await c.ads("Mega", "shopee", [
        (date(2026, 11, 5), "20.00", "999.00"),
        (date(2026, 11, 20), "10.50", None),
        (date(2026, 12, 1), "5000.00", None),  # fora de novembro
    ])  # fmt: skip

    # marquezini — o exemplo do pedido.
    p(i_marq, nov, loja_bling="3002", sobrou="8336.38", liquido="30000", custo="23889.20",
      faturavel=True)  # fmt: skip
    p(i_marq, nov, loja_bling="3002", situacao="aguardando", liquido="40000.00", custo="30000.00")
    await c.ads(
        "marquezini",
        "ml",
        [(date(2026, 11, 5), "3000.00", None), (date(2026, 11, 20), "4442.49", None)],
        integ=i_marq,
    )
    # kfa — o exemplo do pedido, sem Ads.
    p(i_kfa, nov, loja_bling="3003", sobrou="685.71", liquido="17000", custo="16550.90")
    # atlas — conta desligada na Conciliação: o pedido não entra.
    p(i_atlas, nov, loja_bling="3004", sobrou=500, custo=100, faturavel=True)
    await c.ads("atlas", "ml", [(date(2026, 11, 5), "80.00", None)], integ=i_atlas)
    # parada — sem pedido em novembro (só em outubro), com Ads.
    p(i_parada, _brt("2026-10-30"), loja_bling="3006", sobrou=1, custo=1)
    await c.ads("parada", "ml", [(date(2026, 11, 7), "50.00", None)], integ=i_parada)
    # Temu sem cadastro: a linha "Sem cadastro (3009)" do Faturamento.
    p(i_temu, nov, loja_bling="3009", sobrou=40, liquido=90, custo=20, faturavel=True)
    await db.commit()
    return c, lj


def _por_loja(body: dict) -> dict[str, dict]:
    return {it["store_id"]: it for it in body["itens"]}


@pytest.mark.asyncio
async def test_margem_real_de_novembro(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    _, lj = await _novembro(db)
    auth_as(await _usuario(db))
    r = await client.get("/api/faturamento", params=NOVEMBRO)
    assert r.status_code == 200, r.text
    linhas = _por_loja(r.json())

    mega = linhas[str(lj["mega"].id)]
    # O Investimento (M-36) tem afiliados + Ads; a Margem desconta SÓ o Ads.
    assert (mega["investimento_afiliados"], mega["investimento_ads"]) == (999.0, 30.5)
    m = mega["margem"]
    assert m["valor"] == pytest.approx((304 - 30.5) / 663)
    assert {k: v for k, v in m.items() if k != "valor"} == {
        "sobrou": 304.0,
        "ads": 30.5,
        "ads_com_dado": True,
        "ads_ate": None,
        "custo": 663.0,
        "pedidos": 9,
        "pagos": 4,
        "previstos": 3,
        "cancelados": 1,
        "sem_valor": 1,
        "inicio": "2026-10-24",
        "antes_do_inicio": False,
    }

    # O pedido vai para a loja do Bling dele: a "outra" recebe o da conta da
    # mega; a "mega" do ML (mesmo nome) não recebe nada.
    outra = linhas[str(lj["outra"].id)]["margem"]
    assert (outra["sobrou"], outra["custo"], outra["pagos"], outra["pedidos"]) == (50.0, 25.0, 1, 1)
    assert (outra["ads"], outra["ads_com_dado"], outra["valor"]) == (0.0, False, 2.0)
    assert linhas[str(lj["mega_ml"].id)]["margem"] is None

    # Os exemplos do pedido.
    marq = linhas[str(lj["marquezini"].id)]["margem"]
    assert (marq["sobrou"], marq["ads"], marq["custo"]) == (18336.38, 7442.49, 53889.2)
    assert round(marq["valor"] * 100, 1) == 20.2
    assert (marq["pagos"], marq["previstos"]) == (1, 1)
    kfa = linhas[str(lj["kfa"].id)]["margem"]
    assert (kfa["sobrou"], kfa["ads"], kfa["ads_com_dado"], kfa["custo"]) == (
        685.71,
        0.0,
        False,
        16550.9,
    )
    assert round(kfa["valor"] * 100, 1) == 4.1

    # Conta desligada na Conciliação = fora dela: "—", mesmo com Ads.
    assert linhas[str(lj["atlas"].id)]["margem"] is None
    assert linhas[str(lj["atlas"].id)]["investimento_ads"] == 80.0

    # Conta na Conciliação sem pedido no período: 0 pedidos, sem valor; o Ads
    # vem junto para o Total descontar.
    parada = linhas[str(lj["parada"].id)]["margem"]
    assert (parada["pedidos"], parada["custo"], parada["valor"]) == (0, 0.0, None)
    assert (parada["ads"], parada["ads_com_dado"], parada["antes_do_inicio"]) == (50.0, True, False)

    # A órfã da Temu (pela loja do Bling escrita na conta da Conciliação).
    temu = linhas["3009"]
    assert temu["loja"] == "Sem cadastro (3009)"
    assert (temu["margem"]["sobrou"], temu["margem"]["custo"], temu["margem"]["valor"]) == (
        40.0,
        20.0,
        2.0,
    )
    assert temu["margem"]["ads_com_dado"] is False


@pytest.mark.asyncio
async def test_periodo_antes_do_inicio_da_conciliacao_fica_sem_margem(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """Item 6: período anterior a 24/10/2026 = "—". Outubro começa antes do
    início (a Conciliação só tem 24–31/10): "—", com o início para o balão."""
    _, lj = await _novembro(db)
    auth_as(await _usuario(db))
    linhas = _por_loja((await client.get("/api/faturamento", params=OUTUBRO)).json())
    mega = linhas[str(lj["mega"].id)]["margem"]
    assert (mega["valor"], mega["antes_do_inicio"], mega["inicio"]) == (None, True, "2026-10-24")
    parada = linhas[str(lj["parada"].id)]["margem"]
    assert (parada["valor"], parada["antes_do_inicio"]) == (None, True)
    assert linhas[str(lj["atlas"].id)]["margem"] is None

    # Setembro inteiro: as lojas com conta ficam "—" pelo início.
    setembro = {"start": "2026-09-01T00:00:00", "end": "2026-10-01T00:00:00"}
    linhas = _por_loja((await client.get("/api/faturamento", params=setembro)).json())
    assert linhas[str(lj["mega"].id)]["margem"]["antes_do_inicio"] is True
    assert linhas[str(lj["mega"].id)]["margem"]["pedidos"] == 0
    assert all(it["margem"] is None or it["margem"]["valor"] is None for it in linhas.values())

    # Do início em diante vale: 24/10–31/10 (personalizado, até 23:59:59) tem o
    # pedido de 25/10 e o de 31/10 às 22:00 de Brasília. Sem Ads em outubro.
    fim_de_outubro = {"start": "2026-10-24T00:00:00", "end": "2026-10-31T23:59:59"}
    linhas = _por_loja((await client.get("/api/faturamento", params=fim_de_outubro)).json())
    mega = linhas[str(lj["mega"].id)]["margem"]
    assert (mega["sobrou"], mega["custo"], mega["pagos"], mega["antes_do_inicio"]) == (
        87.0,
        107.0,
        2,
        False,
    )
    assert mega["valor"] == pytest.approx(87 / 107)
    assert (mega["ads"], mega["ads_com_dado"]) == (0.0, False)


@pytest.mark.asyncio
async def test_ads_da_conferencia_sem_os_afiliados(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """Shopee pela Conferência (M-36): Investimento = afiliados + Ads da
    semana; a Margem desconta só o Ads e avisa até quando vão as semanas."""
    dono = await _usuario(db)
    c = Cena(db, dono)
    i_vortan = await c.integ("vortan", IntegrationPlatform.SHOPEE)
    vortan = await c.loja("vortan", "shopee", "3010", i_vortan)
    c.conciliacao(i_vortan)
    conta = ConferenciaShopeeConta(
        plataforma="shopee", adspower_user_id="k1d0901", nome="Vortan", grupo="mala",
        conta_key="vortan",
    )  # fmt: skip
    db.add(conta)
    await db.flush()
    criado = datetime(2026, 11, 9, 16, 30, tzinfo=UTC)
    ex = ConferenciaShopeeExecucao(
        plataforma="shopee",
        tipo="semanal",
        origem="agenda",
        semanas=[{"inicio": "2026-11-01", "fim": "2026-11-07"}],
        afiliados_ate=date(2026, 11, 7),
        esperar_afiliados_ate=criado,
        corte=criado,
        prazo=criado,
        status="pronto",
        criado_em=criado,
    )
    db.add(ex)
    await db.flush()
    semana = {
        "inicio": "2026-11-01",
        "fim": "2026-11-07",
        "afiliados": {"comissao": 500.0, "vendas": 5000.0, "pedidos": 3},
        "ads": {"gasto": 40.0, "vendas": 320.0, "impressoes": 1000, "cliques": 50},
        "vendas": {"valor": 1000.0, "pedidos": 10},
    }
    db.add(
        ConferenciaShopeeColeta(
            execucao_id=ex.id,
            conta_id=conta.id,
            adspower_user_id=conta.adspower_user_id,
            nome=conta.nome,
            grupo=conta.grupo,
            status="ok",
            dados={"versao": 1, "semanas": [semana]},
            concluido_em=criado + timedelta(hours=1),
        )
    )
    c.pedido(i_vortan, _brt("2026-11-03"), loja_bling="3010", sobrou=300, custo=1000)
    await db.commit()
    auth_as(await _usuario(db))
    r = await client.get("/api/faturamento", params=NOVEMBRO)
    assert r.status_code == 200, r.text
    linha = _por_loja(r.json())[str(vortan.id)]
    assert (linha["investimento_afiliados"], linha["investimento_ads"]) == (500.0, 40.0)
    m = linha["margem"]
    assert (m["sobrou"], m["ads"], m["custo"]) == (300.0, 40.0, 1000.0)
    assert m["valor"] == pytest.approx(0.26), "(300 − 40) ÷ 1.000: os 500 de afiliados não saem"
    assert m["ads_ate"] == "2026-11-07", "as semanas fechadas da Conferência vão até 07/11"


@pytest.mark.asyncio
@pytest.mark.parametrize("equipes", [None, [101]])
async def test_margem_so_para_o_administrador_e_sem_ler_a_conciliacao(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    monkeypatch: pytest.MonkeyPatch,
    equipes: list[int] | None,
):
    await _novembro(db)

    async def nao_pode(*_a, **_k):
        raise AssertionError("a Conciliação não é lida para quem não é ADM")

    monkeypatch.setattr(margem_svc, "pedidos_das_lojas", nao_pode)
    u = await _usuario(db, role=UserRole.USER)
    u.sales_teams = equipes
    await db.commit()
    auth_as(u)
    r = await client.get("/api/faturamento", params=NOVEMBRO)
    assert r.status_code == 200, r.text
    assert r.json()["itens"]
    for it in r.json()["itens"]:
        assert "margem" not in it, it
    assert "sobrou" not in r.text


def test_conta_da_margem():
    """(Σ sobrou − Ads) ÷ Σ custo; sem custo = None; Ads sem dado = 0;
    período antes do início = None."""
    ped = margem_svc.PedidosDaLoja(
        sobrou=D("100"), custo=D("400"), pagos=1, inicio=date(2026, 10, 24)
    )
    m = margem_svc.margem(ped, D("20"), date(2026, 11, 1))
    assert (m.valor, m.ads, m.ads_com_dado, m.antes_do_inicio) == (D("0.2"), D("20"), True, False)
    m = margem_svc.margem(ped, None, date(2026, 11, 1))
    assert (m.valor, m.ads, m.ads_com_dado) == (D("0.25"), D("0"), False)
    # Negativa: o Ads maior que o sobrou.
    assert margem_svc.margem(ped, D("140"), date(2026, 11, 1)).valor == D("-0.1")
    assert margem_svc.margem(ped, None, date(2026, 10, 23)).valor is None
    assert margem_svc.margem(ped, None, date(2026, 10, 24)).antes_do_inicio is False
    so_cancelado = margem_svc.PedidosDaLoja(sobrou=D("-5"), custo=D("0"), cancelados=1)
    assert margem_svc.margem(so_cancelado, D("1"), date(2026, 11, 1)).valor is None
    assert so_cancelado.pedidos == 1
