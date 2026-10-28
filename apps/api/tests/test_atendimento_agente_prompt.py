# ruff: noqa: E501, F811 — as frases do prompt ficam inteiras; fixtures importadas do e2e
"""O prompt do agente depois do eval real da Kia (21/10/2026).

O eval (80 conversas reais da Shopee, braço A2, nota dupla às cegas) achou 34
erros críticos; 32 eram rascunhos de PESSOA com promessa de espera ("nossa
equipe vai verificar/conferir/confirmar e responder por aqui", "a equipe
acompanha o caso"), proibida pela regra 16 do manual. Os ajustes do
`agente.prompt` foram guiados só pelos comentários do split DEV; aqui:

- o texto: o rascunho de pessoa é a resposta DEPOIS da ação (o que a pessoa faz
  vai só no `motivo_pessoa`; sem nada a dizer antes, texto vazio); responder em
  vez de encaminhar (os padrões do dev); o cancelamento depois do envio e a
  devolução com as palavras do manual; lacuna só com o dado;
- as frases que o prompt manda dizer passam nas travas do próprio sistema
  (`validador`, `checagens`) — o prompt e o código não podem brigar;
- ponta a ponta (`ia.gerar_rascunho`, SDK sobre o transporte falso, sem LLM): o
  texto vazio de "pessoa" vira rascunho sem texto para a pessoa (nunca
  descartado, nunca enviado); a resposta de pessoa com o que já se sabe fica
  pendente; o cancelamento depois do envio com as palavras novas sai sozinho
  com o assunto liberado;
- o tamanho e o retrato (hash) do bloco 1: mudar um byte invalida o cache de
  1 h de todas as conversas — o retrato obriga a mudança a ser de propósito.
"""

from __future__ import annotations

import hashlib
import re

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Logistica
from app.services.atendimento import constantes, ia, validador
from app.services.atendimento.agente import checagens, prompt, saida
from app.services.atendimento.constantes import (
    MODO_AUTO,
    MODO_COPILOTO,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_DESCARTADO,
    RASCUNHO_PENDENTE,
)
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    _decisao,
    _do_agente,
    _final,
    _ligar,
    agente_ligado,
    api,
    envios,
    ia_ligada,
    leitura,
    lojas,
    redis_mem,
    sem_busca_pendurada,
    telegram,
)
from tests.test_atendimento_ia import (
    _conversa,
    _importar_manual_base,
    _msg,
    _pedido_completo,
)

# O retrato do bloco 1 (sha256 de `prompt.bloco_geral()`, 16 hex). Mudou o
# prompt de propósito? O teste falha dizendo o novo valor: troque aqui — e
# lembre que o cache de 1 h zera para todas as conversas na primeira resposta.
# 21/10/2026 (tarde): o agente é o atendente da loja (decisão do dono) — o
# exemplo do `pediu_pessoa`, a linha do pedido de atendente e a resposta honesta
# ao "é robô?" (antes "9359d96e440eb15d", a revisão do r5i).
# 21/10/2026 (noite): os ajustes do 1º dia ao vivo na Kia e do eval do TikTok — a
# condição pela "condicao" da ficha, o "ok" depois de a loja tratar, as
# evidências da linha EVIDÊNCIAS (antes "bcc65fc5ce18aafe").
# 21/10/2026 (Barbosa ao vivo, item 10, com a revisão): "perguntar como ajudar é
# resposta completa" na linha "confianca" do `_SAIDA` (só o "como ajudar", não toda
# pergunta); os cabeçalhos das ferramentas e dos <dados> e o "com os 7 campos"
# encurtaram para caber no teto (antes "ebaf25a7a50b2948").
# 21/10/2026 (noite, item 13 da auditoria): a régua da "confianca" no `_SAIDA` — 0,9
# com cada afirmação nos ids citados (ou na orientação do assunto) e a pergunta
# respondida; 0,6 a 0,8 parcial ou incerta. Para caber no teto, palavras a menos no
# `_COMO_TRABALHAR` (2), `_DADOS`, `_ESTILO`, `_VARIACOES`, `_TROCA` e `_PAPEL`, e a
# frase repetida do "precisa agir ou decidir agora" saiu das REGRAS DURAS (antes
# "9f30de51538953e1").
# 22/10/2026 (item 22 da auditoria ao vivo, 440a83e7): o "ao receber" do cancelamento e
# do RESPONDA é só de quem desiste de um pedido a caminho ("pede prioridade ou data"
# ficou à parte); "não chegou ou atrasou" é entrega_nao_recebida (nunca "ao receber"
# nem "recuse"; a previsão vencida é abrir_chamado). Para caber no teto, as duas linhas
# do pedido sem número/que não aparece viraram uma (antes "835308a2d9a8805c").
# 22/10/2026 (item 32 da auditoria ao vivo, a equipe em todo turno): o "par:<id>" do
# exemplo da equipe usado passa a ser evidência (`_SAIDA`) e o buscar_respostas_equipe
# fica "só o TOM; nada deles vale como dado" (`_FERRAMENTAS`) — 9 caracteres a menos; o
# resto vai no bloco 2 (`prompt.COMO_A_EQUIPE_RESPONDE`) (antes "6edf4627eca7db37").
# 28/10/2026 (o dono: "temos que perder essa trava"): a devolução/reclamação é tratada
# como a equipe da loja trata, o cliente decide — no lugar do "nunca desestimule
# reclamação" (antes "633eaf94186a21ee").
RETRATO_BLOCO_GERAL = "b5bb3c081a415e6b"


