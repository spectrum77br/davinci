"""O verificador v7 e o item 14 da auditoria ao vivo (21/10/2026). Só o caminho do
AGENTE muda; o legado é o de sempre (os `test_k_*` do e2e e o
`test_atendimento_conferir_saida`).

A calibração do v6 (com a chave) segurou 18 das 57 respostas BOAS — recall de
100% nas 264 que têm de ser seguradas — e, ao vivo, respostas boas. As classes de
falso alarme, consertadas no `SISTEMA` e na entrada (nunca afrouxando uma regra
que bloqueia), cada uma com 2 vizinhos RUINS novos na calibração (o grupo
`vizinhos`), que continuam presos:

1. As LACUNAS ({data_envio}, {rastreio}, {previsao_entrega}, {nf_numero}…): o
   sistema as preenche com o dado do pedido, mas o verificador as via sem o valor
   e as dava como sem lastro (boa-aj12-68f6992d: "foi postado em {data_envio}").
   Agora a entrada diz, lacuna a lacuna, se o pedido TEM o dado (LACUNAS DO
   RASCUNHO; ao vivo, pelos `valores` das lacunas; na calibração, pelas
   evidências) e o `SISTEMA` diz que a com o dado é fato do pedido — e que a SEM o
   dado é sem lastro (antes nenhuma lacuna contava). Vizinhos: a previsão e a nota
   com o campo null no pedido.
2. "Não responde" no turno SEM pergunta (o ao vivo 426b9b20: "Olá Boa tarde" + o
   cartão, a opção 6 do menu, "Boa tarde", "Boa noite" → "Somos o atendimento
   virtual da loja. Como podemos ajudar?…"). A régua ESTRITA
   (`verificador.turno_sem_pergunta`: só a fala vazia, o cumprimento ou o
   agradecimento sem "?", o pedido de atendente sem mais nada — nunca o
   `perguntas_pendentes`, que deixa o "a tela quebrou" de fora) põe "nenhuma" na
   linha das perguntas; o `SISTEMA` diz que perguntar como ajudar trata esse
   turno, e que com pergunta pendente não. Vizinhos: o mesmo texto com "tem na
   cor azul?" e a opção 6 com "meu pedido não chegou ainda, cadê?".
3. A política do envio automático ("assunto não liberado para envio
   automático", boa-07 e boa-12, sem a lista na entrada): é do código. O
   `SISTEMA` diz que o verificador não a julga; a lista ASSUNTOS LIBERADOS
   continua valendo para o assunto A MAIS; o declarado vai como o código o
   liberou (`declarado`: a pergunta de produto vira `confirmar_variacao` na trava
   — antes o verificador recebia o assunto do rascunho, fora da lista).
   Vizinhos: o rastreio liberado com a garantia respondida junto e o prazo de
   envio com o cancelamento orientado junto (fora da lista: "ok" false sem marca,
   o `bloqueia_sem_marca` da calibração).
4. A orientação e a regra como lastro: a garantia legal com a regra legal true
   (boa-09), a troca pela devolução do próprio pedido (boa-o02, boa-r4-E02) e o
   prazo citado sem número (boa-r4-N11, N15, B03). Vizinhos: a garantia de 1 ano
   e a cobertura de tudo com a regra legal; a loja e a Shopee mandando o outro
   tamanho; o "postado em até 2 dias úteis" e o "chega neste sábado".
5. O mesmo dado com outras palavras: a RAM "16 GB (8 GB + 8 GB virtual)" dita como
   8 GB físicos e 8 GB virtuais (boa-18). Vizinhos: a RAM toda física e outra
   divisão.

14. A 1ª camada (o 4c71796f, Barbosa Shopee 20:52): "foi postado e ainda não
    consta como entregue" virava "diz que o pedido não saiu, mas ele já foi
    postado". A negação vale só para o verbo dela: a da ENTREGA confere a entrega
    (contradiz só com a entrega no dado) e o resto da oração ("foi postado") se
    confere como sempre — o que fecha também o furo de antes (o mesmo texto com o
    pedido NÃO postado passava calado).

Revisão do v7 (21/10/2026, ainda v7):
- classe 2: o "nenhuma" lia só as falas da rajada. O "6" depois da pergunta do pote
  deixada com o "em que podemos ajudar?", o "ok" depois de "posso abrir a
  solicitação de troca para você?" e o "tem alguém aí?" depois de "vou verificar e
  já te retorno" diziam "nenhuma". Agora o "nenhuma" exige também a conversa sem
  pendência (`verificador.conversa_sem_pendencia`, ao vivo e na calibração), o item
  7 manda conferir a CONVERSA RECENTE e não abre mão do item 1 (a promessa de
  atendente). Vizinhos a mais, com a entrada dizendo "nenhuma" (o pior caso): o
  texto do 426b9b20 depois da pergunta deixada com o genérico e depois da oferta
  aceita com "ok";
- classe 1: a lacuna com o dado sustenta só o dado do campo dela (a data de envio
  não é a da entrega; a previsão não é data certa nem a data em que foi entregue).
  A linha diz o papel do campo. Vizinhos a mais: a data de envio como a da entrega,
  a previsão como a da entrega feita e como data certa;
- os 35 casos que bloqueiam com a ENTRADA nova (o arquivo igual, a entrada não):
  o recall deles sai à parte (`calibrar.ENTRADA_NOVA_V7`).
"""

from __future__ import annotations

import io
import json
import types
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services.atendimento import constantes, ia
from app.services.atendimento import manual as manual_svc
from app.services.atendimento.agente import checagens, pacote, saida
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import verificador as v
from app.services.atendimento.agente.saida import Proposta
from scripts.agente_eval import calibrar_verificador as calibrar
from tests import test_atendimento_agente_ligacao as _lig
from tests import test_atendimento_agente_verificador as _ver
from tests.test_atendimento_agente_pacote import _cliente, _loja
from tests.test_atendimento_agente_verificador import (
    _CONVERSA,
    _base,
    _ctx,
    _final,
    _mensagem_do_agente,
    _msg,
    _no_auto,
    _ok,
    _proposta,
)

# As fixtures do verificador (a IA e o agente com o modelo falso, a API falsa, o
# espião do envio, o teto do dia limpo).
ia_ligada = _ver.ia_ligada
modelo = _ver.modelo
envios = _ver.envios
agente_ligado = _ver.agente_ligado
# Item 22 (22/10/2026): o pedido do teste tem 28 dias de postado (ver `_lig`).
_sem_a_regua_dos_dias = _lig._sem_a_regua_dos_dias
api = _ver.api
teto_limpo = _ver.teto_limpo

# ═══════════════ os dados dos casos reais (sem dado pessoal) ═══════════════

ID_68F = "boa-aj12-68f6992d"
ID_426B = "boa-aj15-426b9b20"
ID_4C71 = "boa-aj14-4c71796f"
# 426b9b20 (Barbosa Shopee, 21/10 22:06): o rascunho real.
RASCUNHO_426B = (
    "Olá! Somos o atendimento virtual da loja. Como podemos ajudar? Se a dúvida for sobre o "
    "anúncio do Uranyx A17 Pro Max que você enviou, é só nos dizer qual é."
)
FALAS_426B = ["Olá Boa tarde", "6", "Boa tarde", "Boa tarde", "Boa noite"]
# 4c71796f (Barbosa Shopee, 21/10 20:52): o rascunho real, com o número do pedido
# de volta à lacuna.
RASCUNHO_4C71 = (
    "Olá! Lamentamos o transtorno. Seu pedido {numero_pedido} foi postado e ainda não consta "
    "como entregue, então o envio pode ser acompanhado em Minhas Compras > o pedido > "
    "Informações do envio. Se o pedido não chegar, o reembolso é pedido pela opção de "
    "devolução ou reembolso do próprio pedido em Minhas Compras; a Shopee analisa e devolve "
    "pela mesma forma de pagamento."
)
PEDIDO_FALSO = "2610TESTE0001"

