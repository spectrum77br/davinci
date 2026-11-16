"""Flex na Shopee: a regra olha TODAS as variações do anúncio (16/11/2026).

Eduardo: "parece que ativou anúncios de flex de volta que não eram para estar
ativos". O robô ligava e desligava os mesmos 8 anúncios de kit a cada 1–3 h
(67 ligações desde 14/11). Eles têm variações "xx1" sem vínculo no DaVinci —
pela regra, sem Flex. A importação da Shopee guarda UM modelo por anúncio (a
última linha gravada vence), então a regra só via a "xx1" quando ela era a
última: desligava; na importação seguinte ela sumia e o "Aprovar sozinho"
religava.

Trava:
  • a rodada lê a lista inteira (`get_model_list`) dos anúncios que a regra
    quer ligados e guarda no estado; com a "xx1" lá, não liga — e não volta a
    ligar quando a importação troca o modelo guardado;
  • antes de LIGAR, a lista tem de ter sido lida há menos de 15 min (senão lê
    na hora); não deu para ler = não liga, a aprovação continua;
  • a variação apagada na Shopee deixa o anúncio voltar a ter Flex;
  • a lista vazia num anúncio com variação vinculada não vale.

Nenhuma chamada externa: os clientes falsos de tests/test_flex_motor.
"""

# ruff: noqa: F811
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FlexAnuncioEstado,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Product,
    ProductLink,
)
from app.security.cipher import encrypt_json
from app.services import flex_motor
from app.services.flex_motor import (
    VINCULO_SEM,
    VINCULO_VIVO,
    Anuncio,
    Variacao,
    _com_modelos,
    lista_confiavel,
    modelos_da_resposta,
)
from tests.test_flex_motor import (  # noqa: F401 — fixtures
    FakeShopee,
    _estados,
    _trilha,
    mundo,
)

DESLIGADA = [
    {"logistic_id": 90001, "enabled": True, "is_free": False},
    {"logistic_id": 90022, "enabled": False, "is_free": False},
]


def _modelo(mid: int, sku: str, estoque: int) -> dict:
    return {
        "model_id": mid,
        "model_sku": sku,
        "stock_info_v2": {"summary_info": {"total_available_stock": estoque}},
    }


PRETA = _modelo(11, "dg053.ci", 10)
XX1 = _modelo(99, "xx1", 0)


@pytest_asyncio.fixture
async def loja(db: AsyncSession, mundo, monkeypatch):
    """Loja da Shopee com o anúncio 888: a variação 11 (dg053.ci, vínculo
    vivo; dg053.sp com 5 peças) e — na Shopee — a "xx1" (id 99, estoque 0,
    nunca vinculada). A importação guardou só o modelo 11 (o último gravado).
    Entrega Direta desligada no anúncio; ativo, escrita da Shopee e "Aprovar
    sozinho" ligados."""
    cfg = mundo["cfg"]
    conta = Integration(user_id=mundo["dono_id"], platform=IntegrationPlatform.SHOPEE,
                        name="atv", credentials=encrypt_json({"access_token": "t"}))
    db.add(conta)
    await db.flush()
    preta = (
        await db.execute(select(Product.id).where(Product.sku == "dg053.ci"))
    ).scalar_one()
    db.add(ProductLink(user_id=mundo["dono_id"], product_id=preta, integration_id=conta.id,
                       platform=IntegrationPlatform.SHOPEE, external_id="888",
                       variation_id="11", stock=10))
    db.add(Listing(user_id=mundo["dono_id"], integration_id=conta.id,
                   platform=IntegrationPlatform.SHOPEE, external_id="888", title="kit",
                   status=ListingStatus.ACTIVE,
                   imported_at=datetime.now(UTC) - timedelta(hours=1),
                   raw_data={"item": {"item_id": 888}, "model": PRETA}))
    await db.commit()
    for chave, valor in {
        "flex_contas": str(conta.id),
        "flex_modo": "ativo",
        "flex_shopee_escrita": True,
    }.items():
        monkeypatch.setattr(cfg, chave, valor)
    monkeypatch.setattr(cfg, "flex_aprovar_sozinho", True, raising=False)
    monkeypatch.setattr(flex_motor, "_FOLGA_MODELOS_S", 0)
    fake = FakeShopee({"888": DESLIGADA})
    fake.modelos["888"] = [dict(PRETA), dict(XX1)]

    async def _montar(integ):
        return fake

    monkeypatch.setattr(flex_motor, "montar_cliente", _montar)
    return {"fake": fake, "conta_id": conta.id, "dono_id": mundo["dono_id"]}