def _geral() -> str:
    return prompt.bloco_geral()


def _secao(titulo: str) -> str:
    """O trecho do bloco 1 que começa no título dado (até o próximo título)."""
    texto = _geral()
    inicio = texto.index(titulo)
    fim = texto.find("\n\n# ", inicio + 1)
    return texto[inicio : fim if fim >= 0 else len(texto)]


def _entre_aspas(texto: str, pos: int) -> bool:
    """A posição está dentro de aspas duplas ou simples (uma citação do que NÃO dizer)?"""
    linha = texto[texto.rfind("\n", 0, pos) + 1 : pos]
    return linha.count('"') % 2 == 1 or linha.count("'") % 2 == 1


# ─────────────── (a) o rascunho de pessoa ───────────────


def test_rascunho_de_pessoa_e_a_resposta_depois_da_acao():
    pessoa = _secao("# O RASCUNHO DE PESSOA")
    assert "faz a ação e envia o texto ao cliente" in pessoa
    assert "texto = a resposta que vale depois da ação" in pessoa
    assert "a parte que depende da pessoa fica fora" in pessoa
    assert "regra 16 do manual" in pessoa and "quem envia já agiu" in pessoa
    # O que a pessoa faz só no motivo; o texto nunca fala com a equipe.
    assert "vai só no motivo_pessoa" in pessoa
    assert "O texto fala só com o cliente: nada para a equipe" in pessoa
    # Sem nada verdadeiro a dizer antes: texto vazio (o código trata, ver o e2e).
    assert 'texto "" (vazio)' in pessoa
    # A única fala da equipe ao cliente é a do pedido de vídeo; em pessoa, nunca.
    assert "num rascunho de pessoa, nunca" in pessoa
    # O passo 3 e o contrato de saída apontam para a seção.
    assert "o texto segue O RASCUNHO DE PESSOA" in _geral()
    saida_final = _secao("# A RESPOSTA FINAL")
    assert '(em "pessoa", a resposta depois da ação: O RASCUNHO DE PESSOA)' in saida_final
    assert "o que a pessoa precisa fazer ou decidir e por quê" in saida_final
    # A seção vem antes da régua (o modelo lê a forma do texto antes dos códigos).
    assert _geral().index("# O RASCUNHO DE PESSOA") < _geral().index('# A RÉGUA "AÇÃO')


@pytest.mark.parametrize(
    "espera",
    [
        "nossa equipe vai",
        "vamos analisar",
        "equipe acompanha o caso",
        "assim que tivermos retorno",
        "confirma para você",
        "a loja não consegue cancelar",
        "não dá para cancelar",
        "não consigo confirmar",
    ],
)
def test_a_frase_de_espera_so_aparece_citada_como_proibida(espera):
    """O prompt cita as frases do eval só para proibir (entre aspas): nenhuma vira
    instrução nem exemplo do que dizer."""
    texto = _geral()
    achadas = [m.start() for m in re.finditer(re.escape(espera), texto)]
    assert achadas, espera
    for pos in achadas:
        assert _entre_aspas(texto, pos), texto[max(0, pos - 80) : pos + 40]


