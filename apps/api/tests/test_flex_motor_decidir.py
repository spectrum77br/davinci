"""A regra do Flex por anúncio, pura (projeto Flex, etapa 3 — services/flex_motor).

Tabela de casos das DECISÕES de 02/10/2026: variações, kits, histerese pelo
estado observado/anterior, estoque desconhecido nunca liga, recusa da
plataforma e o limite de anúncios por família. Nada de banco nem de API.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.services import flex_motor as fm
from app.services.flex_motor import (
    Anuncio,
    ConfigFlex,
    SaldoSp,
    Variacao,
    analisar_sku,
    decidir,
    decidir_lote,
)

CONTA = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
CONTA2 = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
CFG = ConfigFlex(n_liga=3, n_desliga=1, kits=False, max_anuncios_por_familia=2)


def _anuncio(*skus, ext="MLB1", observado=None, anterior=None, recusa=None, estoques=None, **kw):
    estoques = estoques or [None] * len(skus)
    return Anuncio(
        integration_id=kw.pop("conta", CONTA),
        external_id=ext,
        plataforma=kw.pop("plataforma", "ml"),
        variacoes=tuple(
            Variacao(sku=s, ativo=kw.get("ativo", True), estoque_publicado=e)
            for s, e in zip(skus, estoques, strict=True)
        ),
        observado=observado,
        desejado_anterior=anterior,
        recusa=recusa,
        desde=kw.get("desde"),
    )


def _saldos(**valores):
    """dg053_sp=5 → {"dg053.sp": SaldoSp(estoque=5)}; tupla = (estoque, pendentes)."""
    out = {}
    for chave, v in valores.items():
        sku = chave.replace("_", ".").replace("..", "+")
        est, pend = v if isinstance(v, tuple) else (v, 0)
        out[sku] = SaldoSp(sku_sp=sku, estoque=est, pendentes=pend)
    return out


# ---- analisar_sku ------------------------------------------------------------


@pytest.mark.parametrize(
    ("sku", "kits", "esperado"),
    [
        ("dg053.ci", False, ("dg053", "dg053.sp")),
        ("DG053.RA", False, ("dg053", "dg053.sp")),
        ("dg053.sp", False, ("dg053", "dg053.sp")),
        (" dg053.pi ", False, ("dg053", "dg053.sp")),
        # Kit: só com flex_kits. O .sp troca só os pedaços com lote — o
        # `irmaos` montaria dg053.sp+x1.sp, que não existe (defeito da análise).
        ("dg053.ci+a001.ci", True, ("dg053+a001", "dg053.sp+a001.sp")),
        ("dg053.ci+x1", True, ("dg053+x1", "dg053.sp+x1")),
    ],
)
def test_analisar_sku_monta_o_sp_certo(sku, kits, esperado):
    assert analisar_sku(sku, kits=kits) == esperado


@pytest.mark.parametrize(
    ("sku", "trecho"),
    [
        (None, "sem produto"),
        ("", "sem produto"),
        ("b009", "sem lote"),  # mala sem lote
        ("b009.8.12.20.24", "sem lote"),  # número não é lote
        ("a017.us", "sem lote"),  # usado não vira novo
        ("a017.cd", "sem lote"),  # Centro de Distribuição não é lote de venda
        ("dg053.ci+a001.ra", "sem lote"),  # kit misturado
        ("fake.dg053.ci", "sem lote"),
        ("dg053.ci+a001.ci", "kit"),  # kits fora (flex_kits=False)
    ],
)
def test_analisar_sku_recusa(sku, trecho):
    r = analisar_sku(sku, kits=False)
    assert isinstance(r, str)
    assert trecho in r


# ---- decidir: um anúncio ------------------------------------------------------


@pytest.mark.parametrize(
    ("saldo", "observado", "anterior", "esperado"),
    [
        # Liga com saldo >= n_liga (3)
        (5, None, None, "ligado"),
        (3, "desligado", None, "ligado"),
        # Entre n_desliga (1) e n_liga (3): mantém o que está (histerese)
        (2, "desligado", None, "desligado"),
        (2, "ligado", None, "ligado"),
        (1, "ligado", None, "ligado"),
        # Sem leitura: vale a decisão anterior
        (2, None, "ligado", "ligado"),
        (2, None, "desligado", "desligado"),
        (2, None, None, "desligado"),
        # O observado manda sobre a decisão anterior
        (2, "desligado", "ligado", "desligado"),
        # Abaixo de n_desliga: desliga mesmo ligado
        (0, "ligado", "ligado", "desligado"),
        (-3, "ligado", None, "desligado"),
    ],
)
def test_histerese(saldo, observado, anterior, esperado):
    a = _anuncio("dg053.ci", observado=observado, anterior=anterior)
    d = decidir(a, _saldos(dg053_sp=saldo), CFG)
    assert d.desejado == esperado
    assert d.saldo == saldo
    assert d.familias == ("dg053",)


def test_saldo_flex_desconta_os_pedidos_flex_fora_do_sp():
    a = _anuncio("dg053.ci")
    # 4 no .sp, 2 vendidos por Flex ainda reservando no .ci → saldo 2.
    d = decidir(a, _saldos(dg053_sp=(4, 2)), CFG)
    assert (d.desejado, d.saldo) == ("desligado", 2)
    assert decidir(a, _saldos(dg053_sp=(5, 2)), CFG).desejado == "ligado"


@pytest.mark.parametrize("observado", [None, "ligado", "desligado"])
def test_estoque_desconhecido_nunca_liga(observado):
    a = _anuncio("dg053.ci", observado=observado, anterior="ligado")
    # Sem o .sp na tabela de saldos (não existe ativo)…
    d = decidir(a, {}, CFG)
    assert d.desejado == "inelegivel"
    assert "nunca liga" in d.motivo
    # …ou com estoque None.
    d = decidir(a, {"dg053.sp": SaldoSp("dg053.sp", estoque=None)}, CFG)
    assert d.desejado == "inelegivel"


def test_kit_fica_fora_sem_flex_kits_e_entra_com():
    a = _anuncio("dg053.ci+a001.ci")
    saldos = {"dg053.sp+a001.sp": SaldoSp("dg053.sp+a001.sp", estoque=9)}
    d = decidir(a, saldos, CFG)
    assert d.desejado == "inelegivel"
    assert "kit" in d.motivo
    d = decidir(a, saldos, ConfigFlex(kits=True))
    assert (d.desejado, d.familias) == ("ligado", ("dg053+a001",))


def test_todas_as_variacoes_vendaveis_precisam_de_sp():
    saldos = _saldos(dg053_sp=5, dg054_sp=6)
    # Duas cores com .sp: liga.
    d = decidir(_anuncio("dg053.ci", "dg054.ra"), saldos, CFG)
    assert (d.desejado, d.familias, d.saldo) == ("ligado", ("dg053", "dg054"), 5)
    # Uma cor sem .sp ativo: não liga (a cor sem SP seria vendida por Flex).
    d = decidir(_anuncio("dg053.ci", "dg060.ci"), saldos, CFG)
    assert d.desejado == "inelegivel"
    assert "dg060.sp" in d.motivo
    # Uma variação de kit no meio: o anúncio inteiro fica fora.
    d = decidir(_anuncio("dg053.ci", "dg053.ci+a001.ci"), saldos, CFG)
    assert d.desejado == "inelegivel"
    # A família mais fraca decide (saldo 2 < 3).
    d = decidir(_anuncio("dg053.ci", "dg054.ci"), _saldos(dg053_sp=5, dg054_sp=2), CFG)
    assert (d.desejado, d.saldo) == ("desligado", 2)
    assert "dg054.sp" in d.motivo


def test_variacao_sem_estoque_publicado_nao_conta():
    saldos = _saldos(dg053_sp=5)
    # A cor zerada (sem .sp) não está à venda: não impede o Flex das outras.
    d = decidir(_anuncio("dg053.ci", "dg060.ci", estoques=[10, 0]), saldos, CFG)
    assert d.desejado == "ligado"
    # Estoque nunca enviado (None) conta como à venda — não se sabe.
    d = decidir(_anuncio("dg053.ci", "dg060.ci", estoques=[10, None]), saldos, CFG)
    assert d.desejado == "inelegivel"
    # Nada à venda: desliga (o Flex não teria o que vender).
    d = decidir(_anuncio("dg053.ci", estoques=[0]), saldos, CFG)
    assert (d.desejado, d.motivo) == ("desligado", "nenhuma variação com estoque publicado")


def test_variacao_com_produto_inativo_ou_sem_produto():
    saldos = _saldos(dg053_sp=5)
    d = decidir(_anuncio("dg053.ci", ativo=False), saldos, CFG)
    assert d.desejado == "inelegivel"
    assert "inativo" in d.motivo
    d = decidir(_anuncio(None), saldos, CFG)
    assert d.desejado == "inelegivel"


def test_anuncio_sem_vinculo_e_negado():
    d = decidir(_anuncio(), _saldos(dg053_sp=50), CFG)
    assert d.desejado == "inelegivel"
    assert "sem vínculo" in d.motivo


def test_recusa_da_plataforma_segura_so_o_ligar():
    saldos = _saldos(dg053_sp=5)
    d = decidir(_anuncio("dg053.ci", recusa="403 item down"), saldos, CFG)
    assert d.desejado == "inelegivel"
    assert "403 item down" in d.motivo
    # Quando a regra já é desligar, a recusa não muda nada.
    d = decidir(_anuncio("dg053.ci", recusa="403 item down"), _saldos(dg053_sp=0), CFG)
    assert d.desejado == "desligado"


def test_config_normaliza():
    c = ConfigFlex(n_liga=0, n_desliga=5, max_anuncios_por_familia=-1)
    assert (c.n_liga, c.n_desliga, c.max_anuncios_por_familia) == (1, 1, 0)
    c = ConfigFlex(n_liga=3, n_desliga=7)
    assert c.n_desliga == 3  # nunca acima do de ligar


# ---- decidir_lote: limite de anúncios por família --------------------------------


def test_limite_por_familia_e_deterministico():
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    anuncios = [
        _anuncio("dg053.ci", ext="MLB4", desde=t0),  # mais antigo
        _anuncio("dg053.ci", ext="MLB3", desde=t0 + timedelta(days=3)),
        _anuncio("dg053.ci", ext="MLB2", anterior="ligado", desde=t0 + timedelta(days=9)),
        _anuncio("dg053.ci", ext="MLB1", observado="ligado", desde=t0 + timedelta(days=9)),
    ]
    dec = decidir_lote(anuncios, _saldos(dg053_sp=10), CFG)
    quer = {k[1]: d.desejado for k, d in dec.items()}
    # 1º já ligado na plataforma, 2º já queria ligado; os outros esperam.
    assert quer == {"MLB1": "ligado", "MLB2": "ligado", "MLB3": "desligado", "MLB4": "desligado"}
    assert "limite de 2" in dec[(CONTA, "MLB3")].motivo
    # A ordem de entrada não muda o resultado.
    assert decidir_lote(list(reversed(anuncios)), _saldos(dg053_sp=10), CFG) == dec


def test_limite_aprovado_por_pessoa_passa_na_frente():
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    anuncios = [
        _anuncio("dg053.ci", ext="A", anterior="ligado", desde=t0),
        _anuncio("dg053.ci", ext="B", anterior="ligado", desde=t0 + timedelta(days=1)),
        # Aprovado depois de uma recusa: a regra anterior era inelegível, mas
        # a escolha explícita da pessoa vale mais que a ordem.
        replace(_anuncio("dg053.ci", ext="C", anterior="inelegivel", desde=t0 + timedelta(days=5)),
                aprovado=True),
    ]
    dec = decidir_lote(anuncios, _saldos(dg053_sp=10), CFG)
    assert {k[1]: d.desejado for k, d in dec.items()} == {
        "A": "ligado",
        "B": "desligado",
        "C": "ligado",
    }


def test_limite_desempata_por_antiguidade_e_id():
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    anuncios = [
        _anuncio("dg053.ci", ext="MLB9", desde=t0 + timedelta(days=1)),
        _anuncio("dg053.ci", ext="MLB8", desde=t0),
        _anuncio("dg053.ci", ext="MLB7", conta=CONTA2, desde=t0 + timedelta(days=1)),
    ]
    cfg = ConfigFlex(max_anuncios_por_familia=2)
    dec = decidir_lote(anuncios, _saldos(dg053_sp=10), cfg)
    ligados = sorted(k[1] for k, d in dec.items() if d.desejado == "ligado")
    # MLB8 (mais antigo) + MLB9 (mesma data que MLB7, conta de id menor).
    assert ligados == ["MLB8", "MLB9"]


def test_limite_conta_cada_familia_do_anuncio():
    saldos = _saldos(dg053_sp=10, dg054_sp=10)
    anuncios = [
        _anuncio("dg053.ci", ext="A", observado="ligado"),
        _anuncio("dg053.ci", "dg054.ci", ext="B"),  # dg053 já tem 1 → cabe
        _anuncio("dg054.ci", ext="C"),  # dg054 já tem 1 (B) → cabe
        _anuncio("dg053.ci", ext="D"),  # dg053 cheia (A, B)
    ]
    dec = decidir_lote(anuncios, saldos, CFG)
    assert {k[1]: d.desejado for k, d in dec.items()} == {
        "A": "ligado",
        "B": "ligado",
        "C": "ligado",
        "D": "desligado",
    }
    assert "dg053" in dec[(CONTA, "D")].motivo


def test_limite_zero_nao_liga_nada_e_familias_diferentes_nao_brigam():
    anuncios = [_anuncio("dg053.ci", ext="A"), _anuncio("dg054.ci", ext="B")]
    saldos = _saldos(dg053_sp=10, dg054_sp=10)
    dec = decidir_lote(anuncios, saldos, ConfigFlex(max_anuncios_por_familia=0))
    assert {d.desejado for d in dec.values()} == {"desligado"}
    dec = decidir_lote(anuncios, saldos, ConfigFlex(max_anuncios_por_familia=1))
    assert {d.desejado for d in dec.values()} == {"ligado"}


# ---- saldo: pedidos Flex que ainda devem ao .sp ---------------------------------


def test_demanda_dos_pedidos_flex_por_peca():
    demanda = fm._demanda_por_peca(
        [
            ("dg053.ci", 2),
            ("dg053.sp", 1),  # já no .sp: o saldo virtual já desconta
            ("DG053.RA+A001.RA", 1),  # kit: cada peça com lote
            ("b009", 4),  # sem lote: não é do .sp
            ("a017.us", 1),  # usado: não é lote de venda
            (None, 3),
        ]
    )
    assert dict(demanda) == {"dg053": 3, "a001": 1}
    assert fm._pendentes("dg053.sp", demanda) == 3
    assert fm._pendentes("a001.sp", demanda) == 1
    # Kit .sp: a peça mais pedida (o estoque do kit já é o da mais escassa).
    assert fm._pendentes("dg053.sp+a001.sp", demanda) == 3
    assert fm._pendentes("dg099.sp", demanda) == 0


def test_id_do_anuncio_shopee_item_modelo():
    assert fm._id_anuncio("shopee", "123_456") == "123"
    assert fm._id_anuncio("shopee", " 123 ") == "123"
    assert fm._id_anuncio("ml", "MLB123") == "MLB123"
