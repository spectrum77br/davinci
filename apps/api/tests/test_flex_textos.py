"""Motivos do Flex em português claro (services/flex_textos — etapa 4, telas).

Cada ramo da regra é rodado de verdade (`flex_motor.decidir`/`decidir_lote`)
e o motivo cru passa por `motivo_claro`: se o motor ganhar um motivo novo (ou
mudar a frase) sem tradução, o teste avisa — a tela mostraria texto técnico.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from app.services import flex_motor as fm
from app.services.flex_motor import Anuncio, ConfigFlex, SaldoSp, Variacao, decidir, decidir_lote
from app.services.flex_textos import motivo_claro

CONTA = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
CFG = ConfigFlex(n_liga=3, n_desliga=1, kits=False, max_anuncios_por_familia=1)
# Palavras de programador que não podem chegar à tela do dono.
_TECNICO = ("histerese", "flex_kits", "saldo Flex", "vínculo vivo", "nunca liga", "nesta fase")


def _an(*skus, ext="MLB1", observado=None, ativo=True, estoques=None, recusa=None):
    estoques = estoques or [None] * len(skus)
    return Anuncio(
        integration_id=CONTA,
        external_id=ext,
        plataforma="ml",
        variacoes=tuple(
            Variacao(sku=s, ativo=ativo, estoque_publicado=e)
            for s, e in zip(skus, estoques, strict=True)
        ),
        observado=observado,
        recusa=recusa,
    )


def _sp(**v):
    return {
        k.replace("_", "."): SaldoSp(sku_sp=k.replace("_", "."), estoque=e) for k, e in v.items()
    }


def _claro(motivo: str) -> str:
    texto = motivo_claro(motivo)
    assert texto and texto != motivo, f"sem tradução: {motivo!r}"
    for palavra in _TECNICO:
        assert palavra not in texto, f"{palavra!r} em {texto!r}"
    return texto


@pytest.mark.parametrize(
    ("anuncio", "saldos", "espera"),
    [
        (_an(), {}, "não está ligado a nenhum produto"),
        (_an("dg053.ci", estoques=[0]), _sp(dg053_sp=5), "Nenhuma variação"),
        (
            _an("dg053.ci", "dg060.ci", estoques=[5, 0]),
            _sp(dg053_sp=5),
            "A variação dg060.ci está sem estoque agora, mas não pode ter Flex (o produto "
            "dg060.sp não existe (ou está inativo) no DaVinci). Quando o estoque voltar",
        ),
        (
            _an("dg053.ci", "dg053.ci+a001.ci", estoques=[5, 0]),
            _sp(dg053_sp=5),
            "não pode ter Flex (o produto dg053.ci+a001.ci é um kit",
        ),
        (
            Anuncio(CONTA, "MLB1", "ml", variacoes=(
                Variacao(sku="dg053.ci"),
                Variacao(sku="x777.ci", vinculo=fm.VINCULO_MORTO, ref="2"),
            )),
            _sp(dg053_sp=5),
            "A variação x777.ci do anúncio não recebe mais o estoque do DaVinci",
        ),
        (
            Anuncio(CONTA, "MLB1", "ml", variacoes=(
                Variacao(sku="dg053.ci"),
                Variacao(sku=None, vinculo=fm.VINCULO_SEM, ref="333"),
            )),
            _sp(dg053_sp=5),
            "A variação id 333 do anúncio não está ligada a nenhum produto do DaVinci",
        ),
        (_an("dg053.ci", ativo=False), {}, "O produto dg053.ci está inativo"),
        (_an(None), {}, "Uma variação do anúncio não tem produto"),
        (_an("b009"), {}, "não é de um lote de venda"),
        (_an("dg053.ci+a001.ci"), {}, "é um kit"),
        (_an("dg053.ci"), {}, "O produto dg053.sp não existe"),
        (_an("dg053.ci"), _sp(dg053_sp=0), "Sem peça livre em São Bernardo do Campo (dg053.sp)"),
        (_an("dg053.ci"), _sp(dg053_sp=5), "5 peças livres em São Bernardo"),
        (_an("dg053.ci"), _sp(dg053_sp=1), "Só 1 peça livre em São Bernardo"),
        (
            _an("dg053.ci", observado="ligado"),
            _sp(dg053_sp=2),
            "continua até ficar com menos de 1",
        ),
        (_an("dg053.ci", recusa="403 item down"), _sp(dg053_sp=5), "não aceitou ligar"),
        # Revisão de 02/10/2026: a conta, o status do anúncio, o anúncio que o
        # DaVinci não conhece e a Shopee só leitura.
        (
            replace(_an("dg053.ci"), bloqueio=fm.bloqueio_da_conta(
                "ml", fm.ContaFlex("ml", False, "out"))),
            _sp(dg053_sp=5),
            "A conta não tem o Flex ativo no Mercado Livre (assinatura: out)",
        ),
        (
            replace(_an("dg053.ci"), bloqueio=fm.bloqueio_da_conta("ml", None)),
            _sp(dg053_sp=5),
            "Ainda não deu para conferir se a conta tem o Flex",
        ),
        (
            replace(_an("dg053.ci"), plataforma="shopee", bloqueio=fm.bloqueio_da_conta(
                "shopee", fm.ContaFlex("shopee", False, "out"))),
            _sp(dg053_sp=5),
            "A Entrega Direta está desligada na loja. Ligue no Seller Center primeiro",
        ),
        (
            replace(_an("dg053.ci"), plataforma="shopee", bloqueio=fm.bloqueio_da_conta(
                "shopee", fm.ContaFlex("shopee", False, "sem_canal"))),
            _sp(dg053_sp=5),
            "A loja não tem o canal Entrega Direta",
        ),
        (
            replace(_an("dg053.ci"), plataforma="shopee", bloqueio=fm.bloqueio_da_conta(
                "shopee", None)),
            _sp(dg053_sp=5),
            "Ainda não deu para conferir se a loja tem a Entrega Direta",
        ),
        (replace(_an(), conhecido=False), {}, "não está ligado a nenhum produto do DaVinci"),
        (
            replace(_an("dg053.ci"), status="paused"),
            _sp(dg053_sp=5),
            "O anúncio está pausado na plataforma — o Flex fica desligado",
        ),
        (
            replace(_an("dg053.ci"), status="under_review"),
            _sp(dg053_sp=5),
            "O anúncio está em revisão na plataforma",
        ),
        (
            replace(_an("dg053.ci"), plataforma="shopee", pode_ligar=False),
            _sp(dg053_sp=5),
            "Na Shopee o sistema só confere: ligar a Entrega Direta é à mão no Seller Center. "
            "5 peças livres em São Bernardo",
        ),
    ],
)
def test_cada_ramo_da_regra_vira_frase_clara(anuncio, saldos, espera):
    d = decidir(anuncio, saldos, CFG)
    assert espera in _claro(d.motivo)


def test_lote_fora_de_venda():
    # `analisar_sku` filtra antes; a frase da defesa também é traduzida.
    assert "lote .us, que não é de venda" in _claro("a017.us: lote .us fora dos lotes de venda")


def test_limite_por_familia_traduz_o_resto_tambem():
    a = _an("dg053.ci", ext="MLB1")
    b = _an("dg053.ci", ext="MLB2")
    d = decidir_lote([a, b], _sp(dg053_sp=9), CFG)
    perdeu = d[(CONTA, "MLB2")]
    texto = _claro(perdeu.motivo)
    assert texto.startswith("A família dg053 já tem 1 anúncio com Flex, que é o limite.")
    assert "9 peças livres em São Bernardo" in texto


def test_emergencia_e_desconhecido():
    assert _claro("emergência: desligado por uma pessoa") == "Desligado pelo botão de emergência."
    # O que não casa volta como veio (nunca some da tela).
    assert motivo_claro("algo novo do motor") == "algo novo do motor"
    assert motivo_claro(None) is None
    assert motivo_claro("  ") is None


def test_motivos_gravados_fora_da_regra():
    """Os textos que o motor grava direto no estado (recusa e emergência)."""
    assert fm.INELEGIVEL == "inelegivel"  # o motor ainda fala assim
    assert "não aceitou ligar o Flex (404 not found)" in _claro(
        "a plataforma recusou ligar: 404 not found"
    )
