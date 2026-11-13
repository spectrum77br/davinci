"""O TIPO da pergunta do cliente pela palavra-chave (banco de respostas, 13/11/2026). PURO.

Os códigos são os assuntos do agente (`atendimento_categorias`): o banco de respostas
guarda cada resposta da equipe com o tipo da pergunta, e é por tipo que a régua de
envio sozinho é medida (80%, 90% nos sensíveis — o Eduardo, 13/11/2026). Quando a
pergunta tem rascunho do agente, vale o assunto que ELE declarou
(`banco_respostas`); este classificador é para o histórico e para a resposta de fora
(Duoke, Seller Center), que não têm rascunho.

Medido na colheita de 13/11/2026 (11.130 turnos respondidos por pessoa): 79% saem com
tipo; o resto é seguimento de conversa ou frase vaga ("indefinido"), que só se
entende com a conversa inteira.

A ORDEM é a prioridade: primeiro o que é problema com a compra (quem reclama de
defeito também fala do carregador), depois o pedido, depois o produto, por fim a
cortesia. Cortesia pura (cumprimento, agradecimento, a opção do menu) é decidida
antes das regras.
"""

from __future__ import annotations

import re

from app.services.atendimento.validador import plano_de

INDEFINIDO = "indefinido"
OUTRO = "outro"

# Reembolso, cancelamento, troca/devolução e ameaça (Procon, processo): a régua de
# envio sozinho é de 90% (o Eduardo, 13/11/2026). A mediação/reclamação aberta é um
# ESTADO da conversa (acao_pessoa `reclamacao_aberta`), não um tipo de pergunta.
SENSIVEIS = frozenset({"reembolso", "cancelamento", "troca_devolucao", "reclamacao_forte"})

GRUPO_PESSOA = "pessoa"
GRUPO_PEDIDO = "pedido"
GRUPO_PRODUTO = "produto"
GRUPO_CORTESIA = "cortesia"

_PESSOA = frozenset(
    {
        "reclamacao_forte",
        "cancelamento",
        "reembolso",
        "entrega_nao_recebida",
        "item_errado_faltando",
        "defeito",
        "troca_devolucao",
        "garantia",
        "endereco",
        "desconto",
        "marca_autorizacao",
        "divergencia_anuncio",
        "substituicao_estoque",
        OUTRO,
    }
)
_PEDIDO = frozenset(
    {
        "rastreio",
        "prazo_envio",
        "entrega_instrucoes",
        "nota_fiscal",
        "confirmar_variacao",
        "pedido_nao_localizado",
    }
)
_PRODUTO = frozenset(
    {
        "conteudo_da_caixa",
        "originalidade_condicao",
        "resistencia_agua",
        "espec_mala",
        "espec_eletro",
        "espec_celular",
        "espec_outros",
        "estoque_variacao",
        "cobertura_regiao",
        "prazo_antes_compra",
        "preco_pagamento",
        "acessorios_avulsos",
        "uso_configuracao",
        "pedido_link_midia",
        "opiniao_recomendacao",
        "o_que_e_o_anuncio",
    }
)
_CORTESIA = frozenset(
    {
        "agradecimento",
        "saudacao_ou_vaga",
        "pede_atendente",
        "canal_externo_dados",
        "parceria_fora_escopo",
        "posvenda_pergunta_publica",
    }
)


def grupo(tipo: str | None) -> str:
    """pessoa | pedido | produto | cortesia | indefinido."""
    if tipo in _PESSOA:
        return GRUPO_PESSOA
    if tipo in _PEDIDO:
        return GRUPO_PEDIDO
    if tipo in _PRODUTO:
        return GRUPO_PRODUTO
    if tipo in _CORTESIA:
        return GRUPO_CORTESIA
    return INDEFINIDO