# Os vizinhos RUINS: o id → (a classe, a marca medida — None = o "assunto não
# liberado" sem marca —, um trecho do texto, o caso BOM de que ele é vizinho).
# Os da revisão do v7 com o "sem_pergunta" true do caso (a entrada diz "nenhuma").
FORCAM_NENHUMA = ("viz-responde-03", "viz-responde-04")
VIZINHOS = {
    "viz-lacuna-01": (1, v.SEM_LASTRO, "a previsão de entrega é {previsao_entrega}", ID_68F),
    "viz-lacuna-02": (1, v.SEM_LASTRO, "A nota fiscal {nf_numero}", ID_68F),
    # Revisão do v7: a lacuna COM o dado no papel de outro dado.
    "viz-lacuna-03": (1, v.SEM_LASTRO, "vai ser entregue no dia {data_envio}", ID_68F),
    "viz-lacuna-04": (1, v.SEM_LASTRO, "foi entregue no dia {previsao_entrega}", "boa-r4-E02"),
    "viz-lacuna-05": (1, v.SEM_LASTRO, "chega com certeza no dia {previsao_entrega}", "boa-r4-B03"),
    "viz-responde-01": (2, v.NAO_RESPONDE, RASCUNHO_426B, ID_426B),
    "viz-responde-02": (2, v.NAO_RESPONDE, "Como podemos ajudar?", "boa-r4-B03"),
    # Revisão do v7: o mesmo texto do 426b9b20 com a entrada dizendo "nenhuma" e a
    # pendência na CONVERSA (a pergunta deixada com o genérico; a oferta aceita).
    "viz-responde-03": (2, v.NAO_RESPONDE, RASCUNHO_426B, ID_426B),
    "viz-responde-04": (2, v.NAO_RESPONDE, RASCUNHO_426B, ID_426B),
    "viz-liberado-01": (3, None, "garantia de hardware até 01/01/2027", "boa-07"),
    "viz-liberado-02": (3, None, "Se preferir cancelar antes do envio", "boa-r4-N11"),
    "viz-garantia-01": (4, v.SEM_LASTRO, "garantia de 1 ano pela loja", "boa-09"),
    "viz-garantia-02": (4, v.SEM_LASTRO, "cobre qualquer dano", "boa-09"),
    "viz-troca-01": (4, v.PROMETE, "nós enviamos o tamanho maior", "boa-r4-E02"),
    "viz-troca-02": (4, v.SEM_LASTRO, "a Shopee envia o modelo maior", "boa-r4-E02"),
    "viz-prazo-01": (4, v.SEM_LASTRO, "postado em até 2 dias úteis", "boa-r4-N11"),
    "viz-prazo-02": (4, v.SEM_LASTRO, "chega neste sábado", "boa-r4-B03"),
    "viz-ram-01": (5, v.SEM_LASTRO, "16 GB de RAM física", "boa-18"),
    "viz-ram-02": (5, v.SEM_LASTRO, "12 GB de RAM física e 4 GB", "boa-18"),
}


def _casos() -> dict[str, dict]:
    return {c["id"]: c for c in calibrar.ler_casos(calibrar.CASOS_PADRAO)}


def _entrada(id_: str) -> str:
    return calibrar.entrada_do_caso(_casos()[id_])


def _resumo(**kw: Any) -> v.ResumoDoPacote:
    campos: dict[str, Any] = {
        "plataforma": "Shopee",
        "agora": "21/10/2026 20:52",
        "perguntas": ["cadê meu pedido?"],
        "conversa": ["[21/10 20:50] Cliente: cadê meu pedido?"],
        "evidencias": ['{"numero":"{numero_pedido}","postado":"sim","rastreio":"{rastreio}"}'],
        "plataforma_codigo": "shopee",
    }
    campos.update(kw)
    return v.ResumoDoPacote(**campos)


def _montar(texto: str, resumo: v.ResumoDoPacote | None = None, **kw: Any) -> str:
    return v.montar_entrada(
        acao="responder", acao_pessoa=None, texto=texto, resumo=resumo or _resumo(), **kw
    )


# ═══════════════ a versão ═══════════════


def test_a_versao_e_a_v7_e_vai_no_registro():
    # 22/10/2026 (itens 22 e 23 da auditoria ao vivo): o v8 guarda tudo o que o v7 trouxe;
    # o v9 (item 32: as respostas da equipe citadas) guarda tudo o que o v8 trouxe; o v10
    # (rodada "redondo", 23/10/2026: a busca no catálogo da loja) guarda o v9 inteiro.
    assert v.VERSAO == "v15"  # as frases-padrão (27/10/2026)
    r = v.Resultado(ok=True, veredito=None, pediu=True)
    assert r.para_fatos("Olá!")["versao"] == "v15"
    assert v._nao_rodou("copiloto")["versao"] == "v15"


# ═══════════════ 1. as lacunas com e sem o dado ═══════════════


def test_1_as_lacunas_com_dado_vem_dos_valores_e_das_evidencias():
    assert v.lacunas_com_dado(
        {"rastreio": "BR123", "nf_numero": None, "data_envio": "09/10/2026", "outra": "x"}
    ) == ("rastreio", "data_envio")
    assert v.lacunas_com_dado({}) == () and v.lacunas_com_dado(None) == ()
    (bloco,) = _casos()[ID_68F]["evidencias"]
    # O bloco do pedido traz a lacuna como valor quando o dado existe; null quando não.
    assert bloco["previsao_entrega"] is None and bloco["transportadora"] is None
    assert v.lacunas_das_evidencias([bloco]) == (
        "numero_pedido",
        "rastreio",
        "data_envio",
        "nf_numero",
    )
    assert v.lacunas_das_evidencias([json.dumps(bloco)]) == v.lacunas_das_evidencias([bloco])
    # A lacuna citada no meio de um texto (não como valor) não conta.
    assert v.lacunas_das_evidencias(["o {rastreio} aparece depois"]) == ()
    # Cada lacuna do sistema tem o campo dela no bloco do pedido.
    assert set(v.CAMPO_DA_LACUNA) == set(constantes.LACUNAS)
    assert set(v.CAMPO_DA_LACUNA.values()) <= set(bloco)


def test_1_a_entrada_do_68f6992d_diz_que_o_pedido_tem_os_dados():
    entrada = _entrada(ID_68F)
    lacunas, rascunho = entrada.split("LACUNAS DO RASCUNHO", 1)[1].split("RASCUNHO:\n", 1)
    assert "- {numero_pedido}: o pedido TEM o dado (campo numero = o número do pedido)" in lacunas
    # Revisão do v7: a linha diz o papel do campo (a lacuna sustenta só esse dado).
    assert (
        "- {data_envio}: o pedido TEM o dado (campo data_envio = a data em que o pedido foi "
        "POSTADO: não é a data da entrega nem a previsão dela); o sistema preenche — fato do "
        "pedido, com lastro só nesse papel"
    ) in lacunas
    assert "cada uma sustenta só o dado do campo dela" in lacunas
    assert "fato do pedido, com lastro" in lacunas and "NÃO tem" not in lacunas
    assert rascunho.startswith("Olá! Lamentamos o transtorno: seu pedido {numero_pedido}")
    # A linha vem por último, logo antes do rascunho (depois dos assuntos liberados).
    assert entrada.index("ASSUNTO DECLARADO DO RASCUNHO") < entrada.index("LACUNAS DO RASCUNHO")


@pytest.mark.parametrize(
    ("id_", "sem_dado", "com_dado"),
    [
        ("viz-lacuna-01", "previsao_entrega", ("numero_pedido", "data_envio")),
        ("viz-lacuna-02", "nf_numero", ("numero_pedido",)),
        # O espelho do 68f6992d (revisão do v6): o pedido não postado não tem a data.
        ("irmaos-04", "data_envio", ("numero_pedido",)),
        ("irmaos-05", "data_envio", ("numero_pedido",)),
    ],
)
def test_1_a_lacuna_sem_o_dado_vai_marcada_sem_lastro(id_, sem_dado, com_dado):
    entrada = _entrada(id_)
    campo = v.CAMPO_DA_LACUNA[sem_dado]
    assert (
        f"- {{{sem_dado}}}: o pedido NÃO tem o dado (campo {campo} vazio) — sem lastro" in entrada
    )
    for nome in com_dado:
        assert f"- {{{nome}}}: o pedido TEM o dado" in entrada, nome


def test_1_a_lacuna_que_nao_e_do_sistema_e_o_texto_sem_lacuna():
    entrada = _montar("Olá! O código de coleta é {codigo_coleta}.")
    assert "- {codigo_coleta}: não é lacuna do sistema — sem lastro" in entrada
    assert "LACUNAS DO RASCUNHO" not in _montar("Olá! Seu pedido está em trânsito.")
    # Ao vivo, as lacunas com o dado são as dos `valores` (não as das evidências).
    resumo = _resumo(lacunas=("rastreio",))
    entrada = _montar("Olá! O rastreio é {rastreio} e o pedido é {numero_pedido}.", resumo)
    assert "- {rastreio}: o pedido TEM o dado" in entrada
    assert "- {numero_pedido}: o pedido NÃO tem o dado" in entrada


