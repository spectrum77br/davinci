"""A linha da dúvida em cima do rascunho (13/11/2026, `services/atendimento/duvida.py`)."""

from __future__ import annotations

import re
from pathlib import Path

from app.services.atendimento import constantes, duvida, ia, regua


def _decisao(motivo: str, **extra) -> dict:
    return {duvida.FATO_AUTO_ENVIO: {"motivo": motivo, "em": "2026-11-13T12:00:00+00:00", **extra}}


def _aprov(decisoes: int, acertos: int, tipo: str = "rastreio", liberado: bool = False):
    return duvida.Aprovacao(
        tipo=tipo,
        decisoes=decisoes,
        acertos=acertos,
        limiar=regua.limiar(tipo),
        liberado=liberado,
        escopo="loja",
    )


def test_sem_decisao_gravada_nao_tem_linha():
    assert duvida.linha(None, "rastreio") is None
    assert duvida.linha({}, "rastreio") is None
    assert duvida.linha({"auto_envio": "x"}, "rastreio") is None
    # O código cru nunca vai para a tela.
    assert duvida.linha(_decisao("codigo_que_nao_existe"), "rastreio") is None


def test_o_fato_e_o_mesmo_do_portao():
    assert duvida.FATO_AUTO_ENVIO == ia.FATO_AUTO_ENVIO


def test_assunto_nao_liberado_sem_regua_diz_so_que_nao_esta_liberado():
    linha = duvida.linha(_decisao("assunto_nao_liberado"), "rastreio", regua_ativa=False)
    assert linha == "Rastreio: o assunto não está liberado para sair sozinho nesta loja."
    # Sem a régua ligada, os números não aparecem mesmo que venham.
    com_numero = duvida.linha(
        _decisao("assunto_nao_liberado_agente"), "rastreio", aprovacao=_aprov(30, 22)
    )
    assert "%" not in com_numero


def test_com_a_regua_a_aprovacao_da_equipe_e_nunca_a_confianca():
    linha = duvida.linha(
        _decisao("assunto_nao_liberado"), "rastreio", aprovacao=_aprov(30, 22), regua_ativa=True
    )
    assert linha == "Rastreio: 73% de aprovação da equipe nas últimas 30 (sai sozinha com 80%)."
    poucas = duvida.linha(
        _decisao("assunto_nao_liberado"), "rastreio", aprovacao=_aprov(8, 8), regua_ativa=True
    )
    assert "só 8 decisões" in poucas and "com 20" in poucas
    nenhuma = duvida.linha(_decisao("orientacao_nao_liberada"), "rastreio", regua_ativa=True)
    assert "ainda não decidiu" in nenhuma and "80%" in nenhuma
    soltou = duvida.linha(
        _decisao("assunto_nao_liberado"),
        "rastreio",
        aprovacao=_aprov(30, 27, liberado=True),
        regua_ativa=True,
    )
    assert "liberado" in soltou and "90%" in soltou


def test_sensivel_mede_com_90():
    linha = duvida.linha(_decisao("assunto_so_humano"), "troca_devolucao", regua_ativa=True)
    assert "90%" in linha
    assert (
        duvida.nome_do_assunto("troca_devolucao")
        == constantes.CATEGORIAS_INFO["troca_devolucao"][0]
    )


def test_caixa_humano_diz_os_motivos_com_o_rotulo_da_tela():
    linha = duvida.linha(
        _decisao("caixa_humano", motivos_humano=["reclamacao", "xingamento", "reclamacao"]),
        "rastreio",
    )
    assert linha == (
        f"Precisa de pessoa: {constantes.ROTULO_HUMANO['reclamacao']}, "
        f"{constantes.ROTULO_HUMANO['xingamento']}."
    )
    assert duvida.linha(_decisao("caixa_humano"), None) == "Precisa de pessoa."


def test_todo_codigo_da_linha_e_um_motivo_que_o_portao_grava():
    """Os códigos que viram frase existem no portão (`ia.py`, o verificador) — um nome
    errado aqui nunca casaria e a linha sumiria sem ninguém ver."""
    raiz = Path(ia.__file__).parent
    fontes = "\n".join(
        p.read_text(encoding="utf-8") for p in (raiz / "ia.py", raiz / "agente" / "verificador.py")
    )
    literais = set(re.findall(r'"([a-z_]+)"', fontes))
    codigos = set(duvida.FRASES) | duvida.ASSUNTO_NAO_LIBERADO | {duvida.CAIXA_HUMANO}
    for codigo in codigos:
        assert codigo in literais, codigo