def _ligacoes(fake: FakeShopee) -> list:
    return [
        c for c in fake.escritas
        if any(e["logistic_id"] == 90022 and e["enabled"] for e in c[1][1])
    ]


async def _import_mostra(db: AsyncSession, modelo: dict) -> None:
    """A importação grava outro modelo na linha do anúncio (a de agora)."""
    linha = (await db.execute(select(Listing).where(Listing.external_id == "888"))).scalar_one()
    linha.raw_data = {"item": {"item_id": 888}, "model": modelo}
    linha.imported_at = datetime.now(UTC)
    await db.commit()


# ---- o defeito de 16/11 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_xx1_na_shopee_nao_liga_mesmo_com_a_importacao_mostrando_so_a_vinculada(
    db, loja
):
    fake = loja["fake"]
    await flex_motor.rodar_motor()
    est = (await _estados(db))["888"]
    assert est.desejado == "inelegivel"
    assert est.motivo == "variação xx1 sem vínculo com produto do DaVinci"
    assert est.aprovado_em is None and est.aguardando_aprovacao is False
    assert [m["id"] for m in est.modelos] == ["11", "99"]
    assert est.modelos_em is not None
    assert _ligacoes(fake) == []
    assert ("aprovar", "ok") not in await _trilha(db, "888")


@pytest.mark.asyncio
async def test_importacao_trocando_o_modelo_nao_faz_vaivem(db, loja):
    """O que acontecia: a importação ora gravava a xx1, ora a vinculada — e a
    regra mudava junto. Com a lista inteira guardada, nada muda."""
    fake = loja["fake"]
    for modelo in (PRETA, XX1, PRETA, XX1, PRETA):
        await _import_mostra(db, modelo)
        await flex_motor.rodar_motor()
        est = (await _estados(db))["888"]
        assert est.desejado == "inelegivel", modelo
    assert _ligacoes(fake) == []
    trilha = await _trilha(db, "888")
    assert ("ligar", "ok") not in trilha and ("aprovar", "ok") not in trilha
    decisoes = [t for t in trilha if t[0] == "decidir"]
    assert len(decisoes) == 1  # decidiu uma vez e não mudou mais


@pytest.mark.asyncio
async def test_sem_variacao_fora_do_davinci_liga_lendo_uma_vez_so(db, loja):
    fake = loja["fake"]
    fake.modelos["888"] = [dict(PRETA)]
    await flex_motor.rodar_motor()
    est = (await _estados(db))["888"]
    assert (est.desejado, est.observado) == ("ligado", "ligado")
    assert len(_ligacoes(fake)) == 1
    # A lista lida na rodada (há segundos) serviu para a conferência antes de
    # ligar: uma chamada só.
    assert [c for c in fake.chamadas if c[0] == "modelos"] == [("modelos", "888")]


@pytest.mark.asyncio
async def test_xx1_apagada_na_shopee_o_anuncio_volta_a_poder_ter_flex(db, loja):
    fake = loja["fake"]
    await flex_motor.rodar_motor()
    assert (await _estados(db))["888"].desejado == "inelegivel"
    fake.modelos["888"] = [dict(PRETA)]
    # Dentro da validade a lista guardada vale (não relê a cada rodada).
    await flex_motor.rodar_motor()
    assert (await _estados(db))["888"].desejado == "inelegivel"
    await db.execute(
        text("UPDATE flex_anuncio_estado SET modelos_em = now() - interval '4 hours'")
    )
    await db.commit()
    await flex_motor.rodar_motor()
    est = (await _estados(db))["888"]
    assert est.desejado == "ligado" and est.aprovado_em is not None
    # Aprovado sozinho nesta rodada; LIGAR só na rodada que relê a conta (a de
    # sempre — `ligar_esperando_releitura`).
    await flex_motor.rodar_motor()
    est = (await _estados(db))["888"]
    assert (est.desejado, est.observado) == ("ligado", "ligado")
    assert len(_ligacoes(fake)) == 1


