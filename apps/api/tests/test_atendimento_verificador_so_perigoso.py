"""Só o perigoso segura (16/11/2026, o Eduardo: "sim, vamos tentar"): sem o "ok" do verificador,
o rascunho segura só com a marca perigosa, o "não liberou" e a afirmação sem lastro sobre o PEDIDO;
a afirmação de produto ou de política e o "não responde" saem com a ressalva no registro (a
Revisão do dia mostra todas). As afirmações abaixo são as do teste de 16/11 (129 conversas)."""

from __future__ import annotations

import types
from typing import Any

import pytest

from app.config import get_settings
from app.services.atendimento.agente import custo, verificador
from app.services.atendimento.constantes import MODO_AUTO
from tests.test_atendimento_agente_verificador import (
    _CONVERSA,
    _base,
    _ctx,
    _final,
    _proposta,
    _veredito,
)

FICHA = ["pedido", "ficha:58252691738"]
DO_BANCO = ["ficha:58252691738", "par:b2408"]


def _nao(**marcas: Any) -> verificador.Resultado:
    v = _veredito(ok_para_envio_automatico=False, motivos=["o motivo"], **marcas)
    return verificador.Resultado(
        ok=False, veredito=verificador.Veredito(**v), pediu=True, uso=custo.Uso(custo_usd=0.002)
    )


@pytest.mark.parametrize(
    "afirmacao",
    [
        "a mala de 24 polegadas é de despacho",
        "vai com o carregador completo",
        "também tem a garantia Apple",
        "Central de Ajuda da Shopee para o valor estornado",
        "os cupons ativos também aparecem na página do anúncio",
        "Não encontramos anúncio de capinha na loja",
        "o reembolso é feito pela Shopee",
    ],
)
def test_a_afirmacao_de_produto_ou_politica_sai_com_a_ressalva(afirmacao):
    assert not verificador.afirmacao_do_pedido(afirmacao)
    r = verificador.so_o_perigoso_segura(_nao(afirma_sem_lastro=[afirmacao]), evidencias=DO_BANCO)
    assert r.ok and r.problemas == [] and r.ressalvas == (verificador.SEM_LASTRO,)
    fatos = r.para_fatos("o texto")
    assert fatos["ok"] is True and fatos["problemas"] == [] and fatos["afirma_sem_lastro"] == []
    assert fatos["ressalvas"] == {
        "problemas": [verificador.SEM_LASTRO],
        "afirma_sem_lastro": [afirmacao],
        "motivos": ["o motivo"],
    }
    assert verificador.texto_da_ressalva(fatos) == f"afirma sem lastro («{afirmacao}»)"


def test_o_nao_responde_sai_com_a_ressalva():
    r = verificador.so_o_perigoso_segura(_nao(responde_o_que_foi_perguntado=False))
    assert r.ok and r.ressalvas == (verificador.NAO_RESPONDE,)
    assert verificador.texto_da_ressalva(r.para_fatos("x")) == "não responde o que foi perguntado"
    dois = verificador.so_o_perigoso_segura(
        _nao(responde_o_que_foi_perguntado=False, afirma_sem_lastro=["tem NFC"]),
        evidencias=DO_BANCO,
    )
    assert dois.ok
    assert verificador.texto_da_ressalva(dois.para_fatos("x")) == (
        "afirma sem lastro («tem NFC»), não responde o que foi perguntado"
    )


@pytest.mark.parametrize(
    "afirmacao",
    [
        "O A17 Pro Max é o modelo da opção que você escolheu",
        "Não encontramos esse pedido nesta conversa",
        "seu pedido já foi enviado",
        "o reembolso já saiu",
        "chega em 3 dias úteis",
        "chega amanhã",
        "o valor de R$ 89,90",
        "foi postado no dia 12/11",
        "está a caminho",
        "O pedido já consta como cancelado",
    ],
)
def test_a_afirmacao_sobre_o_pedido_segura(afirmacao):
    assert verificador.afirmacao_do_pedido(afirmacao)
    antes = _nao(afirma_sem_lastro=["vai com o carregador completo", afirmacao])
    assert verificador.so_o_perigoso_segura(antes, evidencias=DO_BANCO) is antes


@pytest.mark.parametrize("marca", verificador.MARCAS)
def test_as_marcas_seguram(marca):
    antes = _nao(**{marca: True, "afirma_sem_lastro": ["vai com o carregador completo"]})
    assert verificador.so_o_perigoso_segura(antes, evidencias=DO_BANCO) is antes