def test_a_orientacao_aprovada_nao_promete_acompanhamento():
    """A ORIENTACAO também vai ao `consultar_manual` e ao verificador como a
    orientação aprovada: nada de "a loja acompanha" / "a equipe acompanha"."""
    for assunto, texto in prompt.ORIENTACAO.items():
        assert not re.search(r"\b(?:loja|equipe)\s+acompanha", texto), assunto
        assert "nossa equipe vai" not in texto, assunto
    assert "nunca prometa nem anuncie ação da equipe" in prompt.VALE_SEMPRE
    assert "o texto já é a resposta depois dela" in prompt.VALE_SEMPRE
    # A oferta da pessoa é só a resposta ao "é robô?" (decisão do dono de
    # 21/10/2026: o atendimento virtual da loja, e uma pessoa pode assumir se o
    # cliente preferir — antes era "a nossa equipe acompanha a conversa").
    assert (
        "perguntou se é robô: é o atendimento virtual da loja e, se preferir, uma pessoa da "
        "nossa equipe pode assumir a conversa (essa frase só nessa resposta)"
    ) in _geral()
    assert "acompanha a conversa" not in _geral()


def test_a_regra_antiga_do_manual_nao_manda_para_pessoa_nem_vira_espera():
    passo = _secao("# COMO TRABALHAR")
    assert '"precisa_humano=true" não é motivo de pessoa (só a régua é)' in passo
    assert "nunca se copia: no lugar, a orientação do assunto" in passo


# ─────────────── (b) responder em vez de encaminhar ───────────────


def test_responda_em_vez_de_encaminhar_lista_os_padroes_do_dev():
    secao = _secao("# RESPONDA EM VEZ DE ENCAMINHAR")
    assert "Vá para pessoa só com um destes códigos." in _geral()
    assert "dado_indisponivel só depois de consultar" in secao
    assert "se o cliente não tem como dar o dado (se só ele tem, pergunte)" in secao
    for padrao in (
        # pedido já postado → orientação (cancelar, trocar, prioridade, data). Item 22
        # (22/10/2026): o "ao receber" só para quem desiste; a data à parte.
        "pedido já postado e o cliente quer cancelar, trocar ou desistir (não diz que não "
        "chegou): devolução ou reembolso ao receber",
        "pede prioridade ou data: envio e previsão em Minhas compras > o pedido, sem prometer "
        "prioridade",
        # prazo antes da compra → o anúncio mostra pelo CEP
        "prazo de entrega antes da compra: aparece no anúncio ao informar o CEP",
        # tamanhos na lista completa de variações
        "a lista de variações responde (com completas=true",
        # NFC etc. por versão na descrição
        "característica que a descrição dá por versão (NFC, 5G, memória): responda por versão",
        # retirada → compra e entrega só pela plataforma
        "retirar no local, comprar ou pagar por fora",
        # já decidiu e só agradece → confirma o caminho no app
        "o cliente já decidiu (devolver, comprar de novo, esperar) e só agradece",
    ):
        assert padrao in secao, padrao
    # O campo que varia por modelo não briga com "responda por versão".
    assert "a descrição diz o de cada versão, responda por versão" in _secao("# PRODUTO")
    # Encaminhar o que os dados respondem é erro (o papel).
    assert "Encaminhar o que os dados já respondem é erro" in _geral()
    assert _geral().index("# RESPONDA EM VEZ DE ENCAMINHAR") < _geral().index('# A RÉGUA "AÇÃO')


def test_perguntar_so_o_que_o_cliente_nao_deu_e_so_o_que_ele_perguntou():
    passo = _secao("# COMO TRABALHAR")
    assert "só o cliente tem e que ele ainda não deu" in passo
    assert "não comente o estado do pedido que ele não perguntou" in passo


def test_condicao_fora_do_titulo_sem_lancar_duvida():
    """cc95e95879e8 (dev): "não consigo confirmar" a condição de um Apple soa como
    dúvida sobre o produto. Revisão do r5i: "diga o que o anúncio informa" levava à
    condição fora do título (a descrição, a variação "NOVO") e ao "1 ano" do título,
    que o código barra — agora, o que o TÍTULO traz e a compra protegida.

    Fontes v1.3, item 10 (dono, 21/10/2026): a condição vem da "condicao" da ficha
    (título, atributo de condição ou descrição do anúncio da conversa); o
    "conflito" vai para pessoa; a certificação (água) segue só pelo título."""
    produto = _secao("# PRODUTO")
    assert 'original) só pela "condicao" da ficha' in produto
    assert '"conflito", ou a loja disse o contrário na conversa: pessoa' in produto
    assert "À prova d'água só pelo TÍTULO" in produto
    assert "nunca pela descrição, atributo ou variação" not in produto
    assert (
        'diga o que o título traz (marca e modelo) e que a compra é protegida pela plataforma, nunca "não consigo confirmar"'
        in produto
    )
    assert "O prazo de garantia do título não se repete" in produto