@pytest.mark.asyncio
async def test_importacao_mais_nova_com_variacao_nova_rele_na_hora(db, loja):
    """Ligado com a lista sem xx1; alguém cria a xx1 e a importação a mostra:
    a regra já não liga (soma a importação à lista) e relê na mesma rodada."""
    fake = loja["fake"]
    fake.modelos["888"] = [dict(PRETA)]
    await flex_motor.rodar_motor()
    assert (await _estados(db))["888"].observado == "ligado"
    fake.modelos["888"] = [dict(PRETA), dict(XX1)]
    await _import_mostra(db, XX1)
    antes = len([c for c in fake.chamadas if c[0] == "modelos"])
    await flex_motor.rodar_motor()
    est = (await _estados(db))["888"]
    assert est.desejado == "inelegivel"
    assert est.observado == "desligado"  # desligou
    assert len([c for c in fake.chamadas if c[0] == "modelos"]) == antes + 1
    # A importação volta a mostrar a vinculada: continua desligado.
    await _import_mostra(db, PRETA)
    await flex_motor.rodar_motor()
    assert (await _estados(db))["888"].desejado == "inelegivel"
    assert len(_ligacoes(fake)) == 1


# ---- a conferência antes de LIGAR -------------------------------------------------------


async def _aprovado_com_lista(db, loja, *, lida_ha: timedelta, modelos: list[dict]) -> None:
    """Estado pronto para LIGAR (aprovado, lido desligado, regra quer ligado)
    com a lista guardada há `lida_ha`."""
    agora = datetime.now(UTC)
    db.add(FlexAnuncioEstado(
        integration_id=loja["conta_id"], external_id="888", plataforma="shopee",
        desejado="ligado", motivo="saldo Flex 5 em dg053.sp (liga com 3)",
        observado="desligado", observado_em=agora, aprovado_em=agora,
        aguardando_aprovacao=False, tentativas=0, leitura_falhas=0, saldo_sp=5,
        modelos=[{"id": str(m["model_id"]), "sku": m["model_sku"], "estoque": 0}
                 for m in modelos],
        modelos_em=agora - lida_ha,
    ))
    await db.commit()


async def _escrever(loja) -> str:
    return await flex_motor._escrever(
        loja["fake"], integration_id=loja["conta_id"], external_id="888",
        plataforma="shopee", acao="ligar", modo="ativo", por=None, skus_sp=("dg053.sp",),
    )


@pytest.mark.asyncio
async def test_ligar_com_lista_velha_le_de_novo_e_barra_a_xx1(db, loja):
    await _aprovado_com_lista(db, loja, lida_ha=timedelta(hours=1), modelos=[PRETA])
    assert await _escrever(loja) == "mudou"
    est = (await _estados(db))["888"]
    assert est.desejado == "inelegivel"
    assert est.motivo == "variação xx1 sem vínculo com produto do DaVinci"
    assert est.aprovado_em is None
    assert [m["id"] for m in est.modelos] == ["11", "99"]
    assert loja["fake"].escritas == []
    assert ("decidir", "ok") in await _trilha(db, "888")


@pytest.mark.asyncio
async def test_ligar_com_lista_fresca_nao_le_de_novo(db, loja):
    loja["fake"].modelos["888"] = [dict(PRETA)]
    await _aprovado_com_lista(db, loja, lida_ha=timedelta(minutes=5), modelos=[PRETA])
    assert await _escrever(loja) == "ok"
    assert [c for c in loja["fake"].chamadas if c[0] == "modelos"] == []
    assert len(_ligacoes(loja["fake"])) == 1