def test_os_avisos_da_1a_camada_e_as_fontes_citadas():
    """A afirmação sem lastro sai só com a resposta da equipe ou o fato aprovado citado ("se está
    no banco, está certo"); só com o anúncio ou só com o pedido, segura ("tem carregamento sem fio",
    "Sim, o cadeado é TSA."). Os avisos fracos e os do conteúdo do produto deixam sair; os outros
    (o defeito, o pedido, a contradição) seguram."""
    produto = _nao(afirma_sem_lastro=["vai com o carregador completo"])
    solta = verificador.so_o_perigoso_segura
    fracos = ["ficha_no_automatico", "assunto_de_produto", "confianca_baixa"]
    assert solta(produto, checagens_feitas=fracos, evidencias=DO_BANCO).ok
    assert solta(produto, checagens_feitas=fracos, evidencias=FICHA) is produto
    for aviso in ("produto_sem_fonte", "caracteristica_fora_do_anuncio", "variacao_sem_dado"):
        assert solta(produto, checagens_feitas=[aviso], evidencias=DO_BANCO).ok, aviso
        assert solta(produto, checagens_feitas=[aviso], evidencias=FICHA) is produto, aviso
        com_fato = solta(produto, checagens_feitas=[aviso], evidencias=["fato:12"])
        assert com_fato.ok, aviso
    for aviso in (
        "defeito_fora_da_orientacao",
        "pedido_sem_lastro",
        "estado_sem_dado",
        "variacao_contradiz",
        "anuncio_se_contradiz",
        "produto_contradiz",
    ):
        assert solta(produto, checagens_feitas=[aviso], evidencias=DO_BANCO) is produto, aviso
    assert solta(produto, evidencias=["pedido"]) is produto
    assert solta(produto) is produto
    # O "não responde" sem afirmação não pede fonte.
    assert solta(_nao(responde_o_que_foi_perguntado=False), evidencias=["pedido"]).ok


def test_o_nao_liberou_a_falha_e_o_ok_nao_mudam():
    nao_liberou = _nao()
    assert nao_liberou.problemas == [verificador.NAO_LIBERADO]
    assert verificador.so_o_perigoso_segura(nao_liberou) is nao_liberou
    falhou = verificador.Resultado.falhou(verificador.FALHA_SEM_TEMPO)
    assert verificador.so_o_perigoso_segura(falhou) is falhou
    ok = verificador.Resultado(ok=True, veredito=verificador.Veredito(**_veredito()), pediu=True)
    assert verificador.so_o_perigoso_segura(ok) is ok
    assert verificador.texto_da_ressalva(ok.para_fatos("x")) is None
    assert verificador.texto_da_ressalva(None) is None


async def _canal(_session, _conversa):
    return types.SimpleNamespace(
        modo=MODO_AUTO, auto_categorias=["rastreio"], auto_agente=["rastreio"]
    )


async def _no_rascunho() -> verificador.Conferencia:
    return await verificador.no_rascunho(
        None,  # type: ignore[arg-type]
        _CONVERSA,
        None,
        _base(),
        _ctx(),
        _proposta(evidencias=["pedido", "par:b2408"]),
        _final(),
        cliente=None,
        prazo_s=30.0,
        checagens_feitas=["ficha_no_automatico"],
    )


async def test_o_nao_de_produto_leva_a_alternativa_que_sai_com_a_ressalva(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_verificador_sombra", "")
    monkeypatch.setattr(s, "atendimento_verificador_so_perigoso", True)
    vereditos = [_nao(afirma_sem_lastro=["vai com o carregador completo"])]

    async def _verificar(*_a, **_k):
        return vereditos[0]

    monkeypatch.setattr(verificador, "verificar", _verificar)
    monkeypatch.setattr(verificador, "_canal", _canal)
    conf = await _no_rascunho()
    # O "não" continua registrado como foi; a alternativa é o mesmo texto, sem a pendência.
    assert conf.codigo == verificador.CODIGO and conf.registro["ok"] is False
    alt = conf.com_ressalva
    assert alt is not None and alt.codigo is None and alt.final == _final()
    assert alt.registro["ok"] is True and alt.custo_usd == conf.custo_usd
    assert alt.registro["ressalvas"]["problemas"] == [verificador.SEM_LASTRO]
    # A trava do envio sozinho deixa sair o texto conferido.
    rascunho = types.SimpleNamespace(
        fatos={"agente": {"verificador": alt.registro}}, texto=_final().texto
    )
    assert verificador.trava(rascunho, ligado=True) is None

    # A marca perigosa: sem alternativa.
    vereditos[0] = _nao(promete_acao_da_equipe=True)
    assert (await _no_rascunho()).com_ressalva is None
    # Desligado (ATENDIMENTO_VERIFICADOR_SO_PERIGOSO=false): todo "não" vai para pessoa.
    vereditos[0] = _nao(afirma_sem_lastro=["vai com o carregador completo"])
    monkeypatch.setattr(s, "atendimento_verificador_so_perigoso", False)
    assert (await _no_rascunho()).com_ressalva is None
