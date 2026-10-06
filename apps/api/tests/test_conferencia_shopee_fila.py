"""Conferência Shopee — a fila (services/conferencia_shopee/fila), com banco.

O que fica travado: a criação (semanas e prazos fixados, uma coleta por loja
ATIVA com a foto do nome/grupo, na ordem da lista; uma rodada coletando por
vez); o lease (mais antiga primeiro, `disponivel_apos`, corte, coleta largada
há 20 min volta, 3 tentativas e acabou, SKIP LOCKED entre dois executores);
o resultado (perfil em uso e afiliados atrasados voltam para a fila sem gastar
tentativa — perfil em uso só 3 vezes —, o resto é final e guarda dados e
saldo); o fechamento (só sem fila; congela o relatório, com o saldo das
semanas passadas e o vínculo do DaVinci); o varredor do prazo; recalcular e
cancelar.

Números INVENTADOS com a forma do ColetaDados (contrato §4). Os helpers daqui
servem também a test_conferencia_shopee_api e _threema.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.models import (
    ConferenciaShopeeColeta,
    ConferenciaShopeeConta,
    ConferenciaShopeeSaldo,
    Integration,
    IntegrationPlatform,
    PricingAccount,
    PricingPlatform,
    Product,
    ProductLink,
    Segment,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.services.conferencia_shopee import fila, periodos

pytestmark = pytest.mark.asyncio

# Terça 06/10/2026, 13:30 em Brasília: a hora da agenda.
AGORA = datetime(2026, 10, 6, 16, 30, tzinfo=UTC)
CORTE = datetime(2026, 10, 6, 20, 30, tzinfo=UTC)  # 17:30 BRT
PRAZO = datetime(2026, 10, 6, 21, 0, tzinfo=UTC)  # corte + 30 min
DUAS = (("k-ana", "Ana", "mala", True, 0), ("k-bia", "Bia", "celular", True, 1))


# ───────────────────────────────────────────────────────────── helpers


async def semear_contas(
    db: AsyncSession,
    specs: tuple[tuple[str, str, str, bool, int], ...] = (
        ("k-bia", "Bia", "celular", True, 1),
        ("k-ana", "Ana", "mala", True, 1),
        ("k-caio", "Caio", "celular", True, 0),
        ("k-velha", "Velha", "celular", False, 0),
    ),
) -> dict[str, ConferenciaShopeeConta]:
    """(adspower_user_id, nome, grupo, ativo, ordem) → contas; fila esperada:
    Caio (ordem 0), Ana, Bia (ordem 1, por nome); Velha está inativa."""
    contas = {
        nome: ConferenciaShopeeConta(
            adspower_user_id=uid, nome=nome, grupo=grupo, ativo=ativo, ordem=ordem,
            conta_key=nome.lower(),
        )
        for uid, nome, grupo, ativo, ordem in specs
    }
    db.add_all(contas.values())
    await db.commit()
    return contas


def dados_loja(
    semanas: list[dict],
    *,
    vendas: float = 2000.0,
    saldo: float | None = 250.0,
    coletado_em: str = "2026-10-06T17:00:00Z",
    itens_ads: list[dict] | None = None,
    afiliados_itens: list[dict] | None = None,
) -> dict:
    """Um ColetaDados v1 inventado: mesmos números nas 4 semanas, menos as
    vendas da S1 (`vendas`)."""
    sem = []
    for i, s in enumerate(semanas):
        sem.append(
            {
                "inicio": s["inicio"],
                "fim": s["fim"],
                "afiliados": {"vendas": 800.0, "comissao": 40.0, "pedidos": 8},
                "afiliados_itens": afiliados_itens
                if afiliados_itens is not None
                else [{"item_id": "900", "nome": "Capinha", "categoria_id": 100013,
                       "vendas": 800.0, "comissao": 40.0, "pedidos": 8}],
                "ads": {"impressoes": 5000, "cliques": 100, "gasto": 60.0, "vendas": 900.0,
                        "pedidos": 6},
                "ads_itens": itens_ads
                if itens_ads is not None
                else [{"item_id": "900", "nome": "Capinha", "tipo": "product_manual",
                       "impressoes": 5000, "cliques": 100, "gasto": 60.0, "vendas": 900.0,
                       "pedidos": 6}],
                "vendas": {"valor": vendas if i == 0 else 1500.0, "pedidos": 20},
                "vendas_itens": [{"item_id": "900", "nome": "Capinha",
                                  "valor": vendas if i == 0 else 1500.0, "pedidos": 20}],
                "avisos": [],
            }
        )
    return {
        "versao": 1,
        "coletado_em": coletado_em,
        "duracao_s": 60.0,
        "chamadas": 40,
        "login": {"username": "loja_teste", "shopid": 1, "shop_name": "Loja Teste"},
        "login_auto_usado": False,
        "saldo_ads": saldo,
        "afiliados_ultimo_dia": semanas[0]["fim"],
        "semanas": sem,
        "avisos": [],
    }


async def coletas_de(db: AsyncSession, execucao_id) -> list[ConferenciaShopeeColeta]:
    return list(
        (
            await db.execute(
                select(ConferenciaShopeeColeta)
                .where(ConferenciaShopeeColeta.execucao_id == execucao_id)
                .order_by(ConferenciaShopeeColeta.criado_em)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def recarregar(db: AsyncSession, obj):
    await db.refresh(obj)
    return obj


async def _criar(db: AsyncSession, tipo: str = "semanal", agora: datetime = AGORA):
    ex = await fila.criar_execucao(db, tipo, "manual", "quem@davinci-test.com", agora)
    await db.commit()
    return ex


async def _lease(db: AsyncSession, agora: datetime, agente: str = "mac-teste"):
    job, esgotadas = await fila.lease(db, agente, agora)
    await db.commit()
    return job, esgotadas


async def _resultado(db, coleta_id, status, agora, *, dados=None, erro=None):
    c = await fila.registrar_resultado(db, uuid.UUID(str(coleta_id)), status, erro, dados, agora)
    await db.commit()
    return c


# ───────────────────────────────────────────────────────────── criar


async def test_criar_fixa_semanas_prazos_e_uma_coleta_por_conta_ativa(db):
    contas = await semear_contas(db)
    ex = await _criar(db)
    assert ex.status == "coletando"
    assert ex.tipo == "semanal" and ex.origem == "manual"
    assert ex.criado_por == "quem@davinci-test.com"
    assert ex.semanas == periodos.semanas("semanal", AGORA.date())
    assert ex.semanas[0] == {"inicio": "2026-09-28", "fim": "2026-10-04"}
    assert ex.afiliados_ate.isoformat() == "2026-10-04"
    assert ex.esperar_afiliados_ate == datetime(2026, 10, 6, 18, 0, tzinfo=UTC)
    assert ex.corte == CORTE
    assert ex.prazo == PRAZO
    coletas = await coletas_de(db, ex.id)
    # Na ordem da lista (ordem, nome); a inativa não entra.
    assert [c.nome for c in coletas] == ["Caio", "Ana", "Bia"]
    caio = coletas[0]
    assert caio.conta_id == contas["Caio"].id
    assert (caio.adspower_user_id, caio.grupo, caio.status) == ("k-caio", "celular", "pendente")
    assert (caio.tentativas, caio.adiamentos) == (0, 0)


async def test_parcial_numa_segunda_vira_semanal(db):
    await semear_contas(db)
    segunda = datetime(2026, 10, 5, 16, 30, tzinfo=UTC)
    ex = await _criar(db, "parcial", segunda)
    assert ex.tipo == "semanal"


async def test_so_uma_rodada_coletando_por_vez(db):
    await semear_contas(db)
    ex = await _criar(db)
    with pytest.raises(fila.FilaError) as e:
        await fila.criar_execucao(db, "parcial", "agenda", None, AGORA)
    assert e.value.code == "conferencia_em_andamento"
    await db.rollback()
    await db.refresh(ex)  # o rollback expira o que estava na sessão
    await fila.cancelar(db, ex, AGORA)
    await db.commit()
    outra = await _criar(db)
    assert outra.id != ex.id


async def test_sem_conta_ativa_nao_cria(db):
    await semear_contas(db, (("k-x", "X", "mala", False, 0),))
    with pytest.raises(fila.FilaError) as e:
        await fila.criar_execucao(db, "semanal", "agenda", None, AGORA)
    assert e.value.code == "conferencia_sem_contas"


# ───────────────────────────────────────────────────────────── lease


async def test_lease_entrega_a_mais_antiga_e_marca_coletando(db):
    await semear_contas(db)
    ex = await _criar(db)
    job, _ = await _lease(db, AGORA)
    assert job == {
        "coleta_id": job["coleta_id"],
        "execucao_id": str(ex.id),
        "conta": "Caio",
        "adspower_user_id": "k-caio",
        "grupo": "celular",
        "semanas": ex.semanas,
        "afiliados_ate": "2026-10-04",
        "esperar_afiliados_ate": "2026-10-06T18:00:00Z",
        "corte": "2026-10-06T20:30:00Z",
        "login_auto": True,
        "tentativa": 1,
    }
    caio = (await coletas_de(db, ex.id))[0]
    assert (caio.status, caio.tentativas, caio.agente) == ("coletando", 1, "mac-teste")
    assert caio.claimed_at == AGORA
    nomes = [(await _lease(db, AGORA))[0]["conta"] for _ in range(2)]
    assert nomes == ["Ana", "Bia"]
    assert (await _lease(db, AGORA))[0] is None


async def test_lease_respeita_disponivel_apos(db):
    await semear_contas(db)
    ex = await _criar(db)
    caio = (await coletas_de(db, ex.id))[0]
    caio.disponivel_apos = AGORA + timedelta(minutes=10)
    await db.commit()
    assert (await _lease(db, AGORA))[0]["conta"] == "Ana"
    assert (await _lease(db, AGORA + timedelta(minutes=9)))[0]["conta"] == "Bia"
    assert (await _lease(db, AGORA + timedelta(minutes=9)))[0] is None
    assert (await _lease(db, AGORA + timedelta(minutes=10)))[0]["conta"] == "Caio"


async def test_lease_nada_depois_do_corte_nem_de_rodada_parada(db):
    await semear_contas(db)
    ex = await _criar(db)
    assert (await _lease(db, CORTE))[0] is None
    assert (await _lease(db, CORTE - timedelta(seconds=1)))[0]["conta"] == "Caio"
    await fila.cancelar(db, ex, AGORA)
    await db.commit()
    assert (await _lease(db, AGORA))[0] is None


async def test_lease_retoma_coleta_largada_ha_mais_de_20_min(db):
    await semear_contas(db, (("k-um", "Um", "mala", True, 0),))
    await _criar(db)
    primeiro, _ = await _lease(db, AGORA)
    assert (await _lease(db, AGORA + timedelta(minutes=19)))[0] is None
    de_novo, _ = await _lease(db, AGORA + timedelta(minutes=21), "mac-2")
    assert de_novo["coleta_id"] == primeiro["coleta_id"]
    assert de_novo["tentativa"] == 2


async def test_lease_tres_tentativas_e_acabou(db):
    await semear_contas(db, (("k-um", "Um", "mala", True, 0),))
    ex = await _criar(db)
    for i, minutos in enumerate((0, 21, 42), start=1):
        job, esgotadas = await _lease(db, AGORA + timedelta(minutes=minutos))
        assert job["tentativa"] == i and not esgotadas
    job, esgotadas = await _lease(db, AGORA + timedelta(minutes=63))
    assert job is None
    assert esgotadas == {ex.id}
    (c,) = await coletas_de(db, ex.id)
    assert (c.status, c.erro) == ("erro", "muitas tentativas")
    # Era a única loja: quem chamou o lease fecha a rodada.
    fechada = await fila.fechar_se_terminou(db, ex.id, AGORA + timedelta(minutes=63))
    await db.commit()
    assert fechada is not None and fechada.status == "pronto"
    assert fechada.relatorio["contas_sem_dados"] == [
        {"conta": "Um", "status": "erro", "erro": "muitas tentativas"}
    ]


async def test_lease_skip_locked_dois_executores_nunca_pegam_a_mesma(db):
    await semear_contas(db)
    await _criar(db)
    async with _db.SessionLocal() as s1, _db.SessionLocal() as s2:
        job1, _ = await fila.lease(s1, "mac-1", AGORA)  # s1 segura a trava (sem commit)
        job2, _ = await fila.lease(s2, "mac-2", AGORA)
        assert job1["conta"] == "Caio"
        # A execução NÃO fica travada (FOR UPDATE OF coleta): o 2º pega a próxima.
        assert job2["conta"] == "Ana"
        await s2.commit()
        await s1.rollback()  # o 1º caiu antes de gravar: Caio continua na fila
    job3, _ = await _lease(db, AGORA)
    assert job3["conta"] == "Caio"


# ───────────────────────────────────────────────────────────── resultado


async def test_perfil_em_uso_volta_3_vezes_sem_gastar_tentativa(db):
    await semear_contas(db, (("k-um", "Um", "celular", True, 0),))
    ex = await _criar(db)
    agora = AGORA
    for adiamento in (1, 2, 3):
        job, _ = await _lease(db, agora)
        assert job["tentativa"] == 1  # adiar não gastou tentativa
        c = await _resultado(db, job["coleta_id"], "perfil_em_uso", agora, erro="perfil aberto")
        assert (c.status, c.adiamentos, c.tentativas) == ("pendente", adiamento, 0)
        assert c.adiamentos_perfil == adiamento
        assert c.disponivel_apos == agora + timedelta(minutes=10)
        assert c.erro == "perfil aberto" and c.claimed_at is None
        assert (await _lease(db, agora + timedelta(minutes=9)))[0] is None
        agora += timedelta(minutes=10)
    job, _ = await _lease(db, agora)
    c = await _resultado(db, job["coleta_id"], "perfil_em_uso", agora)
    assert (c.status, c.adiamentos, c.concluido_em) == ("perfil_em_uso", 3, agora)
    assert (await recarregar(db, ex)).status == "coletando"  # fechar é com quem chama


async def test_afiliados_atrasados_reagendam_ate_o_corte(db):
    await semear_contas(db, (("k-um", "Um", "celular", True, 0),))
    await _criar(db)
    agora = AGORA
    for adiamento in range(1, 6):  # sem o teto de 3 do perfil em uso
        job, _ = await _lease(db, agora)
        c = await _resultado(db, job["coleta_id"], "aguardando_afiliados", agora)
        assert (c.status, c.adiamentos, c.tentativas) == ("pendente", adiamento, 0)
        assert c.adiamentos_perfil == 0
        agora += timedelta(minutes=10)
    job, _ = await _lease(db, CORTE - timedelta(minutes=1))
    # Chegou depois do corte: nenhuma loja começa mais, então é final.
    c = await _resultado(db, job["coleta_id"], "aguardando_afiliados", CORTE)
    assert c.status == "erro"
    assert c.erro == fila.ERRO_AFILIADOS_NO_CORTE


async def test_espera_dos_afiliados_nao_gasta_as_voltas_do_perfil_em_uso(db):
    """Rodada manual de manhã: a loja espera os afiliados várias vezes; quando
    enfim acha o perfil aberto, ainda tem as 3 voltas do perfil em uso (a
    cota é só dele — antes, as esperas a gastavam e a loja acabava sem
    dados na 1ª vez)."""
    await semear_contas(db, (("k-um", "Um", "celular", True, 0),))
    await _criar(db)
    agora = AGORA
    for _ in range(5):
        job, _ = await _lease(db, agora)
        c = await _resultado(db, job["coleta_id"], "aguardando_afiliados", agora)
        assert c.status == "pendente"
        agora += timedelta(minutes=10)
    for volta in (1, 2, 3):
        job, _ = await _lease(db, agora)
        c = await _resultado(db, job["coleta_id"], "perfil_em_uso", agora)
        assert (c.status, c.adiamentos, c.adiamentos_perfil) == ("pendente", 5 + volta, volta)
        agora += timedelta(minutes=10)
    job, _ = await _lease(db, agora)
    c = await _resultado(db, job["coleta_id"], "perfil_em_uso", agora)
    assert (c.status, c.adiamentos, c.adiamentos_perfil) == ("perfil_em_uso", 8, 3)


async def test_perfil_em_uso_depois_do_corte_e_final(db):
    await semear_contas(db, (("k-um", "Um", "celular", True, 0),))
    await _criar(db)
    job, _ = await _lease(db, CORTE - timedelta(minutes=1))
    c = await _resultado(db, job["coleta_id"], "perfil_em_uso", CORTE + timedelta(minutes=1))
    assert (c.status, c.adiamentos) == ("perfil_em_uso", 0)


async def test_resultado_final_grava_dados_e_saldo(db):
    await semear_contas(db)
    ex = await _criar(db)
    job, _ = await _lease(db, AGORA)
    d = dados_loja(ex.semanas, saldo=353.0008, coletado_em="2026-10-06T16:41:02Z")
    c = await _resultado(db, job["coleta_id"], "ok", AGORA + timedelta(minutes=2), dados=d)
    assert (c.status, c.erro) == ("ok", None)
    assert c.dados == d
    assert c.concluido_em == AGORA + timedelta(minutes=2)
    (saldo,) = (await db.execute(select(ConferenciaShopeeSaldo))).scalars().all()
    assert saldo.adspower_user_id == "k-caio"
    assert saldo.valor == Decimal("353.00")
    assert saldo.lido_em == datetime(2026, 10, 6, 16, 41, 2, tzinfo=UTC)
    assert saldo.execucao_id == ex.id

    # Sem saldo (carteira falhou) ou sem dados: nada no histórico.
    job, _ = await _lease(db, AGORA)
    sem_saldo = dados_loja(ex.semanas, saldo=None)
    await _resultado(db, job["coleta_id"], "parcial", AGORA, dados=sem_saldo)
    job, _ = await _lease(db, AGORA)
    c = await _resultado(db, job["coleta_id"], "deslogada", AGORA, erro="  caiu no login  ")
    assert (c.status, c.erro, c.dados) == ("deslogada", "caiu no login", None)
    assert len((await db.execute(select(ConferenciaShopeeSaldo))).scalars().all()) == 1


async def test_resultado_recusado(db):
    await semear_contas(db)
    ex = await _criar(db)
    with pytest.raises(fila.FilaError) as e:
        await fila.registrar_resultado(db, uuid.uuid4(), "ok", None, None, AGORA)
    assert e.value.code == "coleta_nao_encontrada"
    job, _ = await _lease(db, AGORA)
    with pytest.raises(fila.FilaError) as e:
        await fila.registrar_resultado(
            db, uuid.UUID(job["coleta_id"]), "expirada", None, None, AGORA
        )
    assert e.value.code == "status_invalido"
    await db.rollback()
    await _resultado(db, job["coleta_id"], "erro", AGORA, erro="x")
    with pytest.raises(fila.FilaError) as e:  # entregue duas vezes
        await fila.registrar_resultado(db, uuid.UUID(job["coleta_id"]), "ok", None, None, AGORA)
    assert e.value.code == "coleta_ja_concluida"
    await db.rollback()
    assert (await recarregar(db, ex)).status == "coletando"


# ───────────────────────────────────────────────────────────── fechar


async def test_fecha_so_quando_nao_sobra_fila_e_congela_o_relatorio(db):
    await semear_contas(db, DUAS)
    ex = await _criar(db)
    # Saldo lido na rodada da semana passada (terça 29/09): vale para S2.
    db.add(ConferenciaShopeeSaldo(adspower_user_id="k-ana",
                                  lido_em=datetime(2026, 9, 29, 17, 0, tzinfo=UTC),
                                  valor=Decimal("410.50")))
    await db.commit()
    ana, _ = await _lease(db, AGORA)
    bia, _ = await _lease(db, AGORA)
    await _resultado(db, ana["coleta_id"], "ok", AGORA, dados=dados_loja(ex.semanas, saldo=300.0))
    assert await fila.fechar_se_terminou(db, ex.id, AGORA) is None  # Bia ainda coletando
    await db.commit()
    await _resultado(db, bia["coleta_id"], "sem_automacao", AGORA, erro="perfil Firefox")
    fim = AGORA + timedelta(minutes=5)
    fechada = await fila.fechar_se_terminou(db, ex.id, fim)
    await db.commit()
    assert fechada is not None
    ex = await recarregar(db, ex)
    assert (ex.status, ex.finalizado_em) == ("pronto", fim)
    rel = ex.relatorio
    assert rel["execucao_id"] == str(ex.id)
    assert rel["gerado_em"] == fim.isoformat()
    mala = next(g for g in rel["grupos"] if g["chave"] == "mala")
    (linha,) = mala["linhas"]
    assert linha["conta"] == "Ana"
    assert [s["saldo_ads"] for s in linha["semanas"]] == [300.0, 410.5, None, None]
    assert linha["semanas"][0]["vendas"] == 2000.0
    assert rel["contas_sem_dados"] == [
        {"conta": "Bia", "status": "sem_automacao", "erro": "perfil Firefox"}
    ]
    assert rel["geral"]["contas"] == 2 and rel["geral"]["sem_dados"] == 1
    # Já fechada: não fecha de novo.
    assert await fila.fechar_se_terminou(db, ex.id, fim) is None


async def test_eletro_pelo_vinculo_do_davinci(db):
    """A conta_key da lista de lojas chega ao vínculo: o item 100 (título e
    categoria de celular) é eletro porque o produto vinculado tem SKU u…"""
    dono = User(open_id="email:conf-fila@davinci-test.com", email="conf-fila@davinci-test.com",
                role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(dono)
    await db.flush()
    integ = Integration(user_id=dono.id, platform=IntegrationPlatform.SHOPEE, name="bia",
                        credentials=b"x")
    seg = Segment(name="Celular", slug="celular")
    loja = StoreInfo(user_id=dono.id, platform="shopee", account_name="Bia")
    db.add_all([integ, seg, loja])
    await db.flush()
    produto = Product(user_id=dono.id, sku="u001", name="Air fryer")
    db.add_all([
        PricingAccount(user_id=dono.id, name="bia", platform=PricingPlatform.SHOPEE,
                       segment_id=seg.id, store_info_id=loja.id, integration_id=integ.id),
        produto,
    ])
    await db.flush()
    db.add(ProductLink(user_id=dono.id, product_id=produto.id, integration_id=integ.id,
                       platform=IntegrationPlatform.SHOPEE, external_id="100", variation_id="0"))
    await db.commit()

    await semear_contas(db, (("k-bia", "Bia", "celular", True, 0),))
    ex = await _criar(db)
    job, _ = await _lease(db, AGORA)
    item = [{"item_id": "100", "nome": "Capinha", "categoria_id": 100013, "vendas": 300.0,
             "comissao": 10.0, "pedidos": 1}]
    d = dados_loja(ex.semanas, afiliados_itens=item)
    await _resultado(db, job["coleta_id"], "ok", AGORA, dados=d)
    await fila.fechar_se_terminou(db, ex.id, AGORA)
    await db.commit()
    rel = (await recarregar(db, ex)).relatorio
    eletro = next(g for g in rel["grupos"] if g["chave"] == "eletro")
    (linha,) = eletro["linhas"]
    assert linha["conta"] == "Bia"
    assert linha["semanas"][0]["vendas_afiliados"] == 300.0
    celular = next(g for g in rel["grupos"] if g["chave"] == "celular")
    assert celular["linhas"][0]["semanas"][0]["vendas_afiliados"] == 500.0
    assert rel["divergencias"] == [
        {"conta": "Bia", "item_id": "100", "nome": "Capinha", "davinci": "eletro",
         "categoria_shopee": 100013}
    ]


async def test_varredor_expira_e_fecha_no_prazo(db):
    await semear_contas(db, DUAS)
    ex = await _criar(db)
    ana, _ = await _lease(db, AGORA)
    await _resultado(db, ana["coleta_id"], "ok", AGORA, dados=dados_loja(ex.semanas))
    bia, _ = await _lease(db, AGORA)
    await _resultado(db, bia["coleta_id"], "perfil_em_uso", AGORA, erro="perfil aberto")

    assert await fila.varrer(db, PRAZO - timedelta(minutes=1)) == []
    await db.commit()
    fechadas = await fila.varrer(db, PRAZO)
    await db.commit()
    assert [e.id for e in fechadas] == [ex.id]
    ex = await recarregar(db, ex)
    assert ex.status == "pronto" and ex.finalizado_em == PRAZO
    bia_c = (await coletas_de(db, ex.id))[1]
    # O erro de antes fica: diz por que a loja não andou.
    assert (bia_c.status, bia_c.erro, bia_c.concluido_em) == ("expirada", "perfil aberto", PRAZO)
    assert ex.relatorio["contas_sem_dados"] == [
        {"conta": "Bia", "status": "expirada", "erro": "perfil aberto"}
    ]
    assert await fila.varrer(db, PRAZO + timedelta(minutes=10)) == []


async def test_varredor_fecha_rodada_sem_fila_esquecida_aberta(db):
    await semear_contas(db, (("k-ana", "Ana", "mala", True, 0),))
    ex = await _criar(db)
    job, _ = await _lease(db, AGORA)
    await _resultado(db, job["coleta_id"], "ok", AGORA, dados=dados_loja(ex.semanas))
    # (a rota não chegou a fechar: o cálculo falhou, o processo caiu…)
    fechadas = await fila.varrer(db, AGORA + timedelta(minutes=10))
    await db.commit()
    assert [e.id for e in fechadas] == [ex.id]
    assert (await recarregar(db, ex)).status == "pronto"


async def test_dados_que_derrubam_o_calculo_nao_travam_a_rodada(db):
    """Executor com defeito: um item que não é objeto passou (a rota hoje
    recusa com 422; aqui o serviço é chamado direto). O cálculo quebrava, o
    varredor levantava a cada volta e a rodada ficava `coletando` para
    sempre. Agora só a loja culpada perde os números (vira `erro`) e a rodada
    fecha com o resto."""
    await semear_contas(db, DUAS)
    ex = await _criar(db)
    ana, _ = await _lease(db, AGORA)
    await _resultado(db, ana["coleta_id"], "ok", AGORA, dados=dados_loja(ex.semanas))
    bia, _ = await _lease(db, AGORA)
    torto = dados_loja(ex.semanas, afiliados_itens=["item-que-nao-e-objeto"])
    torto["semanas"][1]["vendas_itens"] = 7
    torto["semanas"].append("semana-que-nao-e-objeto")
    torto["avisos"] = 3
    # A rota fecharia aqui; o fechamento com rede já se vira:
    await _resultado(db, bia["coleta_id"], "ok", AGORA, dados=torto)
    fechada = await fila.fechar_se_terminou(db, ex.id, AGORA)
    await db.commit()
    assert fechada is not None
    ex = await recarregar(db, ex)
    assert ex.status == "pronto" and ex.relatorio is not None
    ana_c, bia_c = await coletas_de(db, ex.id)
    assert ana_c.status == "ok" and ana_c.dados is not None
    # O defensivo do cálculo já aguenta este torto: a loja fica com os dados.
    assert bia_c.status == "ok"
    assert [g["total"]["contas"] for g in ex.relatorio["grupos"]] == [1, 1, 0]


async def test_varredor_descarta_so_os_dados_que_quebram_e_fecha(db, monkeypatch):
    """Se ainda assim o cálculo quebrar por causa de UMA loja, ela vira `erro`
    sem os dados, a rodada fecha (no varredor e na rota) e a próxima pode ser
    criada."""
    await semear_contas(db, DUAS)
    ex = await _criar(db)
    ana, _ = await _lease(db, AGORA)
    await _resultado(db, ana["coleta_id"], "ok", AGORA, dados=dados_loja(ex.semanas))
    bia, _ = await _lease(db, AGORA)
    await _resultado(db, bia["coleta_id"], "ok", AGORA,
                     dados=dados_loja(ex.semanas) | {"bomba": True})

    real = fila.calculo.montar_relatorio

    def explode_com_a_bomba(execucao, coletas, **kw):
        coletas = list(coletas)
        if any((c.get("dados") or {}).get("bomba") for c in coletas):
            raise AttributeError("'str' object has no attribute 'get'")
        return real(execucao, coletas, **kw)

    monkeypatch.setattr(fila.calculo, "montar_relatorio", explode_com_a_bomba)
    fechadas = await fila.varrer(db, PRAZO + timedelta(minutes=1))
    await db.commit()
    assert [e.id for e in fechadas] == [ex.id]
    ex = await recarregar(db, ex)
    assert ex.status == "pronto"
    ana_c, bia_c = await coletas_de(db, ex.id)
    assert ana_c.status == "ok" and ana_c.dados is not None
    assert (bia_c.status, bia_c.dados) == ("erro", None)
    assert bia_c.erro.startswith(fila.ERRO_DADOS_INVALIDOS)
    assert ex.relatorio["contas_sem_dados"] == [
        {"conta": "Bia", "status": "erro", "erro": bia_c.erro}
    ]
    # A rodada não ficou presa: a próxima é criada.
    nova = await fila.criar_execucao(db, "semanal", "manual", None, PRAZO + timedelta(hours=1))
    assert nova.status == "coletando"


async def test_calculo_que_quebra_sem_culpada_fica_para_a_proxima_volta(db, monkeypatch):
    """Quebra que não é dos dados de nenhuma loja (ex.: banco): o varredor não
    levanta — a rodada fica para a próxima volta, as outras seguem."""
    await semear_contas(db, (("k-ana", "Ana", "mala", True, 0),))
    ex = await _criar(db)
    job, _ = await _lease(db, AGORA)
    await _resultado(db, job["coleta_id"], "ok", AGORA, dados=dados_loja(ex.semanas))

    async def falha(*a, **kw):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(fila.classificacao, "carregar_mapa_davinci", falha)
    assert await fila.varrer(db, AGORA + timedelta(minutes=10)) == []
    await db.commit()
    assert (await recarregar(db, ex)).status == "coletando"
    (c,) = await coletas_de(db, ex.id)
    assert c.status == "ok" and c.dados is not None  # nada descartado à toa


async def test_varredor_expira_coleta_largada_em_coletando(db):
    await semear_contas(db, (("k-ana", "Ana", "mala", True, 0),))
    ex = await _criar(db)
    await _lease(db, AGORA)
    await fila.varrer(db, PRAZO)
    await db.commit()
    (c,) = await coletas_de(db, ex.id)
    assert (c.status, c.erro) == ("expirada", fila.ERRO_PRAZO)


# ───────────────────────────────────────────────────────────── recalcular / cancelar


async def test_recalcular_so_pronta_e_refaz_com_os_dados_guardados(db):
    await semear_contas(db, (("k-ana", "Ana", "mala", True, 0),))
    ex = await _criar(db)
    with pytest.raises(fila.FilaError) as e:
        await fila.recalcular(db, ex, AGORA)
    assert e.value.code == "conferencia_nao_pronta"
    await db.rollback()
    job, _ = await _lease(db, AGORA)
    await _resultado(db, job["coleta_id"], "ok", AGORA, dados=dados_loja(ex.semanas, vendas=2000.0))
    await fila.fechar_se_terminou(db, ex.id, AGORA)
    await db.commit()
    (c,) = await coletas_de(db, ex.id)
    c.dados = dados_loja(ex.semanas, vendas=3000.0)
    await db.commit()
    depois = AGORA + timedelta(days=1)
    await fila.recalcular(db, ex, depois)
    await db.commit()
    ex = await recarregar(db, ex)
    linha = ex.relatorio["grupos"][0]["linhas"][0]
    assert linha["semanas"][0]["vendas"] == 3000.0
    assert ex.relatorio["gerado_em"] == depois.isoformat()
    assert ex.finalizado_em == AGORA  # recalcular não muda quando fechou


async def test_cancelar_expira_a_fila_sem_relatorio(db):
    await semear_contas(db)
    ex = await _criar(db)
    job, _ = await _lease(db, AGORA)
    await _resultado(db, job["coleta_id"], "ok", AGORA, dados=dados_loja(ex.semanas))
    await _lease(db, AGORA)  # Ana coletando
    await fila.cancelar(db, ex, AGORA + timedelta(minutes=1))
    await db.commit()
    ex = await recarregar(db, ex)
    assert (ex.status, ex.relatorio) == ("cancelado", None)
    assert ex.finalizado_em == AGORA + timedelta(minutes=1)
    coletas = await coletas_de(db, ex.id)
    assert [(c.nome, c.status) for c in coletas] == [
        ("Caio", "ok"), ("Ana", "expirada"), ("Bia", "expirada")
    ]
    assert coletas[1].erro == fila.ERRO_CANCELADA
    ana_id = coletas[1].id
    with pytest.raises(fila.FilaError) as e:
        await fila.cancelar(db, ex, AGORA)
    assert e.value.code == "conferencia_nao_coletando"
    await db.rollback()
    # Resultado atrasado de loja cancelada: recusado.
    with pytest.raises(fila.FilaError) as e:
        await fila.registrar_resultado(db, ana_id, "ok", None, None, AGORA)
    assert e.value.code == "coleta_ja_concluida"
    await db.rollback()