# ─────────────── (c) a orientação com as palavras do manual ───────────────


def test_cancelamento_depois_do_envio_com_as_palavras_do_manual():
    """Revisão do r5i: sem a negativa ("não permite cancelar" era o "não dá para
    cancelar" do manual com outro sujeito); aguarda o OK do Eduardo."""
    o = prompt.ORIENTACAO["cancelamento"]
    assert "como o pedido já foi enviado, para cancelar agora o caminho é a devolução" in o
    assert "não permite cancelar um pedido já enviado" not in o
    assert "pedir a devolução ou o reembolso em Minhas compras > o pedido" in o
    assert "a plataforma (diga o nome) analisa e devolve pela mesma forma de pagamento" in o
    assert (
        "Nunca 'não dá para cancelar', 'não é possível', 'não permite' nem 'a loja não consegue cancelar'"
        in o
    )
    assert "Nunca oriente a recusar a entrega nem proponha cancelar" in o
    assert "segurar_envio" in o
    # A frase velha ("a loja não consegue mais cancelar") saiu do prompt inteiro.
    assert "não consegue mais cancelar" not in _geral()


def test_devolucao_e_devolucao_e_troca_so_quando_o_cliente_pediu():
    o = prompt.ORIENTACAO["troca_devolucao"]
    assert o.startswith("Devolução se chama devolução")
    assert "a plataforma analisa e faz o reembolso pela mesma forma de pagamento" in o
    assert "sem o motivo, pergunte qual é (nunca como condição)" in o
    # Revisão do r5i: separada por postado e por defeito.
    assert (
        "Troca, só se o cliente pediu troca: não postado e quer outra variação ou modelo, excecao"
        in o
    )
    assert (
        "postado, a devolução deste pedido ao receber, e o outro modelo ou variação é uma compra nova"
        in o
    )
    assert "por defeito ou avaria, a orientação do defeito (vídeo e fotos), nunca compra nova" in o
    assert "aprovar_fora_da_plataforma" in o and "analisar_evidencia" in o


def test_defeito_pede_o_video_uma_vez_e_nao_escolhe_a_solucao():
    o = prompt.ORIENTACAO["defeito"]
    assert "uma vez só" in o
    assert "'troca, devolução ou reembolso seguem depois da análise' (sem escolher um)" in o
    assert "sem repetir se o texto já pediu o vídeo" in _secao("# GARANTIA")
    assert "Nunca 'a equipe confirma o prazo'" in prompt.ORIENTACAO["garantia"]


# ─────────────── (d) lacunas só com o dado ───────────────


def test_lacuna_so_com_o_campo_preenchido():
    lacunas = _secao("# LACUNAS DO PEDIDO")
    assert "campo null ou ausente = o dado não existe, e a frase sai sem ele" in lacunas
    assert "Nunca deixe lacuna, colchete ou espaço para alguém completar" in lacunas
    assert "nem lacuna no motivo_pessoa" in lacunas
    for lacuna in constantes.LACUNAS:
        assert "{" + lacuna + "}" in lacunas


# ─────────────── as frases do prompt passam nas travas do sistema ───────────────

CANCELAMENTO_POSTADO = (
    "Olá! Como o pedido {numero_pedido} já foi enviado, para cancelar agora o caminho é a "
    "devolução: ao receber, você pode pedir a devolução ou o reembolso em Minhas Compras > o "
    "pedido; a Shopee analisa e devolve pela mesma forma de pagamento."
)
DEVOLUCAO = (
    "Olá! A devolução é pedida em Minhas Compras > o pedido, e a Shopee analisa e faz o "
    "reembolso pela mesma forma de pagamento. Pode nos contar o motivo da devolução?"
)
TROCA_PELA_DEVOLUCAO = (
    "Olá! A troca segue pela devolução deste pedido no app: em Minhas Compras > o pedido, "
    "peça a devolução; a Shopee analisa e devolve pela mesma forma de pagamento, e o outro "
    "modelo é uma compra nova."
)
RETIRADA = (
    "Olá! A compra, o pagamento e a entrega são só pela Shopee: o pedido chega no endereço "
    "informado na compra."
)
JA_DECIDIU = (
    "Combinado! A devolução é pedida em Minhas Compras > o pedido, e a Shopee analisa e "
    "devolve pela mesma forma de pagamento."
)
DEFEITO = (
    "Sentimos muito pelo problema. Pode enviar por aqui um vídeo curto mostrando o que "
    "acontece e fotos do aparelho, sem dados pessoais na tela? A nossa equipe analisa e "
    "responde por aqui, e troca, devolução ou reembolso seguem depois da análise."
)
VALORES = {"numero_pedido": "251020ABCDEF12"}


