"""Dados novos podem destravar o turno; renovação cosmética não gasta modelo."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.atendimento import reavaliacao as r

AGORA = datetime(2026, 10, 19, 12, tzinfo=UTC)


def retido(motivo="conversa sem pedido identificado", **valores):
    base = {
        "status": "pendente",
        "motivo": motivo,
        "created_at": AGORA - timedelta(minutes=10),
        "fatos": {"pedido": None, "lacunas": {}, "auto_envio": {"motivo": "precisa_humano"}},
        "validador_erros": [],
    }
    base.update(valores)
    return SimpleNamespace(**base)


def test_pedido_chegou_libera_reavaliacao_sem_mudar_rascunho_antigo():
    antigo = retido()
    antes = deepcopy(antigo.fatos)
    atuais = {
        "pedido": {"situacao_no_sistema": "Em digitação"},
        "lacunas": {"numero_pedido": "123"},
    }
    meta = r.nova_tentativa(antigo, atuais, agora=AGORA)
    assert meta["n"] == 1
    assert meta["assinaturas_vistas"] == [r.assinatura(antes), r.assinatura(atuais)]
    assert antigo.fatos == antes


def test_reavaliacao_nao_se_repete_no_mesmo_contexto_nem_depois_do_limite():
    antigo = retido("sem dado para {rastreio}")
    atuais = {"pedido": None, "lacunas": {"rastreio": "AA123456789BR"}}
    meta = r.nova_tentativa(antigo, atuais, agora=AGORA)
    assert meta is not None
    novo = retido("sem dado para {rastreio}", fatos={**atuais, r.CHAVE: meta})
    assert r.nova_tentativa(novo, atuais, agora=AGORA + timedelta(minutes=10)) is None
    novo.fatos[r.CHAVE] = {**meta, "n": 2}
    assert not r.pode_reavaliar(novo, agora=AGORA + timedelta(minutes=10))


@pytest.mark.parametrize("idade", [timedelta(minutes=4, seconds=59), timedelta(seconds=-1)])
def test_espera_minima_e_relogio_futuro_nao_disparam(idade):
    antigo = retido(created_at=AGORA - idade)
    assert not r.pode_reavaliar(antigo, agora=AGORA)


def test_somente_uma_das_lacunas_necessarias_nao_basta():
    antigo = retido("sem dado para {rastreio}, {previsao_entrega}")
    parciais = {"lacunas": {"rastreio": "AA123456789BR"}}
    assert r.nova_tentativa(antigo, parciais, agora=AGORA) is None
    parciais["lacunas"]["previsao_entrega"] = "20/10/2026"
    assert r.nova_tentativa(antigo, parciais, agora=AGORA) is not None


def test_nf_so_reavalia_quando_chega_a_nf_e_nao_apenas_o_rastreio():
    antigo = retido("sem dado para {nf_numero}")
    assert r.nova_tentativa(antigo, {"lacunas": {"rastreio": "AA123456789BR"}}, agora=AGORA) is None
    assert r.nova_tentativa(antigo, {"lacunas": {"nf_numero": "123"}}, agora=AGORA) is not None


def test_fonte_relativa_ordem_de_atributos_e_diagnostico_nao_sao_contexto_novo():
    ficha = {"titulo": "Relógio", "atributos": ["Cor: azul", "Tamanho: M/G"], "descricao": "Texto"}
    antigo = {"pedido": None, "ficha_do_anuncio": [{**ficha, "fonte": "loja, há um dia"}]}
    atual = {
        "pedido": None,
        "ficha_do_anuncio": [
            {**ficha, "atributos": list(reversed(ficha["atributos"])), "fonte": "loja, agora"}
        ],
        "auto_envio": {"motivo": "outro"},
    }
    assert r.assinatura(antigo) == r.assinatura(atual)
    assert r.nova_tentativa(retido("não consta na ficha", fatos=antigo), atual, agora=AGORA) is None


def test_ficha_antes_ausente_agora_com_dado_pode_ser_reavaliada():
    antigo = retido("sem ficha disponível")
    atual = {"ficha_do_anuncio": [{"titulo": "Relógio", "atributos": ["Pulseira: M/G"]}]}
    assert r.nova_tentativa(antigo, atual, agora=AGORA) is not None
    assert r.nova_tentativa(retido(), atual, agora=AGORA) is None  # ainda falta pedido


@pytest.mark.parametrize("status", ["descartado", "substituido", "enviado", "editado"])
def test_recusa_ou_resposta_ja_concluida_nao_reabre(status):
    assert not r.pode_reavaliar(retido(status=status), agora=AGORA)


@pytest.mark.parametrize(
    "motivo",
    [
        "pedido não encontrado; garantia só pessoa",
        "sem dado para {rastreio}; cliente insistiu em falar com uma pessoa",
        "sem ficha; a IA mudou de assunto",
        "pedido não encontrado; assunto só para pessoa (reembolso)",
        "a IA respondeu fora do formato combinado",
        "Necessário confirmar estoque e data de reposição",
        "sem dado para {nf_numero}; pedido de arquivo — ação da equipe",
        "sem dado para {nf_numero}; nota fiscal não recebida",
        "sem ficha; dúvida de remetente sem procedimento aprovado",
        "sem ficha; instrução de recebimento — política não confirmada",
    ],
)
def test_falta_de_dado_nao_sobrepoe_trava_sensivel_ou_erro_de_modelo(motivo):
    assert not r.pode_reavaliar(retido(motivo), agora=AGORA)


def test_pedido_pela_tela_ou_erro_de_validador_nao_reabre():
    assert not r.pode_reavaliar(retido(fatos={"pedida_pela_tela": True}), agora=AGORA)
    assert not r.pode_reavaliar(
        retido(validador_erros=["prazo de garantia só pode vir do sistema"]), agora=AGORA
    )
    assert not r.pode_reavaliar(retido(categoria="garantia"), agora=AGORA)


@pytest.mark.parametrize("meta", ["inválido", {}, {"n": True, "assinaturas_vistas": []}])
def test_metadados_invalidos_nao_reiniciam_orcamento(meta):
    assert not r.pode_reavaliar(retido(fatos={r.CHAVE: meta}), agora=AGORA)


def test_reclamacao_nova_nao_libera_contexto_mesmo_com_pedido_encontrado():
    atual = {"pedido": {"situacao_no_sistema": "Em digitação"}, "reclamacoes_abertas": 1}
    assert r.nova_tentativa(retido(), atual, agora=AGORA) is None
