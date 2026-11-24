"""Faturamento M-36 (24/11/2026): coluna Investimento = afiliados + Ads do
Marketing, por conta (prévia aprovada pelo dono em 24/11).

Cobre:
  * Shopee pelas coletas da Conferência (afiliados.comissao + ads.gasto), com
    os MESMOS números da tela da Conferência: a conta de Celular (linhas
    Celular + Eletro) soma o total da conta;
  * só a rodada SEMANAL mais recente de cada dia de cada conta: a parcial e a
    cancelada ficam de fora; coleta que falhou nela (deslogada…), seção null ou
    semana que não veio deixam a semana "—", como a tela da Conferência, e NÃO
    voltam para a rodada anterior (correção de 24/11/2026); rodada sem coleta
    da conta ou com a coleta ainda na fila não decide nada;
  * semana que cruza o mês entra pelos dias (rateio); o dia a dia da coleta
    (item 6, campo `dias`) vale quando cobre todos os dias da semana no período,
    pedaço a pedaço; semanas segunda–domingo (até 30/10) e domingo–sábado não
    contam o mesmo dia duas vezes;
  * ML: o Ads por dia do Marketing vence o da Conferência; afiliados do ML e
    Ads da Amazon têm estado na Conferência → sem número;
  * a conta da Conferência vai para a loja pela chave interna (bling_loja_id,
    conta_key, integração), nunca pelo nome da tela;
  * sem coleta com número: "—" (None), nunca zero;
  * os pedaços `investimento_afiliados` / `investimento_ads` e o
    `investimento_ate` (semana corrente entra quando fechar) na resposta.
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
from app.services.conferencia_shopee import fila
from app.services.faturamento_conferencia import _Semana, valor_nos_dias

PERM = {"faturamento": {"view": True, "edit": False, "delete": False}}
NOVEMBRO = {"start": "2026-11-01T00:00:00", "end": "2026-12-01T00:00:00"}

# Semanas domingo–sábado de nov/2026 (01/11 é domingo).
W_OUT2 = ("2026-10-18", "2026-10-24")
W_OUT = ("2026-10-25", "2026-10-31")
W1 = ("2026-11-01", "2026-11-07")
W2 = ("2026-11-08", "2026-11-14")
W3 = ("2026-11-15", "2026-11-21")
W4 = ("2026-11-22", "2026-11-28")
W5 = ("2026-11-29", "2026-12-05")  # cruza o mês


@pytest_asyncio.fixture(autouse=True)
async def _limpa_marketing(db: AsyncSession):
    async def wipe():
        await db.execute(text("DELETE FROM marketing_accounts"))
        await db.commit()

    await wipe()
    yield
    await wipe()


async def _usuario(db: AsyncSession, *, role: UserRole = UserRole.ADMIN) -> User:
    email = f"m36-{uuid.uuid4().hex[:6]}@davinci-test.com"
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


def _semana(w, afiliados=None, ads=None, **extra) -> dict:
    s = {"inicio": w[0], "fim": w[1]}
    if afiliados is not None:
        s["afiliados"] = {"comissao": afiliados, "vendas": afiliados * 10, "pedidos": 3}
    if ads is not None:
        s["ads"] = {"gasto": ads, "vendas": ads * 8, "impressoes": 1000, "cliques": 50}
    s["vendas"] = {"valor": 1000.0, "pedidos": 10}
    s.update(extra)
    return s


def _dados(*semanas) -> dict:
    return {"versao": 1, "semanas": list(semanas)}


class Cena:
    """Lojas, contas da Conferência e rodadas; `rodada` cria uma execução com
    as coletas [(conta, status, dados)]."""

    def __init__(self, db: AsyncSession, dono: User):
        self.db = db
        self.dono = dono
        self.n = 0

    async def loja(self, nome, plataforma, bling, integ=None, arquivada=False) -> StoreInfo:
        s = StoreInfo(
            user_id=self.dono.id,
            platform=plataforma,
            account_name=nome,
            bling_store_id=bling,
            sales_team=101,
            commercial_team=1,
            integration_id=integ,
            archived_at=datetime(2026, 1, 1, tzinfo=UTC) if arquivada else None,
        )
        self.db.add(s)
        await self.db.flush()
        return s

    async def integ(self, nome) -> Integration:
        i = Integration(
            user_id=self.dono.id,
            platform=IntegrationPlatform.ML,
            name=nome,
            credentials=b"x",
            status="active",
        )
        self.db.add(i)
        await self.db.flush()
        return i

    async def conta(self, nome, plataforma="shopee", grupo="mala", **extra):
        self.n += 1
        c = ConferenciaShopeeConta(
            plataforma=plataforma,
            adspower_user_id=f"k1d{self.n:04d}" if plataforma == "shopee" else None,
            nome=nome,
            grupo=grupo,
            **extra,
        )
        self.db.add(c)
        await self.db.flush()
        return c

    async def rodada(
        self, criado_em, semanas, coletas, *, plataforma="shopee", tipo="semanal", status="pronto"
    ) -> ConferenciaShopeeExecucao:
        ex = ConferenciaShopeeExecucao(
            plataforma=plataforma,
            tipo=tipo,
            origem="agenda",
            semanas=[{"inicio": i, "fim": f} for i, f in semanas],
            afiliados_ate=date.fromisoformat(semanas[0][1]),
            esperar_afiliados_ate=criado_em,
            corte=criado_em,
            prazo=criado_em,
            status=status,
            criado_em=criado_em,
        )
        self.db.add(ex)
        await self.db.flush()
        for conta, st, dados in coletas:
            self.db.add(
                ConferenciaShopeeColeta(
                    execucao_id=ex.id,
                    conta_id=conta.id,
                    adspower_user_id=conta.adspower_user_id,
                    nome=conta.nome,
                    grupo=conta.grupo,
                    status=st,
                    dados=dados,
                    concluido_em=criado_em + timedelta(hours=1),
                )
            )
        await self.db.flush()
        return ex


def _quando(dia: str) -> datetime:
    """13:30 em Brasília (16:30 UTC), a hora da agenda."""
    return datetime.fromisoformat(f"{dia}T16:30:00+00:00")


def _ads_itens() -> list[dict]:
    """Um item eletro pelo título (Air Fryer) e um de celular."""
    return [
        {"item_id": "900", "nome": "Capinha", "tipo": "product_manual", "gasto": 600.0,
         "impressoes": 4000, "cliques": 80, "vendas": 600.0, "pedidos": 4},
        {"item_id": "700", "nome": "Air Fryer 4L", "tipo": "product_manual", "gasto": 400.0,
         "impressoes": 1000, "cliques": 20, "vendas": 300.0, "pedidos": 2},
    ]  # fmt: skip


def _afiliados_itens() -> list[dict]:
    return [
        {"item_id": "900", "nome": "Capinha", "comissao": 1500.0, "vendas": 9000.0,
         "pedidos": 5, "cliques": 30},
        {"item_id": "700", "nome": "Air Fryer 4L", "comissao": 500.0, "vendas": 3000.0,
         "pedidos": 1, "cliques": 10},
    ]  # fmt: skip


async def _novembro(db: AsyncSession) -> tuple[Cena, dict]:
    """A cena de nov/2026 lida em 24/11 (os nomes dos exemplos do pedido):

    - mega Shopee (Celular, com item eletro): rodadas de 16/11 e 23/11; 08–14
      nas duas com números diferentes (a de 23/11 vence). Com a de 23/11:
      afiliados 2000 + 1811,94 + 1900 = 5.711,94 e Ads 1000 + 818,63 + 900 =
      2.718,63 → R$ 8.430,57 (o exemplo do pedido). Uma rodada PARCIAL (24/11)
      e uma CANCELADA (25/11) com números enormes ficam de fora;
    - vortan Shopee: deslogada em 23/11, ok em 16/11 → "—": a rodada mais
      recente manda, como na tela da Conferência de 23/11 (não volta para a
      de 16/11);
    - atv Shopee: a conta da Conferência se chama "mega" na tela, mas a chave
      (conta_key) é "atv": vai para a loja atv, nunca para a mega;
    - victor mei Shopee: só coleta deslogada → "—";
    - semconta Shopee: nenhuma conta → "—";
    - marquezini ML: Ads por dia no Marketing (7.442,49) E Ads na Conferência
      do ML (9.999): vale o por dia; afiliados do ML "não coletado";
    - barbosa ML: sem Ads por dia; a Conferência do ML (pela loja do Bling);
    - kfa Amazon: coleta com número de Ads, mas Ads da Amazon "aguardando
      acesso" → "—"."""
    dono = await _usuario(db)
    c = Cena(db, dono)
    lj = {}
    lj["mega"] = await c.loja("mega", "shopee", "5001")
    lj["vortan"] = await c.loja("Vortan", "shopee", "5002")
    lj["atv"] = await c.loja("atv", "shopee", "5003")
    lj["victor"] = await c.loja("victor mei", "shopee", "5004")
    lj["semconta"] = await c.loja("semconta", "shopee", "5005")
    i_marq = await c.integ("marquezini")
    i_barb = await c.integ("barbosa")
    lj["marquezini"] = await c.loja("marquezini", "ml", "5006", integ=i_marq.id)
    lj["barbosa"] = await c.loja("barbosa", "ml", "5007")
    lj["kfa"] = await c.loja("kfa", "amazon", "5008")

    mega = await c.conta("Mega (tela)", grupo="celular", conta_key="mega")
    vortan = await c.conta("Vortan", conta_key="vortan")
    atv = await c.conta("mega", conta_key="atv")
    victor = await c.conta("victor mei", conta_key="victor mei")
    marq = await c.conta("Marquezini", "ml", "celular", conta_key="x", integration_id=i_marq.id)
    barb = await c.conta("Barbosa", "ml", "celular", conta_key="y", integration_id=i_barb.id,
                         bling_loja_id="5007")  # fmt: skip
    kfa = await c.conta("KFA", "amazon", "celular", conta_key="kfa", bling_loja_id="5008")

    # 16/11: S1 = 08–14 … S4 = 18–24/10.
    await c.rodada(
        _quando("2026-11-16"),
        [W2, W1, W_OUT, W_OUT2],
        [
            (mega, "ok", _dados(_semana(W2, 1500.0, 700.0), _semana(W1, 1900.0, 900.0),
                                _semana(W_OUT, 50.0, 5.0), _semana(W_OUT2, 40.0, 4.0))),
            (vortan, "ok", _dados(_semana(W2, 300.0, 30.0), _semana(W1, 200.0, 20.0),
                                  _semana(W_OUT, 100.0, 10.0))),
            (atv, "ok", _dados(_semana(W2, 11.0, 1.0), _semana(W1, 22.0, 2.0))),
            (victor, "deslogada", None),
        ],
    )  # fmt: skip
    # 23/11: S1 = 15–21 … S4 = 25–31/10. Em 15–21 a mega tem o item eletro.
    ex23 = await c.rodada(
        _quando("2026-11-23"),
        [W3, W2, W1, W_OUT],
        [
            (mega, "ok", _dados(
                _semana(W3, 2000.0, 1000.0, ads_itens=_ads_itens(),
                        afiliados_itens=_afiliados_itens()),
                _semana(W2, 1811.94, 818.63),
                _semana(W1, 1900.0, 900.0),
                _semana(W_OUT, 60.0, 6.0),
            )),
            (vortan, "deslogada", None),
            (atv, "ok", _dados(_semana(W3, 33.0, 3.0), _semana(W2, 12.0, 1.5),
                               _semana(W1, 22.0, 2.0))),
            (victor, "deslogada", None),
        ],
    )  # fmt: skip
    # Parcial do "Gerar agora" (24/11) e semanal cancelada (25/11): fora.
    await c.rodada(
        _quando("2026-11-24"),
        [("2026-11-22", "2026-11-23"), ("2026-11-15", "2026-11-16")],
        [(mega, "ok", _dados(_semana(("2026-11-22", "2026-11-23"), 90000.0, 90000.0),
                             _semana(("2026-11-15", "2026-11-16"), 90000.0, 90000.0)))],
        tipo="parcial",
    )  # fmt: skip
    await c.rodada(
        _quando("2026-11-25"),
        [W3, W2, W1, W_OUT],
        [(mega, "ok", _dados(_semana(W3, 70000.0, 70000.0), _semana(W2, 70000.0, 70000.0)))],
        status="cancelado",
    )  # fmt: skip
    # Conferência do ML e da Amazon (rodadas por plataforma).
    await c.rodada(
        _quando("2026-11-23"),
        [W3, W2, W1, W_OUT],
        [
            (marq, "ok", _dados(_semana(W3, 123.0, 9999.0))),
            (barb, "ok", _dados(_semana(W3, 77.0, 300.0), _semana(W2, None, 200.5),
                                _semana(W1, None, 100.0))),
        ],
        plataforma="ml",
    )  # fmt: skip
    await c.rodada(
        _quando("2026-11-23"),
        [W3, W2, W1, W_OUT],
        [(kfa, "ok", _dados(_semana(W3, None, 50.0)))],
        plataforma="amazon",
    )
    # Ads por dia do Marketing (ML).
    ads_marq = MarketingAccount(
        integration_id=i_marq.id,
        name="marquezini",
        platform="ml",
        department="geral",
        acos_target=7.0,
        agent_enabled=False,
    )
    db.add(ads_marq)
    await db.flush()
    for dia, gasto in ((date(2026, 10, 20), "1.00"), (date(2026, 11, 5), "3000.00"),
                       (date(2026, 11, 20), "4442.49")):  # fmt: skip
        db.add(
            MarketingGastoDia(
                marketing_account_id=ads_marq.id, dia=dia, gasto_ads=Decimal(gasto), origem="ml_ads"
            )
        )
    await db.commit()
    lj["ex23"] = ex23
    return c, lj