def _preenchido(texto: str) -> str:
    preenchido, sem_dado, desconhecidas = ia.preencher(texto, VALORES)
    assert not sem_dado and not desconhecidas
    return preenchido


@pytest.mark.parametrize(
    ("assunto", "texto"),
    [
        ("cancelamento", CANCELAMENTO_POSTADO),
        ("troca_devolucao", DEVOLUCAO),
        ("troca_devolucao", TROCA_PELA_DEVOLUCAO),
        ("outro", RETIRADA),
        ("troca_devolucao", JA_DECIDIU),
    ],
)
def test_a_orientacao_que_o_prompt_manda_dizer_passa_nas_travas(assunto, texto):
    final = _preenchido(texto)
    assert validador.validar(final, plataforma="shopee", canal="chat", origem=ia.ORIGEM_IA) == []
    # Não é recusa de direito (nem pela estrutura, nem pela paráfrase).
    assert not validador.recusa_por_estrutura(final)
    assert not validador.recusa_cobertura(final)
    assert not validador.nega_garantia(final)
    # Não é promessa da equipe.
    assert (
        checagens.conferir_promessa_de_equipe(
            texto, acao=saida.RESPONDER, acoes_pessoa=[], assunto=assunto
        )
        == []
    )


def test_o_pedido_de_video_do_defeito_e_a_orientacao_aprovada():
    clientes = ["a tela está piscando, quero trocar"]
    assert (
        checagens.conferir_orientacao_do_defeito(
            DEFEITO,
            acao=saida.RESPONDER,
            assunto="defeito",
            textos_cliente=clientes,
            com_pedido=True,
        )
        == []
    )
    assert (
        checagens.conferir_promessa_de_equipe(
            DEFEITO,
            acao=saida.RESPONDER,
            acoes_pessoa=[],
            assunto="defeito",
            textos_cliente=clientes,
        )
        == []
    )


def test_o_exemplo_de_pessoa_do_prompt_nao_afirma_estado_nem_promete():
    """O exemplo do `_PESSOA` (enviar_arquivo_nf): a resposta depois de anexar."""
    exemplo = "Olá! Segue o arquivo da nota fiscal do pedido {numero_pedido}."
    assert exemplo in _secao("# O RASCUNHO DE PESSOA")
    final = _preenchido(exemplo)
    assert validador.validar(final, plataforma="shopee", canal="chat", origem=ia.ORIGEM_IA) == []
    assert checagens.conferir_estado_do_pedido(final, None, None) == []
    # E a frase de espera que o eval achou é a promessa que o sistema já pega na
    # resposta (a mesma regra que o prompt agora aplica ao rascunho de pessoa).
    espera = "Olá! Nossa equipe vai confirmar e responder por aqui."
    assert checagens.conferir_promessa_de_equipe(
        espera, acao=saida.RESPONDER, acoes_pessoa=[], assunto="outro"
    )


# ─────────────── tamanho e retrato ───────────────


def test_tamanho_do_bloco_geral():
    """~3,5 caracteres por token (o dia 1 mede com `count_tokens`): o mesmo teto do
    `test_atendimento_agente_laco.test_tamanho_do_prefixo` (4 a 6,5 mil tokens)."""
    tokens = len(_geral()) / 3.5
    assert 4_000 <= tokens <= 6_500, len(_geral())
    # Nada que muda por conversa e nenhum prazo de garantia em número.
    assert not re.search(r"\b\d+ (meses|dias)\b", _geral())
    assert not re.search(r"\b20[2-3]\d\b", _geral())


def test_retrato_do_bloco_geral():
    atual = hashlib.sha256(_geral().encode()).hexdigest()[:16]
    assert atual == RETRATO_BLOCO_GERAL, (
        f"o bloco 1 mudou ({atual}): se foi de propósito, troque RETRATO_BLOCO_GERAL — "
        "o cache de 1 h zera para todas as conversas"
    )


