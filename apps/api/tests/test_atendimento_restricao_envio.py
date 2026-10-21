# ruff: noqa: F811 — fixtures importadas dos testes da ficha
"""A RESTRIÇÃO DE ENVIO POR REGIÃO para o agente (20/10/2026, `restricao_envio.py`).

- a frase do anúncio (descrição CRUA, título): a negação seguida do estado,
  nas formas reais ("Não enviamos para o Rio de Janeiro", "NÃO ENTREGAMOS NO
  RJ", "Proibido envio para RJ", "…exceto RJ") — e nunca a frase positiva
  ("…inclusive RJ") nem a negação que não restringe ("não enviamos SÓ…");
- a regra fixa Apple → RJ da Shopee e a restrição da loja (tela Lojas),
  pelas MESMAS funções e constantes do faturamento automático;
- a Kia (produção, 20/10/2026): "RJ" com a exceção de valor 1500 (abaixo
  disso o pedido vai) e a ficha da loja ligada só pelo `stores`;
- o pós-venda só sabe "sim/não" — a UF do comprador nunca sai.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Company, Listing, Product, ProductLink, Store, StoreInfo
from app.models.company import Marketplace
from app.models.enums import IntegrationPlatform
from app.services import nf_auto_enfileirar
from app.services.atendimento import enriquecer, ficha
from app.services.atendimento import restricao_envio as r
from tests.test_atendimento_ficha_loja import (  # noqa: F401 — o Redis falso é fixture
    SHOPEE,
    RedisMem,
    _loja,
    redis_mem,
)

# ─────────────── a frase do anúncio (pura) ───────────────


@pytest.mark.parametrize(
    ("frase", "ufs"),
    [
        ("Não enviamos para o Rio de Janeiro", ["RJ"]),
        ("Não enviamos para RJ.", ["RJ"]),
        ("Proibido envio para RJ", ["RJ"]),
        ("NÃO ENTREGAMOS NO RJ", ["RJ"]),
        ("Não atendemos o estado do Rio de Janeiro", ["RJ"]),
        (
            "Conforme informado no anúncio, não realizamos envios para o Rio de Janeiro.",
            ["RJ"],
        ),
        ("Enviamos para todo o Brasil, exceto RJ e ES.", ["RJ", "ES"]),
        ("Não enviamos para RJ, SP e MG", ["RJ", "SP", "MG"]),
        ("Não enviamos este produto para o estado de São Paulo", ["SP"]),
        ("Não enviamos para Pará e Amapá", ["PA", "AP"]),
        ("não enviamos para rj", ["RJ"]),
        ("Produto não é enviado para o Amazonas", ["AM"]),
        # "SE" (Sergipe) em caixa alta depois do RJ é a conjunção: fica fora.
        ("NÃO ENVIAMOS PARA O RJ, SE TIVER DÚVIDA CHAME", ["RJ"]),
        # Frases que NÃO restringem.
        ("enviamos para todo o Brasil, inclusive RJ", []),
        ("Não enviamos apenas para SP, enviamos para todo o Brasil", []),
        ("Não temos loja física no RJ", []),
        ("Não deixe de conferir: enviamos para RJ", []),
        ("Não enviamos para fora do Brasil", []),
        ("Não perca: entregamos no RJ em 2 dias", []),
        ("Não enviamos produtos com bateria para o Amazonas", []),
        ("não para de funcionar mesmo no Pará", []),
        ("Envio rápido para todo o Brasil. Não esqueça de avaliar!", []),
        # Revisão de 20/10/2026: o Pará sem acento na lista, o "p/", e a
        # exceção que só vale em frase de cobertura (não de prazo ou frete).
        ("NAO ENVIAMOS PARA OS ESTADOS DA BAHIA E DO PARA!", ["BA", "PA"]),
        ("NÃO ENVIAMOS PARA O ESTADO DO PARA", ["PA"]),
        ("NÃO ENVIAMOS P/ RJ", ["RJ"]),
        ("Não enviamos para os estados da Bahia e do Rio de Janeiro", ["BA", "RJ"]),
        ("Enviamos para todos os estados, menos RJ", ["RJ"]),
        ("Entrega em 2 dias úteis exceto RJ", []),
        (
            "Prazo de entrega de 3 dias, exceto para o Rio de Janeiro onde pode levar até 7 dias",
            [],
        ),
        ("Frete grátis para todo o Brasil, exceto RJ", []),
        ("Pelo menos SP tem entrega rápida", []),
    ],
)
def test_frases_do_anuncio(frase, ufs):
    assert r.ufs_negadas(frase) == ufs


def test_restricao_no_texto_cita_a_frase_sem_contato():
    ufs, trecho = r.restricao_no_texto(
        "Celular X",
        "Linha 1<br>Não enviamos para o Rio de Janeiro. Chame no whats 11 99999-9999<br>Envio 24h",
    )
    assert (ufs, trecho) == (["RJ"], "Não enviamos para o Rio de Janeiro.")
    # A frase com contato fora da loja: as UFs valem, o trecho não vai ao modelo.
    assert r.restricao_no_texto(None, "NÃO ENTREGAMOS NO RJ - whatsapp 11999999999") == (
        ["RJ"],
        None,
    )
    assert r.restricao_no_texto("Mala de bordo 20", None, "") == ([], None)


def test_regra_apple_shopee_pelas_constantes_do_faturamento():
    assert r.regra_apple_shopee("shopee", "Apple iPhone 15 128GB") == [
        nf_auto_enfileirar._RESTRICAO_UF
    ]
    assert r.regra_apple_shopee(IntegrationPlatform.SHOPEE, "Fone AirPods Pro") == ["RJ"]
    assert r.regra_apple_shopee("ml", "Apple iPhone 15") == []
    assert r.regra_apple_shopee("shopee", "Uranyx A18 Pro Max") == []


def test_loja_avaliada_pela_funcao_do_faturamento():
    kia = r.RestricaoLoja(frozenset({"RJ"}), ({"tipo": "valor", "valor": 1500.0, "termos": None},))
    itens = [("dg089.ci+a003.ci", "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata")]
    # Sem preço: depende do valor do pedido (o valor nunca vai ao modelo).
    assert r.avaliar_loja_no_anuncio([kia], itens, []) == ([], ["RJ"])
    # Abaixo do limite: duas unidades (ou a variação mais cara) passam dele, e
    # o faturamento olha o TOTAL do pedido — depende do pedido, nunca some.
    assert r.avaliar_loja_no_anuncio([kia], itens, [899.9]) == ([], ["RJ"])
    assert r.avaliar_loja_no_anuncio([kia], itens, [955.0]) == ([], ["RJ"])
    assert r.avaliar_loja_no_anuncio([kia], itens, [1999.0]) == (["RJ"], [])
    assert r.avaliar_loja_no_anuncio([kia], itens, [899.9, 1999.0]) == ([], ["RJ"])
    # A loja do ML com exceção de palavra ("mala"): a mala vai, o celular não.
    ml = r.RestricaoLoja(
        frozenset({"ES", "MA", "PI", "RS"}), ({"tipo": "palavra", "termos": ["mala"]},)
    )
    assert r.avaliar_loja_no_anuncio([ml], [("b026", "Mala de bordo 20")], []) == ([], [])
    assert r.avaliar_loja_no_anuncio([ml], [("dg1", "Celular")], []) == (
        ["ES", "MA", "PI", "RS"],
        [],
    )


# ─────────────── banco e Redis ───────────────

ITEM = "58265493964"
TITULO = "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium"


async def _kia(
    db: AsyncSession, make_user, *, excecoes: list | None, ufs=("RJ",), bling: int = 205857216
):
    """A Kia como em produção: a ficha da loja SEM `integration_id`, ligada
    pelo `stores.bling_store_id`."""
    user = await make_user()
    integ = await _loja(db, user, SHOPEE, "kia")
    empresa = Company(razao_social="Kia Teste LTDA", apelido="kia")
    db.add(empresa)
    await db.flush()
    db.add(
        Store(
            company_id=empresa.id,
            marketplace=Marketplace.SHOPEE,
            bling_store_id=bling,
            integration_id=integ.id,
        )
    )
    db.add(
        StoreInfo(
            user_id=user.id,
            platform="shopee",
            bling_store_id=str(bling),
            uf_restrictions=list(ufs),
            excecoes=excecoes,
        )
    )
    produto = Product(user_id=user.id, sku="dg089.ci+a003.ci", name="Uranyx A18 - Prata", stock=5)
    db.add(produto)
    await db.flush()
    db.add(
        ProductLink(
            user_id=user.id,
            product_id=produto.id,
            integration_id=integ.id,
            platform=IntegrationPlatform.SHOPEE,
            external_id=ITEM,
            variation_id="1",
            stock=5,
        )
    )
    await db.commit()
    return user, integ


def _fresca(redis_mem: RedisMem, integ, descricao: str | None, titulo: str = TITULO) -> None:
    redis_mem.dados[ficha.chave_ficha("shopee", integ.id, ITEM)] = json.dumps(
        {
            "titulo": titulo,
            "descricao": descricao,
            "raw": {},
            "buscada_em": "2026-10-20T10:00:00+00:00",
        }
    )


async def test_restricao_da_loja_pelo_stores_e_inerte_sem_excecao(db: AsyncSession, make_user):
    _, integ = await _kia(db, make_user, excecoes=[{"tipo": "valor", "valor": 1500.0}])
    (loja,) = await r.restricao_da_loja(db, integ.id)
    assert loja.ufs == frozenset({"RJ"}) and loja.regras == ({"tipo": "valor", "valor": 1500.0},)
    # Sem exceção, a Restrição é inerte no faturamento: aqui também.
    _, outra = await _kia(db, make_user, excecoes=None, bling=7)
    assert await r.restricao_da_loja(db, outra.id) == []
    assert await r.restricao_da_loja(db, None) == []


async def test_anuncio_da_kia_rj_conforme_o_pedido_e_pelo_preco_do_cartao(
    db: AsyncSession, make_user, redis_mem: RedisMem
):
    _, integ = await _kia(db, make_user, excecoes=[{"tipo": "valor", "valor": 1500.0}])
    _fresca(redis_mem, integ, "Uranyx S5 + Fone UFB10\nEnvio imediato")

    sem_preco = await r.restricao_do_anuncio(db, SHOPEE, integ.id, ITEM, TITULO)
    assert sem_preco == {
        "ufs": [],
        "fonte": "loja",
        "fontes": ["loja"],
        "trecho": None,
        "ufs_conforme_pedido": ["RJ"],
    }
    assert "1500" not in json.dumps(sem_preco)
    # O cartão do anúncio (o enriquecimento) traz o preço: abaixo da exceção,
    # ainda depende do pedido (o preço do cartão é o menor; o faturamento
    # olha o total — revisão de 20/10/2026).
    redis_mem.dados[enriquecer.chave_produto("shopee", integ.id, ITEM)] = json.dumps(
        {"preco": 899.9}
    )
    assert await r.restricao_do_anuncio(db, SHOPEE, integ.id, ITEM, TITULO) == sem_preco
    redis_mem.dados[enriquecer.chave_produto("shopee", integ.id, ITEM)] = json.dumps(
        {"preco": 1899.9}
    )
    caro = await r.restricao_do_anuncio(db, SHOPEE, integ.id, ITEM, TITULO)
    assert caro["ufs"] == ["RJ"] and caro["ufs_conforme_pedido"] == []


async def test_anuncio_que_diz_que_nao_envia_e_a_regra_apple(
    db: AsyncSession, make_user, redis_mem: RedisMem
):
    _, integ = await _kia(db, make_user, excecoes=None)
    _fresca(redis_mem, integ, "Linha 1\nNão enviamos para o Rio de Janeiro.\nGarantia de 90 dias")

    res = await r.restricao_do_anuncio(db, SHOPEE, integ.id, ITEM, TITULO)
    assert res == {
        "ufs": ["RJ"],
        "fonte": "anuncio",
        "fontes": ["anuncio"],
        "trecho": "Não enviamos para o Rio de Janeiro.",
        "ufs_conforme_pedido": [],
    }
    # Apple na Shopee: a regra fixa, sem a frase no anúncio.
    _fresca(redis_mem, integ, "Lacrado", titulo="Apple iPhone 15 128GB")
    apple = await r.restricao_do_anuncio(db, SHOPEE, integ.id, ITEM, None)
    assert (apple["ufs"], apple["fonte"], apple["trecho"]) == (["RJ"], "regra_apple_shopee", None)
    # Frase positiva: nada.
    _fresca(redis_mem, integ, "Enviamos para todo o Brasil, inclusive RJ.")
    assert await r.restricao_do_anuncio(db, SHOPEE, integ.id, ITEM, TITULO) is None


async def test_sem_ficha_fresca_le_o_catalogo_com_o_mesmo_titulo(
    db: AsyncSession, make_user, redis_mem: RedisMem
):
    user, integ = await _kia(db, make_user, excecoes=None)
    db.add(
        Listing(
            user_id=user.id,
            integration_id=integ.id,
            platform=IntegrationPlatform.SHOPEE,
            external_id=ITEM,
            title=TITULO,
            description="Produto novo.\nNÃO ENTREGAMOS NO RJ",
            imported_at=datetime.now(UTC),
        )
    )
    await db.commit()
    res = await r.restricao_do_anuncio(db, SHOPEE, integ.id, ITEM, TITULO)
    assert res["ufs"] == ["RJ"] and res["trecho"] == "NÃO ENTREGAMOS NO RJ"
    # O catálogo de outro título (antes da renomeação) não vale.
    assert await r.restricao_do_anuncio(db, SHOPEE, integ.id, ITEM, "Outro título") is None
    # Redis fora do ar: segue para o catálogo.
    redis_mem.fora = True
    assert (await r.restricao_do_anuncio(db, SHOPEE, integ.id, ITEM, TITULO))["ufs"] == ["RJ"]


async def test_pos_venda_so_diz_sim_ou_nao(db: AsyncSession, make_user):
    await _kia(db, make_user, excecoes=[{"tipo": "valor", "valor": 1500.0}])

    def pedido(numero: str, uf: str, valor: float, *itens: str, loja: str = "205857216"):
        for i, nome in enumerate(itens):
            db.add(
                BlingOrder(
                    numero=numero,
                    numeroloja=f"MK{numero}",
                    loja=loja,
                    uf_destino=uf,
                    valorbase=valor,
                    item_index=i,
                    item_codigo=f"sku{i}",
                    item_descricao=nome,
                )
            )

    pedido("1001", "RJ", 899.9, "Uranyx A18 Pro Max")
    pedido("1002", "RJ", 1999.0, "Uranyx A18 Pro Max")
    pedido("1003", " rj ", 300.0, "Capa", "Apple iPhone 15")
    pedido("1004", "SP", 1999.0, "Apple iPhone 15")
    pedido("1005", "", 1999.0, "Apple iPhone 15")
    await db.commit()

    assert await r.pedido_para_uf_restrita(db, numero="1001") is False  # abaixo de 1500, vai
    assert await r.pedido_para_uf_restrita(db, numero="1002") is True
    assert await r.pedido_para_uf_restrita(db, numeroloja="MK1003") is True  # Apple → RJ
    assert await r.pedido_para_uf_restrita(db, numero="1004") is False
    assert await r.pedido_para_uf_restrita(db, numero="1004", ufs_do_anuncio=["sp"]) is True
    assert await r.pedido_para_uf_restrita(db, numero="1005") is False  # sem UF
    assert await r.pedido_para_uf_restrita(db, numero="9999") is False
    assert await r.pedido_para_uf_restrita(db) is False