@pytest.mark.asyncio
async def test_ligar_sem_conseguir_ler_as_variacoes_nao_liga_e_guarda_a_aprovacao(
    db, loja, monkeypatch
):
    async def _quebrada(item_id):
        raise RuntimeError("shopee fora do ar")

    monkeypatch.setattr(loja["fake"], "get_model_list", _quebrada)
    await _aprovado_com_lista(db, loja, lida_ha=timedelta(hours=1), modelos=[PRETA])
    assert await _escrever(loja) == "repetir"
    est = (await _estados(db))["888"]
    assert est.aprovado_em is not None
    assert flex_motor.MOTIVO_SEM_MODELOS in (est.ultimo_erro or "")
    assert est.proxima_tentativa is not None
    assert loja["fake"].escritas == []


@pytest.mark.asyncio
async def test_lista_vazia_com_variacao_vinculada_nao_vale(db, loja):
    loja["fake"].modelos["888"] = []
    await _aprovado_com_lista(db, loja, lida_ha=timedelta(hours=1), modelos=[PRETA])
    assert await _escrever(loja) == "repetir"
    assert loja["fake"].escritas == []


@pytest.mark.asyncio
async def test_aprovar_de_uma_pessoa_rele_a_lista_antes(db, loja):
    """A pessoa aprova o anúncio com a lista guardada há 1 h sem a xx1 (criada
    depois): a rodada da aprovação relê e não liga."""
    await _aprovado_com_lista(db, loja, lida_ha=timedelta(hours=1), modelos=[PRETA])
    await flex_motor.aprovar(loja["conta_id"], "888", por=loja["dono_id"])
    est = (await _estados(db))["888"]
    assert est.desejado == "inelegivel"
    assert loja["fake"].escritas == []


# ---- funções puras ---------------------------------------------------------------------


def test_modelos_da_resposta():
    assert modelos_da_resposta({"model": [PRETA, XX1, PRETA, {"sem": "id"}, "x"]}) == (
        ("11", "dg053.ci", 10),
        ("99", "xx1", 0),
    )
    assert modelos_da_resposta({"model": []}) == ()
    assert modelos_da_resposta({"tier_variation": []}) is None
    assert modelos_da_resposta(None) is None


def test_lista_confiavel():
    assert lista_confiavel((("11", None, 1),), {"11"})
    assert lista_confiavel((), {""})  # anúncio sem variação: lista vazia é certa
    assert not lista_confiavel((), {"11"})  # variação vinculada e a Shopee sem lista


def _anuncio(importado_em, sem=()):
    return Anuncio(
        integration_id=None, external_id="888", plataforma="shopee",
        variacoes=(Variacao(sku="dg053.ci", ref="11"),)
        + tuple(Variacao(sku=s, vinculo=VINCULO_SEM, ref=r) for r, s in sem),
        cobertas=frozenset({"11"}), importado_em=importado_em,
    )


def test_com_modelos_substitui_quando_a_lista_e_mais_nova():
    agora = datetime.now(UTC)
    # A importação (velha) mostrava a xx1; a lista lida depois não tem: some.
    a = _com_modelos(_anuncio(agora - timedelta(hours=2), [("99", "xx1")]),
                     [("11", "dg053.ci", 1)], agora)
    assert [v.vinculo for v in a.variacoes] == [VINCULO_VIVO]
    assert a.modelos_atrasados is False


def test_com_modelos_soma_quando_a_importacao_e_mais_nova():
    agora = datetime.now(UTC)
    a = _com_modelos(_anuncio(agora, [("77", "nova")]),
                     [("11", "dg053.ci", 1), ("99", "xx1", 0)], agora - timedelta(hours=1))
    assert sorted(v.ref for v in a.variacoes if v.vinculo == VINCULO_SEM) == ["77", "99"]
    assert a.modelos_atrasados is True  # a 77 a lista não conhece: reler
    b = _com_modelos(_anuncio(agora, [("99", "xx1")]),
                     [("11", "dg053.ci", 1), ("99", "xx1", 0)], agora - timedelta(hours=1))
    assert b.modelos_atrasados is False


