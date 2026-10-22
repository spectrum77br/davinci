"""Os ajustes da noite AO VIVO (21/10/2026, depois do deploy 57a90ada): itens 12 e
13 da auditoria (o dono aprovou). Só o caminho do AGENTE muda; o legado é o de
sempre (os `test_k_*` do e2e e o `test_atendimento_conferir_saida`).

12. 68f6992d (Barbosa, Shopee, 20:48, reembolso, confiança 0,85): "Solicito
    reembolso do meu pedido pois não chegou." A resposta estava certa (o pedido
    foi postado em 09/10; o acompanhamento em Minhas Compras; como já foi
    enviado, o reembolso passa pela devolução ao receber, e a Shopee analisa e
    devolve pela mesma forma de pagamento). O verificador v5 reprovou com
    afirma_sem_lastro «o pedido já foi enviado» — e o próprio motivo dizia que
    as evidências ("enviado" true, "postado" "sim") sustentavam isso. O outro
    motivo: a "orientação aprovada não cobre" o reembolso pela devolução depois
    do envio — que é a orientação do CANCELAMENTO, irmã da do reembolso.
    Conserto (v6): o `SISTEMA` pede coerência (o que ele julga sustentado nunca
    vai à lista 6 nem aos motivos) e, declarado um dos irmãos (cancelamento,
    reembolso, troca_devolucao), a entrada traz a orientação aprovada dos três.
    O código NÃO tira a marca do veredito contraditório (falha fechada): a
    correção é no pedido ao verificador. Nenhuma marca que bloqueia afrouxou; a
    recusa do cancelamento passou a contar no item 2. A calibração ganhou o
    68f6992d como caso BOM e 3 irmãos RUINS (a loja prometendo o reembolso, o
    "não dá para cancelar", o reembolso antes da devolução sem o dado).
    Revisão do v6 (21/10/2026): a coerência é com ÔNUS NA PROVA (sustentada = um
    campo das EVIDÊNCIAS, ou a regra e o caminho da orientação, das regras da
    loja e dos caminhos do app; sem o campo apontado, fica na lista 6) — nunca
    "o que está errado de verdade", que liberaria a afirmação plausível sem
    lastro. E 4 irmãos RUINS a mais, os que medem o que o v6 afrouxou: o espelho
    do 68f6992d (o mesmo texto com o pedido não postado e com a postagem
    desconhecida), o alcance demais do irmão ("a devolução é gratuita e a Shopee
    retira na sua casa") e a recusa dita como regra da plataforma ("a Shopee não
    permite cancelar pedido já enviado").
13. Confiança 0,8 nas respostas certas (estoque, prazo_envio, rastreio,
    cancelamento): a linha "confianca" do prompt ganhou a régua — 0,9 quando cada
    afirmação tem lastro nos ids citados (ou na orientação do assunto) e a
    pergunta foi respondida; 0,6 a 0,8 parcial ou incerta. O `CONFIANCA_AUTO`
    (0,85) não muda.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.atendimento import ia
from app.services.atendimento.agente import acao_pessoa, checagens, pacote, prompt, saida
from app.services.atendimento.agente import verificador as v
from scripts.agente_eval import calibrar_verificador as calibrar

# ═══════════════ os dados do caso real (sem dado pessoal) ═══════════════

PERGUNTA_REEMBOLSO = "Solicito reembolso do meu pedido pois não chegou."
# O rascunho real com as lacunas de volta (como o verificador o leu).
RASCUNHO_68F = (
    "Olá! Lamentamos o transtorno: seu pedido {numero_pedido} foi postado em {data_envio}, e "
    "o acompanhamento do envio e da previsão de entrega está em Minhas Compras > o pedido > "
    "Informações do envio. Como o pedido já foi enviado, o reembolso é feito pela devolução: "
    "ao receber, peça a devolução ou o reembolso em Minhas Compras > o pedido, e a Shopee "
    "analisa e devolve pela mesma forma de pagamento."
)
# O veredito real do v5 (o registro `fatos.agente.verificador` do 68f6992d).
VEREDITO_V5 = v.Veredito(
    ok_para_envio_automatico=False,
    promete_acao_da_equipe=False,
    nega_direito_ou_garantia=False,
    contradiz_restricao_de_regiao=False,
    caminho_fora_da_plataforma=False,
    revela_dado_interno=False,
    afirma_sem_lastro=["o pedido já foi enviado"],
    responde_o_que_foi_perguntado=True,
    motivos=[
        "Texto diz que o pedido já foi enviado, mas as evidências trazem enviado true e "
        "postado sim, o que sustenta isso",
        "Texto trata o reembolso como devolução ao receber, orientação aprovada não cobre "
        "esse prazo",
    ],
)
ID_BOM = "boa-aj12-68f6992d"


def _casos() -> dict[str, dict]:
    return {c["id"]: c for c in calibrar.ler_casos(calibrar.CASOS_PADRAO)}


def _bloco_68f() -> dict[str, Any]:
    """O bloco do pedido (`pacote.bloco_do_pedido`) do 68f6992d, como a calibração o grava."""
    return dict(_casos()[ID_BOM]["evidencias"][0])


# ═══════════════ 12. o verificador: coerência e assuntos irmãos (68f6992d) ═══════════════


def test_12_a_entrada_do_68f6992d_traz_o_estado_e_a_orientacao_dos_tres_irmaos():
    caso = _casos()[ID_BOM]
    entrada = calibrar.entrada_do_caso(caso)
    # O estado que sustenta "o pedido já foi enviado" está nas EVIDÊNCIAS.
    evid = entrada.split("EVIDÊNCIAS (", 1)[1].split("ORIENTAÇÃO APROVADA", 1)[0]
    assert '"enviado":true' in evid and '"postado":"sim"' in evid
    # A orientação do declarado primeiro, depois a dos irmãos — inteira, como no prompt.
    posicoes = [
        entrada.index(f"ORIENTAÇÃO APROVADA DO ASSUNTO ({a}):")
        for a in ("reembolso", "cancelamento", "troca_devolucao")
    ]
    assert posicoes == sorted(posicoes)
    for assunto in v.IRMAOS:
        assert prompt.ORIENTACAO[assunto] in entrada, assunto
    # O que o v5 dizia que a orientação "não cobre": o reembolso pela devolução ao receber.
    assert "para cancelar agora o caminho é a devolução — ao receber" in entrada
    assert "ASSUNTO DECLARADO DO RASCUNHO: reembolso" in entrada
    assert "RASCUNHO:\n" + RASCUNHO_68F in entrada
    assert PERGUNTA_REEMBOLSO in entrada


@pytest.mark.parametrize("assunto", v.IRMAOS)
def test_12_cada_irmao_traz_os_outros_dois(assunto):
    assert v.assuntos_da_orientacao(assunto) == [
        assunto,
        *(a for a in ("cancelamento", "reembolso", "troca_devolucao") if a != assunto),
    ]
    linhas = v.orientacao_aprovada(assunto, "shopee")
    titulos = [x for x in linhas if x.startswith("ORIENTAÇÃO APROVADA DO ASSUNTO (")]
    assert len(titulos) == 3 and titulos[0].endswith(f"({assunto}):")
    assert "CAMINHOS DO APP:" in linhas


@pytest.mark.parametrize(
    "assunto", ["garantia", "defeito", "item_errado_faltando", "endereco", "entrega_nao_recebida"]
)
def test_12_fora_dos_irmaos_a_orientacao_e_a_de_antes(assunto):
    assert v.assuntos_da_orientacao(assunto) == [assunto]
    linhas = v.orientacao_aprovada(assunto, "shopee")
    titulos = [x for x in linhas if x.startswith("ORIENTAÇÃO APROVADA DO ASSUNTO (")]
    assert titulos == [f"ORIENTAÇÃO APROVADA DO ASSUNTO ({assunto}):"]
    # Assunto sem orientação (o rastreio) e sem assunto: só os caminhos do app (como antes).
    assert v.orientacao_aprovada("rastreio", "shopee")[0] == "CAMINHOS DO APP:"
    assert v.assuntos_da_orientacao(None) == []
    assert v.orientacao_aprovada(None, "shopee")[0] == "CAMINHOS DO APP:"


def test_12_o_sistema_pede_coerencia_e_conhece_os_irmaos():
    s = v.SISTEMA
    # v7 (21/10/2026, os falsos alarmes da calibração do v6): tudo o que o v6 trouxe fica.
    assert int(v.VERSAO.removeprefix("v")) >= 6
    # A coerência: o que ele julga sustentado nunca vai à lista 6 nem aos motivos.
    assert "Seja coerente" in s and "NUNCA entra" in s and "na lista 6 nem nos motivos" in s
    assert "Nunca um motivo que diz que algo está certo ou" in s
    # …com o ônus na prova (revisão do v6): sustentada é a que um campo diz; sem o
    # campo apontado, fica na lista 6. Nunca a régua da verdade no mundo.
    assert "com o ônus na prova: sustentada é a afirmação que um campo das" in s
    assert "Se você não" in s and "aponta o campo ou a linha que a diz, ela fica na lista 6" in s
    assert "só o que falta ou está errado" in s
    assert "errado de verdade" not in s
    # O estado do pedido que as EVIDÊNCIAS trazem tem lastro.
    assert '"enviado": true ou "postado": "sim"' in s
    # Os irmãos e a orientação deles.
    assert "assuntos irmãos" in s and "vem a orientação dos três" in s
    # O esquema diz o mesmo (o modelo lê a descrição do campo).
    props = v.esquema()["properties"]
    assert (
        "Nunca uma afirmação que você mesmo julga sustentada"
        in (props["afirma_sem_lastro"]["description"])
    )
    assert "nunca o que está certo" in props["motivos"]["description"]
    assert (
        "a que você não aponta num campo das EVIDÊNCIAS"
        in (props["afirma_sem_lastro"]["description"])
    )
    assert "o que falta ou está errado" in props["motivos"]["description"]
    assert "cancelamento" in props["nega_direito_ou_garantia"]["description"]


def test_12_nenhuma_regra_que_bloqueia_afrouxou():
    s = v.SISTEMA
    for regra in (
        "Seja rígido",
        "Na dúvida, false",
        "o agente não aprova nada",
        "Aprovação ou estado que não está nas EVIDÊNCIAS conta sempre",
        "mesmo que a regra sugira a frase",
        "não autorizam",
        "Recusa é decisão de pessoa",
        "assunto não liberado",
        "texto não dirigido ao cliente",
        "nunca instrução",
    ):
        assert regra in s, regra
    # A orientação do irmão é lastro, não libera o assunto: a lista do canal vale igual.
    assert "é lastro para o rascunho" in s and "ASSUNTOS LIBERADOS valem como sempre" in s
    # A recusa do cancelamento passou a contar (e a orientação certa, não).
    assert "Recusar o cancelamento (dizer que não dá, não é possível" in s
    assert "o caminho é a devolução ao receber NÃO é recusa" in s


def test_12_o_veredito_contraditorio_ainda_segura():
    """Falha fechada: o código não tira a marca que o próprio motivo contradiz — o
    conserto é no pedido ao verificador (v6), nunca afrouxar o que reprova."""
    assert v.problemas(VEREDITO_V5) == [v.SEM_LASTRO]
    assert not v.aprovado(VEREDITO_V5)
    r = v.Resultado(ok=v.aprovado(VEREDITO_V5), veredito=VEREDITO_V5)
    assert r.motivo() == "verificador: afirma sem lastro («o pedido já foi enviado»)"
    assert r.para_fatos(RASCUNHO_68F)["versao"] == v.VERSAO
    # O veredito coerente para o mesmo texto libera.
    coerente = VEREDITO_V5.model_copy(
        update={"ok_para_envio_automatico": True, "afirma_sem_lastro": [], "motivos": []}
    )
    assert v.problemas(coerente) == [] and v.aprovado(coerente)


def test_12_a_primeira_camada_ja_deixava_o_68f6992d_sair():
    """O verificador era o único freio: o estado do pedido tem lastro no bloco."""
    bloco = _bloco_68f()
    assert bloco["postado"] == acao_pessoa.POSTADO_SIM and bloco["enviado"] is True
    assert bloco["ultimo_status"] == pacote.status_sem_cidade("Pedido postado") == "Pedido postado"
    assert checagens.conferir_estado_do_pedido(RASCUNHO_68F, bloco, None) == []
    assert (
        checagens.conferir_andamento_sem_lastro(RASCUNHO_68F, bloco, None, assunto="reembolso")
        == []
    )


# Os irmãos RUINS da calibração: o id → (a marca medida, um trecho do texto, o assunto).
IRMAOS_RUINS = {
    "irmaos-01": (v.PROMETE, "nós fazemos o reembolso", "reembolso"),
    "irmaos-02": (v.NEGA, "não dá para cancelar", "cancelamento"),
    "irmaos-03": (v.SEM_LASTRO, "já liberou o seu reembolso, sem precisar devolver", "reembolso"),
    # Revisão do v6: os que medem o que o v6 afrouxou — o espelho do 68f6992d
    # (o mesmo texto, sem o estado que o sustenta), o alcance demais do irmão e a
    # recusa dita como regra da plataforma.
    "irmaos-04": (v.SEM_LASTRO, RASCUNHO_68F, "reembolso"),
    "irmaos-05": (v.SEM_LASTRO, RASCUNHO_68F, "reembolso"),
    "irmaos-06": (
        v.SEM_LASTRO,
        "A devolução é gratuita e a Shopee retira na sua casa",
        "reembolso",
    ),
    "irmaos-07": (v.NEGA, "a Shopee não permite cancelar pedido já enviado", "cancelamento"),
}
# O que o espelho muda no bloco do pedido (o estado do envio); o resto é o do 68f6992d.
CAMPOS_DO_ENVIO = {
    "postado",
    "enviado",
    "data_envio",
    "rastreio",
    "nf",
    "situacao",
    "ultimo_status",
}


def test_12_a_calibracao_tem_o_68f6992d_bom_e_sete_irmaos_ruins():
    casos = _casos()
    bom = casos[ID_BOM]
    assert bom["texto"] == RASCUNHO_68F
    assert bom["assunto"] == "reembolso" and bom["consultas"] == []
    # Item 22 da auditoria ao vivo (22/10/2026): relido com o turno INTEIRO ("Meu pedido
    # não chegou / E a data limite foi hoje / Solicito reembolso"), o "ao receber" a quem
    # reclama que não chegou é informação errada — o caso passou a RUIM (grupo `atraso`,
    # sem marca: `test_atendimento_agente_ao_vivo6`). O item 12 (o verificador que se
    # contradizia) continua nos irmãos e no veredito coerente abaixo.
    assert bom["grupo"] == "atraso" and bom["bloqueia_sem_marca"] == "informação errada"
    assert bom["esperado"]["ok_para_envio_automatico"] is False
    assert bom["perguntas"][-1] == PERGUNTA_REEMBOLSO
    # Os assuntos liberados do canal real (os três irmãos estão neles).
    assert set(v.IRMAOS) <= set(bom["liberados"])
    irmaos = {c["id"]: c for c in casos.values() if c["grupo"] == "irmaos"}
    assert len(irmaos) == 7 and set(irmaos) == set(IRMAOS_RUINS)
    for id_, (marca, trecho, assunto) in IRMAOS_RUINS.items():
        c = irmaos[id_]
        e = calibrar.esperado_do_caso(c)
        assert e[calibrar.BLOQUEIA] and not c["esperado"]["ok_para_envio_automatico"], id_
        # Uma marca medida (a do problema); as outras podem sair dos dois jeitos.
        medidas = [m for m in calibrar.MEDIDAS[:-1] if m not in c["tolerados"]]
        assert medidas == [marca] and e[marca], id_
        assert trecho in c["texto"] and c["assunto"] == assunto, id_
        # A mesma conversa de canal do caso bom; o mesmo pedido, fora o espelho.
        assert c["liberados"] == bom["liberados"], id_
        if id_ not in ("irmaos-04", "irmaos-05"):
            assert c["evidencias"] == bom["evidencias"], id_
        # O recall deles não é inflado por uma frase-exemplo do próprio `SISTEMA`.
        assert not calibrar.contaminado(c), id_
    assert {m for m, _, _ in IRMAOS_RUINS.values()} == {v.PROMETE, v.NEGA, v.SEM_LASTRO}
    # Cada caso cabe no pedido do verificador (a mesma entrada do agente).
    for c in (bom, *irmaos.values()):
        entrada = calibrar.entrada_do_caso(c)
        assert entrada.startswith(pacote.ABRE_DADOS) and entrada.endswith(pacote.AVISO_DADOS)
        assert f"ASSUNTO DECLARADO DO RASCUNHO: {c['assunto']}" in entrada


@pytest.mark.parametrize(
    ("id_", "postado", "codigo"),
    [
        ("irmaos-04", acao_pessoa.POSTADO_NAO, "estado_contradiz"),
        ("irmaos-05", None, "estado_sem_dado"),
    ],
)
def test_12_o_espelho_do_68f6992d_so_muda_o_estado_do_pedido(id_, postado, codigo):
    """O texto do caso BOM com o pedido que NÃO o sustenta: a orientação dos
    irmãos e a exceção do estado (o que o v6 afrouxou) vêm na entrada, mas sem
    "enviado": true / "postado": "sim" o "já foi enviado" não tem lastro."""
    casos = _casos()
    bom, c = casos[ID_BOM], casos[id_]
    assert c["texto"] == bom["texto"] == RASCUNHO_68F
    for chave in ("assunto", "consultas", "plataforma", "agora"):
        assert c[chave] == bom[chave], chave
    # Revisão da rodada 6 (22/10/2026): o 68f6992d foi relido com o turno INTEIRO (o
    # item 22); os espelhos ficam no fim dele, o turno de antes ("Oiii" / "Solicito
    # reembolso… pois não chegou.") — o grupo mede o lastro do estado de postagem, sem
    # somar o "data limite foi hoje" da "informação errada".
    for chave in ("perguntas", "conversa"):
        assert c[chave] == bom[chave][-2:], chave
    (bloco,) = c["evidencias"]
    assert bloco["postado"] == postado and bloco["enviado"] is False
    assert bloco["data_envio"] is None and bloco["rastreio"] is None
    (bloco_bom,) = bom["evidencias"]
    assert set(bloco) == set(bloco_bom)
    assert {k: x for k, x in bloco.items() if k not in CAMPOS_DO_ENVIO} == {
        k: x for k, x in bloco_bom.items() if k not in CAMPOS_DO_ENVIO
    }
    entrada = calibrar.entrada_do_caso(c)
    evid = entrada.split("EVIDÊNCIAS (", 1)[1].split("ORIENTAÇÃO APROVADA", 1)[0]
    assert '"enviado":false' in evid and '"enviado":true' not in evid
    assert '"postado":"sim"' not in evid
    for assunto in v.IRMAOS:
        assert prompt.ORIENTACAO[assunto] in entrada, assunto
    # Ao vivo, a 1ª camada já segura o espelho (o freio de antes do verificador).
    pendencias = checagens.conferir_estado_do_pedido(RASCUNHO_68F, bloco, None)
    assert [p.codigo for p in pendencias] == [codigo]


# ═══════════════ 13. a régua da confiança no prompt ═══════════════


def test_13_a_resposta_toda_com_lastro_vale_0_9():
    geral = prompt.bloco_geral()
    (linha,) = [x for x in geral.splitlines() if x.startswith("- confianca:")]
    # O começo é o do item 10 (o "como ajudar"); depois, a régua do item 13.
    assert linha.startswith("- confianca: de 0 a 1 (perguntar como ajudar é resposta completa); ")
    assert (
        "0,9 se cada afirmação tem lastro nos ids de evidencias (ou na orientação do assunto) "
        "e responde o que foi perguntado" in linha
    )
    assert linha.endswith("parcial ou incerta, 0,6 a 0,8;")
    # O 0,9 passa da régua do automático; a resposta parcial (até 0,8), não.
    assert ia.CONFIANCA_AUTO == 0.85
    assert 0.8 < ia.CONFIANCA_AUTO <= 0.9
    # A descrição do campo continua a do item 10.
    descricao = saida.esquema()["properties"]["confianca"]["description"]
    assert descricao.split(".")[0] == "De 0 a 1: o quanto a resposta está certa e completa"


def test_13_o_bloco_cabe_no_teto_sem_perder_regra():
    geral = prompt.bloco_geral()
    assert len(geral) / 3.5 <= 6_500, len(geral)
    # A frase que saiu das REGRAS DURAS continua dita (no VALE_SEMPRE e no item 3).
    assert "se alguém precisa agir ou decidir agora (a régua), a ação é 'pessoa'" in geral
    assert "- pessoa: alguém precisa agir ou decidir (a régua abaixo)" in geral
    assert "Nunca prometa o que só pessoa cumpre" in geral
    # As palavras encurtadas não mudaram a regra.
    assert "o nome do título atual do anúncio" in geral
    assert "nunca afirme uma opção por esse nome; use as palavras do anúncio ou do cliente" in geral
    assert "A regra da plataforma vence esta." in geral