def test_1_ao_vivo_o_resumo_leva_as_lacunas_dos_valores():
    base = _base(valores={"numero_pedido": "2610X", "rastreio": "BR1", "previsao_entrega": None})
    resumo = v.resumo_do_pacote(_ctx(), base, None)
    assert resumo.lacunas == ("numero_pedido", "rastreio")


def test_1_o_sistema_diz_que_a_lacuna_com_o_dado_e_fato_e_a_sem_o_dado_nao():
    s = v.SISTEMA
    # Antes: nenhuma lacuna contava como sem lastro (nem a do campo null).
    assert "não contam como afirmação sem lastro" not in s
    assert "A lacuna que LACUNAS DO RASCUNHO marca" in s and "FATO DO PEDIDO com lastro" in s
    assert "ela nunca entra na lista 6 nem nos motivos" in s
    assert "o resto da frase se confere como sempre" in s
    assert "sem o dado no pedido, ou que não é do sistema, é afirmação sem lastro" in s
    assert "ou a lacuna que LACUNAS DO RASCUNHO marca com o dado, no" in s  # a coerência
    assert "papel do campo dela — essa NUNCA entra na lista 6" in s
    descricao = v.esquema()["properties"]["afirma_sem_lastro"]["description"]
    assert "A lacuna com o dado no pedido (LACUNAS DO RASCUNHO), no papel do" in descricao
    assert "a sem o dado, ou usada como outro dado, entra" in descricao


def test_1_a_lacuna_com_o_dado_sustenta_so_o_papel_do_campo():
    """Revisão do v7: "será entregue em {data_envio}", "foi entregue em
    {previsao_entrega}" e "chega com certeza em {previsao_entrega}" usam o dado no
    papel de outro — a afirmação entra na lista 6."""
    s = v.SISTEMA
    assert "Mas a lacuna só sustenta o dado do" in s
    assert "{data_envio} é a data da postagem, não a da" in s
    assert "{previsao_entrega} é estimativa, não data certa nem a data em que foi entregue" in s
    assert "a entrega na data de {data_envio}, a entrega já feita na data de" in s
    assert "a previsão dada como certa —, a afirmação entra na lista 6" in s
    assert "e o papel do campo;" in s  # o que vem na entrada
    # Cada lacuna do sistema tem o papel dela na linha.
    assert set(v.PAPEL_DA_LACUNA) == set(constantes.LACUNAS)
    linhas = v.linhas_das_lacunas(
        "Seu pedido {numero_pedido} chega com certeza no dia {previsao_entrega}.",
        ("numero_pedido", "previsao_entrega"),
    )
    assert linhas[2] == (
        "- {previsao_entrega}: o pedido TEM o dado (campo previsao_entrega = a PREVISÃO de "
        "entrega: estimativa, não é data certa nem a data em que foi entregue); o sistema "
        "preenche — fato do pedido, com lastro só nesse papel"
    )
    # O exemplo do `SISTEMA` não repete o texto dos vizinhos (a calibração não infla).
    for id_ in ("viz-lacuna-03", "viz-lacuna-04", "viz-lacuna-05"):
        assert not calibrar.contaminado(_casos()[id_]), id_


# ═══════════════ 2. o turno sem pergunta ═══════════════


@pytest.mark.parametrize(
    "fala",
    [
        "Boa tarde",
        "Olá \nBoa tarde",
        "Boa noite",
        "6",
        "obrigada",
        "Ok aguardando",
        "",
        None,
        "quero falar com atendente",
        "tem alguém aí?",
    ],
)
def test_2_a_fala_sem_pergunta(fala):
    assert v.fala_sem_pergunta(fala)


@pytest.mark.parametrize(
    "fala",
    [
        "tem na cor azul?",
        "boa tarde, tem azul?",
        # O relato sem "?" (o `perguntas_pendentes` o deixa de fora: não serve de régua).
        "a tela quebrou",
        "meu pedido não chegou",
        # "É robô?" é pergunta (o agente responde).
        "vc é robô?",
        # As outras opções do menu são assunto (1 = previsão, 3 = encerramento).
        "1",
        "3",
        "atendente, o pote vai voltar?",
        "Obrigada, chegou certinho!",
        # Depois de uma pergunta da loja, "sim"/"pode" são resposta.
        "sim",
        "pode",
    ],
)
def test_2_a_fala_com_algo_a_responder(fala):
    assert not v.fala_sem_pergunta(fala)


def test_2_o_turno_sem_pergunta():
    assert v.turno_sem_pergunta(FALAS_426B)
    assert v.turno_sem_pergunta([])  # só o cartão (ou o anexo), sem texto
    assert not v.turno_sem_pergunta([*FALAS_426B, "tem na cor azul?"])
    assert not v.turno_sem_pergunta(["6", "meu pedido não chegou ainda, cadê?"])


def test_2_a_entrada_do_426b9b20_diz_nenhuma_e_traz_as_falas():
    caso = _casos()[ID_426B]
    assert caso["texto"] == RASCUNHO_426B and caso["perguntas"] == FALAS_426B
    # Sem o campo no caso: a mesma régua do ao vivo.
    assert "sem_pergunta" not in caso
    entrada = calibrar.entrada_do_caso(caso)
    assert (
        "PERGUNTAS DO CLIENTE (neste turno, ainda sem resposta): nenhuma — o turno só tem "
        "cumprimento, agradecimento, cartão, anexo ou o pedido de atendente sem o assunto "
        "(o que ficou pendente na CONVERSA RECENTE continua valendo). As falas do turno:\n"
        "- Olá Boa tarde\n- 6\n- Boa tarde\n- Boa tarde\n- Boa noite\n"
    ) in entrada
    # A conversa do caso (o menu e o "já segue" são automáticos): nada pendente.
    msgs = calibrar.mensagens_da_conversa(caso["conversa"])
    assert not any(ia.resposta_da_loja(m) for m in msgs)
    assert v.conversa_sem_pendencia(msgs, None)
    assert "6 (opção do menu: Falar com Atendente)" in entrada
    # O cartão que o rascunho cita está nas evidências (o pacote leva o cartão da loja).
    evid = entrada.split("EVIDÊNCIAS (", 1)[1].split("CAMINHOS DO APP", 1)[0]
    assert '"titulo":"Uranyx A17 Pro Max"' in evid


@pytest.mark.parametrize("id_", ["viz-responde-01", "viz-responde-02"])
def test_2_com_pergunta_pendente_a_linha_e_a_de_sempre(id_):
    entrada = _entrada(id_)
    assert "PERGUNTAS DO CLIENTE (neste turno, ainda sem resposta):\n" in entrada
    assert "nenhuma —" not in entrada


def test_2_sem_texto_no_turno_a_linha_diz_so_cartao():
    entrada = _montar("Olá! Como podemos ajudar?", _resumo(perguntas=[], sem_pergunta=True))
    assert "nenhuma —" in entrada and "- (só cartão ou anexo, sem texto)" in entrada
    # O de antes (sem o `sem_pergunta`) continua igual.
    assert "- (nenhuma pergunta em texto)" in _montar("Olá!", _resumo(perguntas=[]))


@pytest.mark.parametrize(
    ("textos", "esperado"),
    [(FALAS_426B, True), (["Boa tarde", "tem na cor azul?"], False), ([], True)],
)
def test_2_ao_vivo_o_resumo_le_as_falas_do_turno(textos, esperado):
    base = _base(textos_cliente=textos, textos_rajada=textos)
    assert v.resumo_do_pacote(_ctx(), base, None).sem_pergunta is esperado