_TIPOS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "reclamacao_forte",
        re.compile(
            r"\b(procon|reclame\s*aqui|reclamac\w*|disputa|mediac\w*|processar|advogad\w*|justica"
            r"|denunci\w*|absurdo|palhacada|vergonha|descaso|golpe|golpista|enganad\w*|mentir\w*"
            r"|ridicul\w*|pessim\w*|lixo)\b"
        ),
    ),
    ("cancelamento", re.compile(r"\b(cancel\w*|desist\w*)\b")),
    (
        "reembolso",
        re.compile(
            r"\b(reembols\w*|estorn\w*|devolv\w*\s+(o\s+|meu\s+)?dinheiro|dinheiro\s+de\s+volta)\b"
        ),
    ),
    (
        "entrega_nao_recebida",
        re.compile(
            r"(consta\s+(como\s+)?entregue|diz\s+que\s+(foi\s+)?entregue|marcad\w+\s+como\s+entregue"
            r"|entregue\s+mas|nao\s+(foi\s+)?entregue|extravi\w*|devolvid\w+\s+ao\s+remetente"
            r"|roubad\w*)"
        ),
    ),
    (
        "item_errado_faltando",
        re.compile(
            r"(veio\s+(errad|outr|diferent|faltand|sem\b)|faltou|faltando|nao\s+veio\s+(o|a|com)"
            r"|veio\s+so\b|cor\s+errada|modelo\s+errado|produto\s+errado|caixa\s+vazia"
            r"|veio\s+trocad)"
        ),
    ),
    (
        "defeito",
        re.compile(
            r"\b(defeit\w*|nao\s+liga|nao\s+carreg\w*|parou\s+de|quebrad\w*|trincad\w*|rachad\w*"
            r"|amassad\w*|riscad\w*|travand\w*|trava\b|esquent\w*|desligand\w*|reinici\w*"
            r"|nao\s+funcion\w*|tela\s+(preta|apagad|piscand)|touch|nao\s+pega|sem\s+sinal"
            r"|bateria\s+(vicia|durando\s+pouco|descarreg)|desliga\b|desligou|apagou\s+do\s+nada"
            r"|nao\s+conecta\w*|continua\s+do\s+mesmo\s+jeito|esta\s+assim\s+desde|falha\w*"
            r"|abafad\w*|chiad\w*|sem\s+som|nao\s+sai\s+som|nao\s+reconhec\w*|nao\s+le\b)"
        ),
    ),
    ("troca_devolucao", re.compile(r"\b(troc[ao]r?\b|trocar|troca\b|devolu\w*|devolver|devolvo)")),
    ("garantia", re.compile(r"\b(garantia|assistencia\s+tecnica|conserto|consertar)\b")),
    ("endereco", re.compile(r"(endereco|destinatari\w*|cep\s+errado|numero\s+da\s+casa)")),
    (
        "desconto",
        re.compile(
            r"\b(desconto|cupom|cupons|mais\s+barato|faz\s+por|abaix\w+\s+o\s+preco|atacado"
            r"|revend\w*|brindes?|frete\s+gratis|promoc\w*|unidades|vender\s+(os|seus)\s+produtos"
            r"|pacote\s+com)\b"
        ),
    ),
    ("nota_fiscal", re.compile(r"\b(nota\s+fiscal|nf\b|nfe|danfe|nota\b|emitid\w+|emissao)")),
    (
        "rastreio",
        re.compile(
            r"\b(rastrei\w*|onde\s+(esta|anda)|cade|nao\s+chegou|ainda\s+nao\s+(recebi|chegou)"
            r"|nao\s+recebi|a\s+caminho|atrasad\w*|atraso|quando\s+(chega|vai\s+chegar|vou\s+receber)"
            r"|previsao\s+d[aeo]\s+(entrega|chegada)|parad\w+\s+(em|no|na)|codigo\s+de\s+rastr"
            r"|quando\s+.{0,25}cheg\w*|ja\s+chegou|nao\s+entregaram|chega\s+hoje"
            r"|quando\s+.{0,25}entregue|nada\s+ainda|entregando\s+no\s+lugar\s+errado"
            r"|o\s+que\s+houve\s+com\s+o\s+(meu\s+)?pedido)\b"
        ),
    ),
    (
        "prazo_envio",
        re.compile(
            r"(quando\s+(vai\s+ser\s+|sera\s+|vao\s+)?(enviad|postad|despachad|envia|manda)\w*"
            r"|ja\s+foi\s+enviad\w*|ja\s+(postou|enviou|mandou|despachou)"
            r"|prazo\s+de\s+(envio|postagem)|nao\s+foi\s+enviad\w*"
            r"|ainda\s+nao\s+(foi\s+)?(enviad|postad)\w*|enviar?\s+hoje"
            r"|quando\s+.{0,25}(sera|vai\s+ser|e)\s+(enviad|postad|despachad)\w*"
            r"|em\s+preparac\w*|envi\w+\s+com\s+urgencia|ja\s+enviaram|previsao\s+de\s+saida"
            r"|prioridade\s+no\s+envio)"
        ),
    ),
    (
        "entrega_instrucoes",
        re.compile(
            r"\b(pegam\s+(na|em)\s+casa|coleta|faco\s+o\s+envio|enviar\s+pelo\s+correio|retirar"
            r"|retirada|agencia|transportadora|entregador|correios|ponto\s+de\s+coleta|portaria"
            r"|sabado|domingo|horario\s+de\s+entrega|jadlog|loggi|j&t|jt\s+express)\b"
        ),
    ),
    (
        "confirmar_variacao",
        re.compile(
            r"(qual\s+(cor|modelo|tamanho)\s+(vai|vem|veio|vou\s+receber)"
            r"|comprei\s+(a|o|na|no)\s+(cor|modelo)|pedi\s+(a|o)\s+(cor|modelo)"
            r"|vai\s+(vir|ser)\s+(a|o|na|no)\s+(cor|modelo)|escolhi\s+a\s+cor)"
        ),
    ),
    (
        "pedido_nao_localizado",
        re.compile(
            r"(nao\s+(acho|encontro|localizo)\s+(o\s+|meu\s+)?pedido|pedido\s+sumiu"
            r"|comprei\s+em\s+outra\s+loja)"
        ),
    ),
    (
        OUTRO,
        re.compile(r"(sao\s+de\s+qual\s+cidade|de\s+onde\s+(voces|vcs)\s+sao|loja\s+fisica)"),
    ),
    (
        "acessorios_avulsos",
        re.compile(
            r"((capa|capinha|pelicula)\w*\s+do\s+iphone|(serve|da|cabe)\s+(nele|nesse|neste|no\s+meu)"
            r"|(serve|encaixa)\s+(a|o)\s+(capa|capinha|pelicula|carregador)"
            r"|vende(m)?\s+(capa|capinha|pelicula|carregador|fone|cabo)"
            r"|tem\s+(capa|capinha|pelicula)\s+(para|pra|desse)|so\s+(a\s+)?(capa|capinha|pelicula))"
        ),
    ),
    (
        "conteudo_da_caixa",
        re.compile(
            r"\b(carregador|fonte|cabo|fone|capinha|capa|pelicula|acompanha|vem\s+com|vem\s+junto"
            r"|o\s+que\s+vem|itens?\s+(da|na)\s+caixa|na\s+caixa|caixa\s+(original|slim)|manual"
            r"|chave\s+(do\s+)?chip|adaptador|tomada|plugue|dentro\s+da\s+caixa|acessorios)\b"
        ),
    ),
    (
        "originalidade_condicao",
        re.compile(
            r"\b(original|originais|lacrad\w*|novo\b|nova\b|usad\w*|seminovo|vitrine|recondicion\w*"
            r"|replica|paralel\w*|pirata|confiavel|confianca|anatel|homologad\w*|nacional"
            r"|importad\w*|global|versao\s+global|liberad\w+\s+no\s+brasil"
            r"|funciona\s+(no\s+brasil|de\s+verdade|perfeitamente|mesmo))\b"
        ),
    ),
    (
        "resistencia_agua",
        re.compile(r"(prova\s+d.?agua|agua|molha|ip6\d|ip5\d|resistente|queda|poeira)"),
    ),
    (
        "espec_mala",
        re.compile(
            r"\b(mala|malas|bagagem|bordo|rodinha\w*|rodas?\b|expansiv\w*|ziper|cadeado|tsa"
            r"|frasqueira|anac|despachar|\d+\s*kg|\d+\s*kilos?|\d+\s*quilos?|polipropileno|abs\b"
            r"|policarbonato)\b"
        ),
    ),
    (
        "espec_eletro",
        re.compile(
            r"\b(air\s*fryer|airfryer|fritadeira|litros?|voltagem|bivolt|110\s*v?|127\s*v?|220\s*v?"
            r"|watts?|potencia|forno|cesto|antiaderente)\b"
        ),
    ),
    (
        "espec_celular",
        re.compile(
            r"\b(memoria|ram|gb\b|\d+\s*gb|armazenamento|camera|mp\b|megapix\w*|bateria|mah|nfc|5g"
            r"|4g|dual\s*chip|chip|esim|e-sim|tela|polegadas|android|processador|snapdragon|helio"
            r"|biometria|digital|face\s*id|reconhecimento|sd\b|cartao\s+de\s+memoria"
            r"|whatsapp\s+duplo|carregamento\s+rapido|turbo|wifi|bluetooth|gps|google|play\s*store"
            r"|banco|pix|operadora|vivo|claro|tim\b|sinal|rede|desbloquead\w*|carcaca|material"
            r"|metal|aluminio|vidro|ligac\w*|chamada|som|alto\s*falante|volume|tamanho\s+da\s+tela"
            r"|peso|gramas|e\s+grande|grande\s+ou\s+pequeno|display|pesado|kls?\b|quilos)\b"
        ),
    ),
    (
        "estoque_variacao",
        re.compile(
            r"\b(cor|cores|azul|preto|preta|branco|branca|rosa|verde|roxo|roxa|dourad\w*|prata"
            r"|cinza|vermelh\w*|laranja|amarel\w*|lilas|bege|marrom|grafite|titanio|natural"
            r"|tem\s+ainda|estao\s+tendo|voces\s+tem|vcs\s+tem|tem\s+esse|tem\s+essa|tamanho"
            r"|tamanhos|disponivel|disponiveis|estoque|esgotad\w*|reposic\w*|vai\s+chegar\s+mais"
            r"|tem\s+(o|a)\s+\w+|ainda\s+tem|teria|tem\s+(na|no|em)\b|variac\w*|modelo|modelos"
            r"|versao|128|256|512)\b"
        ),
    ),
    (
        "cobertura_regiao",
        re.compile(
            r"(entrega\s+(em|para|pra|no|na|aqui)|envia\s+(para|pra)|enviam\s+(para|pra)"
            r"|manda\s+(para|pra)|chega\s+(em|no|na|aqui|ate)\s+[a-z]|atende\s+(minha\s+)?regiao"
            r"|\bcep\b|\b(rj|rio\s+de\s+janeiro|rio\s+grande\s+do\s+sul|manaus|amazonas"
            r"|bahia|nordeste|norte|interior|capital)\b)"
        ),
    ),
    (
        "prazo_antes_compra",
        re.compile(
            r"(quantos\s+dia|ate\s+quantos|chega\s+certinho|quanto\s+tempo|prazo"
            r"|chega\s+(rapido|logo|antes|ate)|se\s+eu\s+comprar\s+hoje|demora|chegaria"
            r"|entrega\s+rapida|full\b|envio\s+imediato|pronta\s+entrega)"
        ),
    ),
    (
        "preco_pagamento",
        re.compile(
            r"\b(frete|parcel\w*|vezes|juros|boleto|pix|cartao|pagamento|pagar|pago|valor|preco"
            r"|tentando\s+comprar|quanto\s+custa|quanto\s+fica|quanto\s+sai|reais"
            r"|nao\s+consigo\s+(pagar|comprar|finalizar))\b"
        ),
    ),
    (
        "uso_configuracao",
        re.compile(
            r"(como\s+(faco|faz|configur\w*|usa|uso|liga|ligo|coloc\w*|instal\w*|ativ\w*|desativ\w*"
            r"|troc\w*\s+o\s+idioma|tir\w*|conect\w*|emparelh\w*|reset\w*|formata\w*)|configurar"
            r"|idioma|ingles|portugues|senha|conta\s+google|frp|atualiza\w*|aplicativo|\bapp\b"
            r"|nao\s+consigo)"
        ),
    ),
    (
        "pedido_link_midia",
        re.compile(
            r"\b(foto|fotos|video|videos|link|imagem|imagens|manda\s+(uma\s+)?foto|foto\s+real)\b"
        ),
    ),
    (
        "opiniao_recomendacao",
        re.compile(
            r"(vale\s+a\s+pena|qual\s+(e\s+)?(o\s+)?melhor|qual\s+(voce|vc)\s+(indica|recomenda)"
            r"|recomenda|indica|bom\s+mesmo|e\s+bom|presta|qual\s+a\s+diferenca|diferenca\s+entre"
            r"|compar\w*|melhor\s+(que|do\s+que))"
        ),
    ),
    (
        "o_que_e_o_anuncio",
        re.compile(
            r"(e\s+o\s+(celular|aparelho)\s+ou|so\s+a\s+capa|e\s+so\s+a|e\s+o\s+produto"
            r"|o\s+anuncio\s+e|e\s+(um\s+)?kit|vem\s+o\s+aparelho)"
        ),
    ),
    (
        "parceria_fora_escopo",
        re.compile(
            r"\b(parceria|divulga\w*|influenc\w*|afiliad\w*|amostra|personaliz\w*|permuta|ugc"
            r"|publi|creator|criador)\b"
        ),
    ),
    (
        "canal_externo_dados",
        re.compile(
            r"\b(whatsapp|whats|zap|telefone|celular\s+de\s+contato|numero\s+de\s+contato"
            r"|instagram|insta|e-?mail|email)\b"
        ),
    ),
    (
        "pede_atendente",
        re.compile(
            r"\b(atendente|alguem\s+(ai|pode|me)|responde\w*|humano|pessoa\s+real|ninguem|robo"
            r"|bot\b|me\s+ajud\w*|ajuda)\b"
        ),
    ),
)