# ---- revisão de 16/11: fila das leituras, "Sincronizar agora", trilha -------------------


@pytest.mark.asyncio
async def test_loja_com_problema_nao_toma_a_vez_das_outras(monkeypatch):
    """A loja A com o token quebrado (toda leitura falha) e 20 anúncios na
    frente; a B saudável com 5. A conta que falha 5 seguidas para SEM gastar
    vaga, o teto conta chamadas, e as contas são intercaladas."""
    from uuid import uuid4

    a_id, b_id = uuid4(), uuid4()
    chamadas: list[str] = []

    class Quebrada:
        async def get_model_list(self, item_id):
            chamadas.append(f"A{item_id}")
            raise RuntimeError("token recusado")

    class Boa:
        async def get_model_list(self, item_id):
            chamadas.append(f"B{item_id}")
            return {"model": [PRETA]}

    clis = {a_id: Quebrada(), b_id: Boa()}

    async def _cli(integ, cache):
        return clis[integ]

    monkeypatch.setattr(flex_motor, "_cliente", _cli)
    monkeypatch.setattr(flex_motor, "_FOLGA_MODELOS_S", 0)

    def _an(iid, n):
        return Anuncio(integration_id=iid, external_id=str(n), plataforma="shopee",
                       cobertas=frozenset({"11"}))

    fila = [_an(a_id, i) for i in range(20)] + [_an(b_id, 100 + i) for i in range(5)]
    integs = {a_id: a_id, b_id: b_id}

    # Sem intercalar e com teto 6: os 15 de A pulados não gastam vaga.
    monkeypatch.setattr(flex_motor, "_TETO_MODELOS_SHOPEE", 6)
    resumo: dict = {}
    lidos, falhou = await flex_motor._ler_modelos(fila, integs, {}, resumo)
    assert (len(falhou), len(lidos)) == (5, 1)
    assert resumo["modelos_adiados"] == 4  # só os de B que ficaram para depois

    # Intercaladas (como a rodada faz) e teto 12: B inteira lida.
    chamadas.clear()
    monkeypatch.setattr(flex_motor, "_TETO_MODELOS_SHOPEE", 12)
    resumo = {}
    lidos, falhou = await flex_motor._ler_modelos(flex_motor._rodizio(fila), integs, {}, resumo)
    assert len(lidos) == 5 and len(falhou) == 5
    assert sum(1 for c in chamadas if c.startswith("A")) == 5
    assert "modelos_adiados" not in resumo


@pytest.mark.asyncio
async def test_leitura_que_falha_espera_e_nao_vira_ligar_erro(db, loja, monkeypatch):
    fake = loja["fake"]
    pedidos: list[str] = []

    async def _quebrada(item_id):
        pedidos.append(str(item_id))
        raise RuntimeError("shopee fora do ar")

    monkeypatch.setattr(fake, "get_model_list", _quebrada)
    await flex_motor.rodar_motor()
    est = (await _estados(db))["888"]
    # Uma falha só: a conferência antes de ligar não chama a Shopee de novo
    # logo depois da rodada ter falhado (nem conta a falha duas vezes).
    assert est.modelos_falhas == 1 and est.modelos_tentativa_em is not None
    assert pedidos == ["888"]
    assert est.modelos is None
    assert _ligacoes(fake) == []  # sem a lista não liga
    trilha = await _trilha(db, "888")
    assert ("ligar", "erro") not in trilha  # não chamou a Shopee: não é escrita
    # Espera crescente: a rodada seguinte nem tenta.
    antes = len(pedidos)
    await flex_motor.rodar_motor()
    assert len(pedidos) == antes
    # Passada a espera, tenta de novo; a Shopee volta e a xx1 aparece.
    monkeypatch.setattr(fake, "get_model_list", FakeShopee.get_model_list.__get__(fake))
    await db.execute(text(
        "UPDATE flex_anuncio_estado SET modelos_tentativa_em = now() - interval '7 hours',"
        " proxima_tentativa = NULL"
    ))
    await db.commit()
    await flex_motor.rodar_motor()
    est = (await _estados(db))["888"]
    assert est.desejado == "inelegivel" and est.modelos_falhas == 0
    assert _ligacoes(fake) == []