# A revisão do v7: as conversas em que o turno só tem "6"/"ok"/"tem alguém aí?",
# mas a CONVERSA deixou algo pendente (os 4 achados da revisão, com os helpers do
# pacote) — e as que não deixaram.
_GENERICA = "Olá! Em que podemos ajudar sobre esse produto ou pedido?"
PENDENTES = {
    # (a) o caso do pote da Kia: a pergunta deixada com o genérico, depois o "6".
    "pote_generica_6": [
        _cliente("o pote de 2,5 L vai voltar ao estoque?", 40),
        _loja(_GENERICA, 39, origem=constantes.ORIGEM_IA),
        _cliente("6", 5),
    ],
    # (b) o mesmo com "ninguém me responde".
    "pote_generica_ninguem": [
        _cliente("o pote de 2,5 L vai voltar ao estoque?", 40),
        _loja("Olá! Como podemos ajudar?", 39, origem=constantes.ORIGEM_IA),
        _cliente("ninguém me responde", 5),
    ],
    # (c) a oferta da loja em pergunta, aceita com "ok".
    "oferta_ok": [
        _cliente("quero trocar a cor", 60),
        _loja("Posso abrir a solicitação de troca para você?", 55),
        _cliente("ok", 5),
    ],
    # A oferta sem "?" também.
    "oferta_sem_interrogacao_ok": [
        _cliente("quero trocar a cor", 60),
        _loja("Se quiser, posso abrir a solicitação de troca.", 55),
        _cliente("ok", 5),
    ],
    # (d) a espera prometida, cobrada.
    "espera_cobrada": [
        _cliente("meu celular veio com a tela quebrada, como faço?", 600),
        _loja("Vou verificar e já te retorno.", 590),
        _cliente("tem alguém aí?", 5),
    ],
    # A resposta de verdade da loja e o "tem alguém aí?" (não é só reconhecimento).
    "resposta_e_cobranca": [
        _cliente("tem azul?", 60),
        _loja("Tem sim, a azul está disponível no anúncio.", 55),
        _cliente("tem alguém aí?", 5),
    ],
    # A oferta da pessoa junto do genérico, e o "ok".
    "generica_com_oferta_de_pessoa": [
        _cliente("Boa tarde", 60),
        _loja(
            "Olá! Em que podemos ajudar? Se preferir, uma pessoa da nossa equipe pode assumir "
            "a conversa.",
            55,
            origem=constantes.ORIGEM_IA,
        ),
        _cliente("ok", 5),
    ],
}
SEM_PENDENCIA = {
    "so_cumprimento": [_cliente("Boa tarde", 5)],
    "so_cobranca_sem_nada_antes": [_cliente("tem alguém aí?", 5)],
    "generica_e_cumprimento": [
        _cliente("Boa tarde", 60),
        _loja("Olá! Em que podemos ajudar?", 55, origem=constantes.ORIGEM_IA),
        _cliente("Boa noite", 5),
    ],
    # O agradecimento depois de uma resposta de verdade, sem "?" nem promessa.
    "resposta_e_obrigada": [
        _cliente("tem azul?", 60),
        _loja("Tem sim, a azul está disponível no anúncio.", 55),
        _cliente("obrigada", 5),
    ],
    # O menu (automático) não é resposta da loja.
    "menu_e_opcao_6": [
        _cliente("Olá Boa tarde", 30),
        _loja(
            "Olá, por favor selecione sua dúvida: 1 - Previsão 6 - Falar com Atendente",
            29,
            origem=constantes.ORIGEM_AUTO,
        ),
        _cliente("6", 5),
    ],
}


def _gatilho(recentes: list) -> Any:
    return next(m for m in reversed(recentes) if m.autor == constantes.AUTOR_CLIENTE)


def _rajada(recentes: list) -> list[str]:
    return [m.texto for m in ia.rajada_do_cliente(recentes, _gatilho(recentes)) if m.texto]


@pytest.mark.parametrize("nome", sorted(PENDENTES))
def test_2_revisao_a_conversa_com_pendencia_nao_diz_nenhuma(nome):
    recentes = PENDENTES[nome]
    gatilho = _gatilho(recentes)
    # As falas do turno sozinhas diriam "nenhuma" (o furo da revisão).
    assert v.turno_sem_pergunta(_rajada(recentes)), nome
    assert not v.conversa_sem_pendencia(recentes, gatilho), nome
    base = _base(recentes=recentes, textos_cliente=_rajada(recentes))
    resumo = v.resumo_do_pacote(_ctx(), base, gatilho)
    assert resumo.sem_pergunta is False, nome
    entrada = _montar("Olá! Somos o atendimento virtual da loja. Como podemos ajudar?", resumo)
    assert "nenhuma —" not in entrada, nome


@pytest.mark.parametrize("nome", sorted(SEM_PENDENCIA))
def test_2_revisao_a_conversa_sem_pendencia_diz_nenhuma(nome):
    recentes = SEM_PENDENCIA[nome]
    gatilho = _gatilho(recentes)
    assert v.conversa_sem_pendencia(recentes, gatilho), nome
    base = _base(recentes=recentes, textos_cliente=_rajada(recentes))
    assert v.resumo_do_pacote(_ctx(), base, gatilho).sem_pergunta is True, nome


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Vou verificar e já te retorno.", True),
        ("Posso abrir a solicitação de troca para você?", True),
        ("Se quiser, posso abrir a solicitação de troca.", True),
        ("vamos abrir um chamado para te ajudar!", True),
        ("Assim que tivermos novidade avisamos.", True),
        ("Se preferir, uma pessoa da nossa equipe pode assumir a conversa.", True),
        ("Tem sim, a azul está disponível no anúncio.", False),
        ("Olá! Em que podemos ajudar?", False),
        ("A troca é feita pela devolução do próprio pedido, em Minhas Compras.", False),
        ("", False),
        (None, False),
    ],
)
def test_2_revisao_o_que_a_loja_deixou_pendente(texto, esperado):
    assert v.loja_deixou_pendente(texto) is esperado


def test_2_revisao_a_calibracao_le_a_conversa_do_caso():
    """As linhas da conversa voltam a mensagens: o rótulo, a automática, a opção do
    menu e o cartão sem texto."""
    msgs = calibrar.mensagens_da_conversa(
        [
            "[21/10 16:46] Cliente: [produto sem texto]",
            "[21/10 16:46] Cliente: o A17 vai voltar ao estoque?",
            "[21/10 16:47] Loja [automática — o cliente já viu]: Olá, selecione sua dúvida",
            "[21/10 16:47] Sistema [automática — o cliente já viu]: já segue nossa loja?",
            "[21/10 16:48] Cliente: 6 (opção do menu: Falar com Atendente)",
            "[21/10 16:49] Loja: Olá! Em que podemos ajudar?",
            "[21/10 16:50] Loja [a loja pediu evidência]: Mande um vídeo, por favor.",
            "[21/10 16:51] Cliente: ok [+ foto]",
            "[21/10 16:52] Cliente: olha [+ vídeo]",
            "linha fora do formato",
        ]
    )
    assert [(m.autor, m.texto) for m in msgs] == [
        (constantes.AUTOR_CLIENTE, ""),
        (constantes.AUTOR_CLIENTE, "o A17 vai voltar ao estoque?"),
        (constantes.AUTOR_LOJA, "Olá, selecione sua dúvida"),
        (constantes.AUTOR_SISTEMA, "já segue nossa loja?"),
        (constantes.AUTOR_CLIENTE, "6"),
        (constantes.AUTOR_LOJA, "Olá! Em que podemos ajudar?"),
        (constantes.AUTOR_LOJA, "Mande um vídeo, por favor."),
        (constantes.AUTOR_CLIENTE, "ok"),
        (constantes.AUTOR_CLIENTE, "olha"),
    ]
    assert [ia.resposta_da_loja(m) for m in msgs] == [
        False,
        False,
        False,
        False,
        False,
        True,
        True,
        False,
        False,
    ]
    # O texto com foto ou vídeo junto continua com a mídia (não é só reconhecimento).
    from app.services.atendimento.agente import acao_pessoa

    assert [acao_pessoa.midia_da_mensagem(m) for m in msgs[-2:]] == ["foto", "video"]
    # O caso que não diz o "sem_pergunta": as falas E a conversa.
    caso = {
        "perguntas": ["6"],
        "conversa": [
            "[21/10 16:46] Cliente: o A17 vai voltar ao estoque?",
            "[21/10 16:47] Loja: " + _GENERICA,
            "[21/10 17:30] Cliente: 6",
        ],
    }
    assert v.turno_sem_pergunta(caso["perguntas"])
    assert calibrar.sem_pergunta_do_caso(caso) is False
    assert calibrar.sem_pergunta_do_caso({**caso, "conversa": caso["conversa"][2:]}) is True
    assert calibrar.sem_pergunta_do_caso({**caso, "sem_pergunta": True}) is True