def _por_loja(body: dict) -> dict[str, dict]:
    return {it["store_id"]: it for it in body["itens"]}


@pytest.mark.asyncio
async def test_novembro_shopee_pela_conferencia_e_ml_pelo_ads_por_dia(
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
    # O exemplo do pedido; 08–14 da rodada de 23/11 (a de 16/11 perde); a
    # parcial e a cancelada não entram.
    assert mega["investimento_afiliados"] == 5711.94
    assert mega["investimento_ads"] == 2718.63
    assert mega["investimento"] == 8430.57
    # A semana corrente (22–28) ainda não fechou: as semanas vão até 21/11.
    assert mega["investimento_ate"] == "2026-11-21"
    assert mega["investimento_parcial"] is False
    assert mega["investimento_desde"] == "2026-10-18"

    vortan = linhas[str(lj["vortan"].id)]
    # Deslogada em 23/11 (a rodada mais recente): "—" como na tela da
    # Conferência; as semanas da de 16/11 NÃO voltam.
    assert vortan["investimento"] is None
    assert (vortan["investimento_afiliados"], vortan["investimento_ads"]) == (None, None)
    assert vortan["investimento_ate"] is None

    atv = linhas[str(lj["atv"].id)]
    # A conta chamada "mega" na tela, com a chave "atv": vai para a atv.
    assert (atv["investimento_afiliados"], atv["investimento_ads"]) == (67.0, 6.5)

    for nome in ("victor", "semconta", "kfa"):
        linha = linhas[str(lj[nome].id)]
        assert linha["investimento"] is None, f"{nome}: sem dado é '—', nunca zero"
        assert linha["investimento_afiliados"] is None
        assert linha["investimento_ads"] is None

    marq = linhas[str(lj["marquezini"].id)]
    # O Ads por dia do Marketing (mais atual) vence o da Conferência do ML; os
    # afiliados do ML estão "não coletado" na Conferência.
    assert (marq["investimento_afiliados"], marq["investimento_ads"]) == (None, 7442.49)
    assert marq["investimento"] == 7442.49
    assert marq["investimento_ate"] is None
    assert marq["investimento_desde"] == "2026-10-20"

    barbosa = linhas[str(lj["barbosa"].id)]
    # Sem Ads por dia: o Ads da Conferência do ML (ligada pela loja do Bling).
    assert (barbosa["investimento_afiliados"], barbosa["investimento_ads"]) == (None, 600.5)

    # Margem e Investimento por loja: a Margem (M-37) vai usar só o pedaço de Ads.
    total = sum(it["investimento"] or 0 for it in linhas.values())
    assert round(total, 2) == round(8430.57 + 73.5 + 7442.49 + 600.5, 2)


@pytest.mark.asyncio
async def test_celular_e_eletro_somados_batem_com_a_tela_da_conferencia(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """A rodada de 23/11 da mega montada como a tela da Conferência monta: as
    linhas Celular e Eletro da conta, somadas nas 3 semanas de novembro, dão o
    Investimento do Faturamento."""
    _, lj = await _novembro(db)
    rel = await fila.montar(db, lj["ex23"], datetime(2026, 11, 23, 18, tzinfo=UTC))
    linhas_mega = [
        lin for g in rel["grupos"] for lin in g["linhas"] if lin["conta"] == "Mega (tela)"
    ]
    assert sorted(g["chave"] for g in rel["grupos"] for lin in g["linhas"]
                  if lin["conta"] == "Mega (tela)") == ["celular", "eletro"]  # fmt: skip
    # Semana sem a lista de itens: o Eletro fica "—" (None) e o total em Celular.
    afiliados = sum(
        lin["semanas"][i]["invest_afiliados"] or 0 for lin in linhas_mega for i in range(3)
    )
    ads = sum(lin["semanas"][i]["invest_ads"] or 0 for lin in linhas_mega for i in range(3))
    eletro = next(lin for g in rel["grupos"] if g["chave"] == "eletro" for lin in g["linhas"])
    assert eletro["semanas"][0]["invest_ads"] == 400.0, "a linha Eletro existe e tem a parte dela"

    auth_as(await _usuario(db))
    linha = _por_loja((await client.get("/api/faturamento", params=NOVEMBRO)).json())[
        str(lj["mega"].id)
    ]
    assert linha["investimento_afiliados"] == round(afiliados, 2)
    assert linha["investimento_ads"] == round(ads, 2)


@pytest.mark.asyncio
async def test_shopee_inteira_igual_a_soma_das_tres_semanas_da_tela(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """Item 1 (correção de 24/11/2026): a Shopee inteira no Faturamento de
    novembro = a linha Investimento do Geral da tela da Conferência de 23/11
    nas três semanas (15–21, 08–14, 01–07). A vortan, deslogada em 23/11, é
    "—" lá e aqui: o número dela de 16/11 não entra."""
    _, lj = await _novembro(db)
    rel = await fila.montar(db, lj["ex23"], datetime(2026, 11, 23, 18, tzinfo=UTC))
    tela = sum(
        (rel["geral"]["semanas"][i].get(c) or 0)
        for i in range(3)
        for c in ("invest_afiliados", "invest_ads")
    )
    auth_as(await _usuario(db))
    linhas = _por_loja((await client.get("/api/faturamento", params=NOVEMBRO)).json())
    shopee = sum(
        linhas[str(lj[n].id)]["investimento"] or 0
        for n in ("mega", "vortan", "atv", "victor", "semconta")
    )
    assert round(tela, 2) == 8504.07, "mega 8.430,57 + atv 73,50; vortan e victor sem número"
    assert round(shopee, 2) == round(tela, 2)


@pytest.mark.asyncio
async def test_rodada_mais_recente_manda_mesmo_sem_numero(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """Correção de 24/11/2026 (M-36 itens 1, 4 e 5): a rodada semanal mais
    recente em que a conta tem coleta encerrada decide as semanas dela, com
    ou sem número — a anterior não preenche o que a tela mostra "—".

    - victor mei: ok em 09/11 (01–07), deslogado em 16/11 e 23/11 → "—" (e
      não o pedaço 01–07 com "semanas fechadas até 07/11");
    - semads: na de 23/11 (parcial) a seção de Ads de 08–14 veio null → o Ads
      dessa semana fica fora; NÃO volta o de 16/11 (30);
    - faltando: na de 23/11 a coleta só trouxe 15–21 → 01–14 ficam "—" como na
      tela, sem voltar para a de 16/11;
    - fora: nenhuma coleta na de 23/11 (a conta nem estava na rodada) → vale
      a de 16/11;
    - nafila: na rodada de 25/11, ainda coletando, a coleta dela está na fila
      → não decide nada: vale a de 23/11."""
    dono = await _usuario(db)
    c = Cena(db, dono)
    l_victor = await c.loja("victor mei", "shopee", "5104")
    l_fora = await c.loja("fora", "shopee", "5105")
    l_semads = await c.loja("semads", "shopee", "5106")
    l_faltando = await c.loja("faltando", "shopee", "5107")
    l_fila = await c.loja("nafila", "shopee", "5108")
    victor = await c.conta("victor mei", conta_key="victor mei")
    fora = await c.conta("fora", conta_key="fora")
    semads = await c.conta("semads", conta_key="semads")
    faltando = await c.conta("faltando", conta_key="faltando")
    nafila = await c.conta("nafila", conta_key="nafila")

    w_out3 = ("2026-10-11", "2026-10-17")  # só a rodada de 09/11 tem esta semana
    await c.rodada(
        _quando("2026-11-09"),
        [W1, W_OUT, W_OUT2, w_out3],
        [(victor, "ok", _dados(_semana(W1, 100.0, 10.0), _semana(W_OUT, 1.0, 1.0),
                               _semana(W_OUT2, 2.0, 0.2), _semana(w_out3, 5.0, 0.5)))],
    )  # fmt: skip
    await c.rodada(
        _quando("2026-11-16"),
        [W2, W1, W_OUT, W_OUT2],
        [
            (victor, "deslogada", None),
            (fora, "ok", _dados(_semana(W2, 20.0, 2.0), _semana(W1, 10.0, 1.0))),
            (semads, "ok", _dados(_semana(W2, 300.0, 30.0), _semana(W1, 200.0, 20.0))),
            (faltando, "ok", _dados(_semana(W2, 40.0, 4.0), _semana(W1, 50.0, 5.0))),
            (nafila, "ok", _dados(_semana(W2, 70.0, 7.0), _semana(W1, 80.0, 8.0))),
        ],
    )  # fmt: skip
    await c.rodada(
        _quando("2026-11-23"),
        [W3, W2, W1, W_OUT],
        [
            (victor, "deslogada", None),
            # 08–14: a seção de Ads veio null (a coleta fica parcial).
            (semads, "parcial", _dados(_semana(W3, 1000.0, 100.0), _semana(W2, 333.0, None),
                                       _semana(W1, 222.0, 22.0))),
            (faltando, "ok", _dados(_semana(W3, 60.0, 6.0))),
            (nafila, "ok", _dados(_semana(W3, 9.0, 0.9), _semana(W2, 7.0, 0.7),
                                  _semana(W1, 8.0, 0.8))),
        ],
    )  # fmt: skip
    ex25 = await c.rodada(
        _quando("2026-11-25"),
        [W3, W2, W1, W_OUT],
        [(nafila, "pendente", None), (semads, "coletando", None)],
        status="coletando",
    )
    await db.commit()
    auth_as(await _usuario(db))
    linhas = _por_loja((await client.get("/api/faturamento", params=NOVEMBRO)).json())
    v, f, s, fa, n = (linhas[str(x.id)] for x in (l_victor, l_fora, l_semads, l_faltando, l_fila))

    assert v["investimento"] is None, "victor mei deslogado na rodada mais recente: '—'"
    assert (v["investimento_afiliados"], v["investimento_ads"]) == (None, None)
    assert v["investimento_ate"] is None

    assert (f["investimento_afiliados"], f["investimento_ads"]) == (30.0, 3.0)
    assert f["investimento_ate"] == "2026-11-14"

    assert s["investimento_afiliados"] == 1555.0  # 1000 + 333 + 222
    assert s["investimento_ads"] == 122.0, "100 + 22: o Ads null de 08–14 não vira o 30 de 16/11"
    assert s["investimento_ate"] == "2026-11-21"

    assert (fa["investimento_afiliados"], fa["investimento_ads"]) == (60.0, 6.0)

    assert (n["investimento_afiliados"], n["investimento_ads"]) == (24.0, 2.4)

    # Quando a rodada de 25/11 fecha com a nafila deslogada, ela passa a "—".
    await db.execute(
        text(
            "UPDATE conferencia_shopee_coleta SET status = 'deslogada' "
            "WHERE execucao_id = :ex AND conta_id = :c"
        ),
        {"ex": ex25.id, "c": nafila.id},
    )
    await db.commit()
    linhas = _por_loja((await client.get("/api/faturamento", params=NOVEMBRO)).json())
    assert linhas[str(l_fila.id)]["investimento"] is None
    # A rodada que falhou decide só as semanas DELA: 11–17/10, que só a de
    # 09/11 tem, continua com o número do victor mei; 18–31/10 (também na de
    # 16/11, deslogada) fica "—".
    outubro = {"start": "2026-10-11T00:00:00", "end": "2026-11-01T00:00:00"}
    linhas = _por_loja((await client.get("/api/faturamento", params=outubro)).json())
    v = linhas[str(l_victor.id)]
    assert (v["investimento_afiliados"], v["investimento_ads"]) == (5.0, 0.5)
    assert v["investimento_ate"] == "2026-10-17"


@pytest.mark.asyncio
async def test_semana_que_cruza_o_mes_e_dia_a_dia(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """29/11–05/12 cruza o mês: sem o dia a dia, nov fica com 2/7 e dez com
    5/7; com o dia a dia completo nos dias do período, os dias exatos; dia
    faltando num pedaço → só aquele pedaço volta para o rateio."""
    dono = await _usuario(db)
    c = Cena(db, dono)
    l_sem = await c.loja("semdias", "shopee", "6001")
    l_dias = await c.loja("comdias", "shopee", "6002")
    l_furo = await c.loja("furo", "shopee", "6003")
    sem = await c.conta("semdias", conta_key="semdias")
    com = await c.conta("comdias", conta_key="comdias")
    furo = await c.conta("furo", conta_key="furo")

    def dias(afiliados, ads):
        return [
            {"dia": (date(2026, 11, 29) + timedelta(days=i)).isoformat(),
             "afiliados": afiliados[i], "ads": ads[i]}
            for i in range(7)
        ]  # fmt: skip

    await c.rodada(
        _quando("2026-12-07"),
        [W5, W4, W3, W2],
        [
            (sem, "ok", _dados(_semana(W5, 700.0, 140.0), _semana(W4, 70.0, 7.0))),
            (com, "ok", _dados(
                _semana(W5, 700.0, 140.0,
                        dias=dias([11.0, 22.0, 100, 100, 100, 100, 267.0],
                                  [1.0, 2.0, 20, 20, 20, 20, 57.0])),
                _semana(W4, 70.0, 7.0),
            )),
            # 30/11 sem afiliados: o pedaço afiliados volta para o rateio; o Ads
            # (completo) fica no dia a dia.
            (furo, "ok", _dados(_semana(
                W5, 700.0, 140.0,
                dias=dias([11.0, None, 100, 100, 100, 100, 267.0],
                          [1.0, 2.0, 20, 20, 20, 20, 57.0]),
            ))),
        ],
    )  # fmt: skip
    await db.commit()
    auth_as(await _usuario(db))

    linhas = _por_loja((await client.get("/api/faturamento", params=NOVEMBRO)).json())
    s, d, f = (linhas[str(x.id)] for x in (l_sem, l_dias, l_furo))
    assert (s["investimento_afiliados"], s["investimento_ads"]) == (270.0, 47.0), "22–28 + 2/7"
    assert (d["investimento_afiliados"], d["investimento_ads"]) == (103.0, 10.0), "dia a dia"
    assert (f["investimento_afiliados"], f["investimento_ads"]) == (200.0, 3.0)
    assert s["investimento_ate"] is None, "as semanas vão até o fim do período"

    dezembro = {"start": "2026-12-01T00:00:00", "end": "2027-01-01T00:00:00"}
    linhas = _por_loja((await client.get("/api/faturamento", params=dezembro)).json())
    s, d = linhas[str(l_sem.id)], linhas[str(l_dias.id)]
    assert (s["investimento_afiliados"], s["investimento_ads"]) == (500.0, 100.0), "5/7"
    assert (d["investimento_afiliados"], d["investimento_ads"]) == (667.0, 137.0)
    assert s["investimento_ate"] == "2026-12-05"
    # Um dia só (personalizado até 23:59:59).
    um_dia = {"start": "2026-11-30T00:00:00", "end": "2026-11-30T23:59:59"}
    linhas = _por_loja((await client.get("/api/faturamento", params=um_dia)).json())
    assert linhas[str(l_dias.id)]["investimento"] == 24.0
    assert linhas[str(l_sem.id)]["investimento"] == 120.0  # (700 + 140) ÷ 7
    # A semana corrente (sem rodada) não tem número: "—".
    corrente = {"start": "2026-12-06T00:00:00", "end": "2026-12-08T00:00:00"}
    linhas = _por_loja((await client.get("/api/faturamento", params=corrente)).json())
    assert linhas[str(l_sem.id)]["investimento"] is None


@pytest.mark.asyncio
async def test_semanas_de_segunda_e_de_domingo_nao_contam_o_mesmo_dia_duas_vezes(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """Até 30/10/2026 as semanas eram segunda–domingo. A rodada nova (domingo–
    sábado) cobre 18–31/10; da velha só sobram 12–17/10 (6 dos 7 dias de 12–18)."""
    dono = await _usuario(db)
    c = Cena(db, dono)
    loja = await c.loja("antiga", "shopee", "7001")
    conta = await c.conta("antiga", conta_key="antiga")
    await c.rodada(
        _quando("2026-10-27"),
        [("2026-10-19", "2026-10-25"), ("2026-10-12", "2026-10-18")],
        [(conta, "ok", _dados(_semana(("2026-10-19", "2026-10-25"), 70.0, 7.0),
                              _semana(("2026-10-12", "2026-10-18"), 700.0, 70.0)))],
    )  # fmt: skip
    await c.rodada(
        _quando("2026-11-02"),
        [W_OUT, W_OUT2],
        [(conta, "ok", _dados(_semana(W_OUT, 7.0, 0.7), _semana(W_OUT2, 14.0, 1.4)))],
    )
    await db.commit()
    auth_as(await _usuario(db))
    periodo = {"start": "2026-10-12T00:00:00", "end": "2026-11-01T00:00:00"}
    linha = _por_loja((await client.get("/api/faturamento", params=periodo)).json())[str(loja.id)]
    assert linha["investimento_afiliados"] == 621.0  # 7 + 14 + 600
    assert linha["investimento_ads"] == 62.1  # 0,7 + 1,4 + 60
    # A conta tem coleta desde 12/10: um período que começa antes é parcial.
    assert linha["investimento_desde"] == "2026-10-12"
    assert linha["investimento_parcial"] is False
    periodo = {"start": "2026-10-01T00:00:00", "end": "2026-11-01T00:00:00"}
    linha = _por_loja((await client.get("/api/faturamento", params=periodo)).json())[str(loja.id)]
    assert linha["investimento_parcial"] is True


@pytest.mark.asyncio
async def test_ligacao_pela_chave_interna(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """bling_loja_id vence; na Shopee a conta_key (minúsculas, sem espaço nas
    pontas) acha a loja ativa; duas lojas para a mesma chave → fora."""
    dono = await _usuario(db)
    c = Cena(db, dono)
    certa = await c.loja("Loja Certa", "shopee", "8001")
    pelo_bling = await c.loja("outra", "shopee", "8002")
    dup1 = await c.loja("dup", "shopee", "8003")
    dup2 = await c.loja("DUP", "shopee", "8004")
    a = await c.conta("qualquer nome", conta_key="  loja certa ")
    # conta_key de uma loja, bling de outra: vale o Bling.
    b = await c.conta("Loja Certa", conta_key="loja certa", bling_loja_id="8002")
    d = await c.conta("dup", conta_key="dup")
    await c.rodada(
        _quando("2026-11-23"),
        [W3],
        [(a, "ok", _dados(_semana(W3, 10.0, 1.0))), (b, "ok", _dados(_semana(W3, 20.0, 2.0))),
         (d, "ok", _dados(_semana(W3, 30.0, 3.0)))],
    )  # fmt: skip
    await db.commit()
    auth_as(await _usuario(db))
    linhas = _por_loja((await client.get("/api/faturamento", params=NOVEMBRO)).json())
    assert linhas[str(certa.id)]["investimento"] == 11.0
    assert linhas[str(pelo_bling.id)]["investimento"] == 22.0
    assert linhas[str(dup1.id)]["investimento"] is None
    assert linhas[str(dup2.id)]["investimento"] is None


@pytest.mark.asyncio
async def test_sem_a_tabela_do_ads_por_dia_a_conferencia_continua(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """Sem a marketing_gasto_dia (deploy antes da 0507) a parte do Ads por dia
    fica sem dado, mas a Conferência continua aparecendo."""
    _, lj = await _novembro(db)
    auth_as(await _usuario(db))
    await db.execute(text("ALTER TABLE marketing_gasto_dia RENAME TO marketing_gasto_dia_fora"))
    await db.commit()
    try:
        r = await client.get("/api/faturamento", params=NOVEMBRO)
    finally:
        await db.execute(text("ALTER TABLE marketing_gasto_dia_fora RENAME TO marketing_gasto_dia"))
        await db.commit()
    assert r.status_code == 200, r.text
    linhas = _por_loja(r.json())
    assert linhas[str(lj["mega"].id)]["investimento"] == 8430.57
    # Sem o por dia, o marquezini cai no Ads da Conferência do ML.
    assert linhas[str(lj["marquezini"].id)]["investimento_ads"] == 9999.0


def test_valor_nos_dias():
    semana = _Semana(
        inicio=date(2026, 11, 29),
        fim=date(2026, 12, 5),
        totais={"afiliados": Decimal("700"), "ads": None},
        dias=[{"dia": "2026-11-29", "afiliados": 5, "ads": 1}, {"dia": "2026-11-30", "ads": 2}],
    )
    nov = [date(2026, 11, 29), date(2026, 11, 30)]
    semana_toda = [date(2026, 11, 29) + timedelta(days=i) for i in range(7)]
    # Afiliados sem o dia 30 no dia a dia: rateio do total (2/7).
    assert valor_nos_dias(semana, "afiliados", nov, nov) == Decimal("200")
    # Ads: dia a dia completo em nov (o total da semana nem existe).
    assert valor_nos_dias(semana, "ads", nov, nov) == Decimal("3")
    # Semana inteira sem o dia a dia completo: o total; Ads sem total: None.
    assert valor_nos_dias(semana, "afiliados", semana_toda, semana_toda) == Decimal("700")
    assert valor_nos_dias(semana, "ads", semana_toda, semana_toda) is None
    assert valor_nos_dias(semana, "afiliados", [], nov) is None