def test_barrado_antes_nao_conta_como_ligado_pelo_davinci():
    t0 = datetime(2026, 11, 16, 12, tzinfo=UTC)
    eventos = [("ligar", "ignorado", "desligado", t0),
               ("ler", "ok", "ligado", t0 + timedelta(minutes=10))]
    assert flex_motor._nosso_pela_trilha(eventos) is False


@pytest.mark.asyncio
async def test_sincronizar_agora_rele_a_lista_e_libera_quem_apagou_a_xx1(db, loja):
    fake = loja["fake"]
    await flex_motor.rodar_motor()
    assert (await _estados(db))["888"].desejado == "inelegivel"
    fake.modelos["888"] = [dict(PRETA)]  # a xx1 foi apagada no Seller Center
    await db.execute(
        text("UPDATE flex_anuncio_estado SET modelos_em = now() - interval '20 minutes'")
    )
    await db.commit()
    await flex_motor.rodar_motor()  # a rodada comum: a lista de 20 min ainda vale
    assert (await _estados(db))["888"].desejado == "inelegivel"
    await flex_motor.rodar_motor(reler_contas=True, origem="sincronizar")
    est = (await _estados(db))["888"]
    assert (est.desejado, est.observado) == ("ligado", "ligado")
    assert len(_ligacoes(fake)) == 1


def test_espera_nao_estoura_com_muitas_falhas():
    """Revisão de 16/11: com 38 falhas seguidas o timedelta estourava e
    derrubava toda rodada do Flex."""
    for n in (0, 1, 5, 37, 38, 100, 10**6):
        assert flex_motor._espera(n) <= timedelta(hours=6)
    assert flex_motor._espera(1) == timedelta(minutes=15)


def test_sincronizar_agora_le_primeiro_quem_pode_ser_liberado():
    """No "Sincronizar agora" os barrados só por variação sem vínculo vão na
    frente dos já ligados (que o portão confere de qualquer jeito)."""
    from uuid import uuid4

    agora = datetime.now(UTC)
    conta = uuid4()
    cfg = flex_motor.ConfigFlex(n_liga=3, n_desliga=1, kits=True, max_anuncios_por_familia=0)
    saldos = {"dg053.sp": flex_motor.SaldoSp("dg053.sp", 5)}

    def _an(ext, sem, lida_ha):
        vs = (Variacao(sku="dg053.ci", ref="11", estoque_publicado=3),)
        if sem:
            vs += (Variacao(sku="xx1", vinculo=VINCULO_SEM, ref="99", estoque_publicado=0),)
        return Anuncio(integration_id=conta, external_id=ext, plataforma="shopee",
                       variacoes=vs, cobertas=frozenset({"11"}),
                       modelos_em=agora - lida_ha)

    ligado_velho = _an("1", False, timedelta(hours=2))
    barrado_novo = _an("2", True, timedelta(minutes=40))
    anuncios = [ligado_velho, barrado_novo]
    decisoes = {a.chave: flex_motor.decidir(a, saldos, cfg) for a in anuncios}
    assert decisoes[barrado_novo.chave].desejado == "inelegivel"
    fila = flex_motor._escolher_modelos(anuncios, decisoes, saldos, cfg, None, agora, fresco=True)
    assert [a.external_id for a in fila] == ["2", "1"]
    # Na rodada comum: o de 40 min ainda vale (3 h); só o velho é relido.
    fila = flex_motor._escolher_modelos(anuncios, decisoes, saldos, cfg, None, agora)
    assert [a.external_id for a in fila] == []