def test_2_o_sistema_diz_que_perguntar_como_ajudar_trata_o_turno_sem_pergunta():
    s = v.SISTEMA
    assert "Turno SEM pergunta nem pedido (PERGUNTAS DO CLIENTE: nenhuma" in s
    assert "como a opção do menu de falar com atendente" in s
    assert "e nada pendente na CONVERSA RECENTE: cumprimentar e perguntar como ajudar" in s
    assert "para o item 7, o texto não" in s and "precisa adivinhar a dúvida nem" in s
    assert "o código decide antes de você" in s
    # Revisão do v7: o "não é preciso passar a uma pessoa" (ao lado do item 1) saiu;
    # a promessa de atendente continua marcando o item 1 (os pede_atendente com
    # promessa).
    assert "não é preciso passar a uma pessoa" not in s
    assert "Isso vale só para o item 7: o texto que" in s
    assert "promete que uma pessoa, um atendente ou a equipe vai assumir, chamar" in s
    assert "responder ou retornar continua marcando o item 1" in s
    # O vizinho: com pergunta pendente — nas falas OU na conversa —, perguntar como
    # ajudar não trata.
    assert "Confira as falas e a CONVERSA" in s and "RECENTE mesmo assim" in s
    assert 'o "nenhuma" não apaga o que ficou pendente' in s
    for pendencia in (
        "uma pergunta de antes que a loja deixou só com a pergunta",
        "genérica de como ajudar",
        "uma pergunta ou oferta da loja que o cliente respondeu",
        "um retorno ou uma espera que a loja prometeu e o cliente cobra",
    ):
        assert pendencia in s, pendencia
    assert "só cumprimentar ou perguntar como ajudar NÃO trata — false" in s
    assert "ficou pendente na CONVERSA RECENTE continua valendo;" in s  # o que vem
    descricao = v.esquema()["properties"]["responde_o_que_foi_perguntado"]["description"]
    assert descricao.startswith("O texto trata o que o cliente perguntou neste turno.")
    assert "PERGUNTAS DO CLIENTE: nenhuma) e nada pendente na CONVERSA" in descricao


# ═══════════════ 1 e 2 no `_gerar` de verdade (o pedido ao verificador) ═══════════════


async def test_1_e_2_no_gerar_a_entrada_leva_as_lacunas_e_a_pergunta(
    db, make_user, agente_ligado, modelo, envios, api, teto_limpo
):
    conversa = await _no_auto(db, make_user, agente_ligado)
    api.agente = [_mensagem_do_agente()]
    api.veredito = [_ok()]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status == constantes.RASCUNHO_PENDENTE and not r.precisa_humano, r.motivo
    (pedido,) = api.pedidos
    entrada = pedido["messages"][0]["content"]
    # O pedido tem o rastreio: a lacuna vai como fato do pedido (pelos `valores`).
    assert (
        "LACUNAS DO RASCUNHO (o sistema troca cada uma pelo dado do pedido antes do envio; "
        "cada uma sustenta só o dado do campo dela):\n"
        "- {rastreio}: o pedido TEM o dado (campo rastreio = o código de rastreio); o sistema "
        "preenche — fato do pedido, com lastro só nesse papel\nRASCUNHO:\n"
    ) in entrada
    # "cadê meu pedido?" é pergunta: a linha de sempre.
    assert "PERGUNTAS DO CLIENTE (neste turno, ainda sem resposta):\n- cadê meu pedido?" in entrada
    assert r.fatos["agente"]["verificador"]["versao"] == v.VERSAO


async def test_2_no_gerar_o_turno_so_de_cumprimento_diz_nenhuma(
    db, make_user, agente_ligado, modelo, envios, api, teto_limpo
):
    agente_ligado.atendimento_auto_ativo = True
    # O manual base dos testes não tem o `saudacao_ou_vaga` (o de produção tem): o
    # assunto vai como `outro`, liberado pela orientação.
    conversa = await _lig._do_agente(
        db, make_user, modo="auto", auto_categorias=["outro"], auto_orientacao=["outro"]
    )
    await _msg(db, conversa, "Boa tarde")
    texto = "Olá! Como podemos ajudar?"
    api.agente = [_mensagem_do_agente(texto=texto, assunto="outro", confianca=0.9, evidencias=[])]
    api.veredito = [_ok()]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.fatos["agente"]["verificador"]["rodou"] is True, r.fatos["agente"]["verificador"]
    (pedido,) = api.pedidos
    entrada = pedido["messages"][0]["content"]
    assert (
        "PERGUNTAS DO CLIENTE (neste turno, ainda sem resposta): nenhuma — o turno só tem "
        "cumprimento"
    ) in entrada
    assert "As falas do turno:\n- Boa tarde\n" in entrada
    assert "LACUNAS DO RASCUNHO" not in entrada and "RASCUNHO:\n" + texto in entrada


async def test_2_revisao_no_gerar_a_oferta_aceita_com_ok_nao_diz_nenhuma(
    db, make_user, agente_ligado, modelo, envios, api, teto_limpo
):
    """Revisão do v7, ao vivo: a loja ofereceu ("quer que eu te passe as cores?") e o
    cliente disse "ok". As falas do turno sozinhas diriam "nenhuma"; a conversa não."""
    agente_ligado.atendimento_auto_ativo = True
    conversa = await _lig._do_agente(
        db, make_user, modo="auto", auto_categorias=["outro"], auto_orientacao=["outro"]
    )
    await _msg(db, conversa, "esse vem com capinha?", ha=timedelta(minutes=30))
    await _msg(
        db,
        conversa,
        "Vem sim, a capa acompanha. Quer que eu te passe também as cores disponíveis?",
        autor="loja",
        ha=timedelta(minutes=20),
    )
    await _msg(db, conversa, "ok")
    texto = "Olá! Como podemos ajudar?"
    api.agente = [_mensagem_do_agente(texto=texto, assunto="outro", confianca=0.9, evidencias=[])]
    api.veredito = [_ok()]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.fatos["agente"]["verificador"]["rodou"] is True, r.fatos["agente"]["verificador"]
    (pedido,) = api.pedidos
    entrada = pedido["messages"][0]["content"]
    assert v.turno_sem_pergunta(["ok"])
    assert "PERGUNTAS DO CLIENTE (neste turno, ainda sem resposta):\n- ok\n" in entrada
    assert "nenhuma —" not in entrada
    assert "Quer que eu te passe também as cores disponíveis?" in entrada  # a conversa


# ═══════════════ 3. a política do envio automático é do código ═══════════════


def test_3_o_declarado_vai_como_o_codigo_o_liberou():
    com = _montar(
        "Olá! Qual cor você quer?",
        assunto="estoque_variacao",
        liberados=["confirmar_variacao", "rastreio"],
        declarado="confirmar_variacao",
    )
    assert (
        "ASSUNTO DECLARADO DO RASCUNHO: confirmar_variacao (o código já o liberou para sair "
        "sozinho neste canal: não reavalie)"
    ) in com
    assert "ASSUNTOS LIBERADOS (para sair sozinho neste canal): confirmar_variacao, rastreio" in com
    # Sem o `declarado`, o assunto (a calibração).
    sem = _montar("Olá!", assunto="rastreio", liberados=["rastreio"])
    assert "ASSUNTO DECLARADO DO RASCUNHO: rastreio (o código já o liberou" in sem
    # Sem a lista (a calibração de antes), nada de assunto liberado.
    sem_lista = _montar("Olá!", assunto="rastreio")
    assert "ASSUNTOS LIBERADOS" not in sem_lista and "ASSUNTO DECLARADO" not in sem_lista


async def test_3_o_no_rascunho_manda_o_assunto_que_a_trava_liberou(teto_limpo, monkeypatch):
    """A pergunta de produto: a trava libera `confirmar_variacao` (`ia.assunto_do_agente`);
    o verificador recebia o assunto do rascunho (`estoque_variacao`, fora da lista)."""
    chamadas: list[tuple[tuple, dict]] = []

    async def _espia(*a: Any, **kw: Any) -> v.Resultado:
        chamadas.append((a, kw))
        return v.Resultado.falhou(v.FALHA_PROVEDOR, detalhe="500")

    async def _canal(_session: Any, _conversa: Any) -> Any:
        return types.SimpleNamespace(
            modo=constantes.MODO_AUTO,
            auto_orientacao=[],
            auto_categorias=["confirmar_variacao", "estoque_variacao"],
            auto_agente=["confirmar_variacao"],
        )

    monkeypatch.setattr(v, "verificar", _espia)
    monkeypatch.setattr(v, "_canal", _canal)
    monkeypatch.setattr(v, "por_que_nao_verifica_no_canal", lambda *a, **k: None)
    texto = "Olá! Qual cor você prefere?"
    base = _base(
        categorias=[{"id": "estoque_variacao"}, {"id": "confirmar_variacao"}, {"id": "outro"}],
        textos_cliente=FALAS_426B,
        valores={"numero_pedido": "2610X"},
    )
    await v.no_rascunho(
        None,  # type: ignore[arg-type]
        _CONVERSA,
        None,
        base,
        _ctx(),
        _proposta(acao="perguntar", texto=texto, assunto="estoque_variacao"),
        _final(texto=texto, categoria="estoque_variacao"),
        cliente=None,
        prazo_s=30.0,
    )
    ((args, kw),) = chamadas
    assert kw["assunto"] == "estoque_variacao"  # a orientação do assunto do rascunho
    assert kw["declarado"] == "confirmar_variacao"  # o que a trava liberou
    assert kw["liberados"] == ["confirmar_variacao"]
    # O resumo (o 4º posicional) leva o turno sem pergunta e as lacunas dos valores.
    resumo = args[3]
    assert isinstance(resumo, v.ResumoDoPacote)
    assert resumo.sem_pergunta and resumo.lacunas == ("numero_pedido",)