# ─────────────── ponta a ponta (sem LLM: o SDK sobre o transporte falso) ───────────────


async def test_pessoa_com_texto_vazio_vai_para_a_pessoa_sem_texto(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    """Nada verdadeiro a dizer antes da pessoa: o texto vazio de "pessoa" grava o
    rascunho SEM texto e bloqueado, para a pessoa escrever — com a ação no motivo.
    Nunca o DESCARTADO do "não pede resposta" (que é só do "responder" vazio) e
    nunca envio."""
    await _importar_manual_base(db)
    await _pedido_completo(db)  # postado
    conversa = await _conversa(db, make_user)
    await _ligar(db, conversa, modo=MODO_AUTO, auto_orientacao=["troca_devolucao"])
    await _msg(db, conversa, "posso pagar a diferença por fora e vocês me mandam o outro modelo?")
    api.roteiro = [
        _final(
            acao="pessoa",
            texto="",
            assunto="troca_devolucao",
            acao_pessoa="aprovar_fora_da_plataforma",
            motivo_pessoa="decidir a troca por fora pagando a diferença",
            evidencias=["pedido"],
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None
    assert r.status == RASCUNHO_BLOQUEADO and r.status != RASCUNHO_DESCARTADO
    assert not (r.texto or "").strip()
    assert r.precisa_humano
    assert "ação de pessoa: Aprovar fora da plataforma" in (r.motivo or "")
    assert _do_agente(r)["acoes_pessoa"] == ["aprovar_fora_da_plataforma"]
    assert envios == []


async def test_pessoa_com_a_resposta_que_ja_se_sabe_fica_pendente(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    """segurar_envio (o exemplo do prompt): o texto é o caminho do cancelamento no
    app — verdadeiro depois que a pessoa segura o envio; fica pendente na caixa
    (a pessoa envia), com a lacuna preenchida, e nunca sai sozinho."""
    await _importar_manual_base(db)
    await _pedido_completo(db)
    await db.execute(update(BlingOrder).values(situacao="6"))
    await db.execute(update(Logistica).values(postagem_data=None, localizacao=None))
    await db.commit()
    conversa = await _conversa(db, make_user)
    await _ligar(db, conversa, modo=MODO_AUTO, auto_orientacao=["cancelamento"])
    await _msg(db, conversa, "quero cancelar o pedido")
    texto = (
        "Olá! Como o pedido {numero_pedido} ainda não foi enviado, o cancelamento é pedido "
        "no próprio pedido, em Minhas Compras."
    )
    api.roteiro = [
        _final(
            acao="pessoa",
            texto=texto,
            assunto="cancelamento",
            acao_pessoa="segurar_envio",
            motivo_pessoa="segurar o envio do pedido",
            evidencias=["pedido"],
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert '"postado":"nao"' in api.pacote()
    assert r.status == RASCUNHO_PENDENTE and r.validador_ok, r.motivo
    assert r.texto and "{" not in r.texto and "ainda não foi enviado" in r.texto
    assert r.precisa_humano and "ação de pessoa: Segurar o envio" in (r.motivo or "")
    assert envios == [] and _decisao(r) == "precisa_humano"


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
async def test_cancelamento_depois_do_envio_com_as_palavras_novas(
    db: AsyncSession, make_user, agente_ligado, api, envios, modo
):
    """A orientação nova (revisão do r5i: "para cancelar agora o caminho é a
    devolução" + devolução ao receber, sem a negativa) é a resposta: limpa no
    copiloto e, com o assunto liberado, sai sozinha no automático (o verificador
    falso dá "ok")."""
    await _importar_manual_base(db)
    await _pedido_completo(db)  # situação 9, postado
    conversa = await _conversa(db, make_user)
    await _ligar(db, conversa, modo=modo, auto_orientacao=["cancelamento"])
    await _msg(db, conversa, "quero cancelar o pedido")
    api.roteiro = [
        _final(texto=CANCELAMENTO_POSTADO, assunto="cancelamento", evidencias=["pedido"])
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert '"postado":"sim"' in api.pacote()
    fatos = _do_agente(r)
    assert fatos["acoes_pessoa"] == [] and not fatos.get("avisos"), fatos.get("avisos")
    assert r.status == RASCUNHO_PENDENTE and not r.precisa_humano, r.motivo
    assert "{" not in (r.texto or "")
    if modo == MODO_AUTO:
        assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado"
    else:
        assert envios == []