TIPOS: tuple[str, ...] = tuple(dict.fromkeys(t for t, _ in _TIPOS))

# Palavra de cortesia (cumprimento, confirmação, a opção do menu do Duoke): o turno
# feito SÓ disso é cortesia, não pergunta.
_CORTESIA_PALAVRAS = re.compile(
    r"\b(oi+|ola|opa|bom|boa|dia|tarde|noite|tudo|td|bem|obrigad\w*|obg|brigad\w*|valeu|vlw"
    r"|ok|okay|certo|blz|beleza|sim|nao|positivo|perfeito|entendi|show|top|otimo|joia"
    r"|combinado|ta|esta|aguardo|amem|gratid\w*|grato|grata|muito|e|voce|vc|voces|vcs|ai|ei"
    r"|hello|conversar|com|vendedor|atendente|\d+)\b"
)
_MENU = re.compile(r"^\W*(\d\W*)+$|conversar\s+com\s+(o\s+)?vendedor")
_AGRADECE = re.compile(r"obrigad|obg|brigad|valeu|vlw|agradec|grat")
_AGRADECE_CURTO = re.compile(
    r"\b(obrigad\w*|obg|brigad\w*|valeu|vlw|agradec\w*|grat\w*|de\s+nada|compreendi|entendi"
    r"|recomendo|muito\s+bom)\b"
)
_MASCARA = re.compile(r"\[[a-z_]+\]")


def tipo_de(texto: str | None) -> str:
    """O tipo da pergunta (um dos assuntos do agente) ou `indefinido`. PURA."""
    limpo = _MASCARA.sub(" ", plano_de(texto or ""))
    resto = re.sub(r"[^a-z0-9 ]", " ", _CORTESIA_PALAVRAS.sub(" ", limpo)).split()
    if _MENU.search(limpo) and len(resto) <= 1:
        return "pede_atendente"
    if not resto:
        return "agradecimento" if _AGRADECE.search(limpo) else "saudacao_ou_vaga"
    if "?" not in limpo and len(limpo.split()) <= 12 and _AGRADECE_CURTO.search(limpo):
        return "agradecimento"
    for tipo, padrao in _TIPOS:
        if padrao.search(limpo):
            return tipo
    return INDEFINIDO