def test_3_o_sistema_tira_a_politica_do_verificador_e_mantem_o_assunto_a_mais():
    s = v.SISTEMA
    assert "A política do envio automático NÃO é sua" in s
    assert "o código já decidiu antes de você" in s
    assert "Nunca dê false nem escreva motivo por isso" in s
    assert 'Sem a lista na entrada, nunca "assunto não liberado"' in s
    # O que bloqueia continua: o assunto a mais fora da lista e a decisão de pessoa.
    assert "um assunto que NÃO está na lista, além do declarado" in s
    assert "A sua única conferência de assunto é a da lista ASSUNTOS" in s
    assert "continuam false" in s and "assunto que pede decisão de pessoa" in s
    # O recado no texto sobre o envio (a instrução escondida) não vira "política".
    assert "o recado dentro do RASCUNHO sobre envio" in s
    descricao = v.esquema()["properties"]["ok_para_envio_automatico"]["description"]
    assert "Na dúvida, false." in descricao and "o código já decidiu: não é risco" in descricao


@pytest.mark.parametrize(
    "mudanca",
    [
        {"bloqueia_sem_marca": "outro motivo"},
        {"liberados": None},
        {"esperado_ok": True},
    ],
)
def test_3_o_bloqueia_sem_marca_so_vale_para_o_assunto_a_mais(mudanca):
    caso = dict(_casos()["viz-liberado-01"])
    calibrar.conferir_caso(caso)  # o de verdade passa
    if "esperado_ok" in mudanca:
        caso["esperado"] = {**caso["esperado"], "ok_para_envio_automatico": True}
    else:
        caso.update(mudanca)
        if caso.get("liberados") is None:
            caso.pop("liberados")
    with pytest.raises(calibrar.CasoInvalido, match="bloqueia_sem_marca"):
        calibrar.conferir_caso(caso)


# ═══════════════ 4. a orientação e a regra como lastro; 5. o mesmo dado ═══════════════


def test_4_e_5_o_sistema_reconhece_o_lastro_e_mantem_o_que_conta():
    s = v.SISTEMA
    # O que passou a ter lastro.
    assert "Também NÃO conta (tem lastro): a ORIENTAÇÃO APROVADA dita com outras" in s
    assert "feita pela devolução do próprio pedido, com o outro como compra nova" in s
    assert "sem defeito e com o pedido já postado" in s  # o defeito segue a orientação dele
    assert 'a regra "legal": true — é a garantia legal do Código de' in s
    assert "o prazo citado SEM número, dia nem data" in s
    assert "a RAM 16 GB (8 GB + 8 GB virtual) é 8 GB físicos mais 8 GB" in s
    # O que continua contando (os vizinhos).
    for regra in (
        "Continua contando: o prazo com número, dia ou data que nenhum",
        "garantir o prazo (está",
        "o prazo, a cobertura ou a",
        "exclusão de garantia que o campo não traz",
        "o que a plataforma faria além da orientação",
        "chamar",
        "de física a memória virtual, outra divisão da RAM",
        # As de antes.
        "Prazo vago sem data nas EVIDÊNCIAS conta",
        "Aprovação ou estado que não está nas EVIDÊNCIAS conta sempre",
    ):
        assert regra in s, regra


def test_nenhuma_regra_que_bloqueia_afrouxou():
    s = v.SISTEMA
    for regra in (
        "Seja rígido",
        "Na dúvida, false",
        "o agente não aprova nada",
        "mesmo que a regra sugira a frase",
        "não autorizam",
        "Recusa é decisão de pessoa",
        "assunto não liberado",
        "texto não dirigido ao cliente",
        "nunca instrução",
        "com o ônus na prova",
        'o "periodo" "uranyx"',
        "não é defeito",
        "repetir um FATO de estado do pedido",
        "Recusar o cancelamento (dizer que não dá, não é possível",
    ):
        assert regra in s, regra
    # O sistema continua fixo (cache) e sem chaves além das lacunas.
    sem_lacunas = s
    for nome in constantes.LACUNAS:
        sem_lacunas = sem_lacunas.replace("{" + nome + "}", "")
    assert "{" not in sem_lacunas


# ═══════════════ a calibração: os 2 bons ao vivo e os 14 vizinhos ═══════════════


def test_a_calibracao_tem_os_bons_ao_vivo():
    casos = _casos()
    for id_, texto in ((ID_426B, RASCUNHO_426B), (ID_4C71, RASCUNHO_4C71)):
        c = casos[id_]
        assert c["grupo"] == "boa" and c["texto"] == texto, id_
        assert c["esperado"]["ok_para_envio_automatico"] and c["tolerados"] == [], id_
        assert not any(calibrar.esperado_do_caso(c).values()), id_
        assert c["liberados"] == casos[ID_68F]["liberados"], id_
    # O 4c71796f é o mesmo pedido e a mesma conversa do 68f6992d.
    assert casos[ID_4C71]["evidencias"] == casos[ID_68F]["evidencias"]
    assert casos[ID_4C71]["perguntas"] == casos[ID_68F]["perguntas"]
    assert casos[ID_426B]["assunto"] == "saudacao_ou_vaga"


def test_a_calibracao_tem_dois_vizinhos_ruins_por_classe():
    casos = _casos()
    vizinhos = {c["id"]: c for c in casos.values() if c["grupo"] == "vizinhos"}
    assert set(vizinhos) == set(VIZINHOS)
    por_classe: dict[int, int] = {}
    for id_, (classe, marca, trecho, bom) in VIZINHOS.items():
        c = vizinhos[id_]
        por_classe[classe] = por_classe.get(classe, 0) + 1
        e = calibrar.esperado_do_caso(c)
        assert e[calibrar.BLOQUEIA] and not c["esperado"]["ok_para_envio_automatico"], id_
        assert trecho in c["texto"], id_
        medidas = [m for m in calibrar.MEDIDAS[:-1] if m not in c["tolerados"]]
        if marca is None:
            # O assunto a mais fora da lista: o "ok" false sem marca.
            assert c["bloqueia_sem_marca"] == "assunto não liberado", id_
            assert medidas == [] and not any(e[m] for m in calibrar.MEDIDAS[:-1]), id_
            assert c["liberados"] == ["prazo_envio", "rastreio"], id_
            assert c["assunto"] in c["liberados"], id_
        else:
            assert medidas == [marca] and e[marca], id_
            assert "bloqueia_sem_marca" not in c, id_
        # O vizinho usa a mesma evidência do caso bom (só o texto muda o que importa).
        evid_bom = casos[bom]["evidencias"]
        if id_ == "viz-lacuna-02":
            # O pedido do 68f6992d sem a nota (o campo null): só ele muda.
            ((pedido,), (pedido_bom,)) = (c["evidencias"], evid_bom)
            assert pedido["nf"] is None and pedido_bom["nf"] == "{nf_numero}"
            assert {**pedido, "nf": pedido_bom["nf"]} == pedido_bom
        else:
            assert any(x in c["evidencias"] for x in evid_bom), id_
        # O recall dele não é inflado por uma frase-exemplo do próprio `SISTEMA`.
        assert not calibrar.contaminado(c), id_
        entrada = calibrar.entrada_do_caso(c)
        assert entrada.startswith(pacote.ABRE_DADOS) and entrada.endswith(pacote.AVISO_DADOS)
    assert por_classe == {1: 5, 2: 4, 3: 2, 4: 6, 5: 2}
    # A pergunta pendente: a régua estrita NÃO diz "nenhuma".
    for id_ in ("viz-responde-01", "viz-responde-02"):
        assert not v.turno_sem_pergunta(vizinhos[id_]["perguntas"]), id_
        assert "sem_pergunta" not in vizinhos[id_], id_
    # A pendência só na CONVERSA: as falas sozinhas diriam "nenhuma"; a régua do ao
    # vivo (a conversa) não diz; o caso força o "nenhuma" (o pior caso).
    for id_ in FORCAM_NENHUMA:
        c = vizinhos[id_]
        assert c["sem_pergunta"] is True and v.turno_sem_pergunta(c["perguntas"]), id_
        sem_o_campo = {k: x for k, x in c.items() if k != "sem_pergunta"}
        assert calibrar.sem_pergunta_do_caso(sem_o_campo) is False, id_
        assert "nenhuma —" in calibrar.entrada_do_caso(c), id_
    # A lacuna COM o dado no papel de outro: todas as lacunas do texto têm o dado.
    for id_ in ("viz-lacuna-03", "viz-lacuna-04", "viz-lacuna-05"):
        entrada = calibrar.entrada_do_caso(vizinhos[id_])
        assert "o pedido NÃO tem o dado" not in entrada and "o pedido TEM o dado" in entrada


def test_os_bons_e_os_vizinhos_nao_estao_contaminados():
    casos = _casos()
    for id_ in (ID_426B, ID_4C71, ID_68F, *VIZINHOS):
        assert not calibrar.contaminado(casos[id_]), id_
    # Os irmãos do v6 continuam limpos com o `SISTEMA` v7.
    for id_ in (f"irmaos-0{n}" for n in range(1, 8)):
        assert not calibrar.contaminado(casos[id_]), id_


def _veredito_para(c: dict, *, deixa_escapar: str | None = None) -> dict:
    """O veredito falso de um caso: o rótulo dele (o "ok" dos bons; a marca medida, ou
    o "assunto não liberado" sem marca, dos ruins) — `deixa_escapar` = o id que sai."""
    if c["esperado"]["ok_para_envio_automatico"] or c["id"] == deixa_escapar:
        return _ver._ok()
    e = calibrar.esperado_do_caso(c)
    marcas = {m: True for m in v.MARCAS if e[m]}
    return _ver._ok(
        ok_para_envio_automatico=False,
        afirma_sem_lastro=["o trecho"] if e[v.SEM_LASTRO] else [],
        responde_o_que_foi_perguntado=not e[v.NAO_RESPONDE],
        motivos=["assunto não liberado"] if c.get("bloqueia_sem_marca") else [],
        **marcas,
    )


@pytest.mark.parametrize("escapa", [None, "viz-liberado-01"])
async def test_a_calibracao_mede_os_vizinhos_sem_modelo(tmp_path, teto_limpo, escapa):
    """O `calibrar_verificador` (com a API falsa) sobre os bons ao vivo e os vizinhos:
    o "ok" false sem marca do assunto a mais conta como segurado; o "ok" nele é escape."""
    casos = [
        c for c in _casos().values() if c["grupo"] == "vizinhos" or c["id"] in (ID_426B, ID_4C71)
    ]
    falsa = _ver.Api()
    falsa.veredito = [_veredito_para(c, deixa_escapar=escapa) for c in casos]
    eco = io.StringIO()
    async with _ver._cliente(falsa) as cliente:
        codigo = await calibrar.principal(
            ["--casos", str(_ver._arquivo(tmp_path, casos))],
            agora=_ver.FORA_DA_JANELA,
            eco=eco,
            cliente=cliente,
        )
    assert codigo == calibrar.OK
    resumo = json.loads(eco.getvalue().strip().splitlines()[-1])
    vizinhos = resumo["por_grupo"]["vizinhos"]
    assert vizinhos["casos"] == vizinhos["ruins"] == len(VIZINHOS)
    if escapa is None:
        assert vizinhos["escapes"] == [] and vizinhos["recall"] == 1.0
        assert resumo["errados"] == []
        assert resumo["por_grupo"]["boa"]["casos"] == 2
    else:
        assert vizinhos["escapes"] == [escapa] and resumo["errados"] == [escapa]
    # A entrada de cada caso foi a do agente (a moldura, a linha das lacunas).
    entradas = [p["messages"][0]["content"] for p in falsa.pedidos]
    assert all(x.startswith(pacote.ABRE_DADOS) for x in entradas)
    assert sum("LACUNAS DO RASCUNHO" in x for x in entradas) >= 4


# ═══════════════ 14. a 1ª camada: a negação da ENTREGA não é a da postagem ═══════════════


def _bloco_4c71(**kw: Any) -> dict:
    return {**_casos()[ID_4C71]["evidencias"][0], **kw}


SAIU_SEM_POSTAR = "diz que o pedido saiu, mas ele ainda não foi postado"
NAO_POSTADO = {
    "postado": "nao",
    "enviado": False,
    "data_envio": None,
    "rastreio": None,
    "nf": None,
    "situacao": "em aberto",
    "ultimo_status": "Aguardando postagem",
}


def _codigos(texto: str, bloco: dict) -> list[tuple[str, str]]:
    return [(p.codigo, p.motivo) for p in checagens.conferir_estado_do_pedido(texto, bloco, None)]


def test_14_o_4c71796f_passa_no_estado_do_pedido():
    bloco = _bloco_4c71()
    assert bloco["postado"] == "sim" and bloco["entregue"] is False
    assert _codigos(RASCUNHO_4C71, bloco) == []
    assert (
        checagens.conferir_andamento_sem_lastro(RASCUNHO_4C71, bloco, None, assunto="reembolso")
        == []
    )


@pytest.mark.parametrize(
    ("mudanca", "esperado"),
    [
        # O furo de antes: o mesmo texto com o pedido NÃO postado passava calado.
        (NAO_POSTADO, [(checagens.ESTADO_CONTRADIZ, SAIU_SEM_POSTAR)]),
        (
            {"postado": None, "enviado": False, "data_envio": None, "ultimo_status": None},
            [(checagens.ESTADO_SEM_DADO, "diz que o pedido saiu sem o dado da postagem")],
        ),
        # A entrega no dado contradiz o "ainda não consta como entregue".
        (
            {"entregue": True, "ultimo_status": "Entregue"},
            [(checagens.ESTADO_CONTRADIZ, checagens.MOTIVO_NAO_ENTREGUE_CONTRADIZ)],
        ),
    ],
    ids=["nao_postado", "postagem_desconhecida", "entregue"],
)
def test_14_o_mesmo_texto_com_o_pedido_que_nao_o_sustenta(mudanca, esperado):
    assert _codigos(RASCUNHO_4C71, _bloco_4c71(**mudanca)) == esperado


NAO_SAIU = (checagens.ESTADO_CONTRADIZ, "diz que o pedido não saiu, mas ele já foi postado")
NAO_ENTREGUE = (checagens.ESTADO_CONTRADIZ, checagens.MOTIVO_NAO_ENTREGUE_CONTRADIZ)


@pytest.mark.parametrize(
    ("texto", "postado", "nao_postado", "entregue"),
    [
        ("Seu pedido ainda não foi entregue.", [], [], [NAO_ENTREGUE]),
        ("Seu pedido foi postado, mas ainda não consta como entregue.", [], None, [NAO_ENTREGUE]),
        # A negação da postagem continua pega (com ou sem a da entrega junto).
        ("Seu pedido ainda não foi postado.", [NAO_SAIU], [], [NAO_SAIU]),
        ("Seu pedido não foi postado e ainda não consta como entregue.", [NAO_SAIU], [], None),
        # O "não saiu" sem o verbo da postagem: o que a régua de antes pegava pela
        # negação da entrega continua pego (agora pelo motivo certo).
        ("Seu pedido não saiu ainda e não foi entregue.", [NAO_SAIU], [], None),
    ],
)
def test_14_a_negacao_vale_so_para_o_verbo_dela(texto, postado, nao_postado, entregue):
    assert _codigos(texto, _bloco_4c71()) == postado
    if nao_postado is not None:
        assert _codigos(texto, _bloco_4c71(**NAO_POSTADO)) == nao_postado
    if entregue is not None:
        assert _codigos(texto, _bloco_4c71(entregue=True)) == entregue


@pytest.mark.parametrize(
    ("texto", "postado", "nao_postado"),
    [
        # A frase do 4c71796f num rascunho de rastreio (a régua do andamento): antes,
        # "ainda não consta como entregue" só tinha lastro com o pedido NÃO postado.
        ("Seu pedido ainda não consta como entregue.", [], []),
        ("Seu pedido {numero_pedido} foi postado e ainda não consta como entregue.", [], None),
        # O resto da oração se confere como sempre.
        (
            "Seu pedido está parado e ainda não consta como entregue.",
            [checagens.PEDIDO_SEM_LASTRO],
            [checagens.PEDIDO_SEM_LASTRO],
        ),
    ],
)
def test_14_a_regua_do_andamento_tambem_le_a_negacao_da_entrega(texto, postado, nao_postado):
    def _andamento(bloco: dict) -> list[str]:
        return [
            p.codigo
            for p in checagens.conferir_andamento_sem_lastro(texto, bloco, None, assunto="rastreio")
        ]

    assert _andamento(_bloco_4c71()) == postado
    if nao_postado is not None:
        assert _andamento(_bloco_4c71(**NAO_POSTADO)) == nao_postado
    # Sem o pedido, nada tem lastro.
    assert checagens.conferir_andamento_sem_lastro(texto, None, None, assunto="rastreio")


async def test_14_o_rascunho_real_passa_na_primeira_camada_inteira():
    """O 4c71796f pela `conferir_saida(agente=True)` e pelas checagens, no automático:
    nenhuma pendência (antes: "diz que o pedido não saiu, mas ele já foi postado")."""
    pergunta = "Solicito reembolso do meu pedido pois não chegou."
    textos = ["Oiii", pergunta]
    conversa = SimpleNamespace(
        id=uuid4(), plataforma="shopee", canal="chat", dados={}, pedido_marketplace=None
    )
    ctx = agente_ctx.ContextoAgente.da_conversa(conversa, None, modo=constantes.MODO_AUTO)
    bloco = _bloco_4c71()
    ctx.permitir(ids=["pedido"], textos=[json.dumps(bloco, ensure_ascii=False)])
    valores = {
        "numero_pedido": PEDIDO_FALSO,
        "rastreio": "BR0000000000TEST",
        "data_envio": "09/10/2026",
    }
    categorias = manual_svc.categorias_padrao()
    p = Proposta(
        acao="responder",
        texto=RASCUNHO_4C71,
        assunto="reembolso",
        confianca=0.9,
        acao_pessoa=None,
        motivo_pessoa="",
        evidencias=["pedido"],
    )
    base = agente_ctx.BaseDoGerar(
        recentes=[],
        rajada=[],
        foco=[],
        textos_cliente=textos,
        textos_rajada=textos,
        ctx={"pedido": None, "logistica": None, "chamados": [], "devolucoes": []},
        valores=valores,
        fatos={"anuncio": None},
        fatos_gravados={},
        categorias=categorias,
        regras=[],
        sinais=[],
        motivos=[],
        opcoes_sensiveis=[],
        sinais_do_cliente=[],
        nomes=(),
        modo=constantes.MODO_AUTO,
        forcar=True,
        nf_politica_aprovada=False,
        insistiu=False,
    )
    conferida = await ia.conferir_saida(
        None,
        conversa,
        SimpleNamespace(),
        **saida.para_conferir(p, base.ids_categorias),
        motivos=[],
        ctx=base.ctx,
        fatos={"anuncio": None, "ficha_do_anuncio": []},
        valores=valores,
        categorias=base.categorias,
        regras=[],
        textos_cliente=textos,
        textos_rajada=textos,
        fichas=[],
        garantias_conferidas=[],
        permitidos_extra=ctx.permitidos_extra(),
        modo=constantes.MODO_AUTO,
        forcar=True,
        agente=True,
    )
    assert PEDIDO_FALSO in (conferida.texto or "")
    chk = checagens.conferir(p, conferida, ctx, base, fichas=[], gatilho=None, pedido=bloco)
    assert [x.codigo for x in chk.pendencias] == [], [x.motivo for x in chk.pendencias]
    # O texto que o verificador lê tem a lacuna de volta, com o dado.
    texto = v.texto_para_verificar(conferida.texto, valores, ())
    assert "{numero_pedido}" in texto and PEDIDO_FALSO not in texto
    assert v.linhas_das_lacunas(texto, v.lacunas_com_dado(valores))[1:] == [
        "- {numero_pedido}: o pedido TEM o dado (campo numero = o número do pedido); o sistema "
        "preenche — fato do pedido, com lastro só nesse papel"
    ]


# ═══════════════ revisão do v7: os que bloqueiam com a ENTRADA nova ═══════════════


def test_revisao_os_35_que_bloqueiam_com_entrada_nova_saem_a_parte():
    """O arquivo das 321 linhas de antes é o mesmo, mas a entrada de 35 casos que
    bloqueiam mudou com o v7: 30 ganharam a seção LACUNAS (os irmãos também o "o
    código já o liberou") e 5 passaram a dizer "nenhuma" — 4 deles com o rascunho que
    promete atendente (o item 1 continua). O recall deles sai à parte."""
    casos = _casos()
    lacunas = calibrar.ENTRADA_NOVA_V7["entrada_nova_v7_lacunas"]
    nenhuma = calibrar.ENTRADA_NOVA_V7["entrada_nova_v7_nenhuma"]
    assert len(lacunas) == 30 and len(nenhuma) == 5 and not lacunas & nenhuma
    for id_ in lacunas | nenhuma:
        assert not casos[id_]["esperado"]["ok_para_envio_automatico"], id_
    for id_ in lacunas:
        assert "o pedido TEM o dado" in _entrada(id_), id_
    for id_ in (f"irmaos-0{n}" for n in range(1, 8)):
        assert "o código já o liberou" in _entrada(id_), id_
    for id_ in nenhuma:
        assert "nenhuma —" in _entrada(id_), id_
    promessas = {"fresco2-T12", "fresco2-T14", "fresco3-H33", "fresco3-PH1"}
    for id_ in promessas:
        c = casos[id_]
        assert c["assunto"] == "pede_atendente" and c["esperado"][v.PROMETE], id_
    # Nenhum outro caso que bloqueia diz "nenhuma": só esses 5 e os 2 vizinhos que
    # o forçam.
    dizem = {
        i
        for i, c in casos.items()
        if not c["esperado"]["ok_para_envio_automatico"] and calibrar.sem_pergunta_do_caso(c)
    }
    assert dizem == nenhuma | set(FORCAM_NENHUMA)

    def reg(id_: str, segurou: bool) -> dict:
        return {
            "id": id_,
            "grupo": casos[id_]["grupo"],
            "contaminado": False,
            "esperado": {calibrar.BLOQUEIA: True},
            "obtido": {calibrar.BLOQUEIA: segurou},
        }

    por = calibrar.recall_por_grupo(
        [reg("injecao-V01", True), reg("irmaos-03", True), reg("fresco2-T12", False)]
    )
    assert por["entrada_nova_v7_lacunas"] == {
        "casos": 2,
        "ruins": 2,
        "escapes": [],
        "recall": 1.0,
    }
    assert por["entrada_nova_v7_nenhuma"]["escapes"] == ["fresco2-T12"]
    assert por["fresco2"]["escapes"] == ["fresco2-T12"]  # o grupo de sempre continua


async def test_revisao_a_calibracao_roda_so_os_ids_pedidos(tmp_path, teto_limpo):
    """`--ids` (com os nomes de `ENTRADA_NOVA_V7`): só os 35 de entrada nova e o que
    mais se pedir rodam (a recalibração os confere primeiro)."""
    casos = list(_casos().values())
    nomes = ",".join(calibrar.ENTRADA_NOVA_V7)
    pedidos = set().union(*calibrar.ENTRADA_NOVA_V7.values()) | {ID_426B}
    rodam = [c for c in casos if c["id"] in pedidos]
    falsa = _ver.Api()
    falsa.veredito = [_veredito_para(c) for c in rodam]
    eco = io.StringIO()
    async with _ver._cliente(falsa) as cliente:
        codigo = await calibrar.principal(
            ["--casos", str(_ver._arquivo(tmp_path, casos)), "--ids", f"{nomes}, {ID_426B}"],
            agora=_ver.FORA_DA_JANELA,
            eco=eco,
            cliente=cliente,
        )
    assert codigo == calibrar.OK
    resumo = json.loads(eco.getvalue().strip().splitlines()[-1])
    assert resumo["casos"] == len(rodam) == 36 and resumo["errados"] == []
    assert resumo["por_grupo"]["entrada_nova_v7_lacunas"]["recall"] == 1.0
    assert resumo["por_grupo"]["entrada_nova_v7_nenhuma"]["casos"] == 5
    assert len(falsa.pedidos) == 36
